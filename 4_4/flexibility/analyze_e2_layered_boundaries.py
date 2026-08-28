# -*- coding: utf-8 -*-
"""从E2正式case重建强迫阶段物理边界与恢复完成联合边界。"""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def forcing_stage_safe(case):
    """仅检查强迫结束前是否发生正式物理约束违规。"""
    forcing_end_s = float(case["case_config"]["forcing_end_s"])
    for record in case["constraints"]["constraints"].values():
        violation_time = record["first_violation_time_s"]
        if violation_time is not None and float(violation_time) < forcing_end_s:
            return False
    return True


def case_row(case_path):
    """读取一个case摘要并提取分层边界所需字段。"""
    case = json.loads(case_path.read_text(encoding="utf-8"))
    config = case["case_config"]
    constraints = case["constraints"]
    solver_failures = int(constraints["metrics"]["solver_failures"])
    return {
        "case_file": case_path.name,
        "study_id": str(config["study_id"]),
        "controller": str(config["controller"]),
        "frequency_hz": float(config["frequency_hz"]),
        "phase_rad": float(config["phase_rad"]),
        "amplitude_pu": float(config["amplitude_pu"]),
        "forcing_stage_safe": forcing_stage_safe(case),
        "full_horizon_physical_safe": bool(constraints["safe"]),
        "recovery_complete": bool(constraints["recovery"]["complete"]),
        "joint_valid_for_boundary": bool(constraints["valid_for_boundary"]),
        "solver_failures": solver_failures,
        "active_constraint": constraints["active_constraint"],
        "first_violation_time_s": constraints["first_violation_time_s"],
        "first_violation_phase": constraints["first_violation_phase"],
    }


def boundary_from_rows(rows, field, tolerance_pu):
    """按幅值递增重建首次失效边界，并标记精度与非星形。"""
    ordered = sorted(rows, key=lambda row: row["amplitude_pu"])
    amplitudes = [row["amplitude_pu"] for row in ordered]
    if len(amplitudes) != len(set(amplitudes)):
        raise ValueError("duplicate amplitudes in one ray")
    if not ordered or not math.isclose(amplitudes[0], 0.0, abs_tol=1e-15):
        raise ValueError("each ray must include the zero-amplitude center")

    values = [bool(row[field]) for row in ordered]
    if not values[0]:
        return {
            "status": "unsafe_at_center",
            "conservative_safe_amplitude_pu": None,
            "first_failure_amplitude_pu": 0.0,
            "bracket_width_pu": None,
            "precision_status": "not_applicable",
            "non_star_shaped": bool(any(values[1:])),
            "transitions": [],
        }

    transitions = []
    for previous, current in zip(ordered[:-1], ordered[1:]):
        if bool(previous[field]) != bool(current[field]):
            transitions.append(
                {
                    "lower_amplitude_pu": previous["amplitude_pu"],
                    "upper_amplitude_pu": current["amplitude_pu"],
                    "from_safe": bool(previous[field]),
                    "to_safe": bool(current[field]),
                }
            )
    first_failure = next(
        (
            transition
            for transition in transitions
            if transition["from_safe"] and not transition["to_safe"]
        ),
        None,
    )
    if first_failure is None:
        return {
            "status": "right_censored_at_search_upper",
            "conservative_safe_amplitude_pu": amplitudes[-1],
            "first_failure_amplitude_pu": None,
            "bracket_width_pu": None,
            "precision_status": "right_censored",
            "non_star_shaped": False,
            "transitions": transitions,
        }

    width = float(
        first_failure["upper_amplitude_pu"]
        - first_failure["lower_amplitude_pu"]
    )
    first_unsafe_index = next(index for index, value in enumerate(values) if not value)
    non_star = any(values[first_unsafe_index + 1 :]) or len(transitions) > 1
    return {
        "status": "bracketed_first_failure",
        "conservative_safe_amplitude_pu": first_failure["lower_amplitude_pu"],
        "first_failure_amplitude_pu": first_failure["upper_amplitude_pu"],
        "bracket_width_pu": width,
        "precision_status": (
            "within_frozen_tolerance"
            if width <= float(tolerance_pu)
            else "coarse_bracket_needs_refinement"
        ),
        "non_star_shaped": bool(non_star),
        "transitions": transitions,
    }


def _same_number(left, right):
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)


def _load_run_identity(run_dir):
    aggregate_paths = sorted(run_dir.glob("*aggregate.json"))
    if len(aggregate_paths) != 1:
        raise ValueError(f"expected one aggregate in {run_dir}")
    aggregate = json.loads(aggregate_paths[0].read_text(encoding="utf-8"))
    if aggregate["status"] != "complete":
        raise ValueError(f"aggregate is not complete: {run_dir}")

    report_paths = sorted(run_dir.glob("e2_formal_ray_*_summary.json"))
    if len(report_paths) != int(aggregate["expected_ray_count"]):
        raise ValueError(f"ray report count does not match aggregate: {run_dir}")
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    config_paths = {report["config_path"] for report in reports}
    if len(config_paths) != 1:
        raise ValueError(f"config_path is inconsistent: {run_dir}")
    config_path = Path(config_paths.pop())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return aggregate_paths[0], aggregate, config_path, config


def analyze_run(run_dir):
    """分析一个已聚合的E2中心频率波次。"""
    aggregate_path, aggregate, config_path, config = _load_run_identity(run_dir)
    tolerance_pu = float(config["bisection_tolerance_pu"])
    rows = [case_row(path) for path in sorted((run_dir / "cases").glob("*.json"))]
    if not rows:
        raise ValueError(f"no case summaries in {run_dir}")
    solver_failure_total = sum(row["solver_failures"] for row in rows)
    if solver_failure_total:
        raise ValueError(f"solver failures prevent boundary analysis: {run_dir}")

    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["controller"], row["frequency_hz"], row["phase_rad"])].append(row)

    ray_results = []
    recovery_limited_indices = []
    forcing_refinement_indices = []
    recovery_phase_physical_violation_cases = []
    for row in rows:
        if row["forcing_stage_safe"] != row["full_horizon_physical_safe"]:
            recovery_phase_physical_violation_cases.append(row["case_file"])

    for ray in sorted(aggregate["rays"], key=lambda item: int(item["ray_index"])):
        key = (
            str(ray["controller"]),
            float(ray["frequency_hz"]),
            float(ray["phase_rad"]),
        )
        ray_rows = grouped.get(key, [])
        if not ray_rows:
            raise ValueError(f"no cases for aggregate ray {ray['ray_index']}: {run_dir}")
        forcing = boundary_from_rows(ray_rows, "forcing_stage_safe", tolerance_pu)
        joint = boundary_from_rows(ray_rows, "joint_valid_for_boundary", tolerance_pu)
        if not _same_number(
            joint["conservative_safe_amplitude_pu"],
            ray["safe_boundary_amplitude_pu"],
        ) or not _same_number(
            joint["first_failure_amplitude_pu"],
            ray["first_failure_amplitude_pu"],
        ):
            raise ValueError(
                f"joint boundary does not reproduce aggregate ray {ray['ray_index']}"
            )

        failure_amplitude = joint["first_failure_amplitude_pu"]
        failure_row = next(
            (
                item
                for item in ray_rows
                if _same_number(item["amplitude_pu"], failure_amplitude)
            ),
            None,
        )
        recovery_limited = bool(
            failure_row is not None
            and failure_row["forcing_stage_safe"]
            and not failure_row["recovery_complete"]
            and not failure_row["joint_valid_for_boundary"]
        )
        if recovery_limited:
            recovery_limited_indices.append(int(ray["ray_index"]))
        if forcing["precision_status"] == "coarse_bracket_needs_refinement":
            forcing_refinement_indices.append(int(ray["ray_index"]))

        forcing_safe = forcing["conservative_safe_amplitude_pu"]
        joint_safe = joint["conservative_safe_amplitude_pu"]
        gap = (
            None
            if forcing_safe is None or joint_safe is None
            else float(forcing_safe - joint_safe)
        )
        ratio = (
            None
            if forcing_safe is None or joint_safe in (None, 0.0)
            else float(forcing_safe / joint_safe)
        )
        ray_results.append(
            {
                "ray_index": int(ray["ray_index"]),
                "controller": ray["controller"],
                "frequency_hz": float(ray["frequency_hz"]),
                "phase_rad": float(ray["phase_rad"]),
                "case_count": len(ray_rows),
                "forcing_stage_physical_boundary": forcing,
                "joint_recovery_complete_boundary": joint,
                "joint_first_failure_recovery_limited": recovery_limited,
                "minimum_forcing_minus_joint_safe_pu": gap,
                "forcing_to_joint_safe_ratio_lower_bound": ratio,
            }
        )

    return {
        "run_dir": str(run_dir.resolve()),
        "aggregate_file": str(aggregate_path.resolve()),
        "study_id": aggregate["shared_identity"]["study_id"],
        "config_file": str(config_path.resolve()),
        "config_sha256": aggregate["shared_identity"]["config_sha256"],
        "code_bundle_sha256": aggregate["shared_identity"]["code_bundle_sha256"],
        "frequency_hz": float(aggregate["rays"][0]["frequency_hz"]),
        "bisection_tolerance_pu": tolerance_pu,
        "case_count": len(rows),
        "solver_failure_total": solver_failure_total,
        "recovery_phase_physical_violation_case_files": sorted(
            recovery_phase_physical_violation_cases
        ),
        "recovery_limited_ray_indices": recovery_limited_indices,
        "forcing_boundary_refinement_ray_indices": forcing_refinement_indices,
        "rays": ray_results,
    }


def build_cross_frequency_summary(runs):
    """汇总三频点中恢复限制与强迫边界精度。"""
    rays = [ray for run in runs for ray in run["rays"]]
    recovery_limited = [ray for ray in rays if ray["joint_first_failure_recovery_limited"]]
    refinement = [
        ray
        for ray in rays
        if ray["forcing_stage_physical_boundary"]["precision_status"]
        == "coarse_bracket_needs_refinement"
    ]
    equal_within_tolerance = [
        ray
        for ray in rays
        if not ray["joint_first_failure_recovery_limited"]
        and _same_number(
            ray["forcing_stage_physical_boundary"]["conservative_safe_amplitude_pu"],
            ray["joint_recovery_complete_boundary"]["conservative_safe_amplitude_pu"],
        )
        and _same_number(
            ray["forcing_stage_physical_boundary"]["first_failure_amplitude_pu"],
            ray["joint_recovery_complete_boundary"]["first_failure_amplitude_pu"],
        )
    ]
    code_counts = Counter(run["code_bundle_sha256"] for run in runs)
    reference_code, _ = code_counts.most_common(1)[0]
    identity_outliers = [
        run["study_id"]
        for run in runs
        if run["code_bundle_sha256"] != reference_code
    ]
    identity_consistent = len(code_counts) == 1
    return {
        "frequency_count": len(runs),
        "ray_count": len(rays),
        "cross_frequency_code_identity_consistent": identity_consistent,
        "code_bundle_sha256_values": sorted(code_counts),
        "reference_code_bundle_sha256": reference_code,
        "identity_outlier_study_ids": identity_outliers,
        "formal_cross_frequency_comparison_ready": bool(identity_consistent),
        "recovery_limited_ray_count": len(recovery_limited),
        "forcing_boundary_refinement_ray_count": len(refinement),
        "forcing_and_joint_equal_within_tolerance_ray_count": len(
            equal_within_tolerance
        ),
        "recovery_limited_controller_phase_pairs": sorted(
            {
                f"{ray['controller']}@{ray['phase_rad']:.12g}"
                for ray in recovery_limited
            }
        ),
        "minimum_forcing_minus_joint_safe_pu": (
            None
            if not recovery_limited
            else min(
                ray["minimum_forcing_minus_joint_safe_pu"]
                for ray in recovery_limited
            )
        ),
        "maximum_forcing_to_joint_safe_ratio_lower_bound": (
            None
            if not recovery_limited
            else max(
                ray["forcing_to_joint_safe_ratio_lower_bound"]
                for ray in recovery_limited
            )
        ),
        "claim_boundary": (
            "联合边界可作为各波次冻结恢复判据下的保守运行边界；恢复限制"
            "射线的强迫阶段物理边界目前只支持区间/下界主张，精确值需定向"
            "二分。跨频正式比较还要求全部波次代码身份一致。"
            if identity_consistent
            else "各波次内部的分层边界结论有效，但代码身份不一致使跨频复现"
            "结论仅为观察；需用参考代码身份重跑异常波次后再形成正式跨频主张。"
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runs = [analyze_run(run_dir.resolve()) for run_dir in args.run_dir]
    result = {
        "analysis_id": "E2-F1-LAYERED-BOUNDARIES-V1",
        "definitions": {
            "forcing_stage_physical_boundary": (
                "强迫结束前全部正式plant/device约束通过；不要求恢复完成"
            ),
            "joint_recovery_complete_boundary": (
                "正式plant/device约束通过且恢复末段持续进入冻结邻域"
            ),
        },
        "cross_frequency_summary": build_cross_frequency_summary(runs),
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
