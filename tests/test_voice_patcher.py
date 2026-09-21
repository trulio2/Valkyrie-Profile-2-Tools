# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Synthetic, source-free round trips for the voice extractor/patcher."""

import csv
import io
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.voice_patcher import audio, build, gui, layout, mapping, movie  # noqa: E402


TABLE_OFFSET = 0x200000
TOTAL = 0x0C00
SEED = 0x49287491
SIGNATURE = 0x516F6699
MASK = 0xFFFFFFFF
BANK = 1483
BANK_OFFSET = 0x210800
BANK_LENGTH = 0x1000
CLIP_ID = 0x8028
UNMAPPED_ENTRY = 685
UNMAPPED_OFFSET = 0x210000
UNMAPPED_LENGTH = 0x800
UNMAPPED_CLIP_ID = 0x0B00
BATTLE_ENTRY = 2138
BATTLE_OFFSET = 0x211800
BATTLE_LENGTH = 0x800
BATTLE_SIGNATURE = 0x5D63FC57
BATTLE_SEED = 0x0006107D
FIELD_ENTRY = 24
FIELD_OFFSET = 0x212000
TAIL_ENTRY = 1011
TAIL_OFFSET = 0x220000
MOVIE_ENTRY = 11
MOVIE_OFFSET = 0x230000


def synthetic_bank():
    data = bytearray(BANK_LENGTH)
    struct.pack_into("<IHH", data, 0, 1, 1, 1)
    sub = 0x800
    data[sub:sub + 4] = b"SEQW"
    struct.pack_into("<I", data, sub + 4, 0x40)
    struct.pack_into("<H", data, sub + 0x1A, CLIP_ID)
    data[sub + 0x40:sub + 0x44] = b"WAV "
    struct.pack_into("<III", data, sub + 0x44, 0x60, 0x20, 0)
    struct.pack_into("<I", data, sub + 0x50, 64)
    data[sub + 0x60:sub + 0xA0] = audio.SILENCE_FRAME * 4
    data[sub + 0x91] = 1
    return bytes(data)


def synthetic_unmapped_entry(tail_flag=7):
    data = bytearray(UNMAPPED_LENGTH)
    data[:4] = b"SEQW"
    struct.pack_into("<I", data, 4, 0x80)
    data[0x80:0x84] = b"WAV "
    struct.pack_into("<III", data, 0x88, 0x80, 0x1C, 64)
    struct.pack_into("<HH", data, 0xA0, 0x1C, UNMAPPED_CLIP_ID)
    struct.pack_into("<I", data, 0xA4, 0x14)
    struct.pack_into("<I", data, 0xB4, 0)
    data[0x100:0x140] = audio.SILENCE_FRAME * 4
    if tail_flag == 3:
        data[0x111] = 2
        data[0x121] = 6
    data[0x131] = tail_flag
    return bytes(data)


def synthetic_battle_entry():
    clear = synthetic_unmapped_entry()
    stored = bytearray(len(clear))
    state = BATTLE_SEED
    for offset in range(0, len(clear), 4):
        state = (state * 0x000323BD + 0x000075BB) & MASK
        word = struct.unpack_from("<I", clear, offset)[0] ^ state
        struct.pack_into("<I", stored, offset, word)
    assert struct.unpack_from("<I", stored)[0] == BATTLE_SIGNATURE
    return bytes(stored)


def synthetic_streamed_scene(tail_flag, extra_tail_sectors=0,
                             indexed_marker=b"TARGET",
                             clip_ids=(0x0A89, 0x0A8A)):
    data = bytearray(layout.SECTOR)
    struct.pack_into("<III", data, 0, 0, 1, 0x20)
    data[0x10:0x14] = b"PAM\0"
    struct.pack_into("<III", data, 0x14, 0, 0x10, 0x30)
    data[0x20:0x24] = b"SLZ\2"
    struct.pack_into("<III", data, 0x24, 0, 0x10, 0)
    data[0x30:0x30 + len(indexed_marker)] = indexed_marker
    tail = bytearray(0x120)
    for clip_id in clip_ids:
        entry = bytearray(synthetic_unmapped_entry(tail_flag))
        struct.pack_into("<H", entry, 0xA2, clip_id)
        tail.extend(entry)
    tail.extend(bytes(extra_tail_sectors * layout.SECTOR))
    data.extend(tail)
    data.extend(bytes(-len(data) % layout.SECTOR))
    return bytes(data)


def synthetic_indexed_audio_scene(tail_flag, extra_audio_sectors=0,
                                  indexed_marker=b"TARGET HUD",
                                  extra_indexed_rows=0):
    audio_rows = []
    for clip_id in (0x0A80, 0x0A83):
        entry = bytearray(synthetic_unmapped_entry(tail_flag))
        struct.pack_into("<H", entry, 0xA2, clip_id)
        entry.extend(bytes(extra_audio_sectors * layout.SECTOR))
        audio_rows.append(bytes(entry))
    leading = bytearray(16 + len(indexed_marker))
    leading[16:] = indexed_marker
    rows = [(b"MINA", leading)]
    rows.extend(
        (b"SYS\0", ("HUD ROW %d" % number).encode("ascii"))
        for number in range(extra_indexed_rows)
    )
    rows.extend(((b"ESM\0", audio_rows[0]),
                 (b"ESM\0", audio_rows[1])))
    count = len(rows)
    table_end = 0x10 + count * 16
    data = bytearray(table_end)
    struct.pack_into("<III", data, 0, 0, count, table_end)
    for number, (tag, payload) in enumerate(rows):
        position = 0x10 + number * 16
        offset = len(data)
        data[position:position + 4] = tag
        struct.pack_into("<III", data, position + 4, 0, len(payload), offset)
        data.extend(payload)
        if number == 0:
            data[offset:offset + 16] = data[position:position + 16]
    data.extend(bytes(-len(data) % layout.SECTOR))
    return bytes(data)


def synthetic_movie_stream():
    def pack():
        return b"\x00\x00\x01\xba" + bytes(10)

    def private(codec, payload):
        body = b"\x80\x00\x00\xff\x90\x00" + bytes((codec,)) + payload
        return (b"\x00\x00\x01\xbd" + len(body).to_bytes(2, "big") +
                body)

    def video(payload):
        body = b"\x80\x00\x00" + payload
        return (b"\x00\x00\x01\xe0" + len(body).to_bytes(2, "big") +
                body)

    pcm = struct.pack("<8h", *range(-4, 4))
    tac = bytearray(0x40)
    struct.pack_into("<I", tac, 0x00, 0x20)
    struct.pack_into("<HH", tac, 0x0C, 1, 99)
    struct.pack_into("<I", tac, 0x14, 0x4E000)
    clear = (
        pack() + video(b"\x00\x00\x01\xb3FIRST") +
        private(movie.PCM_CODEC, pcm[:8]) +
        private(movie.PCM_CODEC, pcm[8:]) +
        video(b"LAST\x00\x00\x01\xb7") +
        pack() + video(b"\x00\x00\x01\xb3SECOND") +
        private(movie.TAC_CODEC, bytes(tac)) +
        video(b"DONE\x00\x00\x01\xb7") +
        b"\x00\x00\x01\xb9"
    )
    clear += bytes(-len(clear) % layout.SECTOR)
    return movie.xor_bytes(clear, movie.load_xor_pad()), pcm, bytes(tac)


def synthetic_disc_movie_stream():
    data = bytearray(movie.DISC_UNIT * 5)
    data[:14] = b"\x00\x00\x01\xba" + bytes(10)
    pcm = struct.pack("<8h", *range(-4, 4))

    pcm_base = movie.DISC_UNIT
    data[pcm_base + movie.DISC_AUDIO_MARKER_OFFSET:
         pcm_base + movie.DISC_AUDIO_MARKER_OFFSET + 2] = (
        movie.DISC_AUDIO_MARKER
    )
    data[pcm_base + movie.DISC_WRAPPER_OFFSET:
         pcm_base + movie.DISC_WRAPPER_OFFSET + 5] = (
        movie.DISC_WRAPPER + bytes((movie.PCM_CODEC,))
    )
    data[pcm_base + movie.DISC_DATA_OFFSET:
         pcm_base + movie.DISC_DATA_OFFSET + len(pcm)] = pcm
    data[pcm_base + movie.DISC_UNIT:
         pcm_base + movie.DISC_UNIT + 4] = b"TAIL"

    tac = bytearray(0x40)
    struct.pack_into("<I", tac, 0x00, 0x20)
    struct.pack_into("<HH", tac, 0x0C, 1, 99)
    struct.pack_into("<I", tac, 0x14, 0x4E000)
    tac_base = movie.DISC_UNIT * 3
    data[tac_base + movie.DISC_AUDIO_MARKER_OFFSET:
         tac_base + movie.DISC_AUDIO_MARKER_OFFSET + 2] = (
        movie.DISC_AUDIO_MARKER
    )
    data[tac_base + movie.DISC_WRAPPER_OFFSET:
         tac_base + movie.DISC_WRAPPER_OFFSET + 5] = (
        movie.DISC_WRAPPER + bytes((movie.TAC_CODEC,))
    )
    data[tac_base + movie.DISC_DATA_OFFSET:
         tac_base + movie.DISC_DATA_OFFSET + len(tac)] = tac
    return bytes(data), pcm, bytes(tac)


def encrypted_index(include_battle=False, scenes=None):
    decoded = [0] * (TOTAL * 3)
    decoded[BANK] = BANK_OFFSET // layout.SECTOR
    decoded[TOTAL + BANK] = BANK_LENGTH // layout.SECTOR
    decoded[UNMAPPED_ENTRY] = UNMAPPED_OFFSET // layout.SECTOR
    decoded[TOTAL + UNMAPPED_ENTRY] = UNMAPPED_LENGTH // layout.SECTOR
    if include_battle:
        decoded[BATTLE_ENTRY] = BATTLE_OFFSET // layout.SECTOR
        decoded[TOTAL + BATTLE_ENTRY] = BATTLE_LENGTH // layout.SECTOR
    for entry, (offset, payload) in (scenes or {}).items():
        decoded[entry] = offset // layout.SECTOR
        decoded[TOTAL + entry] = len(payload) // layout.SECTOR
    raw = decoded[:]
    key = SEED
    for index in range(TOTAL):
        raw[index] ^= key
        key = (key ^ ((key << 1) & MASK)) & MASK
        raw[TOTAL + index] ^= key
        key = (key ^ (~SEED & MASK)) & MASK
        raw[2 * TOTAL + index] ^= key
        key = (key ^ ((key << 2) & MASK) ^ SEED) & MASK
    raw[0] = SIGNATURE
    return struct.pack("<%dI" % len(raw), *raw)


def synthetic_iso(path, boot="SLUS_214.52", battle=False, scenes=None):
    spans = [BANK_OFFSET + BANK_LENGTH, UNMAPPED_OFFSET + UNMAPPED_LENGTH]
    if battle:
        spans.append(BATTLE_OFFSET + BATTLE_LENGTH)
    spans.extend(offset + len(payload)
                 for offset, payload in (scenes or {}).values())
    image = bytearray(max(spans))
    image[0x1000:0x1000 + len(boot)] = boot.encode("ascii")
    index = encrypted_index(battle, scenes)
    image[TABLE_OFFSET:TABLE_OFFSET + len(index)] = index
    image[BANK_OFFSET:BANK_OFFSET + BANK_LENGTH] = synthetic_bank()
    image[UNMAPPED_OFFSET:UNMAPPED_OFFSET + UNMAPPED_LENGTH] = (
        synthetic_unmapped_entry()
    )
    if battle:
        image[BATTLE_OFFSET:BATTLE_OFFSET + BATTLE_LENGTH] = (
            synthetic_battle_entry()
        )
    for offset, payload in (scenes or {}).values():
        image[offset:offset + len(payload)] = payload
    path.write_bytes(image)


def write_wav(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(audio.SAMPLE_RATE)
        output.writeframes(struct.pack("<%dh" % len(samples), *samples))


class MovieAudioTests(unittest.TestCase):
    def test_demuxes_japanese_leading_disc_pes_packet(self):
        tac = bytearray(0x40)
        struct.pack_into("<I", tac, 0x00, 0x20)
        struct.pack_into("<HH", tac, 0x0C, 1, 99)
        struct.pack_into("<I", tac, 0x14, 0x4E000)
        body = (
            b"\x83\x80\x0a" + bytes(10) +
            movie.DISC_PES_WRAPPER + bytes((movie.TAC_CODEC,)) + tac
        )
        clear = (
            b"\x00\x00\x01" + bytes((movie.DISC_PES_STREAM,)) +
            len(body).to_bytes(2, "big") + body
        )

        tracks = movie.parse_audio_tracks(clear)

        self.assertEqual(1, len(tracks))
        self.assertEqual(movie.TAC_CODEC, tracks[0].codec)
        self.assertEqual(bytes(tac), tracks[0].data)

    def test_demuxes_real_disc_units_and_preserves_cross_unit_tail(self):
        clear, pcm, tac = synthetic_disc_movie_stream()
        tracks = movie.parse_audio_tracks(clear)

        self.assertEqual(
            [(0, movie.PCM_CODEC), (1, movie.TAC_CODEC)],
            [(track.movie, track.codec) for track in tracks],
        )
        self.assertEqual(pcm, tracks[0].data[:len(pcm)])
        self.assertEqual(
            movie.DISC_UNIT - movie.DISC_DATA_OFFSET +
            movie.DISC_NEXT_HEADER_OFFSET,
            tracks[0].capacity,
        )
        tail = movie.DISC_UNIT - movie.DISC_DATA_OFFSET
        self.assertEqual(b"TAIL", tracks[0].data[tail:tail + 4])
        self.assertEqual(tac, tracks[1].data[:len(tac)])
        self.assertEqual(100, movie.tac_info(tracks[1].data).samples)

    def test_demuxes_pcm_and_tac_from_concatenated_programs(self):
        stored, pcm, tac = synthetic_movie_stream()
        clear = movie.xor_bytes(stored, movie.load_xor_pad())
        tracks = movie.parse_audio_tracks(clear)

        self.assertEqual(
            [(0, movie.PCM_CODEC), (1, movie.TAC_CODEC)],
            [(track.movie, track.codec) for track in tracks],
        )
        self.assertEqual(pcm, tracks[0].data)
        self.assertEqual(tac, tracks[1].data)
        self.assertEqual(100, movie.tac_info(tracks[1].data).samples)
        self.assertEqual(
            (MOVIE_ENTRY, 0),
            movie.parse_exported_filename("fmv-0011-000.wav"),
        )

    def test_pcm_replacement_only_changes_audio_spans(self):
        stored, _pcm, _tac = synthetic_movie_stream()
        clear = movie.xor_bytes(stored, movie.load_xor_pad())
        track = movie.parse_audio_tracks(clear)[0]
        replacement = struct.pack("<4h", 100, -100, 200, -200)
        rebuilt = movie.replace_pcm_track(clear, track, replacement)
        changed = {
            offset for span in track.spans
            for offset in range(span.offset, span.offset + span.length)
        }
        self.assertTrue(all(
            before == after or offset in changed
            for offset, (before, after) in enumerate(zip(clear, rebuilt))
        ))
        checked = movie.parse_audio_tracks(rebuilt)[0]
        self.assertEqual(
            replacement + bytes(track.capacity - len(replacement)),
            checked.data,
        )
        with self.assertRaisesRegex(ValueError, "packet slots"):
            movie.replace_pcm_track(
                clear, track, bytes(track.capacity + movie.PCM_FRAME_BYTES)
            )

    def test_extracts_movie_candidates_but_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            output = root / "patched.iso"
            stored, pcm, _tac = synthetic_movie_stream()
            synthetic_iso(source, scenes={
                MOVIE_ENTRY: (MOVIE_OFFSET, stored),
            })
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_bank_map", return_value={}), \
                    mock.patch.object(build, "load_unmapped_map", return_value={}):
                result = build.extract_voices(source, root / "voices")
            pcm_path = (
                result.output / "0010" /
                "fmv-0011-000.wav"
            )
            tac_path = (
                result.output / "0010" /
                "fmv-0011-001.laac"
            )
            self.assertTrue(pcm_path.is_file())
            self.assertTrue(tac_path.is_file())
            self.assertEqual(2, result.movie_tracks)
            replacement = struct.pack("<4h", 20, -20, 30, -30)
            movie.write_pcm_wav(pcm_path, replacement)
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_unmapped_map", return_value={}):
                with self.assertRaisesRegex(
                        ValueError, "replacement is disabled"):
                    build.patch_iso(source, result.output, output=output)
            self.assertFalse(output.exists())


class LayoutTests(unittest.TestCase):
    def test_exported_name_carries_every_patch_identity(self):
        clip = layout.parse_bank(synthetic_bank())[0]
        name = layout.exported_filename(BANK, clip)
        self.assertEqual("1483-000-8028.wav", name)
        self.assertEqual((BANK, 0, CLIP_ID), layout.parse_exported_filename(name))

    def test_slack_bank_parser_does_not_depend_on_the_generic_classifier(self):
        clips = layout.parse_bank(synthetic_bank())
        self.assertEqual(1, len(clips))
        self.assertEqual(64, clips[0].payload_length)
        self.assertEqual(1, clips[0].tail_flag)

    def test_all_voice_banks_have_a_valid_placement_category(self):
        owners = layout.load_bank_map()
        self.assertEqual(85, len(owners))
        self.assertEqual(
            (1297, 1170, 7),
            (owners[1482].resource, owners[1482].voice_scene,
             owners[1482].slot_count),
        )
        self.assertEqual(set(layout.VOICE_BANKS), set(owners))
        self.assertEqual(
            (1337, None, 21, "cutscene"),
            (owners[1520].resource, owners[1520].voice_scene,
             owners[1520].slot_count, owners[1520].category),
        )
        self.assertEqual(
            (1323, None, 16, "alternate"),
            (owners[1562].resource, owners[1562].voice_scene,
             owners[1562].slot_count, owners[1562].category),
        )
        self.assertEqual(
            (1337, 402, 2, "cutscene"),
            (owners[1524].resource, owners[1524].voice_scene,
             owners[1524].slot_count, owners[1524].category),
        )

    def test_unmapped_map_and_standalone_sample_identity_are_exact(self):
        voices = layout.load_unmapped_map()
        self.assertEqual(93, len(voices))
        self.assertEqual(
            layout.UnmappedVoice(
                UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0
            ),
            voices[(UNMAPPED_ENTRY, 0)],
        )
        self.assertIn((1582, 14), voices)
        self.assertIn((1621, 21), voices)
        clip = layout.parse_standalone(synthetic_unmapped_entry())[0]
        name = layout.unmapped_filename(UNMAPPED_ENTRY, clip)
        self.assertEqual("unmapped-0685-000-0b00-0.wav", name)
        self.assertEqual(
            (UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0),
            layout.parse_unmapped_filename(name),
        )
        self.assertEqual(64, clip.payload_length)
        self.assertEqual(7, clip.tail_flag)

    def test_review_maps_cover_every_cutscene_and_battle_slot(self):
        cutscenes = mapping.load_cutscene_map()
        groups, battle_slots = mapping.load_battle_groups()
        self.assertEqual(1625, len(cutscenes))
        self.assertEqual(680, len(groups))
        self.assertEqual(4067, len(battle_slots))
        self.assertEqual("Alicia", cutscenes[(1483, 0)]["speaker"])
        self.assertEqual("Freya", cutscenes[(1526, 7)]["speaker"])
        self.assertEqual("Barbarossa", cutscenes[(1520, 0)]["speaker"])
        self.assertEqual("Lezard", cutscenes[(1559, 0)]["speaker"])
        self.assertEqual(
            "background ritual speech",
            cutscenes[(1541, 5)]["context"],
        )

    def test_review_csvs_do_not_change_patch_filenames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [{
                "kind": "cutscene", "relative_path": "1197/1483-000-8028.wav",
                "bank": "1483", "sub": "0", "clip_id": "8028",
                "entry": "", "sample": "", "zone": "",
            }, {
                "kind": "battle",
                "relative_path": "battle/battle-2138-000-1836-0.wav",
                "bank": "", "sub": "", "clip_id": "1836",
                "entry": "2138", "sample": "0", "zone": "0",
            }]
            cutscene_count, battle_count = mapping.write_voice_maps(root, rows)
            self.assertEqual((1, 1), (cutscene_count, battle_count))
            with (root / "1197" / "voice-map.csv").open(
                    encoding="utf-8") as source:
                cutscene = next(csv.DictReader(source))
            with (root / "battle" / "voice-map.csv").open(
                    encoding="utf-8") as source:
                battle = next(csv.DictReader(source))
            self.assertEqual("1483-000-8028.wav", cutscene["file"])
            self.assertEqual("Alicia", cutscene["speaker"])
            self.assertEqual(
                "battle/battle-2138-000-1836-0.wav",
                battle["example_file"],
            )

    def test_battle_transform_and_filename_are_reversible(self):
        stored = synthetic_battle_entry()
        clear, signature = layout.decode_battle_entry(stored)
        self.assertEqual(BATTLE_SIGNATURE, signature)
        self.assertEqual(synthetic_unmapped_entry(), clear)
        self.assertEqual(stored, layout.encode_battle_entry(clear, signature))
        clip = layout.parse_standalone(clear)[0]
        name = layout.battle_filename(BATTLE_ENTRY, clip)
        self.assertEqual("battle-2138-000-0b00-0.wav", name)
        self.assertEqual(
            (BATTLE_ENTRY, 0, UNMAPPED_CLIP_ID, 0),
            layout.parse_battle_filename(name),
        )


class GuiStyleTests(unittest.TestCase):
    @unittest.skipIf(gui.TK_IMPORT_ERROR is not None, "Tkinter unavailable")
    def test_notebook_border_does_not_inherit_clam_light_colors(self):
        root = mock.Mock()
        style = mock.Mock()
        with mock.patch.object(gui.ttk, "Style", return_value=style):
            gui.apply_dark_theme(root)

        notebook = next(
            call.kwargs for call in style.configure.call_args_list
            if call.args == ("TNotebook",)
        )
        self.assertEqual(0, notebook["borderwidth"])
        self.assertEqual("flat", notebook["relief"])
        self.assertEqual(gui.DARK["bg"], notebook["bordercolor"])
        self.assertEqual(gui.DARK["bg"], notebook["lightcolor"])
        self.assertEqual(gui.DARK["bg"], notebook["darkcolor"])

    @unittest.skipIf(gui.TK_IMPORT_ERROR is not None, "Tkinter unavailable")
    def test_checkbox_hover_and_disabled_background_stays_dark(self):
        root = mock.Mock()
        style = mock.Mock()
        with mock.patch.object(gui.ttk, "Style", return_value=style):
            gui.apply_dark_theme(root)

        checkbutton = next(
            call.kwargs for call in style.map.call_args_list
            if call.args == ("Chip.TCheckbutton",)
        )
        self.assertEqual(
            [("disabled", gui.DARK["surface"]),
             ("pressed", gui.DARK["surface"]),
             ("active", gui.DARK["surface"])],
            checkbutton["background"],
        )


class ExtractionTests(unittest.TestCase):
    def test_extracts_to_language_and_known_cutscene_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            synthetic_iso(source)
            owner = layout.BankOwner(BANK, 1197, 10, 1)
            with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={BANK: owner}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            wav = result.output / "1197" / "1483-000-8028.wav"
            self.assertTrue(wav.is_file())
            self.assertEqual("en", result.region)
            self.assertEqual(1, result.clips)
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                rows = list(csv.DictReader(manifest))
            self.assertEqual("1197/1483-000-8028.wav", rows[0]["relative_path"])
            self.assertEqual("10", rows[0]["voice_scene"])

    def test_japanese_disc_uses_jp_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "jp.iso"
            synthetic_iso(source, "SLPM_664.19")
            owner = layout.BankOwner(BANK, 1197, 10, 1)
            with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={BANK: owner}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            self.assertEqual(root / "voices" / "jp", result.output)

    def test_unverified_bank_stays_under_alternate_takes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "jp.iso"
            synthetic_iso(source, "SLPM_664.19")
            owner = layout.BankOwner(
                BANK, None, None, 1, "alternate"
            )
            with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={BANK: owner}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            wav = (result.output / "unmapped" / "alternate-takes" /
                   "1483-000-8028.wav")
            self.assertTrue(wav.is_file())
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                row = next(csv.DictReader(manifest))
            self.assertEqual("alternate", row["kind"])
            self.assertEqual("", row["resource"])

    def test_scene_owned_alternate_bank_goes_under_its_resource(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "jp.iso"
            synthetic_iso(source, "SLPM_664.19")
            owner = layout.BankOwner(
                BANK, 1323, None, 1, "alternate"
            )
            with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={BANK: owner}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            wav = (result.output / "1323" / "alternate-takes" /
                   "1483-000-8028.wav")
            self.assertTrue(wav.is_file())
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                row = next(csv.DictReader(manifest))
            self.assertEqual("alternate", row["kind"])
            self.assertEqual("1323", row["resource"])

    def test_extracts_language_dependent_samples_to_unmapped_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            synthetic_iso(source)
            voice = layout.UnmappedVoice(
                UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0
            )
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={(UNMAPPED_ENTRY, 0): voice}):
                result = build.extract_voices(source, root / "voices")
            target = (result.output / "unmapped" /
                      "unmapped-0685-000-0b00-0.wav")
            self.assertTrue(target.is_file())
            self.assertEqual(1, result.unmapped_clips)
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                rows = list(csv.DictReader(manifest))
            self.assertEqual("unmapped", rows[0]["kind"])

    def test_owned_unmapped_sample_goes_to_its_resource_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            synthetic_iso(source)
            voice = layout.UnmappedVoice(
                UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0, resource=1195
            )
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={(UNMAPPED_ENTRY, 0): voice}):
                result = build.extract_voices(source, root / "voices")
            target = (result.output / "1195" /
                      "unmapped-0685-000-0b00-0.wav")
            self.assertTrue(target.is_file())
            self.assertFalse((result.output / "unmapped" /
                              "unmapped-0685-000-0b00-0.wav").exists())
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                row = next(csv.DictReader(manifest))
            self.assertEqual("1195", row["resource"])

    def test_extracts_encrypted_battle_samples_to_battle_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            synthetic_iso(source, battle=True)
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            target = (result.output / "battle" /
                      "battle-2138-000-0b00-0.wav")
            self.assertTrue(target.is_file())
            self.assertEqual(1, result.battle_clips)
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                rows = list(csv.DictReader(manifest))
            self.assertEqual("battle", rows[0]["kind"])

    def test_extracts_field_and_tower_audio_to_their_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "usa.iso"
            field = synthetic_indexed_audio_scene(tail_flag=7)
            tower = synthetic_streamed_scene(
                tail_flag=7, clip_ids=(0x0A89, 0x0A9C)
            )
            synthetic_iso(source, scenes={
                FIELD_ENTRY: (FIELD_OFFSET, field),
                TAIL_ENTRY: (TAIL_OFFSET, tower),
            })
            with mock.patch.object(build, "VOICE_BANKS", ()), \
                    mock.patch.object(build, "load_bank_map",
                                      return_value={}), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.extract_voices(source, root / "voices")
            self.assertTrue((result.output / "field" /
                             "field-0024-00-000-0a80-0.wav").is_file())
            self.assertTrue((result.output / "field" /
                             "field-0024-01-000-0a83-0.wav").is_file())
            self.assertTrue((result.output / "lezard" /
                             "lezard-1011-00-000-0a89-0.wav").is_file())
            self.assertTrue((result.output / "lezard" /
                             "lezard-1011-01-000-0a9c-0.wav").is_file())
            with (result.output / "manifest.csv").open(
                    encoding="utf-8") as manifest:
                kinds = {row["kind"] for row in csv.DictReader(manifest)}
            self.assertEqual({"field", "lezard"}, kinds)

    def test_field_and_lezard_filenames_round_trip(self):
        field = layout.parse_standalone(
            build._indexed_audio_groups(
                synthetic_indexed_audio_scene(tail_flag=7))[0][4])[0]
        name = layout.field_filename(FIELD_ENTRY, 0, field)
        self.assertEqual("field-0024-00-000-0a80-0.wav", name)
        self.assertEqual(
            (FIELD_ENTRY, 0, 0, 0x0A80, 0),
            layout.parse_field_filename(name),
        )
        tower = layout.parse_standalone(
            build._streamed_audio_groups(
                synthetic_streamed_scene(tail_flag=7))[1][0][1])[0]
        name = layout.lezard_filename(TAIL_ENTRY, 0, tower)
        self.assertEqual("lezard-1011-00-000-0a89-0.wav", name)
        self.assertEqual(
            (TAIL_ENTRY, 0, 0, 0x0A89, 0),
            layout.parse_lezard_filename(name),
        )


class PatchingTests(unittest.TestCase):
    def test_replaces_only_the_payload_and_preserves_iso_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source)
            replacement = voices / "1483-000-8028.wav"
            write_wav(replacement, [1200, -1200] * 28)
            before = source.read_bytes()
            result = build.patch_iso(source, voices, output)
            after = output.read_bytes()
            clip = layout.parse_bank(synthetic_bank())[0]
            start = BANK_OFFSET + clip.sub_offset + clip.payload_offset
            end = start + clip.payload_length
            self.assertEqual(before[:start], after[:start])
            self.assertNotEqual(before[start:end], after[start:end])
            self.assertEqual(before[end:], after[end:])
            self.assertEqual(len(before), len(after))
            self.assertEqual(1, len(result.replacements))
            self.assertEqual(1, after[end - 15])

    def test_replaces_only_one_unmapped_sample_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source)
            replacement = voices / "unmapped-0685-000-0b00-0.wav"
            write_wav(replacement, [900, -900] * 14)
            before = source.read_bytes()
            voice = layout.UnmappedVoice(
                UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0
            )
            with mock.patch.object(
                    build, "load_unmapped_map",
                    return_value={(UNMAPPED_ENTRY, 0): voice}):
                result = build.patch_iso(source, voices, output)
            after = output.read_bytes()
            start = UNMAPPED_OFFSET + 0x100
            end = start + 64
            self.assertEqual(before[:start], after[:start])
            self.assertNotEqual(before[start:end], after[start:end])
            self.assertEqual(before[end:], after[end:])
            self.assertEqual("unmapped", result.replacements[0].kind)
            self.assertEqual(UNMAPPED_ENTRY, result.replacements[0].entry)
            self.assertEqual(7, after[end - 15])

    def test_unmapped_loop_flag_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source)
            with source.open("r+b") as image:
                image.seek(UNMAPPED_OFFSET)
                image.write(synthetic_unmapped_entry(tail_flag=3))
            replacement = voices / "unmapped-0685-000-0b00-0.wav"
            write_wav(replacement, [700, -700] * 14)
            voice = layout.UnmappedVoice(
                UNMAPPED_ENTRY, 0, UNMAPPED_CLIP_ID, 0
            )
            with mock.patch.object(
                    build, "load_unmapped_map",
                    return_value={(UNMAPPED_ENTRY, 0): voice}):
                build.patch_iso(source, voices, output)
            payload = output.read_bytes()[
                UNMAPPED_OFFSET + 0x100:UNMAPPED_OFFSET + 0x140
            ]
            self.assertEqual(
                [0, 2, 6, 3],
                [payload[offset + 1]
                for offset in range(0, len(payload), audio.FRAME)],
            )

    def test_replaces_battle_sample_and_restores_encrypted_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source, battle=True)
            replacement = voices / "battle-2138-000-0b00-0.wav"
            write_wav(replacement, [850, -850] * 14)
            before = source.read_bytes()
            result = build.patch_iso(source, voices, output)
            after = output.read_bytes()
            self.assertEqual(before[:BATTLE_OFFSET], after[:BATTLE_OFFSET])
            self.assertNotEqual(
                before[BATTLE_OFFSET:BATTLE_OFFSET + BATTLE_LENGTH],
                after[BATTLE_OFFSET:BATTLE_OFFSET + BATTLE_LENGTH],
            )
            self.assertEqual(
                before[BATTLE_OFFSET + BATTLE_LENGTH:],
                after[BATTLE_OFFSET + BATTLE_LENGTH:],
            )
            clear, signature = layout.decode_battle_entry(
                after[BATTLE_OFFSET:BATTLE_OFFSET + BATTLE_LENGTH]
            )
            self.assertEqual(BATTLE_SIGNATURE, signature)
            clip = layout.parse_standalone(clear)[0]
            self.assertEqual(7, clear[
                clip.payload_offset + clip.payload_length - 15
            ])
            self.assertEqual("battle", result.replacements[0].kind)

    def test_cutscene_and_battle_replacements_can_share_one_build(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source, battle=True)
            write_wav(voices / "1483-000-8028.wav", [500, -500] * 14)
            write_wav(
                voices / "battle-2138-000-0b00-0.wav", [650, -650] * 14
            )
            result = build.patch_iso(source, voices, output)
            self.assertEqual(
                {"cutscene", "battle"},
                {replacement.kind for replacement in result.replacements},
            )

    def test_replaces_field_sample_in_place(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            field = synthetic_indexed_audio_scene(tail_flag=7)
            synthetic_iso(source, scenes={
                FIELD_ENTRY: (FIELD_OFFSET, field),
            })
            write_wav(
                voices / "field-0024-00-000-0a80-0.wav", [900, -900] * 14
            )
            before = source.read_bytes()
            result = build.patch_iso(source, voices, output)
            after = output.read_bytes()
            groups = build._indexed_audio_groups(field)
            clip = layout.parse_standalone(groups[0][4])[0]
            start = FIELD_OFFSET + groups[0][2] + clip.payload_offset
            end = start + clip.payload_length
            self.assertEqual(before[:start], after[:start])
            self.assertNotEqual(before[start:end], after[start:end])
            self.assertEqual(before[end:], after[end:])
            self.assertEqual("field", result.replacements[0].kind)

    def test_replaces_lezard_sample_in_place(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            tower = synthetic_streamed_scene(
                tail_flag=7, clip_ids=(0x0A89, 0x0A9C)
            )
            synthetic_iso(source, scenes={
                TAIL_ENTRY: (TAIL_OFFSET, tower),
            })
            write_wav(
                voices / "lezard-1011-00-000-0a89-0.wav", [700, -700] * 14
            )
            before = source.read_bytes()
            result = build.patch_iso(source, voices, output)
            after = output.read_bytes()
            position, _payload, clips = build._streamed_audio_groups(tower)[1][0]
            clip = clips[0]
            start = TAIL_OFFSET + position + clip.payload_offset
            end = start + clip.payload_length
            self.assertEqual(before[:start], after[:start])
            self.assertNotEqual(before[start:end], after[start:end])
            self.assertEqual(before[end:], after[end:])
            self.assertEqual("lezard", result.replacements[0].kind)

    def test_legacy_dub_kit_manifest_makes_clip_id_names_reversible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            translated = root / "ptbr-chatterbox"
            translated.mkdir()
            (root / "manifest.csv").write_text(
                "id,bank,sub,slot_bytes\n8028,1483,0,64\n",
                encoding="utf-8",
            )
            write_wav(translated / "8028.wav", [0] * 28)
            found = build.discover_replacements(translated)
            self.assertEqual(
                {(BANK, 0, CLIP_ID): translated / "8028.wav"}, found
            )

    def test_overlong_audio_is_rejected_without_an_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source)
            write_wav(voices / "1483-000-8028.wav", [0] * 1000)
            with self.assertRaisesRegex(ValueError, "game slot"):
                build.patch_iso(source, voices, output)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".partial").exists())

    def test_overlong_audio_can_be_deliberately_trimmed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.iso"
            output = root / "output.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source)
            write_wav(voices / "1483-000-8028.wav", [800] * 1000)
            result = build.patch_iso(
                source, voices, output, allow_overlong=True
            )
            self.assertTrue(output.is_file())
            self.assertTrue(result.replacements[0].truncated)

    def test_pal_release_is_rejected_by_fixed_wav_patching(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "wrong.iso"
            voices = root / "voices"
            voices.mkdir()
            synthetic_iso(source, "SLES_546.44")
            write_wav(voices / "1483-000-8028.wav", [0] * 28)
            with self.assertRaisesRegex(ValueError, "fixed-slot voice patching"):
                build.patch_iso(source, voices, root / "output.iso")


class JapaneseAudioImportTests(unittest.TestCase):
    def test_archive_repack_preserves_entry_order_and_uses_donor_sizes(self):
        total = 4
        base = [0, 100, 102, 104] + [0, 2, 2, 1] + [0, 10, 12, 14]
        donor = [0, 200, 202, 206] + [0, 2, 4, 1] + [0, 20, 22, 26]
        rebuilt, active, end = build._canonical_archive_layout(
            base, donor, total, (2,)
        )
        self.assertEqual((1, 2, 3), active)
        self.assertEqual([100, 102, 106], rebuilt[1:4])
        self.assertEqual([2, 4, 1], rebuilt[total + 1:total + 4])
        self.assertEqual(107, end)

    def test_archive_repack_uses_exact_hybrid_sector_override(self):
        total = 4
        base = [0, 100, 102, 104] + [0, 2, 2, 1] + [0, 10, 12, 14]
        donor = [0, 200, 202, 206] + [0, 2, 4, 1] + [0, 20, 22, 26]
        rebuilt, active, end = build._canonical_archive_layout(
            base, donor, total, (2,), {2: 3}
        )
        self.assertEqual((1, 2, 3), active)
        self.assertEqual([100, 102, 105], rebuilt[1:4])
        self.assertEqual([2, 3, 1], rebuilt[total + 1:total + 4])
        self.assertEqual(106, end)

    def test_indexed_audio_merge_preserves_target_hud_rows(self):
        base = synthetic_indexed_audio_scene(
            tail_flag=7, indexed_marker=b"USA HUD"
        )
        donor = synthetic_indexed_audio_scene(
            tail_flag=3, extra_audio_sectors=1,
            indexed_marker=b"JP HUD",
        )
        rebuilt, groups = build._merge_indexed_audio_groups(base, donor)
        base_entries = build.vp2_dcms.parse_pk1(base)
        donor_entries = build.vp2_dcms.parse_pk1(donor)
        rebuilt_entries = build.vp2_dcms.parse_pk1(rebuilt)

        self.assertEqual(2, groups)
        self.assertEqual(layout.SECTOR * 2, len(rebuilt) - len(base))
        self.assertEqual(
            base[base_entries[0][1]:sum(base_entries[0][1:])],
            rebuilt[
                rebuilt_entries[0][1]:sum(rebuilt_entries[0][1:])
            ],
        )
        self.assertIn(b"USA HUD", rebuilt)
        self.assertNotIn(b"JP HUD", rebuilt)
        for number in (1, 2):
            donor_payload = donor[
                donor_entries[number][1]:sum(donor_entries[number][1:])
            ]
            rebuilt_payload = rebuilt[
                rebuilt_entries[number][1]:sum(rebuilt_entries[number][1:])
            ]
            self.assertEqual(donor_payload, rebuilt_payload)

    def test_indexed_audio_merge_rejects_identity_drift(self):
        base = synthetic_indexed_audio_scene(tail_flag=7)
        donor = bytearray(synthetic_indexed_audio_scene(tail_flag=3))
        group = build._indexed_audio_groups(donor)[1]
        struct.pack_into("<H", donor, group[2] + 0xA2, 0x0B00)
        with self.assertRaisesRegex(ValueError, "structure does not match"):
            build._merge_indexed_audio_groups(base, bytes(donor))

    def test_indexed_audio_merge_allows_regional_row_positions(self):
        base = synthetic_indexed_audio_scene(
            tail_flag=7, extra_indexed_rows=1
        )
        donor = synthetic_indexed_audio_scene(tail_flag=3)
        rebuilt, groups = build._merge_indexed_audio_groups(base, donor)

        self.assertEqual(2, groups)
        self.assertIn(b"HUD ROW 0", rebuilt)
        self.assertEqual(
            tuple(item[3] for item in build._indexed_audio_groups(donor)),
            tuple(item[3] for item in build._indexed_audio_groups(rebuilt)),
        )

    def test_indexed_audio_is_discovered_without_resource_ids(self):
        base = synthetic_indexed_audio_scene(tail_flag=7)
        donor = synthetic_indexed_audio_scene(
            tail_flag=3, extra_audio_sectors=1
        )
        total = 2
        base_values = [0, 0, 0, len(base) // layout.SECTOR, 0, 0]
        donor_values = [0, 0, 0, len(donor) // layout.SECTOR, 0, 0]
        hybrids, groups = build._indexed_audio_hybrids(
            io.BytesIO(base), base_values,
            io.BytesIO(donor), donor_values, total,
        )

        self.assertEqual((1,), tuple(hybrids))
        self.assertEqual(2, groups)
        self.assertIn(b"TARGET HUD", hybrids[1])

    def test_streamed_scene_audio_merge_keeps_target_indexed_content(self):
        base = synthetic_streamed_scene(
            tail_flag=7, indexed_marker=b"USA INDEXED"
        )
        donor = synthetic_streamed_scene(
            tail_flag=3, extra_tail_sectors=1,
            indexed_marker=b"JP INDEXED",
        )
        rebuilt, clips = build._merge_streamed_audio_tail(base, donor)
        base_start, identities = build._streamed_audio_tail(base)
        donor_start, donor_identities = build._streamed_audio_tail(donor)

        self.assertEqual(2, clips)
        self.assertEqual(identities, donor_identities)
        self.assertEqual(base[:base_start], rebuilt[:base_start])
        self.assertEqual(donor[donor_start:], rebuilt[base_start:])
        self.assertEqual(
            (base_start, donor_identities),
            build._streamed_audio_tail(rebuilt),
        )
        self.assertIn(b"USA INDEXED", rebuilt[:base_start])
        self.assertNotIn(b"JP INDEXED", rebuilt[:base_start])

    def test_streamed_scene_audio_merge_rejects_identity_drift(self):
        base = synthetic_streamed_scene(tail_flag=7)
        donor = bytearray(synthetic_streamed_scene(tail_flag=3))
        donor_start, _identities = build._streamed_audio_tail(donor)
        struct.pack_into("<H", donor, donor_start + 0x120 + 0xA2, 0x0B00)
        with self.assertRaisesRegex(ValueError, "clip identities"):
            build._merge_streamed_audio_tail(base, bytes(donor))

    def test_streamed_scene_audio_is_discovered_without_resource_ids(self):
        base = synthetic_streamed_scene(tail_flag=7)
        donor = synthetic_streamed_scene(
            tail_flag=3, extra_tail_sectors=1
        )
        total = 2
        base_values = [0, 0, 0, len(base) // layout.SECTOR, 0, 0]
        donor_values = [0, 0, 0, len(donor) // layout.SECTOR, 0, 0]
        hybrids, clips = build._streamed_audio_hybrids(
            io.BytesIO(base), base_values,
            io.BytesIO(donor), donor_values, total,
        )

        self.assertEqual((1,), tuple(hybrids))
        self.assertEqual(2, clips)
        self.assertEqual(base[:layout.SECTOR], hybrids[1][:layout.SECTOR])
        self.assertEqual(donor[layout.SECTOR:], hybrids[1][layout.SECTOR:])

    def test_region_asset_merge_uses_only_matching_package_flags(self):
        def package(items, flags):
            count = len(items)
            table_end = 8 + (count + 1) * 8
            offsets = [table_end]
            for item in items:
                offsets.append(offsets[-1] + len(item))
            result = bytearray(offsets[-1])
            result[:4] = b"p@Ck"
            struct.pack_into("<BBH", result, 4, 1, 0, count)
            for index, offset in enumerate(offsets):
                item_flag = flags[index] if index < count else 0
                struct.pack_into(
                    "<II", result, 8 + index * 8, offset, item_flag
                )
            for index, item in enumerate(items):
                result[offsets[index]:offsets[index + 1]] = item
            return bytes(result)

        base = package((b"USA0", b"KEEP", b"USA2"),
                       (0x5400, 0x2000, 0x5400))
        donor = package((b"JP00", b"NOPE", b"JP22"),
                        (0x5400, 0x2000, 0x5400))
        merged, selected = build._replace_flagged_package_items(
            base, donor, 0x5400
        )
        parsed = build.package_archive.layout(merged)
        items = tuple(
            merged[start:end]
            for start, end in zip(parsed.offsets, parsed.offsets[1:])
        )
        self.assertEqual((0, 2), selected)
        self.assertEqual((b"JP00", b"KEEP", b"JP22"), items)

    def test_logical_positions_follow_active_entries_across_zero_rows(self):
        total = 5
        original = (
            [10, 0, 20, 23, 30] +
            [2, 7, 3, 1, 1] +
            [100, 0, 102, 105, 50]
        )
        rebuilt = list(original)
        rebuilt[total + 0] = 4
        build._rewrite_logical_positions(original, rebuilt, total)
        self.assertEqual([100, 0, 104, 107, 50], rebuilt[2 * total:])

    def test_import_copies_complete_resources_and_reads_them_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / "usa.iso"
            donor = root / "jp.iso"
            output = root / "imported.iso"
            synthetic_iso(base, "SLUS_214.52", battle=True)
            synthetic_iso(donor, "SLPM_664.19", battle=True)
            with donor.open("r+b") as image:
                image.seek(BANK_OFFSET + 0x100)
                image.write(b"Japanese resource marker")
            with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                    mock.patch.object(build, "load_unmapped_map",
                                      return_value={}):
                result = build.import_japanese_audio(base, donor, output)
            self.assertEqual((BANK, BATTLE_ENTRY), result.resources)
            self.assertEqual(0, result.appended_sectors)
            with output.open("rb") as image:
                total, table = layout.read_index(image)
                active = [
                    entry for entry in range(1, total)
                    if table[total + entry]
                ]
                self.assertTrue(all(
                    table[previous] + table[total + previous] == table[entry]
                    for previous, entry in zip(active, active[1:])
                ))
                offset, length = layout.entry_span(table, total, BANK)
                image.seek(offset)
                imported = image.read(length)
                unmapped_offset, unmapped_length = layout.entry_span(
                    table, total, UNMAPPED_ENTRY
                )
                image.seek(unmapped_offset)
                retained = image.read(unmapped_length)
            self.assertEqual(
                donor.read_bytes()[BANK_OFFSET:BANK_OFFSET + BANK_LENGTH],
                imported,
            )
            self.assertEqual(
                base.read_bytes()[
                    UNMAPPED_OFFSET:UNMAPPED_OFFSET + UNMAPPED_LENGTH
                ],
                retained,
            )

    def test_import_requires_supported_target_and_japanese_donor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            usa = root / "usa.iso"
            japan = root / "jp.iso"
            synthetic_iso(usa, "SLUS_214.52", battle=True)
            synthetic_iso(japan, "SLPM_664.19", battle=True)
            with self.assertRaisesRegex(ValueError, "Japanese-audio import"):
                build.import_japanese_audio(japan, japan, root / "bad.iso")
            with self.assertRaisesRegex(ValueError, "donor selection"):
                build.import_japanese_audio(usa, usa, root / "bad.iso")

    def test_every_pal_release_is_an_import_target_and_keeps_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            donor = root / "jp.iso"
            synthetic_iso(donor, layout.JAPAN_BOOT, battle=True)
            for boot, release in layout.PAL_BOOTS.items():
                with self.subTest(boot=boot):
                    base = root / (release + ".iso")
                    output = root / (release + "-jp.iso")
                    synthetic_iso(base, boot, battle=True)
                    with base.open("ab") as image:
                        image.write(bytes(3 * layout.SECTOR))
                    original_size = base.stat().st_size
                    original_sectors = original_size // layout.SECTOR
                    pvd = bytearray(layout.SECTOR)
                    pvd[0] = 1
                    pvd[1:6] = b"CD001"
                    struct.pack_into("<I", pvd, 80, original_sectors)
                    struct.pack_into(">I", pvd, 84, original_sectors)
                    with base.open("r+b") as image:
                        image.seek(16 * layout.SECTOR)
                        image.write(pvd)
                    with mock.patch.object(build, "VOICE_BANKS", (BANK,)), \
                            mock.patch.object(build, "load_unmapped_map",
                                              return_value={}):
                        result = build.import_japanese_audio(
                            base, donor, output
                        )
                    self.assertEqual(original_size, result.output.stat().st_size)
                    self.assertEqual((release, boot), build.describe_disc(output))
                    with output.open("rb") as image:
                        image.seek(16 * layout.SECTOR + 80)
                        volume = image.read(8)
                    self.assertEqual(
                        original_sectors, struct.unpack_from("<I", volume)[0]
                    )
                    self.assertEqual(
                        original_sectors, struct.unpack_from(">I", volume, 4)[0]
                    )

    def test_pal_release_remains_outside_fixed_wav_workflows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pal = root / "pal.iso"
            synthetic_iso(pal, "SLES_546.44")
            with self.assertRaisesRegex(ValueError, "voice extraction"):
                build.extract_voices(pal, root / "voices")


if __name__ == "__main__":
    unittest.main()
