import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import fmean


EXPECTED_HASHES = {
    "download.zip": "2F51105D9566BCC728A3A981FB89D3EB15F73A631FF454EE3C40AC6AD2F8D4DA",
    "Raw_Data.rar": "B7DAC380F01FE2E4D55CEB4365130FCF4E7D7EAED2F9BD2F3A3DBBD0E7C0953B",
    "NWP.csv": "19C11D2A64924D2B48639780F5BCBE435FBAF9A7D4F886ACD9AA280C6707C722",
    "wind farm historical data.csv": "2AB798258A566F2F2C6A4BCAB0023E6485E34C08C432A49D2C0F2D4DE4E09E6F",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def numeric_summary(values):
    clean = [float(value) for value in values if value not in (None, "")]
    return {
        "count": len(clean),
        "min": min(clean),
        "max": max(clean),
        "mean": fmean(clean),
    }


def interval_counts(timestamps):
    ordered = sorted(timestamps)
    intervals = Counter(
        int((current - previous).total_seconds())
        for previous, current in zip(ordered, ordered[1:])
    )
    return {str(key): value for key, value in sorted(intervals.items())}


def audit_historical(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    timestamps = [datetime.strptime(row["Date"], "%d/%m/%Y %H:%M:%S") for row in rows]
    missing = {
        field: sum(row[field] == "" for row in rows)
        for field in ("Date", "Speed", "Direction", "Energy")
    }
    energy = numeric_summary(row["Energy"] for row in rows)
    return {
        "rows": len(rows),
        "columns": list(rows[0]),
        "start": min(timestamps).isoformat(sep=" "),
        "end": max(timestamps).isoformat(sep=" "),
        "timezone": "not documented in the dataset files",
        "duplicate_timestamps": len(timestamps) - len(set(timestamps)),
        "interval_seconds": interval_counts(timestamps),
        "missing": missing,
        "speed": numeric_summary(row["Speed"] for row in rows),
        "direction": numeric_summary(row["Direction"] for row in rows),
        "energy": energy,
        "equivalent_average_power_mw_if_energy_is_kwh_per_record": {
            "min": energy["min"] * 6.0 / 1000.0,
            "max": energy["max"] * 6.0 / 1000.0,
            "mean": energy["mean"] * 6.0 / 1000.0,
            "status": "derived under an unverified unit interpretation; not a raw field",
        },
    }


def audit_nwp(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    timestamps = [datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S") for row in rows]
    fields = list(rows[0])
    return {
        "rows": len(rows),
        "columns": fields,
        "start": min(timestamps).isoformat(sep=" "),
        "end": max(timestamps).isoformat(sep=" "),
        "timezone": "not documented in the dataset files",
        "duplicate_timestamps": len(timestamps) - len(set(timestamps)),
        "interval_seconds": interval_counts(timestamps),
        "missing": {field: sum(row[field] == "" for row in rows) for field in fields},
    }


def audit_legacy(project_root):
    assets = []
    for path in sorted(project_root.glob("**/wind_disturbance_400s_pu.npy")):
        record = {
            "path": str(path.relative_to(project_root)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "provenance_status": "unresolved: raw source and generation script not found",
        }
        try:
            import numpy as np

            values = np.load(str(path))
            record.update(
                {
                    "shape": list(values.shape),
                    "dtype": str(values.dtype),
                    "min": float(values.min()),
                    "max": float(values.max()),
                    "mean": float(values.mean()),
                    "std": float(values.std()),
                }
            )
        except Exception as error:
            record["numeric_audit_error"] = str(error)
        assets.append(record)
    return assets


def main():
    parser = argparse.ArgumentParser(description="Audit W0 wind-data provenance and structure.")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    model_root = Path(__file__).resolve().parents[1]
    data_root = args.data_root or model_root / "data" / "wind"
    project_root = args.project_root or model_root.parent
    out_path = args.out or data_root / "manifests" / "w0_audit.json"
    raw_dir = data_root / "raw" / "sotavento_mendeley_v1"

    files = {}
    hash_pass = True
    for name, expected in EXPECTED_HASHES.items():
        path = raw_dir / name
        exists = path.is_file()
        actual = sha256(path) if exists else None
        files[name] = {
            "exists": exists,
            "bytes": path.stat().st_size if exists else None,
            "sha256": actual,
            "expected_sha256": expected,
            "hash_pass": actual == expected,
        }
        hash_pass = hash_pass and actual == expected

    report = {
        "dataset_id": "mendeley:vtsgxnwswn:1",
        "doi": "10.17632/vtsgxnwswn.1",
        "license": "CC BY 4.0 (landing-page statement)",
        "files": files,
        "historical": audit_historical(raw_dir / "wind farm historical data.csv"),
        "nwp": audit_nwp(raw_dir / "NWP.csv"),
        "legacy_assets": audit_legacy(project_root),
        "w0_acquisition_pass": hash_pass,
        "readiness": {
            "wind_farm_10_minute_statistics": hash_pass,
            "time_ordered_forecasting": hash_pass,
            "direct_0_5_second_control_input": False,
        },
        "open_issues": [
            "Energy field unit is not explicit in the downloaded CSV or landing-page metadata.",
            "Timestamp timezone is not documented in the downloaded files.",
            "The 10-minute wind-farm series cannot substantiate 0.5-second high-frequency fluctuations.",
            "Legacy 400-second NPY trajectories still lack their raw-source and generation chain.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not hash_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
