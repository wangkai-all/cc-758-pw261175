"""Core data model for fictional roster matches.

Nothing here knows about ratings; it covers loading, duplicate detection,
connected components, exact-lineup keys and result revisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lineup_key(player_ids: Iterable[str]) -> str:
    return "|".join(sorted(player_ids))


def match_fingerprint(
    day: int, side_a: Sequence[str], side_b: Sequence[str], score_a: int, score_b: int
) -> str:
    """Orientation-independent identity of a match result."""
    a = tuple(sorted(side_a))
    b = tuple(sorted(side_b))
    if a <= b:
        left, right, sa, sb = a, b, int(score_a), int(score_b)
    else:
        left, right, sa, sb = b, a, int(score_b), int(score_a)
    raw = json.dumps([int(day), list(left), list(right), sa, sb], separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    note: str = ""
    club: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "note": self.note, "club": self.club}


@dataclass
class Match:
    id: str
    day: int
    side_a: Tuple[str, ...]
    side_b: Tuple[str, ...]
    score_a: int
    score_b: int
    batch: str = "import-1"
    duplicate_of: str | None = None
    tags: Tuple[str, ...] = ()
    result_version: int = 0

    @property
    def is_draw(self) -> bool:
        return self.score_a == self.score_b

    @property
    def fingerprint(self) -> str:
        return match_fingerprint(self.day, self.side_a, self.side_b, self.score_a, self.score_b)

    def with_result(self, score_a: int, score_b: int, version: int) -> "Match":
        return Match(
            id=self.id,
            day=self.day,
            side_a=self.side_a,
            side_b=self.side_b,
            score_a=int(score_a),
            score_b=int(score_b),
            batch=self.batch,
            duplicate_of=self.duplicate_of,
            tags=self.tags,
            result_version=version,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "day": self.day,
            "side_a": list(self.side_a),
            "side_b": list(self.side_b),
            "score_a": self.score_a,
            "score_b": self.score_b,
            "batch": self.batch,
            "duplicate_of": self.duplicate_of,
            "tags": list(self.tags),
            "result_version": self.result_version,
            "is_draw": self.is_draw,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Match":
        return cls(
            id=d["id"],
            day=int(d["day"]),
            side_a=tuple(d["side_a"]),
            side_b=tuple(d["side_b"]),
            score_a=int(d["score_a"]),
            score_b=int(d["score_b"]),
            batch=d.get("batch", "import-1"),
            duplicate_of=d.get("duplicate_of"),
            tags=tuple(d.get("tags", [])),
            result_version=int(d.get("result_version", 0)),
        )


@dataclass
class Dataset:
    id: str
    title: str
    description: str
    players: List[Player]
    matches: List[Match]
    base_goals: float
    skill_scale: float
    drift_true: float
    seed: int
    truth: dict = field(default_factory=dict)
    truth_meta: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)
    generated_at: str = ""

    def player_map(self) -> Dict[str, Player]:
        return {p.id: p for p in self.players}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "players": [p.to_dict() for p in self.players],
            "matches": [m.to_dict() for m in self.matches],
            "base_goals": self.base_goals,
            "skill_scale": self.skill_scale,
            "drift_true": self.drift_true,
            "seed": self.seed,
            "truth": self.truth,
            "truth_meta": self.truth_meta,
            "scenarios": self.scenarios,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Dataset":
        return cls(
            id=d["id"],
            title=d["title"],
            description=d.get("description", ""),
            players=[Player(**p) for p in d["players"]],
            matches=[Match.from_dict(m) for m in d["matches"]],
            base_goals=float(d["base_goals"]),
            skill_scale=float(d["skill_scale"]),
            drift_true=float(d["drift_true"]),
            seed=int(d["seed"]),
            truth=d.get("truth", {}),
            truth_meta=d.get("truth_meta", {}),
            scenarios=d.get("scenarios", {}),
            generated_at=d.get("generated_at", ""),
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "Dataset":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def dedupe_matches(
    matches: Sequence[Match],
) -> Tuple[List[Match], List[dict], List[dict]]:
    """Split into usable matches, duplicate imports and score conflicts.

    A duplicate is the same day, the same two rosters (either orientation)
    and the same score.  The first occurrence wins; later ones are reported
    and excluded from analysis.  Same fixture with a different score is a
    conflict and is kept, but flagged.
    """
    seen: Dict[str, Match] = {}
    clean: List[Match] = []
    duplicates: List[dict] = []
    conflicts: List[dict] = []
    for m in matches:
        if m.duplicate_of:
            # Explicitly marked by the importer.
            duplicates.append(
                {
                    "id": m.id,
                    "duplicate_of": m.duplicate_of,
                    "batch": m.batch,
                    "day": m.day,
                    "reason": "导入批次显式标记的重复行",
                }
            )
            continue
        fp = m.fingerprint
        if fp in seen:
            first = seen[fp]
            duplicates.append(
                {
                    "id": m.id,
                    "duplicate_of": first.id,
                    "batch": m.batch,
                    "day": m.day,
                    "reason": "同日、同阵容、同比分（指纹相同）",
                }
            )
            continue
        # Same fixture, different score.
        base = match_fingerprint(m.day, m.side_a, m.side_b, 0, 0)
        for prior in clean:
            if match_fingerprint(prior.day, prior.side_a, prior.side_b, 0, 0) == base:
                if (prior.score_a, prior.score_b) != (m.score_a, m.score_b):
                    conflicts.append(
                        {
                            "id": m.id,
                            "conflict_with": prior.id,
                            "day": m.day,
                            "scores": [
                                [prior.score_a, prior.score_b],
                                [m.score_a, m.score_b],
                            ],
                            "reason": "同一对阵出现不同比分，保留两行并提示",
                        }
                    )
        seen[fp] = m
        clean.append(m)
    return clean, duplicates, conflicts


class UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def components(player_ids: Sequence[str], matches: Sequence[Match]) -> List[List[str]]:
    """Connected player groups: sharing a match makes ratings comparable."""
    uf = UnionFind(player_ids)
    for m in matches:
        everyone = list(m.side_a) + list(m.side_b)
        for other in everyone[1:]:
            uf.union(everyone[0], other)
    groups: Dict[str, List[str]] = {}
    for pid in player_ids:
        groups.setdefault(uf.find(pid), []).append(pid)
    return sorted((sorted(v) for v in groups.values()), key=lambda g: (-len(g), g[0]))


@dataclass
class ResultBook:
    """Append-only result revisions.

    ``revisions[match_id]`` is a list of {version, score_a, score_b, at,
    reason, actor}; version 0 always mirrors the imported dataset row.
    """

    dataset: str
    revisions: Dict[str, List[dict]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str, dataset: str) -> "ResultBook":
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return cls(dataset=dataset)
        return cls(dataset=raw.get("dataset", dataset), revisions=raw.get("revisions", {}))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"dataset": self.dataset, "revisions": self.revisions},
                fh,
                ensure_ascii=False,
                indent=1,
            )

    def latest_version(self, match: Match) -> int:
        entries = self.revisions.get(match.id, [])
        return max((e["version"] for e in entries), default=match.result_version)

    def resolve(self, match: Match) -> Match:
        entries = self.revisions.get(match.id, [])
        if not entries:
            return match
        latest = max(entries, key=lambda e: e["version"])
        return match.with_result(latest["score_a"], latest["score_b"], latest["version"])

    def resolve_all(self, matches: Sequence[Match]) -> List[Match]:
        return [self.resolve(m) for m in matches]

    def record(
        self, match: Match, score_a: int, score_b: int, reason: str, actor: str = "analyst"
    ) -> int:
        entries = self.revisions.setdefault(match.id, [])
        version = max((e["version"] for e in entries), default=match.result_version) + 1
        entries.append(
            {
                "version": version,
                "score_a": int(score_a),
                "score_b": int(score_b),
                "at": now_iso(),
                "reason": reason,
                "actor": actor,
            }
        )
        return version

    def version_map(self, matches: Sequence[Match]) -> Dict[str, int]:
        return {m.id: self.latest_version(m) for m in matches}
