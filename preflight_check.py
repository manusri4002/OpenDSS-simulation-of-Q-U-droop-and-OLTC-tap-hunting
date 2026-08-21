import argparse
import opendssdirect as dss

import config
from feeder_setup import setup_full_scenario
from pv_profile import generate_pv_pu_profile
from cosim_engine import run_qsts, pick_monitor_buses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeder", required=True, choices=list(config.FEEDER_PATHS.keys()))
    args = ap.parse_args()

    print(f"[1/5] Loading {args.feeder} raw (no PV/controls yet) to inspect structure...")
    dss.Text.Command("Clear")
    dss.Text.Command(f'Compile "{config.FEEDER_PATHS[args.feeder]}"')
    dss.Text.Command("Solve")
    if not dss.Solution.Converged():
        print("  !! Base feeder did NOT converge. Stop here -- fix the .dss file/path first.")
        return

    all_buses = dss.Circuit.AllBusNames()
    print(f"  -> {len(all_buses)} buses. First 15: {all_buses[:15]}")

    reg = dss.RegControls.First()
    reg_names = []
    while reg:
        reg_names.append(dss.RegControls.Name())
        reg = dss.RegControls.Next()
    print(f"  -> RegControls found: {reg_names}")
    if not reg_names:
        print("  !! WARNING: no RegControl in this feeder file. Tap-hunting metric "
              "will be zero by construction. You'll need to add one manually via "
              "`New RegControl....` before this feeder is usable for the study.")

    cap = dss.CapControls.First()
    cap_names = []
    while cap:
        cap_names.append(dss.CapControls.Name())
        cap = dss.CapControls.Next()
    print(f"  -> CapControls found: {cap_names}")

    # Diagnostic: print the ACTUAL bus each RegControl senses/regulates
    # (the secondary/downstream side of its transformer winding), since
    # guessing "the substation bus" is often wrong -- e.g. IEEE13's '650'
    # and IEEE34's '800' are both SOURCE-side buses that stay essentially
    # fixed, while the real regulated bus (what RegControl reacts to) is
    # downstream, at the transformer's regulated winding bus.
    print(f"\n  RegControl actual regulated buses (use these for monitor_buses, "
          f"not guesses):")
    rn = dss.RegControls.First()
    while rn:
        rc_name = dss.RegControls.Name()
        xfmr = dss.RegControls.Transformer()
        wdg = dss.RegControls.TapWinding()
        dss.Transformers.Name(xfmr)
        dss.Transformers.Wdg(wdg)
        bus_reg = dss.CktElement.BusNames()[wdg - 1] if wdg >= 1 else dss.CktElement.BusNames()[-1]
        print(f"    {rc_name}: transformer={xfmr}, winding={wdg}, regulated bus={bus_reg}")
        rn = dss.RegControls.Next()

    total_kw = dss.Circuit.TotalPower()[0] * -1  # OpenDSS reports substation power as negative (delivered)
    print(f"  -> Total feeder load (approx, from substation power): {total_kw:.1f} kW")

    print(f"\n[2/5] Checking configured monitor buses against real bus names...")
    mon_buses = pick_monitor_buses(args.feeder)
    for label, bus in mon_buses.items():
        match = bus in all_buses or any(b.startswith(bus + ".") for b in all_buses) or bus in [b.split(".")[0] for b in all_buses]
        status = "OK" if match else "!! NOT FOUND -- fix feeder_setup.pick_monitor_buses()"
        print(f"  {label}: '{bus}' -> {status}")

    print(f"\n[3/5] Setting up full scenario (PV fleet + InvControl + RegControl) at baseline params...")
    context = setup_full_scenario(
        feeder_key=args.feeder,
        qu_deadband_pct=config.BASELINE["qu_deadband_pct"],
        qu_response_tau_s=config.BASELINE["qu_response_tau_s"],
        oltc_deadband_pct=config.BASELINE["oltc_ldc_deadband_pct"],
        oltc_delay_s=config.BASELINE["oltc_delay_s"],
        seed=1,
    )
    print(f"  -> PV fleet: {len(context['pv_names'])} systems")
    pv_total_kw = 0.0
    for n in context["pv_names"]:
        dss.PVsystems.Name(n)
        pv_total_kw += dss.PVsystems.Pmpp()
    penetration_pct = (pv_total_kw / total_kw * 100) if total_kw > 0 else float("nan")
    print(f"  -> Total PV nameplate: {pv_total_kw:.1f} kW ({penetration_pct:.0f}% of feeder load)")
    if penetration_pct > 150 or penetration_pct < 10:
        print("  !! PV penetration looks extreme (either way) -- check "
              "config.PV_PENETRATION_FRACTION / PV_TO_LOAD_KW_RATIO_RANGE before trusting results.")

    print(f"\n[4/5] Running a 60-SECOND smoke test (not the full day)...")
    t_hours, pv_pu = generate_pv_pu_profile(12.0, 12.0 + 60.0 / 3600.0, dt_s=1.0, seed=1)
    results = run_qsts(context, t_hours, pv_pu,
                        qu_deadband_pct=config.BASELINE["qu_deadband_pct"], dt_s=1.0,
                        warmup_seconds=0.0)  # smoke test window is only 60s total --
                                              # a real warmup would consume all of it
    print(f"  -> Ran {len(t_hours)} steps without crashing.")
    print(f"  -> Tap ops in 60s: {results['total_tap_ops']} (expect 0 or very few over just 60s)")
    print(f"  -> Curtailment in 60s: {results['curtailment_mvarh']:.6f} Mvar-h")
    print(f"  -> Sample voltage columns: {[c for c in results['log'].columns if c.startswith('v_pu_')]}")
    print(results["log"].head(3).to_string())

    print(f"\n[5/5] Verdict:")
    problems = []
    if not reg_names:
        problems.append("no RegControl found")
    for label, bus in mon_buses.items():
        if not (bus in all_buses or bus in [b.split(".")[0] for b in all_buses]):
            problems.append(f"monitor bus '{bus}' ({label}) not found")
    if penetration_pct > 150 or penetration_pct < 10:
        problems.append("PV penetration looks off")

    if problems:
        print("  NOT READY for full run_single.py yet. Fix these first:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  Looks good -- safe to proceed to the full `python run_single.py "
              f"--feeder {args.feeder}` run.")


if __name__ == "__main__":
    main()