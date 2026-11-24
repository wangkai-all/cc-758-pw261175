#!/usr/bin/env python3
"""Generate the deterministic fictional dataset used by the app and tests."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rating.generator import generate_dataset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=75826)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--out", default="data/generated/season.json")
    args = ap.parse_args()
    ds = generate_dataset(seed=args.seed, days=args.days)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ds.save(out)
    print(f"wrote {out}: {len(ds.players)} players, {len(ds.matches)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
