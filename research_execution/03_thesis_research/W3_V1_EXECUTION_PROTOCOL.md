# W3-V1非均匀预测时域正式执行协议

**状态：PRE-REGISTERED / F1-B1-R0 PASS / FORMAL RESOURCE-BLOCKED**  
**配置：`w3_v1_typical_validation`**  
**日期：2026-08-23**

## 1. 目标与主张边界

W3-V1的主要检验是在相同完整预测曲线、相同6 h跨度和相同30个阀门决策变量下，近端密远端粗的nonuniform时域是否相对uniform-tail形成可重复的闭环Pareto收益。PID是共享provider和issuance集合、但只消费固定15 s单点的架构基线。实验不检验预测算法先进性，也不把perfect foresight收益归因于控制器。

本轮使用W2-F1冻结的`persistence`作为实际预测输入。ridge仍是未合格负比较，perfect foresight仍是不可部署信息上界，二者不进入V1主门。

## 2. 数据与场景

- 数据：Sotavento 2016清洗数据，仅允许`validation`；
- 窗口：W1在任何W3控制结果产生前已登记的`typical_6h`，2016-07-20 00:10至06:00；
- 重构：10 min真实风功率记录之间做分段线性慢参考重构，只用于0.5 s控制积分，不称为高频实测；
- 系统映射：以窗口首值为中心，按`17.56 MW / 100 MW`映射到90%核功率参考：

\[
P_{ref}(t)=0.9-\frac{17.56}{100}\left(P_w(t)-P_w(t_0)\right).
\]

`boundary_construction`和`final_extrapolation`不得访问。V1是validation-only结构选择，不是最终外推。

## 3. 预测发布契约

W2预测每10 min发布一次。0.5 s控制更新之间只使用不晚于当前控制时刻的最近一次发布；禁止读取未来issue。每条发布曲线由lead=0的发布时刻值与10、20、30、60、120、360 min六个冻结点组成，节点间按绝对目标时间线性插值。当前时刻晚于最近issue时，6 h预测节点可能比最后已发布目标多出不足10 min，该尾部保持360 min预测值。

provider必须拒绝重复键、六时域缺失、非有限预测、跨split和未来issue。nonuniform、uniform-tail与PID前馈共享同一个provider和issuance集合。PID只消费固定15 s单点，MPC消费完整6 h曲线，因此它是同来源、同发布时间的架构基线，不宣称三者具有相同的信息容量。

## 4. 比较系统

1. `pid_forecast_ff`：现有PID/droop/压力修正结构，使用同一forecast provider在固定15 s lead处的预测作为前馈参考，并保留相同阀门幅值和速率限制；
2. `mpc_uniform_tail`：0.5 s首步加29个尽可能均匀的远端区间；
3. `mpc_nonuniform`：W3-P0冻结的近端密远端粗节点，包含15 s与W2六个误差锚点。

两种MPC保持相同模型、输出、权重、约束、OSQP容差、30个决策和6 h跨度。OSQP fallback不允许进入正式证据。

## 5. 指标与判定

必须共同报告跟踪RMSE/MAE/最大误差、频率峰值、冷却剂平均温度偏差、棒速峰值、阀门峰值速率与总变差、BESS峰值/吞吐、SOC范围，以及求解mean/P95/max和0.5 s deadline miss比例。

W3-S0的单次QP通过不代表nonuniform性能有效。V1三个系统必须完成同一窗口和同一预测发布集合，指标有限且身份一致；两种MPC均须OSQP零fallback。只有nonuniform在多指标与计算代价上形成可重复Pareto收益时才允许正向结论；否则按预注册规则报告负结果，不调整节点追逐收益。

## 6. 执行顺序与资源门

1. `W3-F1`：具体W2 forecast provider及数据隔离测试；
2. `W3-B1`：同provider/issuance的固定15 s PID前馈及限幅/限速测试；
3. `W3-R0`：runner、聚合器、2 s三控制器integration smoke；
4. `W3-V1`：完整21000 s窗口正式闭环。

前三步只允许轻量测试。W3-V1启动时必须确认E2相关进程数为0；E2运行期间不得启动正式闭环。E3正式波次与W3-V1也不得在本机并发。

## 7. 证据入口

- V1配置：`../04_experiments/configs/w3_v1_typical_validation.json`；
- W3节点配置：`../04_experiments/configs/w3_nonuniform_horizon_v1.json`；
- W3准备结果：`../04_experiments/W3_PREPARATION_RESULTS.md`；
- W2冻结接口：`../04_experiments/configs/w2_f1_forecast_interface_v1.json`。
