import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

import config
from sweep import RESULTS_CSV, pick_tuned_config


def make_heatmap(df, feeder, x_col, y_col, val_col, title, fname, x_label, y_label):
    sub = df[df["feeder"] == feeder]
    if sub.empty:
        print(f"  (no data for {feeder}, skipping {fname})")
        return

    x_vals = sorted(sub[x_col].unique())
    y_vals = sorted(sub[y_col].unique())
    grid = np.full((len(y_vals), len(x_vals)), np.nan)

    for _, row in sub.iterrows():
        xi = x_vals.index(row[x_col])
        yi = y_vals.index(row[y_col])
        grid[yi, xi] = row[val_col]

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(grid, aspect="auto", origin="lower", cmap="inferno")
    ax.set_xticks(range(len(x_vals)))
    ax.set_xticklabels(x_vals)
    ax.set_yticks(range(len(y_vals)))
    ax.set_yticklabels(y_vals)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(f"{title}\n{feeder}")

    for yi in range(len(y_vals)):
        for xi in range(len(x_vals)):
            v = grid[yi, xi]
            if not np.isnan(v):
                ax.text(xi, yi, f"{v:.1f}", ha="center", va="center",
                        color="white" if v < np.nanmax(grid) * 0.6 else "black", fontsize=8)

    fig.colorbar(im, ax=ax)
    plt.tight_layout()
    out_path = os.path.join(config.OUTPUT_DIR, fname)
    plt.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  Saved: {out_path}")


def make_combined_heatmap(df, feeders, x_col, y_col, val_col, suptitle, fname,
                           x_label, y_label, panel_labels=None, unit="", value_fmt="{:.0f}"):
    panel_labels = panel_labels or feeders
    n = len(feeders)

    # First pass: build each feeder's grid and find the shared vmin/vmax.
    grids = {}
    all_x_vals, all_y_vals = None, None
    global_min, global_max = np.inf, -np.inf
    for feeder in feeders:
        sub = df[df["feeder"] == feeder]
        if sub.empty:
            grids[feeder] = None
            continue
        x_vals = sorted(sub[x_col].unique())
        y_vals = sorted(sub[y_col].unique())
        if all_x_vals is None:
            all_x_vals, all_y_vals = x_vals, y_vals
        grid = np.full((len(y_vals), len(x_vals)), np.nan)
        for _, row in sub.iterrows():
            xi = x_vals.index(row[x_col])
            yi = y_vals.index(row[y_col])
            grid[yi, xi] = row[val_col]
        grids[feeder] = grid
        if np.nanmin(grid) < global_min:
            global_min = np.nanmin(grid)
        if np.nanmax(grid) > global_max:
            global_max = np.nanmax(grid)

    fig, axes = plt.subplots(1, n, figsize=(5.5 * n, 5), sharey=True)
    if n == 1:
        axes = [axes]

    im = None
    for ax, feeder, label in zip(axes, feeders, panel_labels):
        grid = grids[feeder]
        if grid is None:
            ax.set_title(f"{label}\n(no data)")
            continue
        im = ax.imshow(grid, aspect="auto", origin="lower", cmap="inferno",
                        vmin=global_min, vmax=global_max)
        ax.set_xticks(range(len(all_x_vals)))
        ax.set_xticklabels(all_x_vals)
        ax.set_yticks(range(len(all_y_vals)))
        ax.set_yticklabels(all_y_vals)
        ax.set_xlabel(x_label)
        ax.set_title(label)
        for yi in range(len(all_y_vals)):
            for xi in range(len(all_x_vals)):
                v = grid[yi, xi]
                if not np.isnan(v):
                    color = "white" if v < global_min + (global_max - global_min) * 0.6 else "black"
                    ax.text(xi, yi, value_fmt.format(v), ha="center", va="center", color=color, fontsize=7)
    axes[0].set_ylabel(y_label)

    if im is not None:
        cbar = fig.colorbar(im, ax=axes, shrink=0.85, label=f"{val_col.replace('_', ' ')}{unit}")

    fig.suptitle(suptitle, fontsize=12)
    out_path = os.path.join(config.OUTPUT_DIR, fname)
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved combined figure: {out_path}")


def make_tradeoff_scatter(df, feeders, panel_labels, fname):
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = {"ieee13": "tab:blue", "ieee123": "tab:orange", "ieee34": "tab:green"}
    markers = {"ieee13": "o", "ieee123": "s", "ieee34": "^"}

    for feeder, label in zip(feeders, panel_labels):
        sub = df[df["feeder"] == feeder]
        if sub.empty:
            continue
        ax.scatter(sub["taps_per_day"], sub["curtailment_mvarh"],
                   c=colors.get(feeder, "gray"), marker=markers.get(feeder, "o"),
                   label=label, alpha=0.75, s=55, edgecolors="black", linewidths=0.4)

    ax.set_xlabel("Taps/day")
    ax.set_ylabel("Q(U) curtailment (Mvar-h)")
    ax.set_title("Figure 5: Trade-off between OLTC tap suppression and PV reactive power curtailment")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()

    out_path = os.path.join(config.OUTPUT_DIR, fname)
    plt.savefig(out_path, dpi=200)
    plt.close(fig)
    print(f"  Saved: {out_path}")
def main():
    if not os.path.exists(RESULTS_CSV):
        print(f"No results file found at {RESULTS_CSV}. Run sweep.py first.")
        return

    df = pd.read_csv(RESULTS_CSV)
    feeders = sorted(df["feeder"].unique())
    print(f"Loaded {len(df)} rows across feeders: {feeders}")

    baseline = config.BASELINE
    xr_ordered_feeders = [f for f in ["ieee13", "ieee123", "ieee34"] if f in feeders]
    xr_panel_labels = ["High X/R (IEEE13)", "Medium X/R (IEEE123)", "Low X/R (IEEE34)"]
    xr_panel_labels = [xr_panel_labels[["ieee13", "ieee123", "ieee34"].index(f)] for f in xr_ordered_feeders]

    print("\nGenerating Figure 1 (taps/day, Q(U) grid, combined panel, shared color scale)...")
    qu_grid_all = df[
        (df["oltc_deadband_pct"] == baseline["oltc_ldc_deadband_pct"])
        & (df["oltc_delay_s"] == baseline["oltc_delay_s"])
    ]
    make_combined_heatmap(qu_grid_all, xr_ordered_feeders, "qu_deadband_pct", "qu_response_tau_s",
                           "taps_per_day",
                           "Figure 1: Daily OLTC tap operations vs Q(U) dead-band and response-time constant",
                           "fig1_taps_heatmap_combined.png",
                           "Q(U) dead-band (%)", "Q(U) response tau (s)",
                           panel_labels=xr_panel_labels, unit=" (taps/day)")

    print("\nGenerating Figure 2 (curtailment, Q(U) grid, combined panel, shared color scale)...")
    make_combined_heatmap(qu_grid_all, xr_ordered_feeders, "qu_deadband_pct", "qu_response_tau_s",
                           "curtailment_mvarh",
                           "Figure 2: Q(U) curtailment vs dead-band and response-time constant",
                           "fig2_curtailment_heatmap_combined.png",
                           "Q(U) dead-band (%)", "Q(U) response tau (s)",
                           panel_labels=xr_panel_labels, unit=" (Mvar-h)", value_fmt="{:.3f}")

    print("\nGenerating Figure 3 (taps/day, OLTC grid, combined panel -- diagnostic)...")
    oltc_grid_all = df[
        (df["qu_deadband_pct"] == baseline["qu_deadband_pct"])
        & (df["qu_response_tau_s"] == baseline["qu_response_tau_s"])
    ]
    make_combined_heatmap(oltc_grid_all, xr_ordered_feeders, "oltc_deadband_pct", "oltc_delay_s",
                           "taps_per_day",
                           "Figure 3: Daily OLTC tap operations vs OLTC dead-band and delay (Q(U) at baseline)",
                           "fig3_oltc_taps_heatmap_combined.png",
                           "OLTC dead-band (%)", "OLTC delay (s)",
                           panel_labels=xr_panel_labels, unit=" (taps/day)")

    print("\nGenerating Figure 5 (taps/day vs curtailment trade-off, all swept configs)...")
    make_tradeoff_scatter(df, xr_ordered_feeders, xr_panel_labels, "fig5_tradeoff_scatter.png")

    print("\n=== Tuned config per feeder ===")
    print("(Table 1's 'tuned' column uses Q(U), since OLTC "
          "band/delay are typically fixed by equipment/utility standards, not "
          "something remotely re-tunable like inverter Q(U) settings. The "
          "global-best row is shown too, for a Discussion point about how much "
          "MORE could be gained if OLTC settings were also adjustable.)")
    for feeder in feeders:
        baseline_row = df[
            (df["feeder"] == feeder)
            & (df["qu_deadband_pct"] == baseline["qu_deadband_pct"])
            & (df["qu_response_tau_s"] == baseline["qu_response_tau_s"])
            & (df["oltc_deadband_pct"] == baseline["oltc_ldc_deadband_pct"])
            & (df["oltc_delay_s"] == baseline["oltc_delay_s"])
        ]
        base_taps = baseline_row["taps_per_day"].iloc[0] if not baseline_row.empty else float("nan")

        qu_only = df[
            (df["feeder"] == feeder)
            & (df["oltc_deadband_pct"] == baseline["oltc_ldc_deadband_pct"])
            & (df["oltc_delay_s"] == baseline["oltc_delay_s"])
        ].sort_values(["taps_per_day", "curtailment_mvarh"])
        qu_best = qu_only.iloc[0] if not qu_only.empty else None

        global_tuned = pick_tuned_config(df, feeder)

        print(f"\n  {feeder}:")
        print(f"    Baseline:        {base_taps:.1f} taps/day")
        if qu_best is not None:
            reduction = (1 - qu_best['taps_per_day']/base_taps)*100 if base_taps else float("nan")
            ripple = qu_best.get('voltage_ripple_pu', float('nan'))
            print(f"    Q(U)-only tuned: db={qu_best['qu_deadband_pct']}% tau={qu_best['qu_response_tau_s']}s "
                  f"-> {qu_best['taps_per_day']:.1f} taps/day ({reduction:.0f}% reduction), "
                  f"curtailment={qu_best['curtailment_mvarh']:.3f} Mvar-h, "
                  f"ripple={ripple:.4f} pu "
                  f"[USE THIS ROW FOR TABLE 1]")
        print(f"    Global best:     qu_db={global_tuned['qu_deadband_pct']}% qu_tau={global_tuned['qu_response_tau_s']}s "
              f"oltc_db={global_tuned['oltc_deadband_pct']}% oltc_delay={global_tuned['oltc_delay_s']}s "
              f"-> {global_tuned['taps_per_day']:.1f} taps/day "
              f"[{global_tuned['lever']}, for Discussion only]")


if __name__ == "__main__":
    main()
