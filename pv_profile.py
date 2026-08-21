import numpy as np


def clear_sky_pu(t_hours: np.ndarray, sunrise=6.0, sunset=19.0) -> np.ndarray:
    """Smooth clear-sky bell curve, 0 outside [sunrise, sunset], peak=1.0 pu at solar noon."""
    pu = np.zeros_like(t_hours)
    daylight = (t_hours > sunrise) & (t_hours < sunset)
    span = sunset - sunrise
    theta = np.pi * (t_hours[daylight] - sunrise) / span
    pu[daylight] = np.sin(theta) ** 1.2  # slightly peaked, avoids a too-flat top
    return pu


def generate_cloud_multiplier(
    n_steps: int,
    dt_s: float = 1.0,
    mean_clear_sojourn_s: float = 240.0,
    mean_cloud_sojourn_s: float = 60.0,
    cloud_attenuation_range=(0.15, 0.6),  # fraction of clear-sky remaining under cloud
    ramp_time_s_range=(1.0, 8.0),
    seed: int | None = None,
) -> np.ndarray:
    """
    2-state Markov (clear/cloud) shading multiplier in [0,1], with randomized
    sojourn durations and finite ramp transitions between states (cloud edges).
    """
    rng = np.random.default_rng(seed)
    mult = np.ones(n_steps)

    state = "clear"
    target = 1.0
    current = 1.0
    steps_in_state = 0
    steps_until_transition = int(rng.exponential(mean_clear_sojourn_s / dt_s))
    ramp_steps_left = 0
    ramp_start_val = 1.0
    ramp_target_val = 1.0
    ramp_total_steps = 1

    for i in range(n_steps):
        if ramp_steps_left > 0:
            frac = 1.0 - (ramp_steps_left / ramp_total_steps)
            current = ramp_start_val + frac * (ramp_target_val - ramp_start_val)
            ramp_steps_left -= 1
        else:
            current = ramp_target_val if ramp_total_steps > 0 else current

        mult[i] = current
        steps_in_state += 1

        if steps_in_state >= steps_until_transition and ramp_steps_left == 0:
            # flip state
            if state == "clear":
                state = "cloud"
                ramp_target_val = rng.uniform(*cloud_attenuation_range)
                steps_until_transition = int(rng.exponential(mean_cloud_sojourn_s / dt_s))
            else:
                state = "clear"
                ramp_target_val = 1.0
                steps_until_transition = int(rng.exponential(mean_clear_sojourn_s / dt_s))

            ramp_start_val = current
            ramp_dur_s = rng.uniform(*ramp_time_s_range)
            ramp_total_steps = max(1, int(ramp_dur_s / dt_s))
            ramp_steps_left = ramp_total_steps
            steps_in_state = 0

    return np.clip(mult, 0.0, 1.0)


def generate_pv_pu_profile(
    start_hour: float,
    end_hour: float,
    dt_s: float = 1.0,
    seed: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (t_hours, pv_pu) at dt_s resolution over [start_hour, end_hour).
    pv_pu is per-unit of each PVSystem's Pmpp (0..1), suitable for driving
    a LoadShape or writing directly into PVSystem.irradiance each step.
    """
    n_steps = int(round((end_hour - start_hour) * 3600.0 / dt_s))
    t_hours = start_hour + np.arange(n_steps) * (dt_s / 3600.0)

    clear = clear_sky_pu(t_hours)
    cloud_mult = generate_cloud_multiplier(n_steps, dt_s=dt_s, seed=seed)

    pv_pu = clear * cloud_mult
    return t_hours, np.clip(pv_pu, 0.0, 1.0)


if __name__ == "__main__":
    # quick sanity plot if run standalone
    import matplotlib.pyplot as plt

    t, pv = generate_pv_pu_profile(8.0, 18.0, dt_s=1.0, seed=42)
    plt.figure(figsize=(10, 3))
    plt.plot(t, pv, lw=0.6)
    plt.xlabel("Hour of day")
    plt.ylabel("PV output (pu of Pmpp)")
    plt.title("Synthetic 1-sec PV profile with cloud-edge transients")
    plt.tight_layout()
    plt.savefig("pv_profile_sanity_check.png", dpi=150)
    print("Saved pv_profile_sanity_check.png -- inspect it before trusting downstream results.")