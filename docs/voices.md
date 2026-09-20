# Voice tool

The Voices page in ValkyrieProfile2-Tools extracts cutscene, battle, and other
voice audio from a USA or Japanese _Valkyrie Profile 2_ image, and can put
replacement WAVs back into a new image. It never modifies the source image and
does not include game audio.

## Extract voices

In the window, select the USA (`SLUS_214.52`) or Japanese (`SLPM_664.19`)
image, choose a voice root, and select **Extract every voice**. From a source
checkout, the equivalent command is:

```bash
python vp2_voices.py extract <source.iso>
python vp2_voices.py extract <source.iso> -o <voice-root>
```

The default root is `voices/`. USA audio goes under `voices/en/`; Japanese
audio goes under `voices/jp/`. Extraction refuses to merge with an existing
`en/` or `jp/` folder, so a partial or older extraction cannot silently mix
with a new one.

Known cutscene banks are grouped by the scene resource number. The opening,
for example, includes files under:

```text
voices/en/1197/1483-000-8028.wav
voices/en/1197/1485-000-....wav
```

Each name is `<bank>-<subfile>-<clip-id>.wav`. Keep it unchanged: it
identifies the game slot the file is patched back into. The adjacent
`manifest.csv` records the region, resource, slot limit, duration, loudness
and hash.

Audio without a cutscene or text-line folder goes under `unmapped/`, and
battle files go under `battle/` with names such as:

```text
voices/en/battle/battle-2189-000-1c49-0.wav
```

The name is `battle-<entry>-<sample>-<clip-id>-<zone>.wav`. Keep all five
parts unchanged so the file can be patched back into its original slot.
Alternate performances are kept under a scene's `alternate-takes/`, or under
`unmapped/alternate-takes/` when their owner is unknown.

Audio stored inside a scene resource rather than a voice bank goes under
`field/` (the field action calls) and `lezard/` (the background speech in the
last two Tower of Lezard areas). Their names are
`field-<entry>-<group>-<sample>-<clip-id>-<zone>.wav` and
`lezard-<entry>-<group>-<sample>-<clip-id>-<zone>.wav`; keep every part for
patching.

## Patch replacement voices

Select the base USA or Japanese image, select any folder containing identified
replacement WAVs, choose the ISO output folder, and select **Patch ISO**. The
folder is searched recursively, so it may be a whole language extraction, one
cutscene folder, or a hand-picked subset.

```bash
python vp2_voices.py patch <base.iso> <replacement-folder>
python vp2_voices.py patch <base.iso> <replacement-folder> -o <output.iso>
```

The default output is `build/<source-name>-voice-patched.iso`. A translated or
cheat-patched USA image can be the base, so subtitles, cheats, and voices can
be combined.

Replacement WAVs must be uncompressed 16-bit, mono PCM at 24000 Hz. A line
must fit its original slot. Overlong audio is rejected by default; shorter
audio is padded with the game's silence frame. A deliberate rough test can
enable **Allow overlong WAVs** in the window or pass `--allow-overlong`,
which trims each tail exactly at the fixed slot boundary.

Battle replacements follow the same fixed-slot duration rule as cutscene
replacements. Several replacement files from one entry may be patched in the
same build.

Replacement audio must be made for the selected base release. To put
Japanese audio into another release, use the Japanese-audio import below
rather than **Allow overlong WAVs**.

## Import all Japanese audio

Open the **Japanese Audio / Undub** tab to make a Japanese-audio copy without
extracting WAV files. Select a supported target image, the original Japanese
image, and an output folder, then choose **Create Japanese-audio ISO**.

Supported targets are:

- USA (`SLUS_214.52`)
- Europe/Australia (`SLES_546.44`)
- France (`SLES_546.45`)
- Germany (`SLES_546.46`)
- Italy (`SLES_546.47`)
- Spain (`SLES_546.48`)

The Japanese donor must be `SLPM_664.19`. The equivalent command is:

```bash
python vp2_voices.py import-japanese <target.iso> <japan.iso>
python vp2_voices.py import-japanese <target.iso> <japan.iso> -o <output.iso>
```

The result keeps the target release's text and menus with Japanese audio.
It does not use extracted WAV files. Both sources are only read, and the
new image is verified before the tool reports success.

The older dubbing-kit layout is supported too. A folder such as
`vp2/dub/opening/ptbr-chatterbox` may contain files named only by clip ID,
such as `8028.wav`, when its parent kit has the original `manifest.csv` with
the `bank` and `sub` columns.

## Safety and limits

- Keep the exported file names or the older kit manifest with the audio.
- Do not select a kit root containing English, Japanese, and generated copies
  of the same line together; duplicate targets are rejected.
- Existing ISO outputs are never overwritten by the command line. The window
  asks before replacing an output ISO.
- Existing extraction folders are never overwritten automatically.
- Fixed-slot replacement does not enlarge a line, so generated delivery must
  fit the recorded slot. Use the separate Japanese-audio import for a complete
  Japanese-audio conversion of any supported target.
- Keep every file's exported name; it is its patch identity. A background line
  whose scene owner is known is placed in that scene's numbered folder but
  keeps its `unmapped-` name. Files with no known owner stay in `unmapped/`.
