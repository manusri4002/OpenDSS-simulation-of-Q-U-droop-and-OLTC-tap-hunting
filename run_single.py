import argparse
import os
import matplotlib.pyplot as plt

import config
from feeder_setup import setup_full_scenario
from pv_profile import generate_pv_pu_profile
from cosim_engine import run_qsts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeder", required=True, choices=list(config.FEEDER_PATHS.keys()))
    ap.add_argument("--qu_db", type=float, default=config.BASELINE["qu_deadband_pct"])
    ap.add_argument("--qu_tau", type=float, default=config.BASELINE["qu_response_tau_s"])
    ap.add_argument("--oltc_db", type=float, default=config.BASELINE["oltc_ldc_deadband_pct"])
    ap.add_argument("--oltc_delay", type=float, default=config.BASELINE["oltc_delay_s"])
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    print(f"[1/4] Setting up scenario: feeder={args.feeder}, "
          f"qu_db={args.qu_db}%, qu_tau={args.qu_tau}s, "
          f"oltc_db={args.oltc_db}%, oltc_delay={args.oltc_delay}s")
    context = setup_full_scenario(
        feeder_key=args.feeder,
        qu_deadband_pct=args.qu_db,
        qu_response_tau_s=args.qu_tau,
        oltc_deadband_pct=args.oltc_db,
        oltc_delay_s=args.oltc_delay,
        seed=args.seed,
    )
    print(f"  -> PV fleet size: {len(context['pv_names'])} systems")
    print(f"  -> RegControls found: {context['regcontrol_names']}")
    print(f"  -> CapControls found: {context['capcontrol_names']}")
    if not context["regcontrol_names"]:
        print("  !! WARNING: no RegControl found on this feeder -- tap-hunting "
              "metric will be zero by construction. Check the feeder's master "
              ".dss file defines a RegControl, or add one manually.")

    print(f"[2/4] Generating PV profile ({config.SIM_START_HOUR}h - {config.SIM_END_HOUR}h, "
          f"{config.TIMESTEP_SECONDS}s resolution)")
    t_hours, pv_pu = generate_pv_pu_profile(
        config.SIM_START_HOUR, config.SIM_END_HOUR,
        dt_s=config.TIMESTEP_SECONDS, seed=args.seed,
    )
    print(f"  -> {len(t_hours)} steps generated")

    print("[3/4] Running QSTS co-simulation loop (this may take a while for a full day at 1s resolution)")
    results = run_qsts(context, t_hours, pv_pu, qu_deadband_pct=args.qu_db,
                        dt_s=config.TIMESTEP_SECONDS)

    sim_days_equiv = results["sim_hours"] / 24.0
    taps_per_day = results["total_tap_ops"] / sim_days_equiv if sim_days_equiv > 0 else float("nan")

    print("[4/4] Results summary")
    print(f"  Tap ops during 5-min warm-up (excluded from metric, shown for transparency): "
          f"{results['warmup_tap_ops']}")
    print(f"  Total tap operations in metric window: {results['total_tap_ops']}")
    print(f"  Simulated metric window: {results['sim_hours']:.2f} h "
          f"-> normalized taps/day: {taps_per_day:.1f}")
    print(f"  Per-RegControl tap ops: {results['tap_ops_per_regcontrol']}")
    print(f"  Estimated Q(U) curtailment: {results['curtailment_mvarh']:.4f} Mvar-h")

    csv_path = os.path.join(config.OUTPUT_DIR, f"log_{args.feeder}_db{args.qu_db}_tau{args.qu_tau}.csv")
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    print(f"  Saving log to: {os.path.abspath(csv_path)}")
    results["log"].to_csv(csv_path, index=False)
    print(f"  Full per-step log saved to: {csv_path}")

    log = results["log"]
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)

    axes[0].plot(log["t_hours"], log["pv_pu"], lw=0.6, color="orange")
    axes[0].set_ylabel("PV output (pu)")
    axes[0].set_title(f"{args.feeder} | Q(U) db={args.qu_db}% tau={args.qu_tau}s | "
                       f"OLTC db={args.oltc_db}% delay={args.oltc_delay}s")

    v_cols = [c for c in log.columns if c.startswith("v_pu_")]
    for c in v_cols:
        axes[1].plot(log["t_hours"], log[c], lw=0.7, label=c.replace("v_pu_", ""))
    axes[1].axhline(1.0, color="gray", lw=0.5, ls="--")
    axes[1].set_ylabel("Voltage (pu)")
    axes[1].legend(fontsize=8, loc="upper right")

    axes[2].step(log["t_hours"], log["tap_ops_this_step"].cumsum(), where="post", color="crimson")
    axes[2].set_ylabel("Cumulative tap ops")
    axes[2].set_xlabel("Hour of day")

    plt.tight_layout()
    fig_path = os.path.join(config.OUTPUT_DIR, f"plot_{args.feeder}_db{args.qu_db}_tau{args.qu_tau}.png")
    os.makedirs(os.path.dirname(os.path.abspath(fig_path)), exist_ok=True)
    plt.savefig(fig_path, dpi=150)
    print(f"  Plot saved to: {fig_path}")

    print("\nSanity checks before trusting these numbers:")
    print("  1. Confirm monitor bus names in feeder_setup.pick_monitor_buses() are "
          "real buses for this feeder (they are placeholder guesses).")
    print("  2. Confirm PV penetration/sizing (config.PV_PENETRATION_FRACTION / "
          "PV_TO_LOAD_KW_RATIO_RANGE) is reasonable relative to feeder's total load.")
    print("  3. Check taps_per_day against literature baselines (a few taps/day is "
          "typical baseline; tens+ suggests genuine hunting OR a wiring bug -- "
          "inspect the voltage plot for oscillation vs. a real trend).")
    print("  4. If regcontrol_names was empty, tap-hunting is being measured as zero "
          "by construction -- fix before drawing conclusions.")


if __name__ == "__main__":
    main()