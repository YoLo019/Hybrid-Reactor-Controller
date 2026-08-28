# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[1]
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
for path in (MODEL_ROOT, FLEXIBILITY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import model_wind
from model_schema import STATE_INDEX
from metrics_source import build_y0, valve_equilibrium_from_state
from mpc_utils_out import build_output_prediction_maps, normalize_target_trajectory
from parameters import get_params
from run_e2_smoke import (
    assess_recovery,
    evaluate_constraints,
    make_e2_input_functions,
)
from run_e2_frequency_diagnostic import (
    build_case_config,
    frequency_metrics,
    validate_config,
)
from run_e2_formal import analyze_coarse_safety
from aggregate_e2_rays import aggregate_reports


CONSTRAINTS = {
    "net_load_target_pu": {"lower": 0.5, "upper": 1.0},
    "frequency_abs_hz": {"upper": 0.5},
    "coolant_average_abs_deviation_c": {"upper": 5.0},
    "neutron_power_pu": {"lower": 0.5, "upper": 1.1},
    "rod_speed_abs_spm": {"upper": 72.0},
    "valve_command_pu": {"lower": 0.0, "upper": 1.2},
    "valve_command_rate_abs_pu_s": {"upper": 0.05},
    "bess_power_abs_mw": {"upper": 5.0},
    "soc": {"lower": 0.1, "upper": 0.9},
}


def make_result(target):
    count = len(target)
    states = np.zeros((44, count))
    states[STATE_INDEX["SOC"], :] = 0.5
    return {
        "t": np.arange(count) * 0.5,
        "DT": 0.5,
        "Target_abs": np.asarray(target, dtype=float),
        "frequency_deviation_hz": np.zeros(count),
        "T_c_avg": np.full(count, 320.0),
        "T_ref": 320.0,
        "P_e": np.asarray(target, dtype=float),
        "P_n": np.full(count, 0.9),
        "rod_speed_spm": np.zeros(count),
        "valve_command_pu": np.full(count, 0.9),
        "bess_power_mw": np.zeros(count),
        "grid_disturbance_pu": np.zeros(count),
        "Y": states,
    }


class E2SmokeTests(unittest.TestCase):
    def test_ray_aggregation_requires_complete_consistent_identity(self):
        def report(index):
            return {
                "study_id": "aggregate-test",
                "input_definition": {"kind": "net_load_reference"},
                "system_scaling": {"model_system_base_mw": 100.0},
                "constraint_registry_id": "TEST-V1",
                "config_sha256": "config",
                "code_bundle_sha256": "code",
                "ray_count_total": 2,
                "ray_results": [
                    {
                        "ray_index": index,
                        "ray": {
                            "controller": "PID",
                            "frequency": {
                                "frequency_hz": 0.02,
                            },
                            "phase_rad": float(index) * np.pi,
                            "power_pu": 0.9,
                            "soc": 0.5,
                        },
                        "boundary_status": "bracketed_first_failure",
                        "safe_boundary_amplitude_pu": 0.1,
                        "first_failure_amplitude_pu": 0.11,
                        "boundary_width_pu": 0.01,
                        "active_constraint": "neutron_power_pu",
                        "first_violation_phase": "forcing",
                        "failure_recovery": {"complete": True},
                        "coarse_scan": {
                            "center_safe": True,
                            "non_star_shaped": False,
                        },
                    }
                ],
            }

        incomplete = aggregate_reports([report(0)])
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertEqual(incomplete["missing_ray_indices"], [1])
        complete = aggregate_reports(
            [report(0), report(1)], fallback_data_peak_system_pu=0.004
        )
        self.assertEqual(complete["status"], "complete")
        self.assertTrue(complete["structurally_ready_for_full_operating_matrix"])
        self.assertAlmostEqual(complete["boundary_summary_pu"]["median"], 0.1)
        self.assertAlmostEqual(complete["rays"][0]["boundary_to_data_peak_ratio"], 25.0)

    def test_frequency_case_identity_includes_phase_and_constraints(self):
        base = {
            "study_id": "cache-contract-test",
            "operating_point": {"nuclear_power_pu": 0.9, "bess_soc": 0.5},
            "amplitude_pu": 0.1,
            "phase_rad": 0.0,
            "simulation": {
                "warmup_s": 20.0,
                "dt_s": 0.5,
                "recovery": {
                    "duration_s": 180.0,
                    "sustain_s": 10.0,
                    "completion_limits": {
                        "power_abs_error_pu": 0.005,
                        "frequency_abs_hz": 0.02,
                        "coolant_average_abs_deviation_c": 0.5,
                        "bess_power_abs_mw": 0.25,
                    },
                },
            },
            "input_definition": {"kind": "net_load_reference"},
            "system_scaling": {"model_system_base_mw": 100.0},
            "constraint_registry_id": "TEST-CONSTRAINTS-V1",
            "mpc": {"horizon_steps": 30},
            "constraints": {"soc": {"lower": 0.1, "upper": 0.9}},
        }
        frequency = {
            "frequency_hz": 0.02,
            "cycles": 1,
            "evidence_class": "unit test",
        }
        phase_changed = dict(base, phase_rad=1.5707963267948966)
        constraint_changed = dict(
            base, constraints={"soc": {"lower": 0.2, "upper": 0.8}}
        )
        identity = build_case_config("PID", frequency, base, "runner", "metrics")
        self.assertNotEqual(
            identity,
            build_case_config("PID", frequency, phase_changed, "runner", "metrics"),
        )
        self.assertNotEqual(
            identity,
            build_case_config(
                "PID", frequency, constraint_changed, "runner", "metrics"
            ),
        )

    def test_formal_first_failure_and_non_star_detection(self):
        monotone = analyze_coarse_safety(
            [0.0, 0.1, 0.2, 0.3], [True, True, False, False]
        )
        self.assertEqual(
            monotone["first_failure_bracket"],
            {
                "lower_amplitude_pu": 0.1,
                "upper_amplitude_pu": 0.2,
                "from_safe": True,
                "to_safe": False,
            },
        )
        self.assertFalse(monotone["non_star_shaped"])

        non_star = analyze_coarse_safety(
            [0.0, 0.1, 0.2, 0.3], [True, False, True, False]
        )
        self.assertTrue(non_star["non_star_shaped"])
        self.assertEqual(
            non_star["first_failure_bracket"]["upper_amplitude_pu"], 0.1
        )

    def test_mpc_target_trajectory_broadcast_and_validation(self):
        np.testing.assert_allclose(
            normalize_target_trajectory(0.9, 3),
            np.asarray([0.9, 0.9, 0.9]),
        )
        np.testing.assert_allclose(
            normalize_target_trajectory([0.9, 0.91, 0.92], 3),
            np.asarray([0.9, 0.91, 0.92]),
        )
        with self.assertRaisesRegex(ValueError, "requires 3"):
            normalize_target_trajectory([0.9, 0.91], 3)

    def test_condensed_prediction_matches_stepwise_recurrence(self):
        ad = np.asarray([[0.9, 0.1], [0.0, 0.8]])
        bd = np.asarray([[0.2], [0.1]])
        x0 = np.asarray([0.3, -0.2])
        inputs = np.asarray([0.1, -0.05, 0.2, 0.0])
        initial_maps, input_maps = build_output_prediction_maps(
            ad, bd, (0, 1), len(inputs)
        )
        condensed = np.column_stack(
            [
                initial_maps[index] @ x0 + input_maps[index] @ inputs
                for index in range(2)
            ]
        )
        state = x0.copy()
        stepwise = []
        for value in inputs:
            state = ad @ state + bd[:, 0] * value
            stepwise.append(state.copy())
        np.testing.assert_allclose(condensed, np.asarray(stepwise), atol=1e-14)

    def test_frequency_diagnostic_rejects_duplicate_frequencies(self):
        config = {
            "frequencies": [
                {"frequency_hz": 0.005, "cycles": 2},
                {"frequency_hz": 0.005, "cycles": 4},
            ],
            "input_definition": {"kind": "net_load_reference"},
            "system_scaling": {"model_system_base_mw": 100.0},
            "simulation": {
                "recovery": {"duration_s": 180.0, "sustain_s": 10.0}
            },
        }
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_config(config)

    def test_zero_amplitude_has_undefined_gain_instead_of_crashing(self):
        time = np.arange(0.0, 30.5, 0.5)
        zeros = np.zeros_like(time)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zero_input.npz"
            np.savez(
                path,
                t=time,
                Target_abs=np.full_like(time, 0.9),
                input_signal_pu=zeros,
                Pe=np.full_like(time, 0.9),
                valve_command_pu=np.full_like(time, 0.9),
                DT=np.asarray([0.5]),
                Tc_avg=np.full_like(time, 320.0),
                frequency_deviation_hz=zeros,
                rod_speed_spm=zeros,
                bess_power_mw=zeros,
                SOC=np.full_like(time, 0.5),
            )
            metrics = frequency_metrics(path, 0.1, 20.0, 1.0)
        self.assertIsNone(metrics["gain"])
        self.assertIsNone(metrics["phase_error_deg"])
        self.assertEqual(metrics["gain_basis"], "undefined_zero_input")

    def test_valve_equilibrium_comes_from_actuator_state(self):
        state = np.zeros(44)
        state[STATE_INDEX["C_tg"]] = 2.0481 * 0.8636106932
        self.assertAlmostEqual(
            valve_equilibrium_from_state(state, {"C_tg0": 2.0481}),
            0.8636106932,
        )

    def test_reference_input_changes_only_target_and_stops_before_recovery(self):
        target, grid, signal, forcing_end = make_e2_input_functions(
            0.9, 0.05, 0.005, 0.0, 20.0, 1.0, "net_load_reference"
        )
        self.assertAlmostEqual(target(20.0) * 0.9, 0.9)
        self.assertAlmostEqual(target(70.0) * 0.9, 0.85)
        self.assertEqual(grid(70.0), 0.0)
        self.assertAlmostEqual(forcing_end, 220.0)
        self.assertEqual(signal(220.0), 0.0)
        self.assertAlmostEqual(target(220.0), 1.0)

    def test_physical_disturbance_keeps_reference_constant(self):
        target, grid, signal, forcing_end = make_e2_input_functions(
            0.9, 0.05, 0.005, 0.0, 20.0, 1.0, "grid_power_disturbance"
        )
        self.assertEqual(target(70.0), 1.0)
        self.assertAlmostEqual(grid(70.0), 0.05)
        self.assertAlmostEqual(grid(220.0), 0.0)
        self.assertAlmostEqual(signal(forcing_end), 0.0)

    def test_positive_grid_disturbance_enters_swing_equation_as_extra_demand(self):
        params, initial_conditions = get_params()
        state = build_y0(params, initial_conditions)
        baseline = model_wind.pwf_model(
            0.0, state, params, initial_conditions, p_grid_disturbance_pu=0.0
        )
        disturbed = model_wind.pwf_model(
            0.0, state, params, initial_conditions, p_grid_disturbance_pu=0.02
        )
        expected_change = -0.02 / (2.0 * float(params["H_g"]))
        self.assertAlmostEqual(
            disturbed[STATE_INDEX["omega_g"]]
            - baseline[STATE_INDEX["omega_g"]],
            expected_change,
        )

    def test_recovery_requires_sustained_return_to_frozen_neighborhood(self):
        result = make_result([0.9] * 41)
        result["t"] = np.arange(41, dtype=float) * 0.5
        recovery = {
            "sustain_s": 5.0,
            "completion_limits": {
                "power_abs_error_pu": 0.005,
                "frequency_abs_hz": 0.02,
                "coolant_average_abs_deviation_c": 0.5,
                "bess_power_abs_mw": 0.25,
            },
        }
        self.assertTrue(assess_recovery(result, 10.0, recovery)["complete"])
        result["frequency_deviation_hz"][-3:] = 0.03
        self.assertFalse(assess_recovery(result, 10.0, recovery)["complete"])

    def test_safe_active_constraint_uses_normalized_margin(self):
        assessment = evaluate_constraints(make_result([0.9, 0.99, 0.9]), CONSTRAINTS)
        self.assertTrue(assessment["safe"])
        self.assertEqual(assessment["active_constraint"], "net_load_target_pu")

    def test_first_violation_records_constraint_and_time(self):
        assessment = evaluate_constraints(make_result([0.9, 1.01, 0.9]), CONSTRAINTS)
        self.assertFalse(assessment["safe"])
        self.assertEqual(assessment["active_constraint"], "net_load_target_pu")
        self.assertEqual(assessment["first_violation_time_s"], 0.5)

    def test_formal_constraints_do_not_treat_target_as_plant_violation(self):
        formal_constraints = {
            name: value
            for name, value in CONSTRAINTS.items()
            if name != "net_load_target_pu"
        }
        assessment = evaluate_constraints(
            make_result([0.9, 1.25, 0.9]), formal_constraints
        )
        self.assertTrue(assessment["safe"])
        self.assertNotIn("net_load_target_pu", assessment["constraints"])


if __name__ == "__main__":
    unittest.main()
