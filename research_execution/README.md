# 核—风—储硕士研究统一实施目录

本目录是后续模型验证、快速投稿、论文研究和实验记录的唯一实施入口。原开题报告保持不变；`idea-stage/` 与 `refine-logs/` 保留为 ARIS 查新和评审档案，不作为日常执行目录。

## 当前状态

- 当前阶段：**E2-F1中心慢域中频波次运行中，但正式聚合已被代码身份混合阻塞**。最高慢频波次和恢复时长敏感性见证已完成；中频目录只保留为诊断，当前任务退出后需在新versioned目录以单一冻结身份完整重跑。运行未被停止；审计见`04_experiments/E2_RUNNING_IDENTITY_AUDIT.md`。D0发现的恢复偏差与相位非对称仍保留，MPC offset-free改进和PID前馈转入W3/E6。
- G0 状态：**G0 PASS（控制导向模型，带明确校准限制）**。31项机器检查全部通过；参数/初值逐键分类，Vajpayee Table I/II原值与为实现44状态稳态闭合所做的调整分开记录。后续参数边界敏感性属于E5/E8，不得把G0通过表述为真实机组绝对校准或安全认证。
- 风电数据状态：**W0 PASS / W1-A PASS / W1-B PASS / W1 PASS**。Sotavento 10分钟数据支持慢尺度；MORE-EU 2 s直接有功功率支持`0.005—0.25 Hz`；Björkö 100 Hz发电机侧实测电功率经官方测量说明、Chalmers功率定义和5个独立运行段审计后支持`0.25—1 Hz`。高频端仍不得称为并网点直接有功功率；现有400 s数组继续作为待审资产。
- W2状态：**W2-F1 PASS / W2 CLOSED**。ridge在6/6时域降低RMSE，但六时域等权平均改善4.8457%低于5%门，且六时域MAE均变差；已按预注册fallback冻结`persistence`为W3/E6实际预测基线、ridge为负比较。完整运行及冻结均未访问boundary/final。
- W3状态：**W3-P0/C0/S0/F1/B1/R0 PASS / W3-V1 RESOURCE-BLOCKED**。W2 issue-time provider、PID固定15 s前馈、三控制器runner和聚合硬门已闭合；当前身份的2 s集成冒烟10/10结构门通过，两个MPC各4/4次OSQP optimal且无fallback。E2运行期间formal入口会被拒绝；当前不支持闭环性能收益。
- E4/E5状态：**E4-P0 PASS / E5-P0 PASS / FORMAL BLOCKED**。独立危险漏判、精确二项上界、边界误差、四类容量、三档归一化中心有限差分与活跃集切换协议已冻结；未知阈值和上游边界证据未伪造，正式门保持关闭。
- E3状态：**E3-P0 PASS / E3-S0 PASS / FORMAL P99 WAVE NOT LAUNCHED**。双域斜坡波形、W1四档速率映射、16射线P99首波、执行/并行/聚合入口和7200 s时长门已冻结；smoke 8/8、机器门9/9、项目回归32/32。当前等待E2释放CPU后执行两条吞吐探针。
- 快速成果目标：3 个月内取得录用，6 个月内取得带 ISBN 或 ISSN 的正式出版成果。
- 快速投稿方案：控制类英文会议作为可选首发方案，中文期刊作为并列方案；正式投稿前必须核验出版标识、预计上线时间和当前排期。

## 文件入口

- [实施设计](00_management/IMPLEMENTATION_DESIGN.md)：目录边界、模型验证门、快速论文与学位论文的关系。
- [总路线](00_management/MASTER_ROADMAP.md)：阶段、日期和验收关系。
- [模型验证协议](01_model_validation/MODEL_VALIDATION_PROTOCOL.md)：G0 的验证方法与阈值。
- [当前验证结果](01_model_validation/VALIDATION_RESULTS.md)：三功率点动态证据与剩余限制。
- [参数来源与范围](01_model_validation/PARAMETER_PROVENANCE.md)：参数证据等级和敏感性预注册范围。
- [五周投稿冲刺](02_fast_publication/FIVE_WEEK_SPRINT.md)：仅在 G0 按期通过时执行。
- [学位研究实施计划](03_thesis_research/IMPLEMENTATION_PLAN.md)：G0 之后的完整研究路线。
- [风电数据与多时间尺度预测工作计划](03_thesis_research/WIND_DATA_WORKPLAN.md)：W0—W3数据、预测、场景标定和控制接口。
- [七章论文结构与证据映射](03_thesis_research/THESIS_CHAPTER_MAP.md)：章节重点、三项创新与工作量。
- [实验跟踪表](04_experiments/EXPERIMENT_TRACKER.md)：各实验的进入条件和状态。
- [E1修正基线结果](04_experiments/E1_BASELINE_RESULTS.md)：公平基线、失败场景和进入E2的依据。
- [W0风电数据结果](04_experiments/W0_WIND_DATA_RESULTS.md)：正式数据源、哈希、结构审计、旧轨迹处置和开放限制。
- [W1风电特征结果](04_experiments/W1_WIND_FEATURE_RESULTS.md)：清洗、时间隔离、频谱/相关性/爬坡、E2/E3候选范围和频率分辨率门。
- [W1-B快速频带审计](04_experiments/W1B_FAST_BAND_RESULTS.md)：2 s直接有功功率、100 Hz发电机侧实测电功率、许可、来源定义、多段审计、PSD和适用边界。
- [W2准备协议](03_thesis_research/W2_PREPARATION_PROTOCOL.md)：时间隔离、任务、基线、轻量方法、指标、合格门和正式运行顺序。
- [W2准备结果](04_experiments/W2_PREPARATION_RESULTS.md)：6项准备门、样本量、非正式smoke结果和当前声明边界。
- [W2完整validation结果](04_experiments/W2_VALIDATION_RESULTS.md)：六时域正式指标、成功门失败、爬坡诊断、持续性决策和复现证据。
- [W2-F1冻结结果](04_experiments/W2_F1_FREEZE.md)：四类预测角色、控制接口、W3输入和数据隔离边界。
- [W3准备协议](03_thesis_research/W3_PREPARATION_PROTOCOL.md)：数据驱动节点、统一跨度/变量预算、可变区间QP和正式运行门。
- [W3准备与冒烟结果](04_experiments/W3_PREPARATION_RESULTS.md)：P0/C0/S0机器门、独立审查、求解时间和声明边界。
- [W3-V1执行协议](03_thesis_research/W3_V1_EXECUTION_PROTOCOL.md)：W2 issue-time provider、PID前馈、三控制器公平门、2 s R0与正式资源门。
- [E4准备协议](03_thesis_research/E4_PREPARATION_PROTOCOL.md) / [E5准备协议](03_thesis_research/E5_PREPARATION_PROTOCOL.md)：正式依赖、统计契约、容量弹性与保守阻塞门。
- [E3准备协议](03_thesis_research/E3_PREPARATION_PROTOCOL.md)：双域斜坡波形、W1速率映射、分波矩阵、门禁和计算预算。
- [E3准备结果](04_experiments/E3_PREPARATION_RESULTS.md)：准备门、smoke、正式P99射线清单、回归和当前执行边界。
- [E2程序冒烟结果](04_experiments/E2_SMOKE_RESULTS.md)：统一扰动语义、约束判定、二分缓存、单工况结果及不能外推的边界。
- [E2 MPC性能诊断](04_experiments/E2_MPC_DIAGNOSTIC_RESULTS.md)：PID曾明显占优的根因、阀门工作点修复、公平结果与后续预览型MPC边界。
- [E2多频率公平诊断](04_experiments/E2_FREQUENCY_DIAGNOSTIC_RESULTS.md)：固定参数下MPC/PID的频段交越、多指标代价和结构修改依据。
- [E2预览与积分增广机制结果](04_experiments/E2_PREVIEW_OFFSETFREE_RESULTS.md)：0.008 Hz交越定位、二因素消融、严格回归与公平验证限制。
- [E2正式执行协议](04_experiments/E2_FORMAL_EXECUTION_PROTOCOL.md)：冻结控制器、快慢证据线、首失效定义、阈值/步长门和三阶段正式网格。
- [E2-D0定义与尺度协议](04_experiments/E2_DEFINITION_AND_SCALING_PROTOCOL.md)：双域语义、系统基准、数据相关幅值、恢复尾段和中心验收矩阵。
- [E2-D0验收结果](04_experiments/E2_D0_RESULTS.md)：12射线边界、上界扩展、恢复偏差诊断、物理扰动见证与四相位放行结论。
- [E2运行中身份审计](04_experiments/E2_RUNNING_IDENTITY_AUDIT.md)：中频波次的混合代码身份、正式聚合阻塞与完整重跑规则。
- [W1-A慢域系统基准映射](02_data/w1a_slow_band/e2_slow_grid_v2.json)：区分17.56 MW风场相对幅值、100 MW系统暴露水平与边界搜索探针。

## 原始材料边界

- 正式开题报告：只读，不修改。
- `idea-stage/IDEA_REPORT.md`：查新与候选方向论证。
- `refine-logs/FINAL_PROPOSAL.md`：研究方案母版，后续转换为统一实施版本。
- `refine-logs/EXPERIMENT_PLAN.md`：实验计划母版，后续在模型验证通过后细化。
- `4_4/`：当前主要模型与控制代码，验证前不搬迁、不重命名。

## 工作规则

1. 所有论文图表必须能追溯到固定代码版本、配置、随机种子和原始结果。
2. 模型验证失败时先修复根因并重新运行全套回归，不绕过失败项。
3. 会议论文与后续期刊论文不得一稿多投；扩展稿必须增加实质性方法和实验内容，并明确引用先前版本。
4. “验证通过”仅表示模型适合作为学术仿真平台，不表示获得真实核电厂安全认证或工程许可。
5. 日常执行只更新本目录；ARIS 原始文件只作证据档案，不再作为“最新版”直接修改。
6. 风电预测训练、柔性域构建、独立验证和最终外推数据必须按时间隔离；分钟级插值不得冒充真实秒级高频观测。
