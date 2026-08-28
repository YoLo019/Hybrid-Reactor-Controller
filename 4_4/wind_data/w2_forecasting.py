"""准备并执行W2多时域风电功率预测基线。"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    PROJECT_ROOT
    / "research_execution"
    / "04_experiments"
    / "configs"
    / "w2_prediction_v1.json"
)


def sha256(path: Path) -> str:
    """计算需要跨处理阶段核验的数据文件身份。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    horizons = config["task"]["horizon_steps"]
    minutes = config["task"]["horizon_minutes"]
    if len(horizons) != len(minutes) or any(
        step * config["dataset"]["sample_seconds"] != minute * 60
        for step, minute in zip(horizons, minutes)
    ):
        raise ValueError("预测步数与分钟数配置不一致")
    return config


def load_and_validate_dataset(config: dict) -> tuple[pd.DataFrame, dict]:
    dataset_path = project_path(config["dataset"]["path"])
    feature_report_path = project_path(config["dataset"]["feature_report"])
    feature_report = json.loads(feature_report_path.read_text(encoding="utf-8"))
    expected_hash = feature_report["processed"]["sha256"]
    actual_hash = sha256(dataset_path)
    if actual_hash != expected_hash:
        raise ValueError("W2输入文件与W1冻结身份不一致")

    frame = pd.read_csv(dataset_path)
    required_columns = {
        config["dataset"]["timestamp_column"],
        config["dataset"]["target_column"],
        "split",
    }
    missing = required_columns - set(frame.columns)
    if missing:
        raise ValueError(f"W2输入缺少字段: {sorted(missing)}")

    frame["timestamp"] = pd.to_datetime(frame[config["dataset"]["timestamp_column"]])
    if not frame["timestamp"].is_monotonic_increasing or frame["timestamp"].duplicated().any():
        raise ValueError("W2时间轴必须严格递增且无重复")
    intervals = frame["timestamp"].diff().dropna().dt.total_seconds().to_numpy()
    sample_seconds = config["dataset"]["sample_seconds"]
    if not np.all(intervals == sample_seconds):
        raise ValueError("W2时间轴不是完整的10分钟规则网格")

    for split in ("train", "validation", "boundary_construction", "final_extrapolation"):
        expected_start, expected_end = config["split_contract"][split]
        subset = frame[frame["split"] == split]
        if subset.empty:
            raise ValueError(f"数据中缺少切分: {split}")
        actual_range = [subset["timestamp"].iloc[0].isoformat(), subset["timestamp"].iloc[-1].isoformat()]
        if actual_range != [expected_start, expected_end]:
            raise ValueError(f"切分{split}的时间范围与冻结配置不一致: {actual_range}")
    return frame, {
        "dataset_path": str(dataset_path),
        "dataset_sha256": actual_hash,
        "feature_report_path": str(feature_report_path),
        "feature_report_sha256": sha256(feature_report_path),
    }


def build_sample_index(
    frame: pd.DataFrame, config: dict, allowed_splits: list[str] | None = None
) -> pd.DataFrame:
    """只生成上下文、发布时刻和目标均位于同一切分的样本。"""
    split_contract = config["split_contract"]
    allowed = allowed_splits or split_contract["preparation_allowed_splits"]
    locked = set(split_contract["locked_splits"])
    if locked.intersection(allowed):
        raise ValueError("准备阶段不得读取boundary_construction或final_extrapolation样本")

    target = frame[config["dataset"]["target_column"]].to_numpy(dtype=float)
    splits = frame["split"].to_numpy()
    timestamps = frame["timestamp"].to_numpy()
    context_steps = int(config["task"]["context_steps"])
    horizons = [int(value) for value in config["task"]["horizon_steps"]]
    finite = np.isfinite(target).astype(np.int64)
    valid_history_count = np.convolve(finite, np.ones(context_steps, dtype=np.int64), mode="full")[
        : len(frame)
    ]

    rows = []
    for issue_index in range(context_steps - 1, len(frame)):
        split = str(splits[issue_index])
        if split not in allowed or valid_history_count[issue_index] != context_steps:
            continue
        context_start = issue_index - context_steps + 1
        if splits[context_start] != split:
            continue
        for horizon in horizons:
            target_index = issue_index + horizon
            if target_index >= len(frame):
                continue
            if splits[target_index] != split or not np.isfinite(target[target_index]):
                continue
            rows.append(
                {
                    "sample_id": f"{split}_h{horizon:03d}_i{issue_index:06d}",
                    "split": split,
                    "horizon_steps": horizon,
                    "issue_index": issue_index,
                    "target_index": target_index,
                    "context_start_time": pd.Timestamp(timestamps[context_start]).isoformat(),
                    "issue_time": pd.Timestamp(timestamps[issue_index]).isoformat(),
                    "target_time": pd.Timestamp(timestamps[target_index]).isoformat(),
                }
            )
    index = pd.DataFrame(rows)
    if index.empty:
        raise ValueError("W2没有生成任何有效样本")
    return index


def design_matrix(
    frame: pd.DataFrame, samples: pd.DataFrame, config: dict
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    target = frame[config["dataset"]["target_column"]].to_numpy(dtype=float)
    issue_indices = samples["issue_index"].to_numpy(dtype=np.int64)
    target_indices = samples["target_index"].to_numpy(dtype=np.int64)
    lags = np.asarray(
        config["systems"]["ridge_direct_ar"]["feature_lags_steps"], dtype=np.int64
    )
    features = target[issue_indices[:, None] - lags[None, :]]
    return features, target[target_indices], target[issue_indices]


def fit_ridge(features: np.ndarray, targets: np.ndarray, alpha: float) -> dict:
    """用训练集标准化参数拟合带截距的闭式ridge。"""
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale == 0] = 1.0
    standardized = (features - mean) / scale
    target_mean = float(targets.mean())
    centered_target = targets - target_mean
    gram = standardized.T @ standardized
    rhs = standardized.T @ centered_target
    if alpha == 0:
        coefficient = np.linalg.lstsq(standardized, centered_target, rcond=None)[0]
    else:
        coefficient = np.linalg.solve(gram + alpha * np.eye(gram.shape[0]), rhs)
    return {
        "feature_mean": mean,
        "feature_scale": scale,
        "coefficient": coefficient,
        "target_mean": target_mean,
        "alpha": float(alpha),
    }


def predict_ridge(model: dict, features: np.ndarray) -> np.ndarray:
    standardized = (features - model["feature_mean"]) / model["feature_scale"]
    return model["target_mean"] + standardized @ model["coefficient"]


def forecast_metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
    issue_value: np.ndarray,
    ramp_threshold: float,
) -> dict:
    error = forecast - actual
    actual_event = np.abs(actual - issue_value) >= ramp_threshold
    forecast_event = np.abs(forecast - issue_value) >= ramp_threshold
    true_positive = int(np.logical_and(actual_event, forecast_event).sum())
    false_positive = int(np.logical_and(~actual_event, forecast_event).sum())
    false_negative = int(np.logical_and(actual_event, ~forecast_event).sum())
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    event_error = np.abs(error[actual_event])
    return {
        "samples": int(actual.size),
        "mae_pu": float(np.mean(np.abs(error))),
        "rmse_pu": float(np.sqrt(np.mean(np.square(error)))),
        "ramp_event_threshold_pu": float(ramp_threshold),
        "ramp_event_count": int(actual_event.sum()),
        "ramp_event_precision": float(precision),
        "ramp_event_recall": float(recall),
        "ramp_event_f1": float(f1),
        "ramp_event_mae_pu": float(event_error.mean()) if event_error.size else None,
        "out_of_range_fraction": float(np.mean((forecast < 0.0) | (forecast > 1.0))),
    }


def run_forecasts(
    frame: pd.DataFrame, sample_index: pd.DataFrame, config: dict, stage: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    smoke = config["smoke"]
    metric_rows = []
    forecast_rows = []
    selected_models = {}
    for horizon in config["task"]["horizon_steps"]:
        train_samples = sample_index[
            (sample_index["split"] == "train")
            & (sample_index["horizon_steps"] == horizon)
        ]
        validation_samples = sample_index[
            (sample_index["split"] == "validation")
            & (sample_index["horizon_steps"] == horizon)
        ]
        if stage == "smoke":
            train_samples = train_samples.head(smoke["max_train_samples_per_horizon"])
            validation_samples = validation_samples.head(
                smoke["max_validation_samples_per_horizon"]
            )
        train_features, train_target, train_issue = design_matrix(
            frame, train_samples, config
        )
        validation_features, validation_target, validation_issue = design_matrix(
            frame, validation_samples, config
        )
        ramp_threshold = float(np.quantile(np.abs(train_target - train_issue), 0.9))
        persistence = validation_issue.copy()
        persistence_metrics = forecast_metrics(
            validation_target, persistence, validation_issue, ramp_threshold
        )
        metric_rows.append(
            {
                "stage": stage,
                "horizon_steps": horizon,
                "forecast_type": "persistence",
                "alpha": None,
                **persistence_metrics,
            }
        )

        candidates = []
        for alpha in config["systems"]["ridge_direct_ar"]["alpha_candidates"]:
            model = fit_ridge(train_features, train_target, float(alpha))
            forecast = predict_ridge(model, validation_features)
            metrics = forecast_metrics(
                validation_target, forecast, validation_issue, ramp_threshold
            )
            candidates.append((metrics["rmse_pu"], float(alpha), model, forecast, metrics))
        _, alpha, model, forecast, metrics = min(candidates, key=lambda item: (item[0], item[1]))
        improvement = 1.0 - metrics["rmse_pu"] / persistence_metrics["rmse_pu"]
        metric_rows.append(
            {
                "stage": stage,
                "horizon_steps": horizon,
                "forecast_type": "ridge_direct_ar",
                "alpha": alpha,
                "rmse_improvement_vs_persistence": float(improvement),
                **metrics,
            }
        )
        selected_models[str(horizon)] = {
            "alpha": alpha,
            "feature_mean": model["feature_mean"].tolist(),
            "feature_scale": model["feature_scale"].tolist(),
            "coefficient": model["coefficient"].tolist(),
            "target_mean": model["target_mean"],
            "ramp_event_threshold_pu": ramp_threshold,
        }
        for forecast_type, values in (
            ("persistence", persistence),
            ("ridge_direct_ar", forecast),
        ):
            subset = validation_samples[["sample_id", "split", "issue_time", "target_time"]].copy()
            subset["horizon_steps"] = horizon
            subset["forecast_type"] = forecast_type
            subset["forecast_output_pu"] = values
            subset["actual_output_pu"] = validation_target
            forecast_rows.append(subset)
    return pd.DataFrame(metric_rows), pd.concat(forecast_rows, ignore_index=True), selected_models


def evaluate_validation_gate(metrics: pd.DataFrame, config: dict) -> dict:
    """按预注册的等权六时域规则判定轻量预测器是否合格。"""
    persistence = (
        metrics.loc[metrics["forecast_type"] == "persistence", ["horizon_steps", "rmse_pu"]]
        .rename(columns={"rmse_pu": "persistence_rmse_pu"})
        .sort_values("horizon_steps")
    )
    ridge = (
        metrics.loc[
            metrics["forecast_type"] == "ridge_direct_ar",
            ["horizon_steps", "rmse_pu"],
        ]
        .rename(columns={"rmse_pu": "ridge_rmse_pu"})
        .sort_values("horizon_steps")
    )
    paired = persistence.merge(ridge, on="horizon_steps", validate="one_to_one")
    expected_horizons = sorted(config["task"]["horizon_steps"])
    if paired["horizon_steps"].astype(int).tolist() != expected_horizons:
        raise ValueError("validation gate requires one paired metric row per frozen horizon")

    mean_persistence = float(paired["persistence_rmse_pu"].mean())
    mean_ridge = float(paired["ridge_rmse_pu"].mean())
    mean_improvement = 1.0 - mean_ridge / mean_persistence
    horizon_improvement = 1.0 - (
        paired["ridge_rmse_pu"] / paired["persistence_rmse_pu"]
    )
    improved_horizons = int((horizon_improvement > 0.0).sum())
    worst_worsening = float(np.maximum(-horizon_improvement, 0.0).max())

    frozen = config["success_gate"]
    checks = {
        "mean_rmse_improvement_at_least_minimum": mean_improvement
        >= float(frozen["mean_rmse_improvement_min"]),
        "improved_horizons_at_least_minimum": improved_horizons
        >= int(frozen["improved_horizons_min"]),
        "no_horizon_worsening_above_maximum": worst_worsening
        <= float(frozen["horizon_worsening_max"]),
    }
    passed = all(checks.values())
    return {
        "pass": passed,
        "decision": "ridge_direct_ar" if passed else "persistence",
        "checks": checks,
        "thresholds": {
            "aggregation": frozen["aggregation"],
            "mean_rmse_improvement_min": float(frozen["mean_rmse_improvement_min"]),
            "improved_horizons_min": int(frozen["improved_horizons_min"]),
            "horizon_worsening_max": float(frozen["horizon_worsening_max"]),
        },
        "observed": {
            "mean_persistence_rmse_pu": mean_persistence,
            "mean_ridge_rmse_pu": mean_ridge,
            "mean_rmse_improvement": mean_improvement,
            "improved_horizons": improved_horizons,
            "worst_horizon_worsening": worst_worsening,
        },
    }


def prepare(config_path: Path, stage: str) -> dict:
    config = load_config(config_path)
    frame, provenance = load_and_validate_dataset(config)
    sample_index = build_sample_index(frame, config)
    index_path = project_path(config["outputs"]["sample_index"])
    index_path.parent.mkdir(parents=True, exist_ok=True)
    sample_index.to_csv(index_path, index=False)

    run_directory_key = "smoke_run_directory" if stage == "smoke" else "validation_run_directory"
    run_directory = project_path(config["outputs"][run_directory_key])
    run_directory.mkdir(parents=True, exist_ok=True)
    metrics, forecasts, models = run_forecasts(frame, sample_index, config, stage)
    metrics.to_csv(run_directory / "metrics.csv", index=False)
    forecasts.to_csv(run_directory / "forecasts.csv", index=False)
    (run_directory / "selected_models.json").write_text(
        json.dumps(models, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    counts = (
        sample_index.groupby(["split", "horizon_steps"]).size().rename("samples").reset_index()
    )
    expected_horizons = set(config["task"]["horizon_steps"])
    indexed_horizons = {
        split: set(
            counts.loc[counts["split"] == split, "horizon_steps"].astype(int).tolist()
        )
        for split in config["split_contract"]["preparation_allowed_splits"]
    }
    gate_checks = {
        "w1_input_identity_matches": provenance["dataset_sha256"]
        == json.loads(
            project_path(config["dataset"]["feature_report"]).read_text(encoding="utf-8")
        )["processed"]["sha256"],
        "only_train_and_validation_indexed": set(sample_index["split"].unique())
        == set(config["split_contract"]["preparation_allowed_splits"]),
        "locked_splits_not_accessed": not set(sample_index["split"].unique()).intersection(
            config["split_contract"]["locked_splits"]
        ),
        "all_horizons_have_train_and_validation_samples": all(
            horizons == expected_horizons for horizons in indexed_horizons.values()
        ),
        "both_forecasters_cover_all_horizons": (
            set(metrics["forecast_type"].unique())
            == {"persistence", "ridge_direct_ar"}
            and len(metrics) == 2 * len(expected_horizons)
        ),
        "forecast_interface_columns_complete": set(
            config["forecast_interface"]["columns"]
        ).issubset(set(forecasts.columns)),
    }
    report = {
        "report_version": 1,
        "study_id": config["study_id"],
        "stage": stage,
        "formal_claim": stage == "validation",
        "decision_scope": (
            "pipeline smoke only; metrics are not formal W2 evidence"
            if stage == "smoke"
            else "validation-only model selection; locked splits remain unopened"
        ),
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "provenance": {
            **provenance,
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "runner_sha256": sha256(Path(__file__).resolve()),
            "sample_index_path": str(index_path),
            "sample_index_sha256": sha256(index_path),
        },
        "locked_splits_accessed": [],
        "preparation_gate": {
            "pass": all(gate_checks.values()),
            "checks": gate_checks,
        },
        "validation_gate": evaluate_validation_gate(metrics, config)
        if stage == "validation"
        else None,
        "sample_counts": counts.to_dict(orient="records"),
        "smoke_metrics": metrics.astype(object)
        .where(pd.notna(metrics), None)
        .to_dict(orient="records")
        if stage == "smoke"
        else None,
        "metrics_path": str(run_directory / "metrics.csv"),
        "metrics_sha256": sha256(run_directory / "metrics.csv"),
        "forecasts_path": str(run_directory / "forecasts.csv"),
        "forecasts_sha256": sha256(run_directory / "forecasts.csv"),
        "models_path": str(run_directory / "selected_models.json"),
        "models_sha256": sha256(run_directory / "selected_models.json"),
    }
    report_path = project_path(config["outputs"]["preparation_report"])
    if stage == "smoke":
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (run_directory / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--stage", choices=("smoke", "validation"), default="smoke")
    args = parser.parse_args()
    report = prepare(args.config.resolve(), args.stage)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
