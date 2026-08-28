# W1-B快速频带数据审计目录

本目录保存W1-B的外部原始数据、来源清单、审计代码和派生产物，不替代W0/W1-A的Sotavento 10分钟数据链。当前机器门为`PASS`：`0.005—0.25 Hz`由MORE-EU直接有功功率支持，`0.25—1 Hz`由Björkö发电机侧实测电功率支持。

## 目录边界

- `raw/`：从官方公开地址下载的原始文件，只读使用；不得覆盖或手工编辑。
- `provenance/`：固定提交或固定记录的一手来源元数据快照。
- `processed/`：审计脚本生成的PSD、五段独立运行片段和逐段统计。
- `figures/`：审计脚本生成的频谱证据图。
- `manifest.json`：来源、许可、获取版本和原始文件SHA-256。
- `audit_report.json`：时间轴、采样率、缺失、连续段和频带覆盖的机器报告。

## 复现

```powershell
python -m pip install -r .\research_execution\02_data\w1b_fast_band\requirements-w1b.txt
python .\research_execution\02_data\w1b_fast_band\audit_fast_band.py
```

脚本不把分钟级插值当作高频实测，也不把Björkö的`DCC×DCV`称为并网点有功功率。数据集元数据、官方风机说明和Chalmers 2024年论文共同支持把该乘积称为“发电机侧实测电功率”；五个独立运行段再用机械功率通道进行交叉检查。新实验的完整快速频带映射为`e2_fast_grid_v3.json`，旧`v2`只服务于已冻结的历史E2-D0。
