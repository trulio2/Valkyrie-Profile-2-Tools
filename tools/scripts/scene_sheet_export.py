"""Scene-sheet export, voice mapping, and Japanese alignment."""

import csv
import hashlib
import os
import struct

from . import triace_ps2_unpack as triace
from . import vp2_dcms as dcms
from . import vp2_cutscene_subtitles as subtitles

SPEAKER_AFTER_DISPLAY = 12


def _workflow_helper(name):
    from . import vp2_cutscene_workflow as workflow
    return getattr(workflow, name)


def derive_scenes(*args, **kwargs):
    return _workflow_helper("derive_scenes")(*args, **kwargs)


def ecs_speakers(*args, **kwargs):
    return _workflow_helper("ecs_speakers")(*args, **kwargs)


def scene_details(*args, **kwargs):
    return _workflow_helper("scene_details")(*args, **kwargs)


def export_run_text(rendered):
    """Clean a run for translators without hiding visible blank rows."""
    visible = subtitles.TAG.sub("", rendered)
    lines = [line.strip() for line in visible.splitlines()]
    content = [index for index, line in enumerate(lines) if line]
    if not content:
        return ""
    first, last = content[0], content[-1]
    body = "\n".join(lines[first:last + 1])
    leading = "\n" if first else ""
    trailing = "\n" if visible.endswith("\n") else ""
    return leading + body + trailing


def record_text(record, metadata, alphabet):
    """Read every run of text in a record, in the order it stores them."""
    parts = []
    for _, _, tokens in subtitles.parse_record(record, metadata):
        rendered, _, _ = subtitles.render_tokens(tokens, metadata, alphabet)
        visible = export_run_text(rendered)
        if visible:
            parts.append(visible)
    joined = ""
    for index, part in enumerate(parts):
        if index:
            joined += (" %s" % subtitles.FRAGMENT_MARKER)
            joined += "" if part.startswith("\n") else " "
        joined += part
    return joined

def read_glyph_names(path):
    """Load ``digest -> character`` from a tracked names file."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {row["digest"]: row["character"]
                for row in csv.DictReader(handle)
                if row.get("character", "").strip()}

def name_unfingerprinted(expanded, layout, alphabet, names):
    """Fill slots the fingerprints left unnamed, returning how many."""
    if not names:
        return 0
    import hashlib
    filled = 0
    for slot in range(layout["glyph_count"]):
        if alphabet.get(slot) is not None:
            continue
        digest = hashlib.sha1(
            subtitles.glyph_bitmap(expanded, layout, slot)).hexdigest()
        character = names.get(digest)
        if character:
            alphabet[slot] = character
            filled += 1
    return filled

SHEET_FIELDS = [
    "scene", "scene_line", "resource", "message_id", "script_offset",
    "voice_scene", "voice_slot", "audio_id", "speaker",
    "original_en", "original_jp", "translated", "details",
]

VOICE_HEADER = bytes((0x9E, 0x80))

def record_voice(record):
    """Return ``(scene id, slot)`` for a voiced record, else ``(None, None)``."""
    if len(record) < 6 or record[:2] != VOICE_HEADER:
        return None, None
    return (struct.unpack_from("<H", record, 2)[0],
            struct.unpack_from("<H", record, 4)[0])

def _voice_bank(message_id, voice_of):
    return voice_of.get(message_id, (None, None))[0]


def _first_offset(lines):
    offsets = [line[1] for line in lines if line[1] is not None]
    return min(offsets) if offsets else None


def order_scenes(scenes, voice_of):
    voiced, unvoiced = {}, []
    for scene in scenes:
        banks = {_voice_bank(line[0], voice_of) for line in scene["lines"]}
        banks.discard(None)
        if not banks:
            unvoiced.append(scene)
            continue
        stray = [line for line in scene["lines"]
                 if _voice_bank(line[0], voice_of) is None]
        if stray:
            unvoiced.append({"first_offset": _first_offset(stray),
                             "lines": stray})
        for line in scene["lines"]:
            bank = _voice_bank(line[0], voice_of)
            if bank is not None:
                voiced.setdefault(bank, []).append(line)
    result = []
    for lines in voiced.values():
        lines.sort(key=lambda line: voice_of[line[0]][1])
        result.append({"first_offset": _first_offset(lines), "lines": lines})
    result.extend(unvoiced)
    result.sort(key=lambda scene: (scene["first_offset"] is None,
                                   scene["first_offset"] or 0))
    return result


def manifest_voice_scene(manifest, english_by_scene):
    """Decide which voice scene a dub manifest describes."""
    wanted = {subtitles.normalized(row.get("en_text", ""))
              for row in manifest if row.get("en_text", "").strip()}
    scored = []
    for voice_scene, texts in english_by_scene.items():
        lines = [subtitles.normalized(text) for text in texts if text.strip()]
        if not lines:
            continue
        hits = sum(1 for line in lines if line in wanted)
        scored.append((hits / len(lines), hits, voice_scene))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.7:
        raise ValueError(
            "the manifest's English does not match any scene in this resource; "
            "pass --manifest-scene to say which voice scene it describes")
    if len(scored) > 1 and scored[1][0] >= 0.7:
        raise ValueError("the manifest matches more than one scene: %s" %
                         ", ".join(str(item[2]) for item in scored if item[0] >= 0.7))
    return scored[0][2]

def sheet_rows(source, table, total, resource, manifest_path=None,
               manifest_scene=None, japanese_text=None, english_names=None):
    """Build one resource's sheet rows.  Returns ``(rows, scenes, matched)``."""
    from . import vp2_title_face as title_face
    raw = dcms.read_entry(source, table, total, resource)
    iso_handle = subtitles.FileIso(source, table, total)
    _, _, _, expanded, layout, alphabet = subtitles.iso_alphabet(
        iso_handle, resource)
    subtitles.discover_generated_glyphs(
        expanded, layout, alphabet, iso_handle,
        recognize_accent_donors=False)
    name_unfingerprinted(expanded, layout, alphabet, english_names or {})
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": subtitles.text_region_end(expanded),
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    records, english, ordered = {}, {}, []
    for _, message_id, offset in pointers:
        record = bytes(expanded[metadata["text_start"] + offset:
                                metadata["text_start"] + next_offset[offset]])
        records[message_id] = record
        ordered.append((message_id, record_voice(record)))
        english[message_id] = record_text(record, metadata, alphabet)
    titles = [message for crib_resource, message in title_face.CHAPTER_RECORDS
              if crib_resource == resource]
    scenes = derive_scenes(raw, set(records), titles)
    if not scenes and records:
        scenes = [{"first_offset": 0,
                   "lines": [(message_id, 0, 0)
                             for message_id in sorted(records)]}]
    scenes = order_scenes(scenes, dict(ordered))

    displayed = {message_id for scene in scenes for message_id, _, _ in scene["lines"]}
    leftover = sorted(set(records) - displayed - set(titles))
    if leftover:
        scenes = scenes + [{"first_offset": None,
                            "lines": [(message_id, None, 0)
                                      for message_id in leftover]}]

    japanese, audio, matched_scene = {}, {}, None
    if manifest_path:
        with open(manifest_path, newline="", encoding="utf-8-sig") as handle:
            manifest = list(csv.DictReader(handle))
        english_by_scene = {}
        for message_id, record in records.items():
            voice_scene, _ = record_voice(record)
            if voice_scene is not None:
                english_by_scene.setdefault(voice_scene, []).append(
                    english.get(message_id, ""))
        matched_scene = (manifest_scene if manifest_scene is not None
                         else manifest_voice_scene(manifest, english_by_scene))
        for row in manifest:
            slot = int(row["sub"])
            japanese[slot] = row.get("jp_text", "")
            audio[slot] = row.get("id", "")

    instructions = dict(ecs_speakers(raw))
    speaker_of = {}
    for scene in scenes:
        for message_id, offset, _ in scene["lines"]:
            if offset is None:
                continue
            name_id = instructions.get(offset + SPEAKER_AFTER_DISPLAY)
            if name_id in records:
                speaker_of[message_id] = english.get(name_id, "").strip()

    japanese_by_message = {}
    if japanese_text:
        japanese_by_message, _ = align_japanese(ordered, japanese_text)

    rows = []
    for index, scene in enumerate(scenes, 1):
        for position, (message_id, offset, times) in enumerate(scene["lines"], 1):
            voice_scene, voice_slot = record_voice(records[message_id])
            rows.append({
                "scene": index,
                "scene_line": position,
                "resource": resource,
                "message_id": message_id,
                "script_offset": "" if offset is None else offset,
                "voice_scene": "" if voice_scene is None else voice_scene,
                "voice_slot": "" if voice_slot is None else voice_slot,
                "audio_id": (audio.get(voice_slot, "")
                             if voice_scene == matched_scene else ""),
                "speaker": speaker_of.get(message_id, ""),
                "original_en": english.get(message_id, ""),
                "original_jp": japanese_by_message.get(
                    message_id,
                    japanese.get(voice_slot, "")
                    if voice_scene == matched_scene else ""),
                "translated": "",
                "details": "; ".join(filter(None, [
                    scene_details(times),
                    "" if offset is not None else
                    "not displayed by the event script"])),
            })
    return rows, scenes, matched_scene

def write_sheet(path, rows):
    """Write a sheet, carrying forward any translation the file already holds."""
    existing = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if (row.get("translated") or "").strip():
                    # a sheet read back from disk holds every field as text,
                    # while a freshly built row holds the id as an int
                    existing[str(row.get("message_id"))] = row["translated"]
    kept, mismatched = 0, []
    for row in rows:
        previous = existing.get(str(row["message_id"]))
        if not previous:
            continue
        if (previous.count(subtitles.FRAGMENT_MARKER)
                != row["original_en"].count(subtitles.FRAGMENT_MARKER)):
            mismatched.append(str(row["message_id"]))
            continue
        row["translated"] = previous
        kept += 1
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return kept, mismatched

def japanese_for(jp_iso, glyph_table, names_path, resources):
    """Decode the Japanese for the given resources out of the JP image."""
    from . import vp2_jp_glyphs as jp_glyphs
    names = jp_glyphs.load_glyph_names(glyph_table, names_path)
    decoded = {}
    with open(jp_iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        for resource in resources:
            try:
                records, _, _ = jp_glyphs.decode_resource(
                    handle, table, total, resource, names)
            except (ValueError, KeyError, IndexError, struct.error):
                continue
            if records:
                decoded[resource] = records
    return decoded

def align_japanese(usa_records, jp_records):
    """Map USA message id to Japanese text, refusing when nothing lines up."""
    if len(usa_records) == len(jp_records):
        agree = all(usa_voice == jp_voice
                    for (_, usa_voice), (_, _, jp_voice, _)
                    in zip(usa_records, jp_records)
                    if usa_voice[0] is not None or jp_voice[0] is not None)
        if agree:
            return ({message_id: text
                     for (message_id, _), (_, _, _, text)
                     in zip(usa_records, jp_records) if text}, "record order")
    by_voice = {voice: text for _, _, voice, text in jp_records
                if voice[0] is not None and text}
    return ({message_id: by_voice[voice]
             for message_id, voice in usa_records
             if voice in by_voice}, "voice slot")

def cmd_sheet(args):
    """Export a translator-facing sheet: one row per line, grouped by scene."""
    if args.all:
        cmd_sheet_all(args)
        return
    if args.resource is None:
        raise ValueError("name a --resource, or pass --all with --manifest-list")
    japanese = {}
    if args.jp_iso:
        japanese = japanese_for(args.jp_iso, args.jp_glyphs, args.jp_names,
                                [args.resource]).get(args.resource, {})
    with open(args.iso, "rb") as source:
        _, total, table = triace.load_table(source)
        rows, scenes, matched = sheet_rows(
            source, table, total, args.resource, args.manifest,
            args.manifest_scene, japanese,
            read_glyph_names(args.en_names))
    kept, mismatched = write_sheet(args.csv, rows)
    voiced = sum(1 for row in rows if row["voice_slot"] != "")
    joined = sum(1 for row in rows if row["original_jp"].strip())
    print("resource #%d: %d scenes, %d lines (%d voiced) -> %s" %
          (args.resource, len(scenes), len(rows), voiced, args.csv))
    if matched is not None:
        print("  manifest matched voice scene %d: %d lines carry Japanese" %
              (matched, joined))
    if kept:
        print("  carried %d existing translation(s) forward" % kept)
    if mismatched:
        print("  %d row(s) NOT carried -- the record's run count changed: %s"
              % (len(mismatched), ", ".join(mismatched[:12])))
    if not args.manifest:
        print("  original_jp and audio_id left blank: pass --manifest for a "
              "scene whose voice bank is known")

def cmd_sheet_all(args):
    """Export a sheet for every resource that holds messages."""
    if not args.manifest_list:
        raise ValueError("--all needs --manifest-list <iso>.triace.csv")
    resources = []
    with open(args.manifest_list, newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["type"] == "pk1":
                resources.append(int(row["index"]))
    resources.sort()
    english_names = read_glyph_names(args.en_names)
    japanese = {}
    if args.jp_iso:
        japanese = japanese_for(args.jp_iso, args.jp_glyphs, args.jp_names,
                                resources)
        print("decoded Japanese for %d resources" % len(japanese))
    written = skipped = lines = scene_count = voiced = japanese_lines = 0
    carried = 0
    dropped = {}
    failures = {}
    with open(args.iso, "rb") as source:
        _, total, table = triace.load_table(source)
        for resource in resources:
            try:
                rows, scenes, _ = sheet_rows(
                    source, table, total, resource,
                    japanese_text=japanese.get(resource),
                    english_names=english_names)
            except (ValueError, KeyError, IndexError, struct.error) as exc:
                reason = str(exc)[:58]
                failures[reason] = failures.get(reason, 0) + 1
                skipped += 1
                continue
            if not rows:
                skipped += 1
                continue
            kept, mismatched = write_sheet(os.path.join(
                args.csv, "resource-%04d-scenes.csv" % resource), rows)
            carried += kept
            if mismatched:
                dropped[resource] = mismatched
            written += 1
            lines += len(rows)
            scene_count += len(scenes)
            voiced += sum(1 for row in rows if row["voice_slot"] != "")
            japanese_lines += sum(1 for row in rows
                                  if row["original_jp"].strip())
    print("wrote %d sheets, %d scenes, %d lines (%d voiced) -> %s" %
          (written, scene_count, lines, voiced, args.csv))
    print("carried %d existing translation(s) forward" % carried)
    if dropped:
        print("NOT carried -- these records changed run count, so their "
              "<PART> structure no longer lines up:")
        for resource, ids in sorted(dropped.items()):
            print("   resource #%d: %s" % (resource, ", ".join(ids[:12])))
    print("skipped %d resources with no readable text" % skipped)
    if japanese:
        print("%d lines carry Japanese" % japanese_lines)
    for reason, count in sorted(failures.items(), key=lambda kv: -kv[1])[:4]:
        print("   %4d x %s" % (count, reason))
