// Panel 6: the planted scenarios. Each one is a concrete case where a naive
// fixed-points table would mislead, paired with what the model actually does.

import { api } from "../api.js";
import { dataTable, el, fmt, pct, signed } from "../ui.js";
import { invalidateAll, playerName, rerender, state, teamLabel, withBusy } from "../main.js";

export async function renderScenarios(panel) {
  if (!state.scenarios) {
    await withBusy("载入情景样例…", async () => {
      state.scenarios = await api.get("/api/scenarios");
    });
  }
  const data = state.scenarios;
  const diag = state.diagnostics;
  const samples = data.samples;
  const flags = scenarioFlags();

  // ---- overview tiles --------------------------------------------------
  panel.append(
    el("div", { class: "stat-row" }, [
      stat("孤岛组", `${data.counts.isolated} 场样例`, "4 人，从不与主池交手"),
      stat("绑定三人组", `${data.counts.bound} 场样例`, "三人总是一起上场"),
      stat("平局", `${diag.draws.count} 场`, `占 ${pct(diag.draws.share)}`),
      stat("重复导入", `${diag.duplicates.length} 行`, "已剔除，保留报告"),
      stat("比分冲突", `${diag.conflicts.length} 组`, "保留两行，提示人工确认"),
    ])
  );

  // ---- warnings --------------------------------------------------------
  if (diag.warnings?.length) {
    panel.append(
      el("div", { class: "callout" }, [
        el("strong", { text: "数据质量提示" }),
        el(
          "ul",
          {},
          diag.warnings.map((w) => el("li", { text: w }))
        ),
      ])
    );
  }

  const grid = el("div", { class: "grid-equal" });
  panel.append(grid);

  // ---- isolated --------------------------------------------------------
  grid.append(
    sectionCard({
      title: "完全未交手的群组",
      sub: "组内可排序；跨组分数没有共同证据",
      body: [
        el("p", {
          class: "card-note",
          text: "孤岛组的每场比赛都只在这 4 人之间进行，与主池没有任何共同对手。下面列出样例比赛与组内终局分数——它们彼此可比，但不能与主池选手直接比较高低。",
        }),
        el("div", { class: "section-title", text: "样例比赛" }),
        matchList(samples.isolated, flags),
        el("div", { class: "section-title", text: "组内终局" }),
        el("div", { id: "iso-standings" }, el("div", { class: "empty", text: "计算中…" })),
      ],
      action: el("button", {
        class: "link-button",
        type: "button",
        text: "在回放中查看孤岛组",
        onclick: () => {
          const p = state.boot.players.find((x) => x.club === "孤岛组");
          state.pendingSelect = { pid: p?.id, index: state.cursor };
          state.tab = "replay";
          rerender();
          window.scrollTo({ top: 0, behavior: "smooth" });
        },
      }),
    })
  );

  // ---- bound -----------------------------------------------------------
  const agg = state.checks?.bound_group?.aggregate;
  grid.append(
    sectionCard({
      title: "同队绑定：贡献不可分",
      sub: "个人分数相同是正确结果，不是缺陷",
      body: [
        el("p", {
          class: "card-note",
          text: `绑定的 ${diag.bound_lineups[0]?.names.join(" + ") || "三人"} 总是同队出场，模型无法把功劳分给任何一人，于是给出相同的个人分数并把组合效应单独记一项。对外只能引用阵容合计。`,
        }),
        agg
          ? el("dl", { class: "kv" }, [
              el("dt", { text: "阵容合计" }),
              el("dd", {
                text: `${signed(agg.combined_mean)} [${signed(agg.lo90)}, ${signed(agg.hi90)}]`,
              }),
              el("dt", { text: "组合项（估计/真值）" }),
              el("dd", { text: `${signed(agg.combo)} / ${signed(agg.combo_true)}` }),
              el("dt", { text: "成员个人分数跨度" }),
              el("dd", { text: fmt(state.checks.bound_group.est_spread, 3) }),
            ])
          : el("div", { class: "card-note", text: "打开「校验」页可看到完整数值。" }),
        el("div", { class: "section-title", text: "样例比赛" }),
        matchList(samples.bound, flags),
      ],
    })
  );

  // ---- draws -----------------------------------------------------------
  grid.append(
    sectionCard({
      title: "平局是正常结果",
      sub: "两队各进相同球数；不强行分胜负",
      body: [
        el("p", {
          class: "card-note",
          text: `本赛季 ${diag.draws.count} 场平局（${pct(
            diag.draws.share
          )}）。模型用两个独立的泊松计数生成比分，平局自然出现在双方进球数相等时，赛前概率也给出「平」这一档。`,
        }),
        el("div", { class: "section-title", text: "样例比赛" }),
        matchList(samples.draw, flags),
      ],
    })
  );

  // ---- duplicates ------------------------------------------------------
  grid.append(
    sectionCard({
      title: "重复导入与比分冲突",
      sub: "指纹去重；冲突保留并提示",
      body: [
        el("p", {
          class: "card-note",
          text: "同一天、同一阵容、同比分的行按指纹判重，只保留第一次出现；同一对阵出现不同比分的行不会被静默合并，而是两行都保留并标记待人工确认。",
        }),
        el("div", { class: "section-title", text: "被判为重复的行" }),
        dataTable(
          [
            { key: "id", label: "行" },
            { key: "of", label: "重复自" },
            { key: "day", label: "天", numeric: true },
            { key: "why", label: "判定依据" },
          ],
          (diag.duplicates || []).map((d) => ({
            id: d.id,
            of: d.duplicate_of || "—",
            day: d.day,
            why: d.reason,
          }))
        ),
        el("div", { class: "section-title", text: "比分冲突" }),
        dataTable(
          [
            { key: "id", label: "行" },
            { key: "with", label: "对阵" },
            { key: "scores", label: "两个比分" },
            { key: "day", label: "天", numeric: true },
          ],
          (diag.conflicts || []).map((c) => ({
            id: c.id,
            with: c.conflict_with,
            scores: c.scores.map((s) => s.join(":")).join(" / "),
            day: c.day,
          }))
        ),
        el("p", {
          class: "card-note",
          text: "要修正其中一行，可在「更正与重放」里提交正确比分；原行与已发布榜单都不会被改写。",
        }),
      ],
    })
  );

  // ---- isolated standings (uses the current replay's last frame) --------
  drawIsolatedStandings();
}

function stat(label, value, foot) {
  return el("div", { class: "stat" }, [
    el("span", { class: "label", text: label }),
    el("span", { class: "value", style: "font-size:18px;font-weight:640", text: value }),
    el("span", { class: "foot", text: foot }),
  ]);
}

function sectionCard({ title, sub, body, action }) {
  return el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: title }),
      el("span", { class: "sub", text: sub }),
    ]),
    ...body,
    action ? el("div", { style: "margin-top:8px" }, [action]) : null,
  ]);
}

function scenarioFlags() {
  const flags = new Map();
  for (const p of state.boot.players) {
    if (p.club === "孤岛组") flags.set(p.id, "isolated");
    else if (p.club === "绑定三人组") flags.set(p.id, "bound");
  }
  return flags;
}

function matchList(matches, flags) {
  if (!matches?.length) return el("div", { class: "empty", text: "没有样例" });
  return el(
    "div",
    { class: "revision-list" },
    matches.slice(0, 6).map((m) =>
      el("div", { class: "match-card" }, [
        el("div", { class: "mc-head" }, [
          el("span", { text: `${m.id} · 第 ${m.day} 天` }),
          el("span", {}, [
            m.result_version
              ? el("span", { class: "chip", text: `结果 v${m.result_version}` })
              : null,
          ]),
        ]),
        el("div", { class: "scoreline" }, [
          el(
            "div",
            { class: "side a" },
            m.side_a.map((p) => el("span", { class: "chip", text: playerName(p) }))
          ),
          el("span", { class: "score", text: `${m.score_a} : ${m.score_b}` }),
          el(
            "div",
            { class: "side b" },
            m.side_b.map((p) => el("span", { class: "chip", text: playerName(p) }))
          ),
        ]),
      ])
    )
  );
}

function drawIsolatedStandings() {
  const host = document.getElementById("iso-standings");
  const bundle = state.frames[state.method];
  if (!host || !bundle) return;
  const iso = new Set(state.boot.scenarios.isolated_group);
  const last = bundle.frames[bundle.frames.length - 1];
  const rows = last.players
    .filter((p) => iso.has(p.id))
    .sort((a, b) => b.mean - a.mean)
    .map((p, i) => ({
      pos: i + 1,
      name: playerName(p.id),
      mean: signed(p.mean, 3),
      interval: `${signed(p.mean - 1.645 * p.sd, 2)} … ${signed(p.mean + 1.645 * p.sd, 2)}`,
      truth: signed(p.true, 3),
    }));
  host.replaceChildren(
    dataTable(
      [
        { key: "pos", label: "#", numeric: true },
        { key: "name", label: "选手" },
        { key: "mean", label: "组内均值", numeric: true },
        { key: "interval", label: "90% 区间", numeric: true },
        { key: "truth", label: "真值", numeric: true },
      ],
      rows
    ),
    el("p", {
      class: "card-note",
      text: "组内排序可信；这 4 人的分数不能直接与主池比较——跨组只由先验基准对齐。",
    })
  );
}
