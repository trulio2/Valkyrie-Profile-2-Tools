#!/usr/bin/env python3
"""tri-Ace SLZ decompressor (STORE / LZSS / LZSS+RLE / LZSS16)."""
import collections
import hashlib
import os
import struct, sys


_MEMO = collections.OrderedDict()
_MEMO_BYTES = 0
try:
    _MEMO_LIMIT = int(os.environ.get("VP2_SLZ_MEMO_BYTES", 256 << 20))
except ValueError:
    _MEMO_LIMIT = 256 << 20


KNOWN = []


def memo_stats():
    """How much the memo holds, for a caller that wants to report it."""
    return {"entries": len(_MEMO), "bytes": _MEMO_BYTES, "limit": _MEMO_LIMIT}


def forget():
    """Drop every remembered payload."""
    global _MEMO_BYTES
    _MEMO.clear()
    _MEMO_BYTES = 0


def _remember(key, plain):
    global _MEMO_BYTES
    if len(plain) > _MEMO_LIMIT:
        return
    _MEMO[key] = plain
    _MEMO_BYTES += len(plain)
    while _MEMO_BYTES > _MEMO_LIMIT:
        _key, evicted = _MEMO.popitem(last=False)
        _MEMO_BYTES -= len(evicted)


def decompress(data, mode=None, out_size=None):
    """Decompress an SLZ blob, remembering what each blob expands to."""
    for known in KNOWN:
        plain = known(data)
        if plain is not None:
            return plain
    if not _MEMO_LIMIT:
        return _decompress(data, mode, out_size)
    blob = bytes(data)
    key = (hashlib.sha1(blob).digest(), mode, out_size)
    plain = _MEMO.get(key)
    if plain is not None:
        _MEMO.move_to_end(key)
        return plain
    plain = _decompress(blob, mode, out_size)
    _remember(key, plain)
    return plain


def _decompress(data, mode, out_size):
    sp = 0
    if len(data) >= 0x10 and data[0:3] == b"SLZ":
        mode = data[3]
        out_size = struct.unpack_from("<I", data, 8)[0]
        sp = 0x10
    if mode is None or out_size is None:
        raise ValueError("mode/out_size required when there is no SLZ header")

    out = bytearray(out_size)
    op = 0

    if mode == 0:  # STORE
        out[:out_size] = data[sp:sp + out_size]
        return bytes(out)

    flags = 0
    while op < out_size:
        flags >>= 1
        if flags <= 0xFFFF:               # reload control bits
            flags = 0x00FF0000 | data[sp]; sp += 1
            if mode == 3:
                flags |= 0xFF000000 | (data[sp] << 8); sp += 1

        if flags & 1:                     # literal
            out[op] = data[sp]; op += 1; sp += 1
            if mode == 3:
                out[op] = data[sp]; op += 1; sp += 1
        else:                             # match / run
            b0 = data[sp]; sp += 1
            b1 = data[sp]; sp += 1
            if mode == 2 and b1 >= 0xF0:   # RLE run
                if b1 > 0xF0:
                    length = (b1 & 0x0F) + 3
                    fill = b0
                else:                      # b1 == 0xF0 : long run
                    length = b0 + 0x13
                    fill = data[sp]; sp += 1
                for _ in range(length):
                    out[op] = fill; op += 1
            else:                          # LZSS back-reference
                pos = b0 | ((b1 & 0x0F) << 8)
                length = (b1 >> 4) + 3
                if mode == 3:
                    length = (length - 1) << 1
                    pos <<= 1
                src = op - pos
                for _ in range(length):
                    out[op] = out[src]; op += 1; src += 1

    return bytes(out)


def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    with open(sys.argv[1], "rb") as f:
        data = f.read()
    out = decompress(data)
    with open(sys.argv[2], "wb") as f:
        f.write(out)
    print("%d -> %d bytes" % (len(data), len(out)))


if __name__ == "__main__":
    main()
