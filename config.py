import os

FEEDER_PATHS = {
    "ieee13": r"C:\Users\Manu Sri\OneDrive\Desktop\apps\Opendss\electricdss-tst-master\electricdss-tst-master\Version8\Distrib\IEEETestCases\13Bus\IEEE13Nodeckt.dss",
    "ieee34": r"C:\Users\Manu Sri\OneDrive\Desktop\apps\Opendss\electricdss-tst-master\electricdss-tst-master\Version8\Distrib\IEEETestCases\34Bus\ieee34Mod1.dss",
    "ieee123": r"C:\Users\Manu Sri\OneDrive\Desktop\apps\Opendss\electricdss-tst-master\electricdss-tst-master\Version8\Distrib\IEEETestCases\123Bus\IEEE123Master.dss",
}

#Output directory for logs/figures
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

#Simulation timing
SIM_START_HOUR = 8.0       # start of simulated window (hours, 0-24)
SIM_END_HOUR = 18.0        # end of simulated window
TIMESTEP_SECONDS = 1       # QSTS resolution; must be 1s per paper Sec 2.2 (sub-minute dynamics)

#Baseline (default / untuned) parameters used unless swept 
BASELINE = {
    "qu_deadband_pct": 0.0,          # Q(U) dead-band width, % of nominal V (0, 1, 2, 3 per Table)
    "qu_response_tau_s": 1.0,        # Q(U) response-time constant (LPF tau), seconds (1,10,30,60 per Table)
    "oltc_ldc_deadband_pct": 1.0,    # RegControl band, % (1, 1.5, 2 per Table)
    "oltc_delay_s": 45.0,            # RegControl delay, seconds (30,45,60,90 per Table)
}

#Sweep grids (Table: "Independent variables")
QU_DEADBAND_LEVELS_PCT = [0.0, 1.0, 2.0, 3.0]
QU_RESPONSE_TAU_LEVELS_S = [1.0, 10.0, 30.0, 60.0]
OLTC_DEADBAND_LEVELS_PCT = [1.0, 1.5, 2.0]
OLTC_DELAY_LEVELS_S = [30.0, 45.0, 60.0, 90.0]

# PV fleet
PV_PENETRATION_FRACTION = 0.6   
PV_TO_LOAD_KW_RATIO_RANGE = (0.5, 1.5)
# fraction of load buses that get a residential PV system
# PV size is now set RELATIVE to each load's own kW (not a fixed absolute kW),
# since IEEE13/34/123 "loads" are lumped aggregates of many customers, not
# single houses. A fixed 3-7kW PV per load point gives <2% feeder-wide
# penetration on IEEE13 (total load ~3800kW) -- nowhere near "high PV" and
# too small to move feeder voltage enough to ever trigger OLTC response.
# (0.5, 1.5) = each PV sized between 50% and 150% of its co-located load's kW.
