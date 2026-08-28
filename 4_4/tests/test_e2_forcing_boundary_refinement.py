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

from run_e2_forcing_boundary_refinement import aggregate_reports, expand_rays, validate_config


CONFIG_PATH = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f1_forcing_boundary_refinement_v1.json"
)


def make_report(index, config):
    frequency = config["frequencies"][index // 2]
    phase = config["phases_rad"][index % 2]
    result = {
        "ray_index": index,
        "ray": {
            "controller": "MPC",
            "power_pu": 0.9,
            "soc": 0.5,
            "frequency_hz": float(frequency["frequency_hz"]),
            "cycles": 1.0,
            "evidence_class": frequency["evidence_class"],
            "phase_rad": float(phase),
        },
        "initial_forcing_bracket": {
            "lower_amplitude_pu": 0.1322,
            "upper_amplitude_pu": 0.2168,
        },
        "final_forcing_boundary": {
            "conservative_safe_amplitude_pu": 0.19565,
            "first_failure_amplitude_pu": 0.2009375,
            "bracket_width_pu": 0.0052875,
            "tolerance_pu": 0.01,
            "precision_status": "within_frozen_tolerance",
        },
        "boundary_status": "bracketed_forcing_first_failure",
        "bisection_iterations": 4,
        "forcing_stage_non_star_shaped": False,
        "solver_failure_total": 0,
        "recovery_phase_physical_violation_case_files": [],
        "endpoint_cases": {
            "lower": {"forcing_stage_safe": True},
            "upper": {"forcing_stage_safe": False},
        },
        "evaluations": [{"case_hash": "case-%d" % index}],
    }
    return {
        "study_id": config["study_id"],
        "config_sha256": "config",
        "core_code_bundle_sha256": "core",
        "refinement_runner_sha256": "runner",
        "ray_count_total": 6,
        "ray_results": [result],
    }


class E2ForcingBoundaryRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_config_expands_to_six_registered_rays(self):
        validation = validate_config(self.config)
        self.assertTrue(validation["pass"])
        rays = expand_rays(self.config)
        self.assertEqual(len(rays), 6)
        self.assertEqual(
            [(ray["frequency_hz"], ray["phase_rad"]) for ray in rays[:2]],
            [
                (0.0003076171875, 0.0),
                (0.0003076171875, 1.5707963267948966),
            ],
        )

    def test_aggregate_freezes_contract_only_after_all_six_rays(self):
        reports = [make_report(index, self.config) for index in range(6)]
        aggregate = aggregate_reports(reports, self.config)
        self.assertEqual(aggregate["status"], "complete")
        self.assertEqual(aggregate["observed_ray_count"], 6)
        self.assertTrue(aggregate["dual_layer_runner_contract_ready"])

        incomplete = aggregate_reports(reports[:5], self.config)
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertFalse(incomplete["dual_layer_runner_contract_ready"])

    def test_controller_is_frozen_to_mpc(self):
        invalid = copy.deepcopy(self.config)
        invalid["controller"] = "PID"
        with self.assertRaises(ValueError):
            validate_config(invalid)


if __name__ == "__main__":
    unittest.main()
