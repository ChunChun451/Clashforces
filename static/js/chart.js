// A small single-series SVG line chart (duel rating over time) with a hover tooltip.
// One series: no legend box, the section title names it. 2px line, 8px markers,
// recessive grid, values in text colors, crosshair + tooltip on hover.

const NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

function niceTicks(lo, hi, count = 4) {
  const span = Math.max(1, hi - lo);
  const step = [10, 20, 25, 50, 100, 200, 250, 500].find((s) => span / s <= count) || 1000;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step / 2; v += step) out.push(v);
  return out;
}

// points: [{x: unixSeconds, y: rating, label: string}]
export function lineChart(points, { height = 220, ariaLabel = "Chart" } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.style.position = "relative";
  const width = 720;
  const pad = { l: 44, r: 16, t: 12, b: 26 };
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": ariaLabel, width: "100%" });
  svg.style.display = "block";
  svg.style.overflow = "visible";
  wrap.append(svg);
  if (!points.length) return wrap;

  const ys = points.map((p) => p.y);
  const ticks = niceTicks(Math.min(...ys) - 20, Math.max(...ys) + 20);
  const yLo = ticks[0], yHi = ticks[ticks.length - 1];
  const n = points.length;
  const x = (i) => pad.l + (n === 1 ? (width - pad.l - pad.r) / 2 : (i * (width - pad.l - pad.r)) / (n - 1));
  const y = (v) => pad.t + ((yHi - v) * (height - pad.t - pad.b)) / Math.max(1, yHi - yLo);

  for (const t of ticks) {
    svg.append(svgEl("line", { x1: pad.l, x2: width - pad.r, y1: y(t), y2: y(t), stroke: "var(--line)", "stroke-width": 1 }));
    const label = svgEl("text", { x: pad.l - 8, y: y(t) + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11, "font-family": "var(--font-mono)" });
    label.textContent = String(t);
    svg.append(label);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  svg.append(svgEl("path", { d, fill: "none", stroke: "var(--accent)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  const last = points[n - 1];
  svg.append(svgEl("circle", { cx: x(n - 1), cy: y(last.y), r: 4, fill: "var(--accent)", stroke: "var(--bg)", "stroke-width": 2 }));
  const lastLabel = svgEl("text", { x: x(n - 1), y: y(last.y) - 10, "text-anchor": "end", fill: "var(--text-strong)", "font-size": 12, "font-family": "var(--font-mono)" });
  lastLabel.textContent = String(last.y);
  svg.append(lastLabel);

  // hover layer
  const cross = svgEl("line", { y1: pad.t, y2: height - pad.b, stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "2 3", visibility: "hidden" });
  const dot = svgEl("circle", { r: 4, fill: "var(--accent)", stroke: "var(--bg)", "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot);
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  wrap.append(tip);
  const hit = svgEl("rect", { x: pad.l - 10, y: 0, width: width - pad.l - pad.r + 20, height, fill: "transparent" });
  svg.append(hit);
  const show = (clientX) => {
    const box = svg.getBoundingClientRect();
    const vx = ((clientX - box.left) / box.width) * width;
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(x(i) - vx) < Math.abs(x(best) - vx)) best = i;
    const p = points[best];
    cross.setAttribute("x1", x(best)); cross.setAttribute("x2", x(best));
    dot.setAttribute("cx", x(best)); dot.setAttribute("cy", y(p.y));
    cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.textContent = `${p.y} · ${p.label}`;
    const left = (x(best) / width) * box.width;
    tip.style.left = `${Math.min(Math.max(left, 60), box.width - 60)}px`;
    tip.style.top = `${(y(p.y) / height) * box.height - 40}px`;
  };
  hit.addEventListener("pointermove", (e) => show(e.clientX));
  hit.addEventListener("pointerleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
  });
  return wrap;
}
