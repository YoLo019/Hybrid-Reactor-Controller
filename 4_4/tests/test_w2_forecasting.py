# -*- coding: utf-8 -*-

import copy
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


MODEL_ROOT = Path(__file__).resolve().parents[1]
WIND_DATA_ROOT = MODEL_ROOT / "wind_data"
if str(WIND_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(WIND_DATA_ROOT))

from w2_forecasting import (
    build_sample_index,
    evaluate_validation_gate,
    fit_ridge,
    forecast_metrics,
    load_config,
    predict_ridge,
)


CONFIG_PATH = (
    MODEL_ROOT.parent
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w2_prediction_v1.json"
)


class W2ForecastingTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(load_config(CONFIG_PATH))
        self.config["task"]["context_steps"] = 4
        self.config["task"]["horizon_steps"] = [1, 2]
        self.config["task"]["horizon_minutes"] = [10, 20]
        self.config["systems"]["ridge_direct_ar"]["feature_lags_steps"] = [0, 1, 2, 3]
        timestamps = pd.date_range("2016-01-01", periods=40, freq="10min")
        self.frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "output_pu": np.linspace(0.1, 0.9, len(timestamps)),
                "split": ["train"] * 24 + ["validation"] * 16,
            }
        )

    def test_samples_never_cross_split_boundary(self):
        index = build_sample_index(self.frame, self.config)
        for row in index.itertuples():
            issue_split = self.frame.loc[row.issue_index, "split"]
            target_split = self.frame.loc[row.target_index, "split"]
            context_split = self.frame.loc[row.issue_index - 3, "split"]
            self.assertEqual(issue_split, target_split)
            self.assertEqual(issue_split, context_split)

    def test_locked_splits_are_rejected_during_preparation(self):
        with self.assertRaises(ValueError):
            build_sample_index(
                self.frame, self.config, allowed_splits=["train", "final_extrapolation"]
            )

    def test_ridge_prediction_is_deterministic(self):
        features = np.column_stack(
            [np.linspace(0.0, 1.0, 100), np.linspace(1.0, 0.0, 100)]
        )
        targets = 0.2 + 0.7 * features[:, 0] - 0.1 * features[:, 1]
        first = predict_ridge(fit_ridge(features, targets, 1e-6), features)
        second = predict_ridge(fit_ridge(features, targets, 1e-6), features)
        self.assertLess(float(np.max(np.abs(first - targets))), 1e-7)
        self.assertTrue(np.array_equal(first, second))

    def test_persistence_metric_contract(self):
        issue = np.array([0.1, 0.2, 0.3, 0.4])
        actual = np.array([0.1, 0.4, 0.2, 0.8])
        metrics = forecast_metrics(actual, issue, issue, ramp_threshold=0.15)
        self.assertAlmostEqual(metrics["mae_pu"], 0.175)
        self.assertEqual(metrics["ramp_event_count"], 2)
        self.assertEqual(metrics["ramp_event_recall"], 0.0)

    def test_validation_gate_uses_frozen_three_part_rule(self):
        self.config["success_gate"] = {
            "aggregation": "equal-weight horizon mean",
            "mean_rmse_improvement_min": 0.05,
            "improved_horizons_min": 2,
            "horizon_worsening_max": 0.05,
        }
        rows = []
        for horizon, persistence, ridge in ((1, 1.0, 0.9), (2, 2.0, 1.8)):
            rows.extend(
                [
                    {
                        "horizon_steps": horizon,
                        "forecast_type": "persistence",
                        "rmse_pu": persistence,
                    },
                    {
                        "horizon_steps": horizon,
                        "forecast_type": "ridge_direct_ar",
                        "rmse_pu": ridge,
                    },
                ]
            )
        decision = evaluate_validation_gate(pd.DataFrame(rows), self.config)
        self.assertTrue(decision["pass"])
        self.assertEqual(decision["decision"], "ridge_direct_ar")
        self.assertAlmostEqual(decision["observed"]["mean_rmse_improvement"], 0.1)

        rows[-1]["rmse_pu"] = 2.2
        failed = evaluate_validation_gate(pd.DataFrame(rows), self.config)
        self.assertFalse(failed["pass"])
        self.assertEqual(failed["decision"], "persistence")


if __name__ == "__main__":
    unittest.main()
