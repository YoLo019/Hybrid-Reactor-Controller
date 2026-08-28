# -*- coding: utf-8 -*-
"""构造正式E2正弦扰动射线，执行粗扫、首失效二分与非星形检查。"""

import argparse
import copy
import json
import sys
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
FLEXIBILITY_ROOT = Path(__file__).resolve().parent
if str(FLEXIBILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLEXIBILITY_ROOT))

from run_e2_frequency_diagnostic import run_case
from run_e2_smoke import canonical_hash, prepare_operating_point, sha256


def analyze_coarse_safety(amplitudes, safe_flags):
    """定位从中心出发的首次安全—失效转换，并标记非星形射线。"""
    if len(amplitudes) != len(safe_flags) or not amplitudes:
        raise ValueError("amplitudes and safe_flags must have the same non-zero length")
    pairs = sorted((float(a), bool(s)) for a, s in zip(amplitudes, safe_flags))
    if pairs[0][0] != 0.0:
        raise ValueError("coarse scan must include zero amplitude")
    transitions = []
    for previous, current in zip(pairs[:-1], pairs[1:]):
        if previous[1] != current[1]:
            transitions.append(
                {
                    "lower_amplitude_pu": previous[0],
                    "upper_amplitude_pu": current[0],
                    "from_safe": previous[1],
                    "to_safe": current[1],
                }
            )
    first_failure = next(
        (
            item
            for item in transitions
            if item["from_safe"] and not item["to_safe"]
        ),
        None,
    )
    first_unsafe_index = next(
        (index for index, (_, safe) in enumerate(pairs) if not safe), None
    )
    safe_after_failure = bool(
        first_unsafe_index is not None
        and any(safe for _, safe in pairs[first_unsafe_index + 1 :])
    )
    return {
        "pairs": [{"amplitude_pu": a, "safe": s} for a, s in pairs],
        "transitions": transitions,
        "first_failure_bracket": first_failure,
        "non_star_shaped": bool(len(transitions) > 1 or safe_after_failure),
        "all_safe": bool(all(safe for _, safe in pairs)),
        "center_safe": bool(pairs[0][1]),
    }


def code_bundle_provenance():
    paths = {
        "formal_runner": Path(__file__).resolve(),
        "frequency_runner": FLEXIBILITY_ROOT / "run_e2_frequency_diagnostic.py",
        "smoke_runner": FLEXIBILITY_ROOT / "run_e2_smoke.py",
        "metrics_source": MODEL_ROOT / "metrics_source.py",
        "mpc_utils": MODEL_ROOT / "mpc_utils_out.py",
        "model": MODEL_ROOT / "model_wind.py",
        "parameters": MODEL_ROOT / "parameters.py",
        "schema": MODEL_ROOT / "model_schema.py",
    }
    hashes = {name: sha256(path) for name, path in paths.items()}
    return hashes, canonical_hash(hashes)


def validate_config(config):
    amplitudes = sorted(float(value) for value in config["amplitude_grid_pu"])
    if not amplitudes or amplitudes[0] != 0.0:
        raise ValueError("amplitude_grid_pu must start at zero")
    if len(amplitudes) != len(set(amplitudes)):
        raise ValueError("amplitude_grid_pu values must be unique")
    if any(value < 0.0 for value in amplitudes):
        raise ValueError("amplitudes must be non-negative")
    if float(config["bisection_tolerance_pu"]) <= 0.0:
        raise ValueError("bisection_tolerance_pu must be positive")
    if not config["operating_points"] or not config["frequencies"]:
        raise ValueError("operating_points and frequencies must not be empty")
    if config["input_definition"]["kind"] not in {
        "net_load_reference",
        "grid_power_disturbance",
    }:
        raise ValueError("unsupported input_definition kind")
    if float(config["system_scaling"]["model_system_base_mw"]) <= 0.0:
        raise ValueError("model_system_base_mw must be positive")
    recovery = config["simulation"]["recovery"]
    if float(recovery["duration_s"]) <= 0.0 or float(recovery["sustain_s"]) <= 0.0:
        raise ValueError("recovery duration and sustain window must be positive")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ray-index", type=int)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    (output_dir / "cases").mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    code_hashes, code_bundle_hash = code_bundle_provenance()
    metrics_hash = code_hashes["metrics_source"]

    rays = []
    for operating_point in config["operating_points"]:
        for frequency_spec in config["frequencies"]:
            for phase_rad in config["phases_rad"]:
                for controller in config["controllers"]:
                    rays.append(
                        {
                            "power_pu": float(operating_point["power_pu"]),
                            "soc": float(operating_point["soc"]),
                            "frequency": frequency_spec,
                            "phase_rad": float(phase_rad),
                            "controller": str(controller),
                        }
                    )
    if args.ray_index is not None:
        if args.ray_index < 0 or args.ray_index >= len(rays):
            raise ValueError(f"ray-index must be in [0, {len(rays) - 1}]")
        selected_rays = [(args.ray_index, rays[args.ray_index])]
    else:
        selected_rays = list(enumerate(rays))

    operating_cache = {}
    ray_results = []
    for ray_index, ray in selected_rays:
        operating_key = (ray["power_pu"], ray["soc"])
        if operating_key not in operating_cache:
            operating_cache[operating_key] = prepare_operating_point(*operating_key)
        params, initial_conditions, operating_state, observation = operating_cache[operating_key]

        cases_by_amplitude = {}

        def evaluate(amplitude):
            amplitude = float(amplitude)
            key = f"{amplitude:.12f}"
            if key in cases_by_amplitude:
                return cases_by_amplitude[key]
            case_config = {
                "study_id": config["study_id"],
                "operating_point": {
                    "nuclear_power_pu": ray["power_pu"],
                    "bess_soc": ray["soc"],
                },
                "amplitude_pu": amplitude,
                "phase_rad": ray["phase_rad"],
                "input_definition": config["input_definition"],
                "system_scaling": config["system_scaling"],
                "constraint_registry_id": config["constraint_registry_id"],
                "simulation": config["simulation"],
                "mpc": config["mpc"],
                "constraints": config["constraints"],
            }
            case = run_case(
                ray["controller"],
                ray["frequency"],
                case_config,
                params,
                initial_conditions,
                operating_state,
                output_dir,
                code_bundle_hash,
                metrics_hash,
            )
            cases_by_amplitude[key] = case
            return case

        coarse_amplitudes = sorted(float(value) for value in config["amplitude_grid_pu"])
        coarse_cases = [evaluate(amplitude) for amplitude in coarse_amplitudes]
        coarse = analyze_coarse_safety(
            coarse_amplitudes,
            [case["constraints"]["valid_for_boundary"] for case in coarse_cases],
        )
        bracket = coarse["first_failure_bracket"]
        bisection_cases = []
        if bracket is not None:
            lower = float(bracket["lower_amplitude_pu"])
            upper = float(bracket["upper_amplitude_pu"])
            tolerance = float(config["bisection_tolerance_pu"])
            max_iterations = int(config.get("max_bisection_iterations", 20))
            for _ in range(max_iterations):
                if upper - lower <= tolerance:
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

        ray_results.append(
            {
                "ray_index": ray_index,
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
                    None
                    if failure_case is None
                    else failure_case["constraints"]["recovery"]
                ),
                "coarse_case_hashes": [case["case_hash"] for case in coarse_cases],
                "bisection_case_hashes": [case["case_hash"] for case in bisection_cases],
            }
        )

    report = {
        "study_id": config["study_id"],
        "scope": (
            "formal E2 reference-feasibility radial search"
            if config["input_definition"]["kind"] == "net_load_reference"
            else "formal E2 physical grid-disturbance rejection radial search"
        ),
        "input_definition": config["input_definition"],
        "system_scaling": config["system_scaling"],
        "constraint_registry_id": config["constraint_registry_id"],
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "code_provenance": code_hashes,
        "code_bundle_sha256": code_bundle_hash,
        "ray_count_total": len(rays),
        "ray_count_in_report": len(ray_results),
        "ray_results": ray_results,
    }
    report["deterministic_summary_sha256"] = canonical_hash(report)
    suffix = "all" if args.ray_index is None else f"ray_{args.ray_index:04d}"
    report_path = output_dir / f"e2_formal_{suffix}_summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
