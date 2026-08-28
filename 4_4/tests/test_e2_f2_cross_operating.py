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

from run_e2_f2_cross_operating import (
    _first_failure_bracket,
    aggregate_reports,
    expand_rays,
    validate_config,
)


CONFIG_PATH = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "e2_f2_cross_operating_slow_v1.json"
)


def _coarse(label):
    return {
        "label": label,
        "status": "bracketed_first_failure",
        "center_safe": True,
        "all_safe": False,
        "first_failure_bracket": {
            "lower_amplitude_pu": 0.1322,
            "upper_amplitude_pu": 0.2168,
            "from_safe": True,
            "to_safe": False,
        },
        "transitions": [
            {
                "lower_amplitude_pu": 0.1322,
                "upper_amplitude_pu": 0.2168,
                "from_safe": True,
                "to_safe": False,
            }
        ],
        "non_star_shaped": False,
        "search_upper_amplitude_pu": 0.3,
    }


def make_report(index, config):
    ray = expand_rays(config, "q0.99")[index]
    result = {
        "wave_ray_index": index,
        "ray": ray,
        "coarse_scan": {
            "amplitude_grid_pu": list(config["amplitude_grid_pu"]),
            "evaluation_count": 7,
            "forcing_stage": _coarse("forcing_stage_safe"),
            "joint_recovery": _coarse("joint_valid_for_boundary"),
        },
        "forcing_stage_boundary": _coarse("forcing_stage_safe"),
        "joint_recovery_complete_boundary": _coarse("joint_valid_for_boundary"),
        "solver_failure_total": 0,
        "recovery_phase_physical_violation_case_files": [],
        "evaluations": [{"case_hash": "case-%d" % index}],
    }
    return {
        "study_id": config["study_id"],
        "mode": "precheck",
        "frequency_label": "q0.99",
        "config_sha256": "config",
        "core_code_bundle_sha256": "core",
        "f2_runner_sha256": "runner",
        "ray_count_total": 72,
        "ray_count_in_report": 1,
        "ray_results": [result],
    }


class E2F2CrossOperatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_config_expands_to_216_and_q099_to_72(self):
        validation = validate_config(self.config)
        self.assertTrue(validation["pass"])
        self.assertEqual(len(expand_rays(self.config)), 216)
        rays = expand_rays(self.config, "q0.99")
        self.assertEqual(len(rays), 72)
        self.assertEqual(
            (rays[0]["power_pu"], rays[0]["soc"], rays[0]["phase_rad"], rays[0]["controller"]),
            (0.8, 0.2, 0.0, "PID"),
        )
        self.assertEqual(
            (rays[1]["phase_rad"], rays[1]["controller"]),
            (0.0, "MPC"),
        )

    def test_per_ray_bracket_marks_first_transition(self):
        rows = [
            {"amplitude_pu": 0.0, "forcing_stage_safe": True},
            {"amplitude_pu": 0.1322, "forcing_stage_safe": True},
            {"amplitude_pu": 0.2168, "forcing_stage_safe": False},
            {"amplitude_pu": 0.3, "forcing_stage_safe": False},
        ]
        bracket = _first_failure_bracket(rows, "forcing_stage_safe")
        self.assertEqual(bracket["status"], "bracketed_first_failure")
        self.assertEqual(
            bracket["first_failure_bracket"],
            {
                "lower_amplitude_pu": 0.1322,
                "upper_amplitude_pu": 0.2168,
                "from_safe": True,
                "to_safe": False,
            },
        )
        self.assertFalse(bracket["non_star_shaped"])

    def test_precheck_aggregate_requires_all_72_reports(self):
        reports = [make_report(index, self.config) for index in range(72)]
        aggregate = aggregate_reports(reports, self.config, "q0.99", "precheck")
        self.assertEqual(aggregate["status"], "complete")
        self.assertEqual(aggregate["observed_ray_count"], 72)
        self.assertTrue(aggregate["precheck_gate_pass"])

        incomplete = aggregate_reports(reports[:71], self.config, "q0.99", "precheck")
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertFalse(incomplete["precheck_gate_pass"])

    def test_f1_case_reuse_is_rejected(self):
        invalid = copy.deepcopy(self.config)
        invalid["identity_policy"]["reuse_f1_center_cases"] = True
        with self.assertRaises(ValueError):
            validate_config(invalid)


if __name__ == "__main__":
    unittest.main()
