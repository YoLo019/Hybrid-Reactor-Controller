# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FLEXIBILITY_ROOT = PROJECT_ROOT / "4_4" / "flexibility"
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_w3_v1 import (
    NuclearReferenceForecastAdapter,
    load_reference,
    preflight_output_directory,
    validate_config_contract,
)


CONFIG_PATH = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w3_v1_typical_validation.json"
)


class FakeWindProvider:
    def forecast_with_metadata(self, **request):
        return {
            "selected_issue_time": "2016-07-20T00:10:00",
            "forecast_output_pu": [0.2 for _ in request["target_times_s"]],
        }


class W3V1RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_reference_uses_frozen_validation_window_and_system_mapping(self):
        reference = load_reference(self.config)
        self.assertEqual(reference["start"], "2016-07-20T00:10:00")
        self.assertEqual(reference["duration_seconds"], 21000.0)
        self.assertAlmostEqual(reference["target_at"](0.0), 0.9)
        expected_end = 0.9 - (17.56 / 100.0) * (
            reference["wind_at"](21000.0) - reference["initial_wind_pu"]
        )
        self.assertAlmostEqual(reference["target_at"](21000.0), expected_end)

    def test_forecast_adapter_maps_wind_and_records_issue_identity(self):
        adapter = NuclearReferenceForecastAdapter(
            FakeWindProvider(), base_power=0.9, wind_scale=0.1756, initial_wind=0.1
        )
        values = adapter(
            issue_time_s=0.0,
            target_times_s=np.asarray([0.5, 15.0]),
            forecast_type="persistence",
            issue_value_pu=0.9,
        )
        np.testing.assert_allclose(values, [0.88244, 0.88244])
        self.assertEqual(
            adapter.selected_issue_times, {"2016-07-20T00:10:00"}
        )

    def test_config_contract_and_output_preflight_fail_closed(self):
        validate_config_contract(self.config)
        changed = json.loads(json.dumps(self.config))
        changed["forecast"]["issue_interval_seconds"] = 601
        with self.assertRaises(ValueError):
            validate_config_contract(changed)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            preflight_output_directory(output_dir)
            (output_dir / "partial.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                preflight_output_directory(output_dir)


if __name__ == "__main__":
    unittest.main()
