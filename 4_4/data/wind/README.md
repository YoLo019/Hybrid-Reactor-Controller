# 风电数据目录

本目录是论文风电数据的唯一正式入口。

## 分层

- `raw/`：下载得到的原始压缩包和原始CSV，只读，不清洗、不改名覆盖。
- `interim/`：由确定性脚本生成的清洗中间数据，可删除后重建。
- `processed/`：冻结的数据划分、预测输入和仿真轨迹，每个文件必须有生成配置。
- `manifests/`：数据卡、获取清单、哈希、结构审计和数据划分记录。

## 当前正式数据源

`raw/sotavento_mendeley_v1/`保存Mendeley Data DOI `10.17632/vtsgxnwswn.1`版本1。来源、许可、字段和限制见`manifests/SOTAVENTO_DATA_CARD.md`。

## 当前处理产物

- `processed/sotavento_2016_clean.csv`：由`4_4/wind_data/analyze_wind_features.py`生成的确定性清洗、标幺、时间划分、趋势和爬坡字段。
- `manifests/w1_feature_report.json`：W1清洗规则、数据质量、时间划分、频谱、自相关、场景候选和分辨率门。
- `processed/figures/`：W1校准池时序/分布、爬坡、频谱和自相关诊断图。
- `processed/w2_prediction_index_v1.csv`：W2防泄漏样本索引，覆盖10—360 min六个预测时域，只含train和validation。
- `manifests/w2_preparation_v1.json`：W2输入身份、样本计数、锁定切分访问记录、6项准备门和smoke产物哈希。

W2当前为“准备门通过、正式validation未执行”。机器报告记录`preparation_gate.pass=true`与`locked_splits_accessed=[]`；不能把smoke指标作为论文预测性能结果。

## 规则

1. 不直接编辑`raw/`中的文件；重新获取必须通过`4_4/wind_data/acquire_sotavento.ps1`并通过固定SHA-256。
2. 所有处理中间与正式结果必须由`4_4/wind_data/`中的脚本生成。
3. 训练、验证、柔性域构建和最终测试按时间段隔离，并在`manifests/`记录。
4. 10分钟数据不得经插值后表述为真实0.5 s高频观测。
5. 旧目录中的`wind_disturbance_400s_pu.npy`不移动、不覆盖，在来源链恢复前仅作待审资产。
6. W2正式validation只使用train拟合、validation选择超参数；在模型、阈值和控制器冻结前不得打开boundary/final输出。
