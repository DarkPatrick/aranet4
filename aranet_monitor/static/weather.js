(() => {
  const { SPARSE, css, $, store, fmtTime, num, dot, toMs, nearest, typicalStep, Periods, SeriesChart, linkCharts, renderCompareTable } = UI;
  const REFRESH_MS = 5 * 60 * 1000;
  const STALE_S = 3600;
  const prefs = store("weather");

  const NAMES = {
    LCLK: "Larnaca (аэропорт)", LCPH: "Paphos (аэропорт)", KPYRGOS: "Kato Pyrgos",
    TEPAK: "TEPAK (Limassol)", VISITOR: "Troodos Visitor Centre", CAVO_GRECO: "Cavo Greco",
    PANAGIA_BRIDGE: "Panagia Bridge", KEPNIC: "KEPNIC", CYTASAT: "CYTASAT",
  };
  const label = code => NAMES[code] || code.split("_").map(w => w[0] + w.slice(1).toLowerCase()).join(" ");

  // wind and pressure: one chart each, from whichever height/reduction the station reports
  const WIND = [["wind10", "Ветер (10 м)"], ["wind2", "Ветер (2 м)"]];
  const PRES = [["p_msl", "Давление (на уровне моря)"], ["p_qnh", "Давление (QNH)"], ["p_station", "Давление (на станции)"]];
  const pick = (opts, metrics) => opts.find(([k]) => metrics.includes(k));

  const METRICS = [
    { key: "temp", el: "c-temp", title: "Температура, °C", unit: "°C", digits: 1, color: "--temp", rows: ["mean", "min", "max"] },
    { key: "rh", el: "c-rh", title: "Влажность, %", unit: "%", digits: 0, color: "--hum", rows: ["mean", "min", "max"] },
    { key: "rain", el: "c-rain", title: "Осадки, мм", unit: "мм", digits: 1, color: "--rain", bar: true,
      rows: ["sum", { label: "макс. за 10 мин", get: s => s.max }] },
    { key: "wind", el: "c-wind", title: "Ветер, м/с", unit: "м/с", digits: 1, color: "--wind", zeroBased: true, rows: ["mean", "max"] },
    { key: "pres", el: "c-pres", title: "Давление, hPa", unit: "hPa", digits: 1, color: "--pres", rows: ["mean", "min", "max"] },
    { key: "rad_global", el: "c-rad", title: "Солнечная радиация, W/m²", unit: "W/m²", digits: 0, color: "--rad", zeroBased: true, rows: ["mean", "max"] },
    { key: "snow", el: "c-snow", title: "Снег, см", unit: "см", digits: 0, color: "--hum", zeroBased: true, rows: ["mean", "max"] },
  ];
  const charts = METRICS.map(m => new SeriesChart($(m.el), m));

  // ---------- home vs outside ----------
  const home = { chart: echarts.init($("c-home")), sets: null };
  function homeOption() {
    const line = (name, color) => ({ name, type: "line", showSymbol: false, sampling: "lttb", data: [],
                                     lineStyle: { width: 2, color }, itemStyle: { color } });
    home.chart.setOption({
      animation: false,
      title: { text: "Температура: дома и на улице, °C", left: 12, top: 8, textStyle: { fontSize: 13, color: css("--text"), fontWeight: 600 } },
      legend: { left: 8, top: 30, textStyle: { color: css("--muted") }, itemWidth: 16 },
      grid: { left: 56, right: 20, top: 64, bottom: 32 },
      tooltip: { trigger: "axis", confine: true, formatter: homeTooltip,
                 backgroundColor: css("--card"), borderColor: css("--border"), textStyle: { color: css("--text") } },
      xAxis: { type: "time", axisLine: { lineStyle: { color: css("--border") } }, axisLabel: { color: css("--muted"), hideOverlap: true } },
      yAxis: { type: "value", scale: true, axisLabel: { color: css("--muted") }, splitLine: { lineStyle: { color: css("--grid") } } },
      series: [line("дома (Aranet)", css("--home")), line("на улице", css("--temp"))],
    }, true);
  }
  function homeTooltip(params) {
    if (!home.sets) return "";
    const t = params[0].axisValue;
    const h = nearest(home.sets.home, "temperature", t, typicalStep(home.sets.home));
    const o = nearest(home.sets.out, "temp", t, typicalStep(home.sets.out));
    let html = `<div style="color:${css("--muted")}">${fmtTime(t)}</div>`;
    if (h && h.v != null) html += `<div>${dot(css("--home"))}дома: <b>${num(h.v, 1)} °C</b></div>`;
    if (o && o.v != null) html += `<div>${dot(css("--temp"))}на улице: <b>${num(o.v, 1)} °C</b></div>`;
    if (h && o && h.v != null && o.v != null) html += `<div style="color:${css("--muted")}">разница ${num(h.v - o.v, 1)} °C</div>`;
    return html;
  }
  homeOption();
  linkCharts(charts);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { homeOption(); renderHome(); setTiles(); });
  window.addEventListener("resize", () => home.chart.resize());

  // ---------- stations, map ----------
  let stations = [], byCode = {}, selected = null;
  const map = L.map("map", { scrollWheelZoom: false, zoomSnap: 0.25 }).setView([35.0, 33.2], 9);
  map.attributionControl.setPrefix(false); // keep the tile attribution, drop the "Leaflet" link
  let tiles = null;
  function setTiles() {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (tiles) map.removeLayer(tiles);
    // Esri grey canvas basemaps: free, no API key, light and dark variants
    tiles = L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${dark ? "World_Dark_Gray_Base" : "World_Light_Gray_Base"}/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 16, attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
    }).addTo(map);
  }
  setTiles();
  const markers = {};

  // temperature -> colour on a cold..hot ramp; label colour picked for contrast
  const STOPS = [[0, [59, 111, 212]], [12, [47, 163, 168]], [20, [224, 161, 42]], [28, [224, 102, 45]], [36, [194, 45, 45]]];
  function tempColor(t) {
    if (t == null) return { bg: "#8b93a1", fg: "#fff" };
    const x = Math.min(STOPS[STOPS.length - 1][0], Math.max(STOPS[0][0], t));
    let i = 0;
    while (i < STOPS.length - 2 && x > STOPS[i + 1][0]) i++;
    const [t0, c0] = STOPS[i], [t1, c1] = STOPS[i + 1], k = (x - t0) / (t1 - t0);
    const c = c0.map((v, j) => Math.round(v + (c1[j] - v) * k));
    const lum = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
    return { bg: `rgb(${c.join(",")})`, fg: lum > 0.55 ? "#1d2330" : "#fff" };
  }

  // labels from zoom 10; below that, 55 badges on an island this size just overlap
  const LABEL_ZOOM = 10;
  map.on("zoomend", () => drawMarkers());

  function drawMarkers() {
    const now = Date.now() / 1000, labels = map.getZoom() >= LABEL_ZOOM;
    for (const st of stations) {
      const t = st.latest ? st.latest.temp : null;
      const stale = !st.latest || now - st.latest.ts > STALE_S;
      const { bg, fg } = tempColor(stale ? null : t);
      const cls = `wx-badge${labels ? "" : " dot"}${st.code === selected ? " sel" : ""}`;
      const text = labels ? (t == null ? "–" : Math.round(t) + "°") : "";
      const html = `<span class="${cls}" style="background:${bg};color:${fg}">${text}</span>`;
      const icon = L.divIcon({ html, className: "", iconSize: null });
      if (!markers[st.code]) {
        markers[st.code] = L.marker([st.lat, st.lon], { icon, keyboard: false })
          .addTo(map).on("click", () => select(st.code));
      } else {
        markers[st.code].setIcon(icon);
      }
      const l = st.latest || {};
      const parts = [l.temp != null && `${num(l.temp, 1)} °C`, l.rh != null && `${num(l.rh, 0)}%`,
                     (l.wind10 ?? l.wind2) != null && `ветер ${num(l.wind10 ?? l.wind2, 1)} м/с`].filter(Boolean);
      markers[st.code].bindTooltip(`<b>${label(st.code)}</b><br>${parts.join(" · ") || "нет данных"}${stale ? "<br>⚠ данные устарели" : ""}`,
                                   { direction: "top", offset: [0, -8] });
      markers[st.code].setZIndexOffset(st.code === selected ? 1000 : 0);
    }
  }

  function fillSelect() {
    const sel = $("station");
    const sorted = [...stations].sort((a, b) => label(a.code).localeCompare(label(b.code)));
    sel.innerHTML = sorted.map(s => `<option value="${s.code}">${label(s.code)}</option>`).join("");
    sel.value = selected;
  }
  $("station").addEventListener("change", () => select($("station").value));

  function setTilesFor(st) {
    const l = st.latest || {};
    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    const tiles = [
      ["Температура", l.temp, 1, "°C"], ["Влажность", l.rh, 0, "%"], ["Осадки за 10 мин", l.rain, 1, "мм"],
      w && [w[1], l[w[0]], 1, "м/с"], p && [p[1], l[p[0]], 1, "hPa"], ["Радиация", l.rad_global, 0, "W/m²"], ["Снег", l.snow, 0, "см"],
    ].filter(x => x && x[1] != null);
    $("tiles").innerHTML = tiles.map(([n, v, d, u]) =>
      `<div class="tile"><div class="label">${n}</div><div class="value">${num(v, d)}<span class="unit">${u}</span></div></div>`).join("");
    const age = l.ts ? Math.round((Date.now() / 1000 - l.ts) / 60) : null;
    const since = st.first ? ` · история с ${fmtTime(st.first * 1000)}` : "";
    $("station-meta").textContent = l.ts
      ? `${st.lat.toFixed(3)}, ${st.lon.toFixed(3)} · данные на ${fmtTime(l.ts * 1000)} (${age} мин назад)${age > 60 ? " ⚠ устарели" : ""}${since}`
      : "нет данных";
  }

  // "Всё" spans both sources: the station's history and the home sensor's
  let homeFirst = null;
  function setBounds(st) {
    const firsts = [st.first, homeFirst].filter(Boolean);
    periods.setBounds(firsts.length ? Math.min(...firsts) * 1000 : null);
  }

  function select(code) {
    if (!byCode[code]) return;
    selected = code;
    prefs.set("station", code);
    $("station").value = code;
    drawMarkers();
    setTilesFor(byCode[code]);
    setBounds(byCode[code]);
    loadCharts().catch(fail);
  }

  // ---------- data ----------
  const q = ([from, to]) => `from=${Math.floor(from / 1000)}&to=${Math.ceil(to / 1000)}`;
  const getJSON = async url => (await fetch(url, { cache: "no-store" })).json();
  const fetchStation = (code, range) => getJSON(`/api/weather/readings?station=${encodeURIComponent(code)}&${q(range)}`);

  function derive(data, st) {
    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    data.wind = w ? data[w[0]] : [];
    data.pres = p ? data[p[0]] : [];
    return data;
  }

  let loadSeq = 0;
  async function loadCharts() {
    const st = byCode[selected];
    const b = periods.b();
    if (!st || b[0] == null || b[1] == null || b[0] > b[1]) return;
    const a = periods.a(b);
    const seq = ++loadSeq;
    const [dataB, dataA, homeB] = await Promise.all([
      fetchStation(st.code, b),
      a ? fetchStation(st.code, a) : Promise.resolve(null),
      getJSON(`/api/readings?${q(b)}`),
    ]);
    if (seq !== loadSeq) return; // a newer selection is already loading
    derive(dataB, st);
    if (dataA) derive(dataA, st);
    const offset = a ? b[0] - a[0] : 0;
    const setB = toMs(dataB), setA = dataA ? toMs(dataA, offset) : null;

    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    const has = m => m.key === "wind" ? !!w : m.key === "pres" ? !!p : st.metrics.includes(m.key);
    METRICS.forEach((m, i) => {
      if (m.key === "wind" && w) m.title = `${w[1]}, м/с`;
      if (m.key === "pres" && p) m.title = `${p[1]}, hPa`;
      charts[i].show(has(m));
      if (has(m)) { charts[i].applyTheme(); charts[i].set(setB, setA, offset, b); }
    });

    $("cmp-panel").classList.toggle("hidden", !a);
    if (a) renderCompareTable($("cmp-table"), METRICS.filter(has), dataB, dataA);

    home.sets = { home: toMs(homeB), out: setB };
    renderHome(b);
  }

  function renderHome(range = home.range) {
    if (!home.sets) return;
    home.range = range;
    const has = home.sets.home.ts.length > 0;
    $("c-home").classList.toggle("hidden", !has);
    if (!has) return;
    home.chart.resize();
    const line = (s, k) => ({ data: s.ts.map((t, i) => [t, s[k][i]]), showSymbol: s.ts.length < SPARSE, symbolSize: 5 });
    home.chart.setOption({
      title: { text: `Температура: дома и на улице (${label(selected)}), °C` },
      xAxis: { min: range[0], max: range[1] },
      series: [line(home.sets.home, "temperature"), line(home.sets.out, "temp")],
    });
  }

  async function loadStations() {
    stations = await getJSON("/api/weather/stations");
    byCode = Object.fromEntries(stations.map(s => [s.code, s]));
    if (!stations.length) {
      $("meta").textContent = "данных пока нет — запусти сборщик (aranet-weather)";
      return false;
    }
    if (!selected) {
      const saved = prefs.get("station", null);
      selected = byCode[saved] ? saved : byCode.ATHALASSA ? "ATHALASSA" : stations[0].code;
      map.fitBounds(stations.map(s => [s.lat, s.lon]), { padding: [12, 12] });
    }
    const newest = Math.max(...stations.map(s => s.latest ? s.latest.ts : 0));
    const live = stations.filter(s => s.latest && newest - s.latest.ts < STALE_S).length;
    $("meta").textContent = `${stations.length} станций (${live} с актуальными данными) · обновлено ${fmtTime(newest * 1000)}`;
    fillSelect();
    drawMarkers();
    setTilesFor(byCode[selected]);
    const latest = await getJSON("/api/latest");
    homeFirst = latest.range && latest.range.first ? latest.range.first : null;
    setBounds(byCode[selected]);
    return true;
  }

  const fail = e => { $("meta").textContent = "ошибка загрузки: " + e.message; };
  const periods = new Periods($("periods"), "weather", () => loadCharts().catch(fail));

  async function refresh() {
    try {
      if (!(await loadStations())) return;
      periods.sync();
      await loadCharts();
    } catch (e) { fail(e); }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
