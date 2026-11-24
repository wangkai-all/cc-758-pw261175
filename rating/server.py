"""Small local JSON API for the web page (stdlib only).

Run with ``python3 -m rating.server --port 8765``.  The server never touches
the network beyond localhost and reads only files under the project's data
directory.  It supports playback, a drift-assumption slider, method
comparison and result corrections with replay.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import os
import shutil
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .diagnostics import build_diagnostics
from .engine import (
    DiagState,
    EngineConfig,
    ModelSpec,
    joint_window_fit,
    lineup_aggregates,
)
from .model import Dataset, ResultBook, now_iso
from .run import (
    RunStore,
    build_run,
    latest_rows,
    replay_from,
)
from .analysis import (
    Z90,
    compare_one_step,
    gradient_check,
    member_indistinguishability,
    response_to_shock,
    score_run,
    truth_at,
    window_truncation,
)
from .boards import BoardStore, build_board

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SAMPLE = os.path.join(DATA, "generated", "season.json")
DATE = "2026-11-24"


# --------------------------------------------------------------------------
# Session: one dataset + lazily built runs
# --------------------------------------------------------------------------


class Session:
    def __init__(self, dataset_path: str):
        self.dataset_path = dataset_path
        self.ds = Dataset.load(dataset_path)
        self.config = EngineConfig(base_goals=self.ds.base_goals)
        self.result_book = ResultBook.load(os.path.join(DATA, "results.json"), self.ds.id)
        self.run_store = RunStore(DATA, self.ds.id)
        self.board_store = BoardStore(os.path.join(DATA, "boards", f"{self.ds.id}.jsonl"))
        self._cache: Dict[tuple, object] = {}
        self._runs: Dict[str, object] = {}
        self._specs: Dict[str, ModelSpec] = {}
        # The HTTP server is threaded; building one run is expensive and must
        # not race with a second request for the same method (or a correction).
        self.lock = threading.RLock()

    # -- resolved inputs ---------------------------------------------------

    def resolved_matches(self):
        from .model import dedupe_matches

        clean, dups, conflicts = dedupe_matches(self.ds.matches)
        clean.sort(key=lambda m: (m.day, m.id))
        versions = {m.id: self.result_book.latest_version(m) for m in clean}
        return clean, self.result_book.resolve_all(clean), versions, dups, conflicts

    def build(self):
        clean, resolved, versions, _, _ = self.resolved_matches()
        spec = ModelSpec([p.id for p in self.ds.players], [], self.config)
        obs = [spec.obs_for(m) for m in resolved]
        spec = ModelSpec([p.id for p in self.ds.players], obs, self.config)
        obs = [spec.obs_for(m) for m in resolved]
        return clean, resolved, versions, spec, obs

    def diagnostics(self):
        clean, resolved, _, _, _ = self.resolved_matches()
        return build_diagnostics(self.ds, clean, self.config)

    # -- runs --------------------------------------------------------------

    def run(self, method: str):
        key = method
        with self.lock:
            clean, resolved, versions, spec, obs = self.build()
            fingerprint = _fingerprint(self.ds, method, self.config, versions)
            cached = self._runs.get(key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            run = self.run_store.load(method, fingerprint)
            if run is None:
                run = build_run(
                    self.ds, spec, self.config, method, resolved, obs, versions
                )
                # The store key is the session fingerprint (config + revisions),
                # not the generator-only fingerprint from build_run.
                run.fingerprint = fingerprint
                self.run_store.save(run)
            self._runs[key] = (fingerprint, run)
            self._specs[key] = spec
            return run

    def spec_for(self, method: str) -> ModelSpec:
        self.run(method)
        return self._specs[method]

    def _spec_for_run(self, method: str, resolved) -> ModelSpec:
        stamp = (method, tuple(m.id for m in resolved))
        cached = self._specs.get(stamp)
        if cached is not None:
            return cached
        base = ModelSpec([p.id for p in self.ds.players], [], self.config)
        obs = [base.obs_for(m) for m in resolved]
        spec = ModelSpec([p.id for p in self.ds.players], obs, self.config)
        self._specs[stamp] = spec
        return spec

    def invalidate(self):
        self._runs.clear()
        self._specs.clear()
        self._cache.clear()

    def correct(self, match_id: str, goals_a: int, goals_b: int, reason: str) -> dict:
        with self.lock:
            return self._correct(match_id, goals_a, goals_b, reason)

    def _correct(self, match_id: str, goals_a: int, goals_b: int, reason: str) -> dict:
        clean, resolved, versions, spec, obs = self.build()
        base = {m.id: m for m in clean}[match_id]
        old_version = self.result_book.latest_version(base)
        old_score = [base.score_a, base.score_b]
        # Snapshot the pre-correction runs before versions change.
        previous_runs = {}
        for method in ("approx", "window"):
            old_fp = _fingerprint(self.ds, method, self.config, versions)
            prev = self.run_store.load(method, old_fp)
            if prev is not None:
                previous_runs[method] = (old_fp, prev)
        new_version = self.result_book.record(base, goals_a, goals_b, reason)
        self.result_book.save(os.path.join(DATA, "results.json"))
        self.invalidate()

        clean, resolved, versions, spec, obs = self.build()
        corrected_index = next(i for i, m in enumerate(resolved) if m.id == match_id)
        reports = {}
        for method, (old_fp, previous) in previous_runs.items():
            new_run, report = replay_from(
                self.ds,
                self._spec_for_run(method, resolved),
                self.config,
                method,
                resolved,
                obs,
                previous,
                corrected_index,
            )
            new_fp = _fingerprint(self.ds, method, self.config, versions)
            new_run.fingerprint = new_fp
            self.run_store.save(new_run)
            compare = _compare_revisions(previous, new_run, spec, corrected_index)
            # Before/after trajectories for the movers, so the page can show
            # that only the tail after the checkpoint moved.
            moves = {}
            for row in compare.get("top_moves", []):
                pid = row["player_id"]
                i = spec.index_of[pid]
                moves[pid] = {
                    "before": [f.post_mean[i] for f in previous.frames],
                    "after": [f.post_mean[i] for f in new_run.frames],
                }
            reports[method] = {
                **report,
                "revision": new_run.revision,
                "old_fingerprint": old_fp,
                "new_fingerprint": new_fp,
                "downstream_changes": compare,
                "trajectories": moves,
                "marks": {
                    "match_id": match_id,
                    "day": resolved[corrected_index].day,
                    "index": corrected_index + 1,
                    "old_score": old_score,
                    "new_score": [
                        resolved[corrected_index].score_a,
                        resolved[corrected_index].score_b,
                    ],
                    "days": [f.day for f in new_run.frames],
                },
            }
        self.invalidate()
        # The corrected score is what the result book now holds.
        resolved_base = {m.id: m for m in resolved}[match_id]
        return {
            "match_id": match_id,
            "old_version": old_version,
            "new_version": new_version,
            "old_score": old_score,
            "new_score": [resolved_base.score_a, resolved_base.score_b],
            "corrected_index": corrected_index,
            "reports": reports,
        }

    def reset(self) -> dict:
        """Remove this dataset's local correction/replay artefacts.

        Only files this app itself wrote are removed: the result book, saved
        runs and published boards.  The generated fixture dataset is never
        touched.  The page asks for confirmation before calling this.
        """
        removed = []
        results_path = os.path.join(DATA, "results.json")
        if os.path.exists(results_path):
            os.remove(results_path)
            removed.append("results.json")
        run_dir = os.path.join(DATA, "runs", self.ds.id)
        if os.path.isdir(run_dir):
            shutil.rmtree(run_dir)
            removed.append(f"runs/{self.ds.id}")
        board_path = os.path.join(DATA, "boards", f"{self.ds.id}.jsonl")
        if os.path.exists(board_path):
            os.remove(board_path)
            removed.append(f"boards/{self.ds.id}.jsonl")
        self.result_book = ResultBook(dataset=self.ds.id)
        self.run_store = RunStore(DATA, self.ds.id)
        self.board_store = BoardStore(board_path)
        self.invalidate()
        _CHECKS_CACHE.clear()
        _SWEEP_CACHE.clear()
        return {"removed": removed, "dataset": self.ds.id}

    def publish(self, method: str, label: str, note: str = "") -> dict:
        run = self.run(method)
        clean, resolved, versions, spec, obs = self.build()
        board = build_board(
            self.ds, spec, run, label, note, self.result_book, versions
        )
        board.version = self.board_store.next_version()
        self.board_store.append(board)
        return board.to_dict()


def _compare_revisions(old, new, spec, corrected_index: int) -> dict:
    """How far did each player's final estimate move after the replay?"""
    if not old.frames or not new.frames:
        return {}
    o = old.frames[-1]
    n = new.frames[-1]
    diffs = [
        {
            "player_id": pid,
            "before": o.post_mean[i],
            "after": n.post_mean[i],
            "delta": round(n.post_mean[i] - o.post_mean[i], 6),
        }
        for i, pid in enumerate(spec.player_ids)
    ]
    diffs.sort(key=lambda d: -abs(d["delta"]))
    changed = [d for d in diffs if abs(d["delta"]) > 1e-6]
    return {
        "final_max_abs_delta": max((abs(d["delta"]) for d in diffs), default=0.0),
        "final_changed_players": len(changed),
        "top_moves": diffs[:8],
        "frames_before": len(old.frames),
        "frames_after": len(new.frames),
        "corrected_index": corrected_index,
    }


def _fingerprint(ds: Dataset, method: str, config: EngineConfig, versions: Dict[str, int]) -> str:
    import hashlib

    from dataclasses import asdict

    raw = json.dumps(
        {
            "dataset": ds.id,
            "method": method,
            "config": asdict(config),
            "versions": versions,
            "n": len(ds.matches),
        },
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


SESSIONS: Dict[str, Session] = {}


def get_session() -> Session:
    key = "sample"
    if key not in SESSIONS:
        SESSIONS[key] = Session(SAMPLE)
    return SESSIONS[key]


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------


def _frame_payload(run, spec, frame, ds):
    players = []
    for i, pid in enumerate(spec.player_ids):
        players.append(
            {
                "id": pid,
                "mean": frame.post_mean[i],
                "sd": frame.post_sd[i],
                "pre_mean": frame.pre_mean[i],
                "pre_sd": frame.pre_sd[i],
                "true": round(truth_at(ds, pid, frame.day), 4),
            }
        )
    return {
        "index": frame.index,
        "match_id": frame.match_id,
        "day": frame.day,
        "goals_a": frame.goals_a,
        "goals_b": frame.goals_b,
        "p_a": frame.p_a,
        "p_draw": frame.p_draw,
        "p_b": frame.p_b,
        "players": players,
        "result_version": frame.result_version,
        "seconds": frame.seconds,
    }


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "RosterRatings/1.0"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- utils -------------------------------------------------------------

    def _send(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str):
        if path in ("/", "/index.html"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(ROOT, "web", path.lstrip("/")))
        if not full.startswith(os.path.join(ROOT, "web")) or not os.path.isfile(full):
            self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        import mimetypes

        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._static(parsed.path)
            return
        q = parse_qs(parsed.query)
        try:
            self.route_get(parsed.path, q)
        except Exception as exc:  # surfaced to the page, never swallowed
            self._send({"error": str(exc), "type": type(exc).__name__}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            self.route_post(parsed.path, payload)
        except Exception as exc:
            self._send({"error": str(exc), "type": type(exc).__name__}, HTTPStatus.BAD_REQUEST)

    # -- GET ---------------------------------------------------------------

    def route_get(self, path: str, q: dict):
        session = get_session()
        if path == "/api/bootstrap":
            self._send(bootstrap(session))
        elif path == "/api/run":
            method = q.get("method", ["approx"])[0]
            run = session.run(method)
            spec = session.spec_for(method)
            self._send(
                {
                    "method": method,
                    "revision": run.revision,
                    "frames": [
                        {
                            "index": f.index,
                            "match_id": f.match_id,
                            "day": f.day,
                            "p_a": f.p_a,
                            "p_draw": f.p_draw,
                            "p_b": f.p_b,
                            "goals_a": f.goals_a,
                            "goals_b": f.goals_b,
                            "seconds": f.seconds,
                        }
                        for f in run.frames
                    ],
                }
            )
        elif path == "/api/frames":
            method = q.get("method", ["approx"])[0]
            run = session.run(method)
            spec = session.spec_for(method)
            self._send(
                {
                    "method": method,
                    "revision": run.revision,
                    "players": list(spec.player_ids),
                    "frames": [
                        _frame_payload(run, spec, f, session.ds) for f in run.frames
                    ],
                }
            )
        elif path == "/api/frame":
            method = q.get("method", ["approx"])[0]
            index = int(q.get("index", ["1"])[0])
            run = session.run(method)
            spec = session.spec_for(method)
            frame = run.frames[max(0, min(index, len(run.frames)) - 1)]
            self._send(_frame_payload(run, spec, frame, session.ds))
        elif path == "/api/player":
            method = q.get("method", ["approx"])[0]
            pid = q.get("id", [""])[0]
            run = session.run(method)
            spec = session.spec_for(method)
            i = spec.index_of[pid]
            traj = [
                {
                    "day": f.day,
                    "index": f.index,
                    "match_id": f.match_id,
                    "mean": f.post_mean[i],
                    "sd": f.post_sd[i],
                    "true": round(truth_at(session.ds, pid, f.day), 4),
                    "played": _played(run, f, pid),
                }
                for f in run.frames
            ]
            self._send({"id": pid, "method": method, "trajectory": traj})
        elif path == "/api/compare":
            payload = compare(session)
            self._send(payload)
        elif path == "/api/diagnostics":
            diag = session.diagnostics()
            payload = diag.to_dict()
            payload["scenarios"] = session.ds.scenarios
            payload["truth_meta"] = session.ds.truth_meta
            self._send(payload)
        elif path == "/api/boards":
            boards = session.board_store.boards
            self._send(
                {
                    "boards": [
                        {
                            "version": b.version,
                            "label": b.label,
                            "created_at": b.created_at,
                            "method": b.method,
                            "result_versions": b.result_versions,
                            "processed": b.processed,
                            "note": b.note,
                        }
                        for b in boards
                    ]
                }
            )
        elif path == "/api/board":
            version = int(q.get("version", ["0"])[0])
            board = session.board_store.get(version)
            if board is None:
                self._send({"error": "no such board"}, HTTPStatus.NOT_FOUND)
            else:
                self._send(board.to_dict())
        elif path == "/api/checks":
            self._send(checks(session))
        elif path == "/api/scenarios":
            self._send(scenarios(session))
        elif path == "/api/revisions":
            self._send(revisions(session))
        elif path == "/api/sweep":
            method = q.get("method", ["approx"])[0]
            self._send(sweep(session, method))
        else:
            self._send({"error": "unknown route"}, HTTPStatus.NOT_FOUND)

    # -- POST --------------------------------------------------------------

    def route_post(self, path: str, payload: dict):
        session = get_session()
        if path == "/api/drift":
            value = float(payload.get("drift_sd", session.config.drift_sd))
            if not (0.0 <= value <= 0.2):
                raise ValueError("drift_sd must be within [0, 0.2]")
            session.config = EngineConfig(
                **{**session.config.__dict__, "drift_sd": value}
            )
            session.invalidate()
            self._send({"drift_sd": value, "config": session.config.__dict__})
        elif path == "/api/hyper":
            updates = {}
            for name in ("drift_sd", "init_skill_sd", "combo_sd", "window", "base_goals"):
                if name in payload:
                    updates[name] = payload[name]
            merged = {**session.config.__dict__, **updates}
            if int(merged["window"]) < 1:
                raise ValueError("window must be >= 1")
            if not (0.0 <= float(merged["drift_sd"]) <= 0.2):
                raise ValueError("drift_sd must be within [0, 0.2]")
            if not (0.05 <= float(merged["init_skill_sd"]) <= 2.0):
                raise ValueError("init_skill_sd must be within [0.05, 2.0]")
            if not (0.0 <= float(merged["combo_sd"]) <= 2.0):
                raise ValueError("combo_sd must be within [0, 2.0]")
            session.config = EngineConfig(**merged)
            session.invalidate()
            self._send({"config": session.config.__dict__})
        elif path == "/api/correct":
            result = session.correct(
                payload["match_id"],
                int(payload["score_a"]),
                int(payload["score_b"]),
                payload.get("reason", "人工更正"),
            )
            self._send(result)
        elif path == "/api/reset":
            self._send(session.reset())
        elif path == "/api/publish":
            board = session.publish(
                payload.get("method", "approx"),
                payload.get("label", f"发布 {now_iso()}"),
                payload.get("note", ""),
            )
            self._send(board)
        else:
            self._send({"error": "unknown route"}, HTTPStatus.NOT_FOUND)


def _played(run, frame, pid: str) -> bool:
    rec = run.resolved[frame.index - 1]
    side_a = rec["side_a"] if isinstance(rec, dict) else rec.side_a
    side_b = rec["side_b"] if isinstance(rec, dict) else rec.side_b
    return pid in side_a or pid in side_b


# --------------------------------------------------------------------------
# Payload builders
# --------------------------------------------------------------------------


def bootstrap(session: Session) -> dict:
    ds = session.ds
    clean, resolved, versions, spec, obs = session.build()
    diag = session.diagnostics().to_dict()
    return {
        "dataset": {
            "id": ds.id,
            "title": ds.title,
            "description": ds.description,
            "n_players": len(ds.players),
            "n_matches": len(clean),
            "base_goals": ds.base_goals,
            "drift_true": ds.drift_true,
            "seed": ds.seed,
        },
        "players": [p.to_dict() for p in ds.players],
        "matches": [_match_payload(m, versions.get(m.id, 0)) for m in resolved],
        "config": session.config.__dict__,
        "diagnostics": diag,
        "scenarios": ds.scenarios,
        "truth_meta": ds.truth_meta,
    }


def _match_payload(m, version: int) -> dict:
    return {
        "id": m.id,
        "day": m.day,
        "side_a": list(m.side_a),
        "side_b": list(m.side_b),
        "score_a": m.score_a,
        "score_b": m.score_b,
        "tags": list(m.tags),
        "duplicate_of": m.duplicate_of,
        "result_version": version,
    }


def _frame_errors(ds, spec, run) -> List[dict]:
    """Mean absolute skill error across all players, frame by frame."""
    out = []
    for f in run.frames:
        errors = [
            abs(f.post_mean[i] - truth_at(ds, pid, f.day))
            for i, pid in enumerate(spec.player_ids)
        ]
        out.append(
            {
                "index": f.index,
                "day": f.day,
                "mae": round(statistics.fmean(errors), 5),
                "max_abs": round(max(errors), 5),
            }
        )
    return out


def _final_state(run, method):
    if not run.frames or not run.checkpoints:
        return None
    payload = run.checkpoints.get(str(len(run.frames)))
    if payload is None:
        payload = run.checkpoints[max(run.checkpoints, key=lambda k: int(k))]
    if method == "approx":
        return DiagState.from_dict(payload["state"])
    states = payload["states"]
    return DiagState.from_dict(states[max(states, key=lambda k: int(k))])


def _lineup_summary(ds, spec, run, method: str) -> dict:
    """Combined lift of the bound lineup, read from the final saved state.

    The window filter keeps the full posterior covariance, so its combined
    interval is available too: for a perma-bound lineup the members' skills
    and the combo term trade off, and only their sum is identified.  Adding
    marginal variances (as the diagonal approximation must) overstates that
    interval; the difference is the point of the trio, so both are reported.
    """
    key = ds.scenarios.get("bound_combo_key", "")
    members = list(ds.scenarios.get("bound_group", []))
    state = _final_state(run, method)
    if not key or not members or state is None:
        return {}
    idxs = [spec.index_of[p] for p in members]
    mean = statistics.fmean(state.skill_mean[i] for i in idxs)
    var = sum(state.skill_var[i] for i in idxs) / (len(idxs) ** 2)
    combo = state.combo_mean.get(key, 0.0)
    total = mean + combo
    sd_diag = math.sqrt(max(var + state.combo_var.get(key, 0.0), 0.0))
    sd = sd_diag
    joint = _joint_lineup_interval(spec, run, key) if method == "window" else None
    if joint is not None:
        mean, combo, total, sd = (
            joint["member_mean"],
            joint["combo"],
            joint["combined_mean"],
            joint["combined_sd"],
        )
    return {
        "lineup": key,
        "members": members,
        "member_mean": round(mean, 4),
        "combo": round(combo, 4),
        "combo_true": round(ds.truth["combo"].get(key, 0.0), 4),
        "combined_mean": round(total, 4),
        "combined_sd": round(sd, 4),
        "lo90": round(total - Z90 * sd, 4),
        "hi90": round(total + Z90 * sd, 4),
        "sd_diagonal": round(sd_diag, 4),
        "covariance_used": joint is not None,
        "note": "阵容合计 = 成员均值 + 组合项；成员个人分数不可分。",
    }


def _joint_lineup_interval(spec, run, key: str):
    """Refit the run's final window and read the lineup's interval from the
    joint posterior covariance (mean and variance together).

    The window filter keeps the state from ``window`` matches back so the
    final window can be rebuilt exactly; if that state is unavailable the
    caller falls back to the diagonal interval.
    """
    if not run.frames or not run.checkpoints:
        return None
    last = len(run.frames)
    # WindowFilter.step for match `last` (1-based) fits observations
    # [last-window, last) against the state saved after `last-window` matches.
    start = max(0, last - spec.config.window)
    template = _state_at(run, start - 1)
    if template is None:
        return None
    window_frames = [f for f in run.frames if f.index > start]
    obs = [_observation_of(run, spec, f) for f in window_frames]
    if len(obs) != last - start:
        return None
    prior = DiagState.from_dict(template)
    mid_day = (obs[0].day + obs[-1].day) / 2.0
    prior = prior.inflate(int(round(mid_day)), spec.config.drift_sd)
    fit = joint_window_fit(spec, obs, prior, spec.config)
    agg = lineup_aggregates(spec, fit, prior).get(key)
    if agg is None:
        return None
    combo = 0.0
    for i, (kind, k) in enumerate(fit.param_keys):
        if kind == "c" and str(k) == key:
            combo = fit.mean[i]
    total, var = agg
    return {
        "member_mean": total - combo,
        "combo": combo,
        "combined_mean": total,
        "combined_sd": math.sqrt(max(var, 0.0)),
    }


def _state_at(run, index: int):
    """State after ``index`` matches, from whatever checkpoint retained it.

    Checkpoints are saved every ``CHECKPOINT_EVERY`` matches and each window
    checkpoint keeps the states inside its window, so a needed state is
    usually present in one of them.  If it is missing the caller keeps the
    diagonal interval rather than a stale prior.
    """
    if index < 0:
        return None
    best = None
    for payload in run.checkpoints.values():
        for key, state in (payload.get("states") or {}).items():
            k = int(key)
            if k <= index and (best is None or k > best[0]):
                best = (k, state)
    return best[1] if best is not None and best[0] == index else None


def _observation_of(run, spec, frame):
    from .engine import Observation

    rec = run.resolved[frame.index - 1]
    side_a = rec["side_a"] if isinstance(rec, dict) else rec.side_a
    side_b = rec["side_b"] if isinstance(rec, dict) else rec.side_b
    return Observation(
        match_id=rec["id"] if isinstance(rec, dict) else rec.id,
        day=frame.day,
        side_a=tuple(spec.index_of[p] for p in side_a),
        side_b=tuple(spec.index_of[p] for p in side_b),
        goals_a=frame.goals_a,
        goals_b=frame.goals_b,
        combo_a="|".join(sorted(side_a)),
        combo_b="|".join(sorted(side_b)),
    )


def compare(session: Session) -> dict:
    results = {}
    for method in ("approx", "window"):
        run = session.run(method)
        spec = session.spec_for(method)
        results[method] = {
            "scorecard": score_run(session.ds, spec, run).to_dict(),
            "revision": run.revision,
            "seconds_total": round(sum(f.seconds for f in run.frames), 4),
            "seconds_per_match": round(
                sum(f.seconds for f in run.frames) / max(1, len(run.frames)), 6
            ),
            "error_series": _frame_errors(session.ds, spec, run),
        }
    # Latest prediction agreement on the same final state.
    last = {}
    for method in ("approx", "window"):
        run = session.run(method)
        spec = session.spec_for(method)
        last[method] = latest_rows(run, session.ds, spec)
    diffs = []
    for a, b in zip(last["approx"], last["window"]):
        diffs.append(abs(a["mean"] - b["mean"]))
    results["agreement"] = {
        "mean_abs_diff_final": round(sum(diffs) / max(1, len(diffs)), 5),
        "max_abs_diff_final": round(max(diffs) if diffs else 0.0, 5),
    }
    return results


_CHECKS_CACHE: Dict[str, object] = {}


def checks(session: Session) -> dict:
    """Everything the page shows under "方法与校验"."""
    latest_versions = {
        k: max(x["version"] for x in entries)
        for k, entries in session.result_book.revisions.items()
    }
    key = session.config.fingerprint() + "|" + "|".join(
        f"{k}:{v}" for k, v in sorted(latest_versions.items())
    )
    if key in _CHECKS_CACHE:
        return _CHECKS_CACHE[key]
    ds = session.ds
    clean, resolved, versions, spec, obs = session.build()
    run_a = session.run("approx")
    run_w = session.run("window")
    spec_w = session.spec_for("window")
    drift = session.config.drift_sd

    one_step_idx = [2, 20, 60, len(obs) - 1]
    one_steps = [
        {**compare_one_step(spec, session.config, obs, i, drift_sd=drift), "index": i + 1}
        for i in one_step_idx
        if 0 <= i < len(obs)
    ]
    grad = gradient_check(spec, obs[-3:], session.config)
    truncation = window_truncation(spec, session.config, obs, len(obs))
    score_a = score_run(ds, spec, run_a).to_dict()
    score_w = score_run(ds, spec_w, run_w).to_dict()

    bound_members = ds.scenarios.get("bound_group", [])
    member_check = (
        member_indistinguishability(ds, spec, run_a, bound_members)
        if bound_members
        else {}
    )
    if member_check:
        member_check = {
            **member_check,
            "aggregate": _lineup_summary(ds, spec, run_a, "approx"),
            # The joint fit's own posterior covariance is the honest interval
            # for the trio; the diagonal one is kept for comparison.
            "aggregate_window": _lineup_summary(ds, spec_w, run_w, "window"),
        }
    iso_members = ds.scenarios.get("isolated_group", [])
    last_day = run_a.frames[-1].day
    iso_means = [run_a.frames[-1].post_mean[spec.index_of[p]] for p in iso_members]
    truth_iso = [truth_at(ds, p, last_day) for p in iso_members]
    shocks = []
    for pid, meta in ds.scenarios.get("shocks", {}).items():
        if meta["day"] >= len(ds.truth["skill"][pid]):
            continue
        shocks.append(
            {
                "player_id": pid,
                "name": ds.player_map()[pid].name,
                "day": meta["day"],
                "delta": meta["delta"],
                "approx": response_to_shock(ds, spec, run_a, pid, meta["day"], meta["delta"]),
                "window": response_to_shock(ds, spec_w, run_w, pid, meta["day"], meta["delta"]),
            }
        )
    absent = ds.scenarios.get("absent_player", {})
    absence = {}
    if absent:
        pid = absent["id"]
        i = spec.index_of[pid]
        relevant = [
            (
                f.day,
                f.post_sd[i],
                f.post_mean[i],
                truth_at(ds, pid, f.day),
            )
            for f in run_a.frames
            if absent["from"] - 10 <= f.day <= absent["until"] + 10
        ]
        absence = {
            "player_id": pid,
            "name": ds.player_map()[pid].name,
            "from": absent["from"],
            "until": absent["until"],
            "series": [
                {"day": d, "sd": round(sd, 4), "mean": round(m, 4), "true": round(t, 4)}
                for d, sd, m, t in relevant
            ],
        }
    payload = {
        "drift_assumption": drift,
        "drift_true": ds.drift_true,
        "one_step_checks": one_steps,
        "gradient_check": grad,
        "truncation": truncation,
        "scorecards": {"approx": score_a, "window": score_w},
        "bound_group": member_check,
        "isolated": {
            "members": iso_members,
            "estimated_mean": round(statistics.fmean(iso_means), 4),
            "true_mean": round(statistics.fmean(truth_iso), 4),
            "level_gap": round(statistics.fmean(iso_means) - statistics.fmean(truth_iso), 4),
        },
        "shocks": shocks,
        "absence": absence,
        "cost": {
            "approx_seconds": round(sum(f.seconds for f in run_a.frames), 4),
            "window_seconds": round(sum(f.seconds for f in run_w.frames), 4),
            "approx_per_match": round(
                sum(f.seconds for f in run_a.frames) / max(1, len(run_a.frames)), 6
            ),
            "window_per_match": round(
                sum(f.seconds for f in run_w.frames) / max(1, len(run_w.frames)), 6
            ),
        },
        "config": session.config.__dict__,
        "matches": len(obs),
    }
    _CHECKS_CACHE[key] = payload
    return payload


_SWEEP_CACHE: Dict[str, dict] = {}


def sweep(session: Session, method: str) -> dict:
    """How the drift assumption changes error and uncertainty.

    ``approx`` is cheap enough to sweep on a fine grid; the joint window fit
    is swept more coarsely because each point re-fit the whole season.
    """
    ds = session.ds
    clean, resolved, versions, spec, obs = session.build()
    key = method + "|" + "|".join(f"{k}:{v}" for k, v in sorted(versions.items()))
    key += "|" + session.config.fingerprint()
    if key in _SWEEP_CACHE:
        return _SWEEP_CACHE[key]
    drifts = (
        [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
        if method == "approx"
        else [0.0, 0.02, 0.04, 0.06, 0.08]
    )
    points = []
    for d in drifts:
        cfg = EngineConfig(**{**session.config.__dict__, "drift_sd": d})
        local_spec = ModelSpec([p.id for p in ds.players], obs, cfg)
        local_obs = [local_spec.obs_for(m) for m in resolved]
        run = build_run(
            ds, local_spec, cfg, method, resolved, local_obs, versions
        )
        card = score_run(ds, local_spec, run).to_dict()
        points.append(
            {
                "drift_sd": d,
                "rmse": round(card["rmse"], 5),
                "mae": round(card["mae"], 5),
                "coverage90": round(card["coverage90"], 4),
                "mean_sd": round(card["mean_sd"], 5),
                "settled_rmse": round(card["settled_rmse"], 5),
                "log_score": round(card["log_score"], 5),
                "seconds": round(sum(f.seconds for f in run.frames), 3),
            }
        )
    payload = {
        "method": method,
        "drift_true": ds.drift_true,
        "current": session.config.drift_sd,
        "points": points,
        "note": "每条记录只改变漂移假设，其余配置与数据相同。",
    }
    _SWEEP_CACHE[key] = payload
    return payload


def revisions(session: Session) -> dict:
    """Audit trail: current revision and how it was produced for each method."""
    out = {}
    for method in ("approx", "window"):
        run = session.run(method)
        out[method] = {
            "method": method,
            "revision": run.revision,
            "parent_revision": run.parent_revision,
            "replayed_from": run.replayed_from,
            "note": run.note,
            "history": run.history,
            "frames": len(run.frames),
            "created_at": run.created_at,
        }
    return out


def scenarios(session: Session) -> dict:
    ds = session.ds
    sc = ds.scenarios
    iso, bound = set(sc.get("isolated_group", [])), set(sc.get("bound_group", []))
    clean, resolved, versions, spec, obs = session.build()
    duplicate_ids = {
        d["id"]: d for d in build_diagnostics(ds, clean, session.config).duplicates
    }
    samples = {
        "isolated": [
            _match_payload(m, versions.get(m.id, 0))
            for m in resolved
            if set(m.side_a + m.side_b) & iso
        ][:8],
        "bound": [
            _match_payload(m, versions.get(m.id, 0))
            for m in resolved
            if set(m.side_a + m.side_b) & bound
        ][:8],
        "draw": [
            _match_payload(m, versions.get(m.id, 0)) for m in resolved if m.is_draw
        ][:8],
        "duplicate": [
            {
                **_match_payload(m, versions.get(m.id, 0)),
                "duplicate_of": m.duplicate_of,
                "batch": m.batch,
                "why": duplicate_ids.get(m.id, {}).get("reason", "重复导入"),
            }
            for m in ds.matches
            if "duplicate-batch" in m.tags or "double-import" in m.tags
        ][:8],
        "conflict": [
            {
                **_match_payload(m, versions.get(m.id, 0)),
                "batch": m.batch,
            }
            for m in ds.matches
            if "conflict" in m.tags
        ][:8],
    }
    return {
        "samples": samples,
        "scenarios": sc,
        "counts": {
            key: len(value)
            for key, value in samples.items()
        },
        "conflicts": build_diagnostics(ds, clean, session.config).conflicts,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Roster ratings local server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if not os.path.exists(SAMPLE):
        print("sample dataset missing; run scripts/build_sample.py first", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
