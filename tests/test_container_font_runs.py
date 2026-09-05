# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import unittest

from tools.scripts import vp2_container_text as container_text

PAYLOAD = bytes([0x65, 0x8E, 0x80, 0x00, 0x00, 0x80, 0xBF, 0x83, 0x01, 0x00])
META = {"text_start": 0, "text_end": 32, "glyph_base": 0x65, "glyph_count": 94}
ALPHABET = {0: "a", 30: "a"}


class ContainerFontRunTests(unittest.TestCase):
    """A character's glyph code depends on the run that draws it."""

    def test_a_record_reads_back_as_the_bytes_it_came_from(self):
        rendered, length = container_text.render_codepage(
            PAYLOAD, META, 0, alphabet=ALPHABET)
        self.assertEqual(rendered, "a<808E:000080BF>a")
        self.assertEqual(length, len(PAYLOAD))

        runs = container_text.codepage_run_tokens(PAYLOAD, META, 0, ALPHABET)
        self.assertEqual(runs, [{"a": 0x65}, {"a": 0x0183}])
        self.assertEqual(
            container_text.encode_codepage(rendered, local_tokens=runs),
            PAYLOAD)

    def test_one_map_for_the_whole_record_draws_the_wrong_cut(self):
        rendered, _length = container_text.render_codepage(
            PAYLOAD, META, 0, alphabet=ALPHABET)
        flat = container_text.local_codepage_tokens(ALPHABET, META)
        self.assertEqual(flat, {"a": 0x65})
        self.assertNotEqual(
            container_text.encode_codepage(rendered, local_tokens=flat),
            PAYLOAD)


if __name__ == "__main__":
    unittest.main()
