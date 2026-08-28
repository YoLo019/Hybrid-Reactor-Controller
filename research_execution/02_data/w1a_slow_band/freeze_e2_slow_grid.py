# -*- coding: utf-8 -*-
"""把Sotavento慢域候选从风场相对标幺映射到100 MW系统基准。"""

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_REPORT = PROJECT_ROOT / "4_4/data/wind/manifests/w1_feature_report.json"
OUTPUT = Path(__file__).resolve().parent / "e2_slow_grid_v2.json"
MODEL_SYSTEM_BASE_MW = 100.0
WIND_PORTFOLIO_CAPACITY_MW = 17.56


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main():
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    calibration = source["scenario_calibration"]
    relative = calibration["sine_amplitude_candidates_abs_fluctuation_pu"]
    scale = WIND_PORTFOLIO_CAPACITY_MW / MODEL_SYSTEM_BASE_MW
    report = {
        "version": 2,
        "source_id": source["dataset_id"],
        "source_report": str(SOURCE_REPORT),
        "source_report_sha256": file_sha256(SOURCE_REPORT),
        "source_relative_amplitudes": relative,
        "system_mapping": {
            "model_system_base_mw": MODEL_SYSTEM_BASE_MW,
            "wind_portfolio_capacity_mw": WIND_PORTFOLIO_CAPACITY_MW,
            "formula": "source relative p.u. * 17.56 MW / 100 MW",
            "claim_limit": "Energy-unit interpretation remains provisional as documented by W1-A",
        },
        "system_amplitudes_pu": {
            name: float(value) * scale for name, value in relative.items()
        },
        "formal_center_frequencies_hz": [
            calibration["sine_frequency_candidates_hz"][4],
            calibration["sine_frequency_candidates_hz"][5],
            calibration["sine_frequency_candidates_hz"][6],
        ],
        "boundary_search_amplitudes_pu": [
            0.0,
            0.024548310554290048,
            float(relative["p99"]) * scale,
            0.0544,
            0.0982,
            0.1322,
            0.2168,
            0.3,
        ],
        "separation_rule": (
            "system_amplitudes_pu are data exposure levels; "
            "boundary_search_amplitudes_pu are system-domain search probes"
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
