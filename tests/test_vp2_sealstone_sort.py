# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import struct
import unittest
from unittest import mock

from tools.scripts import sle, vp2_build
from tools.scripts import vp2_sealstone_sort as sealstone_sort


def all_names():
    return {
        sealstone_id: "Name %03d" % (100 - sealstone_id)
        for sealstone_id in sealstone_sort.NAMED_IDS
    }


class SealstoneNameTests(unittest.TestCase):
    def test_container_648_maps_messages_to_the_66_named_records(self):
        rows = [
            {"message_id": str(sealstone_sort.NAME_MESSAGE_BASE + sealstone_id),
             "original_en": "English %d" % sealstone_id,
             "translated": "Nome %d" % sealstone_id}
            for sealstone_id in sealstone_sort.NAMED_IDS
        ]
        names = sealstone_sort.names_from_rows(rows)
        self.assertEqual(set(sealstone_sort.NAMED_IDS), set(names))
        self.assertNotIn(24, names)
        self.assertNotIn(64, names)
        self.assertTrue(sealstone_sort.has_translated_names(rows))


class SealstoneTableTests(unittest.TestCase):
    def output(self):
        size = (sealstone_sort.TABLE_OFFSET
                + sealstone_sort.SEALSTONE_COUNT * sealstone_sort.RECORD_SIZE)
        output = bytearray(size)
        struct.pack_into("<I", output, 8, sealstone_sort.LOAD_ADDRESS)
        ordinal = 0
        for sealstone_id in range(sealstone_sort.SEALSTONE_COUNT):
            if sealstone_id in sealstone_sort.EMPTY_IDS:
                key = 0 if sealstone_id == 24 else 1
            else:
                key = 0xA000 | (0x22 + ordinal)
                ordinal += 1
            struct.pack_into(
                "<H", output,
                sealstone_sort.TABLE_OFFSET
                + sealstone_id * sealstone_sort.RECORD_SIZE
                + sealstone_sort.SORT_KEY_OFFSET,
                key,
            )
        return bytes(output)

    def test_only_comparator_rank_bits_change(self):
        original = self.output()
        rebuilt, changed = sealstone_sort.rewrite_output(original, all_names())
        self.assertGreater(changed, 0)
        old_ranks = []
        ranks = []
        key_bytes = set()
        for sealstone_id in range(sealstone_sort.SEALSTONE_COUNT):
            offset = (sealstone_sort.TABLE_OFFSET
                      + sealstone_id * sealstone_sort.RECORD_SIZE
                      + sealstone_sort.SORT_KEY_OFFSET)
            key_bytes.update((offset, offset + 1))
            old = struct.unpack_from("<H", original, offset)[0]
            new = struct.unpack_from("<H", rebuilt, offset)[0]
            if sealstone_id in sealstone_sort.EMPTY_IDS:
                self.assertEqual(old, new)
                continue
            old_ranks.append((old & sealstone_sort.RANK_MASK)
                             - sealstone_sort.RANK_BASE)
            self.assertEqual(old & ~sealstone_sort.RANK_MASK,
                             new & ~sealstone_sort.RANK_MASK)
            ranks.append((new & sealstone_sort.RANK_MASK)
                         - sealstone_sort.RANK_BASE)
        self.assertEqual(66, len(set(old_ranks)))
        self.assertEqual(list(range(66)), sorted(ranks))
        changed_bytes = {offset for offset, pair in enumerate(zip(original, rebuilt))
                         if pair[0] != pair[1]}
        self.assertLessEqual(changed_bytes, key_bytes)

    def test_fixed_stream_reads_back_and_is_idempotent(self):
        resource = sle.conceal(
            sealstone_sort.slz3.compress(self.output())) + bytes(128)
        first = sealstone_sort.patch_resource(resource, all_names())
        second = sealstone_sort.patch_resource(first.data, all_names())
        self.assertEqual(len(resource), len(first.data))
        self.assertGreater(first.changed, 0)
        self.assertGreaterEqual(first.room, 0)
        self.assertEqual(0, second.changed)
        self.assertEqual(first.data, second.data)


class BuildIntegrationTests(unittest.TestCase):
    def test_resource_648_drives_the_sealstone_rank_patch(self):
        rows = [
            {"message_id": str(sealstone_sort.NAME_MESSAGE_BASE + sealstone_id),
             "original_en": "English %d" % sealstone_id,
             "translated": "Nome %d" % sealstone_id}
            for sealstone_id in sealstone_sort.NAMED_IDS
        ]
        expected = sealstone_sort.Result(b"patched", 60, 100, 20)
        manifest = [{"kind": "container", "resource": "648",
                     "sheet": "sealstones.csv"}]
        fake_iso = object()
        with mock.patch.object(vp2_build, "_read_sheet_with_dedupe",
                               return_value=rows), \
                mock.patch.object(sealstone_sort, "apply_to_iso",
                                  return_value=expected) as apply:
            result = vp2_build.apply_sealstone_name_sort(fake_iso, manifest)
        self.assertIs(expected, result)
        apply.assert_called_once()
        self.assertIs(fake_iso, apply.call_args.args[0])
        self.assertEqual(set(sealstone_sort.NAMED_IDS),
                         set(apply.call_args.args[1]))


if __name__ == "__main__":
    unittest.main()
