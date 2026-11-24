// Panel 7: what the data is, and what the importer did with it.

import { dataTable, el, fmt, pct } from "../ui.js";
import { playerName, rerender, state } from "../main.js";

export function renderData(panel) {
  const ds = state.boot.dataset;
  const diag = state.diagnostics;
  const truthMeta = state.boot.truth_meta;

  panel.append(
    el("div", { class: "stat-row" }, [
      stat("虚构比赛", `${state.boot.matches.length} 场`, `原始导入 ${ds.n_matches_imported ?? state.boot.matches.length} 行`),
      stat("选手", `${state.boot.players.length} 人`, "主池 + 孤岛组 + 绑定组"),
      stat("时间跨度", `${truthMeta?.days ?? 180} 天`, `种子 ${ds.seed}`),
      stat("真实漂移", fmt(ds.drift_true, 3), "每天每人的随机游走 σ"),
      stat("基础进球率", fmt(ds.base_goals, 2), "泊松模型的基准 λ"),
    ])
  );

  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: ds.title }),
        el("span", { class: "sub", text: `数据集 ${ds.id}` }),
      ]),
      el("p", { class: "card-note", text: ds.description }),
      el("dl", { class: "kv", style: "margin-top:10px" }, [
        el("dt", { text: "生成方式" }),
        el("dd", {
          text: "潜在能力按日做高斯随机游走；比分由两队上场选手的平均能力与阵容组合项决定泊松均值后抽样。",
        }),
        el("dt", { text: "为何可用真值校验" }),
        el("dd", {
          text: "真值由生成器保存，估计器从未读取；所有误差、覆盖率与响应速度都对照这份真值。",
        }),
        el("dt", { text: "数据来源" }),
        el("dd", { text: "全部本地生成，不连接任何真实赛事或投注平台。" }),
      ]),
    ])
  );

  // ---- counts / import audit ------------------------------------------
  const counts = [
    ["可分析比赛", state.boot.matches.length, "去重后"],
    ["重复行（剔除）", diag.duplicates.length, "同日同阵容同比分"],
    ["比分冲突（保留）", diag.conflicts.length, "同对阵不同比分"],
    ["平局", diag.draws.count, `占 ${pct(diag.draws.share)}`],
    ["未交手对", diag.unplayed_pairs.length, "同组内从未同场"],
    ["孤岛组", diag.isolated_groups.reduce((n, g) => n + g.players.length, 0), "从不与主池交手"],
    ["绑定阵容", diag.bound_lineups.length, "成员仅在该阵容出现"],
  ];
  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: "导入审计" }),
        el("span", { class: "sub", text: "每一行要么进入分析，要么在报告里留下记录" }),
      ]),
      dataTable(
        [
          { key: "item", label: "项目" },
          { key: "n", label: "数量", numeric: true },
          { key: "note", label: "说明" },
        ],
        counts.map(([item, n, note]) => ({ item, n, note }))
      ),
    ])
  );

  // ---- players table ---------------------------------------------------
  const players = [...state.boot.players];
  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: "选手与分组" }),
        el("span", { class: "sub", text: `${players.length} 人` }),
      ]),
      el(
        "div",
        { class: "table-view", style: "max-height:420px" },
        dataTable(
          [
            { key: "id", label: "编号" },
            { key: "name", label: "姓名" },
            { key: "club", label: "队伍" },
            { key: "role", label: "角色" },
          ],
          players.map((p) => ({
            id: p.id,
            name: p.name,
            club: p.club,
            role:
              p.note === "core"
                ? "主力"
                : p.note === "rotation"
                ? "轮换"
                : p.note === "isolated"
                ? "孤岛组"
                : p.note === "bound"
                ? "绑定组"
                : p.note,
          }))
        )
      ),
    ])
  );

  // ---- match table -----------------------------------------------------
  const matches = state.boot.matches;
  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: "比赛记录（生效版本）" }),
        el("span", { class: "sub", text: `${matches.length} 场 · 按日期排序` }),
      ]),
      el(
        "div",
        { class: "table-view", style: "max-height:420px" },
        dataTable(
          [
            { key: "id", label: "编号" },
            { key: "day", label: "天", numeric: true },
            { key: "a", label: "A 队" },
            { key: "score", label: "比分", numeric: true },
            { key: "b", label: "B 队" },
            { key: "tags", label: "标记" },
            { key: "v", label: "版本", numeric: true },
          ],
          matches.map((m) => ({
            id: m.id,
            day: m.day,
            a: m.side_a.map(playerName).join(" · "),
            score: `${m.score_a} : ${m.score_b}`,
            b: m.side_b.map(playerName).join(" · "),
            tags: (m.tags || []).join("、") || "—",
            v: m.result_version ?? 0,
          }))
        )
      ),
    ])
  );

  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: "复现方式" }),
        el("span", { class: "sub", text: "命令行" }),
      ]),
      el("pre", {
        class: "json-out",
        text: [
          "npm run sample     # 重新生成确定性虚构数据集（同一 seed 结果一致）",
          "npm run api        # 启动本地 JSON API：127.0.0.1:8765",
          "npm run dev        # 启动页面：127.0.0.1:5173（/api 代理到 8765）",
          "npm test           # 运行 Python 测试（引擎、重放、榜单、诊断）",
        ].join("\n"),
      }),
    ])
  );
}

function stat(label, value, foot) {
  return el("div", { class: "stat" }, [
    el("span", { class: "label", text: label }),
    el("span", { class: "value", text: value }),
    el("span", { class: "foot", text: foot }),
  ]);
}
