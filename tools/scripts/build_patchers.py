"""Build-row adapters for each VP2 text storage format."""

import argparse
import contextlib
import csv
import io
import os
import subprocess
import sys

from .paths import FROZEN, PROJECT_ROOT
from . import vp2_container_text
from . import vp2_iso_buffer as iso_buffer
from . import vp2_shared_font as shared_font
from . import vp2_dragon_hall
from .build_config import expand_flags
from .build_translations import _read_sheet_with_dedupe

def run(args, *, dry_run=False):
    """Print and optionally execute a subprocess; return CompletedProcess."""
    print(f"$ {' '.join(str(a) for a in args)}")
    if dry_run:
        return type('R', (), {'returncode': 0})()
    if FROZEN:
        raise RuntimeError(
            "packaged build cannot run the subprocess step "
            f"{' '.join(str(a) for a in args[1:3])}; run it from a checkout")
    return subprocess.run(args, cwd=PROJECT_ROOT)

def audit_args(iso, row):
    """Subprocess args for a row's per-step audit (scenes only)."""
    if row['kind'] != 'scene':
        return None
    return [sys.executable, '-m', 'tools.scripts.vp2_cutscene_workflow',
            'audit', iso,
            '--resource', row['resource'], '--csv', row['sheet']]

def wants_verify(row):
    """Whether a row asked for its post-patch gate."""
    return (row.get('verify') or '').strip().lower() in ('yes', 'true', '1')

def verify_scene_in_memory(iso_path, row, reference_iso,
                           primary_lookup=None):
    """Run the scene verify gate against the image being built."""
    if row['kind'] != 'scene' or not wants_verify(row):
        return
    from . import vp2_cutscene_subtitles as subtitles
    subtitles.verify_scene_sheet(argparse.Namespace(
        csv=row['sheet'],
        resource=int(row['resource']),
        iso=str(iso_path),
        reference_iso=str(reference_iso),
        en_names=None,
        primary_lookup=primary_lookup,
        chapter_title=row.get('chapter_title') or None,
        chapter_title_message=row.get('chapter_title_message') or None,
    ))

def verify_args(iso, row, reference_iso):
    """Subprocess args for a row's per-step verify (scenes only)."""
    if row['kind'] != 'scene':
        return None
    if (row.get('verify') or '').strip().lower() not in ('yes', 'true', '1'):
        return None
    args = [sys.executable, '-m', 'tools.scripts.vp2_cutscene_subtitles',
            'verify',
            iso, row['sheet'], '--reference-iso', reference_iso]
    if row.get('chapter_title'):
        args += ['--chapter-title', row['chapter_title'],
                 '--chapter-title-message', row['chapter_title_message']]
    return args

def collect_shared_font_characters(rows, primary_lookup=None):
    """Walk every row's sheet, accumulate characters eligible for entry 8."""
    chars = set()
    for row in rows:
        flags = (row.get('flags') or '').split()
        if ('shared-font-glyphs' not in flags
                and row.get('kind') not in ('scene', 'fontless', 'worldmap')):
            continue
        sheet = row.get('sheet')
        if not sheet or not os.path.exists(sheet):
            continue
        try:
            for line in _read_sheet_with_dedupe(
                    sheet, primary_lookup=primary_lookup):
                chars.update(line.get('translated') or '')
        except (OSError, csv.Error):
            continue
    return chars & set(shared_font.SHARED_EXTENSION_TOKENS)

def install_shared_font_once(working_iso, rows, *, dry_run=False):
    """Pre-install shared-font accents on the working ISO."""
    needed = collect_shared_font_characters(rows)
    if not needed:
        print("shared-font: no rows requested the lowercase accent profile; "
              "entry 8 left untouched")
        return
    if dry_run:
        print(f"shared-font: would install {len(needed)} character(s) on "
              f"the working ISO: {''.join(sorted(needed))}")
        return
    info = shared_font.install_for_iso(working_iso, needed)
    print("shared-font: " + shared_font.describe_install(info))

def install_shared_font_in_memory(iso, rows, *, dry_run=False,
                                  primary_lookup=None):
    """Pre-install shared-font accents inside an IsoBuffer."""
    needed = collect_shared_font_characters(
        rows, primary_lookup=primary_lookup)
    if not needed:
        print("shared-font: no rows requested the lowercase accent profile; "
              "entry 8 left untouched")
        return
    if dry_run:
        print(f"shared-font: would install {len(needed)} character(s) on "
              f"the working ISO: {''.join(sorted(needed))}")
        return
    original = iso.read_entry(shared_font.SHARED_FONT_ENTRY)
    rebuilt, info = shared_font.install_glyphs(
        original, needed, shared_font.SHARED_EXTENSION_TOKENS)
    if not info.get("no_op"):
        iso.write_entry(shared_font.SHARED_FONT_ENTRY, rebuilt)
    print("shared-font: " + shared_font.describe_install(info))

def patch_container_resource_in_memory(iso, row, *, primary_lookup=None):
    """Read a container row's sheet and patch the resource in *iso*."""
    sheet_path = row['sheet']
    sheet_rows = _read_sheet_with_dedupe(sheet_path, primary_lookup=primary_lookup)
    supplied = {
        r['message_id']: r
        for r in sheet_rows
        if (r.get('translated') or '').strip()
    }
    if not supplied:
        return {"written": 0, "details": {"wrapper": "unchanged"},
                "font_patch": None}
    flags = (row.get('flags') or '').split()
    record_kinds = {
        (item.get('record_kind') or item.get('kind') or '').strip()
        for item in sheet_rows
    }
    if 'dragon_hall_prompt' in record_kinds:
        if record_kinds != {'dragon_hall_prompt'}:
            raise ValueError(
                f"{sheet_path}: Dragon Hall prompt is mixed with other records")
        return vp2_dragon_hall.patch_resource_in_memory(
            iso,
            int(row['resource']),
            supplied,
            accent_tokens=(shared_font.SHARED_EXTENSION_TOKENS
                           if 'shared-font-glyphs' in flags else None),
        )
    subresource = (row.get('subresource') or '').strip()
    return vp2_container_text.patch_resource_in_memory(
        iso,
        int(row['resource']),
        supplied,
        shared_font_glyphs='shared-font-glyphs' in flags,
        accent_donors_path=None,
        warn_line_width=None,
        keep_region='keep-region' in flags,
        subresource=int(subresource, 0) if subresource else None,
    )

def patch_fontless_resource_in_memory(iso, row, *, primary_lookup=None):
    """Read a fontless/shared-font row's sheet and patch the resource in *iso*."""
    sheet_path = row['sheet']
    sheet_rows = _read_sheet_with_dedupe(sheet_path, primary_lookup=primary_lookup)
    edits = {}
    for sheet_row in sheet_rows:
        if not (sheet_row.get('translated') or '').strip():
            continue
        edits[('message_id', int(sheet_row['message_id']))] = {
            'en_text': sheet_row.get('original_en', ''),
            'translated': sheet_row['translated'],
        }
    from . import vp2_text_patch as text_patch
    return text_patch.patch_resource_in_memory(
        iso,
        int(row['resource']),
        edits,
    )

patch_worldmap_resource_in_memory = patch_fontless_resource_in_memory

def _scene_args_from_row(row):
    """Construct an argparse-like Namespace for one scene manifest row."""
    import argparse
    flags = set((row.get('flags') or '').split())
    args = argparse.Namespace(
        csv=row['sheet'],
        audio_id=None,
        all_translated=True,
        skip_audio_id=[],
        resource=int(row['resource']),
        opening_only_font_reuse='opening-only-font-reuse' in flags,
        safe_font_reuse='safe-font-reuse' in flags,
        full_font='full-font' in flags,
        use_vacated='use-vacated' in flags,
        relocate='relocate' in flags,
        allow_pk1_growth='allow-pk1-growth' in flags,
        pk1_align=4,
        accent_donors=None,
        en_names=None,
        chapter_title=row.get('chapter_title') or None,
        chapter_title_message=int(row['chapter_title_message']) if row.get('chapter_title_message') else None,
        dry_run=False,
    )
    return args

def patch_scene_resource_in_memory(iso, row, *, primary_lookup=None,
                                   reference=None):
    """Read a scene row's sheet and patch the resource in *iso*."""
    sheet_path = row['sheet']
    from . import vp2_cutscene_subtitles as subtitles
    rows = subtitles.read_scene_rows(
        sheet_path, int(row['resource']), primary_lookup=primary_lookup)
    title = (row.get('chapter_title') or '').strip()
    if not rows and not title:
        print(f"warning: no translatable rows in {sheet_path}", file=sys.stderr)
        return {'written': 0, 'details': {}, 'rendered': [], 'installed': []}
    args = _scene_args_from_row(row)
    iso.is_in_memory = isinstance(iso, iso_buffer.IsoBuffer)
    return subtitles.patch_resource_in_memory(
        iso, int(row['resource']), args, rows, reference=reference)

def patch_args(working_iso, row):
    """Subprocess args for the row's patch step."""
    kind = row['kind']
    sheet = row['sheet']
    flags = expand_flags(row, kind)

    if kind == 'scene':
        args = [sys.executable, '-m', 'tools.scripts.vp2_cutscene_subtitles',
                'patch',
                working_iso, working_iso, sheet, '--all-translated']
        if row.get('chapter_title'):
            args += ['--chapter-title', row['chapter_title'],
                     '--chapter-title-message', row['chapter_title_message']]
    elif kind == 'container':
        args = [sys.executable, '-m', 'tools.scripts.vp2_container_text',
                'patch',
                working_iso, working_iso, sheet, '--resource', row['resource']]
    elif kind in ('fontless', 'worldmap'):
        args = [sys.executable, '-m', 'tools.scripts.vp2_text_patch',
                working_iso, sheet, working_iso, '--resources', row['resource']]
    else:
        print(f"error: unknown kind '{kind}' for row {row}", file=sys.stderr)
        sys.exit(1)

    return args + flags

def audit_scene_row_in_memory(reference_iso, row):
    """Run one scene row's strict coverage audit inside this process."""
    from . import vp2_cutscene_workflow as workflow

    workflow.cmd_audit(argparse.Namespace(
        iso=str(reference_iso),
        resource=int(row['resource']),
        csv=row['sheet'],
        strict=True,
    ))

def preflight(reference_iso, rows, *, dry_run, verbose=False):
    """Audit every scene row against the reference ISO with --strict."""
    scene_rows = [r for r in rows if r['kind'] == 'scene']
    if not scene_rows:
        return
    print(f"== pre-flight: auditing {len(scene_rows)} row(s) against "
          f"{reference_iso} ==")
    for row in scene_rows:
        if dry_run:
            run([sys.executable, '-m', 'tools.scripts.vp2_cutscene_workflow',
                 'audit', str(reference_iso), '--resource', row['resource'],
                 '--csv', row['sheet'], '--strict'], dry_run=True)
            continue
        audit_log = io.StringIO()
        try:
            with contextlib.redirect_stdout(audit_log):
                audit_scene_row_in_memory(reference_iso, row)
        except SystemExit as exc:
            print(audit_log.getvalue(), end='')
            print(f"pre-flight failed on {row['kind']} {row['resource']}: "
                  f"{exc}", file=sys.stderr)
            sys.exit(exc.code if isinstance(exc.code, int) else 1)
        except (OSError, ValueError, KeyError, IndexError, csv.Error) as exc:
            print(audit_log.getvalue(), end='')
            print(f"pre-flight failed on {row['kind']} {row['resource']}: "
                  f"{exc}", file=sys.stderr)
            sys.exit(1)
        if verbose:
            print(audit_log.getvalue(), end='')
    print(f"== pre-flight ok: {len(scene_rows)} row(s) ==")
