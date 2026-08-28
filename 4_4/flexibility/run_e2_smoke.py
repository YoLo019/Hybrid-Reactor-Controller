# -*- coding: utf-8 -*-
"""执行E2单工况正弦柔性域冒烟测试并缓存确定性数值结果。"""

import argparse
import copy
import hashlib
import json
import platform
import sys
from pathlib import Path

import cvxpy
import numpy as np
import scipy
from scipy.integrate import solve_ivp


MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

import model_wind
from metrics_source import (
    build_y0,
    project_detector_equilibrium,
    run_mpc_scenario,
    run_pid_scenario,
    settle_initial_state,
)
from model_schema import STATE_INDEX, solver_absolute_tolerances
from parameters import get_params


NUMERICAL_TOLERANCE = 1e-8


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def canonical_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def mathematical_result_hash(result, input_signal):
    digest = hashlib.sha256()
    arrays = {
        "t": result["t"],
        "Y": result["Y"],
        "P_e": result["P_e"],
        "P_n": result["P_n"],
        "T_c_avg": result["T_c_avg"],
        "Target_abs": result["Target_abs"],
        "rod_speed_spm": result["rod_speed_spm"],
        "valve_command_pu": result["valve_command_pu"],
        "valve_actual_pu": result["valve_actual_pu"],
        "bess_power_mw": result["bess_power_mw"],
        "frequency_deviation_hz": result["frequency_deviation_hz"],
        "input_signal_pu": input_signal,
        "grid_disturbance_pu": result["grid_disturbance_pu"],
    }
    for name, values in arrays.items():
        array = np.ascontiguousarray(np.asarray(values))
        digest.update(name.encode("utf-8"))
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
        digest.update(array.tobytes())
    return digest.hexdigest().upper()


def prepare_operating_point(base_power, soc):
    params, initial_conditions = get_params()
    params["rod_control_deadband_c"] = 0.3
    params["Kp_temp"] = 30.0
    params["Ki_temp"] = 0.1
    initial_conditions["SOC0"] = float(soc)
    full_power = settle_initial_state(
        params,
        initial_conditions,
        build_y0(params, initial_conditions),
    )
    params["P_load_ref"] = float(base_power)
    transition = solve_ivp(
        lambda current_t, current_y: model_wind.pwf_model(
            current_t,
            current_y,
            params,
            initial_conditions,
            disturbance_case=0,
            control_mode="pid",
        ),
        (0.0, 1800.0),
        full_power,
        method="Radau",
        rtol=1e-7,
        atol=solver_absolute_tolerances(1e-9),
    )
    if not transition.success:
        raise RuntimeError(f"90% operating-point transition failed: {transition.message}")
    state = project_detector_equilibrium(transition.y[:, -1], params)
    state[STATE_INDEX["SOC"]] = float(soc)
    state[STATE_INDEX["e_int_freq"]] = 0.0
    observation = model_wind.observe_model(0.0, state, params, initial_conditions)
    if abs(observation["p_e_pu"] - base_power) >= 5e-4:
        raise RuntimeError(
            f"Operating point is not at the requested power: {observation['p_e_pu']:.9f}"
        )
    if abs(observation["frequency_pu"] - 1.0) >= 1e-4:
        raise RuntimeError("Operating-point frequency is not recovered.")
    return params, initial_conditions, state, observation


def make_e2_input_functions(
    base_power,
    amplitude,
    frequency_hz,
    phase_rad,
    warmup_s,
    cycles,
    input_kind,
):
    """构造参考域或物理扰动域输入，并在受迫周期后归零。"""
    if input_kind not in {"net_load_reference", "grid_power_disturbance"}:
        raise ValueError(f"unsupported E2 input kind: {input_kind}")
    forcing_end_s = float(warmup_s) + float(cycles) / float(frequency_hz)

    def input_signal(current_time):
        if current_time < warmup_s or current_time >= forcing_end_s:
            return 0.0
        elapsed = current_time - warmup_s
        return float(
            amplitude
            * np.sin(2.0 * np.pi * frequency_hz * elapsed + phase_rad)
        )

    def target_profile(current_time):
        if input_kind == "net_load_reference":
            return float((base_power - input_signal(current_time)) / base_power)
        return 1.0

    def grid_disturbance(current_time):
        if input_kind == "grid_power_disturbance":
            return input_signal(current_time)
        return 0.0

    return target_profile, grid_disturbance, input_signal, forcing_end_s


def margin_series(result, constraints):
    dt = float(result["DT"])
    target = np.asarray(result["Target_abs"], dtype=float)
    frequency = np.asarray(result["frequency_deviation_hz"], dtype=float)
    temperature = np.asarray(result["T_c_avg"], dtype=float)
    neutron = np.asarray(result["P_n"], dtype=float)
    rod = np.asarray(result["rod_speed_spm"], dtype=float)
    valve = np.asarray(result["valve_command_pu"], dtype=float)
    valve_rate = np.r_[0.0, np.abs(np.diff(valve)) / dt]
    bess = np.asarray(result["bess_power_mw"], dtype=float)
    soc = np.asarray(result["Y"][STATE_INDEX["SOC"], :], dtype=float)

    def two_sided(values, spec):
        return np.minimum(values - float(spec["lower"]), float(spec["upper"]) - values)

    margins = {
        "finite_outputs": np.where(
            np.isfinite(target)
            & np.isfinite(frequency)
            & np.isfinite(temperature)
            & np.isfinite(neutron)
            & np.isfinite(rod)
            & np.isfinite(valve)
            & np.isfinite(bess)
            & np.isfinite(soc),
            1.0,
            -1.0,
        ),
        "frequency_abs_hz": float(constraints["frequency_abs_hz"]["upper"]) - np.abs(frequency),
        "coolant_average_abs_deviation_c": (
            float(constraints["coolant_average_abs_deviation_c"]["upper"])
            - np.abs(temperature - float(result["T_ref"]))
        ),
        "neutron_power_pu": two_sided(neutron, constraints["neutron_power_pu"]),
        "rod_speed_abs_spm": float(constraints["rod_speed_abs_spm"]["upper"]) - np.abs(rod),
        "valve_command_pu": two_sided(valve, constraints["valve_command_pu"]),
        "valve_command_rate_abs_pu_s": (
            float(constraints["valve_command_rate_abs_pu_s"]["upper"]) - valve_rate
        ),
        "bess_power_abs_mw": float(constraints["bess_power_abs_mw"]["upper"]) - np.abs(bess),
        "soc": two_sided(soc, constraints["soc"]),
    }
    # 正式柔性域中目标轨迹是外部扰动坐标，不是plant/device安全状态。
    # 冒烟测试仍可显式提供该项，用于检查场景生成器的研究范围。
    if "net_load_target_pu" in constraints:
        margins["net_load_target_pu"] = two_sided(
            target, constraints["net_load_target_pu"]
        )
    return margins


def evaluate_constraints(result, constraints):
    margins = margin_series(result, constraints)
    times = np.asarray(result["t"], dtype=float)
    scales = {
        "finite_outputs": 1.0,
        "frequency_abs_hz": constraints["frequency_abs_hz"]["upper"],
        "coolant_average_abs_deviation_c": constraints["coolant_average_abs_deviation_c"]["upper"],
        "neutron_power_pu": constraints["neutron_power_pu"]["upper"] - constraints["neutron_power_pu"]["lower"],
        "rod_speed_abs_spm": constraints["rod_speed_abs_spm"]["upper"],
        "valve_command_pu": constraints["valve_command_pu"]["upper"] - constraints["valve_command_pu"]["lower"],
        "valve_command_rate_abs_pu_s": constraints["valve_command_rate_abs_pu_s"]["upper"],
        "bess_power_abs_mw": constraints["bess_power_abs_mw"]["upper"],
        "soc": constraints["soc"]["upper"] - constraints["soc"]["lower"],
    }
    if "net_load_target_pu" in constraints:
        scales["net_load_target_pu"] = (
            constraints["net_load_target_pu"]["upper"]
            - constraints["net_load_target_pu"]["lower"]
        )
    records = {}
    first_violations = []
    for name, values in margins.items():
        values = np.asarray(values, dtype=float)
        minimum_index = int(np.argmin(values))
        violation_indices = np.flatnonzero(values < -NUMERICAL_TOLERANCE)
        first_index = int(violation_indices[0]) if len(violation_indices) else None
        if first_index is not None:
            first_violations.append((float(times[first_index]), name))
        records[name] = {
            "pass": first_index is None,
            "minimum_margin": float(values[minimum_index]),
            "normalized_minimum_margin": float(values[minimum_index] / float(scales[name])),
            "minimum_margin_time_s": float(times[minimum_index]),
            "first_violation_time_s": None if first_index is None else float(times[first_index]),
        }
    first_violations.sort()
    safe = all(record["pass"] for record in records.values())
    if first_violations:
        active_constraint = first_violations[0][1]
        first_violation_time = first_violations[0][0]
    else:
        active_constraint = min(
            records,
            key=lambda name: records[name]["normalized_minimum_margin"],
        )
        first_violation_time = None
    return {
        "safe": bool(safe),
        "active_constraint": active_constraint,
        "first_violation_time_s": first_violation_time,
        "constraints": records,
    }


def save_case_npz(path, result, input_signal):
    np.savez_compressed(
        path,
        t=np.asarray(result["t"]),
        Y=np.asarray(result["Y"]),
        Pe=np.asarray(result["P_e"]),
        Pn=np.asarray(result["P_n"]),
        Tc_avg=np.asarray(result["T_c_avg"]),
        Target_abs=np.asarray(result["Target_abs"]),
        input_signal_pu=np.asarray(input_signal),
        grid_disturbance_pu=np.asarray(result["grid_disturbance_pu"]),
        rod_speed_spm=np.asarray(result["rod_speed_spm"]),
        valve_command_pu=np.asarray(result["valve_command_pu"]),
        valve_actual_pu=np.asarray(result["valve_actual_pu"]),
        bess_power_mw=np.asarray(result["bess_power_mw"]),
        frequency_deviation_hz=np.asarray(result["frequency_deviation_hz"]),
        SOC=np.asarray(result["Y"][STATE_INDEX["SOC"], :]),
        DT=np.asarray([result["DT"]]),
    )


def assess_recovery(result, forcing_end_s, recovery):
    """检查恢复段末尾是否持续回到冻结邻域。"""
    times = np.asarray(result["t"], dtype=float)
    sustain_s = float(recovery["sustain_s"])
    window_start = max(float(forcing_end_s), float(times[-1]) - sustain_s)
    mask = times >= window_start
    target_final = float(np.asarray(result["Target_abs"], dtype=float)[-1])
    checks = {
        "power_abs_error_pu": float(
            np.max(np.abs(np.asarray(result["P_e"], dtype=float)[mask] - target_final))
        ),
        "frequency_abs_hz": float(
            np.max(np.abs(np.asarray(result["frequency_deviation_hz"], dtype=float)[mask]))
        ),
        "coolant_average_abs_deviation_c": float(
            np.max(
                np.abs(
                    np.asarray(result["T_c_avg"], dtype=float)[mask]
                    - float(result["T_ref"])
                )
            )
        ),
        "bess_power_abs_mw": float(
            np.max(np.abs(np.asarray(result["bess_power_mw"], dtype=float)[mask]))
        ),
    }
    limits = recovery["completion_limits"]
    return {
        "complete": bool(
            all(checks[name] <= float(limits[name]) for name in checks)
        ),
        "window_start_s": float(window_start),
        "window_end_s": float(times[-1]),
        "observed": checks,
        "limits": {name: float(limits[name]) for name in checks},
    }


def summarize_case(result, constraints, input_signal, forcing_end_s, recovery):
    assessment = evaluate_constraints(result, constraints)
    valve = np.asarray(result["valve_command_pu"], dtype=float)
    summary = {
        **assessment,
        "metrics": {
            "tracking_rmse_pu": float(np.sqrt(np.mean((result["P_e"] - result["Target_abs"]) ** 2))),
            "frequency_max_abs_hz": float(np.max(np.abs(result["frequency_deviation_hz"]))),
            "coolant_average_max_abs_deviation_c": float(
                np.max(np.abs(result["T_c_avg"] - result["T_ref"]))
            ),
            "neutron_power_min_pu": float(np.min(result["P_n"])),
            "neutron_power_max_pu": float(np.max(result["P_n"])),
            "rod_peak_abs_spm": float(np.max(np.abs(result["rod_speed_spm"]))),
            "valve_command_min_pu": float(np.min(valve)),
            "valve_command_max_pu": float(np.max(valve)),
            "valve_command_max_rate_pu_s": float(np.max(np.abs(np.diff(valve))) / result["DT"]),
            "bess_peak_abs_mw": float(np.max(np.abs(result["bess_power_mw"]))),
            "soc_min": float(np.min(result["Y"][STATE_INDEX["SOC"], :])),
            "soc_max": float(np.max(result["Y"][STATE_INDEX["SOC"], :])),
            "solver_failures": int(sum(
                str(status) not in ("optimal", "optimal_inaccurate")
                for status in result.get("solver_status", [])
            )),
        },
        "recovery": assess_recovery(result, forcing_end_s, recovery),
        "mathematical_result_sha256": mathematical_result_hash(result, input_signal),
    }
    summary["valid_for_boundary"] = bool(
        summary["safe"] and summary["recovery"]["complete"]
    )
    if not summary["safe"] and summary["first_violation_time_s"] is not None:
        summary["first_violation_phase"] = (
            "forcing"
            if float(summary["first_violation_time_s"]) < float(forcing_end_s)
            else "recovery"
        )
    else:
        summary["first_violation_phase"] = None
    return summary


def run_case(controller_name, amplitude, context):
    config = context["config"]
    scenario = config["scenario"]
    simulation = config["simulation"]
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    frequency_hz = float(scenario["frequency_hz"])
    forcing_end_s = (
        float(scenario["warmup_s"]) + float(scenario["cycles"]) / frequency_hz
    )
    duration = forcing_end_s + float(simulation["recovery"]["duration_s"])
    case_config = {
        "study_id": config["study_id"],
        "controller": controller_name,
        "amplitude_pu": float(amplitude),
        "frequency_hz": frequency_hz,
        "phase_rad": float(scenario["phase_rad"]),
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "warmup_s": float(scenario["warmup_s"]),
        "forcing_end_s": forcing_end_s,
        "duration_s": duration,
        "recovery": simulation["recovery"],
        "dt_s": float(simulation["dt_s"]),
        "mpc_horizon_steps": int(simulation["mpc_horizon_steps"]),
        "constraints": config["constraints"],
        "runner_sha256": context["runner_sha256"],
        "metrics_source_sha256": context["metrics_source_sha256"],
    }
    case_hash = canonical_hash(case_config)
    stem = f"{controller_name.lower()}_a{amplitude:.8f}_{case_hash[:12].lower()}"
    summary_path = context["cases_dir"] / f"{stem}.json"
    npz_path = context["cases_dir"] / f"{stem}.npz"
    if summary_path.is_file() and npz_path.is_file():
        cached = json.loads(summary_path.read_text(encoding="utf-8"))
        if cached.get("case_hash") == case_hash:
            return cached

    params = copy.deepcopy(context["params"])
    initial_conditions = copy.deepcopy(context["initial_conditions"])
    operating_state = np.asarray(context["operating_state"], dtype=float).copy()
    target_function, grid_disturbance_function, input_function, forcing_end_s = (
        make_e2_input_functions(
            base_power,
            float(amplitude),
            frequency_hz,
            float(scenario["phase_rad"]),
            float(scenario["warmup_s"]),
            float(scenario["cycles"]),
            str(config["input_definition"]["kind"]),
        )
    )
    common = {
        "scenario_name": "SineEquivalentNetLoad",
        "params": params,
        "ic": initial_conditions,
        "y0": operating_state,
        "dt": float(simulation["dt_s"]),
        "t_end": duration,
        "target_function": target_function,
        "grid_disturbance_function": grid_disturbance_function,
        "scenario_title": "E2 sine equivalent-net-load smoke",
        "scenario_tag": "E2SineSmoke",
        "show_progress": False,
    }
    if controller_name == "MPC":
        result = run_mpc_scenario(
            **common,
            n=int(simulation["mpc_horizon_steps"]),
            q_weights={"power": simulation["q_power"], "Tavg": simulation["q_temperature"]},
            r_weights={"move": simulation["move_weight"], "magnitude": simulation["magnitude_weight"]},
        )
    elif controller_name == "PID":
        result = run_pid_scenario(**common, enforce_valve_rate_limit=True)
    else:
        raise ValueError(f"Unsupported controller: {controller_name}")

    input_signal = np.asarray(
        [input_function(value) for value in result["t"]], dtype=float
    )
    summary = {
        "case_hash": case_hash,
        "case_config": case_config,
        "controller": controller_name,
        "amplitude_pu": float(amplitude),
        "npz": str(npz_path),
        **summarize_case(
            result,
            config["constraints"],
            input_signal,
            forcing_end_s,
            simulation["recovery"],
        ),
    }
    save_case_npz(npz_path, result, input_signal)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def search_first_failure(controller_name, context):
    scenario = context["config"]["scenario"]
    low = float(scenario["safe_bracket_amplitude_pu"])
    high = float(scenario["failure_bracket_amplitude_pu"])
    evaluations = []

    low_result = run_case(controller_name, low, context)
    evaluations.append(low_result)
    high_result = run_case(controller_name, high, context)
    evaluations.append(high_result)
    if not low_result["safe"]:
        return {
            "status": "baseline_unsafe",
            "safe_amplitude_pu": None,
            "first_failed_amplitude_pu": low,
            "evaluations": evaluations,
        }
    if high_result["safe"]:
        return {
            "status": "failure_not_bracketed",
            "safe_amplitude_pu": high,
            "first_failed_amplitude_pu": None,
            "evaluations": evaluations,
        }

    for _ in range(int(scenario["bisection_iterations"])):
        middle = 0.5 * (low + high)
        result = run_case(controller_name, middle, context)
        evaluations.append(result)
        if result["safe"]:
            low = middle
        else:
            high = middle
            high_result = result
    return {
        "status": "bracketed",
        "safe_amplitude_pu": low,
        "first_failed_amplitude_pu": high,
        "bracket_width_pu": high - low,
        "first_failure_constraint": high_result["active_constraint"],
        "evaluations": evaluations,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runner_path = Path(__file__).resolve()
    metrics_source_path = MODEL_ROOT / "metrics_source.py"
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    soc = float(config["operating_point"]["bess_soc"])
    params, initial_conditions, operating_state, observation = prepare_operating_point(base_power, soc)
    context = {
        "config": config,
        "params": params,
        "initial_conditions": initial_conditions,
        "operating_state": operating_state,
        "cases_dir": cases_dir,
        "runner_sha256": sha256(runner_path),
        "metrics_source_sha256": sha256(metrics_source_path),
    }

    nominal = {}
    boundaries = {}
    for controller_name in ("MPC", "PID"):
        nominal[controller_name] = run_case(
            controller_name,
            float(config["scenario"]["nominal_amplitude_pu"]),
            context,
        )
        boundaries[controller_name] = search_first_failure(controller_name, context)

    run_pass = all(
        nominal[name]["metrics"]["solver_failures"] == 0
        and boundaries[name]["status"] == "bracketed"
        for name in ("MPC", "PID")
    )
    summary = {
        "study_id": config["study_id"],
        "run_pass": bool(run_pass),
        "scope": "program-path smoke test only; not a formal flexibility-domain result",
        "disturbance_semantics": config["disturbance_semantics"],
        "evidence_class": config["evidence_class"],
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "cvxpy": cvxpy.__version__,
        },
        "provenance": {
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "runner_sha256": context["runner_sha256"],
            "metrics_source_sha256": context["metrics_source_sha256"],
        },
        "operating_point": {
            "requested_power_pu": base_power,
            "electrical_power_pu": float(observation["p_e_pu"]),
            "frequency_pu": float(observation["frequency_pu"]),
            "soc": float(operating_state[STATE_INDEX["SOC"]]),
        },
        "nominal_cases": nominal,
        "first_failure_search": boundaries,
    }
    summary_path = output_dir / "e2_smoke_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if run_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
