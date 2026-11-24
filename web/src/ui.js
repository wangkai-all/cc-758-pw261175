// Formatting and small DOM helpers shared by the panels.

export function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const fixed = Number(value).toFixed(digits);
  return fixed === "-0.00" ? "0.00" : fixed;
}

export function signed(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const v = Number(value);
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function compactSeconds(value) {
  if (value === null || value === undefined) return "—";
  const v = Number(value);
  if (v >= 1) return `${v.toFixed(2)} 秒`;
  if (v >= 0.001) return `${(v * 1000).toFixed(1)} 毫秒`;
  return `${(v * 1e6).toFixed(0)} 微秒`;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const SVG_NS = "http://www.w3.org/2000/svg";

export function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    // `text` is content, not an attribute: SVG has no text attribute, so
    // setting it as one silently renders nothing.
    if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Tick values that land on readable round numbers.
export function niceTicks(min, max, count = 5) {
  if (min === max) return [min];
  const span = max - min;
  const raw = span / Math.max(1, count - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 1e-9; v += step) {
    ticks.push(Math.round(v / step) * step);
  }
  return ticks;
}

export function debounce(fn, ms = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// "Download / table view" is the non-hover path to every value.
export function tableToggle(label, buildTable) {
  let open = false;
  const wrap = el("div", { class: "table-view" });
  const btn = el("button", {
    class: "link-button",
    type: "button",
    "aria-expanded": "false",
    text: label || "查看数据表",
    onclick: () => {
      open = !open;
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "收起数据表" : label || "查看数据表";
      if (open && !wrap.firstChild) wrap.append(buildTable());
      wrap.hidden = !open;
    },
  });
  wrap.hidden = true;
  return el("div", { class: "table-view-host" }, [btn, wrap]);
}

export function dataTable(columns, rows) {
  const thead = el(
    "thead",
    {},
    el(
      "tr",
      {},
      columns.map((c) =>
        el("th", { class: c.numeric ? "num" : "", scope: "col", text: c.label })
      )
    )
  );
  const tbody = el(
    "tbody",
    {},
    rows.map((row) =>
      el(
        "tr",
        {},
        columns.map((c) =>
          el("td", {
            class: c.numeric ? "num" : "",
            text: row[c.key] === undefined || row[c.key] === null ? "—" : row[c.key],
          })
        )
      )
    )
  );
  return el("table", { class: "data-table" }, [thead, tbody]);
}
