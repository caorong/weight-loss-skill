"""Offline documentation/fixture checks, not execution of live-model evals."""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "diet-tracking-analysis"


class ReviewContractTests(unittest.TestCase):
    def test_manual_eval_is_explicitly_not_run_and_cases_are_unique(self):
        fixture = json.loads((SKILL / "evals/daily-weekly-reviews.json").read_text())
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["evaluation"]["result_status"], "not_run")
        self.assertIn("not executed by offline", fixture["purpose"])
        cases = fixture["cases"]
        self.assertGreaterEqual(len(cases), 20)
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertIsInstance(case["context"], str)
                for key in ("expect", "forbid"):
                    self.assertTrue(case[key])
                    self.assertTrue(all(isinstance(item, str) and item.strip()
                                        for item in case[key]))

    def test_schema_documents_parse_and_internal_references_resolve(self):
        for filename in ("coach-review.schema.json", "coach-reminder-settings.schema.json"):
            schema = json.loads((SKILL / "references" / filename).read_text())
            with self.subTest(schema=filename):
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertEqual(schema["type"], "object")
                self.assertTrue(schema["required"])

                def visit(value):
                    if isinstance(value, dict):
                        if "$ref" in value:
                            self.assertTrue(value["$ref"].startswith("#/"))
                            target = schema
                            for part in value["$ref"][2:].split("/"):
                                target = target[part.replace("~1", "/").replace("~0", "~")]
                            self.assertIsInstance(target, dict)
                        if "required" in value:
                            self.assertEqual(len(value["required"]), len(set(value["required"])))
                        for child in value.values():
                            visit(child)
                    elif isinstance(value, list):
                        for child in value:
                            visit(child)

                visit(schema)

    def test_schema_types_match_explicit_runtime_contract(self):
        report = json.loads((SKILL / "references/coach-review.schema.json").read_text())
        self.assertEqual(report["properties"]["review_type"]["enum"], ["daily", "weekly", "custom"])
        self.assertIn("review_type", report["$defs"]["reviewSaveInput"]["properties"])
        self.assertFalse(report["properties"]["review_boundaries"]["properties"]["automatic_plan_change"]["const"])
        settings = json.loads((SKILL / "references/coach-reminder-settings.schema.json").read_text())
        self.assertEqual(settings["properties"]["review_type"]["enum"], ["daily", "weekly"])
        self.assertNotIn("review_period", settings["properties"])
        self.assertNotIn("default", settings["properties"]["enabled"])

    def test_operating_docs_cover_review_safety_and_delivery_tradeoff(self):
        flow = " ".join((SKILL / "references/coach-flows.md").read_text().split())
        native = " ".join((SKILL / "references/native-reminders.md").read_text().split())
        entry = " ".join((SKILL / "SKILL.md").read_text().split())
        for phrase in ("review --review-type daily", "review --review-type weekly",
                       "not actual energy surplus/deficit", "not a personalized optimizer",
                       "Do not write a new meal or", "occurrence date"):
            self.assertIn(phrase, flow)
        for phrase in ("at-most-once", "exact route", "review_already_reserved",
                       "combined_with_other_review", "missed message", "legacy last7"):
            self.assertIn(phrase, native)
        self.assertIn("without requiring", entry)
        self.assertIn("Food identity uncertainty MUST trigger clarification", entry)


if __name__ == "__main__":
    unittest.main()
