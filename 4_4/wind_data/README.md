# 风电数据工具

## W0获取

```powershell
& '.\4_4\wind_data\acquire_sotavento.ps1'
```

脚本下载固定版本、核对四个SHA-256、解包原始CSV、将原始文件设为只读，并生成机器可读获取清单。目标文件已存在且哈希一致时不会覆盖；哈希不同则失败。

## W0审计

```powershell
python '.\4_4\wind_data\audit_wind_data.py'
```

审计结果写入`4_4/data/wind/manifests/w0_audit.json`，包括文件哈希、字段、时间范围、间隔、缺失、重复和旧400 s轨迹清单。

## W1特征分析

```powershell
python '.\4_4\wind_data\analyze_wind_features.py'
```

脚本执行确定性清洗、时间隔离划分、趋势—波动、爬坡、自相关、频谱分析和场景标定。只填补被有效值夹住的单个10分钟缺口；长缺口不填补。10分钟数据只用于慢尺度统计，不能重采样后宣称含有0.005—1 Hz真实波动。任何正式处理文件都不得由Excel手工修改生成。

## W2短时预测

准备门和非正式冒烟：

```powershell
python '.\4_4\wind_data\w2_forecasting.py' --stage smoke
```

正式validation入口：

```powershell
python '.\4_4\wind_data\w2_forecasting.py' --stage validation
```

冻结配置为`research_execution/04_experiments/configs/w2_prediction_v1.json`。准备阶段只允许读取train和validation；每个样本的24 h历史、发布时刻和目标时刻必须在同一切分。`boundary_construction`与`final_extrapolation`保持锁定。smoke只验证样本、算法、指标和文件接口，不提供正式预测性能证据。
