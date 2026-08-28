# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "4_4"
FLEXIBILITY_ROOT = MODEL_ROOT / "flexibility"
for path in (MODEL_ROOT, FLEXIBILITY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mpc_utils_out import (
    build_output_prediction_maps,
    build_variable_output_prediction_maps,
    normalize_prediction_interval_steps,
    prediction_integral_map,
    prediction_move_interval_steps,
    prediction_quadrature_scale,
)
from metrics_source import resolve_preview_forecast
from prepare_w3_horizon import DEFAULT_CONFIG, prepare


class W3NonuniformHorizonTests(unittest.TestCase):
    def test_unit_intervals_reproduce_uniform_prediction_maps(self):
        ad = np.array([[0.9, 0.1], [0.0, 0.8]])
        bd = np.array([[0.1], [0.2]])
        expected = build_output_prediction_maps(ad, bd, (0, 1), 5)
        actual = build_variable_output_prediction_maps(ad, bd, (0, 1), [1] * 5)
        np.testing.assert_allclose(actual[0], expected[0], rtol=0.0, atol=1e-14)
        np.testing.assert_allclose(actual[1], expected[1], rtol=0.0, atol=1e-14)

    def test_blocked_maps_match_explicit_step_simulation(self):
        ad = np.array([[0.95]])
        bd = np.array([[0.2]])
        intervals = [1, 3, 2]
        initial_maps, input_maps = build_variable_output_prediction_maps(
            ad, bd, (0,), intervals
        )
        initial = 0.4
        controls = np.array([0.1, -0.2, 0.3])
        state = initial
        outputs = []
        for control, interval in zip(controls, intervals):
            for _ in range(interval):
                state = float(ad[0, 0] * state + bd[0, 0] * control)
            outputs.append(state)
        predicted = initial_maps[0, :, 0] * initial + input_maps[0] @ controls
        np.testing.assert_allclose(predicted, outputs, rtol=0.0, atol=1e-14)

    def test_preparation_freezes_equal_budget_and_all_w2_anchors(self):
        report = prepare(DEFAULT_CONFIG)
        self.assertTrue(report["pass"])
        self.assertEqual(report["nonuniform"]["decision_nodes"], 30)
        self.assertEqual(report["uniform_tail"]["decision_nodes"], 30)
        self.assertEqual(report["nonuniform"]["prediction_span_seconds"], 21600.0)
        self.assertEqual(report["uniform_tail"]["prediction_span_seconds"], 21600.0)
        nodes = report["nonuniform"]["node_end_seconds"]
        for horizon_minutes in (10, 20, 30, 60, 120, 360):
            self.assertIn(60.0 * horizon_minutes, nodes)

    def test_actual_forecast_uses_issue_time_provider_not_future_truth(self):
        calls = []

        def provider(**request):
            calls.append(request)
            return np.full(len(request["target_times_s"]), request["issue_value_pu"])

        def forbidden_future_truth(_):
            raise AssertionError("actual forecast path accessed future truth")

        values = resolve_preview_forecast(
            issue_time_s=12.0,
            node_times_s=[12.5, 20.0, 60.0],
            current_target_power_abs=0.9,
            forecast_type="persistence",
            target_function=forbidden_future_truth,
            forecast_provider=provider,
        )
        np.testing.assert_array_equal(values, [0.9, 0.9, 0.9])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["issue_time_s"], 12.0)

    def test_actual_forecast_fails_closed_without_provider(self):
        with self.assertRaises(ValueError):
            resolve_preview_forecast(
                issue_time_s=0.0,
                node_times_s=[0.5],
                current_target_power_abs=0.9,
                forecast_type="persistence",
            )

    def test_fractional_prediction_intervals_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_prediction_interval_steps([1, 1.9, 3])

    def test_variable_interval_cost_integral_and_move_scales(self):
        intervals = [1, 3, 2]
        np.testing.assert_allclose(
            prediction_quadrature_scale(intervals), np.sqrt([1.0, 3.0, 2.0])
        )
        np.testing.assert_array_equal(
            prediction_move_interval_steps(intervals), [1, 1, 3]
        )
        np.testing.assert_allclose(
            prediction_integral_map(intervals, 0.5),
            [
                [0.5, 0.0, 0.0],
                [0.5, 1.5, 0.0],
                [0.5, 1.5, 1.0],
            ],
        )


if __name__ == "__main__":
    unittest.main()
