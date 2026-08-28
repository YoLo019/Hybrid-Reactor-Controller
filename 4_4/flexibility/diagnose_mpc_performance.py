# -*- coding: utf-8 -*-
"""比较少量预注册MPC配置，诊断单频场景下的幅值衰减。"""

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

from metrics_source import run_mpc_scenario
from model_schema import STATE_INDEX
from run_e2_smoke import make_target_function, prepare_operating_point, save_case_npz, summarize_case


CANDIDATES = [
    {
        "id": "corrected_equilibrium_h30",
        "horizon_steps": 30,
        "q_power": 1000.0,
        "q_temperature": 0.5,
        "move_weight": 2.0,
        "magnitude_weight": 0.05,
    },
    {
        "id": "higher_tracking_weight_h30",
        "horizon_steps": 30,
        "q_power": 10000.0,
        "q_temperature": 0.5,
        "move_weight": 2.0,
        "magnitude_weight": 0.05,
    },
    {
        "id": "longer_horizon_h60",
        "horizon_steps": 60,
        "q_power": 1000.0,
        "q_temperature": 0.5,
        "move_weight": 2.0,
        "magnitude_weight": 0.05,
    },
    {
        "id": "longer_horizon_low_move_h60",
        "horizon_steps": 60,
        "q_power": 3000.0,
        "q_temperature": 0.5,
        "move_weight": 0.5,
        "magnitude_weight": 0.01,
    },
]


def frequency_response(npz_path, frequency_hz, warmup_s):
    data = np.load(npz_path)
    time = np.asarray(data["t"], dtype=float)
    mask = time >= warmup_s
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
    output_amplitude, output_phase = fit(data["Pe"])
    phase_error = np.angle(np.exp(1j * (output_phase - target_phase)))
    return {
        "gain": output_amplitude / target_amplitude,
        "phase_error_deg": float(np.degrees(phase_error)),
        "target_amplitude_pu": target_amplitude,
        "output_amplitude_pu": output_amplitude,
    }


def summarize_npz(npz_path, frequency_hz, warmup_s):
    data = np.load(npz_path)
    time = np.asarray(data["t"], dtype=float)
    target = np.asarray(data["Target_abs"], dtype=float)
    output = np.asarray(data["Pe"], dtype=float)
    valve = np.asarray(data["valve_command_pu"], dtype=float)
    dt = float(np.asarray(data["DT"]).reshape(-1)[0])
    return {
        "tracking_rmse_pu": float(np.sqrt(np.mean((output - target) ** 2))),
        "frequency_max_abs_hz": float(np.max(np.abs(data["frequency_deviation_hz"]))),
        "coolant_average_max_abs_deviation_c": float(np.max(np.abs(data["Tc_avg"] - data["Tc_avg"][0]))),
        "rod_peak_abs_spm": float(np.max(np.abs(data["rod_speed_spm"]))),
        "valve_command_max_rate_pu_s": float(np.max(np.abs(np.diff(valve))) / dt),
        "valve_command_total_variation_pu": float(np.sum(np.abs(np.diff(valve)))),
        "bess_peak_abs_mw": float(np.max(np.abs(data["bess_power_mw"]))),
        "soc_min": float(np.min(data["SOC"])),
        "soc_max": float(np.max(data["SOC"])),
        **frequency_response(npz_path, frequency_hz, warmup_s),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate", action="append", choices=[item["id"] for item in CANDIDATES])
    args = parser.parse_args()

    config = json.loads(args.config.resolve().read_text(encoding="utf-8"))
    reference = json.loads(args.reference_summary.resolve().read_text(encoding="utf-8"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base_power = float(config["operating_point"]["nuclear_power_pu"])
    soc = float(config["operating_point"]["bess_soc"])
    scenario = config["scenario"]
    simulation = config["simulation"]
    amplitude = float(scenario["nominal_amplitude_pu"])
    frequency_hz = float(scenario["frequency_hz"])
    warmup_s = float(scenario["warmup_s"])
    duration_s = warmup_s + float(scenario["cycles"]) / frequency_hz
    params, initial_conditions, operating_state, _ = prepare_operating_point(base_power, soc)
    target_function = make_target_function(
        base_power,
        amplitude,
        frequency_hz,
        float(scenario["phase_rad"]),
        warmup_s,
    )

    results = {
        "baseline_mpc": summarize_npz(
            Path(reference["nominal_cases"]["MPC"]["npz"]), frequency_hz, warmup_s
        ),
        "baseline_pid": summarize_npz(
            Path(reference["nominal_cases"]["PID"]["npz"]), frequency_hz, warmup_s
        ),
    }
    selected = set(args.candidate or [item["id"] for item in CANDIDATES])
    for candidate in (item for item in CANDIDATES if item["id"] in selected):
        npz_path = output_dir / f"{candidate['id']}.npz"
        json_path = output_dir / f"{candidate['id']}.json"
        if not npz_path.is_file():
            result = run_mpc_scenario(
                "SineEquivalentNetLoad",
                copy.deepcopy(params),
                copy.deepcopy(initial_conditions),
                operating_state.copy(),
                float(simulation["dt_s"]),
                duration_s,
                n=int(candidate["horizon_steps"]),
                q_weights={"power": candidate["q_power"], "Tavg": candidate["q_temperature"]},
                r_weights={"move": candidate["move_weight"], "magnitude": candidate["magnitude_weight"]},
                target_function=target_function,
                scenario_title="E2 MPC performance diagnostic",
                scenario_tag=candidate["id"],
                show_progress=False,
            )
            wind_effect = base_power - np.asarray(result["Target_abs"], dtype=float)
            save_case_npz(npz_path, result, wind_effect)
            assessment = summarize_case(result, config["constraints"], wind_effect)
            json_path.write_text(
                json.dumps({"candidate": candidate, "assessment": assessment}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        results[candidate["id"]] = {
            "configuration": candidate,
            **summarize_npz(npz_path, frequency_hz, warmup_s),
        }

    baseline_pid_rmse = results["baseline_pid"]["tracking_rmse_pu"]
    for name, values in results.items():
        values["rmse_better_than_baseline_pid"] = bool(
            values["tracking_rmse_pu"] < baseline_pid_rmse
        )
    report = {
        "scope": "single-case diagnosis; not controller selection or paper evidence",
        "scenario": {
            "power_pu": base_power,
            "soc": soc,
            "amplitude_pu": amplitude,
            "frequency_hz": frequency_hz,
            "duration_s": duration_s,
        },
        "results": results,
    }
    report_path = output_dir / "mpc_diagnostic_summary.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
