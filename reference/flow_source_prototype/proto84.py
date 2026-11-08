"""#84/#102 prototype (seat b), kept for reference: how the consensus estimator was found.
Run from backend/:  python ../reference/flow_source_prototype/proto84.py

It benchmarks against the flow_source operator as it was before #93, so absolute numbers differ from
tests/bench_flow_source.py; the production version is flow_source.consensus_intersection."""

import sys

sys.path[:0] = [".", "tests"]
import numpy as np  # noqa: E402
from synthetic import FixedClock, LocalGrid, events_from_xy  # noqa: E402

from app.engine.operators.flow_source import FlowSourceParams, _sorted_tracks, flow_source  # noqa: E402

GRID = LocalGrid(cell_m=25.0, h=24, w=24)  # 600 m
O = np.array([300.0, 300.0])


def scenario(n=200, hide=0.2, gps=8.0, dur=60, dt=5, spoof_frac=0.0, seed=0, decoy=(150.0, 450.0)):
    rng = np.random.default_rng(seed)
    rows = []
    start = O + rng.uniform(-150, 150, size=(n, 2))
    for i, s in enumerate(start):
        rel = s - O
        r = np.linalg.norm(rel)
        if r < 150 and rng.random() > hide:
            ang = np.arctan2(rel[1], rel[0]) + np.radians(rng.uniform(-30, 30))
            v = rng.uniform(2, 4) * np.array([np.cos(ang), np.sin(ang)])
        elif r < 150:
            v = np.zeros(2)
        else:
            ang = rng.uniform(0, 2 * np.pi)
            v = 1.2 * np.array([np.cos(ang), np.sin(ang)])
        for t in range(0, dur + 1, dt):
            p = s + v * t + rng.normal(0, gps, 2)
            rows.append((f"u{i}", float(t), *p))
    n_sp = int(round(spoof_frac * n))
    d = np.array(decoy)
    for j in range(n_sp):  # coordinated fakes "fleeing" a decoy, with realistic noise
        ang = rng.uniform(0, 2 * np.pi)
        s = d + rng.uniform(5, 40) * np.array([np.cos(ang), np.sin(ang)])
        v = rng.uniform(2, 4) * np.array([np.cos(ang), np.sin(ang)])
        for t in range(0, dur + 1, dt):
            rows.append((f"f{j}", float(t), *(s + v * t + rng.normal(0, gps, 2))))
    return events_from_xy(GRID, rows)


def lines(ev, now, window, min_move=20.0):
    sel = ev.select((ev.t > now - window) & (ev.t <= now) & (ev.user_id != ""))
    order, uidx = _sorted_tracks(sel.user_id, sel.t)
    x, y = GRID.to_xy(sel.lat[order], sel.lon[order])
    t = sel.t[order]
    out = []
    for u in np.unique(uidx):
        m = uidx == u
        if m.sum() < 2:
            continue
        P = np.c_[x[m], y[m]]
        tt = t[m]
        c = P.mean(0)
        Q = P - c
        _, _, vt = np.linalg.svd(Q, full_matrices=False)
        d = vt[0]
        proj = Q @ d
        if np.corrcoef(tt, proj)[0, 1] < 0 if len(tt) > 2 else proj[-1] < proj[0]:
            d, proj = -d, -proj
        L = proj.max() - proj.min()
        if L < min_move:
            continue
        perp = Q @ np.array([-d[1], d[0]])
        n = len(proj)
        S = float(((proj - proj.mean()) ** 2).sum())
        res = float((perp**2).sum() / max(n - 2, 1)) if n > 2 else np.nan
        out.append((c, d, n, S, res, L, P[0]))
    return out


def solve(tracks, iters=10, opposite_w=0.1):
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    sig2 = np.nanmedian(res) if np.isfinite(res).any() else 25.0
    sig2 = max(sig2, 1.0)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    p = C.mean(0)
    w_extra = np.ones(len(C))
    for it in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)  # distance from centroid to foot point along the line
        var = sig2 / n + along**2 * sig2 / np.maximum(S, 1e-6)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        z = r / np.sqrt(var)
        w = w_extra / var / (1 + (z / 2.385) ** 2)
        A = np.einsum("n,nij->ij", w, proj)
        b = np.einsum("n,nij,nj->i", w, proj, C)
        p = np.linalg.solve(A + 1e-12 * np.eye(2), b)
        if it == 3:  # direction consistency: decide kind, downweight opposing tracks
            radial = np.einsum("ij,ij->i", D, C - p)
            kind = 1.0 if np.average(np.sign(radial), weights=w) > 0 else -1.0
            w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    along = np.einsum("ij,ij->i", D, p - C)
    var = sig2 / n + along**2 * sig2 / np.maximum(S, 1e-6)
    r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
    z = r / np.sqrt(var)
    w = w_extra / var / (1 + (z / 2.385) ** 2)
    A = np.einsum("n,nij->ij", w, proj)
    s2 = float((w * r**2).sum() / max(w_extra.sum() - 2, 1)) * float(w.sum() and 1)
    cov = np.linalg.inv(A) * max(s2 / max((w * var).sum() / len(w), 1e-9), 1.0)
    return p, cov


def run(label, kw, window=61.0, seeds=range(10)):
    e_old, e_new, e_unc, cover = [], [], [], 0
    for s in seeds:
        ev = scenario(seed=s, **kw)
        now = float(ev.t.max())
        r = flow_source(ev, FlowSourceParams(window_s=window), None, FixedClock(now), GRID)
        if r.extras["point"] is None:
            e_old.append(np.nan)
        else:
            e_old.append(GRID.dist_m(r.extras["point"], O))
        tr = lines(ev, now, window)
        p, cov = solve(tr)
        err = float(np.linalg.norm(p - O))
        e_new.append(err)
        unc = float(np.sqrt(np.trace(cov)))
        e_unc.append(unc)
        cover += err <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))  # ~95% radius
    print(
        f"{label:<32} old {np.nanmedian(e_old):6.1f} (max {np.nanmax(e_old):6.1f})"
        f"  new {np.median(e_new):6.1f} (max {max(e_new):6.1f})  unc {np.median(e_unc):5.1f}  cover95 {cover}/{len(e_new)}"
    )


if __name__ == "__main__":
    for n in (200, 80, 40, 20, 10):
        run(f"n={n}", {"n": n})
    run("gps 20 m", {"gps": 20.0})
    run("hide 50%", {"hide": 0.5})
    for f in (0.1, 0.25, 0.4):
        run(f"spoof {int(f * 100)}%", {"spoof_frac": f})
    run("default 20 s window", {}, window=20.0)
    run("n=40, 20 s window", {"n": 40}, window=20.0)


def solve2(tracks, mode="hetero", iters=12, opposite_w=0.1, cap=120.0):
    """mode: 'len' = weight by min(L, cap) (reference); 'hetero' = 1/var with along-track growth.
    Robust: Cauchy on residuals scaled by an adaptive MAD scale. Sandwich covariance."""
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    L = np.array([t[5] for t in tracks])
    sig2 = max(float(np.nanmedian(res)) if np.isfinite(res).any() else 25.0, 1.0)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    base = np.minimum(L, cap)
    p = np.average(C, axis=0, weights=base)
    w_extra = np.ones(len(C))
    w = base.copy()
    for it in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        if mode == "hetero":
            var = sig2 / n + along**2 * sig2 / np.maximum(S, 1e-6)
            z = r / np.sqrt(var)
            prior = 1.0 / var
        else:
            prior = base
            z = r
        scale = 1.4826 * np.median(np.abs(z)) + 1e-9
        w = w_extra * prior / (1 + (z / (2.385 * scale)) ** 2)
        A = np.einsum("n,nij->ij", w, proj)
        b = np.einsum("n,nij,nj->i", w, proj, C)
        p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), b)
        if it == 3:
            radial = np.einsum("ij,ij->i", D, C - p)
            kind = 1.0 if np.average(np.sign(radial), weights=w) > 0 else -1.0
            w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    # sandwich covariance: A^-1 (sum w^2 g g^T) A^-1, g = projected residual vector
    g = np.einsum("nij,nj->ni", proj, p - C)
    A = np.einsum("n,nij->ij", w, proj)
    B = np.einsum("n,ni,nj->ij", w**2, g, g)
    Ai = np.linalg.inv(A)
    neff = w.sum() ** 2 / (w**2).sum()
    cov = Ai @ B @ Ai * neff / max(neff - 2, 1.0)
    return p, cov


def run2(label, kw, window=61.0, seeds=range(10)):
    rows = {}
    for mode in ("len", "hetero"):
        errs, cover = [], 0
        for s in seeds:
            ev = scenario(seed=s, **kw)
            now = float(ev.t.max())
            p, cov = solve2(lines(ev, now, window), mode=mode)
            err = float(np.linalg.norm(p - O))
            errs.append(err)
            cover += err <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))
        rows[mode] = (np.median(errs), max(errs), cover)
    old = []
    for s in seeds:
        ev = scenario(seed=s, **kw)
        r = flow_source(ev, FlowSourceParams(window_s=window), None, FixedClock(float(ev.t.max())), GRID)
        old.append(GRID.dist_m(r.extras["point"], O) if r.extras["point"] else np.nan)
    print(
        f"{label:<22} old {np.nanmedian(old):5.1f}/{np.nanmax(old):5.1f} | "
        + " | ".join(f"{m} {a:5.1f}/{b:5.1f} cov{c}" for m, (a, b, c) in rows.items())
    )


def main2():
    for n in (200, 80, 40, 20, 10):
        run2(f"n={n}", {"n": n})
    run2("gps 20 m", {"gps": 20.0})
    run2("hide 50%", {"hide": 0.5})
    for f in (0.1, 0.25, 0.4):
        run2(f"spoof {int(f * 100)}%", {"spoof_frac": f})
    run2("20 s window", {}, window=20.0)
    run2("n=40, 20 s window", {"n": 40}, window=20.0)


def solve3(tracks, iters=12, opposite_w=0.1, cap=120.0, c=2.385, warm=2):
    """hybrid: prior = min(L, cap); robust Cauchy on z = r / physical sigma(along), fixed c."""
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    L = np.array([t[5] for t in tracks])
    sig2 = max(float(np.nanmedian(res)) if np.isfinite(res).any() else 25.0, 1.0)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    base = np.minimum(L, cap)
    w_extra = np.ones(len(C))
    w = base.copy()
    A = np.einsum("n,nij->ij", w, proj)
    p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), np.einsum("n,nij,nj->i", w, proj, C))
    for it in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        sd = np.sqrt(sig2 / n + along**2 * sig2 / np.maximum(S, 1e-6))
        z = r / sd
        w = w_extra * base / (1 + (z / c) ** 2)
        A = np.einsum("n,nij->ij", w, proj)
        b = np.einsum("n,nij,nj->i", w, proj, C)
        p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), b)
        if it == warm:
            radial = np.einsum("ij,ij->i", D, C - p)
            kind = 1.0 if np.average(np.sign(radial), weights=w) > 0 else -1.0
            w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    g = np.einsum("nij,nj->ni", proj, p - C)
    A = np.einsum("n,nij->ij", w, proj)
    B = np.einsum("n,ni,nj->ij", w**2, g, g)
    Ai = np.linalg.inv(A)
    neff = w.sum() ** 2 / (w**2).sum()
    cov = Ai @ B @ Ai * neff / max(neff - 2, 1.0)
    return p, cov


def run3(label, kw, window=61.0, seeds=range(10)):
    errs, cover = [], 0
    for s in seeds:
        ev = scenario(seed=s, **kw)
        p, cov = solve3(lines(ev, float(ev.t.max()), window))
        err = float(np.linalg.norm(p - O))
        errs.append(err)
        cover += err <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))
    print(f"{label:<22} hybrid {np.median(errs):5.1f}/{max(errs):5.1f} cov{cover}")


def main3():
    for n in (200, 80, 40, 20, 10):
        run3(f"n={n}", {"n": n})
    run3("gps 20 m", {"gps": 20.0})
    run3("hide 50%", {"hide": 0.5})
    for f in (0.1, 0.25, 0.4):
        run3(f"spoof {int(f * 100)}%", {"spoof_frac": f})
    run3("20 s window", {}, window=20.0)
    run3("n=40, 20 s window", {"n": 40}, window=20.0)




def _hn(qv):
    # inverse CDF of |N(0,1)| at q via bisection (no scipy)
    lo, hi = 0.0, 10.0
    from math import erf, sqrt

    for _ in range(60):
        mid = (lo + hi) / 2
        if erf(mid / sqrt(2)) < qv:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def solve5(tracks, iters=12, opposite_w=0.1, cap=120.0, c=2.385, warm=2, q=0.3, prior="len"):
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    L = np.array([t[5] for t in tracks])
    sig2 = max(float(np.nanmedian(res)) if np.isfinite(res).any() else 25.0, 1.0)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    base = np.minimum(L, cap)
    zq = _hn(q)
    w_extra = np.ones(len(C))
    w = base.copy()
    A = np.einsum("n,nij->ij", w, proj)
    p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), np.einsum("n,nij,nj->i", w, proj, C))
    kappa2 = 0.0
    for it in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        ang = np.arctan2(r, np.maximum(np.abs(along), 1e-6))
        ang_noise2 = sig2 / np.maximum(S, 1e-6)
        excess = np.maximum(ang**2 - ang_noise2, 0.0)
        kappa2 = (np.quantile(np.sqrt(excess), q) / zq) ** 2
        var = sig2 / n + along**2 * (sig2 / np.maximum(S, 1e-6) + kappa2)
        z = r / np.sqrt(var)
        pr = base if prior == "len" else 1.0 / var
        w = w_extra * pr / (1 + (z / c) ** 2)
        A = np.einsum("n,nij->ij", w, proj)
        b = np.einsum("n,nij,nj->i", w, proj, C)
        p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), b)
        if it == warm:
            radial = np.einsum("ij,ij->i", D, C - p)
            kind = 1.0 if np.average(np.sign(radial), weights=w) > 0 else -1.0
            w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    g = np.einsum("nij,nj->ni", proj, p - C)
    A = np.einsum("n,nij->ij", w, proj)
    B = np.einsum("n,ni,nj->ij", w**2, g, g)
    Ai = np.linalg.inv(A)
    neff = w.sum() ** 2 / (w**2).sum()
    cov = Ai @ B @ Ai * neff / max(neff - 2, 1.0)
    return p, cov, float(np.degrees(np.sqrt(kappa2)))


def run5(label, kw, window=61.0, seeds=range(10), **solver):
    errs, cover, kap = [], 0, []
    for s in seeds:
        ev = scenario(seed=s, **kw)
        p, cov, k = solve5(lines(ev, float(ev.t.max()), window), **solver)
        err = float(np.linalg.norm(p - O))
        errs.append(err)
        kap.append(k)
        cover += err <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))
    return f"{np.median(errs):5.1f}/{max(errs):5.1f} c{cover:<2}"


def main5():
    cases = [(f"n={n}", {"n": n}, 61.0) for n in (200, 80, 40, 20, 10)]
    cases += [("gps 20 m", {"gps": 20.0}, 61.0), ("hide 50%", {"hide": 0.5}, 61.0)]
    cases += [(f"spoof {int(f * 100)}%", {"spoof_frac": f}, 61.0) for f in (0.1, 0.25, 0.4)]
    cases += [("20 s window", {}, 20.0), ("n=40, 20 s", {"n": 40}, 20.0)]
    print(f"{'':<14} {'len q.3':<20}{'len q.5':<20}{'inv q.3':<20}")
    for label, kw, win in cases:
        a = run5(label, kw, win, q=0.3, prior="len")
        b = run5(label, kw, win, q=0.5, prior="len")
        cc = run5(label, kw, win, q=0.3, prior="inv")
        print(f"{label:<14} {a:<20}{b:<20}{cc:<20}")


def consensus_start(C, D, base, sig2, n, S, kappa0=0.35, c=2.385, m=60, radial_check=True):
    """Best intersection of track-line pairs by robust agreement (RANSAC-style, deterministic)."""
    idx = np.argsort(-base)[:m]
    cand = [np.average(C, axis=0, weights=base)]
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            M = np.c_[D[i], -D[j]]
            if abs(np.linalg.det(M)) < 0.17:  # < ~10 degrees apart: unstable intersection
                continue
            s = np.linalg.solve(M, C[j] - C[i])
            cand.append(C[i] + s[0] * D[i])
    cand = np.array(cand)  # [K, 2]
    diff = cand[:, None, :] - C[None, :, :]  # [K, N, 2]
    along = np.einsum("knj,nj->kn", diff, D)
    perp = diff - along[..., None] * D[None]
    r = np.linalg.norm(perp, axis=2)
    sd = np.sqrt(sig2 / n + along**2 * (sig2 / np.maximum(S, 1e-6) + kappa0**2))
    score_n = base / (1 + (r / sd / c) ** 2)
    if radial_check:
        radial = -along  # D·(C - p) = -along
        src = (score_n * (radial > 0)).sum(1)
        snk = (score_n * (radial < 0)).sum(1)
        score = np.maximum(src, snk)
    else:
        score = score_n.sum(1)
    return cand[int(np.argmax(score))]


def solve6(tracks, iters=12, opposite_w=0.1, cap=120.0, c=2.385, q=0.3):
    C = np.array([t[0] for t in tracks])
    D = np.array([t[1] for t in tracks])
    n = np.array([t[2] for t in tracks], float)
    S = np.array([t[3] for t in tracks])
    res = np.array([t[4] for t in tracks])
    L = np.array([t[5] for t in tracks])
    sig2 = max(float(np.nanmedian(res)) if np.isfinite(res).any() else 25.0, 1.0)
    proj = np.eye(2)[None] - D[:, :, None] * D[:, None, :]
    base = np.minimum(L, cap)
    zq = _hn(q)
    p = consensus_start(C, D, base, sig2, n, S)
    radial = np.einsum("ij,ij->i", D, C - p)
    w0 = base / (1 + 0 * radial)
    kind = 1.0 if np.average(np.sign(radial), weights=w0) > 0 else -1.0
    w_extra = np.where(np.sign(radial) == kind, 1.0, opposite_w)
    w = base.copy()
    for _ in range(iters):
        along = np.einsum("ij,ij->i", D, p - C)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - C), axis=1)
        ang = np.arctan2(r, np.maximum(np.abs(along), 1e-6))
        excess = np.maximum(ang**2 - sig2 / np.maximum(S, 1e-6), 0.0)
        kappa2 = (np.quantile(np.sqrt(excess[w_extra == 1.0]) if (w_extra == 1).any() else np.sqrt(excess), q) / zq) ** 2
        var = sig2 / n + along**2 * (sig2 / np.maximum(S, 1e-6) + kappa2)
        z = r / np.sqrt(var)
        w = w_extra * base / (1 + (z / c) ** 2)
        A = np.einsum("n,nij->ij", w, proj)
        p = np.linalg.solve(A + 1e-9 * np.trace(A) * np.eye(2), np.einsum("n,nij,nj->i", w, proj, C))
    g = np.einsum("nij,nj->ni", proj, p - C)
    A = np.einsum("n,nij->ij", w, proj)
    B = np.einsum("n,ni,nj->ij", w**2, g, g)
    Ai = np.linalg.inv(A)
    neff = w.sum() ** 2 / (w**2).sum()
    cov = Ai @ B @ Ai * neff / max(neff - 2, 1.0)
    return p, cov


def run6(label, kw, window=61.0, seeds=range(10)):
    errs, cover, old = [], 0, []
    for s in seeds:
        ev = scenario(seed=s, **kw)
        now = float(ev.t.max())
        p, cov = solve6(lines(ev, now, window))
        err = float(np.linalg.norm(p - O))
        errs.append(err)
        cover += err <= 2.45 * np.sqrt(np.max(np.linalg.eigvalsh(cov)))
        r = flow_source(ev, FlowSourceParams(window_s=window), None, FixedClock(now), GRID)
        old.append(GRID.dist_m(r.extras["point"], O) if r.extras["point"] else np.nan)
    print(f"{label:<16} old {np.nanmedian(old):5.1f}/{np.nanmax(old):5.1f}   new {np.median(errs):5.1f}/{max(errs):5.1f}  cover95 {cover}/10")


def main6():
    for n in (200, 80, 40, 20, 10):
        run6(f"n={n}", {"n": n})
    run6("gps 20 m", {"gps": 20.0})
    run6("hide 50%", {"hide": 0.5})
    for f in (0.1, 0.25, 0.4):
        run6(f"spoof {int(f * 100)}%", {"spoof_frac": f})
    run6("20 s window", {}, window=20.0)
    run6("n=40, 20 s", {"n": 40}, window=20.0)
