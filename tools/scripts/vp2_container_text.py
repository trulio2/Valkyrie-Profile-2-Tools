#!/usr/bin/env python3
"""Decode, encode, and patch MCPS2 container text banks."""
import argparse
import csv
import io
import os
import re
import hashlib
import struct
import sys

from .paths import PROJECT_ROOT, TOOLS_DIR

HERE = os.fspath(TOOLS_DIR)

from . import triace_ps2_unpack as triace
from . import vp2_dcms as dcms
from .vp2_dcms import read_entry
from . import vp2_iso_buffer as iso_buffer
from .slz import decompress

TOKEN_BASE = 0x0165          # 0x0100 | (slot + 0x65)
LINE_BREAK = 0x8080          # the record's own line break
# Where resource 10's cutscene cut starts; 0-48 are the credits face.
SUBTITLE_CUT_FIRST_SLOT = 49
CODEPAGE_SHIFT = 0x1F
CODEPAGE_SPACE = 0x0E
CODEPAGE_TAG = re.compile(r"<[0-9A-Fa-f]{4}(?::[0-9A-Fa-f]+)?>")

CODEPAGE_CHARACTERS = {
    character: token for token, character in dcms.ENGLISH_CONTROLS.items()
}
CODEPAGE_CHARACTERS.update({
    chr(token + CODEPAGE_SHIFT): token for token in range(0x20, 0x60)
})

RESOURCE_10_SLOTS = list(
    " ®Game Dsignr"
    "MkNotPlhdTS"
    "LuUBYcKR&bjyAEwCVf3pOvzH"
    "FDanger.Fom"
    " wht?Yuc'lHdy"
    "kIsif,!OpARETqQvSVbB-L")

SLOT_NAMES = {10: RESOURCE_10_SLOTS}

RESOURCE_10_EXACT_ACCENT_DONORS = frozenset("àáâçéêíó")

RESOURCE_10_NATIVE_MARKS = {
    "acute": (
        (6, 5, 3), (7, 5, 3), (8, 5, 3), (9, 5, 3), (10, 5, 3), (11, 5, 1),
        (5, 6, 5), (6, 6, 7), (7, 6, 7), (8, 6, 7), (9, 6, 7), (10, 6, 7),
        (11, 6, 5), (4, 7, 5), (5, 7, 7), (6, 7, 7), (7, 7, 7), (8, 7, 11),
        (9, 7, 9), (10, 7, 7), (11, 7, 5), (3, 8, 4), (4, 8, 7), (5, 8, 7),
        (6, 8, 7), (7, 8, 13), (8, 8, 15), (9, 8, 12), (10, 8, 7), (11, 8, 5),
    ),
    "tilde": (
        (3, 6, 3), (4, 6, 5), (5, 6, 5), (6, 6, 5), (7, 6, 7), (8, 6, 7),
        (9, 6, 7), (10, 6, 7), (11, 6, 7), (2, 7, 3), (3, 7, 7), (4, 7, 7),
        (5, 7, 7), (6, 7, 7), (7, 7, 7), (8, 7, 7), (9, 7, 7), (10, 7, 7),
        (11, 7, 7), (2, 8, 6), (3, 8, 7), (4, 8, 7), (5, 8, 11), (6, 8, 13),
        (7, 8, 11), (8, 8, 11), (9, 8, 14), (10, 8, 7), (11, 8, 7),
    ),
}

from .container_archive import (
    _compress_container, _decode_slz_group, _encode_slz_window,
    _pack_bare_slz, _pack_inline_slz, _pack_zls_stream, _round_up,
    _slz_groups, _tighten, container, container_stream_offset,
    find_container_stream, pack_container_entry, patch_643_literal_probe,
    patch_slz_literal_source, pk1_section_tag, resource_10_marked_block,
    rewrite_slz_preserving_groups, trace_slz_origins,
    unpack_container_entry,
)

def layout(blob):
    keys = ("table_start", "text_start", "text_end", "font_start", "glyph_count")
    values = struct.unpack_from("<5I", blob, 0x24)
    out = dict(zip(keys, values))
    out["glyph_base"] = struct.unpack_from("<I", blob, 0x50)[0]
    if out["text_end"] == 0:
        stored = struct.unpack_from("<I", blob, 0x20)[0]
        out["text_end"] = min(stored, len(blob)) if stored else len(blob)
    return out

def entries(blob, meta):
    """The message table: (id, offset) pairs, in table order."""
    out, seen = [], set()
    for position in range(meta["table_start"], meta["text_start"], 8):
        message_id, offset = struct.unpack_from("<II", blob, position)
        if message_id in seen:
            continue
        seen.add(message_id)
        out.append((message_id, offset))
    return out

def render_tokens(blob, meta, offset, slots):
    """Decode a token record, returning (text, byte length including the 0)."""
    start = meta["text_start"] + offset
    position, out = start, []
    while position < meta["text_end"]:
        if blob[position] == 0:
            break
        token = struct.unpack_from("<H", blob, position)[0]
        position += 2
        index = token - TOKEN_BASE
        if 0 <= index < len(slots):
            out.append(slots[index])
        elif token == 0x0100:
            out.append(" ")
        elif token == LINE_BREAK:
            out.append("\n")
        else:
            out.append("<%04X>" % token)
    return "".join(out), position - start + 1

def codepage_record_is_local(blob, meta, offset):
    from . import vp2_cutscene_subtitles as subtitles

    position = meta["text_start"] + offset
    base = meta.get("glyph_base", 0)
    count = meta.get("glyph_count", 0)
    while position < meta["text_end"]:
        byte = blob[position]
        if byte == 0:
            break
        if byte < 0x80:
            token, position = byte, position + 1
        else:
            if position + 1 >= meta["text_end"]:
                break
            token, position = byte | (blob[position + 1] << 8), position + 2
        if subtitles.token_slot(token, base, count) is not None:
            continue
        width = subtitles.RECORD_PARAMETERS.get(token, 0)
        if width:
            position += width
            continue
        text = dcms.decode_english_tokens([token])
        if text and text.strip():
            return False
    return True


def render_codepage(blob, meta, offset, accent_tokens=None, alphabet=None):
    """Decode a container record, returning ``(text, byte length)``."""
    # Imported here rather than at module scope, like the other uses in
    # this file, so the two modules stay free to import each other.
    from . import vp2_cutscene_subtitles as subtitles

    start = meta["text_start"] + offset
    position = start
    if alphabet and not codepage_record_is_local(blob, meta, offset):
        alphabet = None
    accents = {token: character for character, token in
               (accent_tokens or {}).items()}
    parts = []
    while position < meta["text_end"]:
        byte = blob[position]
        if byte == 0:
            position += 1
            break
        if byte < 0x80:
            token = byte
            position += 1
        else:
            if position + 1 >= meta["text_end"]:
                raise ValueError("codepage record at 0x%X ends inside a "
                                 "two-byte token" % offset)
            token = byte | (blob[position + 1] << 8)
            position += 2
        slot = subtitles.token_slot(
            token, meta.get("glyph_base", 0), meta.get("glyph_count", 0))
        if slot is not None:
            named = (alphabet or {}).get(slot)
            parts.append(named if named else "<%04X>" % token)
            continue
        if token in accents:
            parts.append(accents[token])
            continue
        width = subtitles.RECORD_PARAMETERS.get(token, 0)
        if width:
            payload = bytes(blob[position:position + width])
            parts.append("<%04X:%s>" % (token, payload.hex().upper()))
            position += width
            continue
        parts.append(dcms.decode_english_tokens([token]))
    return "".join(parts), position - start

RUN_BOUNDARIES = frozenset({0x808E})


def codepage_record_runs(blob, meta, offset):
    """The glyph slots the record at *offset* draws, split into runs."""
    from . import vp2_cutscene_subtitles as subtitles

    base, count = meta["glyph_base"], meta["glyph_count"]
    position = meta["text_start"] + offset
    runs, current = [], []
    while position < meta["text_end"]:
        byte = blob[position]
        if byte == 0:
            break
        if byte < 0x80:
            token, position = byte, position + 1
        else:
            token, position = byte | (blob[position + 1] << 8), position + 2
        slot = subtitles.token_slot(token, base, count)
        if slot is not None:
            current.append(slot)
            continue
        position += subtitles.RECORD_PARAMETERS.get(token, 0)
        if token in RUN_BOUNDARIES:
            runs.append(current)
            current = []
    runs.append(current)
    return runs


def codepage_run_tokens(blob, meta, offset, alphabet):
    """Per-run ``{character: token}`` maps for the record at *offset*."""
    from . import vp2_cutscene_subtitles as subtitles

    base = meta["glyph_base"]
    # The same rule `render_codepage` decodes by: a mixed record's local
    # glyphs came back as <XXXX> tags, which re-encode on their own.
    local = codepage_record_is_local(blob, meta, offset)
    runs = []
    for run in codepage_record_runs(blob, meta, offset):
        current = {}
        for slot in (run if local else ()):
            character = alphabet.get(slot)
            if character and character not in current:
                current[character] = subtitles.slot_token(slot, base)
        runs.append(current)
    return runs


def codepage_font_cuts(blob, meta, alphabet):
    count = meta["glyph_count"]
    parent = list(range(count))

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for _message_id, offset in entries(blob, meta):
        for run in codepage_record_runs(blob, meta, offset):
            for slot in run[1:]:
                first, other = find(run[0]), find(slot)
                if first != other:
                    parent[first] = other
    cut_of_slot = [find(slot) for slot in range(count)]
    cuts = {}
    for slot in range(count):
        character = alphabet.get(slot)
        if character:
            cuts.setdefault(cut_of_slot[slot], {}).setdefault(character, slot)
    return cut_of_slot, cuts


def codepage_text_runs(text):
    """Split authored text into the runs a record will draw it in."""
    runs, current, position = [], [], 0
    while position < len(text):
        tag = CODEPAGE_TAG.match(text, position)
        if not tag:
            current.append(text[position])
            position += 1
            continue
        token = int(tag.group()[1:-1].partition(":")[0], 16)
        position = tag.end()
        if token in RUN_BOUNDARIES:
            runs.append("".join(current))
            current = []
    runs.append("".join(current))
    return runs


def local_codepage_tokens(alphabet, meta):
    """``{character: token}`` for encoding into a resource's own font."""
    from . import vp2_cutscene_subtitles as subtitles
    base = meta.get("glyph_base", 0)
    out = {}
    for slot in sorted(alphabet):
        character = alphabet[slot]
        if character and character not in out:
            out[character] = subtitles.slot_token(slot, base)
    return out


def encode_codepage(text, label="codepage text", accent_tokens=None,
                    local_tokens=None):
    """Encode proved shared-codepage characters and preserved control tags."""
    runs = local_tokens or {}
    runs = list(runs) if isinstance(runs, (list, tuple)) else [runs]
    run = 0
    output = bytearray()
    position = 0
    while position < len(text):
        tag = CODEPAGE_TAG.match(text, position)
        if tag:
            body = tag.group()[1:-1]
            value, _, payload = body.partition(":")
            token = int(value, 16)
            if token == 0:
                raise ValueError("%s contains <0000>, the record terminator"
                                 % label)
            if token < 0x80:
                output.append(token)
            else:
                output.extend(struct.pack("<H", token))
            if payload:
                output.extend(bytes.fromhex(payload))
            if token in RUN_BOUNDARIES:
                run = min(run + 1, len(runs) - 1)
            position = tag.end()
            continue
        character = text[position]
        token = runs[run].get(character)
        if token is None:
            token = (accent_tokens or {}).get(
                character, CODEPAGE_CHARACTERS.get(character))
        if token is None:
            raise ValueError(
                "%s uses unsupported shared-codepage character %r at text "
                "position %d; preserve an unknown value as <XXXX>, or wait "
                "for the shared-font glyph mapping" %
                (label, character, position))
        if token < 0x80:
            output.append(token)
        else:
            output.extend(struct.pack("<H", token))
        position += 1
    output.append(0)
    return bytes(output)

def codepage_semantic_text(text, accent_tokens=None, local_tokens=None,
                           meta=None, alphabet=None):
    """Render editable codepage text as it will read back from the game."""
    payload = encode_codepage(text, accent_tokens=accent_tokens,
                              local_tokens=local_tokens)
    frame = {"text_start": 0, "text_end": len(payload)}
    if meta is not None:
        frame["glyph_base"] = meta.get("glyph_base", 0)
        frame["glyph_count"] = meta.get("glyph_count", 0)
    rendered, _ = render_codepage(
        payload, frame, 0, accent_tokens=accent_tokens, alphabet=alphabet)
    return rendered

def shared_codepage_advances(archive, accent_tokens=None):
    """Return the rendered pixel advance for each mapped shared-font glyph."""
    from . import vp2_text_patch as text_patch

    _, _, _, _, expanded, font = text_patch.shared_font_stream(archive)
    advances = {}
    for token in range(1, 0x60):
        character = dcms.decode_english_tokens([token])
        if len(character) == 1:
            advances[character] = expanded[
                font["text_end"] + (token - 1) * 2]
    for character, token in (accent_tokens or {}).items():
        if 1 <= token <= font["glyph_count"]:
            advances[character] = expanded[
                font["text_end"] + (token - 1) * 2]
    return advances

def codepage_wrap_warnings(translations, advances, limit):
    """Return explicit translated lines that exceed a known screen width."""
    if limit <= 0:
        raise ValueError("codepage line-width warning needs a positive limit")
    warnings = []
    for message_id, row in translations.items():
        for line_number, line in enumerate(row["translated"].splitlines(), 1):
            visible = CODEPAGE_TAG.sub("", line)
            if not visible.strip() or any(c not in advances for c in visible):
                continue
            width = sum(advances[c] for c in visible)
            if width > limit:
                warnings.append((message_id, line_number, width, line))
    return warnings

class RegionOverflow(Exception):
    """The rebuilt text does not fit the region and growing it is refused."""

    def __init__(self, overflow):
        super().__init__("text region overflows by %d bytes" % overflow)
        self.overflow = overflow

_RECORD_LIMITS_PATH = os.path.join(
    os.fspath(PROJECT_ROOT),
    "data", "record-limits.csv")

def load_record_limits(path=_RECORD_LIMITS_PATH, scope="container"):
    """``{resource: max_extent}`` -- how far a bank's record data may reach."""
    limits = {}
    if not path or not os.path.exists(path):
        return limits
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                resource = int(row["resource"])
                extent = int(row["max_extent"])
            except (TypeError, ValueError, KeyError):
                continue
            if (row.get("scope") or "container").strip() != scope:
                continue
            kind = (row.get("kind") or "verified").strip() or "verified"
            limits[resource] = (extent, kind)
    return limits

RECORD_LIMITS = load_record_limits()

SCENE_CONTENT_LIMITS = load_record_limits(scope="scene-content")


def load_streamed_neighbour_exceptions(path=_RECORD_LIMITS_PATH):
    """``{resource: kind}`` -- streamed scenes allowed to reclaim a neighbour.

    These rows are permission rather than an extent, so ``max_extent`` is
    written as ``0``.
    """
    exceptions = {}
    if not path or not os.path.exists(path):
        return exceptions
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("scope") or "").strip() != "streamed-neighbours":
                continue
            try:
                resource = int(row["resource"])
            except (TypeError, ValueError, KeyError):
                continue
            exceptions[resource] = (row.get("kind") or "verified").strip() or "verified"
    return exceptions


#: Streamed scenes with a play-tested exception, by resource.
STREAMED_NEIGHBOUR_EXCEPTIONS = load_streamed_neighbour_exceptions()


class StreamedNeighbourReclaimed(ValueError):
    """A streamed scene rewrote a neighbour without a recorded play-test."""


def check_streamed_neighbours(resource, reclaimed, exceptions=None,
                              warn=None):
    """Refuse an unvouched-for neighbour rewrite; note a vouched-for one."""
    if not reclaimed:
        return
    table = (STREAMED_NEIGHBOUR_EXCEPTIONS if exceptions is None
             else exceptions)
    kind = table.get(resource)
    tags = ", ".join(sorted({tag for tag, _old, _new in reclaimed}))
    where = os.path.basename(_RECORD_LIMITS_PATH)
    if kind is None:
        raise StreamedNeighbourReclaimed(
            "resource #%d only fits its translated text by rewriting %s. "
            "The rewrite is lossless and reads back correctly, but it is "
            "not known to be safe when the game runs, so it is refused. "
            "Shorten or re-word this scene until it fits with %s left "
            "alone, or test this build in the scene and record the "
            "resource in %s with scope=streamed-neighbours."
            % (resource, tags, tags, where))
    if kind == "candidate":
        # stderr, like every other candidate notice here: the build driver
        # swallows a patcher's stdout, so a warning printed there is a
        # warning nobody receives.
        say = warn or (lambda text: print(text, file=sys.stderr))
        say("CANDIDATE: resource #%d rewrote %s and is under test, not "
            "released. Play the scene enough times to be confident, then "
            "change its row in %s to kind=verified -- or give the scene "
            "more room." % (resource, tags, where))


class SceneContentCeilingExceeded(ValueError):
    """A plain archive's content ends past the extent verified to run."""


def check_scene_content_extent(resource, content_end, pristine_allocation,
                               limits=None):
    """Refuse a plain scene whose content ends past its budget."""
    table = SCENE_CONTENT_LIMITS if limits is None else limits
    entry = table.get(resource)
    allowed, measured = (entry if entry else (pristine_allocation, None))
    if content_end <= allowed:
        return
    if measured == "candidate":
        print("CANDIDATE: resource #%d ends at %d, %d past the furthest "
              "played (%d). This build is for testing that extent, not for "
              "release. Enter this scene from a memory-card save enough "
              "times to mean something against a two-in-five failure rate, "
              "then change its row to kind=verified -- or lower it."
              % (resource, content_end, content_end - allowed, allowed),
              file=sys.stderr)
        return
    if measured:
        source = "the furthest extent verified to run in game"
    else:
        source = "this resource's pristine outer allocation"
    raise SceneContentCeilingExceeded(
        "resource #%d: its indexed content ends at %d and %d is %s. Give "
        "back %d byte(s) of translation, or play-test this resource and "
        "record the extent in data/record-limits.csv with "
        "scope=scene-content. Going past an unmeasured ceiling fails "
        "intermittently and does not look like a text bug."
        % (resource, content_end, allowed, source, content_end - allowed))

class RegionExtentExceeded(ValueError):
    """A bank's records reach past the extent verified to run."""

def check_record_extent(resource, extent, limits=None, warn=None):
    """Refuse or warn when the rebuilt records reach too far."""
    entry = (RECORD_LIMITS if limits is None else limits).get(resource)
    if not entry:
        return
    ceiling, kind = entry
    if extent <= ceiling:
        return
    if kind == "candidate":
        (warn or (lambda text: print(text, file=sys.stderr)))(
            "CANDIDATE: resource #%d reaches %d bytes into its text region, "
            "%d past the furthest played (%d). This build is for testing "
            "that extent, not for release. Exercise this bank's screen; if "
            "it holds, change its row to kind=verified, and if it does not, "
            "lower the row instead."
            % (resource, extent, extent - ceiling, ceiling))
        return
    if kind == "limit":
        raise RegionExtentExceeded(
            "resource #%d: its records reach %d bytes into the text region "
            "and %d is the furthest verified to run in game. This is a "
            "budget for the bank, not a limit on any one line -- shorten "
            "any %d bytes of translation, not necessarily the longest."
            % (resource, extent, ceiling, extent - ceiling))
    message = (
        "warning: resource #%d reaches %d bytes into its text region, %d "
        "past the furthest ever played (%d). That is untested, not known "
        "bad -- exercise this bank's screen in game, and raise its row in "
        "data/record-limits.csv once it is confirmed."
        % (resource, extent, extent - ceiling, ceiling))
    (warn or (lambda text: print(text, file=sys.stderr)))(message)

def _append_glyph_slot(blob, meta, resource):
    from . import vp2_cutscene_subtitles as subtitles

    count = meta["glyph_count"]
    if meta["text_end"] + count * 2 + 2 > meta["font_start"]:
        raise ValueError(
            "resource #%d is out of glyph slots: %d cut and the metric table "
            "holds %d" % (resource, count,
                          (meta["font_start"] - meta["text_end"]) // 2))
    try:
        subtitles.slot_token(count, meta["glyph_base"])
    except ValueError:
        raise ValueError(
            "resource #%d is out of glyph codes: slot %d at base 0x%X needs a "
            "code past 0xFF, and no record has been seen to address one"
            % (resource, count, meta["glyph_base"]))
    font_end = meta["font_start"] + count * GLYPH_BYTES
    blob[font_end:font_end] = bytearray(GLYPH_BYTES)
    struct.pack_into("<I", blob, 0x34, count + 1)
    struct.pack_into("<I", blob, 0x20, len(blob))
    meta["glyph_count"] = count + 1
    return count


def _compose_into_cut(blob, meta, cut, character, resource):
    from . import vp2_cutscene_subtitles as subtitles
    from . import vp2_glyph_compose as glyph_compose

    recipe = glyph_compose.COMPOSITES.get(character)
    if recipe is None or recipe[1] not in subtitles.ACCENT_MARKS:
        raise ValueError(
            "resource #%d cannot draw %r: the face that would draw it does "
            "not hold it, and it is not a mark over a letter" %
            (resource, character))
    base, donor, _position = recipe
    base_slot = cut.get(base)
    if base_slot is None:
        raise ValueError(
            "resource #%d cannot draw %r: the face that would draw it has no "
            "%r to put the %s over" % (resource, character, base, donor))
    origin = meta["font_start"] + base_slot * GLYPH_BYTES
    stamp = subtitles.ACCENT_MARKS[donor]
    block = bytes(glyph_compose.compose_character(
        bytes(blob[origin:origin + GLYPH_BYTES]), character,
        glyph_compose.unpack(stamp["pixels"]), stamp["rows"],
        donor_bottom=stamp.get("donor_bottom")))
    metric = bytes(blob[meta["text_end"] + base_slot * 2:
                        meta["text_end"] + base_slot * 2 + 2])
    return block, metric, "%r mark over the face's own %r" % (donor, base)


def grow_codepage_font(blob, meta, alphabet, replacements, resource):
    from . import vp2_cutscene_subtitles as subtitles

    blob = bytearray(blob)
    meta = dict(meta)
    alphabet = dict(alphabet)
    cut_of, cuts = codepage_font_cuts(blob, meta, alphabet)
    record_runs = {offset: codepage_record_runs(blob, meta, offset)
                   for _message_id, offset in entries(blob, meta)}

    def faces(runs):
        """The cut each run draws from; an empty run inherits the last one."""
        out, last = [], None
        for run in runs:
            last = cut_of[run[0]] if run else last
            out.append(last)
        return out

    drawn_locally = {offset: codepage_record_is_local(blob, meta, offset)
                     for offset in record_runs}

    kept, wanted = set(), {}
    for offset, runs in sorted(record_runs.items()):
        if offset not in replacements or not drawn_locally[offset]:
            for run in runs:
                kept.update(run)
            continue
        text_runs = codepage_text_runs(replacements[offset])
        if len(text_runs) != len(runs):
            raise ValueError(
                "resource #%d record at 0x%X draws %d run(s) and its "
                "translation has %d: a run-boundary tag was added or lost"
                % (resource, offset, len(runs), len(text_runs)))
        for text_run, cut_id in zip(text_runs, faces(runs)):
            for character in text_run:
                if character == "\n":
                    continue
                if cut_id is None:
                    raise ValueError(
                        "resource #%d record at 0x%X draws %r in a run that "
                        "held no glyph, so nothing says which face to cut it "
                        "in" % (resource, offset, character))
                slot = cuts[cut_id].get(character)
                if slot is None:
                    wanted.setdefault(cut_id, set()).add(character)
                else:
                    kept.add(slot)

    free = {}
    for slot in range(meta["glyph_count"]):
        if slot not in kept:
            free.setdefault(cut_of[slot], []).append(slot)

    recut = []
    for cut_id in sorted(wanted):
        for character in sorted(wanted[cut_id]):
            block, metric, source = _compose_into_cut(
                blob, meta, cuts[cut_id], character, resource)
            spare = free.get(cut_id)
            if spare:
                slot = spare.pop(0)
                released = alphabet.get(slot)
            else:
                slot = _append_glyph_slot(blob, meta, resource)
                cut_of.append(cut_id)
                released = None
            origin = meta["font_start"] + slot * GLYPH_BYTES
            blob[origin:origin + GLYPH_BYTES] = block
            blob[meta["text_end"] + slot * 2:
                 meta["text_end"] + slot * 2 + 2] = metric
            alphabet[slot] = character
            cuts[cut_id][character] = slot
            recut.append({"character": character, "slot": slot,
                          "source": source, "released": released})

    base = meta["glyph_base"]
    runs_by_offset = {}
    for offset in replacements:
        if not drawn_locally[offset]:
            runs_by_offset[offset] = [{} for _run in record_runs[offset]]
            continue
        runs_by_offset[offset] = [
            {} if cut_id is None else
            {character: subtitles.slot_token(slot, base)
             for character, slot in cuts[cut_id].items()}
            for cut_id in faces(record_runs[offset])]
    return blob, meta, alphabet, runs_by_offset, recut


def rebuild_codepage_records(blob, resource, translations,
                             accent_tokens=None, keep_region=False,
                             alphabet=None):
    """Replace indexed codepage records and relocate the whole text region."""
    if not translations:
        return bytearray(blob), 0
    blob = bytearray(blob)
    meta, messages = read_messages(
        bytes(blob), resource, codepage_accents=accent_tokens)
    by_id = {str(message["message_id"]): message for message in messages}
    unknown = sorted(set(translations) - set(by_id))
    if unknown:
        raise ValueError("resource #%d sheet names unknown message(s): %s" %
                         (resource, ", ".join(unknown[:12])))
    wrong = [key for key in translations if by_id[key]["kind"] != "codepage"]
    if wrong:
        raise ValueError("resource #%d message(s) are not codepage records: %s"
                         % (resource, ", ".join(sorted(wrong)[:12])))

    messages_by_offset = {}
    for message in messages:
        messages_by_offset.setdefault(message["offset"], []).append(message)
    replacements = {}
    for key, row in translations.items():
        message = by_id[key]
        offset = message["offset"]
        text = row["translated"]
        previous = replacements.get(offset)
        if previous is not None and previous[0] != text:
            aliases = ", ".join(str(item["message_id"])
                                for item in messages_by_offset[offset])
            raise ValueError("resource #%d messages %s share one record but "
                             "their translations differ" % (resource, aliases))
        replacements[offset] = (text, message["byte_length"])

    runs_by_offset, recut = {}, []
    if alphabet is not None:
        blob, meta, alphabet, runs_by_offset, recut = grow_codepage_font(
            blob, meta, alphabet,
            {offset: text for offset, (text, _length) in replacements.items()},
            resource)
        for row in recut:
            print("  cut %r into resource #%d slot %d, %s%s"
                  % (row["character"], resource, row["slot"], row["source"],
                     "" if row["released"] is None
                     else ", reusing the slot %r had" % row["released"]))

    encoded = {
        offset: encode_codepage(
            text,
            "resource #%d message %s" % (
                resource,
                ",".join(str(item["message_id"])
                         for item in messages_by_offset[offset])),
            accent_tokens=accent_tokens,
            local_tokens=runs_by_offset.get(offset))
        for offset, (text, _) in replacements.items()
    }
    if all(len(encoded[offset]) <= old_length
           for offset, (_, old_length) in replacements.items()):
        for offset, payload in encoded.items():
            old_length = replacements[offset][1]
            start = meta["text_start"] + offset
            blob[start:start + old_length] = (
                payload + bytes(old_length - len(payload)))
        _, check = read_messages(
            bytes(blob), resource, codepage_accents=accent_tokens,
            alphabet=alphabet)
        check_by_id = {str(message["message_id"]): message for message in check}
        failures = []
        for key, row in translations.items():
            expected = codepage_semantic_text(
                row["translated"], accent_tokens=accent_tokens,
                local_tokens=runs_by_offset.get(by_id[key]["offset"]),
                meta=meta, alphabet=alphabet)
            if check_by_id.get(key, {}).get("original_en") != expected:
                failures.append(key)
        if failures:
            raise ValueError("resource #%d codepage read-back mismatch for: %s"
                             % (resource, ", ".join(failures[:12])))
        return blob, len(replacements)

    region_size = meta["text_end"] - meta["text_start"]
    text_region = bytes(blob[meta["text_start"]:meta["text_end"]])
    indexed_end = max(message["offset"] + message["byte_length"]
                      for message in messages)
    nonzero_end = len(text_region.rstrip(bytes(1)))
    if nonzero_end < region_size:
        nonzero_end += 1
    used_end = max(indexed_end, nonzero_end)
    if keep_region:
        budget = region_size - used_end
        costs = sorted(
            ((len(encoded[offset]) - old_length, offset)
             for offset, (_text, old_length) in replacements.items()),
            key=lambda pair: pair[0])
        spent, dropped = 0, []
        for cost, offset in costs:
            if spent + cost <= budget:
                spent += cost
            else:
                dropped.append(offset)
        for offset in dropped:
            del replacements[offset]
            del encoded[offset]
        if dropped:
            print("  region budget: %d byte(s) spare, %d translation(s) kept, "
                  "%d left out to keep the container its original size"
                  % (budget, len(replacements), len(dropped)))
    offsets = sorted(messages_by_offset)

    def _rebuild_region(active):
        """Rebuild the region, emitting one copy of each repeated record."""
        moved, rebuilt, emitted, shared = {}, bytearray(), {}, 0
        for index, offset in enumerate(offsets):
            limit = offsets[index + 1] if index + 1 < len(offsets) else used_end
            if not offset <= limit <= used_end:
                raise ValueError(
                    "resource #%d has invalid message offsets" % resource)
            segment = bytes(blob[meta["text_start"] + offset:
                                 meta["text_start"] + limit])
            replacement = replacements.get(offset) if offset in active else None
            if replacement is not None:
                _text, old_length = replacement
                if old_length > len(segment):
                    raise ValueError(
                        "resource #%d message at 0x%X overlaps the "
                        "next record" % (resource, offset))
                segment = encoded[offset] + segment[old_length:]
                previous = emitted.get(segment)
                if previous is not None:
                    moved[offset] = previous
                    shared += 1
                    continue
                emitted[segment] = len(rebuilt)
            moved[offset] = len(rebuilt)
            rebuilt += segment
        return rebuilt, moved, shared

    rebuilt, moved, shared = _rebuild_region(set(replacements))
    check_record_extent(resource, len(rebuilt))

    if len(rebuilt) > region_size and keep_region:
        raise RegionOverflow(len(rebuilt) - region_size)
    if len(rebuilt) > region_size:
        growth = _round_up(len(rebuilt) - region_size, 16)
        print("  text region grown by %d bytes (resource #%d): the container "
              "now expands to more than it did" % (growth, resource))
        tail = bytes(blob[meta["text_end"]:])
        blob = (bytearray(blob[:meta["text_end"]]) + bytearray(growth) +
                bytearray(tail))
        struct.pack_into("<I", blob, 0x2C, meta["text_end"] + growth)
        if meta["font_start"]:
            struct.pack_into("<I", blob, 0x30, meta["font_start"] + growth)
        struct.pack_into("<I", blob, 0x20, len(blob))
        meta = layout(bytes(blob))
    start = meta["text_start"]
    blob[start:start + len(rebuilt)] = rebuilt
    blob[start + len(rebuilt):meta["text_end"]] = bytes(
        meta["text_end"] - start - len(rebuilt))
    for position in range(meta["table_start"], meta["text_start"], 8):
        _, old_offset = struct.unpack_from("<II", blob, position)
        if old_offset in moved:
            struct.pack_into("<I", blob, position + 4, moved[old_offset])

    # A semantic read-back catches an encoder/relocation mismatch before the
    # much larger ISO is copied.  Tags are canonicalised to upper-case.
    _, check = read_messages(
        bytes(blob), resource, codepage_accents=accent_tokens,
        alphabet=alphabet)
    check_by_id = {str(message["message_id"]): message for message in check}
    failures = []
    for key, row in translations.items():
        if by_id[key]["offset"] not in replacements:
            continue
        expected = codepage_semantic_text(
            row["translated"], accent_tokens=accent_tokens,
            local_tokens=runs_by_offset.get(by_id[key]["offset"]),
            meta=meta, alphabet=alphabet)
        if check_by_id.get(key, {}).get("original_en") != expected:
            failures.append(key)
    if failures:
        raise ValueError("resource #%d codepage read-back mismatch for: %s" %
                         (resource, ", ".join(failures[:12])))
    return blob, len(replacements)

GLYPH_BYTES = 448
SHARED_FONT_ENTRY = 8

def cmd_codepage(args):
    """Render entry 8's glyphs captioned with the byte that draws each one."""
    from . import vp2_jp_glyphs as jg
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        blob = unpack_container_entry(
            bytes(read_entry(handle, table, total, SHARED_FONT_ENTRY)),
            SHARED_FONT_ENTRY)
    meta = layout(blob)
    font = struct.unpack_from("<I", blob, 0x30)[0]
    rows = []
    for slot in range(meta["glyph_count"]):
        rows.append({
            "glyph": "%02X" % (slot + meta["glyph_base"]),
            "block": bytes(blob[font + slot * GLYPH_BYTES:
                                font + (slot + 1) * GLYPH_BYTES]),
        })
    paths = jg.render_sheets(rows, args.outdir, args.columns,
                             args.columns * 3, args.scale, "codepage",
                             caption=lambda row: row["glyph"])
    for path in paths:
        print(path)
    print("%d glyphs; the caption is the codepage byte, and what it draws is "
          "what vp2_dcms.decode_english_tokens should say" % len(rows))
    mismatch = [row["glyph"] for row in rows
                if len(dcms.decode_english_tokens([int(row["glyph"], 16)])) != 1]
    print("unnamed codes:", " ".join(mismatch) if mismatch else "none")

def japanese_text(iso_path, resource, glyph_table, names_path=None,
                  subresource=None):
    """Return ``{string message id: Japanese}`` for a Japanese text bank."""
    import hashlib
    from . import vp2_cutscene_subtitles as subs
    from . import vp2_jp_glyphs as jg
    from .vp2_scene_fingerprint import token_slot

    named = jg.load_glyph_names(glyph_table, names_path)
    with open(iso_path, "rb") as handle:
        _, total, table = triace.load_table(handle)
        blob = unpack_container_entry(
            bytes(read_entry(handle, table, total, resource)), resource,
            subresource)
    meta = layout(blob)
    font = struct.unpack_from("<I", blob, 0x30)[0]
    names = []
    for index in range(meta["glyph_count"]):
        block = bytes(blob[font + index * GLYPH_BYTES:
                           font + (index + 1) * GLYPH_BYTES])
        if not any(block):
            names.append(" ")
            continue
        names.append(named.get(hashlib.sha1(block).hexdigest(), ""))

    span = meta["text_end"] - meta["text_start"]
    pairs = [(mid, off) for mid, off in entries(blob, meta) if off < span]
    offsets = sorted({off for _, off in pairs})
    following = {off: (offsets[i + 1] if i + 1 < len(offsets) else span)
                 for i, off in enumerate(offsets)}
    parse_meta = {"glyph_base": meta["glyph_base"],
                  "glyph_count": meta["glyph_count"]}
    out = {}
    for message_id, offset in pairs:
        key = str(message_id)
        if key in out:
            continue
        record = bytes(blob[meta["text_start"] + offset:
                            meta["text_start"] + following[offset]])
        pieces = []
        for _, _, tokens in subs.parse_record(record, parse_meta):
            for token in tokens:
                if token == LINE_BREAK:
                    pieces.append("\n")
                    continue
                slot = token_slot(token, meta["glyph_base"],
                                  meta["glyph_count"])
                if slot is None or not 0 <= slot < len(names):
                    continue
                pieces.append(names[slot] or "〓")
        out[key] = "".join(pieces)
    return out

def local_alphabet(blob, meta, resource=None):
    from . import vp2_cutscene_subtitles as subtitles
    names, _rejects = subtitles.load_name_corrections()
    start = meta.get("font_start") or 0
    count = meta.get("glyph_count") or 0
    if not start or not count:
        return {}
    out = {}
    for slot in range(count):
        block = bytes(blob[start + slot * GLYPH_BYTES:
                           start + (slot + 1) * GLYPH_BYTES])
        name = names.get(hashlib.sha1(block).hexdigest())
        if name:
            out[slot] = name
    for slot, name in enumerate(SLOT_NAMES.get(resource) or []):
        if name and slot < count:
            out[slot] = name
    return out


def resource_alphabet(blob, resource):
    """The resource's own font names, or ``{}`` when it has no font header."""
    if len(blob) < 0x54:
        return {}
    return local_alphabet(bytearray(blob), layout(bytearray(blob)), resource)


def read_messages(blob, resource, slots=None, codepage_accents=None,
                  alphabet=None):
    """Every message, decoded with whichever record format it uses."""
    meta = layout(blob)
    if slots is None:
        slots = SLOT_NAMES.get(resource, [])
    rows = []
    for message_id, offset in entries(blob, meta):
        if offset >= meta["text_end"] - meta["text_start"]:
            continue
        start = meta["text_start"] + offset
        # A token record's first byte is the low half of 0x01xx, so the byte
        # after it is 0x01.  A codepage record never has that shape.
        token_like = (start + 1 < len(blob) and blob[start + 1] == 0x01)
        if token_like and slots:
            text, length = render_tokens(blob, meta, offset, slots)
            kind = "token"
        else:
            text, length = render_codepage(
                blob, meta, offset, accent_tokens=codepage_accents,
                alphabet=alphabet)
            kind = "codepage"
        rows.append({"resource": resource, "message_id": message_id,
                     "offset": offset, "byte_length": length, "kind": kind,
                     "original_en": text})
    return meta, rows

def walk_block(blob, meta, slots):
    """Every token record in the block, continuations included."""
    referenced = {}
    for message_id, offset in entries(blob, meta):
        referenced.setdefault(offset, message_id)
    first = min((o for o in referenced
                 if meta["text_start"] + o + 1 < len(blob)
                 and blob[meta["text_start"] + o + 1] == 0x01), default=None)
    if first is None:
        return []
    out, position = [], meta["text_start"] + first
    parent, suffix = None, 0
    while position < meta["text_end"]:
        begin = position
        while position < meta["text_end"] and blob[position] != 0:
            position += 2
        position += 1
        offset = begin - meta["text_start"]
        text, _ = render_tokens(blob, meta, offset, slots)
        if offset in referenced:
            parent, suffix = referenced[offset], 0
            key = str(parent)
        else:
            suffix += 1
            key = "%s+%d" % (parent, suffix)
        out.append({"key": key, "offset": offset,
                    "byte_length": position - begin, "original_en": text})
        if position >= meta["text_end"] or (
                not any(blob[position:position + 8])):
            break
    return out

def cmd_export(args):
    subresource = getattr(args, "subresource", None)
    section_tag = None
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        blob = container(handle, table, total, args.resource, subresource)
        if subresource is not None:
            section_tag = pk1_section_tag(
                bytes(read_entry(handle, table, total, args.resource)),
                subresource)
    meta, rows = read_messages(
        blob, args.resource,
        alphabet=resource_alphabet(blob, args.resource))
    slots = SLOT_NAMES.get(args.resource, [])
    keep = [dict(row, key=str(row["message_id"]))
            for row in rows if row["original_en"].strip()
            and row["kind"] != "token"]
    if slots:
        for record in walk_block(blob, meta, slots):
            if record["original_en"].strip():
                keep.append(dict(record, resource=args.resource, kind="token",
                                 message_id=record["key"]))
    existing = {}
    if os.path.exists(args.csv):
        with open(args.csv, newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                if (row.get("translated") or "").strip():
                    existing[row["message_id"]] = row["translated"]
    japanese = {}
    if getattr(args, "jp_iso", None):
        japanese = japanese_text(
            args.jp_iso, args.resource,
            getattr(args, "jp_glyphs", None),
            getattr(args, "jp_names", None),
            section_tag)
    fields = ["resource", "message_id", "kind", "offset", "byte_length",
              "original_en", "original_jp", "translated"]
    keep.sort(key=lambda r: r["offset"])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in keep:
        message_id = row.get("message_id")
        row = {field: row.get(field, "") for field in fields}
        row["original_jp"] = japanese.get(str(message_id), "")
        row["translated"] = existing.get(str(row["message_id"]), "")
        writer.writerow(row)
    with open(args.csv, "w", newline="", encoding="utf-8-sig") as target:
        target.write(buffer.getvalue())
    kinds = {}
    for row in keep:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    print("resource #%d: %d messages (%s) -> %s"
          % (args.resource, len(keep),
             ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())),
             args.csv))
    if existing:
        print("  carried %d existing translation(s) forward" % len(existing))
    print("  text region %d..%d (%d bytes), font %d slots at %d"
          % (meta["text_start"], meta["text_end"],
             meta["text_end"] - meta["text_start"],
             meta["glyph_count"], meta["font_start"]))

def cmd_budget(args):
    """Report the text-region room and any resource-local token font."""
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        blob = container(handle, table, total, args.resource)
    meta, rows = read_messages(blob, args.resource)
    slots = SLOT_NAMES.get(args.resource, [])
    token_rows = [r for r in rows if r["kind"] == "token"]
    used = set("".join(r["original_en"] for r in token_rows)) - {"\n"}
    # slots the credits cut owns are not ours to release
    cut_start = min((i for i, c in enumerate(slots)
                     if c in used and i >= 48), default=48)
    owned = {i for i, c in enumerate(slots) if i >= 48}
    print("resource #%d: %d token records, %d codepage records"
          % (args.resource, len(token_rows), len(rows) - len(token_rows)))
    if token_rows:
        print("  token characters English uses: %s" % "".join(sorted(used)))
        print("  slots 0-47 belong to the credits cut and cannot be released")
        print("  slots %d-%d are the cutscene's own: %d"
              % (min(owned), max(owned), len(owned)))
    else:
        visible = sorted(set("".join(r["original_en"] for r in rows)) - {"\n"})
        print("  shared-codepage characters : %s" % "".join(visible))
        print("  no resource-local token font; text uses shared entry #8")
    last = max((meta["text_start"] + r["offset"] + r["byte_length"])
               for r in rows)
    print("  text region ends at %d; region runs to %d, font starts at %d"
          % (last, meta["text_end"], meta["font_start"]))
    print("  spare bytes after the last record: %d (+%d before the font)"
          % (meta["text_end"] - last, meta["font_start"] - meta["text_end"]))

def slot_lookup(slots, first=SUBTITLE_CUT_FIRST_SLOT):
    """character -> slot, restricted to the cut this text actually uses."""
    out = {}
    for index in range(len(slots) - 1, first - 1, -1):
        out[slots[index]] = index
    return out

def encode_tokens(text, lookup, label):
    """Encode a translation into tokens, keeping <XXXX> control codes."""
    tokens, position = [], 0
    while position < len(text):
        character = text[position]
        if character == "<" and text.find(">", position) > position:
            end = text.find(">", position)
            body = text[position + 1:end]
            try:
                tokens.append(int(body, 16))
                position = end + 1
                continue
            except ValueError:
                pass
        if character == "\n":
            tokens.append(LINE_BREAK)
        elif character in lookup:
            tokens.append(TOKEN_BASE + lookup[character])
        else:
            raise ValueError(
                "%s needs a glyph this container does not have: %r. Its font "
                "holds: %s" % (label, character,
                               "".join(sorted(set(lookup) - {"\n"}))))
        position += 1
    return b"".join(struct.pack("<H", t) for t in tokens) + b"\0"

def cmd_patch(args):
    if os.path.exists(args.output_iso) and not getattr(args, "dry_run", False) \
            and os.path.abspath(args.iso) != os.path.abspath(args.output_iso):
        raise ValueError("%s already exists" % args.output_iso)
    with open(args.csv, newline="", encoding="utf-8-sig") as source:
        supplied = {r["message_id"]: r for r in csv.DictReader(source)
                    if (r.get("translated") or "").strip()}
    iso = iso_buffer.IsoBuffer.from_path(args.iso)
    details = patch_resource_in_memory(
        iso, args.resource, supplied,
        shared_font_glyphs=getattr(args, "shared_font_glyphs", False),
        accent_donors_path=getattr(args, "accent_donors", None),
        warn_line_width=getattr(args, "warn_line_width", None),
        keep_region=getattr(args, "keep_region", False),
    )
    if not getattr(args, "dry_run", False):
        iso.commit(args.output_iso)

def record_kind(row):
    """Whether a sheet row is a ``token`` or a ``codepage`` record."""
    explicit = (row.get("record_kind") or "").strip()
    if explicit:
        return explicit
    legacy = (row.get("kind") or "").strip()
    return legacy if legacy in ("token", "codepage") else "token"


def patch_resource_in_memory(iso, resource, supplied, *,
                             shared_font_glyphs=False,
                             subresource=None,
                             accent_donors_path=None,
                             warn_line_width=None,
                             keep_region=False):
    """Patch a container resource in-place inside *iso* (IsoBuffer)."""
    kinds = {record_kind(row) for row in supplied.values()}
    invalid_kinds = kinds - {"token", "codepage"}
    if invalid_kinds:
        raise ValueError("sheet has unsupported record kind(s): %s" %
                         ", ".join(sorted(invalid_kinds)))
    codepage_rows = {key: row for key, row in supplied.items()
                     if record_kind(row) == "codepage"}
    rows = {key: row for key, row in supplied.items()
            if record_kind(row) == "token"}
    if not supplied:
        raise ValueError("sheet has no translated rows")
    accent_tokens = {}
    font_patch = None
    text_patch = None
    if shared_font_glyphs:
        from . import vp2_text_patch as text_patch
        accent_tokens = text_patch.SHARED_EXTENSION_TOKENS
    accents = (set("".join(row["translated"]
                           for row in codepage_rows.values())) &
               set(accent_tokens))
    raw = bytearray(iso.read_entry(resource))
    if accents or shared_font_glyphs:
        from . import vp2_text_patch as text_patch
        original_font = iso.read_entry(text_patch.SHARED_FONT_ENTRY)
        patched_font, font_info = text_patch.install_glyphs(
            original_font, accents, accent_tokens)
        font_patch = (patched_font, font_info)
    if warn_line_width is not None and codepage_rows:
        if font_patch:
            shared_font = font_patch[0]
        else:
            shared_font = iso.read_entry(SHARED_FONT_ENTRY)
        advances = shared_codepage_advances(shared_font, accent_tokens)
        for message_id, line_number, width, line in codepage_wrap_warnings(
                codepage_rows, advances, warn_line_width):
            print("warning: resource #%d message %s line %d is %d px wide "
                  "(limit %d); the game may auto-wrap it: %s" %
                  (resource, message_id, line_number, width,
                   warn_line_width, line))
    blob = bytearray(unpack_container_entry(bytes(raw), resource,
                                            subresource))

    codepage_alphabet = resource_alphabet(blob, resource) or None
    if not rows:
        blob, codepage_written = rebuild_codepage_records(
            blob, resource, codepage_rows,
            accent_tokens=accent_tokens, keep_region=keep_region,
            alphabet=codepage_alphabet)
        raw, details = pack_container_entry(raw, blob, resource,
                                           subresource)
        iso.write_entry(resource, raw)
        if font_patch and not font_patch[1].get("no_op"):
            iso.write_entry(SHARED_FONT_ENTRY, font_patch[0])
        return _finish_patch(iso, resource, raw, blob, codepage_written,
                             details, font_patch, subresource)

    meta = layout(blob)
    slots = SLOT_NAMES.get(resource)
    if not slots:
        raise ValueError("resource #%d has no slot names" % resource)
    lookup = slot_lookup(slots)
    _, messages = read_messages(bytes(blob), resource)
    finished = set()
    for record in walk_block(blob, meta, slots):
        row = rows.get(record["key"])
        finished |= set(row["translated"] if row else record["original_en"])
    missing = sorted(c for c in finished
                     if c not in lookup and c not in ("\n", "<", ">")
                     and not c.isdigit())
    if missing:
        spare = [i for i in range(SUBTITLE_CUT_FIRST_SLOT, len(slots))
                 if slots[i] not in finished and slots[i] != " "]
        anywhere = {}
        for index in range(len(slots) - 1, SUBTITLE_CUT_FIRST_SLOT - 1, -1):
            anywhere[slots[index]] = index
        from . import vp2_cutscene_subtitles as subtitles
        from . import vp2_glyph_compose as glyph_compose
        donors = subtitles.read_accent_donors(accent_donors_path)
        recut, grown_slots = [], 0
        for character in missing:
            source_slot = anywhere.get(character)
            block = metric = None
            source_description = None
            if (source_slot is None and
                    character in RESOURCE_10_EXACT_ACCENT_DONORS and
                    character in donors):
                block, metric, _ = donors[character]
                source_description = "PAL title-font donor"
            if source_slot is None and block is None:
                recipe = glyph_compose.COMPOSITES.get(character)
                if recipe is not None and recipe[1] in subtitles.ACCENT_MARKS:
                    base, donor, _position = recipe
                    base_slot = anywhere.get(base)
                    base_block = base_metric = None
                    if base_slot is not None:
                        origin = meta["font_start"] + base_slot * GLYPH_BYTES
                        base_block = bytes(blob[origin:origin + GLYPH_BYTES])
                        base_metric = bytes(
                            blob[meta["text_end"] + base_slot * 2:
                                 meta["text_end"] + base_slot * 2 + 2])
                    elif base in subtitles.POOL:
                        base_block = subtitles.POOL[base]["pixels"]
                        base_metric = subtitles.POOL[base]["metric"]
                    if base_block is not None:
                        stamp = subtitles.ACCENT_MARKS[donor]
                        block = bytes(glyph_compose.compose_character(
                            base_block, character,
                            glyph_compose.unpack(stamp["pixels"]),
                            stamp["rows"],
                            donor_bottom=stamp.get("donor_bottom")))
                        metric = base_metric
                        source_description = (
                            "composed %r over %r" % (donor, base))
            # Older path, kept for anything the composites do not cover.
            if (source_slot is None and block is None and
                    character in subtitles.ACCENTS):
                base, mark = subtitles.ACCENTS[character]
                base_slot = anywhere.get(base)
                if base_slot is not None:
                    origin = meta["font_start"] + base_slot * 448
                    local_base = bytes(blob[origin:origin + 448])
                    native = resource_10_marked_block(local_base, mark)
                    block = (native or
                             subtitles.accented_block(local_base, mark))
                    source_description = (
                        "PAL title-font %s over local %r" % (mark, base)
                        if native else "derived %s over local %r" % (mark, base))
                    metric = bytes(blob[meta["text_end"] + base_slot * 2:
                                        meta["text_end"] + base_slot * 2 + 2])
            if source_slot is None and block is None:
                pool_row = subtitles.POOL.get(character)
                if (pool_row is not None
                        and len(pool_row["pixels"]) == GLYPH_BYTES):
                    block = pool_row["pixels"]
                    metric = pool_row["metric"]
                    source_description = "glyph pool"
            if source_slot is None and block is None and character in donors:
                block, metric, _ = donors[character]
                source_description = "regional donor"
            if source_slot is None and block is None:
                raise ValueError(
                    "resource #%d cannot draw %r: no slot holds it, the donor "
                    "table does not carry it, and it cannot be derived"
                    % (resource, character))
            if not spare:
                count = struct.unpack_from("<I", blob, 0x34)[0]
                if meta["text_end"] + count * 2 + 2 > meta["font_start"]:
                    raise ValueError(
                        "resource #%d is out of glyph slots: %d cut and the "
                        "metric table holds %d" % (resource, count,
                        (meta["font_start"] - meta["text_end"]) // 2))
                blob += bytearray(448)
                struct.pack_into("<I", blob, 0x34, count + 1)
                struct.pack_into("<I", blob, 0x20, len(blob))
                slots.append("")
                spare.append(count)
                grown_slots += 1
            target = spare.pop(0)
            start = meta["font_start"] + target * 448
            if block is not None:
                blob[start:start + 448] = block
                blob[meta["text_end"] + target * 2:
                     meta["text_end"] + target * 2 + 2] = metric
                lookup[character] = target
                recut.append((character, source_description, target,
                               slots[target],
                               blob[meta["text_end"] + target * 2]))
                continue
            source = meta["font_start"] + source_slot * 448
            blob[start:start + 448] = blob[source:source + 448]
            metric = meta["text_end"] + target * 2
            blob[metric:metric + 2] = blob[meta["text_end"] + source_slot * 2:
                                           meta["text_end"] + source_slot * 2 + 2]
            lookup[character] = target
            recut.append((character, source_slot, target, slots[target],
                          blob[metric]))
        if grown_slots:
            print("  font: grown by %d slot(s) to %d"
                  % (grown_slots, struct.unpack_from("<I", blob, 0x34)[0]))
        for character, src, dst, was, width in recut:
            origin = "slot %d" % src if isinstance(src, int) else src
            print("  font: %r from %s into slot %d (was %r), advance %d"
                  % (character, origin, dst, was, width))
    block_start = min(m["offset"] for m in messages if m["kind"] == "token")
    region = meta["text_end"] - meta["text_start"]
    # walk the block sequentially: table entries do not cover the continuations
    records, position = [], meta["text_start"] + block_start
    while position < meta["text_start"] + region:
        begin = position
        while position < meta["text_start"] + region and blob[position] != 0:
            position += 2
        position += 1
        records.append((begin - meta["text_start"], bytes(blob[begin:position])))
        if position >= meta["text_start"] + region:
            break
        if blob[position - 1] == 0 and position - begin <= 1 and            all(b == 0 for b in blob[position:position + 8]):
            break
    by_offset = {}
    for record in walk_block(blob, meta, slots):
        by_offset.setdefault(record["offset"], []).append(
            {"message_id": record["key"], "offset": record["offset"]})
    written, moved, out = 0, {}, bytearray()
    for offset, original in records:
        payload = original
        for message in by_offset.get(offset, []):
            row = rows.get(str(message["message_id"]))
            if row is None:
                continue
            label = "message %s" % message["message_id"]
            payload = encode_tokens(row["translated"], lookup, label)
            written += 1
            print("  %-7s %r" % (message["message_id"], row["translated"]))
        moved[offset] = block_start + len(out)
        out += payload
    if block_start + len(out) > region:
        needed = block_start + len(out)
        grow = (needed - region + 15) // 16 * 16
        tail = bytes(blob[meta["text_end"]:])
        blob = bytearray(blob[:meta["text_end"]]) + bytearray(grow) + bytearray(tail)
        struct.pack_into("<I", blob, 0x2C, meta["text_end"] + grow)
        struct.pack_into("<I", blob, 0x30, meta["font_start"] + grow)
        struct.pack_into("<I", blob, 0x20, len(blob))
        print("  text region grown by %d bytes; metrics and font follow it" % grow)
        meta = layout(bytes(blob))
        region = meta["text_end"] - meta["text_start"]
    blob[meta["text_start"] + block_start:meta["text_start"] + block_start + len(out)] = out
    for position in range(meta["table_start"], meta["text_start"], 8):
        message_id, offset = struct.unpack_from("<II", blob, position)
        if offset in moved:
            struct.pack_into("<I", blob, position + 4, moved[offset])
    if not written:
        raise ValueError("no token records were translated")
    blob, codepage_written = rebuild_codepage_records(
        blob, resource, codepage_rows,
        accent_tokens=accent_tokens, keep_region=keep_region,
        alphabet=resource_alphabet(blob, resource) or None)
    raw, details = pack_container_entry(raw, blob, resource,
                                        subresource)
    iso.write_entry(resource, raw)
    if font_patch and not font_patch[1].get("no_op"):
        iso.write_entry(SHARED_FONT_ENTRY, font_patch[0])
    return _finish_patch(iso, resource, raw, blob, written + codepage_written,
                         details, font_patch, subresource)

def _finish_patch(iso, resource, raw, expected_blob, written, details,
                  font_patch, subresource=None):
    """Verify a write against the buffer and return the patch summary dict."""
    stored = iso.read_entry(resource)
    check = unpack_container_entry(bytes(stored), resource,
                                   subresource)
    if check != bytes(expected_blob):
        raise ValueError("output ISO container does not read back byte-for-byte")
    if font_patch and not font_patch[1].get("no_op"):
        from . import vp2_text_patch as text_patch
        expected_font, font_info = font_patch
        stored_font = iso.read_entry(SHARED_FONT_ENTRY)
        if bytes(stored_font) != bytes(expected_font):
            raise ValueError("shared-font entry does not read back byte-for-byte")
    shift = details.get("suffix_shift", 0)
    if shift:
        print("  %s stream suffix shifted %d bytes into trailing slack"
              % (details["wrapper"], shift))
    groups = details.get("groups")
    if groups and groups["first_group"] is not None:
        print("  original SLZ groups %d-%d rewritten; %d compressed byte(s) "
              "differ" % (groups["first_group"], groups["last_group"],
                           groups["changed_bytes"]))
    print("patched %d record(s); %s container %d -> %d compressed bytes"
          % (written, details["wrapper"], details["stored_before"],
             details["stored_after"]))
    if font_patch:
        from . import vp2_shared_font as shared_font
        print("  " + shared_font.describe_install(font_patch[1]))
    print("verified in memory")
    return {"written": written, "details": details, "font_patch": font_patch}

def cmd_probe_643(args):
    """Build the one-byte, no-recompression resource-643 diagnostic image."""
    if os.path.exists(args.output_iso) and not args.dry_run \
            and os.path.abspath(args.iso) != os.path.abspath(args.output_iso):
        raise ValueError("%s already exists" % args.output_iso)
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        original = bytes(read_entry(handle, table, total, 643))
    rebuilt, details = patch_643_literal_probe(original)
    if args.dry_run:
        print("dry run: resource #643 SLZ literal +0x%X; Party -> Tarty "
              "in messages 6, 15 and 23" % details["slz_byte_offset"])
        return
    print("one entry byte changed; no recompression")
    import shutil
    if os.path.abspath(args.iso) != os.path.abspath(args.output_iso):
        shutil.copyfile(args.iso, args.output_iso)
    with open(args.output_iso, "r+b") as target:
        _, total, table = triace.load_table(target)
        allocated = table[total + 643] * triace.SECTOR
        if len(rebuilt) != allocated:
            raise ValueError("resource #643 allocation changed")
        target.seek(table[643] * triace.SECTOR)
        target.write(rebuilt)
    with open(args.output_iso, "rb") as target:
        _, total, table = triace.load_table(target)
        check = bytes(read_entry(target, table, total, 643))
    if check != rebuilt:
        raise ValueError("output ISO resource #643 does not read back exactly")
    check_again, repeated = patch_643_literal_probe(original)
    if check_again != check or repeated != details:
        raise ValueError("resource #643 probe verification is not repeatable")
    print("resource #643: changed one original SLZ literal byte at entry +0x%X"
          % details["entry_byte_offset"])
    print("verified Party -> Tarty in messages 6, 15 and 23; no recompression")
    print("verified: %s" % args.output_iso)

def _subresource(value):
    """A PK1 row number, or the table tag that names it on any image."""
    try:
        return int(value, 0)
    except ValueError:
        return value


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write a translation sheet")
    export.add_argument("iso")
    export.add_argument("csv")
    export.add_argument("--resource", type=int, default=10)
    export.add_argument("--subresource", type=_subresource, default=None,
                        help="PK1 row number or table tag holding the bank, "
                             "for a resource whose text is not in the first "
                             "one")
    export.add_argument("--jp-iso", help="Japanese image to read original_jp from")
    export.add_argument("--jp-glyphs", help="the working glyph table holding the bitmaps")
    export.add_argument("--jp-names", help="digest-to-character names for Japanese glyphs")
    export.set_defaults(func=cmd_export)
    budget = commands.add_parser("budget", help="report glyph and byte room")
    budget.add_argument("iso")
    budget.add_argument("--resource", type=int, default=10)
    budget.set_defaults(func=cmd_budget)
    codepage = commands.add_parser(
        "codepage", help="render the shared font, labelled with its codepage byte")
    codepage.add_argument("iso")
    codepage.add_argument("outdir")
    codepage.add_argument("--scale", type=int, default=7)
    codepage.add_argument("--columns", type=int, default=16)
    codepage.set_defaults(func=cmd_codepage)
    patch = commands.add_parser("patch", help="write the translations into an ISO")
    patch.add_argument("iso")
    patch.add_argument("output_iso")
    patch.add_argument("csv")
    patch.add_argument("--resource", type=int, default=10)
    patch.add_argument("--accent-donors", default=None,
                       help="glyph table for accents (default: the data directory)")
    patch.add_argument(
        "--keep-region", action="store_true",
        help="never grow the container's text region: spend the slack it "
             "already has and leave out the translations that do not fit. "
             "Resource 652 needs this -- the save/load screen reads it and "
             "a grown 652 crashes the game.")
    patch.add_argument("--shared-font-glyphs", action="store_true",
                       help="install and encode the configured shared-font "
                            "extension profile in entry 8")
    patch.add_argument("--warn-line-width", type=int, metavar="PX",
                       help="warn when an explicit codepage line exceeds this "
                            "measured screen width in pixels")
    patch.add_argument("--dry-run", action="store_true",
                       help="rebuild and verify in memory without copying ISO")
    patch.set_defaults(func=cmd_patch)
    probe = commands.add_parser(
        "probe-643",
        help="change Party to Tarty through one original compressed literal")
    probe.add_argument("iso")
    probe.add_argument("output_iso")
    probe.add_argument("--dry-run", action="store_true",
                       help="verify the one-byte edit without copying an ISO")
    probe.set_defaults(func=cmd_probe_643)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, OSError) as error:
        print("error: %s" % error)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
