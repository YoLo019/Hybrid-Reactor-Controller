# W3非均匀预测时域准备与冒烟结果

**判定：W3-P0 PASS / W3-C0 PASS / W3-S0 PASS / W3-F1 PASS / W3-B1 PASS / W3-R0 PASS**  
**当前边界：W3-V1正式闭环尚未启动**  
**日期：2026-08-24**

## 1. W3-P0节点与公平预算

机器准备门9/9通过。nonuniform与uniform-tail均使用0.5 s控制更新、6 h预测跨度、30个阀门决策以及相同QP输出、权重和求解设置。nonuniform保留15 s控制锚点和W2的10、20、30、60、120、360 min误差锚点；第一预测区间为0.5 s。

W2 persistence RMSE随六个时域由0.025924单调增至0.110489 p.u.，远端不增加节点密度。6 h跨度小于W1约22.67 h的`1/e`相关时间。W2-F1仍选择persistence，`locked_splits_accessed=[]`。

## 2. W3-C0独立代码审查

首轮审查发现并关闭两个阻塞：

1. 原preview入口直接读取未来真实目标。现已改为显式`forecast_type`和issue-time provider；persistence/ridge缺少provider时失败，只有显式`perfect_foresight`可以读取未来目标；
2. 原OSQP异常可静默fallback到SCS且smoke不记录路径。现已逐次记录solver、fallback和异常，W3-S0要求全部使用OSQP且无fallback。

复审确认W3-P0/W3-S0无剩余阻塞。审查记录见`../../refine-logs/EXPERIMENT_CODE_REVIEW.md`。

## 3. W3-S0真实对象结构冒烟

在90%功率、SOC=0.5的真实44状态工作点线性化上，两种QP各构造一次、重复求解3次。两者共享同一发布时刻冻结的常值persistence provider；本实验不运行闭环长仿真。

| 网格 | 决策数 | 跨度 | 构造时间/s | 平均求解/s | 状态 | fallback |
|---|---:|---:|---:|---:|---|---|
| nonuniform | 30 | 21600 s | 0.01790 | 0.01523 | 3/3 OSQP optimal | 0/3 |
| uniform-tail | 30 | 21600 s | 0.01776 | 0.01241 | 3/3 OSQP optimal | 0/3 |

8项结构门全部通过：P0通过、预算相同、命令有限、求解状态合格、全部使用冻结OSQP、无fallback、构造时间和平均求解时间均在smoke预算内。锁定切分访问为0。

## 4. 回归与声明边界

W3/W2相关轻量回归13/13通过，覆盖分段常值凝聚映射、全1步恢复原映射、W2锚点、公平预算、实际预测禁止偷读未来、provider缺失失败、整数区间、区间代价/积分/限速尺度及W2冻结契约。

当前允许声明“6 h、30变量非均匀与公平均匀QP均可在真实对象线性化上构造并由OSQP稳定求解”。不得声明nonuniform改善闭环性能，也不得把S0常值provider视为已完成W2预测表接入。

消费W2预测表的具体provider、PID固定15 s前馈基线、正式场景/切分、闭环指标、超时判据和运行资源门现已冻结并通过2 s集成冒烟。E2运行期间仍不启动W3-V1。

## 5. 证据入口

- 协议：`../03_thesis_research/W3_PREPARATION_PROTOCOL.md`；
- 配置：`configs/w3_nonuniform_horizon_v1.json`；
- P0机器结果：`runs/w3_preparation_v1/summary.json`；
- S0机器结果：`runs/w3_smoke_v1/summary.json`；
- 控制器：`../../4_4/mpc_utils_out.py`；
- 预测接口：`../../4_4/metrics_source.py`；
- 测试：`../../4_4/tests/test_w3_nonuniform_horizon.py`。

## 6. W3-F1与W3-B1

W3-F1将W2-F1冻结的validation-only预测表接入issue-time provider。它按控制时刻选择不晚于当前时刻的最近发布，拒绝未来issue、跨split、重复键、六时域缺失、非有限值和过期发布；10、20、30、60、120、360 min节点之间按绝对目标时间线性插值。

W3-B1冻结PID固定15 s前馈。PID与两个MPC共享同一provider和issuance集合，但PID只消费15 s单点、MPC消费完整6 h曲线，因此公平含义是“同来源、同发布时间的架构基线”，不是相同信息容量。

## 7. W3-R0闭环集成冒烟

fresh-agent独立审查判定可执行仅2 s、无性能主张的R0。初版冒烟后复审发现standalone聚合器可能把4步R0误认证为V1；该结果保留为审查见证但不再作为当前入口。根因修复后的v2三控制器全部完成，10/10结构门通过：study与stage一致、每个case为4个控制步、validation-only、同场景、同预测类型、同发布集合、指标有限、两个MPC绝对预算均为30个决策与21600 s、OSQP状态均为`optimal`且fallback为0。两个MPC各完成4/4次求解，0.5 s deadline miss为0。

R0仅覆盖4个控制步，跟踪误差处于数值噪声量级，任何相对优劣都没有统计或物理意义，不进入论文性能结论。当前机器结果见`runs/w3_v1_integration_smoke_v2/summary.json`。

独立审查提出的正式门缺口已落实为fail-closed检查：study/stage、R0与V1的4/42000控制步身份、solver status及精确计数、配置中的绝对节点/跨度预算、场景身份、非空发布集、部分目录与summary覆盖、配置常量一致性以及E2/E3正式进程冲突。fresh-agent复审PASS，跨W2/W3/E2/E3的65项回归通过；在E2运行时尝试formal入口会在模型启动前被拒绝。

因此当前状态是：W3实现与R0闭合，W3-V1只剩资源释放后的21000 s正式运行与结果判定，尚不支持nonuniform性能收益主张。
