// Shared dashboard pieces: period B / comparison A controls, stats table, charts with A/B overlay.
window.UI = (() => {
  const HOUR = 3600e3, DAY = 24 * HOUR, MONTH = 30 * DAY;
  const fmtMonth = ms => new Date(ms).toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = id => document.getElementById(id);

  function store(ns) {
    return {
      get(k, d) { try { const v = localStorage.getItem(`${ns}.${k}`); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
      set(k, v) { try { localStorage.setItem(`${ns}.${k}`, JSON.stringify(v)); } catch (e) {} },
    };
  }

  // ---------- formatting ----------
  const pad = n => String(n).padStart(2, "0");
  const toInput = ms => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const dayStart = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d).getTime(); };
  const dayEnd = s => dayStart(s) + DAY - 1;
  const fmtTime = ms => new Date(ms).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const fmtDay = ms => new Date(ms).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
  const fmtRange = (a, b) => b - a < 2 * DAY ? `${fmtTime(a)} – ${fmtTime(b)}` : `${fmtDay(a)} – ${fmtDay(b)}`;
  const yearBack = ms => { const d = new Date(ms); d.setFullYear(d.getFullYear() - 1); return d.getTime(); };
  const num = (v, digits) => v == null ? "–" : v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (v, digits) => {
    if (v == null) return "–";
    const r = Number(v.toFixed(digits)); // sign of the shown value, so no "−0,0"
    return (r > 0 ? "+" : r < 0 ? "−" : "") + num(Math.abs(r), digits);
  };

  // ---------- period B + comparison A ----------
  const PANEL = `
    <div class="row">
      <span class="tag"><span class="swatch"></span>Период B</span>
      <div class="quick">
        <button data-h="6">6 ч</button><button data-h="24">24 ч</button><button data-h="168">7 дн</button>
        <button data-h="720">30 дн</button><button data-h="all">Всё</button>
      </div>
      <label>с <input type="date" data-f="b-from"></label>
      <label>по <input type="date" data-f="b-to"></label>
    </div>
    <div class="row">
      <span class="tag"><span class="swatch a"></span>Сравнить с A</span>
      <select data-f="mode">
        <option value="none">без сравнения</option>
        <option value="prev">предыдущий период той же длины</option>
        <option value="day">те же часы сутки назад</option>
        <option value="week">те же дни неделю назад</option>
        <option value="year">те же даты год назад</option>
        <option value="custom">свои даты</option>
      </select>
      <span data-f="a-custom" class="row hidden" style="margin:0">
        <label>с <input type="date" data-f="a-from"></label>
        <label>по <input type="date" data-f="a-to"></label>
      </span>
      <span class="note" data-f="a-label" style="margin:0"></span>
    </div>`;

  class Periods {
    constructor(container, ns, onChange) {
      container.innerHTML = PANEL;
      this.el = name => container.querySelector(`[data-f="${name}"]`);
      this.buttons = [...container.querySelectorAll(".quick button")];
      this.store = store(ns);
      this.onChange = onChange;
      this.first = null; // ms of the oldest data point, for "all"
      const g = (k, d) => this.store.get(k, d);
      this.s = { quick: g("quick", "24"), bFrom: g("bFrom", null), bTo: g("bTo", null),
                 mode: g("mode", "none"), aFrom: g("aFrom", null), aTo: g("aTo", null) };
      if (!this.s.quick && (this.s.bFrom == null || this.s.bTo == null)) this.s.quick = "24";

      this.buttons.forEach(btn => btn.addEventListener("click", () => { this.s.quick = btn.dataset.h; this.changed(); }));
      const dates = (fromId, toId, apply) => () => {
        const f = this.el(fromId).value, t = this.el(toId).value;
        if (!f || !t) return;
        let from = dayStart(f), to = dayEnd(t);
        if (from > to) [from, to] = [dayStart(t), dayEnd(f)];
        apply(from, to);
        this.changed();
      };
      const onB = dates("b-from", "b-to", (from, to) => Object.assign(this.s, { quick: null, bFrom: from, bTo: to }));
      const onA = dates("a-from", "a-to", (from, to) => Object.assign(this.s, { aFrom: from, aTo: to }));
      this.el("b-from").addEventListener("change", onB);
      this.el("b-to").addEventListener("change", onB);
      this.el("a-from").addEventListener("change", onA);
      this.el("a-to").addEventListener("change", onA);
      this.el("mode").addEventListener("change", () => { this.s.mode = this.el("mode").value; this.changed(); });
    }

    changed() {
      for (const k of Object.keys(this.s)) this.store.set(k, this.s[k]);
      this.sync();
      this.onChange();
    }

    setBounds(firstMs) {
      this.first = firstMs;
      for (const id of ["b-from", "b-to", "a-from", "a-to"]) {
        if (firstMs) this.el(id).min = toInput(firstMs);
        this.el(id).max = toInput(Date.now());
      }
    }

    b() {
      const s = this.s;
      if (s.quick) {
        const now = Date.now();
        if (s.quick === "all") return [this.first || now - 30 * DAY, now];
        return [now - Number(s.quick) * HOUR, now];
      }
      return [s.bFrom, s.bTo];
    }

    a(b = this.b()) {
      const [from, to] = b;
      switch (this.s.mode) {
        case "prev": return [from - (to - from), from];
        case "day": return [from - DAY, to - DAY];
        case "week": return [from - 7 * DAY, to - 7 * DAY];
        case "year": return [yearBack(from), yearBack(to)];
        case "custom": return this.s.aFrom != null && this.s.aTo != null ? [this.s.aFrom, this.s.aTo] : null;
        default: return null;
      }
    }

    sync() {
      const s = this.s, b = this.b();
      this.buttons.forEach(btn => btn.classList.toggle("active", btn.dataset.h === s.quick));
      if (b[0] != null) this.el("b-from").value = toInput(b[0]);
      if (b[1] != null) this.el("b-to").value = toInput(b[1]);
      this.el("mode").value = s.mode;
      this.el("a-custom").classList.toggle("hidden", s.mode !== "custom");
      if (s.mode === "custom") {
        if (s.aFrom == null || s.aTo == null) {
          // sensible start for own dates: the period right before B
          s.aFrom = dayStart(toInput(b[0] - (b[1] - b[0]))); s.aTo = dayEnd(toInput(b[0] - 1));
        }
        this.el("a-from").value = toInput(s.aFrom);
        this.el("a-to").value = toInput(s.aTo);
      }
      const a = this.a(b);
      this.el("a-label").textContent = a ? `A: ${fmtRange(a[0], a[1])}` : "";
    }
  }

  // ---------- data helpers ----------
  // API columns use unix seconds; charts and lookups use ms, A shifted onto B
  const toMs = (d, shift = 0) => Object.fromEntries(Object.entries(d).map(([k, v]) => [k, k === "ts" ? v.map(t => t * 1000 + shift) : v]));
  // median spacing: an average would be inflated by the very gaps it is used to detect
  const typicalStep = set => {
    if (!set || set.ts.length < 2) return 600e3;
    const d = set.ts.slice(1).map((t, i) => t - set.ts[i]).sort((a, b) => a - b);
    return d[d.length >> 1] || 600e3;
  };

  // nearest point to t in a columnar {ts (ms, ascending), [key]} set, or null if too far
  function nearest(set, key, t, maxGap) {
    if (!set || !set.ts.length) return null;
    let lo = 0, hi = set.ts.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; (set.ts[mid] < t ? lo = mid : hi = mid); }
    const i = Math.abs(set.ts[lo] - t) <= Math.abs(set.ts[hi] - t) ? lo : hi;
    if (Math.abs(set.ts[i] - t) > maxGap) return null;
    return { t: set.ts[i], v: set[key][i] };
  }

  // [[t, v], ...] -> same, with a null point inside every gap longer than `maxGap`,
  // so the line breaks there instead of bridging days without data
  function breakGaps(pts, maxGap) {
    const out = [];
    for (let i = 0; i < pts.length; i++) {
      if (i && pts[i][0] - pts[i - 1][0] > maxGap) out.push([(pts[i][0] + pts[i - 1][0]) / 2, null]);
      out.push(pts[i]);
    }
    return out;
  }
  const gapLimit = step => Math.max(3 * step, 30 * 60e3);

  // sum `key` into buckets of `size` ms (local-day buckets when size >= DAY)
  function bucketSum(set, key, size) {
    const out = { ts: [], [key]: [] };
    if (!set) return out;
    const bucket = t => {
      const d = new Date(t);
      if (size >= MONTH) return new Date(d.getFullYear(), d.getMonth(), 1).getTime();
      if (size >= DAY) return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      return Math.floor(t / size) * size;
    };
    let cur = null, sum = null;
    set.ts.forEach((t, i) => {
      const b = bucket(t), v = set[key][i];
      if (b !== cur) {
        if (cur != null) { out.ts.push(cur); out[key].push(sum); }
        cur = b; sum = null;
      }
      if (v != null) sum = (sum || 0) + v;
    });
    if (cur != null) { out.ts.push(cur); out[key].push(sum); }
    return out;
  }

  // ---------- stats + comparison table ----------
  function stats(values) {
    const v = values.filter(x => x != null);
    if (!v.length) return null;
    const sum = v.reduce((a, b) => a + b, 0);
    return { mean: sum / v.length, min: Math.min(...v), max: Math.max(...v), sum, n: v.length,
             above: t => 100 * v.filter(x => x > t).length / v.length };
  }

  const ROWS = {
    mean: { label: "среднее", get: s => s.mean },
    min: { label: "минимум", get: s => s.min },
    max: { label: "максимум", get: s => s.max },
    sum: { label: "сумма", get: s => s.sum },
  };

  // metrics: [{key, title, unit, digits, rows: ["mean", ...] or row objects {label, get, pp}, lowerIsBetter}]
  function renderCompareTable(table, metrics, setB, setA) {
    const out = [`<tr><th>Показатель</th><th>A <span class="swatch a"></span></th><th>B <span class="swatch"></span></th><th>Δ (B − A)</th><th>Δ%</th></tr>`];
    for (const m of metrics) {
      const sB = stats(setB[m.key] || []), sA = stats(setA[m.key] || []);
      if (!sB && !sA) continue;
      m.rows.map(r => typeof r === "string" ? ROWS[r] : r).forEach((r, i) => {
        const b = sB ? r.get(sB) : null, a = sA ? r.get(sA) : null;
        const digits = r.pp ? 1 : (r.digits ?? m.digits);
        const unit = r.pp ? "%" : (r.unit ?? m.unit);
        const d = a != null && b != null ? b - a : null;
        const pct = !r.pp && d != null && a ? 100 * d / Math.abs(a) : null;
        // only metrics with a "good" direction get colour; the rest stay neutral
        const cls = !m.lowerIsBetter || d == null || Math.abs(d) < 1e-9 ? "neutral" : d < 0 ? "pos-good" : "pos-bad";
        out.push(`<tr class="${i === 0 ? "first" : ""}">
          <td>${i === 0 ? `<span class="metric">${m.title.split(",")[0]}</span> · ` : ""}${r.label}</td>
          <td>${num(a, digits)} ${a == null ? "" : unit}</td>
          <td>${num(b, digits)} ${b == null ? "" : unit}</td>
          <td class="${cls}">${d == null ? "–" : `${signed(d, digits)} ${r.pp ? "п.п." : unit}`}</td>
          <td class="${cls}">${r.pp ? "" : pct == null ? "–" : signed(pct, 1) + "%"}</td></tr>`);
      });
    }
    out.push(`<tr class="first"><td class="neutral">точек в периоде</td><td class="neutral">${setA.ts.length}</td><td class="neutral">${setB.ts.length}</td><td></td><td></td></tr>`);
    table.innerHTML = out.join("");
  }

  // ---------- charts ----------
  const SPARSE = 60; // below this many points a line chart also draws its points
  const dot = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:${c};margin-right:6px"></span>`;

  // ---------- drag-to-zoom ----------
  // Drag across a chart to zoom its time axis to the selection (charts linked with
  // echarts.connect follow); double-click resets. Off on touch screens, where a
  // drag has to scroll the page.
  const canZoom = window.matchMedia("(pointer: fine)").matches;

  function zoomOptions() {
    if (!canZoom) return {};
    return {
      // "inside" holds the zoom window; wheel and drag-to-pan stay off, as before
      // weakFilter: the y axis re-fits to what is in view, lines still run to the edges
      dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "weakFilter",
                   zoomOnMouseWheel: false, moveOnMouseMove: false, moveOnMouseWheel: false, preventDefaultMouseMove: false }],
      // the box-select tool needs a toolbox; it is kept invisible (empty icons)
      toolbox: { show: true, itemSize: 1, showTitle: false, right: -10, top: -10,
                 feature: { dataZoom: { yAxisIndex: "none", icon: { zoom: "path://", back: "path://" },
                                        brushStyle: { color: "rgba(120, 140, 170, 0.18)", borderColor: "rgba(120, 140, 170, 0.6)" } } } },
    };
  }

  // call after every setOption(..., true): a fresh option drops the select cursor
  function armZoom(chart) {
    if (!canZoom) return;
    chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: true });
    if (!chart.__zoomReset) {
      chart.__zoomReset = true;
      chart.getZr().on("dblclick", () => chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
    }
  }

  function resetZoom(chart) {
    if (canZoom) chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  }

  // ---------- pressure ----------
  const HPA_TO_MM = 0.750062;
  // sea-level pressure, hPa (normal 1013.25 hPa = 760 mmHg); Cyprus usually sits 1005-1020
  // [upper bound, short label for the chart margin, full description]
  const PRESSURE_SCALE = [
    [987, "глуб. циклон", "глубокий циклон, шторм"], [1000, "циклон", "циклон, ненастье"],
    [1009, "пониженное", "пониженное"], [1017, "норма", "нормальное"],
    [1027, "повышенное", "повышенное, антициклон"], [Infinity, "высокое", "высокое, антициклон"],
  ];
  const pressureLabel = seaHpa => PRESSURE_SCALE.find(([to]) => seaHpa < to)[2];
  // bands in display units for a sensor that reads `offset` hPa below sea level
  function pressureBands(offset = 0, factor = 1) {
    return PRESSURE_SCALE.map(([to, name], i) => ({  // short name: it has to fit the right margin
      from: ((i ? PRESSURE_SCALE[i - 1][0] : -Infinity) - offset) * factor,
      to: (to - offset) * factor, label: name,
    }));
  }
  // WMO pressure tendency over 3 hours
  function tendency(d3h) {
    const a = Math.abs(d3h), dir = d3h > 0 ? "растёт" : "падает";
    if (a < 0.5) return "стабильно";
    if (a < 1.6) return `медленно ${dir}`;
    if (a < 3.6) return dir;
    if (a < 6) return `быстро ${dir}`;
    return `очень быстро ${dir}`;
  }

  // ---------- sun ----------
  // Solar position from the standard low-precision ephemeris (as in SunCalc / the
  // Astronomical Almanac): ~0.1-0.3 degrees, plenty for elevation charts and
  // sunrise/sunset to the minute.
  const RAD = Math.PI / 180;
  function sunPosition(ms, lat, lon) {
    const d = ms / 864e5 - 0.5 + 2440588 - 2451545;          // days since J2000
    const M = RAD * (357.5291 + 0.98560028 * d);              // mean anomaly
    const C = RAD * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
    const L = M + C + RAD * 102.9372 + Math.PI;               // ecliptic longitude
    const e = RAD * 23.4397;                                  // obliquity
    const dec = Math.asin(Math.sin(e) * Math.sin(L));
    const ra = Math.atan2(Math.sin(L) * Math.cos(e), Math.cos(L));
    const H = RAD * (280.16 + 360.9856235 * d) + RAD * lon - ra; // hour angle
    const phi = RAD * lat;
    const elevation = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H)) / RAD;
    const azimuth = (Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi)) / RAD + 180) % 360;
    return { elevation, azimuth };
  }
  // sunrise / sunset (upper limb with refraction: -0.833 deg), solar noon, for the local day of `ms`
  function sunTimes(ms, lat, lon) {
    const day = new Date(ms); day.setHours(0, 0, 0, 0);
    const step = 60e3, h0 = -0.833;
    let rise = null, set = null, noon = null, best = -91, prev = sunPosition(day.getTime(), lat, lon).elevation;
    for (let t = day.getTime() + step; t <= day.getTime() + 864e5; t += step) {
      const el = sunPosition(t, lat, lon).elevation;
      if (prev < h0 && el >= h0) rise = t;
      if (prev >= h0 && el < h0) set = t;
      if (el > best) { best = el; noon = t; }
      prev = el;
    }
    return { rise, set, noon, maxElevation: best };
  }
  // elevation -> phase of the day (standard twilight limits)
  const SUN_SCALE = [[-18, "ночь"], [-12, "астрономические сумерки"], [-6, "навигационные сумерки"],
                     [-0.833, "гражданские сумерки"], [6, "низкое солнце"], [Infinity, "день"]];
  const sunPhase = el => SUN_SCALE.find(([to]) => el < to)[1];
  const SUN_COLORS = ["--sky-night", "--sky-astro", "--sky-nautical", "--sky-civil", "--sky-low", "--sky-day"];
  const sunBands = () => SUN_SCALE.map(([to, label], i) => ({ from: i ? SUN_SCALE[i - 1][0] : -Infinity, to, label, color: css(SUN_COLORS[i]) }));
  // legend rows: [colour, name, range in degrees]
  const sunLegend = () => SUN_SCALE.map(([to, label], i) => {
    const from = i ? SUN_SCALE[i - 1][0] : null, f = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toString().replace(".", ",") + "°";
    const range = from == null ? `ниже ${f(to)}` : Number.isFinite(to) ? `${f(to)} … ${f(from)}` : `выше ${f(from)}`;
    return [css(SUN_COLORS[i]), label, range];
  }).reverse();
  const compass = az => ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"][Math.round(az / 45) % 8];

  // UV index, WHO scale
  const UV_SCALE = [[3, "низкий"], [6, "умеренный"], [8, "высокий"], [11, "очень высокий"], [Infinity, "экстремальный"]];
  const uvLabel = v => UV_SCALE.find(([to]) => v < to)[1];
  const uvBands = () => UV_SCALE.map(([to, label], i) => ({ to, label: `${label} (${i ? UV_SCALE[i - 1][0] : 0}${Number.isFinite(to) ? "–" + (to - 1) : "+"})` }));

  // ---------- indoor comfort ----------
  // Humidex (Environment Canada): temperature + humidity, no wind -> suits indoors
  const humidex = (t, rh) => t == null || rh == null ? null
    : t + 0.5555 * (6.112 * Math.pow(10, 7.5 * t / (237.7 + t)) * rh / 100 - 10);
  // [upper bound, short label for the chart margin, full description]
  // the official Environment Canada scale has just these steps (it starts at 20)
  const HUMIDEX_SCALE = [[20, "ниже шкалы (< 20)", "ниже 20, шкала не применяется"],
                         [30, "без дискомфорта (20–29)", "без дискомфорта"],
                         [40, "некоторый дискомфорт (30–39)", "некоторый дискомфорт"],
                         [46, "сильный дискомфорт (40–45)", "сильный дискомфорт, избегать нагрузок"],
                         [Infinity, "опасно (46+)", "опасно, возможен тепловой удар"]];
  const humidexLabel = h => HUMIDEX_SCALE.find(([to]) => h < to)[2];
  const humidexBands = () => HUMIDEX_SCALE.map(([to, label], i) => ({ from: i ? HUMIDEX_SCALE[i - 1][0] : -Infinity, to, label }));

  // PMV / PPD (ISO 7730, Fanger), port of the standard's reference code; checked against
  // the standard's worked examples and pythermalcomfort
  function pmv(ta, tr, vel, rh, met, clo) {
    const pa = rh * 10 * Math.exp(16.6536 - 4030.183 / (ta + 235));
    const icl = 0.155 * clo, m = met * 58.15, mw = m;
    const fcl = icl <= 0.078 ? 1 + 1.29 * icl : 1.05 + 0.645 * icl;
    const hcf = 12.1 * Math.sqrt(vel), taa = ta + 273, tra = tr + 273;
    const tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1);
    const p1 = icl * fcl, p2 = p1 * 3.96, p3 = p1 * 100, p4 = p1 * taa;
    const p5 = 308.7 - 0.028 * mw + p2 * Math.pow(tra / 100, 4);
    let xn = tcla / 100, xf = tcla / 50, hc = hcf, n = 0;
    while (Math.abs(xn - xf) > 0.00015 && n++ < 150) {
      xf = (xf + xn) / 2;
      hc = Math.max(hcf, 2.38 * Math.pow(Math.abs(100 * xf - taa), 0.25));
      xn = (p5 + p4 * hc - p2 * Math.pow(xf, 4)) / (100 + p3 * hc);
    }
    const tcl = 100 * xn - 273;
    const hl1 = 3.05e-3 * (5733 - 6.99 * mw - pa), hl2 = mw > 58.15 ? 0.42 * (mw - 58.15) : 0;
    const hl3 = 1.7e-5 * m * (5867 - pa), hl4 = 0.0014 * m * (34 - ta);
    const hl5 = 3.96 * fcl * (Math.pow(xn, 4) - Math.pow(tra / 100, 4)), hl6 = fcl * hc * (tcl - ta);
    const v = (0.303 * Math.exp(-0.036 * m) + 0.028) * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6);
    return { pmv: v, ppd: 100 - 95 * Math.exp(-0.03353 * v ** 4 - 0.2179 * v ** 2) };
  }
  // assumptions for a flat in Cyprus: seated (1.1 met), still air, walls at air temperature,
  // clothing by season (clo): light summer clothes, trousers + sweater in winter
  const clothing = date => { const m = date.getMonth() + 1; return m >= 5 && m <= 9 ? 0.5 : m === 4 || m === 10 ? 0.7 : 1.0; };
  const PMV_LABELS = [[-2.5, "холодно"], [-1.5, "прохладно"], [-0.5, "слегка прохладно"], [0.5, "нейтрально"],
                      [1.5, "слегка тепло"], [2.5, "тепло"], [Infinity, "жарко"]];
  const CLOTHING = { light: [0.5, "лёгкая"], medium: [0.7, "средняя"], warm: [1.0, "тёплая"] };
  const cloFor = (date, choice = "auto") => CLOTHING[choice] ? CLOTHING[choice][0] : clothing(date);
  // PMV of a reading (seated, still air, walls at air temperature)
  const pmvAt = (t, rh, date, choice) => t == null || rh == null ? null : pmv(t, t, 0.1, rh, 1.1, cloFor(date, choice)).pmv;
  const pmvLabel = v => PMV_LABELS.find(([to]) => v < to)[1];
  const pmvBands = () => PMV_LABELS.map(([to, label], i) => ({ from: i ? PMV_LABELS[i - 1][0] : -Infinity, to, label }));
  // clo: a CLOTHING key, or "auto" (by season)
  function comfort(t, rh, date = new Date(), choice = "auto") {
    if (t == null || rh == null) return null;
    const clo = CLOTHING[choice] ? CLOTHING[choice][0] : clothing(date);
    const at = x => pmv(x, x, 0.1, rh, 1.1, clo).pmv;
    // the air temperature at which PMV = 0 for this humidity and clothing
    let lo = 5, hi = 40;
    for (let i = 0; i < 40; i++) { const mid = (lo + hi) / 2; at(mid) < 0 ? lo = mid : hi = mid; }
    const r = pmv(t, t, 0.1, rh, 1.1, clo);
    return { ...r, clo, neutral: lo, delta: t - lo, label: PMV_LABELS.find(([to]) => r.pmv < to)[1],
             clothing: Object.values(CLOTHING).find(([c]) => c === clo)[1] };
  }

  // band names sit inside the plot, top-left of their band, on a translucent pad so
  // they stay readable over the line (the right margin was too narrow for them)
  const bandLabel = text => ({
    show: true, position: "insideTopLeft", distance: 4, formatter: text, fontSize: 10, color: css("--muted"),
    backgroundColor: css("--card"), padding: [1, 4], borderRadius: 3,
  });

  // Shaded horizontal reference bands with names, from 0 up to the band holding the
  // highest value in view (so a calm week isn't drawn on a 0-33 m/s axis).
  // bands: [{to: upper bound, label}], ascending; the last `to` may be Infinity.
  // Bands: shading behind the data (markArea on the B series); the names are drawn by
  // the chart as `graphic` text on top (markArea labels always end up under the line).
  // A band thinner than ~8 % of the axis gets no name: it wouldn't fit.
  function bandLayers(spans, range, seriesB) {
    seriesB.markArea = {
      silent: true, label: { show: false },
      data: spans.map(({ from, to, i, color }) => [{ yAxis: from, itemStyle: { color: color || (i % 2 ? css("--band") : "transparent") } }, { yAxis: to }]),
    };
    return spans; // which ones get a name is decided in pixels, see placeBandNames
  }

  function bandsOption(bands, sets, key, seriesB) {
    const vals = [sets.b, sets.a].filter(Boolean).flatMap(s => s[key]).filter(v => v != null);
    if (bands[0].from !== undefined) return rangeBands(bands, vals, seriesB);
    const hi = vals.length ? Math.max(...vals) : 0;
    let n = bands.findIndex(b => hi < b.to);
    n = n < 0 ? bands.length - 1 : n;
    n = Math.max(n, 2); // always show a few bands for scale
    const top = Number.isFinite(bands[n].to) ? bands[n].to : hi * 1.1;
    const spans = bands.slice(0, n + 1).map((b, i) => ({ from: i ? bands[i - 1].to : 0, to: Math.min(b.to, top), label: b.label, color: b.color, i }));
    return { yAxis: { min: 0, max: top }, labels: bandLayers(spans, [0, top], seriesB) };
  }

  // Bands with explicit {from, to} that don't start at zero (pressure): the axis
  // follows the data, and only the bands crossing it are drawn, clipped to it.
  function rangeBands(bands, vals, seriesB) {
    if (!vals.length) return {};
    const lo = Math.min(...vals), hi = Math.max(...vals), pad = Math.max(1, (hi - lo) * 0.15);
    // axis ends on round values (1, 2, 5 x 10^n), e.g. -90 rather than -77
    const raw = (hi - lo + 2 * pad) / 6, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map(k => k * mag).find(v => v >= raw);
    const view = [Math.floor((lo - pad) / step) * step, Math.ceil((hi + pad) / step) * step];
    const spans = bands.map((b, i) => ({ ...b, i })).filter(b => b.to > view[0] && b.from < view[1])
      .map(b => ({ from: Math.max(b.from, view[0]), to: Math.min(b.to, view[1]), label: b.label, color: b.color, i: b.i }));
    return { yAxis: { min: view[0], max: view[1] }, labels: bandLayers(spans, view, seriesB) };
  }

  // One metric, B solid + A dashed (shifted onto B). `bar: true` draws sums per bucket.
  class SeriesChart {
    constructor(el, m) {
      this.el = el; this.m = m;
      this.chart = echarts.init(el);
      this.sets = { b: null, a: null }; this.offset = 0; this.step = 600e3;
      this.applyTheme();
    }

    tooltip(params) {
      const m = this.m, t = params[0].axisValue;
      const gap = this.step * (m.bar ? 0.5 : 1);
      const b = nearest(this.sets.b, m.key, t, gap), a = nearest(this.sets.a, m.key, t, gap);
      const when = ms => m.bar && this.step >= MONTH ? fmtMonth(ms)
        : m.daily || (m.bar && this.step >= DAY) ? fmtDay(ms) : fmtTime(ms);
      const extra = v => m.describe ? `<div style="color:${css("--muted")};margin-left:16px">${m.describe(v)}</div>` : "";
      const line = (p, color, label, at) => p && p.v != null
        ? `<div>${dot(color)}${label} ${when(at)}: <b>${num(p.v, m.digits)} ${m.unit}</b>${extra(p.v)}</div>` : "";
      // "B"/"A" only mean something when there is a comparison
      let html = line(b, css(m.color), this.sets.a ? "B" : "", b && b.t) + line(a, css("--cmp"), "A", a && a.t - this.offset);
      if (m.extra) {
        const x = nearest(this.sets.b, m.extra.key, t, gap);
        if (x && x.v != null) html += `<div style="color:${css("--muted")};margin-left:16px">${m.extra.label}: <b>${num(x.v, m.digits)}</b>${m.describe ? " · " + m.describe(x.v) : ""}</div>`;
      }
      if (a && b && a.v != null && b.v != null) {
        const d = b.v - a.v;
        const pct = a.v ? ` (${signed(100 * d / Math.abs(a.v), 1)}%)` : "";
        html += `<div style="color:${css("--muted")}">Δ ${signed(d, m.digits)} ${m.unit}${pct}</div>`;
      }
      return html || fmtTime(t);
    }

    applyTheme() {
      const m = this.m, color = css(m.color), type = m.bar ? "bar" : "line";
      const series = (name, c, extra) => Object.assign({ name, type, data: [] },
        m.bar ? { itemStyle: { color: c }, barMaxWidth: 14, barGap: "-60%" }
              : { showSymbol: false, sampling: "lttb", lineStyle: { width: 2, color: c }, itemStyle: { color: c } }, extra);
      const opt = {
        animation: false,
        title: { text: m.title, left: 12, top: 8, textStyle: { fontSize: 13, color: css("--text"), fontWeight: 600 } },
        grid: { left: 56, right: 20, top: 40, bottom: 32 },
        tooltip: {
          trigger: "axis", formatter: p => this.tooltip(p), confine: true,
          backgroundColor: css("--card"), borderColor: css("--border"), textStyle: { color: css("--text") },
        },
        xAxis: { type: "time", axisLine: { lineStyle: { color: css("--border") } },
                 axisLabel: { color: css("--muted"), hideOverlap: true }, splitLine: { show: false } },
        yAxis: { type: "value", scale: !m.bar && !m.zeroBased, min: m.bar || m.zeroBased ? 0 : null,
                 axisLabel: { color: css("--muted"), formatter: v => v.toFixed(m.digits === 0 ? 0 : 1) },
                 splitLine: { lineStyle: { color: css("--grid") } } },
        series: [
          series("B", color, { z: 3 }),
          m.bar ? series("A", css("--cmp"), { z: 2, itemStyle: { color: "transparent", borderColor: css("--cmp"), borderType: "dashed", borderWidth: 1 } })
                : series("A", css("--cmp"), { z: 2, lineStyle: { width: 1.5, type: "dashed", color: css("--cmp") } }),
        ],
      };
      Object.assign(opt, zoomOptions());
      if (m.decorate) m.decorate(opt);
      this.chart.setOption(opt, true);
      armZoom(this.chart);
      if (this.sets.b) this.render();
    }

    // setB/setA: ms-based columnar sets (A already shifted by `offset`)
    // fit: start the x axis at this chart's own first point instead of the period start
    // (for "Всё": every chart begins where its data begins)
    set(setB, setA, offset, range, { fit = false } = {}) {
      const m = this.m;
      this.offset = offset;
      if (fit) {
        const first = s => s && s.ts.length ? s.ts[s.ts.findIndex((t, i) => s[m.key][i] != null)] : null;
        const firsts = [first(setB), first(setA)].filter(x => x != null);
        if (firsts.length) range = [Math.min(...firsts), range[1]];
      }
      this.range = range;
      resetZoom(this.chart); // new data / new period: start unzoomed
      if (m.bar) {
        const span = range[1] - range[0];
        const size = span > 400 * DAY ? MONTH : span > 3 * DAY ? DAY : HOUR;
        this.sets = { b: bucketSum(setB, m.key, size), a: setA ? bucketSum(setA, m.key, size) : null };
        this.step = size;
      } else {
        this.sets = { b: setB, a: setA };
        this.step = typicalStep(setB);
      }
      this.render();
    }

    render() {
      const pts = s => {
        if (!s) return [];
        const raw = s.ts.map((t, i) => [t, s[this.m.key][i]]);
        return this.m.bar ? raw : breakGaps(raw, gapLimit(this.step));
      };
      // a line through one or two points draws nothing: show the points while data is sparse
      const sparse = s => !this.m.bar && s && s.ts.length < SPARSE;
      const series = s => ({ data: pts(s), ...(this.m.bar ? {} : { showSymbol: !!sparse(s), symbolSize: 5 }) });
      const upd = { xAxis: { min: this.range[0], max: this.range[1] }, series: [series(this.sets.b), series(this.sets.a)] };
      if (this.m.axis) upd.yAxis = this.m.axis(this.sets);
      this.bandNames = null;
      const ex = this.m.extra;
      if (ex && this.sets.b) {
        // a second line of the same quantity from another column (e.g. altitude-corrected UV)
        upd.series.push({ type: "line", silent: true, showSymbol: false, z: 4,
          lineStyle: { width: 1.5, type: ex.dash || "dashed", color: css(ex.color || this.m.color) },
          data: breakGaps(this.sets.b.ts.map((t, i) => [t, this.sets.b[ex.key][i]]), gapLimit(this.step)) });
      }
      if (this.m.bands) {
        const bandSets = ex && this.sets.b ? { b: { [this.m.key]: this.sets.b[this.m.key].concat(this.sets.b[ex.key]) }, a: this.sets.a } : this.sets;
        const { labels, ...rest } = bandsOption(this.m.bands, bandSets, this.m.key, upd.series[0]);
        Object.assign(upd, rest);
        this.bandNames = labels || null;
        // physical limits of the quantity (sun elevation: ±90°) beat the rounded axis
        if (this.m.limits && upd.yAxis) {
          upd.yAxis.min = Math.max(upd.yAxis.min, this.m.limits[0]);
          upd.yAxis.max = Math.min(upd.yAxis.max, this.m.limits[1]);
        }
      }
      // dashed "now" line for charts that reach into the forecast
      if (this.m.nowLine) {
        const now = Date.now(), inView = now >= this.range[0] && now <= this.range[1];
        upd.series.push({
          type: "line", data: [], silent: true, tooltip: { show: false },
          markLine: { silent: true, symbol: "none", animation: false,
                      lineStyle: { type: "dashed", width: 1.5, color: css("--text"), opacity: 0.55 },
                      label: { formatter: "сейчас", position: "end", distance: 2, color: css("--muted"), fontSize: 11 },
                      data: inView ? [{ xAxis: now }] : [] },
        });
      }
      this.chart.setOption(upd);
      this.placeBandNames();
    }

    // band names as graphic text at the top-left of each band, above everything else
    placeBandNames() {
      if (!this.range) return;
      const names = this.bandNames || [];
      // x from the grid itself: the time axis may be zoomed, the left edge stays put
      const rect = this.chart.getModel().getComponent("grid").coordinateSystem.getRect();
      const px = v => this.chart.convertToPixel({ yAxisIndex: 0 }, v);
      // a name needs ~16 px of band height to fit
      const elements = names.filter(({ from, to }) => Math.abs(px(from) - px(to)) >= 16).map(({ to, label }) => {
        const y = px(to);
        return { type: "text", silent: true, z: 100, left: rect.x + 6, top: Math.max(rect.y, y) + 3,
                 style: { text: label, fill: css("--muted"), font: "10px sans-serif",
                          backgroundColor: css("--card"), padding: [1, 4], borderRadius: 3 } };
      });
      this.chart.setOption({ graphic: { elements } }, { replaceMerge: ["graphic"] });
    }

    show(on) { this.el.classList.toggle("hidden", !on); if (on) { this.chart.resize(); this.placeBandNames(); } }
  }

  function linkCharts(charts) {
    echarts.connect(charts.map(c => c.chart));
    window.addEventListener("resize", () => charts.forEach(c => { c.chart.resize(); if (c.placeBandNames) c.placeBandNames(); }));
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => charts.forEach(c => c.applyTheme()));
  }

  return { sunPosition, sunTimes, sunPhase, sunBands, sunLegend, compass, uvLabel, uvBands, humidex, humidexLabel, humidexBands, pmv, comfort, pmvAt, pmvLabel, pmvBands, breakGaps, gapLimit, HPA_TO_MM, pressureLabel, pressureBands, tendency, zoomOptions, armZoom, resetZoom, HOUR, DAY, MONTH, SPARSE, css, $, store, toInput, fmtTime, fmtDay, fmtRange, num, signed, dot,
           Periods, toMs, nearest, typicalStep, stats, renderCompareTable, SeriesChart, linkCharts };
})();
