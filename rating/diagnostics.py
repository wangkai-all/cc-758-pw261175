"""Diagnostics: disconnected groups, perma-bound lineups, draws, duplicates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

from .engine import EngineConfig, lineup_key
from .model import Dataset, Match, components, dedupe_matches


@dataclass
class Diagnostics:
    groups: List[dict]
    isolated_groups: List[dict]
    unplayed_pairs: List[dict]
    bound_lineups: List[dict]
    shared_lineups: List[dict]
    draws: dict
    duplicates: List[dict]
    conflicts: List[dict]
    warnings: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


def build_diagnostics(
    ds: Dataset, matches: Sequence[Match], config: EngineConfig
) -> Diagnostics:
    ids = [p.id for p in ds.players]
    pmap = ds.player_map()
    groups_raw = components(ids, matches)

    groups: List[dict] = []
    isolated: List[dict] = []
    linked_pairs: Dict[str, set] = {p: set() for p in ids}
    for m in matches:
        everyone = list(m.side_a) + list(m.side_b)
        for a in everyone:
            for b in everyone:
                if a != b:
                    linked_pairs[a].add(b)

    for g in groups_raw:
        gset = set(g)
        n_matches = sum(1 for m in matches if gset & set(m.side_a + m.side_b))
        days = [m.day for m in matches if gset & set(m.side_a + m.side_b)]
        entry = {
            "players": g,
            "names": [pmap[p].name for p in g],
            "n_matches": n_matches,
            "first_day": min(days) if days else None,
            "last_day": max(days) if days else None,
            "cross_group_links": sum(
                1
                for a in g
                for b in linked_pairs[a]
                if b not in gset
            ),
        }
        groups.append(entry)
        # "Isolated" means small and unable to be compared with the main pool.
        # The main component itself is not isolated just because it is alone
        # at the top of its own component list.
        if len(groups_raw) > 1 and entry["cross_group_links"] == 0 and len(g) < len(ids) / 2:
            isolated.append(entry)

    unplayed: List[dict] = []
    for g in groups_raw:
        if len(g) < 2:
            continue
        for i, a in enumerate(g):
            for b in g[i + 1 :]:
                if b in linked_pairs[a]:
                    continue
                unplayed.append(
                    {
                        "player_a": a,
                        "player_b": b,
                        "name_a": pmap[a].name,
                        "name_b": pmap[b].name,
                        "reason": "从未同场（无论队友或对手）",
                    }
                )

    lineup_counts: Dict[str, int] = {}
    player_lineups: Dict[str, set] = {pid: set() for pid in ids}
    for m in matches:
        for side in (m.side_a, m.side_b):
            key = lineup_key(side)
            lineup_counts[key] = lineup_counts.get(key, 0) + 1
            for p in side:
                player_lineups[p].add(key)

    bound: List[dict] = []
    for key, count in sorted(lineup_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        members = key.split("|")
        if len(members) < 2 or count < 3:
            continue
        if all(player_lineups[p] == {key} for p in members):
            bound.append(
                {
                    "lineup": key,
                    "players": members,
                    "names": [pmap[p].name for p in members],
                    "appearances": count,
                    "reason": "成员只在此阵容出现，个人贡献不可分",
                }
            )

    shared = [
        {"lineup": k, "appearances": c, "size": len(k.split("|"))}
        for k, c in sorted(lineup_counts.items(), key=lambda kv: -kv[1])
        if c >= config.combo_min_count
    ]

    draws = {
        "count": sum(1 for m in matches if m.is_draw),
        "total": len(matches),
        "share": round(
            sum(1 for m in matches if m.is_draw) / max(1, len(matches)), 4
        ),
        "match_ids": [m.id for m in matches if m.is_draw],
    }

    # Report duplicates/conflicts against the *raw* import, not the cleaned
    # sequence: the caller may already have removed duplicates from ``matches``.
    _, duplicates, conflicts = dedupe_matches(ds.matches)

    warnings: List[str] = []
    if isolated:
        warnings.append(
            "存在完全未交手的群组：组内可以排序，但跨组分数只由先验基准对齐，"
            "不可当作同一强度尺度上的可信比较。"
        )
    if bound:
        names = "、".join("+".join(b["names"]) for b in bound)
        warnings.append(f"同队绑定：{names} 的个人贡献不可分，应只读阵容合计。")
    if duplicates:
        warnings.append(f"重复导入 {len(duplicates)} 行，已从分析中剔除并保留在报告里。")
    if conflicts:
        warnings.append(f"同一对阵出现 {len(conflicts)} 组不同比分，保留两行并提示人工确认。")

    return Diagnostics(
        groups=groups,
        isolated_groups=isolated,
        unplayed_pairs=unplayed,
        bound_lineups=bound,
        shared_lineups=shared,
        draws=draws,
        duplicates=duplicates,
        conflicts=conflicts,
        warnings=warnings,
    )


def absent_players(matches: Sequence[Match], players: Sequence[str], upto_day: int, min_days: int = 30) -> List[dict]:
    last_seen: Dict[str, int] = {}
    for m in matches:
        if m.day > upto_day:
            continue
        for p in m.side_a + m.side_b:
            last_seen[p] = max(last_seen.get(p, -1), m.day)
    out = []
    for p in players:
        seen = last_seen.get(p)
        gap = None if seen is None else upto_day - seen
        if gap is None or gap >= min_days:
            out.append({"player_id": p, "last_seen": seen, "days_absent": gap})
    return out
