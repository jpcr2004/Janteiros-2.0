// Janteiros 2.0 monitor. Reads our own event stream only (via jt ui); never the arena.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const eur = (x) => (x == null || !isFinite(x) ? "–" : Math.round(x).toLocaleString("en-US").replace(/,/g, " "));
const num = (x, d = 2) => (x == null || !isFinite(x) ? "–" : Number(x).toFixed(d));
const pct = (x, d = 1) => (x == null || !isFinite(x) ? "–" : (100 * x).toFixed(d) + "%");
// timestamps are stored in UTC: show them in this computer's local time
const when = (ts) => {
  const d = ts ? new Date(ts) : null;
  if (!d || isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const COMPONENTS = ["price", "uncovered_need", "fallback_risk", "payment_terms", "freight", "late_delivery", "quality", "tooling", "index_clause", "index_risk", "relationship"];

const S = { runs: [], run: null, events: [], history: [], es: null, offset: 0, cursor: null, playing: false, speed: 4, timer: null };

// ---------------------------------------------------------------- theme
const savedTheme = (() => { try { return localStorage.getItem("jt-theme"); } catch { return null; } })();
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("jt-theme", document.documentElement.dataset.theme); } catch {}
}
$("#theme").onclick = toggleTheme;

// ---------------------------------------------------------------- data
async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(r.status); return r.json(); }

async function openRun(id) {
  if (S.run === id && S.events.length) return;
  S.run = id; S.events = []; S.cursor = null;
  if (S.es) { S.es.close(); S.es = null; }
  const snap = await getJSON(`/api/runs/${encodeURIComponent(id)}/events`);
  S.events = snap.events;
  S.es = new EventSource(`/api/runs/${encodeURIComponent(id)}/stream?offset=${snap.offset}`);
  S.es.onmessage = (m) => { try { S.events.push(JSON.parse(m.data)); schedule(); } catch {} };
  S.es.onopen = () => setLive(true);
  S.es.onerror = () => setLive(false);
}
function setLive(on) { const el = $("#live"); el.textContent = on ? "live" : "offline"; el.className = "pill" + (on ? " on" : ""); }

let pending = false;
function schedule() { if (pending) return; pending = true; setTimeout(() => { pending = false; render(); }, 400); }

// ---------------------------------------------------------------- model
function derive(events, upTo) {
  const cases = new Map();
  const get = (id) => {
    if (!cases.has(id)) cases.set(id, { id, threads: new Map(), decisions: [], alerts: [], events: [], llm: { calls: 0, fallback: 0, seconds: 0 } });
    return cases.get(id);
  };
  const thr = (c, sid) => {
    if (!c.threads.has(sid)) c.threads.set(sid, { sid, mails: [], ours: [], theirs: [], floor: [], status: "open", lastReply: null, stop: null, model: {} });
    return c.threads.get(sid);
  };
  for (const e of events) {
    if (upTo != null && e.t > upTo) break;
    if (!e.case_id) continue;
    const c = get(e.case_id);
    c.events.push(e);
    const ts = Date.parse(e.ts);
    if (e.kind === "case_start") { c.wave = e.wave; c.theme = e.theme; c.deadline = ts + 1000 * (e.seconds_left || 0); c.attempt = e.attempt; if (e.attempt > 1) c.alerts.push(["warn", `retry ${e.attempt}`]); }
    if (e.kind === "case_open") {
      const k = e.case || {}; c.title = k.title; c.type = k.type; c.goal = k.buyer && k.buyer.category_goal; c.session = e.session_id;
      c.suppliers = e.suppliers; c.case = k; c.opened = ts; if (e.resumed) c.alerts.push(["warn", "resumed"]);
    }
    if (e.kind === "quote") { const t = thr(c, e.supplier_id); t.quote = e; t.theirs.push({ t: e.t, c: e.c, label: "quote" }); t.mails.push({ dir: "theirs", e }); t.lastReply = ts; }
    if (e.kind === "plan" && e.round == null) { const t = thr(c, e.supplier_id); t.plan = e; if (e.c_alt != null && e.c_alt !== "inf") c.c_alt = e.c_alt; if (e.floor_c != null) t.floor.push({ t: e.t, c: e.floor_c }); }
    if (e.kind === "plan" && e.round != null) { const t = thr(c, e.supplier_id); t.lastPlan = e; c.decisions.push({ t: e.t, sid: e.supplier_id, kind: "plan", text: e.reason }); }
    if (e.kind === "send") {
      const t = thr(c, e.supplier_id); t.mails.push({ dir: "ours", e }); const cs = (e.c || []).filter((x) => x != null && isFinite(x));
      if (cs.length) t.ours.push({ t: e.t, c: Math.min(...cs), label: `r${e.round}` });
    }
    if (e.kind === "reply") { const t = thr(c, e.supplier_id); t.mails.push({ dir: "theirs", e }); t.status = e.status || t.status; t.lastReply = ts; if (e.c != null) t.theirs.push({ t: e.t, c: e.c, label: e.status }); }
    if (e.kind === "model_update") {
      const t = thr(c, e.supplier_id); Object.assign(t.model, e);
      if (e.floor_c != null && isFinite(e.floor_c)) t.floor.push({ t: e.t, c: e.floor_c });
      if (e.stop) { t.stop = e.stop; c.decisions.push({ t: e.t, sid: e.supplier_id, kind: "stop", text: e.stop }); }
      if (e.pruned) { t.pruned = true; }
    }
    if (e.kind === "accept" || e.kind === "walk") c.decisions.push({ t: e.t, sid: e.supplier_id, kind: e.kind, text: e.reason });
    if (e.kind === "timeout") c.alerts.push(["bad", `timeout ${e.supplier_id || ""}`]);
    if (e.kind === "validate" && !e.ok) c.alerts.push(["warn", "validator block"]);
    if (e.kind === "error") c.alerts.push(["bad", `error: ${e.where}`]);
    if (e.kind === "clock" && e.what === "panic_close") c.alerts.push(["bad", "panic close"]);
    if (e.kind === "llm_call") { c.llm.calls++; c.llm.seconds += e.seconds || 0; if (e.fallback) c.llm.fallback++; }
    if (e.kind === "result") { c.result = e.result || {}; c.reasons = e.reasons; c.ended = ts; }
  }
  for (const c of cases.values()) {
    const quotes = [...c.threads.values()].map((t) => t.quote && t.quote.c).filter((x) => x != null && isFinite(x));
    c.c_open = (c.result && c.result.c_open) || (quotes.length ? Math.min(...quotes) : null);
    c.c_best = c.result && c.result.c_best;
    if (c.result && c.result.c_alt != null) c.c_alt = c.result.c_alt;
    const stand = [...c.threads.values()].map((t) => (t.theirs.length ? t.theirs[t.theirs.length - 1].c : null)).filter((x) => x != null && isFinite(x));
    c.bestStanding = stand.length ? Math.min(...stand) : null;
    c.rounds = [...c.threads.values()].reduce((n, t) => n + t.mails.filter((m) => m.dir === "ours").length, 0);
    const seen = new Set(); c.alerts = c.alerts.filter(([k, v]) => (seen.has(v) ? false : seen.add(v)));
    if (c.llm.fallback) c.alerts.push(["warn", `LLM fallback ×${c.llm.fallback}`]);
  }
  return cases;
}

// ---------------------------------------------------------------- views
function nav(which) { document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.nav === which)); }

function clockLeft(lastReply, now) { if (!lastReply) return null; return 60 - (now - lastReply) / 1000; }

const KIND = {
  real: ["REAL ARENA", "on", "Jogado na arena.tacto.ai contra os fornecedores reais. Estes scores são os que contam."],
  rehearsal: ["ENSAIO DA FINAL", "warn", "Ensaio da Live Final num arena-sim local (fornecedor roteirizado): testa as ondas e os relógios, não a tática."],
  local: ["ARENA-SIM LOCAL", "", "Jogado num arena-sim local (um fornecedor roteirizado): testa a mecânica, não a tática."],
  sim: ["SIMULADOR", "", "Avaliação offline: arena-sim S01-S03 e sim2 (nosso simulador calibrado em sessões reais). Serve para comparar versões, não é score real."],
  other: ["OUTRO", "", "Rodada de debug ou de stress."],
};
const badge = (k) => `<span class="pill ${KIND[k] ? KIND[k][1] : ""}">${KIND[k] ? KIND[k][0] : esc(k)}</span>`;

const HELP = `<details class="card help"><summary><b>Como ler</b> (o que cada número significa)</summary>
<ul>
<li><b>Score</b> (0 a 1): a nota da arena para o caso. 1 = fechamos o melhor acordo possível; 0 = não melhoramos nada sobre a cotação inicial (ou quebramos um must-have). A nota de uma rodada é a média dos casos.</li>
<li><b>C</b> = custo total do acordo para a Hollerau em EUR, no horizonte do caso (preço × volume, mais juros do prazo de pagamento, frete, atrasos, qualidade, cláusula de índice, necessidade não coberta e, nas metas <i>stabilise/secure_supply</i>, uma penalidade se apertamos a margem do fornecedor demais). Menor é melhor.</li>
<li><b>C abertura</b> (C_open): o custo da cotação inicial mais barata. É o ponto zero da nota.</li>
<li><b>C melhor</b> (C_best): o melhor acordo que algum fornecedor aceitaria (ou a alternativa, se for melhor). É a nota 1.</li>
<li><b>Score = (C abertura − C acordo) / (C abertura − C melhor)</b>, ou seja, quanto do ganho possível capturamos.</li>
<li><b>Economia</b>: C abertura − C acordo, em EUR. <b>Na mesa</b>: C acordo − C melhor, o que ainda dava para ganhar.</li>
<li><b>Status do fornecedor</b>: <i>open</i> negociando · <i>agreed</i> aceitou a nossa proposta · <i>final_offer</i> última oferta · <i>walked_away</i> desistiu · <i>pruned</i> paramos de negociar com ele (outro era melhor).</li>
<li><b>Leaderboard da arena</b>: mostra o <i>melhor</i> score de cada caso entre todas as sessões do time, por isso costuma ser maior que a média de uma rodada.</li>
</ul></details>`;

function caseRows(cases) {
  return [...cases].sort((a, b) => a.id.localeCompare(b.id, "en", { numeric: true }));
}

function priceInfo(c) {
  const r = c.result || {};
  const sup = r.supplier_id;
  const t = sup ? c.threads.get(sup) : [...c.threads.values()][0];
  const quote = t && t.quote && t.quote.terms ? t.quote.terms.unit_price : null;
  const deal = r.terms ? r.terms.unit_price : null;
  return { quote, deal, delta: quote && deal ? deal / quote - 1 : null };
}

function boardView() {
  nav(S.runs.find((r) => r.run_id === S.run && r.kind === "real") ? "arena" : "all");
  const cases = caseRows(derive(S.events).values());
  const run = S.runs.find((r) => r.run_id === S.run) || {};
  const now = Date.now();
  const ended = cases.filter((c) => c.result);
  const live = cases.filter((c) => !c.result);
  const mean = ended.length ? ended.reduce((a, c) => a + (c.result.score || 0), 0) / ended.length : null;
  const saved = ended.reduce((a, c) => a + (c.c_open != null && c.result.c_deal != null ? c.c_open - c.result.c_deal : 0), 0);
  let html = `<div class="sub"><a href="#/">← arena</a> · <a href="#/all">todas as rodadas</a></div>
    <h1>${badge(run.kind)} ${esc(run.set || run.mode || "")} <span class="sub">${esc(S.run)}</span></h1>
    <div class="sub">${esc(KIND[run.kind] ? KIND[run.kind][2] : "")}</div>
    <div class="kpis">
      <div class="card"><div class="k">score médio</div><div class="score-big">${num(mean, 3)}</div><div class="sub">${ended.length} de ${cases.length} casos terminados</div></div>
      <div class="card"><div class="k">acordos</div><div class="score-big">${ended.filter((c) => c.result.deal).length}</div><div class="sub">sem acordo: ${ended.filter((c) => !c.result.deal).length}</div></div>
      <div class="card"><div class="k">economia total vs cotações</div><div class="score-big">${eur(saved)}</div><div class="sub">EUR no horizonte dos casos</div></div>
      <div class="card"><div class="k">commit</div><div class="score-big" style="font-size:20px">${esc(run.commit)}</div><div class="sub">${esc(when(run.started))}</div></div>
    </div>${HELP}`;
  if (live.length) {
    html += `<h2>Em andamento</h2><div class="grid">${live.map((c) => caseCard(c, now)).join("")}</div>`;
  }
  if (ended.length) {
    html += `<h2>Casos (${ended.length})</h2><table class="cases"><tr><th>caso</th><th>tipo · meta</th><th>score</th><th>resultado</th>
      <th class="num">preço cotação → acordo</th><th class="num">economia EUR</th><th class="num">na mesa EUR</th><th class="num">msgs</th><th>alertas</th></tr>`;
    for (const c of ended) {
      const r = c.result, p = priceInfo(c);
      const left = r.c_deal != null && c.c_best != null ? r.c_deal - c.c_best : null;
      const sv = c.c_open != null && r.c_deal != null ? c.c_open - r.c_deal : null;
      const sc = r.score || 0;
      html += `<tr class="click" data-href="#/run/${encodeURIComponent(S.run)}/case/${encodeURIComponent(c.id)}">
        <td><b>${esc(c.id.replace("case:", ""))}</b><div class="sub">${esc((c.title || "").slice(0, 60))}</div></td>
        <td>${esc(c.type || "")}<div class="sub">${esc(c.goal || "")} · ${c.threads.size} forn.</div></td>
        <td><div class="scorecell"><b>${num(sc, 3)}</b><span class="bar"><i style="width:${Math.max(0, Math.min(100, 100 * sc))}%;background:${sc >= 0.8 ? "var(--good)" : sc >= 0.6 ? "var(--warn)" : "var(--bad)"}"></i></span></div></td>
        <td>${r.deal ? `<span class="tag deal">acordo</span><div class="sub">${esc(r.supplier_id)}</div>` : `<span class="tag nodeal">sem acordo</span><div class="sub">${esc(r.end_reason || "")}</div>`}</td>
        <td class="num">${num(p.quote)} → ${num(p.deal)}<div class="sub">${p.delta == null ? "" : (p.delta > 0 ? "+" : "") + pct(p.delta)}</div></td>
        <td class="num">${eur(sv)}</td><td class="num">${eur(left)}</td><td class="num">${c.rounds}</td>
        <td>${c.alerts.map(([k, v]) => `<span class="pill ${k}">${esc(v)}</span>`).join(" ")}</td></tr>`;
    }
    html += `</table><div class="sub" style="margin-top:6px">Clique num caso para ver a negociação mensagem a mensagem.</div>`;
  }
  if (!cases.length) html += `<div class="empty">Nenhum caso ainda nesta rodada. Aguardando…</div>`;
  return html;
}

function caseCard(c, now) {
  const p = priceInfo(c);
  let thr = "";
  for (const t of c.threads.values()) {
    const left = clockLeft(t.lastReply, now);
    const last = t.theirs.length ? t.theirs[t.theirs.length - 1] : null;
    thr += `<div class="thr"><span>${esc(t.sid)} ${t.pruned ? '<span class="tag">pruned</span>' : ""}</span><span class="tag ${esc(t.status)}">${esc(t.status)}</span>
      <span class="clock ${left != null && left < 15 ? "low" : ""}" title="segundos até o relógio de 60 s deste fornecedor estourar">${left != null && !t.stop ? Math.max(0, left).toFixed(0) + "s" : ""}</span></div>`;
    void last;
  }
  const dl = c.deadline ? Math.max(0, (c.deadline - now) / 1000) : null;
  return `<a class="card link" href="#/run/${encodeURIComponent(S.run)}/case/${encodeURIComponent(c.id)}">
    <div class="head"><span class="title">${esc(c.id.replace("case:", ""))} · ${esc(c.title || "")}</span><span class="tag open">ao vivo</span></div>
    <div class="sub">${esc(c.type || "")} · ${esc(c.goal || "")} · ${c.rounds} msgs enviadas${dl != null ? ` · onda ${esc(c.wave)}: ${Math.floor(dl / 60)}:${String(Math.floor(dl % 60)).padStart(2, "0")} restantes` : ""}</div>
    <div class="kv"><div><div class="k">cotação</div><div class="v">${num(p.quote)}</div></div>
      <div><div class="k">melhor oferta agora (C)</div><div class="v">${eur(c.bestStanding)}</div></div>
      <div><div class="k">C abertura</div><div class="v">${eur(c.c_open)}</div></div></div>
    <div class="threads">${thr}</div>
    <div class="alerts">${c.alerts.map(([k, v]) => `<span class="pill ${k}">${esc(v)}</span>`).join("")}</div></a>`;
}

function chart(t, c, w = 560, h = 210) {
  const pts = [...t.ours, ...t.theirs, ...t.floor].map((p) => p.c).filter((x) => x != null && isFinite(x));
  const lines = [["C open", c.c_open, "var(--open)"], ["C alt", c.c_alt, "var(--alt)"], ["C best", c.c_best, "var(--best)"]].filter((l) => l[1] != null && isFinite(l[1]));
  const all = pts.concat(lines.map((l) => l[1]));
  if (!all.length) return "";
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || hi * 0.01 || 1; lo -= pad; hi += pad;
  const ts = [...t.ours, ...t.theirs].map((p) => p.t);
  const t0 = Math.min(...ts), t1 = Math.max(...ts, t0 + 1);
  const L = 70, R = 10, T = 10, B = 22;
  const X = (x) => L + ((x - t0) / (t1 - t0)) * (w - L - R);
  const Y = (y) => T + (1 - (y - lo) / (hi - lo)) * (h - T - B);
  const path = (arr, color, dash = "") => arr.length ? `<polyline fill="none" stroke="${color}" stroke-width="2" ${dash ? `stroke-dasharray="${dash}"` : ""} points="${arr.map((p) => `${X(p.t).toFixed(1)},${Y(p.c).toFixed(1)}`).join(" ")}"/>` +
    arr.map((p) => `<circle cx="${X(p.t).toFixed(1)}" cy="${Y(p.c).toFixed(1)}" r="3" fill="${color}"><title>${esc(p.label || "")} ${eur(p.c)}</title></circle>`).join("") : "";
  let g = `<g class="grid">`;
  for (let i = 0; i <= 4; i++) { const v = lo + ((hi - lo) * i) / 4; g += `<line x1="${L}" x2="${w - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 3}" text-anchor="end">${eur(v)}</text>`; }
  g += `</g>`;
  const hl = lines.map(([n, v, col]) => `<line x1="${L}" x2="${w - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${col}" stroke-dasharray="5 4" stroke-width="1.3"/><text x="${w - R - 2}" y="${Y(v) - 4}" text-anchor="end" style="fill:${col}">${n}</text>`).join("");
  const floor = t.floor.filter((p) => p.t >= t0 && isFinite(p.c));
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="total cost C over time">${g}${hl}${path(floor, "var(--muted)", "2 3")}${path(t.theirs, "var(--theirs)")}${path(t.ours, "var(--ours)")}
    <text x="${L}" y="${h - 5}">${num(t0, 0)} s</text><text x="${w - R}" y="${h - 5}" text-anchor="end">${num(t1, 0)} s</text></svg>
    <div class="legend"><span><i style="background:var(--ours)"></i>our sheets (min C)</span><span><i style="background:var(--theirs)"></i>their standing C</span><span><i style="background:var(--muted)"></i>floor estimate</span></div>`;
}

function sheetText(terms) { return Object.entries(terms || {}).map(([k, v]) => `${k}=${v && typeof v === "object" ? `${v.series} ${Math.round(100 * v.material_share)}% db ${v.deadband_pct}` : v}`).join("  "); }

function mailHTML(m) {
  const e = m.e;
  if (m.dir === "ours") {
    return `<div class="mail ours"><div class="meta"><b>Us</b> r${e.round} · ${num(e.t, 1)} s ${e.keepalive ? '<span class="tag">keepalive</span>' : ""} · clock age ${num(e.age, 0)} s</div>
      ${(e.sheets || []).map((s, i) => `<div class="sheet">${sheetText(s)} → C ${eur((e.c || [])[i])}</div>`).join("")}
      ${(e.claims || []).length ? `<div class="claims">${e.claims.map(esc).join("<br>")}</div>` : ""}
      <div class="why">↳ ${esc(e.reason)}</div>
      <details><summary class="sub">e-mail</summary><pre>${esc(e.body)}</pre></details></div>`;
  }
  const res = (e.claim_results || []).map((r) => `<span class="${esc(r.result)}">#${r.index} ${esc(r.result)}</span>`).join(" · ");
  return `<div class="mail theirs"><div class="meta"><b>${esc(e.supplier_id)}</b> ${e.kind === "quote" ? "quote" : `r${e.round ?? ""}`} · ${num(e.t, 1)} s
      <span class="tag ${esc(e.status)}">${esc(e.status)}</span>${e.seconds ? ` · replied in ${num(e.seconds, 1)} s` : ""}</div>
    <div class="sheet">${sheetText(e.terms)} → C ${eur(e.c)}</div>
    ${(e.missing_fields || []).length ? `<div class="sub">missing: ${esc(e.missing_fields.join(", "))} (close on its standard terms)</div>` : ""}
    ${res ? `<div class="claims">${res}</div>` : ""}
    <details><summary class="sub">e-mail</summary><pre>${esc(e.body)}</pre></details></div>`;
}

function waterfall(b, w = 560, h = 220) {
  if (!b) return "";
  const items = COMPONENTS.map((k) => [k, b[k] || 0]).filter(([, v]) => Math.abs(v) > 0.005);
  const total = items.reduce((a, [, v]) => a + v, 0);
  let run = 0; const bars = [];
  for (const [k, v] of items) { bars.push([k, run, run + v, v]); run += v; }
  bars.push(["C", 0, total, total]);
  const max = Math.max(...bars.map((x) => Math.max(x[1], x[2]))), min = Math.min(0, ...bars.map((x) => Math.min(x[1], x[2])));
  const L = 110, R = 80, rowH = (h - 10) / bars.length;
  const X = (x) => L + ((x - min) / (max - min || 1)) * (w - L - R);
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="cost breakdown">${bars.map(([k, a, z, v], i) => {
    const x0 = X(Math.min(a, z)), x1 = X(Math.max(a, z)), y = 5 + i * rowH;
    const col = k === "C" ? "var(--accent)" : v < 0 ? "var(--good)" : "var(--ours)";
    return `<text x="${L - 6}" y="${y + rowH * 0.65}" text-anchor="end">${k}</text><rect x="${x0}" y="${y + 2}" width="${Math.max(1, x1 - x0)}" height="${rowH - 5}" rx="2" fill="${col}"/><text x="${x1 + 4}" y="${y + rowH * 0.65}">${eur(v)}</text>`;
  }).join("")}</svg>`;
}

function caseView(caseId) {
  nav(S.runs.find((r) => r.run_id === S.run && r.kind === "real") ? "arena" : "all");
  const evs = S.events;
  const caseEvents = evs.filter((e) => e.case_id === caseId);
  const tmin = caseEvents.length ? caseEvents[0].t : 0, tmax = caseEvents.length ? caseEvents[caseEvents.length - 1].t : 0;
  const upTo = S.cursor == null ? null : S.cursor;
  const c = derive(evs, upTo).get(caseId);
  if (!c) return `<div class="empty">No events for ${esc(caseId)} yet.</div>`;
  const r = c.result;
  let html = `<div class="sub"><a href="#/run/${encodeURIComponent(S.run)}">← board</a></div>
    <h1>${esc(caseId)} · ${esc(c.title || "")}</h1>
    <div class="sub">${esc(c.type)} · ${esc(c.goal)} · session ${esc(c.session)} · ${c.llm.calls} LLM calls (${c.llm.fallback} fallbacks, ${num(c.llm.seconds, 1)} s)</div>
    <div class="case-top">
      <div class="card"><div class="kv">
        <div><div class="k">score</div><div class="score-big">${r ? num(r.score, 3) : "–"}</div></div>
        <div><div class="k">C open</div><div class="v">${eur(c.c_open)}</div></div>
        <div><div class="k">C alt</div><div class="v">${eur(c.c_alt)}</div></div>
        <div><div class="k">C best</div><div class="v">${eur(c.c_best)}</div></div>
        <div><div class="k">C deal</div><div class="v">${eur(r && r.c_deal)}</div></div>
        <div><div class="k">captured</div><div class="v">${r && c.c_open && c.c_best != null && c.c_open > c.c_best ? pct((c.c_open - (r.c_deal ?? c.c_open)) / (c.c_open - c.c_best)) : "–"}</div></div>
      </div>${r ? `<div class="sub" style="margin-top:6px">${r.deal ? `deal with ${esc(r.supplier_id)} · ${esc(sheetText(r.terms))}` : `no deal (${esc(r.end_reason)})`}</div>` : ""}
      <div class="alerts">${c.alerts.map(([k, v]) => `<span class="pill ${k}">${esc(v)}</span>`).join("")}</div></div>
      ${r && r.cost_breakdown ? `<div class="card"><b>Cost breakdown of the deal</b>${waterfall(r.cost_breakdown)}</div>` : ""}
    </div>
    <h2>Decisions</h2><div class="card"><ol class="reasons">${c.decisions.slice(-40).map((d) => `<li><span class="sub">${num(d.t, 1)} s · ${esc(d.sid)} · ${d.kind}</span> — ${esc(d.text)}</li>`).join("")}</ol></div>
    <div class="thread-cols">`;
  for (const t of c.threads.values()) {
    const m = t.model;
    html += `<div class="card"><div class="head"><span class="title">${esc(t.sid)}</span><span class="tag ${esc(t.status)}">${esc(t.status)}</span></div>
      <div class="sub">${t.plan ? `${esc(t.plan.owner || "?")} · ${esc((t.plan.traits || []).join(", "))} · rate ${pct(t.plan.rate)} · patience ${num(t.plan.patience)}` : ""}</div>
      <div class="kv"><div><div class="k">ratio</div><div class="v">${num(m.ratio)}</div></div><div><div class="k">patience used</div><div class="v">${num(m.cum_gap)}</div></div>
        <div><div class="k">floor C</div><div class="v">${eur(m.floor_c)}</div></div><div><div class="k">stop</div><div class="v">${esc(t.stop || "–")}</div></div></div>
      ${chart(t, c)}
      ${t.mails.map(mailHTML).join("")}</div>`;
  }
  html += `</div>`;
  if (c.reasons && c.reasons.length) {
    html += `<h2>The supplier's reasons (trace)</h2><div class="card"><ul class="reasons">${c.reasons.map((x) => `<li><b>${esc(x.actor)}</b> r${x.round} <span class="tag ${esc(x.status)}">${esc(x.status)}</span> ${esc((x.reasons || []).join(" "))}</li>`).join("")}</ul></div>`;
  }
  html += `<div class="replay"><button id="rp-play" class="primary">${S.playing ? "❚❚" : "▶"}</button><button id="rp-back">◀</button><button id="rp-fwd">▶|</button>
    <input id="rp" type="range" min="${tmin}" max="${tmax}" step="0.1" value="${S.cursor ?? tmax}"><button id="rp-speed">${S.speed}×</button>
    <button id="rp-live">live</button><span class="t">${num(S.cursor ?? tmax, 1)} / ${num(tmax, 1)} s</span></div>
    <div class="sub">keys: space play · ← → step · + − speed · l live · t theme</div>`;
  setTimeout(() => bindReplay(caseEvents, tmin, tmax), 0);
  return html;
}

function bindReplay(evs, tmin, tmax) {
  const rp = $("#rp"); if (!rp) return;
  const times = [...new Set(evs.map((e) => e.t))].sort((a, b) => a - b);
  rp.oninput = () => { S.cursor = Number(rp.value); render(); };
  $("#rp-play").onclick = () => play(times, tmax);
  $("#rp-back").onclick = () => step(times, -1);
  $("#rp-fwd").onclick = () => step(times, 1);
  $("#rp-speed").onclick = () => { S.speed = S.speed >= 32 ? 1 : S.speed * 2; render(); };
  $("#rp-live").onclick = () => { S.cursor = null; S.playing = false; clearTimeout(S.timer); render(); };
  S._times = times; S._tmax = tmax;
}
function step(times, d) {
  const cur = S.cursor ?? times[times.length - 1];
  const i = times.findIndex((x) => x >= cur - 1e-9);
  const j = Math.max(0, Math.min(times.length - 1, (i < 0 ? times.length - 1 : i) + d));
  S.cursor = times[j]; render();
}
function play(times, tmax) {
  S.playing = !S.playing; clearTimeout(S.timer);
  if (S.playing && (S.cursor == null || S.cursor >= tmax)) S.cursor = times[0];
  const tick = () => {
    if (!S.playing) return;
    const i = times.findIndex((x) => x > S.cursor + 1e-9);
    if (i < 0) { S.playing = false; render(); return; }
    const wait = Math.min(3000, ((times[i] - S.cursor) * 1000) / S.speed);
    S.timer = setTimeout(() => { S.cursor = times[i]; render(); tick(); }, wait);
  };
  render(); tick();
}

function scoreColor(sc) { return sc == null ? "transparent" : sc >= 0.8 ? "var(--good)" : sc >= 0.6 ? "var(--warn)" : "var(--bad)"; }

function arenaView() {
  nav("arena");
  const real = S.runs.filter((r) => r.kind === "real").sort((a, b) => (a.started < b.started ? 1 : -1));
  let html = `<h1>Arena real</h1><div class="sub">Só as rodadas jogadas na arena.tacto.ai contra os fornecedores reais. Ensaios e simulador ficam em <a href="#/all">todas as rodadas</a>.</div>${HELP}`;
  if (!real.length) return html + `<div class="empty">Nenhuma rodada na arena ainda. Rode: <code>uv run jt run --set practice --real</code></div>`;
  // best per case over every real run: what the practice leaderboard counts
  const best = {};
  for (const r of real) for (const [c, v] of Object.entries(r.by_case || {})) if (!best[c] || v.score > best[c].score) best[c] = { ...v, run: r.run_id };
  const cases = Object.keys(best).sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  const bestMean = cases.length ? cases.reduce((a, c) => a + best[c].score, 0) / cases.length : null;
  const last = real[0];
  html += `<div class="kpis">
    <div class="card"><div class="k">última rodada</div><div class="score-big">${num(last.mean, 3)}</div><div class="sub">${esc(when(last.started))} · ${last.results} casos · ${esc(last.commit)}</div></div>
    <div class="card"><div class="k">nosso melhor por caso</div><div class="score-big">${num(bestMean, 3)}</div><div class="sub">média do melhor score de cada caso nas rodadas abaixo (como o leaderboard conta, só que só com as nossas rodadas)</div></div>
    <div class="card"><div class="k">rodadas na arena</div><div class="score-big">${real.length}</div><div class="sub">${real.reduce((a, r) => a + r.results, 0)} sessões</div></div>
  </div>`;
  html += `<h2>Histórico das rodadas</h2><table><tr><th>quando</th><th>conjunto</th><th class="num">casos</th><th class="num">acordos</th><th class="num">score médio</th><th class="num">Δ vs anterior</th><th>versão</th></tr>`;
  real.forEach((r, k) => {
    const prev = real[k + 1];
    // compare on the cases both runs played
    let d = null;
    if (prev) {
      const common = Object.keys(r.by_case || {}).filter((c) => prev.by_case && prev.by_case[c]);
      if (common.length) d = common.reduce((a, c) => a + r.by_case[c].score - prev.by_case[c].score, 0) / common.length;
    }
    html += `<tr class="click" data-href="#/run/${encodeURIComponent(r.run_id)}"><td>${esc(when(r.started))}</td><td>${esc(r.set || "")}</td><td class="num">${r.results}</td><td class="num">${r.deals}</td>
      <td class="num"><b>${num(r.mean, 3)}</b></td><td class="num ${d > 0.005 ? "up" : d < -0.005 ? "down" : ""}">${d == null ? "" : (d > 0 ? "+" : "") + num(d, 3)}</td><td>${esc(r.commit)}</td></tr>`;
  });
  html += `</table><div class="sub" style="margin-top:6px">Δ compara só os casos que as duas rodadas jogaram.</div>`;
  const cols = real.slice(0, 8);
  html += `<h2>Score por caso, rodada a rodada</h2><table class="matrix"><tr><th>caso</th>${cols.map((r) => `<th class="num">${esc(when(r.started))}<div class="sub">${esc(r.commit)}</div></th>`).join("")}<th class="num">melhor</th></tr>`;
  for (const c of cases) {
    html += `<tr><td><b>${esc(c.replace("case:", ""))}</b></td>${cols.map((r) => {
      const v = (r.by_case || {})[c];
      return v ? `<td class="num click" data-href="#/run/${encodeURIComponent(r.run_id)}/case/${encodeURIComponent(c)}"><span class="dot" style="background:${scoreColor(v.score)}"></span>${num(v.score, 3)}${v.deal ? "" : " <span class='sub'>s/ acordo</span>"}</td>` : `<td class="num sub">·</td>`;
    }).join("")}<td class="num"><b>${num(best[c].score, 3)}</b></td></tr>`;
  }
  html += `</table><div class="sub" style="margin-top:6px">Clique num score para abrir aquela negociação.</div>`;
  return html;
}

function runsView() {
  nav("all");
  const groups = [["real", "Arena real (os scores que contam)"], ["rehearsal", "Ensaios da final"], ["sim", "Simulador (comparar versões)"], ["local", "Arena-sim local"], ["other", "Outros (debug, stress)"]];
  let html = `<h1>Todas as rodadas</h1><div class="sub">Inclui ensaios e simulador. Para só a arena real, veja <a href="#/">Arena</a>.</div>`;
  for (const [k, title] of groups) {
    const rs = S.runs.filter((r) => r.kind === k);
    if (!rs.length) continue;
    html += `<details ${k === "real" || k === "rehearsal" ? "open" : ""}><summary><h2 style="display:inline">${badge(k)} ${esc(title)} <span class="sub">(${rs.length})</span></h2></summary>
    <table><tr><th>quando</th><th>conjunto</th><th class="num">casos</th><th class="num">acordos</th><th class="num">score médio</th><th>commit</th><th>id</th></tr>
    ${rs.map((r) => `<tr class="click" data-href="#/run/${encodeURIComponent(r.run_id)}"><td>${esc(when(r.started))}</td><td>${esc(r.set || r.mode || "")}</td>
      <td class="num">${r.results}/${r.cases}</td><td class="num">${r.deals}</td><td class="num"><b>${num(r.mean, 3)}</b></td><td>${esc(r.commit)}</td><td class="sub">${esc(r.run_id)}</td></tr>`).join("")}</table></details>`;
  }
  return html;
}

function historyView() {
  nav("history");
  const rows = S.history.slice().reverse();
  const suites = [...new Set(rows.flatMap((r) => Object.keys(r.suites || {})))];
  let html = `<h1>Versions</h1><div class="sub">results/history.jsonl: one line per evaluation run. Δ is against the previous run of the same agent and suite.</div>`;
  for (const s of suites) {
    const rs = rows.filter((r) => r.suites && r.suites[s]);
    html += `<h2>${esc(s)}</h2><table><tr><th>when</th><th>agent</th><th>commit</th><th>model</th><th class="num">n</th><th class="num">mean</th><th class="num">Δ</th><th class="num">deals</th><th class="num">crashes</th><th class="num">timeouts</th><th>note</th></tr>`;
    rs.forEach((r, i) => {
      const prev = rs.slice(i + 1).find((x) => x.agent === r.agent && x.suites[s].n === r.suites[s].n);
      const d = prev ? r.suites[s].mean - prev.suites[s].mean : null;
      html += `<tr class="click" data-href="#/run/${encodeURIComponent(r.run_id)}"><td>${esc(when(r.ts))}</td><td>${esc(r.agent)}</td><td>${esc(r.commit)}${r.dirty ? "*" : ""}</td><td>${esc(r.model || "no LLM")}</td>
        <td class="num">${r.suites[s].n}</td><td class="num"><b>${num(r.suites[s].mean, 4)}</b></td><td class="num ${d > 0 ? "up" : d < 0 ? "down" : ""}">${d == null ? "" : (d > 0 ? "+" : "") + num(d, 4)}</td>
        <td class="num">${r.suites[s].deals}</td><td class="num">${r.suites[s].crashes}</td><td class="num">${r.timeouts ?? ""}</td><td>${esc(r.note)}</td></tr>`;
    });
    html += `</table>`;
    const last = rs.find((r) => r.agent === "jt"), old = rs.find((r) => r.agent === "old" && r.suites[s].n === (last && last.suites[s].n));
    if (last) {
      const oc = Object.fromEntries(((old && old.suites[s].cases) || []).map((c) => [c.case, c.score]));
      html += `<h2>${esc(s)} · latest jt per case ${old ? "vs old agent" : ""}</h2><table><tr><th>case</th><th>type</th><th>goal</th><th class="num">jt</th><th class="num">old</th><th class="num">Δ</th><th class="num">rounds</th></tr>
        ${last.suites[s].cases.map((c) => { const o = oc[c.case]; const d = o == null ? null : c.score - o; return `<tr><td>${esc(c.case)}</td><td>${esc(c.type)}</td><td>${esc(c.goal)}</td><td class="num">${num(c.score, 3)}</td><td class="num">${num(o, 3)}</td><td class="num ${d > 0.005 ? "up" : d < -0.005 ? "down" : ""}">${d == null ? "" : num(d, 3)}</td><td class="num">${c.rounds ?? ""}</td></tr>`; }).join("")}</table>`;
    }
  }
  return html;
}

// ---------------------------------------------------------------- router
async function render() {
  const h = location.hash.replace(/^#\/?/, "");
  const parts = h.split("/").map(decodeURIComponent);
  const app = $("#app");
  try {
    if (parts[0] === "history") { S.history = await getJSON("/api/history"); app.innerHTML = historyView(); }
    else if (parts[0] === "all") {
      S.runs = await getJSON("/api/runs"); app.innerHTML = runsView();
    } else if (parts[0] !== "run") {
      S.runs = await getJSON("/api/runs"); app.innerHTML = arenaView();
    } else {
      if (!S.runs.length) S.runs = await getJSON("/api/runs");
      await openRun(parts[1]);
      app.innerHTML = parts[2] === "case" ? caseView(parts[3]) : boardView();
    }
    app.querySelectorAll("[data-href]").forEach((el) => (el.onclick = () => (location.hash = el.dataset.href)));
  } catch (e) { app.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", () => { S.cursor = null; S.playing = false; render(); });
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" && e.key !== " ") return;
  if (e.key === "t") toggleTheme();
  if (!S._times) return;
  if (e.key === " ") { e.preventDefault(); play(S._times, S._tmax); }
  if (e.key === "ArrowLeft") step(S._times, -1);
  if (e.key === "ArrowRight") step(S._times, 1);
  if (e.key === "+" || e.key === "=") { S.speed = Math.min(64, S.speed * 2); render(); }
  if (e.key === "-") { S.speed = Math.max(0.5, S.speed / 2); render(); }
  if (e.key === "l") { S.cursor = null; S.playing = false; render(); }
});
setInterval(() => {  // the Arena and run lists pick up new runs on their own
  const p = location.hash.replace(/^#\/?/, "").split("/");
  if ((p[0] === "" || p[0] === "all") && !document.querySelector("details[open].help")) render();
}, 15000);
setInterval(() => {
  const p = location.hash.replace(/^#\/?/, "").split("/");
  if (p[0] === "run" && p[2] !== "case" && S.cursor == null && S.events.some((e) => e.kind === "case_open") && !document.querySelector("details[open].help")) render();  // board clocks
}, 1000);
render();
