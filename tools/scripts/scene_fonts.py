"""Scene-local font layout, donors, planning, and installation."""

import base64
import csv
import hashlib
import os
import struct

from . import slz
from . import triace_ps2_unpack as triace
from . import vp2_dcms as dcms
from . import vp2_glyph_compose as glyph_compose
from .scene_codec import (
    REFERENCE_BY_CODEPOINT, REFERENCE_FONT_RESOURCE, pack_tokens,
)
from .scene_glyphs import accented_block, glyph_value, set_glyph_value
from .vp2_scene_fingerprint import token_slot
from .vp2_cutscene_subtitles import (
    ACCENTS, ACCENT_DONORS_DEFAULT, ACCENT_MARKS, ATLAS, AUTHORED,
    BASIC_DONORS, POOL, RESOURCE_EXTRA_SLOTS,
    SHARED_ACCENT_DONORS_DEFAULT,
)


def _record_helper(name):
    from . import vp2_cutscene_subtitles as facade
    return getattr(facade, name)


def byte_tokens(*args, **kwargs):
    return _record_helper("byte_tokens")(*args, **kwargs)


def message_pointers(*args, **kwargs):
    return _record_helper("message_pointers")(*args, **kwargs)


def parse_record(*args, **kwargs):
    return _record_helper("parse_record")(*args, **kwargs)


def split_nonempty(*args, **kwargs):
    return _record_helper("split_nonempty")(*args, **kwargs)


def row_visible_parts(*args, **kwargs):
    from .vp2_cutscene_subtitles import row_visible_parts as implementation
    return implementation(*args, **kwargs)


def scene_run_plan(*args, **kwargs):
    from .vp2_cutscene_subtitles import scene_run_plan as implementation
    return implementation(*args, **kwargs)


def find_dcms(raw, resource):
    if not raw or triace.classify(raw, len(raw)) != "pk1":
        raise ValueError("resource #%d is not a PK1 archive" % resource)
    for tag, offset, length in dcms.parse_pk1(raw):
        if tag == "DCMS":
            return offset, length, raw[offset:offset + length]
    raise ValueError("resource #%d has no DCMS section" % resource)

def font_layout(expanded):
    words = struct.unpack_from("<15I", expanded, 0x20)
    text_end, font_start, glyph_count = words[3], words[4], words[5]
    glyph_height, glyph_pitch, glyph_base = words[9], words[10], words[12]
    glyph_bytes = glyph_pitch * glyph_height // 2
    font_end = font_start + glyph_count * glyph_bytes
    if not (0 < glyph_bytes and text_end <= font_start <= font_end <= len(expanded)):
        raise ValueError("invalid local subtitle font layout")
    return {
        "text_end": text_end, "font_start": font_start, "font_end": font_end,
        "glyph_count": glyph_count, "glyph_bytes": glyph_bytes,
        "glyph_base": glyph_base,
    }

def bitmap_fingerprints(expanded, known=None):
    layout = font_layout(expanded)
    blocks = []
    for slot in range(layout["glyph_count"]):
        start = layout["font_start"] + slot * layout["glyph_bytes"]
        blocks.append(bytes(expanded[start:start + layout["glyph_bytes"]]))
    if known is None:
        return layout, blocks
    alphabet = {}
    for slot, block in enumerate(blocks):
        character = known.get(hashlib.sha1(block).digest())
        if character is not None:
            alphabet[slot] = character
    return layout, blocks, alphabet

def require_local_font(resource, layout, alphabet):
    """Refuse a resource that draws with the shared font rather than its own."""
    if alphabet:
        return
    raise ValueError(
        "resource #%d has no local font (%d glyph slots), so its text is drawn "
        "from a font this tool does not own and cannot re-cut. 358 resources "
        "are in this position, the world map among them; they need the "
        "shared-font path, not the scene patcher."
        % (resource, layout.get("glyph_count", 0)))

def iso_alphabet(iso, target_resource, reference=None):
    """Identify a scene's glyphs by fingerprinting them against Soul Street."""
    source = reference or iso
    reference_raw = source.read_entry(REFERENCE_FONT_RESOURCE)
    _, _, reference_slz = find_dcms(reference_raw, REFERENCE_FONT_RESOURCE)
    reference = slz.decompress(reference_slz)
    reference_layout, reference_blocks = bitmap_fingerprints(reference)
    known = {}
    for character, row in POOL.items():
        digest = row.get("digest")
        if digest:
            known.setdefault(bytes.fromhex(digest), character)
    for token, character in REFERENCE_BY_CODEPOINT.items():
        slot = token_slot(token, reference_layout["glyph_base"],
                          reference_layout["glyph_count"])
        if slot is not None:
            known[hashlib.sha1(reference_blocks[slot]).digest()] = character

    target_raw = iso.read_entry(target_resource)
    offset, length, target_slz = find_dcms(target_raw, target_resource)
    if target_slz[:4] != b"SLZ\x02":
        raise ValueError("resource #%d DCMS is not mode-2 SLZ" % target_resource)
    expanded = bytearray(slz.decompress(target_slz))
    layout, blocks, alphabet = bitmap_fingerprints(expanded, known)
    alphabet.update(RESOURCE_EXTRA_SLOTS.get(target_resource, {}))
    return target_raw, offset, length, expanded, layout, alphabet

def slot_token(slot, glyph_base):
    code = glyph_base + slot
    if code < 0x80:
        return code
    if code <= 0xFF:
        return 0x0100 | code
    raise ValueError("local glyph slot cannot be represented: %d" % slot)

def donor_glyph(iso, resource, slot):
    raw = iso.read_entry(resource)
    _, _, packed = find_dcms(raw, resource)
    donor = slz.decompress(packed)
    layout = font_layout(donor)
    if slot >= layout["glyph_count"]:
        raise ValueError("invalid glyph donor #%d slot %d" % (resource, slot))
    start = layout["font_start"] + slot * layout["glyph_bytes"]
    metric = layout["text_end"] + slot * 2
    return (donor[start:start + layout["glyph_bytes"]],
            donor[metric:metric + 2], layout["glyph_bytes"])

def punctuation_block(period, punctuation):
    """Derive comma/colon using the game's own period pixels and palette."""
    block = bytearray(period)
    occupied = [(x, y, glyph_value(period, x, y))
                for y in range(28) for x in range(24)
                if glyph_value(period, x, y)]
    if not occupied:
        raise ValueError("cannot derive punctuation from an empty period")
    if punctuation == ":":
        # Duplicate the complete antialiased dot above the baseline dot.
        for x, y, value in occupied:
            if y >= 10:
                set_glyph_value(block, x, y - 10, value)
    elif punctuation == ",":
        # Raise the original dot by two pixels, then give it a descending tail.
        block = bytearray(len(period))
        for x, y, value in occupied:
            if y >= 2:
                set_glyph_value(block, x, y - 2, value)
        center = (min(x for x, _, _ in occupied) + max(x for x, _, _ in occupied)) // 2
        for x, y, value in ((center + 1, 24, 15), (center + 1, 25, 15),
                            (center, 26, 15), (center - 1, 27, 15),
                            (center + 2, 24, 7), (center + 2, 25, 7),
                            (center + 1, 26, 7), (center, 27, 7)):
            if glyph_value(block, x, y) == 0:
                set_glyph_value(block, x, y, value)
    else:
        raise ValueError("unsupported derived punctuation: %s" % punctuation)
    return bytes(block)

def read_accent_donors(path=None, shared_path=None, shared_whitelist=None):
    """Load ``character -> (block, metric, size)`` for borrowed accent glyphs."""
    path = path or ACCENT_DONORS_DEFAULT
    donors = {}
    if not path or not os.path.exists(path):
        return donors
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if not row.get("character", "").strip() or not row.get("pixels"):
                continue
            donors[row["character"]] = (
                base64.b64decode(row["pixels"]),
                base64.b64decode(row.get("metric") or ""),
                int(row.get("bytes") or 0))
    shared_source = shared_path or SHARED_ACCENT_DONORS_DEFAULT
    if shared_whitelist and shared_source and os.path.exists(shared_source):
        whitelist = set(shared_whitelist)
        with open(shared_source, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                character = row.get("character", "").strip()
                if (not character or not row.get("pixels")
                        or character in donors
                        or character not in whitelist):
                    continue
                donors[character] = (
                    base64.b64decode(row["pixels"]),
                    base64.b64decode(row.get("metric") or ""),
                    int(row.get("bytes") or 0))
    return donors

def required_glyph_lists(needed, char_to_slot, donated, target_resource):
    """Which donor source is responsible for appending each character."""
    def prefer_composition():
        """Whether a composable character should skip the retail sources."""
        return os.environ.get("VP2_PREFER_COMPOSITION") == "1"

    def atlas_covered(character):
        return (target_resource is not None
                and (target_resource, character) in ATLAS)

    def wanted(character):
        return character in needed and character not in char_to_slot

    def composable(character):
        recipe = glyph_compose.COMPOSITES.get(character)
        return (recipe is not None and recipe[1] in ACCENT_MARKS
                and (recipe[0] in char_to_slot or recipe[0] in POOL))

    claimed = set()
    wanted_order = sorted(needed)

    def take(candidates):
        chosen = [c for c in candidates if c not in claimed]
        claimed.update(chosen)
        return chosen

    if prefer_composition():
        composed_chars = take([c for c in wanted_order
                               if wanted(c) and composable(c)])
        atlas_chars = take([c for c in wanted_order
                            if c not in char_to_slot and atlas_covered(c)])
        pool_chars = take([c for c in wanted_order if wanted(c) and c in POOL])
    else:
        atlas_chars = take([c for c in wanted_order
                            if c not in char_to_slot and atlas_covered(c)])
        pool_chars = take([c for c in wanted_order if wanted(c) and c in POOL])
        composed_chars = take([c for c in wanted_order
                               if wanted(c) and composable(c)])
    borrowed = take([c for c in donated if wanted(c)])
    accents = take([c for c in ACCENTS if wanted(c) and c not in donated])
    basic = take([c for c in BASIC_DONORS if wanted(c)])
    punctuation = take([c for c in (",", ":") if wanted(c)])
    authored_chars = take([c for c in wanted_order
                           if c not in char_to_slot and c in AUTHORED])
    return (basic, punctuation, accents, borrowed, atlas_chars,
            composed_chars, pool_chars, authored_chars)

def append_required_glyphs(expanded, layout, alphabet, needed,
                           iso, donors=None, *, target_resource=None):
    """Append missing Latin, punctuation, and accent glyphs for a patch set."""
    char_to_slot = {character: slot for slot, character in alphabet.items()}
    donated = dict(donors or {})
    (basic, punctuation, accents, borrowed, atlas_chars,
     composed_chars, pool_chars, authored_chars) = required_glyph_lists(
        needed, char_to_slot, donated, target_resource)
    required_count = (len(basic) + len(punctuation) + len(accents)
                      + len(borrowed) + len(atlas_chars)
                      + len(composed_chars) + len(pool_chars)
                      + len(authored_chars))
    installed = []
    blocks = []
    metrics = []
    metric_start = layout["text_end"]

    def base_glyph(character):
        """The bitmap and metric of a glyph this pass may derive from."""
        slot = char_to_slot.get(character)
        if slot is not None and slot < layout["glyph_count"]:
            start = layout["font_start"] + slot * layout["glyph_bytes"]
            return (bytes(expanded[start:start + layout["glyph_bytes"]]),
                    bytes(expanded[metric_start + slot * 2:
                                   metric_start + slot * 2 + 2]))
        if slot is not None:
            pending = slot - layout["glyph_count"]
            if 0 <= pending < len(blocks):
                return bytes(blocks[pending]), bytes(metrics[pending])
        row = POOL.get(character)
        if row is not None:
            return bytes(row["pixels"]), bytes(row["metric"])
        raise ValueError("no source for base glyph %r" % character)
    font_start = layout["font_start"]
    metric_capacity = font_start - metric_start
    required_metric = (layout["glyph_count"] + required_count) * 2
    if metric_capacity < required_metric:
        shift = required_metric - metric_capacity
        shift += (-shift) % 16
        expanded[font_start:font_start] = b"\0" * shift
        font_start += shift
        struct.pack_into("<I", expanded, 0x30, font_start)
        layout["font_start"] = font_start
        layout["font_end"] += shift

    for character in atlas_chars:
        atlas_row = ATLAS[(target_resource, character)]
        block = atlas_row["pixels"]
        metric = atlas_row["metric"]
        if len(block) != layout["glyph_bytes"]:
            raise ValueError("incompatible atlas glyph for %r" % character)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(bytes(block))
        metrics.append(bytes(metric))
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    for character in pool_chars:
        pool_row = POOL[character]
        block = pool_row["pixels"]
        if len(block) != layout["glyph_bytes"]:
            raise ValueError("incompatible pool glyph for %r" % character)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(bytes(block))
        metrics.append(bytes(pool_row["metric"]))
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    for character in composed_chars:
        base, donor, _position = glyph_compose.COMPOSITES[character]
        mark = ACCENT_MARKS[donor]
        body, base_metric = base_glyph(base)
        block = glyph_compose.compose_character(
            body, character, glyph_compose.unpack(mark["pixels"]), mark["rows"],
            donor_bottom=mark.get("donor_bottom"))
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(block)
        metrics.append(base_metric)
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    if authored_chars:
        print("warning: %d glyph(s) drawn from a system font, not this "
              "scene's face -- they will not match the surrounding text: %s"
              % (len(authored_chars), " ".join(sorted(authored_chars))))

    for character in authored_chars:
        atlas_row = AUTHORED[character]
        block = atlas_row["pixels"]
        metric = atlas_row["metric"]
        if len(block) != layout["glyph_bytes"]:
            raise ValueError("incompatible authored glyph for %r" % character)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(bytes(block))
        metrics.append(bytes(metric))
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    for character in basic:
        resource, donor_slot = BASIC_DONORS[character]
        block, metric, glyph_bytes = donor_glyph(
            iso, resource, donor_slot)
        if glyph_bytes != layout["glyph_bytes"]:
            raise ValueError("incompatible glyph donor for %r" % character)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(bytes(block))
        metrics.append(bytes(metric))
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    if punctuation:
        if "." not in char_to_slot and "." not in POOL:
            raise ValueError("subtitle font lacks a period punctuation donor")
        period, period_metric = base_glyph(".")
        for character in punctuation:
            target_slot = layout["glyph_count"] + len(blocks)
            blocks.append(punctuation_block(period, character))
            metrics.append(period_metric)
            alphabet[target_slot] = character
            char_to_slot[character] = target_slot
            installed.append((character, target_slot))

    for character in borrowed:
        block, metric, glyph_bytes = donated[character]
        if glyph_bytes != layout["glyph_bytes"]:
            raise ValueError("incompatible accent donor for %r" % character)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(bytes(block))
        metrics.append(bytes(metric))
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    for character in accents:
        base, mark = ACCENTS[character]
        if base not in char_to_slot and base not in POOL:
            raise ValueError("subtitle font lacks accent base %r" % base)
        source, source_metric = base_glyph(base)
        target_slot = layout["glyph_count"] + len(blocks)
        blocks.append(accented_block(source, mark))
        metrics.append(source_metric)
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))

    append_glyph_blocks(expanded, layout, zip(blocks, metrics))
    return installed


def append_glyph_blocks(expanded, layout, arts):
    arts = [(bytes(bitmap), bytes(metric)) for bitmap, metric in arts]
    if not arts:
        return []
    metric_start = layout["text_end"]
    font_start = layout["font_start"]
    required_metric = (layout["glyph_count"] + len(arts)) * 2
    if font_start - metric_start < required_metric:
        shift = required_metric - (font_start - metric_start)
        shift += (-shift) % 16
        expanded[font_start:font_start] = b"\0" * shift
        font_start += shift
        struct.pack_into("<I", expanded, 0x30, font_start)
        layout["font_start"] = font_start
        layout["font_end"] += shift
    # The splice point has to be exactly one glyph past the last native
    # slot, or every appended bitmap lands misaligned.
    if layout["font_end"] - layout["font_start"] != (
            layout["glyph_count"] * layout["glyph_bytes"]):
        raise ValueError(
            "font block is %d bytes but %d glyphs need %d; the layout "
            "went stale before the splice"
            % (layout["font_end"] - layout["font_start"],
               layout["glyph_count"],
               layout["glyph_count"] * layout["glyph_bytes"]))
    slots = []
    for index, (bitmap, metric) in enumerate(arts):
        if len(bitmap) != layout["glyph_bytes"]:
            raise ValueError("incompatible glyph block for slot %d"
                             % (layout["glyph_count"] + index))
        slot = layout["glyph_count"] + index
        target = metric_start + slot * 2
        expanded[target:target + 2] = metric
        slots.append(slot)
    expanded[layout["font_end"]:layout["font_end"]] = b"".join(
        bitmap for bitmap, _ in arts)
    struct.pack_into("<I", expanded, 0x20, len(expanded))
    struct.pack_into("<I", expanded, 0x34, layout["glyph_count"] + len(arts))
    layout["glyph_count"] += len(arts)
    layout["font_end"] += sum(len(bitmap) for bitmap, _ in arts)
    return slots

def install_required_glyphs_in_slots(expanded, layout, alphabet, needed,
                                     iso, candidates, *,
                                     target_resource=None,
                                     allow_partial=False):
    """Install missing glyphs into reusable slots without growing the font."""
    char_to_slot = {character: slot for slot, character in alphabet.items()}
    required = []
    for group in (BASIC_DONORS, POOL, ACCENTS):
        for character in group:
            if (character in needed and character not in char_to_slot
                    and character not in required):
                required.append(character)

    protected = {}
    for slot, character in alphabet.items():
        if character in needed and character not in protected:
            protected[character] = slot
    available = []
    for slot in candidates:
        old_character = alphabet.get(slot)
        if old_character not in needed or protected.get(old_character) != slot:
            available.append(slot)
    if len(required) > len(available):
        if not allow_partial:
            raise ValueError("font reuse needs %d slots but only %d are safe" %
                             (len(required), len(available)))
        required = required[:len(available)]

    metric_start = layout["text_end"]
    installed = []
    for character, target_slot in zip(required, available):
        atlas_row = (ATLAS.get((target_resource, character))
                     if target_resource is not None else None)
        if atlas_row is None:
            # The pool before the authored fallback: real art from the
            # game's own face beats a system font every time.
            atlas_row = POOL.get(character)
        if atlas_row is None and AUTHORED:
            atlas_row = AUTHORED.get(character)
        if atlas_row is not None:
            block = atlas_row["pixels"]
            metric = atlas_row["metric"]
            if len(block) != layout["glyph_bytes"]:
                raise ValueError("incompatible atlas glyph for %r" % character)
        elif character in BASIC_DONORS:
            resource, donor_slot = BASIC_DONORS[character]
            block, metric, glyph_bytes = donor_glyph(
                iso, resource, donor_slot)
            if glyph_bytes != layout["glyph_bytes"]:
                raise ValueError("incompatible glyph donor for %r" % character)
        else:
            base, mark = ACCENTS[character]
            if base not in char_to_slot:
                raise ValueError("subtitle font lacks accent base %r" % base)
            base_slot = char_to_slot[base]
            start = layout["font_start"] + base_slot * layout["glyph_bytes"]
            source = bytes(expanded[start:start + layout["glyph_bytes"]])
            block = accented_block(source, mark)
            metric = bytes(expanded[metric_start + base_slot * 2:
                                    metric_start + base_slot * 2 + 2])

        start = layout["font_start"] + target_slot * layout["glyph_bytes"]
        expanded[start:start + layout["glyph_bytes"]] = block
        expanded[metric_start + target_slot * 2:
                 metric_start + target_slot * 2 + 2] = metric
        alphabet.pop(target_slot, None)
        alphabet[target_slot] = character
        char_to_slot[character] = target_slot
        installed.append((character, target_slot))
    return installed

def glyph_bitmap(expanded, layout, slot):
    start = layout["font_start"] + slot * layout["glyph_bytes"]
    return bytes(expanded[start:start + layout["glyph_bytes"]])

def glyph_metric(expanded, layout, slot):
    at = layout["text_end"] + slot * 2
    return bytes(expanded[at:at + 2])


def lowest_character_slots(alphabet):
    """Map each character to its lowest slot in a possibly duplicated face."""
    slots = {}
    for slot in sorted(alphabet):
        character = alphabet[slot]
        if character is not None:
            slots.setdefault(character, slot)
    return slots


def plan_full_font(expanded, layout, alphabet, metadata, translated,
                   iso, protected=(), use_vacated=False,
                   replaced=None, keep=None, assignment_order=None,
                   displayed=None):
    """Re-cut a resource's whole font around the text it will finally carry."""
    pointers, next_offset = message_pointers(expanded, metadata)
    base, count = metadata["glyph_base"], metadata["glyph_count"]

    # what each message will finally say: a translation if there is one, else
    # the characters it already shows
    frequency = {}
    for text in translated.values():
        for character in text:
            if character != "\n":
                frequency[character] = frequency.get(character, 0) + 1

    opaque = set(protected)
    replaced = replaced or {}
    for _, message_id, offset in pointers:
        if displayed is not None and message_id not in displayed:
            continue
        spans = replaced.get(message_id)
        if message_id in translated and spans is None:
            # Nothing says which bytes the patch will overwrite, so keep the
            # old assumption that re-encoding the message rewrites the record.
            continue
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        for _, part_offset, part in split_nonempty(record):
            try:
                tokens = byte_tokens(part)
            except ValueError:
                continue
            surviving = range(len(tokens))
            if spans is not None and any(
                    part_offset < start + length and
                    start < part_offset + len(part)
                    for start, length in spans):
                visible = [index for index, token in enumerate(tokens)
                           if (slot := token_slot(token, base,
                                                  max(alphabet) + 1)) is not None
                           and slot in alphabet]
                if visible:
                    surviving = [index for index in range(len(tokens))
                                 if index < visible[0] or index > visible[-1]]
            for index in surviving:
                slot = token_slot(tokens[index], base, count)
                if slot is None:
                    continue
                character = alphabet.get(slot)
                if character is None:
                    # a display-face glyph, or one the fingerprints never
                    # identified; keep it exactly as it is
                    opaque.add(slot)
                else:
                    frequency[character] = frequency.get(character, 0) + 1

    needed = sorted(frequency, key=lambda c: (-frequency[c], c))
    dropped = sorted({c for c in alphabet.values() if c != "\n"} - set(needed))

    char_to_slot = lowest_character_slots(alphabet)
    for character, slot in (keep or {}).items():
        if character in frequency:
            char_to_slot.setdefault(character, slot)
    assignment, taken = {}, set(opaque)
    for character in needed:
        slot = char_to_slot.get(character)
        if slot is not None and slot not in taken:
            assignment[character] = slot
            taken.add(slot)
    vacated = [slot for slot in range(count) if slot not in taken]
    appended = count
    for character in needed:
        if character in assignment:
            continue
        if use_vacated and vacated:
            assignment[character] = vacated.pop(0)
        else:
            assignment[character] = appended
            appended += 1
        taken.add(assignment[character])

    if assignment_order is not None:
        if set(assignment_order) != set(needed):
            raise ValueError("font assignment order does not match its glyphs")
        slots = sorted(assignment.values())
        assignment = dict(zip(assignment_order, slots))

    remap = {}
    for old_slot, character in alphabet.items():
        if character in assignment:
            remap[old_slot] = assignment[character]
    for old_slot in opaque:
        remap[old_slot] = old_slot
    return needed, assignment, remap, dropped, opaque

def apply_full_font(expanded, layout, alphabet, assignment, opaque,
                    iso, donors=None, keep=None):
    """Write the re-cut font, widening the metric table if it is too small."""
    size = layout["glyph_bytes"]
    highest = max(list(assignment.values()) + sorted(opaque) or [0])
    total_slots = highest + 1

    bitmaps = {}
    metrics = {}
    char_to_slot = lowest_character_slots(alphabet)
    for slot in opaque:
        bitmaps[slot] = glyph_bitmap(expanded, layout, slot)
        metrics[slot] = glyph_metric(expanded, layout, slot)
    placed = {}
    for character in assignment:
        if character in char_to_slot:
            source = char_to_slot[character]
            placed[character] = (glyph_bitmap(expanded, layout, source),
                                 glyph_metric(expanded, layout, source))

    for character, slot in (keep or {}).items():
        if character in assignment and character not in placed:
            placed[character] = (glyph_bitmap(expanded, layout, slot),
                                 glyph_metric(expanded, layout, slot))

    installed = []

    for character, slot in assignment.items():
        if character in placed or character not in POOL:
            continue
        row = POOL[character]
        if len(row["pixels"]) != size:
            raise ValueError("incompatible pool glyph for %r" % character)
        placed[character] = (bytes(row["pixels"]), bytes(row["metric"]))
        installed.append((character, slot))

    for character, slot in assignment.items():
        if character in placed:
            continue
        recipe = glyph_compose.COMPOSITES.get(character)
        if recipe is None:
            continue
        base_character, donor, _position = recipe
        mark = ACCENT_MARKS.get(donor)
        if mark is None:
            continue
        if base_character in placed:
            body, body_metric = placed[base_character]
        else:
            row = POOL.get(base_character)
            if row is None or len(row["pixels"]) != size:
                continue
            body, body_metric = bytes(row["pixels"]), bytes(row["metric"])
        try:
            block = glyph_compose.compose_character(
                body, character, glyph_compose.unpack(mark["pixels"]),
                mark["rows"], donor_bottom=mark.get("donor_bottom"))
        except ValueError:
            continue
        placed[character] = (bytes(block), bytes(body_metric))
        installed.append((character, slot))

    for character, slot in assignment.items():
        if character in placed or character not in BASIC_DONORS:
            continue
        resource, donor_slot = BASIC_DONORS[character]
        block, metric, donor_bytes = donor_glyph(
            iso, resource, donor_slot)
        if donor_bytes != size:
            raise ValueError("incompatible glyph donor for %r" % character)
        placed[character] = (bytes(block), bytes(metric))
        installed.append((character, slot))

    donated = donors or {}
    for character, slot in assignment.items():
        if character in placed or character not in donated:
            continue
        block, metric, donor_bytes = donated[character]
        if donor_bytes != size:
            raise ValueError("incompatible accent donor for %r" % character)
        placed[character] = (bytes(block), bytes(metric))
        installed.append((character, slot))

    for character, slot in assignment.items():
        if character in placed or character not in AUTHORED:
            continue
        block = AUTHORED[character]["pixels"]
        metric = AUTHORED[character]["metric"]
        if len(block) != size:
            raise ValueError("incompatible authored glyph for %r" % character)
        placed[character] = (bytes(block), bytes(metric))
        installed.append((character, slot))

    for character, slot in assignment.items():
        if character in placed:
            continue
        if character in (",", ":"):
            base_character, mark = ".", character
        elif character in ACCENTS:
            base_character, mark = ACCENTS[character]
        else:
            raise ValueError("no way to produce glyph %r" % character)
        if base_character in placed:
            source_block, source_metric = placed[base_character]
        else:
            row = POOL.get(base_character)
            if row is None or len(row["pixels"]) != size:
                raise ValueError("font lacks %r, needed to derive %r"
                                 % (base_character, character))
            source_block = bytes(row["pixels"])
            source_metric = bytes(row["metric"])
        block = (punctuation_block(source_block, mark)
                 if character in (",", ":")
                 else accented_block(source_block, mark))
        placed[character] = (bytes(block), bytes(source_metric))
        installed.append((character, slot))

    for character, slot in assignment.items():
        bitmaps[slot], metrics[slot] = placed[character]

    blank = b"\0" * size
    metric_start = layout["text_end"]
    font_start = layout["font_start"]
    needed_metric = total_slots * 2
    shift = 0
    if font_start - metric_start < needed_metric:
        shift = needed_metric - (font_start - metric_start)
        shift += (-shift) % 16
        # the metric table's size is a per-resource layout choice, not a
        # format limit, so widen the gap and move the font block down
        expanded[font_start:font_start] = b"\0" * shift
        font_start += shift
        struct.pack_into("<I", expanded, 0x30, font_start)

    rebuilt = bytearray()
    for slot in range(total_slots):
        rebuilt.extend(bitmaps.get(slot, blank))
    expanded[font_start:] = rebuilt
    for slot in range(total_slots):
        at = metric_start + slot * 2
        expanded[at:at + 2] = metrics.get(slot, b"\0\0")
    struct.pack_into("<I", expanded, 0x20, len(expanded))
    struct.pack_into("<I", expanded, 0x34, total_slots)

    alphabet.clear()
    for character, slot in assignment.items():
        alphabet[slot] = character
    return installed, total_slots, shift

def remap_untranslated(expanded, metadata, replaced, remap, base, count,
                       new_base, displayed=None):
    """Re-encode every record part the translation writer leaves untouched."""
    pointers, next_offset = message_pointers(expanded, metadata)
    source_meta = dict(metadata)
    source_meta["glyph_base"] = base
    source_meta["glyph_count"] = count
    replacements = {}
    for _, message_id, offset in pointers:
        if displayed is not None and message_id not in displayed:
            continue
        spans = replaced.get(message_id, ())
        record = bytes(expanded[metadata["text_start"] + offset:
                                metadata["text_start"] + next_offset[offset]])
        for relative, run_end, tokens in parse_record(record, source_meta):
            part = record[relative:run_end]
            if any(relative < start + length
                   and start < run_end
                   for start, length in spans):
                continue
            rebuilt, changed = [], False
            for token in tokens:
                slot = token_slot(token, base, count)
                if slot is None or slot not in remap:
                    rebuilt.append(token)
                    continue
                target = remap[slot]
                rebuilt.append(slot_token(target, new_base))
                changed = changed or target != slot
            if not changed:
                continue
            replacements.setdefault(offset, []).append(
                (relative, len(part), pack_tokens(rebuilt, terminated=False),
                 part))
    return replacements

def untouched_workspace_characters(expanded, metadata, alphabet, translated,
                                   replaced):
    """Characters in selected rows whose source bytes need no replacement."""
    pointers, next_offset = message_pointers(expanded, metadata)
    result = set()
    for _, message_id, offset in pointers:
        if message_id not in translated or replaced.get(message_id):
            continue
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        for _, _, part in split_nonempty(record):
            try:
                tokens = byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = token_slot(token, metadata["glyph_base"],
                                  metadata["glyph_count"])
                if slot in alphabet:
                    result.add(alphabet[slot])
    return result

def remap_punctuation_to_period(text, alphabet):
    """Render commas/colons with the resource's established period glyph."""
    characters = (set(alphabet.values()) if hasattr(alphabet, "values")
                  else set(alphabet))
    for punctuation in (",", ":"):
        if punctuation not in characters:
            text = text.replace(punctuation, ".")
    return text

def safe_reuse_candidates(expanded, metadata, alphabet, rows):
    """Return slots used only inside the event fragments being translated."""
    pointers, next_offset = message_pointers(expanded, metadata)
    selected = set()
    scene_rows = bool(rows and "record_byte_offset" not in rows[0])
    if scene_rows:
        offsets = {}
        for _, message_id, record_offset in pointers:
            offsets.setdefault(message_id, record_offset)
        for message_id, runs in scene_run_plan(
                expanded, metadata, alphabet, rows).items():
            record_offset = offsets[message_id]
            selected.update((record_offset, start)
                            for start, _end, visible, target in runs
                            if target != visible)
    else:
        for row in rows:
            record_offset = int(row["record_byte_offset"], 0)
            parts = row_visible_parts(row)
            if parts:
                selected.update((record_offset, int(part["relative_offset"]))
                                for part in parts)
            else:
                selected.add((record_offset,
                              int(row["text_relative_offset"], 0) - record_offset))

    uses = {slot: set() for slot in range(metadata["glyph_count"])}
    for _, _, record_offset in pointers:
        record = expanded[
            metadata["text_start"] + record_offset:
            metadata["text_start"] + next_offset[record_offset]
        ]
        if scene_rows:
            token_runs = ((start, tokens)
                          for start, _end, tokens
                          in parse_record(record, metadata))
        else:
            token_runs = []
            for _, part_offset, part in split_nonempty(record):
                try:
                    token_runs.append((part_offset, byte_tokens(part)))
                except ValueError:
                    continue
        for part_offset, tokens in token_runs:
            location = (record_offset, part_offset)
            for token in tokens:
                slot = token_slot(
                    token, metadata["glyph_base"], metadata["glyph_count"])
                if slot is not None:
                    uses[slot].add(location)
    unused = [slot for slot, locations in uses.items() if not locations]
    selected_only = [slot for slot, locations in uses.items()
                     if locations and locations <= selected]
    return unused + selected_only

def clear_released_glyphs(expanded, layout, alphabet, needed, candidates,
                          installed):
    """Blank safe source glyphs which disappear from the translated fragments."""
    installed_slots = {slot for _, slot in installed}
    cleared = []
    for slot in candidates:
        character = alphabet.get(slot)
        if (slot in installed_slots or character is None or
                character in needed):
            continue
        start = layout["font_start"] + slot * layout["glyph_bytes"]
        expanded[start:start + layout["glyph_bytes"]] = \
            b"\0" * layout["glyph_bytes"]
        metric = layout["text_end"] + slot * 2
        expanded[metric:metric + 2] = b"\0\0"
        alphabet.pop(slot, None)
        cleared.append((character, slot))
    return cleared

def discover_generated_glyphs(expanded, layout, alphabet,
                              iso, original_alphabet=None,
                              original_blocks=None, reference=None,
                              target_resource=None,
                              recognize_accent_donors=True):
    """Recognize glyphs appended by :func:`append_required_glyphs`."""
    _, blocks = bitmap_fingerprints(expanded)
    char_to_slot = {character: slot for slot, character in alphabet.items()}
    iso = reference or iso

    def recognize(character, expected):
        matches = [slot for slot, block in enumerate(blocks)
                   if block == bytes(expected)]
        usable = [slot for slot in matches
                  if alphabet.get(slot) in (None, character)]
        if not usable:
            aliased = [slot for slot in matches
                       if alphabet.get(slot) is not None]
            if aliased:
                char_to_slot[character] = aliased[-1]
                print("warning: %r shares its bitmap with %r at slot %d; "
                      "encoder will draw them the same" %
                      (character, alphabet[aliased[-1]], aliased[-1]))
                return
            raise ValueError("could not recognize generated glyph %r" % character)
        # A few resources contain duplicate slots with the same bitmap.  They
        # are visually the same character and all must decode the same way.
        for slot in usable:
            alphabet[slot] = character
        char_to_slot[character] = usable[-1]

    for character, (resource, donor_slot) in BASIC_DONORS.items():
        if character in char_to_slot:
            continue
        block, _, glyph_bytes = donor_glyph(
            iso, resource, donor_slot)
        if glyph_bytes == layout["glyph_bytes"] and block in blocks:
            recognize(character, block)

    if recognize_accent_donors:
        for character, (block, _, glyph_bytes) in read_accent_donors().items():
            if character in char_to_slot:
                continue
            if glyph_bytes == layout["glyph_bytes"] and block in blocks:
                recognize(character, block)

    for character, row in AUTHORED.items():
        if character in char_to_slot:
            continue
        block = row["pixels"]
        if len(block) == layout["glyph_bytes"] and block in blocks:
            recognize(character, block)

    for character, (base, donor, _position) in glyph_compose.COMPOSITES.items():
        if character in char_to_slot:
            continue
        mark = ACCENT_MARKS.get(donor)
        base_slot = char_to_slot.get(base)
        if mark is None or base_slot is None:
            continue
        body = bytes(expanded[
            layout["font_start"] + base_slot * layout["glyph_bytes"]:
            layout["font_start"] + (base_slot + 1) * layout["glyph_bytes"]])
        try:
            block = glyph_compose.compose_character(
                body, character, glyph_compose.unpack(mark["pixels"]),
                mark["rows"], donor_bottom=mark.get("donor_bottom"))
        except ValueError:
            continue
        if block in blocks:
            recognize(character, block)

    for (atlas_resource, character), row in ATLAS.items():
        if target_resource is not None and atlas_resource != target_resource:
            continue
        if character in char_to_slot:
            continue
        block = row["pixels"]
        if len(block) == layout["glyph_bytes"] and block in blocks:
            recognize(character, block)

    period_slots = [slot for slot, character in alphabet.items()
                    if character == "."]
    if original_alphabet:
        period_slots.extend(
            slot for slot, character in original_alphabet.items()
            if character == "." and slot not in period_slots)
    if period_slots:
        for character in (",", ":"):
            if character not in char_to_slot:
                for period_slot in period_slots:
                    source_blocks = original_blocks or blocks
                    derived = punctuation_block(
                        source_blocks[period_slot], character)
                    if derived not in blocks:
                        continue
                    for slot, block in enumerate(blocks):
                        if block == derived:
                            alphabet[slot] = character
                            char_to_slot[character] = slot
                            break
                    if character in char_to_slot:
                        break

    name_comma_among_periods(expanded, layout, alphabet)

    for character, (base, mark) in ACCENTS.items():
        if character in char_to_slot or base not in char_to_slot:
            continue
        derived = accented_block(blocks[char_to_slot[base]], mark)
        if derived in blocks:
            recognize(character, derived)
    return alphabet

def name_comma_among_periods(expanded, layout, alphabet):
    """Tell a scene font's comma apart from its period by how far it descends."""
    if "," in set(alphabet.values()):
        return None
    candidates = [slot for slot, character in alphabet.items()
                  if character == "."]
    if len(candidates) != 2:
        return None
    depth = {}
    for slot in candidates:
        start = layout["font_start"] + slot * layout["glyph_bytes"]
        block = bytes(expanded[start:start + layout["glyph_bytes"]])
        rows = [y for y in range(28) for x in range(32)
                if glyph_value(block, x, y)]
        if not rows:
            return None
        depth[slot] = max(rows)
    deepest = max(depth, key=depth.get)
    shallowest = min(depth, key=depth.get)
    if depth[deepest] - depth[shallowest] < 2:
        return None
    alphabet[deepest] = ","
    return deepest, shallowest

def ambiguous_glyphs(expanded, layout, alphabet):
    """Characters claimed by several slots whose bitmaps actually differ."""
    _, blocks = bitmap_fingerprints(expanded)
    by_character = {}
    for slot, character in alphabet.items():
        by_character.setdefault(character, []).append(slot)
    report = {}
    for character, slots in by_character.items():
        if len(slots) < 2:
            continue
        distinct = {}
        for slot in sorted(slots):
            if slot < len(blocks):
                distinct.setdefault(blocks[slot], []).append(slot)
        if len(distinct) > 1:
            report[character] = [group for group in distinct.values()]
    return report

def describe_ambiguous(report):
    return "; ".join(
        "%r is claimed by slots %s with different bitmaps"
        % (character, " vs ".join(
            "/".join(str(slot) for slot in group) for group in groups))
        for character, groups in sorted(report.items()))
