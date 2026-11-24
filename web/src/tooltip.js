// Hover/tooltip layer shared by the charts. Values lead, labels follow; the
// series key is a short stroke, never a filled box. Everything is built with
// textContent so dataset labels can never inject markup.

import { el } from "./ui.js";

let node = null;

function host() {
  if (!node) {
    node = el("div", { class: "tooltip", role: "status", hidden: true });
    document.body.append(node);
  }
  return node;
}

// build() returns { head, rows: [{color, label, value}], note }
export function showTooltip(clientX, clientY, build) {
  const tip = host();
  const content = build();
  if (!content) {
    tip.hidden = true;
    return;
  }
  tip.hidden = false;
  tip.replaceChildren();
  if (content.head) {
    tip.append(el("div", { class: "tt-head", text: content.head }));
  }
  for (const row of content.rows || []) {
    tip.append(
      el("div", { class: "tt-row" }, [
        el("span", { class: "tt-left" }, [
          el("span", {
            class: "tt-key",
            style: `background:${row.color}`,
          }),
          el("span", { text: row.label }),
        ]),
        el("span", { class: "tt-val", text: row.value }),
      ])
    );
  }
  if (content.note) {
    tip.append(
      el("div", { class: "tt-head", style: "margin-top:5px;margin-bottom:0", text: content.note })
    );
  }
  const pad = 14;
  const rect = tip.getBoundingClientRect();
  let x = clientX + pad;
  let y = clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = clientY - rect.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

export function hideTooltip() {
  if (node) node.hidden = true;
}
