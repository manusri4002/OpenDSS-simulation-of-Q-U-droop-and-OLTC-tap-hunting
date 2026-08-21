import argparse
import os
import time
import itertools
import pandas as pd

import config
from feeder_setup import setup_full_scenario
from pv_profile import generate_pv_pu_profile
from cosim_engine import run_qsts


RESULTS_CSV = os.path.join(config.OUTPUT_DIR, "sweep_results.csv")

RESULT_COLUMNS = [
    "feeder", "qu_deadband_pct", "qu_response_tau_s",
    "oltc_deadband_pct", "oltc_delay_s",
    "total_tap_ops", "warmup_tap_ops", "taps_per_day",
    "curtailment_mvarh", "sim_hours",
    "tap_ops_reg1", "tap_ops_reg2", "tap_ops_reg3",  # populated best-effort; see note in run_one_scenario
    "wall_seconds", "seed",
]


def build_scenario_list(feeders, grids):
    """
    Returns a list of scenario dicts to run, deduplicated (the shared
    baseline point across the qu-grid and oltc-grid is only listed once).
    """
    scenarios = []
    seen = set()

    baseline = config.BASELINE

    for feeder in feeders:
        if "qu" in grids:
            for db, tau in itertools.product(config.QU_DEADBAND_LEVELS_PCT,
                                              config.QU_RESPONSE_TAU_LEVELS_S):
                key = (feeder, db, tau, baseline["oltc_ldc_deadband_pct"], baseline["oltc_delay_s"])
                if key not in seen:
                    seen.add(key)
                    scenarios.append({
                        "feeder": feeder, "qu_deadband_pct": db, "qu_response_tau_s": tau,
                        "oltc_deadband_pct": baseline["oltc_ldc_deadband_pct"],
                        "oltc_delay_s": baseline["oltc_delay_s"],
                    })

        if "oltc" in grids:
            for db, delay in itertools.product(config.OLTC_DEADBAND_LEVELS_PCT,
                                                config.OLTC_DELAY_LEVELS_S):
                key = (feeder, baseline["qu_deadband_pct"], baseline["qu_response_tau_s"], db, delay)
                if key not in seen:
                    seen.add(key)
                    scenarios.append({
                        "feeder": feeder,
                        "qu_deadband_pct": baseline["qu_deadband_pct"],
                        "qu_response_tau_s": baseline["qu_response_tau_s"],
                        "oltc_deadband_pct": db, "oltc_delay_s": delay,
                    })

    return scenarios


def load_completed_keys():
    if not os.path.exists(RESULTS_CSV):
        return set()
    df = pd.read_csv(RESULTS_CSV)
    keys = set()
    for _, r in df.iterrows():
        keys.add((r["feeder"], float(r["qu_deadband_pct"]), float(r["qu_response_tau_s"]),
                   float(r["oltc_deadband_pct"]), float(r["oltc_delay_s"])))
    return keys


def append_result_row(row: dict):
    os.makedirs(os.path.dirname(os.path.abspath(RESULTS_CSV)), exist_ok=True)
    df_row = pd.DataFrame([row], columns=RESULT_COLUMNS)
    write_header = not os.path.exists(RESULTS_CSV)
    df_row.to_csv(RESULTS_CSV, mode="a", header=write_header, index=False)


def run_one_scenario(sc: dict, start_hour: float, end_hour: float, seed: int = 42) -> dict:
    t0 = time.time()

    context = setup_full_scenario(
        feeder_key=sc["feeder"],
        qu_deadband_pct=sc["qu_deadband_pct"],
        qu_response_tau_s=sc["qu_response_tau_s"],
        oltc_deadband_pct=sc["oltc_deadband_pct"],
        oltc_delay_s=sc["oltc_delay_s"],
        seed=seed,
    )
    t_hours, pv_pu = generate_pv_pu_profile(start_hour, end_hour, dt_s=config.TIMESTEP_SECONDS, seed=seed)
    results = run_qsts(context, t_hours, pv_pu, qu_deadband_pct=sc["qu_deadband_pct"],
                        dt_s=config.TIMESTEP_SECONDS)

    sim_days = results["sim_hours"] / 24.0
    taps_per_day = results["total_tap_ops"] / sim_days if sim_days > 0 else float("nan")

    # Best-effort: report up to 3 individual RegControl tap counts by name.
    # Feeders have different numbers/names of RegControls (IEEE13: 3,
    # IEEE34/123: 6-7), so this is NOT a complete per-regulator breakdown --
    # it's a quick top-3 for spot-checking, not a substitute for reading
    # tap_ops_per_regcontrol from the full log if you need all of them.
    per_reg = results["tap_ops_per_regcontrol"]
    per_reg_sorted = sorted(per_reg.items(), key=lambda kv: -kv[1])
    reg_vals = [v for _, v in per_reg_sorted[:3]] + [None] * max(0, 3 - len(per_reg_sorted))

    wall_seconds = time.time() - t0

    return {
        "feeder": sc["feeder"],
        "qu_deadband_pct": sc["qu_deadband_pct"],
        "qu_response_tau_s": sc["qu_response_tau_s"],
        "oltc_deadband_pct": sc["oltc_deadband_pct"],
        "oltc_delay_s": sc["oltc_delay_s"],
        "total_tap_ops": results["total_tap_ops"],
        "warmup_tap_ops": results["warmup_tap_ops"],
        "taps_per_day": taps_per_day,
        "curtailment_mvarh": results["curtailment_mvarh"],
        "sim_hours": results["sim_hours"],
        "tap_ops_reg1": reg_vals[0], "tap_ops_reg2": reg_vals[1], "tap_ops_reg3": reg_vals[2],
        "wall_seconds": wall_seconds,
        "seed": seed,
    }


def pick_tuned_config(df: pd.DataFrame, feeder: str) -> dict:
    """
    Selects the "tuned" Q(U) config for Table 1: minimum taps/day among the
    Q(U) grid runs for this feeder (OLTC held at baseline). Ties broken by
    lower curtailment. ADJUST THIS if your paper's definition of "tuned"
    should weight curtailment more heavily (e.g. minimize taps/day subject
    to curtailment below some cap) -- as written this purely minimizes taps.
    """
    baseline = config.BASELINE
    sub = df[
        (df["feeder"] == feeder)
        & (df["oltc_deadband_pct"] == baseline["oltc_ldc_deadband_pct"])
        & (df["oltc_delay_s"] == baseline["oltc_delay_s"])
    ]
    if sub.empty:
        return {}
    sub = sub.sort_values(["taps_per_day", "curtailment_mvarh"])
    best = sub.iloc[0]
    return {
        "qu_deadband_pct": best["qu_deadband_pct"],
        "qu_response_tau_s": best["qu_response_tau_s"],
        "taps_per_day": best["taps_per_day"],
        "curtailment_mvarh": best["curtailment_mvarh"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeders", nargs="+", default=list(config.FEEDER_PATHS.keys()),
                     choices=list(config.FEEDER_PATHS.keys()))
    ap.add_argument("--grid", nargs="+", default=["qu", "oltc"], choices=["qu", "oltc"])
    ap.add_argument("--hours", type=float, default=None,
                     help="Simulated window length in hours (default: config.SIM_START_HOUR to "
                          "SIM_END_HOUR, i.e. the full paper window). Shrink this for faster "
                          "iteration while debugging; use the full window for final results.")
    ap.add_argument("--start_hour", type=float, default=config.SIM_START_HOUR)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    start_hour = args.start_hour
    end_hour = (start_hour + args.hours) if args.hours else config.SIM_END_HOUR

    scenarios = build_scenario_list(args.feeders, args.grid)
    completed = load_completed_keys()

    todo = [sc for sc in scenarios
            if (sc["feeder"], sc["qu_deadband_pct"], sc["qu_response_tau_s"],
                sc["oltc_deadband_pct"], sc["oltc_delay_s"]) not in completed]

    print(f"Sweep plan: {len(scenarios)} total scenarios, {len(completed)} already done, "
          f"{len(todo)} remaining.")
    print(f"Simulated window per run: {start_hour}h - {end_hour}h ({end_hour - start_hour:.1f}h)")
    print(f"Results file: {os.path.abspath(RESULTS_CSV)}")

    if not todo:
        print("Nothing to do -- all scenarios already completed. "
              "Delete/rename sweep_results.csv to rerun from scratch.")
        return

    run_times = []
    for i, sc in enumerate(todo):
        eta_str = ""
        if run_times:
            avg = sum(run_times) / len(run_times)
            remaining = avg * (len(todo) - i)
            eta_str = f" (avg {avg:.0f}s/run, ETA {remaining/60:.1f} min)"

        print(f"\n[{i+1}/{len(todo)}] {sc['feeder']}: "
              f"qu_db={sc['qu_deadband_pct']}% qu_tau={sc['qu_response_tau_s']}s "
              f"oltc_db={sc['oltc_deadband_pct']}% oltc_delay={sc['oltc_delay_s']}s{eta_str}")
        try:
            row = run_one_scenario(sc, start_hour, end_hour, seed=args.seed)
            append_result_row(row)
            run_times.append(row["wall_seconds"])
            print(f"  -> taps/day={row['taps_per_day']:.1f}, "
                  f"curtailment={row['curtailment_mvarh']:.4f} Mvar-h, "
                  f"took {row['wall_seconds']:.1f}s")
        except Exception as e:
            print(f"  !! FAILED: {e}")
            print("  Skipping this scenario, continuing with the rest. "
                  "Rerun sweep.py later to retry only failed/missing scenarios.")

    print(f"\nSweep complete. Results in {os.path.abspath(RESULTS_CSV)}")

    if os.path.exists(RESULTS_CSV):
        df = pd.read_csv(RESULTS_CSV)
        print("\n=== Tuned config per feeder (min taps/day within Q(U) grid) ===")
        for feeder in args.feeders:
            tuned = pick_tuned_config(df, feeder)
            if tuned:
                print(f"  {feeder}: db={tuned['qu_deadband_pct']}% tau={tuned['qu_response_tau_s']}s "
                      f"-> {tuned['taps_per_day']:.1f} taps/day, "
                      f"{tuned['curtailment_mvarh']:.4f} Mvar-h curtailment")
            else:
                print(f"  {feeder}: no Q(U) grid data found yet")


if __name__ == "__main__":
    main()