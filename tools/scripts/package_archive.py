"""Read and rewrite MCPS2 banks nested in tri-Ace ``p@Ck`` packages."""

from __future__ import annotations

import dataclasses
import struct

from . import slz
from . import slz_compress


MAGIC = b"p@Ck"
MCPS2_MAGIC = b"mcps2lib"


class PackageError(ValueError):
    """A package is malformed or cannot be rebuilt inside its allocation."""


class ContainerNotFound(PackageError):
    """No unambiguous nested MCPS2 bank exists in the entry."""


@dataclasses.dataclass(frozen=True)
class PackageLayout:
    count: int
    offsets: tuple[int, ...]
    flags: tuple[int, ...]
    size: int


@dataclasses.dataclass(frozen=True)
class PackageStep:
    item: int
    compression: int | None


@dataclasses.dataclass(frozen=True)
class ContainerPath:
    root_offset: int
    root_size: int
    steps: tuple[PackageStep, ...]


def layout(package: bytes) -> PackageLayout:
    """Parse one complete ``p@Ck`` package and its terminal table row."""
    if len(package) < 0x10 or package[:4] != MAGIC:
        raise PackageError("not a p@Ck package")
    count = struct.unpack_from("<H", package, 6)[0]
    table_end = 8 + (count + 1) * 8
    if not count or table_end > len(package):
        raise PackageError("p@Ck item table exceeds the package")
    offsets = []
    flags = []
    for index in range(count + 1):
        offset, item_flags = struct.unpack_from("<II", package, 8 + index * 8)
        offsets.append(offset)
        flags.append(item_flags)
    if offsets[0] < table_end or offsets[-1] > len(package):
        raise PackageError("p@Ck item extent exceeds the package")
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        raise PackageError("p@Ck item offsets are not sorted")
    if flags[-1] != 0:
        raise PackageError("p@Ck terminal table row has flags")
    return PackageLayout(count, tuple(offsets), tuple(flags[:-1]), offsets[-1])


def _stream(item: bytes) -> tuple[bytes, int] | None:
    if len(item) < 0x10 or item[:3] != b"SLZ" or item[3] > 3:
        return None
    stored = struct.unpack_from("<I", item, 4)[0]
    end = 0x10 + stored
    if end > len(item):
        raise PackageError("nested SLZ stream exceeds its package item")
    return item[:end], item[3]


def _walk(package: bytes, steps: tuple[PackageStep, ...], found: list) -> None:
    parsed = layout(package)
    for index in range(parsed.count):
        start, end = parsed.offsets[index:index + 2]
        item = package[start:end]
        if item.startswith(MCPS2_MAGIC):
            found.append((item, steps + (PackageStep(index, None),)))
            continue
        if item.startswith(MAGIC):
            try:
                _walk(item, steps + (PackageStep(index, None),), found)
            except PackageError:
                pass
            continue
        packed = _stream(item)
        if packed is None:
            continue
        stream, mode = packed
        try:
            decoded = slz.decompress(stream)
        except (IndexError, ValueError):
            continue
        next_steps = steps + (PackageStep(index, mode),)
        if decoded.startswith(MCPS2_MAGIC):
            found.append((decoded, next_steps))
        elif decoded.startswith(MAGIC):
            try:
                _walk(decoded, next_steps, found)
            except PackageError:
                pass


def locate_container(raw: bytes) -> tuple[bytes, ContainerPath]:
    """Find the sole MCPS2 bank reachable through embedded packages."""
    found = []
    package_extents = []
    position = raw.find(MAGIC)
    while position >= 0:
        if any(start < position < end for start, end in package_extents):
            position = raw.find(MAGIC, position + 1)
            continue
        try:
            parsed = layout(raw[position:])
            package_extents.append((position, position + parsed.size))
            package = raw[position:position + parsed.size]
            nested = []
            _walk(package, (), nested)
            found.extend(
                (blob, ContainerPath(position, parsed.size, steps))
                for blob, steps in nested
            )
        except PackageError:
            pass
        position = raw.find(MAGIC, position + 1)

    unique = {}
    for blob, path in found:
        unique[(path.root_offset, path.steps)] = (blob, path)
    matches = list(unique.values())
    if not matches:
        raise ContainerNotFound("entry has no MCPS2 bank in a p@Ck package")
    if len(matches) != 1:
        raise ContainerNotFound(
            "entry has %d MCPS2 banks in p@Ck packages; the writer cannot "
            "choose one safely" % len(matches))
    return matches[0]


def unpack_container(raw: bytes) -> bytes:
    return locate_container(raw)[0]


def _pack_stream(decoded: bytes, mode: int, item_size: int, old: bytes) -> bytes:
    old_stored = struct.unpack_from("<I", old, 4)[0]
    packed = bytearray(slz_compress.compress(decoded, mode=mode, optimal=False))
    if len(packed) > item_size:
        packed = bytearray(slz_compress.compress(decoded, mode=mode, optimal=True))
    if len(packed) > item_size:
        raise PackageError(
            "rebuilt nested SLZ needs %d bytes but its package item holds %d"
            % (len(packed), item_size))
    encoded_stored = len(packed) - 0x10
    if encoded_stored <= old_stored and 0x10 + old_stored <= item_size:
        struct.pack_into("<I", packed, 4, old_stored)
        packed.extend(bytes(old_stored - encoded_stored))
    if slz.decompress(bytes(packed)) != decoded:
        raise PackageError("rebuilt nested SLZ does not round-trip")
    return bytes(packed)


def _replace(package: bytes, steps: tuple[PackageStep, ...], blob: bytes) -> bytes:
    parsed = layout(package)
    step = steps[0]
    start, end = parsed.offsets[step.item:step.item + 2]
    old_item = package[start:end]
    if len(steps) == 1:
        child = blob
    else:
        if step.compression is None:
            child = _replace(old_item, steps[1:], blob)
        else:
            packed = _stream(old_item)
            if packed is None:
                raise PackageError("package path no longer names an SLZ item")
            child = _replace(slz.decompress(packed[0]), steps[1:], blob)
    if step.compression is not None:
        child = _pack_stream(child, step.compression, end - start, old_item)
    if len(child) <= end - start:
        rebuilt = bytearray(package)
        rebuilt[start:end] = child + bytes(end - start - len(child))
        return bytes(rebuilt)
    return _grow_item(package, parsed, step.item, child)


def _alignment(offsets: tuple[int, ...]) -> int:
    """The item alignment the package already keeps."""
    align = 16
    while align > 1 and any(offset % align for offset in offsets):
        align //= 2
    return align


def _grow_item(package: bytes, parsed: PackageLayout, index: int,
               child: bytes) -> bytes:
    """Widen one item, shift the ones behind it, and rewrite the table."""
    start, end = parsed.offsets[index:index + 2]
    align = _alignment(parsed.offsets)
    needed = len(child)
    if needed % align:
        needed += align - (needed % align)
    extra = needed - (end - start)

    rebuilt = bytearray(package[:start])
    rebuilt += child + bytes(needed - len(child))
    rebuilt += package[end:]
    for position in range(parsed.count + 1):
        offset = parsed.offsets[position]
        if position > index:
            offset += extra
        struct.pack_into("<I", rebuilt, 8 + position * 8, offset)

    # Parse the result rather than trusting the arithmetic: a table that no
    # longer describes its own bytes is the one failure this must not ship.
    checked = layout(bytes(rebuilt))
    if checked.count != parsed.count:
        raise PackageError("grown package lost an item")
    if checked.offsets[index + 1] - checked.offsets[index] != needed:
        raise PackageError("grown package item is not the size asked for")
    return bytes(rebuilt)


def _first_stream_size(package: bytes, steps: tuple[PackageStep, ...]) -> int | None:
    parsed = layout(package)
    step = steps[0]
    start, end = parsed.offsets[step.item:step.item + 2]
    item = package[start:end]
    if step.compression is not None:
        return struct.unpack_from("<I", item, 4)[0]
    if len(steps) > 1:
        return _first_stream_size(item, steps[1:])
    return None


def pack_container(raw: bytes, blob: bytes, *,
                   absorb_growth: bool = False) -> tuple[bytes, dict]:
    """Replace the sole nested MCPS2 bank without moving package items."""
    original, path = locate_container(raw)
    root = raw[path.root_offset:path.root_offset + path.root_size]
    rebuilt_root = _replace(root, path.steps, bytes(blob))
    if len(rebuilt_root) > path.root_size and not absorb_growth:
        raise PackageError(
            "rebuilt p@Ck package needs %d bytes but the entry holds %d; the "
            "growth has to be absorbed by an enclosing compressed item"
            % (len(rebuilt_root), path.root_size))
    rebuilt_root = rebuilt_root.ljust(path.root_size, b"\0")
    stored_before = _first_stream_size(root, path.steps)
    stored_after = _first_stream_size(rebuilt_root, path.steps)
    rebuilt = bytearray(raw)
    rebuilt[path.root_offset:path.root_offset + path.root_size] = rebuilt_root
    checked, checked_path = locate_container(bytes(rebuilt))
    if checked != bytes(blob):
        raise PackageError("rebuilt p@Ck package did not read back byte-for-byte")
    if (checked_path.root_offset != path.root_offset
            or checked_path.steps != path.steps
            or (checked_path.root_size != path.root_size
                and not absorb_growth)):
        raise PackageError("rebuilt p@Ck package did not read back byte-for-byte")
    return bytes(rebuilt), {
        "wrapper": "p@Ck",
        "package_offset": path.root_offset,
        "package_size": len(rebuilt_root),
        "items": [step.item for step in path.steps],
        "stored_before": stored_before if stored_before is not None else len(original),
        "stored_after": stored_after if stored_after is not None else len(blob),
    }
