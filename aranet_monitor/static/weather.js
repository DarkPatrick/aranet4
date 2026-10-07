(() => {
  const { windName, windDir, utciLabel, utciBands, sunPosition, sunTimes, sunPhase, sunBands, sunLegend, compass, uvLabel, uvBands, breakGaps, gapLimit, HPA_TO_MM, pressureLabel, pressureBands, zoomOptions, armZoom, resetZoom, SPARSE, DAY, css, $, store, fmtTime, fmtDay, fmtRange, num, dot, toMs, nearest, typicalStep, Periods, SeriesChart, linkCharts, renderCompareTable } = UI;
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
    { key: "utci_shade", el: "c-net", title: "Ощущается (UTCI), °C", unit: "°C", digits: 1, color: "--feel", rows: ["mean", "min", "max"],
      extra: { key: "utci_sun", label: "на солнце", dash: "dashed", color: "--sun" }, notes: [{ key: "net", label: "NET метеослужбы" }],
      bands: utciBands(), describe: v => utciLabel(v) },
    { key: "rh", el: "c-rh", title: "Влажность, %", unit: "%", digits: 0, color: "--hum", rows: ["mean", "min", "max"] },
    { key: "rain", el: "c-rain", title: "Осадки, мм", unit: "мм", digits: 1, color: "--rain", bar: true,
      rows: ["sum", { label: "макс. за 10 мин", get: s => s.max }] },
    { key: "wind", el: "c-wind", title: "Ветер, м/с", unit: "м/с", digits: 1, color: "--wind", zeroBased: true, rows: ["mean", "max"],
      bands: BEAUFORT, describe: beaufort,
      notes: [{ key: "wdir", label: "направление", fmt: d => `${windName(d)} (${Math.round(d)}°)` }] },
    // direction as dots on a compass axis: a line would jump across north, arrows pile up on long periods
    { key: "wdir", el: "c-wdir", title: "Направление ветра (откуда дует)", unit: "°", digits: 0, color: "--wind", scatter: true,
      rows: [], describe: d => windName(d),
      axis: () => ({ min: 0, max: 360, interval: 90, scale: false,
                     axisLabel: { formatter: v => ({ 0: "С", 90: "В", 180: "Ю", 270: "З", 360: "С" })[v] ?? "" } }) },
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

  // ---------- map time: live, or a moment picked on the timeline under the map ----------
  // live: the latest observations and 4 h of lightning; on the timeline: what the stations
  // reported at that moment, the half hour of rain before it and the hour of lightning before it
  const LIVE_TRAIL = 4 * 3600, PAST_TRAIL = 3600, STEP = 600;
  const tl = { at: null, span: 24, end: null, history: null, bolts: null, timer: null };
  const lastLE = (arr, x) => { let lo = 0, hi = arr.length - 1, r = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m] <= x) { r = m; lo = m + 1; } else hi = m - 1; } return r; };

  function mapState(st) {
    if (tl.at == null) {
      const l = st.latest;
      return l && Date.now() / 1000 - l.ts <= STALE_S ? l : null;
    }
    const h = tl.history && tl.history[st.code];
    const i = h ? lastLE(h.ts, tl.at) : -1;
    if (i < 0 || tl.at - h.ts[i] > 1200) return null;
    let rain = null;
    for (let j = i; j >= 0 && h.ts[j] > tl.at - 1800; j--) if (h.rain[j] != null) rain = (rain || 0) + h.rain[j];
    return { ts: h.ts[i], temp: h.temp[i], rh: h.rh[i], wind10: h.wind10[i], wind2: h.wind2[i], wdir: h.wdir[i],
             rain_30m: rain == null ? null : Math.round(rain * 10) / 10 };
  }

  // ---------- lightning (Meteosat-12 Lightning Imager, ten-minute windows) ----------
  // older flashes are smaller, fainter and cooler in colour; live, the newest window is
  // replayed as flashes with its real timing (the Data Store publishes ~1 min after it closes)
  const BOLT_COLORS = [[0.08, "#fff7c2"], [0.2, "#ffd23f"], [0.4, "#ff9f1c"], [0.7, "#f2542d"], [1.01, "#b5179e"]];
  // a lightning-bolt glyph on the canvas renderer (fast with thousands of flashes): a circle
  // marker that draws a zigzag instead of a circle, `radius` setting its half-height
  const BOLT_SHAPE = [[0.2, -1], [-0.5, 0.15], [-0.02, 0.15], [-0.22, 1], [0.5, -0.18], [0.05, -0.18]];
  L.Canvas.include({
    _updateBolt(layer) {
      if (!this._drawing || layer._empty()) return;
      const p = layer._point, r = layer._radius, ctx = this._ctx;
      ctx.beginPath();
      BOLT_SHAPE.forEach(([x, y], i) => ctx[i ? "lineTo" : "moveTo"](p.x + x * r, p.y + y * r));
      ctx.closePath();
      this._fillStroke(ctx, layer);
    },
  });
  const BoltMarker = L.CircleMarker.extend({ _updatePath() { this._renderer._updateBolt(this); } });
  const boltCanvas = L.canvas({ padding: 0.2 });
  const boltLayer = L.layerGroup().addTo(map);
  let boltLive = null, boltWindowEnd = null, boltTimers = [];
  function boltDot(t, lat, lon, at, trail) {
    const f = Math.min(Math.max((at - t) / trail, 0), 1);
    return new BoltMarker([lat, lon], { renderer: boltCanvas, radius: 8 - 5 * f, weight: 0.8, color: "#3b2a00", lineJoin: "round",
      opacity: 1 - 0.8 * f, fillColor: BOLT_COLORS.find(([x]) => f < x)[1], fillOpacity: 1 - 0.85 * f, interactive: false });
  }
  function drawBolts(d, at, trail, skipFrom = Infinity) {
    boltLayer.clearLayers();
    if (!d) return 0;
    let n = 0;
    d.ts.forEach((t, i) => {
      if (t > at || t < at - trail) return;
      n++;
      if (t < skipFrom) boltDot(t, d.lat[i], d.lon[i], at, trail).addTo(boltLayer);
    });
    return n;
  }
  function boltFlash(t, lat, lon) {
    if (tl.at != null) return;  // the timeline took over meanwhile
    const icon = L.divIcon({ html: '<span class="bolt-flash"></span>', className: "", iconSize: null });
    const m = L.marker([lat, lon], { icon, interactive: false, keyboard: false, zIndexOffset: -500 }).addTo(map);
    setTimeout(() => { map.removeLayer(m); if (tl.at == null) boltDot(t, lat, lon, Date.now() / 1000, LIVE_TRAIL).addTo(boltLayer); }, 1100);
  }
  async function loadLightning() {
    const now = Date.now() / 1000;
    const d = await getJSON(`/api/weather/lightning?from=${Math.floor(now - LIVE_TRAIL)}`);
    boltLive = d;
    const w = d.window, fresh = w && w.end > (boltWindowEnd || 0);
    if (fresh) {
      boltTimers.forEach(clearTimeout);
      boltTimers = [];
      boltWindowEnd = w.end;
      d.ts.forEach((t, i) => {
        if (t >= w.start) boltTimers.push(setTimeout(() => boltFlash(t, d.lat[i], d.lon[i]), (t - w.start) * 1000));
      });
    }
    // flashes of the window being replayed become dots only after their flash
    if (tl.at == null) drawBolts(d, now, LIVE_TRAIL, fresh ? w.start : Infinity);
    boltNote();
  }
  // the summary under the map; flashes off the visible part get a "show" link
  function boltNote() {
    const note = $("map-bolt"), live = tl.at == null;
    const d = live ? boltLive : tl.bolts, at = live ? Date.now() / 1000 : tl.at, trail = live ? LIVE_TRAIL : PAST_TRAIL;
    if (!d || !d.window) { note.classList.add("hidden"); return; }
    const idx = d.ts.map((t, i) => i).filter(i => d.ts[i] <= at && d.ts[i] >= at - trail);
    const view = map.getBounds(), seen = idx.filter(i => view.contains([d.lat[i], d.lon[i]])).length;
    const hm = s => new Date(s * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    const period = live ? "за последние 4 часа" : `за час до ${hm(at)}`;
    note.classList.remove("hidden");
    if (!idx.length) { note.textContent = `Молний рядом с Кипром ${period} нет · спутник Meteosat-12${live ? `, данные по ${hm(d.window.end)}` : ""}`; return; }
    const off = idx.length - seen;
    note.innerHTML = `<span class="bolt-key"></span>Молнии ${period}: ${idx.length}` +
      (off ? ` (на видимой части карты ${seen}, <a href="#" id="bolt-show">показать все</a>)` : "") +
      ` · спутник Meteosat-12` + (live ? `, данные по ${hm(d.window.end)}; последние 10 минут проигрываются вспышками с задержкой ~${Math.max(1, Math.round((Date.now() / 1000 - d.window.start) / 60))} мин; старые молнии мельче и бледнее` : "");
    const link = $("bolt-show");
    if (link) link.onclick = e => {
      e.preventDefault();
      const pts = idx.map(i => [d.lat[i], d.lon[i]]).concat(stations.map(s => [s.lat, s.lon]));
      map.fitBounds(pts, { padding: [12, 12], animate: false });
    };
  }
  map.on("moveend", boltNote);
  // started after the whole script has run (getJSON is defined further down)
  setTimeout(() => loadLightning().catch(() => {}), 0);
  setInterval(() => loadLightning().catch(() => {}), 60e3);
  map.on("zoomend", () => drawMarkers());

  // ---------- the timeline ----------
  const tlFmt = s => new Date(s * 1000).toLocaleString("ru-RU", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  async function tlLoad() {
    const end = Math.floor(Date.now() / 1000 / STEP) * STEP, from = end - tl.span * 3600;
    const [h, b] = await Promise.all([getJSON(`/api/weather/map-history?from=${from - 1800}&to=${end}`),
                                      getJSON(`/api/weather/lightning?from=${from - PAST_TRAIL}&to=${end}`)]);
    Object.assign(tl, { end, history: h.stations, bolts: b });
    const r = $("tl-range");
    r.max = tl.span * 3600 / STEP;
    if (tl.at == null) r.value = r.max;
  }
  function tlApply() {
    const r = $("tl-range"), k = +r.value, n = +r.max;
    tl.at = k >= n ? null : tl.end - (n - k) * STEP;
    $("tl-label").textContent = tl.at == null ? "сейчас" : tlFmt(tl.at);
    $("tl-live").classList.toggle("hidden", tl.at == null);
    $("timeline").classList.toggle("past", tl.at != null);
    if (tl.at == null) drawBolts(boltLive, Date.now() / 1000, LIVE_TRAIL);
    else drawBolts(tl.bolts, tl.at, PAST_TRAIL);
    drawMarkers();
    boltNote();
  }
  function tlStop() { clearInterval(tl.timer); tl.timer = null; $("tl-play").textContent = "▶"; }
  $("tl-range").addEventListener("input", async () => {
    if (!tl.history) await tlLoad().catch(fail);
    tlApply();
  });
  $("tl-span").addEventListener("change", async () => {
    tlStop();
    tl.span = +$("tl-span").value;
    tl.at = null;
    await tlLoad().catch(fail);
    tlApply();
  });
  $("tl-live").addEventListener("click", () => { tlStop(); $("tl-range").value = $("tl-range").max; tlApply(); });
  $("tl-play").addEventListener("click", async () => {
    if (tl.timer) { tlStop(); return; }
    if (!tl.history) await tlLoad().catch(fail);
    const r = $("tl-range");
    if (+r.value >= +r.max) r.value = 0;  // from live: play the whole span from its start
    $("tl-play").textContent = "❚❚";
    tl.timer = setInterval(() => {
      r.value = +r.value + 1;
      tlApply();
      if (+r.value >= +r.max) tlStop();
    }, 350);
  });

  // rain now: the last half hour's sum as mm/h, WMO intensity classes (< 2.5 / < 7.6 / more)
  const DROP = `<svg class="drop" viewBox="0 0 10 14" width="7" height="10"><path d="M5 0C5 0 0 6.5 0 9a5 5 0 0 0 10 0C10 6.5 5 0 5 0z" fill="currentColor"/></svg>`;
  function rainLevel(mm30) {
    if (!(mm30 > 0)) return null;
    const rate = mm30 * 2;
    return rate < 2.5 ? { cls: "light", name: "слабый" } : rate < 7.6 ? { cls: "moderate", name: "умеренный" } : { cls: "heavy", name: "сильный" };
  }
  function rainSummary() {
    const raining = stations.filter(s => { const l = mapState(s); return l && l.rain_30m > 0; });
    const when = tl.at == null ? "сейчас (за последние 30 мин)" : `в ${new Date(tl.at * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })} (за 30 мин до этого)`;
    $("map-rain").classList.toggle("hidden", !raining.length);
    $("map-rain").innerHTML = raining.length
      ? `<span class="rain-ring"></span>Дождь ${when}: ${raining.map(s => label(s.code)).sort().join(", ")}` : "";
  }

  // an arrow from the station downwind, longer for stronger wind (10 m wind + direction only)
  function windArrow(l, labels) {
    if (!l || l.wdir == null || l.wind10 == null || l.wind10 < 0.3) return "";
    const start = labels ? 11 : 7, len = Math.min(8 + l.wind10 * 2.5, 30), R = start + len + 4;
    const tip = R - start - len, base = R - start;
    const path = `M${R} ${base} L${R} ${tip + 5} M${R - 4} ${tip + 6} L${R} ${tip} L${R + 4} ${tip + 6}`;
    return `<svg class="wx-wind" width="${2 * R}" height="${2 * R}" style="transform:translate(-50%,-50%) rotate(${(l.wdir + 180) % 360}deg)">` +
      `<path d="${path}" stroke="var(--card)" stroke-width="4.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` +
      `<path d="${path}" stroke="var(--text)" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }

  // wind arrows on the map are opt-in: 36 of them make the island hard to read
  let showWind = prefs.get("mapWind", false);
  $("map-wind").checked = showWind;
  $("map-wind").addEventListener("change", () => { showWind = $("map-wind").checked; prefs.set("mapWind", showWind); drawMarkers(); });

  function drawMarkers() {
    rainSummary();
    const labels = map.getZoom() >= LABEL_ZOOM, live = tl.at == null;
    for (const st of stations) {
      const l = mapState(st), stale = !l;
      const t = l ? l.temp : null;
      const { bg, fg } = tempColor(t);
      const rain = l ? rainLevel(l.rain_30m) : null;
      const cls = `wx-badge${labels ? "" : " dot"}${st.code === selected ? " sel" : ""}${rain ? ` rain ${rain.cls}` : ""}`;
      const text = labels ? (t == null ? "–" : Math.round(t) + "°") + (rain ? DROP : "") : "";
      const html = (showWind ? windArrow(l, labels) : "") + `<span class="${cls}" style="background:${bg};color:${fg}">${text}</span>`;
      const icon = L.divIcon({ html, className: "", iconSize: null });
      if (!markers[st.code]) {
        markers[st.code] = L.marker([st.lat, st.lon], { icon, keyboard: false })
          .addTo(map).on("click", () => select(st.code));
      } else {
        markers[st.code].setIcon(icon);
      }
      // live and stale: still show the last values, marked
      const shown = l || (live ? st.latest : null) || {};
      const parts = [shown.temp != null && `${num(shown.temp, 1)} °C`, shown.rh != null && `${num(shown.rh, 0)}%`,
                     (shown.wind10 ?? shown.wind2) != null && `ветер ${num(shown.wind10 ?? shown.wind2, 1)} м/с${shown.wdir != null && (shown.wind10 ?? 0) >= 0.3 ? ` ${windDir(shown.wdir)}` : ""}`].filter(Boolean);
      const rainLine = rain ? `<br><span style="color:${css("--rain")}">${rain.name} дождь: ${num(l.rain_30m, 1)} мм за 30 мин</span>` : "";
      const whenLine = live ? (stale ? "<br>⚠ данные устарели" : "") : (stale ? "<br>нет данных на этот момент" : `<br>${tlFmt(l.ts)}`);
      markers[st.code].bindTooltip(`<b>${label(st.code)}</b><br>${parts.join(" · ") || "нет данных"}${rainLine}${whenLine}`,
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

  // where the radiation behind "in the sun" comes from
  const radNote = src => !src ? "нет данных" : src.kind === "own" ? "датчик станции"
    : src.kind === "near" ? `станция ${label(src.station)}, ${num(src.km, 0)} км` : "модель Open-Meteo";

  function setTilesFor(st) {
    const l = st.latest || {};
    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    const tiles = [
      ["Температура", l.temp, 1, "°C"],
      ["Ощущается в тени", l.utci_shade, 1, "°C", l.utci_shade != null ? utciLabel(l.utci_shade) : ""],
      ["Ощущается на солнце", l.utci_sun, 1, "°C", l.utci_sun == null ? "" : l.utci_sun === l.utci_shade && sunPosition(l.ts * 1000, st.lat, st.lon).elevation <= 0
        ? "солнце за горизонтом" : `${utciLabel(l.utci_sun)} · радиация: ${radNote(l.rad_src)}`], ["Влажность", l.rh, 0, "%"], ["Осадки за 10 мин", l.rain, 1, "мм"],
      w && [w[1], l[w[0]], 1, "м/с", l[w[0]] != null ? BEAUFORT.find(b => l[w[0]] < b.to).name +
        (l.wdir != null && (l.wind10 ?? 0) >= 0.3 ? ` · ${windName(l.wdir)} (${Math.round(l.wdir)}°)` : "") : ""], p && [p[1], l[p[0]], 1, "hPa"], ["Радиация", l.rad_global, 0, "W/m²"], ["Снег", l.snow, 0, "см"],
    ].filter(x => x && x[1] != null);
    $("tiles").innerHTML = tiles.map(([n, v, d, u, sub]) =>
      `<div class="tile"><div class="label">${n}</div><div class="value">${num(v, d)}<span class="unit">${u}</span></div>${sub ? `<div class="note" style="margin:0">${sub}</div>` : ""}</div>`).join("");
    const age = l.ts ? Math.round((Date.now() / 1000 - l.ts) / 60) : null;
    const since = st.first ? ` · история с ${fmtTime(st.first * 1000)}` : "";
    $("station-meta").textContent = l.ts
      ? `${st.lat.toFixed(3)}, ${st.lon.toFixed(3)} · данные на ${fmtTime(l.ts * 1000)} (${age} мин назад)${age > 60 ? " ⚠ устарели" : ""}${since}`
      : "нет данных";
  }

  // ---------- the station nearest to the viewer (browser geolocation; HTTPS or localhost only) ----------
  const canGeo = "geolocation" in navigator && window.isSecureContext;
  $("geo").classList.toggle("hidden", !canGeo);
  let meMarker = null, geoTried = false;
  const distKm = (lat1, lon1, lat2, lon2) => {
    const r = Math.PI / 180, a = Math.sin((lat2 - lat1) * r / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(a));
  };
  const locate = () => new Promise((ok, err) =>
    navigator.geolocation.getCurrentPosition(p => ok(p.coords), err, { timeout: 20000, maximumAge: 10 * 60e3 }));

  // nearest station with fresh data (any station if none is fresh)
  function nearestStation(lat, lon) {
    const now = Date.now() / 1000, live = stations.filter(s => s.latest && now - s.latest.ts < STALE_S);
    return (live.length ? live : stations).map(s => ({ st: s, km: distKm(lat, lon, s.lat, s.lon) })).sort((a, b) => a.km - b.km)[0];
  }

  // byUser: the button (say why it failed); otherwise the automatic pick on opening, which
  // stays quiet and doesn't override a station chosen while the browser was locating
  async function goNearest(byUser) {
    const before = selected;
    let c;
    try { c = await locate(); } catch (e) {
      if (byUser) $("geo-note").textContent = e.code === 1 ? "доступ к геолокации запрещён в настройках браузера" : "не удалось определить местоположение";
      return;
    }
    const near = nearestStation(c.latitude, c.longitude);
    if (!near) return;
    if (!meMarker) meMarker = L.circleMarker([c.latitude, c.longitude], { radius: 7, weight: 2, color: "#fff", fillColor: "#2f7fd1", fillOpacity: 1 })
      .addTo(map).bindTooltip("вы здесь", { direction: "top", offset: [0, -6] });
    else meMarker.setLatLng([c.latitude, c.longitude]);
    $("geo-note").textContent = `ближайшая к вам: ${label(near.st.code)}, ${num(near.km, near.km < 10 ? 1 : 0)} км`;
    if (!byUser && selected !== before) return;
    if (near.st.code !== selected) select(near.st.code);
  }
  $("geo").addEventListener("click", () => goNearest(true));

  // on opening: ask (or use the permission already given), unless the browser has it denied
  async function autoNearest() {
    if (!canGeo || geoTried) return;
    geoTried = true;
    try {
      const p = navigator.permissions && await navigator.permissions.query({ name: "geolocation" });
      if (p && p.state === "denied") return;
    } catch (e) { /* no Permissions API (older Safari): just ask */ }
    goNearest(false);
  }

  // "Всё" spans both sources: the station's history and the home sensor's
  let homeFirst = null, airFirst = null; // the air-quality history (since 2016) can open the period further back
  function setBounds(st) {
    const firsts = [st.first, homeFirst, airFirst].filter(Boolean);
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
  const fetchStation = (code, range, agg) => getJSON(`/api/weather/readings?station=${encodeURIComponent(code)}&${q(range)}&${periods.query(agg)}`);

  function derive(data, st) {
    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    data.wind = w ? data[w[0]] : [];
    // direction in a calm says nothing: no dot below 0.3 m/s (the 10 m wind it comes with)
    if (data.wdir) data.wdir = data.wdir.map((d, i) => (data.wind10 && data.wind10[i] != null && data.wind10[i] >= 0.3) ? d : null);
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
    const dataB = await fetchStation(st.code, b);
    // A bucketed like B; the home sensor picks its own step from its own data span
    const [dataA, homeB] = await Promise.all([
      a ? fetchStation(st.code, a, dataB.agg) : Promise.resolve(null),
      getJSON(`/api/readings?${q(b)}&${periods.query()}`),
    ]);
    if (seq !== loadSeq) return; // a newer selection is already loading
    periods.resolved(dataB.agg);
    derive(dataB, st);
    if (dataA) derive(dataA, st);
    const offset = a ? b[0] - a[0] : 0;
    const setB = toMs(dataB), setA = dataA ? toMs(dataA, offset) : null;

    const w = pick(WIND, st.metrics), p = pick(PRES, st.metrics);
    const has = m => m.key === "wind" ? !!w : m.key === "pres" ? !!p : st.metrics.includes(m.key);
    METRICS.forEach((m, i) => {
      if (m.key === "wind" && w) m.title = `${w[1]}, м/с`;
      if (m.key === "utci_shade") m.subtitle = `пунктир — на солнце (радиация: ${radNote(dataB.rad_src)}) · NET метеослужбы — в подсказке`;
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
    if (a) renderCompareTable($("cmp-table"), METRICS.filter(m => has(m) && !m.scatter), dataB, dataA);

    home.sets = { home: toMs(homeB), out: setB };
    renderHome(b);
    renderSst();
    loadSunUv().catch(fail);
    loadAir().catch(fail);
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
    const line = (s, k) => ({ data: breakGaps(s.ts.map((t, i) => [t, s[k][i]]), gapLimit(typicalStep(s))),
                              showSymbol: s.ts.length < SPARSE, symbolSize: 5 });
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
    airFirst = (await getJSON("/api/weather/air/first")).first;
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
    const f = marine.forecast;
    renderAlerts(marine.alerts || [], marine.warnings && marine.warnings.text, f);
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

  // Meteoalarm awareness types and levels
  const ALERT_TYPES = { 1: "сильный ветер", 2: "снег, гололёд", 3: "грозы", 4: "туман", 5: "жара", 6: "холод",
    7: "явления на побережье", 8: "пожароопасность", 9: "лавины", 10: "сильный дождь", 12: "паводки", 13: "дождь и паводки" };
  const ALERT_LEVELS = { 2: ["🟡", "Жёлтое"], 3: ["🟠", "Оранжевое"], 4: ["🔴", "Красное"] };
  // the Department writes descriptions in capitals: make them readable
  const sentenceCase = t => (t || "").toLowerCase().replace(/(^\s*|[.!?]\s+)([a-zа-яё])/g, (m, p1, c) => p1 + c.toUpperCase());

  function renderAlerts(alerts, agrometText, f) {
    const now = Date.now() / 1000;
    const span = a => {
      const sameDay = new Date(a.onset * 1000).toDateString() === new Date(a.expires * 1000).toDateString();
      const end = sameDay ? new Date(a.expires * 1000 + 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : fmtTime(a.expires * 1000 + 1000);
      return `${fmtTime(a.onset * 1000)} – ${end}`;
    };
    // in force first, then upcoming; within each, the most severe first
    const items = alerts.sort((a, b) => (a.onset > now) - (b.onset > now) || (b.level || 0) - (a.level || 0) || a.onset - b.onset).map(a => {
      const [icon, level] = ALERT_LEVELS[a.level] || ["⚠", ""];
      const what = ALERT_TYPES[a.type] || esc(a.event);
      const when = a.onset > now ? `начнётся через ${Math.max(1, Math.round((a.onset - now) / 3600))} ч` : "действует сейчас";
      return `<div class="alert lvl${a.level || 0}">
        <div class="alert-title">${icon} ${level} предупреждение: ${what}</div>
        <div class="alert-when">${span(a)} · ${when}${a.areas && a.areas !== "Cyprus" ? " · " + esc(a.areas) : ""}</div>
        <div class="alert-text">${esc(sentenceCase(a.description))}</div>
        ${a.instruction || a.description_el ? `<details><summary>что делать и оригинал</summary>
          ${a.instruction ? `<p>${esc(sentenceCase(a.instruction))}</p>` : ""}
          ${a.description_el ? `<p lang="el">${esc(a.description_el)}</p>` : ""}</details>` : ""}
      </div>`;
    });
    // agromet's own card, if it shows something Meteoalarm doesn't have
    if (!items.length && agrometText) items.push(`<div class="alert lvl0"><div class="alert-title">⚠ Предупреждение метеослужбы</div><div class="alert-text" lang="el">${esc(agrometText)}</div></div>`);
    if (f && f.warnings && !/^nil$/i.test(f.warnings.trim()))
      items.push(`<div class="alert lvl0"><div class="alert-title">⚠ Море</div><div class="alert-text">${esc(f.warnings)}</div></div>`);
    $("warn-banner").innerHTML = items.join("");
    $("warn-banner").classList.toggle("hidden", !items.length);
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

  // ---------- sun and UV (for the selected station's coordinates) ----------
  const SUN = { key: "elev", title: "Высота солнца над горизонтом, °", unit: "°", digits: 0, color: "--sun", rows: ["max"],
                bands: sunBands(), describe: v => sunPhase(v), limits: [-90, 90], nowLine: true };
  const UV = { key: "uv", title: "UV-индекс (CAMS)", unit: "", digits: 1, color: "--uv", zeroBased: true, rows: ["mean", "max"],
               bands: uvBands(), describe: v => uvLabel(v), nowLine: true };
  const sunChart = new SeriesChart($("c-sun"), SUN), uvChart = new SeriesChart($("c-uv"), UV);
  // thin twilight bands can't carry a name inside the chart: list them under it
  const drawSunLegend = () => { $("sun-legend").innerHTML = sunLegend().map(([c, name, range]) =>
    `<span><span class="sw" style="background:${c}"></span>${name}: ${range}</span>`).join(""); };
  drawSunLegend();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { SUN.bands = sunBands(); drawSunLegend(); });
  linkCharts([sunChart, uvChart]); // own group: their axis reaches into the forecast
  const AHEAD = 48 * 3600e3;
  const hm = ms => new Date(ms).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  // elevation samples over [from, to], at most ~1500 points
  function sunSeries(st, [from, to], shift = 0) {
    const step = Math.max(10 * 60e3, (to - from) / 1500), out = { ts: [], elev: [] };
    for (let t = from; t <= to; t += step) { out.ts.push(t + shift); out.elev.push(Math.round(sunPosition(t, st.lat, st.lon).elevation * 10) / 10); }
    return out;
  }

  let uvSeq = 0;
  async function loadSunUv() {
    const st = byCode[selected];
    const b = periods.b();
    if (!st || b[0] == null) return;
    const a = periods.a(b), now = Date.now();
    // when the period ends now, look two days ahead (UV forecast, tomorrow's sun)
    const ahead = b[1] >= now - 600e3 ? AHEAD : 0;
    const rb = [b[0], b[1] + ahead], ra = a ? [a[0], a[1] + ahead] : null, offset = a ? b[0] - a[0] : 0;
    const seq = ++uvSeq;
    const uvUrl = (r, agg) => `/api/weather/uv?station=${encodeURIComponent(st.code)}&${q(r)}&${periods.query(agg)}`;
    const uB = await getJSON(uvUrl(rb));
    const uA = ra ? await getJSON(uvUrl(ra, uB.agg)) : null;
    if (seq !== uvSeq) return;
    sunChart.set(sunSeries(st, rb), ra ? sunSeries(st, ra, offset) : null, offset, rb);
    // altitude correction only where it changes something (stations above ~150 m)
    const alt = uB.elevation != null && uB.elevation >= 150;
    UV.extra = alt ? { key: "uv_alt", label: `с поправкой на высоту ${Math.round(uB.elevation)} м`, dash: "dotted" } : null;
    UV.subtitle = alt ? `точками — с поправкой на высоту ${Math.round(uB.elevation)} м` : "";
    uvChart.show(uB.ts.length > 0);
    if (uB.ts.length) { uvChart.applyTheme(); uvChart.set(toMs(uB), uA ? toMs(uA, offset) : null, offset, rb); }

    // tiles: now / today
    const pos = sunPosition(now, st.lat, st.lon), day = sunTimes(now, st.lat, st.lon);
    const len = day.rise && day.set ? Math.round((day.set - day.rise) / 60e3) : null;
    const iNow = uB.ts.findIndex(t => t * 1000 > now - 3600e3 && t * 1000 <= now);
    const uvNow = iNow >= 0 ? uB.uv[iNow] : null, uvNowAlt = iNow >= 0 ? uB.uv_alt[iNow] : null;
    const today = new Date(now).toDateString();
    let uvMax = null, uvMaxAt = null, uvMaxAlt = null;
    uB.ts.forEach((t, i) => { if (new Date(t * 1000).toDateString() === today && uB.uv[i] != null && (uvMax == null || uB.uv[i] > uvMax)) { uvMax = uB.uv[i]; uvMaxAt = t * 1000; uvMaxAlt = uB.uv_alt[i]; } });
    const altNote = v => alt && v ? ` · с высотой ${num(v, 1)}` : "";
    const tile = (label, value, sub) => `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div><div class="note" style="margin:0">${sub}</div></div>`;
    $("sun-tiles").innerHTML = [
      tile("Солнце сейчас", `${pos.elevation >= 0 ? "+" : "−"}${num(Math.abs(pos.elevation), 1)}<span class="unit">°</span>`,
           `${sunPhase(pos.elevation)} · азимут ${Math.round(pos.azimuth)}° (${compass(pos.azimuth)})`),
      tile("Восход – закат", day.rise && day.set ? `${hm(day.rise)}–${hm(day.set)}` : "–",
           `${len != null ? `день ${Math.floor(len / 60)} ч ${len % 60} мин · ` : ""}полдень ${hm(day.noon)}, ${num(day.maxElevation, 0)}°`),
      tile("UV сейчас", uvNow != null ? num(uvNow, 1) : "–", uvNow != null ? uvLabel(uvNow) + altNote(uvNowAlt) : "нет данных"),
      tile("UV максимум сегодня", uvMax != null ? num(uvMax, 1) : "–", uvMax != null ? `${uvLabel(uvMax)} · около ${hm(uvMaxAt)}${altNote(uvMaxAlt)}` : "нет данных"),
    ].join("");
    $("sun-h").textContent = `Солнце и UV · ${label(st.code)}`;
  }

  // ---------- air quality: nearest DLI measurements + CAMS at the station ----------
  // the DLI network's hourly scale (µg/m³): low / moderate / high / very high
  const AIR_LEVELS = ["низкий", "умеренный", "высокий", "очень высокий"];
  const AIR_SCALE = { pm25: [25, 50, 100], pm10: [50, 100, 200], no2: [100, 150, 200], o3: [100, 140, 180] };
  const airLevel = (p, v) => { const i = AIR_SCALE[p].findIndex(to => v < to); return AIR_LEVELS[i < 0 ? 3 : i]; };
  const airBands = p => [...AIR_SCALE[p], Infinity].map((to, i, a) =>
    ({ to, label: `${AIR_LEVELS[i]} (${Number.isFinite(to) ? `${i ? a[i - 1] : 0}–${to}` : `> ${a[i - 1]}`})` }));
  const EAQI = [[20, "хороший"], [40, "удовлетворительный"], [60, "умеренный"], [80, "плохой"], [100, "очень плохой"], [Infinity, "крайне плохой"]];
  const eaqiLabel = v => EAQI.find(([to]) => v < to)[1];
  const AIR = [
    { p: "pm25", el: "c-pm25", name: "PM2.5", color: "--pm25" },
    { p: "pm10", el: "c-pm10", name: "PM10", color: "--pm10" },
    { p: "dust", el: "c-dust", name: "Пыль (модель CAMS)", color: "--dust" },
    { p: "no2", el: "c-no2", name: "NO₂", color: "--no2" },
    { p: "o3", el: "c-o3", name: "O₃ (озон)", color: "--o3" },
  ].map(a => Object.assign(a, { key: a.p, title: a.name, unit: "мкг/м³", digits: 1, zeroBased: true, rows: ["mean", "max"],
    nowLine: true, fitData: true, bands: AIR_SCALE[a.p] ? airBands(a.p) : null, describe: AIR_SCALE[a.p] ? v => airLevel(a.p, v) : null }));
  // dust is whole µg/m³ and ~0-2 on a clean day: keep the axis at 10+ so that noise doesn't look like an event
  AIR.find(m => m.p === "dust").axis = sets => {
    const hi = Math.max(0, ...[sets.b, sets.a].filter(Boolean).flatMap(s => s.dust_cams).filter(v => v != null));
    return { min: 0, max: hi < 10 ? 10 : null };
  };
  const airCharts = AIR.map(m => new SeriesChart($(m.el), m));
  linkCharts(airCharts);

  let airSeq = 0;
  async function loadAir() {
    const st = byCode[selected];
    const b = periods.b();
    if (!st || b[0] == null) return;
    const a = periods.a(b), now = Date.now();
    const ahead = b[1] >= now - 600e3 ? AHEAD : 0;
    const rb = [b[0], b[1] + ahead], ra = a ? [a[0], a[1] + ahead] : null, offset = a ? b[0] - a[0] : 0;
    const seq = ++airSeq;
    const url = (r, agg) => `/api/weather/air?station=${encodeURIComponent(st.code)}&${q(r)}&${periods.query(agg)}`;
    const dB = await getJSON(url(rb));
    const dA = ra ? await getJSON(url(ra, dB.agg)) : null;
    // coarse steps: the tiles still need the latest hours as they are
    const coarse = !["raw", "hour"].includes(dB.agg);
    const dNow = coarse ? await getJSON(url([now - 6 * 3600e3, now + 3600e3], "raw")) : dB;
    if (seq !== airSeq) return;
    const measured = p => !!dB.sources[p] && dB[p].some(v => v != null);
    const where = s => `${s.name}, ${s.kind}, ${num(s.km, 0)} км`;
    AIR.forEach((m, i) => {
      const meas = measured(m.p), key = meas ? m.p : m.p + "_cams";
      m.key = key;
      m.extra = meas ? { key: m.p + "_cams", label: "модель CAMS", dash: "dotted" } : null;
      m.title = `${m.name}, мкг/м³`;
      m.subtitle = meas ? `${where(dB.sources[m.p])} · точками — модель CAMS`
                        : m.p === "dust" ? "" : "модель CAMS (рядом не меряют)";
      const has = dB[key].some(v => v != null);
      airCharts[i].show(has);
      if (has) { airCharts[i].applyTheme(); airCharts[i].set(toMs(dB), dA ? toMs(dA, offset) : null, offset, rb); }
    });

    // tiles: the latest measurement (≤ 3 h old), else the model's current hour
    const iNow = dNow.ts.findIndex(t => t * 1000 > now - 3600e3 && t * 1000 <= now);
    const cams = k => iNow >= 0 ? dNow[k + "_cams"][iNow] : null;
    const latest = p => {
      for (let i = dNow.ts.length - 1; i >= 0; i--) {
        if (dNow.ts[i] * 1000 > now) continue;
        if (dNow.ts[i] * 1000 < now - 3 * 3600e3) break;
        if (dNow[p][i] != null) return { v: dNow[p][i], at: dNow.ts[i] * 1000 };
      }
      return null;
    };
    const tile = (lbl, value, sub) => `<div class="tile"><div class="label">${lbl}</div><div class="value">${value}<span class="unit">${value === "–" ? "" : "мкг/м³"}</span></div><div class="note" style="margin:0">${sub}</div></div>`;
    const tiles = ["pm25", "pm10", "no2", "o3"].map(p => {
      const name = AIR.find(m => m.p === p).name, x = latest(p), c = cams(p);
      if (x) return tile(name, num(x.v, 1), `${airLevel(p, x.v)} · ${dNow.sources[p].name}, ${hm(x.at)}${c != null ? ` · модель ${num(c, 1)}` : ""}`);
      return tile(name, c != null ? num(c, 1) : "–", c != null ? `${airLevel(p, c)} · модель CAMS` : "нет данных");
    });
    const dust = cams("dust"), aqi = cams("eaqi");
    tiles.push(tile("Пыль", dust != null ? num(dust, 1) : "–", dust != null ? "сахарская, модель CAMS" : "нет данных"));
    tiles.push(`<div class="tile"><div class="label">Индекс EAQI</div><div class="value">${aqi != null ? Math.round(aqi) : "–"}</div><div class="note" style="margin:0">${aqi != null ? `${eaqiLabel(aqi)} · модель CAMS` : "нет данных"}</div></div>`);
    $("air-tiles").innerHTML = tiles.join("");
    $("air-h").textContent = `Качество воздуха · ${label(st.code)}`;
  }

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
    loadRecords().catch(fail);
  }

  // ---------- records for today's date and the current hour (ERA5-Land since 1950 + the archive) ----------
  const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const dayName = md => `${+md.slice(3)} ${MONTHS_GEN[+md.slice(0, 2) - 1]}`;
  const atDate = s => s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(0, 4)}` : "";  // "YYYY-MM-DD..." -> DD.MM.YYYY
  let recSeq = 0;
  async function loadRecords() {
    const seq = ++recSeq, code = climSelected;
    const r = await getJSON(`/api/weather/climate/records?station=${encodeURIComponent(code)}`);
    if (seq !== recSeq) return;
    const day = dayName(r.md), hh = `${String(r.hour).padStart(2, "0")}:00`;
    const t = v => v == null ? "–" : `${num(v, 1)}<span class="unit">°C</span>`;
    const tile = (lbl, value, sub) => `<div class="tile"><div class="label">${lbl}</div><div class="value">${value}</div><div class="note" style="margin:0">${sub}</div></div>`;
    const h = r.hourly, d = r.daily, a = r.archive;
    const arch = (v, at) => a && v != null ? `<br>архив метеослужбы: ${num(v, 1)} °C, ${atDate(at)}` : "";
    $("clim-tiles").innerHTML = !h && !d ? "" : [
      tile(`Мин. в ${hh}`, t(h && h.min), h ? atDate(h.min_at) : "нет данных"),
      tile(`Макс. в ${hh}`, t(h && h.max), h ? atDate(h.max_at) : "нет данных"),
      tile("Мин. за день", t(d && d.min), d ? `${atDate(d.min_at)}, ${d.min_at.slice(11, 16)}${arch(a && a.min, a && a.min_at)}` : "нет данных"),
      tile("Макс. за день", t(d && d.max), d ? `${atDate(d.max_at)}, ${d.max_at.slice(11, 16)}${arch(a && a.max, a && a.max_at)}` : "нет данных"),
    ].join("");
    $("clim-tiles-head").classList.toggle("hidden", !h && !d);
    $("clim-tiles-head").textContent = `Рекорды ${day}${r.period ? ` за ${r.period[0].slice(0, 4)}–${r.period[1].slice(0, 4)}` : ""}`;
    const note = $("clim-tiles-note");
    note.classList.remove("hidden");
    note.textContent = r.period
      ? `Рекорды температуры для этой даты и часа за ${r.period[0].slice(0, 4)}–${atDate(r.period[1])}: реанализ ERA5-Land (Copernicus, сетка ~9 км) в координатах станции; реанализ сглаживает пики, измеренные рекорды бывают выше на 1–3 °C. «Архив метеослужбы» — измеренный рекорд этой даты${a ? ` за ${a.period[0].slice(0, 4)}–${a.period[1].slice(0, 4)}` : ""}.`
      : "Рекорды за 1950–сегодня ещё загружаются (реанализ ERA5-Land, первая загрузка занимает две ночи).";
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
      autoNearest(); // once, in the background: charts load for the saved station meanwhile
      periods.sync();
      await loadCharts();
    } catch (e) { fail(e); }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();
