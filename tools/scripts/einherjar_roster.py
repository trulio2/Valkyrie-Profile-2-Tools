# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import csv
import re

from .paths import DATA_DIR

ROSTERS_PATH = DATA_DIR / "einherjar-rosters.csv"
MATERIALIZATION_MARKER = "Perform materialization?"

JOIN_LINE = re.compile(r"^(.+?) <PART> has joined the party\.$")
STORY_CAST = frozenset({
    "Dylan", "Lezard", "Leone", "Arngrim", "Mithra",
    "Lenneth", "Silmeria", "Brahms", "Hrist", "Freya", "Valkyrie",
})


def _text(value):
    return " ".join((value or "").split())


def load_rosters(path=None):
    source = path or ROSTERS_PATH
    rosters = {}
    try:
        with open(source, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                resource = (row.get("resource") or "").strip()
                name = (row.get("einherjar") or "").strip()
                if resource and name:
                    rosters.setdefault(resource, set()).add(name)
    except (OSError, csv.Error, UnicodeDecodeError):
        return {}
    return {key: frozenset(value) for key, value in rosters.items()}


def site_einherjar(rows):
    names = []
    for row in rows:
        found = JOIN_LINE.match(_text(row.get("original_en")))
        if found and found.group(1) not in STORY_CAST:
            names.append(found.group(1))
    return names


def is_site(rows):
    return any(_text(row.get("original_en")) == MATERIALIZATION_MARKER
               for row in rows)


def suppressed_rows(rows, rosters=None):
    if not is_site(rows):
        return []
    offered = site_einherjar(rows)
    if not offered:
        return []
    table = load_rosters() if rosters is None else rosters
    resource = next(((row.get("resource") or "").strip()
                     for row in rows if (row.get("resource") or "").strip()),
                    "")
    roster = table.get(resource)
    if roster is None:
        return []
    dead = set(offered) - set(roster)
    if not dead:
        return []
    offered_set = set(offered)
    suppressed = []
    for row in rows:
        english = _text(row.get("original_en"))
        found = JOIN_LINE.match(english)
        if found:
            name = found.group(1)
        elif _text(row.get("speaker")) in offered_set:
            name = _text(row.get("speaker"))
        elif english in offered_set:
            name = english
        else:
            continue
        if name in dead:
            suppressed.append(row)
    return suppressed
