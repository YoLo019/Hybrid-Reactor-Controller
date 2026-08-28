# W2-F1预测接口冻结结果

**判定：W2-F1 PASS / W2 CLOSED**  
**冻结版本：`w2_f1_forecast_interface_v1`**  
**日期：2026-08-23**

## 1. 冻结依据

W2-V1按预注册规则完成全部train/validation模型选择。ridge在6/6时域降低RMSE，但六时域等权平均改善为4.8457%，低于5%门；总体门失败，机器决策为`persistence`。本步骤不重跑模型、不修改门限，也不访问`boundary_construction`或`final_extrapolation`。

## 2. 冻结角色

| 类型 | 冻结用途 | 论文边界 |
|---|---|---|
| `none` | 无未来信息控制对照，保持发布时刻参考 | 不是预测器 |
| `persistence` | W3/E6实际预测基线 | 唯一冻结的可部署预测输入 |
| `ridge_direct_ar` | 轻量负比较 | 未通过门，不得称为合格实际预测器 |
| `perfect_foresight` | 信息上界 | 不可部署，不得计作控制器自身收益 |

控制接口必需列冻结为`issue_time`、`target_time`、`split`、`horizon_steps`、`forecast_type`和`forecast_output_pu`。证据表可额外包含`sample_id`与`target_output_pu`，但它们不属于控制器输入。

## 3. W3输入

W3节点设计可以使用W1的相关时间/频谱证据，以及W2在10、20、30、60、120和360 min上的逐时域validation误差。W2-F1只冻结输入证据和预测角色，不在此处事后选择非均匀节点；节点位置、统一跨度和算力预算应由W3-P0独立预注册。

## 4. 数据与运行边界

- `boundary_construction`与`final_extrapolation`继续锁定，W2-F1没有打开或读取它们；
- 关闭动作只写入接口和证据文件，不启动预测或控制仿真；
- 正在运行的E2目录、配置、进程和输出均不属于本步骤；
- 后续若改变预测角色、成功门或接口列，必须登记新版本，不能覆盖本冻结。

## 5. 证据入口

- 机器接口：`configs/w2_f1_forecast_interface_v1.json`；
- 冻结源配置：`configs/w2_prediction_v1.json`；
- W2-V1机器判定：`runs/w2_validation_v1/summary.json`；
- 逐时域误差：`runs/w2_validation_v1/metrics.csv`；
- 完整validation说明：`W2_VALIDATION_RESULTS.md`。

以上证据闭合W2。W3入口已开放，但本步骤不等同于启动W3正式闭环计算。
