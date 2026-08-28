# -*- coding: utf-8 -*-
"""运行W3-V1同provider/issuance PID基线与两种MPC闭环比较。"""

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "4_4"
WIND_DATA_ROOT = MODEL_ROOT / "wind_data"
for path in (MODEL_ROOT, WIND_DATA_ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import model_wind
from aggregate_w3_v1 import LOWER_IS_BETTER, aggregate
from metrics_source import resolve_preview_forecast, valve_equilibrium_from_state
from model_schema import STATE_INDEX, solver_absolute_tolerances
from mpc_utils_out import MPCController, get_linear_model
from prepare_w3_horizon import prepare as prepare_horizons
from run_e2_smoke import prepare_operating_point
from w3_forecast_provider import W2IssueTimeForecastProvider
from w3_pid_feedforward import PREVIEW_LEAD_S, PreviewPIDFeedforwardController


DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w3_v1_typical_validation.json"
)


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _conflicting_formal_processes():
    try:
        import psutil
    except ImportError as error:
        raise RuntimeError("Formal W3 resource gate requires psutil") from error
    matches = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command_parts = process.info.get("cmdline") or []
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        if not command_parts or not Path(command_parts[0]).name.lower().startswith("python"):
            continue
        command = " ".join(command_parts).lower()
        if (
            "run_e2" in command
            or "e2_" in command
            or "run_e3_formal" in command
            or "run_e3_parallel" in command
        ):
            matches.append({"pid": int(process.info["pid"]), "command": command})
    return matches


def validate_config_contract(config):
    if not math.isclose(
        float(config["pid_forecast_ff"]["preview_lead_seconds"]),
        PREVIEW_LEAD_S,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("Configured PID preview lead differs from the implementation")
    if not math.isclose(
        float(config["forecast"]["issue_interval_seconds"]),
        W2IssueTimeForecastProvider.ISSUANCE_INTERVAL_SECONDS,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("Configured forecast issuance interval differs from the provider")
    if int(config["mpc"]["decision_nodes"]) != 30 or not math.isclose(
        float(config["mpc"]["prediction_span_seconds"]),
        21600.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("W3-V1 must retain the frozen 30-decision/6 h MPC budget")


def preflight_output_directory(output_dir):
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            "W3 output directory is not empty; refusing a partial or overwrite run: {}".format(
                output_dir
            )
        )


def load_reference(config):
    dataset = config["dataset"]
    scenario = config["scenario"]
    frame = pd.read_csv(PROJECT_ROOT / dataset["path"], usecols=[
        dataset["timestamp_column"],
        dataset["wind_output_column"],
        dataset["split_column"],
    ])
    frame[dataset["timestamp_column"]] = pd.to_datetime(
        frame[dataset["timestamp_column"]], errors="raise"
    )
    start = pd.Timestamp(scenario["start"])
    end = pd.Timestamp(scenario["end"])
    selected = frame[
        (frame[dataset["timestamp_column"]] >= start)
        & (frame[dataset["timestamp_column"]] <= end)
    ].copy()
    if selected.empty:
        raise ValueError("W3 scenario window contains no wind records")
    if set(selected[dataset["split_column"]]) != {dataset["allowed_split"]}:
        raise ValueError("W3 scenario must remain inside the frozen validation split")
    expected_times = pd.date_range(
        start, end, freq=pd.to_timedelta(float(dataset["sample_seconds"]), unit="s")
    )
    if not selected[dataset["timestamp_column"]].reset_index(drop=True).equals(
        pd.Series(expected_times)
    ):
        raise ValueError("W3 scenario records must be complete and equally spaced")
    wind = selected[dataset["wind_output_column"]].to_numpy(dtype=float)
    if not np.all(np.isfinite(wind)):
        raise ValueError("W3 scenario wind values must be finite")
    elapsed = (
        selected[dataset["timestamp_column"]] - start
    ).dt.total_seconds().to_numpy(dtype=float)
    initial_wind = float(wind[0])
    scale = float(scenario["wind_capacity_mw"]) / float(scenario["system_base_mw"])
    base_power = float(scenario["operating_power_pu"])

    def wind_at(simulation_time_s):
        value = float(simulation_time_s)
        if not 0.0 <= value <= float(elapsed[-1]):
            raise ValueError("Actual W3 reference request is outside the scenario window")
        return float(np.interp(value, elapsed, wind))

    def target_at(simulation_time_s):
        return float(base_power - scale * (wind_at(simulation_time_s) - initial_wind))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_seconds": float(elapsed[-1]),
        "initial_wind_pu": initial_wind,
        "wind_scale": scale,
        "wind_at": wind_at,
        "target_at": target_at,
    }


class NuclearReferenceForecastAdapter:
    """把W2风功率预测映射为同一系统基准下的核功率参考。"""

    def __init__(self, wind_provider, base_power, wind_scale, initial_wind):
        self.wind_provider = wind_provider
        self.base_power = float(base_power)
        self.wind_scale = float(wind_scale)
        self.initial_wind = float(initial_wind)
        self.selected_issue_times = set()

    def __call__(self, **request):
        evidence = self.wind_provider.forecast_with_metadata(**request)
        self.selected_issue_times.add(evidence["selected_issue_time"])
        wind_forecast = np.asarray(evidence["forecast_output_pu"], dtype=float)
        return self.base_power - self.wind_scale * (wind_forecast - self.initial_wind)


def _empty_signals(steps):
    names = (
        "rod_speed_spm",
        "valve_command_pu",
        "valve_actual_pu",
        "bess_power_mw",
        "frequency_deviation_hz",
        "p_e_pu",
    )
    return {name: np.zeros(steps + 1, dtype=float) for name in names}


def _record(signals, index, simulation_time_s, state, params, initial_conditions, valve):
    observation = model_wind.observe_model(
        simulation_time_s,
        state,
        params,
        initial_conditions,
        disturbance_case=0,
        control_mode="pid",
        u_tg_ext=float(valve),
    )
    for name in signals:
        signals[name][index] = float(observation[name])


def _integrate_step(state, start_time_s, dt, valve, params, initial_conditions):
    solution = solve_ivp(
        lambda current_time, current_state: model_wind.pwf_model(
            current_time,
            current_state,
            params,
            initial_conditions,
            disturbance_case=0,
            control_mode="pid",
            u_tg_ext=float(valve),
            p_grid_disturbance_pu=0.0,
        ),
        (float(start_time_s), float(start_time_s + dt)),
        np.asarray(state, dtype=float),
        method="Radau",
        rtol=1e-6,
        atol=solver_absolute_tolerances(1e-8),
    )
    if not solution.success:
        raise RuntimeError(
            "W3 integration failed at t={}: {}".format(start_time_s, solution.message)
        )
    next_state = np.asarray(solution.y[:, -1], dtype=float)
    next_state[STATE_INDEX["SOC"]] = np.clip(
        next_state[STATE_INDEX["SOC"]], 0.0, 1.0
    )
    return next_state


def _build_mpc(config, horizon_intervals, params, initial_conditions, state):
    dt = float(config["simulation"]["dt_seconds"])
    valve_equilibrium = valve_equilibrium_from_state(state, initial_conditions)
    ad, bd = get_linear_model(
        params, initial_conditions, state, valve_equilibrium, dt=dt
    )
    x_total = float(params["X_d_prime"] + params["X_line"])
    pe_gain = float(params["E_prime"] * params["V_inf"] / x_total)
    mpc = config["mpc"]
    controller = MPCController(
        ad,
        bd,
        state,
        valve_equilibrium,
        dt,
        n=len(horizon_intervals),
        q_power=float(mpc["q_power"]),
        q_temperature=float(mpc["q_temperature"]),
        move_weight=float(mpc["move_weight"]),
        magnitude_weight=float(mpc["magnitude_weight"]),
        pe_gain=pe_gain,
        integral_weight=float(mpc["integral_weight"]),
        integral_error_limit=mpc["integral_error_limit"],
        use_reference_trajectory=True,
        valve_rate_limit_pu_s=float(params.get("valve_rate_limit_pu_s", 0.05)),
        prediction_interval_steps=horizon_intervals,
    )
    return controller, valve_equilibrium


def simulate_controller(
    controller_name,
    config,
    reference,
    forecast_provider,
    horizon_report,
    duration_seconds,
):
    dt = float(config["simulation"]["dt_seconds"])
    steps = int(round(float(duration_seconds) / dt))
    if not math.isclose(steps * dt, float(duration_seconds), rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("W3 duration must be an integer number of control steps")
    params, initial_conditions, state, observation = prepare_operating_point(
        float(config["scenario"]["operating_power_pu"]),
        float(config["scenario"]["bess_soc"]),
    )
    params = copy.deepcopy(params)
    initial_conditions = copy.deepcopy(initial_conditions)
    if not math.isclose(
        float(params.get("valve_rate_limit_pu_s", float("nan"))),
        float(config["pid_forecast_ff"]["valve_rate_limit_pu_s"]),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("Configured PID valve-rate limit differs from the plant model")
    time_grid = np.linspace(0.0, float(duration_seconds), steps + 1)
    states = np.zeros((state.size, steps + 1), dtype=float)
    states[:, 0] = state
    targets = np.asarray([reference["target_at"](value) for value in time_grid])
    signals = _empty_signals(steps)
    command_times = []
    solver_status = []
    solver_names = []
    solver_fallbacks = []

    if controller_name == "pid_forecast_ff":
        valve = valve_equilibrium_from_state(state, initial_conditions)
        controller = PreviewPIDFeedforwardController(valve, dt)
        intervals = None
    elif controller_name in ("mpc_uniform_tail", "mpc_nonuniform"):
        key = "uniform_tail" if controller_name == "mpc_uniform_tail" else "nonuniform"
        intervals = horizon_report[key]["interval_steps"]
        controller, valve = _build_mpc(
            config, intervals, params, initial_conditions, state
        )
        node_offsets = dt * np.cumsum(np.asarray(intervals, dtype=int))
    else:
        raise ValueError("Unsupported W3 controller: {}".format(controller_name))

    for index in range(steps):
        current_time = float(time_grid[index])
        target_now = float(targets[index])
        start_command = time.perf_counter()
        if controller_name == "pid_forecast_ff":
            valve = controller.compute_command(
                issue_time_s=current_time,
                state=state,
                params=params,
                initial_conditions=initial_conditions,
                current_target_power_abs=target_now,
                forecast_type=config["forecast"]["type"],
                forecast_provider=forecast_provider,
            )
        else:
            forecast_target = resolve_preview_forecast(
                issue_time_s=current_time,
                node_times_s=current_time + node_offsets,
                current_target_power_abs=target_now,
                forecast_type=config["forecast"]["type"],
                forecast_provider=forecast_provider,
            )
            valve = controller.solve(
                state,
                forecast_target,
                valve,
                current_target_power_abs=target_now,
            )
            solver_status.append(controller.last_status)
            solver_names.append(controller.last_solver)
            solver_fallbacks.append(bool(controller.last_fallback_used))
        command_times.append(time.perf_counter() - start_command)
        _record(
            signals,
            index,
            current_time,
            state,
            params,
            initial_conditions,
            valve,
        )
        state = _integrate_step(
            state, current_time, dt, valve, params, initial_conditions
        )
        states[:, index + 1] = state

    _record(
        signals,
        -1,
        float(time_grid[-1]),
        state,
        params,
        initial_conditions,
        valve,
    )
    return {
        "controller": controller_name,
        "t": time_grid,
        "states": states,
        "target_pu": targets,
        "signals": signals,
        "command_times": np.asarray(command_times, dtype=float),
        "solver_status": solver_status,
        "solver_names": solver_names,
        "solver_fallbacks": solver_fallbacks,
        "operating_electrical_power_pu": float(observation["p_e_pu"]),
        "decision_nodes": None if intervals is None else len(intervals),
        "prediction_span_seconds": (
            None if intervals is None else float(dt * sum(intervals))
        ),
    }


def summarize_result(result, config, adapter, stage):
    dt = float(config["simulation"]["dt_seconds"])
    target = result["target_pu"]
    output = result["signals"]["p_e_pu"]
    error = output - target
    valve = result["signals"]["valve_command_pu"]
    bess = result["signals"]["bess_power_mw"]
    states = result["states"]
    coolant = 0.5 * (
        states[STATE_INDEX["T_c1"], :] + states[STATE_INDEX["T_c2"], :]
    )
    command_times = result["command_times"]
    deadline = float(config["metrics"]["solve_deadline_seconds"])
    metrics = {
        "tracking_rmse_pu": float(np.sqrt(np.mean(error ** 2))),
        "tracking_mae_pu": float(np.mean(np.abs(error))),
        "tracking_max_abs_error_pu": float(np.max(np.abs(error))),
        "frequency_max_abs_hz": float(
            np.max(np.abs(result["signals"]["frequency_deviation_hz"]))
        ),
        "coolant_average_max_abs_deviation_c": float(
            np.max(np.abs(coolant - coolant[0]))
        ),
        "rod_peak_abs_spm": float(
            np.max(np.abs(result["signals"]["rod_speed_spm"]))
        ),
        "valve_command_max_rate_pu_s": float(
            np.max(np.abs(np.diff(valve))) / dt
        ),
        "valve_command_total_variation_pu": float(np.sum(np.abs(np.diff(valve)))),
        "bess_peak_abs_mw": float(np.max(np.abs(bess))),
        "bess_throughput_mwh": float(np.sum(np.abs(bess[:-1])) * dt / 3600.0),
        "soc_min": float(np.min(states[STATE_INDEX["SOC"], :])),
        "soc_max": float(np.max(states[STATE_INDEX["SOC"], :])),
        "solve_mean_seconds": float(np.mean(command_times)),
        "solve_p95_seconds": float(np.quantile(command_times, 0.95)),
        "solve_max_seconds": float(np.max(command_times)),
        "deadline_miss_fraction": float(np.mean(command_times > deadline)),
    }
    if not all(math.isfinite(value) for value in metrics.values()):
        raise RuntimeError("W3 case produced non-finite metrics")
    is_mpc = result["controller"].startswith("mpc_")
    record = {
        "study_id": config["study_id"],
        "stage": stage,
        "controller": result["controller"],
        "scenario_id": config["scenario"]["label"],
        "split": config["dataset"]["allowed_split"],
        "forecast_type": config["forecast"]["type"],
        "control_steps": int(result["command_times"].size),
        "forecast_issue_times": sorted(adapter.selected_issue_times),
        "decision_nodes": result["decision_nodes"],
        "prediction_span_seconds": result["prediction_span_seconds"],
        "solver": config["mpc"]["solver"] if is_mpc else None,
        "solver_status_counts": {
            status: result["solver_status"].count(status)
            for status in sorted(set(result["solver_status"]))
        },
        "solver_fallback_count": int(sum(result["solver_fallbacks"])),
        "metrics": metrics,
        "locked_splits_accessed": [],
    }
    if is_mpc and (
        any(name != config["mpc"]["solver"] for name in result["solver_names"])
        or record["solver_fallback_count"] != 0
    ):
        raise RuntimeError("W3 formal MPC used an unfrozen solver path")
    return record


def save_case(output_dir, result, record):
    cases = output_dir / "cases"
    cases.mkdir(parents=True, exist_ok=True)
    stem = record["controller"]
    json_path = cases / (stem + ".json")
    npz_path = cases / (stem + ".npz")
    if json_path.exists() or npz_path.exists():
        raise FileExistsError("W3 case output already exists: {}".format(stem))
    np.savez_compressed(
        npz_path,
        t=result["t"],
        target_pu=result["target_pu"],
        states=result["states"],
        command_times=result["command_times"],
        **result["signals"],
    )
    record = dict(record)
    record["npz"] = str(npz_path)
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--stage", choices=("smoke", "formal"), required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--controller", action="append")
    args = parser.parse_args()

    config = _load_json(args.config.resolve())
    validate_config_contract(config)
    if args.stage == "formal":
        conflicting = _conflicting_formal_processes()
        if conflicting:
            raise RuntimeError(
                "W3-V1 formal launch blocked by E2/E3 formal processes: {}".format(
                    [item["pid"] for item in conflicting]
                )
            )
    horizon_config = PROJECT_ROOT / config["simulation"]["forecast_horizon_config"]
    horizon_report = prepare_horizons(horizon_config)
    reference = load_reference(config)
    duration = (
        float(config["simulation"]["smoke_duration_seconds"])
        if args.stage == "smoke"
        else float(config["simulation"]["formal_duration_seconds"])
    )
    if duration > reference["duration_seconds"]:
        raise ValueError("W3 requested duration exceeds the frozen scenario")
    output_dir = args.output_dir
    if output_dir is None:
        key = "smoke_directory" if args.stage == "smoke" else "formal_directory"
        output_dir = PROJECT_ROOT / config["outputs"][key]
    output_dir = output_dir.resolve()
    preflight_output_directory(output_dir)

    selected = args.controller or list(config["simulation"]["formal_controllers"])
    if len(selected) != len(set(selected)) or not set(selected).issubset(
        set(config["simulation"]["formal_controllers"])
    ):
        raise ValueError("W3 controller selection is invalid or duplicated")
    records = []
    for controller_name in selected:
        wind_provider = W2IssueTimeForecastProvider(
            scenario_start_timestamp=config["scenario"]["start"],
            forecast_csv_path=PROJECT_ROOT / config["forecast"]["w2_forecast_table"],
            interface_config_path=PROJECT_ROOT / config["forecast"]["w2_interface"],
            split=config["dataset"]["allowed_split"],
        )
        adapter = NuclearReferenceForecastAdapter(
            wind_provider,
            float(config["scenario"]["operating_power_pu"]),
            reference["wind_scale"],
            reference["initial_wind_pu"],
        )
        result = simulate_controller(
            controller_name,
            config,
            reference,
            adapter,
            horizon_report,
            duration,
        )
        record = summarize_result(
            result,
            config,
            adapter,
            "W3-R0" if args.stage == "smoke" else "W3-V1",
        )
        records.append(save_case(output_dir, result, record))

    if set(selected) == set(config["simulation"]["formal_controllers"]):
        report = aggregate(config, records)
        report["scope"] = (
            "2 s integration smoke; no performance claim"
            if args.stage == "smoke"
            else config["evidence_class"]
        )
        (output_dir / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        if not report["pass"]:
            raise SystemExit(1)
    else:
        print(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
