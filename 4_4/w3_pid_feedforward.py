"""W3同预测信息PID前馈基线。"""

from copy import copy

import numpy as np

import model_wind
from metrics_source import resolve_preview_forecast
from model_schema import validate_state_vector


PREVIEW_LEAD_S = 15.0
VALVE_COMMAND_MIN_PU = 0.0
VALVE_COMMAND_MAX_PU = 1.2


class PreviewPIDFeedforwardController:
    """用同一issue-time预测修正PID负荷参考，并执行统一阀门约束。"""

    def __init__(self, initial_valve_command_pu, control_interval_s):
        initial_command = float(initial_valve_command_pu)
        interval = float(control_interval_s)
        if not np.isfinite(initial_command):
            raise ValueError("Initial valve command must be finite")
        if not VALVE_COMMAND_MIN_PU <= initial_command <= VALVE_COMMAND_MAX_PU:
            raise ValueError("Initial valve command violates the PID amplitude limit")
        if not np.isfinite(interval) or interval <= 0.0:
            raise ValueError("Control interval must be finite and positive")

        self._previous_valve_command_pu = initial_command
        self._control_interval_s = interval
        self._last_issue_time_s = None
        self._last_preview_reference_pu = None

    @property
    def previous_valve_command_pu(self):
        return self._previous_valve_command_pu

    @property
    def last_preview_reference_pu(self):
        return self._last_preview_reference_pu

    def compute_command(
        self,
        issue_time_s,
        state,
        params,
        initial_conditions,
        current_target_power_abs,
        forecast_type,
        forecast_provider=None,
        target_function=None,
        target_scale=1.0,
        allow_perfect_foresight=False,
    ):
        """计算一次前馈PID命令；任何非有限或时序异常均拒绝更新控制器状态。"""
        issue_time = float(issue_time_s)
        current_target = float(current_target_power_abs)
        scale = float(target_scale)
        state_array = np.asarray(state, dtype=float).reshape(-1)
        validate_state_vector(state_array)

        if not np.isfinite(issue_time):
            raise ValueError("Issue time must be finite")
        if self._last_issue_time_s is not None and issue_time <= self._last_issue_time_s:
            raise ValueError("Issue times must be strictly increasing")
        if not np.all(np.isfinite(state_array)):
            raise ValueError("PID state must be finite")
        if not np.isfinite(current_target):
            raise ValueError("Current target power must be finite")
        if not np.isfinite(scale):
            raise ValueError("Target scale must be finite")
        if forecast_type == "perfect_foresight" and not allow_perfect_foresight:
            raise ValueError("perfect_foresight requires explicit upper-bound opt-in")

        target_time = issue_time + PREVIEW_LEAD_S
        if not np.isfinite(target_time):
            raise ValueError("Preview target time must be finite")
        preview_reference = float(resolve_preview_forecast(
            issue_time_s=issue_time,
            node_times_s=np.asarray([target_time], dtype=float),
            current_target_power_abs=current_target,
            forecast_type=forecast_type,
            target_function=target_function,
            forecast_provider=forecast_provider,
            target_scale=scale,
        )[0])
        if not np.isfinite(preview_reference):
            raise ValueError("Preview reference must be finite")

        command_params = copy(params)
        command_params["P_load_ref"] = preview_reference
        raw_command = float(model_wind.compute_turbine_command_pu(
            issue_time,
            state_array,
            command_params,
            initial_conditions,
        ))
        if not np.isfinite(raw_command):
            raise ValueError("PID command must be finite")

        rate_limit = float(params.get("valve_rate_limit_pu_s", 0.05))
        if not np.isfinite(rate_limit) or rate_limit < 0.0:
            raise ValueError("Valve rate limit must be finite and non-negative")
        elapsed_s = (
            self._control_interval_s
            if self._last_issue_time_s is None
            else issue_time - self._last_issue_time_s
        )
        max_move = rate_limit * elapsed_s
        lower = max(
            VALVE_COMMAND_MIN_PU,
            self._previous_valve_command_pu - max_move,
        )
        upper = min(
            VALVE_COMMAND_MAX_PU,
            self._previous_valve_command_pu + max_move,
        )
        command = float(np.clip(raw_command, lower, upper))
        if not np.isfinite(command):
            raise ValueError("Limited PID command must be finite")

        self._previous_valve_command_pu = command
        self._last_issue_time_s = issue_time
        self._last_preview_reference_pu = preview_reference
        return command
