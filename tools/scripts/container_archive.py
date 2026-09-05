"""Container stream discovery, compression, and framing."""

import functools
import struct
import sys

from . import package_archive
from . import protected_package
from .slz import decompress
from .vp2_dcms import parse_pk1, read_entry


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

def unpack_container_entry(raw, resource, subresource=None):
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

def trace_slz_origins(packed):
    """Decompress mode 1/2 SLZ and name each output byte's stored source."""
    if packed[:3] != b"SLZ" or len(packed) < 0x10:
        raise ValueError("literal tracing needs an SLZ stream")
    mode = packed[3]
    if mode not in (1, 2):
        raise ValueError("literal tracing supports SLZ modes 1 and 2")
    stored = struct.unpack_from("<I", packed, 4)[0]
    out_size = struct.unpack_from("<I", packed, 8)[0]
    if len(packed) < 0x10 + stored:
        raise ValueError("SLZ stream is shorter than its stored size")

    output = bytearray(out_size)
    origins = []
    source, target, flags = 0x10, 0, 0
    while target < out_size:
        flags >>= 1
        if flags <= 0xFFFF:
            if source >= 0x10 + stored:
                raise ValueError("SLZ control byte exceeds the stored stream")
            flags = 0x00FF0000 | packed[source]
            source += 1
        if flags & 1:
            if source >= 0x10 + stored:
                raise ValueError("SLZ literal exceeds the stored stream")
            output[target] = packed[source]
            origins.append(source)
            source += 1
            target += 1
            continue

        if source + 2 > 0x10 + stored:
            raise ValueError("SLZ match exceeds the stored stream")
        first_at = source
        first, second = packed[source], packed[source + 1]
        source += 2
        if mode == 2 and second >= 0xF0:
            if second > 0xF0:
                length = (second & 0x0F) + 3
                fill, fill_at = first, first_at
            else:
                if source >= 0x10 + stored:
                    raise ValueError("SLZ long run exceeds the stored stream")
                length = first + 0x13
                fill, fill_at = packed[source], source
                source += 1
            if target + length > out_size:
                raise ValueError("SLZ run exceeds the output size")
            for _ in range(length):
                output[target] = fill
                origins.append(fill_at)
                target += 1
            continue

        distance = first | ((second & 0x0F) << 8)
        length = (second >> 4) + 3
        copied = target - distance
        if distance <= 0 or copied < 0 or target + length > out_size:
            raise ValueError("invalid SLZ back-reference")
        for _ in range(length):
            output[target] = output[copied]
            origins.append(origins[copied])
            target += 1
            copied += 1

    if bytes(output) != decompress(packed):
        raise ValueError("SLZ provenance trace disagrees with the decoder")
    return bytes(output), origins

def patch_slz_literal_source(packed, positions, before, after):
    """Change literal sources only if their complete effect is in *positions*."""
    positions = set(positions)
    if not positions:
        raise ValueError("literal-source patch needs output positions")
    output, origins = trace_slz_origins(packed)
    if any(not 0 <= position < len(output) for position in positions):
        raise ValueError("literal-source output position is out of range")
    if any(output[position] != before for position in positions):
        raise ValueError("literal-source patch does not match expected output")
    source_offsets = {origins[position] for position in positions}
    affected = {position for position, origin in enumerate(origins)
                if origin in source_offsets}
    if affected != positions:
        raise ValueError(
            "literal source is also used outside the requested positions")
    if any(packed[offset] != before for offset in source_offsets):
        raise ValueError("output is not backed by the expected literal byte")

    rebuilt = bytearray(packed)
    for offset in source_offsets:
        rebuilt[offset] = after
    expected = bytearray(output)
    for position in positions:
        expected[position] = after
    if decompress(bytes(rebuilt)) != bytes(expected):
        raise ValueError("literal-source patch changed unexpected output")
    return bytes(rebuilt), sorted(source_offsets)

def patch_643_literal_probe(raw):
    """Turn the three USA `Party` labels into `Tarty` without recompression."""
    raw = bytes(raw)
    if raw[:4] != b"ZLS\0" or raw[0x10:0x13] != b"SLZ":
        raise ValueError("resource #643 is not the expected ZLS/SLZ entry")
    outer_size = struct.unpack_from("<I", raw, 4)[0]
    span = struct.unpack_from("<I", raw, 0x0C)[0]
    if not 0x10 <= outer_size <= span or 0x10 + span > len(raw):
        raise ValueError("resource #643 has an invalid ZLS layout")
    packed = raw[0x10:0x10 + outer_size]
    blob = unpack_container_entry(raw, 643)
    meta, messages = read_messages(blob, 643)
    wanted_ids = (6, 15, 23)
    by_id = {message["message_id"]: message for message in messages}
    if any(message_id not in by_id for message_id in wanted_ids):
        raise ValueError("resource #643 is missing a Party probe record")
    selected = [by_id[message_id] for message_id in wanted_ids]
    if any(message["kind"] != "codepage" or
           message["original_en"] != "Party" or
           message["byte_length"] != 6 for message in selected):
        raise ValueError("resource #643 Party records do not match the USA bank")

    positions = [meta["text_start"] + message["offset"]
                 for message in selected]
    before = encode_codepage("P")[0]
    after = encode_codepage("T")[0]
    rebuilt_packed, source_offsets = patch_slz_literal_source(
        packed, positions, before, after)
    rebuilt = bytearray(raw)
    rebuilt[0x10:0x10 + outer_size] = rebuilt_packed

    changed = [index for index, pair in enumerate(zip(raw, rebuilt))
               if pair[0] != pair[1]]
    expected_changed = [0x10 + offset for offset in source_offsets]
    if changed != expected_changed or len(changed) != 1:
        raise ValueError("resource #643 probe did not produce one raw-byte edit")
    _, checked = read_messages(unpack_container_entry(bytes(rebuilt), 643), 643)
    checked_by_id = {message["message_id"]: message for message in checked}
    if any(checked_by_id[message_id]["original_en"] != "Tarty"
           for message_id in wanted_ids):
        raise ValueError("resource #643 probe did not decode as Tarty")
    return bytes(rebuilt), {
        "resource": 643,
        "message_ids": wanted_ids,
        "output_positions": tuple(positions),
        "entry_byte_offset": changed[0],
        "slz_byte_offset": source_offsets[0],
        "outer_size": outer_size,
        "span": span,
    }

def _decode_slz_group(raw, history, mode, token_count):
    """Decode one stored eight-token group against an existing output prefix."""
    output = bytearray(history)
    start = len(output)
    source = 1
    flags = raw[0]
    for bit in range(token_count):
        if flags & (1 << bit):
            if source >= len(raw):
                raise ValueError("SLZ group ends inside a literal")
            output.append(raw[source])
            source += 1
            continue
        if source + 2 > len(raw):
            raise ValueError("SLZ group ends inside a match")
        first, second = raw[source], raw[source + 1]
        source += 2
        if mode == 2 and second >= 0xF0:
            if second > 0xF0:
                length = (second & 0x0F) + 3
                fill = first
            else:
                if source >= len(raw):
                    raise ValueError("SLZ group ends inside a long run")
                length = first + 0x13
                fill = raw[source]
                source += 1
            output.extend(bytes([fill]) * length)
            continue
        distance = first | ((second & 0x0F) << 8)
        length = (second >> 4) + 3
        copied = len(output) - distance
        if distance <= 0 or copied < 0:
            raise ValueError("invalid SLZ group back-reference")
        for _ in range(length):
            output.append(output[copied])
            copied += 1
    if source != len(raw):
        raise ValueError("SLZ group has unconsumed payload bytes")
    return bytes(output[start:])

def _slz_groups(packed):
    """Return the original compressed and output boundaries of each group."""
    if packed[:3] != b"SLZ" or packed[3] not in (1, 2):
        raise ValueError("group-preserving rewrite needs mode 1/2 SLZ")
    stored = struct.unpack_from("<I", packed, 4)[0]
    out_size = struct.unpack_from("<I", packed, 8)[0]
    if len(packed) != 0x10 + stored:
        raise ValueError("group-preserving rewrite needs the exact SLZ body")
    mode = packed[3]
    source = 0x10
    output = bytearray()
    groups = []
    while len(output) < out_size:
        group_start = source
        output_start = len(output)
        if source >= len(packed):
            raise ValueError("SLZ ends before its declared output")
        flags = packed[source]
        source += 1
        token_count = 0
        for bit in range(8):
            if len(output) >= out_size:
                break
            token_count += 1
            if flags & (1 << bit):
                if source >= len(packed):
                    raise ValueError("SLZ ends inside a literal")
                output.append(packed[source])
                source += 1
                continue
            if source + 2 > len(packed):
                raise ValueError("SLZ ends inside a match")
            first, second = packed[source], packed[source + 1]
            source += 2
            if mode == 2 and second >= 0xF0:
                if second > 0xF0:
                    length = (second & 0x0F) + 3
                    fill = first
                else:
                    if source >= len(packed):
                        raise ValueError("SLZ ends inside a long run")
                    length = first + 0x13
                    fill = packed[source]
                    source += 1
                if len(output) + length > out_size:
                    raise ValueError("SLZ run exceeds the declared output")
                output.extend(bytes([fill]) * length)
                continue
            distance = first | ((second & 0x0F) << 8)
            length = (second >> 4) + 3
            copied = len(output) - distance
            if (distance <= 0 or copied < 0 or
                    len(output) + length > out_size):
                raise ValueError("invalid SLZ back-reference")
            for _ in range(length):
                output.append(output[copied])
                copied += 1
        groups.append({
            "raw": packed[group_start:source],
            "compressed_start": group_start,
            "compressed_end": source,
            "output_start": output_start,
            "output_end": len(output),
            "token_count": token_count,
        })
    if source != len(packed):
        raise ValueError("SLZ body has bytes after its final token group")
    if bytes(output) != decompress(packed):
        raise ValueError("SLZ group parser disagrees with the decoder")
    return bytes(output), groups

def _encode_slz_window(full_output, groups, mode):
    """Encode one complete group window with identical token/byte budgets."""
    start = groups[0]["output_start"]
    end = groups[-1]["output_end"]
    token_count = sum(group["token_count"] for group in groups)
    payload_budget = (sum(len(group["raw"]) for group in groups) -
                      len(groups))
    group_counts = [group["token_count"] for group in groups]

    options = {}
    for position in range(start, end):
        max_length = min(17 if mode == 2 else 18, end - position)
        matches = {}
        for distance in range(1, min(4095, position) + 1):
            length = 0
            while (length < max_length and
                   full_output[position + length] ==
                   full_output[position - distance + length]):
                length += 1
            for usable in range(3, length + 1):
                matches.setdefault(usable, distance)
        run = 1
        if mode == 2:
            while (position + run < end and run < 0xFF + 0x13 and
                   full_output[position + run] == full_output[position]):
                run += 1
        options[position] = matches, run

    memo = {}
    missing = object()

    def solve(position, tokens_left, payload_left):
        key = (position, tokens_left, payload_left)
        found = memo.get(key, missing)
        if found is not missing:
            return found
        memo[key] = found = _search(position, tokens_left, payload_left)
        return found

    def _search(position, tokens_left, payload_left):
        if tokens_left == 0:
            return () if position == end and payload_left == 0 else None
        remaining = end - position
        if (remaining < tokens_left or remaining > (0xFF + 0x13) * tokens_left
                or payload_left < tokens_left
                or payload_left > 3 * tokens_left
                or remaining > tokens_left + 273 *
                (payload_left - tokens_left)):
            return None
        matches, run = options[position]
        for length in sorted(matches, reverse=True):
            if payload_left >= 2:
                rest = solve(position + length, tokens_left - 1,
                             payload_left - 2)
                if rest is not None:
                    return (("match", matches[length], length),) + rest
        if mode == 2:
            for length in range(min(run, end - position), 3, -1):
                cost = 2 if length <= 18 else 3
                if payload_left >= cost:
                    rest = solve(position + length, tokens_left - 1,
                                 payload_left - cost)
                    if rest is not None:
                        return (("run", full_output[position], length),) + rest
        if payload_left:
            rest = solve(position + 1, tokens_left - 1, payload_left - 1)
            if rest is not None:
                return (("literal", full_output[position], 1),) + rest
        return None

    limit = sys.getrecursionlimit()
    try:
        needed = token_count * 6 + 1000
        if needed > limit:
            sys.setrecursionlimit(needed)
        tokens = solve(start, token_count, payload_budget)
    finally:
        sys.setrecursionlimit(limit)
    if tokens is None:
        raise ValueError(
            "changed SLZ groups %d-%d cannot retain their original token and "
            "byte budgets" % (groups[0]["number"], groups[-1]["number"]))
    encoded = bytearray()
    token_at = 0
    for count in group_counts:
        selected = tokens[token_at:token_at + count]
        token_at += count
        flags = 0
        body = bytearray()
        for bit, token in enumerate(selected):
            kind, value, length = token
            if kind == "literal":
                flags |= 1 << bit
                body.append(value)
            elif kind == "match":
                body.extend((value & 0xFF,
                             ((value >> 8) & 0x0F) |
                             ((length - 3) << 4)))
            elif length <= 18:
                body.extend((value, 0xF0 | (length - 3)))
            else:
                body.extend((length - 0x13, 0xF0, value))
        encoded.append(flags)
        encoded.extend(body)
    expected_size = sum(len(group["raw"]) for group in groups)
    if len(encoded) != expected_size:
        raise ValueError("group-preserving SLZ rewrite changed the byte budget")
    return bytes(encoded)

def rewrite_slz_preserving_groups(packed, desired_output):
    """Retain every unaffected original group and resynchronize changed ones."""
    original, groups = _slz_groups(packed)
    desired_output = bytes(desired_output)
    if len(desired_output) != len(original):
        raise ValueError("group-preserving SLZ rewrite cannot change output size")
    for number, group in enumerate(groups, 1):
        group["number"] = number
    dirty = []
    for index, group in enumerate(groups):
        rendered = _decode_slz_group(
            group["raw"], desired_output[:group["output_start"]], packed[3],
            group["token_count"])
        expected = desired_output[group["output_start"]:group["output_end"]]
        if rendered != expected:
            dirty.append(index)
    if not dirty:
        return bytes(packed), {"first_group": None, "last_group": None,
                               "changed_bytes": 0}

    first, last = min(dirty), max(dirty)
    window = groups[first:last + 1]
    replacement = _encode_slz_window(desired_output, window, packed[3])
    start = window[0]["compressed_start"]
    end = window[-1]["compressed_end"]
    rebuilt = bytes(packed[:start] + replacement + packed[end:])
    if len(rebuilt) != len(packed):
        raise ValueError("group-preserving SLZ rewrite changed stream size")
    if decompress(rebuilt) != desired_output:
        raise ValueError("group-preserving SLZ rewrite changed unexpected output")
    if rebuilt[:start] != packed[:start] or rebuilt[end:] != packed[end:]:
        raise ValueError("group-preserving SLZ rewrite touched a clean group")
    return rebuilt, {
        "first_group": first + 1,
        "last_group": last + 1,
        "changed_bytes": sum(a != b for a, b in zip(packed, rebuilt)),
    }

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
    group_details = None
    original_packed = raw[base + 0x10:base + 0x10 + old_size]
    original_output_size = struct.unpack_from("<I", original_packed, 8)[0]
    if resource == 643 and len(blob) == original_output_size:
        packed, group_details = rewrite_slz_preserving_groups(
            original_packed, blob)
        packed = bytearray(packed)
        encoded_stored = len(packed) - 16
    else:
        packed, encoded_stored = _compress_container(
            blob, raw[base + 0x13], target_stored=max(old_size - 16, 0),
            exact_stored=True)
    old_span = struct.unpack_from("<I", raw, base + 0x0C)[0]
    if group_details is None:
        # Staying inside the existing span means no following stream moves.
        packed, encoded_stored = _tighten(
            packed, encoded_stored, blob, raw[base + 0x13],
            max(old_size - 16, 0), True, max(old_span - 0x10, 0))
    suffix_at = base + old_span
    if not base + 0x10 + old_size <= suffix_at <= len(raw):
        raise ValueError("resource #%d has an invalid ZLS span" % resource)
    new_span = max(old_span, _round_up(0x10 + len(packed), 128))
    suffix = raw[suffix_at:]
    used = len(suffix.rstrip(bytes(1)))
    if base + new_span + used > len(raw):
        raise ValueError(
            "resource #%d needs %d more compressed bytes but its ZLS "
            "entry has only %d bytes of trailing slack" %
            (resource, new_span - old_span,
             len(raw) - suffix_at - used))
    rebuilt = bytearray(len(raw))
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
    if group_details is not None:
        details["groups"] = group_details
    check = unpack_container_entry(bytes(rebuilt), resource)
    if check != bytes(blob):
        raise ValueError("resource #%d packed entry does not read back "
                         "byte-for-byte" % resource)
    return bytes(rebuilt), details
