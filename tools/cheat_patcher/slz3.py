# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Deterministic encoder for tri-Ace SLZ mode 3 (16-bit LZSS)."""

import os
import struct

from . import deferral
from ..scripts import slz_cache
from ..scripts.paths import CACHE_ROOT


UNIT = 2
MIN_UNITS = 2
MAX_UNITS = 17
MAX_DISTANCE = 4095
MAX_CHAIN = 4096

_CACHE_NAMESPACE = "slz3-v1"
_DEFAULT_CACHE_DIR = os.path.join(os.fspath(CACHE_ROOT), "overlay")


def _match_table(source, max_chain=MAX_CHAIN):
    unit_count = len(source) // UNIT
    heads = {}
    previous = [-1] * unit_count
    best_lengths = [0] * unit_count
    best_distances = [0] * unit_count
    for index in range(unit_count):
        if index + MIN_UNITS > unit_count:
            break
        offset = index * UNIT
        key = source[offset:offset + MIN_UNITS * UNIT]
        candidate = heads.get(key, -1)
        earliest = index - MAX_DISTANCE
        length = distance = chain = 0
        maximum = min(MAX_UNITS, unit_count - index)
        while candidate >= 0 and candidate >= earliest and chain < max_chain:
            candidate_offset = candidate * UNIT
            if (length == 0 or source[candidate_offset + length * UNIT] ==
                    source[offset + length * UNIT]):
                matched = 0
                while (matched < maximum and
                       source[candidate_offset + matched * UNIT:
                              candidate_offset + (matched + 1) * UNIT] ==
                       source[offset + matched * UNIT:
                              offset + (matched + 1) * UNIT]):
                    matched += 1
                if matched > length:
                    length = matched
                    distance = index - candidate
                    if matched == maximum:
                        break
            candidate = previous[candidate]
            chain += 1
        best_lengths[index] = length
        best_distances[index] = distance
        previous[index] = heads.get(key, -1)
        heads[key] = index
    return best_lengths, best_distances


def compress_body(source, max_chain=MAX_CHAIN):
    """Encode bytes as an optimal-token-count mode-3 body."""
    if len(source) % UNIT:
        raise ValueError(
            "SLZ mode 3 encodes whole 16-bit units; input length %d is odd"
            % len(source)
        )
    unit_count = len(source) // UNIT
    best_lengths, best_distances = _match_table(source, max_chain)
    costs = [0] * (unit_count + 1)
    choices = [None] * (unit_count + 1)
    for index in range(unit_count - 1, -1, -1):
        cost = costs[index + 1] + 1
        choice = (0, 1)
        maximum = best_lengths[index]
        if maximum >= MIN_UNITS:
            distance = best_distances[index]
            for length in range(MIN_UNITS, maximum + 1):
                alternative = costs[index + length] + 1
                if alternative < cost:
                    cost = alternative
                    choice = (distance, length)
        costs[index] = cost
        choices[index] = choice

    tokens = []
    index = 0
    while index < unit_count:
        distance, length = choices[index]
        tokens.append((distance, length, index))
        index += length

    output = bytearray()
    for group_start in range(0, len(tokens), 16):
        flags = 0
        body = bytearray()
        for bit, (distance, length, position) in enumerate(
                tokens[group_start:group_start + 16]):
            if distance == 0:
                flags |= 1 << bit
                start = position * UNIT
                body.extend(source[start:start + UNIT])
            else:
                body.append(distance & 0xFF)
                body.append(((distance >> 8) & 0x0F) | ((length - 2) << 4))
        output.extend((flags & 0xFF, (flags >> 8) & 0xFF))
        output.extend(body)
    return bytes(output)


def _compress_uncached(source, next_offset, max_chain):
    body = compress_body(source, max_chain=max_chain)
    return b"SLZ\x03" + struct.pack(
        "<III", len(body), len(source), next_offset
    ) + body


def compress(source, next_offset=0, max_chain=MAX_CHAIN):
    """Return one complete mode-3 SLZ stream."""
    source = bytes(source)
    session = deferral.current()
    if session is not None:
        placeholder = session.compressed(source, next_offset)
        if placeholder is not None:
            return placeholder
    store = slz_cache.resolve(None, _DEFAULT_CACHE_DIR, "VP2_OVERLAY_CACHE")
    if not store:
        return _compress_uncached(source, next_offset, max_chain)
    key = slz_cache.name(_CACHE_NAMESPACE, source, next_offset, max_chain)
    return slz_cache.cached(
        store, key,
        lambda: _compress_uncached(source, next_offset, max_chain))
