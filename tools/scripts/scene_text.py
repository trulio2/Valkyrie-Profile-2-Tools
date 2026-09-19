"""Scene text encoding, structured runs, and replacement planning."""

import csv
import difflib
import hashlib
import json
import os
import re
import struct

from . import normalize_sheet_newlines
from .scene_fonts import (
    glyph_bitmap, remap_punctuation_to_period,
)
from .scene_layout import (
    NPC_DIALOGUE_MAX_LINES, SUBTITLE_MAX_WIDTH,
    break_overflowing_run_junction, dialogue_max_lines,
    materialize_blank_line, preserve_input_icon_spacing,
    preserve_source_run_edges, preserve_translated_run_spacing,
    record_owns_authored_layout, wrap_structured_translations,
    wrap_translation,
)
from .scene_codec import pack_tokens
from .vp2_scene_fingerprint import PAGE_BREAK, PAGE_BREAK_TEXT, render_tokens
from .vp2_cutscene_subtitles import (
    ACCENT_DONORS_DEFAULT, ALLOWED_SUBSTITUTIONS, CODEPAGE_ONLY,
    CODEPAGE_TOKENS, CONTROL_SPELLING, EN_NAMES_DEFAULT, EventTextOverflow,
    FRAGMENT_MARKER, HARD_BREAK_TEXT, PAGE_BREAK_SPELLING, RAW_TOKEN,
    SCENE_COLUMNS, SPLIT_SUBTITLE_AUDIO, apply_hard_breaks,
    canonical_page_breaks, visible_characters,
)


SHARED_HEADER = re.compile(r"^<[^<>\r\n]+>$")


def row_uses_shared_header(row):
    """Whether a standalone angle-bracket heading uses the shared UI face."""
    return bool(SHARED_HEADER.fullmatch(
        (row.get("original_en") or "").strip()))


def run_uses_shared_header(text, tokens, metadata, alphabet):
    """Whether a structured run is a shared-UI heading in local chevrons."""
    if not SHARED_HEADER.fullmatch(clean_text(text).strip()):
        return False
    local = []
    for token in tokens:
        if token >= 0x8000:
            continue
        slot = token_slot(token, metadata["glyph_base"],
                          metadata["glyph_count"])
        if slot is None:
            continue
        character = alphabet.get(slot)
        if character is not None and not character.isspace():
            local.append(character)
    return set(local) == {"<", ">"}


def encode_shared_header(text, source_tokens, metadata, alphabet):
    """Keep the source chevrons and encode only their title in the UI face."""
    visible = []
    for index, token in enumerate(source_tokens):
        rendered, _, _ = render_tokens([token], metadata, alphabet)
        if clean_text(rendered) in ("<", ">"):
            visible.append((index, clean_text(rendered)))
    left = next((index for index, character in visible if character == "<"), None)
    right = next((index for index, character in reversed(visible)
                  if character == ">"), None)
    if left is None or right is None or left >= right:
        raise ValueError("shared UI heading has no preserved chevron frame")
    stripped = text.strip()
    if not (stripped.startswith("<") and stripped.endswith(">")):
        raise ValueError(
            "%r is a heading the game draws inside < and >; write them "
            "around the translation, as in <%s>" % (stripped, stripped))
    inner = stripped[1:-1]
    body = visible_text_tokens(
        inner, alphabet, metadata["glyph_base"], codepage=True)
    return pack_tokens(
        list(source_tokens[:left + 1]) + body + list(source_tokens[right:]))


def _facade_helper(name):
    from . import vp2_cutscene_subtitles as facade
    return getattr(facade, name)


def byte_tokens(*args, **kwargs):
    return _facade_helper("byte_tokens")(*args, **kwargs)


def clean_text(*args, **kwargs):
    return _facade_helper("clean_text")(*args, **kwargs)


def message_pointers(*args, **kwargs):
    return _facade_helper("message_pointers")(*args, **kwargs)


def parse_record(*args, **kwargs):
    return _facade_helper("parse_record")(*args, **kwargs)


def split_nonempty(*args, **kwargs):
    return _facade_helper("split_nonempty")(*args, **kwargs)


def token_slot(*args, **kwargs):
    return _facade_helper("token_slot")(*args, **kwargs)


def slot_token(*args, **kwargs):
    return _facade_helper("slot_token")(*args, **kwargs)


def glyph_advances(*args, **kwargs):
    return _facade_helper("glyph_advances")(*args, **kwargs)


def run_uses_local_font(tokens, metadata, alphabet):
    """Whether a run draws any visible glyph from its local font."""
    for token in tokens:
        slot = token_slot(token, metadata["glyph_base"],
                          metadata["glyph_count"])
        if slot is None:
            continue
        character = alphabet.get(slot)
        if character is None or not character.isspace():
            return True
    return False

def shared_codepage_owns_layout(run_faces, run_texts=None):
    """Whether the shared-codepage consumer lays a record out.

    A local-font run only claims the layout when it breaks its own line;
    without ``run_texts`` every local run is taken to claim it.
    """
    if not any(face is True for face in run_faces):
        return False
    texts = list(run_texts or ())
    for index, face in enumerate(run_faces):
        if face is False and "\n" in (texts[index] if index < len(texts)
                                      else "\n"):
            return False
    return True

def scene_required_local_glyphs(expanded, metadata, alphabet, rows):
    """Characters needed by translated runs that actually use this font."""
    pointers, next_offset = message_pointers(expanded, metadata)
    offsets = {message_id: offset for _, message_id, offset in pointers}
    needed = set()
    for row in rows:
        message_id = int(row["message_id"], 0)
        if message_id not in offsets:
            continue
        record_offset = offsets[message_id]
        record = bytes(expanded[
            metadata["text_start"] + record_offset:
            metadata["text_start"] + next_offset[record_offset]])
        runs = []
        for _start, _end, tokens in parse_record(record, metadata):
            text, _, _ = render_tokens(tokens, metadata, alphabet)
            if clean_text(text):
                runs.append((text, tokens))
        raw_targets = row["translated"].split(FRAGMENT_MARKER)
        targets = [fragment_target(part) for part in raw_targets]
        if len(targets) != len(runs):
            # ``run_replacements`` will report the structural mismatch. Keep
            # font planning conservative until it does.
            needed.update(visible_characters(
                row["translated"].replace(FRAGMENT_MARKER, "")))
            continue
        force_shared = row_uses_shared_header(row)
        for (source_text, tokens), target in zip(runs, targets):
            glyphs = [token for token in tokens if token < 0x8000]
            if (not force_shared
                    and not run_uses_shared_header(
                        source_text, glyphs, metadata, alphabet)
                    and run_uses_local_font(glyphs, metadata, alphabet)):
                if run_mixes_faces(glyphs, metadata, alphabet):
                    # Only the local glyphs the run keeps stay local.
                    local = local_run_characters(glyphs, metadata, alphabet)
                    needed.update(character
                                  for character in visible_characters(target)
                                  if character in local)
                else:
                    needed.update(visible_characters(target))
    return needed - CODEPAGE_ONLY

def encode_subtitle(row, alphabet, glyph_base, source_alphabet=None,
                    source_base=None, advances=None):
    """Re-encode a record's visible run, keeping the tokens around it."""
    source_tokens = [int(value, 16) for value in row["source_tokens"].split()]
    search = source_alphabet or alphabet
    search_base = glyph_base if source_base is None else source_base
    visible = [index for index, token in enumerate(source_tokens)
               if (slot := token_slot(token, search_base,
                                      max(search) + 1)) is not None
               and slot in search]
    if not visible:
        raise ValueError("audio %s has no visible source glyphs" % row["audio_id"])
    prefix = source_tokens[:visible[0]]
    suffix = source_tokens[visible[-1] + 1:]
    text = remap_punctuation_to_period(
        wrap_translation(row["translated"].strip(), source_tokens, advances),
        alphabet)
    try:
        body = visible_text_tokens(text, alphabet, glyph_base)
    except ValueError as unsupported:
        raise ValueError("audio %s needs unsupported subtitle glyphs: %s" %
                         (row["audio_id"],
                          str(unsupported).split(": ", 1)[-1])) from None
    return pack_tokens(prefix + body + suffix), text

def _encode_characters(segment, char_to_token, missing,
                       materialize_blank_rows=False):
    """Tokens for a stretch of plain text, digits decided run by run."""
    from . import vp2_shared_font
    displaced = vp2_shared_font.displaced_characters()
    tokens = []
    position = 0
    while position < len(segment):
        character = segment[position]

        if character.isdigit():
            end = position
            while end < len(segment) and segment[end].isdigit():
                end += 1
            run = segment[position:end]
            if all(digit in char_to_token for digit in run):
                tokens.extend(char_to_token[digit] for digit in run)
            elif all(digit in CODEPAGE_TOKENS for digit in run):
                tokens.extend(CODEPAGE_TOKENS[digit] for digit in run)
            else:
                missing.update(digit for digit in run
                               if digit not in char_to_token)
            position = end
            continue

        if character == "\n":
            if (materialize_blank_rows and position
                    and segment[position - 1] == "\n"):
                space = char_to_token.get(" ", CODEPAGE_TOKENS[" "])
                tokens.extend((space, space))
            tokens.append(0x8080)
        elif (character in char_to_token
              and not (character in displaced
                       and char_to_token[character]
                       == CODEPAGE_TOKENS.get(character))):
            tokens.append(char_to_token[character])
        elif character in displaced:
            raise vp2_shared_font.displaced_error(
                "the text", character, displaced[character])
        elif character in CODEPAGE_ONLY:
            tokens.append(CODEPAGE_TOKENS[character])
        else:
            missing.add(character)
        position += 1
    return tokens

def codepage_char_tokens():
    """``character -> token`` for a record drawn through the shared face."""
    from . import vp2_shared_font
    tokens = dict(CODEPAGE_TOKENS)
    tokens.update(vp2_shared_font.SHARED_EXTENSION_TOKENS)
    return tokens

def local_run_characters(tokens, metadata, alphabet):
    """The characters a run draws from its local font."""
    found = set()
    for token in tokens:
        slot = token_slot(token, metadata["glyph_base"],
                          metadata["glyph_count"])
        character = alphabet.get(slot) if slot is not None else None
        if character is not None and not character.isspace():
            found.add(character)
    return found

def run_frames_shared_heading(tokens, metadata, alphabet):
    """Whether a run's only local glyphs are the chevrons around a heading."""
    return local_run_characters(tokens, metadata, alphabet) <= {"<", ">"}

def run_local_face_is_ornament(tokens, metadata, alphabet):
    """Whether a run's local glyphs are punctuation the shared text wears."""
    characters = local_run_characters(tokens, metadata, alphabet)
    return bool(characters) and not any(character.isalnum()
                                        for character in characters)

def run_mixes_faces(tokens, metadata, alphabet):
    """Whether a run draws from both its local font and the shared face."""
    shared = any(
        token_slot(token, metadata["glyph_base"],
                   metadata["glyph_count"]) is None
        or alphabet.get(token_slot(token, metadata["glyph_base"],
                                   metadata["glyph_count"])) is None
        for token in tokens)
    return shared and bool(local_run_characters(tokens, metadata, alphabet))

def mixed_run_char_tokens(tokens, metadata, alphabet):
    """Shared-face tokens, overridden by the local ones a run already used."""
    char_tokens = codepage_char_tokens()
    for token in tokens:
        slot = token_slot(token, metadata["glyph_base"],
                          metadata["glyph_count"])
        character = alphabet.get(slot) if slot is not None else None
        # Whitespace is not a face choice: a local space is merely wider, and
        # overriding every space with it re-spaces the whole line.
        if character is not None and not character.isspace():
            char_tokens[character] = token
    return char_tokens

def visible_text_tokens(text, alphabet, glyph_base, codepage=False,
                        materialize_blank_rows=False, char_tokens=None):
    """The inverse of ``render_tokens`` for one run of text."""
    text = canonical_page_breaks(text)
    if HARD_BREAK_TEXT in text:
        raise ValueError(
            "%s reached the encoder unresolved" % HARD_BREAK_TEXT)
    if char_tokens is not None:
        char_to_token = char_tokens
    elif codepage:
        char_to_token = codepage_char_tokens()
    else:
        text = remap_punctuation_to_period(text, alphabet)
        char_to_token = {
            character: slot_token(slot, glyph_base)
            for slot, character in alphabet.items()
        }
    missing = set()

    def characters(segment):
        return _encode_characters(
            segment, char_to_token, missing,
            materialize_blank_rows=materialize_blank_rows)

    body, position = [], 0
    for match in CONTROL_SPELLING.finditer(text):
        body.extend(characters(text[position:match.start()]))
        raw = RAW_TOKEN.fullmatch(match.group())
        body.append(int(raw.group(1), 16) if raw else PAGE_BREAK)
        position = match.end()
    body.extend(characters(text[position:]))
    if missing:
        raise ValueError("unsupported visible glyphs: %s" % ", ".join(
            repr(character) for character in sorted(missing)))
    return body

def encode_visible_text(text, alphabet, glyph_base, codepage=False,
                        materialize_blank_rows=False, char_tokens=None):
    return pack_tokens(visible_text_tokens(text, alphabet, glyph_base,
                                           codepage=codepage,
                                           materialize_blank_rows=
                                           materialize_blank_rows,
                                           char_tokens=char_tokens))

def encode_visible_part(part, text, alphabet, glyph_base,
                        source_alphabet=None, source_base=None,
                        advances=None):
    """Replace a part's visible span while keeping its surrounding controls."""
    tokens = byte_tokens(part)
    search = source_alphabet or alphabet
    search_base = glyph_base if source_base is None else source_base
    visible = [index for index, token in enumerate(tokens)
               if (slot := token_slot(token, search_base, max(search) + 1))
               is not None and slot in search]
    if not visible:
        raise ValueError("event fragment has no visible source glyphs")
    wrapped = wrap_translation(text.strip(), tokens, advances)
    body = visible_text_tokens(wrapped, alphabet, glyph_base)
    return pack_tokens(tokens[:visible[0]] + body + tokens[visible[-1] + 1:]), wrapped

def row_visible_parts(row):
    value = row.get("visible_parts_json", "").strip()
    if not value:
        return []
    parts = json.loads(value)
    if not isinstance(parts, list):
        raise ValueError("visible_parts_json must contain a list")
    return parts

def text_substitutions(wanted, drawn):
    """Return [(asked for, drawn)] where the ISO differs from the CSV."""
    # Both sides, or the marker the read-back inserts is reported as a
    # substitution against nothing and buries the real differences.
    wanted = " ".join(wanted.replace(FRAGMENT_MARKER, " ").split())
    drawn = " ".join(drawn.replace(FRAGMENT_MARKER, " ").split())
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, wanted, drawn, autojunk=False).get_opcodes():
        if tag != "equal":
            changes.append((wanted[i1:i2], drawn[j1:j2]))
    return changes

def describe_substitutions(changes, characters):
    """Split a row's substitutions into the allowed fallback and the rest."""
    allowed, rejected = {}, []
    for wanted, drawn in changes:
        pair = (wanted, drawn)
        if pair in ALLOWED_SUBSTITUTIONS and wanted not in characters:
            allowed[pair] = allowed.get(pair, 0) + 1
        else:
            rejected.append(pair)
    return allowed, rejected

def blank_referenced_glyphs(expanded, layout, metadata,
                            source_expanded, source_layout, displayed=None):
    """Return ``{slot: {message ids}}`` for glyphs a record draws but lost."""
    pointers, next_offset = message_pointers(expanded, metadata)
    base, count = metadata["glyph_base"], metadata["glyph_count"]
    source_slots = source_layout["glyph_count"]
    lost = {}
    for _, message_id, offset in pointers:
        if displayed is not None and message_id not in displayed:
            continue
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        for _, _, part in split_nonempty(record):
            try:
                tokens = byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = token_slot(token, base, count)
                if slot is None or slot >= source_slots:
                    continue
                if any(glyph_bitmap(expanded, layout, slot)):
                    continue
                if any(glyph_bitmap(source_expanded, source_layout, slot)):
                    lost.setdefault(slot, set()).add(message_id)
    return lost

def replaced_spans(row):
    """Return the ``(offset, length)`` spans inside a record a patch rewrites."""
    if row["audio_id"].casefold() == SPLIT_SUBTITLE_AUDIO.casefold():
        return None
    record_offset = int(row["record_byte_offset"], 0)
    parts = row_visible_parts(row)
    if len(parts) > 1:
        return [(int(part["relative_offset"]), int(part["byte_length"]))
                for part in parts]
    return [(int(row["text_relative_offset"], 0) - record_offset,
             int(row["text_byte_length"], 0))]

def known_replaced_spans(rows):
    """Map message id -> rewritten spans, omitting rows that cannot say."""
    known = {}
    for row in rows:
        spans = replaced_spans(row)
        if spans is not None:
            known[int(row["message_id"])] = spans
    return known

def split_fragment_translation(text, count, audio_id):
    parts = [part.strip() for part in re.split(
        r"\s*%s\s*" % re.escape(FRAGMENT_MARKER), text.strip())]
    if len(parts) != count or not all(parts):
        raise ValueError(
            "audio %s spans %d event fragments; separate its translation "
            "fragments with %s" % (audio_id, count, FRAGMENT_MARKER))
    return parts

def replace_visible_subsequence(part, source_text, target_text,
                                alphabet, glyph_base, source_alphabet=None):
    """Replace one exact visible phrase while retaining surrounding controls."""
    tokens = byte_tokens(part)
    source = visible_text_tokens(source_text, source_alphabet or alphabet,
                                 glyph_base)
    target = visible_text_tokens(target_text, alphabet, glyph_base)
    matches = [index for index in range(len(tokens) - len(source) + 1)
               if tokens[index:index + len(source)] == source]
    if len(matches) != 1:
        raise ValueError("expected one %r token sequence, found %d" %
                         (source_text, len(matches)))
    index = matches[0]
    return pack_tokens(tokens[:index] + target + tokens[index + len(source):])

def split_subtitle_translation(text):
    """Split the one opening subtitle whose sentence spans two event parts."""
    parts = text.strip().split(". ", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            "audio %s translation must contain two sentences separated by "
            "'. '" % SPLIT_SUBTITLE_AUDIO)
    # The event itself draws the dash joining both fragments, so the first
    # replacement intentionally does not retain the CSV sentence period.
    return parts[0], parts[1]

def rebuild_event_text(expanded, metadata, replacements, grow=False):
    """Rewrite the indexed text region, optionally enlarging it."""
    pointers, next_offset = message_pointers(expanded, metadata)
    text_start, text_end = metadata["text_start"], metadata["text_end"]
    old_text = bytes(expanded[text_start:text_end])
    offsets = sorted(next_offset)
    new_text = bytearray()
    new_offsets = {}
    for old_offset in offsets:
        new_offsets[old_offset] = len(new_text)
        segment = bytearray(old_text[old_offset:next_offset[old_offset]])
        edits = sorted(replacements.get(old_offset, []), reverse=True)
        for relative, old_length, replacement, expected in edits:
            if bytes(segment[relative:relative + old_length]) != expected:
                raise ValueError("CSV subtitle bytes no longer match the source ISO")
            segment[relative:relative + old_length] = replacement
        new_text.extend(segment)
    capacity = text_end - text_start
    if len(new_text) > capacity:
        overflow = len(new_text) - capacity
        last_record = max(new_offsets.values()) if new_offsets else 0
        terminator = new_text.find(b"\0", last_record)
        needed = len(new_text) if terminator < 0 else terminator + 1
        if needed <= capacity and not any(new_text[capacity:]):
            del new_text[capacity:]
        elif grow:
            extra = overflow + (-overflow % 16)
            font_start = struct.unpack_from("<I", expanded, 0x30)[0]
            expanded[text_end:text_end] = b"\0" * extra
            struct.pack_into("<I", expanded, 0x2C, text_end + extra)
            struct.pack_into("<I", expanded, 0x30, font_start + extra)
            struct.pack_into("<I", expanded, 0x20, len(expanded))
            text_end += extra
            capacity += extra
            metadata["text_end"] = text_end
        else:
            raise EventTextOverflow(overflow)
    new_text.extend(b"\0" * (capacity - len(new_text)))
    expanded[text_start:text_end] = new_text
    for message_index, _, old_offset in pointers:
        struct.pack_into("<I", expanded,
                         metadata["table_start"] + message_index * 8 + 4,
                         new_offsets[old_offset])

def read_patch_rows(path, audio_ids):
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    selected = []
    for audio_id in audio_ids:
        matches = [row for row in rows if row["audio_id"].casefold() == audio_id.casefold()]
        if len(matches) != 1:
            raise ValueError("expected one CSV row for audio %s, found %d" %
                             (audio_id, len(matches)))
        row = matches[0]
        if not row["message_id"] or not row["translated"].strip():
            raise ValueError("audio %s has no patchable subtitle" % audio_id)
        selected.append(row)
    return selected

def is_scene_sheet(path):
    """A sheet from `vp2_cutscene_workflow.py sheet` carries no byte offsets."""
    with open(path, newline="", encoding="utf-8-sig") as source:
        fields = csv.DictReader(source).fieldnames or []
    return all(name in fields for name in SCENE_COLUMNS) \
        and "record_byte_offset" not in fields

CRLF = chr(13) + chr(10)
NEWLINE = chr(10)
UNDRAWN_MARK = "-"


def undrawn_placeholder(english, mark=UNDRAWN_MARK):
    out = []
    for line in english.replace(CRLF, NEWLINE).split(NEWLINE):
        if line.strip() == "---":
            out.append(line)
            continue
        out.append("<PART>".join(
            (" %s " % mark if piece.strip() else piece)
            for piece in line.split("<PART>")))
    return NEWLINE.join(out)


def undrawn_mark(english):
    words = english.replace("<PART>", " ")
    for character in words:
        if character.isascii() and character.isalpha():
            return character.lower()
    return None


def shared_mark(rows, hidden):
    counts = {}
    for row in rows:
        if row.get("message_id") in hidden:
            continue
        for character in (row.get("translated") or ""):
            if character.isascii() and character.isalpha():
                lowered = character.lower()
                counts[lowered] = counts.get(lowered, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda letter: (-counts[letter], letter))


def reclaim_undrawn_rows(rows, mark=None):
    from . import undrawn_records

    hidden = undrawn_records.reclaimable_message_ids(rows)
    if not hidden:
        return rows, 0
    chosen_for_scene = mark or shared_mark(rows, hidden)
    reclaimed = 0
    for row in rows:
        if row.get("message_id") not in hidden:
            continue
        if (row.get("translated") or "").strip():
            continue
        english = row.get("original_en") or ""
        if not english.strip():
            continue
        chosen = chosen_for_scene or undrawn_mark(english)
        if chosen is None:
            continue
        held = undrawn_placeholder(english, chosen)
        if len(held) >= len(english):
            continue  # a record too short to be worth holding
        row["translated"] = held
        reclaimed += 1
    return rows, reclaimed


def read_scene_rows(path, resource=None, *, primary_lookup=None,
                    reclaim_undrawn=False):
    """Read a scene sheet into the shape the patcher works in."""
    from . import normalize_sheet_newlines
    rows, _, _ = normalize_sheet_newlines.read_rows(path)
    if primary_lookup is not None:
        from .flag_duplicates import resolve_duplicates
        from .vp2_build import sheet_kind
        rows, _ = resolve_duplicates(rows, primary_lookup=primary_lookup,
                                     kind=sheet_kind(path))
    if reclaim_undrawn:
        rows, reclaimed = reclaim_undrawn_rows(rows)
        if reclaimed:
            print("  reclaimed %d record(s) this scene never draws" % reclaimed)
    rows = [row for row in rows
            if row.get("message_id") and row.get("translated", "").strip()]
    from .vp2_title_face import CHAPTER_RECORDS
    out = []
    for row in rows:
        index = int(row["resource"])
        if resource is not None and index != resource:
            continue
        if (index, int(row["message_id"])) in CHAPTER_RECORDS:
            continue
        out.append({
            "resource_index": str(index),
            "message_id": row["message_id"],
            "translated": row["translated"],
            "audio_id": row.get("audio_id") or "r%04d-m%04d" % (
                index, int(row["message_id"])),
        })
    return out

def drop_display_face(expanded, layout, alphabet, iso):
    """Unname any slot holding a chapter-title glyph, returning how many."""
    from . import vp2_title_face as title_face
    try:
        face, _ = title_face.donor_index(iso, skip_patched=True)
    except (ValueError, KeyError, IndexError, struct.error):
        return 0
    dropped = 0
    for slot in range(layout["glyph_count"]):
        if alphabet.get(slot) is None:
            continue
        block = glyph_bitmap(expanded, layout, slot)
        if not any(block):
            continue
        if hashlib.sha1(block).digest() in face:
            alphabet.pop(slot, None)
            dropped += 1
    return dropped

def verification_glyph_advances(expanded, layout, alphabet, iso):
    """Measure dialogue with the same face-only alphabet as the writer."""
    subtitle_alphabet = dict(alphabet)
    _facade_helper("drop_display_face")(
        expanded, layout, subtitle_alphabet, iso)
    advances = glyph_advances(
        expanded, layout["text_end"], subtitle_alphabet)
    for slot in sorted(subtitle_alphabet):
        if subtitle_alphabet[slot] != " ":
            continue
        advance = expanded[layout["text_end"] + slot * 2]
        if advance:
            advances[" "] = advance
            break
    return advances

_COMPOSED_DIGESTS = None


def composed_glyph_digests():
    """``{digest: character}`` for every accent this build can compose."""
    global _COMPOSED_DIGESTS
    if _COMPOSED_DIGESTS is not None:
        return _COMPOSED_DIGESTS
    from . import vp2_glyph_compose as glyph_compose
    from .vp2_cutscene_subtitles import ACCENT_MARKS, POOL

    digests = {}
    for character, (base, donor, _position) in glyph_compose.COMPOSITES.items():
        mark = ACCENT_MARKS.get(donor)
        row = POOL.get(base)
        if mark is None or row is None:
            continue
        try:
            block = glyph_compose.compose_character(
                row["pixels"], character,
                glyph_compose.unpack(mark["pixels"]), mark["rows"],
                donor_bottom=mark.get("donor_bottom"))
        except ValueError:
            continue
        digests.setdefault(hashlib.sha1(bytes(block)).hexdigest(), character)
    _COMPOSED_DIGESTS = digests
    return digests


def display_alphabet(expanded, layout, alphabet, names_path=None):
    """``alphabet`` plus the glyphs only a reader needs."""
    names = {}
    for source, key in ((names_path or EN_NAMES_DEFAULT, "character"),
                        (ACCENT_DONORS_DEFAULT, "character")):
        if not os.path.exists(source):
            continue
        with open(source, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if row.get("digest") and row.get(key, "").strip():
                    names.setdefault(row["digest"], row[key])
    for digest, character in composed_glyph_digests().items():
        names.setdefault(digest, character)
    if not names:
        return dict(alphabet)
    display = dict(alphabet)
    for slot in range(layout["glyph_count"]):
        if display.get(slot) is not None:
            continue
        digest = hashlib.sha1(glyph_bitmap(expanded, layout, slot)).hexdigest()
        if digest in names:
            display[slot] = names[digest]
    return display

def fragment_target(part):
    """One fragment of a translation, without the separator's padding."""
    core = part.strip()
    if not core:
        return ""
    prefix = part[:len(part) - len(part.lstrip(" \t\n"))]
    suffix = part[len(part.rstrip(" \t\n")):]
    return "\n" * prefix.count("\n") + core + "\n" * suffix.count("\n")

def check_page_breaks(message_id, old_bytes, new_bytes, fragment=None,
                      target=None, allowed_added=0):
    """A re-encoded run must carry the page breaks the original carried."""
    old = byte_tokens(old_bytes).count(PAGE_BREAK)
    new = byte_tokens(new_bytes).count(PAGE_BREAK)
    if new == old + allowed_added:
        return
    where = ("message %d" % message_id if fragment is None else
             "message %d fragment %d" % (message_id, fragment + 1))
    raise ValueError(
        "%s: the record has %d page break(s), automatic layout adds %d, and "
        "the translation has %d. A "
        "page break is three dashes alone on a line, exactly as the sheet's "
        "original text spells it; a run of four dashes is literal text and "
        "has to keep all four.%s"
        % (where, old, allowed_added, new,
           "" if target is None else " Fragment reads %s" % ascii(target[:70])))

def run_replacements(expanded, metadata, alphabet, glyph_base, rows,
                     source_alphabet=None, source_base=None, display=None,
                     source_glyph_count=None, extras=None,
                     display_types=None):
    """Rewrite the runs a translation changes, leaving the rest byte for byte."""
    pointers, next_offset = message_pointers(expanded, metadata)
    offsets = {}
    for _, message_id, offset in pointers:
        offsets.setdefault(message_id, offset)
    search = display or source_alphabet or alphabet
    search_base = glyph_base if source_base is None else source_base
    search_count = source_glyph_count or metadata["glyph_count"]
    writable, owned = {}, set()
    for slot in sorted(alphabet):
        character = alphabet[slot]
        if character is None or character in owned:
            continue
        writable[slot] = character
        owned.add(character)
    for slot, character in (extras or {}).items():
        if character in owned or character.isalnum():
            continue
        writable[slot] = character
        owned.add(character)
    advances = glyph_advances(expanded, metadata["text_end"], writable)
    box = glyph_advances(expanded, metadata["text_end"], search)
    replacements, rendered = {}, []
    for row in rows:
        message_id = int(row["message_id"], 0)
        max_lines = dialogue_max_lines(
            (display_types or {}).get(message_id, ()))
        if message_id not in offsets:
            raise ValueError("no record for message %d" % message_id)
        record_offset = offsets[message_id]
        record = bytes(expanded[metadata["text_start"] + record_offset:
                                metadata["text_start"] + next_offset[record_offset]])
        source_meta = dict(metadata)
        source_meta["glyph_base"] = search_base
        source_meta["glyph_count"] = search_count
        runs = []
        padding_edits = []
        for start, end, tokens in parse_record(record, source_meta):
            text, _, _ = render_tokens(tokens, source_meta, search)
            visible = clean_text(text)
            if visible:
                runs.append((start, end, visible, text, tokens))
            else:
                padded = materialize_blank_line(tokens)
                if padded is not None:
                    padding_edits.append(
                        (start, end - start, padded, record[start:end]))
        raw_targets = row["translated"].split(FRAGMENT_MARKER)
        targets = [fragment_target(part) for part in raw_targets]
        if len(targets) != len(runs):
            raise ValueError(
                "%s spans %d run(s) but its translation has %d; separate them "
                "with %s exactly as the sheet's original does" %
                (row["audio_id"], len(runs), len(targets), FRAGMENT_MARKER))
        edits, shown, drawn, codepage_runs = list(padding_edits), [], [], []
        layout_runs = []
        source_owns_layout = record_owns_authored_layout(runs, max_lines)
        auto_paginate = (len(runs) == 1
                         and max_lines == NPC_DIALOGUE_MAX_LINES)
        prepared = []
        for index, ((start, end, visible, source_text, source_tokens),
                    raw_target, target) in enumerate(
                        zip(runs, raw_targets, targets)):
            source_run = [token for token in source_tokens if token < 0x8000]
            shared_header = (
                row_uses_shared_header(row)
                or run_uses_shared_header(
                    source_text, source_run, source_meta, search))
            from_codepage = (
                shared_header
                or (bool(source_run)
                    and not run_uses_local_font(
                        source_run, source_meta, search)))
            if (from_codepage and source_run
                    and token_slot(source_run[-1], source_meta["glyph_base"],
                                   source_meta["glyph_count"]) is not None):
                source_text = source_text.rstrip(" \t")
            target = preserve_source_run_edges(source_text, target)
            target = preserve_translated_run_spacing(
                shown[-1] if shown else "", source_text, raw_target, target)
            leading_gap = (record[runs[index - 1][1]:start]
                           if index else b"")
            trailing_gap = (record[end:runs[index + 1][0]]
                            if index + 1 < len(runs) else b"")
            target = preserve_input_icon_spacing(
                target, leading_gap=leading_gap, trailing_gap=trailing_gap)
            shown.append(target)
            prepared.append(((start, end, visible, source_text, source_tokens),
                             target, from_codepage))
            codepage_runs.append(from_codepage if source_run else None)
            layout_runs.append(
                (from_codepage
                 or run_frames_shared_heading(
                     source_run, source_meta, search)
                 or run_local_face_is_ornament(
                     source_run, source_meta, search))
                if source_run else None)

        codepage_layout = shared_codepage_owns_layout(
            layout_runs, [source_run[2] for source_run in runs])

        if (len(prepared) > 1 and not any(codepage_runs)
                and not source_owns_layout):
            wrapped_runs = wrap_structured_translations(
                [target for _run, target, _from_codepage in prepared],
                advances, max_lines=max_lines)
        else:
            wrapped_runs = []
            for (_run, target, from_codepage) in prepared:
                if from_codepage or codepage_layout or source_owns_layout:
                    wrapped = apply_hard_breaks(target)
                else:
                    wrapped = wrap_translation(
                        target, _run[4], advances, max_lines=max_lines,
                        auto_paginate=auto_paginate)
                    wrapped = break_overflowing_run_junction(
                        "".join(wrapped_runs), wrapped, box)
                wrapped_runs.append(wrapped)

        for index, (((start, end, visible, source_text, source_tokens),
                     target, from_codepage), wrapped) in enumerate(
                         zip(prepared, wrapped_runs)):
            drawn.append(wrapped)
            if wrapped == visible:
                continue
            if (row_uses_shared_header(row)
                    or run_uses_shared_header(
                        source_text, source_tokens, source_meta, search)):
                replacement = encode_shared_header(
                    wrapped, source_tokens, source_meta, search)
            elif (not from_codepage
                  and run_mixes_faces([token for token in source_tokens
                                       if token < 0x8000],
                                      source_meta, search)):
                replacement = encode_visible_text(
                    wrapped, writable, glyph_base, materialize_blank_rows=True,
                    char_tokens=mixed_run_char_tokens(
                        [token for token in source_tokens if token < 0x8000],
                        source_meta, search))
            else:
                replacement = encode_visible_text(
                    wrapped if from_codepage
                    else remap_punctuation_to_period(wrapped, writable),
                    writable, glyph_base, codepage=from_codepage,
                    materialize_blank_rows=from_codepage)
            # encode_visible_text terminates its output; a run sits inside the
            # record rather than ending it, so the terminator is dropped.
            if replacement.endswith(b"\0"):
                replacement = replacement[:-1]
            authored_breaks = len(PAGE_BREAK_SPELLING.findall(
                canonical_page_breaks(target)))
            wrapped_breaks = len(PAGE_BREAK_SPELLING.findall(
                canonical_page_breaks(wrapped)))
            check_page_breaks(
                message_id, record[start:end], replacement,
                fragment=index, target=target,
                allowed_added=max(0, wrapped_breaks - authored_breaks))
            edits.append((start, end - start, replacement, record[start:end]))
        combined = "".join(drawn)
        page_lines = [0]
        for line in combined.split("\n"):
            if line.strip() == PAGE_BREAK_TEXT.strip():
                page_lines.append(0)
            elif line.strip():
                page_lines[-1] += 1
        needed_lines = max(page_lines, default=0)
        if needed_lines > max_lines and not codepage_layout \
                and not source_owns_layout:
            raise ValueError(
                "message %d needs %d lines; its dialogue box holds %d. "
                "Shorten it." % (message_id, needed_lines, max_lines))
        overrun = max((sum(box.get(c, box.get(".", 8)) for c in line)
                       for line in combined.split("\n")), default=0)
        if (len(runs) > 1 and overrun > SUBTITLE_MAX_WIDTH
                and not codepage_layout):
            raise ValueError(
                "message %d draws %d px across %d runs; the box holds %d. "
                "Put a line break in the translation where the sentence wants "
                "one: %r" % (message_id, overrun, len(runs),
                             SUBTITLE_MAX_WIDTH, combined[:60]))
        if edits:
            replacements.setdefault(record_offset, []).extend(edits)
        rendered.append((row["audio_id"], message_id,
                         (" %s " % FRAGMENT_MARKER).join(shown)))
    return replacements, rendered

def scene_run_plan(expanded, metadata, alphabet, rows):
    """Pair each record's runs with the sheet's parts, run for run."""
    pointers, next_offset = message_pointers(expanded, metadata)
    offsets = {}
    for _, message_id, offset in pointers:
        offsets.setdefault(message_id, offset)
    plan = {}
    for row in rows:
        message_id = int(row["message_id"], 0)
        if message_id not in offsets:
            continue
        record_offset = offsets[message_id]
        record = bytes(expanded[metadata["text_start"] + record_offset:
                                metadata["text_start"] + next_offset[record_offset]])
        runs = []
        for start, end, tokens in parse_record(record, metadata):
            text, _, _ = render_tokens(tokens, metadata, alphabet)
            visible = clean_text(text)
            if visible:
                runs.append((start, end, visible))
        targets = [fragment_target(part)
                   for part in row["translated"].split(FRAGMENT_MARKER)]
        if len(targets) != len(runs):
            continue
        plan[message_id] = [(start, end, visible, target)
                            for (start, end, visible), target
                            in zip(runs, targets)]
    return plan

def scene_replaced_spans(expanded, metadata, alphabet, rows):
    """Which byte spans of each record a scene sheet will rewrite."""
    plan = scene_run_plan(expanded, metadata, alphabet, rows)
    return {message_id: [(start, end - start)
                         for start, end, visible, target in runs
                         if target != visible]
            for message_id, runs in plan.items()}

def read_translated_rows(path, audio_ids=None):
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = [row for row in csv.DictReader(source)
                if row.get("message_id") and row.get("translated", "").strip()]
    if audio_ids is None:
        return rows
    selected = []
    for audio_id in audio_ids:
        matches = [row for row in rows
                   if row["audio_id"].casefold() == audio_id.casefold()]
        if len(matches) != 1:
            raise ValueError("expected one translated CSV row for audio %s, found %d" %
                             (audio_id, len(matches)))
        selected.append(matches[0])
    return selected
