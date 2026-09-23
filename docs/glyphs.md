# Glyph tool

The Glyphs page in ValkyrieProfile2-Tools lets PCSX2 draw the game's text with
high-resolution letters. It never modifies the source image and does not
include game art: the glyphs are extracted from your own disc.

The steps run in this order, and the same patched image is used for all of
them and for playing.

## 1. Patch ISO

Select the USA image (`SLUS_214.52`) or a translated build of it, choose an
output folder, and select **Patch ISO**. The copy is named
`<image>-glyphs.iso`. The patch draws each letter as its own texture, which
changes nothing on screen, and turns the game's anti-cheat off when it is on;
without that, the game freezes. An image that already has both is refused,
since it can be used as it is.

A translated build made by this project may already have part of it; the
patch adds only what is missing.

```bash
python vp2_glyphs.py patch <image.iso> -o <folder>
```

## 2. Extract Glyphs

Select the patched image and an output folder. A folder named
`<image>-glyphs` is created inside it, holding one `glyph-<hash>.png` per
glyph -- a white letter over a black outline -- and `glyphs-sheet.png`, all of
them on one page.

```bash
python vp2_glyphs.py extract <patched-image.iso> -o <folder>
```

## 3. Edit

Redraw or upscale the PNGs in any editor, keeping each file's name. A glyph
must stay square, at any size. Transparency is the outline and brightness is
the letter, so keep the letter white and the outline black.

## 4. Create DDS

Select the folder of edited PNGs and an output folder. A folder named
`<png folder>-dds` is created, holding three textures per glyph, one for each
way the game draws text. Other files in the PNG folder are ignored, and a
glyph without a PNG keeps its original look.

```bash
python vp2_glyphs.py dds <glyph-png-folder> -o <folder>
```

Copy the folder into PCSX2's `textures/SLUS-21452/replacements` folder and
turn on **Load Textures** in the graphics settings.

Glyph names depend only on the letter's pixels, so a finished set keeps
working across rebuilds of the same translation. A translation that adds new
letters needs those glyphs extracted again.
