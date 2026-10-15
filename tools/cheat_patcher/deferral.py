# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct

from ..scripts import slz as shared_slz

_session = None

HEADER = b"SLZ\x03"
MARK = b"VP2-DEFERRED"
BLOCK = 4096


def current():
    return _session


class Session:
    def __init__(self):
        self.resource = None
        self.defer = False
        self.outputs = {}
        self.inputs = {}
        self.last_real = {}
        self.mixed = set()

    def __enter__(self):
        global _session
        self._previous, _session = _session, self
        shared_slz.KNOWN.append(self.output_for)
        return self

    def __exit__(self, *_exc):
        global _session
        _session = self._previous
        shared_slz.KNOWN.remove(self.output_for)
        return False

    def begin(self, resource, defer):
        self.resource = resource
        self.defer = defer

    def end(self):
        self.resource = None
        self.defer = False

    def compressed(self, source, next_offset):
        """A placeholder for *source*, or None to compress it for real."""
        if self.resource is None:
            return None
        key = (self.resource, len(source), next_offset)
        previous = self.inputs.get(key)
        if previous is not None and not _same_stream(previous, source):
            self.mixed.add(self.resource)
        self.inputs[key] = source
        self.last_real[key] = not self.defer
        if not self.defer:
            return None
        body = MARK + struct.pack("<I", len(self.outputs))
        stream = HEADER + struct.pack("<III", len(body), len(source),
                                      next_offset) + body
        self.outputs[stream] = source
        return stream

    def output_for(self, data):
        if not self.outputs or bytes(data[:4]) != HEADER:
            return None
        stored = struct.unpack_from("<I", data, 4)[0]
        return self.outputs.get(bytes(data[:16 + stored]))

    def unfinished(self):
        """Resources still holding a placeholder, or whose streams mixed."""
        return self.mixed | {resource for (resource, _, _), real
                             in self.last_real.items() if not real}


def _same_stream(previous, source):
    """Two inputs of one stream differ in a few patched words, not most."""
    blocks = range(0, len(source), BLOCK)
    changed = sum(previous[at:at + BLOCK] != source[at:at + BLOCK]
                  for at in blocks)
    return changed * 4 <= len(blocks)
