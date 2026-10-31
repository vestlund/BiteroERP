/* Små SVG-diagram utan beroenden: staplar (grupperade/staplade) och linje, med hovringstips. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const fmt = v => Math.round(v).toLocaleString("sv-SE");
  const fmtKr = v => v.toLocaleString("sv-SE", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + " kr";
  const el = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  function skala(min, max) {
    if (min === max) { max = min + 1; }
    const spann = max - min, raa = spann / 5, pot = Math.pow(10, Math.floor(Math.log10(raa)));
    const steg = [1, 2, 2.5, 5, 10].map(s => s * pot).find(s => spann / s <= 6) || pot * 10;
    const lo = Math.floor(min / steg) * steg, hi = Math.ceil(max / steg) * steg;
    const ticks = []; for (let v = lo; v <= hi + steg / 2; v += steg) ticks.push(Math.round(v * 100) / 100);
    return { lo, hi, ticks };
  }

  function ram(behallare, hojd) {
    behallare.classList.add("diagram");
    const B = Math.max(behallare.clientWidth, 320), H = hojd;
    const svg = el("svg", { viewBox: `0 0 ${B} ${H}`, role: "img" }, behallare);
    const tips = document.createElement("div"); tips.className = "tips"; behallare.appendChild(tips);
    return { svg, tips, B, H };
  }

  function axlar(svg, s, y, x0, x1, kortTal) {
    const g = el("g", { class: "rutnat" }, svg), a = el("g", { class: "axel" }, svg);
    s.ticks.forEach(t => {
      el("line", { x1: x0, x2: x1, y1: y(t), y2: y(t), class: t === 0 ? "noll" : "" }, g);
      const txt = el("text", { x: x0 - 8, y: y(t) + 4, "text-anchor": "end" }, a);
      txt.textContent = kortTal ? kort(t) : fmt(t);
    });
    return a;
  }
  const kort = v => Math.abs(v) >= 1e6 ? (v / 1e6).toLocaleString("sv-SE", { maximumFractionDigits: 1 }) + " mn"
    : Math.abs(v) >= 1e3 ? Math.round(v / 1e3).toLocaleString("sv-SE") + " k" : fmt(v);

  function visaTips(d, x, y, html) {
    d.tips.innerHTML = html; d.tips.style.left = x + "px"; d.tips.style.top = y + "px"; d.tips.style.opacity = 1;
  }

  /* serier: [{namn, farg, varden:[], grupp:"a", ihalig:bool}] – samma grupp staplas, olika grupper står bredvid */
  window.stapeldiagram = function (behallare, { kategorier, serier, hojd = 260 }) {
    const d = ram(behallare, hojd), V = 54, T = 10, U = 26, HO = 8;
    const grupper = [...new Set(serier.map(s => s.grupp || s.namn))];
    const summor = kategorier.map((_, i) => grupper.map(g => serier.filter(s => (s.grupp || s.namn) === g)
      .reduce((a, s) => a + Math.max(0, s.varden[i]), 0)));
    const negmin = Math.min(0, ...serier.flatMap(s => s.varden));
    const s = skala(negmin, Math.max(1, ...summor.flat()));
    const y = v => T + (d.H - T - U) * (1 - (v - s.lo) / (s.hi - s.lo));
    axlar(d.svg, s, y, V, d.B - HO, true);
    const bandB = (d.B - V - HO) / kategorier.length;
    const stapelB = Math.min(28, (bandB * 0.72) / grupper.length - 2);
    const ax = el("g", { class: "axel" }, d.svg);
    kategorier.forEach((k, i) => {
      const mitt = V + bandB * (i + 0.5);
      const t = el("text", { x: mitt, y: d.H - 8, "text-anchor": "middle" }, ax); t.textContent = k;
      const total = grupper.length * (stapelB + 2) - 2;
      grupper.forEach((g, gi) => {
        let bas = 0;
        const x = mitt - total / 2 + gi * (stapelB + 2);
        serier.filter(se => (se.grupp || se.namn) === g).forEach(se => {
          const v = se.varden[i]; if (!v) return;
          const y0 = y(bas), y1 = y(bas + v);
          const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y0 - y1) - (bas ? 2 : 0));
          const r = el("rect", { x, y: top, width: stapelB, height: h, rx: 3,
            fill: se.ihalig ? "#fff" : se.farg, stroke: se.farg, "stroke-width": se.ihalig ? 1.6 : 0 }, d.svg);
          if (se.ihalig) r.setAttribute("fill", `url(#rand-${se.farg.slice(1)})`);
          bas += v;
        });
      });
      // träffyta för hela kategorin
      const hit = el("rect", { x: V + bandB * i, y: T, width: bandB, height: d.H - T - U, fill: "transparent" }, d.svg);
      hit.addEventListener("mouseenter", () => {
        hit.setAttribute("fill", "rgba(0,0,0,.035)");
        const rader = serier.filter(se => se.varden[i]).map(se => `<div><span class="prick" style="background:${se.farg}"></span>${se.namn}: <b>${fmtKr(se.varden[i])}</b></div>`).join("");
        visaTips(d, mitt * behallare.clientWidth / d.B, y(Math.max(...summor[i])) * behallare.clientHeight / d.H, `<div>${k}</div>${rader}`);
      });
      hit.addEventListener("mouseleave", () => { hit.setAttribute("fill", "transparent"); d.tips.style.opacity = 0; });
    });
    // randmönster för ihåliga (obetalda) staplar
    const defs = el("defs", {}, d.svg);
    serier.filter(se => se.ihalig).forEach(se => {
      const p = el("pattern", { id: `rand-${se.farg.slice(1)}`, width: 6, height: 6, patternUnits: "userSpaceOnUse",
        patternTransform: "rotate(45)" }, defs);
      el("rect", { width: 6, height: 6, fill: "#fff" }, p);
      el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: se.farg, "stroke-width": 2, opacity: .45 }, p);
    });
  };

  /* punkter: [{etikett, varde, detalj}] */
  window.linjediagram = function (behallare, { punkter, farg = "#2a78d6", negFarg = "#b42318", hojd = 260 }) {
    const d = ram(behallare, hojd), V = 54, T = 14, U = 26, HO = 14;
    const varden = punkter.map(p => p.varde);
    const s = skala(Math.min(0, ...varden), Math.max(0, ...varden));
    const y = v => T + (d.H - T - U) * (1 - (v - s.lo) / (s.hi - s.lo));
    axlar(d.svg, s, y, V, d.B - HO, true);
    const steg = (d.B - V - HO) / Math.max(1, punkter.length - 1);
    const x = i => V + i * steg;
    const ax = el("g", { class: "axel" }, d.svg);
    if (s.lo < 0) {
      const neg = el("rect", { x: V, y: y(0), width: d.B - V - HO, height: y(s.lo) - y(0), fill: "#fef3f2" });
      d.svg.insertBefore(neg, d.svg.firstChild);
    }
    el("polyline", { points: punkter.map((p, i) => `${x(i)},${y(p.varde)}`).join(" "), fill: "none", stroke: farg,
      "stroke-width": 2, "stroke-linejoin": "round" }, d.svg);
    punkter.forEach((p, i) => {
      const t = el("text", { x: x(i), y: d.H - 8, "text-anchor": "middle" }, ax); t.textContent = p.etikett;
      const neg = p.varde < 0;
      el("circle", { cx: x(i), cy: y(p.varde), r: 4.5, fill: "#fff", stroke: neg ? negFarg : farg, "stroke-width": 2 }, d.svg);
      const hit = el("rect", { x: x(i) - steg / 2, y: T, width: steg, height: d.H - T - U, fill: "transparent" }, d.svg);
      hit.addEventListener("mouseenter", () => visaTips(d, x(i) * behallare.clientWidth / d.B,
        y(p.varde) * behallare.clientHeight / d.H - 8,
        `<div>${p.etikett}</div><div>Saldo: <b>${fmtKr(p.varde)}</b></div>${p.detalj || ""}`));
      hit.addEventListener("mouseleave", () => d.tips.style.opacity = 0);
    });
  };
  /* flera linjer: serier [{namn, farg, varden, streckad}] mot gemensamma etiketter */
  window.linjer = function (behallare, { etiketter, serier, hojd = 260 }) {
    const d = ram(behallare, hojd), V = 54, T = 14, U = 26, HO = 14;
    const alla = serier.flatMap(s => s.varden.filter(v => v !== null));
    const s = skala(Math.min(0, ...alla), Math.max(1, ...alla));
    const y = v => T + (d.H - T - U) * (1 - (v - s.lo) / (s.hi - s.lo));
    axlar(d.svg, s, y, V, d.B - HO, true);
    const steg = (d.B - V - HO) / Math.max(1, etiketter.length - 1), x = i => V + i * steg;
    const ax = el("g", { class: "axel" }, d.svg);
    etiketter.forEach((e, i) => { const t = el("text", { x: x(i), y: d.H - 8, "text-anchor": "middle" }, ax); t.textContent = e; });
    serier.forEach(se => {
      const pts = se.varden.map((v, i) => v === null ? null : `${x(i)},${y(v)}`).filter(Boolean).join(" ");
      el("polyline", { points: pts, fill: "none", stroke: se.farg, "stroke-width": 2, "stroke-dasharray": se.streckad ? "5 4" : "",
        "stroke-linejoin": "round" }, d.svg);
      se.varden.forEach((v, i) => { if (v !== null && !se.streckad) el("circle", { cx: x(i), cy: y(v), r: 3.5, fill: "#fff", stroke: se.farg, "stroke-width": 2 }, d.svg); });
    });
    etiketter.forEach((e, i) => {
      const hit = el("rect", { x: x(i) - steg / 2, y: T, width: steg, height: d.H - T - U, fill: "transparent" }, d.svg);
      hit.addEventListener("mouseenter", () => {
        const max = Math.max(...serier.map(se => se.varden[i] ?? -Infinity));
        visaTips(d, x(i) * behallare.clientWidth / d.B, y(max) * behallare.clientHeight / d.H - 8,
          `<div>${e}</div>` + serier.filter(se => se.varden[i] !== null).map(se =>
            `<div><span class="prick" style="background:${se.farg}"></span>${se.namn}: <b>${fmtKr(se.varden[i])}</b></div>`).join(""));
      });
      hit.addEventListener("mouseleave", () => d.tips.style.opacity = 0);
    });
  };
})();
