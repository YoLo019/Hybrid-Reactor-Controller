# -*- coding: utf-8 -*-
"""审计E2射线的恢复误差形状，并区分强迫约束与联合恢复判据。"""

import argparse
import json
from pathlib import Path

import numpy as np

from analyze_e2_layered_boundaries import forcing_stage_safe


def recovery_row(case_path, trend_window_s):
    """从单案例摘要与数组计算恢复末段误差和趋势。"""
    case = json.loads(case_path.read_text(encoding="utf-8"))
    arrays = np.load(str(case_path.parent / case["npz"]))
    t = arrays["t"]
    signed_error = arrays["Pe"] - arrays["Target_abs"]
    recovery = case["constraints"]["recovery"]
    sustain_s = float(case["case_config"]["recovery"]["sustain_s"])
    final_time = float(t[-1])
    sustain_mask = t >= final_time - sustain_s
    trend_mask = t >= final_time - float(trend_window_s)
    trend_t = t[trend_mask] - t[trend_mask][0]
    trend_slope = float(np.polyfit(trend_t, signed_error[trend_mask], 1)[0])
    computed_max_abs = float(np.max(np.abs(signed_error[sustain_mask])))
    reported_max_abs = float(recovery["observed"]["power_abs_error_pu"])
    if not np.isclose(computed_max_abs, reported_max_abs, rtol=0.0, atol=1e-12):
        raise ValueError(f"recovery metric mismatch: {case_path}")
    return {
        "case_file": case_path.name,
        "npz_file": case["npz"],
        "study_id": case["case_config"]["study_id"],
        "controller": case["case_config"]["controller"],
        "phase_rad": float(case["case_config"]["phase_rad"]),
        "amplitude_pu": float(case["case_config"]["amplitude_pu"]),
        "forcing_constraints_pass": forcing_stage_safe(case),
        "full_horizon_physical_constraints_pass": bool(
            case["constraints"]["safe"]
        ),
        "recovery_complete": bool(recovery["complete"]),
        "joint_valid_for_boundary": bool(case["constraints"]["valid_for_boundary"]),
        "solver_failures": int(case["constraints"]["metrics"]["solver_failures"]),
        "power_error_limit_pu": float(recovery["limits"]["power_abs_error_pu"]),
        "power_error_max_abs_final_sustain_pu": computed_max_abs,
        "power_error_mean_signed_final_sustain_pu": float(
            np.mean(signed_error[sustain_mask])
        ),
        "power_error_endpoint_signed_pu": float(signed_error[-1]),
        "power_error_trend_slope_pu_per_s": trend_slope,
    }


def detect_transitions(rows, field):
    """记录随幅值增加时布尔判据的全部切换。"""
    transitions = []
    for previous, current in zip(rows[:-1], rows[1:]):
        if previous[field] != current[field]:
            transitions.append(
                {
                    "lower_amplitude_pu": previous["amplitude_pu"],
                    "upper_amplitude_pu": current["amplitude_pu"],
                    "from": previous[field],
                    "to": current[field],
                }
            )
    return transitions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--phase-rad", type=float, required=True)
    parser.add_argument("--recovery-duration-s", type=float)
    parser.add_argument("--trend-window-s", type=float, default=60.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.trend_window_s <= 0.0:
        raise ValueError("trend-window-s must be positive")
    case_paths = sorted((args.run_dir / "cases").glob("*.json"))
    rows = []
    for case_path in case_paths:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        config = case["case_config"]
        if str(config["controller"]) != args.controller:
            continue
        if not np.isclose(float(config["phase_rad"]), args.phase_rad):
            continue
        if args.recovery_duration_s is not None and not np.isclose(
            float(config["recovery"]["duration_s"]), args.recovery_duration_s
        ):
            continue
        rows.append(recovery_row(case_path, args.trend_window_s))
    if not rows:
        raise ValueError("no matching cases")
    rows.sort(key=lambda row: row["amplitude_pu"])

    study_ids = sorted({row["study_id"] for row in rows})
    limits = sorted({row["power_error_limit_pu"] for row in rows})
    if len(study_ids) != 1 or len(limits) != 1:
        raise ValueError("case identity or recovery limit is inconsistent")
    joint_transitions = detect_transitions(rows, "joint_valid_for_boundary")
    forcing_transitions = detect_transitions(rows, "forcing_constraints_pass")
    recovery_failures_with_forcing_pass = [
        row["amplitude_pu"]
        for row in rows
        if row["forcing_constraints_pass"] and not row["recovery_complete"]
    ]
    result = {
        "study_id": study_ids[0],
        "controller": args.controller,
        "phase_rad": args.phase_rad,
        "recovery_duration_s": args.recovery_duration_s,
        "trend_window_s": args.trend_window_s,
        "power_error_limit_pu": limits[0],
        "case_count": len(rows),
        "solver_failure_total": sum(row["solver_failures"] for row in rows),
        "joint_validity_transitions": joint_transitions,
        "forcing_constraint_transitions": forcing_transitions,
        "recovery_failures_with_forcing_pass_pu": recovery_failures_with_forcing_pass,
        "threshold_driven_non_star_joint_validity": bool(
            len(joint_transitions) > 1 and recovery_failures_with_forcing_pass
        ),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
