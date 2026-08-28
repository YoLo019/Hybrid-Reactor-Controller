# -*- coding: utf-8 -*-
"""静态验证E4/E5准备契约，不读取数据或运行模型。"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _check(checks: List[Dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "pass": bool(passed), "detail": detail})


def build_preparation_report(project_root: Path) -> Dict[str, Any]:
    """返回机器可读准备门；只读取两份准备配置。"""
    config_root = project_root / "research_execution" / "04_experiments" / "configs"
    e4_path = config_root / "e4_validation_v1.json"
    e5_path = config_root / "e5_capacity_elasticity_v1.json"
    e4 = _load_json(e4_path)
    e5 = _load_json(e5_path)

    checks: List[Dict[str, Any]] = []
    _check(
        checks,
        "e4_dependencies_frozen",
        e4["dependencies"]["required_completed_studies"] == ["E2", "E3"],
        "E4正式执行依赖E2/E3",
    )
    _check(
        checks,
        "e4_data_roles_separated",
        e4["data_isolation"]["required_roles"]
        == ["boundary_construction", "independent_validation", "adversarial_stress"],
        "建域、独立验证和对抗压力测试角色分离",
    )
    _check(
        checks,
        "locked_splits_untouched",
        e4["data_isolation"]["locked_splits_accessed"] == [],
        "准备阶段boundary/final访问数为0",
    )
    dangerous_miss = e4["dangerous_miss"]
    _check(
        checks,
        "e4_exact_binomial_upper_frozen",
        dangerous_miss["confidence_level"] == 0.95
        and dangerous_miss["interval"]
        == "one-sided exact Clopper-Pearson binomial upper confidence bound",
        "冻结95%单侧精确二项上界",
    )
    _check(
        checks,
        "e4_boundary_error_contract_frozen",
        e4["boundary_error"]["signed_error_formula"]
        == "rho_inner - rho_ff_validation"
        and e4["boundary_error"]["acceptance_thresholds"] is None,
        "误差定义已冻结，未知通过阈值保持未填",
    )
    _check(
        checks,
        "e4_unseen_manifest_schema_frozen",
        len(e4["trajectory_manifests"]["required_fields"]) >= 10
        and e4["trajectory_manifests"]["independent_validation"] == []
        and e4["trajectory_manifests"]["adversarial_stress"] == [],
        "轨迹清单字段已冻结，准备阶段清单不打开锁定数据",
    )

    capacities = [item["id"] for item in e5["capacity_dimensions"]]
    _check(
        checks,
        "e5_capacity_dimensions_frozen",
        capacities == ["bess_power", "bess_energy", "valve_rate", "rod_rate"],
        "四个容量维度与论文计划一致",
    )
    step_levels = e5["relative_step_levels"]
    _check(
        checks,
        "e5_three_step_structure_frozen",
        [item["label"] for item in step_levels] == ["small", "nominal", "large"]
        and all(item["fraction"] is None for item in step_levels),
        "三档结构已冻结，未虚构数值步长",
    )
    _check(
        checks,
        "e5_dimensionless_central_difference_frozen",
        e5["finite_difference"]["method"] == "normalized central finite difference"
        and "c_i / rho(c)" in e5["finite_difference"]["dimensionless_elasticity_formula"],
        "中心有限差分与无量纲归一化公式已冻结",
    )
    active_set = e5["active_set_and_nondifferentiability"]
    _check(
        checks,
        "e5_active_set_switch_reporting_frozen",
        active_set["required_active_sets"] == ["minus", "base", "plus"]
        and active_set["one_sided_slopes_required_on_switch"] is True,
        "活跃集切换要求单侧斜率和不可微报告",
    )
    _check(
        checks,
        "preparation_is_read_only",
        not e4["execution_policy"]["preparation_may_open_locked_splits"]
        and not e4["execution_policy"]["preparation_may_run_model"]
        and not e5["execution_policy"]["preparation_may_read_boundary_results"]
        and not e5["execution_policy"]["preparation_may_run_model"],
        "静态准备不得访问锁定数据、边界结果或模型",
    )

    e4_blockers = [
        name for name, passed in e4["formal_execution_gate"].items() if not passed
    ]
    e5_blockers = [
        name for name, passed in e5["formal_execution_gate"].items() if not passed
    ]
    preparation_pass = all(item["pass"] for item in checks)
    formal_pass = (
        preparation_pass
        and not e4_blockers
        and not e5_blockers
        and e4["execution_policy"]["formal_execution_allowed"]
        and e5["execution_policy"]["formal_execution_allowed"]
    )
    return {
        "study_id": "E4-E5-P0-STATIC-V1",
        "preparation_gate": {"pass": preparation_pass, "checks": checks},
        "formal_execution_gate": {
            "pass": formal_pass,
            "e4_blockers": e4_blockers,
            "e5_blockers": e5_blockers,
        },
        "data_access": {
            "files_opened": [
                str(e4_path.relative_to(project_root)).replace("\\", "/"),
                str(e5_path.relative_to(project_root)).replace("\\", "/"),
            ],
            "locked_splits_accessed": [],
            "boundary_results_accessed": [],
        },
        "model_execution": {"launched": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_preparation_report(args.project_root.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["preparation_gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
