# -*- coding: utf-8 -*-
"""在真实44状态线性化对象上执行W3可变时域单次QP冒烟。"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "4_4"
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from metrics_source import resolve_preview_forecast
from mpc_utils_out import MPCController, get_linear_model
from model_schema import STATE_INDEX
from prepare_w3_horizon import DEFAULT_CONFIG, prepare
from run_e2_smoke import prepare_operating_point


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _build_and_solve(
    label, intervals, config, params, initial_conditions, state, forecast_provider
):
    base_dt = float(config["budget"]["base_control_step_seconds"])
    valve_equilibrium = float(state[STATE_INDEX["C_tg"]]) / float(initial_conditions["C_tg0"])
    start_build = time.perf_counter()
    ad, bd = get_linear_model(
        params, initial_conditions, state, valve_equilibrium, dt=base_dt
    )
    x_total = float(params["X_d_prime"] + params["X_line"])
    pe_gain = float(params["E_prime"] * params["V_inf"] / x_total)
    controller = MPCController(
        ad,
        bd,
        state,
        valve_equilibrium,
        base_dt,
        n=len(intervals),
        q_power=1000.0,
        q_temperature=0.5,
        move_weight=2.0,
        magnitude_weight=0.05,
        pe_gain=pe_gain,
        use_reference_trajectory=True,
        valve_rate_limit_pu_s=float(params.get("valve_rate_limit_pu_s", 0.05)),
        prediction_interval_steps=intervals,
    )
    build_seconds = time.perf_counter() - start_build
    current_power = pe_gain * np.sin(float(state[STATE_INDEX["delta"]]))
    node_times_s = base_dt * np.cumsum(intervals)
    target = resolve_preview_forecast(
        issue_time_s=0.0,
        node_times_s=node_times_s,
        current_target_power_abs=current_power,
        forecast_type="persistence",
        forecast_provider=forecast_provider,
    )
    solve_seconds = []
    commands = []
    previous = valve_equilibrium
    statuses = []
    solvers = []
    fallbacks = []
    primary_exceptions = []
    for _ in range(int(config["smoke"]["repeated_solves"])):
        start_solve = time.perf_counter()
        command = controller.solve(
            state,
            target,
            previous,
            current_target_power_abs=current_power,
        )
        solve_seconds.append(time.perf_counter() - start_solve)
        commands.append(float(command))
        statuses.append(controller.last_status)
        solvers.append(controller.last_solver)
        fallbacks.append(controller.last_fallback_used)
        primary_exceptions.append(controller.last_primary_exception)
        previous = command
    return {
        "label": label,
        "decision_nodes": len(intervals),
        "prediction_span_seconds": float(base_dt * sum(intervals)),
        "build_seconds": float(build_seconds),
        "solve_seconds": [float(value) for value in solve_seconds],
        "mean_solve_seconds": float(np.mean(solve_seconds)),
        "commands_pu": commands,
        "solver_status": statuses,
        "solver": solvers,
        "solver_fallback_used": fallbacks,
        "primary_solver_exceptions": primary_exceptions,
        "all_commands_finite": bool(np.all(np.isfinite(commands))),
        "all_solves_accepted": all(value in ("optimal", "optimal_inaccurate") for value in statuses),
        "all_solves_used_frozen_osqp": all(value == "OSQP" for value in solvers),
        "no_solver_fallback": not any(fallbacks),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = _load_json(args.config.resolve())
    preparation = prepare(args.config)
    params, initial_conditions, state, _ = prepare_operating_point(
        float(config["smoke"]["operating_power_pu"]),
        float(config["smoke"]["bess_soc"]),
    )
    target_delta = float(config["smoke"]["target_delta_pu"])

    def persistence_provider(**request):
        return np.full(
            len(request["target_times_s"]),
            float(request["issue_value_pu"]) + target_delta,
            dtype=float,
        )

    cases = [
        _build_and_solve(
            "nonuniform",
            preparation["nonuniform"]["interval_steps"],
            config,
            params,
            initial_conditions,
            state,
            persistence_provider,
        ),
        _build_and_solve(
            "uniform_tail",
            preparation["uniform_tail"]["interval_steps"],
            config,
            params,
            initial_conditions,
            state,
            persistence_provider,
        ),
    ]
    checks = {
        "preparation_gate_passed": preparation["pass"],
        "both_controllers_built_with_equal_budget": (
            cases[0]["decision_nodes"] == cases[1]["decision_nodes"]
            and cases[0]["prediction_span_seconds"] == cases[1]["prediction_span_seconds"]
        ),
        "all_commands_finite": all(case["all_commands_finite"] for case in cases),
        "all_solves_accepted": all(case["all_solves_accepted"] for case in cases),
        "all_solves_used_frozen_osqp": all(
            case["all_solves_used_frozen_osqp"] for case in cases
        ),
        "no_solver_fallback": all(case["no_solver_fallback"] for case in cases),
        "build_time_within_smoke_budget": all(
            case["build_seconds"] <= float(config["smoke"]["max_build_seconds_per_controller"])
            for case in cases
        ),
        "mean_solve_time_within_smoke_budget": all(
            case["mean_solve_seconds"] <= float(config["smoke"]["max_mean_solve_seconds"])
            for case in cases
        ),
    }
    report = {
        "study_id": config["study_id"],
        "stage": "W3-S0",
        "scope": "structural QP smoke on the real 44-state linearization; not closed-loop performance evidence",
        "pass": all(checks.values()),
        "checks": checks,
        "cases": cases,
        "locked_splits_accessed": [],
    }
    output = args.output
    if output is None:
        output = PROJECT_ROOT / config["outputs"]["smoke_directory"] / "summary.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
