#!/usr/bin/env python3
"""Name the Japanese glyphs a VP2 scene font draws, and decode JP text with them."""
import argparse
import base64
import csv
import hashlib
import os
import struct
import sys


from .paths import TOOLS_DIR

HERE = os.fspath(TOOLS_DIR)

from . import vp2_cutscene_subtitles as subtitles
from . import vp2_dcms as dcms
from . import triace_ps2_unpack as triace

GLYPH_STRIDE = 16
GLYPH_ROWS = 28
GLYPH_WIDTH = GLYPH_STRIDE * 2
FIELDS = ["glyph", "character", "resources", "slots", "first_resource",
          "first_slot", "digest", "bitmap"]
NAME_FIELDS = ["digest", "character", "resources", "first_resource",
               "first_slot"]


def glyph_pixels(block):
    """Return ``GLYPH_ROWS`` rows of 4-bit intensities."""
    rows = []
    for y in range(GLYPH_ROWS):
        row = block[y * GLYPH_STRIDE:(y + 1) * GLYPH_STRIDE]
        line = []
        for byte in row:
            line.append(byte & 0xF)
            line.append(byte >> 4)
        rows.append(line)
    return rows


def resource_fonts(handle, table, total, resource):
    """Every local font an entry carries; a PK1 may hold more than one bank."""
    from . import container_archive
    from . import vp2_container_text as ct
    raw = dcms.read_entry(handle, table, total, resource)
    found, seen = [], set()

    def keep(expanded):
        layout = subtitles.font_layout(expanded)
        if not layout["glyph_count"]:
            return
        mark = (layout["font_start"], layout["glyph_count"], len(expanded))
        if mark in seen:
            return
        seen.add(mark)
        found.append((expanded, layout))

    try:
        offset, length, _ = subtitles.find_dcms(raw, resource)
        body = raw[offset:offset + length]
        keep(bytearray(subtitles.slz.decompress(body)
                       if body[:3] == b"SLZ" else body))
    except (ValueError, KeyError, IndexError, struct.error):
        pass
    try:
        for section in container_archive.pk1_container_sections(bytes(raw)):
            keep(section["blob"])
    except (ValueError, KeyError, IndexError, struct.error):
        pass
    if not found:
        try:
            blob = ct.unpack_container_entry(bytes(raw), resource)
        except (ValueError, KeyError, IndexError, struct.error):
            return []
        if blob.startswith(b"mcps2lib"):
            keep(blob)
    return found


def resource_font(handle, table, total, resource):
    """Return ``(expanded, layout)`` for a resource's local font, or ``None``."""
    found = resource_fonts(handle, table, total, resource)
    return found[0] if found else None


def collect(handle, table, total, resources):
    """Gather every distinct non-blank glyph bitmap in the image."""
    glyphs = {}
    for resource in resources:
        try:
            fonts = resource_fonts(handle, table, total, resource)
        except (ValueError, KeyError, IndexError, struct.error):
            continue
        for expanded, layout in fonts:
            for slot in range(layout["glyph_count"]):
                block = subtitles.glyph_bitmap(expanded, layout, slot)
                if not any(block):
                    continue
                digest = hashlib.sha1(block).hexdigest()
                entry = glyphs.get(digest)
                if entry is None:
                    glyphs[digest] = {
                        "digest": digest, "block": bytes(block),
                        "resources": {resource}, "slots": 1,
                        "first_resource": resource, "first_slot": slot,
                    }
                else:
                    entry["resources"].add(resource)
                    entry["slots"] += 1
    return glyphs


def read_table(path):
    """Load a glyph table, keyed by digest."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["block"] = base64.b64decode(row["bitmap"])
    return rows


def read_names(path):
    """Load ``digest -> character`` from the tracked names file."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {row["digest"]: row["character"]
                for row in csv.DictReader(handle)
                if row.get("character", "").strip()}


def cmd_names(args):
    """Write the named glyphs out to the tracked names file."""
    rows = [row for row in read_table(args.csv)
            if row.get("character", "").strip()]
    rows.sort(key=lambda row: row["digest"])
    parent = os.path.dirname(os.path.abspath(args.names))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.names, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=NAME_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in NAME_FIELDS})
    print("%d named glyphs -> %s" % (len(rows), args.names))


def cmd_index(args):
    resources = []
    with open(args.manifest, newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["type"] in ("pk1", "slz", "zls"):
                resources.append(int(row["index"]))
    resources.sort()
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        glyphs = collect(handle, table, total, resources)
    # Most-used first: a glyph in four hundred resources is a kana worth
    # naming before one that appears once.
    order = sorted(glyphs.values(),
                   key=lambda item: (-len(item["resources"]), -item["slots"],
                                     item["first_resource"], item["first_slot"]))
    existing = dict(read_names(args.names))
    if args.keep and os.path.exists(args.csv):
        for row in read_table(args.csv):
            if row.get("character", "").strip():
                existing.setdefault(row["digest"], row["character"])
    parent = os.path.dirname(os.path.abspath(args.csv))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.csv, "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for number, item in enumerate(order):
            writer.writerow({
                "glyph": number,
                "character": existing.get(item["digest"], ""),
                "resources": len(item["resources"]),
                "slots": item["slots"],
                "first_resource": item["first_resource"],
                "first_slot": item["first_slot"],
                "digest": item["digest"],
                "bitmap": base64.b64encode(item["block"]).decode("ascii"),
            })
    named = sum(1 for value in existing.values() if value)
    print("%d distinct glyphs -> %s%s" %
          (len(order), args.csv,
           " (%d names carried over)" % named if named else ""))


def render_sheets(rows, outdir, columns, per_sheet, scale, prefix,
                  caption=lambda row: row["glyph"]):
    """Write ``rows`` as indexed contact sheets, returning the paths."""
    from PIL import Image, ImageDraw
    os.makedirs(outdir, exist_ok=True)
    pad, label = 6, 12
    cell_w = GLYPH_WIDTH * scale + pad * 2
    cell_h = GLYPH_ROWS * scale + pad * 2 + label
    written, paths = 0, []
    for start in range(0, len(rows), per_sheet):
        chunk = rows[start:start + per_sheet]
        height = ((len(chunk) + columns - 1) // columns) * cell_h
        image = Image.new("L", (columns * cell_w, height), 255)
        draw = ImageDraw.Draw(image)
        for position, row in enumerate(chunk):
            cx = (position % columns) * cell_w
            cy = (position // columns) * cell_h
            draw.text((cx + 2, cy + 1), caption(row), fill=140)
            pixels = glyph_pixels(row["block"])
            for y, line in enumerate(pixels):
                for x, value in enumerate(line):
                    if not value:
                        continue
                    shade = 255 - int(value * 255 / 15)
                    draw.rectangle(
                        [cx + pad + x * scale, cy + pad + label + y * scale,
                         cx + pad + (x + 1) * scale - 1,
                         cy + pad + label + (y + 1) * scale - 1], fill=shade)
        path = os.path.join(outdir, "%s-%03d.png" % (prefix, written))
        image.save(path)
        paths.append(path)
        written += 1
    return paths


def cmd_sheets(args):
    rows = read_table(args.csv)
    if args.unnamed:
        rows = [row for row in rows if not row.get("character", "").strip()]
    if not rows:
        print("nothing to render")
        return
    paths = render_sheets(rows, args.outdir, args.columns,
                          args.columns * args.rows, args.scale, "jp-glyphs")
    per = args.columns * args.rows
    for index, path in enumerate(paths):
        chunk = rows[index * per:(index + 1) * per]
        print("%s: glyphs %s-%s" % (path, chunk[0]["glyph"], chunk[-1]["glyph"]))
    print("%d sheets covering %d glyphs" % (len(paths), len(rows)))


def resource_gaps(handle, table, total, resource, table_by_digest):
    """Return ``{glyph number: times used}`` for what a resource cannot draw."""
    found = resource_font(handle, table, total, resource)
    if found is None:
        return {}
    expanded, layout = found
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    missing = {}
    for _, _, offset in pointers:
        record = bytes(expanded[metadata["text_start"] + offset:
                                metadata["text_start"] + next_offset[offset]])
        for _, _, tokens in subtitles.parse_record(record, metadata):
            for token in tokens:
                slot = subtitles.token_slot(
                    token, metadata["glyph_base"], metadata["glyph_count"])
                if slot is None:
                    continue
                block = subtitles.glyph_bitmap(expanded, layout, slot)
                if not any(block):
                    continue
                row = table_by_digest.get(hashlib.sha1(block).hexdigest())
                if row is not None and not row.get("character", "").strip():
                    number = int(row["glyph"])
                    missing[number] = missing.get(number, 0) + 1
    return missing


def cmd_gaps(args):
    """Render only the glyphs the named resources still cannot draw."""
    rows = read_table(args.csv)
    by_digest = {row["digest"]: row for row in rows}
    by_number = {int(row["glyph"]): row for row in rows}
    for row in rows:
        if not row.get("character", "").strip():
            continue
    names = read_names(args.names)
    for digest, character in names.items():
        if digest in by_digest and not by_digest[digest].get("character", "").strip():
            by_digest[digest]["character"] = character
    counts = {}
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        for resource in args.resource:
            found = resource_gaps(handle, table, total, resource, by_digest)
            for number, times in found.items():
                counts[number] = counts.get(number, 0) + times
    if not counts:
        print("resources %s need no further glyphs" %
              ", ".join(str(r) for r in args.resource))
        return
    order = sorted(counts, key=lambda number: (-counts[number], number))
    selected = [by_number[number] for number in order]
    paths = render_sheets(selected, args.outdir, args.columns,
                          args.columns * args.rows, args.scale, "jp-gaps",
                          caption=lambda row: "%s x%d" % (
                              row["glyph"], counts[int(row["glyph"])]))
    print("%d unnamed glyphs across resources %s" %
          (len(order), ", ".join(str(r) for r in args.resource)))
    for path in paths:
        print("  %s" % path)
    print("  most used: %s" % ", ".join(
        "%d(x%d)" % (number, counts[number]) for number in order[:12]))


UNKNOWN = "\u3013"
VOICE_HEADER = bytes((0x9E, 0x80))


def decode_resource(handle, table, total, resource, names):
    """Return ``[(index, message id, voice key, text)]`` for a JP resource."""
    found = resource_font(handle, table, total, resource)
    if found is None:
        return {}, 0, 0
    expanded, layout = found
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    alphabet = {}
    for slot in range(layout["glyph_count"]):
        block = subtitles.glyph_bitmap(expanded, layout, slot)
        if not any(block):
            alphabet[slot] = " "
            continue
        digest = hashlib.sha1(block).hexdigest()
        if digest in names:
            alphabet[slot] = names[digest]
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    decoded, complete = [], 0
    for index, message_id, offset in pointers:
        record = bytes(expanded[metadata["text_start"] + offset:
                                metadata["text_start"] + next_offset[offset]])
        voice = (None, None)
        if len(record) >= 6 and record[:2] == VOICE_HEADER:
            voice = (struct.unpack_from("<H", record, 2)[0],
                     struct.unpack_from("<H", record, 4)[0])
        runs, missing = [], 0
        for offset, _, tokens in subtitles.parse_record(record, metadata):
            text = []
            for token in tokens:
                slot = subtitles.token_slot(
                    token, metadata["glyph_base"], metadata["glyph_count"])
                if slot is None:
                    continue
                if slot in alphabet:
                    text.append(alphabet[slot])
                else:
                    text.append(UNKNOWN)
                    missing += 1
            visible = "".join(text).strip()
            if visible:
                runs.append((offset, visible))
        best = (" %s " % subtitles.FRAGMENT_MARKER).join(
            visible for _, visible in sorted(runs))
        decoded.append((index, message_id, voice, best))
        if best and not missing:
            complete += 1
    return decoded, len(alphabet), complete


def load_glyph_names(table_path, names_path=None):
    """Merge the working table's characters with the tracked names file."""
    names = dict(read_names(names_path))
    if table_path and os.path.exists(table_path):
        for row in read_table(table_path):
            character = row.get("character", "").strip()
            if character:
                names.setdefault(row["digest"], character)
    return names


def cmd_decode(args):
    names = load_glyph_names(args.csv, args.names)
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        texts, slots, complete = decode_resource(
            handle, table, total, args.resource, names)
    shown = [item for item in texts if item[3]]
    for number, (_, message_id, _, text) in enumerate(shown):
        if args.limit and number >= args.limit:
            break
        print("  %5d %s" % (message_id, text))
    print("resource #%d: %d slots named | %d messages, %d fully decoded" %
          (args.resource, slots, len(shown), complete))


def cmd_context(args):
    """Show a resource's own Japanese around every use of an unnamed glyph."""
    names = load_glyph_names(args.csv, args.names)
    rows = read_table(args.csv)
    by_digest = {row["digest"]: row for row in rows}
    with open(args.iso, "rb") as handle:
        _, total, table = triace.load_table(handle)
        found = resource_font(handle, table, total, args.resource)
    if found is None:
        print("resource #%d carries no local font" % args.resource)
        return
    expanded, layout = found
    metadata = {
        "table_start": struct.unpack_from("<I", expanded, 0x24)[0],
        "text_start": struct.unpack_from("<I", expanded, 0x28)[0],
        "text_end": struct.unpack_from("<I", expanded, 0x2C)[0],
        "glyph_base": layout["glyph_base"],
        "glyph_count": layout["glyph_count"],
    }
    alphabet = {}
    for slot in range(layout["glyph_count"]):
        block = subtitles.glyph_bitmap(expanded, layout, slot)
        if not any(block):
            alphabet[slot] = " "
            continue
        digest = hashlib.sha1(block).hexdigest()
        if digest in names:
            alphabet[slot] = names[digest]
        elif digest in by_digest:
            alphabet[slot] = "[%s]" % by_digest[digest]["glyph"]
    wanted = {"[%d]" % number for number in args.glyph}
    pointers, next_offset = subtitles.message_pointers(expanded, metadata)
    shown = 0
    for _index, message_id, offset in pointers:
        if shown >= args.limit:
            break
        record = bytes(expanded[metadata["text_start"] + offset:
                                metadata["text_start"] + next_offset[offset]])
        pieces = []
        for _, _, part in subtitles.split_nonempty(record):
            try:
                tokens = subtitles.byte_tokens(part)
            except ValueError:
                continue
            for token in tokens:
                slot = subtitles.token_slot(
                    token, metadata["glyph_base"], metadata["glyph_count"])
                if slot is None:
                    continue
                pieces.append(alphabet.get(slot) or UNKNOWN)
        text = "".join(pieces)
        if not any(mark in text for mark in wanted):
            continue
        print("  %5d  %s" % (message_id, text[:args.width]))
        shown += 1
    if not shown:
        print("no record in resource #%d uses %s"
              % (args.resource, ", ".join(str(n) for n in args.glyph)))


def cmd_name(args):
    """Set character on glyphs by number."""
    rows = read_table(args.csv)
    by_number = {int(row["glyph"]): row for row in rows}
    assignments = []
    if args.name:
        for spec in args.name:
            if "=" in spec:
                number, character = spec.split("=", 1)
            else:
                parts = spec.split(None, 1)
                if len(parts) != 2:
                    raise ValueError("--name expects NUMBER=CHAR: %r" % spec)
                number, character = parts[0], parts[1]
            assignments.append((int(number), character))
    if not assignments:
        print("nothing to do (pass --name 1317=あ)", file=sys.stderr)
        return
    updated, missing = 0, []
    for number, character in assignments:
        row = by_number.get(number)
        if row is None:
            missing.append(number)
            continue
        row["character"] = character
        updated += 1
    rows.sort(key=lambda row: int(row["glyph"]))
    with open(args.csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})
    if missing:
        print("not in working table: %s" % ", ".join(str(n) for n in missing),
              file=sys.stderr)
    print("named %d glyph(s) in %s" % (updated, args.csv))
    if args.sync_names:
        cmd_names(argparse.Namespace(csv=args.csv, names=args.sync_names))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser(
        "index", help="collect every distinct Japanese glyph in the image")
    index.add_argument("iso")
    index.add_argument("manifest", help="<iso>.triace.csv naming the resources")
    index.add_argument("csv")
    index.add_argument("--keep", action="store_true",
                       help="carry over characters already named in the table")
    index.add_argument("--names", help="tracked digest,character file to seed from")
    index.set_defaults(func=cmd_index)

    names = commands.add_parser(
        "names", help="write the named glyphs to the tracked names file")
    names.add_argument("csv", help="the working table holding the bitmaps")
    names.add_argument("names")
    names.set_defaults(func=cmd_names)

    sheets = commands.add_parser(
        "sheets", help="render the glyphs as indexed contact sheets")
    sheets.add_argument("csv")
    sheets.add_argument("outdir")
    sheets.add_argument("--columns", type=int, default=16)
    sheets.add_argument("--rows", type=int, default=8)
    sheets.add_argument("--scale", type=int, default=3)
    sheets.add_argument("--unnamed", action="store_true",
                        help="render only the glyphs still without a character")
    sheets.set_defaults(func=cmd_sheets)

    gaps = commands.add_parser(
        "gaps", help="render only what the named resources still cannot draw")
    gaps.add_argument("iso")
    gaps.add_argument("csv")
    gaps.add_argument("outdir")
    gaps.add_argument("--resource", type=int, action="append", required=True,
                      help="repeatable")
    gaps.add_argument("--names", help="tracked digest,character file")
    gaps.add_argument("--columns", type=int, default=12)
    gaps.add_argument("--rows", type=int, default=8)
    gaps.add_argument("--scale", type=int, default=5)
    gaps.set_defaults(func=cmd_gaps)

    decode = commands.add_parser(
        "decode", help="decode a resource's Japanese with the named glyphs")
    decode.add_argument("iso")
    decode.add_argument("csv")
    decode.add_argument("--resource", type=int, required=True)
    decode.add_argument("--limit", type=int, default=20)
    decode.add_argument("--names", help="tracked digest,character file")
    decode.set_defaults(func=cmd_decode)

    context = commands.add_parser(
        "context", help="show a resource's text around an unnamed glyph")
    context.add_argument("iso")
    context.add_argument("csv")
    context.add_argument("--resource", type=int, required=True)
    context.add_argument("--glyph", type=int, action="append", required=True,
                         help="glyph number to locate; repeatable")
    context.add_argument("--names", help="tracked digest,character file")
    context.add_argument("--limit", type=int, default=10)
    context.add_argument("--width", type=int, default=70)
    context.set_defaults(func=cmd_context)

    name = commands.add_parser(
        "name", help="set character on glyphs by number; repeatable "
                     "(--name 1317=あ). Faster than editing the CSV by hand "
                     "when reading contact sheets.")
    name.add_argument("csv", help="the working table to update in place")
    name.add_argument("--name", action="append", metavar="NUMBER=CHAR",
                      help="repeatable; one mapping per flag")
    name.add_argument("--sync-names", metavar="NAMES",
                      help="after updating the working table, write the "
                           "tracked digest,character file from it")
    name.set_defaults(func=cmd_name)

    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, KeyError, IndexError, csv.Error,
            struct.error) as exc:
        parser.exit(1, "error: %s\n" % exc)


if __name__ == "__main__":
    main()
