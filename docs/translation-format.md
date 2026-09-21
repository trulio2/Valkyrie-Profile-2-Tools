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
  images/
  fis-image-layouts.json
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
each. Its `kind` is `scene`, `container`, `fontless`, `image`, `chapter-label`,
or `misc`. A build only touches what this file names, so a pack translating one
menu lists one resource and finishes in seconds.

A chapter title is written with its scene's row. The word above every chapter
number is not: it needs one `chapter-label` row for each resource that draws
it, naming `chapter.csv`. `misc.csv` needs a `misc` row naming resource 1781.
Leave these rows out to test a few scenes quickly:

```csv
"chapter-label","60","chapter.csv","","",""
"misc","1781","misc.csv","","",""
```

The chapter-label resources are 60, 1196, 1200, 1202, 1212, 1282, 1298, 1302,
1304, 1312 and 1356.

An `image` row is the one that does not name a CSV: parts of the interface are
drawn rather than written, and its `sheet` column names the pack directory
holding repainted copies of them, `images/` by default. See
[translator.md](translator.md) for how to get a picture out and put one
back.

```json
{
  "version": 1,
  "images": {
    "fis-1721-decrypted-slz-0x0-80.png": {
      "mode": "canvas",
      "name": "Battle victory labels",
      "size": [512, 256],
      "dtt": {
        "records": [
          {
            "index": 10,
            "name": "Nível",
            "from": [342, 154, 389, 193],
            "to": [338, 154, 397, 193]
          }
        ]
      }
    }
  }
}
```

A resource may hold more than one bank of text. Where it does, the row's
`subresource` column names the one it means, and a row that leaves it blank
takes the resource's only bank. The end-credits roll is the one that needs
it today.

## Misc labels

`misc.csv` holds text that is not a message record, one `key` per row. The
columns are `key`, `translated`, `offset_x` and `notes`. `offset_x` is this
row's horizontal nudge in screen pixels, negative to the left and positive to
the right; left blank, the label keeps its own position. Whole numbers and
quarters are accepted where a label reads it.

- `battle_target` is the word over the selected enemy in battle, at most eight
  unaccented letters. Left blank, the word is centred over the arrow below it
  the way `Target` is; an `offset_x` of `0` starts it where `Target` starts,
  and any other value nudges the centred position.
- `battle_status_00` through `battle_status_10` are the short battle labels
  (status effects and battle events) drawn by the battle overlay, in the same
  face as `battle_target`.

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

## Menu units

Menu text is highly duplicated, and identical English labels can require
different translations in different contexts. The pack's menu files use one
row per distinct unit, and the builder expands that translation to every
matching record.
