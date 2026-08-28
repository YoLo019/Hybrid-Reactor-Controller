# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
for path in (MODEL_ROOT, FLEXIBILITY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_e2_recovery_sensitivity import (
    build_run_config,
    expand_cases,
    pending_case_indices,
    validate_config,
)


def make_config():
    return {
        "execution_status": "contract_frozen_runner_pending",
        "controller": "MPC",
        "input_definition": {"kind": "net_load_reference"},
        "amplitudes_pu": [0.04, 0.1],
        "recovery_durations_s": [360.0, 720.0],
        "baseline_cases": {"recovery_duration_s": 180.0},
        "acceptance": {
            "expected_new_case_count": 4,
            "frozen_power_error_limit_pu": 0.005,
        },
        "simulation": {
            "dt_s": 0.5,
            "warmup_s": 20.0,
            "recovery_sustain_s": 10.0,
            "completion_limits": {"power_abs_error_pu": 0.005},
        },
        "study_id": "test",
        "operating_point": {"nuclear_power_pu": 0.9, "bess_soc": 0.5},
        "phase_rad": 0.0,
        "system_scaling": {},
        "constraint_registry_id": "test",
        "mpc": {},
        "constraints": {},
    }


class RecoverySensitivityTests(unittest.TestCase):
    def test_matrix_order_and_case_mapping_are_deterministic(self):
        config = make_config()
        validate_config(config)
        matrix = expand_cases(config)
        self.assertEqual(
            matrix,
            [
                {"amplitude_pu": 0.04, "recovery_duration_s": 360.0},
                {"amplitude_pu": 0.04, "recovery_duration_s": 720.0},
                {"amplitude_pu": 0.1, "recovery_duration_s": 360.0},
                {"amplitude_pu": 0.1, "recovery_duration_s": 720.0},
            ],
        )
        run_config = build_run_config(config, matrix[1])
        self.assertEqual(run_config["amplitude_pu"], 0.04)
        self.assertEqual(run_config["simulation"]["recovery"]["duration_s"], 720.0)
        self.assertEqual(
            run_config["simulation"]["recovery"]["completion_limits"]
            ["power_abs_error_pu"],
            0.005,
        )

    def test_rejects_duration_not_longer_than_baseline(self):
        config = make_config()
        config["recovery_durations_s"] = [180.0]
        config["acceptance"]["expected_new_case_count"] = 2
        with self.assertRaisesRegex(ValueError, "exceed the baseline"):
            validate_config(config)

    def test_pending_case_indices_skip_existing_summaries(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            (output_dir / "e2_recovery_case_0001_summary.json").write_text(
                "{}", encoding="utf-8"
            )
            self.assertEqual(pending_case_indices(output_dir, 3), [0, 2])


if __name__ == "__main__":
    unittest.main()
