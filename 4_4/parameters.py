import numpy as np


def get_params():
    """返回包含模型所有参数和初始条件的字典。"""

    # 1. 先计算 beta_total 用于单位换算
    beta_i = [2.15e-4, 1.424e-3, 1.274e-3, 2.568e-3, 7.48e-4, 2.73e-4]
    beta_total = sum(beta_i)

    params = {
        # ===================================================
        # 【关键参数 1】：控制回路增益 (双重控制 + 频域解耦)
        # ===================================================
        # 1. 温度回路 (主导，消除漂移)
        'Kp_temp': 30.0,  # E1筛选后冻结：满足60 s降载斜坡温度约束的最小离散候选
        'Ki_temp': 0.1,  # 积分增益 (已增大，用于消除稳态误差)
        'rod_control_deadband_c': 0.3,

        # 2. 功率回路 (辅助，增加阻尼)
        'Kp_power_loop': 0.8,

        # 3. 滤波器 (创新点2：频域解耦)
        'tau_filter': 20.0,  # 20秒低通滤波，屏蔽高频风电噪声

        # ===================================================
        # 【关键参数 2】：物理与安全设定
        # ===================================================
        # 控制棒价值 G 的单位修正 (cents/step -> absolute/step)
        'G': 9.679e-1 * (beta_total / 100.0),



        # 蒸汽旁路 (Steam Dump) - 阈值提高到 8.0 以避免误动
        'Press_dump_setpoint': 8.5,
        'K_dump': 500.0,

        # ===================================================
        # 电网与调速器参数
        # ===================================================
        'grid_mode': 'infinite_bus',  # 并网模式
        'R_droop': 0.05,  # 5% 下垂控制
        'D_damping': 2.0,  # 系统频率阻尼为研究场景假设，不对应特定电网
        'P_load_ref': 1.0,  # 初始满功率

        # 发电机参数
        'H_g': 3.7, 'X_d_prime': 0.296, 'X_line': 0.1,
        'V_inf': 1.0, 'E_prime': 1.1,
        'omega_base': 2 * 3.1415926535 * 60,

        # 调速器液压参数
        'K_tg': 6.25, 'zeta_tg': 0.4933, 'varpi_tg': 14.6253,
        'valve_rate_limit_pu_s': 0.05,
        'rod_speed_limit_spm': 72.0,

        # 风光互补参数 (模拟不确定性)
        'Wind_Amp_Main': 0.08, 'Wind_Freq_Main': 0.08,
        'Wind_Amp_Noise': 0.02, 'Wind_Freq_Noise': 1.2,

        # --- 以下为固有物理参数 (保持不变) ---
        'Kp_power': 3.087e-2, 'Ki_power': 3.947e-3,  # 备用
        'Kp_steam': 5.368e-1, 'Ki_steam': 1.169e-1,
        'Kp_heat': 4.092e3, 'Ki_heat': 2.861e2,
        'Kp_spr': 2.935e5, 'Ki_spr': 1.695e5,
        'Kp_level': 1.275e3, 'Ki_level': 7.366e2,
        'Kp_speed': 6.430e2, 'Ki_speed': 8.426e2,

        'lambda_i': [1.2437e-2, 3.05e-2, 1.1141e-1, 3.013e-1, 1.12866, 3.0130],
        'beta_i': beta_i,
        'Lambda': 3e-5,
        'H_f': 71.876, 'H_c': 1.1228,
        'tau_f': 4.376, 'tau_c': 7.166, 'tau_r': 0.674,
        'tau_rxu': 2.517, 'tau_rxi': 2.145,
        'tau_hot': 0.234, 'tau_cold': 1.310,
        'tau_sgu': 0.726, 'tau_sgi': 0.659,
        'tau_p1': 1.2815, 'tau_p2': 1.2815,
        'tau_pm1': 0.5824, 'tau_pm2': 0.5825,
        'tau_mp1': 0.3519, 'tau_mp2': 0.1676,
        'tau_ms1': 0.3522, 'tau_ms2': 0.1676,
        'Ums1Sms1': 172468418.07, 'Ums2Sms2': 3.6312e8,
        'm_str': 2.1642e3, 'cpfw': 5.4791e3,
        'Tsat_Ps': 9.47, 'hss': 2.763998e6, 'Ks': 8.1016e7, 'Tfw': 232.2,
        'm_s': 2.0518e3, 'm_w': 1.8167e4,
        'd_w': 595.6684, 'd_s': 100.9506,
        'Vm': 30.4988, 'Ap': 3.566, 'l': 14.2524,
        'h_spr': 1.336e6, 'h_w': 1.6266e6, 'h-w': 9.7209e5,
        'v_w': 1.7e-3, 'v_s': 9.9e-3, 'J_p': 5.4027,
        'V_v': [0.5991, 0.1814, 0.1814, 1.3164, 0.2752, 2.776, 0.6022, 0.6022, 0.2776, 0.1927],
        'K_1p': -0.8152e-3, 'K_2p': 4.708e-3, 'K_3p': -1.118e-4, 'K_4p': 4.708e-3,
        'Fhp': 0.33, 'Fip': 0, 'Flp': 0.67, 'Orv': 1.0,
        'tau_hp': 10.0, 'tau_ip': 0.4, 'tau_lp': 1.0, 'k_hp': 0.8,
        'J_tur': 5.4040, 'I_tg': 1.99642e5, 'msor': 2.1642e3,
        'alpha_f': -2.16e-5, 'alpha_c': -1.8e-4, 'alpha_p': 1.5664e-4,
        'tau_1': 5e-8, 'tau_2': 2e-3, 'K_lo': 1.95692, 'k_lo': 1.1067e10,
        'tau_3': 1, 'tau_4': 1.01, 'K_lr': 47.065, 'tau_rtd': 8.2, 'K_rtd': 10.667,
        'h_lp': 2.35e6, 'tau_co': 7.0, 'm_coh': 41422.9, 'h_cow': 69.74, 'h-cow': 1036,
        'C_heat': 11.3, 'R_heat': 0.088, 'K_heat': 1000,

        # === BESS (电池储能系统) 参数 ===
        'BESS_Capacity': 5.0,  # MWh (假设值，可调整)
        'Kp_bess': 20.0,  # 频率响应比例增益 (MW/p.u. freq)
        'Ki_bess': 10.0,  # 频率响应积分增益
        'tau_bess_recovery': 60.0,  # s，消除调频后残余功率，避免SOC持续漂移
        'P_bess_max': 5.0,  # BESS 最大充放电功率 (MW) - 对应论文中的额定限制
        'SOC_min': 0.1,  # 最小荷电状态 (10%)
        'SOC_max': 0.9,  # 最大荷电状态 (90%)
        'Eg_base': 100.0,  # 系统基准功率 (MW)，用于标幺值转换
    }

    initial_conditions = {
        'P_n0': 1.0,
        'C_in1_6_0': [1.0] * 6,
        'T_f0': 626.66,
        'T_c10': 312.13, 'T_c20': 327.30,
        'T_rxu0': 327.30, 'T_hot0': 327.30,
        'T_sgi0': 327.30, 'T_sgu0': 296.96,
        'T_cold0': 296.96, 'T_rxi0': 296.96,
        'T_p10': 306.75, 'T_p20': 296.96,
        'T_m10': 297.41, 'T_m20': 292.51,
        'Ts0': 288.06, 'p_s0': 7.28,
        'p_p0': 15.41, 'l_w0': 8.5527,
        'omega_tur0': 60.0, 'h_wo0': 69.74,


        'rho_rod0': 0.0,
        # i_lo环节含5e-8 s时间常数，四舍五入初值会制造巨大伪瞬态。
        'i_lo0': params['K_lo'] * np.log10(params['k_lo'] * 1.0),
        'i_lr0': 12, 'v_ilo0': 0.0, 'v_ilr': 0.0,
        'C_tg0': 2.0481, 'v_Ctg0': 0.0, 'Q_heat0': 0.0,
        'T_rtd1': 319.715, 'T_rtd2': 312.13,
        'e_int_power0': 0.0,

        # 【新增】：滤波后的功率初始值
        'P_tur_filtered0': 1.0,

        'SOC0': 0.5,  # 初始荷电状态 50%
        'e_int_freq0': 0.0,  # BESS 频率偏差积分项初始值
    }

    # 初始功角计算
    X_total = params['X_d_prime'] + params['X_line']
    P_e0 = 1.0
    sin_delta0 = P_e0 * X_total / (params['E_prime'] * params['V_inf'])
    if abs(sin_delta0) > 1.0: sin_delta0 = 0.5
    initial_conditions['delta0'] = np.arcsin(sin_delta0)

    return params, initial_conditions
