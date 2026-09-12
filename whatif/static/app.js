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
  sse: null, pollTimer: null, chat: {}, lastSeq: 0,
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
  $('#providerLabel').textContent = `${s.provider} · ${s.model}${s.strong_model ? ' + ' + s.strong_model : ''}`;
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
  return Object.values(state.sc.branches).some(b => ['running', 'retrieving', 'pending'].includes(b.status))
    || (state.sc.personas || []).some(p => p.dossier_status === 'researching')
    || (state.sc.plans || []).some(pl => ['proposing', 'running'].includes(pl.status));
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    if (isActive()) { await refreshScenario(); await loadConfig(); }
    schedulePoll();
  }, isActive() ? 2500 : 8000);
}

// ------------------------------------------------------------------ SSE console
function handleBusMessage(m) {
    if (m.seq != null) { if (m.seq <= state.lastSeq) return; state.lastSeq = m.seq; }
    if (m.type === 'log') logLine(m);
    else if (m.type === 'agent_actions') {
      const b = state.sc?.branches?.[m.branch_id];
      logLine({ts: m.ts, level: 'agent', msg: `[${b ? b.name : m.branch_id}] ${m.date} · ` + m.actions.map(a => `${a.name}: ${a.action}`).join('  ‖  ')});
    } else if (m.type === 'llm_call') logLine({ts: m.ts, level: 'llm', msg: `llm ${m.kind || ''} ${m.model} ${m.ms}ms ${m.tokens ? m.tokens + ' tok' : ''}`});
    else if (m.type === 'scenario_ready' || m.type === 'branch_status' || m.type === 'branch_progress' || m.type === 'dossier' || m.type === 'plan_status') {
      if (state.sc && m.scenario_id === state.sc.id) refreshScenario();
      if (m.type === 'scenario_ready') loadScenarios();
    }
}

function connectSSE() {
  state.lastSeq = 0;
  try {
    const es = new EventSource('/api/events');
    state.sse = es;
    es.onmessage = ev => handleBusMessage(JSON.parse(ev.data));
    es.onerror = () => { /* browser retries; polling covers the gap */ };
  } catch (e) { /* no EventSource */ }
  pollLog();
}

async function pollLog() {
  try {
    const r = await api(`/api/log?after=${state.lastSeq}`);
    r.items.forEach(handleBusMessage);
  } catch (e) { /* server busy/restarting */ }
  setTimeout(pollLog, isActive() ? 2000 : 6000);
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
  const laneSel = state.sel?.type === 'branch' ? sc.branches[state.sel.id] : null;
  const castList = laneSel ? [...sc.personas.filter(p => !(laneSel.retired || []).includes(p.name)), ...(laneSel.extra_personas || [])] : sc.personas;
  $('#agentCount').textContent = castList.length + (laneSel ? ' on lane' : '');
  const plans = sc.plans || [];
  $('#plansPanel').hidden = !plans.length;
  $('#planCount').textContent = plans.length;
  $('#planList').innerHTML = plans.map(pl => `<div class="item ${state.sel?.type === 'plan' && state.sel.id === pl.id ? 'active' : ''}" onclick="showPlan('${pl.id}')"><div class="t"><span class="name">${esc(pl.target)}</span><span class="spacer"></span><span class="status ${pl.status === 'completed' ? 'completed' : pl.status === 'failed' ? 'failed' : 'running'}">${pl.status}</span></div><div class="sub" style="margin-left:0">by ${pl.deadline} · ${pl.k} candidates × ${pl.runs} runs</div></div>`).join('');
  const dstat = p => p.dossier_status === 'researching' ? '<span class="status running">researching…</span>' : p.dossier_status === 'done' ? `<span class="status completed" title="evidence-backed dossier">${(p.dossier?.sources || []).length} src</span>` : p.dossier_status === 'failed' ? '<span class="status failed">no dossier</span>' : '';
  const stateTag = p => { const st = laneSel?.agent_state?.[p.name]; return st ? `<span class="muted" title="capital / credibility / pressure"> · cap ${Number(st.capital).toFixed(2)} · pr ${Number(st.pressure).toFixed(2)}</span>` : ''; };
  const entered = new Set((laneSel?.extra_personas || []).map(p => p.id));
  al.innerHTML = castList.map(p => `<div class="item ${state.sel?.type === 'persona' && state.sel.id === p.id ? 'active' : ''}" onclick="showPersona('${p.id}')"><div class="t"><span class="name">${entered.has(p.id) ? '＋ ' : ''}${esc(p.name)}</span><span class="spacer"></span>${dstat(p)}</div><div class="sub" style="margin-left:0">${esc(laneSel?.agent_state?.[p.name]?.office || p.role)}${stateTag(p)}</div></div>`).join('') || '<div class="muted small">casting…</div>';
  if (laneSel && (laneSel.retired || []).length) al.insertAdjacentHTML('beforeend', `<div class="muted small" style="padding:4px 8px">left the stage: ${esc(laneSel.retired.join(', '))}</div>`);
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
        const core = e.kind === 'juncture' ? `<rect class="core" x="${-r}" y="${-r}" width="${2 * r}" height="${2 * r}" transform="rotate(45)" fill="${fill}" fill-opacity="${op}"/>`
          : e.kind === 'exogenous' ? `<rect class="core" x="${-r}" y="${-r}" width="${2 * r}" height="${2 * r}" rx="2" fill="#94a3b8" fill-opacity="${op}" stroke="${b.color}"/>`
          : `<circle class="core" r="${r}" fill="${fill}" fill-opacity="${op}"/>`;
        g += `<g class="ev ${e.kind} ${sel}" data-id="${e.id}" data-b="${b.id}" transform="translate(${X(e.date)},${y + off})">
          <circle class="halo" r="${r + 6}"/>
          ${e.divergence > 0.05 ? `<circle class="ring" r="${r + 3.5}" stroke-opacity="${e.divergence}" stroke-width="${1 + e.divergence * 2}"/>` : ''}
          ${core}
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
      <label>Analyst notes for this branch (optional — priors about how specific actors will behave)<textarea id="forkNotes" rows="2" placeholder="e.g. Altman will not go quietly: expect him to take staff and funders with him within days."></textarea></label>
      <div class="grid2">
        <label>Branch name (optional)<input id="forkName"></label>
        <label>Rounds <span id="forkRoundsHint" class="muted"></span><input id="forkRounds" type="number" min="1" max="60" value="${suggestRounds(sc, e.date)}" oninput="updateRoundsHint('${e.date}')"></label>
        <label>Independent runs (Monte Carlo)<select id="forkRuns"><option value="1">1 — single story</option><option value="3">3</option><option value="5">5</option><option value="8">8</option></select></label>
        <label>Seed (optional, for reproducibility)<input id="forkSeed" type="number" placeholder="random"></label>
      </div>
      <div class="row end"><span class="muted small" id="forkCost">${estimateCalls(sc, e.date)} LLM calls</span><button class="primary" onclick="doFork('${b.id}','${e.id}')">⑂ Fork timeline</button></div>
    </div>` : ''}
    ${acts.length ? `<div class="dsec"><h4>What the swarm did this period</h4>${acts.map(a => `<div class="agentAct">
        <div class="who">${esc(a.name)} <span>· ${esc(a.role)}</span></div>
        <div>${esc(a.action)}</div>
        ${a.statement ? `<div class="say">${esc(a.statement)}</div>` : ''}
        ${(a.messages || []).length ? a.messages.map(m => `<div class="small" style="color:var(--warn)">✉ to ${esc(m.to)}: ${esc(m.text)}</div>`).join('') : ''}
        ${a.thoughts ? `<details><summary>private reasoning</summary>${esc(a.thoughts)}${a.predicted_next ? `<br><em>expects:</em> ${esc(a.predicted_next)}` : ''}</details>` : ''}
      </div>`).join('')}</div>` : ''}
    ${e.sources?.length ? `<div class="dsec"><h4>Sources</h4>${e.sources.map(s => `<div class="small">${esc(s)}</div>`).join('')}</div>` : ''}
  `;
  if (canFork) updateRoundsHint(e.date);
}

function examplePremise(sc, e) {
  const a = (e.actors && e.actors[0]) || 'the key actor';
  return `${a} does the opposite: ${e.headline.toLowerCase()} never happens`;
}

function suggestRounds(sc, fromDate) {
  const days = daysBetween(fromDate, sc.horizon_date), cap = state.config?.max_rounds || 12;
  const natural = Math.max(1, Math.ceil(days / sc.step_days));
  if (days > 365 * 3) return Math.max(cap, 24);
  if (days > 365) return Math.max(cap, 16);
  return Math.min(cap, natural);
}
function updateRoundsHint(fromDate) {
  const r = parseInt($('#forkRounds')?.value || '0', 10) || 1;
  const days = daysBetween(fromDate, state.sc.horizon_date);
  const per = Math.ceil(days / r);
  const h = $('#forkRoundsHint'); if (h) h.textContent = `≈ ${per >= 365 ? (per / 365).toFixed(1) + ' years' : per >= 60 ? Math.round(per / 30) + ' months' : per + ' days'} per round${per > 180 ? ' ⚠ coarse' : ''}`;
  const c = $('#forkCost'); if (c) c.textContent = `${estimateCalls(state.sc, fromDate, r)} LLM calls per run`;
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
  const notes = $('#forkNotes')?.value.trim() || '';
  const runs = parseInt($('#forkRuns')?.value || '1', 10) || 1;
  const seed = $('#forkSeed')?.value || '';
  if (runs > 1 && !confirm(`Launch ${runs} independent runs? ≈${runs} × ${estimateCalls(state.sc, dateOverride || findEvent(eventId, parentId)?.date || state.sc.anchor_date, rounds).replace('≈', '')} LLM calls.`)) return;
  try {
    const res = await api(`/api/scenarios/${state.sc.id}/fork`, 'POST', {parent_branch_id: parentId, fork_event_id: eventId, premise, name, max_rounds: rounds, fork_date: dateOverride, notes, runs, seed});
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
      <label>Analyst notes for this branch (optional)<textarea id="forkNotes" rows="2"></textarea></label>
      <div class="grid2"><label>Branch name<input id="forkName"></label><label>Rounds <span id="forkRoundsHint" class="muted"></span><input id="forkRounds" type="number" min="1" max="60" value="${suggestRounds(state.sc, lastEv ? lastEv.date : minD)}"></label>
        <label>Independent runs<select id="forkRuns"><option value="1">1</option><option value="3">3</option><option value="5">5</option><option value="8">8</option></select></label><label>Seed<input id="forkSeed" type="number" placeholder="random"></label></div>
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
      ${b.notes ? `<div class="muted small" style="margin-top:4px"><b>Analyst notes:</b> ${esc(b.notes)}</div>` : ''}
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
        ${r.answer_to_question ? `<p><b>Answer (this run):</b> ${esc(r.answer_to_question)}</p>` : ''}
        ${r.dice_sensitivity ? `<p class="small"><b>Dice sensitivity:</b> ${esc(r.dice_sensitivity)}</p>` : ''}
        ${r.what_changed ? `<p class="small"><b>Mechanism:</b> ${esc(r.what_changed)}</p>` : ''}
        ${(r.key_divergences || []).length ? `<h4 style="margin-top:10px">Key divergences</h4><ul class="md">${r.key_divergences.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${(r.assumptions || []).length ? `<h4 style="margin-top:10px">Rests on these assumptions</h4><ul class="md">${r.assumptions.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${(r.signposts || []).length ? `<h4 style="margin-top:10px">Signposts</h4><ul class="md">${r.signposts.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${r.convergence_note ? `<p class="small muted">${esc(r.convergence_note)}</p>` : ''}
      </div>` : ''}
    ${b.report?.aggregate ? aggregateHTML(b.report.aggregate) : (b.run_group ? `<div class="dsec small muted">Part of a ${Object.values(sc.branches).filter(x => x.run_group === b.run_group).length}-run Monte Carlo group — the aggregate appears here when all runs finish. <button class="ghost small" onclick="runAggregate('${b.run_group}')">aggregate now</button></div>` : '')}
    ${b.critic_notes?.length ? `<div class="dsec"><details><summary class="muted small">Plausibility critic — ${b.critic_notes.length} period(s) corrected</summary>${b.critic_notes.map(n => `<div class="small" style="margin:4px 0"><span class="mono muted">${n.date}</span> ${esc((n.changes || []).join(' · '))}${n.notes ? ` <span class="muted">— ${esc(n.notes)}</span>` : ''}</div>`).join('')}</details></div>` : ''}
    ${!['running', 'retrieving', 'pending'].includes(b.status) ? planFormHTML(b) : ''}
    ${b.world_vars && Object.keys(b.world_vars).length ? `<div class="dsec"><h4>World variables (end of run)</h4><div class="kv">${Object.entries(b.world_vars).map(([k, v]) => `<b>${esc(k.replace(/_/g, ' '))}</b><span>${esc(String(v))}</span>`).join('')}</div></div>` : ''}
    ${b.report?.calibration ? calibrationHTML(b.report.calibration) : (b.parent_branch_id && b.status === 'completed' && sc.baseline_branch_id ? `<div class="dsec"><h4>Calibration</h4><div class="small muted">Score this run's junctures and events against what really happened in the same window (meaningful for plain forecasts; premise-dependent items are marked n/a).</div><button class="small" style="margin-top:6px" onclick="runCalibrate('${b.id}')">score vs. actual history</button></div>` : '')}
    ${b.hazards && Object.keys(b.hazards).length ? `<div class="dsec"><h4>Recurring hazards</h4>${Object.entries(b.hazards).map(([k, h]) => `<div class="small"><span class="tag">${esc(k)}</span> ${esc(h.question || '')} — rolled ${h.rolls}×, ${h.yes} yes, last p ${h.last_p}</div>`).join('')}</div>` : ''}
    ${b.junctures?.length ? `<div class="dsec"><h4>Junctures rolled (${b.junctures.length})</h4>${b.junctures.map(j => `<div class="agentAct"><div class="who"><span class="mono muted">${j.date}</span> ${esc(j.question)}</div><div><span class="tag">p(yes) ${j.p_yes}</span> <span class="tag">rolled ${j.roll}</span> → <b style="color:${j.outcome === 'yes' ? 'var(--ok)' : 'var(--warn)'}">${j.outcome.toUpperCase()}</b> · ${esc(j.headline)}</div>${j.base_rate_note ? `<div class="small muted">base rate: ${esc(j.base_rate_note)}</div>` : ''}</div>`).join('')}</div>` : ''}
    ${b.causal_map?.length ? `<div class="dsec"><h4>Causal map of real post-fork events</h4><div class="small muted" style="margin-bottom:6px">${esc(b.world_notes || '')}</div>${b.causal_map.map(c => `<div class="small" style="margin:4px 0"><span class="mono muted">${c.date}</span> <span class="tag" style="color:${c.verdict === 'independent' ? 'var(--ok)' : c.verdict === 'dependent' ? 'var(--bad)' : 'var(--warn)'}">${c.verdict}${c.verdict !== 'dependent' ? ' p=' + Number(c.p).toFixed(2) : ''}</span> ${esc(c.headline)}<div class="muted" style="margin-left:8px">${esc(c.rationale)}${c.interceptable_by?.length ? ' · interceptable by ' + esc(c.interceptable_by.join(', ')) : ''}${c.resolved ? ' · resolved' : ''}</div></div>`).join('')}</div>` : ''}
    ${b.structural?.length ? `<div class="dsec"><details><summary class="muted small">Structural calendar (${b.structural.length})</summary>${b.structural.map(x => `<div class="small" style="margin:3px 0"><span class="mono muted">${x.date}</span> <span class="tag">${esc(x.kind)}</span> ${esc(x.event)}${x.actor ? ' · ' + esc(x.actor) : ''} <span class="muted">${esc(x.note)}</span></div>`).join('')}</details></div>` : ''}
    ${(b.extra_personas?.length || b.retired?.length) ? `<div class="dsec"><h4>Cast changes</h4>${(b.extra_personas || []).map(p => `<div class="small">＋ <b>${esc(p.name)}</b> — ${esc(p.role)}</div>`).join('')}${(b.retired || []).map(n => `<div class="small muted">− ${esc(n)} left the stage</div>`).join('')}</div>` : ''}
    ${b.indicators?.length > 1 ? `<div class="dsec"><h4>Indicators</h4>${sparkSVG(b.indicators)}<div class="small muted"><span style="color:var(--bad)">■</span> tension <span style="color:var(--ok)">■</span> public support <span style="color:var(--warn)">■</span> economic stress</div></div>` : ''}
    ${b.world_state ? `<div class="dsec"><h4>World state at end</h4><div class="small">${esc(b.world_state)}</div></div>` : ''}
    <div class="dsec"><h4>Timeline (${b.events.length})</h4>${b.events.slice().sort((x, y) => x.date.localeCompare(y.date)).map(e => `<div class="small" style="margin:3px 0;cursor:pointer" onclick="selectEvent('${e.id}','${b.id}')"><span class="mono muted">${e.date}</span> ${esc(e.headline)}${e.divergence > .5 ? ' <span style="color:var(--bad)">◆</span>' : ''}</div>`).join('')}</div>
    ${b.events.length > 1 && sc.personas.length ? `<div class="dsec"><h4>Interview an agent on this branch</h4>
      <div class="chat" id="chatBox">${chat.map(c => `<div class="q">You → ${esc(c.who)}: ${esc(c.q)}</div><div class="a">${esc(c.a)}</div>`).join('')}</div>
      <div class="row"><select id="ivWho" style="width:45%">${[...sc.personas, ...(b.extra_personas || [])].map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select><input id="ivQ" placeholder="Ask…" style="flex:1" onkeydown="if(event.key==='Enter')doInterview('${b.id}')"><button onclick="doInterview('${b.id}')">ask</button></div>
    </div>` : ''}
    ${others.length ? `<div class="dsec"><h4>Compare with…</h4><div class="row"><select id="cmpWith" style="flex:1">${others.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select><button onclick="doCompare('${b.id}')">compare</button></div><div id="cmpOut" class="md"></div></div>` : ''}
    ${b.briefing ? `<div class="dsec"><details><summary class="muted small">Briefing the agents received (cutoff ${b.knowledge_cutoff})</summary>
        ${(b.briefing_flags || []).map(f => `<div class="flag">⚠ ${esc(f)}</div>`).join('')}
        <div class="md small">${md(b.briefing)}</div></details></div>` : ''}
  `;
}

function aggregateHTML(a) {
  const qs = a.outcome_questions || Object.keys(a.frequencies || {});
  return `<div class="dsec"><h4>Monte Carlo · ${a.n_runs} runs</h4>
    ${qs.map(q => { const f = (a.frequencies || {})[q] || {}; const n = (f.yes || 0) + (f.no || 0) + (f.partial || 0) || a.n_runs;
      return `<div style="margin:6px 0"><div class="small">${esc(q)}</div><div class="meter" style="height:10px;display:flex"><i style="width:${100 * (f.yes || 0) / n}%;background:var(--ok)" title="yes ${f.yes || 0}"></i><i style="width:${100 * (f.partial || 0) / n}%;background:var(--warn)" title="partial ${f.partial || 0}"></i><i style="width:${100 * (f.no || 0) / n}%;background:var(--bad)" title="no ${f.no || 0}"></i></div><div class="small muted">yes ${f.yes || 0} · partial ${f.partial || 0} · no ${f.no || 0}</div></div>`; }).join('')}
    <div class="md">${md(a.summary || '')}</div>
    ${(a.decisive_junctures || []).length ? `<h4 style="margin-top:8px">Decisive junctures</h4><ul class="md">${a.decisive_junctures.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    ${(a.per_run || []).length ? `<details><summary class="muted small">per run</summary>${a.per_run.map(r => `<div class="small" style="margin:3px 0"><b>${esc(r.run)}</b> — ${esc(r.one_line)} <span class="muted">${esc(Object.entries(r.answers || {}).map(([k, v]) => v).join(' / '))}</span></div>`).join('')}</details>` : ''}
  </div>`;
}

function planFormHTML(b) {
  const sc = state.sc;
  return `<div class="dsec forkForm"><h4>Plan an intervention (time-traveller mode)</h4>
    <div class="small muted">Give a target outcome and a deadline. The planner proposes minimal interventions on this lane, runs each as a Monte-Carlo group to the deadline, scores them against the target and writes a decision memo. Cost ≈ candidates × runs × (rounds × (agents+2)) calls.</div>
    <label>Target outcome<textarea id="planTarget" rows="2" placeholder="e.g. No mass-casualty terrorist attack on US soil before 2002-01-01"></textarea></label>
    <div class="grid2">
      <label>Deadline<input id="planDeadline" type="date" min="${b.fork_date || sc.anchor_date}" max="${sc.horizon_date}" value="${sc.horizon_date}"></label>
      <label>Window start<input id="planStart" type="date" min="${b.fork_date || sc.anchor_date}" max="${sc.horizon_date}" value="${b.fork_date || sc.anchor_date}"></label>
      <label>Candidates<select id="planK"><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
      <label>Runs per candidate<select id="planRuns"><option>1</option><option>2</option><option selected>3</option><option>5</option></select></label>
      <label>Rounds per run<input id="planRounds" type="number" min="3" max="40" value="10"></label>
    </div>
    <label>Notes to the planner (optional)<textarea id="planNotes" rows="2"></textarea></label>
    <div class="row end"><button class="primary" onclick="doPlan('${b.id}')">⑂ Search interventions</button></div></div>`;
}

async function doPlan(bid) {
  const body = {parent_branch_id: bid, target: $('#planTarget').value.trim(), deadline: $('#planDeadline').value, start: $('#planStart').value,
    k: +$('#planK').value, runs: +$('#planRuns').value, rounds: +$('#planRounds').value, notes: $('#planNotes').value.trim()};
  if (!body.target || !body.deadline) return alert('Target and deadline are required.');
  const est = body.k * body.runs * (body.rounds * ((state.sc.personas.length || 6) + 2) + 4);
  if (!confirm(`Launch ${body.k} candidates × ${body.runs} runs? ≈${est} LLM calls.`)) return;
  try { const pl = await api(`/api/scenarios/${state.sc.id}/plan`, 'POST', body); await refreshScenario(); showPlan(pl.id); }
  catch (e) { alert(e.message); }
}

function showPlan(pid) {
  const pl = (state.sc.plans || []).find(x => x.id === pid); if (!pl) return;
  state.sel = {type: 'plan', id: pid};
  const r = pl.report || {};
  const cands = pl.candidates || [];
  $('#detailBody').innerHTML = `<div class="dhead"><div class="kicker">intervention plan · <span class="status ${pl.status === 'completed' ? 'completed' : 'running'}">${pl.status}</span></div><h2>${esc(pl.target)}</h2><div class="muted">by ${pl.deadline} · window from ${pl.start} · ${pl.k} candidates × ${pl.runs} runs</div>${pl.error ? `<div class="errbox">${esc(pl.error)}</div>` : ''}</div>
    ${r.recommendation ? `<div class="dsec"><h4>Recommendation</h4><div class="md">${md(r.recommendation)}</div>${(r.caveats || []).length ? `<ul class="md small">${r.caveats.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>` : ''}
    ${(r.ranking || []).length ? `<div class="dsec"><h4>Ranking</h4>${r.ranking.map((x, i) => `<div class="agentAct"><div class="who">${i + 1}. ${esc(x.name)} <span>· success ${Math.round((x.success_rate || 0) * 100)}% · footprint ${x.footprint} · plausibility ${Math.round((x.prior_plausibility || 0) * 100)}%</span></div><div class="small">${esc(x.why || '')}</div></div>`).join('')}</div>` : ''}
    <div class="dsec"><h4>Candidates</h4>${cands.map(c => `<div class="agentAct"><div class="who">${esc(c.name)} <span>· ${c.date} · footprint ${c.footprint} · prior ${Math.round(c.prior_plausibility * 100)}%${c.success_rate != null ? ' · <b>success ' + Math.round(c.success_rate * 100) + '%</b>' : ''}</span></div>
        <div>${esc(c.premise)}</div><div class="small muted">${esc(c.mechanism)}</div>${c.risks ? `<div class="small muted">risks: ${esc(c.risks)}</div>` : ''}
        ${c.aggregate?.summary ? `<details><summary class="small muted">runs</summary><div class="md small">${md(c.aggregate.summary)}</div>${(c.aggregate.per_run || []).map(rr => `<div class="small">• ${esc(rr.run)}: ${esc(rr.one_line)}</div>`).join('')}</details>` : ''}
        ${c.group ? `<div class="small"><a href="#" onclick="event.preventDefault();selectPlanLane('${c.group}')">open lanes</a></div>` : ''}</div>`).join('') || '<div class="muted small">proposing…</div>'}</div>`;
  renderSidebar();
}

function selectPlanLane(group) {
  const b = Object.values(state.sc.branches).find(x => x.run_group === group || x.id === group);
  if (b) selectBranch(b.id);
}

function calibrationHTML(c) {
  return `<div class="dsec"><h4>Calibration vs. actual history</h4>
    <div class="row"><span class="tag">Brier ${c.brier ?? 'n/a'} (${c.n_scored_junctures} junctures; 0 = perfect, 0.25 = coin flip)</span><span class="tag">event hit rate ${c.event_hit_rate ?? 'n/a'}</span><span class="tag">to ${esc(c.window_end || '')}</span></div>
    <div class="md"><p>${esc(c.summary || '')}</p></div>
    ${(c.systematic_biases || []).length ? `<ul class="md">${c.systematic_biases.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    <details><summary class="muted small">per juncture / event</summary>${(c.junctures || []).map(j => `<div class="small"><span class="tag">${esc(j.actual)}</span> p=${j.p_yes} · ${esc(j.question)} <span class="muted">${esc(j.note || '')}</span></div>`).join('')}${(c.events || []).map(e => `<div class="small"><span class="tag">${esc(e.actual)}</span> ${esc(e.headline)} <span class="muted">${esc(e.note || '')}</span></div>`).join('')}</details></div>`;
}

async function runCalibrate(bid) {
  toast('scoring against actual history…');
  try { await api(`/api/scenarios/${state.sc.id}/branches/${bid}/calibrate`, 'POST'); await refreshScenario(); }
  catch (e) { alert(e.message); }
}

async function runAggregate(group) {
  toast('aggregating runs…');
  try { await api(`/api/scenarios/${state.sc.id}/aggregate?group=${group}`); await refreshScenario(); }
  catch (e) { alert(e.message); }
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
  const p = [...state.sc.personas, ...(state.sc.branches[bid]?.extra_personas || [])].find(x => x.id === who);
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

const PERSONA_FIELDS = [['name', 'Name'], ['role', 'Role'], ['goals', 'Goals (ranked)'], ['stance', 'Stance'], ['style', 'Style under pressure'],
  ['resources', 'Levers'], ['background', 'Track record'], ['playbook', 'Playbook'], ['relationships', 'Relationships'], ['red_lines', 'Red lines']];

function findPersona(pid) {
  return state.sc.personas.find(x => x.id === pid) || Object.values(state.sc.branches).flatMap(b => b.extra_personas || []).find(x => x.id === pid);
}
function showPersona(pid, edit = false) {
  const p = pid === 'new' ? {id: 'new', name: '', role: '', goals: '', stance: '', style: '', resources: '', background: '', playbook: '', relationships: '', red_lines: ''}
    : findPersona(pid);
  if (!p) return;
  state.sel = {type: 'persona', id: pid};
  const box = $('#detailBody');
  if (edit || pid === 'new') {
    box.innerHTML = `<div class="dhead"><div class="kicker">agent · editing</div><h2>${esc(p.name || 'New actor')}</h2>
        <div class="muted small">Your edits are kept through recasts and used by every future fork.</div></div>
      <div class="dsec">${PERSONA_FIELDS.map(([k, label]) => `<label>${label}${k === 'name' || k === 'role' ? `<input data-k="${k}" value="${esc(p[k])}">` : `<textarea data-k="${k}" rows="${k === 'background' || k === 'playbook' ? 4 : 2}">${esc(p[k])}</textarea>`}</label>`).join('')}
        <div class="row end"><button class="ghost" onclick="${pid === 'new' ? 'renderDetail()' : `showPersona('${pid}')`}">cancel</button><button class="primary" onclick="savePersona('${pid}')">save</button></div></div>`;
    return;
  }
  const d = p.dossier && !p.dossier.error ? p.dossier : null;
  box.innerHTML = `<div class="dhead"><div class="kicker">agent${p.user_edited ? ' · edited by you' : ''}${p.dossier_status === 'researching' ? ' · <span class="status running">researching…</span>' : ''}</div><h2>${esc(p.name)}</h2><div class="muted">${esc(p.role)}</div>
      <div class="row" style="margin-top:8px"><button class="small" onclick="showPersona('${pid}', true)">edit</button><button class="ghost small" onclick="researchPersona('${pid}')">${d ? 're-research' : 'research'}</button><button class="ghost small danger" onclick="deletePersona('${pid}')">remove</button></div>
      ${p.dossier?.error ? `<div class="errbox" style="margin-top:8px">dossier failed: ${esc(p.dossier.error)}</div>` : ''}</div>
    <div class="dsec"><div class="kv">${PERSONA_FIELDS.slice(2).filter(([k]) => p[k]).map(([k, label]) => `<b>${label.toLowerCase()}</b><span>${esc(p[k])}</span>`).join('')}</div></div>
    ${d ? dossierHTML(d) : `<div class="dsec small muted">No evidence dossier yet${p.dossier_status === 'researching' ? ' — building now' : ' — click research'}. Edit the profile if you know this actor better than the model does.</div>`}`;
  renderSidebar();
}

function dossierHTML(d) {
  const kv = obj => obj && typeof obj === 'object' ? `<div class="kv">${Object.entries(obj).filter(([, v]) => typeof v === 'string' && v).map(([k, v]) => `<b>${esc(k.replace(/_/g, ' '))}</b><span>${esc(v)}</span>`).join('')}</div>` : '';
  const list = arr => Array.isArray(arr) && arr.length ? `<ul class="md">${arr.map(x => `<li>${esc(typeof x === 'string' ? x : JSON.stringify(x))}</li>`).join('')}</ul>` : '';
  const conf = d.confidence != null ? `<span class="tag">confidence ${Math.round(d.confidence * 100)}%</span>` : '';
  const crit = d.critic || {};
  return `
    <div class="dsec"><h4>Dossier ${conf} <span class="tag">as of ${esc(d.built_for_cutoff || '')}</span></h4>
      <div class="md"><p>${esc(d.summary || '')}</p></div>
      ${(crit.leakage || []).length || (crit.unsupported || []).length ? `<div class="flag">⚠ critic: ${esc([...(crit.leakage || []).map(x => 'leakage: ' + x), ...(crit.unsupported || []).map(x => 'unsupported: ' + x)].join(' · ')).slice(0, 600)}</div>` : ''}
      ${crit.verdict ? `<div class="small muted">${esc(crit.verdict)}</div>` : ''}</div>
    ${Array.isArray(d.precedents) && d.precedents.length ? `<div class="dsec"><h4>Precedents</h4>${d.precedents.map(p => `<div class="agentAct"><div class="who">${esc(p.when || '')} <span>· ${esc(p.situation || '')}</span></div><div>${esc(p.what_they_did || '')}</div><div class="say">→ ${esc(p.outcome || '')}</div>${p.source ? `<div class="small"><a href="${esc(p.source)}" target="_blank" rel="noopener">source</a></div>` : ''}</div>`).join('')}</div>` : ''}
    ${d.operational_code ? `<div class="dsec"><h4>Operational code</h4>${kv(d.operational_code)}</div>` : ''}
    ${d.decision_style ? `<div class="dsec"><h4>Decision style</h4>${typeof d.decision_style === 'string' ? esc(d.decision_style) : kv(d.decision_style)}</div>` : ''}
    ${d.leadership_traits ? `<div class="dsec"><h4>Leadership traits (Hermann LTA)</h4>${kv(d.leadership_traits)}</div>` : ''}
    ${(d.stated_commitments || []).length ? `<div class="dsec"><h4>On-record commitments</h4>${list(d.stated_commitments)}</div>` : ''}
    ${(d.pressure_points || []).length || (d.constraints || []).length ? `<div class="dsec"><h4>Pressure points &amp; constraints</h4>${list(d.pressure_points)}${list(d.constraints)}</div>` : ''}
    ${Array.isArray(d.relationships) && d.relationships.length ? `<div class="dsec"><h4>Relationships</h4>${list(d.relationships.map(r => typeof r === 'string' ? r : `${r.with}: ${r.nature}${r.leverage ? ' (leverage: ' + r.leverage + ')' : ''}`))}</div>` : ''}
    ${d.voice?.quotes?.length ? `<div class="dsec"><h4>In their own words</h4><div class="small muted">${esc(d.voice.style || '')}</div>${list(d.voice.quotes)}</div>` : ''}
    ${(d.gaps || []).length ? `<div class="dsec"><h4>Gaps</h4>${list(d.gaps)}</div>` : ''}
    ${(d.sources || []).length ? `<div class="dsec"><details><summary class="muted small">${d.sources.length} sources · ${(d.evidence || []).length} evidence items</summary>${d.sources.map(s => `<div class="small" style="margin:3px 0"><span class="tag">${esc(s.kind)}</span> <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a> <span class="muted">${esc(s.as_of || '')}${s.cutoff_ok === false ? ' ⚠ filtered' : ''} · ${((s.chars || 0) / 1000).toFixed(1)}k</span></div>`).join('')}
      <details style="margin-top:6px"><summary class="muted small">evidence</summary>${(d.evidence || []).map(e => `<div class="small" style="margin:3px 0"><span class="mono muted">${esc(e.date)}</span> <span class="tag">${esc(e.type)}</span> ${esc(e.claim)}${e.quote ? ` <i>"${esc(e.quote)}"</i>` : ''}</div>`).join('')}</details></details></div>` : ''}`;
}

async function researchPersona(pid) {
  await api(`/api/scenarios/${state.sc.id}/research`, 'POST', {persona_ids: [pid]});
  toast('researching ' + (state.sc.personas.find(p => p.id === pid)?.name || 'actor') + '…');
  setTimeout(refreshScenario, 800);
}

async function savePersona(pid) {
  const fields = {};
  $$('#detailBody [data-k]').forEach(el => fields[el.dataset.k] = el.value);
  if (!fields.name?.trim()) return alert('Name is required.');
  try {
    const p = await api(`/api/scenarios/${state.sc.id}/personas/${pid}`, 'PATCH', fields);
    await refreshScenario();
    showPersona(p.id);
  } catch (e) { alert(e.message); }
}

async function deletePersona(pid) {
  if (!confirm('Remove this actor from the cast? Existing lanes keep their history; future forks run without them.')) return;
  await api(`/api/scenarios/${state.sc.id}/personas/${pid}`, 'DELETE');
  state.sel = null; await refreshScenario();
}

async function recastAgents() {
  const notes = prompt('Guidance for recasting (who is missing, who to drop, how someone really behaves). Actors you edited by hand are kept.\n\nExample: "Add Satya Nadella, Greg Brockman, Emmett Shear and a spokesperson for the 700 employees. Drop the generic media actor."');
  if (notes === null) return;
  toast('recasting agents…');
  try {
    await api(`/api/scenarios/${state.sc.id}/recast`, 'POST', {notes});
    await refreshScenario();
    toast('recast complete');
  } catch (e) { alert('Recast failed: ' + e.message); }
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
    notes: $('#fNotes').value.trim(),
    user_docs: $('#fDocs').value.trim() ? [{title: 'Seed document', text: $('#fDocs').value}] : [],
  };
  const missing = [!body.title && 'Title', !body.anchor_date && 'Anchor date (must be a real calendar date)', !body.horizon_date && 'Horizon date (must be a real calendar date — e.g. April has 30 days)'].filter(Boolean);
  if (missing.length) return alert('Please fix: ' + missing.join('; '));
  if (body.horizon_date <= body.anchor_date) return alert('Horizon must be after the anchor date.');
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
  $('#pBase').value = s.base_url; $('#pModel').value = s.model; $('#pStrong').value = s.strong_model || '';
  $('#pDepth').value = s.research_depth || 'standard'; $('#pCritic').value = s.critic === false ? '0' : '1'; $('#pExa').value = ''; $('#pSerper').value = '';
  $('#pExa').placeholder = s.has_exa ? 'key set — blank keeps it' : 'no key (DuckDuckGo + Wayback fallback)';
  $('#pSerper').placeholder = s.has_serper ? 'key set — blank keeps it' : 'no key'; $('#pConc').value = s.concurrency; $('#pRpm').value = s.rpm; $('#pRounds').value = c.max_rounds; $('#pKey').value = '';
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
    strong_model: $('#pStrong').value, research_depth: $('#pDepth').value, critic: $('#pCritic').value === '1', exa_api_key: $('#pExa').value, serper_api_key: $('#pSerper').value, concurrency: +$('#pConc').value || undefined, rpm: $('#pRpm').value === '' ? undefined : +$('#pRpm').value, max_rounds: +$('#pRounds').value || undefined};
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
  $('#addAgentBtn').onclick = () => { if (state.sc) showPersona('new'); };
  $('#recastBtn').onclick = () => { if (state.sc?.baseline_branch_id) recastAgents(); };
  $('#researchBtn').onclick = async () => { if (!state.sc) return; const r = await api(`/api/scenarios/${state.sc.id}/research`, 'POST', {}); toast(r.started ? `researching ${r.started} actors…` : 'all actors already have dossiers (use re-research on an actor to rebuild)'); setTimeout(refreshScenario, 800); };
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
