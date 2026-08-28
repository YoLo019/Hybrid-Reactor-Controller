# -*- coding: utf-8 -*-
"""聚合W3-V1三控制器结果并执行公平性硬门。"""

import argparse
import json
import math
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w3_v1_typical_validation.json"
)


LOWER_IS_BETTER = (
    "tracking_rmse_pu",
    "tracking_mae_pu",
    "tracking_max_abs_error_pu",
    "frequency_max_abs_hz",
    "coolant_average_max_abs_deviation_c",
    "rod_peak_abs_spm",
    "valve_command_max_rate_pu_s",
    "valve_command_total_variation_pu",
    "bess_peak_abs_mw",
    "bess_throughput_mwh",
    "solve_mean_seconds",
    "solve_p95_seconds",
    "solve_max_seconds",
    "deadline_miss_fraction",
)


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _finite_metrics(record, required_metrics):
    metrics = record.get("metrics", {})
    return all(
        key in metrics
        and isinstance(metrics[key], (int, float))
        and math.isfinite(float(metrics[key]))
        for key in required_metrics
    )


def _relative_change(candidate, baseline):
    if baseline == 0.0:
        return None
    return float(candidate / baseline - 1.0)


def aggregate(config, records):
    expected = list(config["simulation"]["formal_controllers"])
    by_controller = {record.get("controller"): record for record in records}
    duplicate_free = len(by_controller) == len(records)
    complete = duplicate_free and set(by_controller) == set(expected)
    required_metrics = tuple(LOWER_IS_BETTER) + ("soc_min", "soc_max")

    comparable_records = [by_controller[name] for name in expected if name in by_controller]
    record_stages = {record.get("stage") for record in comparable_records}
    common_stage = len(record_stages) == 1 and next(iter(record_stages), None) in {
        "W3-R0",
        "W3-V1",
    }
    stage = next(iter(record_stages)) if common_stage else "invalid"
    duration_key = (
        "smoke_duration_seconds" if stage == "W3-R0" else "formal_duration_seconds"
    )
    expected_control_steps = (
        int(round(float(config["simulation"][duration_key]) / float(config["simulation"]["dt_seconds"])))
        if common_stage
        else None
    )
    control_steps_match_stage = complete and common_stage and all(
        record.get("control_steps") == expected_control_steps
        for record in comparable_records
    )
    common_study = {record.get("study_id") for record in comparable_records} == {
        config["study_id"]
    }
    common_scenario = {record.get("scenario_id") for record in comparable_records} == {
        config["scenario"]["label"]
    }
    common_split = {record.get("split") for record in comparable_records} == {
        config["dataset"]["allowed_split"]
    }
    common_forecast = {record.get("forecast_type") for record in comparable_records} == {
        config["forecast"]["type"]
    }
    issue_time_sets = {
        tuple(record.get("forecast_issue_times", []))
        for record in comparable_records
    }
    common_issue_times = bool(comparable_records) and all(
        record.get("forecast_issue_times") for record in comparable_records
    ) and len(issue_time_sets) == 1
    metrics_complete = complete and all(
        _finite_metrics(record, required_metrics) for record in comparable_records
    )

    mpc_records = [
        by_controller[name]
        for name in ("mpc_uniform_tail", "mpc_nonuniform")
        if name in by_controller
    ]
    expected_budget = (
        int(config["mpc"]["decision_nodes"]),
        float(config["mpc"]["prediction_span_seconds"]),
    )
    equal_mpc_budget = len(mpc_records) == 2 and all(
        (
            record.get("decision_nodes"),
            record.get("prediction_span_seconds"),
        ) == expected_budget
        for record in mpc_records
    )
    mpc_solver_frozen = len(mpc_records) == 2 and all(
        record.get("solver") == config["mpc"]["solver"]
        and int(record.get("solver_fallback_count", -1)) == 0
        and bool(record.get("solver_status_counts"))
        and set(record["solver_status_counts"]) == {"optimal"}
        and int(record["solver_status_counts"]["optimal"]) == expected_control_steps
        for record in mpc_records
    ) if expected_control_steps is not None else False
    checks = {
        "complete_unique_controller_set": complete,
        "common_configured_study": common_study,
        "common_stage_and_expected_control_steps": control_steps_match_stage,
        "common_scenario": common_scenario,
        "validation_split_only": common_split,
        "common_forecast_type": common_forecast,
        "common_issue_time_information": common_issue_times,
        "all_required_metrics_finite": metrics_complete,
        "equal_mpc_decision_and_span_budget": equal_mpc_budget,
        "mpc_uses_frozen_solver_without_fallback": mpc_solver_frozen,
    }

    comparisons = {}
    if metrics_complete:
        uniform = by_controller["mpc_uniform_tail"]["metrics"]
        nonuniform = by_controller["mpc_nonuniform"]["metrics"]
        pid = by_controller["pid_forecast_ff"]["metrics"]
        comparisons = {
            metric: {
                "nonuniform_vs_uniform_relative": _relative_change(
                    float(nonuniform[metric]), float(uniform[metric])
                ),
                "nonuniform_vs_pid_relative": _relative_change(
                    float(nonuniform[metric]), float(pid[metric])
                ),
            }
            for metric in LOWER_IS_BETTER
        }
    return {
        "study_id": config["study_id"],
        "stage": stage,
        "pass": all(checks.values()),
        "checks": checks,
        "controllers": by_controller,
        "comparisons": comparisons,
        "claim_supported": "not_evaluated_by_aggregator",
        "claim_rule": config["success_gate"]["nonuniform_performance"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = _load_json(args.config.resolve())
    run_dir = args.run_dir.resolve()
    records = [
        _load_json(run_dir / "cases" / f"{controller}.json")
        for controller in config["simulation"]["formal_controllers"]
    ]
    report = aggregate(config, records)
    output = args.output.resolve() if args.output else run_dir / "summary.json"
    if output.exists():
        raise FileExistsError("W3 aggregate output already exists: {}".format(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
