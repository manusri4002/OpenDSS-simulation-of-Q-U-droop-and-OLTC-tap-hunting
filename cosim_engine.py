from dataclasses import dataclass, field
import numpy as np
import pandas as pd
import opendssdirect as dss

from feeder_setup import build_vv_curve_points


@dataclass
class TapTracker:
    """Tracks per-unit tap position of each RegControl's transformer winding."""
    reg_names: list
    transformer_of: dict = field(default_factory=dict)   # reg_name -> (xfmr_name, winding)
    tap_step_pu: dict = field(default_factory=dict)       # reg_name -> pu per discrete tap
    last_tap_pu: dict = field(default_factory=dict)
    tap_op_count: dict = field(default_factory=dict)

    @classmethod
    def build(cls, reg_names):
        tt = cls(reg_names=reg_names)
        for rn in reg_names:
            dss.RegControls.Name(rn)
            xfmr = dss.RegControls.Transformer()
            wdg = dss.RegControls.TapWinding()
            tt.transformer_of[rn] = (xfmr, wdg)

            dss.Transformers.Name(xfmr)
            dss.Transformers.Wdg(wdg)
            max_tap = dss.Transformers.MaxTap()
            min_tap = dss.Transformers.MinTap()
            num_taps = dss.Transformers.NumTaps()
            step = (max_tap - min_tap) / num_taps if num_taps else 0.00625
            tt.tap_step_pu[rn] = step if step > 0 else 0.00625
            tt.last_tap_pu[rn] = dss.Transformers.Tap()
            tt.tap_op_count[rn] = 0
        return tt

    def poll(self):
        """Call after each Solve(). Returns dict reg_name -> tap ops incurred this step."""
        step_ops = {}
        for rn in self.reg_names:
            xfmr, wdg = self.transformer_of[rn]
            dss.Transformers.Name(xfmr)
            dss.Transformers.Wdg(wdg)
            cur = dss.Transformers.Tap()
            delta = cur - self.last_tap_pu[rn]
            n_ops = int(round(abs(delta) / self.tap_step_pu[rn])) if self.tap_step_pu[rn] else 0
            step_ops[rn] = n_ops
            self.tap_op_count[rn] += n_ops
            self.last_tap_pu[rn] = cur
        return step_ops


def get_bus_voltage_pu(bus_name: str) -> float:
    dss.Circuit.SetActiveBus(bus_name)
    vmag = dss.Bus.puVmagAngle()[0::2]  # [Vmag_pu, Angle, Vmag_pu, Angle, ...] per phase
    return float(np.mean(vmag)) if vmag else float("nan")


def pick_monitor_buses(feeder_key: str) -> dict:
    
    guesses = {
        "ieee13": {"regulator_adjacent": "rg60", "midpoint": "632", "end_of_feeder": "680"},
        "ieee34": {"regulator_adjacent": "814r", "midpoint": "816", "end_of_feeder": "890"},
        "ieee123": {"regulator_adjacent": "150", "midpoint": "60", "end_of_feeder": "610"},
    }
    return guesses.get(feeder_key, {})


def run_qsts(context: dict, t_hours: np.ndarray, pv_pu: np.ndarray,
             qu_deadband_pct: float, dt_s: float = 1.0,
             warmup_seconds: float = 300.0) -> dict:
    pv_names = context["pv_names"]
    reg_names = context["regcontrol_names"]
    feeder_key = context["feeder_key"]

    dss.Text.Command("Set Mode=Daily")
    dss.Text.Command(f"Set stepsize={dt_s}s")
    dss.Text.Command("Set number=1")
    dss.Text.Command("Set ControlMode=Time")
    start_hour = float(t_hours[0])
    dss.Text.Command(f"Set time=({int(start_hour)}, {(start_hour % 1) * 3600:.1f})")

    tap_tracker = TapTracker.build(reg_names) if reg_names else None
    mon_buses = pick_monitor_buses(feeder_key)
    curve_pts = build_vv_curve_points(qu_deadband_pct)
    warmup_steps = int(round(warmup_seconds / dt_s))

    def curve_q_target(v_pu: float) -> float:
        vs = [p[0] for p in curve_pts]
        qs = [p[1] for p in curve_pts]
        return float(np.interp(v_pu, vs, qs, left=qs[0], right=qs[-1]))

    records = []
    total_tap_ops = 0
    curtailment_mvarh = 0.0
    warmup_tap_ops = 0

    for i, (t_h, pv_val) in enumerate(zip(t_hours, pv_pu)):
        for pv_name in pv_names:
            dss.PVsystems.Name(pv_name)
            dss.PVsystems.Irradiance(max(pv_val, 0.0001))

        dss.Solution.Solve()

        step_ops = tap_tracker.poll() if tap_tracker else {}
        step_total_ops = sum(step_ops.values())
        in_warmup = i < warmup_steps

        if in_warmup:
            warmup_tap_ops += step_total_ops
        else:
            total_tap_ops += step_total_ops

        row = {"t_hours": t_h, "pv_pu": pv_val, "tap_ops_this_step": step_total_ops,
               "in_warmup": in_warmup}

        for label, bus in mon_buses.items():
            if bus:
                row[f"v_pu_{label}"] = get_bus_voltage_pu(bus)

        step_curtail = 0.0
        for pv_name in pv_names:
            dss.PVsystems.Name(pv_name)
            actual_kvar = dss.PVsystems.kvar()
            kva_rated = dss.PVsystems.kVARated()
            bus1 = dss.CktElement.BusNames()[0]
            v_here = get_bus_voltage_pu(bus1.split(".")[0])
            target_q_pu = curve_q_target(v_here)
            target_kvar = target_q_pu * kva_rated
            step_curtail += max(0.0, abs(target_kvar) - abs(actual_kvar))
        step_curtail_mvarh = (step_curtail / 1000.0) * (dt_s / 3600.0)
        if not in_warmup:
            curtailment_mvarh += step_curtail_mvarh

        records.append(row)

    log_df = pd.DataFrame.from_records(records)
    sim_hours_excl_warmup = float(t_hours[-1] - t_hours[0]) - (warmup_steps * dt_s / 3600.0)
    metric_log = log_df[~log_df["in_warmup"]] if "in_warmup" in log_df.columns else log_df
    v_cols = [c for c in log_df.columns if c.startswith("v_pu_")]
    ripple_by_bus = {}
    for c in v_cols:
        col = metric_log[c].dropna()
        if len(col) > 0:
            ripple_by_bus[c] = float(col.max() - col.min())
    voltage_ripple_pu = max(ripple_by_bus.values()) if ripple_by_bus else float("nan")

    return {
        "log": log_df,
        "total_tap_ops": total_tap_ops,
        "warmup_tap_ops": warmup_tap_ops,
        "tap_ops_per_regcontrol": tap_tracker.tap_op_count if tap_tracker else {},
        "curtailment_mvarh": curtailment_mvarh,
        "sim_hours": sim_hours_excl_warmup,
        "voltage_ripple_pu": voltage_ripple_pu,
        "voltage_ripple_by_bus_pu": ripple_by_bus,
    }
