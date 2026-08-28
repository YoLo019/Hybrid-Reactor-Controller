# W2-V1完整validation结果

**执行判定：W2-V1 COMPLETED / RIDGE QUALIFICATION GATE FAILED**  
**W2-F1冻结决策：`persistence`**  
**日期：2026-08-23**

## 1. 执行范围与数据隔离

本次按`w2_prediction_v1`执行完整train/validation：train用于拟合和爬坡阈值，validation用于逐时域alpha选择与正式W2-V1指标。样本的24 h上下文、发布时刻和目标时刻均在同一切分；机器报告记录`locked_splits_accessed=[]`，未读取9—10月boundary或11—12月final。

六个时域的validation有效样本分别为8784、8783、8782、8779、8773和8749。预测表共105300行，只包含validation、两类预测器和六个冻结时域，重复键与非有限预测均为0。

## 2. 正式指标

| 时域 | 样本 | persistence MAE | ridge MAE | persistence RMSE | ridge RMSE | ridge RMSE改善 | ridge爬坡召回 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 min | 8784 | 0.015478 | 0.015919 | 0.025924 | 0.025254 | 2.586% | 0.286% |
| 20 min | 8783 | 0.023926 | 0.025253 | 0.040267 | 0.039790 | 1.185% | 0% |
| 30 min | 8782 | 0.029835 | 0.031471 | 0.049690 | 0.048778 | 1.836% | 0% |
| 60 min | 8779 | 0.041775 | 0.043440 | 0.067633 | 0.064706 | 4.327% | 0% |
| 120 min | 8773 | 0.053559 | 0.055530 | 0.084556 | 0.079525 | 5.951% | 0% |
| 360 min | 8749 | 0.074404 | 0.076486 | 0.110489 | 0.102164 | 7.535% | 0% |
| 六时域等权平均 | — | — | — | 0.063093 | 0.060036 | **4.846%** | — |

所有时域都选择`alpha=0`。ridge在6/6时域降低RMSE，但在6/6时域提高MAE，说明收益来自减少少数较大误差，而不是普遍降低绝对误差。

## 3. 预注册成功门

| 检查 | 阈值 | 观测 | 判定 |
|---|---:|---:|---|
| 六时域等权平均RMSE改善 | ≥5% | 4.8457% | **FAIL** |
| 改善时域数 | ≥4/6 | 6/6 | PASS |
| 任一时域最大恶化 | ≤5% | 0% | PASS |

总体门为三项同时通过，因此`validation_gate.pass=false`。平均改善距阈值少0.1543个百分点，不能通过四舍五入或事后修改门限判为合格。按冻结fallback，W2-F1应选择持续性预测作为W3/E6实际预测基线，并把ridge记录为轻量负比较。

## 4. 爬坡与范围诊断

train-only 90%阈值下，validation爬坡事件数随时域为349、378、423、489、466和335。持续性预测按定义不预测变化，召回均为0；ridge只在10 min命中1个事件，召回`1/349=0.286%`，其余时域召回为0。ridge的事件MAE虽均低于持续性，但不具备可用的事件检出能力。

ridge预测超出`[0,1]`的比例在10 min为0.5123%、20 min为0.0228%，其余时域为0；主指标按协议未裁剪。持续性预测的超范围比例均为0。

## 5. 复现与回归

最终代码与配置身份下连续运行两次，样本索引、`metrics.csv`、`forecasts.csv`、`selected_models.json`和`summary.json`逐项一致。项目回归26/26通过，其中W2专用5项包含成功门三部分规则测试。

## 6. 证据入口与声明边界

- 机器决策：`runs/w2_validation_v1/summary.json`；
- 逐时域指标：`runs/w2_validation_v1/metrics.csv`；
- 完整预测：`runs/w2_validation_v1/forecasts.csv`；
- 模型参数：`runs/w2_validation_v1/selected_models.json`；
- 控制台日志：`runs/w2_validation_v1/console.log`；
- 冻结配置：`configs/w2_prediction_v1.json`。

W2-V1是validation-only模型选择证据，不是final外推结果。当前可以声明“ridge在全部时域降低RMSE但未通过预注册总体门”，不能声明ridge为合格实际预测器，也不能把理想预知或锁定数据用于补强结论。W2-F1现已冻结持续性预测及控制接口，关闭证据见`W2_F1_FREEZE.md`。
