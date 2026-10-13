"""Instruction/fixture regression checks, NOT a live-model vision evaluation."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "diet-tracking-analysis"


class FoodIdentityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (BASE / "SKILL.md").read_text(encoding="utf-8")
        cls.flow = (BASE / "references/standalone-flow.md").read_text(encoding="utf-8")
        cls.coach_flow = (BASE / "references/coach-flows.md").read_text(encoding="utf-8")

    def test_identity_question_overrides_auto_save(self):
        self.assertIn("食物身份不确定时先询问，不受打卡免确认规则影响", self.skill)
        self.assertIn("Identity clarification overrides check-in auto-save", self.flow)
        self.assertIn("it NEVER means guessing an unidentified food", self.skill)
        self.assertNotIn("Only ask to clarify\nbare ambiguous photos", self.skill)

    def test_identity_portion_and_intent_remain_separate(self):
        for text in ("Identity: what food is it?", "Portion: how much was eaten?", "Intent: consumed check-in"):
            self.assertIn(text, self.flow)
        self.assertIn("No second model API call from Python", self.skill)

    def test_waiting_has_no_fake_or_implicit_complete_record(self):
        self.assertIn("no meal write while any food identity remains unresolved", self.flow)
        self.assertIn("Unknown is not zero", self.flow)
        self.assertIn("Only if the user explicitly asks to save known items", self.flow)
        self.assertIn("append` ONLY the newly resolved food", self.flow)

    def test_resuming_preserves_original_date_and_intent(self):
        self.assertIn("original resolved ISO eating date", self.flow)
        self.assertIn("not automatically another meal", self.flow)
        self.assertIn("No durable pending-meal store is provided", self.flow)
        self.assertIn("reply to the pending question", self.coach_flow)
        self.assertIn("Use the original eating date", self.coach_flow)

    def test_no_live_model_guarantee_is_claimed(self):
        self.assertIn("not a Python vision validator", self.flow)
        self.assertIn("NOT evidence that a live model asked the right question", self.flow)
        self.assertIn("ledger cannot detect confident misidentification", self.skill)

    def test_acceptance_cases_cover_required_branches(self):
        document = json.loads((BASE / "evals/food-identity-confirmation.json").read_text(encoding="utf-8"))
        cases = document["cases"]
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("not executed", document["purpose"])
        expected = {"explicit_checkin_unknown_identity", "materially_ambiguous_identity",
                    "known_food_estimated_portion", "unusable_image", "clarification_same_meal",
                    "clarification_after_midnight", "calorie_advice_not_checkin", "explicit_partial_then_append",
                    "no_answer_no_autofinalize", "lost_context_or_multiple_pending"}
        self.assertEqual({case["id"] for case in cases}, expected)
        self.assertEqual(len(cases), len(expected))
        for case in cases:
            for field in ("user", "observation", "expect"):
                self.assertIsInstance(case[field], str)
                self.assertTrue(case[field].strip())
            self.assertTrue(case["forbid"])


if __name__ == "__main__":
    unittest.main()
