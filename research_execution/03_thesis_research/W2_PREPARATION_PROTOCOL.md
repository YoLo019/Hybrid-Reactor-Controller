# W2风电短时预测准备协议

**状态：W2-F1 PASS / W2 CLOSED**  
**冻结版本：`w2_prediction_v1`**

## 1. W2在论文中的作用

W2不承担新的预测算法创新。它提供三个后续必需量：持续性预测公平基线、可复现的轻量实际预测、以及随预测时域增长的误差曲线。W3据此选择近端细、远端粗的预测节点；E6据此比较无预测、持续性预测、实际预测和理想预知，避免把额外未来信息误写成控制器收益。

## 2. 冻结数据隔离

| 切分 | 时间 | W2用途 | 当前权限 |
|---|---|---|---|
| train | 2016-01-01至06-30 | 模型拟合、训练期爬坡阈值 | 开放 |
| validation | 2016-07-01至08-31 | 超参数选择、准备后正式验证 | 开放 |
| boundary_construction | 2016-09-01至10-31 | E2/E3边界构建及冻结后预测输入 | 锁定 |
| final_extrapolation | 2016-11-01至2017-01-01 | 控制器与阈值冻结后的最终外推 | 锁定 |

每个样本的完整24 h历史、预测发布时刻和目标时刻必须位于同一切分；禁止随机打散。准备脚本只允许索引train和validation，并在机器报告中记录`locked_splits_accessed=[]`。

## 3. 预测任务

- 数据间隔：10 min；
- 历史上下文：144步，即24 h，覆盖W1估计的约22.67 h `1/e`相关时间；
- 直接多时域：10、20、30、60、120、360 min，对应1、2、3、6、12、36步；
- 目标：`output_pu`点预测；不将10分钟数据插值成0.5 s或快速功率观测；
- `output_pu`沿用W1对`Energy`的工作解释。由于所有方法使用同一常数缩放，方法间相对误差排序不受单位常数影响，但绝对p.u.物理解释仍保留数据卡限制。

## 4. 必须比较的系统

1. `persistence`：所有时域均预测为发布时刻最新观测；
2. `ridge_direct_ar`：每个时域独立的直接线性AR，使用10个预注册历史滞后和train标准化；`alpha`只在validation选择；
3. `none`与`perfect_foresight`只作为后续控制输入契约：前者保持发布时刻参考，后者是信息上界，不属于可部署预测器。

`NWP.csv`暂不进入v1必需模型。该文件只有目标时间戳，没有预报发布时间和lead-time元数据；直接使用目标时刻NWP会产生信息可用性歧义。取得预报周期说明后可作为附加比较，但不阻塞W2最低闭环。

## 5. 指标与选择门

- 主指标：逐时域MAE、RMSE、相对持续性的RMSE改善；
- 次指标：train-only分时域90%爬坡阈值下的事件precision、recall、F1和事件MAE；
- 诊断：预测超出`[0,1]`比例；主指标禁止先裁剪预测；
- 轻量预测合格门：先对六个时域的RMSE分别等权算术平均，再计算ridge相对持续性的平均RMSE改善；该改善至少5%，同时至少4/6时域改善，且任一时域不得恶化超过5%；
- 若未通过：持续性预测保留为W3/E6实际基线，ridge作为负结果报告，不增加复杂模型来追逐小幅收益。

## 6. 执行顺序

1. `W2-P0`准备门：数据身份、切分、样本索引、指标和输出接口；已完成；
2. `W2-S0`冒烟：每时域4096个train与1024个validation样本；已完成，只验证链路；
3. `W2-V1`正式validation：使用全部train/validation样本选择每时域alpha并报告完整指标；已完成，ridge总体门失败；
4. `W2-F1`冻结：根据V1冻结持续性预测、forecast表结构和W3节点输入；已完成，见`../04_experiments/W2_F1_FREEZE.md`；
5. boundary与final只能在W3/E6控制器和阈值冻结后按各自用途开启。

## 7. 复现入口

```powershell
python .\4_4\wind_data\w2_forecasting.py --stage smoke
python .\4_4\wind_data\w2_forecasting.py --stage validation
```

预测研究配置为`../04_experiments/configs/w2_prediction_v1.json`；W2-F1机器接口为`../04_experiments/configs/w2_f1_forecast_interface_v1.json`；准备证据为`../../4_4/data/wind/manifests/w2_preparation_v1.json`；正式validation和冻结结果见`../04_experiments/W2_VALIDATION_RESULTS.md`与`../04_experiments/W2_F1_FREEZE.md`。再次执行上述命令只用于同身份复现，不得改变冻结阈值或覆盖W2-F1接口。
