"""#84 prototype round 2: fixed heading spread from the consensus start; Cauchy vs Tukey refinement."""

import runpy
from pathlib import Path

import numpy as np

P = str(Path(__file__).with_name("proto84.py"))
g = runpy.run_path(P, run_name="proto")
O = g["O"]


def arrays(tracks):
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    L = np.array([t[5] for t in tracks])
    sig2 = max(float(np.nanmedian(res)) if np.isfinite(res).any() else 25.0, 1.0)
    return C, D, n, S, L, sig2


def solve8(tracks, weight="cauchy", iters=12, cap=120.0, c=2.385, ct=4.685, q=0.5, opposite_w=0.1):
    C, D, n, S, L, sig2 = arrays(tracks)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    base = np.minimum(L, cap)
    p = g["consensus_start"](C, D, base, sig2, n, S)
    along = np.einsum("ij,ij->i", D, p - C)
    r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
    radial = -along
    kind = 1.0 if np.average(np.sign(radial), weights=base) > 0 else -1.0
    w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    # heading spread from tracks consistent with the start (angle < 45 deg, right direction)
    ang = np.arctan2(r, np.maximum(np.abs(along), 1e-6))
    excess = np.sqrt(np.maximum(ang**2 - sig2 / np.maximum(S, 1e-6), 0.0))
    ok = (ang < np.radians(45)) & (w_extra == 1.0)
    kappa = float(np.quantile(excess[ok], q) / g["_hn"](q)) if ok.sum() >= 3 else 0.35
    kappa = min(max(kappa, np.radians(3)), np.radians(45))
    w = base.copy()
    for _ in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        var = sig2 / n + along**2 * (sig2 / np.maximum(S, 1e-6) + kappa**2)
        z = r / np.sqrt(var)
        rob = 1 / (1 + (z / c) ** 2) if weight == "cauchy" else np.where(z < ct, (1 - (z / ct) ** 2) ** 2, 0.0)
        w = w_extra * base * rob
        if w.sum() <= 0:
            break
        A = np.einsum("n,nij->ij", w, proj)
        p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), np.einsum("n,nij,nj->i", w, proj, C))
    g_ = np.einsum("nij,nj->ni", proj, p - C)
    A = np.einsum("n,nij->ij", w, proj)
    B = np.einsum("n,ni,nj->ij", w**2, g_, g_)
    Ai = np.linalg.inv(A)
    neff = w.sum() ** 2 / (w**2).sum()
    cov = Ai @ B @ Ai * neff / max(neff - 2, 1.0)
    return p, cov


def run(label, kw, window=61.0):
    out = []
    for weight in ("cauchy", "tukey"):
        errs, cover = [], 0
        for s in range(10):
            ev = g["scenario"](seed=s, **kw)
            p, cov = solve8(g["lines"](ev, float(ev.t.max()), window), weight=weight)
            e = float(np.linalg.norm(p - O))
            errs.append(e)
            cover += e <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))
        out.append(f"{np.median(errs):5.1f}/{max(errs):5.1f} c{cover:<2}")
    print(f"{label:<14} {out[0]:<20}{out[1]:<20}")


if __name__ == "__main__":
    print(f"{'':<14} {'cauchy fixed-k':<20}{'tukey fixed-k':<20}")
    for n in (200, 80, 40, 20, 10):
        run(f"n={n}", {"n": n})
    run("gps 20 m", {"gps": 20.0})
    run("hide 50%", {"hide": 0.5})
    for f in (0.1, 0.25, 0.4):
        run(f"spoof {int(f * 100)}%", {"spoof_frac": f})
    run("20 s window", {}, 20.0)
    run("n=40, 20 s", {"n": 40}, 20.0)
