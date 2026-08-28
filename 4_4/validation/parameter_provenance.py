# -*- coding: utf-8 -*-
"""定义模型参数与初始条件的机器可读来源分类。"""

VAJPAYEE_2020 = {
    "citation": (
        "Vajpayee et al., Dynamic modelling, simulation, and control design of a "
        "pressurized water-type nuclear power plant, Nuclear Engineering and Design "
        "370 (2020) 110901"
    ),
    "doi": "10.1016/j.nucengdes.2020.110901",
    "table_i": "accepted manuscript p.6 (PDF p.7), Table I",
    "table_ii": "accepted manuscript p.10 (PDF p.11), Table II",
}


PARAMETER_PROVENANCE_GROUPS = {
    "vajpayee_2020_table_i": {
        "Ap", "C_heat", "Fhp", "Fip", "Flp", "G", "H_c", "H_f", "I_tg",
        "J_p", "J_tur", "K_1p", "K_2p", "K_3p", "K_4p", "K_heat", "K_lo",
        "K_lr", "K_rtd", "K_tg", "Ks", "Lambda", "Orv", "R_heat", "Tfw",
        "Tsat_Ps", "Ums1Sms1", "Ums2Sms2", "V_v", "Vm", "alpha_c", "alpha_f",
        "alpha_p", "beta_i", "cpfw", "d_s", "d_w", "h-cow", "h-w", "h_cow",
        "h_spr", "h_w", "hss", "k_hp", "k_lo", "l", "lambda_i", "m_coh",
        "m_s", "m_str", "m_w", "msor", "tau_1", "tau_2", "tau_3", "tau_4",
        "tau_c", "tau_co", "tau_cold", "tau_f", "tau_hot", "tau_hp", "tau_ip",
        "tau_lp", "tau_mp1", "tau_mp2", "tau_ms1", "tau_ms2", "tau_p1",
        "tau_p2", "tau_pm1", "tau_pm2", "tau_r", "tau_rtd", "tau_rxi",
        "tau_rxu", "tau_sgi", "tau_sgu", "v_s", "v_w", "varpi_tg", "zeta_tg",
    },
    "vajpayee_2020_table_ii": {
        "Ki_heat", "Ki_level", "Ki_power", "Ki_speed", "Ki_spr", "Ki_steam",
        "Kp_heat", "Kp_level", "Kp_power", "Kp_speed", "Kp_spr", "Kp_steam",
    },
    "model_design_choice": {
        "K_dump", "Ki_temp", "Kp_power_loop", "Kp_temp", "Press_dump_setpoint",
        "rod_control_deadband_c", "rod_speed_limit_spm", "tau_filter",
        "valve_rate_limit_pu_s",
    },
    "grid_scenario_assumption": {
        "D_damping", "E_prime", "H_g", "P_load_ref", "R_droop", "V_inf",
        "X_d_prime", "X_line", "grid_mode", "omega_base",
    },
    "bess_scenario_assumption": {
        "BESS_Capacity", "Eg_base", "Ki_bess", "Kp_bess", "P_bess_max",
        "SOC_max", "SOC_min", "tau_bess_recovery",
    },
    "wind_scenario_assumption": {
        "Wind_Amp_Main", "Wind_Amp_Noise", "Wind_Freq_Main", "Wind_Freq_Noise",
    },
    "inherited_untraced_model_value": {"h_lp"},
}


INITIAL_CONDITION_PROVENANCE_GROUPS = {
    "vajpayee_2020_table_i": {
        "C_tg0", "T_c10", "T_c20", "T_cold0", "T_f0", "T_hot0", "T_m10",
        "T_m20", "T_p10", "T_p20", "T_rxi0", "T_rxu0", "T_sgi0", "T_sgu0",
        "Ts0", "i_lr0", "omega_tur0", "p_p0", "p_s0",
    },
    "derived_equilibrium_initialization": {
        "C_in1_6_0", "P_n0", "P_tur_filtered0", "Q_heat0", "delta0",
        "e_int_freq0", "e_int_power0", "h_wo0", "i_lo0", "rho_rod0", "v_Ctg0",
        "v_ilo0", "v_ilr",
    },
    "model_instance_initialization": {"SOC0", "T_rtd1", "T_rtd2", "l_w0"},
}


STEADY_STATE_CLOSURE_ADJUSTMENTS = {
    "H_f": {"published": 71.8725, "model": 71.876},
    "H_c": {"published": 1.1254, "model": 1.1228},
    "tau_pm1": {"published": 0.5826, "model": 0.5824},
    "tau_pm2": {"published": 0.5826, "model": 0.5825},
    "tau_ms1": {"published": 0.3519, "model": 0.3522},
    "Ums1Sms1": {"published": 1.7295e8, "model": 172468418.07},
    "hss": {"published": 2.7656e6, "model": 2.763998e6},
    "K_1p": {"published": -8.152e-3, "model": -0.8152e-3},
    "T_rtd1": {"published": 327.30, "model": 319.715},
    "T_rtd2": {"published": 327.30, "model": 312.13},
    "l_w0": {"published": 28.06, "model": 8.5527},
}


def _audit_groups(actual_keys, groups):
    assignments = {}
    for group_name, keys in groups.items():
        for key in keys:
            assignments.setdefault(key, []).append(group_name)
    missing = sorted(set(actual_keys) - set(assignments))
    extra = sorted(set(assignments) - set(actual_keys))
    duplicates = {
        key: names for key, names in sorted(assignments.items()) if len(names) != 1
    }
    return {
        "missing": missing,
        "extra": extra,
        "duplicates": duplicates,
        "covered": not missing and not extra and not duplicates,
        "assignments": {key: names[0] for key, names in sorted(assignments.items())},
    }


def audit_parameter_provenance(params, initial_conditions):
    """检查每个代码参数与初值是否恰好归入一个来源类别。"""
    parameter_audit = _audit_groups(params, PARAMETER_PROVENANCE_GROUPS)
    initial_condition_audit = _audit_groups(
        initial_conditions, INITIAL_CONDITION_PROVENANCE_GROUPS
    )
    return {
        "source": VAJPAYEE_2020,
        "parameters": parameter_audit,
        "initial_conditions": initial_condition_audit,
        "steady_state_closure_adjustments": STEADY_STATE_CLOSURE_ADJUSTMENTS,
        "adjustment_rationale": (
            "The published parameter and initial-condition table does not directly close "
            "the reduced 44-state implementation at numerical equilibrium. These values "
            "are retained as explicit model-instance calibration adjustments and are not "
            "reported as verbatim published values."
        ),
        "pass": bool(
            parameter_audit["covered"] and initial_condition_audit["covered"]
        ),
    }
