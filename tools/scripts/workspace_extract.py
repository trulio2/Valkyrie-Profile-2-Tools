# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Generate ignored translator reference and internal build state."""

from __future__ import annotations

import csv
import contextlib
import io
import json
import os
import shutil
import struct
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import disc_identity
from . import resource_classify
from . import scene_sheet_export
from . import triace_ps2_unpack as triace
from . import vp2_container_text
from . import container_archive
from . import package_archive
from . import protected_package
from . import vp2_dcms as dcms
from . import vp2_jp_glyphs
from . import vp2_dragon_hall
from . import vp2_title_face
from .translation_pack import PackError, _read_csv, _write_csv_atomic
from .translation_layout import rename_tree, write_reference_tree


INVENTORY_FIELDS = (
    "index",
    "byte_offset",
    "length",
    "type",
    "classification",
    "message_count",
    "error",
)

CONTAINER_CLASSES = frozenset({
    "container_slz",
    "container_zls",
    "container_sle",
    "container_nested",
})


def _entry_type(raw: bytes, allocated: int) -> str:
    head = raw[:0x1000]
    if len(head) < 0x20:
        head += b"\0" * (0x20 - len(head))
    return triace.classify(head, allocated)


def _looks_like_stream_chain(raw: bytes) -> bool:
    """Recognize an outer stream table before scanning its embedded streams."""
    if len(raw) < 0x10:
        return False
    marker, count, first_offset = struct.unpack_from("<3I", raw)
    return (marker == 0 and 0 < count < 0x10000
            and 0x10 <= first_offset < len(raw)
            and first_offset % 0x10 == 0)


def write_inventory(image: Path, target: Path, *, inspect_text: bool) -> list[dict[str, str]]:
    """Write a structural resource inventory and return its rows."""
    rows: list[dict[str, str]] = []
    with image.open("rb") as source:
        game, total, table = triace.load_table(source)
        if game != "VP2":
            raise PackError(f"{image} is {game}, not a VP2 image")
        for resource in range(total):
            sectors = table[total + resource]
            allocated = sectors * triace.SECTOR
            raw = bytes(dcms.read_entry(source, table, total, resource))
            kind = _entry_type(raw, allocated) if raw else "empty"
            classification = ""
            message_count = ""
            error = ""
            if inspect_text:
                if not raw:
                    classification = "empty"
                else:
                    try:
                        classification, info = resource_classify.classify_entry(raw)
                        if info:
                            message_count = str(info.get("message_count", ""))
                    except (ValueError, KeyError, IndexError, struct.error) as exc:
                        classification = "unreadable"
                        error = str(exc)
                if classification in {"non_text", "unreadable"}:
                    try:
                        if raw[:8] == b"mcps2lib":
                            blob = raw
                        elif _looks_like_stream_chain(raw):
                            _offset, blob = container_archive.find_container_stream(
                                raw)
                            if blob is None:
                                raise ValueError(
                                    "stream table has no MCPS2 text bank")
                        else:
                            try:
                                blob = package_archive.unpack_container(raw)
                            except package_archive.ContainerNotFound:
                                clear, _layout = protected_package.decode_entry(raw)
                                blob = package_archive.unpack_container(clear)
                        _meta, messages = vp2_container_text.read_messages(
                            blob, resource)
                        classification = "container_nested"
                        message_count = str(len(messages))
                        error = ""
                    except (ValueError, KeyError, IndexError, struct.error,
                            package_archive.ContainerNotFound,
                            protected_package.ProtectedPackageError):
                        pass
            rows.append({
                "index": str(resource),
                "byte_offset": str(table[resource] * triace.SECTOR),
                "length": str(allocated),
                "type": kind,
                "classification": classification,
                "message_count": message_count,
                "error": error,
            })
    _write_csv_atomic(target, list(INVENTORY_FIELDS), rows)
    return rows


def _normalize_source_sheet(path: Path, family: str) -> int:
    """Give an extractor sheet the shared local-workspace identity columns."""
    fields, rows = _read_csv(path)
    if not rows:
        path.unlink()
        return 0
    if family == "container":
        if "kind" not in fields:
            raise PackError(f"{path}: container sheet has no record kind")
        fields = ["record_kind" if field == "kind" else field for field in fields]
        for row in rows:
            row["record_kind"] = row.pop("kind", "")
    output_fields = ["kind"] + [field for field in fields if field != "kind"]
    message_at = output_fields.index("message_id") + 1
    for field in reversed(("message_index",)):
        if field not in output_fields:
            output_fields.insert(message_at, field)
    if "notes" not in output_fields:
        output_fields.append("notes")
    for row in rows:
        row["kind"] = family
        row.setdefault("message_index", "")
        row.setdefault("notes", "")
        row["translated"] = ""
    _write_csv_atomic(path, output_fields, rows)
    return len(rows)


def _index_japanese(
    image: Path,
    inventory: Path,
    names: Path,
    target: Path,
) -> None:
    args = SimpleNamespace(
        iso=os.fspath(image),
        manifest=os.fspath(inventory),
        csv=os.fspath(target),
        names=os.fspath(names),
        keep=False,
    )
    vp2_jp_glyphs.cmd_index(args)


def _export_scenes(
    usa_image: Path,
    usa_inventory: Path,
    output: Path,
    english_names: Path,
    japanese_image: Path | None,
    japanese_glyphs: Path | None,
    japanese_names: Path,
) -> tuple[int, int]:
    output.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(
        iso=os.fspath(usa_image),
        csv=os.fspath(output),
        all=True,
        manifest_list=os.fspath(usa_inventory),
        en_names=os.fspath(english_names),
        jp_iso=os.fspath(japanese_image) if japanese_image else None,
        jp_glyphs=os.fspath(japanese_glyphs) if japanese_glyphs else None,
        jp_names=os.fspath(japanese_names),
    )
    scene_sheet_export.cmd_sheet_all(args)
    sheets = lines = 0
    for path in sorted(output.glob("*.csv")):
        count = _normalize_source_sheet(path, "scene")
        if count:
            sheets += 1
            lines += count
    return sheets, lines


def _export_containers(
    usa_image: Path,
    inventory_rows: list[dict[str, str]],
    output: Path,
    japanese_image: Path | None,
    japanese_glyphs: Path | None,
    japanese_names: Path,
) -> tuple[int, int, int]:
    output.mkdir(parents=True, exist_ok=True)
    sheets = lines = skipped = 0
    for inventory in inventory_rows:
        if inventory["classification"] not in CONTAINER_CLASSES:
            continue
        resource = int(inventory["index"])
        target = output / f"container-{resource:04d}.csv"
        args = SimpleNamespace(
            iso=os.fspath(usa_image),
            csv=os.fspath(target),
            resource=resource,
            jp_iso=os.fspath(japanese_image) if japanese_image else None,
            jp_glyphs=os.fspath(japanese_glyphs) if japanese_glyphs else None,
            jp_names=os.fspath(japanese_names),
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                vp2_container_text.cmd_export(args)
            count = _normalize_source_sheet(target, "container")
        except (OSError, ValueError, KeyError, IndexError, struct.error):
            if target.exists():
                target.unlink()
            skipped += 1
            continue
        if count:
            sheets += 1
            lines += count
    return sheets, lines, skipped


CONTAINER_SUBRESOURCES = {31: 10}

def _export_container_subresources(
    usa_image: Path,
    output: Path,
    japanese_image: Path | None,
    japanese_glyphs: Path | None,
    japanese_names: Path,
) -> tuple[int, int]:
    """Export container banks the inventory's classification does not reach."""
    output.mkdir(parents=True, exist_ok=True)
    sheets = lines = 0
    for resource, subresource in sorted(CONTAINER_SUBRESOURCES.items()):
        target = output / f"container-{resource:04d}.csv"
        args = SimpleNamespace(
            iso=os.fspath(usa_image),
            csv=os.fspath(target),
            resource=resource,
            subresource=subresource,
            jp_iso=os.fspath(japanese_image) if japanese_image else None,
            jp_glyphs=os.fspath(japanese_glyphs) if japanese_glyphs else None,
            jp_names=os.fspath(japanese_names),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            vp2_container_text.cmd_export(args)
        count = _normalize_source_sheet(target, "container")
        if count:
            sheets += 1
            lines += count
    return sheets, lines


def _export_dragon_hall_prompts(
    usa_image: Path,
    output: Path,
    japanese_image: Path | None,
) -> tuple[int, int]:
    """Expose the one fixed SPDDragonHall prompt as container-style rows."""
    output.mkdir(parents=True, exist_ok=True)
    fields = [
        "kind", "resource", "message_id", "message_index", "record_kind",
        "offset", "byte_length", "original_en", "original_jp", "translated",
        "notes",
    ]
    sheets = lines = 0
    with usa_image.open("rb") as usa_handle:
        _game, usa_total, usa_table = triace.load_table(usa_handle)
        if japanese_image is not None:
            jp_handle = japanese_image.open("rb")
            _jp_game, jp_total, jp_table = triace.load_table(jp_handle)
        else:
            jp_handle = None
            jp_total = jp_table = None
        try:
            for resource in vp2_dragon_hall.RESOURCES:
                usa_raw = bytes(dcms.read_entry(
                    usa_handle, usa_table, usa_total, resource))
                jp_raw = (bytes(dcms.read_entry(
                    jp_handle, jp_table, jp_total, resource))
                    if jp_handle is not None else None)
                rows = vp2_dragon_hall.source_rows(resource, usa_raw, jp_raw)
                _write_csv_atomic(
                    output / f"container-{resource:04d}.csv", fields, rows)
                sheets += 1
                lines += len(rows)
        finally:
            if jp_handle is not None:
                jp_handle.close()
    return sheets, lines


def _export_chapters(
    usa_image: Path,
    records_path: Path,
    output: Path,
    japanese_image: Path | None = None,
    japanese_glyphs: Path | None = None,
    japanese_names: Path | None = None,
) -> int:
    """Decode chapter display-face records named by structural configuration."""
    fields, records = _read_csv(records_path)
    required = {"chapter", "resource", "message_id"}
    if japanese_image is not None:
        required.add("japanese_message_id")
    if not required.issubset(fields):
        raise PackError(
            f"{records_path}: missing chapter field(s): "
            f"{', '.join(sorted(required - set(fields)))}")
    rows = []
    japanese_by_resource: dict[int, dict[str, str]] = {}
    with contextlib.ExitStack() as stack:
        handle = stack.enter_context(usa_image.open("rb"))
        _game, total, table = triace.load_table(handle)
        iso = vp2_title_face.FileIsoForTitleFace(handle, table, total)
        face, _sources = vp2_title_face.build_face(iso)
        if japanese_image is not None:
            japanese_handle = stack.enter_context(japanese_image.open("rb"))
            _jp_game, japanese_total, japanese_table = triace.load_table(
                japanese_handle)
            japanese_characters = vp2_jp_glyphs.load_glyph_names(
                os.fspath(japanese_glyphs) if japanese_glyphs else None,
                os.fspath(japanese_names) if japanese_names else None,
            )
        for record in records:
            resource = int(record["resource"])
            message_id = int(record["message_id"])
            original_jp = ""
            if japanese_image is not None:
                if resource not in japanese_by_resource:
                    decoded, _slots, _complete = vp2_jp_glyphs.decode_resource(
                        japanese_handle, japanese_table, japanese_total,
                        resource, japanese_characters)
                    japanese_by_resource[resource] = {
                        str(japanese_id): text
                        for _index, japanese_id, _voice, text in decoded
                        if text
                    }
                japanese_message_id = record["japanese_message_id"].strip()
                original_jp = japanese_by_resource[resource].get(
                    japanese_message_id, "")
                if not original_jp:
                    raise PackError(
                        f"{records_path}: chapter {record['chapter']} Japanese "
                        f"message {japanese_message_id or '<blank>'} was not "
                        f"decoded from resource {resource}")
            rows.append({
                "kind": "chapter",
                "chapter": record["chapter"],
                "resource": str(resource),
                "message_id": str(message_id),
                "message_index": "",
                "original_en": vp2_title_face.decode_title(
                    iso, resource, message_id, face=face),
                "original_jp": original_jp,
                "translated": "",
                "notes": "",
            })
    _write_csv_atomic(output, [
        "kind", "chapter", "resource", "message_id", "message_index",
        "original_en", "original_jp", "translated", "notes",
    ], rows)
    return len(rows)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _replace_generated_tree(target: Path, generated: Path) -> None:
    """Atomically promote one complete generated snapshot with rollback."""
    backup = target.with_name("." + target.name + "-previous")
    # Recover the only complete snapshot after an interrupted earlier swap.
    if backup.exists() and not target.exists():
        rename_tree(backup, target)
    elif backup.exists():
        _remove_path(backup)
    if target.exists():
        rename_tree(target, backup)
    try:
        rename_tree(generated, target)
    except BaseException:
        if backup.exists() and not target.exists():
            rename_tree(backup, target)
        raise
    if backup.exists():
        _remove_path(backup)


def resolve_sources(images, workspace):
    """Sort disc images into their roles, remembering earlier ones."""
    found: dict[str, Path] = {}
    for image in images:
        if image is None:
            continue
        try:
            boot, region = disc_identity.identify(image)
        except disc_identity.DiscError as exc:
            raise PackError(str(exc)) from exc
        if region == "europe":
            raise PackError(
                f"{Path(image).name} is {boot}, a European release. Its text "
                f"is already a translation, so it is not a source this can "
                f"read from")
        resolved = Path(image).expanduser().resolve()
        if region in found and found[region] != resolved:
            raise PackError(
                f"two different {region} images were given: "
                f"{found[region]} and {resolved}")
        found[region] = resolved

    remembered = _remembered_sources(workspace)
    for region, path in remembered.items():
        if region in found:
            continue
        if path.is_file():
            print(f"reusing the {region} image from the last run: {path}",
                  flush=True)
            found[region] = path
        else:
            print(f"the {region} image used last time is gone ({path}); "
                  f"its text will be dropped", flush=True)

    if "usa" not in found:
        stamp = (Path(workspace).expanduser().resolve()
                 / "internal" / "generation.json")
        if stamp.is_file() and not remembered:
            raise PackError(
                "this workspace was made before generate recorded which "
                "images it read, so the USA image it used cannot be found "
                "again. Pass both images once -- `generate <usa> <japanese>` "
                "-- and later runs will remember them")
        raise PackError(
            "no USA image: it is the release everything is built from, and "
            "nothing here can be generated without it")
    return found["usa"], found.get("japan")


def _remembered_sources(workspace) -> dict[str, Path]:
    """Disc images a previous run of ``generate`` recorded, if any."""
    stamp = Path(workspace).expanduser().resolve() / "internal" / "generation.json"
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8")).get("sources")
    except (OSError, ValueError):
        return {}
    if not isinstance(recorded, dict):
        return {}
    return {region: Path(path) for region, path in recorded.items()
            if isinstance(path, str)}


def generate_workspace(
    images,
    workspace: str | os.PathLike[str],
    *,
    japanese_image: str | os.PathLike[str] | None = None,
    data_root: str | os.PathLike[str] | None = None,
) -> dict[str, int | bool]:
    """Generate local reference and internal state with rollback on error."""
    if isinstance(images, (str, os.PathLike)):
        images = [images]
    usa, japanese = resolve_sources(tuple(images) + (japanese_image,),
                                   workspace)
    root = Path(workspace).expanduser().resolve()
    public_data = (Path(data_root).resolve() if data_root else
                   Path(__file__).resolve().parents[2] / "data")
    english_names = public_data / "glyph-names" / "en.csv"
    japanese_names = public_data / "glyph-names" / "jp.csv"
    chapter_records = public_data / "chapter-records.csv"
    menu_layout = public_data / "menu-layout.csv"
    for path in (english_names, japanese_names, chapter_records, menu_layout):
        if not path.is_file():
            raise PackError(f"required public data is missing: {path}")

    root.mkdir(parents=True, exist_ok=True)
    staging = root / (".generate-" + uuid.uuid4().hex)
    generated = staging / "internal"
    inventory_dir = generated / "inventory"
    records_dir = generated / "records"
    cache_dir = generated / "cache"
    inventory_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    try:
        usa_inventory = inventory_dir / "usa.csv"
        print("inventory: scanning USA image", flush=True)
        usa_rows = write_inventory(usa, usa_inventory, inspect_text=True)

        japanese_glyphs = None
        if japanese is not None:
            print("inventory: scanning Japanese image", flush=True)
            jp_inventory = inventory_dir / "japanese.csv"
            write_inventory(japanese, jp_inventory, inspect_text=False)
            japanese_glyphs = cache_dir / "japanese-glyphs.csv"
            print("glyphs: indexing Japanese local fonts", flush=True)
            _index_japanese(japanese, jp_inventory, japanese_names,
                            japanese_glyphs)

        print("tables: exporting scene resources", flush=True)
        scene_sheets, scene_lines = _export_scenes(
            usa, usa_inventory, records_dir / "scenes", english_names,
            japanese, japanese_glyphs, japanese_names)
        print("tables: exporting container resources", flush=True)
        container_sheets, container_lines, skipped = _export_containers(
            usa, usa_rows, records_dir / "containers", japanese,
            japanese_glyphs, japanese_names)
        extra_sheets, extra_lines = _export_container_subresources(
            usa, records_dir / "containers", japanese, japanese_glyphs,
            japanese_names)
        container_sheets += extra_sheets
        container_lines += extra_lines
        dragon_sheets, dragon_lines = _export_dragon_hall_prompts(
            usa, records_dir / "containers", japanese)
        container_sheets += dragon_sheets
        container_lines += dragon_lines
        print("tables: exporting chapter titles", flush=True)
        chapter_lines = _export_chapters(
            usa, chapter_records, records_dir / "chapters.csv",
            japanese, japanese_glyphs, japanese_names)

        metadata = {
            "format": 2,
            "records": "records",
            "sources": {region: os.fspath(path) for region, path in
                        (("usa", usa), ("japan", japanese)) if path},
            "usa_bytes": usa.stat().st_size,
            "japanese": japanese is not None,
            "scene_sheets": scene_sheets,
            "scene_lines": scene_lines,
            "container_sheets": container_sheets,
            "container_lines": container_lines,
            "container_candidates_skipped": skipped,
            "chapter_sheets": 1 if chapter_lines else 0,
            "chapter_lines": chapter_lines,
        }
        (generated / "generation.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        print("reference: arranging translator-facing tables", flush=True)
        reference_metadata = write_reference_tree(
            records_dir, menu_layout, staging / "reference")
        metadata.update({
            "reference_rows": (
                reference_metadata["chapter_rows"]
                + reference_metadata["dialogue_rows"]
                + reference_metadata["menu_rows"]),
            "reference_menu_occurrences":
                reference_metadata["menu_occurrences"],
        })
        (generated / "generation.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        _replace_generated_tree(root / "internal", generated)
        _replace_generated_tree(root / "reference", staging / "reference")
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return metadata
