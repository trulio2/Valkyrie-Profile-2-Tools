#!/usr/bin/env python3
r"""Compatibility facade and CLI for scene text export, patch, and verify."""
import argparse
import base64
import contextlib
import csv
import hashlib
import json
import io
import os
import random
import re
import struct
import time

from .paths import CACHE_ROOT, DATA_DIR, PROJECT_ROOT, TOOLS_DIR

HERE = os.fspath(TOOLS_DIR)

from . import slz
from . import slz_compress
from . import vp2_container_text as container_text
from . import triace_ps2_unpack as triace
from . import vp2_dcms as dcms
from . import vp2_iso_space as iso_space
from .scene_codec import pack_tokens
from .vp2_scene_fingerprint import (PAGE_BREAK, PAGE_BREAK_TEXT, glyph_blocks,
                                    local_alphabet, render_tokens, token_slot)
from .pk1_archive import repack_pk1_subresource
from .scene_glyphs import accented_block

DISPLAY_OPCODE = bytes((0x07, 0x41, 0x00, 0x00))
DISPLAY_TYPE_AT = 16
DISPLAY_ID_AT = 20
DISPLAY_TAIL_AT = 24
DISPLAY_TAIL = struct.pack("<f", 1.0)
AREA_BANNER_PREFIX = bytes.fromhex(
    "8e80000000408a803333933f3333933f")

def displayed_message_types(raw, known=None):
    """``message id -> display types`` from real ECS instructions."""
    body = None
    for tag, offset, length in dcms.parse_pk1(raw):
        if tag == "ECS":
            body = raw[offset:offset + length]
    if body is None:
        return {}
    plain = slz.decompress(body) if body[:3] == b"SLZ" else body
    found, start = {}, 0
    while True:
        at = plain.find(DISPLAY_OPCODE, start)
        if at < 0:
            break
        start = at + 1
        if at + DISPLAY_TAIL_AT + 4 > len(plain):
            continue
        if plain[at + DISPLAY_TAIL_AT:at + DISPLAY_TAIL_AT + 4] \
                != DISPLAY_TAIL:
            continue
        message_id = struct.unpack_from("<I", plain, at + DISPLAY_ID_AT)[0]
        if known is None or message_id in known:
            display_type = struct.unpack_from(
                "<I", plain, at + DISPLAY_TYPE_AT)[0]
            found.setdefault(message_id, set()).add(display_type)
    return found

def displayed_message_ids(raw, known=None):
    """Message ids referenced by real display instructions in the ECS."""
    return set(displayed_message_types(raw, known))

def area_banner_message_ids(expanded, metadata, known=None):
    """Message ids fetched directly by the area-name UI."""
    found = set()
    pointers, next_offset = message_pointers(expanded, metadata)
    for _, message_id, offset in pointers:
        if known is not None and message_id not in known:
            continue
        start = metadata["text_start"] + offset
        record = bytes(expanded[start:start + next_offset[offset] - offset])
        if record.startswith(AREA_BANNER_PREFIX):
            found.add(message_id)
    return found

def runtime_displayed_message_ids(raw, expanded, metadata, known=None):
    """Message ids consumed by ECS instructions or the implicit area UI."""
    return displayed_message_ids(raw, known) | area_banner_message_ids(
        expanded, metadata, known)

class FileIso:
    """Adapter that lets file-mode helpers share the iso duck type."""

    def __init__(self, handle, table, total):
        self.handle = handle
        self.table = table
        self.total = total

    def read_entry(self, resource):
        return dcms.read_entry(self.handle, self.table, self.total, resource)

OPENING_RESOURCE = 1197
CHAPTER_TITLE_MESSAGE = 2739
CHAPTER_TITLE_TEXT = "Defiers of the Gods"
SPLIT_SUBTITLE_AUDIO = "804a"
SPLIT_SUBTITLE_SOURCE = ("Don't worry about it",
                         "just do as I say for a while.")
FRAGMENT_MARKER = "<PART>"

def area_banner_visible_text(text):
    """Drop the source sheet's opaque leading area-banner placeholder."""
    parts = text.split(FRAGMENT_MARKER)
    if len(parts) > 1 and parts[0].strip() == "_":
        return FRAGMENT_MARKER.join(parts[1:]).strip()
    return text

class EventTextOverflow(ValueError):
    """The rebuilt indexed records need more room before the local font."""

    def __init__(self, overflow):
        self.overflow = overflow
        super().__init__(
            "translated event text exceeds its region by %d bytes" % overflow)

FIELDS = [
    "audio_id", "resource_index", "message_index", "message_id", "speaker",
    "match_score", "source_text", "manifest_text", "translated",
    "record_byte_offset", "record_byte_length", "text_relative_offset",
    "text_byte_length", "text_part_index", "visible_part_count",
    "visible_parts_json", "source_tokens", "source_raw_hex", "source_rendered",
    "translator_notes",
]

TAG = re.compile(r"<(?:[0-9A-Fa-f]{4}|\?|END)>")

RAW_TOKEN = re.compile(r"<([0-9A-Fa-f]{4})>")

PAGE_BREAK_SPELLING = re.compile(r"(?:\n|\A)[ \t]*---(?!-)[ \t]*(?:\n|\Z)")

CONTROL_SPELLING = re.compile(
    r"<[0-9A-Fa-f]{4}>|" + PAGE_BREAK_SPELLING.pattern)

def canonical_page_breaks(text):
    """Text with every page break spelled the way ``render_tokens`` spells it."""
    return PAGE_BREAK_SPELLING.sub(PAGE_BREAK_TEXT, text)

def strip_raw_tokens(text):
    """Text with the raw-token tags removed, for anything counting glyphs."""
    return RAW_TOKEN.sub("", text)

def visible_characters(text):
    """The characters a translation actually asks the font to draw."""
    return CONTROL_SPELLING.sub("", text)

def _codepage_tokens():
    """``character -> token`` for the shared code page, minus the slots the"""
    tokens = {character: token
              for token, character in dcms.ENGLISH_CONTROLS.items()
              if token < 0x20 and isinstance(character, str)}
    tokens.update({chr(token + 0x1F): token for token in range(0x20, 0x60)})
    return {character: token for character, token in tokens.items()
            if token not in dcms.CODEPAGE_UNUSED}

CODEPAGE_TOKENS = _codepage_tokens()

def render_raw_tokens(text):
    """Text with each raw-token tag replaced by what the screen draws."""
    def shown(match):
        token = int(match.group(1), 16)
        rendered, _, _ = render_tokens(
            [token], {"glyph_base": 0, "glyph_count": 0}, {})
        return rendered
    return RAW_TOKEN.sub(shown, text)

ACCENTS = {
    "à": ("a", "grave"),
    "á": ("a", "acute"),
    "â": ("a", "circumflex"),
    "ã": ("a", "tilde"),
    "é": ("e", "acute"),
    "ê": ("e", "circumflex"),
    "í": ("i", "acute_dotless"),
    "ó": ("o", "acute"),
    "ô": ("o", "circumflex"),
    "ú": ("u", "acute"),
    "õ": ("o", "tilde"),
    "ç": ("c", "cedilla"),
    "Á": ("A", "acute"),
    "À": ("A", "grave"),
    "Ã": ("A", "tilde"),
    "É": ("E", "acute_upper"),
    "Ê": ("E", "circumflex"),
    "Í": ("I", "acute_dotless"),
    "Ó": ("O", "acute_upper"),
    "Õ": ("O", "tilde"),
    "Ú": ("U", "acute"),
    "Ç": ("C", "cedilla"),
}
BASIC_DONORS = {
    "E": (33, 49),
    "F": (35, 40),
    "G": (35, 42),
    "L": (35, 6),
    "M": (33, 44),
    "Q": (75, 32),
    "R": (33, 45),
    "U": (143, 46),
    "V": (33, 48),
    "j": (33, 30),
    "q": (39, 37),
    "z": (33, 43),
}
OPENING_EXTRA_SLOTS = {
    29: "!",
    35: ",",
}
RESOURCE_EXTRA_SLOTS = {
    OPENING_RESOURCE: OPENING_EXTRA_SLOTS,
    1195: {10: "!"},
}
OPENING_REUSE_CANDIDATES = [46] + list(range(53, 66))

_DEFAULT_TITLE_FACE_PATH = os.path.join(
    os.fspath(DATA_DIR), "chapter-title-face.csv")

def _load_display_face_digests(path):
    """SHA-1s of the ornate chapter-title face, as a rejection set."""
    digests = set()
    if not path or not os.path.exists(path):
        return digests
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("digest"):
                digests.add(row["digest"])
    return digests

_DEFAULT_GLYPH_NAMES_PATH = os.path.join(
    os.fspath(DATA_DIR), "glyph-pool-names.csv")

def load_name_corrections(path=_DEFAULT_GLYPH_NAMES_PATH):
    """``(overrides, rejects)`` -- the names established by reading the game."""
    overrides = {}
    rejects = set()
    if not path or not os.path.exists(path):
        return overrides, rejects
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            digest = (row.get("digest") or "").strip()
            if not digest:
                continue
            character = row.get("character") or ""
            if character:
                overrides[digest] = character
            else:
                rejects.add(digest)
    return overrides, rejects

def _atlas_entry(row):
    """One atlas CSV row in the shape the installer reads."""
    return {
        "digest": row["digest"],
        "metric": base64.b64decode(row["metric"]),
        "pixels": base64.b64decode(row["pixels"]),
        "source_release": row["source_release"],
        "source_slot": int(row["source_slot"]),
    }

def _load_atlas_glyphs(path, reject_digests=(), name_overrides=None):
    """Load ``vp2-font-atlas.csv`` into ``{(scene, character): row}``."""
    table = {}
    dropped = []
    renamed = []
    if not path or not os.path.exists(path):
        return table, dropped, renamed
    reject_digests = set(reject_digests)
    name_overrides = dict(name_overrides or {})
    corrected = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (int(row["scene"]), row["character"])
            if row["digest"] in reject_digests:
                dropped.append(key)
                continue
            settled = name_overrides.get(row["digest"], row["character"])
            if settled != row["character"]:
                dropped.append(key)
                corrected.append(((key[0], settled), key[1], row))
                continue
            table[key] = _atlas_entry(row)
    for key, was, row in corrected:
        if key in table:
            continue
        table[key] = _atlas_entry(row)
        renamed.append((key[0], was, key[1]))
    dropped = [key for key in dropped if key not in table]
    return table, dropped, renamed

def _load_authored_glyphs(path):
    """Load ``vp2-authored-glyphs.csv`` into ``{character: row}``."""
    table = {}
    if not path or not os.path.exists(path):
        return table
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            character = row["character"]
            table[character] = {
                "digest": row["digest"],
                "metric": base64.b64decode(row["metric"]),
                "pixels": base64.b64decode(row["pixels"]),
                "source_release": "authored",
                "source_slot": int(row["slot"]),
            }
    return table

_DEFAULT_ATLAS_PATH = os.path.join(
    os.fspath(CACHE_ROOT), "font-atlas.csv")

_DEFAULT_AUTHORED_PATH = os.path.join(
    os.fspath(DATA_DIR), "authored-glyphs.csv")

def _resolve_atlas_path():
    override = os.environ.get("VP2_FONT_ATLAS")
    if override == "0":
        return None
    if override:
        return override
    return _DEFAULT_ATLAS_PATH

def _resolve_authored_path():
    override = os.environ.get("VP2_AUTHORED")
    if override == "0":
        return None
    if override:
        return override
    return _DEFAULT_AUTHORED_PATH

def _load_accent_marks(path, source="authored"):
    """Load accent marks into ``{character: row}``."""
    table = {}
    if not path or not os.path.exists(path):
        return table
    with open(path, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            table[row["character"]] = {
                "base": row["base"],
                "position": row["position"],
                "rows": [int(value) for value in row["rows"].split()],
                "donor_bottom": (int(row["donor_bottom"])
                                 if row.get("donor_bottom") else None),
                "pixels": base64.b64decode(row["pixels"]),
                "source": source,
            }
    return table

def _load_glyph_pool(path):
    """Load ``vp2-glyph-pool.csv`` into ``{character: row}``."""
    table = {}
    if not path or not os.path.exists(path):
        return table
    with open(path, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if not row.get("character") or not row.get("pixels"):
                continue
            table[row["character"]] = {
                "digest": row["digest"],
                "metric": base64.b64decode(row["metric"]),
                "pixels": base64.b64decode(row["pixels"]),
                "source_resource": int(row["source_resource"]),
                "source_slot": int(row["source_slot"]),
            }
    return table

_DEFAULT_POOL_PATH = os.path.join(
    os.fspath(PROJECT_ROOT),
    "workspace", "internal", "cache", "glyph-pool.csv",
)

DISPLAY_FACE_DIGESTS = _load_display_face_digests(_DEFAULT_TITLE_FACE_PATH)
GLYPH_NAMES, GLYPH_NAME_REJECTS = load_name_corrections()
ATLAS, ATLAS_REJECTED, ATLAS_RENAMED = _load_atlas_glyphs(
    _resolve_atlas_path(), DISPLAY_FACE_DIGESTS | GLYPH_NAME_REJECTS,
    GLYPH_NAMES)
_DEFAULT_AUTHORED_MARKS_PATH = os.path.join(
    os.fspath(DATA_DIR), "authored-marks.csv")


def _resolve_authored_marks_path():
    override = os.environ.get("VP2_AUTHORED_MARKS")
    if override == "0":
        return None
    return override or _DEFAULT_AUTHORED_MARKS_PATH


ACCENT_MARKS = _load_accent_marks(_resolve_authored_marks_path())
AUTHORED_MARKS = ACCENT_MARKS
AUTHORED = _load_authored_glyphs(_resolve_authored_path())
POOL = _load_glyph_pool(os.environ.get("VP2_GLYPH_POOL")
                        or _DEFAULT_POOL_PATH)

CODEPAGE_ONLY = frozenset(character for character in CODEPAGE_TOKENS
                          if character not in POOL and not character.isspace())

RECORD_PARAMETERS = {
    0x809E: 4,
    0x8083: 4,
    0x808A: 8,
    0x8086: 4,
    0x808C: 4,
    0x8088: 1,
    0x8084: 1,
    0x8094: 1,
    0x8099: 1,
    0x80A1: 1,
    0x8098: 1,
    0x8097: 4,
    0x808E: 4,
}
TEXT_BREAKS = (0x8080, 0x8081)

from .scene_records import (
    byte_tokens, clean_text, cmd_export, cmd_export_records, load_resource,
    message_pointers, normalized, pair_manifest, parse_record,
    split_nonempty, subtitle_fingerprints, subtitle_records, text_region_end,
)

GLYPH_ROWS = 28
GLYPH_COLUMNS = 32
ACCENT_DONORS_DEFAULT = os.path.join(
    os.fspath(DATA_DIR), "vp2-accent-donors.csv")
SHARED_ACCENT_DONORS_DEFAULT = os.path.join(
    os.fspath(DATA_DIR), "vp2-shared-font-accent-donors.csv")

from . import scene_fonts as _scene_fonts
from .scene_fonts import (
    ambiguous_glyphs, append_required_glyphs, apply_full_font,
    bitmap_fingerprints, clear_released_glyphs, describe_ambiguous,
    discover_generated_glyphs, donor_glyph, find_dcms, font_layout,
    glyph_bitmap, glyph_metric, install_required_glyphs_in_slots,
    iso_alphabet, name_comma_among_periods, plan_full_font,
    punctuation_block, read_accent_donors, remap_punctuation_to_period,
    remap_untranslated, require_local_font, required_glyph_lists,
    safe_reuse_candidates, slot_token, untouched_workspace_characters,
)


def _sync_scene_font_data():
    """Keep reloads and compatibility monkey-patches visible after extraction."""
    for name in (
            "ACCENTS", "ACCENT_DONORS_DEFAULT", "ACCENT_MARKS", "ATLAS",
            "AUTHORED", "BASIC_DONORS", "POOL", "RESOURCE_EXTRA_SLOTS",
            "SHARED_ACCENT_DONORS_DEFAULT"):
        setattr(_scene_fonts, name, globals()[name])


def _font_call(name, *args, **kwargs):
    _sync_scene_font_data()
    return getattr(_scene_fonts, name)(*args, **kwargs)


def required_glyph_lists(*args, **kwargs):
    return _font_call("required_glyph_lists", *args, **kwargs)


def append_required_glyphs(*args, **kwargs):
    return _font_call("append_required_glyphs", *args, **kwargs)


def install_required_glyphs_in_slots(*args, **kwargs):
    return _font_call("install_required_glyphs_in_slots", *args, **kwargs)


def plan_full_font(*args, **kwargs):
    return _font_call("plan_full_font", *args, **kwargs)


def apply_full_font(*args, **kwargs):
    return _font_call("apply_full_font", *args, **kwargs)


def discover_generated_glyphs(*args, **kwargs):
    return _font_call("discover_generated_glyphs", *args, **kwargs)


_sync_scene_font_data()

from .scene_layout import (
    NPC_DIALOGUE_DISPLAY_TYPE, NPC_DIALOGUE_MAX_LINES, NPC_PAGE_SEPARATOR,
    STRUCTURED_RUN_BOUNDARY, SUBTITLE_MAX_LINES, SUBTITLE_MAX_WIDTH,
    TEXT_RUN_END, _record_gap_tokens, break_overflowing_run_junction,
    dialogue_max_lines, glyph_advances, materialize_blank_line,
    preserve_input_icon_spacing, preserve_source_run_edges,
    soften_dialogue_breaks, verification_dialogue_layout,
    wrap_between_breaks, wrap_structured_translations, wrap_to_width,
    wrap_translation,
)

ALLOWED_SUBSTITUTIONS = {(",", "."), (":", ".")}

SCENE_COLUMNS = ("resource", "message_id")

EN_NAMES_DEFAULT = os.path.join(os.fspath(DATA_DIR), "glyph-names", "en.csv")

from .scene_text import (
    _encode_characters, blank_referenced_glyphs, check_page_breaks,
    codepage_char_tokens, describe_substitutions, display_alphabet,
    drop_display_face, encode_subtitle, encode_visible_part,
    encode_visible_text, fragment_target, is_scene_sheet,
    known_replaced_spans, local_run_characters, mixed_run_char_tokens,
    read_patch_rows, read_scene_rows, read_translated_rows,
    rebuild_event_text, replace_visible_subsequence, replaced_spans,
    row_visible_parts, run_frames_shared_heading, run_mixes_faces,
    run_replacements, run_uses_local_font,
    scene_replaced_spans, scene_required_local_glyphs, scene_run_plan,
    shared_codepage_owns_layout, split_fragment_translation,
    split_subtitle_translation, text_substitutions,
    verification_glyph_advances, visible_text_tokens,
)

def _title_slot_cost(data):
    """How small a candidate title-glyph placement makes the DCMS."""
    return len(slz_compress.compress(data, mode=2, optimal=False))


def patch_resource_bytes(raw, resource_index, args, rows, iso,
                         reference=None):
    """Compute the patched bytes + diagnostic info for one scene resource."""
    scene_sheet = is_scene_sheet(args.csv)
    glyph_iso = reference if reference is not None else iso
    (raw_unused, dcms_offset, dcms_length, expanded, layout,
     alphabet) = iso_alphabet(iso, resource_index, reference)
    require_local_font(resource_index, layout, alphabet)
    needed = set("".join(
        visible_characters(row["translated"].replace(FRAGMENT_MARKER, ""))
        for row in rows)) - CODEPAGE_ONLY
    discover_generated_glyphs(expanded, layout, alphabet, glyph_iso,
                              target_resource=resource_index)
    face_slots = drop_display_face(expanded, layout, alphabet, glyph_iso)
    if face_slots:
        print("display face: %d slot(s) held back from subtitle text"
              % face_slots)
    ambiguous = ambiguous_glyphs(expanded, layout, alphabet)
    if ambiguous:
        raise ValueError(
            "resource #%d has misidentified glyphs, so the encoder would "
            "silently pick the wrong slot: %s. Name the odd slot in "
            "RESOURCE_EXTRA_SLOTS." % (resource_index, describe_ambiguous(ambiguous)))
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    cleared = []
    title_installed, title_released, title_tokens = [], [], None
    title_slots, title_pending, title_free = {}, [], []
    if args.chapter_title:
        from . import vp2_title_face as title_face
        title_face_names, title_donors = title_face.donor_index(
            glyph_iso, skip_patched=True)
        (title_slots, title_installed, title_released,
         title_pending) = title_face.plan_title(
            expanded, layout, args.chapter_title, glyph_iso,
            args.chapter_title_message,
            face=title_face_names, donors=title_donors)
        title_protected = set(title_slots.values())
        for slot in title_protected | set(title_released):
            alphabet.pop(slot, None)
    else:
        title_protected = set()
    full_font = None
    remapped = {}
    search_alphabet = None
    old_base = None
    accent_blocks = read_accent_donors(args.accent_donors)
    if accent_blocks:
        print("accents: %d borrowed glyph(s), the rest drawn"
              % len(accent_blocks))
    display = display_alphabet(expanded, layout, alphabet, args.en_names)
    display_count = layout["glyph_count"]
    en_extras = {slot: character for slot, character in display.items()
                 if alphabet.get(slot) is None}
    if scene_sheet and not args.full_font:
        needed = scene_required_local_glyphs(
            expanded, metadata, display, rows)
    if args.full_font:
        def face_text(text):
            """Only what the re-cut font has to hold: control spellings and"""
            return "".join(character
                           for character in visible_characters(text)
                           if character not in CODEPAGE_ONLY)

        final_text = {int(row["message_id"]):
                      face_text(row["translated"].replace(FRAGMENT_MARKER, " "))
                      for row in rows}
        if scene_sheet:
            for message_id, runs in scene_run_plan(
                    expanded, metadata, display, rows).items():
                final_text[message_id] = " ".join(
                    face_text(target)
                    for _, _, visible, target in runs
                    if target != visible)
        keep_glyphs = {character: slot
                       for slot, character in display.items()
                       if alphabet.get(slot) is None
                       and not character.isalnum()}
        replaced = (scene_replaced_spans(
                        expanded, metadata, display, rows)
                    if scene_sheet
                    else known_replaced_spans(rows))
        known_messages = {message_id for _, message_id, _
                          in message_pointers(expanded, metadata)[0]}
        displayed = runtime_displayed_message_ids(
            raw, expanded, metadata, known_messages)
        plan = plan_full_font(expanded, layout, alphabet, metadata,
                              final_text, glyph_iso,
                              protected=title_protected, keep=keep_glyphs,
                              use_vacated=args.use_vacated,
                              replaced=replaced,
                              assignment_order=getattr(
                                  args, "_font_assignment_order", None),
                              displayed=displayed)
        characters, assignment, remap, dropped, opaque = plan
        search_alphabet = dict(alphabet)
        old_base, old_count = metadata["glyph_base"], metadata["glyph_count"]
        stable_characters = untouched_workspace_characters(
            expanded, metadata, search_alphabet, final_text, replaced)
        remapped = remap_untranslated(
            expanded, metadata, replaced, remap, old_base, old_count,
            layout["glyph_base"], displayed=displayed)
        installed, slot_total, shift = apply_full_font(
            expanded, layout, alphabet, assignment, opaque,
            glyph_iso, accent_blocks, keep_glyphs)
        layout = font_layout(expanded)
        metadata["glyph_count"] = layout["glyph_count"]
        title_free = [slot for slot in range(layout["glyph_count"])
                      if slot not in set(assignment.values())
                      and slot not in opaque
                      and slot not in title_protected]
        full_font = {
            "characters": characters, "dropped": dropped,
            "installed": installed, "slots": slot_total,
            "opaque": sorted(opaque), "shift": shift,
            "remapped": sum(len(v) for v in remapped.values()),
            "assignment": dict(assignment),
            "one_byte_cutoff": 0x80 - old_base,
            "stable_characters": sorted(stable_characters),
        }
    elif args.opening_only_font_reuse:
        if resource_index != OPENING_RESOURCE:
            raise ValueError("opening-only font reuse requires resource #%d" %
                             OPENING_RESOURCE)
        pool = list(OPENING_REUSE_CANDIDATES)
        pool += [slot for slot in
                 safe_reuse_candidates(
                     expanded, metadata,
                     display if scene_sheet else alphabet, rows)
                 if slot not in pool]
        candidates = [slot for slot in pool
                      if slot not in title_protected]
        installed = install_required_glyphs_in_slots(
            expanded, layout, alphabet, needed, glyph_iso,
            candidates, target_resource=resource_index,
            allow_partial=bool(args.chapter_title))
        if args.chapter_title:
            installed = installed + append_required_glyphs(
                expanded, layout, alphabet, needed, glyph_iso,
                target_resource=resource_index)
    elif args.safe_font_reuse:
        candidates = safe_reuse_candidates(
            expanded, metadata, display if scene_sheet else alphabet, rows)
        installed = install_required_glyphs_in_slots(
            expanded, layout, alphabet, needed, glyph_iso,
            candidates, target_resource=resource_index,
            allow_partial=True)
        installed = installed + append_required_glyphs(
            expanded, layout, alphabet, needed, glyph_iso,
            accent_blocks, target_resource=resource_index)
        cleared = clear_released_glyphs(
            expanded, layout, alphabet, needed, candidates, installed)
    else:
        installed = append_required_glyphs(
            expanded, layout, alphabet, needed, glyph_iso,
            accent_blocks, target_resource=resource_index)
    if title_pending:
        extra, placed, appended = title_face.place_title(
            expanded, layout, title_pending, title_free,
            measure=_title_slot_cost if title_free else None)
        title_slots.update(extra)
        title_installed = title_installed + placed
        title_protected |= set(extra.values())
        for slot in title_protected:
            alphabet.pop(slot, None)
        if appended:
            layout = font_layout(expanded)
            metadata["glyph_count"] = layout["glyph_count"]
        print("chapter title: %s -> slot(s) %s%s"
              % (" ".join(character for character, _s, _r, _d in placed),
                 " ".join(str(slot) for _c, slot, _r, _d in placed),
                 " (appended %d)" % len(appended) if appended else
                 " (free of %d)" % len(title_free)))
    if args.chapter_title:
        title_tokens = title_face.title_tokens(
            args.chapter_title, title_slots, layout["glyph_base"])
    _, next_offset = message_pointers(expanded, metadata)
    advances = glyph_advances(expanded, metadata["text_end"], alphabet)
    replacements = dict(remapped)
    rendered = []
    if scene_sheet:
        display_types = displayed_message_types(
            raw, {int(row["message_id"], 0) for row in rows})
        scene_edits, rendered = run_replacements(
            expanded, metadata, alphabet, layout["glyph_base"], rows,
            search_alphabet, old_base, display, display_count, en_extras,
            display_types)
        for record_offset, edits in scene_edits.items():
            replacements.setdefault(record_offset, []).extend(edits)
    for row in [] if scene_sheet else rows:
        if row["audio_id"].casefold() in skipped_audio_for(args):
            print("%s -> already patched; retained for font analysis" %
                  row["audio_id"])
            continue
        record_offset = int(row["record_byte_offset"], 0)
        text_offset = int(row["text_relative_offset"], 0)
        relative = text_offset - record_offset
        visible_parts = row_visible_parts(row)
        if len(visible_parts) > 1:
            targets = split_fragment_translation(
                row["translated"], len(visible_parts), row["audio_id"])
            edits = []
            wrapped_parts = []
            for part, target in zip(visible_parts, targets):
                old_part = bytes.fromhex(part["source_raw_hex"])
                new_part, wrapped = encode_visible_part(
                    old_part, target, alphabet, layout["glyph_base"],
                    search_alphabet, old_base, advances)
                edits.append((int(part["relative_offset"]),
                              int(part["byte_length"]), new_part, old_part))
                wrapped_parts.append(wrapped)
            replacements.setdefault(record_offset, []).extend(edits)
            rendered.append((row["audio_id"], int(row["message_id"]),
                             (" %s " % FRAGMENT_MARKER).join(wrapped_parts)))
            continue
        if row["audio_id"].casefold() == SPLIT_SUBTITLE_AUDIO.casefold():
            first_text, second_text = split_subtitle_translation(
                row["translated"])
            old_second = bytes.fromhex(row["source_raw_hex"])
            new_second = replace_visible_subsequence(
                old_second, SPLIT_SUBTITLE_SOURCE[1], second_text,
                alphabet, layout["glyph_base"], search_alphabet)
            record = bytes(expanded[
                metadata["text_start"] + record_offset:
                metadata["text_start"] + next_offset[record_offset]
            ])
            first_source = visible_text_tokens(
                SPLIT_SUBTITLE_SOURCE[0], search_alphabet or alphabet,
                layout["glyph_base"])
            first_matches = []
            for _, part_relative, part in split_nonempty(record):
                tokens = byte_tokens(part)
                matches = [
                    index for index in
                    range(len(tokens) - len(first_source) + 1)
                    if tokens[index:index + len(first_source)] == first_source
                ]
                if matches:
                    first_matches.append((part_relative, part))
            if len(first_matches) != 1:
                raise ValueError(
                    "expected one first fragment for audio %s, found %d" %
                    (row["audio_id"], len(first_matches)))
            first_relative, old_first = first_matches[0]
            new_first = replace_visible_subsequence(
                old_first, SPLIT_SUBTITLE_SOURCE[0], first_text,
                alphabet, layout["glyph_base"], search_alphabet)
            replacements.setdefault(record_offset, []).extend((
                (relative, int(row["text_byte_length"]), new_second,
                 old_second),
                (first_relative, len(old_first), new_first, old_first),
            ))
            rendered.append((row["audio_id"], int(row["message_id"]),
                             first_text + "--" + second_text))
            continue
        replacement, text = encode_subtitle(
            row, alphabet, layout["glyph_base"], search_alphabet, old_base,
            advances)
        replacements.setdefault(record_offset, []).append((
            relative, int(row["text_byte_length"]), replacement,
            bytes.fromhex(row["source_raw_hex"])))
        rendered.append((row["audio_id"], int(row["message_id"]), text))
    chapter_title_patched = None
    if title_tokens is not None or args.opening_only_font_reuse:
        title_message = (args.chapter_title_message if args.chapter_title
                         else CHAPTER_TITLE_MESSAGE)
        pointers, next_offset = message_pointers(expanded, metadata)
        matches = [(message_index, record_offset)
                   for message_index, message_id, record_offset in pointers
                   if message_id == title_message]
        if len(matches) != 1:
            raise ValueError("expected one chapter-title message, found %d" %
                             len(matches))
        _, record_offset = matches[0]
        record = bytes(expanded[
            metadata["text_start"] + record_offset:
            metadata["text_start"] + next_offset[record_offset]
        ])
        parts = split_nonempty(record)
        if len(parts) != 1:
            raise ValueError("unexpected chapter-title record structure")
        _, relative, old_title = parts[0]
        if title_tokens is not None:
            new_title = title_tokens
            chapter_title_patched = args.chapter_title
        else:
            new_title = encode_visible_text(
                CHAPTER_TITLE_TEXT, alphabet, layout["glyph_base"])
            chapter_title_patched = CHAPTER_TITLE_TEXT
        replacements.setdefault(record_offset, []).append((
            relative, len(old_title), new_title, old_title))
    rebuild_event_text(expanded, metadata, replacements,
                       grow=bool(args.full_font))
    dump_path = os.environ.get("VP2_DUMP_DCMS")
    if dump_path:
        with open(dump_path, "wb") as dump_file:
            dump_file.write(bytes(expanded))
        print("dumped decompressed DCMS (%d bytes) -> %s"
              % (len(expanded), dump_path))
    recompressed = slz_compress.compress(
        bytes(expanded), mode=2,
        optimal=not getattr(args, "_fast_compress", False))
    if slz.decompress(recompressed) != bytes(expanded):
        raise ValueError("mode-2 subtitle compression round-trip failed")
    relocated_offset = None
    grown_sectors = None
    reclaimed, spare = None, None
    if len(recompressed) <= dcms_length:
        patched = bytearray(raw)
        patched[dcms_offset:dcms_offset + dcms_length] = \
            recompressed.ljust(dcms_length, b"\0")
        patched = bytes(patched)
    elif args.relocate:
        if getattr(iso, "is_in_memory", False):
            raise ValueError("--relocate is not supported by the in-memory "
                             "build path; run via the subprocess interface")
        patched, relocated_offset = iso_space.append_subresource(
            raw, "DCMS", recompressed)
    else:
        try:
            patched = repack_pk1_subresource(
                raw, "DCMS", recompressed, alignment=args.pk1_align)
        except ValueError:
            reclaimed = []
            try:
                patched, spare = iso_space.repack_content_region(
                    raw, "DCMS", recompressed, report=reclaimed,
                    announce=lambda message: print(message, flush=True),
                    recompressible=getattr(
                        args, "_recompressible_tags", None))
            except ValueError as shortfall:
                if getattr(iso, "is_in_memory", False):
                    raise ValueError(
                        "%s. Growing the archive needs a larger outer "
                        "allocation, which the in-memory build cannot give "
                        "it; patch this resource with "
                        "`vp2_cutscene_subtitles.py patch`." % shortfall)
                print("  %s" % shortfall)
                reclaimed = []
                patched, grown_sectors = iso_space.repack_grown(
                    raw, "DCMS", recompressed, report=reclaimed,
                    announce=lambda message: print(message, flush=True))
                print("growing the archive by %d sector(s); the streamed tail "
                      "moves with it" % grown_sectors)
    return {
        "patched": patched,
        "rendered": rendered,
        "installed": installed,
        "cleared": cleared,
        "full_font": full_font,
        "title_installed": title_installed,
        "title_released": title_released,
        "chapter_title_patched": chapter_title_patched,
        "dcms_offset": dcms_offset,
        "dcms_length": dcms_length,
        "recompressed": recompressed,
        "reclaimed": reclaimed,
        "spare": spare,
        "relocated_offset": relocated_offset,
        "grown_sectors": grown_sectors,
    }

def skipped_audio_for(args):
    """Set of audio IDs the user asked to skip, case-folded for matching."""
    return {audio_id.casefold() for audio_id in args.skip_audio_id}

FONT_LAYOUT_CACHE_VERSION = 1
FONT_LAYOUT_CACHE = os.path.join(os.fspath(CACHE_ROOT), "font-layout")
FONT_LAYOUT_SEEDS = os.path.join(os.fspath(DATA_DIR), "font-layouts")

def font_layout_cache_key(baseline):
    packed = baseline.get("recompressed")
    if not packed:
        return None
    return hashlib.sha256(
        ("font-layout-v%d\0" % FONT_LAYOUT_CACHE_VERSION).encode("ascii")
        + packed).hexdigest()

def font_layout_cache_paths(baseline):
    key = font_layout_cache_key(baseline)
    root = os.environ.get("VP2_FONT_LAYOUT_CACHE", FONT_LAYOUT_CACHE)
    if key is None or root == "0":
        return []
    return [os.path.join(FONT_LAYOUT_SEEDS, key + ".json"),
            os.path.join(root, key[:2], key + ".json")]

def read_font_layout_cache(baseline, characters, include_mutable=True):
    paths = font_layout_cache_paths(baseline)
    if not include_mutable:
        paths = paths[:1]
    for path in paths:
        try:
            with open(path, encoding="utf-8") as source:
                order = json.load(source).get("order")
        except (FileNotFoundError, OSError, ValueError, TypeError):
            continue
        if isinstance(order, list) and set(order) == set(characters) \
                and len(order) == len(characters):
            return order
    return None

def write_font_layout_cache(baseline, order):
    paths = font_layout_cache_paths(baseline)
    if not paths:
        return
    path = paths[-1]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.tmp.%d" % (path, os.getpid())
    with open(temporary, "w", encoding="utf-8") as output:
        json.dump({"version": FONT_LAYOUT_CACHE_VERSION, "order": order},
                  output, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, path)

def _report_layout_search(resource_index, timing, started):
    """Say what a cold font-layout search actually cost."""
    elapsed = time.perf_counter() - started
    if elapsed < 5.0:
        return
    print("  font-layout search #%d: %d fast + %d full candidate(s) in %.1fs"
          % (resource_index, timing["fast_n"], timing["full_n"], elapsed))

def bitmap_similarity_font_orders(baseline):
    """Deterministic font orders with visually similar bitmap blocks adjacent."""
    font = baseline.get("full_font") or {}
    assignment = font.get("assignment") or {}
    packed = baseline.get("recompressed")
    if not assignment or not packed:
        return []
    try:
        expanded = bytearray(slz.decompress(packed))
        layout = font_layout(expanded)
        order = sorted(assignment, key=assignment.__getitem__)
        bitmaps = {character: glyph_bitmap(
            expanded, layout, assignment[character]) for character in order}
    except (IndexError, KeyError, TypeError, ValueError):
        return []

    def distance(left, right):
        return sum(a != b for a, b in zip(bitmaps[left], bitmaps[right]))

    fixed = {" "} | set(font.get("stable_characters", ()))
    movable = [character for character in order if character not in fixed]
    results = []
    for start in movable:
        remaining = set(movable)
        remaining.remove(start)
        sequence = [start]
        while remaining:
            following = min(
                remaining,
                key=lambda character: (distance(sequence[-1], character),
                                       character))
            sequence.append(following)
            remaining.remove(following)
        iterator = iter(sequence)
        results.append([character if character in fixed else next(iterator)
                        for character in order])
    return results


def _preserves_streamed_neighbours(candidate):
    """Did *candidate* fit without rewriting an unrelated PK1 payload?"""
    return (candidate is not None
            and not candidate.get("grown_sectors")
            and candidate.get("relocated_offset") is None
            and not candidate.get("reclaimed"))


def fit_streamed_font_layout(raw, resource_index, args, rows, iso, reference,
                             baseline, attempts=1200, search=True,
                             include_mutable_cache=True):
    """Find a byte-fitting permutation without changing glyph semantics."""
    font = baseline.get("full_font") or {}
    assignment = font.get("assignment") or {}
    if not assignment:
        return baseline
    cutoff = font["one_byte_cutoff"]
    base_order = sorted(assignment, key=assignment.__getitem__)
    fixed = {" "} | set(font.get("stable_characters", ()))
    low = [index for index, character in enumerate(base_order)
           if assignment[character] < cutoff and character not in fixed]
    high = [index for index, character in enumerate(base_order)
            if assignment[character] >= cutoff and character not in fixed]
    rng = random.Random(resource_index)
    ranked = []
    old_order = getattr(args, "_font_assignment_order", None)
    old_fast = getattr(args, "_fast_compress", False)
    args.use_vacated = True
    try:
        cached_order = read_font_layout_cache(
            baseline, base_order, include_mutable=include_mutable_cache)
        if cached_order is not None:
            args._font_assignment_order = cached_order
            args._fast_compress = False
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    candidate = patch_resource_bytes(
                        raw, resource_index, args, rows, iso,
                        reference=reference)
            except ValueError:
                candidate = None
            promoted_fit = (not include_mutable_cache
                            and candidate is not None
                            and not candidate.get("grown_sectors")
                            and candidate.get("relocated_offset") is None)
            if promoted_fit or _preserves_streamed_neighbours(candidate):
                print("streamed archive kept in place by cached font ordering")
                return candidate

        if not search:
            return baseline

        similarity_ranked = []
        _timing = {"fast_n": 0, "full_n": 0}
        _started = time.perf_counter()
        for index, order in enumerate(bitmap_similarity_font_orders(baseline)):
            args._font_assignment_order = order
            args._fast_compress = True
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    candidate = patch_resource_bytes(
                        raw, resource_index, args, rows, iso,
                        reference=reference)
            except ValueError:
                continue
            similarity_ranked.append((len(candidate["recompressed"]),
                                      -index - 1, order))
            _timing["fast_n"] += 1
        args._fast_compress = False
        for _size, _index, order in sorted(similarity_ranked)[:10]:
            args._font_assignment_order = order
            _timing["full_n"] += 1
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    candidate = patch_resource_bytes(
                        raw, resource_index, args, rows, iso,
                        reference=reference)
            except ValueError:
                continue
            if _preserves_streamed_neighbours(candidate):
                write_font_layout_cache(baseline, order)
                _report_layout_search(resource_index, _timing, _started)
                print("streamed archive kept in place by bitmap-similarity "
                      "font ordering")
                return candidate
        ranked.extend(similarity_ranked)

        for index in range(attempts + 1):
            order = list(base_order)
            if index:
                for positions in (low, high):
                    values = [order[position] for position in positions]
                    rng.shuffle(values)
                    for position, character in zip(positions, values):
                        order[position] = character
            args._font_assignment_order = order
            args._fast_compress = True
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    candidate = patch_resource_bytes(
                        raw, resource_index, args, rows, iso,
                        reference=reference)
            except ValueError:
                continue
            ranked.append((len(candidate["recompressed"]), index, order))
            _timing["fast_n"] += 1

        args._fast_compress = False
        for _size, _index, order in sorted(ranked)[:40]:
            args._font_assignment_order = order
            _timing["full_n"] += 1
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    candidate = patch_resource_bytes(
                        raw, resource_index, args, rows, iso,
                        reference=reference)
            except ValueError:
                continue
            if _preserves_streamed_neighbours(candidate):
                write_font_layout_cache(baseline, order)
                _report_layout_search(resource_index, _timing, _started)
                print("streamed archive kept in place by compression-aware "
                      "font ordering")
                return candidate
        _report_layout_search(resource_index, _timing, _started)
        return baseline
    finally:
        args.use_vacated = False
        args._font_assignment_order = old_order
        args._fast_compress = old_fast

def patch_resource_in_memory(iso, resource_index, args, rows,
                             reference=None):
    """Patch one scene resource in-place inside an :class:`IsoBuffer`."""
    raw = iso.read_entry(resource_index)
    streamed = iso_space.has_streamed_tail(raw)
    missing = object()
    old_recompressible = getattr(args, "_recompressible_tags", missing)
    old_full_font = args.full_font
    if streamed:
        args._recompressible_tags = iso_space.FAST_RECOMPRESSIBLE
    else:
        args._recompressible_tags = ()
    try:
        try:
            info = patch_resource_bytes(raw, resource_index, args, rows, iso,
                                        reference=reference)
        except EventTextOverflow:
            if old_full_font:
                raise
            args.full_font = True
            info = patch_resource_bytes(raw, resource_index, args, rows, iso,
                                        reference=reference)
            print("event text region grown by automatic full-font re-cut")
        if (not args.full_font and not args.safe_font_reuse
                and info.get("grown_sectors") and streamed):
            args.safe_font_reuse = True
            try:
                compact = patch_resource_bytes(
                    raw, resource_index, args, rows, iso,
                    reference=reference)
            finally:
                args.safe_font_reuse = False
            if not compact.get("grown_sectors"):
                print("streamed archive kept in place by reusing released "
                      "glyph slots")
            info = compact
        if (args.full_font and not args.use_vacated
                and info.get("grown_sectors") and streamed):
            args.use_vacated = True
            try:
                compact = patch_resource_bytes(
                    raw, resource_index, args, rows, iso, reference=reference)
            finally:
                args.use_vacated = False
            if not compact.get("grown_sectors"):
                print("streamed archive kept in place by reusing released "
                      "glyph slots")
                info = compact
            else:
                info = fit_streamed_font_layout(
                    raw, resource_index, args, rows, iso, reference, compact,
                    search=False, include_mutable_cache=False)
                if info.get("grown_sectors"):
                    args._recompressible_tags = iso_space.RECOMPRESSIBLE
                    args.use_vacated = True
                    try:
                        pam = patch_resource_bytes(
                            raw, resource_index, args, rows, iso,
                            reference=reference)
                    finally:
                        args.use_vacated = False
                    if not pam.get("grown_sectors"):
                        print("streamed archive kept in place by deferred PAM "
                              "recompression")
                        info = pam
                    else:
                        args._recompressible_tags = \
                            iso_space.FAST_RECOMPRESSIBLE
                        info = fit_streamed_font_layout(
                            raw, resource_index, args, rows, iso, reference,
                            compact)
        if (streamed and info.get("reclaimed")
                and not info.get("grown_sectors")
                and container_text.STREAMED_NEIGHBOUR_EXCEPTIONS.get(
                    resource_index) is None):
            searched = fit_streamed_font_layout(
                raw, resource_index, args, rows, iso, reference, info)
            if _preserves_streamed_neighbours(searched):
                print("streamed archive kept off its neighbours by "
                      "font ordering")
                info = searched
    finally:
        args.full_font = old_full_font
        if old_recompressible is missing:
            try:
                del args._recompressible_tags
            except AttributeError:
                pass
        else:
            args._recompressible_tags = old_recompressible
    if info.get("grown_sectors") and streamed:
        raise ValueError(
            "resource #%d is a streamed archive and still needs %d extra "
            "sector(s) after safe in-place font reuse; moving its streamed "
            "tail is not supported" %
            (resource_index, info["grown_sectors"]))
    container_text.check_streamed_neighbours(
        resource_index, info.get("reclaimed"))
    if info.get("grown_sectors") or info.get("relocated_offset") is not None:
        pass
    else:
        iso.write_entry(resource_index, info["patched"])
    info["written"] = len([row for row in rows
                            if (row.get("translated") or "").strip()])
    return info

def cmd_patch(args):
    scene_sheet = is_scene_sheet(args.csv)
    if scene_sheet:
        if not args.all_translated:
            raise ValueError("a scene sheet patches every translated row; "
                             "pass --all-translated")
        rows = read_scene_rows(args.csv, args.resource)
        if not rows:
            raise ValueError("no translated rows in %s" % args.csv)
    else:
        rows = (read_translated_rows(args.csv) if args.all_translated
                else read_patch_rows(args.csv, args.audio_id))
    available_audio = {row["audio_id"].casefold() for row in rows}
    unknown_skips = sorted(skipped_audio_for(args) - available_audio)
    if unknown_skips:
        raise ValueError("skip-audio IDs are not in the selected rows: %s" %
                         ", ".join(unknown_skips))
    resources = {int(row["resource_index"]) for row in rows}
    if len(resources) != 1:
        raise ValueError("selected subtitles must belong to one resource")
    resource = resources.pop()
    if not args.dry_run and os.path.exists(args.output_iso) \
            and os.path.abspath(args.source_iso) != os.path.abspath(args.output_iso):
        raise ValueError("output ISO already exists: %s" % args.output_iso)

    from . import vp2_iso_buffer as iso_buffer_module
    iso = iso_buffer_module.IsoBuffer.from_path(args.source_iso)
    iso.is_in_memory = False
    info = patch_resource_in_memory(iso, resource, args, rows)
    patched = info["patched"]
    rendered = info["rendered"]
    installed = info["installed"]
    cleared = info["cleared"]
    full_font = info["full_font"]
    title_installed = info["title_installed"]
    title_released = info["title_released"]
    chapter_title_patched = info["chapter_title_patched"]
    dcms_length = info["dcms_length"]
    recompressed = info["recompressed"]
    reclaimed = info["reclaimed"]
    spare = info["spare"]
    relocated_offset = info["relocated_offset"]
    grown_sectors = info["grown_sectors"]

    for audio_id, message_id, text in rendered:
        print("%s -> resource #%d message %d: %s" %
              (audio_id, resource, message_id, text.replace("\n", " / ")))
    if installed:
        label = ("subtitle font reuse" if (args.opening_only_font_reuse or
                                            args.safe_font_reuse)
                 else "subtitle font")
        print(label + ": " + ", ".join(
            "%s=slot%d" % item for item in installed))
    if cleared:
        print("released glyphs: " + ", ".join(
            "%s=slot%d" % item for item in cleared))
    if full_font is not None:
        print("full font re-cut: %d slots (%d display-face slots kept)"
              % (full_font["slots"], len(full_font["opaque"])))
        print("  characters carried : %d" % len(full_font["characters"]))
        if full_font["dropped"]:
            print("  recovered slots    : %d  %s"
                  % (len(full_font["dropped"]), "".join(full_font["dropped"])))
        if full_font["installed"]:
            print("  new glyphs         : " + ", ".join(
                "%s=slot%d" % item for item in full_font["installed"]))
        if full_font["shift"]:
            print("  metric table widened by %d bytes" % full_font["shift"])
        print("  untranslated fragments re-encoded: %d" % full_font["remapped"])
    if reclaimed is not None:
        for tag, old, new in dict.fromkeys(reclaimed):
            print("recompressed %s: %d -> %d bytes (freed %d)"
                  % (tag, old, new, old - new))
        if spare is not None:
            print("content region repacked in place; %d bytes still spare%s"
                  % (spare, "" if reclaimed else
                     " (no neighbour needed re-encoding)"))
    if title_installed:
        print("chapter-title face: " + ", ".join(
            "%s=slot%d (from #%d slot %d)" % item for item in title_installed))
    if title_released:
        print("chapter-title slots released: " + ", ".join(
            "slot%d" % slot for slot in title_released))
    if args.opening_only_font_reuse:
        print("warning: opening-only font reuse preserves archive offsets but "
              "repurposes glyphs used by a later event in resource #1197")
    if chapter_title_patched is not None:
        print("chapter title: message %d -> %s" %
              (args.chapter_title_message if args.chapter_title
               else CHAPTER_TITLE_MESSAGE, chapter_title_patched))
        if not args.chapter_title:
            print("warning: no --chapter-title given, so the title was "
                  "re-encoded in the subtitle font and will not render in the "
                  "chapter-title face")
    print("DCMS: %d -> %d bytes (%+d)" %
          (dcms_length, len(recompressed), len(recompressed) - dcms_length))
    if args.dry_run:
        print("dry run OK; no ISO created")
        return
    iso.commit(args.output_iso)
    if relocated_offset is not None or grown_sectors is not None:
        info_reloc = iso_space.relocate(args.output_iso, resource, patched)
        print("relocated resource #%d: lba %d (%d sectors) -> lba %d "
              "(%d sectors)" %
              (resource, info_reloc["old_lba"], info_reloc["old_sectors"],
               info_reloc["new_lba"], info_reloc["new_sectors"]))
        if relocated_offset is not None:
            print("DCMS moved past the streamed tail to offset 0x%X; every "
                  "other subresource and the tail keep their original offsets"
                  % relocated_offset)
        else:
            print("archive grew by %d sector(s): subresources keep their "
                  "order, DCMS grew in place, and the streamed tail moved to "
                  "the sector above the new content end -- the layout the JP "
                  "and CH discs ship" % grown_sectors)
        print("image: %d sectors (%.1f MB) including %d padding sectors; "
              "volume descriptor updated"
              % (info_reloc["image_sectors"], info_reloc["image_bytes"] / 1e6,
                 info_reloc["pad_sectors"]))
        extended = info_reloc.get("extended_file")
        if extended:
            print("extended %s to cover the new sectors: %d -> %d bytes "
                  "(ends lba %d)" % (extended["name"], extended["old_size"],
                                     extended["new_size"], extended["new_end"]))
    else:
        pass
    with open(args.output_iso, "rb") as output:
        _, total, table = triace.load_table(output)
        stored = dcms.read_entry(output, table, total, resource)
        if stored[:len(patched)] != patched or any(stored[len(patched):]):
            raise ValueError("post-write subtitle verification failed")
    print("verified: %s" % args.output_iso)




from .scene_verify import collapse, cmd_verify, verify_scene_sheet


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export a cutscene subtitle CSV")
    export.add_argument("inventory_dir")
    export.add_argument("manifest")
    export.add_argument("csv")
    export.add_argument("--resource", type=int, default=OPENING_RESOURCE)
    export.add_argument("--message-id-min", type=int, default=1)
    export.add_argument("--message-id-max", type=int, default=37)
    export.add_argument(
        "--allow-weak-matches", action="store_true",
        help="export low-confidence short vocalizations with review notes")
    export.set_defaults(func=cmd_export)
    records = commands.add_parser(
        "export-records",
        help="export subtitle records without requiring an audio-bank manifest")
    records.add_argument("inventory_dir")
    records.add_argument("csv")
    records.add_argument("--resource", type=int, required=True)
    records.add_argument("--message-id-min", type=int, default=1)
    records.add_argument("--message-id-max", type=int, default=0xFFFFFFFF)
    records.add_argument("--message-id", type=int, action="append",
                         help="export one message ID (repeatable)")
    records.set_defaults(func=cmd_export_records)
    patch = commands.add_parser("patch", help="patch selected cutscene subtitles")
    patch.add_argument("source_iso")
    patch.add_argument("output_iso")
    patch.add_argument("csv")
    selection = patch.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--audio-id", action="append",
        help="audio/subtitle ID such as 8028 (repeatable)")
    selection.add_argument(
        "--all-translated", action="store_true",
        help="patch every CSV row containing a translation")
    font_mode = patch.add_mutually_exclusive_group()
    font_mode.add_argument(
        "--opening-only-font-reuse", action="store_true",
        help="proof only: reuse later-event font slots so PK1 offsets stay fixed")
    font_mode.add_argument(
        "--safe-font-reuse", action="store_true",
        help="reuse only glyph slots confined to the translated event fragments")
    patch.add_argument("--resource", type=int,
                       help="limit a scene sheet to one resource")
    patch.add_argument("--accent-donors", default=None,
                       help="character,resource,slot table for --donor-iso")
    patch.add_argument("--en-names", default=None,
                       help="digest,character file naming the glyphs the "
                            "fingerprints cannot, used to find a record's runs")
    patch.add_argument(
        "--full-font", action="store_true",
        help="re-cut the whole local font around the finished text, "
             "re-encoding every message so English-only glyphs are recovered")
    patch.add_argument(
        "--use-vacated", action="store_true",
        help="when re-cutting, write new glyphs into the slots the dropped "
             "characters vacate instead of appending them; keeps the font "
             "block small when bytes are the constraint")
    patch.add_argument(
        "--relocate", action="store_true",
        help="when the DCMS outgrows its slot, move the resource to fresh "
             "sectors at the end of the image and append the enlarged DCMS "
             "past the streamed tail, leaving every other offset untouched")
    patch.add_argument(
        "--pk1-align", type=int, default=4,
        help="pad a repacked subresource so following offsets keep their "
             "alignment residue (default 4, which is what every release uses; raise if data still misreads)")
    patch.add_argument(
        "--allow-pk1-growth", action="store_true",
        help="let the opening archive be repacked when its DCMS outgrows the "
             "original slot, consuming the outer allocation's zero padding")
    patch.add_argument(
        "--chapter-title",
        help="re-cut the scene's chapter-title glyph block for this text, "
             "borrowing display-face glyphs from other resources")
    patch.add_argument(
        "--chapter-title-message", type=int, default=CHAPTER_TITLE_MESSAGE,
        help="message ID holding the chapter title (default %d)" %
             CHAPTER_TITLE_MESSAGE)
    patch.add_argument("--dry-run", action="store_true")
    patch.add_argument(
        "--skip-audio-id", action="append", default=[],
        help="selected line already present in the base ISO; keep it only for "
             "safe font analysis (repeatable)")
    patch.set_defaults(func=cmd_patch)
    verify = commands.add_parser(
        "verify", help="decode a patched ISO and compare it with the subtitle CSV")
    verify.add_argument("iso")
    verify.add_argument("csv")
    verify.add_argument("--resource", type=int,
                        help="limit a scene sheet to one resource")
    verify.add_argument("--en-names", default=None,
                        help="digest,character file naming the glyphs the "
                             "fingerprints cannot")
    verify.add_argument(
        "--reference-iso",
        help="pristine ROM to read glyph fingerprints and donors from; needed "
             "once resource 33 has itself been translated")
    verify.add_argument(
        "--chapter-title",
        help="also verify the chapter title decodes to this text in the "
             "display face")
    verify.add_argument(
        "--chapter-title-message", type=int, default=CHAPTER_TITLE_MESSAGE,
        help="message ID holding the chapter title (default %d)" %
             CHAPTER_TITLE_MESSAGE)
    verify.add_argument("--audio-id", action="append",
                        help="verify one audio/subtitle ID (repeatable)")
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, KeyError, IndexError, csv.Error, struct.error) as exc:
        parser.exit(1, "error: %s\n" % exc)

if __name__ == "__main__":
    main()
