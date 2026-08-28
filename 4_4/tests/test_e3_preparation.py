# -*- coding: utf-8 -*-

import copy
import json
import sys
import unittest
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e3_formal import (
    expand_rays,
    make_e3_input_functions,
    validate_config,
)
from aggregate_e3_rays import aggregate_reports


CONFIG_ROOT = (
    MODEL_ROOT.parent / "research_execution" / "04_experiments" / "configs"
)


class E3PreparationTests(unittest.TestCase):
    def load_config(self, name):
        return json.loads((CONFIG_ROOT / name).read_text(encoding="utf-8"))

    def test_ramp_hold_return_waveform_is_exact(self):
        _, _, signal, boundaries = make_e3_input_functions(
            0.9, 0.1, 0.01, 2.0, 1, 1.0, "net_load_reference"
        )
        self.assertAlmostEqual(boundaries["ramp_duration_s"], 10.0)
        self.assertAlmostEqual(boundaries["forcing_end_s"], 23.0)
        expected = {
            0.0: 0.0,
            1.0: 0.0,
            6.0: 0.05,
            11.0: 0.1,
            13.0: 0.1,
            18.0: 0.05,
            23.0: 0.0,
        }
        for time_s, value in expected.items():
            self.assertAlmostEqual(signal(time_s), value)

    def test_dual_domain_semantics_remain_separate(self):
        ref_target, ref_grid, _, _ = make_e3_input_functions(
            0.9, 0.1, 0.01, 0.0, 1, 0.0, "net_load_reference"
        )
        dist_target, dist_grid, _, _ = make_e3_input_functions(
            0.9, 0.1, 0.01, 0.0, 1, 0.0, "grid_power_disturbance"
        )
        self.assertAlmostEqual(ref_target(5.0), (0.9 - 0.05) / 0.9)
        self.assertEqual(ref_grid(5.0), 0.0)
        self.assertEqual(dist_target(5.0), 1.0)
        self.assertAlmostEqual(dist_grid(5.0), 0.05)

    def test_direction_changes_only_signal_sign(self):
        _, _, positive, _ = make_e3_input_functions(
            0.9, 0.1, 0.01, 0.0, 1, 0.0, "grid_power_disturbance"
        )
        _, _, negative, _ = make_e3_input_functions(
            0.9, 0.1, 0.01, 0.0, -1, 0.0, "grid_power_disturbance"
        )
        self.assertAlmostEqual(positive(5.0), -negative(5.0))

    def test_formal_ray_expansion_is_deterministic_and_complete(self):
        config = self.load_config("e3_f1_center_p99_wave_v1.json")
        first = expand_rays(config)
        second = expand_rays(config)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertEqual(
            {ray["input_definition"]["domain_id"] for ray in first},
            {"D_ref", "D_dist"},
        )
        self.assertEqual({ray["controller"] for ray in first}, {"PID", "MPC"})
        self.assertEqual({ray["direction"] for ray in first}, {-1, 1})
        self.assertEqual(
            {ray["hold_level"]["duration_s"] for ray in first}, {0.0, 600.0}
        )

    def test_duration_cap_rejects_accidental_oversized_matrix(self):
        config = self.load_config("e3_f1_center_p99_wave_v1.json")
        decision = validate_config(config)
        self.assertTrue(decision["pass"])
        self.assertLessEqual(
            decision["maximum_case_duration_s"], config["max_case_duration_s"]
        )
        oversized = copy.deepcopy(config)
        oversized["max_case_duration_s"] = 100.0
        with self.assertRaises(ValueError):
            validate_config(oversized)

    def test_aggregation_requires_complete_consistent_rays(self):
        def report(index):
            return {
                "study_id": "test",
                "formal_claim": True,
                "config_sha256": "config",
                "mapping_sha256": "mapping",
                "code_bundle_sha256": "code",
                "constraint_registry_id": "constraints",
                "ray_count_total": 2,
                "ray_results": [
                    {
                        "ray_index": index,
                        "ray": {
                            "input_definition": {"domain_id": "D_ref"},
                            "controller": "PID",
                            "direction": 1,
                            "rate_level": {"label": "p99", "rate_pu_per_s": 1e-4},
                            "hold_level": {"label": "triangle", "duration_s": 0.0},
                            "power_pu": 0.9,
                            "soc": 0.5,
                        },
                        "coarse_scan": {"center_safe": True, "non_star_shaped": False},
                        "boundary_status": "right_censored_at_search_upper",
                        "safe_boundary_amplitude_pu": 0.1,
                        "first_failure_amplitude_pu": None,
                        "boundary_width_pu": None,
                        "active_constraint": None,
                        "first_violation_phase": None,
                        "failure_recovery": None,
                        "solver_failures": 0,
                        "waveform_returned_to_zero": True,
                    }
                ],
            }

        complete = aggregate_reports([report(0), report(1)])
        self.assertEqual(complete["status"], "complete")
        self.assertTrue(complete["structurally_ready_for_next_wave"])
        incomplete = aggregate_reports([report(0)])
        self.assertEqual(incomplete["missing_ray_indices"], [1])
        self.assertFalse(incomplete["structurally_ready_for_next_wave"])


if __name__ == "__main__":
    unittest.main()
