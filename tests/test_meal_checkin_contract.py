"""Text-contract checks and real ledger regressions, NOT live dialogue/model evals."""
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_meal_log import mod, food, request

BASE = Path(__file__).resolve().parents[1] / "diet-tracking-analysis"


class MealCheckinContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (BASE / "SKILL.md").read_text()
        cls.flow = (BASE / "references/standalone-flow.md").read_text()
        cls.coach = (BASE / "references/coach-flows.md").read_text()

    def test_checkin_confirms_consumption_without_completion_phrase(self):
        self.assertIn('clearly established intake check-in context counts as confirmation of consumption', self.skill)
        self.assertIn('another "吃完了"', self.skill)
        self.assertIn('Do not require another', self.flow)
        self.assertIn('save once without another', self.coach)

    def test_premeal_and_future_override_shorthand(self):
        self.assertIn('Explicit not-yet-eaten, future or premeal/advice intent takes precedence', self.flow)
        self.assertIn('"午餐打卡，还没吃，先看看搭配" is advice only', self.flow)
        self.assertIn('Explicit "还没吃", future meals and premeal advice override', self.skill)
        self.assertIn('premeal advice overrides', self.coach)

    def test_bare_photos_and_ambiguous_intent_do_not_auto_save(self):
        self.assertIn('A standalone food photo does NOT establish check-in intent', self.skill)
        self.assertIn('ask once whether this is an intake check-in or premeal advice', self.skill)
        self.assertIn('do not restart intent confirmation after an identity or portion answer', self.flow)

    def test_identity_gate_and_usable_estimates_stay_separate(self):
        self.assertIn('Identity uncertainty still requires clarification', self.flow)
        self.assertIn('Reasonable labelled portion estimates suffice', self.flow)
        self.assertIn('If there is no useful quantity estimate, ask how much', self.flow)
        self.assertIn('no meal write while any food identity remains unresolved', self.flow)

    def test_clarification_retains_intent_and_original_date(self):
        self.assertIn('Keep original intent/date during short clarification replies', self.flow)
        self.assertIn('original resolved ISO eating date', self.flow)
        self.assertIn('For advice or future intent, answer without writing intake', self.flow)
        self.assertIn('Only an explicit change', self.flow)
        self.assertIn('newly stated actual eating date', self.flow)

    def test_leftovers_use_existing_ids_and_current_quantity(self):
        self.assertIn('use `update`/`scale` on that food, not `add`', self.flow)
        self.assertIn('compute the scale against the CURRENT quantity', self.flow)
        self.assertIn('Use `delete` for a wholly uneaten item', self.flow)
        self.assertIn('Use a new correction request ID', self.flow)
        self.assertIn('they do not add a duplicate meal', self.coach)

    def test_acceptance_scenarios_cover_positive_and_negative_branches(self):
        doc = json.loads((BASE / 'evals/meal-checkin-intent.json').read_text())
        expected = {'explicit_checkin', 'established_checkin', 'bare_photo',
                    'ambiguous_then_checkin', 'explicit_premeal_override', 'future_override',
                    'unknown_identity', 'short_answer_after_midnight', 'advice_clarification',
                    'portion_clarification', 'leftovers_correction', 'already_corrected_leftovers',
                    'wholly_uneaten', 'explicit_change_to_consumed'}
        self.assertEqual(doc['schema_version'], 1)
        self.assertEqual({case['id'] for case in doc['cases']}, expected)
        self.assertEqual(len(doc['cases']), len(expected))
        for case in doc['cases']:
            for field in ('user', 'observation', 'expect'):
                self.assertIsInstance(case[field], str)
                self.assertTrue(case[field].strip())
            self.assertTrue(case['forbid'])
            self.assertTrue(all(isinstance(rule, str) and rule.strip() for rule in case['forbid']))

    def test_offline_tests_do_not_claim_model_validation(self):
        self.assertIn('do not execute a live conversational model', self.flow)
        self.assertIn('run these scenarios with the deployed model separately', self.flow)


class MealCheckinLedgerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = mod.Ledger(Path(self.temp.name), 'Asia/Tokyo')

    def test_leftovers_after_prior_edit_do_not_duplicate_or_double_scale(self):
        original = request(foods=[food(), food('vegetables', 80)])
        saved = self.ledger.execute('add', original)
        rice_id = saved['meal']['foods'][0]['id']
        unchanged = saved['meal']['foods'][1]
        correction = dict(request_id='portion-edit', date=saved['date'],
                          meal_id=saved['meal_id'], food_id=rice_id,
                          expected_revision=1, scale=100 / 150)
        self.ledger.execute('update', correction)
        current = self.ledger.load(saved['date'])[0]
        leftovers = dict(correction, request_id='leftovers-edit',
                         expected_revision=current['revision'],
                         scale=75 / current['foods'][0]['quantity'])
        updated = self.ledger.execute('update', leftovers)
        self.assertTrue(self.ledger.execute('update', leftovers)['duplicate'])
        # A delayed retry of the original add must not resurrect its old portions.
        self.assertTrue(self.ledger.execute('add', original)['duplicate'])
        rows = self.ledger.load(saved['date'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], saved['meal_id'])
        self.assertEqual(rows[0]['foods'][0]['id'], rice_id)
        # The ledger rounds scale factors and stored nutrients to four decimals.
        self.assertAlmostEqual(rows[0]['foods'][0]['quantity'], 75, delta=0.01)
        self.assertAlmostEqual(rows[0]['foods'][0]['calories'], 100, delta=0.01)
        self.assertEqual(rows[0]['foods'][1], unchanged)
        self.assertAlmostEqual(updated['logged_totals']['calories'], 180, delta=0.01)

    def test_wholly_uneaten_item_deleted_without_readding_meal(self):
        saved = self.ledger.execute('add', request(foods=[food(), food('vegetables', 80)]))
        correction = dict(request_id='uneaten-rice', date=saved['date'],
                          meal_id=saved['meal_id'], food_id=saved['meal']['foods'][0]['id'],
                          expected_revision=1)
        self.ledger.execute('delete', correction)
        self.assertTrue(self.ledger.execute('delete', correction)['duplicate'])
        rows = self.ledger.load(saved['date'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], saved['meal_id'])
        self.assertEqual(rows[0]['foods'], [saved['meal']['foods'][1]])
        self.assertEqual(rows[0]['calories'], 80)


if __name__ == '__main__':
    unittest.main()
