/* Zen-chan charts: tiny, dependency-free, offline. Untrusted text only ever goes
   through textContent (page titles can contain anything). */
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  appendKids(node, kids);
  return node;
}

function svg(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  appendKids(node, kids);
  return node;
}

function applyAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
}

function appendKids(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

function fmtMin(m) {
  m = Math.round(m || 0);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h}h ${String(r).padStart(2, "0")}m` : `${h}h`;
}

function pct(x, digits = 0) { return `${((x || 0) * 100).toFixed(digits)}%`; }

const Charts = {
  /** Horizontal bars. items: [{label, value, color, note}] */
  bars(items, { fmt = fmtMin, max } = {}) {
    const top = max || Math.max(1e-9, ...items.map((i) => i.value));
    return el("div", { class: "bars" }, items.map((i) =>
      el("div", { class: "bar-row", title: i.title || i.label },
        el("span", { class: "name", text: i.label }),
        el("span", { class: "track" },
          el("span", { class: "fill", style: { display: "block", width: `${Math.max(1.5, (100 * i.value) / top)}%`, background: i.color || "var(--green)", color: i.color || "var(--green)" } })),
        el("span", { class: "val", text: i.note ?? fmt(i.value) }))));
  },

  /** Score ring 0..100 */
  ring(value, size = 116) {
    const r = size / 2 - 9, c = 2 * Math.PI * r;
    const v = value == null ? 0 : value;
    const color = v >= 70 ? "var(--green)" : v >= 45 ? "var(--amber)" : "var(--red)";
    return svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `score ${value}` },
      svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "var(--faint)", "stroke-width": 9 }),
      svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: color, "stroke-width": 9, "stroke-linecap": "round",
        "stroke-dasharray": `${(c * v) / 100} ${c}`, transform: `rotate(-90 ${size / 2} ${size / 2})`, style: `filter: drop-shadow(0 0 4px ${color})` }),
      svg("text", { x: "50%", y: "52%", "text-anchor": "middle", "dominant-baseline": "middle", fill: color,
        "font-family": "VT323, monospace", "font-size": size / 2.9, text: value == null ? "—" : String(value) }));
  },

  /** 7x24 minutes grid */
  heatmap(grid) {
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const max = Math.max(1, ...grid.flat());
    const wrap = el("div", { class: "heatmap" }, el("span"));
    for (let h = 0; h < 24; h++) wrap.append(el("span", { class: "hr", text: h % 3 === 0 ? String(h) : "" }));
    grid.forEach((row, d) => {
      wrap.append(el("span", { class: "lbl", text: days[d] }));
      row.forEach((m, h) => {
        const a = m / max;
        wrap.append(el("span", { class: "cell", title: `${days[d]} ${String(h).padStart(2, "0")}:00 — ${fmtMin(m)}`,
          style: { background: a > 0 ? `rgba(57,255,20,${0.08 + 0.92 * Math.pow(a, 0.7)})` : "" } }));
      });
    });
    return wrap;
  },

  /** Stacked daily columns: productive / other / late, with feed as a red cap. */
  columns(daily, height = 160) {
    const w = Math.max(320, daily.length * 22), pad = 18;
    const max = Math.max(30, ...daily.map((d) => d.minutes));
    const bw = (w - pad) / daily.length;
    const y = (m) => height - pad - ((height - pad - 6) * m) / max;
    const s = svg("svg", { viewBox: `0 0 ${w} ${height}`, width: "100%", height, preserveAspectRatio: "none", role: "img", "aria-label": "minutes per day" });
    [0.5, 1].forEach((f) => s.append(svg("line", { x1: pad, x2: w, y1: y(max * f), y2: y(max * f), stroke: "var(--faint)", "stroke-dasharray": "2 3" })));
    s.append(svg("text", { x: 0, y: y(max) + 4, fill: "var(--dim)", "font-size": 10, text: fmtMin(max) }));
    daily.forEach((d, i) => {
      const x = pad + i * bw + 2, bwi = Math.max(2, bw - 4);
      const other = Math.max(0, d.minutes - d.productive - d.late);
      let base = height - pad;
      const seg = (m, color) => {
        if (m <= 0) return;
        const top = base - ((height - pad - 6) * m) / max;
        s.append(svg("rect", { x, y: top, width: bwi, height: base - top, fill: color, rx: 1 }));
        base = top;
      };
      seg(d.productive, "#39ff14");
      seg(other, "#1f6b33");
      seg(d.late, "#ff3366");
      const t = svg("title", { text: `${d.day}: ${fmtMin(d.minutes)} (productive ${fmtMin(d.productive)}, late ${fmtMin(d.late)}, feeds ${fmtMin(d.feed)})${d.score != null ? `, score ${d.score}` : ""}` });
      s.append(svg("rect", { x, y: 0, width: bwi, height: height - pad, fill: "transparent" }, t));
      if (daily.length <= 14 || i % Math.ceil(daily.length / 10) === 0)
        s.append(svg("text", { x: x + bwi / 2, y: height - 4, "text-anchor": "middle", fill: "var(--dim)", "font-size": 9, text: d.day.slice(5) }));
    });
    return s;
  },

  /** Valence over time, -1..1 around a zero line. */
  line(points, height = 110) {
    const w = 400, pad = 10;
    const s = svg("svg", { viewBox: `0 0 ${w} ${height}`, width: "100%", height, preserveAspectRatio: "none", role: "img", "aria-label": "mood timeline" });
    if (points.length < 2) return s;
    const x = (i) => pad + ((w - 2 * pad) * i) / (points.length - 1);
    const y = (v) => height / 2 - (height / 2 - pad) * Math.max(-1, Math.min(1, v * 2.2));
    s.append(svg("line", { x1: pad, x2: w - pad, y1: height / 2, y2: height / 2, stroke: "var(--faint)" }));
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.valence).toFixed(1)}`).join(" ");
    s.append(svg("path", { d: path, fill: "none", stroke: "var(--cyan)", "stroke-width": 2, style: "filter: drop-shadow(0 0 3px var(--cyan))" }));
    points.forEach((p, i) => s.append(svg("circle", { cx: x(i), cy: y(p.valence), r: 3, fill: p.valence >= 0 ? "var(--green)" : "var(--red)" },
      svg("title", { text: `${p.day}: valence ${p.valence.toFixed(2)}, arousal ${p.arousal.toFixed(2)}` }))));
    s.append(svg("text", { x: pad, y: 12, fill: "var(--dim)", "font-size": 10, text: "brighter" }));
    s.append(svg("text", { x: pad, y: height - 4, fill: "var(--dim)", "font-size": 10, text: "heavier" }));
    return s;
  },

  /** 24h radial activity clock. */
  clock(hours, size = 200) {
    const c = size / 2, r0 = size * 0.2, r1 = size * 0.46;
    const max = Math.max(1e-9, ...hours);
    const s = svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": "activity by hour" });
    s.append(svg("circle", { cx: c, cy: c, r: r0, fill: "none", stroke: "var(--faint)" }));
    hours.forEach((v, h) => {
      const a = ((h / 24) * 2 - 0.5) * Math.PI, len = r0 + (r1 - r0) * (v / max);
      const night = h < 6 || h >= 23;
      s.append(svg("line", { x1: c + r0 * Math.cos(a), y1: c + r0 * Math.sin(a), x2: c + len * Math.cos(a), y2: c + len * Math.sin(a),
        stroke: night ? "var(--red)" : "var(--green)", "stroke-width": 5, "stroke-linecap": "round", opacity: 0.25 + 0.75 * (v / max) },
        svg("title", { text: `${String(h).padStart(2, "0")}:00 — ${pct(v, 1)} of activity` })));
    });
    [[0, "00"], [6, "06"], [12, "12"], [18, "18"]].forEach(([h, t]) => {
      const a = ((h / 24) * 2 - 0.5) * Math.PI;
      s.append(svg("text", { x: c + (r0 - 12) * Math.cos(a), y: c + (r0 - 12) * Math.sin(a) + 3, "text-anchor": "middle", fill: "var(--dim)", "font-size": 10, text: t }));
    });
    return s;
  },
};

window.Zen = { el, svg, fmtMin, pct, Charts };
