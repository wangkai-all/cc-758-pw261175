// Panel 1: match-by-match replay. Move the cursor, read the pre-match
// forecast and the post-match update, and see the selected player's whole
// trajectory with its uncertainty band against the simulated truth.

import { api } from "../api.js";
import {
  compactSeconds,
  dataTable,
  el,
  fmt,
  pct,
  signed,
  tableToggle,
} from "../ui.js";
import { lineChart, responsive, nearest } from "../charts.js";
import { showTooltip, hideTooltip } from "../tooltip.js";
import {
  currentFrame,
  ensureFrames,
  frameCount,
  playerMeta,
  playerName,
  rerender,
  state,
  teamLabel,
  withBusy,
} from "../main.js";

let matchIndex = null;
let bootRef = null;

function matchesByIndex() {
  if (matchIndex && bootRef === state.boot) return matchIndex;
  bootRef = state.boot;
  matchIndex = new Map();
  for (const m of state.boot.matches) matchIndex.set(m.id, m);
  return matchIndex;
}

function outcomeLabel(goalsA, goalsB) {
  if (goalsA === goalsB) return "平局";
  return goalsA > goalsB ? "A 队胜" : "B 队胜";
}

function teamChips(ids, flags) {
  return ids.map((pid) => {
    const tag = flags.get(pid);
    return el("span", {
      class: `chip${tag === "isolated" ? "" : ""}`,
      title: tag ? `${playerName(pid)} · ${tag}` : playerName(pid),
      text: playerName(pid) + (tag === "bound" ? " ·绑" : tag === "isolated" ? " ·孤" : ""),
    });
  });
}

function tagFlags() {
  const flags = new Map();
  for (const p of state.boot.players) {
    if (p.club === "孤岛组") flags.set(p.id, "isolated");
    else if (p.club === "绑定三人组") flags.set(p.id, "bound");
  }
  return flags;
}

export function renderReplay(panel) {
  const flags = tagFlags();
  const total = frameCount();
  const bundle = state.frames[state.method];
  const matches = matchesByIndex();

  const last = state.cursor || total;
  const slider = el("input", {
    type: "range",
    min: "1",
    max: String(total),
    step: "1",
    value: String(last),
    "aria-label": "比赛进度",
  });
  const sliderValue = el("span", {
    class: "mono-value",
    text: `${last}/${total}`,
  });

  const playBtn = el("button", {
    class: "secondary",
    type: "button",
    text: "▶ 播放",
  });
  let timer = null;
  const stop = () => {
    if (timer) {
      clearInterval(timer);
      timer = null;
      playBtn.textContent = "▶ 播放";
    }
  };
  playBtn.addEventListener("click", () => {
    if (timer) {
      stop();
      return;
    }
    if (state.cursor >= total) state.cursor = 0;
    playBtn.textContent = "⏸ 暂停";
    timer = setInterval(() => {
      if (state.cursor >= total) {
        stop();
        return;
      }
      state.cursor += 1;
      slider.value = String(state.cursor);
      paint();
    }, 160);
  });

  const prevBtn = el("button", {
    class: "icon-button",
    type: "button",
    text: "← 上一场",
    onclick: () => {
      stop();
      setCursor(state.cursor - 1);
    },
  });
  const nextBtn = el("button", {
    class: "icon-button",
    type: "button",
    text: "下一场 →",
    onclick: () => {
      stop();
      setCursor(state.cursor + 1);
    },
  });

  const sliderWrap = el("div", { class: "group grow" }, [
    el("label", { text: "进度", for: "replay-slider" }),
    slider,
    sliderValue,
  ]);
  slider.id = "replay-slider";

  const truthToggle = el("input", {
    type: "checkbox",
    id: "show-truth",
    checked: state.showTruth ? true : null,
    onchange: (evt) => {
      state.showTruth = evt.target.checked;
      paint();
    },
  });
  const controls = el("div", { class: "controls" }, [
    prevBtn,
    nextBtn,
    playBtn,
    sliderWrap,
    el("div", { class: "group" }, [
      el("label", { text: "本场日期" }),
      el("span", { class: "mono-value", id: "replay-day", text: "—" }),
    ]),
    el("div", { class: "group" }, [
      truthToggle,
      el("label", { for: "show-truth", text: "显示模拟真值" }),
    ]),
  ]);

  const stats = el("div", { class: "stat-row", id: "replay-stats" });
  const matchCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "本场比赛" }),
      el("span", { class: "sub", id: "replay-match-sub", text: "" }),
    ]),
    el("div", { id: "replay-match-body" }),
  ]);

  const trajCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "个人轨迹与不确定区间" }),
      el("span", { class: "sub", id: "traj-sub", text: "" }),
    ]),
    el("div", { id: "traj-legend", class: "legend" }),
    el("div", { class: "chart-wrap", id: "traj-chart" }),
    el("div", { id: "traj-table" }),
    el("p", {
      class: "card-note",
      text: "阴影为 90% 区间（±1.645σ）。竖线是当前回放位置；底部刻度为该选手的出场。",
    }),
  ]);

  const rankCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "此刻的评分" }),
      el("span", { class: "sub", id: "rank-sub", text: "" }),
    ]),
    el("div", { id: "rank-list", class: "rank-list" }),
  ]);

  const columnA = el("div", { class: "panel" }, [matchCard, trajCard]);
  const columnB = el("div", { class: "panel" }, [rankCard]);
  panel.append(controls, stats, el("div", { class: "grid-2" }, [columnA, columnB]));

  // -- painting ---------------------------------------------------------

  let hoverX = null;

  function setCursor(value) {
    const v = Math.max(1, Math.min(total, value));
    if (v === state.cursor) return;
    state.cursor = v;
    slider.value = String(v);
    paint();
  }

  slider.addEventListener("input", () => {
    stop();
    const v = Number(slider.value);
    state.cursor = v;
    paint();
  });

  function paint() {
    const frame = currentFrame();
    if (!frame) return;
    sliderValue.textContent = `${state.cursor}/${total}`;
    const dayEl = document.getElementById("replay-day");
    if (dayEl) dayEl.textContent = `第 ${frame.day} 天`;

    paintStats(frame, bundle);
    paintMatch(frame, matches);
    paintTrajectory(frame);
    paintRanking(frame, flags);
  }

  function paintStats(frame, bundle) {
    const match = matches.get(frame.match_id);
    const players = frame.players;
    const active = new Set(match ? [...match.side_a, ...match.side_b] : []);
    const rated = players.filter((p) => active.has(p.id));
    const avgSd = rated.length
      ? rated.reduce((s, p) => s + p.sd, 0) / rated.length
      : 0;
    const correctSide =
      frame.goals_a === frame.goals_b
        ? "draw"
        : frame.goals_a > frame.goals_b
        ? "a"
        : "b";
    const pActual =
      correctSide === "a" ? frame.p_a : correctSide === "draw" ? frame.p_draw : frame.p_b;
    const maxP = Math.max(frame.p_a, frame.p_draw, frame.p_b);
    stats.replaceChildren(
      tile("已处理比赛", `${frame.index}`, `/ ${total} 场`),
      tile("本场日期", `第 ${frame.day} 天`, `第 ${frame.index} 场`),
      tile(
        "赛前预测",
        `${pct(frame.p_a, 0)} / ${pct(frame.p_draw, 0)} / ${pct(frame.p_b, 0)}`,
        "A 胜 / 平 / B 胜",
        true
      ),
      tile(
        "命中概率",
        pct(pActual, 1),
        `实际：${outcomeLabel(frame.goals_a, frame.goals_b)}`
      ),
      tile(
        "本场更新耗时",
        compactSeconds(frame.seconds),
        state.method === "approx" ? "逐场近似" : "窗口联合拟合"
      ),
      tile(
        "上场选手平均 σ",
        fmt(avgSd, 3),
        `${rated.length} 人上场`
      ),
      tile(
        "预测置信",
        pct(maxP, 1),
        maxP > 0.55 ? "倾向明确" : "接近五五开"
      )
    );
  }

  function paintMatch(frame, matches) {
    const match = matches.get(frame.match_id);
    const sub = document.getElementById("replay-match-sub");
    if (sub) {
      sub.textContent = `${frame.match_id} · ${match?.batch || ""}${
        frame.result_version ? ` · 结果版本 v${frame.result_version}` : ""
      }`;
    }
    const body = document.getElementById("replay-match-body");
    if (!body) return;
    if (!match) {
      body.replaceChildren(el("div", { class: "empty", text: "找不到本场记录" }));
      return;
    }
    const pre = new Map(frame.players.map((p) => [p.id, p]));
    const sideRow = (ids, cls) =>
      el("div", { class: `side ${cls}` }, teamChips(ids, flags));
    const probs = el("div", { class: "probs" }, [
      el("span", { text: `A 胜 ${pct(frame.p_a)}` }),
      el("span", { text: `平 ${pct(frame.p_draw)}` }),
      el("span", { text: `B 胜 ${pct(frame.p_b)}` }),
    ]);
    const scoreline = el("div", { class: "scoreline" }, [
      sideRow(match.side_a, "a"),
      el("span", { class: "score", text: `${frame.goals_a} : ${frame.goals_b}` }),
      sideRow(match.side_b, "b"),
    ]);
    const tags = [];
    if (match.tags.includes("isolated"))
      tags.push(el("span", { class: "chip", text: "孤岛组" }));
    if (match.tags.includes("bound"))
      tags.push(el("span", { class: "chip", text: "绑定阵容" }));
    if (match.tags.includes("conflict"))
      tags.push(el("span", { class: "chip crit", text: "比分冲突行" }));
    const line = el("div", { class: "match-card" }, [
      el("div", { class: "mc-head" }, [
        el("span", { text: `第 ${frame.day} 天` }),
        el("span", { class: "mc-head-right" }, tags),
      ]),
      scoreline,
      probs,
    ]);

    const rows = [...match.side_a, ...match.side_b].map((pid) => {
      const p = pre.get(pid);
      return {
        who: playerName(pid),
        side: match.side_a.includes(pid) ? "A" : "B",
        pre: p ? signed(p.pre_mean, 2) : "—",
        post: p ? signed(p.mean, 2) : "—",
        sd: p ? fmt(p.sd, 3) : "—",
        true: p ? signed(p.true, 2) : "—",
      };
    });

    body.replaceChildren(
      line,
      el("div", { class: "section-title", text: "上场选手：赛前 → 赛后" }),
      tableToggle("查看本场选手表", () =>
        dataTable(
          [
            { key: "who", label: "选手" },
            { key: "side", label: "队" },
            { key: "pre", label: "赛前", numeric: true },
            { key: "post", label: "赛后", numeric: true },
            { key: "sd", label: "σ", numeric: true },
            { key: "true", label: "真值", numeric: true },
          ],
          rows
        )
      ),
      el("div", { class: "card-note", text: "点击右侧榜单中的选手可切换轨迹。" })
    );
  }

  function paintTrajectory(frame) {
    const pid = state.selectedPlayer;
    const sub = document.getElementById("traj-sub");
    const meta = playerMeta(pid);
    if (sub) {
      sub.textContent = meta
        ? `${meta.name} · ${meta.club} · ${meta.note === "core" ? "主力" : meta.note === "rotation" ? "轮换" : meta.note}`
        : "";
    }

    const seriesPts = [];
    const bandPts = [];
    const games = [];
    for (const f of bundle.frames) {
      const p = f.players.find((x) => x.id === pid);
      if (!p) continue;
      seriesPts.push({ x: f.day, y: p.mean, frame: f, p });
      bandPts.push({ x: f.day, lo: p.mean - 1.645 * p.sd, hi: p.mean + 1.645 * p.sd });
      const m = matches.get(f.match_id);
      if (m && (m.side_a.includes(pid) || m.side_b.includes(pid))) {
        games.push(f.day);
      }
    }
    const truthPts = seriesPts.map((p) => ({ x: p.x, y: p.p.true }));
    const days = bundle.frames.map((f) => f.day);
    const xDomain = [Math.min(...days), Math.max(...days)];

    const legend = document.getElementById("traj-legend");
    if (legend) {
      legend.replaceChildren(
        el("span", { class: "item" }, [
          el("span", { class: "key-line" }),
          el("span", { text: "赛后估计" }),
        ]),
        el("span", { class: "item" }, [
          el("span", {
            class: "key-box",
            style: "background:var(--series-1);opacity:.22;border-radius:2px",
          }),
          el("span", { text: "90% 区间" }),
        ]),
        state.showTruth
          ? el("span", { class: "item" }, [
              el("span", { class: "key-dash" }),
              el("span", { text: "模拟真值" }),
            ])
          : null,
        el("span", { class: "item" }, [
          el("span", { class: "key-line", style: "background:var(--text-muted)" }),
          el("span", { text: "当前回放位置" }),
        ])
      );
    }

    const tipAt = (clientX, clientY) => {
      for (const p of seriesPts) {
        if (p.x !== hoverX) continue;
        showTooltip(clientX, clientY, () => ({
          head: `第 ${p.frame.day} 天 · ${p.frame.match_id}`,
          rows: [
            {
              color: "var(--series-1)",
              label: "赛后估计",
              value: `${signed(p.p.mean)} ± ${fmt(1.645 * p.p.sd)}`,
            },
            {
              color: "var(--series-2)",
              label: "模拟真值",
              value: signed(p.p.true),
            },
            {
              color: "var(--text-muted)",
              label: "赛前估计",
              value: signed(p.p.pre_mean),
            },
          ],
          note: p.p.sd > 0.48 ? "区间较宽：近期出场少或信息不足" : undefined,
        }));
        return;
      }
      hideTooltip();
    };

    const hostEl = document.getElementById("traj-chart");
    if (!hostEl) return;
    if (hostEl._dispose) hostEl._dispose();
    hostEl._dispose = responsive(hostEl, (width) => {
      const chart = lineChart(width, {
        height: 300,
        series: [
          ...(state.showTruth
            ? [
                {
                  points: truthPts,
                  colorSlot: 2,
                  dashed: true,
                  width: 2,
                  endDot: false,
                },
              ]
            : []),
          {
            points: seriesPts,
            colorSlot: 1,
            width: 2,
            gameMarks: games,
            endLabel: meta?.name,
            endLabelSide: "left",
          },
        ],
        bands: [{ points: bandPts, colorSlot: 1 }],
        xDomain,
        yDomain: [-1.15, 1.15],
        xLabel: "日期（天）",
        yLabel: "能力评分（对数进球强度）",
        xFormat: (v) => `${v}`,
        yFormat: (v) => v.toFixed(2),
        zeroLine: true,
        vlines: [
          {
            x: frame.day,
            label: `第 ${frame.index} 场`,
          },
        ],
        hoverIndex: hoverX,
        onHover: (x, evt) => {
          const nextX = x === null ? null : nearest(seriesPts, x)?.x ?? null;
          if (nextX === hoverX) {
            if (evt) tipAt(evt.clientX, evt.clientY);
            else hideTooltip();
            return;
          }
          hoverX = nextX;
          paintTrajectory(frame);
          if (hoverX !== null && evt) tipAt(evt.clientX, evt.clientY);
          else hideTooltip();
        },
        ariaLabel: `${meta?.name || pid} 的能力估计随日期变化，含 90% 区间`,
      });
      return chart;
    });

    const tableHost = document.getElementById("traj-table");
    if (tableHost) {
      tableHost.replaceChildren(
        tableToggle("查看该选手数据表", () =>
          dataTable(
            [
              { key: "day", label: "天" },
              { key: "match", label: "比赛" },
              { key: "est", label: "估计", numeric: true },
              { key: "lo", label: "下限", numeric: true },
              { key: "hi", label: "上限", numeric: true },
              { key: "truth", label: "真值", numeric: true },
              { key: "err", label: "误差", numeric: true },
            ],
            seriesPts.map((p) => ({
              day: p.frame.day,
              match: p.frame.match_id,
              est: fmt(p.p.mean, 3),
              lo: fmt(p.p.mean - 1.645 * p.p.sd, 3),
              hi: fmt(p.p.mean + 1.645 * p.p.sd, 3),
              truth: fmt(p.p.true, 3),
              err: signed(p.p.mean - p.p.true, 3),
            }))
          )
        )
      );
    }
  }

  function paintRanking(frame, flags) {
    const list = document.getElementById("rank-list");
    const sub = document.getElementById("rank-sub");
    if (!list) return;
    const ordered = [...frame.players].sort((a, b) => b.mean - a.mean);
    if (sub) sub.textContent = `第 ${frame.index} 场后 · ${ordered.length} 人`;
    const rows = ordered.map((p, i) => {
      const meta = playerMeta(p.id);
      const flag = flags.get(p.id);
      return el(
        "div",
        {
          class: "rank-row",
          role: "button",
          tabindex: "0",
          "aria-selected": String(p.id === state.selectedPlayer),
          onclick: () => {
            state.selectedPlayer = p.id;
            paint();
          },
          onkeydown: (evt) => {
            if (evt.key === "Enter" || evt.key === " ") {
              evt.preventDefault();
              state.selectedPlayer = p.id;
              paint();
            }
          },
        },
        [
          el("span", { class: "pos", text: String(i + 1) }),
          el("span", { class: "who" }, [
            el("div", { class: "name" }, [
              p.id === state.selectedPlayer ? "● " : "",
              playerName(p.id),
              flag
                ? el("span", {
                    class: "flag",
                    text: flag === "isolated" ? "孤岛" : "绑定",
                  })
                : null,
            ]),
            el("div", {
              class: "meta",
              text: `${meta?.club || ""} · ${meta?.note === "core" ? "主力" : meta?.note === "rotation" ? "轮换" : meta?.note || ""}`,
            }),
          ]),
          el("span", { class: "score" }, [
            el("div", { class: "m", text: signed(p.mean, 2) }),
            el("div", { class: "ci", text: `±${fmt(1.645 * p.sd, 2)}` }),
          ]),
        ]
      );
    });
    const rankedAfter = frame.players.reduce((acc, p) => (p.sd < 0.47 ? acc + 1 : acc), 0);
    list.replaceChildren(
      ...rows,
      el("div", {
        class: "card-note",
        text: `其中 ${rankedAfter} 人的不确定区间已明显收窄（σ<0.47）；其余人要么出场少，要么正处缺席。`,
      })
    );
  }

  // Deep link from other panels: select a player and jump to a match.
  if (state.pendingSelect) {
    state.cursor = Math.min(total, Math.max(1, state.pendingSelect.index || state.cursor));
    state.selectedPlayer = state.pendingSelect.pid || state.selectedPlayer;
    state.pendingSelect = null;
    slider.value = String(state.cursor);
  }

  paint();
  // Re-render the trajectory when the method's data arrives later.
  if (!state.frames[state.method]) {
    withBusy("载入逐场细节…", async () => {
      await ensureFrames(state.method);
      rerender();
    });
  }
}

function tile(label, value, foot, small = false) {
  return el("div", { class: "stat" }, [
    el("span", { class: "label", text: label }),
    small
      ? el("span", { class: "value", style: "font-size:16px;font-weight:600", text: value })
      : el("span", { class: "value", text: value }),
    el("span", { class: "foot", text: foot }),
  ]);
}
