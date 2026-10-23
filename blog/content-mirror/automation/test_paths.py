import unittest
from pathlib import Path

from paths import normalize_title, plan_article_paths


class PathPlanningTests(unittest.TestCase):
    def test_normalize_title_matches_legacy_rules(self):
        self.assertEqual(normalize_title(' A? "B": C/<D>\\E|F*G \t'), "A B - C D E F G")
        self.assertEqual(normalize_title("\x01 leading... title -- "), "leading... title")
        self.assertEqual(normalize_title("Árvíztűrő tükörfúrógép"), "Árvíztűrő tükörfúrógép")
        self.assertEqual(normalize_title("... - "), "")

    def test_path_includes_date_title_and_wordpress_id(self):
        record = {
            "id": 123,
            "type": "post",
            "title": {"rendered": "A <em>Simple</em> Title"},
            "date": "2024-02-03T04:05:06",
        }
        self.assertEqual(
            plan_article_paths([record]),
            {"post:123": "2024/02/2024-02-03 04.05 A Simple Title [123].md"},
        )

    def test_title_date_corrections_change_path_and_untrusted_title_stays_local(self):
        record = {
            "id": 8,
            "type": "page",
            "title": {"rendered": "../CON?: report"},
            "date": "2025-09-10T11:12:13",
        }
        self.assertEqual(
            plan_article_paths([record])["page:8"],
            "2025/09/2025-09-10 11.12 CON - report [8].md",
        )
        corrected = {**record, "title": {"rendered": "Corrected"}, "date": "2025-09-11T11:12:13"}
        self.assertEqual(
            plan_article_paths([corrected])["page:8"],
            "2025/09/2025-09-11 11.12 Corrected [8].md",
        )

    def test_case_insensitive_collisions_suffix_every_member_independent_of_order(self):
        first = {"id": 10, "type": "post", "title": {"rendered": "Same Title"}, "date": "2024-01-02T03:04:05"}
        second = {"id": 20, "type": "page", "title": {"rendered": "same title"}, "date": "2024-01-02T03:04:05"}
        expected = {
            "post:10": "2024/01/2024-01-02 03.04 Same Title [10].md",
            "page:20": "2024/01/2024-01-02 03.04 same title [20].md",
        }
        self.assertEqual(plan_article_paths([first, second]), expected)
        self.assertEqual(plan_article_paths([second, first]), expected)

    def test_ids_keep_active_and_retained_paths_distinct(self):
        retained = {
            "key": "post:1",
            "id": 1,
            "type": "post",
            "title": "A title",
            "published_at": "2024-01-02T03:04:05",
            "path": "2024/01/2024-01-02 03.04 A title [1].md",
        }
        self.assertEqual(
            plan_article_paths([], [retained]),
            {"post:1": retained["path"]},
        )
        active = {
            "id": 2,
            "type": "page",
            "title": {"rendered": "A title"},
            "date": "2024-01-02T03:04:05",
        }
        result = plan_article_paths([active], [retained])
        self.assertEqual(result["post:1"], "2024/01/2024-01-02 03.04 A title [1].md")
        self.assertEqual(result["page:2"], "2024/01/2024-01-02 03.04 A title [2].md")

    def test_rejects_noncanonical_or_timezone_aware_dates_and_unsafe_retained_paths(self):
        base = {"id": 9, "type": "post", "title": {"rendered": "Safe"}}
        with self.assertRaises(ValueError):
            plan_article_paths([{**base, "date": "2024-01-02T03:04:05+01:00"}])
        with self.assertRaises(ValueError):
            plan_article_paths([{**base, "date": "2024-1-2T03:04:05"}])
        retained = {
            "id": 9,
            "type": "post",
            "title": "Safe",
            "published_at": "2024-01-02T03:04:05",
            "path": "../../outside.md",
        }
        with self.assertRaises(ValueError):
            plan_article_paths([], [retained])

    def test_checks_windows_component_and_optional_absolute_path_limits(self):
        long = {"id": 7, "type": "post", "title": {"rendered": "x" * 250}, "date": "2024-01-02T03:04:05"}
        with self.assertRaises(ValueError):
            plan_article_paths([long])
        short = {"id": 7, "type": "post", "title": {"rendered": "short"}, "date": "2024-01-02T03:04:05"}
        with self.assertRaises(ValueError):
            plan_article_paths([short], articles_root=Path("D:/" + "r" * 250))


if __name__ == "__main__":
    unittest.main()
