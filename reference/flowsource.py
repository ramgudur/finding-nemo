"""
flowsource.py — reference "divergence / flux" operator for moving-GPS inputs.

Two outputs from the same tracks:
  1. divergence_grid(): heat layer = net outflow of people per grid cell
     (discrete flux: exits minus entries), Gaussian-smoothed.
  2. backproject_source(): point estimate = the spot the flight lines
     emanate from (weighted least-squares intersection of lines, with
     robust reweighting so random walkers / bad tracks count less).
     Also says whether the flow is a SOURCE (people leaving: threat)
     or a SINK (people converging: exit, shelter, or a crowd forming).

Numpy only. Coordinates: lat/lon in, local meters internally.
"""
import numpy as np

EARTH_R = 6_371_000.0


def to_xy(lat, lon, lat0, lon0):
    x = np.radians(lon - lon0) * EARTH_R * np.cos(np.radians(lat0))
    y = np.radians(lat - lat0) * EARTH_R
    return x, y


def to_latlon(x, y, lat0, lon0):
    lat = lat0 + np.degrees(y / EARTH_R)
    lon = lon0 + np.degrees(x / (EARTH_R * np.cos(np.radians(lat0))))
    return lat, lon


def displacements(pings, t0, t1, min_move_m=20.0):
    """pings: array of (user_id, t, x, y). Returns per-user (start_xy, unit_dir, dist, trust_key)
    using first->last ping in [t0, t1]; a long baseline beats GPS jitter."""
    out = []
    sel = pings[(pings[:, 1] >= t0) & (pings[:, 1] <= t1)]
    for uid in np.unique(sel[:, 0]):
        p = sel[sel[:, 0] == uid]
        if len(p) < 2:
            continue
        p = p[np.argsort(p[:, 1])]
        start, end = p[0, 2:4], p[-1, 2:4]
        d = end - start
        dist = np.linalg.norm(d)
        if dist < min_move_m:          # stationary / hiding / stationary spam: no flow signal
            continue
        out.append((start, d / dist, dist, uid))
    return out


def backproject_source(moves, trust=None, iters=8):
    """Weighted least-squares point closest to all back-extended flight lines.
    Returns (xy, kind, spread_m) where kind is 'source' or 'sink'."""
    if len(moves) < 3:
        return None, None, None
    X = np.array([m[0] for m in moves])
    D = np.array([m[1] for m in moves])
    w = np.array([min(m[2], 120.0) for m in moves])          # faster movers = stronger signal
    if trust is not None:
        w = w * np.array([trust.get(m[3], 1.0) for m in moves])
    p = X.mean(axis=0)
    for _ in range(iters):
        A = np.zeros((2, 2)); b = np.zeros(2)
        for xi, di, wi in zip(X, D, w):
            P = np.eye(2) - np.outer(di, di)                  # projector orthogonal to the line
            A += wi * P; b += wi * P @ xi
        p = np.linalg.solve(A + 1e-6 * np.eye(2), b)
        # robust reweighting: perpendicular distance from p to each line (Cauchy, scale 40 m)
        r = np.array([np.linalg.norm((np.eye(2) - np.outer(di, di)) @ (p - xi)) for xi, di in zip(X, D)])
        w = w / (1.0 + (r / 40.0) ** 2)
    radial = np.einsum("ij,ij->i", D, X - p)                  # >0: moving away from p
    kind = "source" if np.average(np.sign(radial), weights=w) > 0 else "sink"
    spread = float(np.sqrt(np.average(r ** 2, weights=w)))
    return p, kind, spread


def divergence_grid(pings, t0, t1, cell_m=25.0, sigma_cells=1.5, extent=None):
    """Discrete flux per cell: (exits - entries) over the window, then Gaussian smoothing.
    Positive = people leaving (a source), negative = people arriving (a sink)."""
    sel = pings[(pings[:, 1] >= t0) & (pings[:, 1] <= t1)]
    if extent is None:
        extent = (sel[:, 2].min(), sel[:, 2].max(), sel[:, 3].min(), sel[:, 3].max())
    xmin, xmax, ymin, ymax = extent
    nx = int(np.ceil((xmax - xmin) / cell_m)) + 1
    ny = int(np.ceil((ymax - ymin) / cell_m)) + 1
    div = np.zeros((ny, nx))
    for uid in np.unique(sel[:, 0]):
        p = sel[sel[:, 0] == uid]
        p = p[np.argsort(p[:, 1])]
        ix = np.clip(((p[:, 2] - xmin) / cell_m).astype(int), 0, nx - 1)
        iy = np.clip(((p[:, 3] - ymin) / cell_m).astype(int), 0, ny - 1)
        for a in range(len(p) - 1):
            if (ix[a], iy[a]) != (ix[a + 1], iy[a + 1]):
                div[iy[a], ix[a]] += 1          # exit
                div[iy[a + 1], ix[a + 1]] -= 1  # entry
    # separable Gaussian smoothing
    k = np.arange(-3 * int(np.ceil(sigma_cells)), 3 * int(np.ceil(sigma_cells)) + 1)
    g = np.exp(-0.5 * (k / sigma_cells) ** 2); g /= g.sum()
    div = np.apply_along_axis(lambda r: np.convolve(r, g, mode="same"), 1, div)
    div = np.apply_along_axis(lambda c: np.convolve(c, g, mode="same"), 0, div)
    return div, (xmin, ymin, cell_m)


def grid_peak(div, origin):
    xmin, ymin, cell_m = origin
    iy, ix = np.unravel_index(np.argmax(div), div.shape)
    return np.array([xmin + (ix + 0.5) * cell_m, ymin + (iy + 0.5) * cell_m])


# ---------------------------------------------------------------- simulation / self-test
def simulate(n_people=200, threat=(40.0, -30.0), flee_radius=150.0, hide_frac=0.2,
             area=300.0, duration=60, dt=5, gps_sigma=8.0, n_spoof=0, seed=0):
    """Threat appears at t=0. Within flee_radius: (1-hide_frac) run away with +-30 deg noise at 2-4 m/s,
    hide_frac stay put. Outside: background walkers in random directions at ~1.2 m/s.
    Optional spoofers: n_spoof fake accounts pinging an identical fixed spot."""
    rng = np.random.default_rng(seed)
    T = np.array(threat)
    start = rng.uniform(-area / 2, area / 2, size=(n_people, 2))
    rows = []
    for i, s in enumerate(start):
        rel = s - T; r = np.linalg.norm(rel)
        if r < flee_radius and rng.random() > hide_frac:
            ang = np.arctan2(rel[1], rel[0]) + np.radians(rng.uniform(-30, 30))
            v = rng.uniform(2.0, 4.0) * np.array([np.cos(ang), np.sin(ang)])
        elif r < flee_radius:
            v = np.zeros(2)
        else:
            ang = rng.uniform(0, 2 * np.pi)
            v = 1.2 * np.array([np.cos(ang), np.sin(ang)])
        for t in range(0, duration + 1, dt):
            pos = s + v * t + rng.normal(0, gps_sigma, 2)
            rows.append((i, t, pos[0], pos[1]))
    for j in range(n_spoof):
        for t in range(0, duration + 1, dt):
            rows.append((10_000 + j, t, -100.0, 100.0))
    return np.array(rows, dtype=float), T


if __name__ == "__main__":
    print(f"{'scenario':<38}{'backproject err':>16}{'kind':>8}{'grid-peak err':>15}")
    for label, kw in [
        ("200 phones, 20% hiding", {}),
        ("80 phones", {"n_people": 80}),
        ("40 phones", {"n_people": 40}),
        ("200 phones + 40 stationary spoofers", {"n_spoof": 40}),
        ("200 phones, GPS noise 20 m", {"gps_sigma": 20.0}),
        ("200 phones, first 20 s only", {"duration": 20}),
    ]:
        errs_b, errs_g, kinds = [], [], []
        for seed in range(10):
            pings, T = simulate(seed=seed, **kw)
            t1 = pings[:, 1].max()
            p, kind, _ = backproject_source(displacements(pings, 0, t1))
            div, origin = divergence_grid(pings, 0, t1)
            errs_b.append(np.linalg.norm(p - T)); kinds.append(kind)
            errs_g.append(np.linalg.norm(grid_peak(div, origin) - T))
        k = max(set(kinds), key=kinds.count)
        print(f"{label:<38}{np.median(errs_b):>13.0f} m{k:>8}{np.median(errs_g):>12.0f} m")
    print("\n(median over 10 random seeds; threat is 1 point in a 300 m x 300 m area)")
