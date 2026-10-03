// Shared dashboard pieces: period B / comparison A controls, stats table, charts with A/B overlay.
window.UI = (() => {
  const HOUR = 3600e3, DAY = 24 * HOUR;
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
  const typicalStep = set => set && set.ts.length > 1 ? (set.ts[set.ts.length - 1] - set.ts[0]) / (set.ts.length - 1) : 600e3;

  // nearest point to t in a columnar {ts (ms, ascending), [key]} set, or null if too far
  function nearest(set, key, t, maxGap) {
    if (!set || !set.ts.length) return null;
    let lo = 0, hi = set.ts.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; (set.ts[mid] < t ? lo = mid : hi = mid); }
    const i = Math.abs(set.ts[lo] - t) <= Math.abs(set.ts[hi] - t) ? lo : hi;
    if (Math.abs(set.ts[i] - t) > maxGap) return null;
    return { t: set.ts[i], v: set[key][i] };
  }

  // sum `key` into buckets of `size` ms (local-day buckets when size >= DAY)
  function bucketSum(set, key, size) {
    const out = { ts: [], [key]: [] };
    if (!set) return out;
    const bucket = t => {
      if (size >= DAY) { const d = new Date(t); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
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
        const digits = r.pp ? 1 : m.digits;
        const unit = r.pp ? "%" : m.unit;
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
  const dot = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:5px;background:${c};margin-right:6px"></span>`;

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
      const when = ms => m.bar && this.step >= DAY ? fmtDay(ms) : fmtTime(ms);
      const line = (p, color, label, at) => p && p.v != null
        ? `<div>${dot(color)}${label} ${when(at)}: <b>${num(p.v, m.digits)} ${m.unit}</b></div>` : "";
      let html = line(b, css(m.color), "B", b && b.t) + line(a, css("--cmp"), "A", a && a.t - this.offset);
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
      if (m.decorate) m.decorate(opt);
      this.chart.setOption(opt, true);
      if (this.sets.b) this.render();
    }

    // setB/setA: ms-based columnar sets (A already shifted by `offset`)
    set(setB, setA, offset, range) {
      const m = this.m;
      this.offset = offset; this.range = range;
      if (m.bar) {
        const size = range[1] - range[0] > 3 * DAY ? DAY : HOUR;
        this.sets = { b: bucketSum(setB, m.key, size), a: setA ? bucketSum(setA, m.key, size) : null };
        this.step = size;
      } else {
        this.sets = { b: setB, a: setA };
        this.step = typicalStep(setB);
      }
      this.render();
    }

    render() {
      const pts = s => s ? s.ts.map((t, i) => [t, s[this.m.key][i]]) : [];
      const upd = { xAxis: { min: this.range[0], max: this.range[1] }, series: [{ data: pts(this.sets.b) }, { data: pts(this.sets.a) }] };
      if (this.m.axis) upd.yAxis = this.m.axis(this.sets);
      this.chart.setOption(upd);
    }

    show(on) { this.el.classList.toggle("hidden", !on); if (on) this.chart.resize(); }
  }

  function linkCharts(charts) {
    echarts.connect(charts.map(c => c.chart));
    window.addEventListener("resize", () => charts.forEach(c => c.chart.resize()));
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => charts.forEach(c => c.applyTheme()));
  }

  return { HOUR, DAY, css, $, store, toInput, fmtTime, fmtDay, fmtRange, num, signed, dot,
           Periods, toMs, nearest, typicalStep, stats, renderCompareTable, SeriesChart, linkCharts };
})();
