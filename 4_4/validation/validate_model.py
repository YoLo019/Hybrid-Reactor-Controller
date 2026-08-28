# -*- coding: utf-8 -*-
"""执行模型可信性验证并输出机器可读证据。"""

import argparse
import copy
import json
import platform
import sys
from pathlib import Path

import numpy as np
from scipy import __version__ as scipy_version
from scipy.integrate import solve_ivp

MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

import model_wind
from metrics_source import build_y0, project_detector_equilibrium, settle_initial_state
from metrics_source import run_pid_scenario
from model_schema import (
    STATE_INDEX,
    STATE_NAMES,
    STATE_RANGES,
    STATE_UNITS,
    solver_absolute_tolerances,
)
from mpc_utils_out import MPCController, get_linear_model
from parameter_provenance import audit_parameter_provenance
from parameters import get_params


def _integrate(params, ic, y0, duration, valve_command=None, rtol=1e-7, atol=1e-9):
    solution = solve_ivp(
        lambda current_t, current_y: model_wind.pwf_model(
            current_t,
            current_y,
            params,
            ic,
            disturbance_case=0,
            control_mode="pid",
            u_tg_ext=valve_command,
        ),
        (0.0, float(duration)),
        np.asarray(y0, dtype=float),
        method="Radau",
        rtol=rtol,
        atol=solver_absolute_tolerances(atol),
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    return solution.y[:, -1], solution.nfev


def _key_outputs(state, params, ic):
    observation = model_wind.observe_model(0.0, state, params, ic)
    return np.asarray([
        observation["p_e_pu"],
        observation["p_tur_pu"],
        observation["frequency_pu"],
        float(state[STATE_INDEX["SOC"]]),
        0.5 * (state[STATE_INDEX["T_c1"]] + state[STATE_INDEX["T_c2"]]),
    ])


def _integrate_trajectory(
    params,
    ic,
    y0,
    duration,
    valve_command,
    max_step,
    sample_step=0.25,
):
    """在统一输出网格上积分，用于步长收敛和确定性重复检查。"""
    sample_count = int(round(float(duration) / float(sample_step)))
    t_eval = np.linspace(0.0, float(duration), sample_count + 1)
    solution = solve_ivp(
        lambda current_t, current_y: model_wind.pwf_model(
            current_t,
            current_y,
            params,
            ic,
            disturbance_case=0,
            control_mode="pid",
            u_tg_ext=valve_command,
        ),
        (0.0, float(duration)),
        np.asarray(y0, dtype=float),
        method="Radau",
        t_eval=t_eval,
        max_step=float(max_step),
        rtol=1e-7,
        atol=solver_absolute_tolerances(1e-9),
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    outputs = np.column_stack([
        _key_outputs(solution.y[:, index], params, ic)
        for index in range(solution.y.shape[1])
    ])
    return t_eval, solution.y, outputs


def _trajectory_summary(t_eval, outputs):
    initial = outputs[:, [0]]
    deviations = np.abs(outputs - initial)
    return np.concatenate([
        np.max(deviations, axis=1),
        outputs[:, -1],
        np.trapz(deviations, t_eval, axis=1),
        np.min(outputs, axis=1),
    ])


def _validate_reduced_power_point(base_params, ic, base_state, target_power):
    """从额定点降载并验证目标功率点的稳态、守恒与线性化。"""
    params = copy.deepcopy(base_params)
    params["P_load_ref"] = float(target_power)
    operating_state, transition_nfev = _integrate(params, ic, base_state, 1800.0)
    operating_state = project_detector_equilibrium(operating_state, params)
    derivative = np.asarray(model_wind.pwf_model(0.0, operating_state, params, ic))
    drift_state, drift_nfev = _integrate(params, ic, operating_state, 600.0)
    observation = model_wind.observe_model(0.0, operating_state, params, ic)
    drift_observation = model_wind.observe_model(600.0, drift_state, params, ic)
    instrument_states = {"i_lo", "v_ilo", "i_lr", "v_ilr"}
    plant_indices = [
        index for index, name in enumerate(STATE_NAMES) if name not in instrument_states
    ]
    power_balance_residual = abs(
        observation["p_tur_pu"] + observation["bess_power_pu"]
        - observation["p_e_pu"] - 2.0 * (observation["frequency_pu"] - 1.0)
    )
    ad, bd = get_linear_model(params, ic, operating_state, observation["valve_command_pu"], dt=0.5)
    metrics = {
        "target_power_pu": float(target_power),
        "electrical_power_pu": float(observation["p_e_pu"]),
        "neutron_power_pu": float(operating_state[STATE_INDEX["P_n"]]),
        "frequency_pu": float(observation["frequency_pu"]),
        "bess_power_mw": float(observation["bess_power_mw"]),
        "soc": float(operating_state[STATE_INDEX["SOC"]]),
        "plant_max_abs_derivative": float(np.max(np.abs(derivative[plant_indices]))),
        "frequency_drift_pu_600s": float(abs(
            drift_observation["frequency_pu"] - observation["frequency_pu"]
        )),
        "soc_drift_600s": float(abs(
            drift_state[STATE_INDEX["SOC"]] - operating_state[STATE_INDEX["SOC"]]
        )),
        "pe_drift_pu_600s": float(abs(
            drift_observation["p_e_pu"] - observation["p_e_pu"]
        )),
        "power_balance_residual_pu": float(power_balance_residual),
        "linear_input_norm": float(np.linalg.norm(bd)),
        "transition_nfev": int(transition_nfev),
        "drift_nfev": int(drift_nfev),
    }
    checks = {
        "target_tracking": abs(observation["p_e_pu"] - target_power) < 5e-4,
        "settled_derivative": metrics["plant_max_abs_derivative"] < 1e-4,
        "frequency_recovery": abs(observation["frequency_pu"] - 1.0) < 1e-4,
        "bess_returns_to_zero": abs(observation["bess_power_mw"]) < 1e-3,
        "soc_within_bounds": params["SOC_min"] < metrics["soc"] < params["SOC_max"],
        "frequency_drift": metrics["frequency_drift_pu_600s"] < 1e-4,
        "soc_drift": metrics["soc_drift_600s"] < 1e-4,
        "electrical_power_drift": metrics["pe_drift_pu_600s"] < 5e-4,
        "power_balance": power_balance_residual < 5e-3,
        "linear_model_finite": (
            ad.shape == (44, 44) and bd.shape == (44, 1)
            and np.all(np.isfinite(ad)) and np.all(np.isfinite(bd))
            and float(np.linalg.norm(bd)) > 0.0
        ),
    }
    checks = {name: bool(value) for name, value in checks.items()}
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "metrics": metrics,
    }


def run_validation():
    params, ic = get_params()
    provenance = audit_parameter_provenance(params, ic)
    raw_state = build_y0(params, ic)
    raw_derivative = np.asarray(model_wind.pwf_model(0.0, raw_state, params, ic), dtype=float)
    settled_state = settle_initial_state(params, ic, raw_state)
    settled_derivative = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic), dtype=float
    )

    drift_state, drift_nfev = _integrate(params, ic, settled_state, 600.0)
    settled_observation = model_wind.observe_model(0.0, settled_state, params, ic)
    drift_observation = model_wind.observe_model(600.0, drift_state, params, ic)
    frequency_drift_pu = abs(drift_observation["frequency_pu"] - settled_observation["frequency_pu"])
    soc_drift = abs(float(drift_state[STATE_INDEX["SOC"]] - settled_state[STATE_INDEX["SOC"]]))
    pe_drift = abs(drift_observation["p_e_pu"] - settled_observation["p_e_pu"])
    temperature_drift = abs(
        0.5 * (drift_state[STATE_INDEX["T_c1"]] + drift_state[STATE_INDEX["T_c2"]])
        - 0.5 * (settled_state[STATE_INDEX["T_c1"]] + settled_state[STATE_INDEX["T_c2"]])
    )
    power_balance_residual = abs(
        settled_observation["p_tur_pu"]
        + settled_observation["bess_power_pu"]
        - settled_observation["p_e_pu"]
        - 2.0 * (settled_observation["frequency_pu"] - 1.0)
    )

    derivative_rod_positive = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, v_rod_ext=10.0)
    )
    derivative_rod_negative = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, v_rod_ext=-10.0)
    )
    derivative_bess_discharge = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, p_bess_ext_mw=5.0)
    )
    derivative_bess_charge = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, p_bess_ext_mw=-5.0)
    )
    derivative_valve_up = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, u_tg_ext=1.02)
    )
    derivative_valve_down = np.asarray(
        model_wind.pwf_model(0.0, settled_state, params, ic, u_tg_ext=0.98)
    )

    direction_params = copy.deepcopy(params)
    direction_params["P_load_ref"] = 0.9
    direction_params["wind_dt"] = 1.0
    direction_params["wind_t_start"] = 0.0
    direction_params["wind_profile"] = [0.01]
    wind_surplus_reference = model_wind.compute_turbine_command_pu(
        0.0, settled_state, direction_params, ic, disturbance_case=6
    )
    direction_params["wind_profile"] = [-0.01]
    wind_deficit_reference = model_wind.compute_turbine_command_pu(
        0.0, settled_state, direction_params, ic, disturbance_case=6
    )
    load_increase_derivative = np.asarray(model_wind.pwf_model(
        0.0, settled_state, params, ic, p_grid_disturbance_pu=0.01
    ))
    load_decrease_derivative = np.asarray(model_wind.pwf_model(
        0.0, settled_state, params, ic, p_grid_disturbance_pu=-0.01
    ))

    loose_state, loose_nfev = _integrate(
        params, ic, settled_state, 30.0, valve_command=0.98, rtol=1e-5, atol=1e-7
    )
    strict_state, strict_nfev = _integrate(
        params, ic, settled_state, 30.0, valve_command=0.98, rtol=1e-7, atol=1e-9
    )
    loose_outputs = _key_outputs(loose_state, params, ic)
    strict_outputs = _key_outputs(strict_state, params, ic)
    convergence_error = float(
        np.max(np.abs(loose_outputs - strict_outputs) / np.maximum(np.abs(strict_outputs), 1.0))
    )

    step_t, step_coarse_state, step_coarse_outputs = _integrate_trajectory(
        params, ic, settled_state, 30.0, valve_command=0.98, max_step=0.5
    )
    _, step_fine_state, step_fine_outputs = _integrate_trajectory(
        params, ic, settled_state, 30.0, valve_command=0.98, max_step=0.25
    )
    _, step_repeat_state, step_repeat_outputs = _integrate_trajectory(
        params, ic, settled_state, 30.0, valve_command=0.98, max_step=0.25
    )
    coarse_summary = _trajectory_summary(step_t, step_coarse_outputs)
    fine_summary = _trajectory_summary(step_t, step_fine_outputs)
    step_halving_error = float(np.max(
        np.abs(coarse_summary - fine_summary) / np.maximum(np.abs(fine_summary), 1.0)
    ))
    deterministic_repeat_error = float(np.max(np.abs(
        step_fine_state - step_repeat_state
    )))

    noise_kwargs = {
        "scenario_name": "E0SeededNoise",
        "ic": copy.deepcopy(ic),
        "y0": settled_state.copy(),
        "dt": 0.5,
        "t_end": 5.0,
        "use_noise": True,
        "target_function": lambda _: 1.0,
        "scenario_title": "E0 seeded reproducibility",
        "scenario_tag": "E0Seeded",
        "show_progress": False,
    }
    seeded_a = run_pid_scenario(
        params=copy.deepcopy(params), seed=20260823, **noise_kwargs
    )
    seeded_b = run_pid_scenario(
        params=copy.deepcopy(params), seed=20260823, **noise_kwargs
    )
    seeded_c = run_pid_scenario(
        params=copy.deepcopy(params), seed=20260824, **noise_kwargs
    )
    seeded_repeat_error = float(np.max(np.abs(seeded_a["Y"] - seeded_b["Y"])))
    seeded_distinct_error = float(np.max(np.abs(seeded_a["Y"] - seeded_c["Y"])))

    ad, bd = get_linear_model(params, ic, settled_state, 1.0, dt=0.5)
    ad_wide_step, bd_wide_step = get_linear_model(
        params, ic, settled_state, 1.0, dt=0.5, relative_step=1e-4
    )
    linearization_step_error = max(
        float(np.linalg.norm(ad - ad_wide_step) / max(np.linalg.norm(ad), 1.0)),
        float(np.linalg.norm(bd - bd_wide_step) / max(np.linalg.norm(bd), 1.0)),
    )
    nonlinear_small_step, _ = _integrate(
        params, ic, settled_state, 0.5, valve_command=1.001
    )
    linear_small_step = settled_state + bd[:, 0] * 0.001
    small_signal_output_error = float(np.max(np.abs(
        _key_outputs(linear_small_step, params, ic)
        - _key_outputs(nonlinear_small_step, params, ic)
    ) / np.maximum(np.abs(_key_outputs(nonlinear_small_step, params, ic)), 1.0)))
    pe_gain = params["E_prime"] * params["V_inf"] / (params["X_d_prime"] + params["X_line"])
    controller = MPCController(
        ad,
        bd,
        settled_state,
        1.0,
        0.5,
        n=5,
        pe_gain=pe_gain,
        valve_rate_limit_pu_s=0.05,
    )
    valve_command = controller.solve(settled_state, 0.9, 1.0)
    first_delta = float(controller.last_applied_delta)

    soc_initialization_ok = True
    for soc_value in (0.2, 0.5, 0.8):
        varied_ic = copy.deepcopy(ic)
        varied_ic["SOC0"] = soc_value
        soc_initialization_ok = soc_initialization_ok and (
            abs(build_y0(params, varied_ic)[STATE_INDEX["SOC"]] - soc_value) < 1e-12
        )
    state_at_soc_min = settled_state.copy()
    state_at_soc_min[STATE_INDEX["SOC"]] = params["SOC_min"]
    state_at_soc_max = settled_state.copy()
    state_at_soc_max[STATE_INDEX["SOC"]] = params["SOC_max"]
    bess_soc_limits_ok = (
        model_wind.compute_bess_power_mw(state_at_soc_min, params, 5.0) == 0.0
        and model_wind.compute_bess_power_mw(state_at_soc_max, params, -5.0) == 0.0
    )
    rod_positive_limit, _ = model_wind.compute_rod_control(
        settled_state, params, ic, v_rod_ext=1e6
    )
    rod_negative_limit, _ = model_wind.compute_rod_control(
        settled_state, params, ic, v_rod_ext=-1e6
    )
    valve_positive_limit = model_wind.compute_turbine_command_pu(
        0.0, settled_state, params, ic, u_tg_ext=1e6
    )
    valve_negative_limit = model_wind.compute_turbine_command_pu(
        0.0, settled_state, params, ic, u_tg_ext=-1e6
    )

    observation_keys = set(settled_observation)
    required_observation_keys = {
        "valve_command_pu", "valve_actual_pu", "rod_speed_spm", "bess_power_mw",
        "frequency_pu", "frequency_deviation_hz", "p_e_pu", "p_tur_pu",
    }
    checks = {
        "state_count": len(raw_state) == 44 == len(STATE_NAMES),
        "state_schema_complete": (
            set(STATE_NAMES) == set(STATE_UNITS) == set(STATE_RANGES)
            and len(STATE_INDEX) == len(STATE_NAMES)
        ),
        "parameter_provenance_complete": provenance["pass"],
        "raw_rounding_transient_removed": float(np.max(np.abs(raw_derivative))) < 0.01,
        "settled_derivative": float(np.max(np.abs(settled_derivative))) < 1e-6,
        "frequency_drift": frequency_drift_pu < 1e-4,
        "soc_drift": soc_drift < 1e-4,
        "electrical_power_drift": pe_drift < 5e-4,
        "temperature_drift": temperature_drift < 0.05,
        "power_balance": power_balance_residual < 5e-3,
        "rod_direction": (
            derivative_rod_positive[STATE_INDEX["rho_rod"]] > 0.0
            and derivative_rod_negative[STATE_INDEX["rho_rod"]] < 0.0
        ),
        "bess_soc_direction": (
            derivative_bess_discharge[STATE_INDEX["SOC"]] < 0.0
            and derivative_bess_charge[STATE_INDEX["SOC"]] > 0.0
        ),
        "bess_frequency_direction": (
            derivative_bess_discharge[STATE_INDEX["omega_g"]]
            > derivative_bess_charge[STATE_INDEX["omega_g"]]
        ),
        "valve_actuator_direction": (
            derivative_valve_up[STATE_INDEX["v_Ctg"]] > 0.0
            and derivative_valve_down[STATE_INDEX["v_Ctg"]] < 0.0
        ),
        "wind_reference_direction": (
            wind_surplus_reference < 0.9 < wind_deficit_reference
        ),
        "load_disturbance_rocof_direction": (
            load_increase_derivative[STATE_INDEX["omega_g"]] < 0.0
            and load_decrease_derivative[STATE_INDEX["omega_g"]] > 0.0
        ),
        "rod_saturation": (
            rod_positive_limit == params["rod_speed_limit_spm"]
            and rod_negative_limit == -params["rod_speed_limit_spm"]
        ),
        "valve_saturation": (
            valve_positive_limit == 1.2 and valve_negative_limit == 0.0
        ),
        "numerical_convergence": convergence_error < 1e-3,
        "step_halving_convergence": step_halving_error < 1e-2,
        "deterministic_reproducibility": deterministic_repeat_error < 1e-12,
        "seeded_reproducibility": (
            seeded_repeat_error < 1e-12 and seeded_distinct_error > 0.0
        ),
        "linearization_step_sensitivity": linearization_step_error < 1e-5,
        "linear_nonlinear_small_signal": small_signal_output_error < 1e-3,
        "linear_model_finite": (
            ad.shape == (44, 44) and bd.shape == (44, 1)
            and np.all(np.isfinite(ad)) and np.all(np.isfinite(bd))
            and float(np.linalg.norm(bd)) > 0.0
        ),
        "mpc_solved": controller.last_status in ("optimal", "optimal_inaccurate"),
        "mpc_true_first_increment": abs(first_delta - (valve_command - 1.0)) < 1e-7,
        "mpc_rate_limit": abs(first_delta) <= 0.05 * 0.5 + 1e-8,
        "soc_initialization": soc_initialization_ok,
        "bess_soc_limits": bess_soc_limits_ok,
        "observation_contract": required_observation_keys.issubset(observation_keys),
    }
    checks = {name: bool(value) for name, value in checks.items()}
    reduced_power_points = {
        "90_percent": _validate_reduced_power_point(params, ic, settled_state, 0.9),
        "80_percent": _validate_reduced_power_point(params, ic, settled_state, 0.8),
    }
    dynamic_baseline_pass = all(checks.values()) and all(
        result["pass"] for result in reduced_power_points.values()
    )

    return {
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy_version,
        },
        "scope": "80%, 90%, and 100% nominal-power operating points",
        "checks": checks,
        "parameter_provenance": provenance,
        "full_power_pass": all(checks.values()),
        "reduced_power_points": reduced_power_points,
        "dynamic_baseline_pass": dynamic_baseline_pass,
        "metrics": {
            "raw_max_abs_derivative": float(np.max(np.abs(raw_derivative))),
            "settled_max_abs_derivative": float(np.max(np.abs(settled_derivative))),
            "frequency_drift_pu_600s": frequency_drift_pu,
            "soc_drift_600s": soc_drift,
            "pe_drift_pu_600s": pe_drift,
            "temperature_drift_c_600s": temperature_drift,
            "power_balance_residual_pu": power_balance_residual,
            "convergence_relative_error": convergence_error,
            "step_halving_relative_error": step_halving_error,
            "deterministic_repeat_max_abs_error": deterministic_repeat_error,
            "seeded_repeat_max_abs_error": seeded_repeat_error,
            "different_seed_max_abs_difference": seeded_distinct_error,
            "wind_surplus_reference_pu": float(wind_surplus_reference),
            "wind_deficit_reference_pu": float(wind_deficit_reference),
            "load_increase_initial_rocof_pu_s": float(
                load_increase_derivative[STATE_INDEX["omega_g"]]
            ),
            "load_decrease_initial_rocof_pu_s": float(
                load_decrease_derivative[STATE_INDEX["omega_g"]]
            ),
            "linearization_step_relative_error": linearization_step_error,
            "small_signal_output_relative_error": small_signal_output_error,
            "loose_nfev": loose_nfev,
            "strict_nfev": strict_nfev,
            "drift_nfev": drift_nfev,
            "linear_input_norm": float(np.linalg.norm(bd)),
            "mpc_valve_command": valve_command,
            "mpc_first_increment": first_delta,
        },
        "g0_pass": bool(dynamic_baseline_pass and provenance["pass"]),
        "g0_remaining": [] if dynamic_baseline_pass and provenance["pass"] else [
            name for name, passed in checks.items() if not passed
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = run_validation()
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if results["dynamic_baseline_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
