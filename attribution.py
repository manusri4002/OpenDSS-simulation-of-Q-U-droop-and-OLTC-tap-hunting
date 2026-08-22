import argparse
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

import config
from feeder_setup import setup_full_scenario
from pv_profile import generate_pv_pu_profile, clear_sky_pu
from cosim_engine import run_qsts
from sweep import RESULTS_CSV


ATTRIBUTION_CSV_TEMPLATE = os.path.join(config.OUTPUT_DIR, "attribution_results{suffix}.csv")


def get_scenario_config(feeder: str, use_tuned: bool) -> dict:
    """Returns the (qu_db, qu_tau, oltc_db, oltc_delay) to use for this feeder's
    attribution run - either the paper's baseline, or that feeder's Q(U)-only
    tuned config (read from sweep_results.csv iftuned is passed)."""
    baseline = config.BASELINE
    if not use_tuned:
        return {
            "qu_deadband_pct": baseline["qu_deadband_pct"],
            "qu_response_tau_s": baseline["qu_response_tau_s"],
            "oltc_deadband_pct": baseline["oltc_ldc_deadband_pct"],
            "oltc_delay_s": baseline["oltc_delay_s"],
        }

    if not os.path.exists(RESULTS_CSV):
        print(f"  !! --tuned requested but {RESULTS_CSV} not found -- run sweep.py first. "
              f"Falling back to baseline for {feeder}.")
        return get_scenario_config(feeder, use_tuned=False)

    df = pd.read_csv(RESULTS_CSV)
    qu_only = df[
        (df["feeder"] == feeder)
        & (df["oltc_deadband_pct"] == baseline["oltc_ldc_deadband_pct"])
        & (df["oltc_delay_s"] == baseline["oltc_delay_s"])
    ].sort_values(["taps_per_day", "curtailment_mvarh"])

    if qu_only.empty:
        print(f"  !! No Q(U) grid data for {feeder} in {RESULTS_CSV}. Falling back to baseline.")
        return get_scenario_config(feeder, use_tuned=False)

    best = qu_only.iloc[0]
    return {
        "qu_deadband_pct": best["qu_deadband_pct"],
        "qu_response_tau_s": best["qu_response_tau_s"],
        "oltc_deadband_pct": baseline["oltc_ldc_deadband_pct"],
        "oltc_delay_s": baseline["oltc_delay_s"],
    }


def build_pv_variants(start_hour: float, end_hour: float, dt_s: float, seed: int):
    """Returns dict of {variant_name: (t_hours, pv_pu)} for the three counterfactuals."""
    t_hours, pv_normal = generate_pv_pu_profile(start_hour, end_hour, dt_s=dt_s, seed=seed)
    pv_smooth = clear_sky_pu(t_hours)
    pv_zero = np.zeros_like(t_hours)
    return {
        "A_normal_cloud_transients": (t_hours, pv_normal),
        "B_smooth_clear_sky": (t_hours, pv_smooth),
        "C_pv_zero": (t_hours, pv_zero),
    }


def run_attribution_for_feeder(feeder: str, sc: dict, start_hour: float, end_hour: float,
                                seed: int = 42, enable_load_shape: bool = False) -> dict:
    variants = build_pv_variants(start_hour, end_hour, config.TIMESTEP_SECONDS, seed)
    results = {}

    for variant_name, (t_hours, pv_pu) in variants.items():
        print(f"    Running variant: {variant_name}...")
        context = setup_full_scenario(
            feeder_key=feeder,
            qu_deadband_pct=sc["qu_deadband_pct"],
            qu_response_tau_s=sc["qu_response_tau_s"],
            oltc_deadband_pct=sc["oltc_deadband_pct"],
            oltc_delay_s=sc["oltc_delay_s"],
            seed=seed,
            enable_load_shape=enable_load_shape,
        )
        run_result = run_qsts(context, t_hours, pv_pu, qu_deadband_pct=sc["qu_deadband_pct"],
                               dt_s=config.TIMESTEP_SECONDS)
        sim_days = run_result["sim_hours"] / 24.0
        taps_per_day = run_result["total_tap_ops"] / sim_days if sim_days > 0 else float("nan")
        results[variant_name] = {
            "taps_per_day": taps_per_day,
            "total_tap_ops": run_result["total_tap_ops"],
        }
        print(f"      -> {taps_per_day:.1f} taps/day")

    total = results["A_normal_cloud_transients"]["taps_per_day"]
    smooth = results["B_smooth_clear_sky"]["taps_per_day"]
    load_only = results["C_pv_zero"]["taps_per_day"]

    load_driven = load_only
    smooth_pv_driven = smooth - load_only
    cloud_transient_driven = total - smooth

    return {
        "feeder": feeder,
        "total_taps_per_day": total,
        "load_driven_taps_per_day": load_driven,
        "smooth_pv_driven_taps_per_day": smooth_pv_driven,
        "cloud_transient_driven_taps_per_day": cloud_transient_driven,
        "qu_deadband_pct": sc["qu_deadband_pct"],
        "qu_response_tau_s": sc["qu_response_tau_s"],
        "oltc_deadband_pct": sc["oltc_deadband_pct"],
        "oltc_delay_s": sc["oltc_delay_s"],
    }


def plot_attribution(rows: list, suffix: str = ""):
    feeders = [r["feeder"] for r in rows]
    load = [r["load_driven_taps_per_day"] for r in rows]
    smooth = [r["smooth_pv_driven_taps_per_day"] for r in rows]
    transient = [r["cloud_transient_driven_taps_per_day"] for r in rows]

    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(feeders))
    ax.bar(x, load, label="Load-driven")
    ax.bar(x, smooth, bottom=load, label="Smooth PV (diurnal)-driven")
    bottom2 = [l + s for l, s in zip(load, smooth)]
    ax.bar(x, transient, bottom=bottom2, label="Cloud-transient-driven")

    ax.set_xticks(x)
    ax.set_xticklabels(feeders)
    ax.set_ylabel("Taps/day")
    ax.set_title("Figure 4: Tap-hunting attribution by cause")
    ax.legend()
    ax.axhline(0, color="black", lw=0.5)
    plt.tight_layout()

    out_path = os.path.join(config.OUTPUT_DIR, f"fig4_attribution{suffix}.png")
    plt.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeders", nargs="+", default=list(config.FEEDER_PATHS.keys()),
                     choices=list(config.FEEDER_PATHS.keys()))
    ap.add_argument("--tuned", action="store_true",
                     help="Use each feeder's Q(U)-only tuned config (from sweep_results.csv) "
                          "instead of baseline Q(U)/OLTC settings.")
    ap.add_argument("--time-varying-load", action="store_true",
                     help="Apply a common 24-hour residential daily load shape "
                          "instead of static (constant kW) loads, giving a nonzero load-driven "
                          "baseline in the attribution decomposition. Off by default -- does NOT "
                          "affect sweep.py/Table 1/Figures 1-3, which remain static-load. See "
                          "feeder_setup.enable_time_varying_load() for the shape used and its caveats.")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rows = []
    for feeder in args.feeders:
        sc = get_scenario_config(feeder, args.tuned)
        print(f"\n{feeder}: qu_db={sc['qu_deadband_pct']}% qu_tau={sc['qu_response_tau_s']}s "
              f"oltc_db={sc['oltc_deadband_pct']}% oltc_delay={sc['oltc_delay_s']}s "
              f"(time_varying_load={args.time_varying_load})")
        row = run_attribution_for_feeder(feeder, sc, config.SIM_START_HOUR, config.SIM_END_HOUR,
                                          seed=args.seed, enable_load_shape=args.time_varying_load)
        rows.append(row)

        print(f"  Decomposition: total={row['total_taps_per_day']:.1f}, "
              f"load={row['load_driven_taps_per_day']:.1f}, "
              f"smooth_pv={row['smooth_pv_driven_taps_per_day']:.1f}, "
              f"cloud_transient={row['cloud_transient_driven_taps_per_day']:.1f}")
        if row["smooth_pv_driven_taps_per_day"] < 0 or row["cloud_transient_driven_taps_per_day"] < 0:
           print("  !! NOTE: a negative component means load and PV effects are interacting "
      "nonlinearly (PV can mask a load-driven excursion that would have triggered "
      "a tap on its own). This is expected, not a bug.")

    suffix = "_timevaryingload" if args.time_varying_load else ""
    attribution_csv = ATTRIBUTION_CSV_TEMPLATE.format(suffix=suffix)
    os.makedirs(os.path.dirname(os.path.abspath(attribution_csv)), exist_ok=True)
    pd.DataFrame(rows).to_csv(attribution_csv, index=False)
    print(f"\nSaved: {attribution_csv}")

    plot_attribution(rows, suffix=suffix)


if __name__ == "__main__":
    main()
