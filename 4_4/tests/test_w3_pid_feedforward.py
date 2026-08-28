# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "4_4"
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

import model_wind
from model_schema import STATE_INDEX, STATE_NAMES
from w3_pid_feedforward import PREVIEW_LEAD_S, PreviewPIDFeedforwardController


def make_state(pressure=6.0, omega=1.0):
    state = np.zeros(len(STATE_NAMES), dtype=float)
    state[STATE_INDEX["p_s"]] = pressure
    state[STATE_INDEX["omega_g"]] = omega
    return state


def make_params(rate_limit=0.05):
    return {
        "P_load_ref": 0.5,
        "R_droop": 0.05,
        "valve_rate_limit_pu_s": rate_limit,
    }


class W3PIDFeedforwardTests(unittest.TestCase):
    def test_uses_same_issue_time_provider_contract_at_frozen_lead(self):
        calls = []

        def provider(**request):
            calls.append(request)
            return np.asarray([0.91])

        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        controller.compute_command(
            issue_time_s=12.0,
            state=make_state(),
            params=make_params(rate_limit=10.0),
            initial_conditions={"p_s0": 6.0},
            current_target_power_abs=0.9,
            forecast_type="ridge_direct_ar",
            forecast_provider=provider,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["issue_time_s"], 12.0)
        np.testing.assert_array_equal(
            calls[0]["target_times_s"], [12.0 + PREVIEW_LEAD_S]
        )
        self.assertEqual(calls[0]["forecast_type"], "ridge_direct_ar")
        self.assertEqual(calls[0]["issue_value_pu"], 0.9)

    def test_actual_forecast_never_reads_future_truth(self):
        def provider(**request):
            return np.full(len(request["target_times_s"]), request["issue_value_pu"])

        def forbidden_future_truth(_):
            raise AssertionError("actual forecast path accessed future truth")

        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        controller.compute_command(
            issue_time_s=0.0,
            state=make_state(),
            params=make_params(rate_limit=10.0),
            initial_conditions={"p_s0": 6.0},
            current_target_power_abs=0.9,
            forecast_type="persistence",
            forecast_provider=provider,
            target_function=forbidden_future_truth,
        )

    def test_persistence_degenerates_to_current_reference(self):
        current_reference = 0.87

        def persistence_provider(**request):
            return np.full(len(request["target_times_s"]), request["issue_value_pu"])

        state = make_state(pressure=6.0, omega=1.002)
        params = make_params(rate_limit=10.0)
        expected_params = params.copy()
        expected_params["P_load_ref"] = current_reference
        expected = model_wind.compute_turbine_command_pu(
            0.0, state, expected_params, {"p_s0": 6.0}
        )

        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        actual = controller.compute_command(
            issue_time_s=0.0,
            state=state,
            params=params,
            initial_conditions={"p_s0": 6.0},
            current_target_power_abs=current_reference,
            forecast_type="persistence",
            forecast_provider=persistence_provider,
        )

        self.assertAlmostEqual(actual, expected)
        self.assertEqual(controller.last_preview_reference_pu, current_reference)
        self.assertEqual(params["P_load_ref"], 0.5)

    def test_pid_amplitude_and_rate_limits_are_applied(self):
        def high_provider(**_):
            return np.asarray([2.0])

        controller = PreviewPIDFeedforwardController(0.8, 0.5)
        first = controller.compute_command(
            issue_time_s=0.0,
            state=make_state(),
            params=make_params(rate_limit=0.05),
            initial_conditions={"p_s0": 6.0},
            current_target_power_abs=0.9,
            forecast_type="ridge_direct_ar",
            forecast_provider=high_provider,
        )
        second = controller.compute_command(
            issue_time_s=0.5,
            state=make_state(),
            params=make_params(rate_limit=0.05),
            initial_conditions={"p_s0": 6.0},
            current_target_power_abs=0.9,
            forecast_type="ridge_direct_ar",
            forecast_provider=high_provider,
        )

        self.assertAlmostEqual(first, 0.825)
        self.assertAlmostEqual(second, 0.85)
        self.assertLessEqual(second, 1.2)

    def test_fails_closed_on_missing_provider_and_nonmonotonic_time(self):
        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        for forecast_type in ("persistence", "ridge_direct_ar"):
            with self.subTest(forecast_type=forecast_type):
                with self.assertRaises(ValueError):
                    controller.compute_command(
                        0.0,
                        make_state(),
                        make_params(),
                        {"p_s0": 6.0},
                        0.9,
                        forecast_type,
                    )

        provider = lambda **request: np.full(
            len(request["target_times_s"]), request["issue_value_pu"]
        )
        controller.compute_command(
            0.0,
            make_state(),
            make_params(),
            {"p_s0": 6.0},
            0.9,
            "persistence",
            forecast_provider=provider,
        )
        with self.assertRaises(ValueError):
            controller.compute_command(
                0.0,
                make_state(),
                make_params(),
                {"p_s0": 6.0},
                0.9,
                "persistence",
                forecast_provider=provider,
            )

    def test_perfect_foresight_requires_explicit_opt_in(self):
        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        with self.assertRaises(ValueError):
            controller.compute_command(
                0.0,
                make_state(),
                make_params(),
                {"p_s0": 6.0},
                0.9,
                "perfect_foresight",
                target_function=lambda _: 0.9,
            )

        command = controller.compute_command(
            0.0,
            make_state(),
            make_params(rate_limit=10.0),
            {"p_s0": 6.0},
            0.9,
            "perfect_foresight",
            target_function=lambda _: 0.88,
            allow_perfect_foresight=True,
        )
        self.assertAlmostEqual(command, 0.88)

    def test_nonfinite_state_or_provider_output_does_not_advance_time(self):
        controller = PreviewPIDFeedforwardController(0.9, 0.5)
        bad_state = make_state()
        bad_state[0] = np.nan
        with self.assertRaises(ValueError):
            controller.compute_command(
                0.0,
                bad_state,
                make_params(),
                {"p_s0": 6.0},
                0.9,
                "persistence",
                forecast_provider=lambda **_: [0.9],
            )
        with self.assertRaises(ValueError):
            controller.compute_command(
                0.0,
                make_state(),
                make_params(),
                {"p_s0": 6.0},
                0.9,
                "persistence",
                forecast_provider=lambda **_: [np.nan],
            )

        command = controller.compute_command(
            0.0,
            make_state(),
            make_params(),
            {"p_s0": 6.0},
            0.9,
            "persistence",
            forecast_provider=lambda **_: [0.9],
        )
        self.assertTrue(np.isfinite(command))


if __name__ == "__main__":
    unittest.main()
