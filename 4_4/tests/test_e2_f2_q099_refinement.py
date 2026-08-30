# -*- coding: utf-8 -*-
import json
import sys
import unittest
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e2_f2_q099_refinement import (  # noqa: E402
    EXPECTED_RAY_COUNT,
    EXTENSION_TARGET_INDICES,
    validate_refinement_inputs,
)


BASE_CONFIG = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f2_cross_operating_slow_v1.json"
)
REFINEMENT_CONFIG = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f2_q099_refinement_v1.json"
)
PRECHECK_AGGREGATE = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "runs"
    / "e2_f2_q099_mpc_upper_extension_v1"
    / "e2_f2_q0.99_precheck_extended_aggregate.json"
)


class E2F2Q099RefinementTests(unittest.TestCase):
    def test_refinement_input_gate_uses_complete_merged_precheck(self):
        validation = validate_refinement_inputs(
            BASE_CONFIG, REFINEMENT_CONFIG, PRECHECK_AGGREGATE
        )
        self.assertTrue(validation["pass"])
        self.assertEqual(validation["expected_ray_count"], EXPECTED_RAY_COUNT)

    def test_extension_target_is_fixed_and_joint_brackets_exist(self):
        extension = json.loads(PRECHECK_AGGREGATE.read_text(encoding="utf-8"))[
            "extension_review"
        ]
        self.assertEqual(
            sorted(extension["target_ray_indices"]), list(EXTENSION_TARGET_INDICES)
        )
        aggregate = json.loads(PRECHECK_AGGREGATE.read_text(encoding="utf-8"))
        self.assertEqual(
            aggregate["gating_issues"]["joint_bracket_missing_ray_indices"], []
        )


if __name__ == "__main__":
    unittest.main()
