(() => {
  // Aranet's own CO2 bands: green < 1000, amber 1000-1400, red > 1400 ppm
  const CO2_WARN = 1000, CO2_BAD = 1400;
  const REFRESH_MS = 5 * 60 * 1000;

  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = id => document.getElementById(id);
  let hours = 24;
  try { hours = localStorage.getItem("aranet.hours") || 24; } catch (e) {}

  const charts = {
    co2: echarts.init($("c-co2")),
    temperature: echarts.init($("c-temp")),
    humidity: echarts.init($("c-hum")),
    pressure: echarts.init($("c-pres")),
  };
  echarts.connect(Object.values(charts));
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));

  const fmtTime = ms => new Date(ms).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  function baseOption(title, unit, color, digits) {
    return {
      animation: false,
      title: { text: title, left: 12, top: 8, textStyle: { fontSize: 13, color: css("--text"), fontWeight: 600 } },
      grid: { left: 56, right: 20, top: 40, bottom: 32 },
      tooltip: {
        trigger: "axis",
        valueFormatter: v => v == null ? "–" : `${Number(v).toFixed(digits)} ${unit}`,
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
        axisLabel: { color: css("--muted"), formatter: v => v.toFixed(digits === 0 ? 0 : 1) },
        splitLine: { lineStyle: { color: css("--grid") } },
      },
      dataZoom: [{ type: "inside", throttle: 50 }],
      series: [{
        type: "line", showSymbol: false, sampling: "lttb", connectNulls: false,
        lineStyle: { width: 2, color }, itemStyle: { color },
        data: [],
      }],
    };
  }

  function co2Option() {
    const opt = baseOption("CO₂, ppm", "ppm", css("--co2"), 0);
    opt.yAxis.scale = false;
    // the line colour comes from visualMap; an explicit colour would override it
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
    return opt;
  }

  // even ticks on a round step, always keeping the 1400 threshold in view
  function co2Axis(values) {
    const v = values.filter(x => x != null);
    const lo = v.length ? Math.min(...v) : 400, hi = v.length ? Math.max(...v) : CO2_BAD;
    const top = Math.max(hi, CO2_BAD + 100);
    const step = top - lo <= 1600 ? 200 : top - lo <= 4000 ? 500 : 1000;
    return {
      min: Math.max(0, Math.floor(lo / step) * step),
      max: Math.ceil((top + 1) / step) * step,
      interval: step,
    };
  }

  function initCharts() {
    charts.co2.setOption(co2Option(), true);
    charts.temperature.setOption(baseOption("Температура, °C", "°C", css("--temp"), 1), true);
    charts.humidity.setOption(baseOption("Влажность, %", "%", css("--hum"), 0), true);
    charts.pressure.setOption(baseOption("Давление, hPa", "hPa", css("--pres"), 1), true);
  }

  async function loadReadings() {
    const q = hours === "all" ? "" : `?hours=${hours}`;
    const data = await (await fetch(`/api/readings${q}`, { cache: "no-store" })).json();
    const ms = data.ts.map(t => t * 1000);
    charts.co2.setOption({ yAxis: co2Axis(data.co2) });
    for (const key of Object.keys(charts)) {
      const series = ms.map((t, i) => [t, data[key][i]]);
      charts[key].setOption({ series: [{ data: series }] });
    }
    return data.ts.length;
  }

  async function loadLatest() {
    const { reading, status } = await (await fetch("/api/latest", { cache: "no-store" })).json();
    const device = status && status.name ? `${status.name} · ` : "";
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
    $("meta").textContent = `${device}последнее измерение: ${fmtTime(reading.ts * 1000)} (${ageMin} мин назад)${stale}`;
  }

  async function refresh() {
    try {
      await Promise.all([loadReadings(), loadLatest()]);
    } catch (e) {
      $("meta").textContent = "ошибка загрузки: " + e.message;
    }
  }

  document.querySelectorAll("#ranges button").forEach(btn => {
    btn.classList.toggle("active", String(btn.dataset.h) === String(hours));
    btn.addEventListener("click", () => {
      hours = btn.dataset.h;
      try { localStorage.setItem("aranet.hours", hours); } catch (e) {}
      document.querySelectorAll("#ranges button").forEach(b => b.classList.toggle("active", b === btn));
      charts.co2.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
      refresh();
    });
  });

  // re-theme when the OS switches light/dark
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { initCharts(); refresh(); });

  initCharts();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
