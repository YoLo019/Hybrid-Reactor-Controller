# -*- coding: utf-8 -*-
"""固定控制器参数，比较MPC与PID在多个扰动频率下的闭环表现。"""

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from metrics_source import run_mpc_scenario, run_pid_scenario
from run_e2_smoke import (
    canonical_hash,
    make_e2_input_functions,
    prepare_operating_point,
    save_case_npz,
    sha256,
    summarize_case,
)


def validate_config(config):
    frequencies = config["frequencies"]
    if not frequencies:
        raise ValueError("frequencies must not be empty")
    values = [float(item["frequency_hz"]) for item in frequencies]
    if any(value <= 0.0 for value in values):
        raise ValueError("frequency_hz must be positive")
    if len(values) != len(set(values)):
        raise ValueError("frequency_hz values must be unique")
    if any(float(item["cycles"]) < 1.0 for item in frequencies):
        raise ValueError("each frequency requires at least one analysis cycle")
    if config["input_definition"]["kind"] not in {
        "net_load_reference",
        "grid_power_disturbance",
    }:
        raise ValueError("unsupported input_definition kind")
    if float(config["system_scaling"]["model_system_base_mw"]) <= 0.0:
        raise ValueError("model_system_base_mw must be positive")
    recovery = config["simulation"]["recovery"]
    if float(recovery["duration_s"]) <= 0.0 or float(recovery["sustain_s"]) <= 0.0:
        raise ValueError("recovery duration and sustain window must be positive")


def frequency_metrics(npz_path, frequency_hz, warmup_s, cycles):
    data = np.load(npz_path)
    time = np.asarray(data["t"], dtype=float)
    forcing_end_s = float(warmup_s) + float(cycles) / float(frequency_hz)
    mask = (time >= warmup_s) & (time < forcing_end_s)
    elapsed = time[mask] - warmup_s
    regressors = np.column_stack(
        [
            np.ones(mask.sum()),
            np.sin(2.0 * np.pi * frequency_hz * elapsed),
            np.cos(2.0 * np.pi * frequency_hz * elapsed),
        ]
    )

    def fit(values):
        coefficients = np.linalg.lstsq(regressors, np.asarray(values)[mask], rcond=None)[0]
        amplitude = float(np.hypot(coefficients[1], coefficients[2]))
        phase = float(np.arctan2(coefficients[2], coefficients[1]))
        return amplitude, phase

    target_amplitude, target_phase = fit(data["Target_abs"])
    input_amplitude, input_phase = fit(data["input_signal_pu"])
    output_amplitude, output_phase = fit(data["Pe"])
    if target_amplitude > 1e-12:
        denominator_amplitude = target_amplitude
        denominator_phase = target_phase
        gain_basis = "reference"
    elif input_amplitude > 1e-12:
        denominator_amplitude = input_amplitude
        denominator_phase = input_phase
        gain_basis = "grid_disturbance"
    else:
        denominator_amplitude = None
        denominator_phase = None
        gain_basis = "undefined_zero_input"
    phase_error = (
        None
        if denominator_phase is None
        else np.angle(np.exp(1j * (output_phase - denominator_phase)))
    )
    target = np.asarray(data["Target_abs"], dtype=float)[mask]
    output = np.asarray(data["Pe"], dtype=float)[mask]
    valve = np.asarray(data["valve_command_pu"], dtype=float)[mask]
    dt = float(np.asarray(data["DT"]).reshape(-1)[0])
    temperature = np.asarray(data["Tc_avg"], dtype=float)
    warmup_temperature = temperature[time < warmup_s]
    reference_temperature = float(np.mean(warmup_temperature[-10:]))
    return {
        "tracking_rmse_pu": float(np.sqrt(np.mean((output - target) ** 2))),
        "gain": (
            None
            if denominator_amplitude is None
            else float(output_amplitude / denominator_amplitude)
        ),
        "gain_basis": gain_basis,
        "phase_error_deg": (
            None if phase_error is None else float(np.degrees(phase_error))
        ),
        "frequency_max_abs_hz": float(
            np.max(np.abs(np.asarray(data["frequency_deviation_hz"], dtype=float)[mask]))
        ),
        "coolant_average_max_abs_deviation_c": float(
            np.max(np.abs(temperature[mask] - reference_temperature))
        ),
        "rod_peak_abs_spm": float(
            np.max(np.abs(np.asarray(data["rod_speed_spm"], dtype=float)[mask]))
        ),
        "valve_command_max_rate_pu_s": float(np.max(np.abs(np.diff(valve))) / dt),
        "valve_command_total_variation_pu": float(np.sum(np.abs(np.diff(valve)))),
        "bess_peak_abs_mw": float(
            np.max(np.abs(np.asarray(data["bess_power_mw"], dtype=float)[mask]))
        ),
        "soc_min": float(np.min(np.asarray(data["SOC"], dtype=float)[mask])),
        "soc_max": float(np.max(np.asarray(data["SOC"], dtype=float)[mask])),
    }


def build_case_config(controller, frequency_spec, config, runner_hash, metrics_hash):
    """构造决定数值结果的完整缓存身份。"""
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    amplitude = float(config["amplitude_pu"])
    frequency_hz = float(frequency_spec["frequency_hz"])
    warmup_s = float(config["simulation"]["warmup_s"])
    forcing_end_s = warmup_s + float(frequency_spec["cycles"]) / frequency_hz
    duration_s = forcing_end_s + float(
        config["simulation"]["recovery"]["duration_s"]
    )
    dt_s = float(config["simulation"]["dt_s"])
    return {
        "study_id": config["study_id"],
        "controller": controller,
        "frequency_hz": frequency_hz,
        "cycles": float(frequency_spec["cycles"]),
        "evidence_class": frequency_spec["evidence_class"],
        "amplitude_pu": amplitude,
        "phase_rad": float(config["phase_rad"]),
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "power_pu": base_power,
        "soc": float(config["operating_point"]["bess_soc"]),
        "warmup_s": warmup_s,
        "forcing_end_s": forcing_end_s,
        "duration_s": duration_s,
        "recovery": config["simulation"]["recovery"],
        "dt_s": dt_s,
        "mpc": config["mpc"],
        "constraints": config["constraints"],
        "runner_sha256": runner_hash,
        "metrics_source_sha256": metrics_hash,
    }


def run_case(
    controller,
    frequency_spec,
    config,
    params,
    initial_conditions,
    operating_state,
    output_dir,
    runner_hash,
    metrics_hash,
):
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    amplitude = float(config["amplitude_pu"])
    frequency_hz = float(frequency_spec["frequency_hz"])
    warmup_s = float(config["simulation"]["warmup_s"])
    forcing_end_s = warmup_s + float(frequency_spec["cycles"]) / frequency_hz
    duration_s = forcing_end_s + float(
        config["simulation"]["recovery"]["duration_s"]
    )
    dt_s = float(config["simulation"]["dt_s"])
    case_config = build_case_config(
        controller, frequency_spec, config, runner_hash, metrics_hash
    )
    case_hash = canonical_hash(case_config)
    stem = f"{controller.lower()}_f{frequency_hz:.8f}_{case_hash[:12].lower()}"
    npz_path = output_dir / "cases" / f"{stem}.npz"
    json_path = output_dir / "cases" / f"{stem}.json"
    if json_path.is_file() and npz_path.is_file():
        cached = json.loads(json_path.read_text(encoding="utf-8"))
        if cached.get("case_hash") == case_hash:
            return cached

    target_function, grid_disturbance_function, input_function, forcing_end_s = (
        make_e2_input_functions(
            base_power,
            amplitude,
            frequency_hz,
            float(config["phase_rad"]),
            warmup_s,
            float(frequency_spec["cycles"]),
            str(config["input_definition"]["kind"]),
        )
    )
    common = {
        "scenario_name": "FrequencyDiagnostic",
        "params": copy.deepcopy(params),
        "ic": copy.deepcopy(initial_conditions),
        "y0": operating_state.copy(),
        "dt": dt_s,
        "t_end": duration_s,
        "target_function": target_function,
        "grid_disturbance_function": grid_disturbance_function,
        "scenario_title": "E2 fixed-parameter frequency diagnostic",
        "scenario_tag": stem,
        "show_progress": False,
    }
    if controller == "MPC":
        mpc = config["mpc"]
        result = run_mpc_scenario(
            **common,
            n=int(mpc["horizon_steps"]),
            q_weights={
                "power": float(mpc["q_power"]),
                "Tavg": float(mpc["q_temperature"]),
            },
            r_weights={
                "move": float(mpc["move_weight"]),
                "magnitude": float(mpc["magnitude_weight"]),
            },
            use_reference_preview=bool(mpc.get("use_reference_preview", False)),
            forecast_type=(
                str(mpc.get("forecast_type", "perfect_foresight"))
                if bool(mpc.get("use_reference_preview", False))
                else None
            ),
            integral_weight=float(mpc.get("integral_weight", 0.0)),
            integral_error_limit=mpc.get("integral_error_limit"),
        )
    elif controller == "PID":
        result = run_pid_scenario(**common)
    else:
        raise ValueError(f"unsupported controller: {controller}")

    input_signal = np.asarray(
        [input_function(value) for value in result["t"]], dtype=float
    )
    save_case_npz(npz_path, result, input_signal)
    record = {
        "case_hash": case_hash,
        "case_config": case_config,
        "npz": str(npz_path),
        "constraints": summarize_case(
            result,
            config["constraints"],
            input_signal,
            forcing_end_s,
            config["simulation"]["recovery"],
        ),
        "analysis_metrics": frequency_metrics(
            npz_path,
            frequency_hz,
            warmup_s,
            float(frequency_spec["cycles"]),
        ),
    }
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)

    runner_path = Path(__file__).resolve()
    metrics_path = MODEL_ROOT / "metrics_source.py"
    runner_hash = sha256(runner_path)
    metrics_hash = sha256(metrics_path)
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    soc = float(config["operating_point"]["bess_soc"])
    params, initial_conditions, operating_state, observation = prepare_operating_point(
        base_power, soc
    )

    cases = []
    for frequency_spec in config["frequencies"]:
        controller_cases = {
            controller: run_case(
                controller,
                frequency_spec,
                config,
                params,
                initial_conditions,
                operating_state,
                output_dir,
                runner_hash,
                metrics_hash,
            )
            for controller in ("MPC", "PID")
        }
        mpc_metrics = controller_cases["MPC"]["analysis_metrics"]
        pid_metrics = controller_cases["PID"]["analysis_metrics"]
        cases.append(
            {
                "frequency_hz": float(frequency_spec["frequency_hz"]),
                "cycles": float(frequency_spec["cycles"]),
                "evidence_class": frequency_spec["evidence_class"],
                "MPC": controller_cases["MPC"],
                "PID": controller_cases["PID"],
                "comparison": {
                    "tracking_rmse_mpc_over_pid": float(
                        mpc_metrics["tracking_rmse_pu"] / pid_metrics["tracking_rmse_pu"]
                    ),
                    "frequency_peak_mpc_over_pid": float(
                        mpc_metrics["frequency_max_abs_hz"]
                        / pid_metrics["frequency_max_abs_hz"]
                    ),
                    "valve_rate_mpc_over_pid": float(
                        mpc_metrics["valve_command_max_rate_pu_s"]
                        / pid_metrics["valve_command_max_rate_pu_s"]
                    ),
                },
            }
        )

    report = {
        "study_id": config["study_id"],
        "scope": (
            "fixed-parameter controller diagnosis; each frequency keeps its configured evidence "
            "class, and data-calibrated exposure levels remain separate from engineering stress amplitudes"
        ),
        "provenance": {
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "runner_sha256": runner_hash,
            "metrics_source_sha256": metrics_hash,
        },
        "operating_point": {
            "requested_power_pu": base_power,
            "electrical_power_pu": float(observation["p_e_pu"]),
            "soc": soc,
        },
        "cases": cases,
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    report_path = output_dir / "frequency_diagnostic_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
