# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Minimal ISO9660 file locator and in-place length editor."""

from dataclasses import dataclass
import struct


SECTOR_SIZE = 0x800


@dataclass(frozen=True)
class FileExtent:
    path: str
    offset: int
    size: int
    allocation_size: int
    record_offset: int


def _primary_volume_descriptor(handle):
    for sector in range(16, 64):
        handle.seek(sector * SECTOR_SIZE)
        descriptor = handle.read(SECTOR_SIZE)
        if len(descriptor) != SECTOR_SIZE or descriptor[1:6] != b"CD001":
            raise ValueError("not an ISO9660 image: missing volume descriptor")
        if descriptor[0] == 1:
            return descriptor
        if descriptor[0] == 255:
            break
    raise ValueError("ISO9660 primary volume descriptor was not found")


def _directory_entries(data, base_offset):
    entries = []
    position = 0
    while position < len(data):
        length = data[position]
        if length == 0:
            position = (position // SECTOR_SIZE + 1) * SECTOR_SIZE
            continue
        if length < 34 or position + length > len(data):
            raise ValueError("malformed ISO9660 directory record")
        record = data[position:position + length]
        name_length = record[32]
        if 33 + name_length > len(record):
            raise ValueError("truncated ISO9660 file name")
        name_bytes = record[33:33 + name_length]
        if not (name_length == 1 and name_bytes in (b"\0", b"\1")):
            try:
                name = name_bytes.decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError("non-ASCII ISO9660 file name") from error
            name = name.split(";", 1)[0]
            is_directory = bool(record[25] & 2)
            if name.endswith(".") and not is_directory:
                name = name[:-1]
            sector_le = struct.unpack_from("<I", record, 2)[0]
            sector_be = struct.unpack_from(">I", record, 6)[0]
            size_le = struct.unpack_from("<I", record, 10)[0]
            size_be = struct.unpack_from(">I", record, 14)[0]
            if sector_le != sector_be or size_le != size_be:
                raise ValueError("ISO9660 directory record byte orders disagree")
            entries.append((
                name,
                sector_le,
                size_le,
                is_directory,
                base_offset + position,
            ))
        position += length
    return entries


def _read_directory(handle, sector, size):
    handle.seek(sector * SECTOR_SIZE)
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("truncated ISO9660 directory")
    return _directory_entries(data, sector * SECTOR_SIZE)


def locate_file(handle, path):
    """Locate one file by an absolute primary-volume path."""
    components = [item for item in path.replace("\\", "/").split("/") if item]
    if not components:
        raise ValueError("ISO9660 file path is empty")
    descriptor = _primary_volume_descriptor(handle)
    root = descriptor[156:190]
    sector = struct.unpack_from("<I", root, 2)[0]
    size = struct.unpack_from("<I", root, 10)[0]
    current_path = ""
    for number, component in enumerate(components):
        matches = [entry for entry in _read_directory(handle, sector, size)
                   if entry[0].upper() == component.upper()]
        if len(matches) != 1:
            raise ValueError(
                "ISO9660 path /%s has %d matches"
                % ("/".join(components[:number + 1]), len(matches))
            )
        name, sector, size, is_directory, record_offset = matches[0]
        current_path += "/" + name
        is_last = number == len(components) - 1
        if is_last:
            if is_directory:
                raise ValueError("ISO9660 path is a directory: %s" % current_path)
            allocation_size = (size + SECTOR_SIZE - 1) // SECTOR_SIZE * SECTOR_SIZE
            return FileExtent(
                current_path, sector * SECTOR_SIZE, size,
                allocation_size, record_offset
            )
        if not is_directory:
            raise ValueError("ISO9660 path component is not a directory: %s" % current_path)
    raise AssertionError("unreachable empty ISO9660 path")


def file_size_writes(extent, size):
    """``(offset, bytes)`` that set a file's both-endian lengths in place."""
    if not extent.size <= size <= extent.allocation_size:
        raise ValueError(
            "ISO9660 file length %d exceeds its fixed %d-byte allocation"
            % (size, extent.allocation_size)
        )
    return ((extent.record_offset + 10, struct.pack("<I", size)),
            (extent.record_offset + 14, struct.pack(">I", size)))


def write_file_size(handle, extent, size):
    """Update both-endian lengths without relocating a file's extent."""
    for offset, data in file_size_writes(extent, size):
        handle.seek(offset)
        handle.write(data)
