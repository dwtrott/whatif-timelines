/* WhatIf Timelines — front-end. Vanilla JS + SVG, no build step. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const DAY = 86400000;
const parseD = iso => new Date(iso + 'T00:00:00Z');
const fmtD = iso => iso ? parseD(iso).toLocaleDateString(undefined, {year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'}) : '';
const daysBetween = (a, b) => Math.round((parseD(b) - parseD(a)) / DAY);

const state = {
  config: null, scenarios: [], sc: null, sel: null, pxPerDay: null, laneOrder: [], hoverTimer: null,
  sse: null, pollTimer: null, chat: {},
};

// ------------------------------------------------------------------ api
async function api(path, method = 'GET', body) {
  const r = await fetch(path, {method, headers: {'content-type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, cls = '') {
  logLine({msg, level: cls || 'info', ts: new Date().toISOString().slice(11, 19)});
}

// ------------------------------------------------------------------ boot
async function boot() {
  await loadConfig();
  await loadScenarios();
  const last = localStorage.getItem('whatif.sc');
  if (state.scenarios.length) await selectScenario(state.scenarios.find(s => s.id === last)?.id || state.scenarios[0].id);
  connectSSE();
  bindUI();
  schedulePoll();
}

async function loadConfig() {
  state.config = await api('/api/config');
  const s = state.config.settings;
  $('#providerLabel').textContent = `${s.provider} · ${s.model}`;
  $('#providerDot').className = 'dot ' + (s.has_key ? 'ok' : 'bad');
  const st = state.config.stats;
  $('#llmStats').textContent = st.calls ? `${st.calls} calls · ${(st.tokens_in + st.tokens_out).toLocaleString()} tok` : '';
}

async function loadScenarios() {
  state.scenarios = await api('/api/scenarios');
  const sel = $('#scenarioSelect');
  sel.innerHTML = state.scenarios.map(s => `<option value="${s.id}">${esc(s.title)} (${s.anchor_date} → ${s.horizon_date})</option>`).join('') || '<option value="">— no scenarios —</option>';
  if (state.sc) sel.value = state.sc.id;
}

async function selectScenario(id) {
  if (!id) { state.sc = null; renderAll(); return; }
  state.sc = await api(`/api/scenarios/${id}`);
  state.sel = null;
  state.pxPerDay = null;
  localStorage.setItem('whatif.sc', id);
  $('#scenarioSelect').value = id;
  renderAll();
}

async function refreshScenario() {
  if (!state.sc) return;
  try {
    const fresh = await api(`/api/scenarios/${state.sc.id}`);
    state.sc = fresh;
    renderAll(true);
  } catch (e) { /* server restarting? */ }
}

function isActive() {
  if (!state.sc) return false;
  if (['retrieving', 'new'].includes(state.sc.status)) return true;
  return Object.values(state.sc.branches).some(b => ['running', 'retrieving', 'pending'].includes(b.status));
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    if (isActive()) { await refreshScenario(); await loadConfig(); }
    schedulePoll();
  }, isActive() ? 2500 : 8000);
}

// ------------------------------------------------------------------ SSE console
function connectSSE() {
  if (state.sse) state.sse.close();
  const es = new EventSource('/api/events');
  state.sse = es;
  es.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === 'log') logLine(m);
    else if (m.type === 'agent_actions') {
      const b = state.sc?.branches?.[m.branch_id];
      logLine({ts: m.ts, level: 'agent', msg: `[${b ? b.name : m.branch_id}] ${m.date} · ` + m.actions.map(a => `${a.name}: ${a.action}`).join('  ‖  ')});
    } else if (m.type === 'llm_call') logLine({ts: m.ts, level: 'llm', msg: `llm ${m.kind || ''} ${m.model} ${m.ms}ms ${m.tokens ? m.tokens + ' tok' : ''}`});
    else if (m.type === 'scenario_ready' || m.type === 'branch_status' || m.type === 'branch_progress') {
      if (state.sc && m.scenario_id === state.sc.id) refreshScenario();
      if (m.type === 'scenario_ready') loadScenarios();
    }
  };
  es.onerror = () => { /* browser retries */ };
}

function logLine(m) {
  const body = $('#consoleBody');
  const div = document.createElement('div');
  div.className = m.level || 'info';
  const ts = (m.ts || '').slice(11, 19);
  div.innerHTML = `<span class="ts">${esc(ts)}</span>${esc(m.msg)}`;
  body.appendChild(div);
  while (body.children.length > 400) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

// ------------------------------------------------------------------ render
function renderAll(soft = false) {
  const sc = state.sc;
  $('#emptyState').hidden = !!sc;
  renderHeader();
  renderSidebar();
  renderTimeline();
  renderDetail(soft);
}

function renderHeader() {
  const sc = state.sc;
  if (!sc) { $('#scTitle').textContent = 'No scenario yet'; $('#scQuestion').textContent = ''; $('#scMeta').innerHTML = ''; return; }
  $('#scTitle').textContent = sc.title;
  $('#scQuestion').textContent = sc.question;
  const nb = Object.keys(sc.branches).length;
  $('#scMeta').innerHTML = `
    <span class="status ${sc.status}">${sc.status}</span>
    <span>${fmtD(sc.anchor_date)} → ${fmtD(sc.horizon_date)}</span>
    <span>${sc.personas.length} agents · ${nb} lane${nb === 1 ? '' : 's'}</span>
    <button class="ghost small" onclick="zoomFit()">fit</button>
    <button class="ghost small danger" onclick="deleteScenario()">delete</button>`;
  if (sc.status === 'failed') $('#scMeta').insertAdjacentHTML('afterbegin', `<span class="errbox">${esc(sc.error)}</span>`);
}

function laneOrder(sc) {
  const kids = {};
  Object.values(sc.branches).forEach(b => { (kids[b.parent_branch_id || 'root'] ||= []).push(b); });
  Object.values(kids).forEach(a => a.sort((x, y) => x.created_at.localeCompare(y.created_at)));
  const out = [];
  const walk = b => { out.push(b); (kids[b.id] || []).forEach(walk); };
  (kids['root'] || []).forEach(walk);
  return out;
}

function renderSidebar() {
  const sc = state.sc;
  const bl = $('#branchList'), al = $('#agentList'), dl = $('#docList');
  if (!sc) { bl.innerHTML = al.innerHTML = dl.innerHTML = '<div class="muted small">—</div>'; $('#branchCount').textContent = $('#agentCount').textContent = $('#docCount').textContent = ''; return; }
  const order = laneOrder(sc);
  $('#branchCount').textContent = order.length;
  bl.innerHTML = order.map(b => {
    const p = b.report?.probability_estimate;
    const active = state.sel?.type === 'branch' && state.sel.id === b.id;
    return `<div class="item ${active ? 'active' : ''}" style="margin-left:${Math.min(b.depth, 4) * 10}px" onclick="selectBranch('${b.id}')">
      <div class="t"><i class="sw" style="background:${b.color}"></i><span class="name">${esc(b.name)}</span><span class="spacer"></span><span class="status ${b.status}">${b.status}</span></div>
      <div class="sub">${b.premise ? 'what if: ' + esc(b.premise) : (b.kind === 'actual' ? 'extracted from sources' : 'no intervention')}</div>
      ${['running', 'retrieving', 'pending'].includes(b.status) ? `<div class="prog"><i style="width:${Math.round(b.progress * 100)}%;background:${b.color}"></i></div>` : ''}
      ${p != null ? `<div class="sub">p ≈ ${Math.round(p * 100)}% · ${b.events.length} events${b.report.converges ? ' · converges' : ''}</div>` : ''}
    </div>`;
  }).join('');
  $('#agentCount').textContent = sc.personas.length;
  al.innerHTML = sc.personas.map(p => `<div class="item" onclick="showPersona('${p.id}')"><div class="t"><span class="name">${esc(p.name)}</span></div><div class="sub" style="margin-left:0">${esc(p.role)}</div></div>`).join('') || '<div class="muted small">casting…</div>';
  $('#docCount').textContent = sc.docs.length;
  const label = {wikipedia_asof: 'wiki as-of', wikipedia_latest: 'wiki today', gdelt: 'gdelt', user: 'seed'};
  dl.innerHTML = sc.docs.slice(0, 60).map(d => `<div class="item" style="padding:4px 8px" title="${esc(d.note)}">
      <div class="t"><span class="tag">${label[d.source] || d.source}</span><span class="name" style="font-weight:500">${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>` : esc(d.title)}</span></div>
      <div class="sub" style="margin-left:0">${esc((d.as_of || '').slice(0, 10))}${d.cutoff_ok === false && d.source !== 'wikipedia_latest' ? ' · ⚠ filtered' : ''}${d.chars ? ' · ' + (d.chars / 1000).toFixed(1) + 'k chars' : ''}</div>
    </div>`).join('') || `<div class="muted small">${sc.status === 'retrieving' ? 'retrieving…' : 'no documents retrieved'}</div>`;
  if (sc.docs.length > 60) dl.insertAdjacentHTML('beforeend', `<div class="muted small">+${sc.docs.length - 60} more</div>`);
}

// ------------------------------------------------------------------ timeline
const PADL = 30, PADR = 60, AXIS_H = 34, LH = 78;

function renderTimeline() {
  const svg = $('#timeline'), labels = $('#laneLabels');
  const sc = state.sc;
  if (!sc) { svg.innerHTML = ''; labels.innerHTML = ''; return; }
  const order = laneOrder(sc);
  state.laneOrder = order;
  const scroll = $('#timelineScroll');
  const totalDays = Math.max(1, daysBetween(sc.anchor_date, sc.horizon_date));
  if (!state.pxPerDay) state.pxPerDay = Math.max(1.4, (Math.max(scroll.clientWidth, 600) - PADL - PADR) / totalDays);
  const ppd = state.pxPerDay;
  const X = iso => PADL + Math.max(0, Math.min(totalDays, daysBetween(sc.anchor_date, iso))) * ppd;
  const W = PADL + totalDays * ppd + PADR;
  const H = AXIS_H + order.length * LH + 20;
  svg.setAttribute('width', W); svg.setAttribute('height', H);
  const laneY = {};
  order.forEach((b, i) => laneY[b.id] = AXIS_H + i * LH + LH / 2);

  let g = '';
  // lane backgrounds
  order.forEach((b, i) => { g += `<rect class="laneBg" x="0" y="${AXIS_H + i * LH}" width="${W}" height="${LH}" fill="${i % 2 ? 'rgba(255,255,255,.015)' : 'transparent'}"/>`; });
  order.forEach((b, i) => { g += `<line class="laneSep" x1="0" x2="${W}" y1="${AXIS_H + (i + 1) * LH}" y2="${AXIS_H + (i + 1) * LH}"/>`; });
  // axis
  g += axisSVG(sc, X, W, H, ppd);
  // today
  const today = state.config?.today;
  if (today && today > sc.anchor_date && today < sc.horizon_date) {
    g += `<line class="todayLine" x1="${X(today)}" x2="${X(today)}" y1="${AXIS_H - 6}" y2="${H}"/><text class="todayText" x="${X(today) + 4}" y="${AXIS_H - 10}">today</text>`;
  }
  // lanes
  order.forEach(b => {
    const y = laneY[b.id];
    const evs = b.events.slice().sort((a, c) => a.date.localeCompare(c.date));
    const start = b.fork_date || sc.anchor_date;
    const last = evs.length ? evs[evs.length - 1].date : start;
    const endX = b.status === 'completed' && b.kind === 'actual' ? X(last) : X(last);
    if (b.parent_branch_id) {
      g += `<path class="inherit" d="M${X(sc.anchor_date)},${y} L${X(start)},${y}" stroke="${b.color}"/>`;
      const py = laneY[b.parent_branch_id];
      if (py != null) {
        const x = X(start);
        g += `<path class="forkLink" d="M${x},${py} C${x},${(py + y) / 2} ${x},${(py + y) / 2} ${x},${y}" stroke="${b.color}"/>`;
      }
    }
    g += `<path class="laneLine" d="M${X(start)},${y} L${Math.max(endX, X(start) + 2)},${y}" stroke="${b.color}" stroke-opacity="${b.kind === 'actual' ? .5 : .85}"/>`;
    if (['running', 'retrieving', 'pending'].includes(b.status)) {
      const hx = Math.max(endX, X(start) + 2);
      g += `<circle class="head" cx="${hx}" cy="${y}" r="7" fill="${b.color}"/><text class="rlabel" x="${hx + 12}" y="${y + 4}">${b.status === 'running' ? `round ${b.rounds_done}/${b.total_rounds}` : b.status + '…'}</text>`;
    } else if (b.status === 'completed' && b.report?.probability_estimate != null) {
      g += `<text class="rlabel" x="${endX + 12}" y="${y + 4}">p≈${Math.round(b.report.probability_estimate * 100)}%${b.report.converges ? ' ↩' : ''}</text>`;
    } else if (b.status === 'failed' || b.status === 'stopped') {
      g += `<text class="rlabel" x="${endX + 12}" y="${y + 4}" fill="var(--bad)">${b.status}</text>`;
    }
    // events, stacked per date
    const byDate = {};
    evs.forEach(e => (byDate[e.date] ||= []).push(e));
    Object.values(byDate).forEach(group => {
      group.forEach((e, k) => {
        const off = group.length === 1 ? 0 : (k - (group.length - 1) / 2) * 16;
        const r = 3.5 + (e.importance || 3) * 1.1;
        const op = e.kind === 'actual' || e.kind === 'premise' ? 1 : 0.45 + (e.confidence ?? .6) * .55;
        const sel = state.sel?.type === 'event' && state.sel.id === e.id ? 'selected' : '';
        const fill = e.kind === 'actual' ? '#e5e7eb' : b.color;
        g += `<g class="ev ${e.kind} ${sel}" data-id="${e.id}" data-b="${b.id}" transform="translate(${X(e.date)},${y + off})">
          <circle class="halo" r="${r + 6}"/>
          ${e.divergence > 0.05 ? `<circle class="ring" r="${r + 3.5}" stroke-opacity="${e.divergence}" stroke-width="${1 + e.divergence * 2}"/>` : ''}
          <circle class="core" r="${r}" fill="${fill}" fill-opacity="${op}"/>
        </g>`;
      });
    });
  });
  svg.innerHTML = g;

  // lane labels (left column)
  labels.innerHTML = order.map((b, i) => `<div class="laneLabel ${state.sel?.type === 'branch' && state.sel.id === b.id ? 'active' : ''}" style="top:${AXIS_H + i * LH - scroll.scrollTop}px;height:${LH}px" onclick="selectBranch('${b.id}')">
      <div class="nm"><i class="sw" style="background:${b.color};width:10px;height:10px;border-radius:3px;flex:none"></i><span title="${esc(b.name)}">${esc(b.name)}</span></div>
      <div class="pr" title="${esc(b.premise)}">${b.premise ? 'what if: ' + esc(b.premise) : (b.kind === 'actual' ? 'actual · from sources' : 'baseline forecast')}</div>
      <div class="acts">
        ${b.status === 'running' || b.status === 'retrieving' ? `<button onclick="event.stopPropagation();stopBranch('${b.id}')">stop</button>` : ''}
        ${b.status === 'completed' && b.report?.summary ? `<button onclick="event.stopPropagation();selectBranch('${b.id}')">report</button>` : ''}
        ${b.status !== 'running' && b.status !== 'retrieving' ? `<button onclick="event.stopPropagation();forkAtDate('${b.id}')">fork at…</button>` : ''}
      </div>
    </div>`).join('');

  // interactions
  $$('.ev', svg).forEach(el => {
    el.addEventListener('click', () => selectEvent(el.dataset.id, el.dataset.b));
    el.addEventListener('mouseenter', ev => showTip(ev, el.dataset.id, el.dataset.b));
    el.addEventListener('mousemove', moveTip);
    el.addEventListener('mouseleave', hideTip);
  });
}

function axisSVG(sc, X, W, H, ppd) {
  let g = `<g class="axis"><line x1="0" x2="${W}" y1="${AXIS_H}" y2="${AXIS_H}"/>`;
  const a = parseD(sc.anchor_date), b = parseD(sc.horizon_date);
  const spanDays = (b - a) / DAY;
  // tick granularity
  let mode = 'month';
  if (ppd * 30 < 45) mode = spanDays > 900 ? 'year' : 'quarter';
  if (ppd * 7 > 60) mode = 'week';
  if (ppd > 25) mode = 'day';
  const d = new Date(a);
  d.setUTCHours(0, 0, 0, 0);
  if (mode === 'month' || mode === 'quarter') d.setUTCDate(1);
  if (mode === 'quarter') d.setUTCMonth(Math.floor(d.getUTCMonth() / 3) * 3, 1);
  if (mode === 'year') { d.setUTCMonth(0, 1); }
  let guard = 0;
  while (d <= b && guard++ < 2000) {
    const iso = d.toISOString().slice(0, 10);
    if (d >= a) {
      const x = X(iso);
      const major = mode === 'day' ? d.getUTCDay() === 1 : mode === 'week' ? d.getUTCDate() <= 7 : mode === 'month' ? d.getUTCMonth() === 0 : true;
      const lab = mode === 'day' ? d.getUTCDate() : mode === 'week' ? d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'})
        : mode === 'month' ? (d.getUTCMonth() === 0 ? d.getUTCFullYear() : d.toLocaleDateString(undefined, {month: 'short', timeZone: 'UTC'}))
        : mode === 'quarter' ? (d.getUTCMonth() === 0 ? d.getUTCFullYear() : 'Q' + (Math.floor(d.getUTCMonth() / 3) + 1)) : d.getUTCFullYear();
      g += `<line class="${major ? 'major' : ''}" x1="${x}" x2="${x}" y1="${AXIS_H - (major ? 10 : 5)}" y2="${H}"/><text x="${x + 3}" y="${AXIS_H - 14}">${lab}</text>`;
    }
    if (mode === 'day') d.setUTCDate(d.getUTCDate() + 1);
    else if (mode === 'week') d.setUTCDate(d.getUTCDate() + 7);
    else if (mode === 'month') d.setUTCMonth(d.getUTCMonth() + 1);
    else if (mode === 'quarter') d.setUTCMonth(d.getUTCMonth() + 3);
    else d.setUTCFullYear(d.getUTCFullYear() + 1);
  }
  return g + '</g>';
}

function zoomFit() { state.pxPerDay = null; renderTimeline(); }

function findEvent(id, bid) {
  const b = state.sc.branches[bid];
  return b?.events.find(e => e.id === id);
}

// tooltip
function showTip(ev, id, bid) {
  const e = findEvent(id, bid); if (!e) return;
  const t = $('#tooltip');
  t.innerHTML = `<span class="d">${fmtD(e.date)} · ${esc(state.sc.branches[bid].name)}</span><b>${esc(e.headline)}</b>${esc(e.summary).slice(0, 180)}${e.summary.length > 180 ? '…' : ''}`;
  t.hidden = false; moveTip(ev);
}
function moveTip(ev) { const t = $('#tooltip'); t.style.left = Math.min(window.innerWidth - 340, ev.clientX + 14) + 'px'; t.style.top = (ev.clientY + 14) + 'px'; }
function hideTip() { $('#tooltip').hidden = true; }

// ------------------------------------------------------------------ selection + detail
function selectEvent(id, bid) { state.sel = {type: 'event', id, bid}; renderTimeline(); renderSidebar(); renderDetail(); }
function selectBranch(id) { state.sel = {type: 'branch', id}; renderTimeline(); renderSidebar(); renderDetail(); }

function renderDetail(soft = false) {
  const box = $('#detailBody');
  const sc = state.sc;
  if (!sc || !state.sel) {
    if (sc && sc.status === 'retrieving') box.innerHTML = `<div class="pad"><h4 class="muted">Building baseline…</h4><div class="md">${sc.log.slice(-8).map(l => `<p class="small ${l.level}">${esc(l.msg)}</p>`).join('')}</div></div>`;
    else if (sc) box.innerHTML = `<div class="pad muted">Click an event on the timeline to inspect it or fork a <em>what if</em> from it. Click a lane name for its report.</div>`;
    else box.innerHTML = `<div class="pad muted">Select an event or a branch.</div>`;
    return;
  }
  // keep typed text during soft refreshes
  if (soft && box.querySelector('textarea, input') && document.activeElement && box.contains(document.activeElement)) return;
  if (state.sel.type === 'event') renderEventDetail(box);
  else renderBranchDetail(box);
}

function renderEventDetail(box) {
  const sc = state.sc, b = sc.branches[state.sel.bid], e = findEvent(state.sel.id, state.sel.bid);
  if (!e || !b) { box.innerHTML = '<div class="pad muted">gone</div>'; return; }
  const canFork = e.date < sc.horizon_date;
  const acts = e.agent_actions || [];
  box.innerHTML = `
    <div class="dhead">
      <div class="kicker"><span style="color:${b.color}">● ${esc(b.name)}</span><span>${fmtD(e.date)}</span><span>${e.kind}</span>${e.category ? `<span>${esc(e.category)}</span>` : ''}</div>
      <h2>${esc(e.headline)}</h2>
      <div>${esc(e.summary)}</div>
      <div class="tags">${(e.actors || []).map(a => `<span class="tag">${esc(a)}</span>`).join('')}</div>
    </div>
    ${e.kind === 'simulated' ? `<div class="dsec"><div class="kv">
        <b>confidence</b><div><div class="meter"><i style="width:${Math.round(e.confidence * 100)}%;background:var(--ok)"></i></div></div>
        <b>divergence</b><div><div class="meter"><i style="width:${Math.round(e.divergence * 100)}%;background:var(--bad)"></i></div></div>
        <b>importance</b><div>${'★'.repeat(e.importance)}${'☆'.repeat(5 - e.importance)}</div>
      </div></div>` : ''}
    ${canFork ? `<div class="dsec forkForm">
      <h4>What if… (fork from ${fmtD(e.date)})</h4>
      <textarea id="forkPremise" placeholder="Describe the counterfactual that becomes true at this point, e.g. “${esc(examplePremise(sc, e))}”"></textarea>
      <div class="grid2">
        <label>Branch name (optional)<input id="forkName"></label>
        <label>Max rounds<input id="forkRounds" type="number" min="1" max="60" value="${state.config?.max_rounds || 12}"></label>
      </div>
      <div class="row end"><span class="muted small">${estimateCalls(sc, e.date)} LLM calls</span><button class="primary" onclick="doFork('${b.id}','${e.id}')">⑂ Fork timeline</button></div>
    </div>` : ''}
    ${acts.length ? `<div class="dsec"><h4>What the swarm did this period</h4>${acts.map(a => `<div class="agentAct">
        <div class="who">${esc(a.name)} <span>· ${esc(a.role)}</span></div>
        <div>${esc(a.action)}</div>
        ${a.statement ? `<div class="say">${esc(a.statement)}</div>` : ''}
        ${a.thoughts ? `<details><summary>private reasoning</summary>${esc(a.thoughts)}${a.predicted_next ? `<br><em>expects:</em> ${esc(a.predicted_next)}` : ''}</details>` : ''}
      </div>`).join('')}</div>` : ''}
    ${e.sources?.length ? `<div class="dsec"><h4>Sources</h4>${e.sources.map(s => `<div class="small">${esc(s)}</div>`).join('')}</div>` : ''}
  `;
}

function examplePremise(sc, e) {
  const a = (e.actors && e.actors[0]) || 'the key actor';
  return `${a} does the opposite: ${e.headline.toLowerCase()} never happens`;
}

function estimateCalls(sc, fromDate, rounds) {
  const cap = rounds || state.config?.max_rounds || 12;
  const days = daysBetween(fromDate, sc.horizon_date);
  const r = Math.min(cap, Math.max(1, Math.ceil(days / sc.step_days)));
  return `≈${r * (sc.personas.length + 1) + 3}`;
}

async function doFork(parentId, eventId, dateOverride) {
  const premise = $('#forkPremise')?.value.trim() || '';
  const name = $('#forkName')?.value.trim() || '';
  const rounds = parseInt($('#forkRounds')?.value || '0', 10) || undefined;
  try {
    const res = await api(`/api/scenarios/${state.sc.id}/fork`, 'POST', {parent_branch_id: parentId, fork_event_id: eventId, premise, name, max_rounds: rounds, fork_date: dateOverride});
    state.sc = res.scenario;
    selectBranch(res.branch_id);
    schedulePoll();
  } catch (e) { alert('Fork failed: ' + e.message); }
}

function forkAtDate(bid) {
  const b = state.sc.branches[bid];
  const box = $('#detailBody');
  state.sel = {type: 'branch', id: bid};
  const minD = b.fork_date || state.sc.anchor_date;
  const lastEv = b.events[b.events.length - 1];
  box.innerHTML = `<div class="dhead"><div class="kicker" style="color:${b.color}">● ${esc(b.name)}</div><h2>Fork at a date</h2></div>
    <div class="dsec forkForm">
      <label>Fork date (${minD} … ${state.sc.horizon_date})<input id="forkDate" type="date" min="${minD}" max="${state.sc.horizon_date}" value="${lastEv ? lastEv.date : minD}"></label>
      <label>What if…<textarea id="forkPremise" placeholder="Counterfactual premise that becomes true on that date (leave empty for a plain forecast)"></textarea></label>
      <div class="grid2"><label>Branch name<input id="forkName"></label><label>Max rounds<input id="forkRounds" type="number" min="1" max="60" value="${state.config?.max_rounds || 12}"></label></div>
      <div class="row end"><button class="primary" onclick="doFork('${b.id}', null, $('#forkDate').value)">⑂ Fork timeline</button></div>
    </div>`;
}

function renderBranchDetail(box) {
  const sc = state.sc, b = sc.branches[state.sel.id];
  if (!b) { box.innerHTML = '<div class="pad muted">gone</div>'; return; }
  const r = b.report || {};
  const parent = b.parent_branch_id ? sc.branches[b.parent_branch_id] : null;
  const others = Object.values(sc.branches).filter(x => x.id !== b.id && x.events.length > 1);
  const chat = state.chat[b.id] || [];
  box.innerHTML = `
    <div class="dhead">
      <div class="kicker"><span style="color:${b.color}">● lane</span><span class="status ${b.status}">${b.status}</span>${parent ? `<span>forked from ${esc(parent.name)} @ ${b.fork_date}</span>` : ''}</div>
      <h2>${esc(b.name)}</h2>
      <div class="muted">${b.premise ? '<b>What if:</b> ' + esc(b.premise) : (b.kind === 'actual' ? 'Actual history extracted from present-day sources.' : 'Baseline forecast, no intervention.')}</div>
      <div class="kv" style="margin-top:8px">
        <b>knowledge cutoff</b><span>${fmtD(b.knowledge_cutoff)}</span>
        <b>periods</b><span>${b.total_rounds ? `${b.rounds_done}/${b.total_rounds} × ${b.step_days} days` : b.events.length + ' events'}</span>
        ${r.probability_estimate != null ? `<b>probability</b><span>≈${Math.round(r.probability_estimate * 100)}% ${r.converges ? '· converges with parent' : ''}</span>` : ''}
      </div>
      ${b.error ? `<div class="errbox" style="margin-top:8px">${esc(b.error)}</div>` : ''}
      <div class="row" style="margin-top:8px">
        ${['running', 'retrieving'].includes(b.status) ? `<button class="danger small" onclick="stopBranch('${b.id}')">stop</button>` : ''}
        ${b.id !== sc.baseline_branch_id && !['running', 'retrieving'].includes(b.status) ? `<button class="ghost small danger" onclick="deleteBranch('${b.id}')">delete lane</button>` : ''}
        ${!['running', 'retrieving'].includes(b.status) ? `<button class="ghost small" onclick="forkAtDate('${b.id}')">fork at date…</button>` : ''}
      </div>
    </div>
    ${r.summary ? `<div class="dsec"><h4>Report</h4><div class="md"><p><b>${esc(r.summary)}</b></p>${md(r.narrative || '')}</div>
        ${r.what_changed ? `<p class="small"><b>Mechanism:</b> ${esc(r.what_changed)}</p>` : ''}
        ${(r.key_divergences || []).length ? `<h4 style="margin-top:10px">Key divergences</h4><ul class="md">${r.key_divergences.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${(r.signposts || []).length ? `<h4 style="margin-top:10px">Signposts</h4><ul class="md">${r.signposts.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${r.convergence_note ? `<p class="small muted">${esc(r.convergence_note)}</p>` : ''}
      </div>` : ''}
    ${b.indicators?.length > 1 ? `<div class="dsec"><h4>Indicators</h4>${sparkSVG(b.indicators)}<div class="small muted"><span style="color:var(--bad)">■</span> tension <span style="color:var(--ok)">■</span> public support <span style="color:var(--warn)">■</span> economic stress</div></div>` : ''}
    ${b.world_state ? `<div class="dsec"><h4>World state at end</h4><div class="small">${esc(b.world_state)}</div></div>` : ''}
    <div class="dsec"><h4>Timeline (${b.events.length})</h4>${b.events.slice().sort((x, y) => x.date.localeCompare(y.date)).map(e => `<div class="small" style="margin:3px 0;cursor:pointer" onclick="selectEvent('${e.id}','${b.id}')"><span class="mono muted">${e.date}</span> ${esc(e.headline)}${e.divergence > .5 ? ' <span style="color:var(--bad)">◆</span>' : ''}</div>`).join('')}</div>
    ${b.events.length > 1 && sc.personas.length ? `<div class="dsec"><h4>Interview an agent on this branch</h4>
      <div class="chat" id="chatBox">${chat.map(c => `<div class="q">You → ${esc(c.who)}: ${esc(c.q)}</div><div class="a">${esc(c.a)}</div>`).join('')}</div>
      <div class="row"><select id="ivWho" style="width:45%">${sc.personas.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select><input id="ivQ" placeholder="Ask…" style="flex:1" onkeydown="if(event.key==='Enter')doInterview('${b.id}')"><button onclick="doInterview('${b.id}')">ask</button></div>
    </div>` : ''}
    ${others.length ? `<div class="dsec"><h4>Compare with…</h4><div class="row"><select id="cmpWith" style="flex:1">${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select><button onclick="doCompare('${b.id}')">compare</button></div><div id="cmpOut" class="md"></div></div>` : ''}
    ${b.briefing ? `<div class="dsec"><details><summary class="muted small">Briefing the agents received (cutoff ${b.knowledge_cutoff})</summary>
        ${(b.briefing_flags || []).map(f => `<div class="flag">⚠ ${esc(f)}</div>`).join('')}
        <div class="md small">${md(b.briefing)}</div></details></div>` : ''}
  `;
}

function sparkSVG(ind) {
  const W = 360, H = 70, n = ind.length;
  const x = i => 4 + i * (W - 8) / Math.max(1, n - 1), y = v => H - 4 - v * (H - 8);
  const line = (k, col) => `<polyline fill="none" stroke="${col}" stroke-width="1.5" points="${ind.map((d, i) => `${x(i)},${y(d[k] ?? .5)}`).join(' ')}"/>`;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${line('tension', 'var(--bad)')}${line('public_support', 'var(--ok)')}${line('economic_stress', 'var(--warn)')}</svg>`;
}

function md(s) {
  // tiny markdown: headers, bold/italic, lists, paragraphs
  const lines = String(s || '').split(/\r?\n/);
  let out = '', inList = false;
  const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|\W)\*(.+?)\*(?=\W|$)/g, '$1<i>$2</i>').replace(/`(.+?)`/g, '<code>$1</code>');
  for (const ln of lines) {
    const m = ln.match(/^(\s*)[-*•]\s+(.*)/);
    if (m) { if (!inList) { out += '<ul>'; inList = true; } out += `<li>${inline(m[2])}</li>`; continue; }
    if (inList) { out += '</ul>'; inList = false; }
    const h = ln.match(/^(#{1,4})\s+(.*)/);
    if (h) { out += `<h3>${inline(h[2])}</h3>`; continue; }
    if (ln.startsWith('>')) { out += `<blockquote>${inline(ln.slice(1))}</blockquote>`; continue; }
    if (ln.trim()) out += `<p>${inline(ln)}</p>`;
  }
  if (inList) out += '</ul>';
  return out;
}

async function doInterview(bid) {
  const who = $('#ivWho').value, q = $('#ivQ').value.trim();
  if (!q) return;
  const p = state.sc.personas.find(x => x.id === who);
  const chat = (state.chat[bid] ||= []);
  chat.push({who: p.name, q, a: '…'});
  renderDetail();
  try {
    const r = await api(`/api/scenarios/${state.sc.id}/branches/${bid}/interview`, 'POST', {persona_id: who, question: q});
    chat[chat.length - 1].a = r.answer;
  } catch (e) { chat[chat.length - 1].a = 'error: ' + e.message; }
  renderDetail();
  const cb = $('#chatBox'); if (cb) cb.scrollTop = cb.scrollHeight;
}

async function doCompare(bid) {
  const other = $('#cmpWith').value, out = $('#cmpOut');
  out.innerHTML = '<p class="muted">comparing…</p>';
  try {
    const r = await api(`/api/scenarios/${state.sc.id}/compare?a=${bid}&b=${other}`);
    out.innerHTML = md(r.comparison || '') + (r.points ? `<table class="small" style="width:100%;border-collapse:collapse;margin-top:6px">${r.points.map(p => `<tr><td class="muted" style="padding:3px 4px;border-top:1px solid var(--line)">${esc(p.dimension)}</td><td style="padding:3px 4px;border-top:1px solid var(--line)">${esc(p.a)}</td><td style="padding:3px 4px;border-top:1px solid var(--line)">${esc(p.b)}</td></tr>`).join('')}</table>` : '')
      + (r.first_divergence ? `<p class="small"><b>First divergence:</b> ${esc(r.first_divergence)}</p>` : '') + (r.verdict ? `<p class="small"><b>Verdict:</b> ${esc(r.verdict)}</p>` : '');
  } catch (e) { out.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}

function showPersona(pid) {
  const p = state.sc.personas.find(x => x.id === pid);
  state.sel = {type: 'persona', id: pid};
  $('#detailBody').innerHTML = `<div class="dhead"><div class="kicker">agent</div><h2>${esc(p.name)}</h2><div class="muted">${esc(p.role)}</div></div>
    <div class="dsec"><div class="kv"><b>goals</b><span>${esc(p.goals)}</span><b>stance</b><span>${esc(p.stance)}</span><b>style</b><span>${esc(p.style)}</span><b>resources</b><span>${esc(p.resources)}</span></div></div>
    <div class="dsec small muted">Open a branch to interview this agent inside that timeline.</div>`;
  renderSidebar();
}

async function stopBranch(bid) { await api(`/api/scenarios/${state.sc.id}/branches/${bid}/stop`, 'POST'); setTimeout(refreshScenario, 500); }
async function deleteBranch(bid) {
  if (!confirm('Delete this lane and everything forked from it?')) return;
  await api(`/api/scenarios/${state.sc.id}/branches/${bid}`, 'DELETE');
  state.sel = null; await refreshScenario();
}
async function deleteScenario() {
  if (!state.sc || !confirm(`Delete scenario “${state.sc.title}”?`)) return;
  await api(`/api/scenarios/${state.sc.id}`, 'DELETE');
  state.sc = null; await loadScenarios();
  if (state.scenarios.length) await selectScenario(state.scenarios[0].id); else renderAll();
}

// ------------------------------------------------------------------ modals
function openModal(id) { $('#' + id).hidden = false; }
function closeModal(id) { $('#' + id).hidden = true; }

async function openNewScenario() {
  openModal('newScenarioModal');
  if (!$('#exampleChips').children.length) {
    const ex = await api('/api/examples');
    $('#exampleChips').innerHTML = ex.map((e, i) => `<button class="ghost" data-i="${i}">${esc(e.title)}</button>`).join('');
    $$('#exampleChips button').forEach(btn => btn.onclick = () => {
      const e = ex[+btn.dataset.i];
      $('#fTitle').value = e.title; $('#fQuestion').value = e.question; $('#fAnchor').value = e.anchor_date; $('#fHorizon').value = e.horizon_date;
      $('#fStep').value = e.step_days; $('#fAgents').value = e.n_agents;
      $('#fDocs').placeholder = 'Suggested fork after the baseline builds: ' + e.forks[0];
    });
  }
  if (!$('#fAnchor').value) {
    const t = new Date(); $('#fAnchor').value = t.toISOString().slice(0, 10);
    t.setUTCDate(t.getUTCDate() + 90); $('#fHorizon').value = t.toISOString().slice(0, 10);
  }
}

async function createScenario() {
  const body = {
    title: $('#fTitle').value.trim(), question: $('#fQuestion').value.trim(), anchor_date: $('#fAnchor').value, horizon_date: $('#fHorizon').value,
    step_days: +$('#fStep').value || 7, n_agents: +$('#fAgents').value || 6,
    wiki_titles: $('#fTitles').value.split(',').map(s => s.trim()).filter(Boolean),
    user_docs: $('#fDocs').value.trim() ? [{title: 'Seed document', text: $('#fDocs').value}] : [],
  };
  if (!body.title || !body.anchor_date || !body.horizon_date) return alert('Title, anchor and horizon are required.');
  const btn = $('#createScenarioBtn'); btn.disabled = true;
  try {
    const sc = await api('/api/scenarios', 'POST', body);
    closeModal('newScenarioModal');
    await loadScenarios();
    await selectScenario(sc.id);
    $('#console').classList.remove('collapsed');
    schedulePoll();
  } catch (e) { alert(e.message); } finally { btn.disabled = false; }
}

function openProvider() {
  const c = state.config, s = c.settings;
  const sel = $('#pPreset');
  sel.innerHTML = c.presets.map(p => `<option value="${p.key}">${esc(p.label)}${p.free_tier ? ' · free' : ''}</option>`).join('') + '<option value="custom">Custom OpenAI-compatible</option>';
  sel.value = c.presets.some(p => p.key === s.provider) ? s.provider : 'custom';
  $('#pBase').value = s.base_url; $('#pModel').value = s.model; $('#pConc').value = s.concurrency; $('#pRpm').value = s.rpm; $('#pRounds').value = c.max_rounds; $('#pKey').value = '';
  const notes = () => {
    const p = c.presets.find(x => x.key === sel.value);
    $('#pNotes').innerHTML = p ? `${esc(p.notes)} ${p.signup ? `<a href="${p.signup}" target="_blank" rel="noopener">get a key ↗</a>` : ''} ${p.key_env ? `· env <code>${p.key_env}</code>` : ''}` : 'Any endpoint that speaks /chat/completions.';
  };
  sel.onchange = () => { const p = c.presets.find(x => x.key === sel.value); if (p) { $('#pBase').value = p.base_url; $('#pModel').value = p.default_model; } notes(); };
  notes();
  $('#pResult').textContent = '';
  openModal('providerModal');
}

async function saveProvider() {
  const body = {provider: $('#pPreset').value === 'custom' ? '' : $('#pPreset').value, api_key: $('#pKey').value, base_url: $('#pBase').value, model: $('#pModel').value,
    concurrency: +$('#pConc').value || undefined, rpm: $('#pRpm').value === '' ? undefined : +$('#pRpm').value, max_rounds: +$('#pRounds').value || undefined};
  if (body.provider === '' && !body.base_url) return alert('Base URL required for a custom provider.');
  $('#pResult').textContent = 'testing…';
  try {
    const r = await api('/api/config', 'POST', body);
    $('#pResult').innerHTML = r.ping.ok ? `<span style="color:var(--ok)">✓ connected: ${esc(r.ping.detail)}</span>` : `<span style="color:var(--bad)">✗ ${esc(r.ping.detail)}</span>`;
    await loadConfig();
  } catch (e) { $('#pResult').innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
}

async function listModels() {
  $('#pResult').textContent = 'fetching models…';
  // apply provider/base/key first (without saving model) so the listing hits the right endpoint
  try {
    await api('/api/config', 'POST', {provider: $('#pPreset').value === 'custom' ? '' : $('#pPreset').value, api_key: $('#pKey').value, base_url: $('#pBase').value, model: $('#pModel').value});
    const r = await api('/api/models');
    $('#modelList').innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join('');
    $('#pResult').textContent = r.models.length ? `${r.models.length} models — start typing in the model box` : (r.error || 'no models listed');
  } catch (e) { $('#pResult').textContent = e.message; }
}

async function runDiag() {
  const out = $('#pResult');
  out.innerHTML = '<span class="muted">probing Wikipedia, GDELT and the model…</span>';
  try {
    const d = await api('/api/diag');
    const row = (k, v) => `<div><span style="color:${v.ok ? 'var(--ok)' : 'var(--bad)'}">${v.ok ? '✓' : '✗'}</span> <b>${esc(k)}</b> <span class="muted">${esc(typeof v.detail === 'string' ? v.detail : JSON.stringify(v.detail))}</span></div>`;
    out.innerHTML = ['wikipedia_search', 'wikipedia_asof', 'wikipedia_latest', 'gdelt', 'llm'].filter(k => d[k]).map(k => row(k, d[k])).join('')
      + `<div class="muted" style="margin-top:4px">user-agent: ${esc(d.user_agent)}</div>`;
    toast('diagnostics: ' + (d.ok ? 'all sources OK' : 'some sources failing — see provider dialog'), d.ok ? 'info' : 'warning');
  } catch (e) { out.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
}

// ------------------------------------------------------------------ ui bindings
function bindUI() {
  $('#scenarioSelect').onchange = e => selectScenario(e.target.value);
  $('#newScenarioBtn').onclick = openNewScenario;
  $('#createScenarioBtn').onclick = createScenario;
  $('#providerChip').onclick = openProvider;
  $('#pSave').onclick = saveProvider;
  $('#pListModels').onclick = e => { e.preventDefault(); listModels(); };
  $('#pDiag').onclick = runDiag;
  $('#helpBtn').onclick = () => openModal('helpModal');
  $('#consoleToggle').onclick = () => { const c = $('#console'); c.classList.toggle('collapsed'); $('#consoleToggle').textContent = c.classList.contains('collapsed') ? 'show' : 'hide'; };
  $$('.modal').forEach(m => m.addEventListener('click', e => { if (e.target === m) m.hidden = true; }));
  const scroll = $('#timelineScroll');
  scroll.addEventListener('scroll', () => { $$('.laneLabel').forEach((el, i) => el.style.top = (AXIS_H + i * LH - scroll.scrollTop) + 'px'); });
  scroll.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (!state.sc) return;
    const rect = scroll.getBoundingClientRect();
    const mx = e.clientX - rect.left + scroll.scrollLeft;
    const dayAt = (mx - PADL) / state.pxPerDay;
    state.pxPerDay = Math.max(0.2, Math.min(80, state.pxPerDay * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
    renderTimeline();
    scroll.scrollLeft = dayAt * state.pxPerDay + PADL - (e.clientX - rect.left);
  }, {passive: false});
  window.addEventListener('resize', () => { if (state.sc && !state.pxPerDayUser) renderTimeline(); });
  if (!state.scenarios.length) setTimeout(() => { if (!localStorage.getItem('whatif.seenHelp')) { openModal('helpModal'); localStorage.setItem('whatif.seenHelp', '1'); } }, 400);
}

boot();
