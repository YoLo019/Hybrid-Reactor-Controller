# E2 双层边界 Runner 冻结契约

状态：`FROZEN`  
契约 ID：`E2-LAYERED-BOUNDARY-RUNNER-V1`  
冻结日期：`2026-08-28`

## 1. 冻结对象

- Runner：`4_4/flexibility/run_e2_forcing_boundary_refinement.py`
- 配置：`research_execution/04_experiments/configs/e2_f1_forcing_boundary_refinement_v1.json`
- 定向二分聚合：`research_execution/04_experiments/runs/e2_f1_forcing_boundary_refinement_v1/e2_f1_forcing_boundary_refinement_aggregate.json`
- 机器冻结见证：`research_execution/04_experiments/runs/e2_f1_forcing_boundary_refinement_v1/e2_layered_boundary_runner_contract.json`
- 核心代码包身份：`C931CE2936BF1A3C2A3EF3060AC34CF48CEBF133953EB8A2B5CE438F65DAA3B1`
- 定向二分 runner 身份：`737D27D15776A65C552A59C944FCF32B69558353067AA5B80B38B28F056EB3E8`
- 定向二分配置身份：`743C09B327D8DB5ECD837457FBEB9242A0FCE6A0E83E8B68F2A3C878CB0E468A`

## 2. 双层边界定义

1. **强迫阶段物理边界**：仅检查 `forcing_end_s` 之前的正式 plant/device 约束；不要求恢复窗口完成。
2. **恢复完成联合边界**：沿用正式 case 的 `valid_for_boundary` 标签，要求正式物理约束通过且恢复末段进入冻结邻域。
3. 强迫阶段二分只使用第一层标签；第二层只并列记录，不能反向改变第一层边界。
4. 每条射线从中心向外采用首次安全—失效转换；若出现多次转换，保留首次失效并单独标记非星形。

## 3. 覆盖范围与结果

中心工况固定为核功率 `0.9 p.u.`、BESS `SOC=0.5`、控制器 `MPC`、输入域 `D_ref`，覆盖三频点和两相位，共 6 条恢复主导射线：

| 频率 | 相位 | 强迫阶段安全—首失效区间/p.u. | 二分次数 |
|---:|---:|---:|---:|
| `3.076171875e-4 Hz`（q0.99） | 0 | `[0.206225, 0.2115125]` | 4 |
| `3.076171875e-4 Hz`（q0.99） | π/2 | `[0.206225, 0.2115125]` | 4 |
| `8.015950520833334e-5 Hz`（q0.95） | 0 | `[0.206225, 0.2115125]` | 4 |
| `8.015950520833334e-5 Hz`（q0.95） | π/2 | `[0.206225, 0.2115125]` | 4 |
| `3.4993489583333335e-5 Hz`（q0.90） | 0 | `[0.206225, 0.2115125]` | 4 |
| `3.4993489583333335e-5 Hz`（q0.90） | π/2 | `[0.206225, 0.2115125]` | 4 |

所有区间宽度均为 `0.0052875 p.u.`，小于冻结容差 `0.01 p.u.`。恢复完成联合边界仍作为第二层保留：MPC 相位 0 为约 `[0.0313049374, 0.0380615643] p.u.`，MPC 相位 π/2 为约 `[0.081775, 0.08725] p.u.`；这两个较小区间来自恢复完成判据，不是强迫阶段物理边界。

## 4. 验收门

- 射线完整性：`6/6`，缺失 `0`，重复 `0`；
- 二分精度：6 条均 `within_frozen_tolerance`；
- 求解失败：`0`；
- 强迫阶段非星形：`0`；
- 恢复阶段新增物理违规：`0`；
- 粗扫端点方向校验：`0`；
- 双层契约：`dual_layer_runner_contract_ready=true`。

## 5. 后续使用规则

- 后续 E2-F2 三功率 × 三 SOC 扩展必须同时写出强迫阶段物理边界和恢复完成联合边界。
- 不得把联合边界的较小数值写成 MPC 的一般物理跟踪能力上限。
- 不得通过放宽恢复阈值、删去相位或改动 `dt=0.5 s` 来改变本契约。
- 若代码、配置、约束或恢复判据发生变化，必须生成新的契约身份并重新完成 6 条射线验收；不得覆盖本冻结见证。
