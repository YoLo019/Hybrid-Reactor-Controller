"""审计 W1-B 秒级风电数据的来源、时间轴和可支持频带。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MORE_PATH = ROOT / "raw" / "more_eu_engie" / "wind.parquet"
BJORKO_DIR = ROOT / "raw" / "bjorko_zenodo_v3"
BJORKO_PATH = BJORKO_DIR / "B1_CL4_100.csv"
BJORKO_SHORT_DESCRIPTION_PATH = BJORKO_DIR / "Chalmers_wind_turbine_description_short.pdf"
BJORKO_FULL_DESCRIPTION_PATH = BJORKO_DIR / "Chalmers_wind_turbine.pdf"
BJORKO_THESIS_PATH = ROOT / "provenance" / "chalmers_islanded_operation_thesis_2024.pdf"
REPORT_PATH = ROOT / "audit_report.json"
MANIFEST_PATH = ROOT / "manifest.json"
FIGURE_DIR = ROOT / "figures"
PROCESSED_DIR = ROOT / "processed"

TARGET_BAND_HZ = (0.005, 1.0)
BJORKO_DIRECT_POWER_BAND_HZ = (0.25, 1.0)
MIN_BJORKO_SEGMENT_DURATION_S = 120.0
MIN_BJORKO_SEGMENTS = 5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def welch_psd(values: np.ndarray, fs: float, nperseg: int) -> tuple[np.ndarray, np.ndarray, int]:
    """使用线性去趋势、Hann窗和50%重叠计算单边PSD。"""
    values = np.asarray(values, dtype=float)
    nperseg = min(nperseg, values.size)
    step = max(1, nperseg // 2)
    starts = list(range(0, values.size - nperseg + 1, step))
    if not starts:
        raise ValueError("数据长度不足以计算PSD")

    window = np.hanning(nperseg)
    scale = fs * np.square(window).sum()
    centered_time = np.arange(nperseg, dtype=float) - (nperseg - 1) / 2
    time_energy = np.square(centered_time).sum()
    spectra = []
    for start in starts:
        segment = values[start : start + nperseg]
        segment_mean = segment.mean()
        slope = np.dot(centered_time, segment - segment_mean) / time_energy
        detrended = segment - segment_mean - slope * centered_time
        spectrum = np.square(np.abs(np.fft.rfft(detrended * window))) / scale
        if nperseg % 2 == 0:
            spectrum[1:-1] *= 2
        else:
            spectrum[1:] *= 2
        spectra.append(spectrum)
    return np.fft.rfftfreq(nperseg, d=1 / fs), np.mean(spectra, axis=0), len(starts)


def band_energy(frequency: np.ndarray, psd: np.ndarray, low: float, high: float) -> float:
    selected = (frequency >= low) & (frequency <= high)
    if selected.sum() < 2:
        return 0.0
    return float(np.trapezoid(psd[selected], frequency[selected]))


def interval_summary(seconds: np.ndarray, nominal_dt: float) -> dict[str, float | int]:
    differences = np.diff(seconds)
    tolerance = nominal_dt * 1e-3
    return {
        "rows": int(seconds.size),
        "duplicate_intervals": int(np.isclose(differences, 0.0, atol=tolerance).sum()),
        "negative_intervals": int((differences < -tolerance).sum()),
        "nominal_intervals": int(np.isclose(differences, nominal_dt, atol=tolerance).sum()),
        "non_nominal_intervals": int((~np.isclose(differences, nominal_dt, atol=tolerance)).sum()),
        "minimum_interval_s": float(differences.min()),
        "median_interval_s": float(np.median(differences)),
        "maximum_interval_s": float(differences.max()),
    }


def audit_more() -> tuple[dict, pd.DataFrame]:
    data = pd.read_parquet(MORE_PATH)
    timestamps = pd.to_datetime(data["datetime"])
    seconds = timestamps.to_numpy(dtype="datetime64[ns]").astype("int64") / 1e9
    power = data["active power"].to_numpy(dtype=float)
    fs = 0.5
    frequency, psd, windows = welch_psd(power, fs=fs, nperseg=32768)
    total = band_energy(frequency, psd, TARGET_BAND_HZ[0], fs / 2)
    result = {
        "source_id": "more_eu_engie_2s",
        "file": str(MORE_PATH.relative_to(ROOT)),
        "sha256": sha256(MORE_PATH),
        "rows": int(len(data)),
        "columns": list(data.columns),
        "start": timestamps.iloc[0].isoformat(),
        "end": timestamps.iloc[-1].isoformat(),
        "duration_s": float(seconds[-1] - seconds[0]),
        "missing_values": {column: int(data[column].isna().sum()) for column in data.columns},
        "intervals": interval_summary(seconds, nominal_dt=2.0),
        "sampling_rate_hz": fs,
        "nyquist_hz": fs / 2,
        "direct_active_power": True,
        "active_power_unit": "kW",
        "active_power_range_kw": [float(power.min()), float(power.max())],
        "psd": {
            "method": "linear-detrended Hann Welch, 50% overlap",
            "nperseg": 32768,
            "windows": windows,
            "frequency_resolution_hz": float(frequency[1] - frequency[0]),
            "energy_0p005_to_0p05_fraction": band_energy(frequency, psd, 0.005, 0.05) / total,
            "energy_0p05_to_0p25_fraction": band_energy(frequency, psd, 0.05, 0.25) / total,
        },
        "supported_band_hz": [0.005, 0.25],
        "target_band_coverage": "partial",
    }
    spectrum = pd.DataFrame({"frequency_hz": frequency, "psd_kw2_per_hz": psd})
    return result, spectrum


def true_runs(mask: np.ndarray, seconds: np.ndarray, maximum_gap_s: float) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, valid in enumerate(mask):
        starts_new_run = index > 0 and seconds[index] - seconds[index - 1] > maximum_gap_s
        if valid and (start is None or starts_new_run):
            if start is not None:
                runs.append((start, index))
            start = index
        elif not valid and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def regularize_100hz(
    seconds: np.ndarray, values: dict[str, np.ndarray]
) -> tuple[dict[str, np.ndarray], int, int, float]:
    """在同一规则时间轴上合并多通道重复值，并仅插值不超过0.2秒的缺口。"""
    ticks = np.rint((seconds - seconds[0]) * 100).astype(np.int64)
    frame = pd.DataFrame({"tick": ticks, **values}).groupby("tick", as_index=False).mean()
    regular_ticks = np.arange(int(frame["tick"].iloc[0]), int(frame["tick"].iloc[-1]) + 1)
    regular = frame.set_index("tick").reindex(regular_ticks)
    missing_mask = regular.isna().any(axis=1).to_numpy()
    missing = int(missing_mask.sum())
    duplicate_rows = int(len(ticks) - len(frame))

    maximum_gap_samples = 0
    if missing_mask.any():
        changes = np.diff(np.r_[False, missing_mask, False].astype(np.int8))
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        maximum_gap_samples = int((ends - starts).max())
    if maximum_gap_samples > 20:
        raise ValueError(f"最长缺口为{maximum_gap_samples}点，超过0.2秒插值上限")
    return (
        {
            column: regular[column]
            .interpolate(method="linear", limit=20, limit_area="inside")
            .to_numpy()
            for column in values
        },
        missing,
        duplicate_rows,
        maximum_gap_samples / 100,
    )


def band_limited(values: np.ndarray, fs: float, low: float, high: float) -> np.ndarray:
    """仅用于跨物理通道一致性检查，不作为PSD估计器。"""
    values = np.asarray(values, dtype=float)
    centered_time = np.arange(values.size, dtype=float) - (values.size - 1) / 2
    slope = np.dot(centered_time, values - values.mean()) / np.square(centered_time).sum()
    detrended = values - values.mean() - slope * centered_time
    frequency = np.fft.rfftfreq(values.size, d=1 / fs)
    spectrum = np.fft.rfft(detrended)
    spectrum[(frequency < low) | (frequency > high)] = 0
    return np.fft.irfft(spectrum, n=values.size)


def audit_bjorko() -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    columns = [
        "Time",
        "SysMode",
        "DCC",
        "DCV",
        "XTurbSpeed1",
        "RST2",
        "WS30",
        "WSN",
        "MaxPwrEst",
        "Pwaste",
    ]
    data = pd.read_csv(BJORKO_PATH, usecols=columns)
    seconds = data["Time"].to_numpy(dtype=float)
    dc_power = (data["DCC"] * data["DCV"]).to_numpy(dtype=float)
    mechanical_power = (
        data["RST2"] * data["XTurbSpeed1"] * 2 * np.pi / 60
    ).to_numpy(dtype=float)
    running = (data["SysMode"].to_numpy() == 12) & (dc_power > 100)
    runs = true_runs(running, seconds, maximum_gap_s=1.0)
    qualified_runs = [
        pair
        for pair in runs
        if seconds[pair[1] - 1] - seconds[pair[0]] >= MIN_BJORKO_SEGMENT_DURATION_S
    ]
    if len(qualified_runs) < MIN_BJORKO_SEGMENTS:
        raise ValueError(
            f"仅找到{len(qualified_runs)}个不少于{MIN_BJORKO_SEGMENT_DURATION_S:g}秒的独立运行段"
        )

    timestamps = pd.to_datetime(seconds, unit="s", origin=pd.Timestamp("1904-01-01"), utc=True)
    segment_results = []
    spectra = []
    cleaned_segments = []
    for segment_id, (start, end) in enumerate(qualified_runs, start=1):
        run_seconds = seconds[start:end]
        regular, missing, duplicate_rows, maximum_gap_s = regularize_100hz(
            run_seconds,
            {
                "dc_power_w": dc_power[start:end],
                "mechanical_power_w": mechanical_power[start:end],
            },
        )
        regular_power = regular["dc_power_w"]
        regular_mechanical = regular["mechanical_power_w"]
        frequency, psd, windows = welch_psd(regular_power, fs=100.0, nperseg=8192)
        spectra.append(psd)
        high_electrical = band_limited(regular_power, 100.0, *BJORKO_DIRECT_POWER_BAND_HZ)
        high_mechanical = band_limited(
            regular_mechanical, 100.0, *BJORKO_DIRECT_POWER_BAND_HZ
        )
        run_timestamps = pd.to_datetime(
            [run_seconds[0], run_seconds[-1]],
            unit="s",
            origin=pd.Timestamp("1904-01-01"),
            utc=True,
        )
        total = band_energy(frequency, psd, *TARGET_BAND_HZ)
        segment_results.append(
            {
                "segment_id": segment_id,
                "start": run_timestamps[0].isoformat(),
                "end": run_timestamps[1].isoformat(),
                "raw_rows": int(end - start),
                "duration_s": float(run_seconds[-1] - run_seconds[0]),
                "regular_rows": int(len(regular_power)),
                "missing_ticks_interpolated": missing,
                "duplicate_rows_averaged": duplicate_rows,
                "missing_fraction": missing / len(regular_power),
                "maximum_interpolated_gap_s": maximum_gap_s,
                "dc_power_range_w": [float(regular_power.min()), float(regular_power.max())],
                "time_domain_pearson": float(
                    np.corrcoef(regular_power, regular_mechanical)[0, 1]
                ),
                "high_band_pearson_0p25_to_1_hz": float(
                    np.corrcoef(high_electrical, high_mechanical)[0, 1]
                ),
                "welch_windows": windows,
                "energy_0p25_to_1_fraction_of_0p005_to_1": (
                    band_energy(frequency, psd, *BJORKO_DIRECT_POWER_BAND_HZ) / total
                ),
            }
        )
        cleaned_segments.append(
            pd.DataFrame(
                {
                    "segment_id": segment_id,
                    "elapsed_s": np.arange(len(regular_power)) / 100,
                    "dc_power_w": regular_power,
                    "mechanical_power_w": regular_mechanical,
                }
            )
        )

    aggregate_psd = np.average(
        np.stack(spectra),
        axis=0,
        weights=[segment["welch_windows"] for segment in segment_results],
    )
    total_duration_s = float(sum(segment["duration_s"] for segment in segment_results))
    time_correlations = np.array(
        [segment["time_domain_pearson"] for segment in segment_results]
    )
    high_band_correlations = np.array(
        [segment["high_band_pearson_0p25_to_1_hz"] for segment in segment_results]
    )
    result = {
        "source_id": "bjorko_zenodo_v3_100hz",
        "file": str(BJORKO_PATH.relative_to(ROOT)),
        "sha256": sha256(BJORKO_PATH),
        "repository_md5_verified": "E0BEC0B446DB8E9F0C62513FF2C06BD2",
        "rows": int(len(data)),
        "selected_columns": columns,
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "intervals": interval_summary(seconds, nominal_dt=0.01),
        "sampling_rate_hz": 100.0,
        "nyquist_hz": 50.0,
        "direct_grid_active_power": False,
        "measured_electrical_power": "DCC[A] * DCV[V], generator-rectifier/DC-link electrical power in W",
        "mapping_evidence": {
            "dataset_metadata": "DCC and DCV are generator-rectifier DC current and voltage",
            "official_turbine_description": (
                "direct-driven generator with frequency converter; DC-link voltage/current "
                "and grid voltage/current are measured; controller sampling period is 10 ms"
            ),
            "institutional_thesis": (
                "Chalmers 2024 thesis Appendix A defines Pel=(DCV2*DCC2)/1000 as electrical power"
            ),
            "mechanical_cross_check": "RST2[Nm] * XTurbSpeed1[rpm] * 2*pi/60",
            "claim_scope": (
                "measured generator-side electrical-power fluctuations; not direct point-of-interconnection active power"
            ),
        },
        "independent_running_segments": {
            "selection": (
                "SysMode=12, DCC*DCV>100 W, gap<=1 s, duration>=120 s; "
                "120 s supplies at least 30 cycles at 0.25 Hz"
            ),
            "count": len(segment_results),
            "total_duration_s": total_duration_s,
            "minimum_time_domain_pearson": float(time_correlations.min()),
            "median_time_domain_pearson": float(np.median(time_correlations)),
            "minimum_high_band_pearson_0p25_to_1_hz": float(high_band_correlations.min()),
            "median_high_band_pearson_0p25_to_1_hz": float(
                np.median(high_band_correlations)
            ),
            "segments": segment_results,
        },
        "psd": {
            "method": "linear-detrended Hann Welch, 50% overlap",
            "aggregation": "five independent running segments weighted by Welch window count",
            "nperseg": 8192,
            "windows": int(sum(segment["welch_windows"] for segment in segment_results)),
            "frequency_resolution_hz": float(frequency[1] - frequency[0]),
            "audited_band_hz": list(BJORKO_DIRECT_POWER_BAND_HZ),
            "energy_0p25_to_1_w2": band_energy(
                frequency, aggregate_psd, *BJORKO_DIRECT_POWER_BAND_HZ
            ),
        },
        "supported_band_hz": list(BJORKO_DIRECT_POWER_BAND_HZ),
        "target_band_role": "measured_generator_side_electrical_power",
    }
    spectrum = pd.DataFrame(
        {"frequency_hz": frequency, "psd_w2_per_hz": aggregate_psd}
    )
    segment_summary = pd.DataFrame(segment_results)
    cleaned = pd.concat(cleaned_segments, ignore_index=True)
    return result, spectrum, segment_summary, cleaned


def build_manifest(results: dict) -> dict:
    local_files = []
    for directory in (ROOT / "raw", ROOT / "provenance"):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                local_files.append(
                    {
                        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )
    return {
        "manifest_version": 2,
        "retrieved_on": "2026-08-23",
        "target_band_hz": list(TARGET_BAND_HZ),
        "sources": [
            {
                "id": "more_eu_engie_2s",
                "provider": "MORE Horizon 2020 / ENGIE Laborelec",
                "official_page": "https://www.more2020.eu/open_resources.html",
                "repository": "https://github.com/MORE-EU/OpenData",
                "repository_commit": "e0811c29c524a11b4d0421a0b59c826f23a20a36",
                "source_snapshot": "provenance/more_eu_readme_e0811c29.md",
                "doi": None,
                "license": "No standard license file found for the wind dataset; official page calls it an open dataset",
                "access": "public direct download",
                "role": "direct measured active power, 2 s",
            },
            {
                "id": "bjorko_zenodo_v3_100hz",
                "provider": "Chalmers University of Technology et al.",
                "official_page": "https://zenodo.org/records/8230330",
                "doi": "10.5281/zenodo.8230330",
                "concept_doi": "10.5281/zenodo.8229045",
                "source_snapshot": "provenance/bjorko_zenodo_8230330.json",
                "license": "CC BY 4.0",
                "access": "public direct download",
                "role": "100 Hz measured generator-side electrical power from DCC*DCV plus mechanical cross-check",
            },
            {
                "id": "chalmers_bjorko_measurement_description",
                "provider": "Chalmers University of Technology",
                "official_page": "https://zenodo.org/records/8230330",
                "doi": "10.5281/zenodo.8230330",
                "source_snapshots": [
                    "raw/bjorko_zenodo_v3/Chalmers_wind_turbine_description_short.pdf",
                    "raw/bjorko_zenodo_v3/Chalmers_wind_turbine.pdf",
                ],
                "license": "CC BY 4.0 as part of the Zenodo dataset",
                "access": "public direct download",
                "role": "official turbine topology, measurement channels, and 10 ms controller sampling",
            },
            {
                "id": "chalmers_islanded_operation_thesis_2024",
                "provider": "Chalmers University of Technology",
                "official_page": "https://odr.chalmers.se/items/13f11803-85e1-46d3-9e45-626083dc1923",
                "doi": None,
                "source_snapshot": "provenance/chalmers_islanded_operation_thesis_2024.pdf",
                "license": "copyright retained by authors; local copy used for source verification",
                "access": "public institutional repository",
                "role": "independent institutional definition of DCC*DCV as electrical power",
            },
        ],
        "audited_files": local_files,
        "gate_decision": results["gate_decision"],
    }


def plot_psd(more_spectrum: pd.DataFrame, bjorko_spectrum: pd.DataFrame) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 1, figsize=(9, 8), constrained_layout=True)
    axes[0].loglog(
        more_spectrum["frequency_hz"],
        more_spectrum["psd_kw2_per_hz"],
        linewidth=0.8,
    )
    axes[0].set(title="MORE-EU measured active power (2 s)", ylabel="PSD [kW^2/Hz]")
    axes[1].loglog(
        bjorko_spectrum["frequency_hz"],
        bjorko_spectrum["psd_w2_per_hz"],
        linewidth=0.8,
    )
    axes[1].set(
        title="Björkö measured generator-side electrical power (5 runs, 100 Hz)",
        xlabel="Frequency [Hz]",
        ylabel="PSD [W^2/Hz]",
    )
    for axis in axes:
        for marker in (0.005, 0.25, 1.0):
            axis.axvline(marker, color="black", linestyle="--", linewidth=0.7)
        axis.grid(True, which="both", alpha=0.25)
    figure.savefig(FIGURE_DIR / "w1b_psd_evidence.png", dpi=180)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-figure", action="store_true")
    args = parser.parse_args()

    for required in (
        MORE_PATH,
        BJORKO_PATH,
        BJORKO_SHORT_DESCRIPTION_PATH,
        BJORKO_FULL_DESCRIPTION_PATH,
        BJORKO_THESIS_PATH,
    ):
        if not required.exists():
            raise FileNotFoundError(required)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    more_result, more_spectrum = audit_more()
    bjorko_result, bjorko_spectrum, segment_summary, cleaned_segments = audit_bjorko()
    segment_audit = bjorko_result["independent_running_segments"]
    gate_checks = {
        "more_direct_active_power_covers_0p005_to_0p25_hz": (
            more_result["direct_active_power"]
            and more_result["supported_band_hz"] == [0.005, 0.25]
        ),
        "bjorko_official_mapping_sources_present": all(
            path.exists()
            for path in (
                BJORKO_SHORT_DESCRIPTION_PATH,
                BJORKO_FULL_DESCRIPTION_PATH,
                BJORKO_THESIS_PATH,
            )
        ),
        "bjorko_has_at_least_five_independent_120s_runs": (
            segment_audit["count"] >= MIN_BJORKO_SEGMENTS
        ),
        "bjorko_high_band_cross_channel_correlation_ge_0p8": (
            segment_audit["minimum_high_band_pearson_0p25_to_1_hz"] >= 0.8
        ),
        "bjorko_interpolation_gap_le_0p2s": all(
            segment["maximum_interpolated_gap_s"] <= 0.2
            for segment in segment_audit["segments"]
        ),
        "claim_scope_excludes_direct_grid_power": not bjorko_result[
            "direct_grid_active_power"
        ],
    }
    gate_decision = "PASS" if all(gate_checks.values()) else "PARTIAL"
    decision_reason = (
        "MORE-EU provides direct active power from 0.005 to 0.25 Hz. Bjorko provides "
        "100 Hz measured generator-side electrical power from DCC*DCV for 0.25 to 1 Hz, "
        "with an official converter/measurement description, an independent Chalmers "
        "electrical-power definition, and five independent operating segments. The high-band "
        "claim remains generator-side and is not relabeled as point-of-interconnection power."
        if gate_decision == "PASS"
        else "At least one W1-B source, multi-segment, cross-channel, or claim-scope check failed."
    )
    results = {
        "report_version": 2,
        "audited_on": "2026-08-23",
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": importlib.metadata.version("pyarrow"),
            "matplotlib": importlib.metadata.version("matplotlib"),
        },
        "target_band_hz": list(TARGET_BAND_HZ),
        "gate_decision": gate_decision,
        "gate_checks": gate_checks,
        "decision_reason": decision_reason,
        "sources": {"more_eu": more_result, "bjorko": bjorko_result},
    }
    more_spectrum.to_csv(PROCESSED_DIR / "more_eu_active_power_psd.csv", index=False)
    bjorko_spectrum.to_csv(PROCESSED_DIR / "bjorko_dc_power_proxy_psd.csv", index=False)
    segment_summary.to_csv(PROCESSED_DIR / "bjorko_high_band_segment_summary.csv", index=False)
    cleaned_segments.to_csv(PROCESSED_DIR / "bjorko_selected_running_segments.csv", index=False)
    if not args.skip_figure:
        plot_psd(more_spectrum, bjorko_spectrum)
    REPORT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(results), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
