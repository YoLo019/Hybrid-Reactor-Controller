# -*- coding: utf-8 -*-
"""执行E3斜坡—保持—回落—恢复射线，并记录首次失效边界。"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from metrics_source import run_mpc_scenario, run_pid_scenario
from run_e2_formal import analyze_coarse_safety
from run_e2_smoke import (
    canonical_hash,
    prepare_operating_point,
    save_case_npz,
    sha256,
    summarize_case,
)


def make_e3_input_functions(
    base_power: float,
    amplitude: float,
    ramp_rate_pu_s: float,
    hold_s: float,
    direction: int,
    warmup_s: float,
    input_kind: str,
):
    """构造对称斜坡—保持—回落输入，并在回落结束后归零。"""
    if input_kind not in {"net_load_reference", "grid_power_disturbance"}:
        raise ValueError(f"unsupported E3 input kind: {input_kind}")
    if amplitude < 0.0 or ramp_rate_pu_s <= 0.0 or hold_s < 0.0:
        raise ValueError("amplitude/hold must be non-negative and ramp rate positive")
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")

    ramp_duration_s = 0.0 if amplitude == 0.0 else amplitude / ramp_rate_pu_s
    ramp_end_s = warmup_s + ramp_duration_s
    hold_end_s = ramp_end_s + hold_s
    forcing_end_s = hold_end_s + ramp_duration_s

    def input_signal(current_time):
        current_time = float(current_time)
        if current_time < warmup_s or current_time >= forcing_end_s:
            return 0.0
        elapsed = current_time - warmup_s
        if elapsed < ramp_duration_s:
            level = ramp_rate_pu_s * elapsed
        elif elapsed < ramp_duration_s + hold_s:
            level = amplitude
        else:
            level = max(
                amplitude
                - ramp_rate_pu_s * (elapsed - ramp_duration_s - hold_s),
                0.0,
            )
        return float(direction * min(level, amplitude))

    def target_profile(current_time):
        if input_kind == "net_load_reference":
            return float((base_power - input_signal(current_time)) / base_power)
        return 1.0

    def grid_disturbance(current_time):
        if input_kind == "grid_power_disturbance":
            return input_signal(current_time)
        return 0.0

    phase_boundaries = {
        "warmup_end_s": float(warmup_s),
        "ramp_end_s": float(ramp_end_s),
        "hold_end_s": float(hold_end_s),
        "forcing_end_s": float(forcing_end_s),
        "ramp_duration_s": float(ramp_duration_s),
    }
    return target_profile, grid_disturbance, input_signal, phase_boundaries


def expand_rays(config: dict) -> list[dict]:
    """按冻结顺序展开工况、双域、速率、保持、方向和控制器。"""
    rays = []
    for operating_point in config["operating_points"]:
        for input_definition in config["input_definitions"]:
            for rate_level in config["ramp_rate_levels"]:
                for hold_level in config["hold_levels"]:
                    for direction in config["directions"]:
                        for controller in config["controllers"]:
                            rays.append(
                                {
                                    "power_pu": float(operating_point["power_pu"]),
                                    "soc": float(operating_point["soc"]),
                                    "input_definition": input_definition,
                                    "rate_level": rate_level,
                                    "hold_level": hold_level,
                                    "direction": int(direction),
                                    "controller": str(controller),
                                }
                            )
    return rays


def validate_config(config: dict) -> dict:
    """拒绝不完整、重复或可能意外生成超长案例的配置。"""
    amplitudes = sorted(float(value) for value in config["amplitude_grid_pu"])
    if not amplitudes or amplitudes[0] != 0.0:
        raise ValueError("amplitude_grid_pu must start at zero")
    if len(amplitudes) != len(set(amplitudes)) or any(value < 0.0 for value in amplitudes):
        raise ValueError("amplitude_grid_pu must contain unique non-negative values")
    if float(config["bisection_tolerance_pu"]) <= 0.0:
        raise ValueError("bisection_tolerance_pu must be positive")
    if not config["operating_points"] or not config["controllers"]:
        raise ValueError("operating_points and controllers must not be empty")
    if set(config["controllers"]) != {"PID", "MPC"}:
        raise ValueError("E3 requires exactly the frozen PID and MPC controllers")
    if set(int(value) for value in config["directions"]) != {-1, 1}:
        raise ValueError("E3 requires both directions -1 and 1")

    domain_ids = [item["domain_id"] for item in config["input_definitions"]]
    kinds = [item["kind"] for item in config["input_definitions"]]
    if set(domain_ids) != {"D_ref", "D_dist"} or set(kinds) != {
        "net_load_reference",
        "grid_power_disturbance",
    }:
        raise ValueError("E3 requires one D_ref and one D_dist input definition")
    if len(domain_ids) != len(set(domain_ids)):
        raise ValueError("input domain ids must be unique")

    rates = [float(item["rate_pu_per_s"]) for item in config["ramp_rate_levels"]]
    holds = [float(item["duration_s"]) for item in config["hold_levels"]]
    if not rates or any(value <= 0.0 for value in rates) or len(rates) != len(set(rates)):
        raise ValueError("ramp rates must be unique and positive")
    if not holds or any(value < 0.0 for value in holds) or len(holds) != len(set(holds)):
        raise ValueError("hold durations must be unique and non-negative")

    simulation = config["simulation"]
    recovery = simulation["recovery"]
    if float(simulation["dt_s"]) <= 0.0 or float(simulation["warmup_s"]) < 0.0:
        raise ValueError("dt must be positive and warmup non-negative")
    if float(recovery["duration_s"]) <= 0.0 or float(recovery["sustain_s"]) <= 0.0:
        raise ValueError("recovery duration and sustain window must be positive")
    if float(config["system_scaling"]["model_system_base_mw"]) <= 0.0:
        raise ValueError("model_system_base_mw must be positive")

    maximum_duration_s = (
        float(simulation["warmup_s"])
        + 2.0 * max(amplitudes) / min(rates)
        + max(holds)
        + float(recovery["duration_s"])
    )
    if maximum_duration_s > float(config["max_case_duration_s"]) + 1e-9:
        raise ValueError(
            f"maximum configured case duration {maximum_duration_s:.3f}s exceeds cap"
        )
    return {
        "pass": True,
        "ray_count": len(expand_rays(config)),
        "maximum_case_duration_s": float(maximum_duration_s),
    }


def code_bundle_provenance() -> tuple[dict, str]:
    paths = {
        "e3_runner": Path(__file__).resolve(),
        "e2_boundary_helper": FLEXIBILITY_ROOT / "run_e2_formal.py",
        "shared_scenario_helper": FLEXIBILITY_ROOT / "run_e2_smoke.py",
        "metrics_source": MODEL_ROOT / "metrics_source.py",
        "mpc_utils": MODEL_ROOT / "mpc_utils_out.py",
        "model": MODEL_ROOT / "model_wind.py",
        "parameters": MODEL_ROOT / "parameters.py",
        "schema": MODEL_ROOT / "model_schema.py",
    }
    hashes = {name: sha256(path) for name, path in paths.items()}
    return hashes, canonical_hash(hashes)


def phase_metrics(result: dict, input_signal: np.ndarray, boundaries: dict) -> dict:
    """分开报告受迫段和恢复段，避免迟发违规被总指标掩盖。"""
    time = np.asarray(result["t"], dtype=float)
    output = np.asarray(result["P_e"], dtype=float)
    target = np.asarray(result["Target_abs"], dtype=float)
    forcing = (time >= boundaries["warmup_end_s"]) & (
        time < boundaries["forcing_end_s"]
    )
    recovery = time >= boundaries["forcing_end_s"]

    def rmse(mask):
        return None if not np.any(mask) else float(np.sqrt(np.mean((output[mask] - target[mask]) ** 2)))

    return {
        "input_peak_abs_pu": float(np.max(np.abs(input_signal))),
        "input_final_abs_pu": float(abs(input_signal[-1])),
        "forcing_tracking_rmse_pu": rmse(forcing),
        "recovery_tracking_rmse_pu": rmse(recovery),
        "forcing_frequency_max_abs_hz": (
            None
            if not np.any(forcing)
            else float(
                np.max(
                    np.abs(np.asarray(result["frequency_deviation_hz"], dtype=float)[forcing])
                )
            )
        ),
        "phase_boundaries": boundaries,
    }


def run_case(
    ray: dict,
    amplitude: float,
    config: dict,
    params: dict,
    initial_conditions: dict,
    operating_state: np.ndarray,
    output_dir: Path,
    code_bundle_hash: str,
    metrics_hash: str,
) -> dict:
    base_power = float(ray["power_pu"])
    rate = float(ray["rate_level"]["rate_pu_per_s"])
    hold = float(ray["hold_level"]["duration_s"])
    warmup = float(config["simulation"]["warmup_s"])
    target_function, grid_function, input_function, boundaries = make_e3_input_functions(
        base_power,
        float(amplitude),
        rate,
        hold,
        int(ray["direction"]),
        warmup,
        str(ray["input_definition"]["kind"]),
    )
    duration_s = boundaries["forcing_end_s"] + float(
        config["simulation"]["recovery"]["duration_s"]
    )
    case_config = {
        "study_id": config["study_id"],
        "formal_claim": bool(config["formal_claim"]),
        "ray": ray,
        "amplitude_pu": float(amplitude),
        "constraint_registry_id": config["constraint_registry_id"],
        "system_scaling": config["system_scaling"],
        "simulation": config["simulation"],
        "forcing_end_s": boundaries["forcing_end_s"],
        "duration_s": duration_s,
        "mpc": config["mpc"],
        "constraints": config["constraints"],
        "code_bundle_sha256": code_bundle_hash,
        "metrics_source_sha256": metrics_hash,
    }
    case_hash = canonical_hash(case_config)
    stem = (
        f"{ray['input_definition']['domain_id'].lower()}_"
        f"{ray['controller'].lower()}_d{ray['direction']:+d}_"
        f"{ray['rate_level']['label']}_h{hold:.0f}_{case_hash[:12].lower()}"
    )
    npz_path = output_dir / "cases" / f"{stem}.npz"
    json_path = output_dir / "cases" / f"{stem}.json"
    if json_path.is_file() and npz_path.is_file():
        cached = json.loads(json_path.read_text(encoding="utf-8"))
        if cached.get("case_hash") == case_hash:
            return cached

    common = {
        "scenario_name": "E3RampHoldReturn",
        "params": copy.deepcopy(params),
        "ic": copy.deepcopy(initial_conditions),
        "y0": operating_state.copy(),
        "dt": float(config["simulation"]["dt_s"]),
        "t_end": duration_s,
        "target_function": target_function,
        "grid_disturbance_function": grid_function,
        "scenario_title": "E3 ramp-hold-return-recovery",
        "scenario_tag": stem,
        "show_progress": False,
    }
    if ray["controller"] == "MPC":
        mpc = config["mpc"]
        result = run_mpc_scenario(
            **common,
            n=int(mpc["horizon_steps"]),
            q_weights={
                "power": float(mpc["q_power"]),
                "Tavg": float(mpc["q_temperature"]),
            },
            r_weights={
                "move": float(mpc["move_weight"]),
                "magnitude": float(mpc["magnitude_weight"]),
            },
            use_reference_preview=bool(mpc.get("use_reference_preview", False)),
            forecast_type=(
                str(mpc.get("forecast_type", "perfect_foresight"))
                if bool(mpc.get("use_reference_preview", False))
                else None
            ),
            integral_weight=float(mpc.get("integral_weight", 0.0)),
            integral_error_limit=mpc.get("integral_error_limit"),
        )
    else:
        result = run_pid_scenario(**common)

    input_signal = np.asarray([input_function(value) for value in result["t"]], dtype=float)
    save_case_npz(npz_path, result, input_signal)
    constraints = summarize_case(
        result,
        config["constraints"],
        input_signal,
        boundaries["forcing_end_s"],
        config["simulation"]["recovery"],
    )
    record = {
        "case_hash": case_hash,
        "case_config": case_config,
        "npz": str(npz_path),
        "constraints": constraints,
        "phase_metrics": phase_metrics(result, input_signal, boundaries),
    }
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def execute(config_path: Path, output_dir: Path, ray_index: int | None = None) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validation = validate_config(config)
    mapping_path = MODEL_ROOT.parent / config["system_scaling"]["mapping_file"]
    if not mapping_path.is_file():
        raise FileNotFoundError(mapping_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)
    code_hashes, code_bundle_hash = code_bundle_provenance()
    metrics_hash = code_hashes["metrics_source"]
    rays = expand_rays(config)
    if ray_index is not None:
        if ray_index < 0 or ray_index >= len(rays):
            raise ValueError(f"ray-index must be in [0, {len(rays) - 1}]")
        selected = [(ray_index, rays[ray_index])]
    else:
        selected = list(enumerate(rays))

    operating_cache = {}
    ray_results = []
    for index, ray in selected:
        operating_key = (ray["power_pu"], ray["soc"])
        if operating_key not in operating_cache:
            operating_cache[operating_key] = prepare_operating_point(*operating_key)
        params, initial_conditions, operating_state, observation = operating_cache[operating_key]
        cases_by_amplitude = {}

        def evaluate(amplitude):
            key = f"{float(amplitude):.12f}"
            if key not in cases_by_amplitude:
                cases_by_amplitude[key] = run_case(
                    ray,
                    float(amplitude),
                    config,
                    params,
                    initial_conditions,
                    operating_state,
                    output_dir,
                    code_bundle_hash,
                    metrics_hash,
                )
            return cases_by_amplitude[key]

        coarse_amplitudes = sorted(float(value) for value in config["amplitude_grid_pu"])
        coarse_cases = [evaluate(value) for value in coarse_amplitudes]
        coarse = analyze_coarse_safety(
            coarse_amplitudes,
            [case["constraints"]["valid_for_boundary"] for case in coarse_cases],
        )
        bracket = coarse["first_failure_bracket"]
        bisection_cases = []
        if bracket is not None:
            lower = float(bracket["lower_amplitude_pu"])
            upper = float(bracket["upper_amplitude_pu"])
            for _ in range(int(config["max_bisection_iterations"])):
                if upper - lower <= float(config["bisection_tolerance_pu"]):
                    break
                midpoint = 0.5 * (lower + upper)
                case = evaluate(midpoint)
                bisection_cases.append(case)
                if case["constraints"]["valid_for_boundary"]:
                    lower = midpoint
                else:
                    upper = midpoint
            safe_boundary = lower
            first_failure = upper
            failure_case = evaluate(upper)
            boundary_status = "bracketed_first_failure"
        elif coarse["all_safe"]:
            safe_boundary = coarse_amplitudes[-1]
            first_failure = None
            failure_case = None
            boundary_status = "right_censored_at_search_upper"
        else:
            safe_boundary = None
            first_failure = 0.0
            failure_case = coarse_cases[0]
            boundary_status = "unsafe_at_center"

        nonzero_cases = [
            case
            for amplitude, case in zip(coarse_amplitudes, coarse_cases)
            if amplitude > 0.0
        ]
        ray_results.append(
            {
                "ray_index": index,
                "ray": ray,
                "operating_observation": {
                    "electrical_power_pu": float(observation["p_e_pu"]),
                    "frequency_pu": float(observation["frequency_pu"]),
                },
                "coarse_scan": coarse,
                "boundary_status": boundary_status,
                "safe_boundary_amplitude_pu": safe_boundary,
                "first_failure_amplitude_pu": first_failure,
                "boundary_width_pu": (
                    None
                    if safe_boundary is None or first_failure is None
                    else float(first_failure - safe_boundary)
                ),
                "active_constraint": (
                    None
                    if failure_case is None
                    else (
                        failure_case["constraints"]["active_constraint"]
                        if not failure_case["constraints"]["safe"]
                        else "recovery_incomplete"
                    )
                ),
                "first_violation_time_s": (
                    None
                    if failure_case is None
                    else failure_case["constraints"]["first_violation_time_s"]
                ),
                "first_violation_phase": (
                    None
                    if failure_case is None
                    else failure_case["constraints"]["first_violation_phase"]
                ),
                "failure_recovery": (
                    None if failure_case is None else failure_case["constraints"]["recovery"]
                ),
                "nonzero_cases_safe": all(
                    case["constraints"]["safe"] for case in nonzero_cases
                ),
                "nonzero_cases_recovered": all(
                    case["constraints"]["recovery"]["complete"] for case in nonzero_cases
                ),
                "solver_failures": int(
                    sum(
                        case["constraints"]["metrics"]["solver_failures"]
                        for case in coarse_cases + bisection_cases
                    )
                ),
                "waveform_returned_to_zero": all(
                    case["phase_metrics"]["input_final_abs_pu"] <= 1e-12
                    for case in coarse_cases
                ),
                "coarse_case_hashes": [case["case_hash"] for case in coarse_cases],
                "bisection_case_hashes": [case["case_hash"] for case in bisection_cases],
            }
        )

    coverage = {
        "domains": sorted({ray["input_definition"]["domain_id"] for ray in rays}),
        "controllers": sorted({ray["controller"] for ray in rays}),
        "directions": sorted({ray["direction"] for ray in rays}),
        "rate_labels": sorted({ray["rate_level"]["label"] for ray in rays}),
        "hold_labels": sorted({ray["hold_level"]["label"] for ray in rays}),
    }
    report = {
        "study_id": config["study_id"],
        "formal_claim": bool(config["formal_claim"]),
        "scope": config["scope"],
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "mapping_path": str(mapping_path),
        "mapping_sha256": sha256(mapping_path),
        "code_provenance": code_hashes,
        "code_bundle_sha256": code_bundle_hash,
        "constraint_registry_id": config["constraint_registry_id"],
        "preparation_gate": validation,
        "ray_count_total": len(rays),
        "ray_count_in_report": len(ray_results),
        "coverage": coverage,
        "ray_results": ray_results,
    }
    if not config["formal_claim"] and ray_index is None:
        smoke_checks = {
            "all_rays_executed": len(ray_results) == len(rays),
            "both_domains_covered": coverage["domains"] == ["D_dist", "D_ref"],
            "both_controllers_covered": coverage["controllers"] == ["MPC", "PID"],
            "both_directions_covered": coverage["directions"] == [-1, 1],
            "all_centers_safe": all(item["coarse_scan"]["center_safe"] for item in ray_results),
            "all_nonzero_cases_safe": all(item["nonzero_cases_safe"] for item in ray_results),
            "all_nonzero_cases_recovered": all(
                item["nonzero_cases_recovered"] for item in ray_results
            ),
            "no_solver_failures": all(item["solver_failures"] == 0 for item in ray_results),
            "all_waveforms_return_to_zero": all(
                item["waveform_returned_to_zero"] for item in ray_results
            ),
        }
        report["smoke_gate"] = {
            "pass": all(smoke_checks.values()),
            "checks": smoke_checks,
        }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    suffix = "all" if ray_index is None else f"ray_{ray_index:04d}"
    report_path = output_dir / f"e3_formal_{suffix}_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ray-index", type=int)
    parser.add_argument("--list-rays", action="store_true")
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if args.list_rays:
        validation = validate_config(config)
        mapping_path = MODEL_ROOT.parent / config["system_scaling"]["mapping_file"]
        if not mapping_path.is_file():
            raise FileNotFoundError(mapping_path)
        rays = expand_rays(config)
        result = {
            "study_id": config["study_id"],
            "formal_claim": bool(config["formal_claim"]),
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "mapping_path": str(mapping_path),
            "mapping_sha256": sha256(mapping_path),
            "data_access": "frozen W1 mapping only; no raw trajectory opened",
            "locked_splits_accessed": [],
            "preparation_gate": validation,
            "coverage": {
                "domains": sorted(
                    {ray["input_definition"]["domain_id"] for ray in rays}
                ),
                "controllers": sorted({ray["controller"] for ray in rays}),
                "directions": sorted({ray["direction"] for ray in rays}),
                "rate_labels": sorted(
                    {ray["rate_level"]["label"] for ray in rays}
                ),
                "hold_labels": sorted(
                    {ray["hold_level"]["label"] for ray in rays}
                ),
            },
            "rays": rays,
        }
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "e3_ray_manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        result = execute(config_path, args.output_dir.resolve(), args.ray_index)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
