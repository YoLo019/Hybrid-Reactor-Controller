# Hybrid Reactor Controller

核—风—储混合系统的模型、MPC/PID 控制器、风电预测接口与柔性域实验工具。

## 目录

- `4_4/`：当前模型、控制器、验证脚本、实验 runner 和回归测试。
- `research_execution/`：实验配置、模型验证记录、数据卡、研究协议和已完成运行证据。
- `requirements.txt`：Python 运行与测试依赖。

## 快速开始

```powershell
python -m pip install -r requirements.txt
python -m pytest 4_4/tests
```

风电原始数据、处理数据和 W2/E2/E3/W3 运行证据已纳入此迁移目录。需要重新生成数据时，
仍应按 `4_4/data/wind/README.md` 和 `4_4/wind_data/README.md` 获取并审计数据，再使用
`research_execution/04_experiments/configs/` 中的冻结配置运行。E2 的二进制轨迹使用 Git LFS
跟踪；活动 `RUNNING.lock` 不属于可迁移证据，不应提交。

W1-B 的 MORE-EU 来源没有标准许可证文本，正式发表或再分发前应按
`research_execution/04_experiments/W1B_FAST_BAND_RESULTS.md` 中的边界确认许可。Chalmers
论文副本因作者保留版权而不公开提交。

当前目录包含 E2-F2 已生成的逐射线结果；如果源实验仍在运行，应先结束进程并确认锁已清理，再复制新的运行目录。
