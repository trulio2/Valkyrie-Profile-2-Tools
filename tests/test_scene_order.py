# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import unittest

from tools.scripts.scene_sheet_export import (
    apply_scene_line_orders, apply_scene_speaker_overrides, order_scenes,
)


def ids(scenes):
    return [[line[0] for line in scene["lines"]] for scene in scenes]


class OrderScenesTests(unittest.TestCase):
    """A scene plays in voice-slot order, not script-dispatch order."""

    def test_a_voiced_scene_plays_in_slot_order(self):
        scenes = [{"first_offset": 10,
                   "lines": [(1, 10, 0), (2, 20, 0), (24, 30, 0), (3, 40, 0)]}]
        voice = {1: (10, 0), 2: (10, 1), 24: (10, 2), 3: (10, 3)}
        self.assertEqual([[1, 2, 24, 3]], ids(order_scenes(scenes, voice)))

    def test_a_gap_in_the_track_still_sorts(self):
        scenes = [{"first_offset": 0,
                   "lines": [(31, 0, 0), (35, 10, 0), (34, 20, 0)]}]
        voice = {31: (10, 6), 35: (10, 9), 34: (10, 5)}
        self.assertEqual([34, 31, 35],
                         ids(order_scenes(scenes, voice))[0])

    def test_a_second_voice_bank_starts_a_new_scene(self):
        scenes = [{"first_offset": 0,
                   "lines": [(1, 0, 0), (2, 10, 0), (5, 20, 0), (6, 30, 0)]}]
        voice = {1: (470, 1), 2: (470, 0), 5: (480, 1), 6: (480, 0)}
        ordered = order_scenes(scenes, voice)
        self.assertEqual([[2, 1], [6, 5]], ids(ordered))
        self.assertEqual([0, 20], [scene["first_offset"] for scene in ordered])

    def test_a_bank_split_across_script_scenes_is_one_scene(self):
        scenes = [{"first_offset": 0,
                   "lines": [(1, 0, 0), (2, 10, 0)]},
                  {"first_offset": 1000,
                   "lines": [(3, 1000, 0), (4, 1010, 0)]}]
        voice = {1: (100, 30), 2: (100, 0), 3: (100, 5), 4: (100, 40)}
        ordered = order_scenes(scenes, voice)
        self.assertEqual([[2, 3, 1, 4]], ids(ordered))

    def test_an_unvoiced_scene_keeps_the_script_order(self):
        scenes = [{"first_offset": None,
                   "lines": [(30, None, 0), (10, None, 0), (20, None, 0)]}]
        ordered = order_scenes(scenes, {})
        self.assertEqual([30, 10, 20], ids(ordered)[0])
        self.assertIsNone(ordered[0]["first_offset"])

    def test_offsets_and_repeat_counts_survive_the_sort(self):
        scenes = [{"first_offset": 0,
                   "lines": [(1, 10, 2), (2, 20, 0)]}]
        voice = {1: (10, 1), 2: (10, 0)}
        self.assertEqual([(2, 20, 0), (1, 10, 2)],
                         order_scenes(scenes, voice)[0]["lines"])

    def test_manual_order_covers_records_without_voice_headers(self):
        rows = [
            {"message_id": "1", "scene": "4", "scene_line": "1"},
            {"message_id": "2", "scene": "4", "scene_line": "2"},
            {"message_id": "4", "scene": "4", "scene_line": "3"},
            {"message_id": "7", "scene": "4", "scene_line": "4"},
        ]
        ordered = apply_scene_line_orders(
            rows, 1337, {1337: [(1, 2, 7, 4)]})
        self.assertEqual(["1", "2", "7", "4"],
                         [row["message_id"] for row in ordered])
        self.assertEqual(["1", "2", "3", "4"],
                         [row["scene_line"] for row in ordered])

    def test_partial_region_specific_manual_order_is_ignored(self):
        rows = [{"message_id": "1"}, {"message_id": "2"}]
        self.assertEqual(
            rows,
            apply_scene_line_orders(
                rows, 1337, {1337: [(1, 2, 7)]}),
        )

    def test_curated_speaker_fills_only_its_resource_and_message(self):
        rows = [
            {"message_id": "1", "speaker": ""},
            {"message_id": "2", "speaker": "From script"},
        ]
        overrides = {(1337, 1): "Barbarossa", (1337, 2): "Other"}
        result = apply_scene_speaker_overrides(rows, 1337, overrides)
        self.assertEqual("Barbarossa", result[0]["speaker"])
        self.assertEqual("From script", result[1]["speaker"])


if __name__ == "__main__":
    unittest.main()
