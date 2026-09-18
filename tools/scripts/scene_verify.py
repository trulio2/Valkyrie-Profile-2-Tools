"""Read-back verification for patched scene text and fonts."""

import contextlib
import csv
import glob
import hashlib
import json
import os
import struct

from . import triace_ps2_unpack as triace
from .scene_fonts import (
    iso_alphabet, remap_punctuation_to_period, require_local_font,
)
from .scene_layout import (
    dialogue_max_lines, glyph_advances, record_owns_authored_layout,
    verification_dialogue_layout,
)
from .scene_records import (
    byte_tokens, clean_text, message_pointers, parse_record, split_nonempty,
    subtitle_fingerprints,
)
from .scene_text import (
    blank_referenced_glyphs, describe_substitutions, display_alphabet,
    is_scene_sheet, read_scene_rows, read_translated_rows, row_visible_parts,
    run_uses_local_font, split_fragment_translation,
    split_subtitle_translation, text_substitutions,
    verification_glyph_advances,
)
from .vp2_scene_fingerprint import glyph_blocks, local_alphabet, render_tokens
from .vp2_cutscene_subtitles import (
    FRAGMENT_MARKER, FileIso, OPENING_RESOURCE, PAGE_BREAK,
    PAGE_BREAK_SPELLING, RESOURCE_EXTRA_SLOTS, SPLIT_SUBTITLE_AUDIO,
    apply_hard_breaks, area_banner_message_ids, area_banner_visible_text,
    canonical_page_breaks, displayed_message_types, render_raw_tokens,
    wrap_translation,
)


def discover_generated_glyphs(*args, **kwargs):
    from .vp2_cutscene_subtitles import discover_generated_glyphs as implementation
    return implementation(*args, **kwargs)


def collapse(text):
    """Whitespace-insensitive form of a line."""
    return " ".join(
        text.replace(FRAGMENT_MARKER, " %s " % FRAGMENT_MARKER).split())

@contextlib.contextmanager
def _image(image):
    """An image to read entries from: an open reader, or a path to open."""
    if image is None or hasattr(image, "read_entry"):
        yield image
        return
    with open(image, "rb") as handle:
        _, total, table = triace.load_table(handle)
        yield FileIso(handle, table, total)

def page_breaks(text):
    """Page breaks a translation asks for, counted run by run as written."""
    return sum(len(PAGE_BREAK_SPELLING.findall(canonical_page_breaks(part)))
               for part in text.split(FRAGMENT_MARKER))

def verify_chapter_title(args, resource):
    """Compare the chapter title on the patched image against the expected one."""
    title = (getattr(args, "chapter_title", None) or "").strip()
    if not title:
        return False
    from . import vp2_title_face as title_face
    message_id = int(getattr(args, "chapter_title_message"))
    with _image(args.iso) as source, _image(args.reference_iso) as donor:
        actual = title_face.decode_title(
            source, resource, message_id, title, donor_iso=donor)
    if actual.lower() != title.lower():
        raise ValueError("chapter title decoded as %s, expected %s"
                         % (ascii(actual), ascii(title)))
    return True


def verify_scene_sheet(args):
    """Read a scene sheet's translations back off the disc, run by run."""
    rows = read_scene_rows(
        args.csv, args.resource,
        primary_lookup=getattr(args, "primary_lookup", None),
        reclaim_undrawn=getattr(args, "reclaim_undrawn", False))
    if not rows:
        if verify_chapter_title(args, int(args.resource)):
            print("verified chapter title in resource #%d: %s"
                  % (int(args.resource), args.iso))
            return
        raise ValueError("no translated rows in %s" % args.csv)
    resources = {int(row["resource_index"]) for row in rows}
    if len(resources) != 1:
        raise ValueError("verified rows must belong to one resource; found %s"
                         % ", ".join(str(index) for index in sorted(resources)))
    resource = resources.pop()
    with contextlib.ExitStack() as stack:
        reference = stack.enter_context(_image(args.reference_iso))
        with _image(args.iso) as iso:
            (_, _, _, expanded, layout,
             alphabet) = iso_alphabet(iso, resource, reference)
            require_local_font(resource, layout, alphabet)
            discover_generated_glyphs(
                expanded, layout, alphabet, iso,
                reference=reference, target_resource=resource)
            display = display_alphabet(
                expanded, layout, alphabet, args.en_names)
            verification_advances = verification_glyph_advances(
                expanded, layout, display, reference or iso)
            resource_raw = iso.read_entry(resource)
        source_font = None
        source_view = None
        if reference is not None:
            _, _, _, source_expanded, source_layout, source_alphabet = iso_alphabet(
                reference, resource)
            source_font = (source_expanded, source_layout)
            source_display = display_alphabet(
                source_expanded, source_layout, source_alphabet, args.en_names)
            source_metadata = {
                "table_start": struct.unpack_from("<I", source_expanded,
                                                   0x24)[0],
                "text_start": struct.unpack_from("<I", source_expanded,
                                                  0x28)[0],
                "text_end": struct.unpack_from("<I", source_expanded,
                                                0x2C)[0],
                "glyph_base": source_layout["glyph_base"],
                "glyph_count": source_layout["glyph_count"],
            }
            source_pointers, source_next = message_pointers(
                source_expanded, source_metadata)
            source_offsets = {message_id: offset
                              for _, message_id, offset in source_pointers}
            source_view = (source_expanded, source_metadata, source_display,
                           source_offsets, source_next)
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    advances = verification_advances
    pointers, next_offset = message_pointers(expanded, metadata)
    offsets = {}
    for _, message_id, offset in pointers:
        offsets.setdefault(message_id, offset)
    known_ids = set(offsets)
    area_banner_ids = area_banner_message_ids(
        expanded, metadata, known_ids)
    display_types = displayed_message_types(resource_raw, known_ids)
    displayed_ids = set(display_types) | area_banner_ids

    def rendered_record(image, meta, glyphs, record_offsets, following,
                        message_id):
        offset = record_offsets[message_id]
        start = meta["text_start"] + offset
        record = bytes(image[start:start + following[offset] - offset])
        drawn, breaks = [], 0
        for _, _, tokens in parse_record(record, meta):
            breaks += tokens.count(PAGE_BREAK)
            rendered, _, _ = render_tokens(tokens, meta, glyphs)
            visible = clean_text(rendered)
            if visible:
                drawn.append(visible)
        return (" %s " % FRAGMENT_MARKER).join(drawn), breaks

    mismatches, substituted, corrupted, fused = [], {}, [], []
    for row in rows:
        message_id = int(row["message_id"], 0)
        if message_id not in offsets:
            raise ValueError("no record for message %d" % message_id)
        actual, breaks = rendered_record(
            expanded, metadata, display, offsets, next_offset, message_id)
        expected_layout = None
        wanted_breaks = page_breaks(row["translated"])
        max_lines = dialogue_max_lines(display_types.get(message_id, ()))
        structured_local = False
        if FRAGMENT_MARKER in row["translated"]:
            record_offset = offsets[message_id]
            start = metadata["text_start"] + record_offset
            record = bytes(expanded[
                start:start + next_offset[record_offset] - record_offset])
            local_runs = []
            for _run_start, _run_end, tokens in parse_record(record, metadata):
                rendered, _, _ = render_tokens(tokens, metadata, display)
                if clean_text(rendered):
                    glyphs = [token for token in tokens if token < 0x8000]
                    if glyphs:
                        local_runs.append(run_uses_local_font(
                            glyphs, metadata, display))
            structured_local = bool(local_runs) and all(local_runs)
        record_owns_layout = False
        if source_view is not None and message_id in source_offsets:
            (source_expanded, source_metadata, source_display,
             source_offsets, source_next) = source_view
            source_start, source_end = (
                source_metadata["text_start"] + source_offsets[message_id],
                source_metadata["text_start"]
                + source_next[source_offsets[message_id]])
            source_runs = []
            for _run_start, _run_end, tokens in parse_record(
                    bytes(source_expanded[source_start:source_end]),
                    source_metadata):
                rendered, _, _ = render_tokens(
                    tokens, source_metadata, source_display)
                if clean_text(rendered):
                    source_runs.append(
                        (_run_start, _run_end, rendered,
                         clean_text(rendered), tokens))
            record_owns_layout = record_owns_authored_layout(
                source_runs, max_lines)
        expected_layout = verification_dialogue_layout(
            row["translated"], advances, max_lines,
            structured_local=structured_local,
            record_owns_layout=record_owns_layout)
        if expected_layout is not None:
            wanted_breaks = page_breaks(expected_layout)
        if breaks != wanted_breaks:
            fused.append((row["audio_id"], message_id, wanted_breaks, breaks))
        expected = (expected_layout if expected_layout is not None
                    else render_raw_tokens(
                        apply_hard_breaks(row["translated"])))
        compared_actual = actual
        if collapse(compared_actual) == collapse(expected):
            continue
        allowed, rejected = describe_substitutions(
            text_substitutions(expected, compared_actual),
            set(display.values()))
        if rejected:
            corrupted.append((row["audio_id"], rejected))
        else:
            for pair, count in allowed.items():
                substituted[pair] = substituted.get(pair, 0) + count
        if not allowed and not rejected:
            mismatches.append((row["audio_id"], message_id, expected,
                               compared_actual))

    if source_view is not None:
        translated_ids = {int(row["message_id"], 0) for row in rows}
        with open(args.csv, newline="", encoding="utf-8-sig") as source:
            sheet_rows = list(csv.DictReader(source))
        (source_expanded, source_metadata, source_display,
         source_offsets, source_next) = source_view
        from .vp2_title_face import CHAPTER_RECORDS
        for sheet_row in sheet_rows:
            raw_id = (sheet_row.get("message_id") or "").strip()
            if not raw_id:
                continue
            message_id = int(raw_id, 0)
            if message_id in translated_ids \
                    or message_id not in displayed_ids \
                    or (resource, message_id) in CHAPTER_RECORDS \
                    or not (sheet_row.get("original_en") or "").strip() \
                    or message_id not in offsets \
                    or message_id not in source_offsets:
                continue
            source_text = sheet_row["original_en"]
            if message_id in area_banner_ids:
                source_text = area_banner_visible_text(source_text)
            expected = render_raw_tokens(source_text)
            actual, _ = rendered_record(
                expanded, metadata, display, offsets, next_offset, message_id)
            if collapse(actual) != collapse(expected):
                label = (sheet_row.get("audio_id") or
                         "r%d-m%04d" % (resource, message_id))
                mismatches.append((label, message_id, expected, actual))

    for audio_id, message_id, expected, actual in mismatches[:12]:
        print("%s message %d expected %s, read %s" %
              (audio_id, message_id, ascii(expected), ascii(actual)))
    for audio_id, rejected in corrupted[:12]:
        print("%s draws %s" % (audio_id, ", ".join(
            "%s as %s" % (ascii(w), ascii(d)) for w, d in rejected[:4])))
    for audio_id, message_id, wanted, got in fused[:12]:
        print("%s message %d should break into %d page(s) but the record "
              "holds %d page break token(s); the dashes are being drawn "
              "instead of breaking the page" %
              (audio_id, message_id, wanted + 1, got))
    if mismatches or corrupted or fused:
        raise ValueError("%d row(s) do not read back as written" %
                         (len(mismatches) + len(corrupted) + len(fused)))
    if substituted:
        print("substituted (font has no such glyph): %s" % ", ".join(
            "%s->%s x%d" % (ascii(w), ascii(d), n)
            for (w, d), n in sorted(substituted.items())))
    if source_font is not None:
        lost = blank_referenced_glyphs(
            expanded, layout, metadata, *source_font,
            displayed=displayed_ids)
        if lost:
            for slot, messages in sorted(lost.items()):
                shown = sorted(messages)
                print("slot %d is blank but drawn by message%s %s" %
                      (slot, "" if len(shown) == 1 else "s",
                       ", ".join(str(m) for m in shown[:6])))
            raise ValueError(
                "%d glyph slot(s) lost a bitmap a record still draws" % len(lost))
    gate = "" if source_font is not None else " (font gate skipped: no --reference-iso)"
    titled = verify_chapter_title(args, resource)
    print("verified %d translated rows%s in resource #%d: %s%s" %
          (len(rows), " plus chapter title" if titled else "",
           resource, args.iso, gate))

def cmd_verify(args):
    if is_scene_sheet(args.csv):
        verify_scene_sheet(args)
        return
    rows = read_translated_rows(args.csv, args.audio_id)
    resources = {int(row["resource_index"]) for row in rows}
    if len(resources) != 1:
        raise ValueError("verified subtitles must belong to one resource")
    resource = resources.pop()
    reference_file = (open(args.reference_iso, "rb")
                      if args.reference_iso else None)
    try:
        reference = None
        if reference_file is not None:
            _, reference_total, reference_table = triace.load_table(reference_file)
            reference = FileIso(reference_file, reference_table, reference_total)
        with open(args.iso, "rb") as source:
            _, total, table = triace.load_table(source)
            iso = FileIso(source, table, total)
            (_, _, _, expanded, layout,
             alphabet) = iso_alphabet(iso, resource, reference)
            require_local_font(resource, layout, alphabet)
            original_alphabet = None
            original_blocks = None
            if resource != OPENING_RESOURCE:
                candidate = os.path.join(
                    os.path.dirname(args.csv), "scene-inventory",
                    "resource-%04d" % resource)
                if os.path.isdir(candidate):
                    paths = glob.glob(os.path.join(candidate, "*.json"))
                    if len(paths) == 1:
                        with open(paths[0], encoding="utf-8") as inventory_file:
                            inventory_metadata = json.load(inventory_file)
                        fingerprints = subtitle_fingerprints(
                            os.path.dirname(candidate))
                        original_alphabet = local_alphabet(
                            candidate, inventory_metadata, fingerprints)
                        original_alphabet.update(
                            RESOURCE_EXTRA_SLOTS.get(resource, {}))
                        original_blocks = glyph_blocks(
                            candidate, inventory_metadata)
            discover_generated_glyphs(
                expanded, layout, alphabet, iso,
                original_alphabet, original_blocks, reference,
                target_resource=resource)
        source_font = None
        if reference_file is not None:
            _, _, _, source_expanded, source_layout, _ = iso_alphabet(
                reference, resource)
            source_font = (source_expanded, source_layout)
    finally:
        if reference_file is not None:
            reference_file.close()

    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    # The same measurement the patch wrapped with, rebuilt from the image it
    # wrote, so a re-wrapped line reads back as itself.
    advances = glyph_advances(expanded, metadata["text_end"], alphabet)
    pointers, next_offset = message_pointers(expanded, metadata)
    by_id = {}
    for _, message_id, record_offset in pointers:
        by_id.setdefault(message_id, []).append(record_offset)

    mismatches = []
    drawn_rows = []
    for row in rows:
        message_id = int(row["message_id"], 0)
        offsets = by_id.get(message_id, [])
        if len(offsets) != 1:
            raise ValueError("expected one ISO record for message %d, found %d" %
                             (message_id, len(offsets)))
        record_offset = offsets[0]
        record = expanded[
            metadata["text_start"] + record_offset:
            metadata["text_start"] + next_offset[record_offset]
        ]
        candidates = []
        blank = []
        for _, part_offset, part in split_nonempty(record):
            try:
                tokens = byte_tokens(part)
            except ValueError:
                # A part ending inside a two-byte token is not text at all:
                # the null it was split on belongs to binary event parameters.
                continue
            rendered, matched, unknown = render_tokens(
                tokens, metadata, alphabet)
            visible = clean_text(rendered)
            if matched and visible:
                candidates.append((len(visible), matched, -unknown,
                                   -part_offset, rendered))
            elif matched:
                blank.append((0, matched, -unknown, -part_offset, rendered))
        candidates = candidates or blank
        if not candidates:
            raise ValueError("message %d has no decodable subtitle" % message_id)
        visible_parts = row_visible_parts(row)
        if len(visible_parts) > 1:
            targets = split_fragment_translation(
                row["translated"], len(visible_parts), row["audio_id"])
            expected_parts = [remap_punctuation_to_period(wrap_translation(
                target,
                [int(value, 16) for value in part["source_tokens"].split()],
                advances),
                alphabet)
                for target, part in zip(targets, visible_parts)]
            actual_parts = [clean_text(item[-1]) for item in candidates]
            missing_parts = [part for part in expected_parts
                             if part not in actual_parts]
            if missing_parts:
                mismatches.append((
                    row["audio_id"], message_id,
                    (" %s " % FRAGMENT_MARKER).join(expected_parts),
                    " / ".join(actual_parts)))
            continue
        if row["audio_id"].casefold() == SPLIT_SUBTITLE_AUDIO.casefold():
            actual_parts = [clean_text(item[-1]) for item in candidates]
            expected_parts = split_subtitle_translation(row["translated"])
            missing_parts = [part for part in expected_parts
                             if part not in actual_parts]
            if missing_parts:
                mismatches.append((
                    row["audio_id"], message_id,
                    "--".join(expected_parts), " / ".join(actual_parts)))
            continue
        rendered = max(candidates)[-1]
        actual = clean_text(rendered)
        source_tokens = [int(value, 16)
                         for value in row["source_tokens"].split()]
        expected = remap_punctuation_to_period(
            wrap_translation(row["translated"].strip(), source_tokens, advances),
            alphabet)
        if actual != expected:
            mismatches.append((row["audio_id"], message_id, expected, actual))
        else:
            drawn_rows.append((row, actual))

    if mismatches:
        for audio_id, message_id, expected, actual in mismatches:
            print("%s message %d expected %s, decoded %s" %
                  (audio_id, message_id, ascii(expected), ascii(actual)))
        raise ValueError("%d subtitle read-back mismatches" % len(mismatches))
    verified_title = verify_chapter_title(args, resource)
    substituted = {}
    corrupted = []
    for row, actual in drawn_rows:
        allowed, rejected = describe_substitutions(
            text_substitutions(render_raw_tokens(row["translated"]), actual),
            set(alphabet.values()))
        for pair, count in allowed.items():
            substituted[pair] = substituted.get(pair, 0) + count
        if rejected:
            corrupted.append((row["audio_id"], rejected))
    if corrupted:
        for audio_id, rejected in corrupted[:12]:
            print("%s draws %s" % (audio_id, ", ".join(
                "%s as %s" % (ascii(w), ascii(d)) for w, d in rejected[:4])))
        raise ValueError("%d row(s) draw text the CSV did not ask for"
                         % len(corrupted))
    if substituted:
        print("substituted (font has no such glyph): %s" % ", ".join(
            "%s->%s x%d" % (ascii(w), ascii(d), n)
            for (w, d), n in sorted(substituted.items())))

    # The font gate.  Every check above reads text, and text cannot see a
    # glyph whose bitmap was dropped while the token still points at it.
    if source_font is not None:
        lost = blank_referenced_glyphs(expanded, layout, metadata, *source_font)
        if lost:
            for slot, messages in sorted(lost.items()):
                shown = sorted(messages)
                print("slot %d is blank but drawn by message%s %s%s" %
                      (slot, "" if len(shown) == 1 else "s",
                       ", ".join(str(m) for m in shown[:6]),
                       "" if len(shown) <= 6 else " and %d more" % (len(shown) - 6)))
            raise ValueError(
                "%d glyph slot(s) lost a bitmap a record still draws" % len(lost))
    suffix = " plus chapter title" if verified_title else ""
    gate = "" if source_font is not None else " (font gate skipped: no --reference-iso)"
    print("verified %d translated subtitles%s in resource #%d: %s%s" %
          (len(rows), suffix, resource, args.iso, gate))
