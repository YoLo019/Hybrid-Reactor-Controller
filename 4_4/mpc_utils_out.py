# -*- coding: utf-8 -*-
"""阀门MPC及其工作点线性化工具。"""

import cvxpy as cp
import numpy as np
from scipy.linalg import expm

import model_wind
from model_schema import STATE_INDEX, validate_state_vector


def normalize_target_trajectory(target_power_abs, horizon):
    """将标量目标广播到预测时域，或验证显式未来参考轨迹。"""
    values = np.asarray(target_power_abs, dtype=float)
    if values.ndim == 0:
        return np.full(int(horizon), float(values), dtype=float)
    values = values.reshape(-1)
    if values.size != int(horizon):
        raise ValueError(
            f"Target trajectory requires {int(horizon)} values, got {values.size}"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("Target trajectory contains non-finite values")
    return values


def get_linear_model(params, initial_conditions, y0, valve_command_eq, dt=1.0,
                     relative_step=1e-5):
    """有限差分连续模型，并对影响闭环的40个物理状态做精确离散。"""
    y0 = np.asarray(y0, dtype=float)
    validate_state_vector(y0)
    n_states = len(y0)

    def rhs(state, valve_command):
        return np.asarray(
            model_wind.pwf_model(
                0.0,
                np.asarray(state, dtype=float),
                params,
                initial_conditions,
                disturbance_case=0,
                control_mode="pid",
                u_tg_ext=float(valve_command),
            ),
            dtype=float,
        )

    a_c = np.zeros((n_states, n_states))
    for index in range(n_states):
        step = relative_step * max(abs(y0[index]), 1.0)
        state_plus = y0.copy()
        state_minus = y0.copy()
        state_plus[index] += step
        state_minus[index] -= step
        a_c[:, index] = (
            rhs(state_plus, valve_command_eq)
            - rhs(state_minus, valve_command_eq)
        ) / (2.0 * step)

    input_step = relative_step * max(abs(float(valve_command_eq)), 1.0)
    b_c = (
        rhs(y0, float(valve_command_eq) + input_step)
        - rhs(y0, float(valve_command_eq) - input_step)
    )[:, None] / (2.0 * input_step)

    detector_names = ("i_lo", "v_ilo", "i_lr", "v_ilr")
    detector_indices = [STATE_INDEX[name] for name in detector_names]
    plant_indices = [index for index in range(n_states) if index not in detector_indices]
    if np.max(np.abs(a_c[np.ix_(plant_indices, detector_indices)])) > 1e-12:
        raise RuntimeError("Detector states unexpectedly feed the physical plant")

    a_plant = a_c[np.ix_(plant_indices, plant_indices)]
    b_plant = b_c[plant_indices, :]
    augmented = np.zeros((len(plant_indices) + 1, len(plant_indices) + 1))
    augmented[:-1, :-1] = a_plant
    augmented[:-1, -1:] = b_plant
    exact = expm(float(dt) * augmented)

    # 仪表快状态只作观测且不反馈闭环，在预测模型中保持当前值。
    ad = np.eye(n_states)
    bd = np.zeros((n_states, 1))
    ad[np.ix_(plant_indices, plant_indices)] = exact[:-1, :-1]
    ad[np.ix_(plant_indices, detector_indices)] = 0.0
    bd[plant_indices, :] = exact[:-1, -1:]
    return ad, bd


def build_output_prediction_maps(ad, bd, output_indices, horizon):
    """构造与逐步递推严格等价的凝聚输出预测矩阵。"""
    return build_variable_output_prediction_maps(
        ad, bd, output_indices, np.ones(int(horizon), dtype=int)
    )


def normalize_prediction_interval_steps(prediction_interval_steps, horizon=None):
    """验证以基础控制步长计数的预测区间。"""
    raw_intervals = np.asarray(prediction_interval_steps, dtype=float).reshape(-1)
    if (
        raw_intervals.size == 0
        or not np.all(np.isfinite(raw_intervals))
        or np.any(raw_intervals <= 0)
        or np.any(raw_intervals != np.floor(raw_intervals))
    ):
        raise ValueError("Prediction intervals must contain positive integer steps")
    intervals = raw_intervals.astype(int)
    if horizon is not None and intervals.size != int(horizon):
        raise ValueError(
            f"Prediction intervals require {int(horizon)} values, got {intervals.size}"
        )
    return intervals


def prediction_quadrature_scale(prediction_interval_steps):
    """返回保持离散逐步代价等价性的区间平方根权重。"""
    intervals = normalize_prediction_interval_steps(prediction_interval_steps)
    return np.sqrt(intervals.astype(float))


def prediction_move_interval_steps(prediction_interval_steps):
    """首个动作按基础步限速，后续动作按上一保持区间限速。"""
    intervals = normalize_prediction_interval_steps(prediction_interval_steps)
    return np.concatenate([np.ones(1, dtype=int), intervals[:-1]])


def prediction_integral_map(prediction_interval_steps, dt):
    """把节点误差映射为按真实区间秒数累计的积分误差。"""
    intervals = normalize_prediction_interval_steps(prediction_interval_steps)
    interval_seconds = float(dt) * intervals.astype(float)
    return np.tril(np.ones((intervals.size, intervals.size))) @ np.diag(interval_seconds)


def _blocked_dynamics(ad, bd, interval_steps):
    """在区间内保持控制量不变，计算对应的精确离散转移。"""
    ad = np.asarray(ad, dtype=float)
    bd = np.asarray(bd, dtype=float)
    augmented = np.zeros((ad.shape[0] + bd.shape[1], ad.shape[1] + bd.shape[1]))
    augmented[: ad.shape[0], : ad.shape[1]] = ad
    augmented[: ad.shape[0], ad.shape[1] :] = bd
    augmented[ad.shape[0] :, ad.shape[1] :] = np.eye(bd.shape[1])
    blocked = np.linalg.matrix_power(augmented, int(interval_steps))
    return blocked[: ad.shape[0], : ad.shape[1]], blocked[: ad.shape[0], ad.shape[1] :]


def build_variable_output_prediction_maps(
    ad, bd, output_indices, prediction_interval_steps
):
    """构造可变预测区间、分段常值控制的凝聚输出映射。"""
    ad = np.asarray(ad, dtype=float)
    bd = np.asarray(bd, dtype=float)
    output_indices = tuple(int(index) for index in output_indices)
    intervals = normalize_prediction_interval_steps(prediction_interval_steps)
    horizon = int(intervals.size)
    selectors = np.eye(ad.shape[0], dtype=float)[list(output_indices)]
    initial_maps = np.empty((len(output_indices), horizon, ad.shape[0]))
    input_maps = np.zeros((len(output_indices), horizon, horizon))
    state_map = np.eye(ad.shape[0], dtype=float)
    control_map = np.zeros((ad.shape[0], horizon), dtype=float)
    dynamics_cache = {}
    for step, interval in enumerate(intervals):
        interval = int(interval)
        if interval not in dynamics_cache:
            dynamics_cache[interval] = _blocked_dynamics(ad, bd, interval)
        interval_ad, interval_bd = dynamics_cache[interval]
        state_map = interval_ad @ state_map
        control_map = interval_ad @ control_map
        control_map[:, step] += interval_bd[:, 0]
        initial_maps[:, step, :] = selectors @ state_map
        input_maps[:, step, :] = selectors @ control_map
    return initial_maps, input_maps


class MPCController:
    """控制阀门命令的线性MPC；控制棒和BESS由已验证低层闭环负责。"""

    def __init__(self, ad, bd, x_equilibrium, valve_equilibrium, dt,
                 horizon=10, n=None, q_power=1000.0, q_temperature=0.5,
                 move_weight=2.0, magnitude_weight=0.05, pe_gain=None,
                 integral_weight=0.0, integral_error_limit=None,
                 use_reference_trajectory=False,
                 valve_min=0.0, valve_max=1.2, valve_rate_limit_pu_s=None,
                 prediction_interval_steps=None):
        if n is not None:
            horizon = n
        if prediction_interval_steps is None:
            prediction_interval_steps = np.ones(int(horizon), dtype=int)
        self.prediction_interval_steps = normalize_prediction_interval_steps(
            prediction_interval_steps, horizon
        )
        self.ad = np.asarray(ad, dtype=float)
        self.bd = np.asarray(bd, dtype=float)
        self.x_eq = np.asarray(x_equilibrium, dtype=float)
        self.valve_eq = float(valve_equilibrium)
        self.dt = float(dt)
        self.horizon = int(horizon)
        self.valve_min = float(valve_min)
        self.valve_max = float(valve_max)
        self.valve_rate_limit_pu_s = (
            None if valve_rate_limit_pu_s is None else float(valve_rate_limit_pu_s)
        )
        self.integral_weight = float(integral_weight)
        self.use_reference_trajectory = bool(use_reference_trajectory)
        self.integral_error_limit = (
            None if integral_error_limit is None else float(integral_error_limit)
        )
        self.integral_error = 0.0
        self.last_applied_delta = 0.0
        if self.bd.shape != (self.ad.shape[0], 1):
            raise ValueError(f"Valve MPC expects Bd shape ({self.ad.shape[0]}, 1), got {self.bd.shape}")
        if pe_gain is None:
            raise TypeError("pe_gain is required")

        self.scale_x = np.maximum(np.abs(self.x_eq), 1.0)
        self.u_dev = cp.Variable((1, self.horizon))
        self.delta_u = cp.Variable((1, self.horizon))
        self.x_init_norm = cp.Parameter(self.ad.shape[0])
        self.target_power = (
            cp.Parameter(self.horizon)
            if self.use_reference_trajectory
            else cp.Parameter()
        )
        self.integral_error_initial = (
            cp.Parameter() if self.integral_weight > 0.0 else None
        )
        self.previous_valve = cp.Parameter()

        state_scale = np.diag(self.scale_x)
        state_scale_inverse = np.diag(1.0 / self.scale_x)
        ad_scaled = state_scale_inverse @ self.ad @ state_scale
        bd_scaled = state_scale_inverse @ self.bd

        delta_index = STATE_INDEX["delta"]
        tc1_index = STATE_INDEX["T_c1"]
        tc2_index = STATE_INDEX["T_c2"]
        delta_eq = float(self.x_eq[delta_index])
        pe_eq = float(pe_gain * np.sin(delta_eq))
        dpe_ddelta = float(pe_gain * np.cos(delta_eq))
        self.pe_gain = float(pe_gain)
        self.delta_index = delta_index
        temperature_eq = 0.5 * (self.x_eq[tc1_index] + self.x_eq[tc2_index])

        initial_maps, input_maps = build_output_prediction_maps(
            ad_scaled, bd_scaled, (delta_index, tc1_index, tc2_index), self.horizon
        ) if np.all(self.prediction_interval_steps == 1) else build_variable_output_prediction_maps(
            ad_scaled,
            bd_scaled,
            (delta_index, tc1_index, tc2_index),
            self.prediction_interval_steps,
        )
        input_vector = self.u_dev[0, :]
        delta_norm = initial_maps[0] @ self.x_init_norm + input_maps[0] @ input_vector
        tc1_norm = initial_maps[1] @ self.x_init_norm + input_maps[1] @ input_vector
        tc2_norm = initial_maps[2] @ self.x_init_norm + input_maps[2] @ input_vector
        pe_pred = pe_eq + dpe_ddelta * self.scale_x[delta_index] * delta_norm
        temperature_pred = temperature_eq + 0.5 * (
            self.scale_x[tc1_index] * tc1_norm
            + self.scale_x[tc2_index] * tc2_norm
        )
        tracking_error = pe_pred - self.target_power

        valve_absolute = self.valve_eq + input_vector
        move_vector = self.delta_u[0, :]
        commanded_moves = cp.hstack(
            [
                valve_absolute[0] - self.previous_valve,
                valve_absolute[1:] - valve_absolute[:-1],
            ]
        )
        constraints = [
            valve_absolute >= self.valve_min,
            valve_absolute <= self.valve_max,
            move_vector == commanded_moves,
        ]
        if valve_rate_limit_pu_s is not None:
            move_interval_steps = prediction_move_interval_steps(
                self.prediction_interval_steps
            )
            max_move = float(valve_rate_limit_pu_s) * self.dt * move_interval_steps
            constraints.extend([move_vector >= -max_move, move_vector <= max_move])

        quadrature_scale = prediction_quadrature_scale(self.prediction_interval_steps)
        objective = (
            float(q_power) * cp.sum_squares(cp.multiply(quadrature_scale, tracking_error))
            + float(q_temperature)
            * cp.sum_squares(
                cp.multiply(quadrature_scale, temperature_pred - temperature_eq)
            )
            + float(move_weight) * cp.sum_squares(move_vector)
            + float(magnitude_weight)
            * cp.sum_squares(cp.multiply(quadrature_scale, input_vector))
        )
        if self.integral_weight > 0.0:
            cumulative_error = (
                self.integral_error_initial
                + prediction_integral_map(self.prediction_interval_steps, self.dt)
                @ tracking_error
            )
            objective += self.integral_weight * cp.sum_squares(
                cp.multiply(quadrature_scale, cumulative_error)
            )
        self.problem = cp.Problem(cp.Minimize(objective), constraints)
        self.last_status = "not_solved"
        self.last_solver = "not_solved"
        self.last_fallback_used = False
        self.last_primary_exception = None

    def solve(self, x_current_abs, target_power_abs, previous_valve, t=None,
              current_target_power_abs=None):
        """求解并返回绝对阀门命令；失败时保持上一命令。"""
        x_current_abs = np.asarray(x_current_abs, dtype=float)
        self.x_init_norm.value = (x_current_abs - self.x_eq) / self.scale_x
        if self.use_reference_trajectory:
            target_trajectory = normalize_target_trajectory(target_power_abs, self.horizon)
            self.target_power.value = target_trajectory
        else:
            target_values = np.asarray(target_power_abs, dtype=float).reshape(-1)
            if target_values.size != 1:
                raise ValueError("Scalar-reference MPC requires exactly one target value")
            target_trajectory = np.full(self.horizon, float(target_values[0]), dtype=float)
            self.target_power.value = float(target_values[0])
        current_target = (
            float(target_trajectory[0])
            if current_target_power_abs is None
            else float(current_target_power_abs)
        )
        if self.integral_weight > 0.0:
            current_output = self.pe_gain * np.sin(x_current_abs[self.delta_index])
            self.integral_error += self.dt * (float(current_output) - current_target)
            if self.integral_error_limit is not None:
                self.integral_error = float(np.clip(
                    self.integral_error,
                    -self.integral_error_limit,
                    self.integral_error_limit,
                ))
        if self.integral_error_initial is not None:
            self.integral_error_initial.value = self.integral_error
        self.previous_valve.value = float(previous_valve)

        self.last_solver = "OSQP"
        self.last_fallback_used = False
        self.last_primary_exception = None
        try:
            self.problem.solve(
                solver=cp.OSQP,
                verbose=False,
                max_iter=20000,
                eps_abs=1e-5,
                eps_rel=1e-5,
                check_termination=25,
                warm_start=True,
            )
        except Exception as error:
            self.last_primary_exception = f"{type(error).__name__}: {error}"
            self.last_fallback_used = True
            self.last_solver = "SCS"
            self.problem.solve(solver=cp.SCS, verbose=False, max_iters=10000)

        self.last_status = str(self.problem.status)
        if self.problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE) or self.u_dev.value is None:
            command = float(np.clip(previous_valve, self.valve_min, self.valve_max))
            self.last_applied_delta = command - float(previous_valve)
            return command
        command = self.valve_eq + float(self.u_dev.value[0, 0])
        command = float(np.clip(command, self.valve_min, self.valve_max))
        if self.valve_rate_limit_pu_s is not None:
            max_move = self.valve_rate_limit_pu_s * self.dt
            command = float(np.clip(
                command,
                float(previous_valve) - max_move,
                float(previous_valve) + max_move,
            ))
        self.last_applied_delta = command - float(previous_valve)
        return command
