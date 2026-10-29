'use strict';

const $ = (id) => document.getElementById(id);
const app = $('app');
const cloud = $('cloud');
let home = null;
let review = { items: [] };
let lastFocus = null;
let busy = false;
let suggestionIndex = 0;
// Wide screens show what you open in a side panel next to the clouds instead of a sheet over them.
const wide = window.matchMedia('(min-width: 960px)');

/* ---------- small helpers ---------- */
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, ...kids) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  kids.forEach((k) => n.append(k));
  return n;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, opts = {}) {
  const headers = {};
  if (opts.body && typeof opts.body === 'string') headers['Content-Type'] = 'application/json';
  if (opts.body instanceof Blob) headers['Content-Type'] = opts.body.type || 'application/octet-stream';
  const res = await fetch(path, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch (_) { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.detail) || `Something went wrong (${res.status}). Try again.`);
  return data;
}

let toastTimer = null;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}

function miniCloud(fill = '#BFE0FF') {
  return svgEl('svg', { width: 40, height: 28, viewBox: '0 0 240 160', 'aria-hidden': 'true' },
    svgEl('g', { fill },
      svgEl('circle', { cx: 78, cy: 88, r: 44 }), svgEl('circle', { cx: 126, cy: 62, r: 54 }),
      svgEl('circle', { cx: 176, cy: 84, r: 42 }), svgEl('circle', { cx: 44, cy: 112, r: 30 }),
      svgEl('circle', { cx: 204, cy: 112, r: 28 }), svgEl('rect', { x: 40, y: 96, width: 168, height: 46, rx: 23 })));
}
const TONE_FILL = { blush: '#FFC6E2', bubblegum: '#FF8CC6', sky: '#BFE0FF', white: '#FFFFFF', mist: '#DCEBFF' };
function cloudShape(tone) {
  const fill = TONE_FILL[tone] || '#FFFFFF';
  return svgEl('svg', { class: 'cloudbg', viewBox: '0 0 240 160', 'aria-hidden': 'true', preserveAspectRatio: 'none' },
    svgEl('g', { fill },
      svgEl('circle', { cx: 78, cy: 88, r: 44 }), svgEl('circle', { cx: 126, cy: 62, r: 54 }),
      svgEl('circle', { cx: 176, cy: 84, r: 42 }), svgEl('circle', { cx: 44, cy: 112, r: 30 }),
      svgEl('circle', { cx: 204, cy: 112, r: 28 }), svgEl('rect', { x: 40, y: 96, width: 168, height: 46, rx: 23 })),
    svgEl('ellipse', { cx: 104, cy: 34, rx: 22, ry: 9, fill: '#FFFFFF', opacity: 0.75 }));
}
function tireIcon() {
  return svgEl('svg', { viewBox: '0 0 22 22', 'aria-hidden': 'true' },
    svgEl('circle', { cx: 11, cy: 11, r: 8.5, fill: 'none', stroke: '#2D2A5A', 'stroke-width': 1.8 }),
    svgEl('circle', { cx: 11, cy: 11, r: 3.5, fill: 'none', stroke: '#2D2A5A', 'stroke-width': 1.8 }));
}
function cameraIcon() {
  return svgEl('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' },
    svgEl('rect', { x: 3, y: 7, width: 18, height: 13, rx: 3.5, fill: 'none', stroke: '#2D2A5A', 'stroke-width': 1.8 }),
    svgEl('path', { d: 'M8.5 7l1.6-2.6h3.8L15.5 7', fill: 'none', stroke: '#2D2A5A', 'stroke-width': 1.8, 'stroke-linejoin': 'round' }),
    svgEl('circle', { cx: 12, cy: 13.5, r: 3.6, fill: 'none', stroke: '#2D2A5A', 'stroke-width': 1.8 }));
}
function rows(list, hot) {
  return el('div', { class: 'rows' }, list.map(([a, b]) =>
    el('div', { class: 'row' + (hot && a === hot ? ' hot' : '') }, el('span', { text: a }), el('span', { text: b }))));
}
function title(text, cls) {
  return el('h2', { id: 'sheetTitle', tabindex: '-1', class: cls || null, text });
}

/* ---------- home: the cloud and its bubbles ---------- */
const TONES = ['tone-blush', 'tone-bubblegum', 'tone-sky', 'tone-white', 'tone-mist'];
function renderHome(h) {
  const first = home === null;
  home = h;
  $('month').textContent = h.month;
  $('headline').textContent = h.headline;
  $('sub').textContent = h.sub;
  const wrap = $('bubbles');
  const existing = new Map([...wrap.children].map((b) => [b.dataset.id, b]));
  h.bubbles.forEach((b, i) => {
    let node = existing.get(b.id);
    if (!node) {
      node = el('button', { type: 'button', class: `bubble bob-${'abc'[i % 3]}`, 'data-id': b.id });
      node.addEventListener('click', () => openBubble(b.id));
      wrap.append(node);
    }
    existing.delete(b.id);
    node.classList.remove(...TONES);
    node.classList.add(`tone-${b.tone}`);
    const kids = [cloudShape(b.tone)];
    if (b.icon === 'tire') kids.push(tireIcon());
    if (b.icon === 'camera') kids.push(cameraIcon());
    if (b.value) kids.push(el('span', { class: 'v', text: b.value }));
    kids.push(el('span', { class: 'l', text: b.label }));
    node.replaceChildren(...kids);
    const spoken = [b.value, b.label].filter(Boolean).join(' ');
    node.setAttribute('aria-label', `${spoken}${b.note ? `, ${b.note}` : ''}. Open.`);
    const sig = `${b.value}|${b.label}`;
    if (!first && node.dataset.sig && node.dataset.sig !== sig) {
      node.classList.remove('pop');
      void node.offsetWidth;
      node.classList.add('pop');
    }
    node.dataset.sig = sig;
  });
  existing.forEach((node) => node.remove());
  const s = h.suggestions || [];
  if (s.length) $('suggest').textContent = `Try saying “${s[suggestionIndex % s.length]}”`;
  const badge = $('navReview');
  badge.textContent = h.review.count ? String(h.review.count) : '';
  badge.hidden = !h.review.count;
  $('navMonth').textContent = new Date(h.today + 'T12:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}

function markOpen(node) {
  document.querySelectorAll('.bubble.open').forEach((b) => { if (b !== node) b.classList.remove('open'); });
  if (node) node.classList.add('open');
  setNav(node ? node.dataset.id : null);
}
// Open one of the month's views: from its cloud, or from the menu on a wide screen.
function openView(id) {
  const node = document.querySelector(`.bubble[data-id="${id}"]`);
  if (wide.matches) markOpen(node);   // the panel opens beside it, so it stays, lit
  else if (node) node.classList.add('leaving');
  setNav(id);
  if (id === 'review') openReview();
  else if (id === 'receipts') openReceipts();
  else if (id.startsWith('report:')) openReport(id.slice(7));
  else openDetail(id);
}
function openBubble(id) { openView(id); }

/* ---------- sheets ---------- */
function openSheet(nodes) {
  if ($('sheet').hidden) lastFocus = document.activeElement;
  const body = $('sheetBody');
  body.replaceChildren(...nodes.filter(Boolean));
  body.scrollTop = 0;
  $('scrim').hidden = wide.matches;
  document.body.classList.add('panel-open');
  const s = $('sheet');
  s.classList.remove('closing');
  s.hidden = false;
  requestAnimationFrame(() => { const t = $('sheetTitle'); if (t) t.focus({ preventScroll: true }); });
}
function closeSheet() {
  const s = $('sheet');
  if (s.hidden) return;
  s.classList.add('closing');
  document.body.classList.remove('panel-open');
  markOpen(null);
  setNav('home');
  setTimeout(() => {
    s.hidden = true;
    $('scrim').hidden = true;
    s.classList.remove('closing');
    document.querySelectorAll('.bubble.leaving').forEach((b) => b.classList.remove('leaving'));
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }, 220);
}
// Ask something from inside a sheet. On a phone the sheet gets out of the way first; on a wide
// screen the panel stays put and the answer replaces it.
function askFromSheet(text) {
  if (wide.matches) { ask(text, false); return; }
  closeSheet();
  setTimeout(() => ask(text, false), 240);
}
function sheetError(msg) {
  openSheet([title('That didn’t load'), el('p', { text: msg }),
    el('div', { class: 'actions' }, el('button', { type: 'button', class: 'btn', text: 'Close', onclick: closeSheet }))]);
}

const ASK_ABOUT = {
  profit: 'What drove my profit this month?',
  turo: 'How did Turo do this month?',
  rides: 'Which rides paid best?',
  tire: 'What did the low tire cost me?',
};
async function openDetail(id) {
  let d;
  try { d = await api(`/api/detail/${encodeURIComponent(id)}`); } catch (e) { sheetError(e.message); return; }
  const nodes = [title(d.title)];
  if (d.value) nodes.push(el('div', { class: 'big', text: d.value }));
  if (d.sub) nodes.push(el('p', { class: 'muted', text: d.sub }));
  if (d.body) nodes.push(el('p', { text: d.body }));
  if (d.series) {
    nodes.push(sparkline(d.series, d.placard));
    nodes.push(el('p', { class: 'muted', text: 'Pink is this tire, blue is the other three, dotted is the door sticker.' }));
  }
  (d.sections || []).forEach((sec, i) => {
    nodes.push(el('div', { class: 'section-title' }, el('span', { text: sec.title }), el('span', { text: sec.total || '' })));
    nodes.push(rows(sec.rows));
    if (i === 1 && d.rules && d.rules.length) {
      nodes.push(el('div', { class: 'section-title' }, el('span', { text: 'Ride rules, replayed on this month' }), el('span', { text: 'vs what you did' })));
      nodes.push(el('div', { class: 'rule-list' }, d.rules.map((r) =>
        el('button', { type: 'button', class: 'rule-row', onclick: () => askFromSheet(`What if I used this rule: ${r.label}?`) },
          el('span', { text: r.label }), el('b', { class: r.value.startsWith('+') ? 'up' : 'down', text: r.value })))));
      nodes.push(el('p', { class: 'muted', text: 'Tap a rule to see what it would have done, or say your own: “What if I skip pickups over 4 miles?”' }));
    }
  });
  if (d.note) nodes.push(el('p', { class: 'muted', text: d.note }));
  if (id === 'profit') {
    nodes.push(el('div', { class: 'section-title' }, el('span', { text: 'Reports' }), el('span', { text: '' })));
    nodes.push(el('div', { class: 'report-links' }, [['pnl', 'Profit and loss'], ['schedule_c', 'Schedule C draft'], ['mileage', 'Mileage log']]
      .map(([r, label]) => el('button', { type: 'button', class: 'rule-row', onclick: () => { setNav(`report:${r}`); openReport(r); } },
        el('span', { text: label }), el('b', { text: 'Open' })))));
  }
  if (ASK_ABOUT[id]) {
    nodes.push(el('div', { class: 'actions' }, el('button', {
      type: 'button', class: 'btn', text: `Ask: ${ASK_ABOUT[id]}`,
      onclick: () => askFromSheet(ASK_ABOUT[id]),
    })));
  }
  openSheet(nodes);
}

/* ---------- reports: P&L, Schedule C draft, mileage log (all computed in code) ---------- */
function grid(columns, list) {
  const strong = (label) => /^(Total|Net|28 |31 )/.test(label);
  return el('div', { class: 'grid' }, el('table', {},
    el('thead', {}, el('tr', {}, columns.map((c) => el('th', { scope: 'col', text: c })))),
    el('tbody', {}, list.map((r) => el('tr', { class: strong(r[0]) ? 'strong' : null },
      r.map((v, i) => (i === 0 ? el('th', { scope: 'row', text: v }) : el('td', { text: v }))))))));
}
async function openReport(name) {
  let d;
  try { d = await api(`/api/reports/${encodeURIComponent(name)}`); } catch (e) { sheetError(e.message); return; }
  const nodes = [];
  if (d.eyebrow) nodes.push(el('span', { class: 'eyebrow', text: d.eyebrow }));
  nodes.push(title(d.title));
  if (d.value) nodes.push(el('div', { class: 'big', text: d.value }));
  if (d.sub) nodes.push(el('p', { class: 'muted', text: d.sub }));
  if (d.status) nodes.push(el('span', { class: 'pill' + (d.status.final ? ' final' : ''), text: d.status.text }));
  (d.sections || []).forEach((sec) => {
    nodes.push(el('div', { class: 'section-title' }, el('span', { text: sec.title }), el('span', { text: sec.total || '' })));
    nodes.push(sec.columns ? grid(sec.columns, sec.rows) : rows(sec.rows));
  });
  if (d.note) nodes.push(el('p', { class: 'muted', text: d.note }));
  nodes.push(el('div', { class: 'actions' },
    el('a', { class: 'btn primary link-btn', href: `/api/reports/${encodeURIComponent(name)}/download`, download: '', text: 'Download as CSV' }),
    el('span', { class: 'muted', style: 'text-align:center', text: 'Opens in Excel, Numbers or Google Sheets' })));
  openSheet(nodes);
}

function sparkline(series, placard) {
  const W = 340, H = 130, P = 10, B = 22;
  const vals = series.flatMap((s) => [s.psi, s.others]).concat([placard]);
  const lo = Math.floor(Math.min(...vals)) - 1, hi = Math.ceil(Math.max(...vals)) + 1;
  const x = (i) => P + (i * (W - 2 * P)) / Math.max(1, series.length - 1);
  const y = (v) => P + ((hi - v) * (H - P - B)) / (hi - lo);
  const pts = (key) => series.map((p, i) => `${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ');
  const low = Math.min(...series.map((p) => p.psi));
  const fmt = (iso) => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'spark', role: 'img', 'aria-label': `Tire pressure for the month. Lowest ${low} psi.` },
    svgEl('line', { x1: P, x2: W - P, y1: y(placard), y2: y(placard), stroke: '#9DBFE6', 'stroke-width': 1.5, 'stroke-dasharray': '3 5' }),
    svgEl('polyline', { points: pts('others'), fill: 'none', stroke: '#8EC5FF', 'stroke-width': 2.5, 'stroke-linejoin': 'round' }),
    svgEl('polyline', { points: pts('psi'), fill: 'none', stroke: '#FF6FAE', 'stroke-width': 3.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }),
    svgEl('text', { x: P, y: H - 4, 'font-size': 12, fill: '#5C5A86' }, document.createTextNode(fmt(series[0].date))),
    svgEl('text', { x: W - P, y: H - 4, 'font-size': 12, fill: '#5C5A86', 'text-anchor': 'end' }, document.createTextNode(fmt(series[series.length - 1].date))));
}

/* ---------- review: one card at a time ---------- */
async function openReview() {
  try { review = await api('/api/review'); } catch (e) { sheetError(e.message); return; }
  review.rules = review.rules || [];
  openSheet(reviewNodes());
}
function reviewNodes() {
  const items = review.items;
  const pending = items.filter((i) => !i.decision);
  if (!pending.length) return doneNodes();
  const it = pending[0];
  const pos = items.length - pending.length + 1;
  const progress = el('span', { class: 'pill strong' },
    el('span', { class: 'progress', 'aria-hidden': 'true' }, items.map((_, i) => el('span', { class: i < pos ? 'on' : null }))),
    el('span', { text: `${pos} of ${items.length}` }));

  const explainBox = el('div', { class: 'explain', hidden: true });
  const choicesBox = el('div', { class: 'choices', hidden: true });
  const suggested = it.suggestion ? it.suggestion.choice : null;
  for (const c of it.choices.filter((c) => c.choice !== suggested)) {
    if (c.choice === 'amount') {
      const input = el('input', { type: 'text', inputmode: 'decimal', placeholder: 'Amount, like 9.99', 'aria-label': 'New amount' });
      choicesBox.append(el('div', { class: 'choice-row' }, input,
        el('button', { type: 'button', class: 'btn', style: 'flex:0 0 auto', text: 'Save', onclick: () => decide(it, 'amount', input.value) })));
    } else {
      choicesBox.append(el('button', { type: 'button', class: 'btn', text: c.label, onclick: () => decide(it, c.choice) }));
    }
  }
  const evidence = [];
  if (it.evidence && it.evidence.receipt) {
    const src = `/evidence/${it.evidence.receipt}`;
    evidence.push(el('button', { type: 'button', class: 'receipt-thumb', onclick: () => viewImage(src) },
      el('img', { src, alt: '' }), 'See the receipt'));
  }
  if (it.evidence && it.evidence.voice) {
    evidence.push(el('button', { type: 'button', class: 'receipt-thumb', onclick: () => new Audio(`/evidence/${it.evidence.voice}`).play() },
      miniCloud('#FFC6E2'), 'Play the voice note'));
  }
  const card = el('div', { class: 'card-in', style: 'display:flex;flex-direction:column;gap:14px' },
    el('span', { class: 'pill', text: it.tag }), title(it.title), el('p', { text: it.body }),
    it.basis ? el('p', { class: 'muted basis', text: it.basis }) : null,
    ...evidence, it.rows.length ? rows(it.rows) : null,
    el('div', { class: 'suggestion' }, miniCloud(),
      el('span', {}, el('b', { text: it.suggestion ? it.suggestion.label : 'The records can’t tell. Which was it?' }),
        el('span', { class: 'muted', text: it.found_by }))),
    explainBox, it.suggestion ? choicesBox : null);

  const explainBtn = el('button', { type: 'button', class: 'btn', text: 'Explain' });
  explainBtn.addEventListener('click', async () => {
    explainBtn.disabled = true;
    explainBtn.textContent = 'Explaining…';
    try {
      const r = await api(`/api/review/${encodeURIComponent(it.id)}/explain`, { method: 'POST' });
      explainBox.replaceChildren(el('span', { text: r.text }), el('div', { class: 'muted', style: 'margin-top:6px', text: 'Explained by Muse Spark' }));
      explainBox.hidden = false;
    } catch (e) { toast(e.message); }
    explainBtn.disabled = false;
    explainBtn.textContent = 'Explain';
  });
  const actions = it.suggestion
    ? el('div', { class: 'actions' },
      el('button', { type: 'button', class: 'btn primary', text: 'Approve', onclick: () => decide(it, it.suggestion.choice) }),
      el('div', { class: 'two' },
        el('button', { type: 'button', class: 'btn', text: 'Change', onclick: () => { choicesBox.hidden = !choicesBox.hidden; } }),
        explainBtn))
    : el('div', { class: 'actions' },
      el('div', { class: 'two' }, it.choices.map((c) =>
        el('button', { type: 'button', class: 'btn tall', text: c.label, onclick: () => decide(it, c.choice) }))),
      explainBtn);
  return [progress, card, actions];
}

async function decide(it, choice, amount) {
  const body = { choice };
  if (choice === 'amount') {
    const n = parseFloat(String(amount || '').replace(/[$,\s]/g, ''));
    if (!(n > 0)) { toast('Type the amount, like 9.99.'); return; }
    body.amount = n;
  }
  try {
    const r = await api(`/api/review/${encodeURIComponent(it.id)}`, { method: 'POST', body: JSON.stringify(body) });
    review.items = r.items;
    review.rules = r.rules || [];
    renderHome(r.home);
    const picked = it.choices.find((c) => c.choice === choice);
    if (r.learned) toast(r.learned);   // a correction became a rule
    else if (it.suggestion && choice === it.suggestion.choice) toast('Approved');
    else if (choice === 'amount') toast(`Saved $${body.amount.toFixed(2)}`);
    else toast(`Saved: ${picked ? picked.label : 'changed'}`);
    openSheet(reviewNodes());
  } catch (e) { toast(e.message); }
}

function doneNodes() {
  const list = el('div', { class: 'done-list' }, review.items.map((it) => {
    const chosen = (it.decision || {}).choice;
    const c = it.choices.find((c) => c.choice === chosen);
    const said = chosen === 'amount' ? 'Amount changed' : (c ? c.label : '');
    return el('div', { class: 'done-item' },
      el('span', {}, el('b', { text: it.title }), el('div', { class: 'muted', text: said })),
      el('button', { type: 'button', text: 'Undo', onclick: () => undoDecision(it) }));
  }));
  const learned = (review.rules || []).length ? [
    el('div', { class: 'section-title' }, el('span', { text: 'What your answers taught the books' })),
    rows(review.rules.map((r) => [r.name, r.says === 'personal' ? 'Personal, every time' : 'Business, every time'])),
    el('p', { class: 'muted', text: 'Next month these are set before the categorizer sees them.' }),
  ] : [];
  return [title(review.items.length ? 'All reviewed' : 'Nothing to review'),
    el('p', { text: review.items.length ? 'Each answer is saved with the receipt or note behind it.' : 'Every receipt and note checks out.' }),
    list, ...learned,
    el('div', { class: 'actions' }, el('button', { type: 'button', class: 'btn primary', text: 'Back to the cloud', onclick: closeSheet }))];
}
async function undoDecision(it) {
  try {
    const r = await api(`/api/review/${encodeURIComponent(it.id)}`, { method: 'DELETE' });
    review.items = r.items;
    review.rules = r.rules || [];
    renderHome(r.home);
    openSheet(reviewNodes());
  } catch (e) { toast(e.message); }
}

/* ---------- receipts: one photo, every slip found (SAM 3.1), read (Muse Spark), checked (code) ---------- */
const photoInput = el('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true });
document.body.append(photoInput);
photoInput.addEventListener('change', () => {
  const f = photoInput.files && photoInput.files[0];
  photoInput.value = '';
  if (f) snapPile(f);
});

async function openReceipts() {
  let r;
  try { r = await api('/api/receipts'); } catch (e) { sheetError(e.message); return; }
  openSheet(r.pile && r.pile.read ? pileNodes(r.pile, 'done') : startNodes(r.sample));
}
function startNodes(sample) {
  // On a laptop there's rarely a pile of receipts to hand, so the sample photo leads there.
  const sampleFirst = wide.matches && sample;
  const own = el('button', { type: 'button', class: sampleFirst ? 'btn' : 'btn primary',
    text: wide.matches ? 'Upload a photo' : 'Take a photo', onclick: () => photoInput.click() });
  const demo = sample ? el('button', { type: 'button', class: sampleFirst ? 'btn primary' : 'btn',
    text: 'Use the sample photo', onclick: () => snapPile(null) }) : null;
  return [title('Snap your receipts'),
    el('p', { text: 'Lay the month’s receipts out and take one photo. SAM 3.1 finds each receipt, Muse Spark reads it, and your books check it against your cards.' }),
    el('div', { class: 'actions' }, sampleFirst ? [demo, own] : [own, demo])];
}
function pileFigure(pile, src, stage) {
  const fig = el('div', { class: `pile ${stage}` }, el('img', { src: src || pile.photo, alt: 'Your receipts photo' }));
  (pile ? pile.items : []).forEach((it, i) => {
    const b = it.box;
    const box = el('div', {
      class: 'box' + (it.amount ? ' read' : '') + (it.flag ? ' flagged' : ''),
      style: `left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%;animation-delay:${i * 120}ms`,
    });
    if (it.amount) box.append(el('span', { class: 'tag', text: it.flag ? `${it.amount} · ${it.flag.toLowerCase()}` : `${it.amount} ✓` }));
    fig.append(box);
  });
  return fig;
}
function statusLine(model, text, working) {
  return el('div', { class: 'status-line' }, el('span', { class: 'pill strong', text: model }), el('span', { text }),
    working ? el('span', { class: 'dots', 'aria-label': 'Working' }, el('span'), el('span'), el('span')) : null);
}
function pileNodes(pile, stage, src) {
  if (stage === 'finding') {
    return [title('Finding your receipts'), pileFigure(null, src, stage), statusLine('SAM 3.1', 'is finding every receipt', true)];
  }
  if (stage === 'reading') {
    return [title(`Found ${pile.found} receipt${pile.found === 1 ? '' : 's'}`), pileFigure(pile, src, stage),
      statusLine('SAM 3.1', `found them in ${pile.seconds.find}s`), statusLine('Muse Spark', 'is reading each one', true)];
  }
  const nodes = [title(`${pile.found} receipt${pile.found === 1 ? '' : 's'} read`), pileFigure(pile, src, stage),
    el('p', { text: `${pile.matched} of ${pile.found} matched to a card charge${pile.flagged ? `, and ${pile.flagged} need${pile.flagged === 1 ? 's' : ''} a look` : ''}.` }),
    el('div', { class: 'rows' }, pile.items.map((it) =>
      el('button', { type: 'button', class: 'row pile-row', onclick: () => { if (it.image) viewImage(it.image); } },
        el('span', {}, el('b', { text: it.title }), el('div', { class: 'muted', text: it.flag ? `${it.flag} · ${it.status}` : it.status })),
        el('span', { text: it.amount || '' })))),
    el('span', { class: 'muted', text: `Found by SAM 3.1 in ${pile.seconds.find}s, read by Muse Spark in ${pile.seconds.read}s, matched to your cards in code.` })];
  nodes.push(el('div', { class: 'actions' },
    pile.flagged
      ? el('button', { type: 'button', class: 'btn primary', text: 'Review them', onclick: () => {
        if (wide.matches) markOpen(document.querySelector('.bubble[data-id="review"]'));
        openReview();
      } })
      : el('button', { type: 'button', class: 'btn primary', text: 'Done', onclick: closeSheet }),
    el('button', { type: 'button', class: 'btn', text: wide.matches ? 'Upload a new photo' : 'Take a new photo', onclick: () => photoInput.click() })));
  return nodes;
}
let pileBusy = false;
async function snapPile(file) {
  if (pileBusy) return;
  pileBusy = true;
  const preview = file ? URL.createObjectURL(file) : '/evidence/receipt_pile.jpg';
  openSheet(pileNodes(null, 'finding', preview));
  try {
    const found = await api('/api/receipts/photo', file
      ? { method: 'POST', body: file }
      : { method: 'POST', body: JSON.stringify({ sample: true }) });
    openSheet(pileNodes(found.pile, 'reading', preview));
    const done = await api('/api/receipts/read', { method: 'POST' });
    renderHome(done.home);
    openSheet(pileNodes(done.pile, 'done'));
  } catch (e) {
    toast(e.message);
    openReceipts();
  } finally {
    pileBusy = false;
    if (file) setTimeout(() => URL.revokeObjectURL(preview), 5000);
  }
}

function viewImage(src) {
  $('viewerImg').src = src;
  $('viewerImg').alt = 'Receipt photo';
  $('viewer').hidden = false;
  $('viewerClose').focus();
}

/* ---------- asking and logging ---------- */
function showSaid(text, label) {
  $('said').hidden = false;
  const t = $('saidText');
  if (text) t.textContent = text;
  else t.replaceChildren(el('span', { class: 'dots', 'aria-label': 'Working' }, el('span'), el('span'), el('span')));
  $('saidLabel').textContent = label || '';
}
function hideSaid() { $('said').hidden = true; }

async function ask(text, spoken) {
  if (busy) return;
  busy = true;
  showSaid(text, 'Muse Spark is checking your books');
  app.classList.add('thinking');
  try {
    const r = await api('/api/ask', { method: 'POST', body: JSON.stringify({ text, spoken: !!spoken }) });
    renderHome(r.home);
    suggestionIndex += 1;
    showResult(r.heard, r.result, spoken);
  } catch (e) {
    toast(e.message);
  } finally {
    app.classList.remove('thinking');
    hideSaid();
    busy = false;
  }
}

function showResult(heard, res, spoken) {
  markOpen(null);
  const nodes = [
    el('div', { class: 'you-said', text: heard }),
    el('span', { class: 'muted', style: 'align-self:flex-end;margin-top:-10px', text: spoken ? 'Heard by Muse Voice Transcribe' : 'You asked' }),
  ];
  if (res.type === 'logged') {
    nodes.push(el('span', { class: 'pill', text: `Added to ${home ? home.month : 'this month'}` }),
      title(res.title), el('div', { class: 'big', text: res.amount }), el('p', { class: 'muted', text: res.meta }));
    if (res.assumption) nodes.push(el('div', { class: 'explain', text: res.assumption }));
    nodes.push(el('span', { class: 'muted', text: 'Booked by Muse Spark' }));
    nodes.push(el('div', { class: 'actions' }, el('div', { class: 'two' },
      el('button', { type: 'button', class: 'btn', text: 'Undo', onclick: () => undoEntry(res.entry_id) }),
      el('button', { type: 'button', class: 'btn primary', text: 'Done', onclick: closeSheet }))));
  } else if (res.type === 'question') {
    nodes.push(title(res.text), el('p', { text: 'Hold the cloud and answer, or type it.' }),
      el('div', { class: 'actions' }, el('button', { type: 'button', class: 'btn primary', text: 'Type the answer', onclick: () => { if (!wide.matches) closeSheet(); openTypebar(); } })));
  } else if (res.type === 'rule') {
    nodes.push(el('span', { class: 'pill', text: res.verdict }), title(res.headline),
      el('div', { class: 'big', text: res.amount }), el('p', { text: res.text }), rows(res.rows),
      el('p', { class: 'muted', text: res.note }),
      el('span', { class: 'muted', text: 'Rule understood by Muse Spark, replayed in code on every offer this month' }));
  } else if (res.type === 'noted') {
    nodes.push(title(res.headline, 'big'), el('p', { text: res.text }));
  } else {
    nodes.push(title(res.headline || 'Here’s what I found', 'big'), el('p', { text: res.text }));
    if (res.followups && res.followups.length) {
      nodes.push(el('div', { class: 'followups' }, res.followups.map((f) =>
        el('button', { type: 'button', text: f, onclick: () => askFromSheet(f) }))));
    }
    nodes.push(el('span', { class: 'muted', text: 'Muse Spark answered from your books' }));
  }
  openSheet(nodes);
}

async function undoEntry(id) {
  try {
    const r = await api(`/api/entries/${encodeURIComponent(id)}/undo`, { method: 'POST' });
    renderHome(r.home);
    toast('Removed');
    closeSheet();
  } catch (e) { toast(e.message); }
}

/* ---------- the microphone: record WAV at 16 kHz in the browser ---------- */
class Recorder {
  constructor() { this.active = false; }
  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const err = new Error('no microphone api');
      err.name = window.isSecureContext ? 'NotFoundError' : 'InsecureContext';
      throw err;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx();
    const resumed = this.ctx.resume();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) { this.ctx.close(); throw e; }
    await resumed;
    this.chunks = [];
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
    this.proc.onaudioprocess = (e) => { if (this.active) this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    this.source.connect(this.analyser);
    this.source.connect(this.proc);
    this.proc.connect(this.ctx.destination);
    this.active = true;
  }
  level() {
    if (!this.analyser) return 0;
    const a = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(a);
    let s = 0;
    for (const v of a) { const x = (v - 128) / 128; s += x * x; }
    return Math.sqrt(s / a.length);
  }
  _teardown() {
    this.active = false;
    try { this.proc.disconnect(); this.source.disconnect(); } catch (_) { /* already gone */ }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    const rate = this.ctx ? this.ctx.sampleRate : 48000;
    if (this.ctx) this.ctx.close();
    return rate;
  }
  cancel() { if (this.ctx) this._teardown(); this.chunks = []; }
  stop() {
    const rate = this._teardown();
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const all = new Float32Array(total);
    let o = 0;
    for (const c of this.chunks) { all.set(c, o); o += c.length; }
    this.chunks = [];
    return encodeWav(downsample(all, rate, 16000), 16000);
  }
}
function downsample(buf, from, to) {
  if (from === to) return buf;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(buf.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio), b = Math.min(buf.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += buf[j];
    out[i] = b > a ? sum / (b - a) : 0;
  }
  return out;
}
function encodeWav(samples, rate) {
  const v = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data');
  v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (const s of samples) { const x = Math.max(-1, Math.min(1, s)); v.setInt16(o, x < 0 ? x * 0x8000 : x * 0x7fff, true); o += 2; }
  return new Blob([v], { type: 'audio/wav' });
}

const rec = new Recorder();
let session = null;
let holding = false;

function micMessage(err) {
  if (err && err.name === 'InsecureContext') return 'The microphone needs a secure page (https). Type instead.';
  if (err && (err.name === 'NotAllowedError' || err.name === 'SecurityError')) return 'Microphone access is off. Allow it in the browser, or type instead.';
  return 'The microphone didn’t start. Type instead.';
}

async function startListening() {
  if (session || busy) return;
  closeTypebar();
  session = { at: performance.now(), ready: rec.start() };
  const mine = session;
  try { await mine.ready; } catch (err) { session = null; holding = false; toast(micMessage(err)); openTypebar(); return; }
  if (session !== mine) { rec.cancel(); return; }
  app.classList.add('listening');
  showSaid(null, 'Listening. Let go when you’re done.');
  cloud.setAttribute('aria-label', 'Listening. Release, or press Enter, to send.');
  const bars = [...cloud.querySelectorAll('.bars rect')];
  const shape = [0.6, 1, 1.35, 0.9, 0.55];
  const loop = () => {
    if (session !== mine) { bars.forEach((b) => { b.style.transform = ''; }); return; }
    const lv = Math.min(1, rec.level() * 6);
    bars.forEach((b, i) => { b.style.transform = `scaleY(${(0.5 + lv * 1.9 * shape[i]).toFixed(2)})`; });
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

async function finishListening(tooShort) {
  const mine = session;
  if (!mine) return;
  try { await mine.ready; } catch (_) { return; }
  session = null;
  app.classList.remove('listening');
  cloud.setAttribute('aria-label', 'Hold to talk. Press Enter to start and stop.');
  if (tooShort || !rec.active) {
    rec.cancel();
    hideSaid();
    toast('Hold the cloud while you talk, then let go.');
    return;
  }
  const wav = rec.stop();
  busy = true;
  app.classList.add('thinking');
  showSaid(null, 'Muse Voice Transcribe is listening back');
  let heard = null;
  try {
    heard = (await api('/api/transcribe', { method: 'POST', body: wav })).heard || '';
  } catch (e) {
    toast(e.message);
  }
  app.classList.remove('thinking');
  hideSaid();
  busy = false;
  if (heard === null) return;
  if (!heard) { toast('I didn’t catch that. Hold the cloud and try again.'); return; }
  await ask(heard, true);
}

cloud.addEventListener('pointerdown', (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  try { cloud.setPointerCapture(e.pointerId); } catch (_) { /* fine */ }
  holding = true;
  startListening();
});
const release = () => {
  if (!holding) return;
  holding = false;
  const held = session ? performance.now() - session.at : 0;
  finishListening(held < 450);
};
cloud.addEventListener('pointerup', release);
cloud.addEventListener('pointercancel', () => { if (!holding) return; holding = false; if (session) { session = null; rec.cancel(); app.classList.remove('listening'); hideSaid(); } });
cloud.addEventListener('contextmenu', (e) => e.preventDefault());
cloud.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  e.preventDefault();
  if (e.repeat) return;
  if (e.key === ' ') { if (!session) { holding = true; startListening(); } return; }   // Space: hold to talk
  if (session) finishListening(false);
  else startListening();
});
cloud.addEventListener('keyup', (e) => { if (e.key === ' ') { e.preventDefault(); release(); } });

// On a wide screen, hold Space anywhere on the clouds' side to talk (not while typing or in the panel).
function spaceTalks(e) {
  if (!wide.matches || e.key !== ' ' || e.target === cloud) return false;
  const a = document.activeElement;
  if (!a || a === document.body) return true;
  return !a.closest('input, textarea, select, button, a, [contenteditable], .sheet, .viewer');
}
document.addEventListener('keydown', (e) => {
  if (!spaceTalks(e)) return;
  e.preventDefault();
  if (e.repeat || session) return;
  holding = true;
  startListening();
});
document.addEventListener('keyup', (e) => { if (spaceTalks(e) || (e.key === ' ' && holding && e.target !== cloud)) { e.preventDefault(); release(); } });

/* ---------- typing ---------- */
function openTypebar() {
  $('typebar').hidden = false;
  document.querySelector('.dock').hidden = !wide.matches;
  $('typeInput').focus();
}
function closeTypebar() {
  if (wide.matches) { $('typeInput').blur(); return; }   // always there on a wide screen
  $('typebar').hidden = true;
  document.querySelector('.dock').hidden = false;
}
function layout() {
  const w = wide.matches;
  $('typebar').hidden = !w;
  document.querySelector('.dock').hidden = false;
  $('typeInput').placeholder = w ? 'Ask about your month, or log cash you spent' : 'Ask, or log cash you spent';
  $('cloudHint').textContent = w ? 'Hold the cloud, or hold Space, to talk' : 'Hold the cloud to talk';
  $('sheet').setAttribute('aria-modal', w ? 'false' : 'true');
  if (!$('sheet').hidden) $('scrim').hidden = w;
  document.querySelectorAll('.bubble.leaving, .bubble.open').forEach((b) => b.classList.remove('leaving', 'open'));
}
wide.addEventListener('change', layout);
layout();
$('typeBtn').addEventListener('click', openTypebar);
$('typebar').addEventListener('submit', (e) => {
  e.preventDefault();
  const text = $('typeInput').value.trim();
  if (!text) return;
  $('typeInput').value = '';
  closeTypebar();
  ask(text, false);
});
$('typeInput').addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTypebar(); });
$('suggest').addEventListener('click', () => {
  const s = (home && home.suggestions) || [];
  if (s.length) ask(s[suggestionIndex % s.length], false);
});

/* ---------- the menu on the left (wide screens) ---------- */
const NAV_KEY = 'rideoff-nav';
function setNav(id) {
  document.querySelectorAll('#sidebar [data-nav]').forEach((b) => {
    if (b.dataset.nav === id) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
}
const navFloats = () => window.innerWidth < 1200;   // narrower than that, it opens over the clouds, only when asked
function navWanted() {
  if (navFloats()) return false;
  try { const v = localStorage.getItem(NAV_KEY); if (v) return v === 'open'; } catch (_) { /* no storage */ }
  return true;
}
function setNavOpen(open, remember) {
  document.body.classList.toggle('nav-closed', !open);
  $('navToggle').setAttribute('aria-expanded', String(open));
  $('navToggle').setAttribute('aria-label', open ? 'Hide the menu' : 'Show the menu');
  if (remember) { try { localStorage.setItem(NAV_KEY, open ? 'open' : 'closed'); } catch (_) { /* no storage */ } }
}
$('navToggle').addEventListener('click', () => setNavOpen(document.body.classList.contains('nav-closed'), !navFloats()));
document.addEventListener('click', (e) => {   // a floating menu closes when you click past it
  if (navFloats() && !document.body.classList.contains('nav-closed') && !e.target.closest('#sidebar, #navToggle')) setNavOpen(false, false);
});
document.querySelectorAll('#sidebar [data-nav]').forEach((b) => b.addEventListener('click', () => {
  const id = b.dataset.nav;
  if (navFloats()) setNavOpen(false, false);   // it floats over the clouds there, so it gets out of the way
  if (id === 'home') closeSheet();
  else if (id === 'settings') openSettings();
  else openView(id);
}));
setNavOpen(navWanted(), false);
requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.remove('preload')));

/* ---------- settings ---------- */
$('settingsBtn').addEventListener('click', () => openSettings());
function openSettings() {
  markOpen(null);
  setNav('settings');
  const resetBtn = el('button', { type: 'button', class: 'btn', text: 'Reset the demo' });
  let armed = false;
  resetBtn.addEventListener('click', async () => {
    if (!armed) { armed = true; resetBtn.textContent = 'Tap again to clear your answers, entries and photos'; return; }
    try { const r = await api('/api/reset', { method: 'POST' }); renderHome(r.home); toast('Demo reset'); closeSheet(); } catch (e) { toast(e.message); }
  });
  const today = home ? new Date(home.today + 'T12:00:00').toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : '';
  openSheet([
    title('Settings'),
    el('p', { text: 'Bayou City Mobility LLC is a made-up company. Every number here comes from a synthetic month built for this demo.' }),
    rows([['Today, in the app', today], ['Month', home ? home.month : ''], ['Your copy', 'Only this browser sees it']]),
    el('p', { class: 'muted', text: 'Please don’t upload real receipts or say real account details. What you send goes to Meta’s models, and Meta may use it to improve them.' }),
    el('div', { class: 'section-title' }, el('span', { text: 'Meta models doing the work' })),
    rows([['Muse Spark', 'Reads receipts, books cash, answers'], ['Muse Spark Open', 'Sorted the month onto Schedule C lines (open weights)'], ['SAM 3.1', 'Finds each receipt in a photo'], ['Muse Voice Transcribe', 'Hears what you say'], ['Muse Image', 'Made the demo receipts']]),
    el('div', { class: 'actions' }, resetBtn),
  ]);
}

/* ---------- wiring ---------- */
$('sheetClose').addEventListener('click', closeSheet);
$('scrim').addEventListener('click', closeSheet);
$('viewerClose').addEventListener('click', () => { $('viewer').hidden = true; });
$('viewer').addEventListener('click', (e) => { if (e.target === $('viewer')) $('viewer').hidden = true; });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('viewer').hidden) { $('viewer').hidden = true; return; }
  closeSheet();
});

/* ---------- first visit: say plainly that this is made-up data ---------- */
const NOTICE_KEY = 'rideoff-notice-1';
function noticeSeen() {
  try { return localStorage.getItem(NOTICE_KEY) === 'yes'; } catch (_) { return false; }
}
function showNotice() {
  openSheet([
    title('A demo with made-up data'),
    el('p', { text: 'You’re looking at a fictional driver’s October. The company, rides, bank feeds and receipts are all synthetic.' }),
    el('p', { text: 'Please don’t upload real receipts or say real account details. What you send goes to Meta’s models, and Meta may use it to improve them.' }),
    el('p', { class: 'muted', text: 'Your answers are yours alone: only this browser sees them, and they’re cleared after 2 days away. Settings has a reset.' }),
    el('div', { class: 'actions' }, el('button', {
      type: 'button', class: 'btn primary', text: 'Got it',
      onclick: () => { try { localStorage.setItem(NOTICE_KEY, 'yes'); } catch (_) { /* private mode */ } closeSheet(); },
    })),
  ]);
}

(async () => {
  try {
    renderHome(await api('/api/home'));
    if (!noticeSeen()) showNotice();
  } catch (e) {
    $('headline').textContent = 'Couldn’t open your books';
    $('sub').textContent = e.message;
  }
})();
