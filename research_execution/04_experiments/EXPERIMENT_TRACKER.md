# 实验跟踪表

| ID | 实验 | 状态 | 进入条件 | 完成判据 |
|---|---|---|---|---|
| E0-A | 静态结构与参数审计 | passed | 无 | 参数/初值逐键唯一分类；原文表值与稳态闭合调整分开记录；状态单位/数值域完整 |
| E0-B | 平衡与守恒 | passed | E0-A | 80%/90%/100%及600 s漂移全部通过 |
| E0-C | 子系统响应 | passed | E0-A | 棒/阀/BESS、风参考、负荷RoCoF、SOC及棒/阀饱和方向全部通过 |
| E0-D | 线性与控制器 | passed | E0-A | 三点线性化、局部一致性、真实增量和硬限速通过 |
| E0-E | 数值和回归 | passed | E0-B/C/D | 容差、0.5/0.25 s步长、确定性与固定种子复现及回归全部通过 |
| E1 | 修正公平基线 | passed | G0-A PASS | 三类场景稳定、指标完整、失败结论已记录 |
| W0 | 风电数据来源与生成链 | passed | 无 | 原始文件、数据卡、许可/字段/采样说明、哈希和处理脚本齐全 |
| W1 | 风电特征与场景标定 | passed | W0 | W1-A慢尺度、MORE-EU直接功率`0.005—0.25 Hz`、Björkö发电机侧实测电功率`0.25—1 Hz`及完整幅频映射均闭合 |
| W2 | 风电短时预测 | passed | W0/W1 | 时间隔离数据集、持续性基线、轻量方法、完整validation与预测接口冻结 |
| E2 | 正弦柔性双域 | in_progress（q0.99 v2聚合、跨频身份门、6条强迫边界定向二分与双层runner冻结均通过；E2-F2待启动） | E1；E2-F0；W1慢域、直接功率快域与发电机侧高频域均已有数据入口 | 按冻结双层runner契约执行三功率×三SOC扩展 |
| E3 | 斜坡柔性域 | in_progress（E3-P0/S0通过，正式P99首波待资源释放） | E1/W1 | 幅值—速率—持续时间双域、迟发违规、首次失效与聚合验收 |
| E4 | 保守性/真实轨迹验证 | blocked（E4-P0静态准备通过） | E2/E3/W1 | 独立危险漏判上界、边界误差和未见风轨迹结果 |
| E5 | 容量弹性与责任迁移 | blocked（E5-P0静态准备通过） | E2/E3/E4 | 多步长稳定区间、活跃集切换和迁移图 |
| W3 | 非均匀预测时域 | in_progress（P0/C0/S0/F1/B1/R0通过，V1资源阻塞） | W1/W2/E1 | 资源释放后执行21000 s三控制器validation并判定Pareto收益或负结果 |
| E6 | 预测与弹性协调QP | blocked | E5/W2/W3 | 同预测信息PID前馈公平基线；新闭环重新建域且存在可重复收益 |
| E7 | 消融 | blocked | E6 | 数据、预测时域、弹性和协调机制贡献分解 |
| E8 | 外推与失败案例 | blocked | E6/E7 | 未见风扰动、参数摄动和最坏反例完成 |

状态只能使用`blocked`、`in_progress`、`passed`、`failed`。修改状态时必须同时在验证、数据或实验结果文件中记录证据路径。

## W2准备关闭见证（2026-08-23）

- `W2-P0`准备门6/6通过：输入数据身份一致、只索引train/validation、锁定切分未访问、六时域样本完整、两类预测器覆盖完整、控制预测接口列完整。机器记录为`../../4_4/data/wind/manifests/w2_preparation_v1.json`。
- `W2-S0`smoke可重复：样本索引、指标、预测表和模型选择文件连续两次运行SHA-256逐项一致；项目回归25/25通过，其中4项专门覆盖跨切分、锁定切分、ridge确定性和持续性指标契约。
- smoke仅使用每时域4096个train与1024个validation样本。ridge相对持续性的RMSE改善为1.40%—4.02%，均未达到5%正式门；该结果只证明链路可运行，不构成模型合格或论文性能证据。
- 完整口径和准备结果见`../03_thesis_research/W2_PREPARATION_PROTOCOL.md`与`W2_PREPARATION_RESULTS.md`。准备关闭时登记的下一步为`W2-V1`；该步骤现已完成，且boundary/final始终锁定。

## W2-V1执行见证（2026-08-23）

- 完整train/validation运行退出码0；预测表105300行，只含validation、六时域和两类预测器，重复键与非有限预测均为0，`locked_splits_accessed=[]`。
- ridge在6/6时域降低RMSE，改善为1.185%—7.535%；六时域等权平均由0.063093降至0.060036，改善4.8457%，低于5%门。改善时域数和最坏恶化门通过，总体门失败，机器决策为`persistence`。
- ridge在6/6时域MAE均高于持续性；爬坡召回除10 min命中1/349外均为0。不能把RMSE局部收益写成可部署预测器全面改善。
- 最终身份连续两次运行的样本索引、指标、预测、模型和summary均一致；项目回归26/26通过。完整数值与边界见`W2_VALIDATION_RESULTS.md`。
- W2-F1已独立冻结`persistence`为W3/E6实际预测基线、`ridge_direct_ar`为轻量负比较、`perfect_foresight`为不可部署信息上界；未调低5%门，未打开boundary/final。关闭证据见`W2_F1_FREEZE.md`，机器接口见`configs/w2_f1_forecast_interface_v1.json`。

## W2-F1关闭见证（2026-08-23）

- W2-V1机器判定保持`validation_gate.pass=false`、`decision=persistence`；W2-F1不重跑模型、不修改门限。
- 六个W3输入误差时域冻结为10、20、30、60、120和360 min；非均匀节点位置留给W3-P0预注册，防止根据后续控制结果事后选择。
- 控制接口必需列和四类预测角色已经写入`configs/w2_f1_forecast_interface_v1.json`，一致性回归见`4_4/tests/test_w2_f1_freeze.py`。
- W2状态更新为`passed`；W3入口条件已满足但尚未启动计算。E2运行目录、进程和输出未被触碰。

## W3准备与冒烟见证（2026-08-23）

- W3-P0机器门9/9通过：nonuniform与uniform-tail均为30个决策、6 h跨度和0.5 s首控制区间；W2六时域均为nonuniform节点，锁定切分访问0。
- W3-C0独立审查关闭“实际预测偷读未来目标”和“OSQP失败静默fallback”两个阻塞；最终复审无剩余P0/S0阻塞。
- W3-S0在真实44状态工作点线性化上通过8/8结构门。两种QP各3/3次OSQP optimal、fallback 0；nonuniform/uniform-tail平均求解分别为0.01523/0.01241 s。
- W3状态更新为`in_progress`。当前结果只证明结构和求解链可用；W3-V1仍等待具体W2预测表provider、同信息PID前馈和正式闭环协议，E2运行期间不启动。
- 完整结果见`W3_PREPARATION_RESULTS.md`，机器结果见`runs/w3_preparation_v1/summary.json`和`runs/w3_smoke_v1/summary.json`。

## W3集成闭合与E4/E5并行准备见证（2026-08-24）

- W3-F1：正式W2 validation预测表provider通过未来issue、跨split、重复键、缺失、非有限与过期发布的fail-closed测试；`locked_splits_accessed=[]`。
- W3-B1：PID固定15 s前馈基线通过限幅、限速、时序与provider契约测试。它与MPC共享provider和issuance集合，但不宣称具有相同6 h信息容量。
- W3-R0 v2：三控制器2 s闭环落盘冒烟10/10结构门通过；两个MPC均为30个决策、21600 s，分别4/4次OSQP optimal、fallback 0、deadline miss 0。每个case显式记录4个控制步，R0不能被认证为42000步的V1。R0不形成性能主张。
- fresh-agent复审关闭stage/控制步误认证问题，并确认solver状态、绝对预算、study/场景/发布身份、覆盖保护、配置一致性及E2/E3进程冲突硬门全部闭合。跨W2/W3/E2/E3回归65/65通过。
- formal入口在当前E2进程存在时于模型启动前拒绝；W3-V1保持资源阻塞，未启动。
- E4/E5静态机器准备门11/11通过，`formal_execution_gate.pass=false`。准备阶段只打开两份配置，boundary结果访问0、锁定切分访问0、模型执行0。
- 当前机器证据：`runs/w3_v1_integration_smoke_v2/summary.json`与`runs/e4_e5_preparation_v1/summary.json`；初版R0已被v2取代。协议见`../03_thesis_research/W3_V1_EXECUTION_PROTOCOL.md`、`../03_thesis_research/E4_PREPARATION_PROTOCOL.md`和`../03_thesis_research/E5_PREPARATION_PROTOCOL.md`。

## E3准备关闭见证（2026-08-23）

- E3-P0完成：W1 P50/P90/P95/P99斜坡率已从17.56 MW源场映射到100 MW系统基准，机器文件为`../02_data/w1a_slow_band/e3_ramp_grid_v1.json`；数据暴露水平与工程边界探针分开。
- 波形冻结为斜坡—保持—对称回落—零输入恢复，`D_ref`与`D_dist`、正负方向和PID/MPC分别建域；正式P99首波为16条射线，最长单案例7122.89 s并由7200 s硬上限保护。
- E3-S0合成smoke完成8/8射线，9/9机器门通过：中心安全、非零案例安全且恢复、求解失败0、全部波形归零；同身份复跑一致。
- 全量回归32/32通过，其中E3新增6项。协议与准备结果见`../03_thesis_research/E3_PREPARATION_PROTOCOL.md`和`E3_PREPARATION_RESULTS.md`。
- 当前E2 q0.99最高慢频同身份 v2 已完成8/8聚合与跨频分层重算；E3正式首波未启动。6条恢复主导射线的强迫边界定向二分与双层runner冻结已完成，E2-F2由粗强迫边界造成的阻塞已解除，最高慢频代码身份不一致已关闭。

## W1-B关闭见证（2026-08-23）

- 机器证据：`../02_data/w1b_fast_band/audit_report.json`，`gate_decision=PASS`且6/6门检查通过。
- 直接功率：MORE-EU 432000行、2 s等间隔、25个Welch窗口，支持`0.005—0.25 Hz`。
- 高频电功率：Björkö 100 Hz公开数据筛得5个不少于120 s的独立运行段，累计`1588.65 s`、31个Welch窗口；`0.25—1 Hz`电—机通道相关系数最低`0.8572`、中位数`0.9226`。
- 定义链：Zenodo元数据定义`DCC/DCV`，官方风机说明定义直驱—变流器与10 ms测量控制链，Chalmers 2024年论文定义`DCC×DCV=P_el`。
- 声明边界：高频端只称“发电机侧实测电功率”，不得称并网点直接有功功率。未来E2使用`../02_data/w1b_fast_band/e2_fast_grid_v3.json`；已执行E2-D0保留冻结的`v2`。

## E0关闭见证（2026-08-23）

- 机器证据：`../01_model_validation/evidence/model_validation_80_90_100pct.json`；31/31检查通过，`g0_pass=true`，来源分类缺失/重复均为0。
- 回归：`4_4/tests/`共21项测试通过，覆盖模型验证、E2扰动语义、首失效/非星形、聚合契约和恢复敏感性。
- 原论文表值不能直接使当前44状态删减扩展模型达到数值稳态；11项差异按稳态闭合调整显式登记，不冒充原文实测值。
- E0关闭将频率阻尼`2.0`和棒速限值`72 spm`从隐藏常数提升为同值显式参数，并修正仅用于旧`disturbance_case=6`接口的固定0.9参考。正在运行的E2正式`D_ref`路径不使用该旧接口，数值常量未改变；后续波次仍需保存各自代码包身份，不得声称二进制身份相同。

## E2-D0执行见证（2026-08-23）

- 冻结配置：`configs/e2_d0_reference_pilot_v1.json`。
- 有效运行目录：`runs/e2_d0_reference_pilot_v1_retry/`；12/12射线完成，身份一致，中心点全部安全，未见非星形。
- 上界扩展：`runs/e2_d0_reference_upper_extension_v1/`；高频射线8—10在0.5 p.u.研究域上界仍安全，按右删失下界报告。
- 物理扰动见证：`runs/e2_d0_disturbance_witness_v1/`；6/6安全、恢复完整、求解失败0。
- 恢复机理：`runs/e2_d0_recovery_tail_witness_v1/`；360 s尾段仍确认无积分MPC存在约0.00528 p.u.稳态功率偏差。
- 结果判定与完整数值见`E2_D0_RESULTS.md`。D0显示显著相位非对称，E2-F1/F2保留四相位。
- 首次启动目录`runs/e2_d0_reference_pilot_v1/`仅保留启动故障见证，不得进入结果汇总：中心幅值0使增益定义发生除零；修复后中心点显式记为`undefined_zero_input`，全套17项回归测试通过。

## E2-F1当前执行见证（2026-08-23）

- 2026-08-24只读审计发现中频目录`e2_f1_center_slow_mid_wave_v1/`存在3种代码身份组合；其8/8输出保留为诊断资产，不进入正式聚合。审计见`E2_RUNNING_IDENTITY_AUDIT.md`；现已冻结代码并在新目录`e2_f1_center_slow_mid_wave_v2/`完整重跑。

- W1-A慢域幅值已从17.56 MW风场自身基准映射到100 MW系统基准，机器文件为`../02_data/w1a_slow_band/e2_slow_grid_v2.json`；数据暴露水平与边界搜索探针分开。
- 凝聚MPC通过逐数组等价见证，18项回归测试通过；吞吐与分波预算见`../../profile_output/E2_F1_PROFILE_REPORT.md`。
- 第一波配置：`configs/e2_f1_center_slow_high_wave_v1.json`；频率`3.076171875e-4 Hz`，中心工况、两控制器、四相位，共8条射线。
- 运行目录：`runs/e2_f1_center_slow_high_wave_v1/`；8/8射线完成，缺失0、重复0、波次内身份一致且中心全部安全，聚合文件为`e2_f1_center_slow_high_wave_aggregate.json`。射线1存在由恢复功率误差阈值穿越造成的非星形联合可行性，射线1、3恢复不完整；按协议保守采用首次失效，不调阈值。该波次代码身份为`B531...`，与后两波的`C931...`不同，单波结果有效但不得直接形成正式跨频比较，需同身份v2重跑。
- q0.99同身份修复重跑：`runs/e2_f1_center_slow_high_wave_v2/`已完成8/8并通过聚合验收；共享代码身份为`C931...`，缺失0、重复0、中心不安全0，非星形仅射线1，恢复不完整为射线1、3。聚合文件为`runs/e2_f1_center_slow_high_wave_v2/e2_f1_center_slow_high_wave_aggregate.json`，已替换旧高频波次参与跨频分层分析。
- 第二波配置：`configs/e2_f1_center_slow_mid_wave_v1.json`；频率`8.015950520833334e-5 Hz`对应W1-A累计谱能量`q0.95`，其余中心工况、控制器、四相位、幅值网格和0.5 s控制周期与第一波一致。v1目录已完成8/8但因代码身份混合不纳入正式结果；v2目录已在冻结代码下完整重跑并聚合通过：8/8、缺失0、重复0、身份唯一、中心安全8/8、非星形0，结构门禁为`true`。聚合文件为`runs/e2_f1_center_slow_mid_wave_v2/e2_f1_center_slow_mid_wave_aggregate.json`。
- 第三波配置：`configs/e2_f1_center_slow_low_wave_v1.json`；频率`3.4993489583333335e-5 Hz`对应W1-A累计谱能量`q0.90`。三波配置已交叉校验，除研究标识、频率与证据标签外实验契约完全一致，每波均为8条射线；q0.95 v2 聚合通过后已在`runs/e2_f1_center_slow_low_wave_v1/`用4个worker完成并聚合通过：8/8、缺失0、重复0、身份唯一、中心安全8/8、非星形0，结构门禁为`true`。聚合文件为`runs/e2_f1_center_slow_low_wave_v1/e2_f1_center_slow_low_wave_aggregate.json`。
- 三分波分层分析：机器结果`runs/e2_f1_layered_boundary_analysis_v2/e2_f1_layered_boundaries.json`覆盖24条射线、276个case，求解失败0。三波代码身份均为`C931...`，`formal_cross_frequency_comparison_ready=true`；波次内部18/24条的强迫阶段物理边界与恢复完成联合边界在冻结精度内一致；6/24条均为MPC相位0或π/2，联合边界由恢复不完整限制。6条强迫边界定向二分聚合见`runs/e2_f1_forcing_boundary_refinement_v1/e2_f1_forcing_boundary_refinement_aggregate.json`：全部为`[0.206225, 0.2115125] p.u.`，宽度`0.0052875 p.u.`，求解失败0、强迫阶段非星形0、恢复阶段新增物理违规0；双层runner冻结见`runs/e2_f1_forcing_boundary_refinement_v1/e2_layered_boundary_runner_contract.json`。
- E2-F2-P0配置与per-ray括区runner审计通过：配置`configs/e2_f2_cross_operating_slow_v1.json`覆盖9个功率×SOC运行点、三频、两控制器和四相位，共216条射线；q0.99预检波次为72条。配置SHA-256为`98B3C08719A647559C3979B5BE867706017CFE9C65CFCAA5A8911D8FB3013053`，runner SHA-256为`949CBCC8F6A5D062467F2D37C7C1E2B12893A84A19C0BD36F24A1B2C0562D43D`，核心代码身份保持`C931...`；审计见`E2_F2_CROSS_OPERATING_AUDIT.md`。q0.99预检已在本地CPU后台启动，运行目录为`runs/e2_f2_cross_operating_q099_precheck_v1/`，完成后再依据逐射线括区门决定后续波次。
- 恢复机理见证：`runs/e2_f1_recovery_duration_sensitivity_v1/`已完成8/8，失败索引0、求解失败0。四个幅值在180/360/720秒下恢复分类完全不变，720秒误差相对180秒仅下降`0.0805%—0.3203%`且末60秒斜率约为`10^-10 p.u./s`；确认最高慢频MPC相位0非星形来自持久恢复偏差与冻结门限共同作用，不是180秒尾段过短。完整结果见`E2_F1_CENTER_SLOW_RESULTS.md`。
