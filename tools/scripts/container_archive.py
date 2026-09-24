"""Container stream discovery, compression, and framing."""

import functools
import struct

from . import package_archive
from . import encrypted_entry
from . import protected_package
from .slz import decompress
from .vp2_dcms import parse_pk1, read_entry
from .triace_ps2_unpack import SECTOR


def encode_codepage(*args, **kwargs):
    from .vp2_container_text import encode_codepage as implementation
    return implementation(*args, **kwargs)


def read_messages(*args, **kwargs):
    from .vp2_container_text import read_messages as implementation
    return implementation(*args, **kwargs)


def resource_10_marked_block(source, mark):
    """Draw a PAL resource-10 acute or tilde over a USA local base glyph."""
    from .vp2_container_text import RESOURCE_10_NATIVE_MARKS
    points = RESOURCE_10_NATIVE_MARKS.get(mark)
    if points is None:
        return None
    from .scene_glyphs import set_glyph_value

    block = bytearray(source)
    for x, y, value in points:
        set_glyph_value(block, x, y, value)
    return bytes(block)

def find_container_stream(raw):
    """Return ``(offset, blob)`` for the first MCPS2 container in an entry."""
    for magic in (b"SLZ", b"SLE"):
        position = 0
        while True:
            at = raw.find(magic, position)
            if at < 0:
                break
            position = at + 1
            if at + 0x10 > len(raw) or raw[at + 3] > 3:
                continue
            stored = struct.unpack_from("<I", raw, at + 4)[0]
            if stored < 16 or at + 0x10 + stored > len(raw):
                continue
            stream = bytes(raw[at:at + 0x10 + stored])
            try:
                if magic == b"SLE":
                    from . import sle
                    blob = sle.decompress(stream)
                else:
                    blob = decompress(stream)
            except Exception:
                continue
            if blob[:8] == b"mcps2lib":
                return at, blob
    return None, None

def _mcps2_magic(packed):
    payload = packed[0x10:]
    if payload[:3] == b"SLZ":
        return None
    try:
        return decompress(payload, packed[3], 16)[:8] == b"mcps2lib"
    except (IndexError, ValueError, struct.error):
        return None


def _pk1_sections(raw):
    """Every PK1 table row that decompresses to an MCPS2 bank."""
    found = []
    for number, (tag, offset, length) in enumerate(parse_pk1(bytes(raw))):
        packed = bytes(raw[offset:offset + length])
        try:
            if packed[:3] == b"SLZ":
                if _mcps2_magic(packed) is False:
                    continue
                blob = decompress(packed)
                wrapper = "SLZ"
            elif packed[:3] == b"SLE":
                from . import sle
                blob = sle.decompress(packed)
                wrapper = "SLE"
            else:
                continue
        except (IndexError, ValueError, struct.error):
            continue
        if blob[:8] == b"mcps2lib":
            found.append({
                "number": number,
                "tag": tag,
                "offset": offset,
                "length": length,
                "packed": packed,
                "blob": blob,
                "wrapper": wrapper,
            })
    return found


def _pk1_container_section(raw, subresource=None):
    found = _pk1_sections(raw)
    if subresource is not None:
        if isinstance(subresource, str):
            chosen = [item for item in found if item["tag"] == subresource]
            what = "tag %s" % subresource
            if len(chosen) > 1:
                raise ValueError(
                    "PK1 holds %d MCPS2 subresources tagged %s: %s"
                    % (len(chosen), subresource, _describe_sections(chosen)))
        else:
            chosen = [item for item in found if item["number"] == subresource]
            what = "row %s" % subresource
        if not chosen:
            raise ValueError(
                "PK1 %s is not an MCPS2 subresource; it holds %s"
                % (what, _describe_sections(found) or "none"))
        return chosen[0]
    if len(found) > 1:
        raise ValueError(
            "PK1 contains %d MCPS2 subresources; the outer resource is "
            "ambiguous: %s" % (len(found), _describe_sections(found)))
    return found[0] if found else None


def _describe_sections(found):
    """Name each candidate, so an ambiguity says what it is ambiguous between."""
    return ", ".join(
        "row %d tag %s (%d bytes)" % (item["number"], item["tag"],
                                      len(item["blob"]))
        for item in found)


def pk1_container_sections(raw):
    """Every MCPS2 subresource in a PK1, ambiguous or not."""
    return _pk1_sections(raw)


def pk1_section_tag(raw, subresource=None):
    try:
        section = _pk1_container_section(raw, subresource)
    except ValueError:
        return None
    return section["tag"] if section else None

def unpack_container_entry(raw, resource, subresource=None, stripped=False):
    """Return the one structurally reachable MCPS2 container in an entry."""
    if raw[:3] == b"SLZ":
        blob = decompress(raw)
    elif raw[:3] == b"ZLS" and raw[0x10:0x13] == b"SLZ":
        blob = decompress(raw[0x10:])
    elif raw[:8] == b"mcps2lib":
        blob = raw
    else:
        blob = None
    if blob is None:
        section = _pk1_container_section(raw, subresource)
        if section is not None:
            blob = section["blob"]
    if blob is not None and blob[:8] != b"mcps2lib" and blob[:4] == b"p@Ck":
        try:
            return package_archive.unpack_container(blob)
        except package_archive.ContainerNotFound:
            pass
    if blob is None or blob[:8] != b"mcps2lib":
        _, blob = find_container_stream(bytes(raw))
    if blob is None:
        try:
            blob = package_archive.unpack_container(bytes(raw))
        except package_archive.ContainerNotFound as exc:
            try:
                clear, _layout = protected_package.decode_entry(raw)
                blob = package_archive.unpack_container(clear)
            except (protected_package.ProtectedPackageError,
                    package_archive.ContainerNotFound) as protected_exc:
                if encrypted_entry.is_encrypted(resource) and not stripped:
                    return unpack_container_entry(
                        encrypted_entry.decode_entry(raw, resource),
                        resource, subresource, stripped=True)
                raise ValueError(
                    "resource #%d is not a readable container (%r)" %
                    (resource, bytes(raw[:4]))) from protected_exc
    return blob

def container_stream_offset(raw):
    """Where the container the reader returns actually starts, or None."""
    if raw[:3] == b"SLZ":
        try:
            if decompress(raw)[:8] == b"mcps2lib":
                return 0
        except Exception:
            pass
    if raw[:3] == b"ZLS" and raw[0x10:0x13] == b"SLZ":
        try:
            if decompress(raw[0x10:])[:8] == b"mcps2lib":
                return 0x10
        except Exception:
            pass
    at, _ = find_container_stream(bytes(raw))
    return at

def container(handle, table, total, resource, subresource=None):
    """Return the decompressed MCPS2 container of a bare entry."""
    return unpack_container_entry(
        bytes(read_entry(handle, table, total, resource)), resource,
        subresource)

def _round_up(value, alignment):
    return (value + alignment - 1) // alignment * alignment

def _tighten(packed, encoded_stored, blob, mode, target_stored, exact_stored,
             room):
    """Re-encode with the shortest-path parse when the greedy one spills."""
    if room is None or len(packed) <= room:
        return packed, encoded_stored
    tighter, tighter_stored = _compress_container(
        blob, mode, target_stored=target_stored, exact_stored=exact_stored,
        optimal=True)
    if len(tighter) < len(packed):
        return tighter, tighter_stored
    return packed, encoded_stored

def _compress_container(blob, mode, target_stored=None, exact_stored=False,
                        optimal=False):
    """Compress a container, retaining its original stored-size envelope."""
    from . import slz_compress
    try:
        packed = bytearray(slz_compress.compress(
            bytes(blob), mode=mode, optimal=optimal,
            target_size=target_stored if exact_stored else None))
    except ValueError:
        if not exact_stored:
            raise
        packed = bytearray(slz_compress.compress(
            bytes(blob), mode=mode, optimal=optimal))
    encoded_stored = len(packed) - 16
    if target_stored is not None and encoded_stored <= target_stored:
        struct.pack_into("<I", packed, 4, target_stored)
        packed += bytes(target_stored - encoded_stored)
    if decompress(bytes(packed)) != bytes(blob):
        raise ValueError("recompressed container does not round-trip")
    return packed, encoded_stored

def pack_container_entry(raw, blob, resource, subresource=None):
    """Put a rebuilt container stream back into its allocated entry."""
    raw = bytes(raw)
    if raw[:8] == b"mcps2lib":
        if bytes(blob[:8]) != b"mcps2lib":
            raise ValueError("resource #%d rebuilt to a non-MCPS2 payload" %
                             resource)
        before = min(struct.unpack_from("<I", raw, 0x20)[0], len(raw))
        after = min(struct.unpack_from("<I", blob, 0x20)[0], len(blob))
        return bytes(blob), {
            "wrapper": "MCPS2",
            "stored_before": before,
            "stored_after": after,
        }
    section = _pk1_container_section(raw, subresource)
    if section is not None:
        return _pack_pk1_slz(raw, blob, resource, section)
    at = container_stream_offset(raw)
    if raw[:3] == b"SLZ":
        try:
            inner = decompress(raw)
        except Exception:
            inner = None
        if inner is not None and inner[:4] == b"p@Ck":
            return _pack_packaged_slz(raw, inner, blob, resource)
    if at == 0 and raw[:3] == b"SLZ":
        return _pack_bare_slz(raw, blob, resource)
    if at is not None and at >= 0x10 and raw[at - 0x10:at - 0x10 + 4] == b"ZLS\0" \
            and raw[at:at + 3] == b"SLZ":
        return _pack_zls_stream(raw, blob, resource, at - 0x10)
    if at is not None and at > 0 and raw[at:at + 3] == b"SLZ":
        return _pack_inline_slz(raw, blob, resource, at)
    try:
        return package_archive.pack_container(raw, blob)
    except package_archive.ContainerNotFound:
        pass
    try:
        clear, protected_layout = protected_package.decode_entry(raw)
        rebuilt_clear, details = package_archive.pack_container(clear, blob)
        rebuilt = protected_package.encode_entry(
            raw, rebuilt_clear, protected_layout)
        if unpack_container_entry(rebuilt, resource) != bytes(blob):
            raise ValueError(
                "resource #%d protected package does not read back "
                "byte-for-byte" % resource)
        return rebuilt, {
            **details,
            "wrapper": "protected-p@Ck",
            "protected_seed": protected_layout.seed,
            "protected_payload_start": protected_layout.payload_start,
            "protected_payload_end": protected_layout.payload_end,
        }
    except (protected_package.ProtectedPackageError,
            package_archive.ContainerNotFound):
        pass
    if encrypted_entry.is_encrypted(resource):
        clear = encrypted_entry.decode_entry(raw, resource)
        rebuilt_clear, details = pack_container_entry(
            clear, blob, resource, subresource)
        if len(rebuilt_clear) != len(raw):
            raise ValueError(
                "resource #%d changed length under its keystream: %d to %d"
                % (resource, len(raw), len(rebuilt_clear)))
        rebuilt = encrypted_entry.encode_entry(rebuilt_clear, resource)
        if unpack_container_entry(rebuilt, resource, subresource) != bytes(blob):
            raise ValueError(
                "resource #%d encrypted entry does not read back "
                "byte-for-byte" % resource)
        return rebuilt, {**details,
                         "wrapper": "encrypted " + details["wrapper"]}
    raise ValueError(
        "resource #%d keeps its container at %s, neither a bare first "
        "MCPS2/SLZ stream nor an SLZ wrapped in a ZLS header; the packer can "
        "only rebuild those layouts" %
        (resource, "no readable offset" if at is None else "0x%X" % at))

def _pack_pk1_slz(raw, blob, resource, section):
    """Rewrite one structurally identified MCPS2 subresource in place."""
    if section["wrapper"] != "SLZ":
        raise ValueError(
            "resource #%d keeps its PK1 container in %s; writing that "
            "compression is not supported" % (resource, section["wrapper"]))
    packed = section["packed"]
    old_stored = struct.unpack_from("<I", packed, 4)[0]
    rebuilt_packed, encoded_stored = _compress_container(
        blob, packed[3], target_stored=old_stored)
    start = section["offset"]
    if len(rebuilt_packed) <= section["length"]:
        rebuilt = bytearray(raw)
        rebuilt[start:start + len(rebuilt_packed)] = rebuilt_packed
        new_offset = start
        new_length = section["length"]
    else:
        from .pk1_archive import repack_pk1_subresource
        rebuilt = bytearray(repack_pk1_subresource(
            raw, section["tag"], rebuilt_packed,
            target_offset=start))
        _, new_offset, new_length = parse_pk1(bytes(rebuilt))[section["number"]]
    written = _pk1_container_section(bytes(rebuilt), section["number"])
    if written is None or written["blob"] != bytes(blob):
        raise ValueError("resource #%d PK1 row %d does not read back "
                         "byte-for-byte" % (resource, section["number"]))
    return bytes(rebuilt), {
        "wrapper": "PK1/SLZ",
        "subresource_tag": section["tag"],
        "subresource_offset_before": start,
        "subresource_offset_after": new_offset,
        "subresource_length_before": section["length"],
        "subresource_length_after": new_length,
        "stored_before": old_stored,
        "stored_after": struct.unpack_from("<I", rebuilt_packed, 4)[0],
        "encoded_after": encoded_stored,
        "subresource_growth": new_length - section["length"],
    }

def _pack_inline_slz(raw, blob, resource, container_offset):
    """Rewrite a container SLZ placed past the entry's leading stream(s)."""
    old_stored = struct.unpack_from("<I", raw, container_offset + 4)[0]
    packed, encoded_stored = _compress_container(
        blob, raw[container_offset + 3], target_stored=old_stored)
    next_offset = struct.unpack_from("<I", raw, container_offset + 0x0C)[0]
    if next_offset:
        suffix_at = container_offset + next_offset
        if not container_offset + 0x10 + old_stored <= suffix_at <= len(raw):
            raise ValueError("resource #%d has an invalid inline SLZ chain" %
                             resource)
        packed, encoded_stored = _tighten(
            packed, encoded_stored, blob, raw[container_offset + 3],
            old_stored, False, next_offset)
        suffix = raw[suffix_at:]
        used = len(suffix.rstrip(bytes(1)))
        new_next = next_offset
        if len(packed) > next_offset:
            new_next += _round_up(len(packed) - next_offset, 16)
        if container_offset + new_next + used > len(raw):
            raise ValueError(
                "resource #%d needs %d more compressed bytes but its inline "
                "stream chain has only %d bytes of trailing slack" %
                (resource, new_next - next_offset,
                 len(raw) - suffix_at - used))
        packed = bytearray(packed)
        struct.pack_into("<I", packed, 0x0C, new_next)
        rebuilt = bytearray(len(raw))
        rebuilt[:container_offset] = raw[:container_offset]
        rebuilt[container_offset:container_offset + len(packed)] = packed
        new_suffix_at = container_offset + new_next
        rebuilt[new_suffix_at:new_suffix_at + len(suffix)] = suffix[
            :len(rebuilt) - new_suffix_at]
        if any(suffix[len(rebuilt) - new_suffix_at:]):
            raise ValueError("shifting resource #%d would truncate its inline "
                             "stream chain" % resource)
    else:
        new_next = 0
        new_total = container_offset + len(packed)
        if new_total > len(raw):
            raise ValueError(
                "resource #%d container needs %d more compressed bytes but "
                "its entry has only %d bytes total" %
                (resource, new_total - len(raw), len(raw)))
        rebuilt = bytearray(len(raw))
        rebuilt[:container_offset] = raw[:container_offset]
        rebuilt[container_offset:new_total] = packed
    details = {
        "wrapper": "inline-SLZ",
        "stored_before": old_stored,
        "stored_after": len(packed) - 0x10,
        "encoded_after": encoded_stored,
        "suffix_shift": max(new_next - next_offset, 0),
    }
    check = unpack_container_entry(bytes(rebuilt), resource)
    if check != bytes(blob):
        raise ValueError("resource #%d packed entry does not read back "
                         "byte-for-byte" % resource)
    return bytes(rebuilt), details

def _pack_packaged_slz(raw, inner, blob, resource):
    rebuilt_inner, details = package_archive.pack_container(
        inner, blob, absorb_growth=True)
    packed, _stored = _compress_container(rebuilt_inner, raw[3])
    if len(packed) > len(raw):
        raise ValueError(
            "resource #%d rebuilt to %d compressed bytes and its entry holds "
            "%d" % (resource, len(packed), len(raw)))
    rebuilt = bytearray(len(raw))
    rebuilt[:len(packed)] = packed
    return bytes(rebuilt), {
        **details,
        "wrapper": "SLZ p@Ck",
        "compressed_before": len(raw),
        "compressed_after": len(packed),
    }


def _pack_bare_slz(raw, blob, resource):
    """Rewrite a bare SLZ stream, the entry's first stream."""
    old_stored = struct.unpack_from("<I", raw, 4)[0]
    packed, encoded_stored = _compress_container(
        blob, raw[3], target_stored=old_stored)
    next_offset = struct.unpack_from("<I", raw, 0x0C)[0]
    packed, encoded_stored = _tighten(
        packed, encoded_stored, blob, raw[3], old_stored, False,
        next_offset if next_offset else None)
    if next_offset:
        suffix = raw[next_offset:]
        used = len(suffix.rstrip(bytes(1)))
        new_next = next_offset
        if len(packed) > next_offset:
            new_next += _round_up(len(packed) - next_offset, 16)
        if new_next + used > len(raw):
            raise ValueError(
                "resource #%d needs %d more compressed bytes but its "
                "stream chain has only %d bytes of trailing slack" %
                (resource, new_next - next_offset,
                 len(raw) - next_offset - used))
        struct.pack_into("<I", packed, 0x0C, new_next)
        rebuilt = bytearray(len(raw))
        rebuilt[:len(packed)] = packed
        rebuilt[new_next:new_next + len(suffix)] = suffix[
            :len(rebuilt) - new_next]
        if any(suffix[len(rebuilt) - new_next:]):
            raise ValueError("shifting resource #%d would truncate its "
                             "stream chain" % resource)
    else:
        if len(packed) > len(raw):
            raise ValueError(
                "resource #%d container needs %d bytes but its entry has "
                "%d" % (resource, len(packed), len(raw)))
        new_next = 0
        rebuilt = bytearray(len(raw))
        rebuilt[:len(packed)] = packed
    details = {
        "wrapper": "SLZ", "stored_before": old_stored,
        "stored_after": len(packed) - 16,
        "encoded_after": encoded_stored,
        "suffix_shift": max(new_next - next_offset, 0),
    }
    check = unpack_container_entry(bytes(rebuilt), resource)
    if check != bytes(blob):
        raise ValueError("resource #%d packed entry does not read back "
                         "byte-for-byte" % resource)
    return bytes(rebuilt), details

def _pack_zls_stream(raw, blob, resource, base):
    """Rewrite an SLZ stream wrapped in a ZLS header at ``base``."""
    old_size = struct.unpack_from("<I", raw, base + 4)[0]
    packed, encoded_stored = _compress_container(
        blob, raw[base + 0x13], target_stored=max(old_size - 16, 0),
        exact_stored=True)
    old_span = struct.unpack_from("<I", raw, base + 0x0C)[0]
    packed, encoded_stored = _tighten(
        packed, encoded_stored, blob, raw[base + 0x13],
        max(old_size - 16, 0), True, max(old_span - 0x10, 0))
    suffix_at = base + old_span
    if not base + 0x10 + old_size <= suffix_at <= len(raw):
        raise ValueError("resource #%d has an invalid ZLS span" % resource)
    new_span = max(old_span, _round_up(0x10 + len(packed), 128))
    suffix = raw[suffix_at:]
    used = len(suffix.rstrip(bytes(1)))
    size = len(raw) + _round_up(max(base + new_span + used - len(raw), 0), SECTOR)
    rebuilt = bytearray(size)
    rebuilt[:base + 0x10] = raw[:base + 0x10]
    struct.pack_into("<I", rebuilt, base + 4, len(packed))
    struct.pack_into("<I", rebuilt, base + 0x0C, new_span)
    rebuilt[base + 0x10:base + 0x10 + len(packed)] = packed
    new_suffix = base + new_span
    rebuilt[new_suffix:new_suffix + len(suffix)] = suffix[
        :len(rebuilt) - new_suffix]
    if any(suffix[len(rebuilt) - new_suffix:]):
        raise ValueError("shifting resource #%d would truncate its ZLS "
                         "stream suffix" % resource)
    details = {
        "wrapper": "ZLS", "stored_before": old_size,
        "stored_after": len(packed),
        "encoded_after": encoded_stored,
        "suffix_shift": new_span - old_span,
    }
    if size > len(raw):
        details["grown_sectors"] = (size - len(raw)) // SECTOR
    check = unpack_container_entry(bytes(rebuilt), resource)
    if check != bytes(blob):
        raise ValueError("resource #%d packed entry does not read back "
                         "byte-for-byte" % resource)
    return bytes(rebuilt), details
