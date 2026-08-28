# 模型问题与关闭条件

| ID | 优先级 | 根因问题 | 当前证据 | 状态 |
|---|---:|---|---|---|
| M01 | P0 | MPC输入偏差被称为增量/速率 | `delta_u`显式等于相邻绝对阀门命令差，自动检查通过 | closed |
| M02 | P0 | 控制棒存在MPC与外部PID双重所有权 | MPC改为阀门单输入；棒和BESS由独立闭环负责 | closed |
| M03 | P0 | 状态24被错误标为阀门状态 | `model_schema.py`建立唯一索引，状态24为`omega_g` | closed |
| M04 | P0 | 频率和BESS功率未完整保存 | NPZ保存`freq/omega_g/P_bess/SOC` | closed |
| M05 | P1 | BESS没有明确外部控制接口 | `p_bess_ext_mw`接口及内部模式均可观测 | closed |
| M06 | P1 | 初始SOC硬编码 | `build_y0()`使用`SOC0` | closed |
| M07 | P1 | 平衡点和多功率点未验证 | 80%/90%/100%平衡、漂移和守恒通过 | closed |
| M08 | P1 | 摆动方程及基准量依据不足 | 负荷正负RoCoF方向、内部功率平衡和场景参数分类通过；绝对电网校准明确排除 | closed |
| M09 | P1 | 数值收敛未验证 | 严/松容差差2.51e-9，0.5/0.25 s步长汇总差7.58e-9 | closed |
| M10 | P1 | 无自动回归 | `tests/test_model_validation.py` 3/3通过 | closed |
| M11 | P2 | BESS及控制参数含假设值 | 已明确标为容量/控制场景并预注册范围；边界敏感性转入E5/E8 | tracked_E5_E8 |
| M12 | P2 | 命令与实际执行器状态记录混杂 | 阀门command/actual及BESS实际功率已分开记录 | closed |
| M13 | P1 | BESS积分造成降载后持续放电 | 加入60 s泄漏恢复；80%/90%点600 s SOC无漂移 | closed |
| M14 | P1 | 超快解耦仪表状态支配积分耗时 | 保留原方程，采用状态量纲容差和稳态流形投影 | closed |
