# -*- coding: utf-8 -*-
"""基于合并预检证据执行 q0.99 全72条射线的双层细化二分。"""

from __future__ import annotations

import argparse
import datetime as dt
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
    _refine_boundary,
    aggregate_reports,
    expand_rays,
    validate_config,
)
from run_e2_formal import code_bundle_provenance  # noqa: E402
from run_e2_frequency_diagnostic import run_case  # noqa: E402
from run_e2_smoke import canonical_hash, prepare_operating_point, sha256  # noqa: E402


FREQUENCY_LABEL = "q0.99"
MODE = "refine"
EXPECTED_RAY_COUNT = 72
EXTENSION_TARGET_INDICES = (1, 3, 5, 9, 11, 13, 17, 19, 21)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _report_path(output_dir: Path, ray_index: int) -> Path:
    return output_dir / f"e2_f2_q0.99_refine_ray_{ray_index:04d}_summary.json"


def _base_report_path(precheck_aggregate_path: Path, ray_index: int) -> Path:
    return precheck_aggregate_path.parent / f"e2_f2_q0.99_precheck_ray_{ray_index:04d}_summary.json"


def _extension_report_path(precheck_aggregate: Dict[str, Any], ray_index: int) -> Path:
    review = precheck_aggregate.get("extension_review", {})
    targeted_path = Path(str(review.get("targeted_aggregate_path", "")))
    return targeted_path.parent / f"e2_f2_q0.99_precheck_upper_extension_ray_{ray_index:04d}_summary.json"


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _source_report_path(
    precheck_aggregate_path: Path,
    precheck_aggregate: Dict[str, Any],
    ray_index: int,
) -> Path:
    if ray_index in EXTENSION_TARGET_INDICES:
        path = _extension_report_path(precheck_aggregate, ray_index)
    else:
        review = precheck_aggregate.get("extension_review", {})
        base_aggregate_path = Path(str(review.get("base_aggregate_path", "")))
        if not base_aggregate_path.is_file():
            raise FileNotFoundError(
                "base aggregate referenced by extension review is missing: "
                + str(base_aggregate_path)
            )
        path = _base_report_path(base_aggregate_path, ray_index)
    return path


def validate_refinement_inputs(
    base_config_path: Path,
    refinement_config_path: Path,
    precheck_aggregate_path: Path,
) -> Dict[str, Any]:
    """校验 q0.99 细化只能从完整合并预检和固定身份开始。"""
    config = _load_json(base_config_path)
    base_validation = validate_config(config)
    refinement = _load_json(refinement_config_path)
    if refinement.get("refinement_id") != "E2-F2-Q099-REFINEMENT-V1":
        raise ValueError("unexpected q0.99 refinement identity")
    if str(refinement.get("frequency_label")) != FREQUENCY_LABEL:
        raise ValueError("refinement is frozen to q0.99")
    acceptance = refinement.get("acceptance", {})
    if int(acceptance.get("expected_ray_count", -1)) != EXPECTED_RAY_COUNT:
        raise ValueError("refinement expected ray count must be 72")
    reuse = refinement.get("reuse_policy", {})
    if reuse.get("reuse_merged_f2_precheck_cases") is not True:
        raise ValueError("refinement must reuse merged F2 precheck cases")
    if reuse.get("reuse_f1_center_cases") is not False:
        raise ValueError("refinement must not reuse F1 center cases")
    if reuse.get("refine_forcing_and_joint_labels_separately") is not True:
        raise ValueError("forcing and joint labels must be refined separately")
    if not precheck_aggregate_path.is_file():
        raise FileNotFoundError(precheck_aggregate_path)
    aggregate = _load_json(precheck_aggregate_path)
    if aggregate.get("status") != "complete" or aggregate.get("precheck_gate_pass") is not True:
        raise ValueError("merged q0.99 precheck must be complete and pass its gate")
    if aggregate.get("mode") != "precheck" or aggregate.get("frequency_label") != FREQUENCY_LABEL:
        raise ValueError("precheck aggregate must be q0.99 precheck")
    if int(aggregate.get("expected_ray_count", -1)) != EXPECTED_RAY_COUNT or int(aggregate.get("observed_ray_count", -1)) != EXPECTED_RAY_COUNT:
        raise ValueError("precheck aggregate must contain all 72 rays")
    issues = aggregate.get("gating_issues", {})
    if issues.get("center_unsafe_ray_indices"):
        raise ValueError("precheck has center-unsafe rays")
    if issues.get("forcing_bracket_missing_ray_indices"):
        raise ValueError("precheck has missing forcing brackets")
    if issues.get("joint_bracket_missing_ray_indices"):
        raise ValueError("precheck has missing joint brackets")
    if issues.get("solver_failure_ray_indices"):
        raise ValueError("precheck has solver failures")
    identity = aggregate.get("shared_identity", {})
    config_hash = sha256(base_config_path)
    if identity.get("config_sha256") != config_hash:
        raise ValueError("precheck config identity does not match base config")
    runner_hash = sha256(FLEXIBILITY_ROOT / "run_e2_f2_cross_operating.py")
    if identity.get("f2_runner_sha256") != runner_hash:
        raise ValueError("precheck F2 runner identity drifted")
    extension_review = aggregate.get("extension_review", {})
    if sorted(int(value) for value in extension_review.get("target_ray_indices", [])) != list(EXTENSION_TARGET_INDICES):
        raise ValueError("precheck extension review does not cover the frozen nine-ray target")
    rays = expand_rays(config, FREQUENCY_LABEL)
    if len(rays) != EXPECTED_RAY_COUNT:
        raise ValueError("q0.99 expansion must contain 72 rays")
    missing_reports = []
    for index in range(EXPECTED_RAY_COUNT):
        path = _source_report_path(precheck_aggregate_path, aggregate, index)
        if not path.is_file():
            missing_reports.append(str(path))
    if missing_reports:
        raise FileNotFoundError("missing per-ray precheck reports: " + ", ".join(missing_reports))
    return {
        "pass": True,
        "base_validation": base_validation,
        "refinement_id": refinement["refinement_id"],
        "expected_ray_count": EXPECTED_RAY_COUNT,
        "config_sha256": config_hash,
        "precheck_aggregate_sha256": sha256(precheck_aggregate_path),
        "f2_runner_sha256": runner_hash,
    }


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


def _load_precheck_result(
    precheck_aggregate_path: Path,
    precheck_aggregate: Dict[str, Any],
    ray_index: int,
) -> Tuple[Path, Dict[str, Any]]:
    path = _source_report_path(precheck_aggregate_path, precheck_aggregate, ray_index)
    report = _load_json(path)
    if report.get("mode") != "precheck" or report.get("frequency_label") != FREQUENCY_LABEL:
        raise ValueError(f"source report identity mismatch: {path}")
    results = report.get("ray_results", [])
    if len(results) != 1 or int(results[0].get("wave_ray_index", -1)) != ray_index:
        raise ValueError(f"source report ray mismatch: {path}")
    return path, results[0]


def run_single_ray(
    base_config_path: Path,
    refinement_config_path: Path,
    precheck_aggregate_path: Path,
    output_dir: Path,
    ray_index: int,
    config: Optional[Dict[str, Any]] = None,
    refinement: Optional[Dict[str, Any]] = None,
    precheck_aggregate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """复用预检案例，并对强迫/联合两个标签分别进行二分。"""
    config = config or _load_json(base_config_path)
    refinement = refinement or _load_json(refinement_config_path)
    precheck_aggregate = precheck_aggregate or _load_json(precheck_aggregate_path)
    validation = validate_refinement_inputs(
        base_config_path, refinement_config_path, precheck_aggregate_path
    )
    if ray_index < 0 or ray_index >= EXPECTED_RAY_COUNT:
        raise ValueError(f"ray-index must be in [0, {EXPECTED_RAY_COUNT - 1}]")

    source_report_path, precheck_result = _load_precheck_result(
        precheck_aggregate_path, precheck_aggregate, ray_index
    )
    ray = expand_rays(config, FREQUENCY_LABEL)[ray_index]
    source_ray = precheck_result["ray"]
    if source_ray != ray:
        raise ValueError(f"source ray differs from registered expansion: {ray_index}")
    initial_rows = list(precheck_result["evaluations"])
    rows = list(initial_rows)
    row_by_amplitude = {
        f"{float(row['amplitude_pu']):.12f}": row for row in rows
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)
    core_provenance, core_bundle_hash = code_bundle_provenance()
    expected_core = str(config["identity_policy"]["expected_core_code_bundle_sha256"])
    if core_bundle_hash != expected_core:
        raise RuntimeError(
            f"core code bundle identity drift: expected {expected_core}, got {core_bundle_hash}"
        )
    f2_runner_hash = sha256(FLEXIBILITY_ROOT / "run_e2_f2_cross_operating.py")
    refinement_runner_hash = sha256(Path(__file__).resolve())
    params, initial_conditions, operating_state, observation = prepare_operating_point(
        ray["power_pu"], ray["soc"]
    )
    frequency_spec = {
        "frequency_hz": ray["frequency_hz"],
        "cycles": ray["cycles"],
        "evidence_class": ray["evidence_class"],
    }

    def evaluate(amplitude: float) -> Dict[str, Any]:
        key = f"{float(amplitude):.12f}"
        if key in row_by_amplitude:
            return row_by_amplitude[key]
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
            "q099_refinement",
        )
        row_by_amplitude[key] = row
        rows.append(row)
        return row

    forcing_coarse = _first_failure_bracket(rows, FORCING_LABEL)
    joint_coarse = _first_failure_bracket(rows, JOINT_LABEL)
    tolerance = float(config["bisection_tolerance_pu"])
    max_iterations = int(config["max_bisection_iterations"])
    forcing_final = _refine_boundary(
        FORCING_LABEL,
        forcing_coarse,
        evaluate,
        tolerance,
        max_iterations,
        rows,
    )
    joint_coarse_after_forcing = _first_failure_bracket(rows, JOINT_LABEL)
    joint_final = _refine_boundary(
        JOINT_LABEL,
        joint_coarse_after_forcing,
        evaluate,
        tolerance,
        max_iterations,
        rows,
    )
    ordered_rows = _sorted_unique_rows(rows)
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
        "precheck_source_report_path": str(source_report_path.resolve()),
        "precheck_evaluation_count": len(initial_rows),
        "coarse_scan": {
            "amplitude_grid_pu": [float(row["amplitude_pu"]) for row in _sorted_unique_rows(initial_rows)],
            "evaluation_count": len(_sorted_unique_rows(initial_rows)),
            "forcing_stage": forcing_coarse,
            "joint_recovery": joint_coarse,
        },
        "forcing_stage_boundary": forcing_final,
        "joint_recovery_complete_boundary": joint_final,
        "solver_failure_total": solver_failure_total,
        "recovery_phase_physical_violation_case_files": recovery_physical_violations,
        "evaluations": ordered_rows,
    }
    report = {
        "study_id": config["study_id"],
        "scope": config["scope"],
        "mode": MODE,
        "frequency_label": FREQUENCY_LABEL,
        "config_path": str(base_config_path.resolve()),
        "config_sha256": sha256(base_config_path),
        "precheck_aggregate_path": str(precheck_aggregate_path.resolve()),
        "precheck_aggregate_sha256": sha256(precheck_aggregate_path),
        "refinement_config_path": str(refinement_config_path.resolve()),
        "refinement_config_sha256": sha256(refinement_config_path),
        "core_code_provenance": core_provenance,
        "core_code_bundle_sha256": core_bundle_hash,
        "f2_runner_sha256": f2_runner_hash,
        "refinement_runner_sha256": refinement_runner_hash,
        "preparation_gate": validation,
        "ray_count_total": EXPECTED_RAY_COUNT,
        "ray_count_in_report": 1,
        "refinement_id": refinement["refinement_id"],
        "ray_results": [result],
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    report_path = _report_path(output_dir, ray_index)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _acquire_lock(output_dir: Path, base_config_path: Path, refinement_config_path: Path) -> Path:
    lock_path = output_dir / "RUNNING.lock"
    payload = {
        "pid": os.getpid(),
        "base_config_path": str(base_config_path.resolve()),
        "refinement_config_path": str(refinement_config_path.resolve()),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"q0.99 refinement lock exists: {lock_path}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return lock_path


def run_parallel(
    base_config_path: Path,
    refinement_config_path: Path,
    precheck_aggregate_path: Path,
    output_dir: Path,
    workers: int,
) -> Dict[str, Any]:
    """以射线为单位并行细化，随后写入72条聚合。"""
    if workers < 1:
        raise ValueError("workers must be positive")
    config = _load_json(base_config_path)
    refinement = _load_json(refinement_config_path)
    precheck_aggregate = _load_json(precheck_aggregate_path)
    validation = validate_refinement_inputs(
        base_config_path, refinement_config_path, precheck_aggregate_path
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "process_logs").mkdir(parents=True, exist_ok=True)
    lock_path = _acquire_lock(output_dir, base_config_path, refinement_config_path)
    try:
        report_paths = {
            index: _report_path(output_dir, index) for index in range(EXPECTED_RAY_COUNT)
        }
        pending = [index for index, path in report_paths.items() if not path.is_file()]

        def run(index: int) -> Tuple[int, int, Path]:
            log_path = output_dir / "process_logs" / f"q0.99_refine_ray_{index:04d}.log"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(base_config_path.resolve()),
                "--refinement-config",
                str(refinement_config_path.resolve()),
                "--precheck-aggregate",
                str(precheck_aggregate_path.resolve()),
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
        observed_paths = [path for path in report_paths.values() if path.is_file()]
        parallel_summary = {
            "status": "complete" if not failures and len(observed_paths) == EXPECTED_RAY_COUNT else "incomplete",
            "frequency_label": FREQUENCY_LABEL,
            "mode": MODE,
            "expected_ray_count": EXPECTED_RAY_COUNT,
            "pending_at_start": pending,
            "failed_ray_indices": sorted(failures),
            "observed_summary_count": len(observed_paths),
        }
        (output_dir / "parallel_q0.99_refine_summary.json").write_text(
            json.dumps(parallel_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if failures or len(observed_paths) != EXPECTED_RAY_COUNT:
            raise RuntimeError(f"q0.99 refinement incomplete: {parallel_summary}")
        reports = [_load_json(report_paths[index]) for index in range(EXPECTED_RAY_COUNT)]
        aggregate = aggregate_reports(reports, config, FREQUENCY_LABEL, MODE)
        aggregate["refinement_review"] = {
            "refinement_id": refinement["refinement_id"],
            "precheck_aggregate_path": str(precheck_aggregate_path.resolve()),
            "precheck_aggregate_sha256": sha256(precheck_aggregate_path),
            "refinement_config_path": str(refinement_config_path.resolve()),
            "refinement_config_sha256": sha256(refinement_config_path),
            "refinement_runner_sha256": sha256(Path(__file__).resolve()),
            "reused_merged_f2_precheck_cases": True,
            "reused_f1_center_cases": False,
            "forcing_and_joint_refined_separately": True,
        }
        aggregate_path = output_dir / "e2_f2_q0.99_refine_aggregate.json"
        aggregate_path.write_text(
            json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
        if aggregate.get("refinement_gate_pass") is not True:
            raise RuntimeError(
                "q0.99 refinement aggregate failed gates: "
                + json.dumps(aggregate.get("gating_issues", {}), ensure_ascii=False)
            )
        return aggregate
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--refinement-config", type=Path, required=True)
    parser.add_argument("--precheck-aggregate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ray-index", type=int)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    base_config_path = args.config.resolve()
    refinement_config_path = args.refinement_config.resolve()
    precheck_aggregate_path = args.precheck_aggregate.resolve()
    output_dir = args.output_dir.resolve()
    config = _load_json(base_config_path)
    refinement = _load_json(refinement_config_path)
    precheck_aggregate = _load_json(precheck_aggregate_path)
    validate_refinement_inputs(
        base_config_path, refinement_config_path, precheck_aggregate_path
    )
    if args.ray_index is None:
        run_parallel(
            base_config_path,
            refinement_config_path,
            precheck_aggregate_path,
            output_dir,
            args.workers,
        )
    else:
        report = run_single_ray(
            base_config_path,
            refinement_config_path,
            precheck_aggregate_path,
            output_dir,
            args.ray_index,
            config,
            refinement,
            precheck_aggregate,
        )
        result = report["ray_results"][0]
        print(
            json.dumps(
                {
                    "ray_index": args.ray_index,
                    "forcing_stage": result["forcing_stage_boundary"],
                    "joint_recovery": result["joint_recovery_complete_boundary"],
                    "evaluation_count": len(result["evaluations"]),
                    "summary": str(_report_path(output_dir, args.ray_index)),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
