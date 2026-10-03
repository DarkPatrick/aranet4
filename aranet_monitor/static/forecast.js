(() => {
  const $ = id => document.getElementById(id);
  const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = ms => new Date(ms).toLocaleString("ru-RU", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const PROVIDER = { google: "Google Translate", mymemory: "MyMemory" };
  const ISSUE_TIME = { A: "05:00", B: "11:00", C: "16:00" };
  let data = null, current = null;

  function render() {
    const b = data.bulletins.find(x => x.issue === current) || data.bulletins[0];
    if (!b) return;
    document.querySelectorAll("#issues button").forEach(btn => btn.classList.toggle("active", btn.dataset.issue === b.issue));
    const days = (b.outlook || "").match(/(\d+)/);
    $("period").textContent = `на ${fmt(b.valid_from * 1000)} – ${fmt(b.valid_to * 1000)}${days ? ` + общий прогноз на ${days[1]} дня` : ""}`;
    const ageH = Math.round((Date.now() / 1000 - b.issued) / 3600);
    $("meta").textContent = `Бюллетень ${b.issue}, выпущен ${fmt(b.issued * 1000)} (${ageH} ч назад)`;

    // the evening issue ends its text with "the temperatures recorded today were:", which
    // only introduces the table below
    const paras = b.observed.length && /:\s*$/.test(b.paragraphs[b.paragraphs.length - 1]?.el || "")
      ? b.paragraphs.slice(0, -1) : b.paragraphs;
    const untranslated = paras.some(p => !p.ru);
    $("text").innerHTML = paras.map((p, i) =>
      `<p class="${i === 0 ? "synopsis" : ""}" ${p.ru ? "" : 'lang="el"'}>${esc(p.ru || p.el)}</p>`).join("") +
      `<div class="note">${untranslated ? "Часть текста пока без перевода — показан оригинал. " : ""}` +
      `${b.providers.length ? "Перевод: " + b.providers.map(p => PROVIDER[p] || p).join(", ") + " (машинный)." : ""}</div>` +
      `<details><summary class="note" style="cursor:pointer">Оригинал на греческом</summary>${paras.map(p => `<p lang="el">${esc(p.el)}</p>`).join("")}</details>`;

    $("observed-panel").classList.toggle("hidden", !b.observed.length);
    $("observed").innerHTML = `<tr><th>Пункт</th><th>Макс.</th><th>Мин.</th><th>Влажн. 15:00</th></tr>` +
      b.observed.map(o => `<tr><td>${esc(o.place)}</td><td>${o.tmax ?? "–"} °C</td><td>${o.tmin ?? "–"} °C</td><td>${o.rh ?? "–"} %</td></tr>`).join("");

    // the images are replaced in place on each issue: version them by issue time
    $("table").src = `${b.table_image}?v=${b.issued}`;
    $("table-link").href = b.table_image;
  }

  const num = (v, d = 0) => v == null ? "–" : v.toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d });
  const signed = (v, d) => (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d);
  const monthName = (y, m) => new Date(y, m - 1, 1).toLocaleDateString("ru-RU", { month: "long" });
  const ru = t => t && (t.ru || t.el);
  const lang = t => t && !t.ru ? ' lang="el"' : "";

  function renderClimate(c) {
    const s = c && c.seasonal, m = c && c.monthly;
    $("season-h").classList.toggle("hidden", !s);
    $("season").classList.toggle("hidden", !s);
    if (s) {
      const p = (s.period || "").match(/^(\d{4})-(\d{2})\.\.(\d{2})$/);
      if (p) $("season-h").textContent = `Сезонный прогноз: ${monthName(+p[1], +p[2])} – ${monthName(+p[1], +p[3])} ${p[1]}`;
      $("season").innerHTML = `<p${lang(s.summary)}>${esc(ru(s.summary))}</p>
        <div class="note">Тенденция по ансамблю сезонных моделей Copernicus C3S, отклонения — от средних за 1993–2016. Это общий характер сезона, а не прогноз конкретных дней. <a href="${esc(s.url)}" target="_blank" rel="noopener">Полный документ (PDF, по-гречески)</a></div>`;
    }
    $("month-h").classList.toggle("hidden", !m);
    $("month").classList.toggle("hidden", !m);
    if (m) {
      const p = (m.period || "").match(/^(\d{4})-(\d{2})$/);
      if (p) $("month-h").textContent = `Прошлый месяц: ${monthName(+p[1], +p[2])} ${p[1]}`;
      const tile = (label, value, sub) => `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="note" style="margin:0">${sub}</div>` : ""}</div>`;
      const norm = k => m.norms && m.norms[k] ? ` ${m.norms[k]}` : "";  // baseline as the report states it
      const tiles = [
        m.temp_anomaly != null && tile("Температура", `${signed(m.temp_anomaly, 2)}<span class="unit">°C</span>`, `к норме${norm("temperature")}`),
        m.rain_mm != null && tile("Осадки за месяц", `${num(m.rain_mm)}<span class="unit">мм</span>`, m.rain_pct != null ? `${num(m.rain_pct)} % нормы${norm("rain")}` : ""),
        m.season_rain_mm != null && tile("С октября (гидрологический год)", `${num(m.season_rain_mm)}<span class="unit">мм</span>`, m.season_rain_pct != null ? `${num(m.season_rain_pct)} % нормы${norm("rain")}` : ""),
      ].filter(Boolean).join("");
      const sec = m.sections || {};
      const more = [["events", "Заметные явления"], ["rain", "Осадки"], ["temperature", "Температура"]]
        .filter(([k]) => sec[k] && sec[k].el)
        .map(([k, title]) => `<details><summary class="note" style="cursor:pointer">${title}</summary><p${lang(sec[k])}>${esc(ru(sec[k]))}</p></details>`).join("");
      $("month").innerHTML = `<div class="tiles" style="margin:0 0 8px">${tiles}</div>
        ${sec.general ? `<p${lang(sec.general)}>${esc(ru(sec.general))}</p>` : ""}${more}
        <div class="note">Ежемесячный бюллетень метеослужбы. <a href="${esc(m.url)}" target="_blank" rel="noopener">Полный документ (PDF, по-гречески)</a></div>`;
    }
  }

  async function load() {
    try {
      data = await (await fetch("/api/weather/forecast", { cache: "no-store" })).json();
    } catch (e) { $("meta").textContent = "ошибка загрузки: " + e.message; return; }
    if (!data.bulletins.length) { $("meta").textContent = "прогнозов пока нет — запусти aranet-forecast"; return; }
    const order = ["A", "B", "C"];
    $("issues").innerHTML = data.bulletins.slice().sort((x, y) => order.indexOf(x.issue) - order.indexOf(y.issue))
      .map(b => `<button data-issue="${b.issue}">${b.issue} · ${ISSUE_TIME[b.issue] || ""}</button>`).join("");
    document.querySelectorAll("#issues button").forEach(btn => btn.addEventListener("click", () => { current = btn.dataset.issue; render(); }));
    if (!current) current = data.bulletins[0].issue; // newest
    $("map-img").src = `${data.map_image}?v=${data.bulletins[0].issued}`;
    render();
    renderClimate(data.climate);
  }

  load();
  setInterval(load, 15 * 60 * 1000);
})();
