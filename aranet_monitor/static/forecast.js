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
  }

  load();
  setInterval(load, 15 * 60 * 1000);
})();
