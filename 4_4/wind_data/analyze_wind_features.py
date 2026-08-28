import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw


SAMPLE_SECONDS = 600
INSTALLED_CAPACITY_MW = 17.56
ENERGY_CAPACITY_PER_RECORD_KWH = INSTALLED_CAPACITY_MW * 1000.0 / 6.0
SHORT_GAP_LIMIT_RECORDS = 1


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def finite_percentiles(values, percentiles):
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    return {
        f"p{percentile:g}": float(np.percentile(clean, percentile))
        for percentile in percentiles
    }


def missing_runs(series):
    missing = series.isna().to_numpy()
    padded = np.pad(missing.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return [(int(start), int(end), int(end - start)) for start, end in zip(starts, ends)]


def interpolate_short_interior_gaps(series, max_records):
    result = series.copy()
    interpolated = pd.Series(False, index=series.index)
    for start, end, length in missing_runs(series):
        if (
            length <= max_records
            and start > 0
            and end < len(series)
            and pd.notna(series.iloc[start - 1])
            and pd.notna(series.iloc[end])
        ):
            left = float(series.iloc[start - 1])
            right = float(series.iloc[end])
            for offset in range(length):
                fraction = (offset + 1) / (length + 1)
                result.iloc[start + offset] = left + fraction * (right - left)
                interpolated.iloc[start + offset] = True
    return result, interpolated


def assign_split(timestamp):
    if timestamp < pd.Timestamp("2016-07-01"):
        return "train"
    if timestamp < pd.Timestamp("2016-09-01"):
        return "validation"
    if timestamp < pd.Timestamp("2016-11-01"):
        return "boundary_construction"
    return "final_extrapolation"


def longest_valid_segment(series):
    valid = series.notna().to_numpy()
    padded = np.pad(valid.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    if len(starts) == 0:
        raise ValueError("No valid wind-power segment is available.")
    lengths = ends - starts
    index = int(np.argmax(lengths))
    return int(starts[index]), int(ends[index])


def autocorrelation_metrics(values, max_lag):
    values = np.asarray(values, dtype=float)
    centered = values - np.mean(values)
    variance = float(np.var(centered))
    fft_length = 1 << (2 * len(centered) - 1).bit_length()
    spectrum = np.fft.rfft(centered, n=fft_length)
    numerator = np.fft.irfft(spectrum * np.conjugate(spectrum), n=fft_length)[: max_lag + 1]
    denominator = variance * np.arange(len(centered), len(centered) - len(numerator), -1)
    acf = numerator / denominator

    def first_at_or_below(threshold):
        positions = np.flatnonzero(acf <= threshold)
        return int(positions[0]) if len(positions) else None

    return acf, {
        "first_lag_at_or_below_0_5_records": first_at_or_below(0.5),
        "first_lag_at_or_below_1_over_e_records": first_at_or_below(1.0 / np.e),
        "first_nonpositive_lag_records": first_at_or_below(0.0),
    }


def power_spectrum(values):
    nperseg = min(4096, len(values))
    values = np.asarray(values, dtype=float)
    step = nperseg // 2
    window = np.hanning(nperseg)
    window_energy = np.sum(window**2)
    sample_rate_hz = 1.0 / SAMPLE_SECONDS
    segment_density = []
    time_index = np.arange(nperseg, dtype=float)
    for start in range(0, len(values) - nperseg + 1, step):
        block = values[start : start + nperseg]
        slope, intercept = np.polyfit(time_index, block, 1)
        transformed = np.fft.rfft((block - (slope * time_index + intercept)) * window)
        density = np.abs(transformed) ** 2 / (sample_rate_hz * window_energy)
        if nperseg % 2 == 0:
            density[1:-1] *= 2.0
        else:
            density[1:] *= 2.0
        segment_density.append(density)
    density = np.mean(segment_density, axis=0)
    frequencies = np.fft.rfftfreq(nperseg, d=SAMPLE_SECONDS)
    positive = frequencies > 0
    frequencies = frequencies[positive]
    density = density[positive]
    bin_width = float(frequencies[1] - frequencies[0]) if len(frequencies) > 1 else np.nan
    spectral_energy = density * bin_width
    cumulative = np.cumsum(spectral_energy) / np.sum(spectral_energy)

    quantile_frequencies = {}
    for quantile in (0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
        index = int(np.searchsorted(cumulative, quantile, side="left"))
        index = min(index, len(frequencies) - 1)
        quantile_frequencies[f"q{quantile:g}_hz"] = float(frequencies[index])
    return frequencies, density, quantile_frequencies, nperseg


def complete_window_difference(series, periods):
    difference = series.diff(periods)
    complete = series.notna().rolling(periods + 1).sum().eq(periods + 1)
    return difference.where(complete)


def select_event_windows(frame, window_records=36):
    calibration = frame[frame["split"].isin(["train", "validation"])].copy()
    records = []
    for start in range(0, len(calibration) - window_records + 1, window_records):
        window = calibration.iloc[start : start + window_records]
        if len(window) != window_records or window["output_pu"].isna().any():
            continue
        values = window["output_pu"].to_numpy()
        ramps = np.diff(values)
        records.append(
            {
                "start": window["timestamp"].iloc[0],
                "end": window["timestamp"].iloc[-1],
                "mean": float(np.mean(values)),
                "std": float(np.std(values)),
                "max_abs_ramp": float(np.max(np.abs(ramps))),
                "signed_extreme_ramp": float(ramps[np.argmax(np.abs(ramps))]),
            }
        )
    candidates = pd.DataFrame(records)
    if candidates.empty:
        raise ValueError("No complete calibration event windows were found.")

    features = ["mean", "std", "max_abs_ramp"]
    medians = candidates[features].median()
    scales = (candidates[features].quantile(0.75) - candidates[features].quantile(0.25)).replace(0, 1.0)
    candidates["typical_score"] = ((candidates[features] - medians) / scales).pow(2).sum(axis=1)

    typical = candidates.loc[candidates["typical_score"].idxmin()]
    strong = candidates.loc[candidates["max_abs_ramp"].idxmax()]
    positive = candidates.loc[candidates["signed_extreme_ramp"].idxmax()]
    negative = candidates.loc[candidates["signed_extreme_ramp"].idxmin()]

    def serialize(label, row):
        return {
            "label": label,
            "start": row["start"].isoformat(),
            "end": row["end"].isoformat(),
            "duration_records": window_records,
            "selection_pool": "train+validation only",
            "mean_pu": float(row["mean"]),
            "std_pu": float(row["std"]),
            "max_abs_10min_ramp_pu": float(row["max_abs_ramp"]),
            "signed_extreme_10min_ramp_pu": float(row["signed_extreme_ramp"]),
        }

    return [
        serialize("typical_6h", typical),
        serialize("strongest_absolute_ramp_6h", strong),
        serialize("strongest_positive_ramp_6h", positive),
        serialize("strongest_negative_ramp_6h", negative),
    ]


def draw_axes(draw, box, title, xlabel, ylabel):
    left, top, right, bottom = box
    draw.rectangle(box, outline="black", width=2)
    draw.text((left, top - 25), title, fill="black")
    draw.text(((left + right) // 2 - 50, bottom + 10), xlabel, fill="black")
    draw.text((5, (top + bottom) // 2), ylabel, fill="black")


def scaled_points(x, y, box, log_x=False, log_y=False):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    if log_x:
        finite &= x > 0
    if log_y:
        finite &= y > 0
    x = x[finite]
    y = y[finite]
    if log_x:
        x = np.log10(x)
    if log_y:
        y = np.log10(y)
    left, top, right, bottom = box
    x_span = np.ptp(x) or 1.0
    y_span = np.ptp(y) or 1.0
    px = left + (x - np.min(x)) / x_span * (right - left)
    py = bottom - (y - np.min(y)) / y_span * (bottom - top)
    return [(int(a), int(b)) for a, b in zip(px, py)]


def make_figures(frame, frequencies, density, acf, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    observed = frame.dropna(subset=["output_pu"])

    canvas = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(canvas)
    upper = (80, 60, 1160, 380)
    lower = (80, 460, 1160, 740)
    draw_axes(draw, upper, "Sotavento 2016 wind-farm output", "Time index", "Output p.u.")
    sample_positions = np.linspace(0, len(observed) - 1, min(3000, len(observed))).astype(int)
    points = scaled_points(sample_positions, observed["output_pu"].to_numpy()[sample_positions], upper)
    draw.line(points, fill="#4472C4", width=1)
    histogram, edges = np.histogram(observed["output_pu"], bins=60, density=True)
    draw_axes(draw, lower, "Wind-output distribution", "Output p.u.", "Density")
    draw.line(scaled_points((edges[:-1] + edges[1:]) / 2, histogram, lower), fill="#4472C4", width=3)
    canvas.save(output_dir / "w1_output_distribution.png", optimize=False)

    canvas = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(canvas)
    upper = (80, 60, 1160, 380)
    lower = (80, 460, 1160, 740)
    draw_axes(draw, upper, "Welch spectrum on longest complete segment", "Frequency Hz (log)", "PSD (log)")
    draw.line(scaled_points(frequencies, density, upper, log_x=True, log_y=True), fill="#4472C4", width=2)
    lags_hours = np.arange(len(acf)) * SAMPLE_SECONDS / 3600.0
    display = lags_hours <= 168
    draw_axes(draw, lower, "Autocorrelation", "Lag hours", "ACF")
    draw.line(scaled_points(lags_hours[display], acf[display], lower), fill="#C00000", width=2)
    canvas.save(output_dir / "w1_spectrum_autocorrelation.png", optimize=False)

    canvas = Image.new("RGB", (1000, 520), "white")
    draw = ImageDraw.Draw(canvas)
    box = (80, 60, 960, 450)
    ramps = frame["ramp_10min_pu"].dropna().to_numpy()
    histogram, edges = np.histogram(ramps, bins=100, density=True)
    draw_axes(draw, box, "Wind ramp distribution", "10-minute change p.u.", "Density")
    draw.line(scaled_points((edges[:-1] + edges[1:]) / 2, histogram, box), fill="#70AD47", width=3)
    canvas.save(output_dir / "w1_ramp_distribution.png", optimize=False)


def main():
    parser = argparse.ArgumentParser(description="Prepare and analyze W1 wind-power features.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--processed", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--figures", type=Path)
    args = parser.parse_args()

    model_root = Path(__file__).resolve().parents[1]
    data_root = model_root / "data" / "wind"
    input_path = args.input or data_root / "raw" / "sotavento_mendeley_v1" / "wind farm historical data.csv"
    processed_path = args.processed or data_root / "processed" / "sotavento_2016_clean.csv"
    report_path = args.report or data_root / "manifests" / "w1_feature_report.json"
    figures_dir = args.figures or data_root / "processed" / "figures"

    raw = pd.read_csv(input_path)
    frame = pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(raw["Date"], format="%d/%m/%Y %H:%M:%S", errors="raise")
    frame["speed_mps"] = pd.to_numeric(raw["Speed"], errors="coerce")
    frame["direction_deg"] = pd.to_numeric(raw["Direction"], errors="coerce")
    frame["energy_raw"] = pd.to_numeric(raw["Energy"], errors="coerce")

    if not frame["timestamp"].is_monotonic_increasing:
        raise ValueError("Timestamps are not monotonically increasing.")
    if frame["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps were found.")
    intervals = frame["timestamp"].diff().dropna().dt.total_seconds()
    if not intervals.eq(SAMPLE_SECONDS).all():
        raise ValueError("The historical series is not a regular 10-minute grid.")

    invalid_speed = frame["speed_mps"].notna() & (frame["speed_mps"] < 0)
    invalid_direction = frame["direction_deg"].notna() & ~frame["direction_deg"].between(0, 360, inclusive="both")
    invalid_energy = frame["energy_raw"].notna() & (frame["energy_raw"] < 0)
    frame.loc[invalid_speed, "speed_mps"] = np.nan
    frame.loc[invalid_direction, "direction_deg"] = np.nan
    frame.loc[invalid_energy, "energy_raw"] = np.nan

    original_energy_missing = frame["energy_raw"].isna()
    frame["energy_clean"], interpolated = interpolate_short_interior_gaps(
        frame["energy_raw"], SHORT_GAP_LIMIT_RECORDS
    )
    frame["quality"] = np.select(
        [interpolated, frame["energy_clean"].isna()],
        ["interpolated_single_gap", "missing_long_gap"],
        default="observed",
    )
    frame["output_pu"] = frame["energy_clean"] / ENERGY_CAPACITY_PER_RECORD_KWH
    frame["split"] = frame["timestamp"].map(assign_split)
    frame["trend_6h_pu"] = frame["output_pu"].rolling(36, center=True, min_periods=36).mean()
    frame["fluctuation_6h_pu"] = frame["output_pu"] - frame["trend_6h_pu"]
    frame["ramp_10min_pu"] = complete_window_difference(frame["output_pu"], 1)
    frame["ramp_1h_pu"] = complete_window_difference(frame["output_pu"], 6)
    frame["ramp_6h_pu"] = complete_window_difference(frame["output_pu"], 36)
    frame["ramp_24h_pu"] = complete_window_difference(frame["output_pu"], 144)

    calibration = frame[frame["split"].isin(["train", "validation"])].copy()
    start, end = longest_valid_segment(calibration["output_pu"])
    segment = calibration.iloc[start:end]
    segment_values = segment["output_pu"].to_numpy()
    frequencies, density, spectral_quantiles, nperseg = power_spectrum(segment_values)
    max_lag = min(14 * 24 * 6, len(segment_values) - 1)
    acf, acf_metrics = autocorrelation_metrics(segment_values, max_lag)
    for key, records in list(acf_metrics.items()):
        acf_metrics[key.replace("_records", "_hours")] = (
            None if records is None else float(records * SAMPLE_SECONDS / 3600.0)
        )

    amplitude_percentiles = finite_percentiles(calibration["fluctuation_6h_pu"].abs(), [50, 75, 90, 95, 99])
    ramp_percentiles = finite_percentiles(calibration["ramp_10min_pu"].abs(), [50, 75, 90, 95, 99])
    event_windows = select_event_windows(frame)

    spectral_grid = sorted(set(spectral_quantiles.values()))
    initial_fast_grid = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
    nyquist_hz = 1.0 / (2.0 * SAMPLE_SECONDS)
    split_summary = {}
    for name, group in frame.groupby("split", sort=False):
        split_summary[name] = {
            "start": group["timestamp"].min().isoformat(),
            "end": group["timestamp"].max().isoformat(),
            "records": int(len(group)),
            "observed_or_interpolated_energy_records": int(group["output_pu"].notna().sum()),
            "missing_energy_records": int(group["output_pu"].isna().sum()),
        }

    processed_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed_path, index=False, date_format="%Y-%m-%dT%H:%M:%S", float_format="%.10g")
    make_figures(calibration, frequencies, density, acf, figures_dir)

    report = {
        "dataset_id": "mendeley:vtsgxnwswn:1",
        "input": {
            "path": str(input_path),
            "sha256": sha256(input_path),
            "records": int(len(frame)),
            "sample_seconds": SAMPLE_SECONDS,
            "timezone": "not documented; timestamps retained as timezone-naive",
        },
        "cleaning_contract": {
            "negative_speed_direction_energy": "set to missing",
            "direction_range_deg": [0, 360],
            "short_gap_interpolation_limit_records": SHORT_GAP_LIMIT_RECORDS,
            "short_gap_interpolation_limit_minutes": SHORT_GAP_LIMIT_RECORDS * 10,
            "long_gaps": "preserved as missing; never bridged for ramp, ACF, or PSD",
            "energy_unit": "not explicit in source files",
            "output_pu_interpretation": "Energy treated as kWh per 10-minute record and divided by 17.56 MW x 1/6 h; this remains a stated working interpretation",
        },
        "quality": {
            "original_missing_energy_records": int(original_energy_missing.sum()),
            "interpolated_energy_records": int(interpolated.sum()),
            "remaining_missing_energy_records": int(frame["energy_clean"].isna().sum()),
            "invalid_speed_records": int(invalid_speed.sum()),
            "invalid_direction_records": int(invalid_direction.sum()),
            "invalid_energy_records": int(invalid_energy.sum()),
            "energy_missing_runs_records": [length for _, _, length in missing_runs(pd.to_numeric(raw["Energy"], errors="coerce"))],
        },
        "time_splits": split_summary,
        "descriptive_statistics_calibration_pool_only": {
            "output_pu": finite_percentiles(calibration["output_pu"], [1, 5, 10, 25, 50, 75, 90, 95, 99]),
            "absolute_10min_ramp_pu": finite_percentiles(calibration["ramp_10min_pu"].abs(), [50, 75, 90, 95, 99, 99.9]),
            "signed_10min_ramp_pu": finite_percentiles(calibration["ramp_10min_pu"], [0.1, 1, 5, 50, 95, 99, 99.9]),
            "absolute_1h_ramp_pu": finite_percentiles(calibration["ramp_1h_pu"].abs(), [50, 75, 90, 95, 99]),
            "absolute_6h_ramp_pu": finite_percentiles(calibration["ramp_6h_pu"].abs(), [50, 75, 90, 95, 99]),
            "absolute_24h_ramp_pu": finite_percentiles(calibration["ramp_24h_pu"].abs(), [50, 75, 90, 95, 99]),
        },
        "acf_psd": {
            "analysis_segment_start": segment["timestamp"].iloc[0].isoformat(),
            "analysis_segment_end": segment["timestamp"].iloc[-1].isoformat(),
            "analysis_segment_records": int(len(segment)),
            "analysis_segment_days": float(len(segment) * SAMPLE_SECONDS / 86400.0),
            "welch_nperseg": nperseg,
            "frequency_resolution_hz": float(frequencies[1] - frequencies[0]),
            "nyquist_hz": nyquist_hz,
            "spectral_energy_quantile_frequencies_hz": spectral_quantiles,
            "autocorrelation": acf_metrics,
        },
        "scenario_calibration": {
            "calibration_pool": "train+validation (2016-01-01 through 2016-08-31); later splits untouched",
            "sine_amplitude_candidates_abs_fluctuation_pu": amplitude_percentiles,
            "sine_frequency_candidates_hz": spectral_grid,
            "ramp_rate_candidates_abs_pu_per_10min": ramp_percentiles,
            "event_windows": event_windows,
        },
        "resolution_gate": {
            "existing_e2_fast_grid_hz": initial_fast_grid,
            "all_existing_fast_grid_above_nyquist": all(value > nyquist_hz for value in initial_fast_grid),
            "direct_0_5_second_input_supported": False,
            "decision": "Use this dataset only for slow-scale amplitude, ramp, correlation, and spectral calibration. Do not interpolate it to claim 0.005-1 Hz evidence. A licensed high-frequency source or an explicitly synthetic high-frequency model is required for the fast E2 band.",
        },
        "processed": {
            "path": str(processed_path),
            "sha256": sha256(processed_path),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
