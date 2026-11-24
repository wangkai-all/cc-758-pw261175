// Panel 2: the two update rules side by side. Error against simulated truth,
// uncertainty calibration, per-match cost, and where the final answers differ.

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
import { barWithIntervals, lineChart, responsive, nearest } from "../charts.js";
import { showTooltip, hideTooltip } from "../tooltip.js";
import {
  ensureFrames,
  playerMeta,
  playerName,
  rerender,
  state,
  withBusy,
} from "../main.js";

const METHOD_LABEL = { approx: "逐场近似", window: "窗口联合" };
const METHOD_SLOT = { approx: 1, window: 2 };

export async function renderCompare(panel) {
  panel.append(el("div", { class: "empty", text: "正在汇总两种更新方式…" }));
  if (!state.compare) {
    await withBusy("汇总两种更新方式…", async () => {
      const [compare] = await Promise.all([
        api.get("/api/compare"),
        ensureFrames("approx"),
        ensureFrames("window"),
      ]);
      state.compare = compare;
    });
  }
  if (!state.compare) {
    panel.replaceChildren(el("div", { class: "empty", text: "汇总失败，请刷新重试" }));
    return;
  }
  panel.replaceChildren();
  const c = state.compare;
  const a = c.approx.scorecard;
  const w = c.window.scorecard;

  // ---- headline tiles -------------------------------------------------
  const truthNote = "对照生成时的模拟真值；越低越好";
  panel.append(
    el("div", { class: "stat-row" }, [
      stat("逐场近似 RMSE", fmt(a.rmse, 3), truthNote),
      stat("窗口联合 RMSE", fmt(w.rmse, 3), truthNote),
      stat(
        "90% 区间覆盖率",
        `${pct(a.coverage90, 1)} / ${pct(w.coverage90, 1)}`,
        "近似 / 窗口 · 目标 90%",
        true
      ),
      stat(
        "每场耗时",
        `${compactSeconds(c.approx.seconds_per_match)}`,
        `窗口 ${compactSeconds(c.window.seconds_per_match)}`,
        true
      ),
    ])
  );

  // ---- error over time -------------------------------------------------
  const errCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "每场结束时，全体选手的平均绝对误差" }),
      el("span", { class: "sub", text: "同一场比赛位置对比" }),
    ]),
    el("div", { class: "legend" }, [
      legendItem("逐场近似", "line", "var(--series-1)"),
      legendItem("窗口联合", "line", "var(--series-2)"),
    ]),
    el("div", { class: "chart-wrap", id: "err-chart" }),
    el("div", { id: "err-table" }),
    el("p", {
      class: "card-note",
      text: "按比赛顺序（非日期）排列。早期两者都受先验影响；随着信息积累，曲线整体下沉。逐场近似不会回头重算旧比赛，窗口联合则每场都重拟合最近 8 场。",
    }),
  ]);

  // ---- calibration + speed --------------------------------------------
  const calCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "校准与稳健性" }),
      el("span", { class: "sub", text: "区间是否真实覆盖、终局是否稳定" }),
    ]),
    el("div", { class: "chart-wrap", id: "cal-chart" }),
    el("div", { id: "cal-table" }),
    el("p", {
      class: "card-note",
      text: "覆盖率低于 90% 表示区间偏窄（过于自信）；高于 90% 表示偏保守。结算误差只看出场 ≥5 场的选手。",
    }),
  ]);

  const grid = el("div", { class: "grid-2" }, [
    el("div", { class: "panel" }, [errCard]),
    el("div", { class: "panel" }, [calCard]),
  ]);
  panel.append(grid);

  // ---- cost + agreement ----------------------------------------------
  const agreeCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "两种方式终局估计的差异" }),
      el("span", {
        class: "sub",
        text: `平均 |差| ${fmt(c.agreement.mean_abs_diff_final, 3)} · 最大 ${fmt(
          c.agreement.max_abs_diff_final,
          3
        )}`,
      }),
    ]),
    el("div", { class: "chart-wrap", id: "agree-chart" }),
    el("div", { id: "agree-table" }),
    el("p", {
      class: "card-note",
      text: "差异本身不代表谁错：窗口联合在每场都重算最近信息，近似只做一次局部更新。下一屏的校验给出对照真值的答案。",
    }),
  ]);
  panel.append(agreeCard);

  // ---- drift assumption sensitivity -----------------------------------
  const driftCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "漂移假设如何改变结论" }),
      el("span", { class: "sub", id: "drift-sub", text: "固定数据，只改一个假设" }),
    ]),
    el("div", { class: "legend" }, [
      legendItem("逐场近似", "line", "var(--series-1)"),
      legendItem("窗口联合", "line", "var(--series-2)"),
    ]),
    el("h4", { class: "chart-title", text: "误差：RMSE 对照真值" }),
    el("div", { class: "chart-wrap", id: "drift-chart" }),
    el("h4", { class: "chart-title", text: "不确定区间：90% 覆盖率（目标 0.90）" }),
    el("div", { class: "chart-wrap", id: "drift-cov-chart" }),
    el("div", { id: "drift-table" }),
    el("p", {
      class: "card-note",
      text: "每条曲线都把全部比赛重跑一遍，只改「每天能力随机游走的标准差」这一个假设。假设过小会让区间偏窄、跟不上真实起伏；过大则跟踪噪声。顶部滑杆改的是当前工作配置，这里的整条曲线用来判断它是否落在合理区间。",
    }),
  ]);
  panel.append(driftCard);

  drawError(errCard);
  drawCalibration(calCard, a, w, c);
  drawAgreement(agreeCard);
  drawDrift(driftCard);

  if (!state.frames.approx || !state.frames.window) {
    await withBusy("载入逐场细节…", async () => {
      await ensureFrames("approx");
      await ensureFrames("window");
      rerender();
    });
  }
}

function stat(label, value, foot, small = false) {
  return el("div", { class: "stat" }, [
    el("span", { class: "label", text: label }),
    el("span", {
      class: "value",
      style: small ? "font-size:16px;font-weight:620" : null,
      text: value,
    }),
    el("span", { class: "foot", text: foot }),
  ]);
}

function legendItem(label, kind, color) {
  return el("span", { class: "item" }, [
    el("span", {
      class: kind === "dash" ? "key-dash" : "key-line",
      style: kind === "dash" ? null : `background:${color}`,
    }),
    el("span", { text: label }),
  ]);
}

function drawError(card) {
  const host = card.querySelector("#err-chart");
  const series = ["approx", "window"].map((m) => ({
    points: state.compare[m].error_series.map((p) => ({ x: p.index, y: p.mae })),
    colorSlot: METHOD_SLOT[m],
    width: 2,
    endDot: true,
  }));
  let hover = null;
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    lineChart(width, {
      height: 280,
      series,
      xDomain: [1, state.compare.approx.error_series.length],
      yDomain: [0, 0.75],
      xLabel: "比赛序号",
      yLabel: "平均绝对误差",
      xFormat: (v) => `${Math.round(v)}`,
      yFormat: (v) => v.toFixed(2),
      zeroLine: false,
      hoverIndex: hover,
      onHover: (x, evt) => {
        hover = x === null ? null : Math.round(nearest(series[0].points, x)?.x ?? 1);
        drawError(card);
        if (hover !== null && evt) {
          const rows = series.map((s, i) => {
            const p = nearest(s.points, hover);
            const m = ["approx", "window"][i];
            return {
              color: `var(--series-${METHOD_SLOT[m]})`,
              label: METHOD_LABEL[m],
              value: fmt(p?.y, 4),
            };
          });
          showTooltip(evt.clientX, evt.clientY, () => ({
            head: `第 ${hover} 场`,
            rows,
          }));
        } else hideTooltip();
      },
      ariaLabel: "两种更新方式的逐场平均绝对误差",
    })
  );
  const tableHost = card.querySelector("#err-table");
  const rows = state.compare.approx.error_series.map((p, i) => ({
    index: p.index,
    day: p.day,
    approx: fmt(p.mae, 4),
    window: fmt(state.compare.window.error_series[i]?.mae, 4),
  }));
  tableHost.replaceChildren(
    tableToggle("查看逐场误差表", () =>
      dataTable(
        [
          { key: "index", label: "场次", numeric: true },
          { key: "day", label: "天", numeric: true },
          { key: "approx", label: "近似", numeric: true },
          { key: "window", label: "窗口", numeric: true },
        ],
        rows
      )
    )
  );
}

function drawCalibration(card, a, w, c) {
  const host = card.querySelector("#cal-chart");
  const rows = [
    {
      label: "近似 · RMSE",
      mean: a.rmse,
      lo: a.rmse,
      hi: a.rmse,
      colorSlot: 1,
      valueLabel: fmt(a.rmse, 3),
    },
    {
      label: "窗口 · RMSE",
      mean: w.rmse,
      lo: w.rmse,
      hi: w.rmse,
      colorSlot: 2,
      valueLabel: fmt(w.rmse, 3),
    },
    {
      label: "近似 · 覆盖率",
      mean: a.coverage90,
      lo: a.coverage90,
      hi: a.coverage90,
      colorSlot: 1,
      valueLabel: pct(a.coverage90),
    },
    {
      label: "窗口 · 覆盖率",
      mean: w.coverage90,
      lo: w.coverage90,
      hi: w.coverage90,
      colorSlot: 2,
      valueLabel: pct(w.coverage90),
    },
    {
      label: "近似 · 结算误差",
      mean: a.settled_rmse,
      lo: a.settled_rmse,
      hi: a.settled_rmse,
      colorSlot: 1,
      valueLabel: fmt(a.settled_rmse, 3),
    },
    {
      label: "窗口 · 结算误差",
      mean: w.settled_rmse,
      lo: w.settled_rmse,
      hi: w.settled_rmse,
      colorSlot: 2,
      valueLabel: fmt(w.settled_rmse, 3),
    },
  ];
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    barWithIntervals(width, {
      rows,
      xDomain: [0, 1],
      xFormat: (v) => v.toFixed(2),
      labelWidth: 118,
    })
  );
  const tableHost = card.querySelector("#cal-table");
  tableHost.replaceChildren(
    tableToggle("查看校准数据表", () =>
      dataTable(
        [
          { key: "name", label: "指标" },
          { key: "approx", label: "逐场近似", numeric: true },
          { key: "window", label: "窗口联合", numeric: true },
        ],
        [
          { name: "RMSE", approx: fmt(a.rmse, 4), window: fmt(w.rmse, 4) },
          { name: "MAE", approx: fmt(a.mae, 4), window: fmt(w.mae, 4) },
          {
            name: "90% 覆盖率",
            approx: pct(a.coverage90, 2),
            window: pct(w.coverage90, 2),
          },
          {
            name: "平均 σ",
            approx: fmt(a.mean_sd, 4),
            window: fmt(w.mean_sd, 4),
          },
          {
            name: "结算 RMSE",
            approx: fmt(a.settled_rmse, 4),
            window: fmt(w.settled_rmse, 4),
          },
          {
            name: "对数得分",
            approx: fmt(a.log_score, 4),
            window: fmt(w.log_score, 4),
          },
          {
            name: "偏差",
            approx: signed(a.bias, 4),
            window: signed(w.bias, 4),
          },
          {
            name: "总耗时",
            approx: compactSeconds(c.approx.seconds_total),
            window: compactSeconds(c.window.seconds_total),
          },
        ]
      )
    )
  );
}

function drawAgreement(card) {
  const host = card.querySelector("#agree-chart");
  const bundleA = state.frames.approx;
  const bundleW = state.frames.window;
  if (!bundleA || !bundleW) {
    host.replaceChildren(el("div", { class: "empty", text: "逐场数据载入中…" }));
    return;
  }
  // Mean |difference| between the two methods, frame by frame.
  const pts = [];
  bundleA.frames.forEach((f, i) => {
    const g = bundleW.frames[i];
    if (!g) return;
    let sum = 0;
    f.players.forEach((p, j) => {
      sum += Math.abs(p.mean - g.players[j].mean);
    });
    pts.push({ x: f.index, y: sum / f.players.length });
  });
  const lastA = bundleA.frames[bundleA.frames.length - 1];
  const lastW = bundleW.frames[bundleW.frames.length - 1];
  const finalDiffs = lastA.players.map((p, j) => ({
    id: p.id,
    diff: p.mean - lastW.players[j].mean,
    a: p.mean,
    w: lastW.players[j].mean,
  }));
  finalDiffs.sort((x, y) => Math.abs(y.diff) - Math.abs(x.diff));

  let hover = null;
  host._dispose?.();
  host._dispose = responsive(host, (width) => {
    const chart = lineChart(width, {
      height: 220,
      series: [{ points: pts, colorSlot: 3, width: 2 }],
      xDomain: [1, bundleA.frames.length],
      yDomain: [0, Math.max(0.06, Math.max(...pts.map((p) => p.y)) * 1.15)],
      xLabel: "比赛序号",
      yLabel: "终局前平均 |差|",
      xFormat: (v) => `${Math.round(v)}`,
      yFormat: (v) => v.toFixed(2),
      hoverIndex: hover,
      onHover: (x, evt) => {
        hover = x === null ? null : Math.round(nearest(pts, x)?.x ?? 1);
        drawAgreement(card);
        if (hover !== null && evt) {
          const p = nearest(pts, hover);
          showTooltip(evt.clientX, evt.clientY, () => ({
            head: `第 ${hover} 场`,
            rows: [{ color: "var(--series-3)", label: "平均 |差|", value: fmt(p?.y, 4) }],
          }));
        } else hideTooltip();
      },
      ariaLabel: "两种方式在同一时刻估计差异的变化",
    });
    return chart;
  });

  const tableHost = card.querySelector("#agree-table");
  tableHost.replaceChildren(
    tableToggle("查看终局差异前 10 名", () =>
      dataTable(
        [
          { key: "name", label: "选手" },
          { key: "a", label: "近似", numeric: true },
          { key: "w", label: "窗口", numeric: true },
          { key: "diff", label: "差", numeric: true },
        ],
        finalDiffs.slice(0, 10).map((d) => ({
          name: playerName(d.id),
          a: signed(d.a, 3),
          w: signed(d.w, 3),
          diff: signed(d.diff, 3),
        }))
      )
    )
  );
}

// The drift sweep re-runs the whole season at each grid point on the server,
// so it is loaded lazily and cached until the config or data version changes.
async function drawDrift(card) {
  if (!card.isConnected) return;
  const host = card.querySelector("#drift-chart");
  const sub = card.querySelector("#drift-sub");
  const tableHost = card.querySelector("#drift-table");
  if (!host) return;
  if (!state.sweep) state.sweep = {};
  // Hold the bucket: a drift change swaps `state.sweep` for null and we must
  // not resurrect a stale result into the new config.
  const bucket = state.sweep;
  const missing = ["approx", "window"].filter((m) => !bucket[m]);
  if (missing.length) {
    host.replaceChildren(
      el("div", {
        class: "empty",
        text: "正在按不同漂移假设重跑整个赛季…（窗口联合较慢）",
      })
    );
    await withBusy("按不同漂移假设重跑…", async () => {
      for (const method of missing) {
        bucket[method] = await api.get(`/api/sweep?method=${method}`);
        if (state.sweep !== bucket) return;
      }
    }).catch((err) => {
      host.replaceChildren(el("div", { class: "empty", text: `重跑失败：${err.message}` }));
    });
    if (!card.isConnected) return;
    if (state.sweep !== bucket) return;
  }
  const sa = state.sweep?.approx;
  const sw = state.sweep?.window;
  if (!sa || !sw) return;
  if (sub) {
    sub.textContent = `当前假设 ${fmt(sa.current, 3)} · 生成真值 ${fmt(
      sa.drift_true,
      3
    )} · 近似 ${sa.points.length} 点 / 窗口 ${sw.points.length} 点`;
  }

  const aPts = sa.points.map((p) => ({ x: p.drift_sd, y: p.rmse }));
  const wPts = sw.points.map((p) => ({ x: p.drift_sd, y: p.rmse }));
  const all = [...aPts, ...wPts];
  const yMax = Math.max(...all.map((p) => p.y));
  const yMin = Math.min(...all.map((p) => p.y));
  const pad = Math.max(0.005, (yMax - yMin) * 0.25);

  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    lineChart(width, {
      height: 240,
      series: [
        {
          points: aPts,
          colorSlot: 1,
          endLabel: "近似",
          endLabelSide: "left",
          endLabelDy: 15,
        },
        {
          points: wPts,
          colorSlot: 2,
          endLabel: "窗口",
          endLabelSide: "left",
          endLabelDy: -10,
        },
      ],
      xDomain: [0, Math.max(...all.map((p) => p.x))],
      yDomain: [yMin - pad, yMax + pad],
      xLabel: "漂移假设 σ/天",
      yLabel: "RMSE（对照真值）",
      xFormat: (v) => v.toFixed(2),
      yFormat: (v) => v.toFixed(3),
      vlines: [
        { x: sa.current, label: "当前" },
        { x: sa.drift_true, label: sa.drift_true === sa.current ? "" : "真值" },
      ],
      ariaLabel: "漂移假设对两种更新方式 RMSE 的影响",
    })
  );

  // Coverage: same x, different scale and job (calibration, not accuracy).
  const covHost = card.querySelector("#drift-cov-chart");
  const aCov = sa.points.map((p) => ({ x: p.drift_sd, y: p.coverage90 }));
  const wCov = sw.points.map((p) => ({ x: p.drift_sd, y: p.coverage90 }));
  const covAll = [...aCov, ...wCov];
  const cMin = Math.min(...covAll.map((p) => p.y), 0.9);
  const cMax = Math.max(...covAll.map((p) => p.y), 0.9);
  covHost._dispose?.();
  covHost._dispose = responsive(covHost, (width) =>
    lineChart(width, {
      height: 200,
      series: [
        { points: aCov, colorSlot: 1 },
        { points: wCov, colorSlot: 2 },
      ],
      xDomain: [0, Math.max(...covAll.map((p) => p.x))],
      yDomain: [Math.max(0, cMin - 0.05), Math.min(1, cMax + 0.05)],
      xLabel: "漂移假设 σ/天",
      yLabel: "90% 覆盖率",
      xFormat: (v) => v.toFixed(2),
      yFormat: (v) => v.toFixed(2),
      vlines: [
        { x: sa.current, label: "" },
        { x: sa.drift_true, label: "" },
      ],
      annotations: [{ x: 0, y: 0.9, label: "目标 0.90", dx: 4, dy: -6 }],
      ariaLabel: "漂移假设对两种更新方式区间覆盖率的影响",
    })
  );

  // Union of grid points: the two methods are swept at different densities.
  const drifts = [...new Set([...sa.points, ...sw.points].map((p) => p.drift_sd))].sort(
    (a, b) => a - b
  );
  const byDrift = (bundle, d) => bundle.points.find((p) => p.drift_sd === d) || null;
  tableHost.replaceChildren(
    tableToggle("查看漂移敏感性数据", () =>
      dataTable(
        [
          { key: "drift", label: "σ/天", numeric: true },
          { key: "aRmse", label: "近似 RMSE", numeric: true },
          { key: "aCov", label: "近似覆盖率", numeric: true },
          { key: "wRmse", label: "窗口 RMSE", numeric: true },
          { key: "wCov", label: "窗口覆盖率", numeric: true },
        ],
        drifts.map((d) => {
          const a = byDrift(sa, d);
          const w = byDrift(sw, d);
          return {
            drift: d.toFixed(2),
            aRmse: a ? fmt(a.rmse, 4) : "—",
            aCov: a ? pct(a.coverage90) : "—",
            wRmse: w ? fmt(w.rmse, 4) : "—",
            wCov: w ? pct(w.coverage90) : "—",
          };
        })
      )
    )
  );
}
