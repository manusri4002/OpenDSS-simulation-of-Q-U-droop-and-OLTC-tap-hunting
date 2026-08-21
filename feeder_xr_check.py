import opendssdirect as dss
from config import FEEDER_PATHS


def compute_feeder_xr(master_path: str) -> dict:
    dss.Text.Command("Clear")
    dss.Text.Command(f'Compile "{master_path}"')
    dss.Text.Command("Solve")

    total_wR = 0.0   # length-weighted resistance
    total_wX = 0.0   # length-weighted reactance
    total_len = 0.0
    per_line = []

    line = dss.Lines.First()
    while line:
        length = dss.Lines.Length()
        nph = dss.Lines.Phases()

        # IMPORTANT: R1()/X1() return the DSS *default* line impedance
        # (0.058 / 0.1206 ohm per unit length) whenever a line's impedance
        # comes from a LineGeometry/LineSpacing/WireData object rather than
        # a plain LineCode with explicit R1/X1 -- which is exactly how
        # IEEE13/34/123 define most of their lines. That default gives a
        # constant X/R = 2.079 regardless of the actual conductor/spacing,
        # which is why the first version of this script returned the same
        # number for every feeder. Use the solved RMatrix/XMatrix instead,
        # which reflects the real computed self/mutual impedances.
        rmat = dss.Lines.RMatrix()   # flattened nph x nph, ohms per unit length
        xmat = dss.Lines.XMatrix()

        if length > 0 and nph > 0 and len(rmat) == nph * nph:
            # average of the diagonal (self) terms as a representative R, X
            r_avg = sum(rmat[i * nph + i] for i in range(nph)) / nph
            x_avg = sum(xmat[i * nph + i] for i in range(nph)) / nph
            total_wR += r_avg * length
            total_wX += x_avg * length
            total_len += length
            per_line.append((dss.Lines.Name(), r_avg, x_avg, length,
                              (x_avg / r_avg) if r_avg != 0 else float("inf")))
        line = dss.Lines.Next()

    avg_xr = (total_wX / total_wR) if total_wR != 0 else float("nan")

    return {
        "avg_xr_length_weighted": avg_xr,
        "total_length": total_len,
        "num_lines": len(per_line),
        "per_line": per_line,
    }


if __name__ == "__main__":
    results = {}
    for name, path in FEEDER_PATHS.items():
        print(f"\n=== {name} ===")
        try:
            r = compute_feeder_xr(path)
            results[name] = r["avg_xr_length_weighted"]
            print(f"  Length-weighted avg X/R: {r['avg_xr_length_weighted']:.3f}")
            print(f"  Lines counted: {r['num_lines']}, total length: {r['total_length']:.1f}")
        except Exception as e:
            print(f"  FAILED to load/compute: {e}")
            print("  -> check FEEDER_PATHS in config.py points at the correct .dss master file")

    if results:
        ranked = sorted(results.items(), key=lambda kv: kv[1])
        print("\n=== Ranked Low -> High X/R ===")
        for name, xr in ranked:
            print(f"  {name}: {xr:.3f}")
        print("\nAssign your paper's Low/Medium/High X/R classes using this ranking, "
              "not bus count. Report these exact avg-X/R numbers in Section 2.2.")
