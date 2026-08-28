import numpy as np

from model_schema import STATE_INDEX, validate_state_vector


def compute_turbine_command_pu(t, y, params, initial_conditions, disturbance_case=0, u_tg_ext=None):
    """计算调速器阀门命令；返回值是标幺命令，不是实际阀位。"""
    if u_tg_ext is not None:
        return float(np.clip(u_tg_ext, 0.0, 1.2))

    p_s = float(y[STATE_INDEX["p_s"]])
    omega_g = float(y[STATE_INDEX["omega_g"]])
    p_s0 = float(initial_conditions["p_s0"])
    p_ref_current = float(params.get("P_load_ref", 1.0))

    if disturbance_case == 6:
        base_load = p_ref_current
        wind_profile = params.get("wind_profile")
        if wind_profile is not None:
            wind_dt = float(params.get("wind_dt", 0.5))
            wind_t_start = float(params.get("wind_t_start", 20.0))
            if t >= wind_t_start:
                index = int(round((t - wind_t_start) / wind_dt))
                index = max(0, min(index, len(wind_profile) - 1))
                total_wind_effect = float(wind_profile[index])
            else:
                total_wind_effect = 0.0
        elif t >= 20.0:
            elapsed = t - 20.0
            total_wind_effect = (
                float(params.get("Wind_Amp_Main", 0.08))
                * np.sin(float(params.get("Wind_Freq_Main", 0.08)) * elapsed)
                + float(params.get("Wind_Amp_Noise", 0.02))
                * np.sin(float(params.get("Wind_Freq_Noise", 1.2)) * elapsed)
            )
        else:
            total_wind_effect = 0.0
        p_ref_current = float(np.clip(base_load - total_wind_effect, 0.5, 1.0))

    delta_omega = omega_g - 1.0
    droop = float(params.get("R_droop", 0.05))
    p_order = p_ref_current - delta_omega / droop
    pressure_ratio = p_s / p_s0 if p_s0 > 0.1 else 1.0
    pressure_correction = 1.0 / max(pressure_ratio, 0.8)
    return float(np.clip(p_order * pressure_correction, 0.0, 1.2))


def compute_bess_power_mw(y, params, p_bess_ext_mw=None):
    """计算实际BESS交流侧功率；放电为正，充电为负。"""
    soc = float(y[STATE_INDEX["SOC"]])
    if p_bess_ext_mw is None:
        delta_omega = float(y[STATE_INDEX["omega_g"]]) - 1.0
        e_int_freq = float(y[STATE_INDEX["e_int_freq"]])
        demand_pu = -(
            float(params.get("Kp_bess", 20.0)) * delta_omega
            + float(params.get("Ki_bess", 10.0)) * e_int_freq
        )
        power_mw = demand_pu * float(params.get("Eg_base", 100.0))
    else:
        power_mw = float(p_bess_ext_mw)

    soc_min = float(params.get("SOC_min", 0.1))
    soc_max = float(params.get("SOC_max", 0.9))
    if (soc >= soc_max and power_mw < 0.0) or (soc <= soc_min and power_mw > 0.0):
        power_mw = 0.0
    limit_mw = float(params.get("P_bess_max", 5.0))
    return float(np.clip(power_mw, -limit_mw, limit_mw))


def compute_rod_control(y, params, initial_conditions, control_mode="pid", v_rod_ext=None):
    """返回控制棒速度命令(spm)和温度积分状态导数。"""
    limit_spm = float(params["rod_speed_limit_spm"])
    if v_rod_ext is not None:
        return float(np.clip(v_rod_ext, -limit_spm, limit_spm)), 0.0
    if control_mode != "pid":
        return 0.0, 0.0

    t_avg = 0.5 * (float(y[STATE_INDEX["T_c1"]]) + float(y[STATE_INDEX["T_c2"]]))
    t_avg_ref = 0.5 * (float(initial_conditions["T_c10"]) + float(initial_conditions["T_c20"]))
    p_tur = (
        float(y[STATE_INDEX["P_hp"]])
        + float(y[STATE_INDEX["P_ip"]])
        + float(y[STATE_INDEX["P_lp"]])
    )
    p_tur_filtered = float(y[STATE_INDEX["P_tur_filtered"]])
    error_temp = t_avg - t_avg_ref
    error_power = float(y[STATE_INDEX["P_n"]]) - p_tur_filtered
    rod_control_deadband_c = float(params.get("rod_control_deadband_c", 0.55))
    if abs(error_temp) <= rod_control_deadband_c:
        return float(np.clip(
            -float(params.get("Kp_power", 0.0)) * error_power,
            -limit_spm,
            limit_spm,
        )), 0.0

    integral_error = float(y[STATE_INDEX["e_int_error"]])
    cmd_temp = float(params["Kp_temp"]) * error_temp + float(params["Ki_temp"]) * integral_error
    cmd_power = float(params.get("Kp_power", 0.0)) * error_power
    integral_derivative = 0.0 if abs(error_temp) > 50.0 else error_temp
    return float(np.clip(
        -(cmd_temp + cmd_power), -limit_spm, limit_spm
    )), integral_derivative


def observe_model(t, y, params, initial_conditions, disturbance_case=0, control_mode="pid",
                  u_tg_ext=None, v_rod_ext=None, p_bess_ext_mw=None):
    """返回论文和验证所需的派生信号，避免用状态索引猜测物理量。"""
    validate_state_vector(y)
    valve_command_pu = compute_turbine_command_pu(
        t, y, params, initial_conditions, disturbance_case, u_tg_ext
    )
    rod_speed_spm, _ = compute_rod_control(
        y, params, initial_conditions, control_mode, v_rod_ext
    )
    bess_power_mw = compute_bess_power_mw(y, params, p_bess_ext_mw)
    delta = float(y[STATE_INDEX["delta"]])
    x_total = float(params["X_d_prime"] + params["X_line"])
    p_e_pu = float(params["E_prime"] * params["V_inf"] / x_total * np.sin(delta))
    omega_g = float(y[STATE_INDEX["omega_g"]])
    frequency_base_hz = float(params["omega_base"]) / (2.0 * np.pi)
    return {
        "valve_command_pu": valve_command_pu,
        "valve_actual_pu": float(y[STATE_INDEX["C_tg"]]) / float(initial_conditions["C_tg0"]),
        "rod_speed_spm": rod_speed_spm,
        "bess_power_mw": bess_power_mw,
        "bess_power_pu": bess_power_mw / float(params.get("Eg_base", 100.0)),
        "frequency_pu": omega_g,
        "frequency_deviation_hz": (omega_g - 1.0) * frequency_base_hz,
        "p_e_pu": p_e_pu,
        "p_tur_pu": float(y[STATE_INDEX["P_hp"]] + y[STATE_INDEX["P_ip"]] + y[STATE_INDEX["P_lp"]]),
    }


def pwf_model(t, y, params, initial_conditions, disturbance_case=0, control_mode='pid', K_c=None,
              u_tg_ext=None, v_rod_ext=None, p_bess_ext_mw=None,
              p_grid_disturbance_pu=0.0):
    validate_state_vector(y)

    # =========================================================================
    # 1. 解包状态变量 (共 44 个状态)
    # =========================================================================
    # --- 原有状态 (0-41) ---
    P_n, C_in1, C_in2, C_in3, C_in4, C_in5, C_in6, T_f, T_c1, T_c2 = y[0:10]
    C_in = y[1:7]
    T_rxu, T_hot, T_sgi, T_sgu, T_cold, T_rxi = y[10:16]
    T_p1, T_p2, T_m1, T_m2, p_s = y[16:21]
    L_w, p_p = y[21:23]
    delta, omega_g = y[23:25]
    P_hp, P_ip, P_lp = y[25:28]
    h_wo, m_cos = y[28:30]
    rho_rod = y[30]
    i_lo, v_ilo, i_lr, v_ilr, T_rtd1, T_rtd2 = y[31:37]
    C_tg, v_Ctg, Q_heat = y[37:40]
    e_int_error = y[40]
    P_tur_filtered = y[41]

    # --- 新增 BESS 状态 (42-43) ---
    SOC = y[42]  # 荷电状态 (0.0 - 1.0)
    e_int_freq = y[43]  # 频率偏差积分项 (用于 BESS PI 控制)

    # =========================================================================
    # 2. 解包参数
    # =========================================================================
    Lambda = params['Lambda']
    beta_i = params['beta_i']
    lambda_i = params['lambda_i']
    H_f, H_c = params['H_f'], params['H_c']
    tau_f, tau_c, tau_r = params['tau_f'], params['tau_c'], params['tau_r']
    tau_rxu, tau_hot, tau_sgi, tau_sgu, tau_cold, tau_rxi = params['tau_rxu'], params['tau_hot'], params['tau_sgi'], \
        params['tau_sgu'], params['tau_cold'], params['tau_rxi']
    tau_p1, tau_p2, tau_pm1, tau_pm2 = params['tau_p1'], params['tau_p2'], params['tau_pm1'], params['tau_pm2']
    tau_mp1, tau_mp2, tau_ms1, tau_ms2 = params['tau_mp1'], params['tau_mp2'], params['tau_ms1'], params['tau_ms2']
    K_s, Ums1Sms1, Ums2Sms2 = params['Ks'], params['Ums1Sms1'], params['Ums2Sms2']
    cpfw, Tfw, hss = params['cpfw'], params['Tfw'], params['hss']
    d_w, d_s, Ap, l = params['d_w'], params['d_s'], params['Ap'], params['l']
    h_spr, h_w, h__w, v_w, v_s, J_p = params['h_spr'], params['h_w'], params['h-w'], params['v_w'], params['v_s'], \
        params['J_p']
    K_1p, K_2p, K_3p, K_4p = params['K_1p'], params['K_2p'], params['K_3p'], params['K_4p']
    F_hp, F_ip, F_lp, tau_hp, tau_ip, tau_lp = params['Fhp'], params['Fip'], params['Flp'], params['tau_hp'], params[
        'tau_ip'], params['tau_lp']
    msor = params['msor']
    alpha_f, alpha_c, G = params['alpha_f'], params['alpha_c'], params['G']
    i_lo0 = initial_conditions['i_lo0']
    tau_rtd = params['tau_rtd']
    tau_1, tau_2, K_lo, k_lo = params['tau_1'], params['tau_2'], params['K_lo'], params['k_lo']
    tau_3, tau_4, K_lr = params['tau_3'], params['tau_4'], params['K_lr']
    zeta_tg, K_tg, varpi_tg = params['zeta_tg'], params['K_tg'], params['varpi_tg']
    C_heat, R_heat, K_heat = params['C_heat'], params['R_heat'], params['K_heat']
    T_f0 = initial_conditions['T_f0']
    T_c10 = initial_conditions['T_c10']
    T_c20 = initial_conditions['T_c20']
    p_s0 = initial_conditions['p_s0']
    Ts0 = initial_conditions['Ts0']
    Tsat_Ps = params['Tsat_Ps']
    H_g = params['H_g']
    X_d_prime = params['X_d_prime']
    X_line = params['X_line']
    V_inf = params['V_inf']
    E_prime = params.get('E_prime', 1.1)
    omega_base = params['omega_base']
    R_droop = params.get('R_droop', 0.05)
    Press_dump_setpoint = params.get('Press_dump_setpoint', 8.5)
    K_dump = params.get('K_dump', 500.0)
    Wind_Amp_Main = params.get('Wind_Amp_Main', 0.08)
    Wind_Freq_Main = params.get('Wind_Freq_Main', 0.08)
    Wind_Amp_Noise = params.get('Wind_Amp_Noise', 0.02)
    Wind_Freq_Noise = params.get('Wind_Freq_Noise', 1.2)
    tau_filter = params.get('tau_filter', 20.0)

    # --- BESS 参数 ---
    Kp_bess = params.get('Kp_bess', 20.0)
    Ki_bess = params.get('Ki_bess', 10.0)
    BESS_Capacity = params.get('BESS_Capacity', 5.0)  # MWh
    P_bess_max = params.get('P_bess_max', 5.0)  # MW
    SOC_min = params.get('SOC_min', 0.1)
    SOC_max = params.get('SOC_max', 0.9)
    Eg_base = params.get('Eg_base', 100.0)  # MW (系统基准值)

    # =========================================================================
    # 3. 扰动与调速器
    # =========================================================================
    u_tg_cmd_pu = compute_turbine_command_pu(
        t, y, params, initial_conditions, disturbance_case, u_tg_ext
    )
    # 转换为物理开度
    u_tg = u_tg_cmd_pu * (initial_conditions['C_tg0'] / K_tg)

    # =========================================================================
    # 4. BESS 控制逻辑 (一次调频)
    # =========================================================================
    delta_omega_bess = omega_g - 1.0
    P_bess_mw = compute_bess_power_mw(y, params, p_bess_ext_mw)

    # 转回 p.u. 用于摇摆方程
    P_bess_pu_final = P_bess_mw / Eg_base

    # SOC 状态方程: E = SOC * Cap. dE/dt = -P_out.
    # dSOC/dt = -P_mw / (Capacity_MWh * 3600 sec/h)
    dSOC_dt = -P_bess_mw / (BESS_Capacity * 3600.0)

    # 泄漏积分使BESS只承担暂态调频，频率恢复后功率和SOC不再持续漂移。
    tau_bess_recovery = float(params.get("tau_bess_recovery", 60.0))
    de_int_freq_dt = (
        delta_omega_bess - e_int_freq / tau_bess_recovery
        if p_bess_ext_mw is None else 0.0
    )

    # =========================================================================
    # 5. 控制棒控制逻辑
    # =========================================================================
    # 过滤器
    tau_filter = params.get('tau_filter', 1.0)
    P_tur_current = P_hp + P_ip + P_lp
    dP_tur_filtered_dt = (P_tur_current - P_tur_filtered) / tau_filter

    v_rod, d_e_int_error_dt = compute_rod_control(
        y, params, initial_conditions, control_mode, v_rod_ext
    )

    # =========================================================================
    # 6. 物理方程
    # =========================================================================
    rho_f_feedback = alpha_f * (T_f - T_f0)
    rho_c1_feedback = alpha_c * (T_c1 - T_c10)
    rho_c2_feedback = alpha_c * (T_c2 - T_c20)
    rho_total = rho_rod + rho_f_feedback + rho_c1_feedback + rho_c2_feedback

    beta_total = np.sum(beta_i)
    precursor_term_for_power = np.dot(np.array(beta_i) / Lambda, C_in)
    dPn_dt = ((rho_total - beta_total) / Lambda) * P_n + precursor_term_for_power
    dCin_dt_list = np.array(lambda_i) * P_n - np.array(lambda_i) * np.array(C_in)
    dCin1_dt, dCin2_dt, dCin3_dt, dCin4_dt, dCin5_dt, dCin6_dt = dCin_dt_list

    dTf_dt = H_f * P_n - (1 / tau_f) * (T_f - T_c1)
    dTc1_dt = H_c * P_n + (1 / tau_c) * (T_f - T_c1) - (2 / tau_r) * (T_c1 - T_rxi)
    dTc2_dt = H_c * P_n + (1 / tau_c) * (T_f - T_c1) - (2 / tau_r) * (T_c2 - T_c1)
    dTrxu_dt = (1 / tau_rxu) * (T_c2 - T_rxu)
    dThot_dt = (1 / tau_hot) * (T_rxu - T_hot)
    dTsgi_dt = (1 / tau_sgi) * (T_hot - T_sgi)
    dTsgu_dt = (1 / tau_sgu) * (T_p2 - T_sgu)
    dTcold_dt = (1 / tau_cold) * (T_sgu - T_cold)
    dTrxi_dt = (1 / tau_rxi) * (T_cold - T_rxi)

    T_s = Ts0 + Tsat_Ps * (p_s - p_s0)
    dTp1_dt = (1 / tau_p1) * (T_sgi - T_p1) - (1 / tau_pm1) * (T_p1 - T_m1)
    dTp2_dt = (1 / tau_p2) * (T_p1 - T_p2) - (1 / tau_pm2) * (T_p2 - T_m2)
    dTm1_dt = (1 / tau_mp1) * (T_p1 - T_m1) - (1 / tau_ms1) * (T_m1 - T_s)
    dTm2_dt = (1 / tau_mp2) * (T_p2 - T_m2) - (1 / tau_ms2) * (T_m2 - T_s)

    m_so = (C_tg / initial_conditions['C_tg0']) * p_s * (msor / p_s0) if (p_s0 != 0) else 0
    margin = p_s - Press_dump_setpoint
    dump_valve_pos = 0.5 * (np.tanh(5.0 * margin) + 1.0)
    if margin < -1.0: dump_valve_pos = 0.0
    m_dump = K_dump * margin * dump_valve_pos
    m_dump = max(m_dump, 0.0)

    m_fw = m_so
    Q_in_sg = Ums1Sms1 * (T_m1 - T_s) + Ums2Sms2 * (T_m2 - T_s)
    H_net_out_sg = (m_so * hss) + (m_dump * hss) - (m_fw * cpfw * Tfw)
    dps_dt = (1 / K_s) * (Q_in_sg - H_net_out_sg)

    all_temp_derivs = np.array(
        [dTrxi_dt, dTc1_dt, dTc2_dt, dTrxu_dt, dThot_dt, dTsgi_dt, dTp1_dt, dTp2_dt, dTsgu_dt, dTcold_dt])
    m_sur = np.dot(params['V_v'], all_temp_derivs) * 0.000008
    i_heat, m_spr = 0.0, 0.0
    C1p = (d_w / d_s) - 1.0 if d_s != 0 else 1.0
    C2p = Ap * (l - L_w) * (d_w / d_s) * K_2p + Ap * L_w * K_1p
    m_w_prz, m_s_prz = d_w * Ap * L_w, d_s * Ap * (l - L_w)
    V_w_prz = m_w_prz / d_w if d_w != 0 else 0
    num_dpp = (Q_heat + m_sur * (p_p * v_s / (J_p * C1p) + h__w / C1p) + m_spr * (
            h_spr - h_w + h__w / C1p + p_p * v_w / (J_p * C1p)))
    den_dpp = (m_w_prz * (K_3p + K_4p * p_p / J_p) + m_s_prz * K_4p * p_p / J_p - V_w_prz / J_p + (C2p / C1p) * (
            h__w + p_p * v_s / J_p))
    dpp_dt = num_dpp / den_dpp if abs(den_dpp) > 1e-9 else 0.0
    term1 = (Ap * (l - L_w) * K_2p - C2p / C1p) * dpp_dt
    term2 = (1 / C1p ** 2) * (C2p * dpp_dt - m_sur - m_spr)
    term3 = m_sur / C1p
    dlw_dt = (1 / (d_s * Ap)) * (term1 + term2 + term3) if abs(d_s * Ap * C1p) > 1e-9 else 0.0

    m_so_norm = m_so / msor if msor != 0 else 0
    dP_hp_dt = (1 / tau_hp) * (F_hp * m_so_norm - P_hp)
    dP_ip_dt = (1 / tau_ip) * (F_ip * m_so_norm - P_ip)
    dP_lp_dt = (1 / tau_lp) * (F_lp * m_so_norm - P_lp)

    X_total = X_d_prime + X_line
    if X_total > 1e-6:
        P_e = (E_prime * V_inf / X_total) * np.sin(delta)
    else:
        P_e = P_tur_current

    # 正净负荷扰动表示额外电气功率需求，必须与控制器参考输入分离。
    D_damping = float(params["D_damping"])
    grid_disturbance_pu = float(p_grid_disturbance_pu)
    d_omega_g_dt = (1 / (2 * H_g)) * (
        P_tur_current
        + P_bess_pu_final
        - P_e
        - grid_disturbance_pu
        - D_damping * (omega_g - 1.0)
    )
    d_delta_dt = omega_base * (omega_g - 1.0)

    dh_wo_dt = m_so * (params['h_cow'] - h_wo) / params['m_coh'] if params['m_coh'] != 0 else 0
    dm_cos_dt = (m_so - m_cos) / params['tau_co'] if params['tau_co'] != 0 else 0
    di_lo_dt = v_ilo
    log_input = k_lo * P_n if P_n > 0 else 1e-10
    U_lo = K_lo * np.log10(log_input)
    dv_ilo_dt = (1 / (tau_1 * tau_2)) * (U_lo - i_lo - (tau_1 + tau_2) * v_ilo)
    di_lr_dt = v_ilr
    dv_ilr_dt = (1 / (tau_3 * tau_4)) * (K_lr * v_ilo + 12 - i_lr) - ((tau_3 + tau_4) / (tau_3 * tau_4)) * v_ilr
    dT_rtd1_dt = (1 / tau_rtd) * ((T_c1 + T_c2) / 2 - T_rtd1)
    dT_rtd2_dt = (1 / tau_rtd) * ((T_cold + T_hot) / 2 - T_rtd2)

    dC_tg_dt = v_Ctg
    dv_Ctg_dt = (varpi_tg ** 2) * (K_tg * u_tg - C_tg) - 2 * zeta_tg * varpi_tg * v_Ctg

    dQ_heat_dt = (1 / C_heat) * (K_heat * i_heat - Q_heat / R_heat) if abs(C_heat * R_heat) > 1e-9 else 0
    drho_rod_dt = G * (v_rod / 60.0)

    derivatives = [
        dPn_dt, dCin1_dt, dCin2_dt, dCin3_dt, dCin4_dt, dCin5_dt, dCin6_dt,
        dTf_dt, dTc1_dt, dTc2_dt,
        dTrxu_dt, dThot_dt, dTsgi_dt, dTsgu_dt, dTcold_dt, dTrxi_dt,
        dTp1_dt, dTp2_dt, dTm1_dt, dTm2_dt, dps_dt,
        dlw_dt, dpp_dt,
        d_delta_dt, d_omega_g_dt,
        dP_hp_dt, dP_ip_dt, dP_lp_dt,
        dh_wo_dt, dm_cos_dt,
        drho_rod_dt,
        di_lo_dt, dv_ilo_dt, di_lr_dt, dv_ilr_dt, dT_rtd1_dt, dT_rtd2_dt,
        dC_tg_dt, dv_Ctg_dt, dQ_heat_dt,
        d_e_int_error_dt,
        dP_tur_filtered_dt,
        dSOC_dt,
        de_int_freq_dt
    ]

    return derivatives
