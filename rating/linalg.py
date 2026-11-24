"""Small dense linear algebra helpers (pure Python, no third-party deps).

Matrices are lists of rows.  Only what the estimator needs is implemented:
symmetric positive-definite solves (Cholesky with a jitter fallback),
inversion, and Cholesky factorisation.
"""

from __future__ import annotations

import math
from typing import List, Sequence


Matrix = List[List[float]]
Vector = List[float]


def zeros(n: int, m: int | None = None) -> Matrix:
    m = n if m is None else m
    return [[0.0] * m for _ in range(n)]


def identity(n: int, scale: float = 1.0) -> Matrix:
    out = zeros(n)
    for i in range(n):
        out[i][i] = scale
    return out


def cholesky(a: Sequence[Sequence[float]], jitter: float = 0.0) -> Matrix:
    """Lower-triangular Cholesky factor of a symmetric matrix.

    ``jitter`` is added to the diagonal before factorising (a fallback for
    numerically semi-definite covariance matrices).
    """
    n = len(a)
    L = zeros(n)
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] + (jitter if i == j else 0.0)
            for k in range(j):
                s -= L[i][k] * L[j][k]
            if i == j:
                if s <= 0.0:
                    raise ValueError("matrix is not positive definite")
                L[i][j] = math.sqrt(s)
            else:
                L[i][j] = s / L[j][j]
    return L


def solve_spd(a: Sequence[Sequence[float]], b: Sequence[float]) -> Vector:
    """Solve ``a x = b`` for symmetric positive definite ``a``."""
    n = len(a)
    try:
        L = cholesky(a)
    except ValueError:
        # Escalating diagonal jitter keeps a barely-indefinite matrix usable.
        for exp in range(-12, 1):
            jitter = 10.0 ** exp
            try:
                L = cholesky(a, jitter)
                break
            except ValueError:
                continue
        else:  # pragma: no cover - defensive
            raise
    # Forward substitution L y = b.
    y = [0.0] * n
    for i in range(n):
        s = b[i]
        for k in range(i):
            s -= L[i][k] * y[k]
        y[i] = s / L[i][i]
    # Back substitution L^T x = y.
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = y[i]
        for k in range(i + 1, n):
            s -= L[k][i] * x[k]
        x[i] = s / L[i][i]
    return x


def invert_spd(a: Sequence[Sequence[float]]) -> Matrix:
    n = len(a)
    out = zeros(n)
    for col in range(n):
        e = [0.0] * n
        e[col] = 1.0
        sol = solve_spd(a, e)
        for row in range(n):
            out[row][col] = sol[row]
    # Symmetrise against round-off.
    for i in range(n):
        for j in range(i + 1, n):
            v = 0.5 * (out[i][j] + out[j][i])
            out[i][j] = out[j][i] = v
    return out


def matvec(a: Sequence[Sequence[float]], x: Sequence[float]) -> Vector:
    return [sum(ai * xi for ai, xi in zip(row, x)) for row in a]


def outer(x: Sequence[float], y: Sequence[float]) -> Matrix:
    return [[xi * yi for yi in y] for xi in x]


def add_inplace(a: Matrix, b: Sequence[Sequence[float]], scale: float = 1.0) -> None:
    for i, row in enumerate(b):
        ai = a[i]
        for j, v in enumerate(row):
            ai[j] += scale * v


def quad_form(x: Sequence[float], a: Sequence[Sequence[float]]) -> float:
    return sum(x[i] * a[i][j] * x[j] for i in range(len(x)) for j in range(len(x)))
