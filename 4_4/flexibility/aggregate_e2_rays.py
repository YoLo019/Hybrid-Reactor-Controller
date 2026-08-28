# -*- coding: utf-8 -*-
"""汇总独立E2射线报告，并执行结构一致性与主张就绪检查。"""

import argparse
import json
from pathlib import Path

import numpy as np


IDENTITY_FIELDS = (
    "study_id",
    "input_definition",
    "system_scaling",
    "constraint_registry_id",
    "config_sha256",
    "code_bundle_sha256",
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate_reports(reports, fallback_data_peak_system_pu=None):
    """把每条射线的单独报告合并为一个可审查的结构化摘要。"""
    if not reports:
        raise ValueError("no ray reports found")

    expected_counts = {int(report["ray_count_total"]) for report in reports}
    if len(expected_counts) != 1:
        raise ValueError("ray_count_total is inconsistent across reports")
    expected_count = expected_counts.pop()

    shared_identity = {}
    for field in IDENTITY_FIELDS:
        values = {_canonical(report[field]) for report in reports}
        if len(values) != 1:
            raise ValueError(f"{field} is inconsistent across reports")
        shared_identity[field] = reports[0][field]

    rays_by_index = {}
    duplicate_indices = []
    for report in reports:
        if len(report["ray_results"]) != 1:
            raise ValueError("each parallel ray report must contain exactly one ray")
        ray = report["ray_results"][0]
        index = int(ray["ray_index"])
        if index in rays_by_index:
            duplicate_indices.append(index)
        rays_by_index[index] = ray

    missing_indices = sorted(set(range(expected_count)) - set(rays_by_index))
    rows = []
    for index in sorted(rays_by_index):
        result = rays_by_index[index]
        ray = result["ray"]
        frequency = ray["frequency"]
        safe_boundary = result["safe_boundary_amplitude_pu"]
        data_peak = frequency.get(
            "data_relevance_peak_system_pu", fallback_data_peak_system_pu
        )
        rows.append(
            {
                "ray_index": index,
                "controller": ray["controller"],
                "frequency_hz": float(frequency["frequency_hz"]),
                "phase_rad": float(ray["phase_rad"]),
                "power_pu": float(ray["power_pu"]),
                "soc": float(ray["soc"]),
                "boundary_status": result["boundary_status"],
                "safe_boundary_amplitude_pu": safe_boundary,
                "first_failure_amplitude_pu": result["first_failure_amplitude_pu"],
                "boundary_width_pu": result["boundary_width_pu"],
                "active_constraint": result["active_constraint"],
                "first_violation_phase": result["first_violation_phase"],
                "center_safe": bool(result["coarse_scan"]["center_safe"]),
                "non_star_shaped": bool(result["coarse_scan"]["non_star_shaped"]),
                "failure_recovery_complete": (
                    None
                    if result["failure_recovery"] is None
                    else bool(result["failure_recovery"]["complete"])
                ),
                "data_relevance_peak_system_pu": data_peak,
                "boundary_to_data_peak_ratio": (
                    None
                    if safe_boundary is None or data_peak in (None, 0)
                    else float(safe_boundary) / float(data_peak)
                ),
            }
        )

    finite_boundaries = [
        float(row["safe_boundary_amplitude_pu"])
        for row in rows
        if row["safe_boundary_amplitude_pu"] is not None
    ]
    gating_issues = {
        "unsafe_center_ray_indices": [row["ray_index"] for row in rows if not row["center_safe"]],
        "non_star_ray_indices": [row["ray_index"] for row in rows if row["non_star_shaped"]],
    }
    boundary_findings = {
        "recovery_incomplete_ray_indices": [
            row["ray_index"]
            for row in rows
            if row["failure_recovery_complete"] is False
        ],
        "right_censored_ray_indices": [
            row["ray_index"]
            for row in rows
            if row["boundary_status"] == "right_censored_at_search_upper"
        ],
    }
    complete = not missing_indices and not duplicate_indices and len(rows) == expected_count
    structurally_ready = complete and not any(gating_issues.values())
    return {
        "status": "complete" if complete else "incomplete",
        "expected_ray_count": expected_count,
        "observed_ray_count": len(rows),
        "missing_ray_indices": missing_indices,
        "duplicate_ray_indices": sorted(set(duplicate_indices)),
        "shared_identity": shared_identity,
        "gating_issues": gating_issues,
        "boundary_findings": boundary_findings,
        "structurally_ready_for_full_operating_matrix": structurally_ready,
        "boundary_summary_pu": {
            "minimum": None if not finite_boundaries else float(np.min(finite_boundaries)),
            "median": None if not finite_boundaries else float(np.median(finite_boundaries)),
            "maximum": None if not finite_boundaries else float(np.max(finite_boundaries)),
        },
        "rays": rows,
    }


def load_data_peak_from_config(reports):
    """从正式配置读取慢域P99暴露水平，兼容频率项未重复该字段的配置。"""
    config_paths = {report.get("config_path") for report in reports}
    config_paths.discard(None)
    if len(config_paths) != 1:
        return None
    config_path = Path(config_paths.pop())
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    amplitudes = config.get("data_relevance_amplitudes_system_pu", {})
    value = amplitudes.get("p99")
    return None if value is None else float(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--summary-name", default="e2_d0_reference_pilot_aggregate.json"
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    report_paths = sorted(output_dir.glob("e2_formal_ray_*_summary.json"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    data_peak = load_data_peak_from_config(reports)
    summary = aggregate_reports(reports, fallback_data_peak_system_pu=data_peak)
    summary_path = output_dir / args.summary_name
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
