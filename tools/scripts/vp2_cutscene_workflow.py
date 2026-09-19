#!/usr/bin/env python3
"""Create and preflight reusable VP2 cutscene subtitle translation sheets."""
import argparse
import csv
import os
import struct

from . import slz
from . import slz_compress
from . import triace_ps2_unpack as triace
from . import vp2_dcms as dcms
from . import vp2_cutscene_subtitles as subtitles

GLYPH_COST = 76


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def cmd_export(args):
    class ExportArgs:
        inventory_dir = args.inventory_dir
        csv = args.csv
        resource = args.resource
        message_id_min = args.message_id_min
        message_id_max = args.message_id_max
        message_id = args.message_id
    subtitles.cmd_export_records(ExportArgs)


DISPLAY_OPCODE = bytes((0x07, 0x41, 0x00, 0x00))
DISPLAY_ID_AT = 20
DISPLAY_TAIL_AT = 24
DISPLAY_TAIL = struct.pack("<f", 1.0)


def ecs_display_ids(raw, known=None):
    """Return [(script offset, message id)] for every display instruction."""
    body = None
    for tag, offset, length in dcms.parse_pk1(raw):
        if tag == "ECS":
            body = raw[offset:offset + length]
    if body is None:
        return []
    plain = slz.decompress(body) if body[:3] == b"SLZ" else body
    found, start = [], 0
    while True:
        at = plain.find(DISPLAY_OPCODE, start)
        if at < 0:
            break
        start = at + 1
        if at + DISPLAY_TAIL_AT + 4 > len(plain):
            continue
        if plain[at + DISPLAY_TAIL_AT:at + DISPLAY_TAIL_AT + 4] != DISPLAY_TAIL:
            continue
        message_id = struct.unpack_from("<I", plain, at + DISPLAY_ID_AT)[0]
        if known is None or message_id in known:
            found.append((at, message_id))
    return found


SCENE_SCRIPT_GAP = 1500
SCENE_ID_GAP = 16


def derive_scenes(raw, message_ids, title_messages=()):
    """Group a resource's messages into the scenes its event script plays."""
    titles = set(title_messages)
    shown = {}
    for offset, message_id in ecs_display_ids(raw, set(message_ids)):
        if message_id in titles:
            continue
        entry = shown.setdefault(message_id, {"first": offset, "count": 0})
        entry["first"] = min(entry["first"], offset)
        entry["count"] += 1
    order = sorted(shown, key=lambda mid: (shown[mid]["first"], mid))
    scenes = []
    current = []
    for index, message_id in enumerate(order):
        if index:
            previous = order[index - 1]
            script_gap = shown[message_id]["first"] - shown[previous]["first"]
            if (script_gap > SCENE_SCRIPT_GAP and
                    abs(message_id - previous) > SCENE_ID_GAP):
                scenes.append(current)
                current = []
        current.append(message_id)
    if current:
        scenes.append(current)
    result = []
    for group in scenes:
        lines = [(message_id, shown[message_id]["first"],
                  shown[message_id]["count"]) for message_id in group]
        result.append({"first_offset": shown[group[0]]["first"], "lines": lines})
    return result


def scene_details(times):
    """The translator-facing note for one line, empty when there is none."""
    return "shown %d times" % times if times > 1 else ""


SPEAKER_OPCODE = bytes((0xE0, 0x01, 0x8C, 0x00))
SPEAKER_ID_AT = 16
SPEAKER_TAIL_AT = 12
SPEAKER_TAIL = struct.pack("<f", 1.0)
SPEAKER_AFTER_DISPLAY = 12


def ecs_speakers(raw):
    """Return [(script offset, name message id)] for every speaker instruction."""
    body = None
    for tag, offset, length in dcms.parse_pk1(raw):
        if tag == "ECS":
            body = raw[offset:offset + length]
    if body is None:
        return []
    plain = slz.decompress(body) if body[:3] == b"SLZ" else body
    found, start = [], 0
    while True:
        at = plain.find(SPEAKER_OPCODE, start)
        if at < 0:
            break
        start = at + 1
        if at + SPEAKER_ID_AT + 4 > len(plain):
            continue
        if plain[at + SPEAKER_TAIL_AT:at + SPEAKER_TAIL_AT + 4] != SPEAKER_TAIL:
            continue
        found.append((at, struct.unpack_from("<I", plain, at + SPEAKER_ID_AT)[0]))
    return found


def scene_text(handle, table, total, resource, reference=None):
    """Return (layout, alphabet, {message id: decoded text}) for a resource."""
    iso_handle = subtitles.FileIso(handle, table, total)
    iso_reference = (subtitles.FileIso(*reference)
                     if reference else None)
    _, _, _, expanded, layout, alphabet = subtitles.iso_alphabet(
        iso_handle, resource, iso_reference)
    subtitles.discover_generated_glyphs(
        expanded, layout, alphabet, iso_handle, reference=iso_reference)
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    texts, slot_use = {}, {}
    for _, message_id, offset in pointers:
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        best = ""
        for _, _, part in subtitles.split_nonempty(record):
            try:
                tokens = subtitles.byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = subtitles.token_slot(token, metadata["glyph_base"],
                                            metadata["glyph_count"])
                if slot is not None:
                    slot_use.setdefault(slot, set()).add(message_id)
            rendered, matched, _ = subtitles.render_tokens(
                tokens, metadata, alphabet)
            if matched >= 3 and len(rendered) > len(best):
                best = subtitles.clean_text(rendered)
        if best:
            texts[message_id] = best
    return layout, alphabet, metadata, texts, slot_use


def cmd_recut(args):
    """Plan a full font re-cut: what a target character set would cost."""
    reference_file = (open(args.reference_iso, "rb")
                      if args.reference_iso else None)
    try:
        reference = None
        if reference_file is not None:
            _, rtotal, rtable = triace.load_table(reference_file)
            reference = (reference_file, rtable, rtotal)
        with open(args.iso, "rb") as handle:
            _, total, table = triace.load_table(handle)
            layout, alphabet, metadata, texts, slot_use = scene_text(
                handle, table, total, args.resource, reference)
    finally:
        if reference_file is not None:
            reference_file.close()

    present = {c for c in alphabet.values() if c != "\n"}
    current = set()
    for text in texts.values():
        current |= set(text)
    current.discard("\n")

    if args.from_sheet:
        translated_ids, target = set(), set()
        with open(args.from_sheet, newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                written = row.get("translated")
                text = subtitles.apply_hard_breaks(
                    (written or "").replace(subtitles.FRAGMENT_MARKER, ""))
                key = (row.get("message_id") or "").strip()
                if text.strip() and key.isdigit():
                    translated_ids.add(int(key))
                    target |= set(text)
        if not target:
            raise ValueError("%s has no translated rows" % args.from_sheet)
        pinned = sorted(mid for mid in texts if mid not in translated_ids)
        for message_id in pinned:
            target |= set(texts[message_id])
        target.discard("\n")
        source_label = ("%s (%d translated, %d still English)"
                        % (args.from_sheet, len(translated_ids), len(pinned)))
    elif args.target_chars:
        target = set(args.target_chars)
        source_label = "--target-chars"
    else:
        target = current
        source_label = "source character set"

    display = {slot for slot in slot_use if slot not in alphabet}
    usable = layout["glyph_count"] - len(display)

    keep = sorted(target & present)
    drop = sorted(present - target)
    add = sorted(target - present)
    metric_room = ((layout["font_start"] - metadata["text_end"]) // 2
                   - layout["glyph_count"])
    room = max(metric_room, 0) + max(args.extra_slots, 0)
    free_after = usable - len(keep) + room
    verdict = "FITS" if len(add) <= free_after else "OVER by %d" % (
        len(add) - free_after)

    print("resource #%d | target set: %s" % (args.resource, source_label))
    print("  font slots            : %d (%d held by the display face)"
          % (layout["glyph_count"], len(display)))
    print("  usable for text       : %d" % usable)
    print("  spare metric entries  : %d" % max(metric_room, 0))
    print("  characters present    : %d" % len(present))
    print("  characters in the text: %d" % len(current))
    print("  target characters     : %d" % len(target))
    print()
    print("  keep  (already cut)   : %d  %s" % (len(keep), "".join(keep)))
    print("  drop  (frees a slot)  : %d  %s" % (len(drop), "".join(drop)))
    print("  add   (needs a glyph) : %d  %s" % (len(add), "".join(add)))
    print()
    print("  slots free after re-cut: %d" % free_after)
    print("  verdict                : %s" % verdict)
    if drop:
        print("\n  dropped characters appear in these messages:")
        for character in drop[:8]:
            users = sorted(m for m, t in texts.items() if character in t)
            print("     %r in %d message(s): %s" %
                  (character, len(users),
                   ", ".join(str(m) for m in users[:8])))


def cmd_audit(args):
    """Prove a sheet covers every message the event script actually shows."""
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        raw = dcms.read_entry(handle, table, total, args.resource)
        iso_handle = subtitles.FileIso(handle, table, total)
        _, _, _, expanded, layout, alphabet = subtitles.iso_alphabet(
            iso_handle, args.resource)
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    records = {mid: offset for _, mid, offset in pointers}
    displayed = ecs_display_ids(raw, set(records))

    def classify(message_id):
        offset = records[message_id]
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        best, slots, unknown = 0, 0, 0
        for _, _, part in subtitles.split_nonempty(record):
            try:
                tokens = subtitles.byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = subtitles.token_slot(token, metadata["glyph_base"],
                                            metadata["glyph_count"])
                if slot is not None:
                    slots += 1
                    if slot not in alphabet:
                        unknown += 1
            _, matched, _ = subtitles.render_tokens(tokens, metadata, alphabet)
            best = max(best, matched)
        if slots and unknown * 2 > slots:
            return "chapter title"
        if best >= 3:
            return "text"
        if slots:
            return "unrecognised glyphs"
        return "no local-font text"

    order = []
    for _, message_id in displayed:
        if message_id not in order:
            order.append(message_id)
    kinds = {mid: classify(mid) for mid in order}
    text_ids = [mid for mid in order if kinds[mid] == "text"]

    print("resource #%d" % args.resource)
    print("  message table       : %d records" % len(records))
    print("  display instructions: %d (%d distinct messages)"
          % (len(displayed), len(order)))
    print("  never displayed     : %d" % len(set(records) - set(order)))
    for kind in ("text", "chapter title", "unrecognised glyphs",
                 "no local-font text"):
        listed = [mid for mid in order if kinds[mid] == kind]
        print("  displayed, %-20s: %d" % (kind, len(listed)))
        if kind != "text" and listed:
            print("      %s" % ", ".join(str(mid) for mid in listed[:16]))

    if not args.csv:
        print("\npass --csv to check a translation sheet against this list")
        return
    with open(args.csv, newline="", encoding="utf-8-sig") as source:
        sheet = {int(row["message_id"]) for row in csv.DictReader(source)
                 if (row.get("message_id") or "").strip().isdigit()}
    missing = [mid for mid in text_ids if mid not in sheet]
    extra = sorted(sheet - set(order))
    print("\nsheet %s" % args.csv)
    print("  rows with a message id : %d" % len(sheet))
    print("  displayed text covered : %d/%d" % (len(text_ids) - len(missing),
                                                len(text_ids)))
    if missing:
        print("  MISSING from the sheet : %s"
              % ", ".join(str(mid) for mid in missing))
    if extra:
        print("  in the sheet but never displayed: %d (%s)"
              % (len(extra), ", ".join(str(mid) for mid in extra[:12])))
    if not missing:
        print("  complete: every displayed message with text is in the sheet")
    elif getattr(args, "strict", False):
        raise SystemExit(
            "audit: %d displayed text message(s) missing from sheet %s" %
            (len(missing), args.csv))


def resource_budget(handle, table, total, resource, measure_reclaim=False,
                    reclaim_limit=0, reference=None):
    """Report how much room a scene resource has for a translated font."""
    raw = dcms.read_entry(handle, table, total, resource)
    if not raw or triace.classify(raw, len(raw)) != "pk1":
        raise ValueError("resource #%d is not a PK1 archive" % resource)

    iso_handle = subtitles.FileIso(handle, table, total)
    iso_reference = (subtitles.FileIso(*reference)
                     if reference else None)
    _, _, _, expanded, layout, alphabet = subtitles.iso_alphabet(
        iso_handle, resource, iso_reference)
    subtitles.discover_generated_glyphs(
        expanded, layout, alphabet, iso_handle, reference=iso_reference)

    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    referenced = set()
    messages = 0
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    for _, _, offset in pointers:
        messages += 1
        record = expanded[metadata["text_start"] + offset:
                          metadata["text_start"] + next_offset[offset]]
        for _, _, part in subtitles.split_nonempty(record):
            try:
                tokens = subtitles.byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = subtitles.token_slot(
                    token, metadata["glyph_base"], metadata["glyph_count"])
                if slot is not None:
                    referenced.add(slot)

    metric_room = ((layout["font_start"] - metadata["text_end"]) // 2
                   - layout["glyph_count"])

    dcms_stored = dcms_stream = None
    for tag, offset, length in dcms.parse_pk1(raw):
        if tag == "DCMS":
            dcms_stored = length
            dcms_stream = 16 + struct.unpack_from(
                "<I", raw[offset:offset + length], 4)[0]

    report = {
        "resource": resource,
        "messages": messages,
        "glyph_count": layout["glyph_count"],
        "identified": len(set(alphabet.values())),
        "referenced": len(referenced),
        "unreferenced": layout["glyph_count"] - len(referenced),
        "metric_room": metric_room,
        "dcms_stored": dcms_stored,
        "dcms_stream": dcms_stream,
        "slot_slack": (dcms_stored - dcms_stream) if dcms_stored else 0,
        "reencode_gain": None,
        "reclaim": None,
        "budget": None,
        "new_glyphs": None,
    }
    if not measure_reclaim:
        return report

    gain = 0
    freed = 0
    for tag, offset, length in dcms.parse_pk1(raw):
        body = raw[offset:offset + length]
        if body[:3] != b"SLZ" or body[3] not in (1, 2, 3):
            continue
        if reclaim_limit and length > reclaim_limit:
            continue
        plain = slz.decompress(body)
        packed = slz_compress.compress(plain, mode=body[3])
        if slz.decompress(packed) != plain:
            raise ValueError("%s re-encode did not round trip" % tag)
        stream = 16 + struct.unpack_from("<I", body, 4)[0]
        if tag == "DCMS":
            gain = max(0, stream - len(packed))
        else:
            freed += max(0, length - len(packed))
    report["reencode_gain"] = gain
    report["reclaim"] = freed
    report["budget"] = report["slot_slack"] + gain + freed
    report["new_glyphs"] = min(report["budget"] // GLYPH_COST,
                               max(metric_room, 0) + report["unreferenced"])
    return report


def cmd_budget(args):
    resources = list(args.resource or [])
    if args.manifest:
        with open(args.manifest, newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                if row["type"] == "pk1":
                    index = int(row["index"])
                    if index not in resources:
                        resources.append(index)
        resources.sort()
    if not resources:
        raise ValueError("name at least one --resource, or pass --manifest")

    reference_file = (open(args.reference_iso, "rb")
                      if args.reference_iso else None)
    rows = []
    try:
        reference = None
        if reference_file is not None:
            _, reference_total, reference_table = triace.load_table(reference_file)
            reference = (reference_file, reference_table, reference_total)
        with open(args.iso, "rb") as handle:
            _, total, table = triace.load_table(handle)
            header = ("%-7s %-8s %-6s %-6s %-6s %-7s %-9s %-8s" %
                      ("res", "messages", "slots", "used", "spare",
                       "+slots", "slot slack", "chars"))
            if args.measure_reclaim:
                header += " %-9s %-8s" % ("budget", "+glyphs")
            print(header)
            for resource in resources:
                try:
                    report = resource_budget(
                        handle, table, total, resource, args.measure_reclaim,
                        args.reclaim_limit, reference)
                except (ValueError, KeyError, IndexError, struct.error,
                        ZeroDivisionError):
                    continue
                line = ("%-7d %-8d %-6d %-6d %-6d %-7d %-9d %-8d" %
                        (report["resource"], report["messages"],
                         report["glyph_count"], report["referenced"],
                         report["unreferenced"], max(report["metric_room"], 0),
                         report["slot_slack"], report["identified"]))
                if args.measure_reclaim:
                    line += " %-9d %-8d" % (report["budget"],
                                            report["new_glyphs"])
                print(line, flush=True)
                rows.append(report)
    finally:
        if reference_file is not None:
            reference_file.close()

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print("\nwrote %s" % args.csv)
    print("\n%d resources reported. 'spare' counts font slots no message "
          "references; '+slots' is metric-table room for appended glyphs."
          % len(rows))
    if args.measure_reclaim:
        print("'budget' is the DCMS slot slack plus what re-encoding this "
              "archive's subresources frees; '+glyphs' divides it by the ~%d "
              "bytes an appended glyph costs, capped by available slots."
              % GLYPH_COST)


def cmd_merge(args):
    """Combine every sheet targeting one resource into a single sheet."""
    fields = []
    rows = []
    for path in args.csv:
        with open(path, newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            for name in reader.fieldnames or []:
                if name not in fields:
                    fields.append(name)
            for row in reader:
                rows.append((path, row))
    if not rows:
        raise ValueError("no rows found in %s" % ", ".join(args.csv))

    resources = {row["resource_index"] for _, row in rows if row.get("resource_index")}
    if len(resources) != 1:
        raise ValueError("sheets must target one resource; found %s" %
                         ", ".join(sorted(resources)))

    def translated(row):
        return bool((row.get("translated") or "").strip())

    def order(row):
        value = (row.get("message_index") or "").strip()
        return int(value) if value.isdigit() else 1 << 30

    best, extras, claimed = {}, [], {}
    for path, row in rows:
        key = (row.get("message_id") or "").strip()
        if not key:
            extras.append(row)
            continue
        if translated(row):
            if key in claimed and claimed[key] != path:
                raise ValueError(
                    "message %s is translated in both %s and %s; resolve the "
                    "conflict before merging" % (key, claimed[key], path))
            claimed[key] = path
        if key not in best or (translated(row) and not translated(best[key])):
            best[key] = row
    merged = sorted(list(best.values()) + extras, key=order)

    if os.path.abspath(args.out) in {os.path.abspath(p) for p in args.csv}:
        raise ValueError("output would overwrite an input sheet")
    with open(args.out, "w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for row in merged:
            writer.writerow({name: row.get(name, "") or "" for name in fields})
    print("merged %d sheets -> %s" % (len(args.csv), args.out))
    print("resource #%s | %d unique messages | %d translated"
          % (resources.pop(), len(merged),
             sum(1 for row in merged if translated(row))))


def cmd_scenes(args):
    """List the scenes an event resource plays, in the order it plays them."""
    from . import vp2_title_face as title_face
    with open(args.iso, "rb") as source:
        _, total, table = triace.load_table(source)
        raw = dcms.read_entry(source, table, total, args.resource)
        iso_handle = subtitles.FileIso(source, table, total)
        _, _, _, expanded, layout, _ = subtitles.iso_alphabet(
            iso_handle, args.resource)
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    pointers, _ = subtitles.message_pointers(expanded, metadata)
    known = {message_id for _, message_id, _ in pointers}
    titles = [message for resource, message in title_face.CHAPTER_RECORDS
              if resource == args.resource]
    scenes = derive_scenes(raw, known, titles)
    shown = sum(len(scene["lines"]) for scene in scenes)
    print("resource #%d: %d scenes, %d displayed messages of %d records%s" %
          (args.resource, len(scenes), shown, len(known),
           " (chapter title %s excluded)" % titles[0] if titles else ""))
    for index, scene in enumerate(scenes, 1):
        ids = [line[0] for line in scene["lines"]]
        print("  scene %d: %d lines, ids %d-%d, script @%d" %
              (index, len(ids), min(ids), max(ids), scene["first_offset"]))
        if args.lines:
            for message_id, offset, times in scene["lines"]:
                note = scene_details(times)
                print("      %5d @%-8d%s" % (message_id, offset,
                                             "  %s" % note if note else ""))


EN_NAMES_DEFAULT = os.path.join("data", "vp2", "vp2-en-glyphs.csv")


from .scene_sheet_export import (
    SHEET_FIELDS, VOICE_HEADER, align_japanese, cmd_sheet, cmd_sheet_all,
    japanese_for, manifest_voice_scene, name_unfingerprinted,
    read_glyph_names, record_text, record_voice, sheet_rows, write_sheet,
)


def cmd_preflight(args):
    rows = [row for row in read_rows(args.csv)
            if row.get("message_id") and row.get("translated", "").strip()]
    if not rows:
        raise ValueError("no translated rows in %s" % args.csv)
    resources = {int(row["resource_index"]) for row in rows}
    if len(resources) != 1:
        raise ValueError("the sheet must contain one resource; found %s" %
                         ", ".join(map(str, sorted(resources))))
    resource = resources.pop()
    with open(args.iso, "rb") as source:
        _, total, table = triace.load_table(source)
        iso_handle = subtitles.FileIso(source, table, total)
        _, _, _, expanded, layout, alphabet = subtitles.iso_alphabet(
            iso_handle, resource)
        subtitles.discover_generated_glyphs(
            expanded, layout, alphabet, iso_handle)
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    needed = set("".join(
        subtitles.apply_hard_breaks(
            row["translated"].replace(subtitles.FRAGMENT_MARKER, ""))
        for row in rows))
    existing = set(alphabet.values())
    missing = sorted(character for character in needed
                     if character != "\n" and character not in existing)
    candidates = subtitles.safe_reuse_candidates(
        expanded, metadata, alphabet, rows)
    protected = {}
    for slot, character in alphabet.items():
        if character in needed and character not in protected:
            protected[character] = slot
    available = [slot for slot in candidates
                 if alphabet.get(slot) not in needed or
                 protected.get(alphabet.get(slot)) != slot]
    print("resource #%d | translated rows: %d" % (resource, len(rows)))
    print("font: %d slots | safe reusable slots: %d" %
          (layout["glyph_count"], len(available)))
    print("missing glyphs: %s" % (", ".join(repr(c) for c in missing) or "none"))
    if len(missing) > len(available):
        raise ValueError("translation needs %d new glyphs but only %d safe slots are available; "
                         "split the scene or add a scene-specific font plan" %
                         (len(missing), len(available)))
    for row in rows:
        parts = subtitles.row_visible_parts(row)
        if len(parts) > 1 and subtitles.FRAGMENT_MARKER not in row["translated"]:
            raise ValueError("%s has %d visible fragments; separate its translation with %s" %
                             (row["audio_id"], len(parts), subtitles.FRAGMENT_MARKER))
    print("preflight OK: safe-font-reuse can patch this sheet")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="create a blank translation sheet")
    export.add_argument("inventory_dir")
    export.add_argument("csv")
    export.add_argument("--resource", type=int, required=True)
    export.add_argument("--message-id-min", type=int, default=1)
    export.add_argument("--message-id-max", type=int, default=0xFFFFFFFF)
    export.add_argument("--message-id", type=int, action="append")
    export.set_defaults(func=cmd_export)
    recut = commands.add_parser(
        "recut", help="plan a full font re-cut for a target character set")
    recut.add_argument("iso")
    recut.add_argument("--resource", type=int, required=True)
    recut.add_argument("--from-sheet",
                       help="take the target set from a sheet's translation "
                            "column (translated)")
    recut.add_argument("--target-chars",
                       help="target character set given literally")
    recut.add_argument("--extra-slots", type=int, default=0,
                       help="metric-table room for appended glyphs, from the "
                            "budget command's '+slots'")
    recut.add_argument("--reference-iso",
                       help="pristine ROM for glyph fingerprints")
    recut.set_defaults(func=cmd_recut)
    audit = commands.add_parser(
        "audit", help="check a sheet covers every message the script displays")
    audit.add_argument("iso")
    audit.add_argument("--resource", type=int, required=True)
    audit.add_argument("--csv", help="translation sheet to check for coverage")
    audit.add_argument("--strict", action="store_true",
                       help="exit non-zero if any displayed text message is "
                            "missing from the sheet (default: warn only)")
    audit.set_defaults(func=cmd_audit)
    budget = commands.add_parser(
        "budget", help="report a scene's font and byte room for translation")
    budget.add_argument("iso")
    budget.add_argument("--resource", type=int, action="append",
                        help="scene resource to report (repeatable)")
    budget.add_argument("--manifest",
                        help="<iso>.triace.csv, to survey every PK1 resource")
    budget.add_argument("--measure-reclaim", action="store_true",
                        help="also re-encode this archive's SLZ subresources "
                             "to measure the bytes --reclaim would free; slow")
    budget.add_argument("--reclaim-limit", type=int, default=0,
                        help="skip subresources larger than this when "
                             "measuring (0 = no limit)")
    budget.add_argument("--reference-iso",
                        help="pristine ROM for glyph fingerprints, once "
                             "resource 33 has itself been translated")
    budget.add_argument("--csv", help="also write the report as CSV")
    budget.set_defaults(func=cmd_budget)
    merge = commands.add_parser(
        "merge", help="combine sheets targeting one resource into one sheet")
    merge.add_argument("out", help="merged sheet to write")
    merge.add_argument("csv", nargs="+", help="sheets to merge")
    merge.set_defaults(func=cmd_merge)
    scenes = commands.add_parser(
        "scenes", help="list the scenes an event resource plays, in order")
    scenes.add_argument("iso")
    scenes.add_argument("--resource", type=int, required=True)
    scenes.add_argument("--lines", action="store_true",
                        help="list each message with its script position")
    scenes.set_defaults(func=cmd_scenes)
    sheet = commands.add_parser(
        "sheet", help="export a translator-facing sheet grouped by scene")
    sheet.add_argument("iso")
    sheet.add_argument("csv")
    sheet.add_argument("--resource", type=int)
    sheet.add_argument("--all", action="store_true",
                       help="write one sheet per resource into the "
                            "csv directory")
    sheet.add_argument("--manifest-list",
                       help="<iso>.triace.csv naming the resources, "
                            "required by --all")
    sheet.add_argument("--manifest",
                       help="dub manifest supplying jp_text and audio ids")
    sheet.add_argument("--en-names", default=EN_NAMES_DEFAULT,
                       help="tracked digest,character file for glyphs the "
                            "fingerprints cannot name")
    sheet.add_argument("--jp-iso", help="Japanese image to read original_jp from")
    sheet.add_argument("--jp-glyphs", default="vp2/text/jp-glyphs.csv",
                       help="glyph table holding the bitmaps")
    sheet.add_argument("--jp-names", default="data/glyph-names/jp.csv",
                       help="tracked digest,character file")
    sheet.add_argument("--manifest-scene", type=int,
                       help="voice scene the manifest describes, when its "
                            "English cannot identify one")
    sheet.set_defaults(func=cmd_sheet)
    preflight = commands.add_parser(
        "preflight", help="check translated rows and safe local-font capacity")
    preflight.add_argument("iso")
    preflight.add_argument("csv")
    preflight.set_defaults(func=cmd_preflight)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, KeyError, IndexError, csv.Error, struct.error) as exc:
        parser.exit(1, "error: %s\n" % exc)


if __name__ == "__main__":
    main()
