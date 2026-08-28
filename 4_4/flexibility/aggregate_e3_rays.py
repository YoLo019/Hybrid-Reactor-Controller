# -*- coding: utf-8 -*-
"""汇总独立E3射线，并检查完整性、身份、中心安全和求解状态。"""

import argparse
import json
from pathlib import Path

import numpy as np


IDENTITY_FIELDS = (
    "study_id",
    "formal_claim",
    "config_sha256",
    "mapping_sha256",
    "code_bundle_sha256",
    "constraint_registry_id",
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate_reports(reports):
    if not reports:
        raise ValueError("no E3 ray reports found")
    expected_counts = {int(report["ray_count_total"]) for report in reports}
    if len(expected_counts) != 1:
        raise ValueError("ray_count_total is inconsistent across reports")
    expected_count = expected_counts.pop()

    identity = {}
    for field in IDENTITY_FIELDS:
        values = {_canonical(report[field]) for report in reports}
        if len(values) != 1:
            raise ValueError(f"{field} is inconsistent across reports")
        identity[field] = reports[0][field]

    rays_by_index = {}
    duplicates = []
    for report in reports:
        if len(report["ray_results"]) != 1:
            raise ValueError("each parallel report must contain exactly one E3 ray")
        result = report["ray_results"][0]
        index = int(result["ray_index"])
        if index in rays_by_index:
            duplicates.append(index)
        rays_by_index[index] = result
    missing = sorted(set(range(expected_count)) - set(rays_by_index))

    rows = []
    for index in sorted(rays_by_index):
        result = rays_by_index[index]
        ray = result["ray"]
        rows.append(
            {
                "ray_index": index,
                "domain_id": ray["input_definition"]["domain_id"],
                "controller": ray["controller"],
                "direction": int(ray["direction"]),
                "rate_label": ray["rate_level"]["label"],
                "rate_pu_per_s": float(ray["rate_level"]["rate_pu_per_s"]),
                "hold_label": ray["hold_level"]["label"],
                "hold_duration_s": float(ray["hold_level"]["duration_s"]),
                "power_pu": float(ray["power_pu"]),
                "soc": float(ray["soc"]),
                "center_safe": bool(result["coarse_scan"]["center_safe"]),
                "non_star_shaped": bool(result["coarse_scan"]["non_star_shaped"]),
                "boundary_status": result["boundary_status"],
                "safe_boundary_amplitude_pu": result["safe_boundary_amplitude_pu"],
                "first_failure_amplitude_pu": result["first_failure_amplitude_pu"],
                "boundary_width_pu": result["boundary_width_pu"],
                "active_constraint": result["active_constraint"],
                "first_violation_phase": result["first_violation_phase"],
                "failure_recovery_complete": (
                    None
                    if result["failure_recovery"] is None
                    else bool(result["failure_recovery"]["complete"])
                ),
                "solver_failures": int(result["solver_failures"]),
                "waveform_returned_to_zero": bool(result["waveform_returned_to_zero"]),
            }
        )

    complete = not missing and not duplicates and len(rows) == expected_count
    unsafe_centers = [row["ray_index"] for row in rows if not row["center_safe"]]
    solver_failure_rays = [
        row["ray_index"] for row in rows if row["solver_failures"] > 0
    ]
    waveform_failure_rays = [
        row["ray_index"] for row in rows if not row["waveform_returned_to_zero"]
    ]
    finite_boundaries = [
        float(row["safe_boundary_amplitude_pu"])
        for row in rows
        if row["safe_boundary_amplitude_pu"] is not None
    ]
    structurally_ready = bool(
        complete
        and not unsafe_centers
        and not solver_failure_rays
        and not waveform_failure_rays
    )
    return {
        "status": "complete" if complete else "incomplete",
        "expected_ray_count": expected_count,
        "observed_ray_count": len(rows),
        "missing_ray_indices": missing,
        "duplicate_ray_indices": sorted(set(duplicates)),
        "shared_identity": identity,
        "gating_issues": {
            "unsafe_center_ray_indices": unsafe_centers,
            "solver_failure_ray_indices": solver_failure_rays,
            "waveform_failure_ray_indices": waveform_failure_rays,
        },
        "boundary_findings": {
            "non_star_ray_indices": [
                row["ray_index"] for row in rows if row["non_star_shaped"]
            ],
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
        },
        "structurally_ready_for_next_wave": structurally_ready,
        "boundary_summary_pu": {
            "minimum": None if not finite_boundaries else float(np.min(finite_boundaries)),
            "median": None if not finite_boundaries else float(np.median(finite_boundaries)),
            "maximum": None if not finite_boundaries else float(np.max(finite_boundaries)),
        },
        "rays": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary-name", default="e3_f1_aggregate.json")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    report_paths = sorted(output_dir.glob("e3_formal_ray_*_summary.json"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    summary = aggregate_reports(reports)
    summary_path = output_dir / args.summary_name
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
