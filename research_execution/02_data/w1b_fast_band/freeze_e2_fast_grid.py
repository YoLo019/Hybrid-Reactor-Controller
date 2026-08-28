"""把W1-B两类实测电功率PSD冻结为E2快速频带代表点与等效正弦幅值。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MORE_PSD_PATH = ROOT / "processed" / "more_eu_active_power_psd.csv"
BJORKO_PSD_PATH = ROOT / "processed" / "bjorko_dc_power_proxy_psd.csv"
AUDIT_PATH = ROOT / "audit_report.json"
OUTPUT_PATH = ROOT / "e2_fast_grid_v3.json"

MODEL_SYSTEM_BASE_MW = 100.0
SOTAVENTO_PORTFOLIO_CAPACITY_MW = 17.56

MORE_BANDS = [
    (0.005, 0.0125, 0.005),
    (0.0125, 0.035, 0.02),
    (0.035, 0.1, 0.05),
    (0.1, 0.25, 0.15),
]
BJORKO_BANDS = [
    (0.25, 0.5, 0.35),
    (0.5, 1.0, 0.75),
]
BJORKO_RATED_POWER_W = 45_000.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> None:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    if audit["gate_decision"] != "PASS":
        raise ValueError("W1-B未通过，不能冻结正式快速频带网格")
    more_source = audit["sources"]["more_eu"]
    bjorko_source = audit["sources"]["bjorko"]
    empirical_scale_kw = float(more_source["active_power_range_kw"][1])
    more_spectrum = pd.read_csv(MORE_PSD_PATH)
    more_frequency = more_spectrum["frequency_hz"].to_numpy(dtype=float)
    more_psd = more_spectrum["psd_kw2_per_hz"].to_numpy(dtype=float)
    bjorko_spectrum = pd.read_csv(BJORKO_PSD_PATH)
    bjorko_frequency = bjorko_spectrum["frequency_hz"].to_numpy(dtype=float)
    bjorko_psd = bjorko_spectrum["psd_w2_per_hz"].to_numpy(dtype=float)

    bands = []
    for low, high, representative in MORE_BANDS:
        selected = (more_frequency >= low) & (more_frequency < high)
        variance_kw2 = float(np.trapezoid(more_psd[selected], more_frequency[selected]))
        peak_kw = float(np.sqrt(2.0 * variance_kw2))
        relative_peak = peak_kw / empirical_scale_kw
        bands.append(
            {
                "source_id": more_source["source_id"],
                "evidence_class": "direct measured active power",
                "band_hz": [low, high],
                "representative_frequency_hz": representative,
                "equivalent_sinusoid_peak_kw": peak_kw,
                "equivalent_sinusoid_peak_source_relative": relative_peak,
                "direct_turbine_peak_system_pu": (
                    peak_kw / 1000.0 / MODEL_SYSTEM_BASE_MW
                ),
                "portfolio_scaled_peak_system_pu": (
                    relative_peak
                    * SOTAVENTO_PORTFOLIO_CAPACITY_MW
                    / MODEL_SYSTEM_BASE_MW
                ),
            }
        )
    for low, high, representative in BJORKO_BANDS:
        selected = (bjorko_frequency >= low) & (bjorko_frequency < high)
        variance_w2 = float(
            np.trapezoid(bjorko_psd[selected], bjorko_frequency[selected])
        )
        peak_w = float(np.sqrt(2.0 * variance_w2))
        relative_peak = peak_w / BJORKO_RATED_POWER_W
        bands.append(
            {
                "source_id": bjorko_source["source_id"],
                "evidence_class": "measured generator-side electrical power; not point-of-interconnection power",
                "band_hz": [low, high],
                "representative_frequency_hz": representative,
                "equivalent_sinusoid_peak_w": peak_w,
                "equivalent_sinusoid_peak_source_relative": relative_peak,
                "direct_turbine_peak_system_pu": (
                    peak_w / 1_000_000.0 / MODEL_SYSTEM_BASE_MW
                ),
                "portfolio_scaled_peak_system_pu": (
                    relative_peak
                    * SOTAVENTO_PORTFOLIO_CAPACITY_MW
                    / MODEL_SYSTEM_BASE_MW
                ),
            }
        )

    report = {
        "version": 3,
        "audit_report_sha256": sha256(AUDIT_PATH),
        "source_ids": [more_source["source_id"], bjorko_source["source_id"]],
        "source_data_sha256": {
            more_source["source_id"]: more_source["sha256"],
            bjorko_source["source_id"]: bjorko_source["sha256"],
        },
        "source_psd_sha256": {
            more_source["source_id"]: sha256(MORE_PSD_PATH),
            bjorko_source["source_id"]: sha256(BJORKO_PSD_PATH),
        },
        "normalization": {
            more_source["source_id"]: {
                "type": "empirical_observed_max_not_nameplate",
                "scale_kw": empirical_scale_kw,
                "warning": "This is a reproducible data scale, not a verified turbine nameplate rating.",
            },
            bjorko_source["source_id"]: {
                "type": "official_rated_power",
                "scale_w": BJORKO_RATED_POWER_W,
            },
        },
        "mapping": "sqrt(2 * integral(PSD df)) gives the peak amplitude of a sinusoid with equal band variance",
        "system_mapping": {
            "model_system_base_mw": MODEL_SYSTEM_BASE_MW,
            "portfolio_capacity_mw": SOTAVENTO_PORTFOLIO_CAPACITY_MW,
            "portfolio_source": "Sotavento 2016 wind-farm rated capacity",
            "direct_turbine_mapping": "source peak converted to MW / model system base MW",
            "portfolio_scaled_mapping": "source-relative band amplitude * 17.56 MW / 100 MW",
            "claim_limit": "portfolio scaling is an explicit cross-source scenario assumption, not a direct measurement of the 17.56 MW Sotavento wind farm at the source sampling rate",
        },
        "bands": bands,
        "formal_cross_operating_frequencies_hz": [0.005, 0.02, 0.15, 0.35, 0.75],
        "omitted_representative_frequency_hz": 0.05,
        "omission_reason": "0.02 represents the interior of the direct-power low fast band; 0.15 retains its low-energy upper contrast; 0.35 and 0.75 separately represent the generator-side measured high band.",
        "claim_limit": "0.25-1 Hz amplitudes are generator-side measured electrical-power exposure levels and must not be described as direct point-of-interconnection active power.",
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
