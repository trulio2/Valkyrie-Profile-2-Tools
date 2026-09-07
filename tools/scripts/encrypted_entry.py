from __future__ import annotations

import struct

MASK32 = 0xFFFFFFFF

ENTRY_KEYS = {
    2591: (0x9A72C5AA, 0x0002F4B1, 0x000C4B93),
}


class EncryptedEntryError(ValueError):
    """The entry is not one this module has a key for."""


def is_encrypted(resource):
    return resource in ENTRY_KEYS


def crypt(data, resource):
    """XOR *data* with the entry's keystream.  Its own inverse."""
    try:
        state, multiplier, increment = ENTRY_KEYS[resource]
    except KeyError:
        raise EncryptedEntryError(
            "resource #%s has no measured keystream" % (resource,)) from None
    if len(data) % 4:
        raise EncryptedEntryError(
            "resource #%s is encrypted in whole words; got %d bytes"
            % (resource, len(data)))
    out = bytearray(len(data))
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        struct.pack_into("<I", out, offset, word ^ state)
        state = (multiplier * state + increment) & MASK32
    return bytes(out)


def decode_entry(raw, resource):
    """Return the entry's clear bytes, or raise if it is not encrypted."""
    return crypt(bytes(raw), resource)


def encode_entry(clear, resource):
    """Restore an entry's stored form.  Length is preserved exactly."""
    return crypt(bytes(clear), resource)
