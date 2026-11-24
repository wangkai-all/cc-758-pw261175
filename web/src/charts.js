// Chart primitives: line + uncertainty band, multi-series line, dot/interval
// table rows. Hand-rolled SVG so the marks follow the project's visual spec
// (2px lines, 10% area wash, hairline grid, surface-ringed dots).
//
// Every chart is also readable without hovering: `table` data is attached to
// the figure by the caller through ui.tableToggle.

import { niceTicks, svg, el } from "./ui.js";

const M = { top: 16, right: 18, bottom: 34, left: 52 };

export function responsive(container, render) {
  let frame = 0;
  const draw = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const width = Math.max(320, container.clientWidth || 640);
      container.replaceChildren(render(width));
    });
  };
  const observer = new ResizeObserver(draw);
  observer.observe(container);
  draw();
  return () => observer.disconnect();
}

function extent(values, fallback = [0, 1]) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v === null || v === undefined || Number.isNaN(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return fallback;
  if (lo === hi) return [lo - 0.5, hi + 0.5];
  const pad = (hi - lo) * 0.06;
  return [lo - pad, hi + pad];
}

export function lineChart(
  width,
  {
    height = 260,
    series = [],
    bands = [],
    xDomain,
    yDomain,
    xTicks = 6,
    yTicks = 5,
    xFormat = (v) => String(v),
    yFormat = (v) => v.toFixed(2),
    xLabel,
    yLabel,
    vlines = [],
    annotations = [],
    zeroLine = false,
    hoverIndex = null,
    onHover = null,
    ariaLabel = "折线图",
  } = {}
) {
  const w = Math.max(320, width);
  const innerW = w - M.left - M.right;
  const innerH = height - M.top - M.bottom;
  const allX = [
    ...series.flatMap((s) => s.points.map((p) => p.x)),
    ...bands.flatMap((b) => b.points.map((p) => p.x)),
  ];
  const allY = [
    ...series.flatMap((s) => s.points.map((p) => p.y)),
    ...bands.flatMap((b) => b.points.flatMap((p) => [p.lo, p.hi])),
  ];
  if (zeroLine) allY.push(0);
  const [x0, x1] = xDomain || extent(allX, [0, 1]);
  const [y0, y1] = yDomain || extent(allY, [-1, 1]);
  const sx = (x) => M.left + ((x - x0) / (x1 - x0 || 1)) * innerW;
  const sy = (y) => M.top + innerH - ((y - y0) / (y1 - y0 || 1)) * innerH;

  const root = svg("svg", {
    viewBox: `0 0 ${w} ${height}`,
    width: "100%",
    height,
    role: "img",
    "aria-label": ariaLabel,
    class: "chart-svg",
  });

  // Grid + y ticks (hairline, recessive).
  const yTickValues = niceTicks(y0, y1, yTicks);
  for (const t of yTickValues) {
    if (t < y0 - 1e-9 || t > y1 + 1e-9) continue;
    root.append(
      svg("line", {
        x1: M.left,
        x2: M.left + innerW,
        y1: sy(t),
        y2: sy(t),
        class: "grid-line",
      })
    );
    root.append(
      svg("text", {
        x: M.left - 8,
        y: sy(t) + 3.5,
        text: yFormat(t),
        class: "axis-label",
        "text-anchor": "end",
      })
    );
  }
  if (zeroLine && y0 < 0 && y1 > 0) {
    root.append(
      svg("line", {
        x1: M.left,
        x2: M.left + innerW,
        y1: sy(0),
        y2: sy(0),
        class: "zero-line",
      })
    );
  }

  const xTickValues = niceTicks(x0, x1, xTicks).filter((t) => t >= x0 && t <= x1);
  for (const t of xTickValues) {
    root.append(
      svg("text", {
        x: sx(t),
        y: M.top + innerH + 18,
        text: xFormat(t),
        class: "axis-label",
        "text-anchor": "middle",
      })
    );
  }
  root.append(
    svg("line", {
      x1: M.left,
      x2: M.left + innerW,
      y1: M.top + innerH,
      y2: M.top + innerH,
      class: "axis-line",
    })
  );

  if (yLabel) {
    root.append(
      svg("text", {
        x: 12,
        y: M.top + innerH / 2,
        text: yLabel,
        class: "axis-title",
        "text-anchor": "middle",
        transform: `rotate(-90 12 ${M.top + innerH / 2})`,
      })
    );
  }
  if (xLabel) {
    root.append(
      svg("text", {
        x: M.left + innerW / 2,
        y: height - 2,
        text: xLabel,
        class: "axis-title",
        "text-anchor": "middle",
      })
    );
  }

  // Vertical markers (corrections, shocks).
  for (const v of vlines) {
    root.append(
      svg("line", {
        x1: sx(v.x),
        x2: sx(v.x),
        y1: M.top,
        y2: M.top + innerH,
        class: "rule-line",
      })
    );
    if (v.label) {
      root.append(
        svg("text", {
          x: sx(v.x) + 4,
          y: M.top + 11,
          text: v.label,
          class: "marker-label",
        })
      );
    }
  }

  // Uncertainty band: a 10% wash of the series hue.
  bands.forEach((band, bi) => {
    const pts = band.points.filter(
      (p) => Number.isFinite(p.lo) && Number.isFinite(p.hi)
    );
    if (!pts.length) return;
    const top = pts.map((p) => `${sx(p.x)},${sy(p.hi)}`);
    const bottom = [...pts].reverse().map((p) => `${sx(p.x)},${sy(p.lo)}`);
    root.append(
      svg("path", {
        d: `M${top.join("L")}L${bottom.join("L")}Z`,
        fill: `var(--series-${band.colorSlot ?? bi + 1})`,
        "fill-opacity": "0.1",
        stroke: "none",
      })
    );
  });

  // Lines.
  series.forEach((s, i) => {
    const pts = s.points.filter((p) => p.y !== null && Number.isFinite(p.y));
    if (!pts.length) return;
    const d = pts
      .map((p, j) => `${j === 0 ? "M" : "L"}${sx(p.x)},${sy(p.y)}`)
      .join("");
    root.append(
      svg("path", {
        d,
        fill: "none",
        stroke: `var(--series-${s.colorSlot ?? i + 1})`,
        "stroke-width": s.width ?? 2,
        "stroke-linejoin": "round",
        "stroke-linecap": "round",
        "stroke-dasharray": s.dashed ? "6 5" : null,
        opacity: s.muted ? 0.45 : 1,
      })
    );
    // End dot: surface ring keeps it legible where lines cross.
    const last = pts[pts.length - 1];
    if (s.endDot !== false) {
      root.append(
        svg("circle", {
          cx: sx(last.x),
          cy: sy(last.y),
          r: 4,
          fill: `var(--series-${s.colorSlot ?? i + 1})`,
          stroke: "var(--surface-1)",
          "stroke-width": 2,
        })
      );
    }
    if (s.endLabel) {
      root.append(
        svg("text", {
          x: sx(last.x) - (s.endLabelSide === "left" ? 8 : -8),
          y: sy(last.y) + (s.endLabelDy || -8),
          text: s.endLabel,
          class: "series-label",
          "text-anchor": s.endLabelSide === "left" ? "end" : "start",
        })
      );
    }
    // Played-game markers: small ticks along the baseline.
    if (s.gameMarks) {
      for (const gx of s.gameMarks) {
        root.append(
          svg("line", {
            x1: sx(gx),
            x2: sx(gx),
            y1: M.top + innerH,
            y2: M.top + innerH + 5,
            stroke: `var(--series-${s.colorSlot ?? i + 1})`,
            "stroke-width": 1.5,
            opacity: 0.7,
          })
        );
      }
    }
  });

  for (const a of annotations) {
    root.append(
      svg("text", {
        x: sx(a.x) + (a.dx ?? 6),
        y: sy(a.y) + (a.dy ?? -8),
        text: a.label,
        class: "annotation-label",
        "text-anchor": a.anchor || "start",
      })
    );
  }

  // Crosshair + focus dot for the hovered x.
  if (hoverIndex !== null && hoverIndex !== undefined) {
    const hx = sx(hoverIndex);
    root.append(
      svg("line", {
        x1: hx,
        x2: hx,
        y1: M.top,
        y2: M.top + innerH,
        class: "crosshair",
      })
    );
    series.forEach((s, i) => {
      const p = nearest(s.points, hoverIndex);
      if (!p || !Number.isFinite(p.y)) return;
      root.append(
        svg("circle", {
          cx: sx(p.x),
          cy: sy(p.y),
          r: 4.5,
          fill: `var(--series-${s.colorSlot ?? i + 1})`,
          stroke: "var(--surface-1)",
          "stroke-width": 2,
        })
      );
    });
  }

  // Transparent overlay = the hit target (bigger than any mark).
  if (onHover) {
    const overlay = svg("rect", {
      x: M.left,
      y: M.top,
      width: innerW,
      height: innerH,
      fill: "transparent",
      class: "hover-overlay",
      tabindex: 0,
    });
    const pointToData = (evt) => {
      const rect = root.getBoundingClientRect();
      const px = ((evt.clientX - rect.left) / rect.width) * w;
      const t = (px - M.left) / innerW;
      return x0 + t * (x1 - x0);
    };
    overlay.addEventListener("pointermove", (evt) => onHover(pointToData(evt), evt));
    overlay.addEventListener("pointerleave", () => onHover(null, null));
    overlay.addEventListener("focus", () => onHover((x0 + x1) / 2, null));
    overlay.addEventListener("blur", () => onHover(null, null));
    root.append(overlay);
  }

  return root;
}

export function nearest(points, x) {
  let best = null;
  let bestD = Infinity;
  for (const p of points) {
    const d = Math.abs(p.x - x);
    if (d < bestD) {
      bestD = d;
      best = p;
    }
  }
  return best;
}

// Grouped comparison of a single metric across methods: horizontal bars with
// 90% interval ticks. Bars capped at 24px, 4px rounded data end, 2px gaps.
export function barWithIntervals(
  width,
  { rows, xDomain, xFormat = (v) => v.toFixed(2), labelWidth = 148 } = {}
) {
  const rowH = 44;
  const height = rows.length * rowH + 40;
  const w = Math.max(320, width);
  const left = labelWidth;
  const right = 76;
  const innerW = w - left - right;
  const [x0, x1] = xDomain || extent(rows.flatMap((r) => [r.lo, r.hi, r.mean]), [0, 1]);
  const sx = (v) => left + ((v - x0) / (x1 - x0 || 1)) * innerW;
  const barH = 16;
  const root = svg("svg", {
    viewBox: `0 0 ${w} ${height}`,
    width: "100%",
    height,
    role: "img",
    "aria-label": "带不确定区间的对比条形图",
    class: "chart-svg",
  });
  for (const t of niceTicks(x0, x1, 5)) {
    if (t < x0 - 1e-9 || t > x1 + 1e-9) continue;
    root.append(
      svg("line", {
        x1: sx(t),
        x2: sx(t),
        y1: 24,
        y2: height - 18,
        class: "grid-line",
      })
    );
    root.append(
      svg("text", {
        x: sx(t),
        y: height - 4,
        text: xFormat(t),
        class: "axis-label",
        "text-anchor": "middle",
      })
    );
  }
  rows.forEach((row, i) => {
    const cy = 30 + i * rowH;
    const y = cy - barH / 2;
    root.append(
      svg("text", {
        x: left - 10,
        y: cy + 4,
        text: row.label,
        class: "row-label",
        "text-anchor": "end",
      })
    );
    // Interval whisker behind the bar.
    root.append(
      svg("line", {
        x1: sx(row.lo),
        x2: sx(row.hi),
        y1: cy,
        y2: cy,
        stroke: `var(--series-${row.colorSlot})`,
        "stroke-width": 2,
        "stroke-linecap": "round",
        opacity: row.intervalMuted ? 0.55 : 1,
      })
    );
    for (const v of [row.lo, row.hi]) {
      root.append(
        svg("line", {
          x1: sx(v),
          x2: sx(v),
          y1: cy - 7,
          y2: cy + 7,
          stroke: `var(--series-${row.colorSlot})`,
          "stroke-width": 2,
          "stroke-linecap": "round",
        })
      );
    }
    const barX = sx(Math.min(0, row.mean));
    const barW = Math.abs(sx(row.mean) - sx(0));
    root.append(
      svg("path", {
        d: roundedRightPath(barX, y, barW, barH, row.mean >= 0 ? 4 : 0),
        fill: `var(--series-${row.colorSlot})`,
        opacity: row.dim ? 0.5 : 1,
      })
    );
    root.append(
      svg("text", {
        x: sx(row.mean) + (row.mean >= 0 ? 8 : -8),
        y: cy + 4,
        text: row.valueLabel ?? xFormat(row.mean),
        class: "value-label",
        "text-anchor": row.mean >= 0 ? "start" : "end",
      })
    );
  });
  return root;
}

function roundedRightPath(x, y, wd, h, r) {
  if (wd <= 0.5) return `M${x},${y}h0`;
  const rr = Math.min(r, wd, h / 2);
  return `M${x},${y}H${x + wd - rr}Q${x + wd},${y} ${x + wd},${y + rr}V${
    y + h - rr
  }Q${x + wd},${y + h} ${x + wd - rr},${y + h}H${x}Z`;
}

// Simple column maths for small multiples: one panel per series.
export function smallMultiple(width, { title, subtitle, height = 120, series, xDomain, yDomain, xFormat }) {
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width: "100%",
    height,
    class: "chart-svg",
    role: "img",
    "aria-label": title,
  });
  const left = 46;
  const bottom = 20;
  const innerW = width - left - 10;
  const innerH = height - bottom - 12;
  const [x0, x1] = xDomain;
  const [y0, y1] = yDomain;
  const sx = (x) => left + ((x - x0) / (x1 - x0 || 1)) * innerW;
  const sy = (y) => 12 + innerH - ((y - y0) / (y1 - y0 || 1)) * innerH;
  for (const t of niceTicks(y0, y1, 3)) {
    if (t < y0 - 1e-9 || t > y1 + 1e-9) continue;
    root.append(
      svg("line", { x1: left, x2: left + innerW, y1: sy(t), y2: sy(t), class: "grid-line" })
    );
    root.append(
      svg("text", {
        x: left - 6,
        y: sy(t) + 3.5,
        text: t.toFixed(2),
        class: "axis-label",
        "text-anchor": "end",
      })
    );
  }
  if (y0 < 0 && y1 > 0) {
    root.append(
      svg("line", { x1: left, x2: left + innerW, y1: sy(0), y2: sy(0), class: "zero-line" })
    );
  }
  const pts = series.points.filter((p) => Number.isFinite(p.y));
  root.append(
    svg("path", {
      d: pts.map((p, j) => `${j === 0 ? "M" : "L"}${sx(p.x)},${sy(p.y)}`).join(""),
      fill: "none",
      stroke: `var(--series-${series.colorSlot})`,
      "stroke-width": 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    })
  );
  if (pts.length) {
    const last = pts[pts.length - 1];
    root.append(
      svg("circle", {
        cx: sx(last.x),
        cy: sy(last.y),
        r: 4,
        fill: `var(--series-${series.colorSlot})`,
        stroke: "var(--surface-1)",
        "stroke-width": 2,
      })
    );
  }
  for (const t of niceTicks(x0, x1, 3)) {
    if (t < x0 - 1e-9 || t > x1 + 1e-9) continue;
    root.append(
      svg("text", {
        x: sx(t),
        y: height - 4,
        text: xFormat ? xFormat(t) : String(t),
        class: "axis-label",
        "text-anchor": "middle",
      })
    );
  }
  return el("figure", { class: "small-multiple" }, [
    el("figcaption", {}, [
      el("span", { class: "sm-title", text: title }),
      subtitle ? el("span", { class: "sm-sub", text: subtitle }) : null,
    ]),
    root,
  ]);
}
