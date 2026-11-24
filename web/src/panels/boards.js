// Panel 5: publish a board, browse versions. Older versions are byte-identical
// to the day they were published, corrections never rewrite them.

import { api } from "../api.js";
import { dataTable, el, fmt, signed, tableToggle } from "../ui.js";
import { invalidateAll, playerName, rerender, state, withBusy } from "../main.js";

export async function renderBoards(panel) {
  await loadBoards();

  const label = el("input", {
    type: "text",
    value: defaultLabel(),
    id: "board-label",
    "aria-label": "榜单标签",
  });
  const note = el("input", {
    type: "text",
    value: "",
    placeholder: "备注（可选）",
    "aria-label": "备注",
  });
  const methodSelect = el(
    "select",
    { "aria-label": "发布方式" },
    [
      ["approx", "逐场近似"],
      ["window", "窗口联合"],
    ].map(([v, t]) =>
      el("option", {
        value: v,
        text: t,
        selected: v === state.method ? true : null,
      })
    )
  );

  const publishBtn = el("button", {
    class: "primary",
    type: "button",
    text: "发布当前榜单",
    onclick: async () => {
      publishBtn.disabled = true;
      try {
        const board = await withBusy("发布榜单…", () =>
          api.post("/api/publish", {
            method: methodSelect.value,
            label: label.value || defaultLabel(),
            note: note.value,
          })
        );
        state.lastPublished = board;
        invalidateAll();
        rerender();
      } catch (err) {
        window.alert(`发布失败：${err.message}`);
      } finally {
        publishBtn.disabled = false;
      }
    },
  });

  panel.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: "发布一版榜单" }),
        el("span", { class: "sub", text: "只追加，不覆盖历史版本" }),
      ]),
      el("div", { class: "form-grid" }, [
        el("div", { class: "field" }, [
          el("label", { for: "board-label", text: "标签" }),
          label,
        ]),
        el("div", { class: "field" }, [
          el("label", { text: "更新方式" }),
          methodSelect,
        ]),
        el("div", { class: "field" }, [
          el("label", { text: "备注" }),
          note,
        ]),
        el("div", { class: "field", style: "justify-self:start" }, [publishBtn]),
      ]),
      el("p", {
        class: "card-note",
        text: "榜单记录发布时的配置指纹、生效的结果版本与每位选手的均值/区间。后续更正会产生新的运行版本，但已发布的行不会被改写。",
      }),
    ])
  );

  if (state.lastPublished) {
    panel.append(
      el("div", { class: "callout" }, [
        el("strong", { text: `已发布 v${state.lastPublished.version}：${state.lastPublished.label}。` }),
        el("span", {
          text: ` 共 ${state.lastPublished.rows.length} 位选手，处理 ${state.lastPublished.processed} 场，最后一天 ${state.lastPublished.last_day}。`,
        }),
      ])
    );
  }

  const boards = state.boards?.boards || [];
  if (!boards.length) {
    panel.append(
      el("div", { class: "card" }, [
        el("div", { class: "empty", text: "还没有发布任何榜单。先在上方发布一版基线。" }),
      ])
    );
    return;
  }

  // newest first
  const ordered = [...boards].sort((a, b) => b.version - a.version);
  const selected = state.selectedBoard || ordered[0].version;
  const detailHost = el("div", { id: "board-detail" });

  const listCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { text: "历史版本" }),
      el("span", { class: "sub", text: `${boards.length} 版 · 只读` }),
    ]),
    el(
      "div",
      { class: "rank-list" },
      ordered.map((b) =>
        el(
          "div",
          {
            class: "rank-row",
            role: "button",
            tabindex: "0",
            "aria-selected": String(b.version === selected),
            onclick: () => {
              state.selectedBoard = b.version;
              rerender();
            },
            onkeydown: (evt) => {
              if (evt.key === "Enter") {
                state.selectedBoard = b.version;
                rerender();
              }
            },
          },
          [
            el("span", { class: "pos", text: `v${b.version}` }),
            el("span", { class: "who" }, [
              el("div", { class: "name", text: b.label || "（无标签）" }),
              el("div", {
                class: "meta",
                text: `${b.method === "approx" ? "逐场近似" : "窗口联合"} · ${b.processed} 场 · 第 ${b.last_day} 天`,
              }),
            ]),
            el("span", { class: "score" }, [
              el("div", { class: "ci", text: formatTime(b.created_at) }),
            ]),
          ]
        )
      )
    ),
  ]);

  panel.append(el("div", { class: "grid-2" }, [
    el("div", { class: "panel" }, [detailHost]),
    el("div", { class: "panel" }, [listCard]),
  ]));
  loadBoardDetail(detailHost, state.selectedBoard || ordered[0].version, boards);
}

function defaultLabel() {
  const n = (state.boards?.boards?.length || 0) + 1;
  return `第 ${n} 版 · ${state.method === "approx" ? "逐场近似" : "窗口联合"}`;
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function loadBoards() {
  if (state.boards) return;
  try {
    state.boards = await api.get("/api/boards");
  } catch (err) {
    state.boards = { boards: [] };
  }
}

async function loadBoardDetail(host, version, list) {
  try {
    const board = await api.get(`/api/board?version=${version}`);
    const versions = Object.entries(board.result_versions || {}).filter(([, v]) => v > 0);
    const body = el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { text: `v${board.version} · ${board.label}` }),
        el("span", { class: "sub", text: formatTime(board.created_at) }),
      ]),
      el("dl", { class: "kv" }, [
        el("dt", { text: "更新方式" }),
        el("dd", { text: board.method === "approx" ? "逐场近似" : "窗口联合" }),
        el("dt", { text: "配置指纹" }),
        el("dd", { text: board.config_fingerprint, style: "font-family:var(--mono);font-size:11.5px" }),
        el("dt", { text: "处理场数" }),
        el("dd", { text: `${board.processed} 场 · 第 ${board.last_day} 天` }),
        el("dt", { text: "生效的结果版本" }),
        el("dd", {
          text: versions.length
            ? versions.map(([id, v]) => `${id}=v${v}`).join("，")
            : "全部为原始导入 v0",
        }),
      ]),
      board.note ? el("p", { class: "card-note", text: `备注：${board.note}` }) : null,
      el("div", { class: "section-title", text: "榜单（按均值降序）" }),
      dataTable(
        [
          { key: "pos", label: "#", numeric: true },
          { key: "name", label: "选手" },
          { key: "club", label: "队伍" },
          { key: "mean", label: "均值", numeric: true },
          { key: "lo", label: "90% 下限", numeric: true },
          { key: "hi", label: "90% 上限", numeric: true },
          { key: "plays", label: "出场", numeric: true },
        ],
        board.rows.map((r, i) => ({
          pos: i + 1,
          name: playerName(r.player_id),
          club: r.club,
          mean: signed(r.mean, 3),
          lo: signed(r.lo90, 3),
          hi: signed(r.hi90, 3),
          plays: r.plays,
        }))
      ),
      el("div", { class: "card-note", style: "margin-top:8px" }, [
        el("button", {
          class: "link-button",
          type: "button",
          text: "导出该版本 JSON",
          onclick: () => {
            const blob = new Blob([JSON.stringify(board, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = el("a", { href: url, download: `board-v${board.version}.json` });
            document.body.append(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          },
        }),
      ]),
      el("p", {
        class: "card-note",
        text: "若之后更正了某场早期赛果，新榜单会以新的结果版本另行发布；本页的数值保持不变。",
      }),
    ]);
    host.replaceChildren(body);
  } catch (err) {
    host.replaceChildren(
      el("div", { class: "empty", text: `读取版本失败：${err.message}` })
    );
  }
}
