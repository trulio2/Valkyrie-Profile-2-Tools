# Translation-pack format

A language pack mirrors the translator reference without publishing the game's
source script.

```text
translations/<locale>/
  pack.toml
  build-profile.csv
  shared-font-slots.csv
  chapter.csv
  dialogue/
    scene-XXXX.csv
    container-0010.csv
  menu/
    menu-1.csv
    menu-2.csv
    menu-3.csv
    menu-4.csv
    menu-5.csv
```

`pack.toml` declares `format = 2`, a BCP 47 `locale`, and a display `name`.
Every translation CSV has exactly these columns:

```csv
resource,message_id,translated,notes
```

- `resource` and `message_id` are generated stable identities. Do not edit
  them.
- `translated` is the authored target text. It may be blank.
- `notes` contains only contributor-authored information safe to publish.

The path supplies the record family, so `kind` is not repeated in every row.
No source text, source hash, or extraction detail belongs in a translation
CSV. Complete blank rows are intentional: they make each language tree line up
with the local reference and expose untranslated coverage without copying
source text.

## Build profile

`build-profile.csv` lists the resources this language's build writes, one row
each. Its `kind` is `scene`, `container`, or `fontless`. A build only touches
what this file names, so a pack translating one menu lists one resource and
finishes in seconds.

A resource may hold more than one bank of text. Where it does, the row's
`subresource` column names the one it means, and a row that leaves it blank
takes the resource's only bank. The end-credits roll is the one that needs
it today.

## Shared-font slots

Dialogue draws from a scene's own font, and the build cuts whatever glyphs a
scene needs. Menus, system messages and the map screen draw from one font
shared by the whole game, which has a fixed number of spare slots.
`shared-font-slots.csv` says which slot holds each of this language's
characters:

```csv
character,token
å,0x3C
ä,0x3D
```

One row per character, one code point in `character`, and a `token` no two
rows share. Only characters the pack's text actually uses are installed, so
listing one costs nothing until it is written.

A build reads its own pack's file, so two languages may put different letters
in the same slot. A pack without the file uses the packaged default.

## Menu units

Menu text is highly duplicated, and identical English labels can require
different translations in different contexts. The pack's menu files use one
row per distinct unit, and the builder expands that translation to every
matching record.

## Validation

`check-pack` rejects English or Japanese source columns, extra or missing CSV
columns, paths outside the pack's `chapter.csv`, `dialogue/`, and `menu/`
files, dialogue filenames whose resource disagrees with their rows, and
duplicate stable identities. `build-profile.csv` and `shared-font-slots.csv`
are configuration rather than translation, and are not checked against the
translation columns.

A translated build only works on the supported USA game revision; the build
itself checks compatibility.
