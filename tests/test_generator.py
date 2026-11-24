"""Generator and import hygiene."""

from __future__ import annotations

from rating.generator import generate_dataset
from rating.model import ResultBook, components, dedupe_matches, match_fingerprint


def test_generator_is_deterministic():
    a = generate_dataset(seed=99)
    b = generate_dataset(seed=99)
    assert [m.id for m in a.matches] == [m.id for m in b.matches]
    assert a.truth == b.truth
    c = generate_dataset(seed=100)
    assert a.truth != c.truth


def test_truth_length_matches_horizon(dataset):
    days = dataset.truth_meta["days"]
    for path in dataset.truth["skill"].values():
        assert len(path) == days


def test_scenarios_exist(dataset):
    sc = dataset.scenarios
    assert len(sc["isolated_group"]) == 4
    assert len(sc["bound_group"]) == 3
    assert sc["duplicate_rows"]
    assert sc["shocks"]
    assert sc["absent_player"]["until"] > sc["absent_player"]["from"]


def test_duplicates_detected_and_excluded(dataset):
    clean, duplicates, _ = dedupe_matches(dataset.matches)
    assert duplicates
    assert len(clean) == len(dataset.matches) - len(duplicates)
    clean_ids = {m.id for m in clean}
    for dup in duplicates:
        assert dup["id"] not in clean_ids
        assert dup["duplicate_of"] in clean_ids


def test_duplicate_fingerprint_is_orientation_independent():
    a = match_fingerprint(3, ["P1", "P2"], ["P3", "P4"], 2, 1)
    b = match_fingerprint(3, ["P4", "P3"], ["P2", "P1"], 1, 2)
    c = match_fingerprint(3, ["P1", "P2"], ["P3", "P4"], 1, 2)
    assert a == b
    assert a != c


def test_isolated_group_never_mixes(dataset):
    iso = set(dataset.scenarios["isolated_group"])
    for m in dataset.matches:
        if set(m.side_a + m.side_b) & iso:
            assert set(m.side_a + m.side_b) <= iso
    groups = components([p.id for p in dataset.players], dataset.matches)
    assert len(groups) == 2
    assert sorted(iso) in [sorted(g) for g in groups]


def test_bound_trio_always_together(dataset):
    bound = set(dataset.scenarios["bound_group"])
    for m in dataset.matches:
        if set(m.side_a) & bound:
            assert set(m.side_a) == bound
            assert not set(m.side_b) & bound
        if set(m.side_b) & bound:
            assert set(m.side_b) == bound
            assert not set(m.side_a) & bound


def test_absent_player_gap(dataset):
    absent = dataset.scenarios["absent_player"]
    days = sorted(
        m.day
        for m in dataset.matches
        if absent["id"] in m.side_a + m.side_b
    )
    inside = [d for d in days if absent["from"] <= d <= absent["until"]]
    assert inside == []
    assert any(d < absent["from"] for d in days)
    assert any(d > absent["until"] for d in days)


def test_draws_are_plausible(dataset):
    draws = sum(1 for m in dataset.matches if m.is_draw)
    share = draws / len(dataset.matches)
    assert 0.05 < share < 0.35


def test_no_team_plays_same_fixture_twice_in_a_day(dataset):
    seen = set()
    for m in dataset.matches:
        if "duplicate-batch" in m.tags or "double-import" in m.tags:
            continue
        if "conflict" in m.tags:
            continue  # the deliberate same-fixture disagreement
        key = (m.day, tuple(sorted(m.side_a)), tuple(sorted(m.side_b)))
        assert key not in seen
        seen.add(key)


def test_result_book_versions(dataset):
    book = ResultBook(dataset=dataset.id)
    match = dataset.matches[0]
    assert book.latest_version(match) == match.result_version
    v1 = book.record(match, 9, 0, "raw", actor="tester")
    v2 = book.record(match, 8, 1, "raw again", actor="tester")
    assert (v1, v2) == (1, 2)
    resolved = book.resolve(match)
    assert (resolved.score_a, resolved.score_b) == (8, 1)
    assert resolved.result_version == 2
    # The original row is untouched.
    assert (match.score_a, match.score_b) == (dataset.matches[0].score_a, dataset.matches[0].score_b)
