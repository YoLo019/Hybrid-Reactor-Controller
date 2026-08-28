# -*- coding: utf-8 -*-
"""定向细化E2强迫阶段物理边界，并保留恢复完成联合边界。"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from analyze_e2_layered_boundaries import forcing_stage_safe
from run_e2_formal import code_bundle_provenance
from run_e2_frequency_diagnostic import run_case
from run_e2_smoke import canonical_hash, prepare_operating_point, sha256


IDENTITY_FIELDS = (
    "study_id",
    "config_sha256",
    "core_code_bundle_sha256",
    "refinement_runner_sha256",
)


def _same_number(left: Any, right: Any) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-15)


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def expand_rays(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按频率、相位展开固定的六条MPC恢复主导射线。"""
    rays = []
    for frequency in config["frequencies"]:
        for phase_rad in config["phases_rad"]:
            rays.append(
                {
                    "controller": config["controller"],
                    "power_pu": float(config["operating_point"]["nuclear_power_pu"]),
                    "soc": float(config["operating_point"]["bess_soc"]),
                    "frequency_hz": float(frequency["frequency_hz"]),
                    "cycles": float(frequency["cycles"]),
                    "evidence_class": str(frequency["evidence_class"]),
                    "base_run_dir": str(frequency["base_run_dir"]),
                    "phase_rad": float(phase_rad),
                }
            )
    return rays


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """校验定向二分只覆盖预注册的六条恢复主导射线。"""
    if config.get("controller") != "MPC":
        raise ValueError("forcing refinement is frozen to MPC")
    if config.get("input_definition", {}).get("kind") != "net_load_reference":
        raise ValueError("forcing refinement is frozen to D_ref")
    phases = [float(value) for value in config.get("phases_rad", [])]
    if len(phases) != 2 or not (
        any(_same_number(value, 0.0) for value in phases)
        and any(_same_number(value, math.pi / 2.0) for value in phases)
    ):
        raise ValueError("phases_rad must contain exactly 0 and pi/2")
    frequencies = config.get("frequencies", [])
    values = [float(item["frequency_hz"]) for item in frequencies]
    if len(values) != 3 or len(values) != len(set(values)):
        raise ValueError("forcing refinement requires three unique frequencies")
    if any(float(item["cycles"]) < 1.0 for item in frequencies):
        raise ValueError("each frequency requires at least one cycle")

    bracket = config["forcing_bracket"]
    lower = float(bracket["lower_amplitude_pu"])
    upper = float(bracket["upper_amplitude_pu"])
    tolerance = float(config["bisection_tolerance_pu"])
    if not (0.0 < lower < upper):
        raise ValueError("forcing bracket must satisfy 0 < lower < upper")
    if tolerance <= 0.0 or upper - lower <= tolerance:
        raise ValueError("forcing bracket must be wider than the positive tolerance")

    simulation = config["simulation"]
    recovery = simulation["recovery"]
    if float(simulation["dt_s"]) <= 0.0 or float(simulation["warmup_s"]) < 0.0:
        raise ValueError("dt_s must be positive and warmup_s non-negative")
    if float(recovery["duration_s"]) <= 0.0 or float(recovery["sustain_s"]) <= 0.0:
        raise ValueError("recovery duration and sustain window must be positive")
    if float(config["system_scaling"]["model_system_base_mw"]) <= 0.0:
        raise ValueError("model_system_base_mw must be positive")

    expected = len(frequencies) * len(phases)
    if int(config["acceptance"]["expected_ray_count"]) != expected:
        raise ValueError("expected_ray_count does not match frequency/phase expansion")
    if len({str(item["base_run_dir"]) for item in frequencies}) != len(frequencies):
        raise ValueError("base_run_dir values must be unique")
    for item in frequencies:
        base_dir = _resolve_project_path(str(item["base_run_dir"]))
        if not (base_dir / "cases").is_dir():
            raise FileNotFoundError(base_dir / "cases")
    mapping_path = _resolve_project_path(str(config["system_scaling"]["mapping_file"]))
    if not mapping_path.is_file():
        raise FileNotFoundError(mapping_path)
    return {
        "pass": True,
        "ray_count": expected,
        "forcing_bracket_width_pu": upper - lower,
        "bisection_tolerance_pu": tolerance,
    }


def _case_row(case_path: Path, case: Dict[str, Any], source: str) -> Dict[str, Any]:
    """提取强迫安全、全时域物理安全和联合恢复安全三种标签。"""
    case_config = case["case_config"]
    constraints = case["constraints"]
    return {
        "case_file": str(case_path.resolve()),
        "case_hash": str(case["case_hash"]),
        "source": source,
        "amplitude_pu": float(case_config["amplitude_pu"]),
        "forcing_stage_safe": bool(forcing_stage_safe(case)),
        "full_horizon_physical_safe": bool(constraints["safe"]),
        "joint_valid_for_boundary": bool(constraints["valid_for_boundary"]),
        "recovery_complete": bool(constraints["recovery"]["complete"]),
        "solver_failures": int(constraints["metrics"]["solver_failures"]),
        "active_constraint": constraints["active_constraint"],
        "first_violation_time_s": constraints["first_violation_time_s"],
        "first_violation_phase": constraints["first_violation_phase"],
    }


def _find_base_case(
    config: Dict[str, Any], ray: Dict[str, Any], amplitude: float, core_bundle_hash: str
) -> Tuple[Path, Dict[str, Any]]:
    """从同身份正式波次中定位唯一的粗扫端点案例。"""
    base_dir = _resolve_project_path(ray["base_run_dir"])
    matches = []
    for path in sorted((base_dir / "cases").glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        case_config = case.get("case_config", {})
        if case_config.get("controller") != "MPC":
            continue
        if not _same_number(case_config.get("frequency_hz"), ray["frequency_hz"]):
            continue
        if not _same_number(case_config.get("phase_rad"), ray["phase_rad"]):
            continue
        if not _same_number(case_config.get("amplitude_pu"), amplitude):
            continue
        if case_config.get("runner_sha256") != core_bundle_hash:
            continue
        npz_path = Path(case.get("npz", ""))
        if not npz_path.is_file():
            npz_path = path.with_suffix(".npz")
        if not npz_path.is_file():
            continue
        matches.append((path.resolve(), case))
    if len(matches) != 1:
        raise ValueError(
            "expected one same-identity endpoint case for "
            f"f={ray['frequency_hz']}, phase={ray['phase_rad']}, A={amplitude}; "
            f"found {len(matches)} in {base_dir}"
        )
    return matches[0]


def _build_run_config(config: Dict[str, Any], ray: Dict[str, Any], amplitude: float) -> Dict[str, Any]:
    """把定向案例映射到现有频率诊断器的冻结输入契约。"""
    return {
        "study_id": config["study_id"],
        "operating_point": config["operating_point"],
        "amplitude_pu": float(amplitude),
        "phase_rad": float(ray["phase_rad"]),
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "simulation": config["simulation"],
        "mpc": config["mpc"],
        "constraints": config["constraints"],
    }


def run_single_ray(
    config_path: Path,
    output_dir: Path,
    ray_index: int,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """执行一条射线的强迫阶段二分，联合标签只作并列诊断。"""
    config = config or json.loads(config_path.read_text(encoding="utf-8"))
    validation = validate_config(config)
    rays = expand_rays(config)
    if ray_index < 0 or ray_index >= len(rays):
        raise ValueError(f"ray-index must be in [0, {len(rays) - 1}]")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)

    core_provenance, core_bundle_hash = code_bundle_provenance()
    refinement_runner_hash = sha256(Path(__file__).resolve())
    ray = rays[ray_index]
    bracket = config["forcing_bracket"]
    lower_amplitude = float(bracket["lower_amplitude_pu"])
    upper_amplitude = float(bracket["upper_amplitude_pu"])
    tolerance = float(config["bisection_tolerance_pu"])

    lower_path, lower_case = _find_base_case(
        config, ray, lower_amplitude, core_bundle_hash
    )
    upper_path, upper_case = _find_base_case(
        config, ray, upper_amplitude, core_bundle_hash
    )
    evaluations = [
        _case_row(lower_path, lower_case, "same_identity_wave_endpoint"),
        _case_row(upper_path, upper_case, "same_identity_wave_endpoint"),
    ]
    if not evaluations[0]["forcing_stage_safe"]:
        raise ValueError(f"lower forcing endpoint is unsafe for ray {ray_index}")
    if evaluations[1]["forcing_stage_safe"]:
        raise ValueError(f"upper forcing endpoint is safe for ray {ray_index}")

    params, initial_conditions, operating_state, observation = prepare_operating_point(
        ray["power_pu"], ray["soc"]
    )
    frequency_spec = {
        "frequency_hz": ray["frequency_hz"],
        "cycles": ray["cycles"],
        "evidence_class": ray["evidence_class"],
    }
    cases_by_amplitude = {
        f"{lower_amplitude:.12f}": lower_case,
        f"{upper_amplitude:.12f}": upper_case,
    }

    def evaluate(amplitude: float) -> Dict[str, Any]:
        key = f"{float(amplitude):.12f}"
        if key not in cases_by_amplitude:
            case = run_case(
                "MPC",
                frequency_spec,
                _build_run_config(config, ray, amplitude),
                params,
                initial_conditions,
                operating_state,
                output_dir,
                core_bundle_hash,
                core_provenance["metrics_source"],
            )
            cases_by_amplitude[key] = case
        case = cases_by_amplitude[key]
        path = (
            lower_path
            if case is lower_case
            else upper_path
            if case is upper_case
            else Path(case["npz"]).with_suffix(".json")
        )
        row = _case_row(path, case, "targeted_forcing_bisection")
        if not any(item["case_hash"] == row["case_hash"] for item in evaluations):
            evaluations.append(row)
        return row

    iterations = 0
    while upper_amplitude - lower_amplitude > tolerance:
        if iterations >= int(config["max_bisection_iterations"]):
            raise RuntimeError(f"bisection iteration cap reached for ray {ray_index}")
        midpoint = 0.5 * (lower_amplitude + upper_amplitude)
        midpoint_row = evaluate(midpoint)
        if midpoint_row["forcing_stage_safe"]:
            lower_amplitude = midpoint
        else:
            upper_amplitude = midpoint
        iterations += 1

    ordered = sorted(evaluations, key=lambda item: item["amplitude_pu"])
    first_unsafe = next(
        (index for index, item in enumerate(ordered) if not item["forcing_stage_safe"]),
        None,
    )
    non_star_shaped = bool(
        first_unsafe is not None
        and any(item["forcing_stage_safe"] for item in ordered[first_unsafe + 1 :])
    )
    solver_failure_rows = [
        item for item in ordered if int(item["solver_failures"]) > 0
    ]
    recovery_phase_physical_violations = [
        item["case_file"]
        for item in ordered
        if item["forcing_stage_safe"] and not item["full_horizon_physical_safe"]
    ]
    result = {
        "ray_index": ray_index,
        "ray": {
            "controller": ray["controller"],
            "power_pu": ray["power_pu"],
            "soc": ray["soc"],
            "frequency_hz": ray["frequency_hz"],
            "cycles": ray["cycles"],
            "evidence_class": ray["evidence_class"],
            "phase_rad": ray["phase_rad"],
        },
        "operating_observation": {
            "electrical_power_pu": float(observation["p_e_pu"]),
            "frequency_pu": float(observation["frequency_pu"]),
        },
        "initial_forcing_bracket": {
            "lower_amplitude_pu": float(bracket["lower_amplitude_pu"]),
            "upper_amplitude_pu": float(bracket["upper_amplitude_pu"]),
        },
        "final_forcing_boundary": {
            "conservative_safe_amplitude_pu": lower_amplitude,
            "first_failure_amplitude_pu": upper_amplitude,
            "bracket_width_pu": upper_amplitude - lower_amplitude,
            "tolerance_pu": tolerance,
            "precision_status": (
                "within_frozen_tolerance"
                if upper_amplitude - lower_amplitude <= tolerance
                else "coarse_bracket_needs_refinement"
            ),
        },
        "boundary_status": "bracketed_forcing_first_failure",
        "bisection_iterations": iterations,
        "forcing_stage_non_star_shaped": non_star_shaped,
        "solver_failure_total": int(sum(item["solver_failures"] for item in ordered)),
        "recovery_phase_physical_violation_case_files": sorted(
            recovery_phase_physical_violations
        ),
        "endpoint_cases": {
            "lower": _case_row(lower_path, lower_case, "same_identity_wave_endpoint"),
            "upper": _case_row(upper_path, upper_case, "same_identity_wave_endpoint"),
        },
        "evaluations": ordered,
    }
    report = {
        "study_id": config["study_id"],
        "scope": config["scope"],
        "refinement_target": config["refinement_target"],
        "config_path": str(config_path.resolve()),
        "config_sha256": sha256(config_path),
        "core_code_provenance": core_provenance,
        "core_code_bundle_sha256": core_bundle_hash,
        "refinement_runner_sha256": refinement_runner_hash,
        "preparation_gate": validation,
        "ray_count_total": len(rays),
        "ray_count_in_report": 1,
        "ray_results": [result],
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    report_path = output_dir / f"e2_forcing_refinement_ray_{ray_index:04d}_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def aggregate_reports(reports: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """汇总六条强迫边界并判定双层runner是否可冻结。"""
    if not reports:
        raise ValueError("no forcing refinement reports found")
    expected_count = int(config["acceptance"]["expected_ray_count"])
    expected_counts = {int(report["ray_count_total"]) for report in reports}
    if expected_counts != {expected_count}:
        raise ValueError(f"ray_count_total is inconsistent: {expected_counts}")
    identity = {}
    for field in IDENTITY_FIELDS:
        values = {json.dumps(report[field], ensure_ascii=False, sort_keys=True) for report in reports}
        if len(values) != 1:
            raise ValueError(f"{field} is inconsistent across reports")
        identity[field] = reports[0][field]

    rays_by_index: Dict[int, Dict[str, Any]] = {}
    duplicate_indices = []
    for report in reports:
        if len(report["ray_results"]) != 1:
            raise ValueError("each refinement report must contain exactly one ray")
        result = report["ray_results"][0]
        index = int(result["ray_index"])
        if index in rays_by_index:
            duplicate_indices.append(index)
        rays_by_index[index] = result
    missing_indices = sorted(set(range(expected_count)) - set(rays_by_index))

    rows = []
    for index in sorted(rays_by_index):
        result = rays_by_index[index]
        final = result["final_forcing_boundary"]
        rows.append(
            {
                "ray_index": index,
                "controller": result["ray"]["controller"],
                "frequency_hz": float(result["ray"]["frequency_hz"]),
                "phase_rad": float(result["ray"]["phase_rad"]),
                "power_pu": float(result["ray"]["power_pu"]),
                "soc": float(result["ray"]["soc"]),
                "initial_forcing_bracket": result["initial_forcing_bracket"],
                "safe_boundary_amplitude_pu": final["conservative_safe_amplitude_pu"],
                "first_failure_amplitude_pu": final["first_failure_amplitude_pu"],
                "boundary_width_pu": final["bracket_width_pu"],
                "tolerance_pu": final["tolerance_pu"],
                "precision_status": final["precision_status"],
                "bisection_iterations": int(result["bisection_iterations"]),
                "forcing_stage_non_star_shaped": bool(result["forcing_stage_non_star_shaped"]),
                "solver_failure_total": int(result["solver_failure_total"]),
                "recovery_phase_physical_violation_case_files": result[
                    "recovery_phase_physical_violation_case_files"
                ],
                "endpoint_cases": result["endpoint_cases"],
                "evaluation_count": len(result["evaluations"]),
            }
        )

    complete = (
        not missing_indices
        and not duplicate_indices
        and len(rows) == expected_count
    )
    coarse_rows = [
        row for row in rows if row["precision_status"] != "within_frozen_tolerance"
    ]
    non_star_rows = [row for row in rows if row["forcing_stage_non_star_shaped"]]
    solver_failure_rows = [row for row in rows if row["solver_failure_total"] > 0]
    recovery_violation_rows = [
        row for row in rows if row["recovery_phase_physical_violation_case_files"]
    ]
    endpoint_failures = [
        row["ray_index"]
        for row in rows
        if not row["endpoint_cases"]["lower"]["forcing_stage_safe"]
        or row["endpoint_cases"]["upper"]["forcing_stage_safe"]
    ]
    contract_ready = bool(
        complete
        and not coarse_rows
        and not non_star_rows
        and not solver_failure_rows
        and not recovery_violation_rows
        and not endpoint_failures
    )
    finite_boundaries = [
        float(row["safe_boundary_amplitude_pu"])
        for row in rows
        if row["safe_boundary_amplitude_pu"] is not None
    ]
    return {
        "analysis_id": "E2-F1-FORCING-BOUNDARY-REFINEMENT-V1",
        "status": "complete" if complete else "incomplete",
        "expected_ray_count": expected_count,
        "observed_ray_count": len(rows),
        "missing_ray_indices": missing_indices,
        "duplicate_ray_indices": sorted(set(duplicate_indices)),
        "shared_identity": identity,
        "gating_issues": {
            "coarse_bracket_ray_indices": [row["ray_index"] for row in coarse_rows],
            "forcing_non_star_ray_indices": [row["ray_index"] for row in non_star_rows],
            "solver_failure_ray_indices": [row["ray_index"] for row in solver_failure_rows],
            "recovery_phase_physical_violation_ray_indices": [
                row["ray_index"] for row in recovery_violation_rows
            ],
            "endpoint_validation_ray_indices": endpoint_failures,
        },
        "boundary_findings": {
            "all_forcing_boundaries_within_tolerance": not coarse_rows,
            "forcing_boundary_tolerance_pu": float(config["bisection_tolerance_pu"]),
            "minimum_safe_boundary_pu": (
                None if not finite_boundaries else float(np.min(finite_boundaries))
            ),
            "median_safe_boundary_pu": (
                None if not finite_boundaries else float(np.median(finite_boundaries))
            ),
            "maximum_safe_boundary_pu": (
                None if not finite_boundaries else float(np.max(finite_boundaries))
            ),
        },
        "dual_layer_runner_contract_ready": contract_ready,
        "contract_basis": {
            "forcing_stage_boundary": "only formal plant/device constraints before forcing_end_s",
            "joint_recovery_complete_boundary": "existing valid_for_boundary label, retained separately",
            "recovery_completion_is_not_used_for_forcing_bisection": True,
        },
        "rays": rows,
    }


def _write_machine_contract(
    output_dir: Path,
    config_path: Path,
    config: Dict[str, Any],
    aggregate: Dict[str, Any],
) -> Path:
    """写入机器冻结见证；正式说明文档由验收后落盘。"""
    if not aggregate["dual_layer_runner_contract_ready"]:
        raise ValueError("cannot freeze dual-layer runner before all acceptance gates pass")
    contract = {
        "contract_id": "E2-LAYERED-BOUNDARY-RUNNER-V1",
        "status": "frozen",
        "frozen_date": dt.date.today().isoformat(),
        "runner_file": str(Path(__file__).resolve()),
        "config_file": str(config_path.resolve()),
        "refinement_config_sha256": aggregate["shared_identity"]["config_sha256"],
        "core_code_bundle_sha256": aggregate["shared_identity"]["core_code_bundle_sha256"],
        "refinement_runner_sha256": aggregate["shared_identity"]["refinement_runner_sha256"],
        "aggregate_file": str(
            (output_dir / "e2_f1_forcing_boundary_refinement_aggregate.json").resolve()
        ),
        "acceptance": aggregate,
        "definitions": aggregate["contract_basis"],
    }
    path = output_dir / "e2_layered_boundary_runner_contract.json"
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _acquire_lock(output_dir: Path, config_path: Path) -> Path:
    lock_path = output_dir / "RUNNING.lock"
    payload = {
        "pid": os.getpid(),
        "config_path": str(config_path.resolve()),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"refinement run lock exists: {lock_path}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return lock_path


def run_parallel(config_path: Path, output_dir: Path, workers: int) -> Dict[str, Any]:
    """以射线为单位并行，父进程只在六条射线结束后聚合和冻结。"""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if workers < 1:
        raise ValueError("workers must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "process_logs").mkdir(parents=True, exist_ok=True)
    lock_path = _acquire_lock(output_dir, config_path)
    try:
        report_paths = [
            output_dir / f"e2_forcing_refinement_ray_{index:04d}_summary.json"
            for index in range(int(config["acceptance"]["expected_ray_count"]))
        ]
        pending = [index for index, path in enumerate(report_paths) if not path.is_file()]

        def run(index: int) -> Tuple[int, int, Path]:
            log_path = output_dir / "process_logs" / f"ray_{index:04d}.log"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(config_path.resolve()),
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

        observed_paths = [path for path in report_paths if path.is_file()]
        parallel_summary = {
            "status": "complete" if not failures and len(observed_paths) == len(report_paths) else "incomplete",
            "expected_ray_count": len(report_paths),
            "pending_at_start": pending,
            "failed_ray_indices": sorted(failures),
            "observed_summary_count": len(observed_paths),
        }
        (output_dir / "parallel_forcing_refinement_summary.json").write_text(
            json.dumps(parallel_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if failures or len(observed_paths) != len(report_paths):
            raise RuntimeError(f"forcing refinement incomplete: {parallel_summary}")

        reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
        aggregate = aggregate_reports(reports, config)
        aggregate_path = output_dir / "e2_f1_forcing_boundary_refinement_aggregate.json"
        aggregate_path.write_text(
            json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if aggregate["dual_layer_runner_contract_ready"]:
            _write_machine_contract(output_dir, config_path, config, aggregate)
        else:
            raise RuntimeError(
                "forcing refinement aggregate complete but dual-layer contract gates failed: "
                f"{aggregate['gating_issues']}"
            )
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
        return aggregate
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ray-index", type=int)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if args.ray_index is None:
        run_parallel(config_path, output_dir, args.workers)
    else:
        report = run_single_ray(config_path, output_dir, args.ray_index, config)
        print(
            json.dumps(
                {
                    "ray_index": args.ray_index,
                    "forcing_boundary": report["ray_results"][0]["final_forcing_boundary"],
                    "summary": str(
                        output_dir / f"e2_forcing_refinement_ray_{args.ray_index:04d}_summary.json"
                    ),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
