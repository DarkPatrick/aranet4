(() => {
  const { HPA_TO_MM, pressureLabel, pressureBands, zoomOptions, armZoom, resetZoom, SPARSE, DAY, css, $, store, fmtTime, fmtDay, fmtRange, num, dot, toMs, nearest, typicalStep, Periods, SeriesChart, linkCharts, renderCompareTable } = UI;
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

  // Beaufort scale, m/s upper bounds and the Russian names (WMO / Росгидромет)
  const BEAUFORT = [
    [0.3, "штиль"], [1.6, "тихий"], [3.4, "лёгкий"], [5.5, "слабый"], [8.0, "умеренный"], [10.8, "свежий"],
    [13.9, "сильный"], [17.2, "крепкий"], [20.8, "очень крепкий"], [24.5, "шторм"], [28.5, "сильный шторм"],
    [32.7, "жестокий шторм"], [Infinity, "ураган"],
  ].map(([to, name], i) => ({ to, name, label: `${i} · ${name}` }));
  const beaufort = v => { const i = BEAUFORT.findIndex(b => v < b.to); return `${BEAUFORT[i].name} (${i} балл${i === 1 ? "" : i > 1 && i < 5 ? "а" : "ов"})`; };

  const METRICS = [
    { key: "temp", el: "c-temp", title: "Температура, °C", unit: "°C", digits: 1, color: "--temp", rows: ["mean", "min", "max"] },
    { key: "net", el: "c-net", title: "Ощущается (NET), °C", unit: "°C", digits: 1, color: "--feel", rows: ["mean", "min", "max"] },
    { key: "rh", el: "c-rh", title: "Влажность, %", unit: "%", digits: 0, color: "--hum", rows: ["mean", "min", "max"] },
    { key: "rain", el: "c-rain", title: "Осадки, мм", unit: "мм", digits: 1, color: "--rain", bar: true,
      rows: ["sum", { label: "макс. за 10 мин", get: s => s.max }] },
    { key: "wind", el: "c-wind", title: "Ветер, м/с", unit: "м/с", digits: 1, color: "--wind", zeroBased: true, rows: ["mean", "max"],
      bands: BEAUFORT, describe: beaufort },
    { key: "pres", el: "c-pres", title: "Давление, гПа", unit: "гПа", digits: 1, color: "--pres", rows: ["mean", "min", "max"] },
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
      ...zoomOptions(),
    }, true);
    armZoom(home.chart);
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
  echarts.connect([home.chart, ...charts.map(c => c.chart)]);
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
      ["Температура", l.temp, 1, "°C"], ["Ощущается", l.net, 1, "°C"], ["Влажность", l.rh, 0, "%"], ["Осадки за 10 мин", l.rain, 1, "мм"],
      w && [w[1], l[w[0]], 1, "м/с", l[w[0]] != null ? BEAUFORT.find(b => l[w[0]] < b.to).name : ""], p && [p[1], l[p[0]], 1, "hPa"], ["Радиация", l.rad_global, 0, "W/m²"], ["Снег", l.snow, 0, "см"],
    ].filter(x => x && x[1] != null);
    $("tiles").innerHTML = tiles.map(([n, v, d, u, sub]) =>
      `<div class="tile"><div class="label">${n}</div><div class="value">${num(v, d)}<span class="unit">${u}</span></div>${sub ? `<div class="note" style="margin:0">${sub}</div>` : ""}</div>`).join("");
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
    if (climStations.some(c => c.code === code)) setClimStation(code, false);
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
      if (m.key === "pres" && p) {
        m.title = `${p[1]}, гПа`;
        // the "normal / cyclone / anticyclone" scale only makes sense for sea-level pressure;
        // station-level pressure on a mountain (Troodos ~830 hPa) is just altitude
        const seaLevel = p[0] !== "p_station";
        m.bands = seaLevel ? pressureBands(0) : null;
        m.describe = hpa => `${num(hpa * HPA_TO_MM, 1)} мм рт. ст.${seaLevel ? " · " + pressureLabel(hpa) : ""}`;
      }
      charts[i].show(has(m));
      if (has(m)) { charts[i].applyTheme(); charts[i].set(setB, setA, offset, b, { fit: fitAll() }); }
    });

    $("cmp-panel").classList.toggle("hidden", !a);
    if (a) renderCompareTable($("cmp-table"), METRICS.filter(has), dataB, dataA);

    home.sets = { home: toMs(homeB), out: setB };
    renderHome(b);
    renderSst();
  }

  const fitAll = () => periods.s.quick === "all";

  function renderHome(range = home.range) {
    if (!home.sets) return;
    home.range = range;
    if (fitAll()) {
      const firsts = [home.sets.home.ts[0], home.sets.out.ts[0]].filter(x => x != null);
      if (firsts.length) range = [Math.min(...firsts), range[1]];
    }
    const has = home.sets.home.ts.length > 0;
    $("c-home").classList.toggle("hidden", !has);
    if (!has) return;
    home.chart.resize();
    resetZoom(home.chart);
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

  // ---------- sea, warnings ----------
  const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const SST = { key: "sst", title: "Температура моря, °C", unit: "°C", digits: 1, color: "--sea", daily: true, rows: ["mean", "min", "max"] };
  const sstChart = new SeriesChart($("c-sst"), SST);
  let marine = null;

  async function loadMarine() {
    marine = await getJSON("/api/weather/marine");
    const w = marine.warnings && marine.warnings.text;
    const f = marine.forecast;
    const seaWarn = f && f.warnings && !/^nil$/i.test(f.warnings.trim()) ? `Море: ${f.warnings}` : "";
    const banner = [w && `⚠ Предупреждение метеослужбы:\n${w}`, seaWarn && `⚠ ${seaWarn}`].filter(Boolean).join("\n\n");
    $("warn-banner").textContent = banner;
    $("warn-banner").classList.toggle("hidden", !banner);
    if (!f) { $("sea").textContent = "данных пока нет — запусти aranet-dom forecast"; return; }
    const coasts = Object.entries(f.areas || {}).map(([coast, rows]) => rows.map((r, i) =>
      `<tr>${i === 0 ? `<td rowspan="${rows.length}"><b>${esc(coast)}</b></td>` : ""}<td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("")).join("");
    $("sea").innerHTML = `
      <div class="tiles" style="margin:0 0 8px">
        <div class="tile"><div class="label">Температура моря</div><div class="value">${num(f.sst, 0)}<span class="unit">°C</span></div></div>
        <div class="tile"><div class="label">Давление</div><div class="value">${num(f.pressure * HPA_TO_MM, 0)}<span class="unit">мм рт. ст.</span></div>
          <div class="note" style="margin:0">${num(f.pressure, 0)} гПа · ${pressureLabel(f.pressure)}</div></div>
      </div>
      <div class="note">Давление в морском прогнозе — это атмосферное давление над Кипром на момент выпуска, приведённое к уровню моря; к самому морю оно не относится. Моряки по нему следят за погодой: ниже ~1009 гПа (757 мм) — область циклона, ветрено и возможны осадки; выше ~1017 гПа (763 мм) — антициклон, обычно тихо и ясно; норма — 1013 гПа (760 мм).</div>
      <div class="note">Прогноз ${esc(f.issue)} на ${fmtRange(f.valid_from * 1000, f.valid_to * 1000)}${f.issued ? `, выпущен ${fmtTime(f.issued * 1000)}` : ""} (ветер — в баллах Бофорта)</div>
      <p class="sea-text">${esc(f.synopsis)}</p>
      <div class="note">Видимость: ${esc(f.visibility)} · предупреждения: ${esc(f.warnings)}</div>
      ${coasts ? `<details><summary class="note" style="cursor:pointer">Ветер и волнение по побережьям</summary>
        <div class="scroll"><table class="coast"><tr><th>Побережье</th><th>Когда</th><th>Ветер</th><th>Волнение</th></tr>${coasts}</table></div></details>` : ""}`;
    renderSst();
  }

  function renderSst() {
    if (!marine) return;
    const all = toMs(marine.sst);
    const b = periods.b(), a = periods.a(b);
    const cut = (set, [from, to], shift) => {
      const idx = set.ts.map((t, i) => i).filter(i => set.ts[i] >= from && set.ts[i] <= to);
      return { ts: idx.map(i => set.ts[i] + shift), sst: idx.map(i => set.sst[i]) };
    };
    sstChart.show(all.ts.length > 0);
    if (!all.ts.length) return;
    // the SST series is daily (one value per forecast issue): widen a short B to
    // three days so there is something to see; A is widened by the same amount
    const widen = Math.max(0, 3 * DAY - (b[1] - b[0]));
    const spanB = [b[0] - widen, b[1]];
    const spanA = a ? [a[0] - widen, a[1]] : null;
    const offset = a ? b[0] - a[0] : 0;
    sstChart.set(cut(all, spanB, 0), a ? cut(all, spanA, offset) : null, offset, spanB, { fit: fitAll() });
  }

  // ---------- radar ----------
  // the images on dom.org.cy are replaced in place; the forecast job records when each
  // last changed, and a radar that stopped updating is hidden instead of showing old rain
  const RADAR_FRESH_S = 3 * 3600;
  let radarImg = "RADAR_Static.png";
  function showRadar() {
    const status = (marine && marine.radar) || {};
    const fresh = img => status[img] && Date.now() / 1000 - status[img] < RADAR_FRESH_S;
    const anyFresh = Object.keys(status).some(fresh);
    const newest = Math.max(0, ...Object.values(status).filter(Boolean));
    $("radar-box").classList.toggle("hidden", !anyFresh);
    $("radar-note").textContent = anyFresh ? ""
      : Object.keys(status).length ? `Радары метеослужбы работают сезонно, с 15 октября по 15 июня; сейчас новых картинок нет (последняя — ${fmtDay(newest * 1000)}). Блок появится сам, как только радар снова начнёт обновляться.`
      : "статус радара ещё не проверен — запусти aranet-dom forecast";
    document.querySelectorAll("#radar-tabs button").forEach(b => { b.disabled = !fresh(b.dataset.img); });
    if (!anyFresh) return;
    if (!fresh(radarImg)) radarImg = Object.keys(status).find(fresh);
    document.querySelectorAll("#radar-tabs button").forEach(b => b.classList.toggle("active", b.dataset.img === radarImg));
    // bust the browser cache once per 5 minutes
    $("radar").src = `https://www.dom.org.cy/RADAR_IMG/${radarImg}?t=${Math.floor(Date.now() / 300000)}`;
  }
  document.querySelectorAll("#radar-tabs button").forEach(btn => btn.addEventListener("click", () => {
    radarImg = btn.dataset.img;
    showRadar();
  }));

  // ---------- daily archive ----------
  const CLIM = [
    { key: "tmax", el: "c-tmax", title: "Максимум за сутки, °C", unit: "°C", digits: 1, color: "--temp", daily: true,
      rows: ["mean", "max", { label: "дней ≥ 35 °C", get: s => Math.round(s.above(34.95) * s.n / 100), digits: 0, unit: "дн." }] },
    { key: "tmin", el: "c-tmin", title: "Минимум за сутки, °C", unit: "°C", digits: 1, color: "--hum", daily: true,
      rows: ["mean", "min", { label: "ночей ≥ 25 °C", get: s => Math.round(s.above(24.95) * s.n / 100), digits: 0, unit: "дн." }] },
    { key: "rain", el: "c-crain", title: "Осадки за сутки, мм", unit: "мм", digits: 1, color: "--rain", bar: true, daily: true,
      rows: ["sum", { label: "дней с осадками ≥ 1 мм", get: s => Math.round(s.above(0.95) * s.n / 100), digits: 0, unit: "дн." }] },
  ];
  const climCharts = CLIM.map(m => new SeriesChart($(m.el), m));
  linkCharts([...climCharts, sstChart]);
  let climStations = [], climSelected = prefs.get("climStation", "ATHALASSA");

  function setClimStation(code, reload = true) {
    if (!climStations.some(c => c.code === code)) return;
    climSelected = code;
    prefs.set("climStation", code);
    $("clim-station").value = code;
    const c = climStations.find(x => x.code === code);
    $("clim-meta").textContent = `${c.days} дней, ${fmtDay(new Date(c.first + "T00:00:00").getTime())} – ${fmtDay(new Date(c.last + "T00:00:00").getTime())}`;
    if (reload) loadClimate().catch(fail);
  }
  $("clim-station").addEventListener("change", () => setClimStation($("clim-station").value));

  async function loadClimStations() {
    climStations = await getJSON("/api/weather/climate/stations");
    $("clim-station").innerHTML = climStations.map(c => `<option value="${c.code}">${esc(c.name)}</option>`).join("");
    if (!climStations.some(c => c.code === climSelected) && climStations.length) climSelected = climStations[0].code;
    if (climStations.length) setClimStation(climSelected, false);
    else $("clim-meta").textContent = "архив ещё не загружен — запусти aranet-dom climate";
  }

  // the archive always shows its whole history: the period filter above doesn't apply
  let climSeq = 0;
  async function loadClimate() {
    if (!climStations.length) { climCharts.forEach(c => c.show(false)); return; }
    const seq = ++climSeq;
    const data = await getJSON(`/api/weather/climate?station=${encodeURIComponent(climSelected)}`);
    if (seq !== climSeq) return;
    const set = toMs(data);
    const range = set.ts.length ? [set.ts[0], Math.max(set.ts[set.ts.length - 1] + DAY, Date.now())] : [Date.now() - DAY, Date.now()];
    climCharts.forEach(c => { c.show(true); c.set(set, null, 0, range); });
  }

  const fail = e => { $("meta").textContent = "ошибка загрузки: " + e.message; };
  const periods = new Periods($("periods"), "weather", () => loadCharts().catch(fail));

  async function refresh() {
    try {
      await loadClimStations();
      loadClimate().catch(fail);
      loadMarine().then(showRadar).catch(fail);
      if (!(await loadStations())) return;
      periods.sync();
      await loadCharts();
    } catch (e) { fail(e); }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
