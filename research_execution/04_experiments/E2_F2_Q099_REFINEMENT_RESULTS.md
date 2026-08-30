# E2-F2 q0.99 双层细化结果

状态：`PASS（2026-08-30）`

## 1. 目的与边界

本波次对 q0.99 的72条跨工况射线分别细化强迫阶段安全边界与恢复完成联合边界。强迫层只使用 `forcing_stage_safe` 标签，联合层只使用 `joint_valid_for_boundary` 标签；恢复完成不得反向改写强迫边界。细化复用合并预检端点，不复用 E2-F1 案例。

## 2. 执行身份

- 细化配置：`configs/e2_f2_q099_refinement_v1.json`
- 双层 runner：`4_4/flexibility/run_e2_f2_q099_refinement.py`
- 输入聚合：`runs/e2_f2_q099_mpc_upper_extension_v1/e2_f2_q0.99_precheck_extended_aggregate.json`
- 输出聚合：`runs/e2_f2_cross_operating_q099_refine_v1/e2_f2_q0.99_refine_aggregate.json`
- 配置 SHA-256：`C63709FFD73650CCDC4C321A9384F675471E5472A79BE4CA074A8CFB3CBD64B1`
- 细化 runner SHA-256：`F41E9806D17782601AB02C9E927D2A437D60AFF10DF749E6A007C73ED8F173C6`
- 共享 F2 身份：配置 `98B3C08719A647559C3979B5BE867706017CFE9C65CFCAA5A8911D8FB3013053`，核心代码 `C931CE2936BF1A3C2A3EF3060AC34CF48CEBF133953EB8A2B5CE438F65DAA3B1`，F2 runner `949CBCC8F6A5D062467F2D37C7C1E2B12893A84A19C0BD36F24A1B2C0562D43D`。

## 3. 机器结果

| 项目 | 结果 |
|---|---:|
| 射线覆盖 | 72/72，缺失0，重复0 |
| 新增细化案例 | 252 |
| 强迫括区宽度 | `0.0052—0.0085 p.u.`，中位数 `0.0052875 p.u.` |
| 联合括区宽度 | `0.0052—0.0085 p.u.`，中位数 `0.005475 p.u.` |
| 求解失败 | 0 |
| 恢复阶段新增物理违规 | 0 |
| 强迫阶段非星形 | 0 |
| 联合层非星形诊断 | `[25, 33, 41]` |
| `refinement_gate_pass` | `true` |

联合层的三条非星形射线仅作为恢复联合边界诊断保留，不改变强迫阶段安全边界或聚合门判定。q0.99 双层细化至此完成，可进入 q0.95 预检波次。
