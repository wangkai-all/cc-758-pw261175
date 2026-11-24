// App state + shell: loads the dataset once, owns the method / revision /
// correction state that every panel reads, and re-renders the active tab.

import { api } from "./api.js";
import { el, debounce, fmt } from "./ui.js";
import { hideTooltip } from "./tooltip.js";
import { renderReplay } from "./panels/replay.js";
import { renderCompare } from "./panels/compare.js";
import { renderValidate } from "./panels/validate.js";
import { renderCorrections } from "./panels/corrections.js";
import { renderBoards } from "./panels/boards.js";
import { renderScenarios } from "./panels/scenarios.js";
import { renderData } from "./panels/data.js";

const TABS = [
  { id: "replay", label: "逐场回放", render: renderReplay },
  { id: "compare", label: "方法对比", render: renderCompare },
  { id: "validate", label: "校验", render: renderValidate },
  { id: "corrections", label: "更正与重放", render: renderCorrections },
  { id: "boards", label: "发布榜单", render: renderBoards },
  { id: "scenarios", label: "情景样例", render: renderScenarios },
  { id: "data", label: "数据与导入", render: renderData },
];

export const state = {
  boot: null,
  diagnostics: null,
  compare: null,
  checks: null,
  sweep: null,
  revisions: null,
  boards: null,
  scenarios: null,
  frames: { approx: null, window: null },
  method: "approx",
  tab: "replay",
  // Replay cursor: index of the last processed match (1..n).
  cursor: 0,
  selectedPlayer: null,
  drift: 0.04,
  showTruth: true,
  status: { state: "busy", text: "载入数据…" },
  busy: 0,
};

let shell = null;

export function setStatus(stateName, text) {
  state.status = { state: stateName, text };
  const pill = shell?.querySelector(".status-pill");
  if (pill) {
    pill.dataset.state = stateName;
    pill.querySelector(".status-text").textContent = text;
  }
}

export async function withBusy(label, fn) {
  state.busy += 1;
  setStatus("busy", label);
  document.body.classList.add("fetching");
  try {
    return await fn();
  } finally {
    state.busy -= 1;
    if (state.busy === 0) {
      document.body.classList.remove("fetching");
      setStatus("idle", "本地数据 · 已就绪");
    }
  }
}

export function frameCount() {
  const f = state.frames[state.method];
  return f?.frames?.length || state.boot?.matches?.length || 0;
}

export function currentFrame(index = state.cursor) {
  const bundle = state.frames[state.method];
  if (!bundle || !bundle.frames.length) return null;
  const i = Math.max(0, Math.min(index, bundle.frames.length) - 1);
  return bundle.frames[i];
}

export function playerName(pid) {
  const p = state.boot?.players?.find((x) => x.id === pid);
  return p?.name || pid;
}

export function playerMeta(pid) {
  return state.boot?.players?.find((x) => x.id === pid) || null;
}

export function teamLabel(ids) {
  return ids.map((p) => playerName(p)).join(" · ");
}

// Load one method's frames once (≈0.5MB, 159 frames × 33 players).
export async function ensureFrames(method) {
  if (state.frames[method]) return state.frames[method];
  const bundle = await api.get(`/api/frames?method=${method}`);
  state.frames[method] = bundle;
  return bundle;
}

export function invalidateAll() {
  state.frames = { approx: null, window: null };
  state.compare = null;
  state.checks = null;
  state.sweep = null;
  state.revisions = null;
  state.boards = null;
}

export function rerender() {
  if (!shell) return;
  hideTooltip();
  const main = shell.querySelector("#panel-root");
  const render = TABS.find((t) => t.id === state.tab)?.render;
  if (!render) return;
  const panel = el("section", { class: "panel", id: `panel-${state.tab}` });
  main.replaceChildren(panel);
  render(panel);
  for (const btn of shell.querySelectorAll(".tabs button")) {
    btn.setAttribute("aria-selected", String(btn.dataset.tab === state.tab));
  }
  syncTopbar();
}

function syncTopbar() {
  for (const btn of shell.querySelectorAll(".method-switch button")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.method === state.method));
  }
}

async function applyDrift(value) {
  const drift = Math.max(0, Math.min(0.2, Number(value)));
  state.drift = drift;
  await withBusy("按新的漂移假设重算…", async () => {
    await api.post("/api/hyper", { drift_sd: drift });
    state.checks = null;
    state.sweep = null;
    state.compare = null;
    state.frames = { approx: null, window: null };
    state.revisions = null;
    await ensureFrames(state.method);
  });
  rerender();
}

function driftControl() {
  const slider = el("input", {
    type: "range",
    min: "0",
    max: "0.1",
    step: "0.005",
    value: String(state.drift),
    id: "drift-slider",
    "aria-label": "漂移假设：每天能力随机游走的标准差",
  });
  const value = el("span", {
    class: "mono-value",
    id: "drift-value",
    text: fmt(state.drift, 3),
  });
  const apply = debounce((v) => {
    applyDrift(v).catch((err) => setStatus("error", `重算失败：${err.message}`));
  }, 420);
  slider.addEventListener("input", () => {
    value.textContent = fmt(Number(slider.value), 3);
    setStatus("busy", "漂移假设已改，等待重算…");
    apply(slider.value);
  });
  return el("div", { class: "group drift-group" }, [
    el("label", { for: "drift-slider", text: "漂移 σ/天" }),
    slider,
    value,
    el("span", {
      class: "sub truth-hint",
      title: "生成真值的实际漂移，仅供对照",
      text: `真值 ${fmt(state.boot?.dataset?.drift_true ?? 0.01, 3)}`,
    }),
  ]);
}

function buildShell() {
  const root = document.getElementById("app");
  root.replaceChildren();

  const methodSwitch = el("div", {
    class: "method-switch",
    role: "group",
    "aria-label": "更新方式",
  });
  for (const [id, label] of [
    ["approx", "逐场近似"],
    ["window", "窗口联合"],
  ]) {
    methodSwitch.append(
      el("button", {
        type: "button",
        dataset: { method: id },
        "aria-pressed": "false",
        text: label,
        onclick: async () => {
          if (state.method === id) return;
          state.method = id;
          syncTopbar();
          await withBusy("载入另一更新方式…", async () => {
            await ensureFrames(id);
            if (state.cursor > frameCount()) state.cursor = frameCount();
            if (!state.cursor) state.cursor = 1;
          });
          rerender();
        },
      })
    );
  }

  const themeBtn = el("button", {
    class: "icon-button",
    type: "button",
    "aria-label": "切换明暗主题",
    onclick: () => {
      const dark = document.documentElement.dataset.theme === "dark";
      const next = dark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem("theme", next);
      } catch (err) {
        /* ignore */
      }
      themeBtn.textContent = next === "dark" ? "☀ 浅色" : "☾ 深色";
    },
  });
  themeBtn.textContent =
    document.documentElement.dataset.theme === "dark" ? "☀ 浅色" : "☾ 深色";

  const topbar = el("header", { class: "topbar" }, [
    el("div", { class: "topbar-inner" }, [
      el("div", { class: "brand" }, [
        el("h1", { text: "星河轮换联赛 · 评分分析" }),
        el("span", { class: "dataset-tag", text: "虚构数据 · 本地运行" }),
      ]),
      el("div", { class: "topbar-spacer" }),
      el("div", { class: "top-actions" }, [
        driftControl(),
        methodSwitch,
        el("span", { class: "status-pill", dataset: { state: "busy" } }, [
          el("span", { class: "status-dot" }),
          el("span", { class: "status-text", text: "载入数据…" }),
        ]),
        themeBtn,
      ]),
    ]),
  ]);

  const tabs = el(
    "nav",
    { class: "tabs", role: "tablist", "aria-label": "分析视图" },
    TABS.map((t) =>
      el("button", {
        type: "button",
        role: "tab",
        dataset: { tab: t.id },
        "aria-selected": String(t.id === state.tab),
        text: t.label,
        onclick: () => {
          state.tab = t.id;
          rerender();
          window.scrollTo({ top: 0, behavior: "smooth" });
        },
      })
    )
  );

  const hero = el("section", { class: "hero" }, [
    el("h2", { text: "看清每个人每一段时间的强弱，以及这份判断有多不确定。" }),
    el(
      "p",
      {
        text: "全部比赛为虚构生成，真值已知。可以逐场回放、翻看个人轨迹、调整漂移假设、比较逐场近似与窗口联合两种更新方式；更正早期赛果时从检查点重放，已发布的榜单保持原样。",
      }
    ),
  ]);

  const main = el("main", { id: "panel-root" });

  const footer = el("footer", { class: "footer" }, [
    el("span", { text: "本地项目 · 不连接任何真实投注或赛事平台。" }),
    el("span", { text: "数值均来自内置模拟真值与本地计算。" }),
  ]);

  root.append(
    el("div", { class: "app-shell" }, [topbar, hero, tabs, main, footer])
  );
  shell = root;
}

export async function boot() {
  buildShell();
  await withBusy("载入数据…", async () => {
    const [bootstrap, diagnostics] = await Promise.all([
      api.get("/api/bootstrap"),
      api.get("/api/diagnostics"),
    ]);
    state.boot = bootstrap;
    state.diagnostics = diagnostics;
    state.drift = bootstrap.config.drift_sd;
    await ensureFrames(state.method);
    state.cursor = frameCount();
    const p003 = bootstrap.players.find((p) => p.id === "P005");
    state.selectedPlayer = p003?.id || bootstrap.players[0]?.id || null;
  });
  rerender();
}

// Start the app. If the API is down, keep the boot screen with a clear note
// rather than an empty page.
boot().catch((err) => {
  const root = document.getElementById("app");
  root.replaceChildren(
    el("div", { class: "boot-screen" }, [
      el("p", { class: "boot-title", text: "无法连接本地数据服务" }),
      el("p", { class: "boot-note", text: err.message }),
      el("p", {
        class: "boot-note",
        text: "请在项目根目录运行 npm run api（127.0.0.1:8765），然后刷新页面。",
      }),
    ])
  );
});
