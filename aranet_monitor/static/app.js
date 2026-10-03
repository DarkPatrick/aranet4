(() => {
  const { css, $, fmtTime, toMs, Periods, SeriesChart, linkCharts, renderCompareTable } = UI;
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
    { key: "pressure", el: "c-pres", title: "Давление, hPa", unit: "hPa", digits: 1, color: "--pres", rows: ["mean", "min", "max"] },
  ];

  const charts = METRICS.map(m => new SeriesChart($(m.el), m));
  linkCharts(charts);

  const EMPTY = { ts: [], co2: [], temperature: [], humidity: [], pressure: [] };
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
    $("v-pres").textContent = reading.pressure != null ? reading.pressure.toFixed(1) : "–";
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

  async function refresh() {
    try {
      await loadLatest();
      periods.sync();
      await loadCharts();
    } catch (e) { fail(e); }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
