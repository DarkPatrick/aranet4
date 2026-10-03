(() => {
  // Aranet's own CO2 bands: green < 1000, amber 1000-1400, red > 1400 ppm
  const CO2_WARN = 1000, CO2_BAD = 1400;
  const REFRESH_MS = 5 * 60 * 1000;
  const HOUR = 3600e3, DAY = 24 * HOUR;

  const METRICS = [
    { key: "co2", el: "c-co2", title: "CO₂, ppm", unit: "ppm", digits: 0, color: "--co2", lowerIsBetter: true },
    { key: "temperature", el: "c-temp", title: "Температура, °C", unit: "°C", digits: 1, color: "--temp" },
    { key: "humidity", el: "c-hum", title: "Влажность, %", unit: "%", digits: 0, color: "--hum" },
    { key: "pressure", el: "c-pres", title: "Давление, hPa", unit: "hPa", digits: 1, color: "--pres" },
  ];

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = id => document.getElementById(id);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("aranet." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("aranet." + k, JSON.stringify(v)); } catch (e) {} },
  };

  // ---------- state ----------
  const state = {
    quick: store.get("quick", "24"),        // "6" | "24" | ... | "all" | null (= own dates)
    bFrom: store.get("bFrom", null), bTo: store.get("bTo", null),
    mode: store.get("mode", "none"),
    aFrom: store.get("aFrom", null), aTo: store.get("aTo", null),
  };
  let dataRange = { first: null, last: null };

  // ---------- dates ----------
  const pad = n => String(n).padStart(2, "0");
  const toInput = ms => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const dayStart = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d).getTime(); };
  const dayEnd = s => dayStart(s) + DAY - 1;
  const fmtTime = ms => new Date(ms).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const fmtDay = ms => new Date(ms).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
  const fmtRange = (a, b) => b - a < 2 * DAY ? `${fmtTime(a)} – ${fmtTime(b)}` : `${fmtDay(a)} – ${fmtDay(b)}`;
  const yearBack = ms => { const d = new Date(ms); d.setFullYear(d.getFullYear() - 1); return d.getTime(); };

  function periodB() {
    if (state.quick) {
      const now = Date.now();
      if (state.quick === "all") return [dataRange.first ? dataRange.first * 1000 : now - 30 * DAY, now];
      return [now - Number(state.quick) * HOUR, now];
    }
    return [state.bFrom, state.bTo];
  }

  function periodA([bFrom, bTo]) {
    switch (state.mode) {
      case "prev": return [bFrom - (bTo - bFrom), bFrom];
      case "day": return [bFrom - DAY, bTo - DAY];
      case "week": return [bFrom - 7 * DAY, bTo - 7 * DAY];
      case "year": return [yearBack(bFrom), yearBack(bTo)];
      case "custom": return state.aFrom != null && state.aTo != null ? [state.aFrom, state.aTo] : null;
      default: return null;
    }
  }

  // ---------- stats ----------
  function stats(values) {
    const v = values.filter(x => x != null);
    if (!v.length) return null;
    const sum = v.reduce((a, b) => a + b, 0);
    return { mean: sum / v.length, min: Math.min(...v), max: Math.max(...v), n: v.length,
             above: t => 100 * v.filter(x => x > t).length / v.length };
  }

  function statRows(m) {
    const rows = [
      { label: "среднее", get: s => s.mean },
      { label: "минимум", get: s => s.min },
      { label: "максимум", get: s => s.max },
    ];
    if (m.key === "co2") {
      rows.push({ label: `доля времени > ${CO2_WARN} ppm`, get: s => s.above(CO2_WARN), pp: true });
      rows.push({ label: `доля времени > ${CO2_BAD} ppm`, get: s => s.above(CO2_BAD), pp: true });
    }
    return rows;
  }

  const num = (v, digits) => v == null ? "–" : v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (v, digits) => {
    if (v == null) return "–";
    const r = Number(v.toFixed(digits)); // sign of the shown value, so no "−0,0"
    return (r > 0 ? "+" : r < 0 ? "−" : "") + num(Math.abs(r), digits);
  };

  function renderTable(dataB, dataA) {
    const rows = [`<tr><th>Показатель</th><th>A <span class="swatch a"></span></th><th>B <span class="swatch"></span></th><th>Δ (B − A)</th><th>Δ%</th></tr>`];
    for (const m of METRICS) {
      const sB = stats(dataB[m.key]), sA = stats(dataA[m.key]);
      statRows(m).forEach((r, i) => {
        const b = sB ? r.get(sB) : null, a = sA ? r.get(sA) : null;
        const digits = r.pp ? 1 : m.digits;
        const unit = r.pp ? "%" : m.unit;
        const d = a != null && b != null ? b - a : null;
        const pct = !r.pp && d != null && a ? 100 * d / Math.abs(a) : null;
        // only CO2 has a "good" direction; everything else stays neutral
        const cls = !m.lowerIsBetter || d == null || Math.abs(d) < 1e-9 ? "neutral" : d < 0 ? "pos-good" : "pos-bad";
        rows.push(`<tr class="${i === 0 ? "first" : ""}">
          <td>${i === 0 ? `<span class="metric">${m.title.split(",")[0]}</span> · ` : ""}${r.label}</td>
          <td>${num(a, digits)} ${a == null ? "" : unit}</td>
          <td>${num(b, digits)} ${b == null ? "" : unit}</td>
          <td class="${cls}">${d == null ? "–" : `${signed(d, digits)} ${r.pp ? "п.п." : unit}`}</td>
          <td class="${cls}">${r.pp ? "" : pct == null ? "–" : signed(pct, 1) + "%"}</td></tr>`);
      });
    }
    rows.push(`<tr class="first"><td class="neutral">точек в периоде</td><td class="neutral">${dataA.ts.length}</td><td class="neutral">${dataB.ts.length}</td><td></td><td></td></tr>`);
    $("cmp-table").innerHTML = rows.join("");
  }

  // ---------- charts ----------
  const charts = {};
  for (const m of METRICS) charts[m.key] = echarts.init($(m.el));
  echarts.connect(Object.values(charts));
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));

  let offset = 0;      // ms added to A timestamps to lay them over B
  let loaded = { b: null, a: null };
  let stepMs = 600e3;  // typical gap between points, for "nearest point" lookups

  // nearest point to t in a columnar {ts (ms, ascending), [key]} set, or null if too far
  function nearest(set, key, t) {
    if (!set || !set.ts.length) return null;
    let lo = 0, hi = set.ts.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; (set.ts[mid] < t ? lo = mid : hi = mid); }
    const i = Math.abs(set.ts[lo] - t) <= Math.abs(set.ts[hi] - t) ? lo : hi;
    if (Math.abs(set.ts[i] - t) > stepMs) return null;
    return { t: set.ts[i], v: set[key][i] };
  }

  // ECharts matches series by exact x on a time axis; A and B are a few seconds
  // apart, so both values are looked up here by the hovered time instead
  function tooltip(m) {
    return params => {
      const t = params[0].axisValue;
      const b = nearest(loaded.b, m.key, t), a = nearest(loaded.a, m.key, t);
      const dot = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:${c};margin-right:6px"></span>`;
      const line = (p, color, label, when) => p && p.v != null
        ? `<div>${dot(color)}${label} ${fmtTime(when)}: <b>${num(p.v, m.digits)} ${m.unit}</b></div>` : "";
      let html = line(b, css(m.color), "B", b && b.t);
      html += line(a, css("--cmp"), "A", a && a.t - offset);
      if (a && b && a.v != null && b.v != null) {
        const d = b.v - a.v;
        const pct = a.v ? ` (${signed(100 * d / Math.abs(a.v), 1)}%)` : "";
        html += `<div style="color:${css("--muted")}">Δ ${signed(d, m.digits)} ${m.unit}${pct}</div>`;
      }
      return html || fmtTime(t);
    };
  }

  function chartOption(m) {
    const color = css(m.color);
    const opt = {
      animation: false,
      title: { text: m.title, left: 12, top: 8, textStyle: { fontSize: 13, color: css("--text"), fontWeight: 600 } },
      grid: { left: 56, right: 20, top: 40, bottom: 32 },
      tooltip: {
        trigger: "axis", formatter: tooltip(m), confine: true,
        backgroundColor: css("--card"), borderColor: css("--border"), textStyle: { color: css("--text") },
      },
      xAxis: {
        type: "time",
        axisLine: { lineStyle: { color: css("--border") } },
        axisLabel: { color: css("--muted"), hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: css("--muted"), formatter: v => v.toFixed(m.digits === 0 ? 0 : 1) },
        splitLine: { lineStyle: { color: css("--grid") } },
      },
      series: [
        { name: "B", type: "line", showSymbol: false, sampling: "lttb", z: 3,
          lineStyle: { width: 2, color }, itemStyle: { color }, data: [] },
        { name: "A", type: "line", showSymbol: false, sampling: "lttb", z: 2,
          lineStyle: { width: 1.5, type: "dashed", color: css("--cmp") }, itemStyle: { color: css("--cmp") }, data: [] },
      ],
    };
    if (m.key === "co2") {
      // B's colour comes from visualMap; an explicit colour would override it
      delete opt.series[0].lineStyle.color;
      delete opt.series[0].itemStyle;
      opt.visualMap = {
        show: false, seriesIndex: 0, dimension: 1,
        pieces: [
          { lt: CO2_WARN, color: css("--good") },
          { gte: CO2_WARN, lt: CO2_BAD, color: css("--warn") },
          { gte: CO2_BAD, color: css("--bad") },
        ],
      };
      opt.series[0].markLine = {
        silent: true, symbol: "none",
        label: { position: "insideEndTop", color: css("--muted"), formatter: "{c} ppm" },
        lineStyle: { type: "dashed", width: 1 },
        data: [
          { yAxis: CO2_WARN, lineStyle: { color: css("--warn") } },
          { yAxis: CO2_BAD, lineStyle: { color: css("--bad") } },
        ],
      };
    }
    return opt;
  }

  // even ticks on a round step, always keeping the 1400 threshold in view
  function co2Axis(values) {
    const v = values.filter(x => x != null);
    const lo = v.length ? Math.min(...v) : 400, hi = v.length ? Math.max(...v) : CO2_BAD;
    const top = Math.max(hi, CO2_BAD + 100);
    const step = top - lo <= 1600 ? 200 : top - lo <= 4000 ? 500 : 1000;
    return { min: Math.max(0, Math.floor(lo / step) * step), max: Math.ceil((top + 1) / step) * step, interval: step };
  }

  function initCharts() {
    for (const m of METRICS) charts[m.key].setOption(chartOption(m), true);
  }

  // ---------- data ----------
  const EMPTY = { ts: [], co2: [], temperature: [], humidity: [], pressure: [] };

  async function fetchRange([from, to]) {
    const q = `from=${Math.floor(from / 1000)}&to=${Math.ceil(to / 1000)}`;
    return (await fetch(`/api/readings?${q}`, { cache: "no-store" })).json();
  }

  let loadSeq = 0;

  async function loadCharts() {
    const b = periodB();
    if (b[0] == null || b[1] == null || b[0] > b[1]) return;
    const a = periodA(b);
    const seq = ++loadSeq;
    const [dataB, dataA] = await Promise.all([fetchRange(b), a ? fetchRange(a) : Promise.resolve(EMPTY)]);
    if (seq !== loadSeq) return; // a newer selection is already loading
    offset = a ? b[0] - a[0] : 0;
    const toMs = (d, shift) => Object.fromEntries(Object.entries(d).map(([k, v]) => [k, k === "ts" ? v.map(t => t * 1000 + shift) : v]));
    loaded = { b: toMs(dataB, 0), a: a ? toMs(dataA, offset) : null };
    if (dataB.ts.length > 1) stepMs = (dataB.ts[dataB.ts.length - 1] - dataB.ts[0]) * 1000 / (dataB.ts.length - 1);

    $("a-label").textContent = a ? `A: ${fmtRange(a[0], a[1])}` : "";
    $("cmp-panel").classList.toggle("hidden", !a);
    if (a) renderTable(dataB, dataA);

    for (const m of METRICS) {
      const sB = dataB.ts.map((t, i) => [t * 1000, dataB[m.key][i]]);
      const sA = dataA.ts.map((t, i) => [t * 1000 + offset, dataA[m.key][i]]);
      const upd = { xAxis: { min: b[0], max: b[1] }, series: [{ data: sB }, { data: sA }] };
      if (m.key === "co2") upd.yAxis = co2Axis(dataB.co2.concat(dataA.co2));
      charts[m.key].setOption(upd);
    }
  }

  async function loadLatest() {
    const { range, reading, status } = await (await fetch("/api/latest", { cache: "no-store" })).json();
    dataRange = range || dataRange;
    for (const id of ["b-from", "b-to", "a-from", "a-to"]) {
      if (range && range.first) $(id).min = toInput(range.first * 1000);
      $(id).max = toInput(Date.now());
    }
    if (!reading) {
      $("meta").textContent = "данных пока нет — запусти сборщик (aranet-collect)";
      return;
    }
    $("v-co2").textContent = reading.co2 ?? "–";
    $("v-temp").textContent = reading.temperature != null ? reading.temperature.toFixed(1) : "–";
    $("v-hum").textContent = reading.humidity != null ? Math.round(reading.humidity) : "–";
    $("v-pres").textContent = reading.pressure != null ? reading.pressure.toFixed(1) : "–";
    $("v-bat").textContent = status && status.battery != null ? status.battery : "–";
    const c = reading.co2;
    $("tile-co2").style.borderLeftColor = c == null ? css("--border") : c < CO2_WARN ? css("--good") : c < CO2_BAD ? css("--warn") : css("--bad");
    const ageMin = Math.round((Date.now() / 1000 - reading.ts) / 60);
    const stale = ageMin > 30 ? " ⚠ данные устарели" : "";
    const device = status && status.name ? `${status.name} · ` : "";
    $("meta").textContent = `${device}последнее измерение: ${fmtTime(reading.ts * 1000)} (${ageMin} мин назад)${stale}`;
  }

  async function refresh() {
    try {
      await loadLatest();
      syncControls();
      await loadCharts();
    } catch (e) {
      $("meta").textContent = "ошибка загрузки: " + e.message;
    }
  }

  // ---------- controls ----------
  function save() { for (const k of Object.keys(state)) store.set(k, state[k]); }

  function syncControls() {
    const b = periodB();
    document.querySelectorAll("#quick button").forEach(btn => btn.classList.toggle("active", btn.dataset.h === state.quick));
    if (b[0] != null) $("b-from").value = toInput(b[0]);
    if (b[1] != null) $("b-to").value = toInput(b[1]);
    $("cmp-mode").value = state.mode;
    $("a-custom").classList.toggle("hidden", state.mode !== "custom");
    if (state.mode === "custom") {
      if (state.aFrom == null || state.aTo == null) {
        // sensible start for own dates: the period right before B
        const a = [b[0] - (b[1] - b[0]), b[0]];
        state.aFrom = dayStart(toInput(a[0])); state.aTo = dayEnd(toInput(a[1] - 1));
      }
      $("a-from").value = toInput(state.aFrom);
      $("a-to").value = toInput(state.aTo);
    }
  }

  function onChange() { save(); syncControls(); loadCharts().catch(e => { $("meta").textContent = "ошибка загрузки: " + e.message; }); }

  document.querySelectorAll("#quick button").forEach(btn => btn.addEventListener("click", () => {
    state.quick = btn.dataset.h;
    onChange();
  }));

  function onBDates() {
    if (!$("b-from").value || !$("b-to").value) return;
    let from = dayStart($("b-from").value), to = dayEnd($("b-to").value);
    if (from > to) [from, to] = [dayStart($("b-to").value), dayEnd($("b-from").value)];
    Object.assign(state, { quick: null, bFrom: from, bTo: to });
    onChange();
  }
  $("b-from").addEventListener("change", onBDates);
  $("b-to").addEventListener("change", onBDates);

  $("cmp-mode").addEventListener("change", () => { state.mode = $("cmp-mode").value; onChange(); });

  function onADates() {
    if (!$("a-from").value || !$("a-to").value) return;
    let from = dayStart($("a-from").value), to = dayEnd($("a-to").value);
    if (from > to) [from, to] = [dayStart($("a-to").value), dayEnd($("a-from").value)];
    Object.assign(state, { aFrom: from, aTo: to });
    onChange();
  }
  $("a-from").addEventListener("change", onADates);
  $("a-to").addEventListener("change", onADates);

  // re-theme when the OS switches light/dark
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { initCharts(); refresh(); });

  if (!state.quick && (state.bFrom == null || state.bTo == null)) state.quick = "24";
  initCharts();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
