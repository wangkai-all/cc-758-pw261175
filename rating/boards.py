"""Immutable published boards and versioned result corrections."""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from .model import Dataset, ResultBook, now_iso


@dataclass
class Board:
    version: int
    label: str
    method: str
    created_at: str
    dataset: str
    config_fingerprint: str
    result_versions: Dict[str, int]
    processed: int
    last_day: Optional[int]
    rows: List[dict]
    note: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        return cls(**d)


class BoardStore:
    """Append-only JSON-lines history; older versions are never rewritten."""

    def __init__(self, path: str):
        self.path = path
        self.boards: List[Board] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self.boards.append(Board.from_dict(json.loads(line)))

    def append(self, board: Board) -> None:
        self.boards.append(board)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(board.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def latest(self) -> Optional[Board]:
        return self.boards[-1] if self.boards else None

    def get(self, version: int) -> Optional[Board]:
        for b in self.boards:
            if b.version == version:
                return b
        return None

    def next_version(self) -> int:
        return self.boards[-1].version + 1 if self.boards else 1


def build_board(
    ds: Dataset,
    spec,
    run,
    label: str,
    note: str,
    result_book: ResultBook,
    versions: Dict[str, int],
) -> Board:
    from collections import Counter

    last = run.frames[-1] if run.frames else None
    counts = Counter()
    for rec in run.resolved:
        players = rec["side_a"] if isinstance(rec, dict) else rec.side_a
        players2 = rec["side_b"] if isinstance(rec, dict) else rec.side_b
        for p in list(players) + list(players2):
            counts[p] += 1
    pmap = ds.player_map()
    rows = []
    for i, pid in enumerate(spec.player_ids):
        p = pmap[pid]
        mean = last.post_mean[i] if last else 0.0
        sd = last.post_sd[i] if last else 0.0
        rows.append(
            {
                "player_id": pid,
                "name": p.name,
                "club": p.club,
                "role": p.note,
                "mean": round(mean, 5),
                "sd": round(sd, 5),
                "lo90": round(mean - 1.6448536269514722 * sd, 5),
                "hi90": round(mean + 1.6448536269514722 * sd, 5),
                "plays": counts.get(pid, 0),
            }
        )
    rows.sort(key=lambda r: (-r["mean"], r["player_id"]))
    return Board(
        version=1,  # assigned by the caller's store
        label=label,
        method=run.method,
        created_at=now_iso(),
        dataset=ds.id,
        config_fingerprint=_cfg_fp(run.config),
        result_versions={k: versions.get(k, 0) for k in versions},
        processed=len(run.frames),
        last_day=last.day if last else None,
        rows=rows,
        note=note,
    )


def _cfg_fp(config: dict) -> str:
    keys = ("drift_sd", "init_skill_sd", "combo_sd", "window", "base_goals")
    return "-".join(f"{k}={config.get(k)}" for k in keys)
