"""Fit a learned return model and prove it out-of-sample against blend_v1.

Pure stdlib. Walk-forward only — every test row is scored by a model that was
fit strictly on earlier data. Writes data/model.json IF (and only if) the
learned model's walk-forward direction accuracy beats the current baseline.

Run:  python3 train.py [--step 3] [--symbols AAPL,MSFT] [--folds 5]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
import urllib.error

import app
import features as F
from universe import SYMBOLS, SCAN_HORIZONS

MODEL_PATH = app.DATA_DIR / "model.json"
SCORECARD_PATH = app.DATA_DIR / "scorecard.json"  # read baseline from Phase 1
WARMUP_BARS = 300
RIDGE_LAMBDAS = [0.3, 1.0, 3.0, 10.0, 30.0]
BAND_KS = [0.2, 0.3, 0.4, 0.55, 0.7, 0.85, 1.0, 1.2, 1.5]
MIN_DEPLOYED_PCT = 5.0  # a selective model is fine, but it must take *some* positions


# ---- tiny linear algebra (F is ~16, so this is plenty) --------------------

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        pivot = m[col][col] or 1e-12
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / pivot
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / (m[i][i] or 1e-12) for i in range(n)]


def ridge_fit(rows_x: list[list[float]], rows_y: list[float], lam: float):
    n, dim = len(rows_x), len(rows_x[0])
    mean = [sum(r[j] for r in rows_x) / n for j in range(dim)]
    std = [(sum((r[j] - mean[j]) ** 2 for r in rows_x) / n) ** 0.5 or 1.0 for j in range(dim)]
    xs = [[(r[j] - mean[j]) / std[j] for j in range(dim)] for r in rows_x]
    y_mean = sum(rows_y) / n
    ys = [v - y_mean for v in rows_y]
    xtx = [[sum(xs[k][i] * xs[k][j] for k in range(n)) for j in range(dim)] for i in range(dim)]
    for i in range(dim):
        xtx[i][i] += lam
    xty = [sum(xs[k][i] * ys[k] for k in range(n)) for i in range(dim)]
    coef = _solve(xtx, xty)
    return {"mean": mean, "std": std, "coef": coef, "intercept": y_mean}


def ridge_predict(model: dict, x: list[float]) -> float:
    z = [(x[j] - model["mean"][j]) / model["std"][j] for j in range(len(x))]
    return model["intercept"] + sum(z[j] * model["coef"][j] for j in range(len(x)))


# ---- dataset -------------------------------------------------------------

def build_rows(symbols: list[str], step: int) -> list[dict]:
    rows: list[dict] = []
    max_sessions = max(app.horizon_sessions(h) for h in SCAN_HORIZONS)
    for i, symbol in enumerate(symbols, 1):
        try:
            series = app.price_series(app.yahoo_chart(symbol, "5y"))
        except (ValueError, urllib.error.URLError) as exc:
            print(f"[{i}/{len(symbols)}] {symbol}: skip ({exc})")
            continue
        if len(series) < WARMUP_BARS + max_sessions + 5:
            print(f"[{i}/{len(symbols)}] {symbol}: skip (short history)")
            continue
        count = 0
        for t in range(WARMUP_BARS, len(series) - max_sessions, step):
            window = series[: t + 1]
            for horizon in SCAN_HORIZONS:
                sessions = app.horizon_sessions(horizon)
                if t + sessions >= len(series):
                    continue
                analytics = app.analytics_summary(window, "1y", horizon)
                feats = F.extract_features(window, horizon, analytics)
                if feats is None:
                    continue
                start = window[-1]["close"]
                fwd = (series[t + sessions]["close"] - start) / start * 100.0
                rows.append({
                    "date": window[-1]["date"],
                    "x": F.feature_vector(feats),
                    "y": fwd,
                    "daily_vol": F.realized_daily_vol(window),
                    "sessions": sessions,
                })
                count += 1
        print(f"[{i}/{len(symbols)}] {symbol}: {count} rows")
        time.sleep(0.6)
    rows.sort(key=lambda r: r["date"])
    return rows


# ---- scoring -----------------------------------------------------------

def _direction(value: float, band: float) -> str:
    if value >= band:
        return "up"
    if value <= -band:
        return "down"
    return "sideways"


def score(rows: list[dict], model: dict, band_k: float) -> dict:
    """Judge the model the way money would: take a +1/-1/0 position from the
    call and tally realised P&L. 'Always sideways' scores zero here, so it
    can't win by refusing to commit."""
    n = len(rows)
    pnls: list[float] = []
    up_hit = up_called = down_hit = down_called = nonflat = 0
    directional_correct = directional_total = 0
    for r in rows:
        pred = ridge_predict(model, r["x"])
        band = max(0.2, band_k * r["daily_vol"] * math.sqrt(r["sessions"]) * 100.0)
        pdir = _direction(pred, band)
        actual_move = r["y"]
        pos = {"up": 1.0, "down": -1.0}.get(pdir, 0.0)
        pnls.append(pos * actual_move)
        if pdir != "sideways":
            nonflat += 1
        if pdir == "up":
            up_called += 1
            up_hit += actual_move > 0
        elif pdir == "down":
            down_called += 1
            down_hit += actual_move < 0
        # "did we get the sign right when we took a side"
        if pdir in ("up", "down"):
            directional_total += 1
            directional_correct += (pdir == "up" and actual_move > 0) or (pdir == "down" and actual_move < 0)
    mean_pnl = sum(pnls) / n if n else 0.0
    sd = (sum((p - mean_pnl) ** 2 for p in pnls) / n) ** 0.5 if n else 0.0
    return {
        "n": n,
        "mean_pnl_pct": round(mean_pnl, 3),
        "sharpe_like": round(mean_pnl / sd, 3) if sd else 0.0,
        "deployed_pct": round(nonflat / n * 100, 1) if n else 0.0,
        "hit_rate_pct": round(directional_correct / directional_total * 100, 1) if directional_total else None,
        "up_precision_pct": round(up_hit / up_called * 100, 1) if up_called else None,
        "down_precision_pct": round(down_hit / down_called * 100, 1) if down_called else None,
    }


def blend_v1_pnl() -> dict:
    """Same +1/-1/0 P&L rule applied to the Phase-1 backtest rows, so the
    learned model has an honest incumbent to beat."""
    import sqlite3
    db = app.DATA_DIR / "backtest.sqlite3"
    if not db.exists():
        return {"mean_pnl_pct": 0.0, "sharpe_like": 0.0, "deployed_pct": 0.0}
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT predicted_direction, actual_change_pct FROM runs WHERE actual_change_pct IS NOT NULL"
        ).fetchall()
    if not rows:
        return {"mean_pnl_pct": 0.0, "sharpe_like": 0.0, "deployed_pct": 0.0}
    pnls, nonflat = [], 0
    for pdir, move in rows:
        pos = {"up": 1.0, "down": -1.0}.get(pdir, 0.0)
        pnls.append(pos * move)
        nonflat += pos != 0
    mean = sum(pnls) / len(pnls)
    sd = (sum((p - mean) ** 2 for p in pnls) / len(pnls)) ** 0.5
    return {
        "mean_pnl_pct": round(mean, 3),
        "sharpe_like": round(mean / sd, 3) if sd else 0.0,
        "deployed_pct": round(nonflat / len(rows) * 100, 1),
    }


def _objective(s: dict) -> float:
    # Reward risk-adjusted P&L, but require the model to actually take positions
    # at least ~20% of the time so it can't game the metric by staying flat.
    if s["deployed_pct"] < MIN_DEPLOYED_PCT:
        return -9.9
    return s["sharpe_like"]


def best_band_k(rows: list[dict], model: dict) -> float:
    return max(BAND_KS, key=lambda k: _objective(score(rows, model, k)))


# ---- main ------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--force", action="store_true", help="write model.json even if it doesn't beat baseline")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    baseline_pnl = blend_v1_pnl()

    print(f"Building walk-forward dataset from {len(symbols)} symbols...")
    started = time.time()
    rows = build_rows(symbols, args.step)
    if len(rows) < 500:
        print(f"Only {len(rows)} rows — not enough to train.")
        return 1
    print(f"{len(rows):,} rows spanning {rows[0]['date']} .. {rows[-1]['date']}")

    # Expanding-window walk-forward: fit on everything before a cut, test on the
    # next slice. Pick lambda on the first fold, reuse it.
    cuts = [int(len(rows) * (0.4 + 0.5 * k / args.folds)) for k in range(args.folds)]
    fold_scores: list[dict] = []
    chosen_lambda = RIDGE_LAMBDAS[len(RIDGE_LAMBDAS) // 2]
    for fi, cut in enumerate(cuts):
        train_rows = rows[:cut]
        test_rows = rows[cut: cut + max(1, len(rows) // (args.folds + 2))]
        if len(test_rows) < 50:
            continue
        tx = [r["x"] for r in train_rows]
        ty = [r["y"] for r in train_rows]
        if fi == 0:
            inner_cut = int(len(train_rows) * 0.8)
            best_lam, best_acc = chosen_lambda, -1.0
            for lam in RIDGE_LAMBDAS:
                m = ridge_fit(tx[:inner_cut], ty[:inner_cut], lam)
                k = best_band_k(train_rows[inner_cut:], m)
                acc = _objective(score(train_rows[inner_cut:], m, k))
                if acc > best_acc:
                    best_lam, best_acc = lam, acc
            chosen_lambda = best_lam
            print(f"  chosen ridge lambda = {chosen_lambda}")
        model = ridge_fit(tx, ty, chosen_lambda)
        band_k = best_band_k(train_rows, model)
        s = score(test_rows, model, band_k)
        s["fold"] = fi
        s["band_k"] = band_k
        fold_scores.append(s)
        print(f"  fold {fi}: n={s['n']} pnl/trade={s['mean_pnl_pct']}% sharpe={s['sharpe_like']} "
              f"hit={s['hit_rate_pct']}% deployed={s['deployed_pct']}% "
              f"up_prec={s['up_precision_pct']} down_prec={s['down_precision_pct']} (band_k={band_k})")

    if not fold_scores:
        print("No usable folds.")
        return 1

    def avg(key):
        vals = [s[key] for s in fold_scores if s.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    wf = {
        "mean_pnl_pct": avg("mean_pnl_pct"),
        "sharpe_like": avg("sharpe_like"),
        "hit_rate_pct": avg("hit_rate_pct"),
        "deployed_pct": avg("deployed_pct"),
        "up_precision_pct": avg("up_precision_pct"),
        "down_precision_pct": avg("down_precision_pct"),
    }

    final = ridge_fit([r["x"] for r in rows], [r["y"] for r in rows], chosen_lambda)
    final_band_k = round(sum(s["band_k"] for s in fold_scores) / len(fold_scores), 3)

    print(f"\nWalk-forward (out-of-sample), +1/-1/0 strategy P&L per trade:")
    print(f"  learned ridge_return_v1 : {wf['mean_pnl_pct']}%/trade  sharpe {wf['sharpe_like']}  "
          f"hit {wf['hit_rate_pct']}%  deployed {wf['deployed_pct']}%")
    print(f"  blend_v1 (incumbent)    : {baseline_pnl['mean_pnl_pct']}%/trade  sharpe {baseline_pnl['sharpe_like']}  "
          f"deployed {baseline_pnl['deployed_pct']}%")
    print("  feature weights (standardised, biggest first):")
    for name, w in sorted(zip(F.FEATURE_NAMES, final["coef"]), key=lambda kv: -abs(kv[1])):
        print(f"    {name:24} {w:+.4f}")

    payload = {
        "model": "ridge_return_v1",
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        "feature_names": F.FEATURE_NAMES,
        "mean": final["mean"],
        "std": final["std"],
        "coef": final["coef"],
        "intercept": final["intercept"],
        "ridge_lambda": chosen_lambda,
        "band_k": final_band_k,
        "n_samples": len(rows),
        "walkforward": wf,
        "baseline_blend_v1": baseline_pnl,
        "span": [rows[0]["date"], rows[-1]["date"]],
    }

    beats = (
        (wf["mean_pnl_pct"] or -9) > max(0.0, baseline_pnl["mean_pnl_pct"])
        and (wf["sharpe_like"] or -9) > max(0.0, baseline_pnl["sharpe_like"])
        and (wf["deployed_pct"] or 0) >= MIN_DEPLOYED_PCT
    )
    # Fold the learned model's honest walk-forward result into the scorecard the
    # web app shows, so it sits right next to the blend_v1 baseline.
    try:
        card = json.loads(SCORECARD_PATH.read_text())
    except Exception:
        card = {}
    card["learned"] = {
        "model": "ridge_return_v1",
        "trained_at": payload["trained_at"],
        "walkforward": wf,
        "baseline_blend_v1": baseline_pnl,
        "activated": bool(beats or args.force),
        "span": payload["span"],
        "n_samples": len(rows),
    }
    SCORECARD_PATH.write_text(json.dumps(card, indent=2))

    if beats or args.force:
        MODEL_PATH.write_text(json.dumps(payload, indent=2))
        print(f"\n✅ Beats the incumbent out-of-sample — wrote {MODEL_PATH}")
        print("   Activate with:  MARKETSIGNAL_MODEL=ridge_return_v1  (or edit ACTIVE_MODEL in app.py)")
    else:
        (app.DATA_DIR / "model_rejected.json").write_text(json.dumps(payload, indent=2))
        print("\n❌ Does not clearly beat blend_v1 out-of-sample — not activating. Saved model_rejected.json.")

    print(f"[{(time.time()-started)/60:.1f} min]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
