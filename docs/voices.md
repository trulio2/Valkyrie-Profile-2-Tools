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

## Patch replacement voices

Select the base USA or Japanese image, select any folder containing identified
replacement WAVs, choose the ISO output folder, and select **Patch ISO**. The
folder is searched recursively, so it may be a whole language extraction, one
cutscene folder, or a hand-picked subset.

```bash
python vp2_voices.py patch <base.iso> <replacement-folder>
python vp2_voices.py patch <base.iso> <replacement-folder> -o <output.iso>
```

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
