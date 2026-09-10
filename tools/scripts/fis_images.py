#!/usr/bin/env python3
r"""Read and write the disc's `FIS` image container."""
import os
import struct
import zlib

from . import encrypted_entry
from . import package_archive
from . import protected_package
from . import sle
from . import slz_compress
from . import triace_ps2_unpack as triace
from .vp2_dcms import parse_pk1, read_entry
from .slz import decompress

MAGIC = b"FIS\0"
HEAD = 0x10
PACKET_AT = 0x90
PSMT8, PSMT4 = 0x13, 0x14
FORMAT_BITS = {PSMT8: 8, PSMT4: 4}
CSM1_4 = list(range(0, 8)) + list(range(16, 24))
EXPANSION_CAP = 32 << 20
MAX_DEPTH = 4


class FisError(ValueError):
    """The bytes are not a FIS item this module can read."""


def _bits(value, low, width):
    return (value >> low) & ((1 << width) - 1)


def clut_extent(item):
    at, shape = PACKET_AT, None
    while at + 16 <= len(item):
        low, high = struct.unpack_from("<QQ", item, at)
        nloop, flg = _bits(low, 0, 15), _bits(low, 58, 2)
        nreg = _bits(low, 60, 4) or 16
        at += 16
        if flg == 2:                                   # IMAGE: the CLUT
            return at, nloop * 16
        if flg == 0:                                   # PACKED registers
            regs = [_bits(high, i * 4, 4) for i in range(nreg)]
            for _ in range(nloop):
                for reg in regs:
                    if at + 16 > len(item):
                        raise FisError("GIF packet runs past the item")
                    dlow, dhigh = struct.unpack_from("<QQ", item, at)
                    at += 16
                    if ((dhigh & 0xFF) if reg == 0x0E else reg) == 0x52:
                        shape = (_bits(dlow, 0, 12), _bits(dlow, 32, 12))
        elif flg == 1:
            at += ((nloop * nreg + 1) // 2) * 16
        else:
            at += nloop * 16
    raise FisError("no CLUT transfer in the GIF packet%s"
                   % ("" if shape is None else " (TRXREG said %sx%s)" % shape))


def descriptor(item):
    """The item's geometry, read from wherever its CLUT block ends."""
    if item[:4] != MAGIC:
        raise FisError("not a FIS item: %r" % item[:4])
    clut_at, clut_size = clut_extent(item)
    at = clut_at + clut_size
    if at + 16 > len(item):
        raise FisError("descriptor at 0x%X is past a %d-byte item"
                       % (at, len(item)))
    draw_width, draw_height = struct.unpack_from("<HH", item, at)
    size, offset = struct.unpack_from("<II", item, at + 4)
    fmt = struct.unpack_from("<H", item, at + 12)[0]
    width, height = 1 << item[at + 14], 1 << item[at + 15]
    if fmt not in FORMAT_BITS:
        raise FisError("unknown pixel format 0x%X" % fmt)
    if not size or offset + size > len(item):
        raise FisError("pixel block %d bytes at %d overruns a %d-byte item"
                       % (size, offset, len(item)))
    return {
        "clut_offset": clut_at, "clut_size": clut_size,
        "descriptor_offset": at,
        "draw_width": draw_width, "draw_height": draw_height,
        "width": width, "height": height,
        "format": fmt, "bits": FORMAT_BITS[fmt],
        "size": size, "offset": offset,
        "measured_bits": size * 8 / (width * height),
    }


def palette(item, meta):
    """The CLUT in index order, de-interleaved for its depth."""
    at = meta["clut_offset"]

    def entry(slot):
        return tuple(item[at + slot * 4: at + slot * 4 + 4])

    if meta["bits"] == 4:
        return [entry(slot) for slot in CSM1_4]
    return [entry((i & 0xE7) | ((i & 0x08) << 1) | ((i & 0x10) >> 1))
            for i in range(256)]


def indices(item, meta):
    block = item[meta["offset"]:meta["offset"] + meta["size"]]
    if meta["bits"] == 8:
        return block
    out = bytearray(len(block) * 2)
    for at, byte in enumerate(block):
        out[at * 2] = byte & 0x0F
        out[at * 2 + 1] = byte >> 4
    return out


def png(path, width, height, rows):
    raw = b"".join(b"\0" + bytes(row) for row in rows)

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body)))

    with open(path, "wb") as out:
        out.write(b"\x89PNG\r\n\x1a\n")
        out.write(chunk(b"IHDR",
                        struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)))
        out.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        out.write(chunk(b"IEND", b""))


def render(item, path):
    meta = descriptor(item)
    colours, pixels = palette(item, meta), indices(item, meta)
    width, height = meta["width"], meta["height"]
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            at = y * width + x
            red, green, blue, alpha = (colours[pixels[at]] if at < len(pixels)
                                       else (0, 0, 0, 0))
            row += [red, green, blue, min(alpha * 2, 255)]
        rows.append(row)
    png(path, width, height, rows)
    return meta


def items_in(blob):
    out, at = [], 0
    while True:
        at = blob.find(MAGIC, at)
        if at < 0:
            return out
        if at + 8 <= len(blob):
            declared = struct.unpack_from("<I", blob, at + 4)[0]
            if 0 < declared and at + HEAD + declared <= len(blob):
                out.append((at, bytes(blob[at:at + HEAD + declared])))
        at += 4


def _slz_streams(blob):
    at = 0
    while at <= len(blob) - 16:
        if blob[at:at + 3] == b"SLZ" and blob[at + 3] < 4:
            expanded = struct.unpack_from("<I", blob, at + 12)[0]
            if expanded < EXPANSION_CAP:
                try:
                    yield "slz@0x%X" % at, decompress(bytes(blob[at:]))
                except Exception:                                # noqa: BLE001
                    pass
        at += 1


def _package_items(blob):
    """Every item of a ``p@Ck``, decompressed where it is a stream."""
    try:
        parsed = package_archive.layout(bytes(blob))
    except Exception:                                            # noqa: BLE001
        return
    for index in range(parsed.count):
        start, end = parsed.offsets[index:index + 2]
        item = bytes(blob[start:end])
        if not item:
            continue
        yield "item%d" % index, item
        if item[:3] == b"SLZ" and item[3] < 4:
            try:
                yield "item%d.slz" % index, decompress(item)
            except Exception:                                    # noqa: BLE001
                pass


def _pk1_rows(blob):
    try:
        rows = parse_pk1(bytes(blob))
    except Exception:                                            # noqa: BLE001
        return
    for number, (tag, offset, length) in enumerate(rows):
        packed = bytes(blob[offset:offset + length])
        if len(packed) < 16:
            continue
        try:
            if packed[:3] == b"SLZ" and packed[3] < 4:
                yield "pk1[%d]%s" % (number, tag), decompress(packed)
            elif packed[:3] == b"SLE":
                yield "pk1[%d]%s" % (number, tag), sle.decompress(packed)
        except Exception:                                        # noqa: BLE001
            continue


def payloads(raw, resource=None):
    seen, out = set(), []
    queue = [("raw", bytes(raw), 0)]
    if resource is not None and encrypted_entry.is_encrypted(resource):
        try:
            queue.append(("decrypted",
                          encrypted_entry.crypt(bytes(raw), resource), 0))
        except Exception:                                        # noqa: BLE001
            pass
    try:
        clear, _parsed = protected_package.decode_entry(bytes(raw))
        queue.append(("unprotected", clear, 0))
    except Exception:                                            # noqa: BLE001
        pass

    while queue:
        where, blob, depth = queue.pop(0)
        digest = hash(blob)
        if digest in seen or not blob:
            continue
        seen.add(digest)
        out.append((where, blob))
        if depth >= MAX_DEPTH:
            continue
        for producer in (_slz_streams, _package_items, _pk1_rows):
            for label, nested in producer(blob):
                if nested and hash(nested) not in seen:
                    queue.append(("%s/%s" % (where, label) if where != "raw"
                                  else label, nested, depth + 1))
    return out


def read_png(path):
    with open(path, "rb") as handle:
        blob = handle.read()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise FisError("%s is not a PNG" % path)
    width = height = None
    data, at = bytearray(), 8
    while at + 8 <= len(blob):
        length, tag = struct.unpack_from(">I4s", blob, at)
        body = blob[at + 8:at + 8 + length]
        at += 12 + length
        if tag == b"IHDR":
            width, height, depth, colour, _c, _f, interlace = \
                struct.unpack(">2I5B", body)
            if (depth, colour, interlace) != (8, 6, 0):
                raise FisError(
                    "%s is depth %d colour type %d interlace %d; this reads "
                    "only 8-bit RGBA, uninterlaced" % (path, depth, colour,
                                                       interlace))
        elif tag == b"IDAT":
            data += body
        elif tag == b"IEND":
            break
    if width is None:
        raise FisError("%s has no IHDR" % path)
    raw = zlib.decompress(bytes(data))
    stride = width * 4
    if len(raw) != (stride + 1) * height:
        raise FisError("%s: %d bytes of pixel data, expected %d"
                       % (path, len(raw), (stride + 1) * height))
    rows, previous = [], bytearray(stride)
    for y in range(height):
        head = y * (stride + 1)
        filt = raw[head]
        line = bytearray(raw[head + 1:head + 1 + stride])
        for x in range(stride):
            left = line[x - 4] if x >= 4 else 0
            up = previous[x]
            upleft = previous[x - 4] if x >= 4 else 0
            if filt == 1:
                line[x] = (line[x] + left) & 0xFF
            elif filt == 2:
                line[x] = (line[x] + up) & 0xFF
            elif filt == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif filt == 4:
                estimate = left + up - upleft
                da, db, dc = (abs(estimate - left), abs(estimate - up),
                              abs(estimate - upleft))
                nearest = left if (da <= db and da <= dc) else (
                    up if db <= dc else upleft)
                line[x] = (line[x] + nearest) & 0xFF
            elif filt != 0:
                raise FisError("%s row %d uses filter %d" % (path, y, filt))
        rows.append(line)
        previous = line
    return width, height, rows


def encode(item, path):
    meta = descriptor(item)
    width, height, rows = read_png(path)
    if (width, height) != (meta["width"], meta["height"]):
        raise FisError("%s is %dx%d; the item is %dx%d"
                       % (path, width, height, meta["width"], meta["height"]))
    colours = palette(item, meta)
    exact = {}
    for index, (red, green, blue, alpha) in enumerate(colours):
        exact.setdefault((red, green, blue, min(alpha * 2, 255)), index)

    def nearest(pixel):
        best, score = 0, None
        for index, (red, green, blue, alpha) in enumerate(colours):
            here = ((pixel[0] - red) ** 2 + (pixel[1] - green) ** 2
                    + (pixel[2] - blue) ** 2
                    + (pixel[3] - min(alpha * 2, 255)) ** 2)
            if score is None or here < score:
                best, score = index, here
        if score > 4 * 96 * 96:
            raise FisError(
                "no palette entry is near rgba%s; this item draws only its "
                "own 16 colours, so paint with them" % (pixel,))
        return best

    values = bytearray(width * height)
    approximated = 0
    for y in range(height):
        line = rows[y]
        for x in range(width):
            pixel = tuple(line[x * 4:x * 4 + 4])
            index = exact.get(pixel)
            if index is None:
                index = nearest(pixel)
                approximated += 1
            values[y * width + x] = index

    block = bytearray(meta["size"])
    if meta["bits"] == 8:
        block[:] = values
    else:
        for at in range(len(block)):
            block[at] = values[at * 2] | (values[at * 2 + 1] << 4)
    out = bytearray(item)
    out[meta["offset"]:meta["offset"] + meta["size"]] = block
    if len(out) != len(item):
        raise FisError("the item changed length")
    return bytes(out), approximated


PACK_DIRECTORY = "images"


def pack_name(resource, route, offset):
    safe = "".join(c if c.isalnum() else "-" for c in route).strip("-")
    return "fis-%04d-%s-%X.png" % (int(resource), safe, offset)


def _put_slz(blob, at, packed):
    room = len(blob) - at
    for other, _data in _slz_streams(blob):
        start = int(other.split("@")[1], 16)
        if at < start < at + room:
            room = start - at
    if len(packed) > room:
        raise FisError(
            "the rebuilt stream is %d bytes and has room for %d; a texture "
            "keeps its own length, so this is the compressor, not the picture"
            % (len(packed), room))
    out = bytearray(blob)
    out[at:at + len(packed)] = packed
    return bytes(out)


def _rewrite(blob, label, child):
    """Put a modified *child* back into the blob it was unwrapped from."""
    if label.startswith("slz@"):
        at = int(label.split("@")[1], 16)
        return _put_slz(blob, at, slz_compress.compress(bytes(child), mode=2))
    if label.startswith("item") and label.endswith(".slz"):
        index = int(label[4:-4])
        parsed = package_archive.layout(bytes(blob))
        start, end = parsed.offsets[index:index + 2]
        packed = slz_compress.compress(bytes(child), mode=2)
        if len(packed) > end - start:
            raise FisError("the rebuilt package item does not fit")
        out = bytearray(blob)
        out[start:start + len(packed)] = packed
        return bytes(out)
    if label.startswith("item"):
        index = int(label[4:])
        parsed = package_archive.layout(bytes(blob))
        start, end = parsed.offsets[index:index + 2]
        if len(child) != end - start:
            raise FisError("the rebuilt package item changed length")
        out = bytearray(blob)
        out[start:end] = child
        return bytes(out)
    raise FisError("no way to write back through %r" % label)


def views(raw, resource=None):
    def identity(blob):
        return bytes(blob)

    seen, out = set(), []
    queue = [("raw", bytes(raw), 0, identity)]

    if resource is not None and encrypted_entry.is_encrypted(resource):
        try:
            clear = encrypted_entry.crypt(bytes(raw), resource)
            queue.append(("decrypted", clear, 0,
                          lambda blob: encrypted_entry.crypt(bytes(blob),
                                                             resource)))
        except Exception:                                        # noqa: BLE001
            pass
    try:
        clear, parsed = protected_package.decode_entry(bytes(raw))
        queue.append(("unprotected", clear, 0,
                      lambda blob, _p=parsed: protected_package.encode_entry(
                          bytes(raw), bytes(blob), _p)))
    except Exception:                                            # noqa: BLE001
        pass

    while queue:
        where, blob, depth, write = queue.pop(0)
        digest = hash(blob)
        if digest in seen or not blob:
            continue
        seen.add(digest)
        out.append((where, blob, write))
        if depth >= MAX_DEPTH:
            continue
        for producer in (_slz_streams, _package_items, _pk1_rows):
            for label, nested in producer(blob):
                if not nested or hash(nested) in seen:
                    continue
                route = ("%s/%s" % (where, label) if where != "raw" else label)
                queue.append((route, nested, depth + 1,
                              lambda new, _b=blob, _l=label, _w=write:
                              _w(_rewrite(_b, _l, new))))
    return out


def pack_files(folder, resource):
    """The pack's PNGs for one resource."""
    prefix = "fis-%04d-" % int(resource)
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder)
                  if name.startswith(prefix) and name.endswith(".png"))


def _by_shape(current, resource, folder, wanted, applied):
    for name in wanted:
        path = os.path.join(folder, name)
        try:
            width, height, _rows = read_png(path)
        except FisError:
            continue
        matches = []
        for route, blob, write in views(current, resource):
            for at, item in items_in(blob):
                try:
                    meta = descriptor(item)
                except FisError:
                    continue
                if meta["width"] != meta["draw_width"]:
                    continue
                if (meta["width"], meta["height"]) == (width, height):
                    matches.append((route, at, item, blob, write))
        if len(matches) != 1:
            continue
        route, at, item, blob, write = matches[0]
        try:
            built, _approximated = encode(item, path)
        except FisError:
            continue
        if built == item:
            applied.append((name, 0))
            continue
        patched = bytearray(blob)
        patched[at:at + len(built)] = built
        current = write(bytes(patched))
        changed = sum(1 for a, b in zip(item, built) if a != b)
        applied.append((name, changed))
    return current


def apply_pack(raw, resource, folder):
    applied, current = [], bytes(raw)
    while True:
        progressed = False
        for route, blob, write in views(current, resource):
            for at, item in items_in(blob):
                try:
                    descriptor(item)
                except FisError:
                    continue
                name = pack_name(resource, route, at)
                if name in {done for done, _n in applied}:
                    continue
                path = os.path.join(folder, name)
                if not os.path.exists(path):
                    continue
                built, _approximated = encode(item, path)
                if built == item:
                    applied.append((name, 0))
                    continue
                patched = bytearray(blob)
                patched[at:at + len(built)] = built
                current = write(bytes(patched))
                changed = sum(1 for a, b in zip(item, built) if a != b)
                applied.append((name, changed))
                progressed = True
                break
            if progressed:
                break
        if not progressed:
            break
    placed = {name for name, _count in applied}
    left = [name for name in pack_files(folder, resource) if name not in placed]
    if left:
        current = _by_shape(current, resource, folder, left, applied)
    placed = {name for name, _count in applied}
    for name in pack_files(folder, resource):
        if name not in placed:
            applied.append((name, None))
    return current, applied
