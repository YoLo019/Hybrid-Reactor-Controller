# W2准备阶段结果

**判定：PREPARATION PASS（历史阶段）/ W2-V1现已完成**  
**日期：2026-08-23**

> 状态更新：完整validation已在准备门关闭后独立执行，ridge未通过预注册总体门；正式结果见`W2_VALIDATION_RESULTS.md`。本文件保留准备阶段口径与非正式smoke证据。

## 1. 准备门结果

`w2_prediction_v1`已经完成数据身份、时间隔离、样本索引、预测方法、指标、输出接口和正式validation入口。机器报告的6项准备检查全部通过，`boundary_construction`与`final_extrapolation`均未被索引。

| 时域 | train有效样本 | validation有效样本 |
|---:|---:|---:|
| 10 min | 25283 | 8784 |
| 20 min | 25280 | 8783 |
| 30 min | 25277 | 8782 |
| 60 min | 25268 | 8779 |
| 120 min | 25255 | 8773 |
| 360 min | 25207 | 8749 |

样本减少只来自缺失目标、24 h完整历史要求和切分边界，不跨越长缺口，也不跨切分借用上下文。

## 2. 非正式冒烟

每个时域只取前4096个train与1024个validation有效样本。下表只证明算法、指标和文件接口可运行，不用于论文预测性能结论。

| 时域 | persistence RMSE | ridge RMSE | ridge相对改善 |
|---:|---:|---:|---:|
| 10 min | 0.03348 | 0.03245 | 3.09% |
| 20 min | 0.05298 | 0.05224 | 1.40% |
| 30 min | 0.06522 | 0.06395 | 1.95% |
| 60 min | 0.08711 | 0.08361 | 4.02% |
| 120 min | 0.10567 | 0.10166 | 3.80% |
| 360 min | 0.11973 | 0.11745 | 1.90% |

冒烟子集上的改善均低于正式5%门，因此不得提前声称ridge合格；正式结论必须来自全部train/validation的`W2-V1`。两个方法在该冒烟子集的爬坡召回均为0，也说明正式W2必须单独报告事件指标，不能只看平均RMSE。

## 3. 复现与回归

相同配置连续运行两次smoke，样本索引、`metrics.csv`、`forecasts.csv`和`selected_models.json`的SHA-256均逐项一致。项目全量回归25/25通过，其中W2新增4项分别覆盖样本不跨切分、锁定切分拒绝、ridge预测确定性和持续性指标契约。

## 4. 产物

- 冻结配置：`configs/w2_prediction_v1.json`；
- 执行代码：`../../4_4/wind_data/w2_forecasting.py`；
- 防泄漏样本索引：`../../4_4/data/wind/processed/w2_prediction_index_v1.csv`；
- 机器准备报告：`../../4_4/data/wind/manifests/w2_preparation_v1.json`；
- 冒烟目录：`runs/w2_preparation_smoke_v1/`；
- 详细协议：`../03_thesis_research/W2_PREPARATION_PROTOCOL.md`。

## 5. 当前边界

准备阶段结束时W2保持`in_progress`且`W2-P0`已通过。随后完成的W2-V1仍未查看或使用boundary/final，并由W2-F1冻结持续性预测、关闭W2；关闭证据见`W2_F1_FREEZE.md`。smoke结果始终不能进入预测性能主张或E6控制收益结论。
