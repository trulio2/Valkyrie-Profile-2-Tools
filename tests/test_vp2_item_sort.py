import struct
import unittest
from unittest import mock

from tools.scripts import sle, vp2_build, vp2_item_sort as item_sort


class ItemNameTests(unittest.TestCase):
    def test_the_name_bank_is_item_id_plus_48(self):
        rows = [
            {"message_id": "47", "original_en": "Not an item",
             "translated": ""},
            {"message_id": "48", "original_en": "Apple",
             "translated": "Maçã"},
            {"message_id": "49", "original_en": "Berry",
             "translated": ""},
            {"message_id": "50", "original_en": "Crown",
             "translated": "Coroa"},
        ]
        with mock.patch.object(item_sort, "ITEM_COUNT", 3):
            self.assertEqual(
                {0: "Maçã", 1: "Berry", 2: "Coroa"},
                item_sort.names_from_rows(rows),
            )
            self.assertTrue(item_sort.has_translated_names(rows))

    def test_collation_ignores_case_accents_spaces_and_punctuation(self):
        self.assertEqual(
            item_sort.collation_key("Água-de-Coco")[0],
            item_sort.collation_key("agua de coco")[0],
        )


class ItemTableTests(unittest.TestCase):
    COUNT = 4
    TABLE = 0x20
    RECORD = 8

    def output(self):
        output = bytearray(self.TABLE + self.COUNT * self.RECORD)
        struct.pack_into("<I", output, 8, item_sort.LOAD_ADDRESS)
        keys = [0x1000, 0x2004, 0x3008, 0x400C]
        for item_id, key in enumerate(keys):
            struct.pack_into(
                "<H", output,
                self.TABLE + item_id * self.RECORD + item_sort.SORT_KEY_OFFSET,
                key,
            )
        return bytes(output)

    def settings(self):
        return mock.patch.multiple(
            item_sort,
            ITEM_COUNT=self.COUNT,
            TABLE_OFFSET=self.TABLE,
            RECORD_SIZE=self.RECORD,
        )

    def test_only_rank_bits_change(self):
        names = {0: "Zulu", 1: "Árvore", 2: "abacate", 3: "Beta"}
        with self.settings():
            original = self.output()
            rebuilt, changed = item_sort.rewrite_output(original, names)
        self.assertEqual(3, changed)
        expected_ranks = [3, 1, 0, 2]
        for item_id, rank in enumerate(expected_ranks):
            offset = self.TABLE + item_id * self.RECORD
            old = struct.unpack_from("<H", original,
                                     offset + item_sort.SORT_KEY_OFFSET)[0]
            new = struct.unpack_from("<H", rebuilt,
                                     offset + item_sort.SORT_KEY_OFFSET)[0]
            self.assertEqual(old & item_sort.TYPE_MASK,
                             new & item_sort.TYPE_MASK)
            self.assertEqual(rank, (new & item_sort.RANK_MASK)
                             // item_sort.RANK_SCALE)
            self.assertEqual(
                original[offset:offset + item_sort.SORT_KEY_OFFSET],
                rebuilt[offset:offset + item_sort.SORT_KEY_OFFSET],
            )

    def test_the_fixed_stream_reads_back_and_is_idempotent(self):
        names = {0: "Zulu", 1: "Árvore", 2: "abacate", 3: "Beta"}
        resource = sle.conceal(item_sort.slz3.compress(self.output())) + bytes(128)
        with self.settings():
            first = item_sort.patch_resource(resource, names)
            second = item_sort.patch_resource(first.data, names)
        self.assertEqual(len(resource), len(first.data))
        self.assertEqual(3, first.changed)
        self.assertGreaterEqual(first.room, 0)
        self.assertEqual(0, second.changed)
        self.assertEqual(first.data, second.data)


class BuildIntegrationTests(unittest.TestCase):
    def test_resource_644_drives_the_resident_rank_patch(self):
        rows = [
            {"message_id": "48", "original_en": "Apple",
             "translated": "Maçã"},
            {"message_id": "49", "original_en": "Berry",
             "translated": "Baga"},
            {"message_id": "50", "original_en": "Crown",
             "translated": "Coroa"},
        ]
        expected = item_sort.Result(b"patched", 2, 100, 20)
        manifest = [{"kind": "container", "resource": "644",
                     "sheet": "items.csv"}]
        fake_iso = object()
        with mock.patch.object(item_sort, "ITEM_COUNT", 3), \
                mock.patch.object(vp2_build, "_read_sheet_with_dedupe",
                                  return_value=rows), \
                mock.patch.object(item_sort, "apply_to_iso",
                                  return_value=expected) as apply:
            result = vp2_build.apply_item_name_sort(fake_iso, manifest)
        self.assertIs(expected, result)
        apply.assert_called_once()
        self.assertIs(fake_iso, apply.call_args.args[0])
        self.assertEqual({0: "Maçã", 1: "Baga", 2: "Coroa"},
                         apply.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
