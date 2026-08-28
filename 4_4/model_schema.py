"""44状态核—风—储模型的唯一状态定义。"""

import numpy as np

STATE_NAMES = (
    "P_n",
    "C_in1", "C_in2", "C_in3", "C_in4", "C_in5", "C_in6",
    "T_f", "T_c1", "T_c2",
    "T_rxu", "T_hot", "T_sgi", "T_sgu", "T_cold", "T_rxi",
    "T_p1", "T_p2", "T_m1", "T_m2", "p_s",
    "L_w", "p_p",
    "delta", "omega_g",
    "P_hp", "P_ip", "P_lp",
    "h_wo", "m_cos",
    "rho_rod",
    "i_lo", "v_ilo", "i_lr", "v_ilr", "T_rtd1", "T_rtd2",
    "C_tg", "v_Ctg", "Q_heat",
    "e_int_error", "P_tur_filtered",
    "SOC", "e_int_freq",
)

STATE_INDEX = {name: index for index, name in enumerate(STATE_NAMES)}

STATE_UNITS = {
    "P_n": "p.u.",
    "C_in1": "relative precursor concentration",
    "C_in2": "relative precursor concentration",
    "C_in3": "relative precursor concentration",
    "C_in4": "relative precursor concentration",
    "C_in5": "relative precursor concentration",
    "C_in6": "relative precursor concentration",
    "T_f": "degC",
    "T_c1": "degC",
    "T_c2": "degC",
    "T_rxu": "degC",
    "T_hot": "degC",
    "T_sgi": "degC",
    "T_sgu": "degC",
    "T_cold": "degC",
    "T_rxi": "degC",
    "T_p1": "degC",
    "T_p2": "degC",
    "T_m1": "degC",
    "T_m2": "degC",
    "p_s": "MPa",
    "L_w": "m",
    "p_p": "MPa",
    "delta": "rad",
    "omega_g": "p.u.",
    "P_hp": "p.u.",
    "P_ip": "p.u.",
    "P_lp": "p.u.",
    "h_wo": "model enthalpy unit",
    "m_cos": "kg/s",
    "rho_rod": "absolute reactivity",
    "i_lo": "mA",
    "v_ilo": "mA/s",
    "i_lr": "mA",
    "v_ilr": "mA/s",
    "T_rtd1": "degC",
    "T_rtd2": "degC",
    "C_tg": "model valve coefficient",
    "v_Ctg": "model valve coefficient/s",
    "Q_heat": "kW/s",
    "e_int_error": "degC*s",
    "P_tur_filtered": "p.u.",
    "SOC": "p.u.",
    "e_int_freq": "p.u.*s",
}


# 这里记录数值模型允许的状态域；None表示该内部状态不施加额外硬边界。
STATE_RANGES = {
    name: (None, None) for name in STATE_NAMES
}
for name in ("P_n", "C_in1", "C_in2", "C_in3", "C_in4", "C_in5", "C_in6"):
    STATE_RANGES[name] = (0.0, None)
for name in (
    "T_f", "T_c1", "T_c2", "T_rxu", "T_hot", "T_sgi", "T_sgu", "T_cold",
    "T_rxi", "T_p1", "T_p2", "T_m1", "T_m2", "T_rtd1", "T_rtd2",
):
    STATE_RANGES[name] = (0.0, None)
STATE_RANGES.update({
    "p_s": (0.0, None),
    "L_w": (0.0, None),
    "p_p": (0.0, None),
    "P_hp": (0.0, None),
    "P_ip": (0.0, None),
    "P_lp": (0.0, None),
    "SOC": (0.0, 1.0),
})


def validate_state_vector(y):
    """验证状态向量长度，防止静默错位。"""
    if len(y) != len(STATE_NAMES):
        raise ValueError(f"Expected {len(STATE_NAMES)} states, got {len(y)}")


def solver_absolute_tolerances(base_atol=1e-9):
    """按状态量纲返回容差，避免解耦的超快仪表状态支配全部积分步长。"""
    tolerances = np.full(len(STATE_NAMES), float(base_atol))
    tolerances[STATE_INDEX["v_ilo"]] = max(float(base_atol), 1e-5)
    tolerances[STATE_INDEX["v_ilr"]] = max(float(base_atol), 1e-5)
    return tolerances
