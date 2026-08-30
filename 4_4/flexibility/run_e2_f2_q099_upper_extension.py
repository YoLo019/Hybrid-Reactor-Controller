# -*- coding: utf-8 -*-
"""定向扩展 q0.99 右删失 MPC 射线的搜索上界并生成合并复核。"""

from __future__ import annotations

import datetime as dt
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


MODEL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e2_f2_cross_operating import (  # noqa: E402
    FORCING_LABEL,
    JOINT_LABEL,
    _build_case_config,
    _case_row,
    _first_failure_bracket,
    _frequency_spec,
    aggregate_reports,
    expand_rays,
    validate_config,
)
from run_e2_formal import code_bundle_provenance  # noqa: E402
from run_e2_frequency_diagnostic import run_case  # noqa: E402
from run_e2_smoke import canonical_hash, prepare_operating_point, sha256  # noqa: E402


EXPECTED_TARGET_RAY_INDICES = (1, 3, 5, 9, 11, 13, 17, 19, 21)
BASE_MODE = "precheck"
FREQUENCY_LABEL = "q0.99"


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _report_path(output_dir: Path, ray_index: int) -> Path:
    return output_dir / f"e2_f2_q0.99_precheck_upper_extension_ray_{ray_index:04d}_summary.json"


def _base_report_path(base_dir: Path, ray_index: int) -> Path:
    return base_dir / f"e2_f2_q0.99_precheck_ray_{ray_index:04d}_summary.json"


def _sorted_unique_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: float(item["amplitude_pu"]))
    seen = set()
    unique = []
    for row in ordered:
        key = f"{float(row['amplitude_pu']):.12f}"
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def validate_extension_inputs(
    base_config_path: Path,
    extension_config_path: Path,
    base_aggregate_path: Path,
) -> Dict[str, Any]:
    """校验扩展只针对当前九条右删失射线，且不改变基础配置身份。"""
    base_config = _load_json(base_config_path)
    validate_config(base_config)
    extension = _load_json(extension_config_path)
    if extension.get("extension_id") != "E2-F2-Q099-MPC-UPPER-EXTENSION-V1":
        raise ValueError("unexpected upper-extension identity")
    if str(extension.get("frequency_label")) != FREQUENCY_LABEL:
        raise ValueError("upper extension is frozen to q0.99")
    target_indices = tuple(int(value) for value in extension.get("target_ray_indices", []))
    if target_indices != EXPECTED_TARGET_RAY_INDICES:
        raise ValueError(
            "target_ray_indices must match the nine q0.99 MPC right-censored rays"
        )
    amplitudes = sorted(float(value) for value in extension.get("extension_amplitude_grid_pu", []))
    if not amplitudes or len(amplitudes) != len(set(amplitudes)):
        raise ValueError("extension_amplitude_grid_pu must be non-empty and unique")
    base_amplitudes = sorted(float(value) for value in base_config["amplitude_grid_pu"])
    if any(value <= base_amplitudes[-1] for value in amplitudes):
        raise ValueError("extension amplitudes must be above the base search upper")
    if extension.get("stop_policy") != "stop_each_ray_after_first_forcing_safe_to_unsafe_bracket":
        raise ValueError("unexpected per-ray stop policy")
    if not base_aggregate_path.is_file():
        raise FileNotFoundError(base_aggregate_path)
    aggregate = _load_json(base_aggregate_path)
    if aggregate.get("status") != "complete":
        raise ValueError("base q0.99 aggregate must be complete")
    if aggregate.get("mode") != BASE_MODE or aggregate.get("frequency_label") != FREQUENCY_LABEL:
        raise ValueError("base aggregate must be q0.99 precheck")
    if int(aggregate.get("expected_ray_count", -1)) != 72 or int(aggregate.get("observed_ray_count", -1)) != 72:
        raise ValueError("base aggregate must contain all 72 q0.99 rays")
    shared = aggregate.get("shared_identity", {})
    if shared.get("config_sha256") != sha256(base_config_path):
        raise ValueError("base aggregate config identity does not match base config")
    missing = tuple(
        int(value)
        for value in aggregate.get("gating_issues", {}).get("forcing_bracket_missing_ray_indices", [])
    )
    if missing != EXPECTED_TARGET_RAY_INDICES:
        raise ValueError(
            "base forcing-bracket missing set differs from the frozen nine-ray target"
        )
    rays = expand_rays(base_config, FREQUENCY_LABEL)
    for index in target_indices:
        ray = rays[index]
        if ray["controller"] != "MPC" or float(ray["power_pu"]) != 0.8:
            raise ValueError(f"target ray {index} is not a P=0.8 MPC ray")
    return {
        "pass": True,
        "extension_id": extension["extension_id"],
        "target_ray_indices": list(target_indices),
        "base_search_upper_amplitude_pu": base_amplitudes[-1],
        "extension_amplitude_grid_pu": amplitudes,
        "base_config_sha256": sha256(base_config_path),
        "base_aggregate_sha256": sha256(base_aggregate_path),
    }


def _target_result_from_base(
    base_report_path: Path,
) -> Dict[str, Any]:
    report = _load_json(base_report_path)
    results = report.get("ray_results", [])
    if len(results) != 1:
        raise ValueError(f"base report must contain one ray: {base_report_path}")
    return results[0]


def run_single_ray(
    base_config_path: Path,
    extension_config_path: Path,
    base_aggregate_path: Path,
    output_dir: Path,
    ray_index: int,
    base_config: Optional[Dict[str, Any]] = None,
    extension: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """复用 0.3 及以下 F2 case，仅对目标射线追加上界案例。"""
    config = base_config or _load_json(base_config_path)
    extension = extension or _load_json(extension_config_path)
    validation = validate_extension_inputs(
        base_config_path, extension_config_path, base_aggregate_path
    )
    target_indices = tuple(validation["target_ray_indices"])
    if ray_index not in target_indices:
        raise ValueError(f"ray-index must be one of {target_indices}")

    base_dir = base_aggregate_path.parent
    base_report_path = _base_report_path(base_dir, ray_index)
    base_result = _target_result_from_base(base_report_path)
    if int(base_result["wave_ray_index"]) != ray_index:
        raise ValueError(f"base report ray index mismatch: {base_report_path}")
    if base_result["ray"]["controller"] != "MPC":
        raise ValueError("upper extension is frozen to MPC")

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    core_provenance, core_bundle_hash = code_bundle_provenance()
    expected_core = str(config["identity_policy"]["expected_core_code_bundle_sha256"])
    if core_bundle_hash != expected_core:
        raise RuntimeError(
            f"core code bundle identity drift: expected {expected_core}, got {core_bundle_hash}"
        )
    f2_runner_path = FLEXIBILITY_ROOT / "run_e2_f2_cross_operating.py"
    f2_runner_hash = sha256(f2_runner_path)
    extension_runner_hash = sha256(Path(__file__).resolve())
    ray = expand_rays(config, FREQUENCY_LABEL)[ray_index]
    params, initial_conditions, operating_state, observation = prepare_operating_point(
        ray["power_pu"], ray["soc"]
    )
    frequency_spec = {
        "frequency_hz": ray["frequency_hz"],
        "cycles": ray["cycles"],
        "evidence_class": ray["evidence_class"],
    }
    rows = list(base_result["evaluations"])
    base_upper = float(validation["base_search_upper_amplitude_pu"])
    observed_base_upper = max(float(row["amplitude_pu"]) for row in rows)
    if abs(observed_base_upper - base_upper) > 1e-12:
        raise ValueError("base report does not reach the registered search upper")
    extension_evaluated = []
    stop_reason = "extension_grid_exhausted_without_forcing_bracket"

    def evaluate(amplitude: float) -> Dict[str, Any]:
        case = run_case(
            ray["controller"],
            frequency_spec,
            _build_case_config(config, ray, amplitude),
            params,
            initial_conditions,
            operating_state,
            output_dir,
            core_bundle_hash,
            core_provenance["metrics_source"],
        )
        row = _case_row(
            Path(case["npz"]).with_suffix(".json"),
            case,
            "q099_upper_extension",
        )
        return row

    forcing_boundary = _first_failure_bracket(rows, FORCING_LABEL)
    joint_boundary = _first_failure_bracket(rows, JOINT_LABEL)
    if forcing_boundary["first_failure_bracket"] is not None:
        stop_reason = "base_report_already_bracketed"
    else:
        for amplitude in validation["extension_amplitude_grid_pu"]:
            row = evaluate(amplitude)
            rows.append(row)
            extension_evaluated.append(float(amplitude))
            forcing_boundary = _first_failure_bracket(rows, FORCING_LABEL)
            joint_boundary = _first_failure_bracket(rows, JOINT_LABEL)
            if forcing_boundary["first_failure_bracket"] is not None:
                stop_reason = "first_forcing_failure_bracket_found"
                break

    ordered_rows = _sorted_unique_rows(rows)
    forcing_boundary = _first_failure_bracket(ordered_rows, FORCING_LABEL)
    joint_boundary = _first_failure_bracket(ordered_rows, JOINT_LABEL)
    solver_failure_total = int(sum(int(row["solver_failures"]) for row in ordered_rows))
    recovery_physical_violations = sorted(
        row["case_file"]
        for row in ordered_rows
        if row[FORCING_LABEL] and not row["full_horizon_physical_safe"]
    )
    result = {
        "wave_ray_index": ray_index,
        "ray": ray,
        "operating_observation": {
            "electrical_power_pu": float(observation["p_e_pu"]),
            "frequency_pu": float(observation["frequency_pu"]),
        },
        "base_report_path": str(base_report_path.resolve()),
        "base_search_upper_amplitude_pu": base_upper,
        "extension_amplitude_grid_pu": list(validation["extension_amplitude_grid_pu"]),
        "extension_evaluated_amplitudes_pu": extension_evaluated,
        "extension_stop_reason": stop_reason,
        "coarse_scan": {
            "amplitude_grid_pu": [float(row["amplitude_pu"]) for row in ordered_rows],
            "evaluation_count": len(ordered_rows),
            "forcing_stage": forcing_boundary,
            "joint_recovery": joint_boundary,
        },
        "forcing_stage_boundary": forcing_boundary,
        "joint_recovery_complete_boundary": joint_boundary,
        "solver_failure_total": solver_failure_total,
        "recovery_phase_physical_violation_case_files": recovery_physical_violations,
        "evaluations": ordered_rows,
    }
    report = {
        "study_id": config["study_id"],
        "scope": config["scope"],
        "mode": BASE_MODE,
        "frequency_label": FREQUENCY_LABEL,
        "config_path": str(base_config_path.resolve()),
        "config_sha256": sha256(base_config_path),
        "base_aggregate_path": str(base_aggregate_path.resolve()),
        "base_aggregate_sha256": sha256(base_aggregate_path),
        "extension_config_path": str(extension_config_path.resolve()),
        "extension_config_sha256": sha256(extension_config_path),
        "core_code_provenance": core_provenance,
        "core_code_bundle_sha256": core_bundle_hash,
        "f2_runner_sha256": f2_runner_hash,
        "extension_runner_sha256": extension_runner_hash,
        "preparation_gate": validation,
        "ray_count_total": 72,
        "ray_count_in_report": 1,
        "extension_id": extension["extension_id"],
        "ray_results": [result],
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    report_path = _report_path(output_dir, ray_index)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _identity_from_reports(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields = (
        "study_id",
        "mode",
        "frequency_label",
        "config_sha256",
        "core_code_bundle_sha256",
        "f2_runner_sha256",
    )
    identity = {}
    for field in fields:
        values = {json.dumps(report.get(field), ensure_ascii=False, sort_keys=True) for report in reports}
        if len(values) != 1:
            raise ValueError(f"{field} is inconsistent across extension reports")
        identity[field] = reports[0].get(field)
    return identity


def aggregate_extension_reports(
    reports: List[Dict[str, Any]],
    config: Dict[str, Any],
    extension_config_path: Path,
    base_aggregate_path: Path,
) -> Dict[str, Any]:
    """聚合九条扩展射线，联合括区仅作为诊断，不阻塞强迫门。"""
    extension = _load_json(extension_config_path)
    targets = sorted(int(value) for value in extension["target_ray_indices"])
    by_index = {}
    duplicates = []
    for report in reports:
        if len(report.get("ray_results", [])) != 1:
            raise ValueError("each extension report must contain exactly one ray")
        result = report["ray_results"][0]
        index = int(result["wave_ray_index"])
        if index in by_index:
            duplicates.append(index)
        by_index[index] = result
    missing = sorted(set(targets) - set(by_index))
    extra = sorted(set(by_index) - set(targets))
    rows = []
    for index in sorted(by_index):
        result = by_index[index]
        rows.append(
            {
                "wave_ray_index": index,
                "global_ray_key": {
                    "power_pu": float(result["ray"]["power_pu"]),
                    "soc": float(result["ray"]["soc"]),
                    "phase_rad": float(result["ray"]["phase_rad"]),
                    "controller": result["ray"]["controller"],
                    "frequency_label": result["ray"]["frequency_label"],
                },
                "base_search_upper_amplitude_pu": result["base_search_upper_amplitude_pu"],
                "extension_evaluated_amplitudes_pu": result["extension_evaluated_amplitudes_pu"],
                "extension_stop_reason": result["extension_stop_reason"],
                "forcing_stage_boundary": result["forcing_stage_boundary"],
                "joint_recovery_complete_boundary": result["joint_recovery_complete_boundary"],
                "solver_failure_total": int(result["solver_failure_total"]),
                "recovery_phase_physical_violation_case_files": result[
                    "recovery_phase_physical_violation_case_files"
                ],
                "evaluation_count": len(result["evaluations"]),
            }
        )
    complete = not missing and not duplicates and not extra and len(rows) == len(targets)
    forcing_missing = [
        row["wave_ray_index"]
        for row in rows
        if row["forcing_stage_boundary"]["first_failure_bracket"] is None
    ]
    center_unsafe = [
        row["wave_ray_index"]
        for row in rows
        if not row["forcing_stage_boundary"]["center_safe"]
    ]
    non_star = [
        row["wave_ray_index"]
        for row in rows
        if row["forcing_stage_boundary"]["non_star_shaped"]
    ]
    solver_failure = [
        row["wave_ray_index"] for row in rows if row["solver_failure_total"] > 0
    ]
    recovery_physical = [
        row["wave_ray_index"]
        for row in rows
        if row["recovery_phase_physical_violation_case_files"]
    ]
    return {
        "analysis_id": extension["extension_id"],
        "status": "complete" if complete else "incomplete",
        "mode": BASE_MODE,
        "frequency_label": FREQUENCY_LABEL,
        "expected_target_ray_count": len(targets),
        "observed_target_ray_count": len(rows),
        "target_ray_indices": targets,
        "missing_target_ray_indices": missing,
        "duplicate_target_ray_indices": sorted(set(duplicates)),
        "extra_ray_indices": extra,
        "shared_identity": _identity_from_reports(reports) if reports else {},
        "base_aggregate_path": str(base_aggregate_path.resolve()),
        "base_aggregate_sha256": sha256(base_aggregate_path),
        "extension_config_path": str(extension_config_path.resolve()),
        "extension_config_sha256": sha256(extension_config_path),
        "gating_issues": {
            "center_unsafe_ray_indices": center_unsafe,
            "forcing_bracket_missing_ray_indices": forcing_missing,
            "forcing_non_star_ray_indices": non_star,
            "solver_failure_ray_indices": solver_failure,
            "recovery_phase_physical_violation_ray_indices": recovery_physical,
        },
        "extension_gate_pass": bool(
            complete
            and not center_unsafe
            and not forcing_missing
            and not solver_failure
            and not recovery_physical
        ),
        "policy": {
            "base_f2_precheck_cases_reused_below_or_at_0_3": True,
            "f1_center_cases_reused": False,
            "joint_bracket_is_diagnostic_only": True,
            "stop_policy": extension["stop_policy"],
        },
        "rays": rows,
    }


def _merged_reports(
    base_aggregate_path: Path,
    extension_output_dir: Path,
    target_indices: List[int],
) -> List[Dict[str, Any]]:
    base_dir = base_aggregate_path.parent
    target_set = set(target_indices)
    reports = []
    for index in range(72):
        path = (
            _report_path(extension_output_dir, index)
            if index in target_set
            else _base_report_path(base_dir, index)
        )
        if not path.is_file():
            raise FileNotFoundError(path)
        reports.append(_load_json(path))
    return reports


def _acquire_lock(output_dir: Path, config_path: Path, extension_config_path: Path) -> Path:
    lock_path = output_dir / "RUNNING.lock"
    payload = {
        "pid": os.getpid(),
        "base_config_path": str(config_path.resolve()),
        "extension_config_path": str(extension_config_path.resolve()),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"upper-extension run lock exists: {lock_path}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return lock_path


def run_parallel(
    base_config_path: Path,
    extension_config_path: Path,
    base_aggregate_path: Path,
    output_dir: Path,
    workers: int,
) -> Dict[str, Any]:
    """并行执行九条目标射线，再写目标聚合与 72 条合并复核。"""
    if workers < 1:
        raise ValueError("workers must be positive")
    config = _load_json(base_config_path)
    extension = _load_json(extension_config_path)
    validation = validate_extension_inputs(
        base_config_path, extension_config_path, base_aggregate_path
    )
    targets = list(validation["target_ray_indices"])
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "process_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _acquire_lock(output_dir, base_config_path, extension_config_path)
    try:
        report_paths = {index: _report_path(output_dir, index) for index in targets}
        pending = [index for index, path in report_paths.items() if not path.is_file()]

        def run(index: int) -> Tuple[int, int, Path]:
            log_path = log_dir / f"q0.99_precheck_upper_extension_ray_{index:04d}.log"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(base_config_path.resolve()),
                "--extension-config",
                str(extension_config_path.resolve()),
                "--base-aggregate",
                str(base_aggregate_path.resolve()),
                "--output-dir",
                str(output_dir.resolve()),
                "--ray-index",
                str(index),
            ]
            with log_path.open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
            return index, completed.returncode, log_path

        failures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, index) for index in pending]
            for future in as_completed(futures):
                index, returncode, log_path = future.result()
                print(
                    json.dumps(
                        {"ray_index": index, "returncode": returncode, "log": str(log_path)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if returncode != 0:
                    failures.append(index)
        observed = [path for path in report_paths.values() if path.is_file()]
        summary = {
            "status": "complete" if not failures and len(observed) == len(report_paths) else "incomplete",
            "frequency_label": FREQUENCY_LABEL,
            "mode": BASE_MODE,
            "expected_target_ray_count": len(report_paths),
            "pending_at_start": pending,
            "failed_ray_indices": sorted(failures),
            "observed_summary_count": len(observed),
        }
        (output_dir / "parallel_q0.99_precheck_upper_extension_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if failures or len(observed) != len(report_paths):
            raise RuntimeError(f"upper extension incomplete: {summary}")

        reports = [_load_json(path) for path in report_paths.values()]
        targeted = aggregate_extension_reports(
            reports, config, extension_config_path, base_aggregate_path
        )
        targeted_path = output_dir / "e2_f2_q0.99_mpc_upper_extension_aggregate.json"
        targeted_path.write_text(
            json.dumps(targeted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        merged_reports = _merged_reports(base_aggregate_path, output_dir, targets)
        merged = aggregate_reports(merged_reports, config, FREQUENCY_LABEL, BASE_MODE)
        merged["extension_review"] = {
            "extension_id": extension["extension_id"],
            "target_ray_indices": targets,
            "targeted_aggregate_path": str(targeted_path.resolve()),
            "targeted_aggregate_sha256": sha256(targeted_path),
            "base_aggregate_path": str(base_aggregate_path.resolve()),
            "base_aggregate_sha256": sha256(base_aggregate_path),
            "extension_config_path": str(extension_config_path.resolve()),
            "extension_config_sha256": sha256(extension_config_path),
            "extension_runner_sha256": sha256(Path(__file__).resolve()),
            "case_reuse_policy": "reuse only F2 q0.99 precheck cases at or below 0.3; never reuse F1 center cases",
        }
        merged_path = output_dir / "e2_f2_q0.99_precheck_extended_aggregate.json"
        merged_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"targeted": targeted, "merged": merged}, ensure_ascii=False, indent=2))
        return merged
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--extension-config", type=Path, required=True)
    parser.add_argument("--base-aggregate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ray-index", type=int)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    base_config_path = args.config.resolve()
    extension_config_path = args.extension_config.resolve()
    base_aggregate_path = args.base_aggregate.resolve()
    output_dir = args.output_dir.resolve()
    config = _load_json(base_config_path)
    extension = _load_json(extension_config_path)
    validate_extension_inputs(base_config_path, extension_config_path, base_aggregate_path)
    if args.ray_index is None:
        run_parallel(
            base_config_path,
            extension_config_path,
            base_aggregate_path,
            output_dir,
            args.workers,
        )
    else:
        report = run_single_ray(
            base_config_path,
            extension_config_path,
            base_aggregate_path,
            output_dir,
            args.ray_index,
            config,
            extension,
        )
        result = report["ray_results"][0]
        print(
            json.dumps(
                {
                    "ray_index": args.ray_index,
                    "forcing_stage": result["forcing_stage_boundary"],
                    "joint_recovery": result["joint_recovery_complete_boundary"],
                    "extension_evaluated_amplitudes_pu": result["extension_evaluated_amplitudes_pu"],
                    "summary": str(_report_path(output_dir, args.ray_index)),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
