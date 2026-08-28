# -*- coding: utf-8 -*-

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "research_execution" / "04_experiments" / "configs"
SUMMARY_PATH = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "runs"
    / "w2_validation_v1"
    / "summary.json"
)


class W2F1FreezeTests(unittest.TestCase):
    def test_freeze_matches_validation_decision_and_source_interface(self):
        with (CONFIG_ROOT / "w2_prediction_v1.json").open(encoding="utf-8") as handle:
            source = json.load(handle)
        with (CONFIG_ROOT / "w2_f1_forecast_interface_v1.json").open(
            encoding="utf-8"
        ) as handle:
            frozen = json.load(handle)
        with SUMMARY_PATH.open(encoding="utf-8") as handle:
            summary = json.load(handle)

        self.assertFalse(summary["validation_gate"]["pass"])
        self.assertEqual(summary["validation_gate"]["decision"], "persistence")
        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(
            frozen["validation_decision"]["selected_forecast"], "persistence"
        )
        self.assertEqual(
            frozen["forecast_interface"]["horizon_steps"],
            source["task"]["horizon_steps"],
        )
        self.assertEqual(
            frozen["forecast_interface"]["horizon_minutes"],
            source["task"]["horizon_minutes"],
        )
        self.assertEqual(
            frozen["forecast_interface"]["required_columns"],
            source["forecast_interface"]["columns"],
        )
        self.assertEqual(frozen["data_isolation"]["locked_splits_accessed"], [])
        self.assertFalse(frozen["w3_inputs"]["node_locations_frozen"])


if __name__ == "__main__":
    unittest.main()
