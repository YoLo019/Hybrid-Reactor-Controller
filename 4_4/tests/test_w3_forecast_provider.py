# -*- coding: utf-8 -*-

import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WIND_DATA_ROOT = PROJECT_ROOT / "4_4" / "wind_data"
if str(WIND_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(WIND_DATA_ROOT))

from w3_forecast_provider import (
    DEFAULT_FORECAST_CSV,
    DEFAULT_INTERFACE_CONFIG,
    W2IssueTimeForecastProvider,
)


HORIZONS = ((1, 10), (2, 20), (3, 30), (6, 60), (12, 120), (36, 360))
FIELDNAMES = (
    "issue_time",
    "target_time",
    "split",
    "horizon_steps",
    "forecast_type",
    "forecast_output_pu",
)


class W3ForecastProviderTests(unittest.TestCase):
    def _write_fixture(self, directory, mutate=None):
        interface_path = Path(directory) / "interface.json"
        csv_path = Path(directory) / "forecasts.csv"
        interface = {
            "status": "frozen",
            "forecast_roles": {
                "persistence": "baseline",
                "ridge_direct_ar": "negative comparison",
            },
            "forecast_interface": {
                "horizon_steps": [item[0] for item in HORIZONS],
                "horizon_minutes": [item[1] for item in HORIZONS],
                "required_columns": list(FIELDNAMES),
            },
            "data_isolation": {
                "opened_splits": ["train", "validation"],
                "locked_splits": ["boundary_construction", "final_extrapolation"],
                "locked_splits_accessed": [],
            },
        }
        interface_path.write_text(json.dumps(interface), encoding="utf-8")
        rows = []
        for issue_hour in (0, 1):
            issue = "2016-07-02T{:02d}:00:00".format(issue_hour)
            for forecast_type in ("persistence", "ridge_direct_ar"):
                for horizon_steps, horizon_minutes in HORIZONS:
                    target_minutes = issue_hour * 60 + horizon_minutes
                    target_hour, target_minute = divmod(target_minutes, 60)
                    target = "2016-07-02T{:02d}:{:02d}:00".format(
                        target_hour, target_minute
                    )
                    if forecast_type == "persistence":
                        value = 0.2 + 0.1 * issue_hour
                    else:
                        value = 0.2 + 0.1 * issue_hour + horizon_minutes / 1000.0
                    rows.append(
                        {
                            "issue_time": issue,
                            "target_time": target,
                            "split": "validation",
                            "horizon_steps": horizon_steps,
                            "forecast_type": forecast_type,
                            "forecast_output_pu": value,
                        }
                    )
        if mutate is not None:
            mutate(rows)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        return csv_path, interface_path

    def _provider(self, directory, mutate=None, start="2016-07-02T00:00:00"):
        csv_path, interface_path = self._write_fixture(directory, mutate)
        return W2IssueTimeForecastProvider(
            scenario_start_timestamp=start,
            forecast_csv_path=csv_path,
            interface_config_path=interface_path,
        )

    def test_latest_non_future_issue_and_linear_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = self._provider(directory)
            result = provider.forecast_with_metadata(
                issue_time_s=5.0 * 60.0,
                target_times_s=[10.0 * 60.0, 15.0 * 60.0, 25.0 * 60.0],
                forecast_type="ridge_direct_ar",
            )
        self.assertEqual(result["selected_issue_time"], "2016-07-02T00:00:00")
        # 10 min 命中节点；15/25 min 分别位于相邻冻结节点中间。
        self.assertAlmostEqual(result["forecast_output_pu"][0], 0.21)
        self.assertAlmostEqual(result["forecast_output_pu"][1], 0.215)
        self.assertAlmostEqual(result["forecast_output_pu"][2], 0.225)
        json.dumps(result, allow_nan=False)

    def test_lead_zero_uses_persistence_issue_value_and_far_tail_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = self._provider(directory)
            lead_zero_values = provider(
                issue_time_s=0.0,
                target_times_s=[0.0, 5.0 * 60.0],
                forecast_type="ridge_direct_ar",
                issue_value_pu=999.0,
            )
            tail_values = provider(
                issue_time_s=9.0 * 60.0,
                target_times_s=[360.0 * 60.0, 369.0 * 60.0],
                forecast_type="ridge_direct_ar",
            )
        self.assertAlmostEqual(lead_zero_values[0], 0.2)
        self.assertAlmostEqual(lead_zero_values[1], 0.205)
        self.assertAlmostEqual(tail_values[0], 0.56)
        self.assertAlmostEqual(tail_values[1], 0.56)

    def test_future_only_issue_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = self._provider(
                directory, start="2016-07-01T23:50:00"
            )
            with self.assertRaisesRegex(ValueError, "future issue"):
                provider(
                    issue_time_s=0.0,
                    target_times_s=[1.0],
                    forecast_type="persistence",
                )

    def test_stale_issue_and_more_than_six_hour_preview_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = self._provider(directory)
            with self.assertRaisesRegex(ValueError, "stale"):
                provider(
                    issue_time_s=2.0 * 3600.0,
                    target_times_s=[2.0 * 3600.0 + 1.0],
                    forecast_type="persistence",
                )
            with self.assertRaisesRegex(ValueError, "6 h span"):
                provider(
                    issue_time_s=0.0,
                    target_times_s=[6.0 * 3600.0 + 1.0],
                    forecast_type="persistence",
                )

    def test_duplicate_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            def duplicate(rows):
                rows.append(dict(rows[0]))

            with self.assertRaisesRegex(ValueError, "Duplicate"):
                self._provider(directory, duplicate)

    def test_missing_six_horizons_fails_closed_when_issue_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            def remove_one(rows):
                rows[:] = [
                    row
                    for row in rows
                    if not (
                        row["issue_time"] == "2016-07-02T01:00:00"
                        and row["forecast_type"] == "ridge_direct_ar"
                        and row["horizon_steps"] == 36
                    )
                ]

            provider = self._provider(directory, remove_one)
            with self.assertRaisesRegex(ValueError, "six-horizon"):
                provider(
                    issue_time_s=3600.0,
                    target_times_s=[3601.0],
                    forecast_type="ridge_direct_ar",
                )

    def test_nonfinite_value_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            def make_nonfinite(rows):
                rows[0]["forecast_output_pu"] = math.nan

            with self.assertRaisesRegex(ValueError, "Non-finite"):
                self._provider(directory, make_nonfinite)

    def test_cross_split_fails_before_locked_value_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            def cross_split(rows):
                rows[0]["split"] = "boundary_construction"
                rows[0]["forecast_output_pu"] = "must_not_be_parsed"

            with self.assertRaisesRegex(ValueError, "Cross-split"):
                self._provider(directory, cross_split)

    def test_formal_w2_artifact_maps_scenario_seconds_without_future_issue(self):
        provider = W2IssueTimeForecastProvider(
            scenario_start_timestamp="2016-07-02T00:05:00",
            forecast_csv_path=DEFAULT_FORECAST_CSV,
            interface_config_path=DEFAULT_INTERFACE_CONFIG,
        )
        result = provider.forecast_with_metadata(
            issue_time_s=0.0,
            target_times_s=[0.0, 300.0, 900.0, 21600.0],
            forecast_type="persistence",
        )
        self.assertEqual(result["selected_issue_time"], "2016-07-02T00:00:00")
        self.assertEqual(result["locked_splits_accessed"], [])
        self.assertTrue(all(math.isfinite(value) for value in result["forecast_output_pu"]))


if __name__ == "__main__":
    unittest.main()
