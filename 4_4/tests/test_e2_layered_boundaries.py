# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from analyze_e2_layered_boundaries import (
    boundary_from_rows,
    build_cross_frequency_summary,
    forcing_stage_safe,
)


def make_case(violation_time):
    return {
        "case_config": {"forcing_end_s": 100.0},
        "constraints": {
            "constraints": {
                "neutron_power_pu": {"first_violation_time_s": violation_time},
                "soc": {"first_violation_time_s": None},
            }
        },
    }


class E2LayeredBoundaryTests(unittest.TestCase):
    def test_forcing_stage_excludes_recovery_phase_violation(self):
        self.assertFalse(forcing_stage_safe(make_case(99.5)))
        self.assertTrue(forcing_stage_safe(make_case(100.0)))
        self.assertTrue(forcing_stage_safe(make_case(120.0)))
        self.assertTrue(forcing_stage_safe(make_case(None)))

    def test_boundary_marks_coarse_first_failure_and_non_star(self):
        rows = [
            {"amplitude_pu": 0.0, "safe": True},
            {"amplitude_pu": 0.04, "safe": False},
            {"amplitude_pu": 0.13, "safe": True},
            {"amplitude_pu": 0.22, "safe": False},
        ]
        boundary = boundary_from_rows(rows, "safe", 0.01)
        self.assertEqual(boundary["status"], "bracketed_first_failure")
        self.assertEqual(boundary["conservative_safe_amplitude_pu"], 0.0)
        self.assertEqual(boundary["first_failure_amplitude_pu"], 0.04)
        self.assertEqual(boundary["precision_status"], "coarse_bracket_needs_refinement")
        self.assertTrue(boundary["non_star_shaped"])

    def test_boundary_accepts_refined_monotonic_bracket(self):
        rows = [
            {"amplitude_pu": 0.0, "safe": True},
            {"amplitude_pu": 0.19565, "safe": True},
            {"amplitude_pu": 0.2009375, "safe": False},
        ]
        boundary = boundary_from_rows(rows, "safe", 0.01)
        self.assertEqual(boundary["precision_status"], "within_frozen_tolerance")
        self.assertFalse(boundary["non_star_shaped"])
        self.assertAlmostEqual(boundary["bracket_width_pu"], 0.0052875)

    def test_cross_frequency_summary_blocks_mixed_code_identity(self):
        ray = {
            "controller": "PID",
            "phase_rad": 0.0,
            "joint_first_failure_recovery_limited": False,
            "forcing_stage_physical_boundary": {
                "conservative_safe_amplitude_pu": 0.1,
                "first_failure_amplitude_pu": 0.11,
                "precision_status": "within_frozen_tolerance",
            },
            "joint_recovery_complete_boundary": {
                "conservative_safe_amplitude_pu": 0.1,
                "first_failure_amplitude_pu": 0.11,
            },
            "minimum_forcing_minus_joint_safe_pu": 0.0,
            "forcing_to_joint_safe_ratio_lower_bound": 1.0,
        }
        runs = [
            {"study_id": "old", "code_bundle_sha256": "old", "rays": [ray]},
            {"study_id": "new-1", "code_bundle_sha256": "new", "rays": [ray]},
            {"study_id": "new-2", "code_bundle_sha256": "new", "rays": [ray]},
        ]
        summary = build_cross_frequency_summary(runs)
        self.assertFalse(summary["cross_frequency_code_identity_consistent"])
        self.assertFalse(summary["formal_cross_frequency_comparison_ready"])
        self.assertEqual(summary["reference_code_bundle_sha256"], "new")
        self.assertEqual(summary["identity_outlier_study_ids"], ["old"])


if __name__ == "__main__":
    unittest.main()
