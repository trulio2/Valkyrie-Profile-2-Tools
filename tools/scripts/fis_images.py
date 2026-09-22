#!/usr/bin/env python3
r"""Read and write the disc's `FIS` image container."""
import functools
import json
import os
import struct
import zlib

from . import encrypted_entry
from . import gs_memory
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
PSMCT32, PSMT8, PSMT4 = 0x00, 0x13, 0x14
FORMAT_BITS = {PSMT8: 8, PSMT4: 4}
CSM1_4 = list(range(0, 8)) + list(range(16, 24))
EXPANSION_CAP = 32 << 20
MAX_DEPTH = 4


class FisError(ValueError):
    """The bytes are not a FIS item this module can read."""


def _bits(value, low, width):
    return (value >> low) & ((1 << width) - 1)


def _transfer(item, at):
    """``(data offset, data bytes, {register: value})`` of a GIF packet."""
    registers = {}
    while at + 16 <= len(item):
        low, high = struct.unpack_from("<QQ", item, at)
        nloop, flg = _bits(low, 0, 15), _bits(low, 58, 2)
        nreg = _bits(low, 60, 4) or 16
        at += 16
        if flg == 2:
            return at, nloop * 16, registers
        if flg == 0:
            regs = [_bits(high, i * 4, 4) for i in range(nreg)]
            for _ in range(nloop):
                for reg in regs:
                    if at + 16 > len(item):
                        raise FisError("GIF packet runs past the item")
                    dlow, dhigh = struct.unpack_from("<QQ", item, at)
                    at += 16
                    registers[(dhigh & 0xFF) if reg == 0x0E else reg] = dlow
        elif flg == 1:
            at += ((nloop * nreg + 1) // 2) * 16
        else:
            at += nloop * 16
    return None, 0, registers


def clut_extent(item):
    at, size, registers = _transfer(item, PACKET_AT)
    if at is None:
        shape = registers.get(0x52)
        raise FisError("no CLUT transfer in the GIF packet%s"
                       % ("" if shape is None else " (TRXREG said %sx%s)"
                          % (_bits(shape, 0, 12), _bits(shape, 32, 12))))
    return at, size


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


def upload(item, meta):
    """How the pixels reach GS memory: the packet the descriptor points at."""
    at, size, registers = _transfer(item, meta["offset"])
    if at is None or 0x50 not in registers or 0x52 not in registers:
        raise FisError("no pixel upload where the descriptor points (0x%X)"
                       % meta["offset"])
    if at + size > len(item):
        raise FisError("the pixel upload runs %d bytes past the item"
                       % (at + size - len(item)))
    bitblt, region = registers[0x50], registers[0x52]
    return {
        "at": at, "size": size,
        "format": _bits(bitblt, 56, 6), "buffer_width": _bits(bitblt, 46, 6),
        "width": _bits(region, 0, 12), "height": _bits(region, 32, 12),
    }


@functools.lru_cache(maxsize=64)
def _layout(bits, width, height, fmt, upload_width, upload_height, buffer_width):
    """Where each texel's index sits in the uploaded data, or ``None`` for raster."""
    texels = width * height
    if fmt == (PSMT8 if bits == 8 else PSMT4):
        if (upload_width, upload_height) != (width, height):
            raise FisError("the texture is %dx%d and its upload %dx%d"
                           % (width, height, upload_width, upload_height))
        return None
    if fmt != PSMCT32:
        raise FisError("pixel upload format 0x%02X is not one this reads" % fmt)
    units = upload_width * upload_height * 4 * (1 if bits == 8 else 2)
    if units != texels:
        raise FisError("a %dx%d PSMCT32 upload cannot carry a %dx%d %d-bit "
                       "texture" % (upload_width, upload_height, width,
                                    height, bits))
    words_across = max(1, buffer_width)
    owner = {}
    for y in range(upload_height):
        for x in range(upload_width):
            base = gs_memory.word32(x, y, words_across) * 4
            first = (y * upload_width + x) * 4
            for k in range(4):
                owner[base + k] = first + k
    texels_across = 2 * words_across
    out = []
    for v in range(height):
        for u in range(width):
            if bits == 8:
                unit = owner.get(gs_memory.byte8(u, v, texels_across))
            else:
                nibble = gs_memory.nibble4(u, v, texels_across)
                unit = owner.get(nibble >> 1)
                unit = None if unit is None else unit * 2 + (nibble & 1)
            if unit is None:
                raise FisError("texel (%d, %d) reads memory its upload does "
                               "not write" % (u, v))
            out.append(unit)
    if len(set(out)) != texels:
        raise FisError("the upload and the texture do not cover each other")
    return tuple(out)


def _units(item, meta):
    transfer = upload(item, meta)
    layout = _layout(meta["bits"], meta["width"], meta["height"],
                     transfer["format"], transfer["width"],
                     transfer["height"], transfer["buffer_width"])
    texels = meta["width"] * meta["height"]
    room = transfer["size"] * (1 if meta["bits"] == 8 else 2)
    if room < texels:
        raise FisError("the upload carries %d texel(s) of %d" % (room, texels))
    return transfer, (range(texels) if layout is None else layout)


def indices(item, meta):
    """The texture's palette indices in raster order."""
    transfer, units = _units(item, meta)
    data = item[transfer["at"]:transfer["at"] + transfer["size"]]
    if meta["bits"] == 8:
        return bytes(data[unit] for unit in units)
    return bytes((data[unit >> 1] >> (4 * (unit & 1))) & 0x0F for unit in units)


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


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _rgba_row(line, width, colour, depth, palette, alpha):
    """One unfiltered PNG scanline as RGBA bytes."""
    if colour == 6:
        return line
    entries = len(palette) // 3
    out = bytearray(width * 4)
    for x in range(width):
        if depth == 8:
            index = line[x]
        else:
            per = 8 // depth
            shift = 8 - depth * (x % per + 1)
            index = (line[x // per] >> shift) & ((1 << depth) - 1)
        if index >= entries:
            raise FisError("palette index %d has no PLTE entry" % index)
        red, green, blue = palette[index * 3:index * 3 + 3]
        alpha_byte = (alpha[index]
                      if alpha is not None and index < len(alpha) else 255)
        out[x * 4:x * 4 + 4] = bytes((red, green, blue, alpha_byte))
    return out


def read_png(path):
    with open(path, "rb") as handle:
        blob = handle.read()
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise FisError("%s is not a PNG" % path)
    width = height = None
    palette = alpha = None
    data, at = bytearray(), 8
    while at + 8 <= len(blob):
        length, tag = struct.unpack_from(">I4s", blob, at)
        body = blob[at + 8:at + 8 + length]
        at += 12 + length
        if tag == b"IHDR":
            width, height, depth, colour, _c, _f, interlace = \
                struct.unpack(">2I5B", body)
            if interlace or not ((colour == 6 and depth == 8)
                                 or (colour == 3 and depth in (1, 2, 4, 8))):
                raise FisError(
                    "%s is depth %d colour type %d interlace %d; this reads "
                    "8-bit RGBA or an indexed PNG" % (path, depth, colour,
                                                      interlace))
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            alpha = body
        elif tag == b"IDAT":
            data += body
        elif tag == b"IEND":
            break
    if width is None:
        raise FisError("%s has no IHDR" % path)
    if colour == 3 and palette is None:
        raise FisError("%s is indexed but has no PLTE" % path)
    raw = zlib.decompress(bytes(data))
    step = max(1, _CHANNELS[colour] * depth // 8)
    stride = (width * _CHANNELS[colour] * depth + 7) // 8
    if len(raw) != (stride + 1) * height:
        raise FisError("%s: %d bytes of pixel data, expected %d"
                       % (path, len(raw), (stride + 1) * height))
    rows, previous = [], bytearray(stride)
    for y in range(height):
        head = y * (stride + 1)
        filt = raw[head]
        line = bytearray(raw[head + 1:head + 1 + stride])
        for x in range(stride):
            left = line[x - step] if x >= step else 0
            up = previous[x]
            upleft = previous[x - step] if x >= step else 0
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
        rows.append(_rgba_row(line, width, colour, depth, palette, alpha))
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

    clearest = min(range(len(colours)), key=lambda index: colours[index][3])
    memo = {}

    def nearest(pixel):
        cached = memo.get(pixel)
        if cached is not None:
            return cached
        if pixel[3] == 0:
            memo[pixel] = clearest
            return clearest
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
        memo[pixel] = best
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

    transfer, units = _units(item, meta)
    start = transfer["at"]
    data = bytearray(item[start:start + transfer["size"]])
    for texel, unit in enumerate(units):
        if meta["bits"] == 8:
            data[unit] = values[texel]
        else:
            shift = 4 * (unit & 1)
            data[unit >> 1] = ((data[unit >> 1] & ~(0x0F << shift) & 0xFF)
                               | (values[texel] << shift))
    out = bytearray(item)
    out[start:start + len(data)] = data
    if len(out) != len(item):
        raise FisError("the item changed length")
    return bytes(out), approximated


PACK_DIRECTORY = "images"
DTT_MAGIC = b"DTT\0"
DTT_HEAD = 0x10
DTT_RECORD = 0x10


def pack_name(resource, route, offset):
    safe = "".join(c if c.isalnum() else "-" for c in route).strip("-")
    return "fis-%04d-%s-%X.png" % (int(resource), safe, offset)


def _item_ends(blob):
    for reader in (package_archive.layout, protected_package.layout):
        try:
            return reader(bytes(blob)).offsets
        except Exception:                                        # noqa: BLE001
            continue
    ends = []
    for _tag, offset, length in parse_pk1(bytes(blob)):
        ends += [offset, offset + length]
    return tuple(ends)


def _put_slz(blob, at, packed, grow=False):
    room = len(blob) - at
    starts = [int(other.split("@")[1], 16) for other, _data in _slz_streams(blob)]
    for end in starts + list(_item_ends(blob)):
        if at < end < at + room:
            room = end - at
    # An entry that is only this stream may grow by whole sectors.
    if len(packed) > room and grow and at == 0 and room == len(blob):
        sectors = -(-len(packed) // triace.SECTOR)
        blob = bytes(blob) + bytes(sectors * triace.SECTOR - len(blob))
        room = len(blob)
    if len(packed) > room:
        try:
            protected = protected_package.layout(blob)
        except protected_package.ProtectedPackageError:
            protected = None
        if protected is not None and at + room == protected.payload_end:
            required = (at + len(packed) + 0x1F) & ~0x1F
            if required <= len(blob):
                blob, _layout = protected_package.extend_payload_end(
                    blob, required)
                room = required - at
    if len(packed) > room:
        raise FisError(
            "the repainted picture compresses to %d bytes and its slot holds "
            "%d; keep more of the original picture's pixels unchanged"
            % (len(packed), room))
    out = bytearray(blob)
    out[at:at + len(packed)] = packed
    return bytes(out)


def _rewrite(blob, label, child, grow=False):
    """Put a modified *child* back into the blob it was unwrapped from."""
    if label.startswith("slz@"):
        at = int(label.split("@")[1], 16)
        return _put_slz(blob, at, slz_compress.compress(bytes(child), mode=2),
                        grow)
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
                entry = depth == 0 and where in ("raw", "decrypted")
                queue.append((route, nested, depth + 1,
                              lambda new, _b=blob, _l=label, _w=write,
                              _g=entry: _w(_rewrite(_b, _l, new, _g))))
    return out


def pack_files(folder, resource):
    """The pack's PNGs for one resource."""
    prefix = "fis-%04d-" % int(resource)
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder)
                  if name.startswith(prefix) and name.endswith(".png"))


def _offset_of(name):
    """The item offset a pack file name ends with, or ``None``."""
    try:
        return int(name[:-len(".png")].rsplit("-", 1)[1], 16)
    except (IndexError, ValueError):
        return None


def _dtt_tables(blob):
    """Valid DTT rectangle tables in *blob*, as ``(offset, records)``."""
    out, at = [], 0
    while True:
        at = blob.find(DTT_MAGIC, at)
        if at < 0:
            return out
        if at + DTT_HEAD <= len(blob):
            body = struct.unpack_from("<I", blob, at + 4)[0]
            end = at + DTT_HEAD + body
            if body and body % DTT_RECORD == 0 and end <= len(blob):
                records = [at + DTT_HEAD + index * DTT_RECORD
                           for index in range(body // DTT_RECORD)]
                out.append((at, records))
        at += 4


def _dtt_geometry(blob, at):
    """``(box, x scale, y scale)`` encoded by a canonical DTT record."""
    left, top, right, bottom, width, height = struct.unpack_from(
        "<6H", blob, at + 4)

    def axis(first, last, extent):
        if extent < 2 or last <= first:
            return None
        span = last - first
        if span % (extent - 1):
            return None
        scale = span // (extent - 1)
        bias = scale // 2
        if not scale or scale % 2 or first % scale != bias \
                or last % scale != bias:
            return None
        return (first - bias) // scale, (last - bias) // scale, scale

    horizontal = axis(left, right, width)
    vertical = axis(top, bottom, height)
    if horizontal is None or vertical is None:
        return None
    x0, x1, x_scale = horizontal
    y0, y1, y_scale = vertical
    return (x0, y0, x1, y1), x_scale, y_scale


def _dtt_box(blob, at):
    """Inclusive source-pixel box encoded by one DTT record, if canonical."""
    geometry = _dtt_geometry(blob, at)
    return geometry[0] if geometry is not None else None


LAYOUT_FILE = "fis-image-layouts.json"
_LAYOUT_KEYS = {
    "file": {"version", "images"},
    "image": {"mode", "name", "size", "regions", "dtt", "screen"},
    "dtt": {"records"},
    "dtt record": {"index", "name", "from", "to"},
    "screen": {"groups"},
    "screen group": {"name", "records", "from", "to"},
}


def _integers(value, count):
    return (isinstance(value, list) and len(value) == count
            and all(isinstance(item, int) and not isinstance(item, bool)
                    for item in value))


def _object(value, kind, where):
    if not isinstance(value, dict):
        raise FisError("%s: %s must be an object" % (where, kind))
    unknown = sorted(set(value) - _LAYOUT_KEYS[kind])
    if unknown:
        raise FisError("%s: unknown %s key(s) %s"
                       % (where, kind, ", ".join(unknown)))
    return value


def _checked_dtt(dtt, where):
    _object(dtt, "dtt", where)
    records = dtt.get("records")
    if not isinstance(records, list) or not records:
        raise FisError("%s: dtt.records must be a non-empty list" % where)
    seen = set()
    for row in records:
        _object(row, "dtt record", where)
        index = row.get("index")
        if (not isinstance(index, int) or isinstance(index, bool) or index < 1
                or index in seen):
            raise FisError("%s: invalid or repeated DTT record index %r"
                           % (where, index))
        seen.add(index)
        for label in ("from", "to"):
            if not _integers(row.get(label), 4):
                raise FisError("%s: DTT record %d needs four integer %s values"
                               % (where, index, label))


def _checked_screen(screen, where):
    _object(screen, "screen", where)
    groups = screen.get("groups")
    if not isinstance(groups, list) or not groups:
        raise FisError("%s: screen.groups must be a non-empty list" % where)
    seen = set()
    for row in groups:
        _object(row, "screen group", where)
        records = row.get("records")
        if (not isinstance(records, list) or not records
                or not all(isinstance(index, int) and not isinstance(index, bool)
                           and index > 0 for index in records)
                or len(set(records)) != len(records)):
            raise FisError("%s: a screen group needs unique positive integer "
                           "records" % where)
        overlap = seen.intersection(records)
        if overlap:
            raise FisError("%s: repeats screen record(s) %s"
                           % (where, ", ".join(str(i) for i in sorted(overlap))))
        seen.update(records)
        for label in ("from", "to"):
            if not _integers(row.get(label), 2):
                raise FisError("%s: screen group %r needs two integer %s values"
                               % (where, records, label))


def layout_path(folder):
    pack = os.path.dirname(os.path.normpath(os.fspath(folder)))
    return os.path.join(pack, LAYOUT_FILE)


def load_layout(folder):
    path = layout_path(folder)
    if not os.path.isfile(path):
        return path, {}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise FisError("cannot read %s: %s" % (path, exc))
    _object(document, "file", path)
    if document.get("version") != 1:
        raise FisError("%s must be a version 1 layout" % path)
    images = document.get("images")
    if not isinstance(images, dict):
        raise FisError("%s needs an images object" % path)
    for name, image in images.items():
        where = "%s image %s" % (path, name)
        _object(image, "image", where)
        if "dtt" in image:
            _checked_dtt(image["dtt"], where)
        if "screen" in image:
            _checked_screen(image["screen"], where)
    return path, images


def _layout_records(folder, name, size):
    """Runtime sprite-box edits for ``name``, checked against its size."""
    path, images = load_layout(folder)
    dtt = (images.get(name) or {}).get("dtt")
    if dtt is None:
        return None
    checked = []
    for row in dtt["records"]:
        index, before, after = row["index"], row["from"], row["to"]
        for label, (left, top, right, bottom) in (("from", before),
                                                  ("to", after)):
            if not (0 <= left <= right < size[0]
                    and 0 <= top <= bottom < size[1]):
                raise FisError("%s record %d %s box is outside the %dx%d image"
                               % (path, index, label, size[0], size[1]))
        checked.append((index, tuple(before), tuple(after)))
    return path, checked


def screen_layouts(folder):
    """``(path, [(image name, [(records, from, to)])])`` for pictures in ``folder``."""
    path, images = load_layout(folder)
    layouts = []
    for name in sorted(images):
        screen = images[name].get("screen")
        if screen is None or not os.path.isfile(os.path.join(folder, name)):
            continue
        layouts.append((name, [(tuple(group["records"]), tuple(group["from"]),
                                tuple(group["to"]))
                               for group in screen["groups"]]))
    return path, layouts


def _patch_dtt_layout(blob, folder, name, size, missing_ok=False):
    configured = _layout_records(folder, name, size)
    if configured is None:
        return bytes(blob), 0
    path, wanted = configured
    matches = []
    for table_at, records in _dtt_tables(blob):
        if all(index <= len(records)
               and _dtt_box(blob, records[index - 1]) in (before, after)
               for index, before, after in wanted):
            matches.append((table_at, records))
    if not matches and missing_ok:
        return bytes(blob), None
    if len(matches) != 1:
        raise FisError(
            "%s matched %d DTT tables; expected exactly one table carrying "
            "the guarded source boxes" % (path, len(matches)))
    _table_at, records = matches[0]
    out = bytearray(blob)
    for index, _before, after in wanted:
        at = records[index - 1]
        geometry = _dtt_geometry(blob, at)
        if geometry is None:  # The guarded table match makes this unreachable.
            raise FisError("%s record %d has non-canonical DTT coordinates"
                           % (path, index))
        _current, x_scale, y_scale = geometry
        left, top, right, bottom = after
        struct.pack_into("<4H", out, at + 4,
                         left * x_scale + x_scale // 2,
                         top * y_scale + y_scale // 2,
                         right * x_scale + x_scale // 2,
                         bottom * y_scale + y_scale // 2)
        struct.pack_into("<2H", out, at + 12,
                         right - left + 1, bottom - top + 1)
    changed = sum(a != b for a, b in zip(blob, out))
    return bytes(out), changed


def _dtt_elsewhere(current, resource, folder, name, size):
    found = []
    for route, blob, write in views(current, resource):
        patched, changed = _patch_dtt_layout(blob, folder, name, size,
                                             missing_ok=True)
        if changed is not None:
            found.append((route, patched, changed, write))
    if not found:
        raise FisError("%s matched 0 DTT tables in resource %s; expected "
                       "exactly one table carrying the guarded source boxes"
                       % (name, resource))
    route, patched, changed, write = max(found, key=lambda row: len(row[0]))
    if any(other != route and not route.startswith(other + "/")
           for other, _patched, _changed, _write in found):
        raise FisError("%s: DTT tables in unrelated views carry the guarded "
                       "source boxes" % name)
    return (write(patched) if changed else current), changed


def _write_with_layout(current, resource, folder, name, size, blob, patched,
                       write):
    patched, moved = _patch_dtt_layout(patched, folder, name, size,
                                       missing_ok=True)
    changed = sum(1 for a, b in zip(blob, patched) if a != b)
    if changed:
        current = write(patched)
    if moved is None:
        current, moved = _dtt_elsewhere(current, resource, folder, name, size)
        changed += moved
    return current, changed


def _by_shape(current, resource, folder, wanted, applied, offset=False):
    for name in wanted:
        path = os.path.join(folder, name)
        try:
            width, height, _rows = read_png(path)
        except FisError:
            continue
        inside = _offset_of(name) if offset else None
        if offset and inside is None:
            continue
        matches = []
        for route, blob, write in views(current, resource):
            for at, item in items_in(blob):
                if offset and at != inside:
                    continue
                try:
                    meta = descriptor(item)
                except FisError:
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
        patched = bytearray(blob)
        patched[at:at + len(built)] = built
        current, changed = _write_with_layout(
            current, resource, folder, name, (width, height), blob,
            bytes(patched), write)
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
                patched = bytearray(blob)
                patched[at:at + len(built)] = built
                meta = descriptor(item)
                current, changed = _write_with_layout(
                    current, resource, folder, name,
                    (meta["width"], meta["height"]), blob, bytes(patched),
                    write)
                applied.append((name, changed))
                if not changed:
                    continue
                progressed = True
                break
            if progressed:
                break
        if not progressed:
            break
    for offset in (True, False):
        placed = {name for name, _count in applied}
        left = [name for name in pack_files(folder, resource)
                if name not in placed]
        if left:
            current = _by_shape(current, resource, folder, left, applied,
                                offset=offset)
    placed = {name for name, _count in applied}
    for name in pack_files(folder, resource):
        if name not in placed:
            applied.append((name, None))
    return current, applied
