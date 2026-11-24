// Panel 3: validation against simulated truth. Exact small-window checks,
// gradient check, truncation sensitivity, response speed, absence, bound trio.

import { api } from "../api.js";
import {
  dataTable,
  el,
  fmt,
  pct,
  signed,
  tableToggle,
} from "../ui.js";
import { lineChart, responsive, nearest, barWithIntervals } from "../charts.js";
import { showTooltip, hideTooltip } from "../tooltip.js";
import { playerName, rerender, state, withBusy } from "../main.js";

const METHOD_LABEL = { approx: "逐场近似", window: "窗口联合" };

export async function renderValidate(panel) {
  panel.append(el("div", { class: "empty", text: "正在跑精确校验…（窗口拟合较慢）" }));
  if (!state.checks) {
    await withBusy("跑精确校验…", async () => {
      state.checks = await api.get("/api/checks");
    });
  }
  if (!state.checks) {
    panel.replaceChildren(el("div", { class: "empty", text: "校验失败，请刷新重试" }));
    return;
  }
  panel.replaceChildren();
  const ch = state.checks;

  // ---- method error, the answer the previous panel defers to ----------
  const scoreA = ch.scorecards.approx;
  const scoreW = ch.scorecards.window;
  const better = scoreA.rmse <= scoreW.rmse ? "approx" : "window";
  panel.append(
    el("div", { class: "stat-row" }, [
      stat("近似 RMSE", fmt(scoreA.rmse, 3), "对照真值"),
      stat("窗口 RMSE", fmt(scoreW.rmse, 3), "对照真值"),
      stat(
        "更接近真值",
        METHOD_LABEL[better],
        `差 ${fmt(Math.abs(scoreA.rmse - scoreW.rmse), 3)}`,
        true
      ),
      stat(
        "校验项",
        `${ch.one_step_checks.length + 1 + ch.truncation.comparisons.length}`,
        "一步对比 · 梯度 · 截断",
        true
      ),
    ])
  );

  panel.append(
    el("div", { class: "callout" }, [
      el("strong", {
        text: `本数据集上，逐场近似的 RMSE 比窗口联合低 ${fmt(
          scoreW.rmse - scoreA.rmse,
          3
        )}。`,
      }),
      el("span", {
        text:
          " 这是如实报告而非预设结论：漂移较强时，只做局部更新的近似对旧信息的累积更少；窗口联合的价值主要体现在每次都给出一致的联合后验，并在阵容共享信息时更可解释。两类误差都在下方用真值逐一核对。",
      }),
    ])
  );

  // ---- one-step exact comparison --------------------------------------
  const oneCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "同一场、同一先验：近似 vs 精确联合解" }),
      el("span", { class: "sub", text: "只差对角线近似本身" }),
    ]),
    el("div", { class: "chart-wrap", id: "one-chart" }),
    el("div", { id: "one-table" }),
    el("p", {
      class: "card-note",
      text: "在单场比赛上，联合模型可精确求解；对角线近似忽略参数间相关，因此差异给出该近似的直接误差量级（以 σ 计的 z 值最易解读）。",
    }),
  ]);

  // ---- gradient check --------------------------------------------------
  const grad = ch.gradient_check;
  const gradCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "联合拟合的梯度校验" }),
      el("span", { class: "sub", text: "解析梯度 vs 中心差分" }),
    ]),
    el("dl", { class: "kv" }, [
      el("dt", { text: "参数个数" }),
      el("dd", { text: String(grad.n_params) }),
      el("dt", { text: "抽查参数" }),
      el("dd", { text: String(grad.checked_params) }),
      el("dt", { text: "最大梯度误差" }),
      el("dd", { text: fmt(grad.max_gradient_error, 8) }),
      el("dt", { text: "收敛" }),
      el("dd", { text: grad.converged ? "是" : "否" }),
      el("dt", { text: "协方差不对称度" }),
      el("dd", { text: fmt(grad.cov_asymmetry, 12) }),
    ]),
    el("p", {
      class: "card-note",
      text: "误差为 0 表示解析导数与数值导数在机器精度内一致，说明拟合确实在解它所声称的目标。",
    }),
  ]);

  // ---- truncation ------------------------------------------------------
  const truncCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "窗口长度：多长才够？" }),
      el("span", { class: "sub", text: "同一终点、不同历史长度" }),
    ]),
    el("div", { class: "chart-wrap", id: "trunc-chart" }),
    el("div", { id: "trunc-table" }),
    el("p", { class: "card-note", text: ch.truncation.note }),
  ]);

  panel.append(
    el("div", { class: "grid-2" }, [
      el("div", { class: "panel" }, [oneCard, truncCard]),
      el("div", { class: "panel" }, [gradCard]),
    ])
  );

  // ---- shock response --------------------------------------------------
  const shockCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "已知状态突变的响应速度" }),
      el("span", { class: "sub", text: "按选手自己出场次数计时" }),
    ]),
    el("div", { class: "multiples", id: "shock-grid" }),
    el("div", { id: "shock-table" }),
    el("p", {
      class: "card-note",
      text: "每位选手在图中标注的日期发生一次真实能力跳变；两张小图分别给出两种方式估计值（实线）与真值（虚线）。半衰期按该选手自己的出场次数计，避免用日历天数误读为「反应慢」。",
    }),
  ]);

  // ---- absence ---------------------------------------------------------
  const absenceCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "长期缺席：不确定性只增不减" }),
      el("span", { class: "sub", id: "abs-sub", text: "" }),
    ]),
    el("div", { class: "chart-wrap", id: "abs-chart" }),
    el("div", { id: "abs-table" }),
    el("p", {
      class: "card-note",
      text: "缺席期间没有新证据，区间随漂移假设变宽而不是变窄；复出后重新收窄。若某个系统在缺席时给出更确定的分数，那是在编造信息。",
    }),
  ]);

  // ---- bound trio ------------------------------------------------------
  const agg = ch.bound_group.aggregate;
  const aggW = ch.bound_group.aggregate_window;
  const boundCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "同队绑定：个人不可分，只读阵容合计" }),
      el("span", { class: "sub", text: "成员个人水平无法识别" }),
    ]),
    el("dl", { class: "kv" }, [
      el("dt", { text: "阵容" }),
      el("dd", { text: ch.bound_group.members.map(playerName).join(" + ") }),
      el("dt", { text: "成员估计跨度" }),
      el("dd", { text: fmt(ch.bound_group.est_spread, 3) }),
      el("dt", { text: "成员真值跨度" }),
      el("dd", { text: fmt(ch.bound_group.true_spread, 3) }),
      el("dt", { text: "阵容合计 · 近似" }),
      el("dd", {
        text: `${signed(agg?.combined_mean)}  [90%: ${signed(agg?.lo90)} … ${signed(
          agg?.hi90
        )}]`,
      }),
      ...(aggW
        ? [
            el("dt", { text: "阵容合计 · 窗口" }),
            el("dd", {
              text: `${signed(aggW.combined_mean)}  [90%: ${signed(
                aggW.lo90
              )} … ${signed(aggW.hi90)}]`,
            }),
            el("dt", { text: "窗口 · 区间宽度" }),
            el("dd", {
              text: aggW.sd_diagonal
                ? `σ ${fmt(aggW.sd_diagonal, 3)} → ${fmt(
                    aggW.combined_sd,
                    3
                  )}（把方差直接相加 → 用联合协方差，${pct(
                    1 - aggW.combined_sd / aggW.sd_diagonal,
                    0
                  )} 更窄）`
                : "—",
            }),
          ]
        : []),
      el("dt", { text: "其中组合项" }),
      el("dd", {
        text: aggW
          ? `近似 ${signed(agg?.combo)} / 窗口 ${signed(aggW.combo)}（真值 ${signed(
              agg?.combo_true
            )}）`
          : `${signed(agg?.combo)}（真值 ${signed(agg?.combo_true)}）`,
      }),
    ]),
    el("div", { class: "callout critical", style: "margin-top:10px" }, [
      el("strong", { text: "为什么个人分数不可分？" }),
      el("span", {
        text:
          " 三人永远同时上场，任何「把功劳分给谁」的规则都同样符合数据。估计器给出相同的个人分数，并把差异计入组合项；应引用阵容合计而不是个人名次。",
      }),
    ]),
    ...(aggW?.covariance_used
      ? [
          el("div", { class: "callout", style: "margin-top:10px" }, [
            el("strong", { text: "为什么窗口的合计区间更窄？" }),
            el("span", {
              text:
                " 窗口联合会同时估计成员能力与组合项，用的是完整后验协方差。三人的能力与组合项彼此此消彼长（后验协方差为负），合计的不确定性小于各项方差之和；把方差直接相加就漏掉了这一项，于是给得偏宽。窄在这里不代表更自信，而是没有重复计入可以互相抵消的部分。",
            }),
          ]),
        ]
      : []),
    el("div", { id: "bound-table" }),
  ]);

  // ---- isolated group --------------------------------------------------
  const iso = ch.isolated;
  const isoCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "完全未交手的群组：跨组不可比" }),
      el("span", { class: "sub", text: "组内可排序，组间只靠先验对齐" }),
    ]),
    el("dl", { class: "kv" }, [
      el("dt", { text: "成员" }),
      el("dd", { text: iso.members.map(playerName).join("、") }),
      el("dt", { text: "组均值（估计）" }),
      el("dd", { text: signed(iso.estimated_mean) }),
      el("dt", { text: "组均值（真值）" }),
      el("dd", { text: signed(iso.true_mean) }),
      el("dt", { text: "水平偏差" }),
      el("dd", { text: signed(iso.level_gap) }),
    ]),
    el("p", {
      class: "card-note",
      text: "与主池没有共同对手，所以两组之间的绝对水平没有数据支撑，仅由先验基准决定。模型把这类群组单列提示，避免把跨组分数当成可信比较。",
    }),
  ]);

  panel.append(
    el("div", { class: "grid-equal" }, [
      el("div", { class: "panel" }, [shockCard]),
      el("div", { class: "panel" }, [absenceCard, boundCard, isoCard]),
    ])
  );

  drawOneStep(oneCard, ch);
  drawTruncation(truncCard, ch);
  drawShocks(shockCard, ch);
  drawAbsence(absenceCard, ch);
  drawBoundTable(boundCard, ch);
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

function drawOneStep(card, ch) {
  const host = card.querySelector("#one-chart");
  const rows = ch.one_step_checks.map((c) => ({
    label: `第 ${c.index} 场`,
    mean: c.max_abs_diff,
    lo: 0,
    hi: c.max_abs_diff,
    colorSlot: 1,
    valueLabel: fmt(c.max_abs_diff, 3),
  })).concat(
    ch.one_step_checks.map((c) => ({
      label: `第 ${c.index} 场 · max z`,
      mean: c.max_z,
      lo: 0,
      hi: c.max_z,
      colorSlot: 3,
      valueLabel: fmt(c.max_z, 2),
    }))
  );
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    barWithIntervals(width, {
      rows,
      xDomain: [0, Math.max(0.05, ...rows.map((r) => r.hi)) * 1.1],
      xFormat: (v) => v.toFixed(2),
      labelWidth: 128,
    })
  );
  const tableHost = card.querySelector("#one-table");
  tableHost.replaceChildren(
    tableToggle("查看一步对比数据", () =>
      dataTable(
        [
          { key: "match", label: "比赛" },
          { key: "players", label: "上场", numeric: true },
          { key: "maxdiff", label: "最大差", numeric: true },
          { key: "rms", label: "RMS 差", numeric: true },
          { key: "maxz", label: "最大 z", numeric: true },
        ],
        ch.one_step_checks.map((c) => ({
          match: c.match_id,
          players: c.n_players,
          maxdiff: fmt(c.max_abs_diff, 4),
          rms: fmt(c.rms_diff, 4),
          maxz: fmt(c.max_z, 3),
        }))
      )
    )
  );
}

function drawTruncation(card, ch) {
  const host = card.querySelector("#trunc-chart");
  const comps = ch.truncation.comparisons;
  const rows = comps.map((c) => ({
    label: `${c.history_matches} 场历史`,
    mean: c.rms_diff_vs_window || 0,
    lo: 0,
    hi: c.rms_diff_vs_window || 0,
    colorSlot: c.reference ? 3 : 1,
    valueLabel: c.reference ? "基准" : fmt(c.rms_diff_vs_window, 3),
    dim: c.reference,
  }));
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    barWithIntervals(width, {
      rows,
      xDomain: [0, Math.max(0.15, ...rows.map((r) => r.hi)) * 1.15],
      xFormat: (v) => v.toFixed(2),
      labelWidth: 100,
    })
  );
  const tableHost = card.querySelector("#trunc-table");
  tableHost.replaceChildren(
    tableToggle("查看截断稳定性数据", () =>
      dataTable(
        [
          { key: "n", label: "历史场数", numeric: true },
          { key: "rms", label: "RMS 差", numeric: true },
          { key: "max", label: "最大差", numeric: true },
        ],
        comps.map((c) => ({
          n: c.history_matches,
          rms: c.reference ? "基准" : fmt(c.rms_diff_vs_window, 4),
          max: c.reference ? "基准" : fmt(c.max_abs_diff_vs_window, 4),
        }))
      )
    )
  );
}

function drawShocks(card, ch) {
  const grid = card.querySelector("#shock-grid");
  const tableHost = card.querySelector("#shock-table");
  const usable = ch.shocks.filter((s) => s.approx.available);
  if (!usable.length) {
    grid.replaceChildren(el("div", { class: "empty", text: "没有可展示的突变样例" }));
    return;
  }
  const cards = usable.map((s) => {
    const series = s.approx.series || [];
    const days = series.map((h) => h.day);
    const xDomain = [Math.min(...days), Math.max(...days)];
    const truthSeries = { points: [], colorSlot: 2 };
    // Truth at each of the player's games, taken from the frame payload.
    const bundle = state.frames[state.method];
    const truthAt = new Map();
    if (bundle) {
      for (const f of bundle.frames) {
        const p = f.players.find((x) => x.id === s.player_id);
        if (p) truthAt.set(f.day, p.true);
      }
    }
    truthSeries.points = series.map((h) => ({ x: h.day, y: truthAt.get(h.day) ?? null }));
    const estSeries = {
      points: series.map((h) => ({ x: h.day, y: h.est })),
      colorSlot: 1,
    };
    return el("figure", { class: "small-multiple" }, [
      el("figcaption", {}, [
        el("span", { class: "sm-title", text: s.name }),
        el("span", {
          class: "sm-sub",
          text: `第 ${s.day} 天突变 ${signed(s.delta)} · 半衰 ${s.approx.half_absorption_game ?? "—"} 场`,
        }),
      ]),
      el("div", { class: "mini-chart", dataset: { pid: s.player_id } }),
    ]);
  });
  grid.replaceChildren(...cards);
  // Render each mini chart after insertion so widths are known.
  requestAnimationFrame(() => {
    usable.forEach((s) => {
      const node = grid.querySelector(`[data-pid="${s.player_id}"]`);
      if (!node) return;
      const series = s.approx.series || [];
      const bundle = state.frames[state.method];
      const truthAt = new Map();
      if (bundle) {
        for (const f of bundle.frames) {
          const p = f.players.find((x) => x.id === s.player_id);
          if (p) truthAt.set(f.day, p.true);
        }
      }
      const days = series.map((h) => h.day);
      node._dispose?.();
      node._dispose = responsive(node, (width) =>
        lineChart(width, {
          height: 132,
          series: [
            {
              points: series.map((h) => ({ x: h.day, y: truthAt.get(h.day) ?? null })),
              colorSlot: 2,
              dashed: true,
              endDot: false,
            },
            { points: series.map((h) => ({ x: h.day, y: h.est })), colorSlot: 1 },
          ],
          xDomain: [Math.min(...days), Math.max(...days)],
          yDomain: [-0.3, 1.7],
          vlines: [{ x: s.day, label: "" }],
          yFormat: (v) => v.toFixed(1),
          xFormat: (v) => `${Math.round(v)}`,
          height: 132,
          ariaLabel: `${s.name} 的状态突变响应`,
        })
      );
    });
  });
  tableHost.replaceChildren(
    tableToggle("查看突变响应数据", () =>
      dataTable(
        [
          { key: "name", label: "选手" },
          { key: "day", label: "突变日", numeric: true },
          { key: "delta", label: "真值变化", numeric: true },
          { key: "aHalf", label: "近似半衰(场)", numeric: true },
          { key: "wHalf", label: "窗口半衰(场)", numeric: true },
        ],
        ch.shocks.map((s) => ({
          name: s.name,
          day: s.day,
          delta: signed(s.delta),
          aHalf: s.approx.available ? s.approx.half_absorption_game ?? "未达半" : "—",
          wHalf: s.window.available ? s.window.half_absorption_game ?? "未达半" : "—",
        }))
      )
    )
  );
}

function drawAbsence(card, ch) {
  const host = card.querySelector("#abs-chart");
  const sub = card.querySelector("#abs-sub");
  const abs = ch.absence;
  if (!abs || !abs.series?.length) {
    host.replaceChildren(el("div", { class: "empty", text: "没有缺席样例" }));
    return;
  }
  if (sub) {
    sub.textContent = `${abs.name} · 第 ${abs.from}–${abs.until} 天无比赛`;
  }
  // The API returns one entry per match; keep one per distinct day.
  const byDay = new Map();
  for (const s of abs.series) {
    const prev = byDay.get(s.day);
    if (!prev || s.sd > prev.sd) byDay.set(s.day, s);
  }
  const pts = [...byDay.values()].sort((a, b) => a.day - b.day);
  host._dispose?.();
  host._dispose = responsive(host, (width) =>
    lineChart(width, {
      height: 250,
      series: [
        { points: pts.map((p) => ({ x: p.day, y: p.true })), colorSlot: 2, dashed: true, endDot: false },
        { points: pts.map((p) => ({ x: p.day, y: p.mean })), colorSlot: 1 },
      ],
      bands: [
        {
          points: pts.map((p) => ({ x: p.day, lo: p.mean - 1.645 * p.sd, hi: p.mean + 1.645 * p.sd })),
          colorSlot: 1,
        },
      ],
      xDomain: [Math.min(...pts.map((p) => p.day)), Math.max(...pts.map((p) => p.day))],
      yDomain: [-1.1, 1.1],
      vlines: [
        { x: abs.from, label: "缺席开始" },
        { x: abs.until, label: "复出" },
      ],
      zeroLine: true,
      xLabel: "日期（天）",
      yLabel: "能力评分",
      xFormat: (v) => `${Math.round(v)}`,
      ariaLabel: `${abs.name} 缺席期间的估计与区间`,
    })
  );
  const tableHost = card.querySelector("#abs-table");
  tableHost.replaceChildren(
    tableToggle("查看缺席期间数据", () =>
      dataTable(
        [
          { key: "day", label: "天", numeric: true },
          { key: "est", label: "估计", numeric: true },
          { key: "sd", label: "σ", numeric: true },
          { key: "lo", label: "下限", numeric: true },
          { key: "hi", label: "上限", numeric: true },
          { key: "truth", label: "真值", numeric: true },
        ],
        pts.map((p) => ({
          day: p.day,
          est: signed(p.mean, 3),
          sd: fmt(p.sd, 4),
          lo: signed(p.mean - 1.645 * p.sd, 3),
          hi: signed(p.mean + 1.645 * p.sd, 3),
          truth: signed(p.true, 3),
        }))
      )
    )
  );
}

function drawBoundTable(card, ch) {
  const host = card.querySelector("#bound-table");
  const b = ch.bound_group;
  const a = b.aggregate;
  const w = b.aggregate_window;
  const rows = [
    { metric: "阵容合计", approx: a ? signed(a.combined_mean) : "—", window: w ? signed(w.combined_mean) : "—" },
    { metric: "区间半宽（90%）", approx: a ? fmt(1.645 * a.combined_sd, 3) : "—", window: w ? fmt(1.645 * w.combined_sd, 3) : "—" },
    { metric: "组合项", approx: a ? signed(a.combo) : "—", window: w ? signed(w.combo) : "—" },
    { metric: "组合项真值", approx: a ? signed(a.combo_true) : "—", window: w ? signed(w.combo_true) : "—" },
  ];
  host.replaceChildren(
    tableToggle("查看绑定成员与合计", () =>
      el("div", {}, [
        dataTable(
          [
            { key: "name", label: "成员" },
            { key: "role", label: "说明" },
          ],
          b.members.map((m) => ({ name: playerName(m), role: "与其余成员永远同队" }))
        ),
        dataTable(
          [
            { key: "metric", label: "指标" },
            { key: "approx", label: "逐场近似", numeric: true },
            { key: "window", label: "窗口联合", numeric: true },
          ],
          rows
        ),
      ])
    )
  );
}
