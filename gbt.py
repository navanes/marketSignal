"""Histogram-based gradient-boosted regression trees. Pure stdlib.

Small, fast enough for ~30k rows x 16 features in pure Python because every
feature is pre-binned into <=64 integer buckets once, so split-finding at a
node is O(rows_at_node + bins*features), not O(rows*thresholds).

Trained model serialises to plain dict/list JSON (see to_dict / predict_one),
so app.py can score it with no dependency on this module.
"""

from __future__ import annotations

import math
import random
from typing import Any

N_BINS = 48


def _bin_edges(values: list[float], n_bins: int) -> list[float]:
    xs = sorted(values)
    if xs[0] == xs[-1]:
        return [xs[0]]
    edges = []
    for k in range(1, n_bins):
        q = xs[min(len(xs) - 1, int(len(xs) * k / n_bins))]
        if not edges or q > edges[-1]:
            edges.append(q)
    return edges or [xs[len(xs) // 2]]


def _to_bin(value: float, edges: list[float]) -> int:
    lo, hi = 0, len(edges)
    while lo < hi:
        mid = (lo + hi) // 2
        if value <= edges[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


class _Node:
    __slots__ = ("feat", "bin_thr", "left", "right", "value")

    def __init__(self):
        self.feat = -1
        self.bin_thr = -1
        self.left: _Node | None = None
        self.right: _Node | None = None
        self.value = 0.0


def _build(
    binned: list[list[int]], target: list[float], idx: list[int], feats: list[int],
    depth: int, max_depth: int, min_leaf: int, lam: float,
) -> _Node:
    node = _Node()
    node.value = sum(target[i] for i in idx) / len(idx)
    if depth >= max_depth or len(idx) < 2 * min_leaf:
        return node

    total_sum = node.value * len(idx)
    total_cnt = len(idx)
    best_gain, best_feat, best_bin = 0.0, -1, -1
    for f in feats:
        col = binned[f]
        nb = max(col[i] for i in idx) + 1
        bin_sum = [0.0] * nb
        bin_cnt = [0] * nb
        for i in idx:
            b = col[i]
            bin_sum[b] += target[i]
            bin_cnt[b] += 1
        left_sum = left_cnt = 0
        for b in range(nb - 1):
            left_sum += bin_sum[b]
            left_cnt += bin_cnt[b]
            if left_cnt < min_leaf or total_cnt - left_cnt < min_leaf:
                continue
            right_sum = total_sum - left_sum
            right_cnt = total_cnt - left_cnt
            gain = (left_sum * left_sum) / (left_cnt + lam) + (right_sum * right_sum) / (right_cnt + lam) \
                - (total_sum * total_sum) / (total_cnt + lam)
            if gain > best_gain:
                best_gain, best_feat, best_bin = gain, f, b

    if best_feat < 0:
        return node
    col = binned[best_feat]
    left_idx = [i for i in idx if col[i] <= best_bin]
    right_idx = [i for i in idx if col[i] > best_bin]
    if len(left_idx) < min_leaf or len(right_idx) < min_leaf:
        return node
    node.feat = best_feat
    node.bin_thr = best_bin
    node.left = _build(binned, target, left_idx, feats, depth + 1, max_depth, min_leaf, lam)
    node.right = _build(binned, target, right_idx, feats, depth + 1, max_depth, min_leaf, lam)
    return node


def _node_to_dict(node: _Node, edges: list[list[float]]) -> dict[str, Any]:
    if node.feat < 0:
        return {"v": round(node.value, 6)}
    thr = edges[node.feat][min(node.bin_thr, len(edges[node.feat]) - 1)]
    return {
        "f": node.feat,
        "t": round(thr, 6),
        "l": _node_to_dict(node.left, edges),
        "r": _node_to_dict(node.right, edges),
    }


def fit(
    X: list[list[float]], y: list[float], *,
    n_trees: int = 250, learning_rate: float = 0.05, max_depth: int = 3,
    min_leaf: int = 60, subsample: float = 0.7, colsample: float = 0.8,
    lam: float = 1.0, seed: int = 7,
) -> dict[str, Any]:
    rng = random.Random(seed)
    n, dim = len(X), len(X[0])
    edges = [_bin_edges([X[i][j] for i in range(n)], N_BINS) for j in range(dim)]
    binned = [[_to_bin(X[i][j], edges[j]) for i in range(n)] for j in range(dim)]

    base = sum(y) / n
    pred = [base] * n
    trees: list[dict[str, Any]] = []
    n_cols = max(1, int(dim * colsample))

    for _ in range(n_trees):
        resid = [y[i] - pred[i] for i in range(n)]
        if subsample < 1.0:
            idx = [i for i in range(n) if rng.random() < subsample] or list(range(n))
        else:
            idx = list(range(n))
        feats = rng.sample(range(dim), n_cols)
        root = _build(binned, resid, idx, feats, 0, max_depth, min_leaf, lam)
        tree_dict = _node_to_dict(root, edges)
        trees.append(tree_dict)
        for i in range(n):
            pred[i] += learning_rate * _eval_tree(tree_dict, X[i])

    return {
        "model_kind": "gbt",
        "base": round(base, 6),
        "learning_rate": learning_rate,
        "trees": trees,
    }


def _eval_tree(node: dict[str, Any], x: list[float]) -> float:
    while "v" not in node:
        node = node["l"] if x[node["f"]] <= node["t"] else node["r"]
    return node["v"]


def predict_one(model: dict[str, Any], x: list[float]) -> float:
    total = model["base"]
    lr = model["learning_rate"]
    for tree in model["trees"]:
        total += lr * _eval_tree(tree, x)
    return total
