# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 inno³ and contributors
"""Tests unitaires du connecteur inno3pilot (helpers purs, sans réseau)."""

import unittest

from inno3pilot_mcp import server


class TestClampLimit(unittest.TestCase):
    def test_normal(self) -> None:
        self.assertEqual(server._clamp_limit(20), 20)

    def test_too_high(self) -> None:
        self.assertEqual(server._clamp_limit(9999), server.MAX_LIMIT)

    def test_invalid(self) -> None:
        self.assertEqual(server._clamp_limit("abc"), server.DEFAULT_LIMIT)
        self.assertEqual(server._clamp_limit(-3), server.DEFAULT_LIMIT)


class TestDumps(unittest.TestCase):
    def test_utf8(self) -> None:
        self.assertEqual(server._dumps({"t": "échéance"}), '{"t": "échéance"}')


class TestCardSummary(unittest.TestCase):
    def test_flattens_card_and_summary(self) -> None:
        raw = {
            "card": {"rowid": 7, "fk_column": 3, "position": 1, "color": "blue",
                     "elementtype": "project_task", "fk_element": 42},
            "summary": {"ref": "TK1", "title": "Titre", "project_title": "Proj",
                        "assignee": "Jean", "native_status_label": "50 %",
                        "date_due": "01/09/2026", "overdue": 0, "priority": 2},
        }
        out = server._card_summary(raw)
        self.assertEqual(out["card_id"], 7)
        self.assertEqual(out["elementtype"], "project_task")
        self.assertEqual(out["ref"], "TK1")
        self.assertEqual(out["status"], "50 %")
        self.assertEqual(out["priority"], 2)

    def test_missing_summary(self) -> None:
        out = server._card_summary({"card": {"rowid": 1}})
        self.assertEqual(out["card_id"], 1)
        self.assertIsNone(out["title"])


if __name__ == "__main__":
    unittest.main()
