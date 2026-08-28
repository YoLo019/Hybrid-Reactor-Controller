# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

MODEL_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = MODEL_ROOT / "validation"
for path in (MODEL_ROOT, VALIDATION_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from validate_model import run_validation


class ModelValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = run_validation()

    def test_full_power_validation(self):
        failed = [name for name, passed in self.results["checks"].items() if not passed]
        self.assertEqual(failed, [], msg=f"Failed checks: {failed}")

    def test_reduced_power_points_and_dynamic_baseline(self):
        self.assertTrue(self.results["dynamic_baseline_pass"])
        for result in self.results["reduced_power_points"].values():
            self.assertTrue(result["pass"])

    def test_g0_evidence_is_complete(self):
        self.assertTrue(self.results["g0_pass"])
        self.assertEqual(self.results["g0_remaining"], [])
        self.assertTrue(self.results["parameter_provenance"]["pass"])


if __name__ == "__main__":
    unittest.main()
