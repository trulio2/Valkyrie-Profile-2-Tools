# Translator guide

The Translate page in ValkyrieProfile2-Tools builds a translated copy of the
USA release of _Valkyrie Profile 2: Silmeria_. It reads the source disc image
and writes a separate ISO; it never changes the source.

## Using the window

Open `ValkyrieProfile2-Tools` from a downloaded release, or run this from
a source checkout:

```bash
python vp2_tools.py
```

Then:

1. Select a clean USA image (`SLUS_214.52`).
2. Optionally select the Japanese image (`SLPM_664.19`). This adds Japanese
   text to the generated reference files but is not required for a build.
3. Select a language and output folder.
4. Choose **Build translated ISO**.

The first build generates a local workspace from the source image and may take
several minutes. Later builds reuse that workspace.

A translated ISO has the game's anti-cheat turned off, the same change as the
cheat patcher's **Disable Anti-Cheat Systems**: the translation changes game
code the anti-cheat would otherwise catch. A source image that already has it
turned off is accepted as it is.

## Command line

| Command                                                    | Purpose                                                        |
| ---------------------------------------------------------- | -------------------------------------------------------------- |
| `python vp2_translate.py generate <image>...`              | Prepare the local workspace and reference files.               |
| `python vp2_translate.py build <usa-image.iso> [language]` | Build a translated ISO.                                        |
| `python vp2_translate.py check-pack <language>`            | Validate a language pack.                                      |
| `python vp2_translate.py --self-check`                     | Check that the translation CLI runtime and data are available. |

`generate` is optional because `build` runs it automatically when the
workspace has not been prepared. To include both English and Japanese in the
local reference:

```bash
python vp2_translate.py generate <usa-image.iso> <japanese-image.iso>
```

The default language is `pt-BR`. Supply another locale from `translations/`
or a path to a language pack:

```bash
python vp2_translate.py build <usa-image.iso> sv-SE
python vp2_translate.py build <usa-image.iso> <path-to-language-pack>
```

Use `--workspace` with `generate` or `build` to change the workspace location.
Use `--output` with `build` to choose the output file:

```bash
python vp2_translate.py build <usa-image.iso> pt-BR --output <translated.iso>
```

Run any command with `--help` for its complete arguments.

## Checking text before a build

`test.py` patches one resource, or a whole language, in memory and says
whether a build would accept the result. The disc image is opened
read-only and no ISO is produced.

```bash
python test.py pt-BR 1197
python test.py pt-BR --all
```

## Interface artwork

Part of the interface is drawn rather than written -- the settings screen,
labels like `COST` and `OTH`, and the battle banners such as `Hit`, `Miss`
and `Direct Assault`. Those are pictures, so no CSV row reaches them. A pack
translates them by carrying repainted copies in its own `images/` directory,
named by a row in its `build-profile.csv`:

```csv
"image","1781","images","","",""
```

## Language packs

Language packs live under `translations/<locale>/`. Each pack decides which
resources its build changes, so small packs can build much faster than a full
translation.

For the file layout and validation rules, see
[translation-format.md](translation-format.md). For contributing translations
or a new language, see [CONTRIBUTING.md](CONTRIBUTING.md).
