# -*- coding: utf-8 -*-
"""把冻结的 W2 issue-time 预测表适配为 W3 preview provider。"""

import bisect
import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORECAST_CSV = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "runs"
    / "w2_validation_v1"
    / "forecasts.csv"
)
DEFAULT_INTERFACE_CONFIG = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w2_f1_forecast_interface_v1.json"
)


def _parse_timestamp(value, field_name):
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        timestamp = datetime.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid {}: {!r}".format(field_name, value)) from error
    return timestamp


def _json_timestamp(value):
    return value.isoformat()


class W2IssueTimeForecastProvider:
    """只读取一个冻结 split，并按最新可用 issuance 返回插值预测。"""

    SUPPORTED_FORECAST_TYPES = ("persistence", "ridge_direct_ar")
    ISSUANCE_INTERVAL_SECONDS = 600.0

    def __init__(
        self,
        scenario_start_timestamp,
        forecast_csv_path=DEFAULT_FORECAST_CSV,
        interface_config_path=DEFAULT_INTERFACE_CONFIG,
        split="validation",
    ):
        self.scenario_start_timestamp = _parse_timestamp(
            scenario_start_timestamp, "scenario_start_timestamp"
        )
        self.forecast_csv_path = Path(forecast_csv_path).resolve()
        self.interface_config_path = Path(interface_config_path).resolve()
        self.split = str(split)
        self._interface = self._load_interface()
        self._horizon_minutes = self._load_horizon_contract()
        self._rows_by_type = {
            forecast_type: {} for forecast_type in self.SUPPORTED_FORECAST_TYPES
        }
        self._load_forecasts()
        all_issues = set()
        for rows_by_issue in self._rows_by_type.values():
            all_issues.update(rows_by_issue)
        self._issue_times = sorted(all_issues)
        if not self._issue_times:
            raise ValueError("W2 forecast table contains no rows for the requested split")

    def _load_interface(self):
        with self.interface_config_path.open(encoding="utf-8") as handle:
            interface = json.load(handle)
        if interface.get("status") != "frozen":
            raise ValueError("W2 forecast interface must be frozen")
        isolation = interface.get("data_isolation", {})
        if self.split not in isolation.get("opened_splits", []):
            raise ValueError("Requested split is not opened by the W2 interface")
        if self.split in isolation.get("locked_splits", []):
            raise ValueError("Locked W2 split cannot be used by the W3 provider")
        if isolation.get("locked_splits_accessed"):
            raise ValueError("W2 freeze record reports access to locked splits")
        roles = interface.get("forecast_roles", {})
        for forecast_type in self.SUPPORTED_FORECAST_TYPES:
            if forecast_type not in roles:
                raise ValueError("W2 interface is missing forecast role: {}".format(forecast_type))
        return interface

    def _load_horizon_contract(self):
        contract = self._interface.get("forecast_interface", {})
        steps = contract.get("horizon_steps", [])
        minutes = contract.get("horizon_minutes", [])
        if len(steps) != len(minutes) or len(steps) != 6:
            raise ValueError("W2 interface must freeze exactly six horizon pairs")
        horizon_minutes = {}
        for step, minute in zip(steps, minutes):
            step = int(step)
            minute = float(minute)
            if step in horizon_minutes or step <= 0 or not math.isfinite(minute) or minute <= 0:
                raise ValueError("Invalid W2 horizon contract")
            horizon_minutes[step] = minute
        if sorted(horizon_minutes.values()) != [10.0, 20.0, 30.0, 60.0, 120.0, 360.0]:
            raise ValueError("W2 horizon minutes do not match the frozen W3 interface")
        return horizon_minutes

    def _load_forecasts(self):
        required_columns = set(
            self._interface["forecast_interface"].get("required_columns", [])
        )
        with self.forecast_csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            missing_columns = required_columns.difference(columns)
            if missing_columns:
                raise ValueError(
                    "W2 forecast table is missing columns: {}".format(
                        ", ".join(sorted(missing_columns))
                    )
                )
            seen_keys = set()
            for row_number, row in enumerate(reader, start=2):
                # 先检查 split，再读取时间和预测值；锁定 split 的数据值不会被打开。
                row_split = row.get("split", "")
                if row_split != self.split:
                    raise ValueError(
                        "Cross-split W2 row at CSV line {}: {!r}".format(
                            row_number, row_split
                        )
                    )
                forecast_type = row.get("forecast_type", "")
                if forecast_type not in self.SUPPORTED_FORECAST_TYPES:
                    raise ValueError(
                        "Unsupported table forecast type at CSV line {}: {!r}".format(
                            row_number, forecast_type
                        )
                    )
                issue_time = _parse_timestamp(row.get("issue_time"), "issue_time")
                target_time = _parse_timestamp(row.get("target_time"), "target_time")
                if issue_time.tzinfo != self.scenario_start_timestamp.tzinfo:
                    raise ValueError("Scenario and W2 timestamps must use the same timezone form")
                try:
                    horizon_steps = int(row.get("horizon_steps", ""))
                    forecast_value = float(row.get("forecast_output_pu", ""))
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "Invalid numeric W2 forecast field at CSV line {}".format(row_number)
                    ) from error
                if horizon_steps not in self._horizon_minutes:
                    raise ValueError(
                        "Unexpected W2 horizon at CSV line {}: {}".format(
                            row_number, horizon_steps
                        )
                    )
                if not math.isfinite(forecast_value):
                    raise ValueError(
                        "Non-finite W2 forecast at CSV line {}".format(row_number)
                    )
                expected_target = issue_time + timedelta(
                    minutes=self._horizon_minutes[horizon_steps]
                )
                if target_time != expected_target:
                    raise ValueError(
                        "W2 target/lead mismatch at CSV line {}".format(row_number)
                    )
                key = (row_split, issue_time, horizon_steps, forecast_type)
                if key in seen_keys:
                    raise ValueError(
                        "Duplicate W2 forecast key at CSV line {}".format(row_number)
                    )
                seen_keys.add(key)
                self._rows_by_type[forecast_type].setdefault(issue_time, {})[
                    horizon_steps
                ] = forecast_value

    def _latest_issue_not_after(self, current_timestamp):
        index = bisect.bisect_right(self._issue_times, current_timestamp) - 1
        if index < 0:
            raise ValueError("No W2 issue is available without reading a future issue")
        selected_issue = self._issue_times[index]
        if selected_issue > current_timestamp:
            raise ValueError("Future W2 issue selection is forbidden")
        return selected_issue

    def _selected_curve(self, selected_issue, forecast_type):
        required_steps = set(self._horizon_minutes)
        forecast_rows = self._rows_by_type[forecast_type].get(selected_issue, {})
        persistence_rows = self._rows_by_type["persistence"].get(selected_issue, {})
        missing_forecast = required_steps.difference(forecast_rows)
        missing_persistence = required_steps.difference(persistence_rows)
        if missing_forecast or missing_persistence:
            raise ValueError(
                "Selected W2 issue is missing one or more frozen six-horizon rows"
            )
        issue_values = [persistence_rows[step] for step in sorted(required_steps)]
        issue_value = issue_values[0]
        if any(value != issue_value for value in issue_values[1:]):
            raise ValueError("Persistence rows disagree on the W2 issue-time value")
        lead_seconds = [0.0]
        values = [issue_value]
        for step, minute in sorted(
            self._horizon_minutes.items(), key=lambda item: item[1]
        ):
            lead_seconds.append(60.0 * minute)
            values.append(forecast_rows[step])
        return lead_seconds, values

    @staticmethod
    def _interpolate_hold_last(lead_seconds, curve_leads, curve_values):
        if lead_seconds < 0.0:
            raise ValueError("Forecast target precedes the selected W2 issue")
        if lead_seconds >= curve_leads[-1]:
            return curve_values[-1]
        right = bisect.bisect_right(curve_leads, lead_seconds)
        left = right - 1
        if curve_leads[left] == lead_seconds:
            return curve_values[left]
        weight = (lead_seconds - curve_leads[left]) / (
            curve_leads[right] - curve_leads[left]
        )
        return curve_values[left] + weight * (
            curve_values[right] - curve_values[left]
        )

    def forecast_with_metadata(
        self,
        issue_time_s,
        target_times_s,
        forecast_type,
        issue_value_pu=None,
    ):
        """返回 JSON 可序列化的预测值和实际采用的 issuance 证据。"""
        del issue_value_pu  # lead=0 必须来自冻结 W2 表，不能由控制器覆盖。
        if forecast_type not in self.SUPPORTED_FORECAST_TYPES:
            raise ValueError("Unsupported W2 forecast type: {}".format(forecast_type))
        try:
            issue_time_s = float(issue_time_s)
            target_times = [float(value) for value in target_times_s]
        except (TypeError, ValueError) as error:
            raise ValueError("Simulation forecast times must be numeric") from error
        if not math.isfinite(issue_time_s) or not target_times or not all(
            math.isfinite(value) for value in target_times
        ):
            raise ValueError("Simulation forecast times must be finite and non-empty")
        if any(value < issue_time_s for value in target_times):
            raise ValueError("Forecast target times cannot precede the control issue time")
        current_timestamp = self.scenario_start_timestamp + timedelta(seconds=issue_time_s)
        selected_issue = self._latest_issue_not_after(current_timestamp)
        issue_age_seconds = (current_timestamp - selected_issue).total_seconds()
        if issue_age_seconds >= self.ISSUANCE_INTERVAL_SECONDS:
            raise ValueError("Latest non-future W2 issue is stale by at least one issuance interval")
        curve_leads, curve_values = self._selected_curve(selected_issue, forecast_type)
        target_timestamps = [
            self.scenario_start_timestamp + timedelta(seconds=value)
            for value in target_times
        ]
        target_leads = [
            (timestamp - selected_issue).total_seconds()
            for timestamp in target_timestamps
        ]
        if any(
            target_time_s - issue_time_s > curve_leads[-1]
            for target_time_s in target_times
        ):
            raise ValueError("W3 forecast targets cannot exceed the frozen 6 h span")
        values = [
            float(self._interpolate_hold_last(lead, curve_leads, curve_values))
            for lead in target_leads
        ]
        return {
            "forecast_type": forecast_type,
            "split": self.split,
            "control_timestamp": _json_timestamp(current_timestamp),
            "selected_issue_time": _json_timestamp(selected_issue),
            "target_times": [_json_timestamp(value) for value in target_timestamps],
            "target_lead_seconds_from_selected_issue": [float(value) for value in target_leads],
            "forecast_output_pu": values,
            "locked_splits_accessed": [],
        }

    def __call__(
        self,
        issue_time_s,
        target_times_s,
        forecast_type,
        issue_value_pu=None,
    ):
        """符合 ``resolve_preview_forecast`` 的 callable 契约。"""
        result = self.forecast_with_metadata(
            issue_time_s=issue_time_s,
            target_times_s=target_times_s,
            forecast_type=forecast_type,
            issue_value_pu=issue_value_pu,
        )
        return result["forecast_output_pu"]
