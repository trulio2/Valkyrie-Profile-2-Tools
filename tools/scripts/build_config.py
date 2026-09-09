"""Manifest loading, flag expansion, and static validation."""

import csv
import os
import sys

from . import normalize_sheet_newlines
from . import vp2_title_face

FLAG_MAP = {
    'scene': {
        'full-font': '--full-font',
        'all-translated': '--all-translated',
        'relocate': '--relocate',
        'allow-pk1-growth': '--allow-pk1-growth',
    },
    'container': {
        'shared-font-glyphs': '--shared-font-glyphs',
        'keep-region': '--keep-region',
    },
    'worldmap': {
        'shared-font-glyphs': '--shared-font-glyphs',
    },
    'fontless': {
        'shared-font-glyphs': '--shared-font-glyphs',
    },
    'image': {},
}

def expand_flags(row, kind):
    """Convert the manifest's flags column into subprocess args."""
    raw = (row.get('flags') or '').split()
    return [FLAG_MAP[kind][f] for f in raw if f in FLAG_MAP[kind]]

def warn_unknown_flags(row, kind):
    """Surface flags that don't map to the tool, so the manifest stays honest."""
    raw = set((row.get('flags') or '').split())
    known = set(FLAG_MAP[kind].keys())
    extra = raw - known
    if extra:
        print(f"warning: unknown flags for {kind} {row.get('resource')}: "
              f"{' '.join(sorted(extra))}", file=sys.stderr)

def load_manifest(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def lint_manifest(rows, *, repair_sheets=True):
    """Static checks on a manifest row list."""
    issues = []
    seen_pairs = {}
    for index, row in enumerate(rows):
        row_label = f"row {index + 1}"
        kind = (row.get('kind') or '').strip()
        resource = (row.get('resource') or '').strip()
        sheet = (row.get('sheet') or '').strip()
        verify = (row.get('verify') or '').strip().lower()

        if not kind:
            issues.append(('error', '', resource,
                           f"{row_label}: missing 'kind'"))
            continue
        if not resource:
            issues.append(('error', kind, '',
                           f"{row_label}: missing 'resource'"))
            continue
        try:
            resource_int = int(resource)
        except ValueError:
            issues.append(('error', kind, resource,
                           f"{row_label}: resource is not an integer: "
                           f"{resource!r}"))
            continue
        del resource_int  # used only to validate parseability

        if kind not in FLAG_MAP:
            issues.append(('error', kind, resource,
                           f"{row_label}: unknown kind: {kind!r} "
                           f"(known: {', '.join(sorted(FLAG_MAP.keys()))})"))

        key = (kind, resource)
        if key in seen_pairs:
            issues.append(('error', kind, resource,
                           f"{row_label}: duplicate of "
                           f"{seen_pairs[key]} (same kind+resource)"))
        else:
            seen_pairs[key] = row_label

        if sheet:
            if not os.path.exists(sheet):
                issues.append(('error', kind, resource,
                               f"{row_label}: sheet missing: {sheet}"))
            else:
                if repair_sheets and not os.path.isdir(sheet):
                    fields, records = normalize_sheet_newlines.repair_in_place(
                        sheet)
                    if fields or records:
                        parts = []
                        if fields:
                            parts.append(f"{fields} CRLF inside quoted "
                                         f"field(s)")
                        if records:
                            parts.append(f"{records} record terminator(s) "
                                         f"without CR")
                        print(f"sheets: repaired {sheet} "
                              f"({'; '.join(parts)})")

        if verify in ('yes', 'true', '1') and kind != 'scene':
            issues.append(('warn', kind, resource,
                           f"{row_label}: verify=yes has no effect for "
                           f"kind={kind!r} (only scene has a verify gate)"))

    return issues

def report_lint(issues, *, out=None):
    """Print ``issues`` to *out* (default stdout) grouped by severity."""
    out = out or sys.stdout
    errors = [m for m in issues if m[0] == 'error']
    warns = [m for m in issues if m[0] == 'warn']
    for severity in ('error', 'warn'):
        bag = errors if severity == 'error' else warns
        for _, kind, resource, message in bag:
            tag = f"{kind or '?'} {resource or '?'}".strip()
            print(f"lint {severity}: {tag}: {message}", file=out)
    print(f"lint: {len(errors)} error(s), {len(warns)} warn(s)", file=out)
    return len(errors), len(warns)
