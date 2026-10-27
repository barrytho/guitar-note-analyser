// A small SVG plotter: axes, ticks, lines, envelopes, points and error bars.
// No library: the charts are plain SVG, so they export as vector SVG or PNG
// exactly as drawn.
"use strict";

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const FONT = "Helvetica, Arial, sans-serif";
  const INK = "#1d1d1b", INK2 = "#55534e", GRID = "#e6e3dc";

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value !== undefined && value !== null) node.setAttribute(key, value);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  function text(parent, x, y, content, attrs) {
    const node = el("text", Object.assign({x, y, "font-family": FONT, "font-size": 12, fill: INK2}, attrs), parent);
    node.textContent = content;
    return node;
  }

  // Round numbers for tick marks: steps of 1, 2 or 5 times a power of ten.
  function niceTicks(min, max, target) {
    if (!(max > min)) { max = min + 1; }
    const raw = (max - min) / (target || 5);
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map(m => m * power).find(s => s >= raw);
    const ticks = [];
    for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step * 1e-9; v += step) {
      ticks.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    }
    const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
    return {ticks, format: v => v.toFixed(decimals).replace("-", "−")};
  }

  function plot(container, spec) {
    const W = spec.width || 720, H = spec.height || 260;
    const m = Object.assign({l: 62, r: 18, t: spec.legend ? 30 : 14, b: 44}, spec.margin);
    const svg = el("svg", {xmlns: NS, viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                           role: "img", "aria-label": spec.title || ""});
    el("rect", {x: 0, y: 0, width: W, height: H, fill: "#ffffff"}, svg);
    const xs = spec.x, ys = spec.y;
    const px = v => m.l + (v - xs.min) / (xs.max - xs.min) * (W - m.l - m.r);
    const py = v => H - m.b - (v - ys.min) / (ys.max - ys.min) * (H - m.t - m.b);
    const clipId = "clip" + Math.random().toString(36).slice(2, 8);
    el("rect", {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b},
       el("clipPath", {id: clipId}, el("defs", {}, svg)));

    // Grid and axes
    const xt = xs.ticks ? {ticks: xs.ticks, format: xs.format || (v => String(v))} : niceTicks(xs.min, xs.max, xs.count || 8);
    const yt = niceTicks(ys.min, ys.max, ys.count || 5);
    for (const v of yt.ticks) {
      el("line", {x1: m.l, x2: W - m.r, y1: py(v), y2: py(v), stroke: GRID, "stroke-width": 1}, svg);
      text(svg, m.l - 7, py(v) + 4, (ys.format || yt.format)(v), {"text-anchor": "end"});
    }
    for (const v of xt.ticks) {
      el("line", {x1: px(v), x2: px(v), y1: H - m.b, y2: H - m.b + 5, stroke: INK2}, svg);
      text(svg, px(v), H - m.b + 18, (xs.format || xt.format)(v), {"text-anchor": "middle"});
    }
    el("line", {x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: INK2}, svg);
    el("line", {x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, stroke: INK2}, svg);
    text(svg, (m.l + W - m.r) / 2, H - 6, xs.label, {"text-anchor": "middle", fill: INK, "font-size": 13});
    text(svg, 0, 0, ys.label, {"text-anchor": "middle", fill: INK, "font-size": 13,
         transform: `translate(15 ${(m.t + H - m.b) / 2}) rotate(-90)`});

    const body = el("g", {"clip-path": `url(#${clipId})`}, svg);
    const labels = el("g", {}, svg);
    for (const layer of spec.layers) {
      const stroke = layer.stroke || INK, width = layer.width || 1.2;
      if (layer.type === "vspan") {
        el("rect", {x: px(layer.x0), y: m.t, width: px(layer.x1) - px(layer.x0), height: H - m.t - m.b,
                    fill: layer.fill || "#1f5fa8", "fill-opacity": layer.opacity || 0.12}, body);
      } else if (layer.type === "band") {
        const top = layer.points.map(p => `${px(p[0]).toFixed(2)},${py(p[2]).toFixed(2)}`);
        const bottom = layer.points.slice().reverse().map(p => `${px(p[0]).toFixed(2)},${py(p[1]).toFixed(2)}`);
        el("polygon", {points: top.concat(bottom).join(" "), fill: layer.fill || "#3b6ea8"}, body);
      } else if (layer.type === "line") {
        const d = layer.points.map((p, i) => `${i ? "L" : "M"}${px(p[0]).toFixed(2)} ${py(p[1]).toFixed(2)}`).join("");
        el("path", {d, fill: "none", stroke, "stroke-width": width, "stroke-dasharray": layer.dash,
                    "stroke-linejoin": "round"}, body);
      } else if (layer.type === "hline") {
        el("line", {x1: m.l, x2: W - m.r, y1: py(layer.y), y2: py(layer.y), stroke, "stroke-width": width,
                    "stroke-dasharray": layer.dash}, body);
        if (layer.label) {
          text(labels, W - m.r - 4, py(layer.y) + (layer.below ? 14 : -5), layer.label,
               {"text-anchor": "end", fill: stroke, "font-size": 11, "font-weight": 600,
                stroke: "#fff", "stroke-width": 3, "paint-order": "stroke"});
        }
      } else if (layer.type === "vline") {
        el("line", {x1: px(layer.x), x2: px(layer.x), y1: m.t, y2: H - m.b, stroke, "stroke-width": width,
                    "stroke-dasharray": layer.dash}, body);
        if (layer.label) {
          text(labels, px(layer.x) + 4, m.t + 12, layer.label, {fill: stroke, "font-size": 11, "font-weight": 600});
        }
      } else if (layer.type === "points") {
        for (const p of layer.points) {
          el("circle", {cx: px(p[0]), cy: py(p[1]), r: layer.r || 3, fill: layer.fill || stroke,
                        "fill-opacity": layer.opacity == null ? 1 : layer.opacity,
                        stroke: layer.outline || "none", "stroke-width": 1}, body);
        }
      } else if (layer.type === "errorbars") {
        for (const [x, , lo, hi] of layer.items) {
          if (lo == null || hi == null) continue;
          el("line", {x1: px(x), x2: px(x), y1: py(lo), y2: py(hi), stroke, "stroke-width": width}, body);
          for (const y of [lo, hi]) {
            el("line", {x1: px(x) - 4, x2: px(x) + 4, y1: py(y), y2: py(y), stroke, "stroke-width": width}, body);
          }
        }
      }
    }

    if (spec.legend) {
      let x = m.l;
      for (const item of spec.legend) {
        const y = 14;
        if (item.type === "point") {
          el("circle", {cx: x + 8, cy: y - 4, r: item.r || 4, fill: item.fill, "fill-opacity": item.opacity == null ? 1 : item.opacity}, svg);
        } else if (item.type === "span") {
          el("rect", {x, y: y - 10, width: 16, height: 12, fill: item.fill, "fill-opacity": item.opacity || 0.12}, svg);
        } else {
          el("line", {x1: x, x2: x + 18, y1: y - 4, y2: y - 4, stroke: item.stroke, "stroke-width": item.width || 1.5,
                      "stroke-dasharray": item.dash}, svg);
        }
        text(svg, x + 24, y, item.label, {fill: INK, "font-size": 12});
        x += 24 + item.label.length * 6.6 + 18;
      }
    }

    container.replaceChildren(svg);
    return svg;
  }

  function save(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function svgText(svg) {
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg);
  }

  function downloadSVG(svg, filename) {
    save(new Blob([svgText(svg)], {type: "image/svg+xml"}), filename + ".svg");
  }

  function downloadPNG(svg, filename, scale) {
    scale = scale || 3;
    const W = Number(svg.getAttribute("width")), H = Number(svg.getAttribute("height"));
    const image = new Image();
    const url = URL.createObjectURL(new Blob([svgText(svg)], {type: "image/svg+xml"}));
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = W * scale;
      canvas.height = H * scale;
      const context = canvas.getContext("2d");
      context.scale(scale, scale);
      context.drawImage(image, 0, 0, W, H);
      URL.revokeObjectURL(url);
      canvas.toBlob(blob => save(blob, filename + ".png"), "image/png");
    };
    image.src = url;
  }

  return {plot, downloadSVG, downloadPNG, svgText};
})();
