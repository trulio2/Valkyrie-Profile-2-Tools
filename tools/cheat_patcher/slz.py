# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Self-contained tri-Ace SLZ decompressor."""

import collections
import struct

from . import deferral


HEADER_SIZE = 0x10
MEMO_BYTES = 128 << 20

_memo = collections.OrderedDict()
_memo_bytes = 0


def decompress(data, mode=None, out_size=None):
    """Decompress an SLZ stream or a headerless SLZ body."""
    global _memo_bytes
    session = deferral.current()
    if session is not None:
        deferred = session.output_for(data)
        if deferred is not None:
            return deferred
    key = (bytes(data), mode, out_size)
    found = _memo.get(key)
    if found is not None:
        _memo.move_to_end(key)
        return found
    result = _decompress(data, mode, out_size)
    _memo[key] = result
    _memo_bytes += len(key[0]) + len(result)
    while _memo_bytes > MEMO_BYTES and len(_memo) > 1:
        (old, _, _), value = _memo.popitem(last=False)
        _memo_bytes -= len(old) + len(value)
    return result


def _decompress(data, mode=None, out_size=None):
    source = memoryview(data)
    position = 0
    if len(source) >= HEADER_SIZE and source[:3] == b"SLZ":
        mode = source[3]
        stored_size, out_size = struct.unpack_from("<II", source, 4)
        if HEADER_SIZE + stored_size > len(source):
            raise ValueError("truncated SLZ body")
        position = HEADER_SIZE
    if mode is None or out_size is None:
        raise ValueError("mode/out_size required without an SLZ header")
    if mode not in (0, 1, 2, 3):
        raise ValueError("unsupported SLZ mode %d" % mode)
    if mode == 0:
        end = position + out_size
        if end > len(source):
            raise ValueError("truncated SLZ store stream")
        return bytes(source[position:end])

    output = bytearray(out_size)
    output_position = 0
    flags = 0
    try:
        while output_position < out_size:
            flags >>= 1
            if flags <= 0xFFFF:
                flags = 0x00FF0000 | source[position]
                position += 1
                if mode == 3:
                    flags |= 0xFF000000 | (source[position] << 8)
                    position += 1
            if flags & 1:
                output[output_position] = source[position]
                output_position += 1
                position += 1
                if mode == 3:
                    output[output_position] = source[position]
                    output_position += 1
                    position += 1
                continue

            byte0 = source[position]
            byte1 = source[position + 1]
            position += 2
            if mode == 2 and byte1 >= 0xF0:
                if byte1 > 0xF0:
                    length = (byte1 & 0x0F) + 3
                    fill = byte0
                else:
                    length = byte0 + 0x13
                    fill = source[position]
                    position += 1
                if output_position + length > out_size:
                    raise ValueError("SLZ run exceeds declared output size")
                output[output_position:output_position + length] = bytes([fill]) * length
                output_position += length
                continue

            distance = byte0 | ((byte1 & 0x0F) << 8)
            length = (byte1 >> 4) + 3
            if mode == 3:
                length = (length - 1) << 1
                distance <<= 1
            if distance <= 0 or distance > output_position:
                raise ValueError("invalid SLZ back-reference distance %d" % distance)
            if output_position + length > out_size:
                raise ValueError("SLZ match exceeds declared output size")
            copy_position = output_position - distance
            for _ in range(length):
                output[output_position] = output[copy_position]
                output_position += 1
                copy_position += 1
    except IndexError as error:
        raise ValueError("truncated SLZ token stream") from error
    return bytes(output)
