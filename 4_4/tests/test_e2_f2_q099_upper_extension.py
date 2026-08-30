# -*- coding: utf-8 -*-
import json
import sys
import unittest
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e2_f2_q099_upper_extension import (  # noqa: E402
    EXPECTED_TARGET_RAY_INDICES,
    validate_extension_inputs,
)


BASE_CONFIG = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f2_cross_operating_slow_v1.json"
)
EXTENSION_CONFIG = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f2_q099_mpc_upper_extension_v1.json"
)
BASE_AGGREGATE = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "runs"
    / "e2_f2_cross_operating_q099_precheck_v1"
    / "e2_f2_q0.99_precheck_aggregate.json"
)


class E2F2Q099UpperExtensionTests(unittest.TestCase):
    def test_registered_target_set_and_upper_grid(self):
        extension = json.loads(EXTENSION_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(
            tuple(extension["target_ray_indices"]), EXPECTED_TARGET_RAY_INDICES
        )
        self.assertGreater(min(extension["extension_amplitude_grid_pu"]), 0.3)

    def test_base_aggregate_selects_only_the_nine_right_censored_rays(self):
        validation = validate_extension_inputs(
            BASE_CONFIG, EXTENSION_CONFIG, BASE_AGGREGATE
        )
        self.assertTrue(validation["pass"])
        self.assertEqual(
            validation["target_ray_indices"], list(EXPECTED_TARGET_RAY_INDICES)
        )
        self.assertEqual(validation["base_search_upper_amplitude_pu"], 0.3)


if __name__ == "__main__":
    unittest.main()
