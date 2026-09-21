# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""What one manifest row read and wrote, kept between builds."""

import contextlib
import csv
import hashlib
import io
import json
import os
import struct

from .paths import CACHE_ROOT, DATA_DIR, PROJECT_ROOT

FORMAT = 1
MAGIC = b"VP2ROW\0"
DEFAULT_STORE = os.path.join(os.fspath(CACHE_ROOT), "rows")
DEFAULT_LIMIT = 2 << 30


def store_limit():
    try:
        return int(os.environ.get("VP2_ROW_CACHE_BYTES", DEFAULT_LIMIT))
    except ValueError:
        return DEFAULT_LIMIT


def resolve_store():
    """Where rows are kept, or an empty string when the cache is off."""
    setting = os.environ.get("VP2_ROW_CACHE")
    if setting == "0" or not store_limit():
        return ""
    return setting or DEFAULT_STORE


class Journal:
    """The entries one row touched, in the order it first touched them."""

    __slots__ = ("reads", "writes")

    def __init__(self):
        self.reads = {}
        self.writes = []

    def read(self, resource, data):
        if resource not in self.reads:
            self.reads[resource] = hashlib.sha256(bytes(data)).hexdigest()

    def wrote(self, resource, data):
        self.writes.append((resource, bytes(data)))


def _file_digest(digest, path_name):
    with open(path_name, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)


def _tree_digest(digest, root, suffix=None, skip=()):
    if not os.path.isdir(root):
        return
    for base, directories, names in os.walk(root):
        directories[:] = sorted(item for item in directories
                                if item not in skip and item != "__pycache__")
        for name in sorted(names):
            if suffix and not name.endswith(suffix):
                continue
            item = os.path.join(base, name)
            digest.update(
                os.path.relpath(item, root).replace("\\", "/").encode("utf-8"))
            digest.update(b"\0")
            _file_digest(digest, item)


def _stat_digest(digest, path_name):
    try:
        stat = os.stat(path_name)
    except OSError:
        digest.update(b"absent\0")
        return
    digest.update(("%d\0%d\0" % (stat.st_size, stat.st_mtime_ns))
                  .encode("ascii"))


def stamp_path(digest, path_name):
    """A file's name, size and mtime, for an input too big to read."""
    digest.update(os.path.basename(path_name).encode("utf-8"))
    _stat_digest(digest, path_name)


def stamp_tree(digest, root):
    if not os.path.isdir(root):
        digest.update(b"absent\0")
        return
    for base, directories, names in os.walk(root):
        directories[:] = sorted(item for item in directories
                                if item != "__pycache__")
        for name in sorted(names):
            item = os.path.join(base, name)
            digest.update(
                os.path.relpath(item, root).replace("\\", "/").encode("utf-8"))
            _stat_digest(digest, item)


def fingerprint(reference_iso, glyph_pool=None):
    digest = hashlib.sha256()
    digest.update(("row-cache-v%d\0" % FORMAT).encode("ascii"))
    _tree_digest(digest, os.path.join(os.fspath(PROJECT_ROOT), "tools"),
                 suffix=".py")
    _tree_digest(digest, os.fspath(DATA_DIR), skip={"slz-cache"})
    _tree_digest(digest, os.path.join(os.fspath(CACHE_ROOT), "font-layout"))
    _stat_digest(digest, os.fspath(reference_iso))
    if glyph_pool:
        _file_digest(digest, os.fspath(glyph_pool))
    return digest.hexdigest()


def inputs_digest(row, primary_lookup=None):
    """The row's own input: its sheet resolved, its images, or its word."""
    digest = hashlib.sha256()
    kind = row.get("kind") or ""
    sheet = row.get("sheet") or ""
    digest.update(("%s\0%s\0%s\0%s\0%s\0%s\0%s\0" % (
        kind, row.get("resource") or "", row.get("flags") or "",
        row.get("verify") or "", row.get("subresource") or "",
        row.get("chapter_title") or "",
        row.get("chapter_title_message") or "")).encode("utf-8"))
    if kind == "image":
        from .fis_images import layout_path
        digest.update(b"images\0")
        _tree_digest(digest, sheet)
        layout = layout_path(sheet)
        if os.path.isfile(layout):
            digest.update(b"layout\0")
            _file_digest(digest, layout)
        return digest.hexdigest()
    if not sheet or not os.path.isfile(sheet):
        return None
    if kind == "chapter-label":
        digest.update((row.get("chapter_label") or "").encode("utf-8"))
        digest.update(b"\0")
        _file_digest(digest, sheet)
        return digest.hexdigest()
    from .build_translations import _read_sheet_with_dedupe
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rows = _read_sheet_with_dedupe(
                sheet, primary_lookup=primary_lookup)
    except (OSError, ValueError, KeyError):
        return None
    digest.update(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


AUDIT_STORE = os.path.join(os.fspath(CACHE_ROOT), "preflight")


def audit_key(mark, row):
    """A pre-flight audit is the image, the resource and the sheet's ids."""
    sheet = row.get("sheet") or ""
    try:
        with open(sheet, newline="", encoding="utf-8-sig") as source:
            ids = sorted({(item.get("message_id") or "").strip()
                          for item in csv.DictReader(source)})
    except (OSError, ValueError, csv.Error):
        return None
    digest = hashlib.sha256()
    digest.update(("preflight\0%s\0%s\0" % (
        mark, row.get("resource") or "")).encode("utf-8"))
    digest.update("\0".join(ids).encode("utf-8"))
    return digest.hexdigest()


def remembered(store, name):
    return os.path.isfile(os.path.join(store, name[:2], name))


def remember(store, name):
    """Leave a mark saying this audit passed, racing safely with a twin."""
    folder = os.path.join(store, name[:2])
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "xb"):
            pass
    except (OSError, ValueError):
        pass


def key(mark, inputs):
    return hashlib.sha256(
        ("%s\0%s" % (mark, inputs)).encode("ascii")).hexdigest()


def path(store, name):
    return os.path.join(store, name[:2], name + ".row")


class Entry:
    """A stored row: what it expected to read, and what it then wrote."""

    __slots__ = ("header", "blob")

    def __init__(self, header, blob):
        self.header = header
        self.blob = blob

    @property
    def verified(self):
        return bool(self.header.get("verified"))

    def _slice(self, start, length):
        return self.blob[start:start + length]

    def matches(self, iso):
        """Is the image still holding what this row was produced from?"""
        for resource, expected in self.header.get("reads", ()):
            try:
                found = iso.read_entry(int(resource))
            except (ValueError, IndexError, KeyError, OSError):
                return False
            if hashlib.sha256(bytes(found)).hexdigest() != expected:
                return False
        return True

    def replay(self, iso):
        """Write what the row wrote, and rebuild what the build loop reads."""
        for resource, start, length in self.header.get("writes", ()):
            iso.write_entry(int(resource), self._slice(start, length))
        details = {"written": self.header.get("written", 0),
                   "details": self.header.get("note") or "from the row cache"}
        if self.header.get("grown_sectors"):
            details["grown_sectors"] = self.header["grown_sectors"]
        if self.header.get("relocated_offset") is not None:
            details["relocated_offset"] = self.header["relocated_offset"]
        if self.header.get("patched") is not None:
            start, length = self.header["patched"]
            details["patched"] = self._slice(start, length)
        return details


def load(store, name):
    try:
        with open(path(store, name), "rb") as source:
            head = source.read(len(MAGIC) + 4)
            if len(head) != len(MAGIC) + 4 or head[:len(MAGIC)] != MAGIC:
                return None
            size = struct.unpack("<I", head[len(MAGIC):])[0]
            header = json.loads(source.read(size).decode("utf-8"))
            if header.get("format") != FORMAT:
                return None
            return Entry(header, source.read())
    except (OSError, ValueError, KeyError):
        return None


def save(store, name, journal, details, verified=False, mark=""):
    """Keep this row's writes, and the reads that justify replaying them."""
    blob = bytearray()
    spans = {}

    def place(data):
        """Where *data* sits in the blob, appending it only once."""
        mark = hashlib.sha256(data).digest()
        found = spans.get(mark)
        if found is None:
            found = [len(blob), len(data)]
            spans[mark] = found
            blob.extend(data)
        return found

    writes = [[int(resource)] + place(data)
              for resource, data in journal.writes]
    header = {
        "format": FORMAT,
        "reads": sorted([int(resource), found]
                        for resource, found in journal.reads.items()),
        "writes": writes,
        "written": details.get("written", 0),
        "verified": bool(verified),
        "mark": mark,
    }
    if details.get("grown_sectors"):
        header["grown_sectors"] = details["grown_sectors"]
    if details.get("relocated_offset") is not None:
        header["relocated_offset"] = details["relocated_offset"]
    patched = details.get("patched")
    if patched is not None:
        header["patched"] = place(bytes(patched))
    note = details.get("details")
    if isinstance(note, str):
        header["note"] = note
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    destination = path(store, name)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = "%s.tmp.%d" % (destination, os.getpid())
    try:
        with open(temporary, "wb") as output:
            output.write(MAGIC)
            output.write(struct.pack("<I", len(encoded)))
            output.write(encoded)
            output.write(blob)
        os.replace(temporary, destination)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _row_mark(path_name):
    """The mark a stored row was made under, without reading its payload."""
    try:
        with open(path_name, "rb") as source:
            head = source.read(len(MAGIC) + 4)
            if len(head) != len(MAGIC) + 4 or head[:len(MAGIC)] != MAGIC:
                return None
            size = struct.unpack("<I", head[len(MAGIC):])[0]
            return json.loads(source.read(size).decode("utf-8")).get("mark")
    except (OSError, ValueError, KeyError):
        return None


def prune(store, limit=None, mark=None):
    limit = store_limit() if limit is None else limit
    kept = []
    removed = 0
    total = 0
    for base, _directories, names in os.walk(store):
        for name in names:
            if not name.endswith(".row"):
                continue
            item = os.path.join(base, name)
            try:
                stat = os.stat(item)
            except OSError:
                continue
            if mark and _row_mark(item) != mark:
                try:
                    os.unlink(item)
                    removed += 1
                    continue
                except OSError:
                    pass
            kept.append((stat.st_mtime_ns, stat.st_size, item))
            total += stat.st_size
    for _when, size, item in sorted(kept):
        if total <= limit:
            break
        try:
            os.unlink(item)
        except OSError:
            continue
        total -= size
        removed += 1
    return removed, total
