# metrics_4_4.py
# -*- coding: utf-8 -*-
"""
Compute actuator-stress & mechanical-protection metrics for Section 4.4.

Input: one or multiple .npz result files (each contains time-series arrays).
Output: a CSV/table of metrics: TV, sign changes, saturation ratio, rate stats, etc.

Example:
  python metrics_4_4.py --cases MPC:res_mpc.npz PID:res_pid.npz --out metrics_4_4.csv

Required signals (best effort auto-detect):
  time: t or time
  rod speed: u_rod / v_rod / rod
  valve: u_val / mu_valve / valve
Optional:
  Pe, Tavg, P_bess, freq, SOC
"""

import argparse
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

import numpy as np

try:
    import pandas as pd
except ImportError:
    pd = None


# ---------------------------
# Helpers: robust signal fetch
# ---------------------------
def _first_existing_key(d: Dict[str, np.ndarray], keys: List[str]) -> Optional[str]:
    for k in keys:
        if k in d:
            return k
    # also try case-insensitive
    lower_map = {kk.lower(): kk for kk in d.keys()}
    for k in keys:
        kk = lower_map.get(k.lower())
        if kk is not None:
            return kk
    return None


def get_signal(d: Dict[str, np.ndarray], keys: List[str], name: str, required: bool = False) -> Optional[np.ndarray]:
    k = _first_existing_key(d, keys)
    if k is None:
        if required:
            raise KeyError(f"Required signal '{name}' not found. Tried keys={keys}. Available keys={list(d.keys())}")
        return None
    arr = np.asarray(d[k]).squeeze()
    return arr


def infer_dt(t: np.ndarray) -> float:
    t = np.asarray(t).squeeze()
    if t.size < 2:
        raise ValueError("time array too short to infer dt")
    dt = float(np.median(np.diff(t)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError(f"invalid dt inferred: {dt}")
    return dt


# ---------------------------
# Metrics
# ---------------------------
def total_variation(x: np.ndarray) -> float:
    x = np.asarray(x).squeeze()
    if x.size < 2:
        return 0.0
    return float(np.sum(np.abs(np.diff(x))))


def rms(x: np.ndarray) -> float:
    x = np.asarray(x).squeeze()
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def max_abs(x: np.ndarray) -> float:
    x = np.asarray(x).squeeze()
    return float(np.max(np.abs(x))) if x.size else 0.0


def mean_abs(x: np.ndarray) -> float:
    x = np.asarray(x).squeeze()
    return float(np.mean(np.abs(x))) if x.size else 0.0


def sign_changes(x: np.ndarray, eps: float = 1e-12) -> int:
    """
    Count sign changes excluding near-zero points.
    """
    x = np.asarray(x).squeeze()
    if x.size < 2:
        return 0
    s = np.sign(x)
    s[np.abs(x) <= eps] = 0
    # forward-fill zeros to avoid artificial flips
    s_ff = s.copy()
    last = 0
    for i in range(s_ff.size):
        if s_ff[i] == 0:
            s_ff[i] = last
        else:
            last = s_ff[i]
    return int(np.sum((s_ff[1:] * s_ff[:-1]) < 0))


def threshold_time_ratio(x: np.ndarray, t: np.ndarray, thr: float) -> float:
    """
    Fraction of time where |x| >= thr.
    """
    x = np.asarray(x).squeeze()
    t = np.asarray(t).squeeze()
    if x.size < 2 or t.size < 2:
        return 0.0
    dt = infer_dt(t)
    mask = (np.abs(x) >= thr).astype(float)
    return float(np.mean(mask))


def segments_over_threshold(x: np.ndarray, thr: float) -> int:
    """
    Number of continuous segments where |x| >= thr.
    """
    x = np.asarray(x).squeeze()
    if x.size < 2:
        return 0
    mask = (np.abs(x) >= thr).astype(int)
    # count rising edges
    return int(np.sum((mask[1:] - mask[:-1]) == 1) + (1 if mask[0] == 1 else 0))


def saturation_ratio(x: np.ndarray, xmin: float, xmax: float, tol: float = 1e-6) -> float:
    """
    Ratio of samples at/near bounds.
    """
    x = np.asarray(x).squeeze()
    if x.size == 0:
        return 0.0
    sat = (x <= xmin + tol) | (x >= xmax - tol)
    return float(np.mean(sat.astype(float)))


def rate_stats(x: np.ndarray, t: np.ndarray) -> Tuple[float, float, float]:
    """
    Compute stats of dx/dt: (max_abs_rate, rms_rate, mean_abs_rate)
    """
    x = np.asarray(x).squeeze()
    t = np.asarray(t).squeeze()
    if x.size < 2 or t.size < 2:
        return 0.0, 0.0, 0.0
    dt = infer_dt(t)
    dx = np.diff(x) / dt
    return max_abs(dx), rms(dx), mean_abs(dx)


def energy_throughput(P: np.ndarray, t: np.ndarray) -> Tuple[float, float]:
    """
    Return (E_abs, E_net) in "power-unit * second" (convert later if you want MWh).
    E_abs = integral |P| dt, E_net = integral P dt
    """
    P = np.asarray(P).squeeze()
    t = np.asarray(t).squeeze()
    if P.size < 2 or t.size < 2:
        return 0.0, 0.0
    E_abs = float(np.trapz(np.abs(P), t))
    E_net = float(np.trapz(P, t))
    return E_abs, E_net


def highfreq_energy_ratio(x: np.ndarray, t: np.ndarray, f_cut: float = 0.05) -> float:
    """
    Simple FFT energy ratio above cutoff frequency.
    f_cut in Hz. With Ts=0.5s, Nyquist=1 Hz.
    """
    x = np.asarray(x).squeeze()
    t = np.asarray(t).squeeze()
    if x.size < 8 or t.size < 8:
        return 0.0
    dt = infer_dt(t)
    fs = 1.0 / dt

    x0 = x - np.mean(x)
    n = x0.size
    # rfft
    X = np.fft.rfft(x0)
    freqs = np.fft.rfftfreq(n, d=dt)
    psd_like = (np.abs(X) ** 2)

    total = float(np.sum(psd_like))
    if total <= 0:
        return 0.0
    hf = float(np.sum(psd_like[freqs >= f_cut]))
    return hf / total


@dataclass
class MetricsConfig:
    # Rod speed threshold for "active motion" (spm). You can tune this.
    rod_active_thr: float = 1e-3
    # Valve threshold for "active motion" (p.u.)
    valve_active_thr: float = 1e-4
    # Bounds (optional) for saturation ratio
    rod_min: Optional[float] = None
    rod_max: Optional[float] = None
    valve_min: Optional[float] = None
    valve_max: Optional[float] = None
    # High-frequency cutoff for FFT energy ratio (Hz)
    hf_cut_hz: float = 0.05


def compute_case_metrics(label: str, data: Dict[str, np.ndarray], cfg: MetricsConfig) -> Dict[str, float]:
    # Required time
    t = get_signal(data, ["t", "time", "T"], "time", required=True)

    # Actuators
    rod = get_signal(data, ["u_rod", "v_rod", "rod", "rod_speed", "crdm", "uRod"], "rod_speed", required=False)
    valve = get_signal(data, ["u_val", "mu_valve", "valve", "valve_open", "uValve", "u_valve"], "valve", required=False)

    # Optional signals
    Pe = get_signal(data, ["Pe", "P_e", "Pelec", "power_e", "Pelec_pu"], "Pe", required=False)
    Tavg = get_signal(data, ["Tavg", "Tc_avg", "T_c", "Tavg_abs", "Tc"], "Tavg", required=False)
    Pbess = get_signal(data, ["P_bess", "Pbess", "Pess", "P_bat"], "P_bess", required=False)
    freq = get_signal(data, ["freq", "omega_g", "w_g", "omega", "df"], "freq", required=False)
    soc = get_signal(data, ["SOC", "soc", "SoC"], "SOC", required=False)

    out: Dict[str, float] = {"case": label}

    # Time window
    out["t_start"] = float(t[0])
    out["t_end"] = float(t[-1])
    out["dt"] = infer_dt(t)
    out["N"] = float(t.size)

    # Rod metrics (核心：4.4 机械保护)
    if rod is not None:
        out["rod_TV"] = total_variation(rod)
        out["rod_maxabs"] = max_abs(rod)
        out["rod_meanabs"] = mean_abs(rod)
        out["rod_rms"] = rms(rod)
        out["rod_sign_changes"] = float(sign_changes(rod))
        out["rod_active_ratio"] = float(threshold_time_ratio(rod, t, cfg.rod_active_thr))
        out["rod_active_segments"] = float(segments_over_threshold(rod, cfg.rod_active_thr))
        rmax, rrms, rmean = rate_stats(rod, t)
        out["rod_rate_maxabs"] = rmax
        out["rod_rate_rms"] = rrms
        out["rod_rate_meanabs"] = rmean
        out["rod_hf_energy_ratio"] = float(highfreq_energy_ratio(rod, t, cfg.hf_cut_hz))
        if cfg.rod_min is not None and cfg.rod_max is not None:
            out["rod_sat_ratio"] = float(saturation_ratio(rod, cfg.rod_min, cfg.rod_max))
    else:
        # keep columns consistent
        out["rod_TV"] = math.nan
        out["rod_maxabs"] = math.nan
        out["rod_meanabs"] = math.nan
        out["rod_rms"] = math.nan
        out["rod_sign_changes"] = math.nan
        out["rod_active_ratio"] = math.nan
        out["rod_active_segments"] = math.nan
        out["rod_rate_maxabs"] = math.nan
        out["rod_rate_rms"] = math.nan
        out["rod_rate_meanabs"] = math.nan
        out["rod_hf_energy_ratio"] = math.nan
        out["rod_sat_ratio"] = math.nan

    # Valve metrics
    if valve is not None:
        out["valve_TV"] = total_variation(valve)
        out["valve_maxabs"] = max_abs(valve)
        out["valve_meanabs"] = mean_abs(valve)
        out["valve_rms"] = rms(valve)
        out["valve_sign_changes"] = float(sign_changes(valve))
        out["valve_active_ratio"] = float(threshold_time_ratio(valve, t, cfg.valve_active_thr))
        out["valve_active_segments"] = float(segments_over_threshold(valve, cfg.valve_active_thr))
        vmax, vrms, vmean = rate_stats(valve, t)
        out["valve_rate_maxabs"] = vmax
        out["valve_rate_rms"] = vrms
        out["valve_rate_meanabs"] = vmean
        out["valve_hf_energy_ratio"] = float(highfreq_energy_ratio(valve, t, cfg.hf_cut_hz))
        if cfg.valve_min is not None and cfg.valve_max is not None:
            out["valve_sat_ratio"] = float(saturation_ratio(valve, cfg.valve_min, cfg.valve_max))
    else:
        out["valve_TV"] = math.nan
        out["valve_maxabs"] = math.nan
        out["valve_meanabs"] = math.nan
        out["valve_rms"] = math.nan
        out["valve_sign_changes"] = math.nan
        out["valve_active_ratio"] = math.nan
        out["valve_active_segments"] = math.nan
        out["valve_rate_maxabs"] = math.nan
        out["valve_rate_rms"] = math.nan
        out["valve_rate_meanabs"] = math.nan
        out["valve_hf_energy_ratio"] = math.nan
        out["valve_sat_ratio"] = math.nan

    # Optional: BESS & frequency (支持“高频由储能承担”的论证)
    if Pbess is not None:
        Eabs, Enet = energy_throughput(Pbess, t)
        out["bess_Pmaxabs"] = max_abs(Pbess)
        out["bess_Eabs"] = Eabs
        out["bess_Enet"] = Enet
    else:
        out["bess_Pmaxabs"] = math.nan
        out["bess_Eabs"] = math.nan
        out["bess_Enet"] = math.nan

    if soc is not None:
        out["soc_min"] = float(np.min(soc))
        out["soc_max"] = float(np.max(soc))
        out["soc_final"] = float(soc[-1])
    else:
        out["soc_min"] = math.nan
        out["soc_max"] = math.nan
        out["soc_final"] = math.nan

    if freq is not None:
        out["freq_maxabs"] = max_abs(freq)
        out["freq_rms"] = rms(freq)
    else:
        out["freq_maxabs"] = math.nan
        out["freq_rms"] = math.nan

    # Optional: power/temperature tracking summaries (如果你想 4.4 里也简单带一句)
    if Pe is not None:
        out["Pe_rms"] = rms(Pe)
    else:
        out["Pe_rms"] = math.nan

    if Tavg is not None:
        out["Tavg_rms"] = rms(Tavg - np.mean(Tavg))  # purely fluctuation
        out["Tavg_maxdev_from_mean"] = float(np.max(np.abs(Tavg - np.mean(Tavg))))
    else:
        out["Tavg_rms"] = math.nan
        out["Tavg_maxdev_from_mean"] = math.nan

    return out


def load_npz(path: str) -> Dict[str, np.ndarray]:
    obj = np.load(path, allow_pickle=True)
    return {k: obj[k] for k in obj.files}


def parse_cases(case_args: List[str]) -> List[Tuple[str, str]]:
    """
    --cases accepts: LABEL:path.npz
    """
    cases = []
    for c in case_args:
        if ":" not in c:
            raise ValueError(f"Invalid case '{c}'. Expected LABEL:path.npz")
        label, path = c.split(":", 1)
        cases.append((label.strip(), path.strip()))
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", required=True,
                    help="Cases as LABEL:path_to_npz  (e.g., MPC:res_mpc.npz PID:res_pid.npz)")
    ap.add_argument("--out", default="metrics_4_4.csv", help="Output CSV path")
    ap.add_argument("--rod-active-thr", type=float, default=1e-3, help="Rod active threshold (spm)")
    ap.add_argument("--valve-active-thr", type=float, default=1e-4, help="Valve active threshold (p.u.)")
    ap.add_argument("--rod-min", type=float, default=None)
    ap.add_argument("--rod-max", type=float, default=None)
    ap.add_argument("--valve-min", type=float, default=0.0)
    ap.add_argument("--valve-max", type=float, default=1.0)
    ap.add_argument("--hf-cut-hz", type=float, default=0.05, help="High-frequency cutoff for FFT energy ratio (Hz)")
    args = ap.parse_args()

    cfg = MetricsConfig(
        rod_active_thr=args.rod_active_thr,
        valve_active_thr=args.valve_active_thr,
        rod_min=args.rod_min,
        rod_max=args.rod_max,
        valve_min=args.valve_min,
        valve_max=args.valve_max,
        hf_cut_hz=args.hf_cut_hz,
    )

    cases = parse_cases(args.cases)
    rows = []
    for label, path in cases:
        data = load_npz(path)
        row = compute_case_metrics(label, data, cfg)
        rows.append(row)

    # Print a readable table
    if pd is not None:
        df = pd.DataFrame(rows)
        # put case first
        cols = ["case"] + [c for c in df.columns if c != "case"]
        df = df[cols]
        print(df.to_string(index=False))
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\nSaved: {args.out}")
    else:
        # fallback: minimal print
        print(rows)
        print(f"\nInstall pandas to export CSV nicely. Intended output: {args.out}")


if __name__ == "__main__":
    main()