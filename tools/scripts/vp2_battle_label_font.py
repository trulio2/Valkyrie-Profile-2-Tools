# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct

from .overlay_edits import Edit, word


RESOURCE = 1781
MAGIC = b"mcps2lib 1.50"
GLYPH_BYTES = 448
LETTER_FIRST_SLOT = 5
LOWERCASE_FIRST_SLOT = LETTER_FIRST_SLOT + 26
RETAIL_GLYPH_COUNT = 70

GLYPHS = {
    "ã": (0x10, 70, "a"),
    "ç": (0x17, 71, "c"),
    "ê": (0x16, 72, "e"),
}

STORED_GLYPHS = {character: row[0] for character, row in GLYPHS.items()}
ACCENT_X_SHIFTS = {"ã": 1, "ê": 1}
MESSAGE_COUNT_AT = 0x54
RETAIL_MESSAGE_COUNT = 3034
SCRIPT_TEMPLATE_ID = 0x2750
SCRIPT_TEMPLATE = bytes.fromhex(
    "8a80 cdcc8c3f cdcc8c3f 9d01 8b80 00")
SCRIPT_IDS = {"ã": 0x2751, "ç": 0x2752, "ê": 0x2753}
RENDERER_CLASS_MASK_OFFSET = 0x15CCDC
RENDERER_CLASS_MASK_ORIGINAL = 0x30420003
RENDERER_CLASS_MASK_PATCHED = 0x30420013


def required_characters(labels):
    used = set("".join(labels))
    return tuple(character for character in GLYPHS if character in used)


def renderer_edits(characters):
    if not set(characters).intersection(GLYPHS):
        return []
    return [Edit(
        RENDERER_CLASS_MASK_OFFSET,
        word(RENDERER_CLASS_MASK_ORIGINAL),
        word(RENDERER_CLASS_MASK_PATCHED),
        "battle label accent character class",
    )]


def _letter_slot(character):
    if not "a" <= character <= "z":
        raise ValueError("battle label base must be lowercase ASCII")
    return LOWERCASE_FIRST_SLOT + ord(character) - ord("a")


def _glyph_script(slot, glyph_base):
    token = glyph_base + slot
    if not 0x80 <= token < 0x4000:
        raise ValueError("battle label glyph token is outside two-byte range")
    script = bytearray(SCRIPT_TEMPLATE)
    script[10:12] = bytes((0x80 | (token & 0x7F), token >> 7))
    return bytes(script)


def _install_scripts(blob, meta, characters, container_text):
    patched = bytearray(blob)
    count = struct.unpack_from("<I", patched, MESSAGE_COUNT_AT)[0]
    capacity = (meta["text_start"] - meta["table_start"]) // 8
    if not 0 < count < capacity:
        raise ValueError("battle label message count exceeds its table")
    rows = [struct.unpack_from("<II", patched, meta["table_start"] + 8 * i)
            for i in range(count)]
    if any(not message_id for message_id, _offset in rows):
        raise ValueError("battle label message table ends before its count")
    if rows != sorted(rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError("battle label message table is not uniquely sorted")

    by_id = {message_id: offset for message_id, offset in rows}
    installed = set()
    for character, message_id in SCRIPT_IDS.items():
        current = by_id.get(message_id)
        if current is None:
            continue
        wanted = _glyph_script(GLYPHS[character][1], meta["glyph_base"])
        at = meta["text_start"] + current
        if bytes(patched[at:at + len(wanted)]) != wanted:
            raise ValueError(
                "battle label message 0x%04X has an unexpected route"
                % message_id)
        installed.add(character)
    if count != RETAIL_MESSAGE_COUNT + len(installed):
        raise ValueError("battle label message count is neither retail nor patched")

    template_offset = by_id.get(SCRIPT_TEMPLATE_ID)
    if template_offset is None:
        raise ValueError("battle label one-glyph script template is missing")
    template_at = meta["text_start"] + template_offset
    if bytes(patched[template_at:template_at + len(SCRIPT_TEMPLATE)]) != SCRIPT_TEMPLATE:
        raise ValueError("battle label one-glyph script template changed")

    _layout, messages = container_text.read_messages(
        bytes(patched), RESOURCE)
    cursor = max(message["offset"] + message["byte_length"]
                 for message in messages)
    for character in characters:
        if character in installed:
            continue
        message_id = SCRIPT_IDS[character]
        script = _glyph_script(GLYPHS[character][1], meta["glyph_base"])
        offset = cursor
        at = meta["text_start"] + cursor
        if at + len(script) > meta["text_end"]:
            raise ValueError("battle label glyph script exceeds text storage")
        if any(patched[at:at + len(script)]):
            raise ValueError("battle label glyph script storage is not empty")
        patched[at:at + len(script)] = script
        rows.append((message_id, offset))
        cursor += len(script)

    rows.sort()
    if len(rows) >= capacity:
        raise ValueError("battle label message table has no terminator row")
    table_size = capacity * 8
    table = bytearray(table_size)
    for index, row in enumerate(rows):
        struct.pack_into("<II", table, index * 8, *row)
    start = meta["table_start"]
    patched[start:start + table_size] = table
    struct.pack_into("<I", patched, MESSAGE_COUNT_AT, len(rows))
    return patched


def patch_font(blob, characters):
    from . import vp2_container_text as container_text
    from . import vp2_cutscene_subtitles as subtitles
    from . import vp2_glyph_compose as glyph_compose

    requested = set(characters).intersection(GLYPHS)
    wanted = tuple(GLYPHS) if requested else ()
    original = bytes(blob)
    if not wanted:
        return original, {"characters": (), "changed_bytes": 0,
                          "no_op": True}
    if not original.startswith(MAGIC):
        raise ValueError("battle label font is not mcps2lib 1.50")
    meta = container_text.layout(original)
    if not RETAIL_GLYPH_COUNT <= meta["glyph_count"] <= len(
            GLYPHS) + RETAIL_GLYPH_COUNT:
        raise ValueError("battle label font has unexpected glyph count %d"
                         % meta["glyph_count"])
    font_end = meta["font_start"] + meta["glyph_count"] * GLYPH_BYTES
    if font_end != len(original):
        raise ValueError("battle label font does not end with its container")

    patched = _install_scripts(original, meta, wanted, container_text)
    installed_count = meta["glyph_count"]
    for character in wanted:
        _stored, target_slot, base = GLYPHS[character]
        base_slot = _letter_slot(base)
        base_start = meta["font_start"] + base_slot * GLYPH_BYTES
        base_block = original[base_start:base_start + GLYPH_BYTES]
        mark_character = glyph_compose.COMPOSITES[character][1]
        mark = subtitles.ACCENT_MARKS[mark_character]
        replacement = glyph_compose.compose_character(
            base_block, character, glyph_compose.unpack(mark["pixels"]),
            mark["rows"], donor_bottom=mark.get("donor_bottom"),
            horizontal_shift=ACCENT_X_SHIFTS.get(character, 0))

        target_start = meta["font_start"] + target_slot * GLYPH_BYTES
        base_metric_at = meta["text_end"] + base_slot * 2
        target_metric_at = meta["text_end"] + target_slot * 2
        metric = original[base_metric_at:base_metric_at + 2]
        if target_slot < installed_count:
            target = original[target_start:target_start + GLYPH_BYTES]
            current_metric = original[target_metric_at:target_metric_at + 2]
            if target != replacement or current_metric != metric:
                raise ValueError(
                    "battle label slot %d has unexpected appended data"
                    % target_slot)
        elif target_slot == installed_count:
            if target_metric_at + 2 > meta["font_start"]:
                raise ValueError("battle label metric table has no room")
            if any(original[target_metric_at:target_metric_at + 2]):
                raise ValueError(
                    "battle label slot %d metric storage is not empty"
                    % target_slot)
            patched[target_metric_at:target_metric_at + 2] = metric
            patched.extend(replacement)
            installed_count += 1
        else:
            raise ValueError("battle label appended slots are not contiguous")

    struct.pack_into("<I", patched, 0x20, len(patched))
    struct.pack_into("<I", patched, 0x34, installed_count)
    changed = sum(a != b for a, b in zip(original, patched))
    changed += abs(len(original) - len(patched))
    return bytes(patched), {
        "characters": wanted,
        "changed_bytes": changed,
        "no_op": changed == 0,
    }


def patch_resource_in_memory(iso, characters):
    from . import vp2_container_text as container_text

    raw = bytes(iso.read_entry(RESOURCE))
    blob = container_text.unpack_container_entry(raw, RESOURCE)
    patched, info = patch_font(blob, characters)
    if info["no_op"]:
        return info
    rebuilt, details = container_text.pack_container_entry(
        raw, patched, RESOURCE)
    if len(rebuilt) != len(raw) or details.get("grown_sectors"):
        raise ValueError("battle label glyphs changed resource 1781 geometry")
    iso.write_entry(RESOURCE, rebuilt)
    check = container_text.unpack_container_entry(
        bytes(iso.read_entry(RESOURCE)), RESOURCE)
    if check != patched:
        raise ValueError("battle label font did not read back byte-for-byte")
    return dict(info, details=details)
