# -*- coding: utf-8 -*-
"""执行 E2-F2 跨工况慢域射线的逐射线括区审计与分层边界搜索。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


MODES = ("precheck", "refine")
FORCING_LABEL = "forcing_stage_safe"
JOINT_LABEL = "joint_valid_for_boundary"


def _same_number(left: Any, right: Any) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-15)


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _frequency_labels(config: Dict[str, Any]) -> List[str]:
    return [str(item["label"]) for item in config["frequencies"]]


def _frequency_spec(config: Dict[str, Any], label: str) -> Dict[str, Any]:
    matches = [item for item in config["frequencies"] if str(item["label"]) == label]
    if len(matches) != 1:
        raise ValueError(f"frequency label must identify one entry: {label}")
    return matches[0]


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """校验 F2 矩阵、逐射线括区和冻结核心代码身份。"""
    if config.get("study_id") != "E2-F2-CROSS-OPERATING-SLOW-V1":
        raise ValueError("unexpected E2-F2 study_id")
    if config.get("input_definition", {}).get("kind") != "net_load_reference":
        raise ValueError("E2-F2 is frozen to D_ref net_load_reference")

    operating_points = config.get("operating_points", [])
    expected_points = {
        (0.8, 0.2),
        (0.8, 0.5),
        (0.8, 0.8),
        (0.9, 0.2),
        (0.9, 0.5),
        (0.9, 0.8),
        (1.0, 0.2),
        (1.0, 0.5),
        (1.0, 0.8),
    }
    point_values = {
        (float(item["power_pu"]), float(item["soc"])) for item in operating_points
    }
    if point_values != expected_points:
        raise ValueError(f"operating_points must be the registered 3x3 matrix: {point_values}")
    if len(operating_points) != len(point_values):
        raise ValueError("operating_points must be unique")

    controllers = [str(value) for value in config.get("controllers", [])]
    if controllers != ["PID", "MPC"]:
        raise ValueError("controllers must remain [PID, MPC]")
    phases = [float(value) for value in config.get("phases_rad", [])]
    expected_phases = [0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0]
    if len(phases) != 4 or any(
        not any(_same_number(value, expected) for value in phases)
        for expected in expected_phases
    ):
        raise ValueError("phases_rad must contain 0, pi/2, pi and 3pi/2")
    if len(set(phases)) != 4:
        raise ValueError("phases_rad must be unique")

    frequencies = config.get("frequencies", [])
    labels = _frequency_labels(config)
    if labels != ["q0.99", "q0.95", "q0.90"]:
        raise ValueError("frequencies must remain ordered as q0.99, q0.95, q0.90")
    values = [float(item["frequency_hz"]) for item in frequencies]
    if len(values) != 3 or len(values) != len(set(values)):
        raise ValueError("frequencies must contain three unique entries")
    if any(float(item["cycles"]) < 1.0 for item in frequencies):
        raise ValueError("each frequency requires at least one cycle")

    amplitudes = sorted(float(value) for value in config.get("amplitude_grid_pu", []))
    if not amplitudes or amplitudes[0] != 0.0 or len(amplitudes) != len(set(amplitudes)):
        raise ValueError("amplitude_grid_pu must be unique and start at zero")
    if any(value < 0.0 for value in amplitudes):
        raise ValueError("amplitudes must be non-negative")
    tolerance = float(config["bisection_tolerance_pu"])
    if tolerance <= 0.0:
        raise ValueError("bisection_tolerance_pu must be positive")
    if int(config["max_bisection_iterations"]) < 1:
        raise ValueError("max_bisection_iterations must be positive")

    simulation = config["simulation"]
    recovery = simulation["recovery"]
    if float(simulation["dt_s"]) <= 0.0 or float(simulation["warmup_s"]) < 0.0:
        raise ValueError("dt_s must be positive and warmup_s non-negative")
    if float(recovery["duration_s"]) <= 0.0 or float(recovery["sustain_s"]) <= 0.0:
        raise ValueError("recovery duration and sustain window must be positive")
    if float(config["system_scaling"]["model_system_base_mw"]) <= 0.0:
        raise ValueError("model_system_base_mw must be positive")
    mapping_file = _resolve_project_path(str(config["system_scaling"]["mapping_file"]))
    if not mapping_file.is_file():
        raise FileNotFoundError(mapping_file)

    expected_total = len(operating_points) * len(frequencies) * len(phases) * len(controllers)
    expected_wave = len(operating_points) * len(phases) * len(controllers)
    acceptance = config["acceptance"]
    if int(acceptance["expected_total_ray_count"]) != expected_total:
        raise ValueError("expected_total_ray_count does not match matrix expansion")
    if int(acceptance["expected_wave_ray_count"]) != expected_wave:
        raise ValueError("expected_wave_ray_count does not match wave expansion")
    identity = config.get("identity_policy", {})
    if identity.get("reuse_f1_center_cases") is not False:
        raise ValueError("F2 must not merge F1 center cases")
    if identity.get("f1_cases_are_reference_only") is not True:
        raise ValueError("F1 cases must remain reference-only")

    return {
        "pass": True,
        "operating_point_count": len(operating_points),
        "total_ray_count": expected_total,
        "wave_ray_count": expected_wave,
        "frequency_labels": labels,
        "amplitude_grid_count": len(amplitudes),
        "bisection_tolerance_pu": tolerance,
    }


def expand_rays(config: Dict[str, Any], frequency_label: Optional[str] = None) -> List[Dict[str, Any]]:
    """按正式顺序展开全矩阵或一个频率波次的射线。"""
    selected_label = None if frequency_label is None else str(frequency_label)
    if selected_label is not None:
        _frequency_spec(config, selected_label)
    all_rays: List[Dict[str, Any]] = []
    for point in config["operating_points"]:
        for frequency in config["frequencies"]:
            if selected_label is not None and str(frequency["label"]) != selected_label:
                continue
            for phase_rad in config["phases_rad"]:
                for controller in config["controllers"]:
                    all_rays.append(
                        {
                            "power_pu": float(point["power_pu"]),
                            "soc": float(point["soc"]),
                            "frequency_label": str(frequency["label"]),
                            "frequency_hz": float(frequency["frequency_hz"]),
                            "cycles": float(frequency["cycles"]),
                            "evidence_class": str(frequency["evidence_class"]),
                            "phase_rad": float(phase_rad),
                            "controller": str(controller),
                        }
                    )
    return all_rays


def _build_case_config(config: Dict[str, Any], ray: Dict[str, Any], amplitude: float) -> Dict[str, Any]:
    return {
        "study_id": config["study_id"],
        "operating_point": {
            "nuclear_power_pu": ray["power_pu"],
            "bess_soc": ray["soc"],
        },
        "amplitude_pu": float(amplitude),
        "phase_rad": ray["phase_rad"],
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "simulation": config["simulation"],
        "mpc": config["mpc"],
        "constraints": config["constraints"],
    }


def _case_row(case_path: Path, case: Dict[str, Any], source: str) -> Dict[str, Any]:
    case_config = case["case_config"]
    constraints = case["constraints"]
    return {
        "case_file": str(case_path.resolve()),
        "case_hash": str(case["case_hash"]),
        "source": source,
        "amplitude_pu": float(case_config["amplitude_pu"]),
        FORCING_LABEL: bool(forcing_stage_safe(case)),
        "full_horizon_physical_safe": bool(constraints["safe"]),
        JOINT_LABEL: bool(constraints["valid_for_boundary"]),
        "recovery_complete": bool(constraints["recovery"]["complete"]),
        "solver_failures": int(constraints["metrics"]["solver_failures"]),
        "active_constraint": constraints["active_constraint"],
        "first_violation_time_s": constraints["first_violation_time_s"],
        "first_violation_phase": constraints["first_violation_phase"],
    }


def _first_failure_bracket(rows: Iterable[Dict[str, Any]], label: str) -> Dict[str, Any]:
    """从一条射线的粗扫标签中提取首次安全—失效括区。"""
    ordered = sorted(rows, key=lambda item: float(item["amplitude_pu"]))
    if not ordered or not _same_number(ordered[0]["amplitude_pu"], 0.0):
        raise ValueError("each per-ray coarse scan must include zero amplitude")
    if len({float(item["amplitude_pu"]) for item in ordered}) != len(ordered):
        raise ValueError("duplicate amplitudes in one per-ray coarse scan")
    values = [bool(item[label]) for item in ordered]
    transitions = []
    for previous, current in zip(ordered[:-1], ordered[1:]):
        previous_safe = bool(previous[label])
        current_safe = bool(current[label])
        if previous_safe != current_safe:
            transitions.append(
                {
                    "lower_amplitude_pu": float(previous["amplitude_pu"]),
                    "upper_amplitude_pu": float(current["amplitude_pu"]),
                    "from_safe": previous_safe,
                    "to_safe": current_safe,
                }
            )
    first_unsafe_index = next((index for index, value in enumerate(values) if not value), None)
    first_failure = next(
        (
            item
            for item in transitions
            if item["from_safe"] and not item["to_safe"]
        ),
        None,
    )
    non_star = bool(
        len(transitions) > 1
        or (
            first_unsafe_index is not None
            and any(values[first_unsafe_index + 1 :])
        )
    )
    if not values[0]:
        status = "unsafe_at_center"
    elif first_failure is not None:
        status = "bracketed_first_failure"
    else:
        status = "right_censored_at_search_upper"
    return {
        "label": label,
        "status": status,
        "center_safe": bool(values[0]),
        "all_safe": bool(all(values)),
        "first_failure_bracket": first_failure,
        "transitions": transitions,
        "non_star_shaped": non_star,
        "search_upper_amplitude_pu": float(ordered[-1]["amplitude_pu"]),
    }


def _refine_boundary(
    label: str,
    coarse_boundary: Dict[str, Any],
    evaluate,
    tolerance: float,
    max_iterations: int,
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    bracket = coarse_boundary["first_failure_bracket"]
    if bracket is None:
        return {
            **coarse_boundary,
            "conservative_safe_amplitude_pu": (
                None if coarse_boundary["status"] == "unsafe_at_center" else coarse_boundary["search_upper_amplitude_pu"]
            ),
            "first_failure_amplitude_pu": (
                0.0 if coarse_boundary["status"] == "unsafe_at_center" else None
            ),
            "bracket_width_pu": None,
            "tolerance_pu": tolerance,
            "precision_status": "not_bracketed",
            "bisection_iterations": 0,
        }
    lower = float(bracket["lower_amplitude_pu"])
    upper = float(bracket["upper_amplitude_pu"])
    iterations = 0
    while upper - lower > tolerance:
        if iterations >= max_iterations:
            raise RuntimeError(f"{label} bisection iteration cap reached")
        midpoint = 0.5 * (lower + upper)
        row = evaluate(midpoint)
        if bool(row[label]):
            lower = midpoint
        else:
            upper = midpoint
        iterations += 1
    final_rows = list(rows)
    final_boundary = _first_failure_bracket(final_rows, label)
    return {
        **final_boundary,
        "conservative_safe_amplitude_pu": lower,
        "first_failure_amplitude_pu": upper,
        "bracket_width_pu": upper - lower,
        "tolerance_pu": tolerance,
        "precision_status": (
            "within_frozen_tolerance"
            if upper - lower <= tolerance
            else "coarse_bracket_needs_refinement"
        ),
        "bisection_iterations": iterations,
    }


def run_single_ray(
    config_path: Path,
    output_dir: Path,
    frequency_label: str,
    wave_ray_index: int,
    mode: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = config or json.loads(config_path.read_text(encoding="utf-8"))
    validation = validate_config(config)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    rays = expand_rays(config, frequency_label)
    if wave_ray_index < 0 or wave_ray_index >= len(rays):
        raise ValueError(f"ray-index must be in [0, {len(rays) - 1}]")

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    core_provenance, core_bundle_hash = code_bundle_provenance()
    expected_core = str(config["identity_policy"]["expected_core_code_bundle_sha256"])
    if core_bundle_hash != expected_core:
        raise RuntimeError(
            f"core code bundle identity drift: expected {expected_core}, got {core_bundle_hash}"
        )
    runner_hash = sha256(Path(__file__).resolve())
    metrics_hash = core_provenance["metrics_source"]
    ray = rays[wave_ray_index]
    params, initial_conditions, operating_state, observation = prepare_operating_point(
        ray["power_pu"], ray["soc"]
    )
    frequency_spec = {
        "frequency_hz": ray["frequency_hz"],
        "cycles": ray["cycles"],
        "evidence_class": ray["evidence_class"],
    }
    amplitudes = sorted(float(value) for value in config["amplitude_grid_pu"])
    cases_by_amplitude: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []

    def evaluate(amplitude: float) -> Dict[str, Any]:
        key = f"{float(amplitude):.12f}"
        if key not in cases_by_amplitude:
            case = run_case(
                ray["controller"],
                frequency_spec,
                _build_case_config(config, ray, amplitude),
                params,
                initial_conditions,
                operating_state,
                output_dir,
                core_bundle_hash,
                metrics_hash,
            )
            cases_by_amplitude[key] = case
        case = cases_by_amplitude[key]
        row = _case_row(Path(case["npz"]).with_suffix(".json"), case, "f2_per_ray")
        if not any(item["case_hash"] == row["case_hash"] for item in rows):
            rows.append(row)
        return row

    for amplitude in amplitudes:
        evaluate(amplitude)
    forcing_coarse = _first_failure_bracket(rows, FORCING_LABEL)
    joint_coarse = _first_failure_bracket(rows, JOINT_LABEL)
    forcing_final = forcing_coarse
    joint_final = joint_coarse
    if mode == "refine":
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
        joint_final = _refine_boundary(
            JOINT_LABEL,
            joint_coarse,
            evaluate,
            tolerance,
            max_iterations,
            rows,
        )

    ordered_rows = sorted(rows, key=lambda item: item["amplitude_pu"])
    solver_failure_total = int(sum(item["solver_failures"] for item in ordered_rows))
    recovery_physical_violations = sorted(
        item["case_file"]
        for item in ordered_rows
        if item[FORCING_LABEL] and not item["full_horizon_physical_safe"]
    )
    result = {
        "wave_ray_index": wave_ray_index,
        "ray": ray,
        "operating_observation": {
            "electrical_power_pu": float(observation["p_e_pu"]),
            "frequency_pu": float(observation["frequency_pu"]),
        },
        "coarse_scan": {
            "amplitude_grid_pu": amplitudes,
            "evaluation_count": len(ordered_rows),
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
        "mode": mode,
        "frequency_label": frequency_label,
        "config_path": str(config_path.resolve()),
        "config_sha256": sha256(config_path),
        "core_code_provenance": core_provenance,
        "core_code_bundle_sha256": core_bundle_hash,
        "f2_runner_sha256": runner_hash,
        "preparation_gate": validation,
        "ray_count_total": len(rays),
        "ray_count_in_report": 1,
        "ray_results": [result],
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    suffix = f"{frequency_label}_{mode}_ray_{wave_ray_index:04d}"
    report_path = output_dir / f"e2_f2_{suffix}_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _report_path(output_dir: Path, frequency_label: str, mode: str, index: int) -> Path:
    return output_dir / f"e2_f2_{frequency_label}_{mode}_ray_{index:04d}_summary.json"


def aggregate_reports(
    reports: List[Dict[str, Any]],
    config: Dict[str, Any],
    frequency_label: str,
    mode: str,
) -> Dict[str, Any]:
    """聚合一个频率波次的逐射线括区，区分预检门和细化门。"""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    expected = int(config["acceptance"]["expected_wave_ray_count"])
    if len(reports) == 0:
        raise ValueError("no F2 reports found")
    expected_counts = {int(report["ray_count_total"]) for report in reports}
    if expected_counts != {expected}:
        raise ValueError(f"ray_count_total is inconsistent: {expected_counts}")
    identity_fields = (
        "study_id",
        "mode",
        "frequency_label",
        "config_sha256",
        "core_code_bundle_sha256",
        "f2_runner_sha256",
    )
    shared_identity: Dict[str, Any] = {}
    for field in identity_fields:
        values = {
            json.dumps(report.get(field), ensure_ascii=False, sort_keys=True)
            for report in reports
        }
        if len(values) != 1:
            raise ValueError(f"{field} is inconsistent across reports")
        shared_identity[field] = reports[0].get(field)
    if shared_identity["frequency_label"] != frequency_label or shared_identity["mode"] != mode:
        raise ValueError("report frequency or mode does not match aggregate request")

    by_index: Dict[int, Dict[str, Any]] = {}
    duplicate_indices: List[int] = []
    for report in reports:
        if len(report.get("ray_results", [])) != 1:
            raise ValueError("each report must contain exactly one ray")
        result = report["ray_results"][0]
        index = int(result["wave_ray_index"])
        if index in by_index:
            duplicate_indices.append(index)
        by_index[index] = result
    missing_indices = sorted(set(range(expected)) - set(by_index))

    rows = []
    for index in sorted(by_index):
        result = by_index[index]
        forcing = result["forcing_stage_boundary"]
        joint = result["joint_recovery_complete_boundary"]
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
                "coarse_scan": result["coarse_scan"],
                "forcing_stage_boundary": forcing,
                "joint_recovery_complete_boundary": joint,
                "solver_failure_total": int(result["solver_failure_total"]),
                "recovery_phase_physical_violation_case_files": result[
                    "recovery_phase_physical_violation_case_files"
                ],
                "evaluation_count": len(result["evaluations"]),
            }
        )

    complete = not missing_indices and not duplicate_indices and len(rows) == expected
    center_unsafe = [
        row["wave_ray_index"]
        for row in rows
        if not row["coarse_scan"]["forcing_stage"]["center_safe"]
        or not row["coarse_scan"]["joint_recovery"]["center_safe"]
    ]
    forcing_missing_bracket = [
        row["wave_ray_index"]
        for row in rows
        if row["coarse_scan"]["forcing_stage"]["first_failure_bracket"] is None
    ]
    joint_missing_bracket = [
        row["wave_ray_index"]
        for row in rows
        if row["coarse_scan"]["joint_recovery"]["first_failure_bracket"] is None
    ]
    forcing_non_star = [
        row["wave_ray_index"]
        for row in rows
        if row["coarse_scan"]["forcing_stage"]["non_star_shaped"]
    ]
    joint_non_star = [
        row["wave_ray_index"]
        for row in rows
        if row["coarse_scan"]["joint_recovery"]["non_star_shaped"]
    ]
    solver_failure = [row["wave_ray_index"] for row in rows if row["solver_failure_total"] > 0]
    recovery_physical = [
        row["wave_ray_index"]
        for row in rows
        if row["recovery_phase_physical_violation_case_files"]
    ]
    if mode == "precheck":
        gate_pass = bool(
            complete
            and not center_unsafe
            and not forcing_missing_bracket
            and not solver_failure
        )
    else:
        coarse_width_failures = [
            row["wave_ray_index"]
            for row in rows
            if row["forcing_stage_boundary"].get("precision_status")
            != "within_frozen_tolerance"
            or row["joint_recovery_complete_boundary"].get("precision_status")
            != "within_frozen_tolerance"
        ]
        gate_pass = bool(
            complete
            and not center_unsafe
            and not coarse_width_failures
            and not solver_failure
            and not recovery_physical
        )
    return {
        "analysis_id": "E2-F2-CROSS-OPERATING-SLOW-V1",
        "status": "complete" if complete else "incomplete",
        "mode": mode,
        "frequency_label": frequency_label,
        "expected_ray_count": expected,
        "observed_ray_count": len(rows),
        "missing_ray_indices": missing_indices,
        "duplicate_ray_indices": sorted(set(duplicate_indices)),
        "shared_identity": shared_identity,
        "gating_issues": {
            "center_unsafe_ray_indices": center_unsafe,
            "forcing_bracket_missing_ray_indices": forcing_missing_bracket,
            "joint_bracket_missing_ray_indices": joint_missing_bracket,
            "forcing_non_star_ray_indices": forcing_non_star,
            "joint_non_star_ray_indices": joint_non_star,
            "solver_failure_ray_indices": solver_failure,
            "recovery_phase_physical_violation_ray_indices": recovery_physical,
        },
        "precheck_gate_pass" if mode == "precheck" else "refinement_gate_pass": gate_pass,
        "per_ray_bracket_policy": {
            "forcing_label": FORCING_LABEL,
            "joint_label": JOINT_LABEL,
            "recovery_completion_is_not_used_for_forcing_bisection": True,
            "f1_center_cases_merged": False,
        },
        "rays": rows,
    }


def _acquire_lock(output_dir: Path, config_path: Path, frequency_label: str, mode: str) -> Path:
    lock_path = output_dir / "RUNNING.lock"
    payload = {
        "pid": os.getpid(),
        "config_path": str(config_path.resolve()),
        "frequency_label": frequency_label,
        "mode": mode,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"F2 run lock exists: {lock_path}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return lock_path


def run_parallel(
    config_path: Path,
    output_dir: Path,
    frequency_label: str,
    mode: str,
    workers: int,
) -> Dict[str, Any]:
    """按射线并行执行一个频率波次，随后写入机器聚合。"""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if workers < 1:
        raise ValueError("workers must be positive")
    _frequency_spec(config, frequency_label)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "process_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _acquire_lock(output_dir, config_path, frequency_label, mode)
    try:
        rays = expand_rays(config, frequency_label)
        report_paths = [
            _report_path(output_dir, frequency_label, mode, index)
            for index in range(len(rays))
        ]
        pending = [index for index, path in enumerate(report_paths) if not path.is_file()]

        def run(index: int) -> Tuple[int, int, Path]:
            log_path = log_dir / f"{frequency_label}_{mode}_ray_{index:04d}.log"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(config_path.resolve()),
                "--output-dir",
                str(output_dir.resolve()),
                "--frequency-label",
                frequency_label,
                "--mode",
                mode,
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
            "status": (
                "complete"
                if not failures and len(observed_paths) == len(report_paths)
                else "incomplete"
            ),
            "frequency_label": frequency_label,
            "mode": mode,
            "expected_ray_count": len(report_paths),
            "pending_at_start": pending,
            "failed_ray_indices": sorted(failures),
            "observed_summary_count": len(observed_paths),
        }
        (output_dir / f"parallel_{frequency_label}_{mode}_summary.json").write_text(
            json.dumps(parallel_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if failures or len(observed_paths) != len(report_paths):
            raise RuntimeError(f"F2 wave incomplete: {parallel_summary}")
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
        aggregate = aggregate_reports(reports, config, frequency_label, mode)
        aggregate_path = output_dir / f"e2_f2_{frequency_label}_{mode}_aggregate.json"
        aggregate_path.write_text(
            json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
    parser.add_argument("--frequency-label", required=True)
    parser.add_argument("--mode", choices=MODES, default="precheck")
    parser.add_argument("--ray-index", type=int)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    if args.ray_index is None:
        run_parallel(
            config_path,
            output_dir,
            args.frequency_label,
            args.mode,
            args.workers,
        )
    else:
        report = run_single_ray(
            config_path,
            output_dir,
            args.frequency_label,
            args.ray_index,
            args.mode,
            config,
        )
        result = report["ray_results"][0]
        print(
            json.dumps(
                {
                    "frequency_label": args.frequency_label,
                    "mode": args.mode,
                    "ray_index": args.ray_index,
                    "forcing_stage": result["forcing_stage_boundary"],
                    "joint_recovery": result["joint_recovery_complete_boundary"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
