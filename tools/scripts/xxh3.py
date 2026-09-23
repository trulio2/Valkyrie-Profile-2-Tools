# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""XXH3-64 (seed 0, default secret)."""
import struct

_M = (1 << 64) - 1
_SECRET = bytes.fromhex(
    "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671f"
    "cb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c"
    "3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8"
    "a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364"
    "eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e"
    "2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e")
_P32_1, _P32_2, _P32_3 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D
_P64_1, _P64_2, _P64_3 = 0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9
_P64_4, _P64_5 = 0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5
_MX2 = 0x9FB21C651E98DF25


def _r64(b, at):
    return struct.unpack_from("<Q", b, at)[0]


def _r32(b, at):
    return struct.unpack_from("<I", b, at)[0]


def _fold(a, b):
    p = a * b
    return (p & _M) ^ (p >> 64)


def _avalanche(h):
    h ^= h >> 37
    h = (h * 0x165667919E3779F9) & _M
    return h ^ (h >> 32)


def _xxh64_avalanche(h):
    h ^= h >> 33
    h = (h * _P64_2) & _M
    h ^= h >> 29
    h = (h * _P64_3) & _M
    return h ^ (h >> 32)


def _mix16(data, at, sec):
    return _fold(_r64(data, at) ^ _r64(_SECRET, sec),
                 _r64(data, at + 8) ^ _r64(_SECRET, sec + 8))


def xxh3_64(data):
    """XXH3_64bits, seed 0, default secret (xxHash 0.8.2, scalar path)."""
    data, n, s = bytes(data), len(data), _SECRET
    if n == 0:
        return _xxh64_avalanche(_r64(s, 56) ^ _r64(s, 64))
    if n <= 3:
        combined = (data[0] << 16) | (data[n >> 1] << 24) | data[n - 1] | (n << 8)
        return _xxh64_avalanche(combined ^ (_r32(s, 0) ^ _r32(s, 4)))
    if n <= 8:
        keyed = ((_r32(data, n - 4) + (_r32(data, 0) << 32)) & _M) ^ (_r64(s, 8) ^ _r64(s, 16))
        rot = ((keyed << 49) | (keyed >> 15)) ^ ((keyed << 24) | (keyed >> 40))
        h = keyed ^ (rot & _M)
        h = (h * _MX2) & _M
        h ^= (h >> 35) + n
        h = (h * _MX2) & _M
        return h ^ (h >> 28)
    if n <= 16:
        lo = _r64(data, 0) ^ (_r64(s, 24) ^ _r64(s, 32))
        hi = _r64(data, n - 8) ^ (_r64(s, 40) ^ _r64(s, 48))
        swapped = int.from_bytes(lo.to_bytes(8, "little"), "big")
        return _avalanche((n + swapped + hi + _fold(lo, hi)) & _M)
    if n <= 128:
        acc = (n * _P64_1) & _M
        if n > 32:
            if n > 64:
                if n > 96:
                    acc += _mix16(data, 48, 96) + _mix16(data, n - 64, 112)
                acc += _mix16(data, 32, 64) + _mix16(data, n - 48, 80)
            acc += _mix16(data, 16, 32) + _mix16(data, n - 32, 48)
        acc += _mix16(data, 0, 0) + _mix16(data, n - 16, 16)
        return _avalanche(acc & _M)
    if n <= 240:
        acc = (n * _P64_1) & _M
        for i in range(8):
            acc += _mix16(data, 16 * i, 16 * i)
        end = _mix16(data, n - 16, 119)
        acc = _avalanche(acc & _M)
        for i in range(8, n // 16):
            end += _mix16(data, 16 * i, 16 * (i - 8) + 3)
        return _avalanche((acc + end) & _M)

    acc = [_P32_3, _P64_1, _P64_2, _P64_3, _P64_4, _P32_2, _P64_5, _P32_1]
    stripes_per_block = (len(s) - 64) // 8
    block_len = 64 * stripes_per_block
    blocks = (n - 1) // block_len
    keys = [[_r64(s, (k + lane) * 8) for lane in range(8)]
            for k in range(stripes_per_block)]

    def stripe(at, key):
        for lane in range(8):
            val = _r64(data, at + lane * 8)
            mixed = val ^ key[lane]
            acc[lane ^ 1] = (acc[lane ^ 1] + val) & _M
            acc[lane] = (acc[lane] + (mixed & 0xFFFFFFFF) * (mixed >> 32)) & _M

    scramble = [_r64(s, len(s) - 64 + lane * 8) for lane in range(8)]
    for b in range(blocks):
        for k in range(stripes_per_block):
            stripe(b * block_len + k * 64, keys[k])
        for lane in range(8):
            a = acc[lane]
            acc[lane] = (((a ^ (a >> 47)) ^ scramble[lane]) * _P32_1) & _M
    for k in range(((n - 1) - block_len * blocks) // 64):
        stripe(blocks * block_len + k * 64, keys[k])
    stripe(n - 64, [_r64(s, len(s) - 71 + lane * 8) for lane in range(8)])
    result = (n * _P64_1) & _M
    for i in range(4):
        result += _fold(acc[2 * i] ^ _r64(s, 11 + 16 * i),
                        acc[2 * i + 1] ^ _r64(s, 19 + 16 * i))
    return _avalanche(result & _M)
