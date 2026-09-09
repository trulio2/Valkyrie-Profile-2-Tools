#!/usr/bin/env python3
"""Build a VP2 translation ISO from the tracked manifest."""

import argparse
import contextlib
import csv
import io
import os
import sys
import time
from pathlib import Path

from .paths import (
    BUILD_DIR, CACHE_ROOT, DATA_DIR, PROJECT_ROOT, WORKSPACE_DIR,
)
from . import vp2_build_parallel as build_parallel
from . import vp2_iso_buffer as iso_buffer
from . import vp2_iso_space as iso_space
from . import vp2_container_text as container_text
from . import vp2_map_names as map_names
from . import vp2_shared_font as shared_font
from .build_config import (
    FLAG_MAP, expand_flags, lint_manifest, load_manifest, report_lint,
    warn_unknown_flags,
)
from .build_patchers import (
    _scene_args_from_row, audit_args, collect_shared_font_characters,
    install_shared_font_in_memory, install_shared_font_once, patch_args,
    patch_container_resource_in_memory, patch_fontless_resource_in_memory,
    patch_image_resource_in_memory, patch_scene_resource_in_memory,
    patch_worldmap_resource_in_memory, preflight, run, verify_args,
    verify_scene_in_memory, wants_verify,
)
from .build_translations import (
    CHAPTERS_CSV, MAX_CONFLICTS_SHOWN, SCENES_DIR, WORKSPACE_DIR,
    _build_dedupe_lookup, _load_dedupe_lookup,
    _load_workspace_translations, _read_sheet_with_dedupe,
    apply_chapter_titles, repair_manifest_sheets, sheet_kind, workspace_kind,
)


def apply_map_area_names(iso, built, scenes_dir):
    sheets = Path(scenes_dir)
    source = sheets / "resource-0029-scenes.csv"
    layout = DATA_DIR / "area-name-layout.csv"
    if not sheets.is_dir():
        return 0

    candidates = {}
    if source.is_file() and layout.is_file():
        with source.open(newline="", encoding="utf-8-sig") as handle:
            records = {
                (row.get("message_id") or "").strip(): (
                    (row.get("original_en") or "").strip(),
                    (row.get("translated") or "").strip(),
                )
                for row in csv.DictReader(handle)
            }
        with layout.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                found = records.get((row.get("message_id") or "").strip())
                target = (row.get("target_resource") or "").strip()
                if found and all(found) and target:
                    candidates.setdefault(target, []).append(found)

    tokens = map_names.accent_tokens()
    patched = 0
    refused = []
    for resource in sorted(built, key=int):
        raw = iso.read_entry(int(resource))
        if not raw or map_names.pamm_row(raw) is None:
            continue
        found = list(candidates.get(str(resource), ()))
        sheet = sheets / ("resource-%04d-scenes.csv" % int(resource))
        if sheet.is_file():
            found += list(map_names.candidate_names(sheet))
        for english, translated in found:
            try:
                new_raw, info = map_names.patch_area_name(
                    raw, english, translated, tokens)
            except map_names.AreaNameTooLong as exc:
                refused.append((resource, english, str(exc)))
                continue
            if info is None:
                continue
            iso.write_entry(int(resource), new_raw)
            patched += 1
            break
    if refused:
        summary = "; ".join(
            f"#{resource} {english}" for resource, english, _ in refused[:6])
        raise ValueError(
            f"{len(refused)} map-screen area name(s) do not fit: {summary}")
    return patched

def _copy_source_image(source_iso, partial):
    """Copy the pristine image, printing coarse progress."""
    last = -1

    def report(percent):
        nonlocal last
        step = percent - percent % 10
        if step > last:
            last = step
            print(f"copy: {step}%", flush=True)

    iso_buffer.copy_image(str(source_iso), str(partial), progress=report)

CANDIDATE_EXTENTS = []

def _check_scene_content_ceiling(source_iso, resource, patched, fail,
                                 record_candidates=False):
    """Refuse a plain scene whose content ends past its measured budget."""
    _table, _entries, content_end, _tail_start, tail =         iso_space._parse_archive(patched)
    if tail:
        return
    with iso_buffer.IsoFile(str(source_iso), 'rb') as pristine:
        allocation = pristine.entry_outer_allocation(resource)
    try:
        container_text.check_scene_content_extent(
            resource, content_end, allocation)
    except container_text.SceneContentCeilingExceeded as exc:
        if not record_candidates:
            fail(str(exc))
            return
        if container_text.record_candidate_extent(
                resource, "scene-content", content_end):
            CANDIDATE_EXTENTS.append((resource, content_end))
            print("CANDIDATE: recorded resource #%d at %d in the limits "
                  "table so this build could finish. %s"
                  % (resource, content_end, exc), file=sys.stderr)


def _report_candidate_extents():
    """Say what a build is resting on that nobody has played."""
    if not CANDIDATE_EXTENTS:
        return
    print("", file=sys.stderr)
    print("%d resource(s) ran past a ceiling nobody has played:"
          % len(CANDIDATE_EXTENTS), file=sys.stderr)
    for resource, extent in CANDIDATE_EXTENTS:
        print("  #%-6d ends at %d" % (resource, extent), file=sys.stderr)
    print("Each is written to the limits table as kind=candidate. This "
          "image is for testing those screens, not for release: play each "
          "one, then mark its row verified -- or lower it.", file=sys.stderr)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description='Drive a VP2 translation build from a manifest.')
    parser.add_argument('source_iso',
                        help='Path to the pristine USA ISO. This is the only '
                             'argument a normal build needs.')
    parser.add_argument('--manifest',
                        default=str(DATA_DIR / 'build-manifest.csv'),
                        help='Build manifest CSV '
                             '(default: %(default)s).')
    parser.add_argument('--output', '--output-iso', dest='output_iso',
                        default=str(BUILD_DIR / 'release.iso'),
                        help='Where to write the translated ISO '
                             '(default: %(default)s). Replaced if '
                             'it exists; the build writes a .partial and '
                             'renames, so the old one survives a failure.')
    parser.add_argument('--reference-iso',
                        help='Pristine USA ISO for verify gates and glyph '
                             'fingerprints. Defaults to source_iso, which is '
                             'what it is on a normal build.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the command chain without running it.')
    parser.add_argument('--verbose', action='store_true',
                        help='Show successful patcher and audit details.')
    parser.add_argument('--skip-resources', default='',
                        help='Comma-separated resource numbers to skip, e.g. '
                             '1197,1299. Useful for testing the rest of a '
                             'manifest while a sheet issue is being fixed.')
    parser.add_argument('--working-iso',
                        default=str(BUILD_DIR / 'working.iso'),
                        help='Only used by --keep-working-iso on the '
                             'parallel path. The serial build patches the '
                             'output in place and writes no working copy.')
    parser.add_argument('--no-preflight', action='store_true',
                        help='Skip the per-scene audit --strict pass that '
                             'runs before patching.')
    parser.add_argument('--no-map-names', action='store_true',
                        help='skip the map-screen area-name pass')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip the per-scene read-back gate the manifest '
                             'asks for. A packaged release build uses this: '
                             'the gate proves the writers against a checkout, '
                             'and re-running it per row roughly doubles the '
                             'work for a user rebuilding tested data.')
    parser.add_argument('--keep-working-iso', action='store_true',
                        help='Leave the working ISO on disk after success '
                             'instead of moving it to output_iso.')
    parser.add_argument('--scenes-dir',
                        default=str(WORKSPACE_DIR / 'internal' / 'records'),
                        help='Directory holding the compiled record sheets '
                             'this build patches from '
                             '(default: %(default)s).')
    parser.add_argument('--shared-font-slots',
                        help='Character-to-token map for the shared font '
                             '(default: the packaged table).')
    parser.add_argument('--record-candidate-extents', action='store_true',
                        help='When a resource ends past its recorded ceiling, '
                             'record the extent it reached as kind=candidate '
                             'and carry on instead of refusing. The build is '
                             'then for testing those screens, not for '
                             'release: play each one and mark its row '
                             'verified, or lower it.')
    parser.add_argument('--lint', action='store_true',
                        help='Static checks only: load the manifest, run '
                             'every '
                             'lint rule, print errors and warnings, and exit '
                             'non-zero on any error. No ISO is copied, no '
                             'patchers run, no preflight. The position '
                             'arguments (source_iso, output_iso, '
                             '--reference-iso) may be placeholder paths.')
    parser.add_argument('--jobs', type=int,
                        default=os.cpu_count() or 4,
                        help='Number of parallel workers (default: '
                             'os.cpu_count(); capped at '
                             '%d). Each worker holds a private IsoBuffer '
                             'copy of the pre-installed ISO, so RAM scales '
                             'as jobs * ISO size (4.6 GB on the USA release). '
                             'Set to 1 to force a single worker.' %
                             build_parallel.DEFAULT_JOBS_CAP)
    parser.add_argument('--parallel', action='store_true',
                        help='Use the worker pool instead of the default '
                             'file-backed path. Each worker holds a whole '
                             'IsoBuffer, so this costs GB of memory per '
                             'worker and is currently slower on a manifest '
                             'this size; measured 23.9s / 34 GB against '
                             '21.2s / 151 MB. Kept for manifests large '
                             'enough that per-row work dominates.')
    parser.add_argument('--no-repair-sheets', action='store_true',
                        help='Do not rewrite sheets whose line endings an '
                             'editor mangled. Reads tolerate the damage '
                             'either way; repairing only keeps it out of a '
                             'commit.')
    parser.add_argument('--no-parallel', action='store_true',
                        help='Accepted and ignored: the serial path is the '
                             'default now.')
    parser.add_argument('--no-preinstall-cache', action='store_true',
                        help='Always rebuild the pre-installed ISO instead '
                             'of hitting the cache at '
                             'the cache directory. The pre-install '
                             'step itself is unchanged; only the cache '
                             'short-circuit is bypassed.')
    parser.add_argument('--preinstall-cache',
                        default=str(CACHE_ROOT / 'preinstall'),
                        help='Directory holding cached pre-installed ISOs. '
                             '(default: %(default)s).')
    args = parser.parse_args()

    if args.shared_font_slots:
        tokens = shared_font.use_slot_assignments(args.shared_font_slots)
        print("shared-font: %d character(s) from %s"
              % (len(tokens), args.shared_font_slots))

    rows = load_manifest(args.manifest)
    if not rows:
        print("manifest is empty", file=sys.stderr)
        sys.exit(1)
    if args.skip_resources:
        skip = {r.strip() for r in args.skip_resources.split(',') if r.strip()}
        skipped = [r for r in rows if r.get('resource') in skip]
        rows = [r for r in rows if r.get('resource') not in skip]
        if skipped:
            print(f"skipping: {', '.join(r['resource'] for r in skipped)}")
        if not rows:
            print("no rows left after skip", file=sys.stderr)
            sys.exit(1)

    if args.lint:
        issues = lint_manifest(rows)
        errors, warns = report_lint(issues)
        sys.exit(1 if errors else 0)

    output_iso = Path(args.output_iso).resolve()
    working_iso = Path(args.working_iso).resolve()

    if output_iso.exists():
        # Safe to replace: the build writes <output>.partial and renames
        # only on success, so the existing ISO survives a failed run.
        print(f"replacing existing output: {output_iso}")
    if output_iso == working_iso:
        print(f"working ISO must differ from output ISO: {working_iso}",
              file=sys.stderr)
        sys.exit(1)
    if args.keep_working_iso and working_iso.exists():
        print(f"refusing to overwrite existing working ISO: {working_iso}",
              file=sys.stderr)
        sys.exit(1)

    source_iso = Path(args.source_iso).resolve()
    reference_iso = Path(args.reference_iso or args.source_iso).resolve()

    if not args.no_preflight:
        preflight(reference_iso, rows, dry_run=args.dry_run,
                  verbose=args.verbose)

    started = time.time()
    working_iso.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        install_shared_font_once(
            str(working_iso), rows, dry_run=True)
        for row in rows:
            warn_unknown_flags(row, row['kind'])
            if row['kind'] == 'container':
                print(f"$ [in-memory] patch container resource "
                      f"{row['resource']} from {row['sheet']}")
                continue
            if row['kind'] in ('fontless', 'worldmap'):
                print(f"$ [in-memory] patch fontless resource "
                      f"{row['resource']} from {row['sheet']}")
                continue
            if row['kind'] == 'scene':
                print(f"$ [in-memory] patch scene resource {row['resource']} "
                      f"from {row['sheet']}")
                continue
            patch = patch_args(str(working_iso), row)
            run(patch, dry_run=True)
            vargs = verify_args(str(working_iso), row, str(reference_iso))
            if vargs:
                run(vargs, dry_run=True)
        print(f"dry run: would write {output_iso}")
        return

    if working_iso.exists():
        print(f"refusing to overwrite existing working ISO: {working_iso}",
              file=sys.stderr)
        sys.exit(1)
    working_iso.parent.mkdir(parents=True, exist_ok=True)


    # The opt-in worker pool merges private IsoBuffer results in manifest order.
    use_parallel = (
        args.parallel
        and len(rows) > 1
        and all(r['kind'] in ('container', 'fontless', 'worldmap', 'scene')
                for r in rows)
    )

    if use_parallel:
        # Each worker holds a private copy of the pre-installed image, so
        # the cap that matters is free memory, not core count.
        fits = build_parallel.jobs_that_fit(source_iso.stat().st_size)
        jobs = max(1, min(args.jobs, build_parallel.DEFAULT_JOBS_CAP, fits))
        if jobs < min(args.jobs, build_parallel.DEFAULT_JOBS_CAP):
            print(f"parallel: {jobs} worker(s); free memory holds that many "
                  f"copies of a {source_iso.stat().st_size / 2**30:.1f} GB image")
        primary_lookup = _load_dedupe_lookup(args.scenes_dir)
        try:
            build_parallel.run_parallel(
                str(source_iso), str(output_iso), rows,
                jobs=jobs,
                cache_root=Path(args.preinstall_cache),
                force_preinstall=args.no_preinstall_cache,
                verbose=args.verbose,
                primary_lookup=primary_lookup,
            )
        except Exception as exc:
            print(f"parallel build failed: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.keep_working_iso:
            iso = iso_buffer.IsoBuffer.from_path(str(output_iso))
            iso.commit(str(working_iso))
            print(f"working ISO retained: {working_iso}")
        total = time.time() - started
        _report_candidate_extents()
        print(f"done. {len(rows)} resources in {total:.1f}s -> {output_iso}")
        if args.keep_working_iso:
            print(f"working ISO retained: {working_iso}")
        return

    # The serial path patches one partial file in place and renames on success.
    output_iso.parent.mkdir(parents=True, exist_ok=True)
    partial = output_iso.with_name(output_iso.name + ".partial")
    if partial.exists():
        partial.unlink()
    print(f"copying source to {partial.name}")
    _copy_source_image(source_iso, partial)
    iso = iso_buffer.IsoFile(str(partial))

    if not args.no_repair_sheets:
        repair_manifest_sheets(rows)

    applied = apply_chapter_titles(rows)
    if applied:
        print(f"chapters: {applied} title(s) from "
              f"{os.path.relpath(CHAPTERS_CSV, PROJECT_ROOT)}")

    primary_lookup = _load_dedupe_lookup(args.scenes_dir)

    install_shared_font_in_memory(iso, rows, primary_lookup=primary_lookup)
    if not iso.table:
        raise RuntimeError("IsoFile missing tri-Ace index")

    reference_reader = iso_buffer.IsoFile(str(reference_iso), mode="rb")

    vacated = []

    def _fail(message, code=1):
        """Stop, leaving the partial image on disk to be looked at."""
        iso.close()
        print(message, file=sys.stderr)
        print(f"partial ISO retained: {partial}", file=sys.stderr)
        sys.exit(code)

    for i, row in enumerate(rows):
        kind = row['kind']
        resource = row['resource']
        row_start = time.time()
        step = f"[{i + 1}/{len(rows)}] {kind} {resource}"

        warn_unknown_flags(row, kind)

        if kind in ('container', 'fontless', 'worldmap', 'scene', 'image'):
            row_log = io.StringIO()
            try:
                with contextlib.redirect_stdout(row_log):
                    if kind == 'image':
                        details = patch_image_resource_in_memory(
                            iso, row, primary_lookup=primary_lookup)
                    elif kind == 'container':
                        details = patch_container_resource_in_memory(
                            iso, row, primary_lookup=primary_lookup)
                    elif kind in ('fontless', 'worldmap'):
                        details = patch_fontless_resource_in_memory(
                            iso, row, primary_lookup=primary_lookup)
                    else:  # scene
                        details = patch_scene_resource_in_memory(
                            iso, row, primary_lookup=primary_lookup,
                            reference=reference_reader)
                written = details.get('written', 0)
            except Exception as exc:
                if row_log.getvalue():
                    print(row_log.getvalue(), end='', file=sys.stderr)
                _fail(f"{step} patch failed: {exc}")
            if args.verbose and row_log.getvalue():
                print(row_log.getvalue(), end='')

            if kind == 'scene' and details.get('patched') is not None:
                _check_scene_content_ceiling(
                    source_iso, int(resource), details['patched'], _fail,
                    record_candidates=args.record_candidate_extents)

            if (details.get('grown_sectors')
                    or details.get('relocated_offset') is not None):
                iso.commit()
                iso.close()
                summary = iso_space.relocate(
                    str(partial), int(resource), details['patched'],
                    vacated=vacated)
                where = ("reusing space a previous move freed"
                         if summary['reused_vacated'] else "appended")
                print(f"  relocated resource #{resource}: "
                      f"{summary['old_sectors']} -> "
                      f"{summary['new_sectors']} sector(s), now at lba "
                      f"{summary['new_lba']} ({where})")
                iso = iso_buffer.IsoFile(str(partial))

            if (kind == 'scene' and wants_verify(row)
                    and not args.no_verify):
                iso.commit()
                iso.close()
                verify_log = io.StringIO()
                try:
                    with contextlib.redirect_stdout(verify_log):
                        verify_scene_in_memory(
                            partial, row, reference_iso,
                            primary_lookup=primary_lookup)
                except Exception as exc:
                    if verify_log.getvalue():
                        print(verify_log.getvalue(), end='', file=sys.stderr)
                    iso = iso_buffer.IsoFile(str(partial))
                    _fail(f"{step} verify failed: {exc}")
                if args.verbose and verify_log.getvalue():
                    print(verify_log.getvalue(), end='')
                iso = iso_buffer.IsoFile(str(partial))
        else:
            iso.commit()
            iso.close()
            pargs = patch_args(str(partial), row)
            result = run(pargs, dry_run=False)
            if result.returncode != 0:
                iso = iso_buffer.IsoFile(str(partial))
                _fail(f"{step} patch failed (exit {result.returncode})",
                      result.returncode)
            vargs = verify_args(str(partial), row, str(reference_iso))
            if vargs:
                result = run(vargs, dry_run=False)
                if result.returncode != 0:
                    iso = iso_buffer.IsoFile(str(partial))
                    _fail(f"{step} verify failed (exit {result.returncode})",
                          result.returncode)
            iso = iso_buffer.IsoFile(str(partial))
            written = None

        elapsed = time.time() - row_start
        suffix = (f" {written} records" if written is not None else "")
        print(f"{step} ok ({elapsed:.1f}s){suffix}")

    if not args.no_map_names:
        try:
            named = apply_map_area_names(
                iso, {row['resource'] for row in rows}, args.scenes_dir)
        except Exception as exc:
            _fail(f"map-screen area names failed: {exc}")
        if named:
            print(f"map-screen area names: {named} resource(s) translated")

    iso.commit()
    iso.close()
    os.replace(str(partial), str(output_iso))
    print(f"wrote output: {output_iso}")

    total = time.time() - started
    _report_candidate_extents()
    print(f"done. {len(rows)} resources in {total:.1f}s -> {output_iso}")

if __name__ == '__main__':
    main()
