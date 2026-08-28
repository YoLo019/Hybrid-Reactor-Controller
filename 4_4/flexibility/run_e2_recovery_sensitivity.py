# -*- coding: utf-8 -*-
"""执行E2恢复时长敏感性矩阵，并复用已冻结的180秒基线。"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e2_frequency_diagnostic import run_case
from run_e2_formal import code_bundle_provenance
from run_e2_smoke import canonical_hash, prepare_operating_point, sha256


def validate_config(config):
    """校验恢复敏感性矩阵及其冻结判据。"""
    if config["execution_status"] not in {
        "contract_frozen_runner_pending",
        "contract_frozen_runner_ready",
    }:
        raise ValueError("unexpected execution_status")
    if config["controller"] != "MPC":
        raise ValueError("recovery sensitivity witness is frozen to MPC")
    if config["input_definition"]["kind"] != "net_load_reference":
        raise ValueError("recovery sensitivity witness is frozen to D_ref")
    amplitudes = [float(value) for value in config["amplitudes_pu"]]
    durations = [float(value) for value in config["recovery_durations_s"]]
    if not amplitudes or len(amplitudes) != len(set(amplitudes)):
        raise ValueError("amplitudes_pu must contain unique values")
    if not durations or len(durations) != len(set(durations)):
        raise ValueError("recovery_durations_s must contain unique values")
    if any(value <= 0.0 for value in amplitudes):
        raise ValueError("witness amplitudes must be positive")
    baseline_duration = float(config["baseline_cases"]["recovery_duration_s"])
    if any(value <= baseline_duration for value in durations):
        raise ValueError("new recovery durations must exceed the baseline")
    if float(config["simulation"]["dt_s"]) <= 0.0:
        raise ValueError("dt_s must be positive")
    if float(config["simulation"]["recovery_sustain_s"]) <= 0.0:
        raise ValueError("recovery_sustain_s must be positive")
    expected = len(amplitudes) * len(durations)
    if int(config["acceptance"]["expected_new_case_count"]) != expected:
        raise ValueError("expected_new_case_count does not match the matrix")
    if not np.isclose(
        float(config["acceptance"]["frozen_power_error_limit_pu"]),
        float(config["simulation"]["completion_limits"]["power_abs_error_pu"]),
    ):
        raise ValueError("frozen power-error limit is inconsistent")


def expand_cases(config):
    """按幅值、恢复时长展开确定性案例索引。"""
    return [
        {
            "amplitude_pu": float(amplitude),
            "recovery_duration_s": float(duration),
        }
        for amplitude in config["amplitudes_pu"]
        for duration in config["recovery_durations_s"]
    ]


def find_baseline_cases(config, project_root):
    """为每个幅值定位唯一的180秒基线案例。"""
    baseline_root = project_root / config["baseline_cases"]["run_dir"] / "cases"
    target_frequency = float(config["frequency"]["frequency_hz"])
    target_phase = float(config["phase_rad"])
    target_duration = float(config["baseline_cases"]["recovery_duration_s"])
    matches = {float(amplitude): [] for amplitude in config["amplitudes_pu"]}
    for case_path in baseline_root.glob("*.json"):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        case_config = case["case_config"]
        if case_config["controller"] != config["controller"]:
            continue
        if not np.isclose(float(case_config["frequency_hz"]), target_frequency):
            continue
        if not np.isclose(float(case_config["phase_rad"]), target_phase):
            continue
        if not np.isclose(
            float(case_config["recovery"]["duration_s"]), target_duration
        ):
            continue
        for amplitude in matches:
            if np.isclose(float(case_config["amplitude_pu"]), amplitude):
                matches[amplitude].append(case_path.resolve())
    invalid = {amplitude: paths for amplitude, paths in matches.items() if len(paths) != 1}
    if invalid:
        raise ValueError(f"each amplitude requires one baseline case: {invalid}")
    return {amplitude: paths[0] for amplitude, paths in matches.items()}


def build_run_config(config, matrix_case):
    """把矩阵案例映射为现有频率诊断器的单案例契约。"""
    return {
        "study_id": config["study_id"],
        "operating_point": config["operating_point"],
        "amplitude_pu": matrix_case["amplitude_pu"],
        "phase_rad": config["phase_rad"],
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "simulation": {
            "dt_s": config["simulation"]["dt_s"],
            "warmup_s": config["simulation"]["warmup_s"],
            "recovery": {
                "duration_s": matrix_case["recovery_duration_s"],
                "sustain_s": config["simulation"]["recovery_sustain_s"],
                "completion_limits": config["simulation"]["completion_limits"],
            },
        },
        "mpc": config["mpc"],
        "constraints": config["constraints"],
    }


def pending_case_indices(output_dir, case_count):
    """只调度尚未形成独立摘要的案例，支持中断后续跑。"""
    return [
        index
        for index in range(case_count)
        if not (output_dir / f"e2_recovery_case_{index:04d}_summary.json").is_file()
    ]


def run_parallel(config_path, output_dir, case_count, workers):
    """通过独立子进程并行执行案例，隔离求解器与案例日志。"""
    if workers < 1:
        raise ValueError("workers must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "process_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pending = pending_case_indices(output_dir, case_count)

    def run(case_index):
        log_path = log_dir / f"case_{case_index:04d}.log"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--case-index",
            str(case_index),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        return case_index, completed.returncode, log_path

    failures = []
    completed_indices = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, case_index) for case_index in pending]
        for future in as_completed(futures):
            case_index, returncode, log_path = future.result()
            print(
                json.dumps(
                    {
                        "case_index": case_index,
                        "returncode": returncode,
                        "log": str(log_path),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if returncode == 0:
                completed_indices.append(case_index)
            else:
                failures.append(case_index)
    observed = case_count - len(pending_case_indices(output_dir, case_count))
    summary = {
        "case_count_total": case_count,
        "case_count_preexisting": case_count - len(pending),
        "case_count_completed_this_run": len(completed_indices),
        "observed_summary_count": observed,
        "failed_case_indices": sorted(failures),
    }
    (output_dir / "parallel_recovery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failures or observed != case_count:
        raise SystemExit(f"recovery matrix incomplete: {summary}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-index", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    project_root = Path.cwd().resolve()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    matrix = expand_cases(config)
    baseline_cases = find_baseline_cases(config, project_root)
    validation = {
        "study_id": config["study_id"],
        "matrix_case_count": len(matrix),
        "baseline_case_count": len(baseline_cases),
        "matrix": matrix,
        "baseline_cases": {
            str(amplitude): str(path) for amplitude, path in baseline_cases.items()
        },
    }
    if args.validate_only:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return

    if args.case_index is not None and args.workers != 1:
        raise ValueError("workers is only valid for full-matrix execution")
    if args.case_index is None and args.workers > 1:
        run_parallel(config_path, args.output_dir.resolve(), len(matrix), args.workers)
        return

    if args.case_index is None:
        selected = list(enumerate(matrix))
    else:
        if args.case_index < 0 or args.case_index >= len(matrix):
            raise ValueError(f"case-index must be in [0, {len(matrix) - 1}]")
        selected = [(args.case_index, matrix[args.case_index])]

    output_dir = args.output_dir.resolve()
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)
    core_hashes, core_bundle_hash = code_bundle_provenance()
    provenance = {
        "sensitivity_runner": sha256(Path(__file__).resolve()),
        "core_bundle": core_bundle_hash,
        **core_hashes,
    }
    runner_hash = canonical_hash(provenance)
    metrics_hash = core_hashes["metrics_source"]
    power = float(config["operating_point"]["nuclear_power_pu"])
    soc = float(config["operating_point"]["bess_soc"])
    params, initial_conditions, operating_state, observation = prepare_operating_point(
        power, soc
    )

    for case_index, matrix_case in selected:
        run_config = build_run_config(config, matrix_case)
        record = run_case(
            config["controller"],
            config["frequency"],
            run_config,
            params,
            initial_conditions,
            operating_state,
            output_dir,
            runner_hash,
            metrics_hash,
        )
        report = {
            "study_id": config["study_id"],
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "code_provenance": provenance,
            "code_bundle_sha256": runner_hash,
            "case_count_total": len(matrix),
            "case_index": case_index,
            "matrix_case": matrix_case,
            "baseline_180s_case": str(
                baseline_cases[matrix_case["amplitude_pu"]]
            ),
            "operating_observation": {
                "electrical_power_pu": float(observation["p_e_pu"]),
                "soc": soc,
            },
            "result": {
                "case_hash": record["case_hash"],
                "npz": record["npz"],
                "constraints": record["constraints"],
                "analysis_metrics": record["analysis_metrics"],
            },
        }
        report["deterministic_summary_sha256"] = canonical_hash(report)
        report_path = output_dir / f"e2_recovery_case_{case_index:04d}_summary.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "case_index": case_index,
                    "amplitude_pu": matrix_case["amplitude_pu"],
                    "recovery_duration_s": matrix_case["recovery_duration_s"],
                    "summary": str(report_path),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
