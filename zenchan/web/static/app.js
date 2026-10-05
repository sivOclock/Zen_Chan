/* Zen-chan dashboard. Vanilla JS, no build step, works offline as a PWA. */
"use strict";

const { el, svg, fmtMin, pct, Charts } = window.Zen;
const $ = (sel) => document.querySelector(sel);

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

const state = {
  window: store.get("zen.window", "7d"),
  tab: store.get("zen.tab", "buddy"),
  insights: {},
  status: null,
  settings: null,
  nudges: [],
  chat: [],
  map: { dim: "2d", colorBy: "category", points: [], yaw: 0.6, pitch: 0.35, spin: true },
};

const api = {
  async get(path) {
    const r = await fetch(path, { credentials: "same-origin" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  async post(path, body, raw = false) {
    const opts = { method: "POST", credentials: "same-origin", headers: { "X-Zen": "1" } };
    if (raw) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body ?? {}); }
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
};

function panel(title, opts = {}, ...kids) {
  return el("article", { class: `panel ${opts.span || "span-6"}`, id: opts.id },
    el("h2", {}, el("span", { text: title }), opts.note ? el("small", { text: opts.note }) : null), ...kids);
}
const empty = (text) => el("div", { class: "empty", text });
const grid = (...kids) => el("div", { class: "grid" }, ...kids);

/* ------------------------------------------------------------------ boot */

async function boot() {
  $("#window").value = state.window;
  $("#window").addEventListener("change", (e) => { state.window = e.target.value; store.set("zen.window", state.window); loadInsights(); });
  $("#refresh").addEventListener("click", () => startRefresh());
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  showTab(state.tab, false);
  connectStream();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  await Promise.allSettled([loadStatus(), loadSettings(), loadNudges(), loadNow()]);
  await loadInsights();
  if (state.status && state.status.visits === 0 && !state.status.refreshing) startRefresh();
  setInterval(loadNow, 60_000);
}

function showTab(name, render = true) {
  state.tab = name;
  store.set("zen.tab", name);
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (render) renderTab(name);
  window.scrollTo({ top: 0 });
}

async function loadStatus() { state.status = await api.get("/api/status"); }
async function loadSettings() { state.settings = await api.get("/api/settings"); }
async function loadNudges() { state.nudges = await api.get("/api/nudges"); markNudgeDot(); }

async function loadNow() {
  try { renderNow(await api.get("/api/now")); } catch { /* offline */ }
}

async function loadInsights() {
  try {
    state.insights[state.window] = await api.get(`/api/insights?window=${state.window}`);
  } catch (e) {
    state.insights[state.window] = { error: e.message };
  }
  renderTab(state.tab);
}

function renderTab(name) {
  const ins = state.insights[state.window] || {};
  const target = $(`#tab-${name}`);
  const render = { buddy: renderBuddy, identity: renderIdentity, patterns: renderPatterns, map: renderMap,
                   sessions: renderSessions, sources: renderSources, settings: renderSettings }[name];
  target.replaceChildren();
  try {
    target.append(render(ins));
  } catch (e) {
    console.error(e);
    target.append(panel("Something broke", { span: "span-12" }, empty(String(e))));
  }
}

function noData(ins) {
  if (ins.error) return grid(panel("Can't reach Zen-chan", { span: "span-12" }, empty(ins.error)));
  const busy = state.status && state.status.refreshing;
  return grid(panel("Hello! (・o・)?", { span: "span-12" },
    el("p", { text: ins.pending || busy ? "I'm reading your browsers for the first time — give me a moment." :
      "No browsing in this window yet." }),
    el("p", { class: "muted", text: "I read history from every browser profile on this machine (Chrome, Edge, Brave, Firefox, Safari, Opera, Vivaldi, Arc and more), plus phones that sync into them. Nothing leaves your computer." }),
    el("button", { class: "btn", text: "⟳ Sync now", onclick: () => startRefresh() })));
}

/* ------------------------------------------------------------------ live: SSE */

function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch { return; }
    if (ev.type === "progress") onProgress(ev);
    else if (ev.type === "status") renderNow(ev);
    else if (ev.type === "nudge") onNudge(ev);
    else if (ev.type === "analysis" && ev.phase === "done") { loadInsights(); loadStatus(); }
    else if (ev.type === "hello" && ev.refreshing) openModal();
  };
  es.onerror = () => { es.close(); setTimeout(connectStream, 5000); };
}

function renderNow(s) {
  if (!s) return;
  $("#now-face").textContent = s.face || "(￣ー￣)";
  $("#now-line").textContent = s.line || "";
  $("#now").classList.toggle("active", !!s.active);
  $("#now").title = s.title || "";
}

function onNudge(n) {
  state.nudges.unshift({ ...n, seen: 0 });
  markNudgeDot();
  toast(n.title, n.body, n.severity);
  if (document.hidden && "Notification" in window && Notification.permission === "granted") {
    try { new Notification(n.title, { body: n.body, icon: "/static/icons/icon-192.png", tag: n.kind }); } catch { /* ignore */ }
  }
  if (state.tab === "buddy") renderTab("buddy");
}

function markNudgeDot() {
  const btn = document.querySelector('#tabs button[data-tab="buddy"]');
  btn.querySelector(".dot")?.remove();
  if (state.nudges.some((n) => !n.seen)) btn.append(el("span", { class: "dot", title: "new nudges" }));
}

function toast(title, body, severity = "warn") {
  const t = el("div", { class: `toast ${severity}`, role: "status" }, el("b", { text: title }), el("span", { text: body }));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 9000);
}

/* ------------------------------------------------------------------ refresh modal */

let progressSteps = 0;
function openModal() {
  progressSteps = 0;
  $("#terminal").textContent = "";
  $("#progress-bar").style.width = "4%";
  $("#modal").hidden = false;
}

async function startRefresh(full = false) {
  openModal();
  $("#refresh").disabled = true;
  try {
    const r = await api.post("/api/refresh", { full });
    if (!r.started) onProgress({ line: "> already running…" });
  } catch (e) {
    onProgress({ line: `> ${e.message}`, done: true, error: true });
  }
}

function onProgress(ev) {
  if ($("#modal").hidden) openModal();
  const term = $("#terminal");
  term.textContent += `${ev.line}\n`;
  term.scrollTop = term.scrollHeight;
  progressSteps += 1;
  $("#progress-bar").style.width = `${Math.min(95, 4 + progressSteps * 7)}%`;
  if (ev.done) {
    $("#progress-bar").style.width = "100%";
    $("#refresh").disabled = false;
    setTimeout(async () => {
      if (!ev.error) $("#modal").hidden = true;
      await loadStatus();
      state.insights = {};
      await loadInsights();
    }, ev.error ? 4000 : 900);
    if (ev.error) setTimeout(() => { $("#modal").hidden = true; }, 6000);
  }
}

/* ------------------------------------------------------------------ BUDDY */

function renderBuddy(ins) {
  if (!ins.totals) return noData(ins);
  const b = ins.buddy || {};
  const p = ins.patterns, t = ins.totals;
  const focus = (ins.states.find((s) => s.state === "Deep Focus") || {}).minutes || 0;
  const backend = state.settings?.narrator?.backend || "local";

  const llmBox = el("div");
  const buddy = el("div", { class: "buddy" },
    el("div", { class: "big-face" }, b.face || "(￣ー￣)", el("span", { class: "mood-tag", text: b.mood || "" })),
    el("div", {},
      el("div", { class: "greeting", text: b.greeting || "" }),
      el("div", { class: "headline", text: b.headline || "" }),
      el("ul", {}, (b.observations || []).map((o) => el("li", { text: o }))),
      b.suggestion ? el("div", { class: "suggestion", text: b.suggestion }) : null,
      backend !== "local" ? el("button", { class: "btn small ghost", style: { marginTop: "10px" }, text: `Ask ${backend} for a deeper read`,
        onclick: async (e) => {
          e.target.disabled = true; llmBox.replaceChildren(el("div", { class: "llm", text: "thinking…" }));
          try {
            const r = await api.get(`/api/buddy/narrate?window=${state.window}`);
            llmBox.replaceChildren(el("div", { class: "llm", text: r.text || r.note || "(no answer)" }));
          } catch (err) { llmBox.replaceChildren(el("div", { class: "llm", text: err.message })); }
          e.target.disabled = false;
        } }) : null,
      llmBox));

  const score = ins.score;
  const scorePanel = panel("Intentionality", { span: "span-4", note: score ? score.label : "" },
    el("div", { class: "score-ring" }, Charts.ring(score ? score.value : null),
      el("ul", { class: "components" }, (score?.components || []).map((c) =>
        el("li", {}, el("span", { text: c.name }), el("span", { class: c.points >= 0 ? "green" : "red", text: `${c.points > 0 ? "+" : ""}${c.points}` }))))),
    el("p", { class: "muted small", text: "Starts at 50. Time on what you call productive and deep focus add; doomscrolling, past-bedtime browsing, fragmentation and blown limits subtract." }));

  const kpi = (v, k, cls = "") => el("div", { class: "kpi" }, el("div", { class: `v ${cls}`, text: v }), el("div", { class: "k", text: k }));
  const kpis = el("div", { class: "kpis" },
    kpi(fmtMin(t.minutes), "online"),
    kpi(fmtMin(t.per_day), "per day"),
    kpi(fmtMin(focus), "deep focus"),
    kpi(fmtMin(p.doom_minutes), "doomscrolling", p.doom_minutes > 30 ? "bad" : p.doom_minutes > 10 ? "warn" : ""),
    kpi(fmtMin(p.late.minutes), "past bedtime", p.late.minutes > 30 ? "bad" : p.late.minutes > 0 ? "warn" : ""),
    kpi(String(Math.round(p.switching.per_hour)), "switches / hr", p.switching.per_hour > 45 ? "warn" : ""),
    kpi(String(t.sites), "sites"),
    kpi(String(ins.devices.length), ins.devices.length === 1 ? "device" : "devices"));

  return grid(
    panel("Zen-chan says", { span: "span-8" }, buddy),
    scorePanel,
    panel("At a glance", { span: "span-12" }, kpis),
    nudgePanel(),
    chatPanel(),
    panel("Where your time went", { span: "span-6", note: `${ins.categories.length} categories` },
      Charts.bars(ins.categories.slice(0, 12).map((c) => ({ label: c.name, value: c.minutes, color: c.color, note: `${fmtMin(c.minutes)} · ${pct(c.share)}` })))),
    panel("Top sites", { span: "span-6" },
      Charts.bars(ins.domains.slice(0, 12).map((d) => ({ label: d.domain + (d.feed ? " ∞" : ""), value: d.minutes,
        color: d.feed ? "var(--red)" : "var(--green)", title: `${d.domain} — ${d.category}${d.feed ? " (infinite feed)" : ""}` }))),
      el("div", { class: "legend" }, el("span", {}, el("i", { style: { background: "var(--red)" } }), "∞ infinite feed"))),
    panel("Devices", { span: "span-12", note: "phones appear when they sync into a desktop browser, or via the extension" },
      el("div", { class: "kpis" }, ins.devices.map((d) => el("div", { class: "kpi" },
        el("div", { class: "v", text: `${deviceIcon(d.kind)} ${fmtMin(d.minutes)}` }),
        el("div", { class: "k", text: `${d.label} · ${d.via.join(", ")}` }))))),
  );
}

function deviceIcon(kind) { return { computer: "▣", phone: "▯", tablet: "▭" }[kind] || "◇"; }

function nudgePanel() {
  const list = el("ul", { class: "list" });
  if (!state.nudges.length) list.append(el("li", { class: "empty", text: "No nudges yet. I'll speak up when something's worth saying." }));
  state.nudges.slice(0, 8).forEach((n) => list.append(el("li", { class: `nudge ${n.severity} ${n.seen ? "" : "unseen"}` },
    el("div", {}, el("div", { class: "t", text: n.title }), el("div", { text: n.body }),
      el("div", { class: "meta", text: new Date(n.ts * 1000).toLocaleString() })))));
  return panel("Watcher feed", { span: "span-6", note: state.status?.watcher ? "watching live" : "watcher off (run `zenchan serve`)" },
    list,
    state.nudges.some((n) => !n.seen) ? el("button", { class: "btn small ghost", text: "Mark all seen", onclick: async () => {
      await api.post("/api/nudges/seen"); state.nudges.forEach((n) => { n.seen = 1; }); markNudgeDot(); renderTab("buddy");
    } }) : null);
}

function chatPanel() {
  const log = el("div", { class: "chat-log", id: "chat-log" });
  const add = (who, text) => { log.append(el("div", { class: `msg ${who}`, text })); log.scrollTop = log.scrollHeight; };
  state.chat.forEach((m) => add(m.who, m.text));
  if (!state.chat.length) add("zen", "Ask me anything about your browsing.");
  const input = el("input", { type: "text", placeholder: "how much youtube today?", "aria-label": "Ask Zen-chan" });
  const ask = async (q) => {
    if (!q.trim()) return;
    state.chat.push({ who: "me", text: q }); add("me", q); input.value = "";
    try {
      const r = await api.post("/api/buddy/chat", { message: q });
      state.chat.push({ who: "zen", text: r.reply }); add("zen", r.reply);
    } catch (e) { add("zen", `(couldn't answer: ${e.message})`); }
  };
  const chips = ["How did I sleep this week?", "Who am I online?", "What could advertisers infer?", "How focused was I today?", "Any tips?"];
  return panel("Talk to Zen-chan", { span: "span-6", note: state.settings?.narrator?.backend || "local" },
    el("div", { class: "suggestions" }, chips.map((c) => el("button", { class: "btn small ghost", text: c, onclick: () => ask(c) }))),
    log,
    el("form", { class: "chat-form", onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, el("button", { class: "btn", text: "Ask" })));
}

/* ------------------------------------------------------------------ IDENTITY */

function renderIdentity(ins) {
  if (!ins.totals) return noData(ins);
  const id = ins.identity, chrono = id.chronotype, ad = id.ad_profile, drift = id.drift;
  const typeChip = { sensitive: "hot", life_event: "warn", commercial: "" };
  return grid(
    panel("Your browsing identity", { span: "span-12" },
      el("div", { class: "headline", style: { fontFamily: "var(--font-display)", fontSize: "34px", color: "var(--green)" }, text: id.headline }),
      el("p", { class: "muted", text: `Chronotype: ${chrono.label} (activity centred around ${String(Math.floor(chrono.center)).padStart(2, "0")}:${String(Math.round((chrono.center % 1) * 60)).padStart(2, "0")}, peak ${chrono.peak_hour}:00) · interest spread ${pct(id.diversity)} · ${pct(id.novelty)} of sites were new in this window.` })),
    panel("Archetypes", { span: "span-6", note: "what and how you browse" },
      Charts.bars(id.archetypes.map((a) => ({ label: a.name, value: a.score, note: pct(a.score), title: `${a.blurb} — ${a.reason}`,
        color: a.kind === "behavior" ? "var(--amber)" : "var(--green)" })), { max: 1 }),
      el("ul", { class: "list small" }, id.archetypes.slice(0, 3).map((a) => el("li", {}, el("b", { text: `${a.name}: ` }), el("span", { class: "muted", text: `${a.blurb} — ${a.reason}` }))))),
    panel("Your 24 hours", { span: "span-6", note: "red = night" },
      el("div", { style: { display: "flex", justifyContent: "center" } }, Charts.clock(chrono.hours, 230))),
    panel("Interests I discovered", { span: "span-6", note: "unsupervised topics from page titles" },
      id.interests.length ? el("ul", { class: "list" }, id.interests.map((i) => el("li", {},
        el("div", {}, el("b", { text: i.label }), el("span", { class: "muted", text: ` · ${fmtMin(i.minutes)} · ${i.category}` })),
        el("div", {}, i.keywords.slice(0, 5).map((k) => el("span", { class: "chip", text: k })))))) : empty("Not enough distinct pages yet.")),
    panel("Identity drift", { span: "span-6", note: "this week vs. the 4 weeks before" },
      drift.similarity == null ? empty("Need a few more weeks of history.") : el("div", {},
        el("p", {}, `This week is `, el("b", { text: pct(drift.similarity) }), " similar to your recent self."),
        drift.risers.length ? el("h3", { text: "Rising" }) : null,
        Charts.bars(drift.risers.map((r) => ({ label: r.category, value: r.delta, note: `+${pct(r.delta, 1)}`, color: "var(--cyan)" })), { max: 0.2 }),
        drift.fallers.length ? el("h3", { text: "Fading" }) : null,
        Charts.bars(drift.fallers.map((r) => ({ label: r.category, value: -r.delta, note: pct(r.delta, 1), color: "var(--dim)" })), { max: 0.2 }))),
    panel("One person, many browsers", { span: "span-12", note: "each profile and device has its own personality" },
      el("div", { class: "kpis" }, id.per_source.map((s) => el("div", { class: "kpi" },
        el("div", { class: "k", text: `${s.kind === "device" ? deviceIcon("") : "◈"} ${s.label}` }),
        el("div", { class: "v", style: { fontSize: "26px" }, text: s.persona }),
        el("div", { class: "k", text: `${fmtMin(s.minutes)} · ${s.top.map((x) => `${x.category.split(" &")[0]} ${pct(x.share)}`).join(" · ")}` }))))),
    panel("What the ad-tech web could infer", { span: "span-7", note: "computed locally — nothing is sent anywhere" },
      ad.segments.length ? el("ul", { class: "list" }, ad.segments.map((s) => el("li", {},
        el("div", {}, el("b", { text: s.segment }), " ", el("span", { class: `chip ${typeChip[s.type] || ""}`, text: s.type.replace("_", " ") }),
          el("span", { class: "muted", text: ` ${pct(s.confidence)} confidence · ${s.signals} signals over ${s.days} days` })),
        el("div", { class: "muted small" }, s.examples.map((x) => el("div", { text: `“${x}”` })))))) : empty("Nothing strongly inferable. Nice."),
      el("p", { class: "muted small", text: "Trackers build profiles like this from the same pages you just visited. Red = categories regulators treat as sensitive." })),
    panel("Who saw you first-hand", { span: "span-5", note: `${ad.distinct_sites} distinct sites` },
      Charts.bars(ad.platforms.map((x) => ({ label: x.company, value: x.share, note: pct(x.share), color: "var(--violet)" })), { max: 1 }),
      el("p", { class: "muted small", text: "Share of your browsing time spent on properties each company owns (not counting third-party trackers, which see even more)." })),
  );
}

/* ------------------------------------------------------------------ PATTERNS */

function renderPatterns(ins) {
  if (!ins.totals) return noData(ins);
  const p = ins.patterns, h = ins.habits;
  const states = Charts.bars(ins.states.map((s) => ({ label: s.state, value: s.minutes, color: s.color, title: s.desc })));
  return grid(
    panel("When you browse", { span: "span-8", note: "minutes by weekday & hour" }, Charts.heatmap(ins.heatmap)),
    panel("States of mind", { span: "span-4", note: "inferred per episode" }, states),
    panel("Day by day", { span: "span-8", note: "green productive · dark other · red past bedtime" }, Charts.columns(ins.daily)),
    panel("Mood of what you read", { span: "span-4", note: ins.mood.label },
      Charts.line(ins.mood.timeline),
      el("p", { class: "muted small", text: "Inferred from your browsing patterns and headline tone — a mirror, not a diagnosis." })),
    panel("Doomscrolling", { span: "span-6", note: `${fmtMin(p.doom_minutes)} total` },
      p.doomscroll.length ? el("ul", { class: "list" }, p.doomscroll.map((e) => el("li", {},
        el("div", {}, el("b", { text: fmtMin(e.minutes) }), ` on ${e.sites.join(", ")}`, e.late ? el("span", { class: "chip hot", text: "past bedtime" }) : null),
        el("div", { class: "meta", text: `${e.when} · ${e.items} items · ~${e.seconds_per_item}s each` })))) : empty("No doomscroll runs. (=^･ω･^=)")),
    panel("Rabbit holes", { span: "span-6" },
      p.rabbit_holes.length ? el("ul", { class: "list" }, p.rabbit_holes.map((r) => el("li", {},
        el("div", {}, el("b", { text: `${r.hops} hops` }), ` on ${r.site} · ${fmtMin(r.minutes)}`),
        el("div", { class: "meta", text: `${r.when}: “${r.from}” → … → “${r.to}”` }),
        el("details", {}, el("summary", { class: "muted small", text: "the path" }), el("ol", { class: "small" }, r.path.map((x) => el("li", { text: x }))))))) : empty("No deep link-chains this time.")),
    panel("Checking loops", { span: "span-4", note: "short repeat visits" },
      p.compulsive.length ? Charts.bars(p.compulsive.map((c) => ({ label: c.site, value: c.per_day, note: `${c.per_day}×/day · ${c.avg_minutes}m`, color: "var(--amber)" }))) : empty("No compulsive checking.")),
    panel("Search spirals", { span: "span-4", note: `${p.worry_searches} worried-sounding searches` },
      p.search_spirals.length ? el("ul", { class: "list" }, p.search_spirals.map((s) => el("li", {},
        el("div", {}, el("b", { text: `${s.count} similar searches` }), s.worry ? el("span", { class: "chip hot", text: "worry" }) : null),
        el("div", { class: "meta", text: `${s.when}: ${s.queries.join(" · ")}` })))) : empty("No search spirals."),
      p.worry_examples.length ? el("div", { class: "muted small", text: `e.g. ${p.worry_examples.slice(0, 3).map((q) => `“${q}”`).join(", ")}` }) : null),
    panel("Habits", { span: "span-4" },
      el("h3", { text: "Gateways" }),
      h.gateways.length ? el("ul", { class: "list small" }, h.gateways.map((g) => el("li", { text: `${pct(g.share)} of ${g.to} starts right after ${g.from}` }))) : empty("—"),
      el("h3", { text: "Daily rituals" }),
      h.rituals.length ? el("ul", { class: "list small" }, h.rituals.map((r) => el("li", { text: `${r.site} — ${pct(r.share)} of mornings, ~${r.typical}` }))) : empty("—"),
      el("h3", { text: "Usually around now" }),
      h.forecast.length ? el("div", {}, h.forecast.map((f) => el("span", { class: "chip", text: `${f.category} ${pct(f.share)}` }))) : empty("—")),
    panel("Unusual days", { span: "span-6", note: "vs. your own 4-week baseline" },
      ins.anomalies.length ? el("ul", { class: "list" }, ins.anomalies.map((a) => el("li", {},
        el("b", { text: new Date(`${a.day}T12:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) }),
        el("div", { class: "meta", text: a.reasons.join(" · ") })))) : empty("Nothing unusual.")),
    panel("Attention", { span: "span-6" },
      el("div", { class: "kpis" },
        el("div", { class: "kpi" }, el("div", { class: "v", text: String(Math.round(p.switching.per_hour)) }), el("div", { class: "k", text: "site switches / active hour" })),
        el("div", { class: "kpi" }, el("div", { class: `v ${p.outrage_share > 0.3 ? "warn" : ""}`, text: pct(p.outrage_share) }), el("div", { class: "k", text: "news & social framed as outrage/fear" })),
        el("div", { class: "kpi" }, el("div", { class: "v", text: String(p.late.nights) }), el("div", { class: "k", text: `late nights${p.late.latest ? ` · latest ${p.late.latest}` : ""}` })))),
    panel("What you searched", { span: "span-12", note: `${ins.totals.searches} searches · stays on this machine` },
      el("div", {}, ins.searches.top_terms.map((t) => el("span", { class: "chip", text: `${t.term} ×${t.n}` }))),
      el("h3", { text: "Recent" }),
      el("div", { class: "muted small" }, ins.searches.recent.map((q) => el("div", { text: q })))),
  );
}

/* ------------------------------------------------------------------ MAP (2D/3D canvas, no deps) */

function renderMap() {
  const canvas = el("canvas", { "aria-label": "semantic map of pages you visited" });
  const tip = el("div", { class: "map-tip" });
  const wrap = el("div", { class: "map-wrap" }, canvas, tip);
  const legend = el("div", { class: "legend" });
  const btn = (label, on, fn) => el("button", { class: `btn small ${on ? "on" : "ghost"}`, text: label, onclick: fn });
  const controls = el("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "10px" } },
    btn("2D", state.map.dim === "2d", () => { state.map.dim = "2d"; renderTab("map"); }),
    btn("3D", state.map.dim === "3d", () => { state.map.dim = "3d"; renderTab("map"); }),
    btn("color: category", state.map.colorBy === "category", () => { state.map.colorBy = "category"; renderTab("map"); }),
    btn("color: topic", state.map.colorBy === "topic", () => { state.map.colorBy = "topic"; renderTab("map"); }));
  const note = el("span", { class: "muted small", text: "loading…" });
  api.get(`/api/map?dim=${state.map.dim}&window=${state.window === "today" ? "7d" : state.window}`).then((r) => {
    state.map.points = r.points;
    note.textContent = `${r.points.length} pages · embedder ${r.embedder || "?"} · ${state.map.dim === "3d" ? "drag to rotate" : "hover for titles"}`;
    drawMap(canvas, tip, legend);
  }).catch((e) => { note.textContent = e.message; });
  return grid(panel("Semantic map of your browsing", { span: "span-12", note: "similar pages sit together; dot size = time" },
    controls, wrap, note, legend));
}

const TOPIC_COLORS = ["#39ff14", "#00e5ff", "#ffb000", "#ff3366", "#da70d6", "#87ceeb", "#adff2f", "#ff7f50", "#9370db", "#20b2aa", "#f0e68c", "#ff69b4"];

function drawMap(canvas, tip, legend) {
  const pts = state.map.points;
  const topics = [...new Set(pts.map((p) => p.topic))];
  const colorOf = (p) => state.map.colorBy === "topic" ? TOPIC_COLORS[topics.indexOf(p.topic) % TOPIC_COLORS.length] : p.color;
  legend.replaceChildren(...(state.map.colorBy === "topic"
    ? topics.slice(0, 16).map((t, i) => el("span", {}, el("i", { style: { background: TOPIC_COLORS[i % TOPIC_COLORS.length] } }), t))
    : [...new Map(pts.map((p) => [p.category, p.color])).entries()].map(([c, col]) => el("span", {}, el("i", { style: { background: col } }), c))));
  if (!pts.length) return;
  const dpr = window.devicePixelRatio || 1;
  const ctx = canvas.getContext("2d");
  const norm = (k) => { const v = pts.map((p) => p[k] ?? 0); const lo = Math.min(...v), hi = Math.max(...v); return (x) => ((x - lo) / (hi - lo || 1)) * 2 - 1; };
  const nx = norm("x"), ny = norm("y"), nz = state.map.dim === "3d" ? norm("z") : () => 0;
  const base = pts.map((p) => ({ p, x: nx(p.x), y: ny(p.y), z: nz(p.z), r: 2 + Math.min(9, Math.sqrt(p.minutes || 0)) }));
  let projected = [];
  let dragging = null;

  function frame() {
    if (!canvas.isConnected) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const s = Math.min(w, h) * 0.42, cy = Math.cos(state.map.yaw), sy = Math.sin(state.map.yaw), cp = Math.cos(state.map.pitch), sp = Math.sin(state.map.pitch);
    projected = base.map((b) => {
      if (state.map.dim === "2d") return { ...b, sx: w / 2 + b.x * s, sy: h / 2 - b.y * s, depth: 0, scale: 1 };
      const x1 = b.x * cy - b.z * sy, z1 = b.x * sy + b.z * cy;
      const y2 = b.y * cp - z1 * sp, z2 = b.y * sp + z1 * cp;
      const persp = 2.6 / (2.6 + z2);
      return { ...b, sx: w / 2 + x1 * s * persp, sy: h / 2 - y2 * s * persp, depth: z2, scale: persp };
    }).sort((a, b) => b.depth - a.depth);
    for (const q of projected) {
      ctx.beginPath();
      ctx.globalAlpha = state.map.dim === "3d" ? 0.35 + 0.6 * Math.min(1, q.scale - 0.4) : 0.8;
      ctx.fillStyle = colorOf(q.p);
      ctx.arc(q.sx, q.sy, q.r * q.scale, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (state.map.dim === "3d" && state.map.spin && !dragging) { state.map.yaw += 0.0025; requestAnimationFrame(frame); }
  }
  canvas.onmousemove = (e) => {
    const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (dragging) {
      state.map.yaw += (mx - dragging.x) * 0.01; state.map.pitch = Math.max(-1.4, Math.min(1.4, state.map.pitch + (my - dragging.y) * 0.01));
      dragging = { x: mx, y: my }; frame(); return;
    }
    let best = null, bd = 144;
    for (const q of projected) { const d = (q.sx - mx) ** 2 + (q.sy - my) ** 2; if (d < bd) { bd = d; best = q; } }
    if (!best) { tip.style.display = "none"; return; }
    tip.replaceChildren(el("b", { text: best.p.title }), el("div", { class: "muted", text: `${best.p.domain} · ${best.p.category} · ${fmtMin(best.p.minutes)}` }),
      best.p.topic ? el("div", { class: "muted", text: `topic: ${best.p.topic}` }) : null);
    tip.style.display = "block";
    tip.style.left = `${Math.min(mx + 14, r.width - 330)}px`; tip.style.top = `${my + 14}px`;
  };
  canvas.onmouseleave = () => { tip.style.display = "none"; dragging = null; };
  canvas.onmousedown = (e) => { const r = canvas.getBoundingClientRect(); dragging = { x: e.clientX - r.left, y: e.clientY - r.top }; state.map.spin = false; };
  canvas.onmouseup = () => { dragging = null; };
  canvas.ontouchmove = (e) => { if (state.map.dim !== "3d") return; e.preventDefault(); const t = e.touches[0]; canvas.onmousemove({ clientX: t.clientX, clientY: t.clientY }); };
  canvas.ontouchstart = (e) => { const t = e.touches[0]; canvas.onmousedown({ clientX: t.clientX, clientY: t.clientY }); };
  canvas.ontouchend = () => { dragging = null; };
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ SESSIONS */

function renderSessions(ins) {
  if (!ins.totals) return noData(ins);
  const model = state.status?.regret_model || ins.regret_model;
  const list = el("ul", { class: "list" });
  ins.sessions.forEach((s) => {
    const actions = el("div", { class: "actions" });
    const setLabel = async (label) => {
      try {
        const r = await api.post(`/api/sessions/${s.id}/label`, { label: s.label === label ? null : label });
        s.label = r.label;
        if (state.status) state.status.regret_model = r.model;
        toast("Thanks!", r.model ? `Your personal model now learns from ${r.model.n} labels.` : `${r.labels} labelled — I need at least 6 (both kinds) to learn your taste.`, "good");
        renderTab("sessions");
      } catch (e) { toast("Couldn't save", e.message); }
    };
    actions.append(
      el("button", { class: `btn small ${s.label === "good" ? "on" : "ghost"}`, text: "worth it", onclick: () => setLabel("good") }),
      el("button", { class: `btn small ${s.label === "regret" ? "on" : "ghost"}`, text: "regret", onclick: () => setLabel("regret") }));
    const color = s.regret > 0.7 ? "var(--red)" : s.regret > 0.4 ? "var(--amber)" : "var(--green)";
    list.append(el("li", { class: "session" },
      el("span", { class: "stripe", style: { background: s.color } }),
      el("div", {},
        el("div", {}, el("b", { text: s.state }), el("span", { class: "muted", text: ` · ${fmtMin(s.minutes)} · ${s.top_domain} · ${s.visits} pages` })),
        el("div", { class: "meta" }, `${s.when} · ${s.device} · mood ${s.mood} · regret risk`,
          el("span", { class: "regret-meter", title: pct(s.regret) }, el("i", { style: { width: pct(s.regret), background: color } })), pct(s.regret))),
      actions));
  });
  return grid(
    panel("Teach Zen-chan your taste", { span: "span-12", note: model ? `personal model · ${model.n} labels${model.accuracy != null ? ` · ~${pct(model.accuracy)} leave-one-out accuracy` : ""}` : "using a generic prior" },
      el("p", { text: "Mark sessions you were glad about and ones you regret. After a handful of each, a small logistic-regression model learns what *your* regrettable sessions look like, and the watcher warns you when a live session starts to match." }),
      model?.drivers ? el("div", {}, el("span", { class: "muted small", text: "Strongest signals: " }),
        model.drivers.map(([f, w]) => el("span", { class: `chip ${w > 0 ? "hot" : "good"}`, text: `${f.replaceAll("_", " ")} ${w > 0 ? "↑" : "↓"}` }))) : null),
    panel("Recent episodes", { span: "span-12", note: `${ins.sessions.length} shown` }, ins.sessions.length ? list : empty("No sessions in this window.")),
  );
}

/* ------------------------------------------------------------------ SOURCES */

function renderSources() {
  const s = state.status;
  if (!s) return empty("loading…");
  const body = grid();
  const sourcesList = el("ul", { class: "list" }, s.sources.length ? s.sources.map((src) => el("li", { class: "source" },
    el("span", { class: `badge ${src.enabled ? src.status : "disabled"}`, text: src.enabled ? src.status : "off" }),
    el("div", {},
      el("div", {}, el("b", { text: `${src.browser}` }), el("span", { class: "muted", text: ` · ${src.name}${src.origin ? ` (${src.origin})` : ""}` })),
      el("div", { class: "meta", text: `${src.visit_count} visits${src.last_sync ? ` · synced ${new Date(src.last_sync * 1000).toLocaleTimeString()}` : ""}${src.seen ? "" : " · not found on last scan"}` }),
      src.detail ? el("div", { class: "meta red", text: src.detail }) : null),
    el("button", { class: `btn small ${src.enabled ? "ghost" : ""}`, text: src.enabled ? "disable" : "enable", onclick: async () => {
      await api.post(`/api/sources/${encodeURIComponent(src.id)}`, { enabled: !src.enabled }); await loadStatus(); renderTab("sources");
    } }))) : el("li", { class: "empty", text: "No browsers found yet. Hit Sync." }));

  const devices = el("ul", { class: "list" }, s.devices.map((d) => {
    const input = el("input", { type: "text", value: d.label, "aria-label": `name for ${d.device}`, style: { width: "100%" } });
    return el("li", { class: "source" }, el("span", { class: "device-icon", text: d.device === "this-device" ? "▣" : "▯" }),
      el("div", {}, input, el("div", { class: "meta", text: `${d.device} · ${d.visits} visits · last ${new Date(d.last * 1000).toLocaleString()}` })),
      el("button", { class: "btn small ghost", text: "rename", onclick: async () => {
        await api.post(`/api/devices/${encodeURIComponent(d.device)}`, { alias: input.value }); await loadStatus(); toast("Saved", "Re-sync to refresh insights.", "good");
      } }));
  }));

  const pairBox = el("div", {}, el("button", { class: "btn", text: "Show pairing QR", onclick: async (e) => {
    try {
      const r = await api.get("/api/pair");
      e.target.replaceWith(el("div", {},
        el("div", { class: "qr" }, el("img", { src: `/api/pair/qr.svg?t=${Date.now()}`, alt: "pairing QR code", width: 185, height: 185 })),
        el("p", { class: "small" }, "Or open ", el("code", { text: r.url })),
        r.lan ? null : el("p", { class: "amber small", text: "Heads-up: the server is bound to this computer only. Restart with `zenchan serve --lan` so your phone can reach it." }),
        el("p", { class: "muted small", text: "The token is like a password for your history. Rotate it if it leaks." }),
        el("button", { class: "btn small danger", text: "Rotate token", onclick: async () => { await api.post("/api/token/rotate"); toast("Token rotated", "Paired devices must re-pair.", "warn"); renderTab("sources"); } })));
    } catch (err) { toast("Only available on the host computer", err.message); }
  } }));

  const drop = el("label", { class: "dropzone" }, "Drop a Google Takeout BrowserHistory.json, Safari History.json/zip, or a CSV — or click to choose",
    el("input", { type: "file", accept: ".json,.csv,.zip", hidden: true, onchange: (e) => upload(e.target.files[0]) }));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drag"); upload(e.dataTransfer.files[0]); });
  async function upload(file) {
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try { await api.post("/api/import", fd, true); openModal(); onProgress({ line: `> imported ${file.name}` }); }
    catch (e) { toast("Import failed", e.message); }
  }

  const ml = s.ml || {};
  body.append(
    panel("Browsers & profiles", { span: "span-7", note: `${s.visits} visits total` }, sourcesList,
      el("p", { class: "muted small", text: "Safari on macOS needs Full Disk Access for your terminal. Deleting history in a browser also removes it here on the next sync." })),
    panel("Devices", { span: "span-5", note: "give your phone a name" }, s.devices.length ? devices : empty("No devices yet.")),
    panel("Pair a phone", { span: "span-6", note: "use the dashboard as an app on your phone" },
      el("p", { text: "Scan this on your phone (same Wi-Fi), then “Add to Home Screen”. Live nudges show up as notifications while the app is open." }), pairBox),
    panel("Mobile & other browsers", { span: "span-6" },
      el("ul", { class: "list small" },
        el("li", {}, el("b", { text: "Android Chrome / iPhone Safari: " }), "turn on history sync (Chrome Sync or iCloud Safari). Synced visits show up here as separate devices automatically."),
        el("li", {}, el("b", { text: "Firefox for Android & desktop browsers: " }), "install the Zen-chan extension (folder ", el("code", { text: "extension/" }), ") for exact foreground time and live tracking — point it at this server with your pairing token."),
        el("li", {}, el("b", { text: "Anything else: " }), "import a Google Takeout or CSV export below."))),
    panel("Import history", { span: "span-12" }, drop),
    panel("This machine", { span: "span-12" },
      el("div", { class: "kpis" },
        el("div", { class: "kpi" }, el("div", { class: "v", style: { fontSize: "24px" }, text: `${s.platform.os}${s.platform.wsl ? " (WSL)" : ""}` }), el("div", { class: "k", text: `python ${s.platform.python} · ${s.platform.machine}` })),
        el("div", { class: "kpi" }, el("div", { class: "v", style: { fontSize: "24px" }, text: ml.sentence_transformers ? "MiniLM" : "hash" }), el("div", { class: "k", text: ml.sentence_transformers ? "semantic embeddings (transformer)" : "lexical fallback — pip install 'zenchan[ml]'" })),
        el("div", { class: "kpi" }, el("div", { class: "v", style: { fontSize: "24px" }, text: ml.umap ? "UMAP" : "PCA" }), el("div", { class: "k", text: "map projection" })),
        el("div", { class: "kpi" }, el("div", { class: "v", style: { fontSize: "24px" }, text: s.watcher ? "on" : "off" }), el("div", { class: "k", text: "live watcher" })))),
  );
  return body;
}

/* ------------------------------------------------------------------ SETTINGS */

const ALL_CATEGORIES = ["Programming & Dev", "AI Tools", "Work & Productivity", "Learning & Education", "Research & Reference", "Science",
  "Jobs & Career", "News & Politics", "Tech News & Gadgets", "Reading & Blogs", "Finance & Crypto", "Health & Fitness", "Shopping",
  "Travel & Maps", "Food & Cooking", "Communication", "Home & Lifestyle", "Art & Design", "Music & Audio", "Social Media",
  "Video & Streaming", "Entertainment & Pop Culture", "Gaming", "Sports"];

function renderSettings() {
  const st = state.settings;
  if (!st) return empty("loading…");
  const g = st.goals, n = st.nudges, nar = st.narrator;
  const row = (label, input) => el("div", { class: "row" }, el("label", { text: label }), input);
  const num = (v, min = 0, max = 1000) => el("input", { type: "number", value: v, min, max });
  const time = (v) => el("input", { type: "time", value: v });
  const check = (v) => el("input", { type: "checkbox", checked: !!v });
  const productive = new Set(g.productive);
  const prodChecks = el("div", { class: "checks" }, ALL_CATEGORIES.map((c) => el("label", {},
    el("input", { type: "checkbox", value: c, checked: productive.has(c) }), el("span", { text: c }))));
  const limits = el("div", { class: "form" }, ["Social Media", "Video & Streaming", "Gaming", "News & Politics", "Shopping"].map((c) =>
    row(`${c} (min/day)`, num(g.limits?.[c] ?? "", 0, 1440))));
  const f = {
    bedtime: time(g.bedtime), wake: time(g.wake),
    enabled: check(n.enabled), doom: num(n.doomscroll_minutes, 5, 240), binge: num(n.binge_minutes, 20, 600),
    sw: num(n.switches_per_30min, 10, 500), checks: num(n.compulsive_checks, 3, 200), spiral: num(n.search_spiral, 2, 50),
    focus: num(n.focus_praise_minutes, 15, 300), regret: num(n.regret_threshold, 0.5, 0.99), forecast: check(n.forecast), cooldown: num(n.cooldown_minutes, 1, 600),
    desktop: check(st.notify.desktop), ntfy: el("input", { type: "url", value: st.notify.ntfy_url || "", placeholder: "https://ntfy.sh/your-secret-topic" }),
    backend: el("select", {}, ["local", "ollama", "anthropic"].map((b) => el("option", { value: b, selected: nar.backend === b, text: b }))),
    ollamaUrl: el("input", { type: "url", value: nar.ollama_url }), ollamaModel: el("input", { type: "text", value: nar.ollama_model }),
    anthropicModel: el("input", { type: "text", value: nar.anthropic_model }), share: check(nar.share_titles),
    embedder: el("select", {}, ["auto", "minilm", "hash"].map((b) => el("option", { value: b, selected: st.ml.embedder === b, text: b }))),
    tz: el("input", { type: "text", value: st.timezone || "", placeholder: "system default, e.g. Asia/Kolkata" }),
    mirror: check(st.mirror_deletions),
  };
  const save = async () => {
    const lim = {};
    limits.querySelectorAll("input").forEach((inp, i) => {
      const c = ["Social Media", "Video & Streaming", "Gaming", "News & Politics", "Shopping"][i];
      if (inp.value !== "" && Number(inp.value) > 0) lim[c] = Number(inp.value);
    });
    const patch = {
      goals: { productive: [...prodChecks.querySelectorAll("input:checked")].map((i) => i.value), limits: lim, bedtime: f.bedtime.value, wake: f.wake.value },
      nudges: { enabled: f.enabled.checked, doomscroll_minutes: +f.doom.value, binge_minutes: +f.binge.value, switches_per_30min: +f.sw.value,
        compulsive_checks: +f.checks.value, search_spiral: +f.spiral.value, focus_praise_minutes: +f.focus.value, regret_threshold: +f.regret.value,
        forecast: f.forecast.checked, cooldown_minutes: +f.cooldown.value },
      notify: { desktop: f.desktop.checked, ntfy_url: f.ntfy.value.trim() },
      narrator: { backend: f.backend.value, ollama_url: f.ollamaUrl.value, ollama_model: f.ollamaModel.value, anthropic_model: f.anthropicModel.value, share_titles: f.share.checked },
      ml: { embedder: f.embedder.value }, timezone: f.tz.value.trim(), mirror_deletions: f.mirror.checked,
    };
    try { state.settings = await api.post("/api/settings", patch); toast("Saved", "Re-sync to apply goals to past insights.", "good"); }
    catch (e) { toast("Couldn't save", e.message); }
  };
  f.regret.step = "0.05";
  return grid(
    panel("What counts as intentional for you", { span: "span-12" }, prodChecks),
    panel("Daily limits & sleep", { span: "span-6" }, el("div", { class: "form" }, limits, row("Bedtime", f.bedtime), row("Wake time", f.wake), row("Timezone", f.tz))),
    panel("The watcher", { span: "span-6" }, el("div", { class: "form" },
      row("Nudges on", f.enabled), row("Doomscroll after (min)", f.doom), row("Long watch after (min)", f.binge),
      row("Scattered: switches / 30 min", f.sw), row("Checking loop: visits / day", f.checks), row("Search spiral: similar searches", f.spiral),
      row("Praise focus after (min)", f.focus), row("Regret warning above", f.regret), row("Forecast drift", f.forecast), row("Quiet time between nudges (min)", f.cooldown))),
    panel("Notifications", { span: "span-6" }, el("div", { class: "form" },
      row("Desktop notifications", f.desktop), row("Push to phone (ntfy topic)", f.ntfy),
      row("This browser", el("button", { class: "btn small ghost", text: "enable web notifications", onclick: async () => {
        if (!("Notification" in window)) return toast("Not supported", "This browser has no notification API.");
        toast("Notifications", `permission: ${await Notification.requestPermission()}`, "good");
      } }))),
      el("p", { class: "muted small", text: "ntfy sends only the nudge text, never your history. Use a long, secret topic name or your own server." })),
    panel("Zen-chan's voice", { span: "span-6" }, el("div", { class: "form" },
      row("Narrator", f.backend), row("Ollama URL", f.ollamaUrl), row("Ollama model", f.ollamaModel), row("Claude model", f.anthropicModel),
      row("Let the LLM see page titles & searches", f.share), row("Embeddings", f.embedder)),
      el("p", { class: "muted small", text: "local = offline templates. ollama = an LLM on your own machine. anthropic = Claude via API (needs ANTHROPIC_API_KEY); it only receives aggregate numbers unless you tick the titles box." })),
    panel("Privacy", { span: "span-12" },
      el("div", { class: "form" }, row("Forget what I delete in my browser", f.mirror)),
      el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "10px" } },
        el("button", { class: "btn", text: "Save settings", onclick: save }),
        el("a", { class: "btn ghost", href: "/api/export", text: "Export my insights (JSON)" }),
        el("button", { class: "btn danger", text: "Forget everything", onclick: async () => {
          if (prompt("This deletes Zen-chan's database (your browsers are untouched). Type: forget everything") !== "forget everything") return;
          await api.post("/api/forget", { confirm: "forget everything" }); state.insights = {}; toast("Forgotten", "Zen-chan's memory is empty.", "good"); await loadStatus();
        } })),
      el("p", { class: "muted small", text: "Everything Zen-chan knows lives in one SQLite file on this computer. Your browsers' own history is only ever read, never modified." })),
  );
}

boot();
