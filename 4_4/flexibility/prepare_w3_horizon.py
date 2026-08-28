# -*- coding: utf-8 -*-
"""从W1/W2冻结证据生成W3非均匀与公平均匀预测节点。"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w3_nonuniform_horizon_v1.json"
)


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _divide_integer_span(total_steps, count):
    if total_steps < count or count <= 0:
        raise ValueError("Each prediction interval requires at least one base step")
    quotient, remainder = divmod(int(total_steps), int(count))
    return [quotient] * (count - remainder) + [quotient + 1] * remainder


def build_nonuniform_intervals(config):
    budget = config["budget"]
    design = config["nonuniform_design"]
    base_dt = float(budget["base_control_step_seconds"])
    segment_ends = [int(round(float(value) / base_dt)) for value in design["segment_end_seconds"]]
    counts = [int(value) for value in design["decision_nodes_per_segment"]]
    if len(segment_ends) != len(counts):
        raise ValueError("Segment ends and node allocations must have equal length")

    intervals = []
    previous_end = 0
    for index, (segment_end, count) in enumerate(zip(segment_ends, counts)):
        segment_steps = segment_end - previous_end
        if index == 0:
            first = int(design["first_interval_steps"])
            if count < 2 or first >= segment_steps:
                raise ValueError("First W3 segment must retain one control step and a tail")
            segment_intervals = [first] + _divide_integer_span(
                segment_steps - first, count - 1
            )
        else:
            segment_intervals = _divide_integer_span(segment_steps, count)
        intervals.extend(segment_intervals)
        previous_end = segment_end
    return intervals


def build_uniform_tail_intervals(config):
    budget = config["budget"]
    first = int(config["uniform_comparator"]["first_interval_steps"])
    total_steps = int(
        round(
            float(budget["prediction_span_seconds"])
            / float(budget["base_control_step_seconds"])
        )
    )
    node_count = int(budget["decision_nodes"])
    return [first] + _divide_integer_span(total_steps - first, node_count - 1)


def _read_persistence_rmse(metrics_path, expected_horizon_steps):
    with Path(metrics_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = {}
    for row in rows:
        if row["stage"] != "validation" or row["forecast_type"] != "persistence":
            continue
        horizon = int(row["horizon_steps"])
        if horizon in selected:
            raise ValueError(f"Duplicate persistence metric for horizon {horizon}")
        value = float(row["rmse_pu"])
        if not np.isfinite(value):
            raise ValueError(f"Non-finite persistence RMSE for horizon {horizon}")
        selected[horizon] = value
    expected = [int(value) for value in expected_horizon_steps]
    if set(selected) != set(expected):
        raise ValueError(
            f"Persistence metric horizons {sorted(selected)} do not match {sorted(expected)}"
        )
    return [selected[horizon] for horizon in expected]


def prepare(config_path):
    config_path = Path(config_path).resolve()
    config = _load_json(config_path)
    evidence = config["evidence"]
    w1 = _load_json(PROJECT_ROOT / evidence["w1_feature_report"])
    w2_interface = _load_json(PROJECT_ROOT / evidence["w2_f1_interface"])
    w2_summary = _load_json(PROJECT_ROOT / evidence["w2_validation_summary"])
    expected_horizon_steps = w2_interface["forecast_interface"]["horizon_steps"]
    persistence_rmse = _read_persistence_rmse(
        PROJECT_ROOT / evidence["w2_validation_metrics"], expected_horizon_steps
    )

    nonuniform = build_nonuniform_intervals(config)
    uniform = build_uniform_tail_intervals(config)
    base_dt = float(config["budget"]["base_control_step_seconds"])
    span_steps = int(round(float(config["budget"]["prediction_span_seconds"]) / base_dt))
    expected_nodes = int(config["budget"]["decision_nodes"])
    nonuniform_end_seconds = [base_dt * value for value in _cumulative(nonuniform)]
    w2_horizons = [float(value) * 60.0 for value in w2_interface["forecast_interface"]["horizon_minutes"]]

    checks = {
        "w2_f1_is_frozen": w2_interface["status"] == "frozen",
        "persistence_is_actual_baseline": (
            w2_interface["w3_inputs"]["actual_forecast_baseline"] == "persistence"
            and w2_summary["validation_gate"]["decision"] == "persistence"
        ),
        "locked_splits_not_accessed": (
            w2_interface["data_isolation"]["locked_splits_accessed"] == []
            and w2_summary["locked_splits_accessed"] == []
        ),
        "persistence_rmse_is_nondecreasing": all(
            later >= earlier for earlier, later in zip(persistence_rmse, persistence_rmse[1:])
        ),
        "w2_error_horizons_are_nodes": all(value in nonuniform_end_seconds for value in w2_horizons),
        "w1_correlation_exceeds_prediction_span": (
            float(w1["acf_psd"]["autocorrelation"]["first_lag_at_or_below_1_over_e_hours"])
            * 3600.0
            >= float(config["budget"]["prediction_span_seconds"])
        ),
        "equal_decision_node_budget": len(nonuniform) == len(uniform) == expected_nodes,
        "equal_prediction_span": sum(nonuniform) == sum(uniform) == span_steps,
        "first_applied_interval_is_one_control_step": nonuniform[0] == uniform[0] == 1,
    }
    report = {
        "study_id": config["study_id"],
        "stage": "W3-P0",
        "pass": all(checks.values()),
        "checks": checks,
        "budget": config["budget"],
        "evidence": {
            "w1_correlation_1_over_e_hours": float(
                w1["acf_psd"]["autocorrelation"]["first_lag_at_or_below_1_over_e_hours"]
            ),
            "w2_horizon_minutes": w2_interface["forecast_interface"]["horizon_minutes"],
            "w2_persistence_rmse_pu": persistence_rmse,
            "selected_forecast": w2_summary["validation_gate"]["decision"],
            "locked_splits_accessed": w2_summary["locked_splits_accessed"],
        },
        "nonuniform": _describe_intervals(nonuniform, base_dt),
        "uniform_tail": _describe_intervals(uniform, base_dt),
    }
    if not report["pass"]:
        raise RuntimeError("W3-P0 preparation gate failed")
    return report


def _cumulative(values):
    result = []
    running = 0
    for value in values:
        running += int(value)
        result.append(running)
    return result


def _describe_intervals(intervals, base_dt):
    return {
        "interval_steps": [int(value) for value in intervals],
        "interval_seconds": [float(base_dt * value) for value in intervals],
        "node_end_seconds": [float(base_dt * value) for value in _cumulative(intervals)],
        "decision_nodes": len(intervals),
        "prediction_span_seconds": float(base_dt * sum(intervals)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = _load_json(args.config.resolve())
    output = args.output
    if output is None:
        output = PROJECT_ROOT / config["outputs"]["preparation_directory"] / "summary.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = prepare(args.config)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
