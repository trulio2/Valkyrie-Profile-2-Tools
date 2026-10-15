# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import unittest

import tools.scripts.vp2_cutscene_subtitles  # noqa: F401
from tools.scripts.scene_fonts import order_assignment


class OrderAssignmentTests(unittest.TestCase):

    def test_the_space_keeps_its_slot_when_the_slots_below_it_change(self):
        assignment = {"b": 4, " ": 6, "c": 7, "d": 5}
        ordered = order_assignment(assignment, ["c", " ", "b", "d"])
        self.assertEqual(ordered, {"c": 4, " ": 6, "b": 5, "d": 7})

    def test_an_order_that_leaves_the_space_alone_is_a_plain_zip(self):
        assignment = {"a": 0, "b": 1, " ": 2, "c": 3}
        order = ["c", "a", " ", "b"]
        self.assertEqual(order_assignment(assignment, order),
                         dict(zip(order, sorted(assignment.values()))))

    def test_a_face_without_a_space(self):
        assignment = {"a": 3, "b": 1}
        self.assertEqual(order_assignment(assignment, ["a", "b"]),
                         {"a": 1, "b": 3})


if __name__ == "__main__":
    unittest.main()
