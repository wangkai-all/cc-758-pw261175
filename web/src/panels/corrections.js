// Panel 4: correct an early result. The old run and every published board are
// kept; the new revision is replayed from the newest checkpoint at or before
// the corrected match, and the page shows exactly what moved.

import { api } from "../api.js";
import {
  dataTable,
  downloadJson,
  el,
  fmt,
  signed,
  tableToggle,
} from "../ui.js";
import { lineChart, responsive } from "../charts.js";
import {
  invalidateAll,
  playerName,
  rerender,
  state,
  teamLabel,
  withBusy,
} from "../main.js";

export async function renderCorrections(panel) {
  const matches = state.boot.matches;
  const lastReport = state.correctionReport || null;

  // ---- form ------------------------------------------------------------
  const select = el(
    "select",
    { id: "corr-match", "aria-label": "选择要更正的比赛" },
    matches.map((m) =>
      el("option", {
        value: m.id,
        text: `第 ${m.day} 天 · ${m.id} · ${teamLabel(m.side_a)} ${m.score_a}:${m.score_b} ${teamLabel(m.side_b)}`,
      })
    )
  );
  const scoreA = el("input", { type: "number", min: "0", max: "30", value: "0", id: "corr-a" });
  const scoreB = el("input", { type: "number", min: "0", max: "30", value: "0", id: "corr-b" });
  const reason = el("input", {
    type: "text",
    value: "录像复核",
    id: "corr-reason",
    placeholder: "更正原因",
  });
  const preview = el("div", { class: "card-note", id: "corr-preview" });

  const applyPreview = () => {
    const m = matches.find((x) => x.id === select.value);
    if (!m) return;
    scoreA.value = String(m.score_a);
    scoreB.value = String(m.score_b);
    preview.textContent = `当前：${teamLabel(m.side_a)} ${m.score_a} : ${m.score_b} ${teamLabel(
      m.side_b
    )} · 第 ${m.day} 天 · 结果版本 v${m.result_version ?? 0}`;
  };
  select.addEventListener("change", applyPreview);
  applyPreview();

  const submit = el("button", {
    class: "primary",
    type: "button",
    text: "记录更正并从检查点重放",
    onclick: async () => {
      const payload = {
        match_id: select.value,
        score_a: Number(scoreA.value),
        score_b: Number(scoreB.value),
        reason: reason.value || "人工更正",
      };
      if (!Number.isInteger(payload.score_a) || payload.score_a < 0) {
        setFormError("A 队比分需为非负整数");
        return;
      }
      if (!Number.isInteger(payload.score_b) || payload.score_b < 0) {
        setFormError("B 队比分需为非负整数");
        return;
      }
      submit.disabled = true;
      try {
        const report = await withBusy("更正后重放…", () => api.post("/api/correct", payload));
        state.correctionReport = report;
        invalidateAll();
        rerender();
      } catch (err) {
        setFormError(err.message);
      } finally {
        submit.disabled = false;
      }
    },
  });
  const errBox = el("div", { class: "card-note", id: "corr-error", style: "color:var(--status-critical)" });
  const setFormError = (msg) => {
    errBox.textContent = msg;
  };

  const form = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "更正一场早期赛果" }),
      el("span", { class: "sub", text: "不修改历史，只追加结果版本" }),
    ]),
    el("div", { class: "form-grid" }, [
      el("div", { class: "field", style: "grid-column:1/-1" }, [
        el("label", { for: "corr-match", text: "比赛" }),
        select,
      ]),
      el("div", { class: "field" }, [
        el("label", { for: "corr-a", text: "A 队比分" }),
        scoreA,
      ]),
      el("div", { class: "field" }, [
        el("label", { for: "corr-b", text: "B 队比分" }),
        scoreB,
      ]),
      el("div", { class: "field" }, [
        el("label", { for: "corr-reason", text: "原因" }),
        reason,
      ]),
      el("div", { class: "field", style: "justify-self:start" }, [submit]),
    ]),
    preview,
    errBox,
    el("p", {
      class: "card-note",
      text: "重放会从该场之前最近的检查点恢复过滤器状态，重算其后的全部比赛，生成新的运行版本。已发布的榜单是只追加的记录，仍保留原赛果下的分数。",
    }),
  ]);

  panel.append(form);

  // ---- report ----------------------------------------------------------
  if (lastReport) {
    panel.append(reportCard(lastReport, matches));
  } else {
    panel.append(
      el("div", { class: "card" }, [
        el("div", { class: "card-head" }, [
          el("h3", { text: "还没有本轮更正" }),
          el("span", { class: "sub", text: "上面提交后会在此显示重放报告" }),
        ]),
        el("p", {
          class: "card-note",
          text: "建议先到「发布榜单」发布一版基线，再回来更正一场早期比赛，即可看到旧榜单逐字节不变、新版本另存的效果。",
        }),
      ])
    );
  }

  // ---- audit trail -----------------------------------------------------
  const revCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "运行版本与血缘" }),
      el("span", { class: "sub", text: "每次更正产生一个子版本" }),
    ]),
    el("div", { id: "rev-body" }, el("div", { class: "empty", text: "载入中…" })),
    el("div", { class: "card-note", style: "margin-top:10px" }, [
      el("button", {
        class: "destructive",
        type: "button",
        text: "清除本地更正与发布记录",
        onclick: async () => {
          const ok = window.confirm(
            "将删除本项目写出的结果版本、运行缓存和已发布榜单（不会动生成的比赛数据）。确定继续？"
          );
          if (!ok) return;
          try {
            const res = await withBusy("清除本地记录…", () => api.post("/api/reset", {}));
            state.correctionReport = null;
            invalidateAll();
            rerender();
            window.alert(`已清理：${res.removed.join("、") || "无文件"}`);
          } catch (err) {
            window.alert(`清理失败：${err.message}`);
          }
        },
      }),
    ]),
  ]);
  panel.append(revCard);
  loadRevisions(revCard);

  // ---- result book -----------------------------------------------------
  panel.append(resultBookCard(matches));
}

async function loadRevisions(card) {
  const host = card.querySelector("#rev-body");
  if (!host) return;
  try {
    if (!state.revisions) state.revisions = await api.get("/api/revisions");
    const rev = state.revisions;
    const items = [];
    for (const method of ["approx", "window"]) {
      const r = rev[method];
      const history = r.history || [];
      items.push(
        el("div", { class: "revision-item" }, [
          el("div", { class: "rev-head" }, [
            el("strong", { text: method === "approx" ? "逐场近似" : "窗口联合" }),
            el("span", { class: "badge info", text: `当前 r${r.revision}` }),
            r.replayed_from !== null && r.replayed_from !== undefined
              ? el("span", { class: "badge", text: `从检查点 ${r.replayed_from} 重放` })
              : null,
            el("span", { class: "sub", text: `${r.frames} 场` }),
          ]),
          history.length
            ? el(
                "div",
                { class: "timeline" },
                history
                  .slice()
                  .reverse()
                  .map((h) =>
                    el("div", { class: "timeline-row" }, [
                      el("span", { class: "when", text: h.corrected_match || "—" }),
                      el("span", {
                        text: `更正为 ${h.corrected_score?.join(" : ")} · 从检查点 ${h.checkpoint_used} 起重放 ${h.replayed_matches} 场`,
                      }),
                      el("span", { class: "badge", text: `r${h.revision}→` }),
                    ])
                  )
              )
            : el("div", { class: "card-note", text: "尚未发生更正。" }),
        ])
      );
    }
    host.replaceChildren(el("div", { class: "revision-list" }, items));
  } catch (err) {
    host.replaceChildren(el("div", { class: "empty", text: `读取版本失败：${err.message}` }));
  }
}

function reportCard(report, matches) {
  const cards = Object.entries(report.reports || {}).map(([method, r]) => {
    const d = r.downstream_changes || {};
    const top = (d.top_moves || []).slice(0, 6);
    return el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: `${method === "approx" ? "逐场近似" : "窗口联合"} · 重放报告` }),
        el("span", { class: "badge info", text: `r${r.revision}` }),
      ]),
      el("dl", { class: "kv" }, [
        el("dt", { text: "使用的检查点" }),
        el("dd", { text: `第 ${r.checkpoint_used} 场之后` }),
        el("dt", { text: "重放比赛数" }),
        el("dd", { text: `${r.replayed_matches} / ${r.total_matches}` }),
        el("dt", { text: "终局最大变化" }),
        el("dd", { text: signed(d.final_max_abs_delta, 3) }),
        el("dt", { text: "终局受影响人数" }),
        el("dd", { text: String(d.final_changed_players ?? 0) }),
      ]),
      el("div", { class: "section-title", text: "终局变化最大的选手" }),
      el(
        "div",
        { class: "rank-list" },
        top.map((m) =>
          el(
            "div",
            {
              class: "rank-row",
              role: "button",
              tabindex: "0",
              onclick: () => jumpTo(m.player_id, r.marks?.index),
              onkeydown: (evt) => {
                if (evt.key === "Enter") jumpTo(m.player_id, r.marks?.index);
              },
            },
            [
              el("span", { class: "pos", text: "" }),
              el("span", { class: "who" }, [
                el("div", { class: "name", text: playerName(m.player_id) }),
                el("div", {
                  class: "meta",
                  text: `${signed(m.before, 3)} → ${signed(m.after, 3)}`,
                }),
              ]),
              el("span", { class: "score" }, [
                el("div", { class: "m", text: signed(m.delta, 3) }),
              ]),
            ]
          )
        )
      ),
      el("div", { id: `corr-chart-${method}`, class: "chart-wrap" }),
      el("div", { id: `corr-table-${method}` }),
      el("p", {
        class: "card-note",
        text: `更正前：${r.marks?.old_score?.join(" : ")} → 更正后：${r.marks?.new_score?.join(
          " : "
        )}。检查点之前的帧保持不变。`,
      }),
    ]);
  });

  const ruler = el(
    "div",
    { class: "callout" },
    [
      el("strong", { text: "更正已记录。" }),
      el("span", {
        text: ` ${report.match_id} 由 v${report.old_version}（${report.old_score.join(
          " : "
        )}）改为 v${report.new_version}（${report.new_score.join(" : ")}），位于第 ${
          report.corrected_index + 1
        } 场。旧运行版本与已发布榜单均未改动。`,
      }),
    ]
  );

  const wrap = el("div", { class: "panel" }, [ruler, ...cards]);
  // Draw after insertion so widths are known.
  requestAnimationFrame(() => {
    for (const [method, r] of Object.entries(report.reports || {})) {
      drawRevisionDiff(method, r);
    }
  });
  return wrap;
}

function jumpTo(pid, index) {
  state.pendingSelect = { pid, index };
  state.tab = "replay";
  rerender();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function drawRevisionDiff(method, r) {
  const host = document.getElementById(`corr-chart-${method}`);
  if (!host) return;
  const topId = r.downstream_changes?.top_moves?.[0]?.player_id;
  const traj = topId ? r.trajectories?.[topId] : null;
  const days = r.marks?.days || [];
  if (!traj || !days.length) {
    host.replaceChildren(el("div", { class: "empty", text: "没有可画的轨迹" }));
    return;
  }
  const before = traj.before.map((y, i) => ({ x: days[i], y }));
  const after = traj.after.map((y, i) => ({ x: days[i], y }));
  document.getElementById(`corr-legend-${method}`)?.remove();
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    lineChart(width, {
      height: 230,
      series: [
        { points: before, colorSlot: 2, width: 2, dashed: true, endDot: false },
        { points: after, colorSlot: 1, width: 2 },
      ],
      xDomain: [Math.min(...days), Math.max(...days)],
      yDomain: [
        Math.min(...traj.before, ...traj.after) - 0.08,
        Math.max(...traj.before, ...traj.after) + 0.08,
      ],
      vlines: [{ x: r.marks.day, label: "更正处" }],
      xLabel: "日期（天）",
      yLabel: `${playerName(topId)} 的估计`,
      xFormat: (v) => `${Math.round(v)}`,
      ariaLabel: "更正前后该选手的轨迹",
    })
  );
  const legend = el("div", { class: "legend", id: `corr-legend-${method}` }, [
    el("span", { class: "item" }, [
      el("span", { class: "key-dash" }),
      el("span", { text: "更正前（原运行）" }),
    ]),
    el("span", { class: "item" }, [
      el("span", { class: "key-line" }),
      el("span", { text: "更正后（重放）" }),
    ]),
  ]);
  host.before(legend);
  const tableHost = document.getElementById(`corr-table-${method}`);
  if (tableHost) {
    tableHost.replaceChildren(
      tableToggle("查看更正前后差异表", () =>
        dataTable(
          [
            { key: "who", label: "选手" },
            { key: "before", label: "更正前", numeric: true },
            { key: "after", label: "更正后", numeric: true },
            { key: "delta", label: "变化", numeric: true },
          ],
          (r.downstream_changes?.top_moves || []).map((m) => ({
            who: playerName(m.player_id),
            before: signed(m.before, 4),
            after: signed(m.after, 4),
            delta: signed(m.delta, 4),
          }))
        )
      )
    );
  }
}

function resultBookCard(matches) {
  const corrected = matches.filter((m) => (m.result_version ?? 0) > 0);
  const card = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "当前生效的更正" }),
      el("span", { class: "sub", text: "结果版本仅追加，不覆盖原行" }),
    ]),
  ]);
  if (!corrected.length) {
    card.append(
      el("div", { class: "empty", text: "本会话还没有生效的更正（原赛果即 v0）。" })
    );
    return card;
  }
  card.append(
    dataTable(
      [
        { key: "file", label: "比赛" },
        { key: "day", label: "天", numeric: true },
        { key: "score", label: "生效比分", numeric: true },
        { key: "version", label: "版本", numeric: true },
      ],
      corrected.map((m) => ({
        file: m.id,
        day: m.day,
        score: `${m.score_a} : ${m.score_b}`,
        version: `v${m.result_version}`,
      }))
    ),
    el("div", { class: "card-note", style: "margin-top:8px" }, [
      el("button", {
        class: "link-button",
        type: "button",
        text: "导出当前生效赛果 JSON",
        onclick: () =>
          downloadJson("result-book.json", {
            dataset: state.boot.dataset.id,
            matches: corrected,
          }),
      }),
    ])
  );
  return card;
}
