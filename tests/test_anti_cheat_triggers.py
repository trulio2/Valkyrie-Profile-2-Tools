"""The anti-cheat trigger report."""
import unittest


class RowFindingTests(unittest.TestCase):
    def findings(self, row, text="", enabled=(), ranges=frozenset({"\u00c0"})):
        from tools.anti_cheat_triggers import row_findings
        return row_findings(dict(row), set(text), set(ranges), set(enabled))

    def test_a_scene_with_a_second_range_character_needs_anti_cheat(self):
        got = self.findings({"kind": "scene", "resource": "117"}, text="\u00c0")
        self.assertTrue(any("second range" in reason for reason in got))

    def test_a_scene_without_one_does_not(self):
        self.assertEqual(
            [], self.findings({"kind": "scene", "resource": "117"},
                              text="ola"))

    def test_a_container_with_the_flag_contributes_its_text(self):
        got = self.findings(
            {"kind": "container", "resource": "641",
             "flags": "shared-font-glyphs"}, text="\u00c0")
        self.assertTrue(any("second range" in reason for reason in got))

    def test_a_container_without_the_flag_contributes_no_text(self):
        self.assertEqual(
            [], self.findings({"kind": "container", "resource": "641",
                               "flags": ""}, text="\u00da"))

    def test_a_container_on_the_save_overlay_is_a_text_bank_not_a_trigger(self):
        self.assertEqual(
            [], self.findings({"kind": "container", "resource": "652"},
                              text="texto"))

    def test_a_container_on_the_battle_overlay_is_a_text_bank_not_a_trigger(self):
        self.assertEqual(
            [], self.findings({"kind": "container", "resource": "1781"},
                              text="texto"))

    def test_an_image_on_the_battle_overlay_is_flagged(self):
        got = self.findings({"kind": "image", "resource": "1781"})
        self.assertTrue(any("battle overlay" in reason for reason in got))

    def test_an_image_on_another_resource_is_not(self):
        self.assertEqual(
            [], self.findings({"kind": "image", "resource": "656"}))

    def test_misc_is_flagged_for_the_battle_overlay(self):
        got = self.findings({"kind": "misc", "resource": "1781"})
        self.assertTrue(any("battle overlay" in reason for reason in got))

    def test_the_item_sort_is_flagged_only_when_it_is_enabled(self):
        row = {"kind": "container", "resource": "644"}
        self.assertTrue(any("item sort" in reason for reason in self.findings(
            row, text="x", enabled=["item-order"])))
        self.assertFalse(any("item sort" in reason for reason in self.findings(
            row, text="x")))

    def test_range_characters_are_those_with_a_second_range_token(self):
        from tools.anti_cheat_triggers import range_characters
        self.assertEqual({"x"}, range_characters({"x": 0x880, "y": 0x11}))


if __name__ == "__main__":
    unittest.main()
