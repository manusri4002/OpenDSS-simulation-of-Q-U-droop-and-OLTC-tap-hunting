import random
import opendssdirect as dss
import config


def load_feeder(feeder_key: str):
    """feeder_key: one of config.FEEDER_PATHS keys, e.g. 'ieee13'."""
    path = config.FEEDER_PATHS[feeder_key]
    dss.Text.Command("Clear")
    dss.Text.Command(f'Compile "{path}"')
    dss.Text.Command("Set MaxControlIter=300")

    dss.Text.Command("Solve")
    if not dss.Solution.Converged():
        raise RuntimeError(f"Base feeder {feeder_key} did not converge on initial solve")


def add_pv_fleet(penetration_fraction: float = None, pv_to_load_kw_ratio_range=None, seed: int = 42):
    penetration_fraction = penetration_fraction or config.PV_PENETRATION_FRACTION
    pv_to_load_kw_ratio_range = pv_to_load_kw_ratio_range or config.PV_TO_LOAD_KW_RATIO_RANGE
    rng = random.Random(seed)

    pv_names = []
    load = dss.Loads.First()
    idx = 0
    while load:
        load_name = dss.Loads.Name()
        bus = dss.CktElement.BusNames()[0]  # e.g. "645.1" or "675"
        kv = dss.Loads.kV()
        load_kw = dss.Loads.kW()

        if rng.random() < penetration_fraction and load_kw > 0:
            idx += 1
            ratio = rng.uniform(*pv_to_load_kw_ratio_range)
            pv_kw = load_kw * ratio
            pv_name = f"pv_{load_name}_{idx}"
            dss.Text.Command(
                f"New PVSystem.{pv_name} bus1={bus} phases=1 kV={kv:.3f} "
                f"kVA={pv_kw * 1.15:.2f} Pmpp={pv_kw:.2f} "
                f"irradiance=1.0 %cutin=0.1 %cutout=0.1 "
                f"VarFollowInverter=yes wattpriority=no"
            )
            pv_names.append(pv_name)

        load = dss.Loads.Next()

    dss.Text.Command("Solve")
    return pv_names


def build_vv_curve_points(deadband_pct: float):
    """
    IEEE 1547-2018 Category-B-style Q(U) curve, with a flat (Q=0) dead-band
    of +/- deadband_pct around 1.0 pu voltage. Returns list of (v_pu, q_pu).
    q_pu is fraction of inverter kVA (positive = inject/supply, per OpenDSS
    InvControl sign convention where curve is defined as %V vs %Q of kVA rated).
    """
    db = deadband_pct / 100.0
    v_lo_sat, q_lo_sat = 0.92, 0.44     # full injection below this V
    v_hi_sat, q_hi_sat = 1.08, -0.44    # full absorption above this V

    v2 = max(v_lo_sat + 0.001, 1.0 - db)
    v3 = min(v_hi_sat - 0.001, 1.0 + db)

    points = [
        (v_lo_sat, q_lo_sat),
        (v2, 0.0),
        (v3, 0.0),
        (v_hi_sat, q_hi_sat),
    ]
    return points


def configure_invcontrol(pv_names, deadband_pct: float, response_tau_s: float,
                          curve_name: str = "qu_curve"):
    """
    (Re)creates the XYCurve for the given dead-band and an InvControl in
    VOLTVAR mode referencing all pv_names, with LPF response time = response_tau_s.
    """
    pts = build_vv_curve_points(deadband_pct)
    npts = len(pts)
    vlist = " ".join(f"{v:.4f}" for v, _ in pts)
    qlist = " ".join(f"{q:.4f}" for _, q in pts)

    dss.Text.Command(
        f"New XYCurve.{curve_name} npts={npts} Yarray=({qlist}) Xarray=({vlist})"
    )

    pv_list_str = " ".join(pv_names)
    dss.Text.Command(
        f"New InvControl.invctrl_qu mode=VOLTVAR voltage_curvex_ref=rated "
        f"vvc_curve1={curve_name} DeltaQ_Factor=0.7 "
        f"VarChangeTolerance=0.0001 VoltageChangeTolerance=0.0001 "
        f"RateOfChangeMode=LPF LPFTau={response_tau_s:.3f} "
        f"EventLog=no PVSystemList=({pv_list_str})"
    )
    dss.Text.Command("Solve")


def configure_regcontrol(deadband_pct: float, delay_s: float):
    band_120base = deadband_pct / 100.0 * 120.0

    name = dss.RegControls.First()
    touched = []
    while name:
        rc_name = dss.RegControls.Name()
        dss.Text.Command(f"Edit RegControl.{rc_name} band={band_120base:.3f} delay={delay_s:.2f}")
        touched.append(rc_name)
        name = dss.RegControls.Next()

    dss.Text.Command("Solve")
    return touched


def configure_capcontrol(delay_s: float = 30.0, deadtime_s: float = 300.0):
    """Optional: sets delay/deadtime on any CapControls present (feeder-dependent)."""
    name = dss.CapControls.First()
    touched = []
    while name:
        cc_name = dss.CapControls.Name()
        dss.Text.Command(f"Edit CapControl.{cc_name} delay={delay_s:.2f} deadtime={deadtime_s:.2f}")
        touched.append(cc_name)
        name = dss.CapControls.Next()
    if touched:
        dss.Text.Command("Solve")
    return touched


def enable_time_varying_load(shape_name: str = "residential_daily_shape"):
    hourly_mult = [
        0.42, 0.40, 0.38, 0.38, 0.40, 0.45,   # 0-5h: overnight low
        0.55, 0.68, 0.75, 0.72, 0.68, 0.65,   # 6-11h: morning ramp, settling
        0.63, 0.62, 0.63, 0.66, 0.70, 0.80,   # 12-17h: midday plateau, afternoon rise
        0.95, 1.00, 0.92, 0.78, 0.60, 0.48,   # 18-23h: evening peak, decline
    ]
    mult_str = " ".join(f"{v:.3f}" for v in hourly_mult)
    dss.Text.Command(
        f"New Loadshape.{shape_name} npts=24 interval=1 useactual=no mult=({mult_str})"
    )

    load = dss.Loads.First()
    touched = 0
    while load:
        load_name = dss.Loads.Name()
        dss.Text.Command(f"Edit Load.{load_name} daily={shape_name}")
        touched += 1
        load = dss.Loads.Next()

    dss.Text.Command("Solve")
    return touched


def setup_full_scenario(feeder_key: str, qu_deadband_pct: float, qu_response_tau_s: float,
                         oltc_deadband_pct: float, oltc_delay_s: float, seed: int = 42,
                         enable_load_shape: bool = False):
    load_feeder(feeder_key)
    if enable_load_shape:
        enable_time_varying_load()
    pv_names = add_pv_fleet(seed=seed)
    configure_invcontrol(pv_names, qu_deadband_pct, qu_response_tau_s)
    reg_names = configure_regcontrol(oltc_deadband_pct, oltc_delay_s)
    cap_names = configure_capcontrol()

    if not dss.Solution.Converged():
        raise RuntimeError(
            f"Scenario did not converge: feeder={feeder_key}, "
            f"qu_db={qu_deadband_pct}, qu_tau={qu_response_tau_s}, "
            f"oltc_db={oltc_deadband_pct}, oltc_delay={oltc_delay_s}"
        )

    return {
        "feeder_key": feeder_key,
        "pv_names": pv_names,
        "regcontrol_names": reg_names,
        "capcontrol_names": cap_names,
    }
