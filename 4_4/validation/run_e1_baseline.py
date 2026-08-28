# -*- coding: utf-8 -*-
"""运行E1确定性基线，并保存配置、原始序列和摘要指标。"""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import cvxpy
import numpy as np
import scipy

MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

from metrics_source import (
    build_y0,
    plot_and_save_compare,
    run_mpc_scenario,
    run_pid_scenario,
    save_npz_result,
    settle_initial_state,
)
from parameters import get_params


DEFAULT_DURATIONS = {
    "Step": 240.0,
    "Ramp": 300.0,
    "Trapezoid": 360.0,
    "NoiseTrapezoid": 400.0,
}


def _total_variation(signal):
    return float(np.sum(np.abs(np.diff(np.asarray(signal, dtype=float)))))


def _result_hash(result):
    digest = hashlib.sha256()
    for key in (
        "t", "P_e", "P_n", "T_c_avg", "Target_abs", "rod_speed_spm",
        "valve_command_pu", "valve_actual_pu", "bess_power_mw",
        "frequency_deviation_hz", "Y",
    ):
        array = np.ascontiguousarray(np.asarray(result[key]))
        digest.update(key.encode("utf-8"))
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
        digest.update(array.tobytes())
    return digest.hexdigest()


def _summarize(result):
    t = np.asarray(result["t"], dtype=float)
    pe = np.asarray(result["P_e"], dtype=float)
    target = np.asarray(result["Target_abs"], dtype=float)
    temperature = np.asarray(result["T_c_avg"], dtype=float)
    frequency_hz = np.asarray(result["frequency_deviation_hz"], dtype=float)
    bess_power = np.asarray(result["bess_power_mw"], dtype=float)
    soc = np.asarray(result["Y"][-2, :], dtype=float)
    rod = np.asarray(result["rod_speed_spm"], dtype=float)
    valve_command = np.asarray(result["valve_command_pu"], dtype=float)
    valve_actual = np.asarray(result["valve_actual_pu"], dtype=float)
    dt = float(result["DT"])
    solver_status = [str(item) for item in result.get("solver_status", [])]
    solver_failures = sum(
        status not in ("optimal", "optimal_inaccurate") for status in solver_status
    )
    all_arrays = (
        pe, target, temperature, frequency_hz, bess_power, soc, rod,
        valve_command, valve_actual,
    )
    metrics = {
        "tracking_rmse_pu": float(np.sqrt(np.mean((pe - target) ** 2))),
        "tracking_mae_pu": float(np.mean(np.abs(pe - target))),
        "tracking_max_abs_error_pu": float(np.max(np.abs(pe - target))),
        "temperature_max_abs_dev_c": float(np.max(np.abs(temperature - result["T_ref"]))),
        "frequency_max_abs_hz": float(np.max(np.abs(frequency_hz))),
        "frequency_rms_hz": float(np.sqrt(np.mean(frequency_hz ** 2))),
        "soc_min": float(np.min(soc)),
        "soc_max": float(np.max(soc)),
        "soc_final": float(soc[-1]),
        "bess_peak_mw": float(np.max(np.abs(bess_power))),
        "bess_throughput_mwh": float(np.trapz(np.abs(bess_power), t) / 3600.0),
        "rod_peak_spm": float(np.max(np.abs(rod))),
        "rod_total_variation_spm": _total_variation(rod),
        "valve_command_total_variation_pu": _total_variation(valve_command),
        "valve_actual_total_variation_pu": _total_variation(valve_actual),
        "valve_command_max_rate_pu_s": float(np.max(np.abs(np.diff(valve_command))) / dt),
        "valve_actual_max_rate_pu_s": float(np.max(np.abs(np.diff(valve_actual))) / dt),
        "simulation_seconds": float(result["sim_time"]),
        "solver_failures": int(solver_failures),
        "finite_outputs": bool(all(np.all(np.isfinite(array)) for array in all_arrays)),
        "rod_limit_respected": bool(np.max(np.abs(rod)) <= 72.0 + 1e-8),
        "valve_limit_respected": bool(
            np.min(valve_command) >= -1e-8 and np.max(valve_command) <= 1.2 + 1e-8
        ),
        "soc_limit_respected": bool(np.min(soc) >= 0.1 - 1e-8 and np.max(soc) <= 0.9 + 1e-8),
        "result_sha256": _result_hash(result),
    }
    metrics["numerical_pass"] = bool(
        metrics["finite_outputs"]
        and metrics["solver_failures"] == 0
        and metrics["rod_limit_respected"]
        and metrics["valve_limit_respected"]
        and metrics["soc_limit_respected"]
    )
    return metrics


def run(args):
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = args.t_end if args.t_end is not None else DEFAULT_DURATIONS[args.scenario]

    params_mpc, ic_mpc = get_params()
    params_mpc["rod_control_deadband_c"] = args.rod_deadband
    params_mpc["Kp_temp"] = args.rod_kp
    params_mpc["Ki_temp"] = args.rod_ki
    initial_mpc = settle_initial_state(params_mpc, ic_mpc, build_y0(params_mpc, ic_mpc))
    result_mpc = run_mpc_scenario(
        args.scenario,
        params_mpc,
        ic_mpc,
        initial_mpc,
        args.dt,
        duration,
        n=args.horizon,
        q_weights={"power": args.q_power, "Tavg": args.q_temperature},
        r_weights={"move": args.move_weight, "magnitude": args.magnitude_weight},
        use_noise=args.use_noise,
        seed=args.seed,
    )

    params_pid, ic_pid = get_params()
    params_pid["rod_control_deadband_c"] = args.rod_deadband
    params_pid["Kp_temp"] = args.rod_kp
    params_pid["Ki_temp"] = args.rod_ki
    initial_pid = settle_initial_state(params_pid, ic_pid, build_y0(params_pid, ic_pid))
    result_pid = run_pid_scenario(
        args.scenario,
        params_pid,
        ic_pid,
        initial_pid,
        args.dt,
        duration,
        use_noise=args.use_noise,
        seed=args.seed,
    )

    suffix = "_noise" if args.use_noise else ""
    stem = f"{args.scenario.lower()}{suffix}"
    mpc_path = output_dir / f"{stem}_mpc.npz"
    pid_path = output_dir / f"{stem}_pid.npz"
    figure_path = output_dir / f"{stem}_comparison.png"
    summary_path = output_dir / f"{stem}_summary.json"
    save_npz_result(result_mpc, mpc_path)
    save_npz_result(result_pid, pid_path)
    plot_and_save_compare(result_mpc, result_pid, figure_path)

    summary = {
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "cvxpy": cvxpy.__version__,
        },
        "configuration": {
            "scenario": args.scenario,
            "dt_s": args.dt,
            "horizon_steps": args.horizon,
            "prediction_horizon_s": args.dt * args.horizon,
            "duration_s": duration,
            "use_noise": args.use_noise,
            "seed": args.seed,
            "q_power": args.q_power,
            "q_temperature": args.q_temperature,
            "move_weight": args.move_weight,
            "magnitude_weight": args.magnitude_weight,
            "rod_control_deadband_c": args.rod_deadband,
            "rod_kp": args.rod_kp,
            "rod_ki": args.rod_ki,
            "pid_valve_rate_limit_pu_s": 0.05,
        },
        "artifacts": {
            "mpc_npz": str(mpc_path),
            "pid_npz": str(pid_path),
            "comparison_png": str(figure_path),
        },
        "mpc": _summarize(result_mpc),
        "pid": _summarize(result_pid),
    }
    summary["run_pass"] = bool(
        summary["mpc"]["numerical_pass"] and summary["pid"]["numerical_pass"]
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["run_pass"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=tuple(DEFAULT_DURATIONS), required=True)
    parser.add_argument("--dt", type=float, default=0.5)
    parser.add_argument("--horizon", type=int, default=30)
    parser.add_argument("--t-end", type=float)
    parser.add_argument("--use-noise", action="store_true")
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--q-power", type=float, default=1000.0)
    parser.add_argument("--q-temperature", type=float, default=0.5)
    parser.add_argument("--move-weight", type=float, default=2.0)
    parser.add_argument("--magnitude-weight", type=float, default=0.05)
    parser.add_argument("--rod-deadband", type=float, default=0.3)
    parser.add_argument("--rod-kp", type=float, default=30.0)
    parser.add_argument("--rod-ki", type=float, default=0.1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.dt <= 0.0 or args.horizon <= 0:
        parser.error("dt and horizon must be positive")
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
