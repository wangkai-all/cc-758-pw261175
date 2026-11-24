"""HTTP API behaviour: bootstrap, frames, comparison, corrections, boards.

The handlers are exercised through a real ``ThreadingHTTPServer`` on an
ephemeral port against a temporary data directory, so the tests cover the
serialisation and routing code the page actually calls.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from rating import server as server_mod
from rating.server import Handler


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A server wired to a throwaway data dir and the shared sample dataset."""
    root = tmp_path / "data"
    (root / "generated").mkdir(parents=True)
    shutil.copy(server_mod.SAMPLE, root / "generated" / "season.json")
    monkeypatch.setattr(server_mod, "DATA", str(root))
    monkeypatch.setattr(server_mod, "SAMPLE", str(root / "generated" / "season.json"))
    server_mod.SESSIONS.clear()
    server_mod._CHECKS_CACHE.clear()
    server_mod._SWEEP_CACHE.clear()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(path, payload):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    class Client:
        pass

    client = Client()
    client.get, client.post, client.base, client.data = get, post, base, root
    yield client
    httpd.shutdown()
    httpd.server_close()
    server_mod.SESSIONS.clear()


def test_bootstrap_shape(api):
    boot = api.get("/api/bootstrap")
    assert boot["dataset"]["n_players"] == len(boot["players"])
    assert boot["matches"]
    assert boot["config"]["base_goals"] == boot["dataset"]["base_goals"]
    assert boot["diagnostics"]["duplicates"]
    m = boot["matches"][0]
    for key in ("id", "day", "side_a", "side_b", "score_a", "score_b", "result_version"):
        assert key in m


def test_frames_payload_matches_frame_count(api):
    boot = api.get("/api/bootstrap")
    bundle = api.get("/api/frames?method=approx")
    assert bundle["method"] == "approx"
    assert len(bundle["frames"]) == len(boot["matches"])
    f = bundle["frames"][0]
    assert len(f["players"]) == len(boot["players"])
    for p in f["players"][:3]:
        assert {"id", "mean", "sd", "pre_mean", "pre_sd", "true"} <= set(p)


def test_single_frame_and_player(api):
    frame = api.get("/api/frame?index=10")
    assert frame["index"] == 10
    traj = api.get("/api/player?id=P005&method=approx")
    assert traj["id"] == "P005"
    assert [p["index"] for p in traj["trajectory"]][:3] == [1, 2, 3]


def test_compare_and_checks(api):
    cmp_ = api.get("/api/compare")
    for method in ("approx", "window"):
        card = cmp_[method]["scorecard"]
        assert card["n_frames"] > 100
        assert 0.0 <= card["coverage90"] <= 1.0
        assert cmp_[method]["seconds_per_match"] >= 0
        assert len(cmp_[method]["error_series"]) == card["n_frames"]
    assert "agreement" in cmp_

    checks = api.get("/api/checks")
    assert checks["gradient_check"]["converged"]
    assert checks["gradient_check"]["max_gradient_error"] < 1e-4
    assert checks["one_step_checks"]
    assert checks["truncation"]["available"]
    assert checks["bound_group"]["identified"] is False
    assert checks["bound_group"]["aggregate"]["combined_sd"] > 0
    # The joint fit's interval uses its full covariance; the diagonal sum
    # (which ignores the combo/skill trade-off) is wider for the same trio.
    joint = checks["bound_group"]["aggregate_window"]
    diag = checks["bound_group"]["aggregate"]
    assert joint["covariance_used"] is True
    assert joint["combined_sd"] < diag["combined_sd"]
    # Same joint fit: adding its marginal variances instead of using its
    # covariance must be strictly wider.
    assert joint["combined_sd"] < joint["sd_diagonal"]
    assert checks["isolated"]["members"]
    assert checks["absence"]["series"]


def test_scenarios_and_diagnostics(api):
    sc = api.get("/api/scenarios")
    for key in ("isolated", "bound", "draw", "duplicate", "conflict"):
        assert key in sc["samples"]
    assert sc["samples"]["duplicate"][0]["why"]
    diag = api.get("/api/diagnostics")
    assert diag["isolated_groups"]
    assert diag["bound_lineups"]
    assert diag["duplicates"] and diag["conflicts"]


def test_drift_change_invalidates_and_rescores(api):
    before = api.get("/api/checks")
    updated = api.post("/api/drift", {"drift_sd": 0.01})
    assert updated["drift_sd"] == 0.01
    after = api.get("/api/checks")
    assert after["drift_assumption"] == 0.01
    assert after["absence"]["series"] != before["absence"]["series"]
    # Restoring the old value must reproduce the old errors.
    api.post("/api/drift", {"drift_sd": 0.04})
    restored = api.get("/api/checks")
    assert restored["scorecards"]["approx"]["rmse"] == pytest.approx(
        before["scorecards"]["approx"]["rmse"]
    )


def test_hyper_rejects_out_of_range(api):
    with pytest.raises(Exception):
        api.post("/api/hyper", {"drift_sd": 5})
    with pytest.raises(Exception):
        api.post("/api/hyper", {"window": 0})


def test_publish_then_correct_keeps_board(api):
    board = api.post("/api/publish", {"method": "approx", "label": "first"})
    assert board["version"] == 1
    rows_before = json.dumps(board["rows"], sort_keys=True)

    target = api.get("/api/bootstrap")["matches"][20]
    result = api.post(
        "/api/correct",
        {
            "match_id": target["id"],
            "score_a": target["score_a"] + 4,
            "score_b": target["score_b"],
            "reason": "test",
        },
    )
    assert result["new_version"] == 1
    assert result["new_score"][0] == target["score_a"] + 4
    report = result["reports"]["approx"]
    assert report["checkpoint_used"] <= result["corrected_index"]
    assert report["downstream_changes"]["final_changed_players"] > 0
    assert report["trajectories"]

    stored = api.get("/api/board?version=1")
    assert json.dumps(stored["rows"], sort_keys=True) == rows_before

    boards = api.get("/api/boards")["boards"]
    assert [b["version"] for b in boards] == [1]

    # The new revision is visible and carries lineage.
    revs = api.get("/api/revisions")
    assert revs["approx"]["revision"] == 2
    assert revs["approx"]["replayed_from"] is not None
    assert revs["approx"]["history"]

    # A second board reflects the correction and is a new version.
    board2 = api.post("/api/publish", {"method": "approx", "label": "second"})
    assert board2["version"] == 2
    assert board2["result_versions"][target["id"]] == 1
    assert json.dumps(board2["rows"], sort_keys=True) != rows_before


def test_bad_request_is_reported_as_json(api):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        api.get("/api/frame?index=not-an-int")
    body = json.loads(excinfo.value.read().decode("utf-8"))
    assert "error" in body


def test_reset_removes_only_local_artefacts(api):
    api.post("/api/publish", {"method": "approx", "label": "x"})
    boot = api.get("/api/bootstrap")
    target = boot["matches"][3]
    api.post(
        "/api/correct",
        {"match_id": target["id"], "score_a": 5, "score_b": 0, "reason": "test"},
    )
    removed = api.post("/api/reset", {})
    assert removed["removed"]
    assert api.get("/api/boards")["boards"] == []
    assert api.get("/api/bootstrap")["matches"][3]["result_version"] == 0
    assert (api.data / "generated" / "season.json").exists()


def test_sweep_shape(api):
    payload = api.get("/api/sweep?method=approx")
    assert payload["points"]
    drifts = [p["drift_sd"] for p in payload["points"]]
    assert drifts == sorted(drifts)
    for p in payload["points"]:
        assert p["rmse"] > 0
