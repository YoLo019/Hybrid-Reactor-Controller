# Sotavento 2016风电数据卡

## 数据身份

- 数据集：`Data for: Short-term wind power forecasting approach based on clustering algorithm and Seq2Seq model using NWP data`
- 版本：1
- DOI：`10.17632/vtsgxnwswn.1`
- 发布者：Mendeley Data
- 贡献者：Yanting Li
- 发布日期：2021-04-26
- 许可：CC BY 4.0（以Mendeley Data落地页声明为准）
- 风场：Sotavento Galicia，24台风机，装机容量17.56 MW
- 获取日期：2026-08-15

## 官方来源

- 数据落地页：<https://data.mendeley.com/datasets/vtsgxnwswn/1>
- DOI：<https://doi.org/10.17632/vtsgxnwswn.1>
- 关联论文：Yu Zhang, Yanting Li, Guangyao Zhang, *Short-term wind power forecasting approach based on Seq2Seq model using NWP data*, Energy 213 (2020) 118371，DOI `10.1016/j.energy.2020.118371`。

## 原始文件

| 文件 | 作用 | SHA-256 |
|---|---|---|
| `download.zip` | Mendeley版本1完整下载包 | `2F51105D9566BCC728A3A981FB89D3EB15F73A631FF454EE3C40AC6AD2F8D4DA` |
| `Raw_Data.rar` | 下载包内原始归档 | `B7DAC380F01FE2E4D55CEB4365130FCF4E7D7EAED2F9BD2F3A3DBBD0E7C0953B` |
| `wind farm historical data.csv` | 2016年10分钟风速、方向和`Energy`序列 | `2AB798258A566F2F2C6A4BCAB0023E6485E34C08C432A49D2C0F2D4DE4E09E6F` |
| `NWP.csv` | 2016年逐小时NWP变量 | `19C11D2A64924D2B48639780F5BCBE435FBAF9A7D4F886ACD9AA280C6707C722` |

## 初步结构审计

- 风场历史数据：52,704行，覆盖`2016-01-01 00:10:00`至`2017-01-01 00:00:00`，时间间隔全部为600 s，无重复时间戳。
- 历史数据缺失：`Speed` 578个，`Direction`和`Energy`各577个。
- NWP数据：8,784行，逐小时，无缺失、无重复时间戳。
- `Energy`原始范围为0—2687.03，但下载文件和落地页没有明确写出该字段单位。

## 使用边界

1. 本数据集作为风场级慢时间尺度趋势、爬坡、场景划分和预测研究的主要数据源。
2. 在单位得到更强证据前，论文统计应保留`Energy`原始量纲；若按“每10分钟kWh”换算平均功率，必须明确标注为待核实解释。
3. 时间戳未注明时区；在获得来源确认前保留naive时间，不擅自附加`Europe/Madrid`。
4. 10分钟数据不得通过插值后冒充0.5 s真实高频观测。快速扰动需要另行获取许可明确的高频数据，或构造由真实低频统计标定的合成残差并明确其性质。
5. 训练、验证、柔性域构建和最终测试必须按时间段隔离。

## 复现入口

- 获取脚本：`4_4/wind_data/acquire_sotavento.ps1`
- 审计脚本：`4_4/wind_data/audit_wind_data.py`
- 获取清单：`sotavento_mendeley_v1_acquisition.json`
- 审计结果：`w0_audit.json`
