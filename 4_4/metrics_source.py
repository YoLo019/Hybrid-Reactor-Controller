# -*- coding: utf-8 -*-
"""运行修正后的阀门MPC与内置PID基线，并保存可追溯信号。"""

import os
import time

import matplotlib.pyplot as plt
import numpy as np
from scipy.integrate import solve_ivp

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable

import model_wind
from model_schema import (
    STATE_INDEX,
    STATE_NAMES,
    solver_absolute_tolerances,
    validate_state_vector,
)
from mpc_utils_out import MPCController, get_linear_model
from parameters import get_params


def set_plot_style():
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["mathtext.fontset"] = "stix"
    plt.rcParams["font.size"] = 11
    plt.rcParams["axes.grid"] = True
    plt.rcParams["lines.linewidth"] = 1.8


def build_y0(params, initial_conditions):
    """从单一初始条件源构造44状态向量。"""
    ic = initial_conditions
    values = {
        "P_n": ic["P_n0"],
        **{f"C_in{i + 1}": value for i, value in enumerate(ic["C_in1_6_0"])},
        "T_f": ic["T_f0"], "T_c1": ic["T_c10"], "T_c2": ic["T_c20"],
        "T_rxu": ic["T_rxu0"], "T_hot": ic["T_hot0"],
        "T_sgi": ic["T_sgi0"], "T_sgu": ic["T_sgu0"],
        "T_cold": ic["T_cold0"], "T_rxi": ic["T_rxi0"],
        "T_p1": ic["T_p10"], "T_p2": ic["T_p20"],
        "T_m1": ic["T_m10"], "T_m2": ic["T_m20"],
        "p_s": ic["p_s0"], "L_w": ic["l_w0"], "p_p": ic["p_p0"],
        "delta": ic["delta0"], "omega_g": 1.0,
        "P_hp": params["Fhp"], "P_ip": params["Fip"], "P_lp": params["Flp"],
        "h_wo": ic["h_wo0"], "m_cos": params["m_str"],
        "rho_rod": ic["rho_rod0"],
        "i_lo": ic["i_lo0"], "v_ilo": ic.get("v_ilo0", 0.0),
        "i_lr": ic["i_lr0"], "v_ilr": ic.get("v_ilr", 0.0),
        "T_rtd1": ic["T_rtd1"], "T_rtd2": ic["T_rtd2"],
        "C_tg": ic["C_tg0"], "v_Ctg": ic["v_Ctg0"], "Q_heat": ic["Q_heat0"],
        "e_int_error": ic.get("e_int_power0", 0.0),
        "P_tur_filtered": ic["P_tur_filtered0"],
        "SOC": ic["SOC0"], "e_int_freq": ic["e_int_freq0"],
    }
    y0 = np.asarray([values[name] for name in STATE_NAMES], dtype=float)
    validate_state_vector(y0)
    return y0


def project_detector_equilibrium(state, params):
    """把不反馈主系统的中子仪表快状态投影到当前功率的静态流形。"""
    projected = np.asarray(state, dtype=float).copy()
    neutron_power = max(float(projected[STATE_INDEX["P_n"]]), 1e-12)
    projected[STATE_INDEX["i_lo"]] = (
        float(params["K_lo"]) * np.log10(float(params["k_lo"]) * neutron_power)
    )
    projected[STATE_INDEX["v_ilo"]] = 0.0
    projected[STATE_INDEX["i_lr"]] = 12.0
    projected[STATE_INDEX["v_ilr"]] = 0.0
    return projected


def settle_initial_state(params, initial_conditions, y0=None, duration=1200.0,
                         rtol=1e-7, atol=1e-9):
    """消除来源初值的舍入残差，返回固定设定下的数值平衡状态。"""
    state0 = build_y0(params, initial_conditions) if y0 is None else np.asarray(y0, dtype=float)
    solution = solve_ivp(
        lambda current_t, current_y: model_wind.pwf_model(
            current_t,
            current_y,
            params,
            initial_conditions,
            disturbance_case=0,
            control_mode="pid",
        ),
        (0.0, float(duration)),
        state0,
        method="Radau",
        rtol=rtol,
        atol=solver_absolute_tolerances(atol),
    )
    if not solution.success:
        raise RuntimeError(f"Equilibrium settling failed: {solution.message}")
    # 这四个仪表状态不反馈主系统；投影消除刚性积分端点的快模态插值残差。
    return project_detector_equilibrium(solution.y[:, -1], params)


def target_step(t, t0=30.0, p0=1.0, p1=0.9):
    return p1 if t >= t0 else p0


def target_ramp(t, t0=30.0, dur=60.0, p0=1.0, p1=0.9):
    if t < t0:
        return p0
    if t >= t0 + dur:
        return p1
    return p0 + (p1 - p0) * (t - t0) / dur


def target_trapezoid(t, t0=30.0, down_dur=60.0, hold_dur=60.0,
                     up_dur=60.0, p0=1.0, p1=0.9):
    if t < t0:
        return p0
    if t < t0 + down_dur:
        return p0 + (p1 - p0) * (t - t0) / down_dur
    if t < t0 + down_dur + hold_dur:
        return p1
    if t < t0 + down_dur + hold_dur + up_dur:
        elapsed = t - (t0 + down_dur + hold_dur)
        return p1 + (p0 - p1) * elapsed / up_dur
    return p0


def get_scenario_meta(scenario_name):
    definitions = {
        "Step": (lambda t: target_step(t), "Case 1: STEP", "Step"),
        "Ramp": (lambda t: target_ramp(t), "Case 2: RAMP", "Ramp"),
        "Trapezoid": (lambda t: target_trapezoid(t), "Case 3: TRAPEZOID", "Trapezoid"),
        "NoiseTrapezoid": (
            lambda t: target_trapezoid(t),
            "Case 4: NOISE + TRAPEZOID",
            "NoiseTrapezoid",
        ),
    }
    if scenario_name not in definitions:
        raise ValueError(f"Unknown scenario_name: {scenario_name}")
    return definitions[scenario_name]


def calculate_metrics(y_history, params):
    p_n = y_history[STATE_INDEX["P_n"], :]
    tc_avg = 0.5 * (
        y_history[STATE_INDEX["T_c1"], :] + y_history[STATE_INDEX["T_c2"], :]
    )
    delta = y_history[STATE_INDEX["delta"], :]
    x_total = params["X_d_prime"] + params["X_line"]
    p_e = params["E_prime"] * params["V_inf"] / x_total * np.sin(delta)
    p_tur_filtered = y_history[STATE_INDEX["P_tur_filtered"], :]
    return p_n, p_e, tc_avg, p_tur_filtered


def make_noise_std(n_states):
    noise_std = np.zeros(n_states)
    noise_std[STATE_INDEX["P_n"]] = 0.005
    noise_std[STATE_INDEX["T_f"]:STATE_INDEX["p_s"]] = 0.2
    noise_std[STATE_INDEX["p_s"]] = 0.02
    noise_std[STATE_INDEX["p_p"]] = 0.02
    noise_std[STATE_INDEX["delta"]] = 2e-3
    noise_std[STATE_INDEX["omega_g"]] = 2e-4
    noise_std[STATE_INDEX["SOC"]] = 1e-4
    if len(noise_std) != n_states:
        raise ValueError(f"Noise vector has {len(noise_std)} states, expected {n_states}")
    return noise_std


def apply_state_noise(x_clean, rng, noise_std):
    state = np.asarray(x_clean, dtype=float) + rng.normal(0.0, noise_std)
    state[STATE_INDEX["P_n"]] = max(state[STATE_INDEX["P_n"]], 1e-8)
    state[STATE_INDEX["SOC"]] = np.clip(state[STATE_INDEX["SOC"]], 0.0, 1.0)
    return state


def _empty_signal_store(steps):
    return {
        "rod_speed_spm": np.zeros(steps + 1),
        "valve_command_pu": np.zeros(steps + 1),
        "valve_actual_pu": np.zeros(steps + 1),
        "bess_power_mw": np.zeros(steps + 1),
        "grid_disturbance_pu": np.zeros(steps + 1),
        "frequency_pu": np.zeros(steps + 1),
        "frequency_deviation_hz": np.zeros(steps + 1),
    }


def _record_observation(store, index, t, state, params, ic, control_mode,
                        valve_command=None, grid_disturbance_pu=0.0):
    observation = model_wind.observe_model(
        t,
        state,
        params,
        ic,
        disturbance_case=0,
        control_mode=control_mode,
        u_tg_ext=valve_command,
    )
    for key, value in observation.items():
        if key in store:
            store[key][index] = value
    store["grid_disturbance_pu"][index] = float(grid_disturbance_pu)


def _build_result(controller_name, title, save_tag, t_eval, y_history, target_profile,
                  target_absolute, t_ref, simulation_time, dt, signals, params,
                  solver_status=None, solver_name=None, solver_fallback=None):
    p_n, p_e, tc_avg, p_tur = calculate_metrics(y_history, params)
    result = {
        "ctrl": controller_name,
        "title": title,
        "save_tag": save_tag,
        "t": t_eval,
        "Y": y_history,
        "P_n": p_n,
        "P_e": p_e,
        "T_c_avg": tc_avg,
        "P_tur": p_tur,
        "Target_profile": target_profile,
        "Target_abs": target_absolute,
        "T_ref": t_ref,
        "sim_time": simulation_time,
        "DT": dt,
        "u_rod_cmd": signals["rod_speed_spm"],
        "u_val_cmd": signals["valve_command_pu"],
        **signals,
    }
    if solver_status is not None:
        result["solver_status"] = np.asarray(solver_status, dtype=object)
    if solver_name is not None:
        result["solver_name"] = np.asarray(solver_name, dtype=object)
    if solver_fallback is not None:
        result["solver_fallback"] = np.asarray(solver_fallback, dtype=bool)
    return result


def resolve_preview_forecast(
    issue_time_s,
    node_times_s,
    current_target_power_abs,
    forecast_type,
    target_function=None,
    forecast_provider=None,
    target_scale=1.0,
):
    """按发布时刻冻结预测信息，禁止实际预测路径读取未来真实目标。"""
    node_times = np.asarray(node_times_s, dtype=float).reshape(-1)
    if node_times.size == 0 or not np.all(np.isfinite(node_times)):
        raise ValueError("Forecast node times must be finite and non-empty")
    if forecast_type == "perfect_foresight":
        if target_function is None:
            raise ValueError("perfect_foresight requires a target function")
        values = np.asarray(
            [float(target_function(value)) * float(target_scale) for value in node_times],
            dtype=float,
        )
    elif forecast_type == "none":
        values = np.full(node_times.size, float(current_target_power_abs), dtype=float)
    elif forecast_type in ("persistence", "ridge_direct_ar"):
        if forecast_provider is None:
            raise ValueError(f"{forecast_type} requires an issue-time forecast provider")
        values = np.asarray(
            forecast_provider(
                issue_time_s=float(issue_time_s),
                target_times_s=node_times.copy(),
                forecast_type=str(forecast_type),
                issue_value_pu=float(current_target_power_abs),
            ),
            dtype=float,
        ).reshape(-1)
    else:
        raise ValueError(f"Unsupported forecast_type: {forecast_type}")
    if values.size != node_times.size or not np.all(np.isfinite(values)):
        raise ValueError("Forecast provider must return one finite value per target node")
    return values


def valve_equilibrium_from_state(state, initial_conditions):
    """由调门执行器稳态位置恢复其绝对命令，避免把功率设定误当作阀门命令。"""
    command = float(state[STATE_INDEX["C_tg"]]) / float(initial_conditions["C_tg0"])
    return float(np.clip(command, 0.0, 1.2))


def run_mpc_scenario(scenario_name, params, ic, y0, dt, t_end, n=30,
                     q_weights=None, r_weights=None, use_noise=False, seed=1,
                     target_function=None, scenario_title=None, scenario_tag=None,
                     show_progress=True, use_reference_preview=False,
                     integral_weight=0.0, integral_error_limit=None,
                     grid_disturbance_function=None,
                     prediction_interval_steps=None, forecast_type=None,
                     forecast_provider=None):
    """阀门由MPC控制；控制棒PID和BESS PI均在对象内闭环。"""
    rng = np.random.default_rng(seed)
    noise_std = make_noise_std(len(y0))
    steps = int(round(t_end / dt))
    t_eval = np.linspace(0.0, t_end, steps + 1)
    if target_function is None:
        get_target, title, save_tag = get_scenario_meta(scenario_name)
    else:
        get_target = target_function
        title = scenario_title or scenario_name
        save_tag = scenario_tag or scenario_name
    valve_equilibrium = valve_equilibrium_from_state(y0, ic)
    ad, bd = get_linear_model(params, ic, y0, valve_equilibrium, dt=dt)
    # 按允许误差量级标度：0.1 p.u.功率误差不应被亚摄氏度温差压倒。
    q_weights = q_weights or {"power": 1000.0, "Tavg": 0.5}
    r_weights = r_weights or {"move": 2.0, "magnitude": 0.05}
    x_total = params["X_d_prime"] + params["X_line"]
    pe_gain = params["E_prime"] * params["V_inf"] / x_total
    if prediction_interval_steps is None:
        prediction_interval_steps = np.ones(int(n), dtype=int)
    prediction_interval_steps = np.asarray(
        prediction_interval_steps, dtype=int
    ).reshape(-1)
    if prediction_interval_steps.size != int(n):
        raise ValueError(
            f"prediction_interval_steps requires {int(n)} values, "
            f"got {prediction_interval_steps.size}"
        )
    preview_offsets_s = dt * np.cumsum(prediction_interval_steps)
    controller = MPCController(
        ad,
        bd,
        y0,
        valve_equilibrium,
        dt,
        n=n,
        q_power=q_weights["power"],
        q_temperature=q_weights["Tavg"],
        move_weight=r_weights["move"],
        magnitude_weight=r_weights["magnitude"],
        pe_gain=pe_gain,
        integral_weight=integral_weight,
        integral_error_limit=integral_error_limit,
        use_reference_trajectory=use_reference_preview,
        valve_rate_limit_pu_s=float(params.get("valve_rate_limit_pu_s", 0.05)),
        prediction_interval_steps=prediction_interval_steps,
    )

    y_history = np.zeros((len(y0), steps + 1))
    y_history[:, 0] = y0
    state = y0.copy()
    signals = _empty_signal_store(steps)
    solver_status = []
    solver_name = []
    solver_fallback = []
    target_profile = np.zeros(steps + 1)
    target_absolute = np.zeros(steps + 1)
    pe0 = pe_gain * np.sin(y0[STATE_INDEX["delta"]])
    previous_valve = valve_equilibrium
    start_time = time.time()
    get_grid_disturbance = grid_disturbance_function or (lambda _: 0.0)

    iterator = tqdm(range(steps), desc=f"MPC-{scenario_name}") if show_progress else range(steps)
    for k in iterator:
        current_time = t_eval[k]
        profile = float(get_target(current_time))
        target_profile[k] = profile
        target_absolute[k] = profile * pe0
        if use_reference_preview:
            target_for_controller = resolve_preview_forecast(
                issue_time_s=current_time,
                node_times_s=current_time + preview_offsets_s,
                current_target_power_abs=target_absolute[k],
                forecast_type=forecast_type,
                target_function=get_target,
                forecast_provider=forecast_provider,
                target_scale=pe0,
            )
        else:
            if forecast_type not in (None, "none"):
                raise ValueError("A future forecast requires use_reference_preview=True")
            target_for_controller = target_absolute[k]
        valve_command = controller.solve(
            state,
            target_for_controller,
            previous_valve,
            current_time,
            current_target_power_abs=target_absolute[k],
        )
        previous_valve = valve_command
        solver_status.append(controller.last_status)
        solver_name.append(controller.last_solver)
        solver_fallback.append(controller.last_fallback_used)
        grid_disturbance_pu = float(get_grid_disturbance(current_time))
        _record_observation(
            signals, k, current_time, state, params, ic, "pid", valve_command,
            grid_disturbance_pu,
        )

        solution = solve_ivp(
            lambda current_t, current_y: model_wind.pwf_model(
                current_t,
                current_y,
                params,
                ic,
                disturbance_case=0,
                control_mode="pid",
                u_tg_ext=valve_command,
                p_grid_disturbance_pu=float(get_grid_disturbance(current_t)),
            ),
            (current_time, current_time + dt),
            state,
            method="Radau",
            rtol=1e-6,
            atol=solver_absolute_tolerances(1e-8),
        )
        if not solution.success:
            raise RuntimeError(f"Integration failed at t={current_time}: {solution.message}")
        state = solution.y[:, -1]
        if use_noise:
            state = apply_state_noise(state, rng, noise_std)
        state[STATE_INDEX["SOC"]] = np.clip(state[STATE_INDEX["SOC"]], 0.0, 1.0)
        y_history[:, k + 1] = state

    target_profile[-1] = float(get_target(t_eval[-1]))
    target_absolute[-1] = target_profile[-1] * pe0
    _record_observation(
        signals, -1, t_eval[-1], state, params, ic, "pid", previous_valve,
        float(get_grid_disturbance(t_eval[-1])),
    )
    return _build_result(
        "MPC", title, save_tag, t_eval, y_history, target_profile, target_absolute,
        0.5 * (ic["T_c10"] + ic["T_c20"]), time.time() - start_time, dt,
        signals, params, solver_status,
        solver_name, solver_fallback,
    )


def run_pid_scenario(scenario_name, params, ic, y0, dt, t_end, use_noise=False, seed=1,
                     enforce_valve_rate_limit=True, target_function=None,
                     scenario_title=None, scenario_tag=None, show_progress=True,
                     grid_disturbance_function=None):
    rng = np.random.default_rng(seed)
    noise_std = make_noise_std(len(y0))
    steps = int(round(t_end / dt))
    t_eval = np.linspace(0.0, t_end, steps + 1)
    if target_function is None:
        get_target, title, save_tag = get_scenario_meta(scenario_name)
    else:
        get_target = target_function
        title = scenario_title or scenario_name
        save_tag = scenario_tag or scenario_name
    y_history = np.zeros((len(y0), steps + 1))
    y_history[:, 0] = y0
    state = y0.copy()
    signals = _empty_signal_store(steps)
    target_profile = np.zeros(steps + 1)
    target_absolute = np.zeros(steps + 1)
    x_total = params["X_d_prime"] + params["X_line"]
    pe_gain = params["E_prime"] * params["V_inf"] / x_total
    pe0 = pe_gain * np.sin(y0[STATE_INDEX["delta"]])
    previous_valve = valve_equilibrium_from_state(y0, ic)
    start_time = time.time()
    get_grid_disturbance = grid_disturbance_function or (lambda _: 0.0)

    iterator = tqdm(range(steps), desc=f"PID-{scenario_name}") if show_progress else range(steps)
    for k in iterator:
        current_time = t_eval[k]
        profile = float(get_target(current_time))
        params["P_load_ref"] = profile * pe0
        target_profile[k] = profile
        target_absolute[k] = profile * pe0
        raw_valve_command = model_wind.compute_turbine_command_pu(
            current_time, state, params, ic
        )
        if enforce_valve_rate_limit:
            max_move = float(params.get("valve_rate_limit_pu_s", 0.05)) * dt
            valve_command = float(np.clip(
                raw_valve_command,
                previous_valve - max_move,
                previous_valve + max_move,
            ))
        else:
            valve_command = raw_valve_command
        previous_valve = valve_command
        grid_disturbance_pu = float(get_grid_disturbance(current_time))
        _record_observation(
            signals, k, current_time, state, params, ic, "pid", valve_command,
            grid_disturbance_pu,
        )
        solution = solve_ivp(
            lambda current_t, current_y: model_wind.pwf_model(
                current_t,
                current_y,
                params,
                ic,
                disturbance_case=0,
                control_mode="pid",
                u_tg_ext=valve_command,
                p_grid_disturbance_pu=float(get_grid_disturbance(current_t)),
            ),
            (current_time, current_time + dt),
            state,
            method="Radau",
            rtol=1e-6,
            atol=solver_absolute_tolerances(1e-8),
        )
        if not solution.success:
            raise RuntimeError(f"Integration failed at t={current_time}: {solution.message}")
        state = solution.y[:, -1]
        if use_noise:
            state = apply_state_noise(state, rng, noise_std)
        state[STATE_INDEX["SOC"]] = np.clip(state[STATE_INDEX["SOC"]], 0.0, 1.0)
        y_history[:, k + 1] = state

    target_profile[-1] = float(get_target(t_eval[-1]))
    target_absolute[-1] = target_profile[-1] * pe0
    params["P_load_ref"] = target_absolute[-1]
    _record_observation(
        signals, -1, t_eval[-1], state, params, ic, "pid", previous_valve,
        float(get_grid_disturbance(t_eval[-1])),
    )
    return _build_result(
        "PID", title, save_tag, t_eval, y_history, target_profile, target_absolute,
        0.5 * (ic["T_c10"] + ic["T_c20"]), time.time() - start_time, dt,
        signals, params,
    )


def plot_and_save_compare(res_mpc, res_pid, save_png):
    t = res_mpc["t"]
    figure, axes = plt.subplots(2, 1, figsize=(10, 7.5), sharex=True)
    axes[0].set_title(f"{res_mpc['title']} | valve MPC vs built-in PID")
    axes[0].plot(t, res_mpc["P_e"], label="Pe (MPC)")
    axes[0].plot(t, res_pid["P_e"], "--", label="Pe (PID)")
    axes[0].plot(t, res_mpc["Target_abs"], "k--", label="Pe reference")
    axes[0].set_ylabel("Pe (p.u.)")
    axes[0].legend()
    axes[1].plot(t, res_mpc["T_c_avg"], label="Tc avg (MPC)")
    axes[1].plot(t, res_pid["T_c_avg"], "--", label="Tc avg (PID)")
    axes[1].axhline(res_mpc["T_ref"], color="gray", linestyle="-.", label="setpoint")
    axes[1].set_ylabel("Temperature (degC)")
    axes[1].set_xlabel("Time (s)")
    axes[1].legend()
    figure.tight_layout()
    figure.savefig(save_png, dpi=300)
    plt.close(figure)


def save_npz_result(result, output_path):
    y_history = result["Y"]
    pack = {
        "t": result["t"], "Y": y_history,
        "Pe": result["P_e"], "Pn": result["P_n"], "Tc_avg": result["T_c_avg"],
        "Target_abs": result["Target_abs"], "Target_profile": result["Target_profile"],
        "u_rod": result["rod_speed_spm"],
        "u_val": result["valve_actual_pu"],
        "u_val_cmd": result["valve_command_pu"],
        "P_bess": result["bess_power_mw"],
        "freq": result["frequency_deviation_hz"],
        "omega_g": result["frequency_pu"],
        "SOC": y_history[STATE_INDEX["SOC"], :],
        "delta": y_history[STATE_INDEX["delta"], :],
        "rho_rod": y_history[STATE_INDEX["rho_rod"], :],
        "C_tg": y_history[STATE_INDEX["C_tg"], :],
        "controller": np.asarray([result["ctrl"]], dtype=object),
        "DT": np.asarray([result["DT"]]),
        "sim_time": np.asarray([result["sim_time"]]),
    }
    if "solver_status" in result:
        pack["solver_status"] = result["solver_status"]
    np.savez(output_path, **pack)


def main():
    set_plot_style()
    dt = 0.5
    horizon = 30
    output_dir = "npz_results"
    os.makedirs(output_dir, exist_ok=True)
    for name, t_end, use_noise in [("NoiseTrapezoid", 400.0, True)]:
        params_mpc, ic_mpc = get_params()
        settled_mpc = settle_initial_state(params_mpc, ic_mpc)
        result_mpc = run_mpc_scenario(
            name,
            params_mpc,
            ic_mpc,
            settled_mpc,
            dt,
            t_end,
            n=horizon,
            use_noise=use_noise,
        )
        params_pid, ic_pid = get_params()
        settled_pid = settle_initial_state(params_pid, ic_pid)
        result_pid = run_pid_scenario(
            name,
            params_pid,
            ic_pid,
            settled_pid,
            dt,
            t_end,
            use_noise=use_noise,
        )
        suffix = "_NOISE" if use_noise else ""
        plot_and_save_compare(result_mpc, result_pid, f"Validate_{name}{suffix}_MPC_vs_PID.png")
        save_npz_result(result_mpc, os.path.join(output_dir, f"res_{name}{suffix}_MPC.npz"))
        save_npz_result(result_pid, os.path.join(output_dir, f"res_{name}{suffix}_PID.npz"))


if __name__ == "__main__":
    main()
