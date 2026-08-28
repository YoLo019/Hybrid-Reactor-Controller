# -*- coding: utf-8 -*-

import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FLEXIBILITY_ROOT = PROJECT_ROOT / "4_4" / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from aggregate_w3_v1 import LOWER_IS_BETTER, aggregate


CONFIG_PATH = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w3_v1_typical_validation.json"
)


class W3V1AggregationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def _record(self, controller):
        metrics = {key: 0.1 for key in LOWER_IS_BETTER}
        metrics.update({"soc_min": 0.49, "soc_max": 0.51})
        record = {
            "study_id": "w3_v1_typical_validation",
            "controller": controller,
            "scenario_id": "w1_typical_6h",
            "split": "validation",
            "forecast_type": "persistence",
            "forecast_issue_times": ["2016-07-20T00:10:00"],
            "stage": "W3-V1",
            "control_steps": 42000,
            "metrics": metrics,
        }
        if controller.startswith("mpc_"):
            record.update(
                {
                    "decision_nodes": 30,
                    "prediction_span_seconds": 21600.0,
                    "solver": "OSQP",
                    "solver_status_counts": {"optimal": 42000},
                    "solver_fallback_count": 0,
                }
            )
        return record

    def test_complete_fair_records_pass(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        report = aggregate(self.config, records)
        self.assertTrue(report["pass"])
        self.assertEqual(
            report["comparisons"]["tracking_rmse_pu"]["nonuniform_vs_uniform_relative"],
            0.0,
        )

    def test_different_issue_information_fails(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        records[-1]["forecast_issue_times"] = ["2016-07-20T00:20:00"]
        self.assertFalse(aggregate(self.config, records)["pass"])

    def test_mpc_budget_or_fallback_mismatch_fails(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        changed = copy.deepcopy(records)
        changed[-1]["decision_nodes"] = 31
        self.assertFalse(aggregate(self.config, changed)["pass"])
        changed = copy.deepcopy(records)
        changed[-1]["solver_fallback_count"] = 1
        self.assertFalse(aggregate(self.config, changed)["pass"])

    def test_wrong_absolute_budget_or_solver_status_fails(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        for record in records:
            if record["controller"].startswith("mpc_"):
                record["decision_nodes"] = 31
        self.assertFalse(aggregate(self.config, records)["pass"])

        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        records[-1]["solver_status_counts"] = {"infeasible": 4}
        self.assertFalse(aggregate(self.config, records)["pass"])

    def test_wrong_scenario_or_empty_issue_set_fails(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        for record in records:
            record["scenario_id"] = "another_scenario"
        self.assertFalse(aggregate(self.config, records)["pass"])

        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        for record in records:
            record["forecast_issue_times"] = []
        self.assertFalse(aggregate(self.config, records)["pass"])

    def test_smoke_cannot_be_certified_as_formal(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        for record in records:
            record["stage"] = "W3-R0"
            record["control_steps"] = 4
            if record["controller"].startswith("mpc_"):
                record["solver_status_counts"] = {"optimal": 4}
        report = aggregate(self.config, records)
        self.assertTrue(report["pass"])
        self.assertEqual(report["stage"], "W3-R0")

        for record in records:
            record["stage"] = "W3-V1"
        self.assertFalse(aggregate(self.config, records)["pass"])

    def test_mixed_stage_or_wrong_study_fails(self):
        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        records[-1]["stage"] = "W3-R0"
        self.assertFalse(aggregate(self.config, records)["pass"])

        records = [self._record(name) for name in self.config["simulation"]["formal_controllers"]]
        records[-1]["study_id"] = "another_study"
        self.assertFalse(aggregate(self.config, records)["pass"])


if __name__ == "__main__":
    unittest.main()
