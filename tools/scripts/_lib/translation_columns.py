#!/usr/bin/env python3
r""""""
import csv
import io
import os


PRIMARY_KEYS = ("resource", "message_id")
ANCHOR_CHAINS = {
    "original_en": ("original_jp", "english_en", "translated"),
    "original_jp": ("original_en", "english_en", "translated"),
}


def open_csv(path, mode="r"):
    return io.open(path, mode, encoding="utf-8-sig", newline="")


def load_reference(path):
    if not os.path.isfile(path):
        return None
    with open_csv(path) as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if not {"resource", "message_id", "original_en", "original_jp"} <= set(fields):
            return None
        lookup = {}
        for row in reader:
            key = (row.get("resource", ""), row.get("message_id", ""))
            lookup[key] = {
                "original_en": row.get("original_en", "") or "",
                "original_jp": row.get("original_jp", "") or "",
                "speaker": row.get("speaker", "") or "",
            }
        return lookup


def read_table(path):
    with open_csv(path) as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fields, rows


def write_table(path, fields, rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(buf.getvalue())


def has_target_columns(fields, targets=("original_en", "original_jp")):
    return any(name in fields for name in targets)


def insert_position(fields, column):
    for anchor in ANCHOR_CHAINS.get(column, ()):
        if anchor in fields:
            return fields.index(anchor)
    return len(fields)


def speaker_position(fields):
    if "message_id" in fields:
        return fields.index("message_id") + 1
    return 0


def apply_column(path, fields, rows, lookup, column, write):
    if column in fields:
        if fill_value(rows, column, lookup) == 0:
            return None
        if write:
            write_table(path, fields, rows)
        return "filled"
    if column == "speaker":
        position = speaker_position(fields)
    else:
        position = insert_position(fields, column)
    fields[:] = fields[:position] + [column] + fields[position:]
    fill_value(rows, column, lookup)
    if write:
        write_table(path, fields, rows)
    return "added"


def row_key(row):
    return (row.get("resource", ""), row.get("message_id", ""))


def fill_value(rows, column, lookup):
    changed = 0
    for row in rows:
        match = lookup.get(row_key(row))
        if not match:
            row.setdefault(column, "")
            continue
        value = match.get(column, "")
        if row.get(column, "") != value:
            row[column] = value
            changed += 1
        elif column not in row:
            row[column] = value
    return changed


def iter_translation_tables(translations_root):
    if not os.path.isdir(translations_root):
        return
    for lang_dir in sorted(os.listdir(translations_root)):
        lang_path = os.path.join(translations_root, lang_dir)
        if lang_dir.startswith("_") or not os.path.isdir(lang_path):
            continue
        for root, _dirs, files in os.walk(lang_path):
            for name in sorted(files):
                if not name.endswith(".csv"):
                    continue
                if name == "build-profile.csv":
                    continue
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, lang_path)
                yield lang_dir, rel_path.replace(os.sep, "/"), abs_path


def reference_path_for(translations_root, lang, rel_path):
    here = os.path.dirname(os.path.abspath(translations_root))
    return os.path.join(here, "workspace", "reference", rel_path.replace("/", os.sep))


def script_relative_default_translations():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "translations"))
