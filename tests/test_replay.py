"""Replay, corrections, published-board immutability and diagnostics."""

from __future__ import annotations

import json
import os

from rating.boards import BoardStore, build_board
from rating.diagnostics import build_diagnostics
from rating.model import ResultBook
from rating.run import build_run, replay_from


def _run(ds, prepared, method="approx"):
    return build_run(
        ds,
        prepared["spec"],
        prepared["config"],
        method,
        prepared["clean"],
        prepared["obs"],
        prepared["versions"],
    )


def test_board_versions_are_append_only(tmp_path):
    store = BoardStore(str(tmp_path / "boards.jsonl"))
    assert store.next_version() == 1
    from rating.boards import Board

    store.append(
        Board(
            version=1,
            label="first",
            method="approx",
            created_at="now",
            dataset="d",
            config_fingerprint="x",
            result_versions={},
            processed=10,
            last_day=20,
            rows=[{"player_id": "a", "mean": 1.0}],
        )
    )
    first_bytes = (tmp_path / "boards.jsonl").read_bytes()
    store.append(
        Board(
            version=2,
            label="second",
            method="approx",
            created_at="now",
            dataset="d",
            config_fingerprint="x",
            result_versions={},
            processed=20,
            last_day=30,
            rows=[{"player_id": "a", "mean": 0.5}],
        )
    )
    after = (tmp_path / "boards.jsonl").read_bytes()
    assert after.startswith(first_bytes)
    reloaded = BoardStore(str(tmp_path / "boards.jsonl"))
    assert [b.version for b in reloaded.boards] == [1, 2]
    assert reloaded.get(1).rows[0]["mean"] == 1.0
    assert reloaded.latest().label == "second"


def test_published_board_is_not_mutated_by_later_correction(tmp_path, dataset, prepared):
    run = _run(dataset, prepared)
    spec = prepared["spec"]
    book = ResultBook(dataset=dataset.id)
    versions = dict(prepared["versions"])
    board = build_board(dataset, spec, run, "发布前", "", book, versions)
    before = json.dumps(board.rows, sort_keys=True)

    # Correct an early match and replay.
    target_index = 25
    target = prepared["clean"][target_index]
    book.record(target, target.score_a + 3, target.score_b, "更正")
    resolved = book.resolve_all(prepared["clean"])
    versions[target.id] = book.latest_version(target)
    replay_spec = prepared["spec"]
    new_obs = [replay_spec.obs_for(m) for m in resolved]
    new_run, report = replay_from(
        dataset,
        replay_spec,
        prepared["config"],
        "approx",
        resolved,
        new_obs,
        run,
        target_index,
    )
    assert report["replayed_matches"] == len(new_run.frames) - report["checkpoint_used"]
    assert json.dumps(board.rows, sort_keys=True) == before

    board2 = build_board(dataset, replay_spec, new_run, "更正后", "", book, versions)
    changed = sum(
        1
        for a, b in zip(board.rows, board2.rows)
        if abs(a["mean"] - b["mean"]) > 1e-9
    )
    assert changed > 0
    assert board2.version == 1  # caller assigns; build_board never mutates stores


def test_replay_starts_from_checkpoint_before_correction(dataset, prepared):
    run = _run(dataset, prepared)
    target_index = 40
    spec = prepared["spec"]
    resolved = list(prepared["clean"])
    resolved[target_index] = resolved[target_index].with_result(9, 0, 1)
    obs = [spec.obs_for(m) for m in resolved]
    new_run, report = replay_from(
        dataset, spec, prepared["config"], "approx", resolved, obs, run, target_index
    )
    assert report["checkpoint_used"] <= target_index
    assert report["checkpoint_used"] == 40
    # Frames before the checkpoint are byte-identical.
    for old, new in zip(run.frames[:40], new_run.frames[:40]):
        assert old.pre_mean == new.pre_mean
        assert old.post_mean == new.post_mean
    # The corrected frame itself reflects the new score.
    assert new_run.frames[target_index].goals_a == 9
    assert new_run.frames[target_index].result_version == 1
    # Some downstream estimate must have moved; nothing before may have.
    assert new_run.frames[-1].post_mean != run.frames[-1].post_mean


def test_replay_window_filter_preserves_prefix(dataset, prepared):
    run = _run(dataset, prepared, method="window")
    target_index = 30
    spec = prepared["spec"]
    resolved = list(prepared["clean"])
    resolved[target_index] = resolved[target_index].with_result(0, 7, 1)
    obs = [spec.obs_for(m) for m in resolved]
    new_run, report = replay_from(
        dataset, spec, prepared["config"], "window", resolved, obs, run, target_index
    )
    assert report["checkpoint_used"] == 30
    assert new_run.frames[target_index].goals_b == 7
    assert new_run.parent_revision == run.revision


def test_diagnostics_flags_isolated_bound_and_duplicates(dataset):
    diag = build_diagnostics(dataset, dataset.matches, prepared_config(dataset))
    assert diag.isolated_groups
    assert diag.bound_lineups
    assert diag.duplicates
    assert diag.conflicts
    iso = set(dataset.scenarios["isolated_group"])
    flagged = set(diag.isolated_groups[0]["players"])
    assert iso <= flagged
    bound = diag.bound_lineups[0]
    assert set(bound["players"]) == set(dataset.scenarios["bound_group"])
    assert diag.draws["count"] > 0
    assert any("未交手" in w for w in diag.warnings)
    assert any("绑定" in w for w in diag.warnings)


def test_unplayed_pairs_reported_only_within_groups(dataset):
    diag = build_diagnostics(dataset, dataset.matches, prepared_config(dataset))
    for pair in diag.unplayed_pairs:
        assert "name_a" in pair and "name_b" in pair


def prepared_config(dataset):
    from rating.engine import EngineConfig

    return EngineConfig(base_goals=dataset.base_goals)
