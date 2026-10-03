(() => {
  const { css, $, fmtTime, num, signed, toMs, Periods, SeriesChart, linkCharts, renderCompareTable,
          HPA_TO_MM, pressureLabel, pressureBands, tendency } = UI;
  // the sensor reads at the flat's altitude; sea-level equivalent = reading + offset (hPa),
  // estimated by the server from nearby stations
  let pOffset = 0;
  // Aranet's own CO2 bands: green < 1000, amber 1000-1400, red > 1400 ppm
  const CO2_WARN = 1000, CO2_BAD = 1400;
  const REFRESH_MS = 5 * 60 * 1000;

  // even ticks on a round step, always keeping the 1400 threshold in view
  function co2Axis(sets) {
    const v = [sets.b, sets.a].filter(Boolean).flatMap(s => s.co2).filter(x => x != null);
    const lo = v.length ? Math.min(...v) : 400, hi = v.length ? Math.max(...v) : CO2_BAD;
    const top = Math.max(hi, CO2_BAD + 100);
    const step = top - lo <= 1600 ? 200 : top - lo <= 4000 ? 500 : 1000;
    return { min: Math.max(0, Math.floor(lo / step) * step), max: Math.ceil((top + 1) / step) * step, interval: step };
  }

  function co2Decorate(opt) {
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

  const METRICS = [
    { key: "co2", el: "c-co2", title: "CO₂, ppm", unit: "ppm", digits: 0, color: "--co2", lowerIsBetter: true,
      decorate: co2Decorate, axis: co2Axis,
      rows: ["mean", "min", "max",
             { label: `доля времени > ${CO2_WARN} ppm`, get: s => s.above(CO2_WARN), pp: true },
             { label: `доля времени > ${CO2_BAD} ppm`, get: s => s.above(CO2_BAD), pp: true }] },
    { key: "temperature", el: "c-temp", title: "Температура, °C", unit: "°C", digits: 1, color: "--temp", rows: ["mean", "min", "max"] },
    { key: "humidity", el: "c-hum", title: "Влажность, %", unit: "%", digits: 0, color: "--hum", rows: ["mean", "min", "max"] },
    { key: "pressure_mm", el: "c-pres", title: "Давление, мм рт. ст.", unit: "мм рт. ст.", digits: 1, color: "--pres",
      rows: ["mean", "min", "max"], bands: pressureBands(0, HPA_TO_MM),
      describe: mm => `${num(mm / HPA_TO_MM, 1)} гПа · ${pressureLabel(mm / HPA_TO_MM + pOffset)}` },
  ];

  const charts = METRICS.map(m => new SeriesChart($(m.el), m));
  linkCharts(charts);

  const EMPTY = { ts: [], co2: [], temperature: [], humidity: [], pressure: [], pressure_mm: [] };
  const fetchRange = async ([from, to]) =>
    (await fetch(`/api/readings?from=${Math.floor(from / 1000)}&to=${Math.ceil(to / 1000)}`, { cache: "no-store" })).json();

  let loadSeq = 0;
  async function loadCharts() {
    const b = periods.b();
    if (b[0] == null || b[1] == null || b[0] > b[1]) return;
    const a = periods.a(b);
    const seq = ++loadSeq;
    const [dataB, dataA] = await Promise.all([fetchRange(b), a ? fetchRange(a) : Promise.resolve(EMPTY)]);
    if (seq !== loadSeq) return; // a newer selection is already loading
    const offset = a ? b[0] - a[0] : 0;
    for (const d of [dataB, dataA]) d.pressure_mm = d.pressure.map(v => v == null ? null : v * HPA_TO_MM);
    const setB = toMs(dataB), setA = a ? toMs(dataA, offset) : null;
    $("cmp-panel").classList.toggle("hidden", !a);
    if (a) renderCompareTable($("cmp-table"), METRICS, dataB, dataA);
    charts.forEach(c => c.set(setB, setA, offset, b, { fit: periods.s.quick === "all" }));
  }

  async function loadLatest() {
    const { range, reading, status } = await (await fetch("/api/latest", { cache: "no-store" })).json();
    periods.setBounds(range && range.first ? range.first * 1000 : null);
    if (!reading) {
      $("meta").textContent = "данных пока нет — запусти сборщик (aranet-collect)";
      return;
    }
    $("v-co2").textContent = reading.co2 ?? "–";
    $("v-temp").textContent = reading.temperature != null ? reading.temperature.toFixed(1) : "–";
    $("v-hum").textContent = reading.humidity != null ? Math.round(reading.humidity) : "–";
    $("v-pres").textContent = reading.pressure != null ? Math.round(reading.pressure * HPA_TO_MM) : "–";
    if (reading.pressure != null) {
      // tendency: now vs ~3 hours ago
      const recent = await (await fetch("/api/readings?hours=3.2", { cache: "no-store" })).json();
      const i = recent.ts.findIndex((t, k) => recent.pressure[k] != null && reading.ts - t <= 3 * 3600 + 300);
      const d3 = i >= 0 && reading.ts - recent.ts[i] >= 2.5 * 3600 ? reading.pressure - recent.pressure[i] : null;
      const trend = d3 == null ? "" : ` · ${d3 > 0.4 ? "↑" : d3 < -0.4 ? "↓" : "→"} ${signed(d3, 1)} гПа за 3 ч, ${tendency(d3)}`;
      $("v-pres-sub").textContent = `${num(reading.pressure, 1)} гПа · ${pressureLabel(reading.pressure + pOffset)}${trend}`;
    }
    $("v-bat").textContent = status && status.battery != null ? status.battery : "–";
    const c = reading.co2;
    $("tile-co2").style.borderLeftColor = c == null ? css("--border") : c < CO2_WARN ? css("--good") : c < CO2_BAD ? css("--warn") : css("--bad");
    const ageMin = Math.round((Date.now() / 1000 - reading.ts) / 60);
    const stale = ageMin > 30 ? " ⚠ данные устарели" : "";
    const device = status && status.name ? `${status.name} · ` : "";
    $("meta").textContent = `${device}последнее измерение: ${fmtTime(reading.ts * 1000)} (${ageMin} мин назад)${stale}`;
  }

  const fail = e => { $("meta").textContent = "ошибка загрузки: " + e.message; };
  const periods = new Periods($("periods"), "aranet", () => loadCharts().catch(fail));

  async function loadOffset() {
    const r = await (await fetch("/api/pressure-offset", { cache: "no-store" })).json();
    pOffset = r.offset ?? 0;
    const pm = METRICS.find(m => m.key === "pressure_mm");
    pm.bands = pressureBands(pOffset, HPA_TO_MM);
    $("pres-note").textContent = r.offset == null
      ? "Шкала — для давления на уровне моря (норма 760 мм = 1013 гПа). Поправку на высоту квартиры посчитать пока не по чему: нет данных станций."
      : `Датчик меряет давление на высоте квартиры: по ${r.stations} ближайшим станциям оно на ${num(r.offset, 1)} гПа (${num(r.offset * HPA_TO_MM, 1)} мм) ниже приведённого к уровню моря (≈${Math.round(r.offset * 8.3)} м над морем). Шкала сдвинута на эту поправку; норма на уровне моря — 760 мм (1013 гПа). Для прогноза важнее тенденция: падение больше ~1,5 гПа за 3 часа — к ухудшению погоды, рост — к улучшению.`;
  }

  async function refresh() {
    try {
      await loadOffset().catch(() => {});
      await loadLatest();
      periods.sync();
      await loadCharts();
    } catch (e) { fail(e); }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
