// DCS Airfield Charts website : search, list filter and page viewer. No dependency.
(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const norm = s => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
const ROOT = document.documentElement.dataset.root || '';

// ---------- search (home page)
let ENTRIES = null, LOADING = null;
function loadEntries() {
  if (!LOADING) {
    LOADING = fetch(ROOT + 'search.json').then(r => r.json()).then(list => {
      ENTRIES = list.map(e => ({...e, key: norm(e.n + ' ' + e.i + ' ' + e.a)}));
      return ENTRIES;
    });
  }
  return LOADING;
}
function rank(q) {
  const nq = norm(q); if (!nq || !ENTRIES) return [];
  const out = [];
  for (const e of ENTRIES) {
    let s = -1; const nn = norm(e.n);
    if (e.i && e.i.toLowerCase() === nq) s = 0;
    else if (nn.startsWith(nq)) s = 1;
    else if (nn.split(' ').some(w => w.startsWith(nq))) s = 2;
    else if (e.i && e.i.toLowerCase().startsWith(nq)) s = 2;
    else if (e.key.includes(nq)) s = 3;
    if (s >= 0) out.push([s + (e.t === 'hp' ? .5 : 0), e]);
  }
  return out.sort((a, b) => a[0] - b[0] || a[1].n.localeCompare(b[1].n)).slice(0, 12).map(x => x[1]);
}
function hl(text, q) {
  const nq = norm(q); const t = norm(text); const i = t.indexOf(nq);
  if (!nq || i < 0 || t.length !== text.length) return esc(text);
  return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + nq.length)) + '</mark>' + esc(text.slice(i + nq.length));
}
function mountSearch(box) {
  const inp = $('input', box), ul = $('.results', box); let sel = -1;
  const draw = () => {
    const items = rank(inp.value); sel = items.length ? 0 : -1;
    if (!inp.value.trim()) { ul.hidden = true; return; }
    ul.hidden = false;
    ul.innerHTML = items.length ? items.map((e, k) => `<li><a href="${esc(ROOT + e.u)}" class="${k === sel ? 'on' : ''}">
      <span class="nm">${hl(e.n, inp.value)}</span>
      <span class="mt">${esc(e.m)}</span>
      <span class="ic">${esc(e.i || '')}</span></a></li>`).join('') : '<li class="none">No airfield or heliport matches.</li>';
  };
  inp.addEventListener('input', () => { loadEntries().then(draw, () => { ul.hidden = false; ul.innerHTML = '<li class="none">The search list could not be loaded.</li>'; }); });
  inp.addEventListener('keydown', ev => {
    const as = $$('a', ul);
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault(); if (!as.length) return;
      sel = (sel + (ev.key === 'ArrowDown' ? 1 : as.length - 1)) % as.length;
      as.forEach((a, k) => a.classList.toggle('on', k === sel)); as[sel].scrollIntoView({block: 'nearest'});
    } else if (ev.key === 'Enter' && sel >= 0 && as[sel]) { location.href = as[sel].getAttribute('href'); }
    else if (ev.key === 'Escape') { inp.value = ''; ul.hidden = true; }
  });
  document.addEventListener('click', ev => { if (!box.contains(ev.target)) ul.hidden = true; });
}

// ---------- list of a theatre
function mountList() {
  const list = $('#list'), table = list && $('table.idx', list);
  if (!table) return;
  const body = $('tbody', table), rows = [...body.children], input = $('.filter');
  const params = new URLSearchParams(location.search);
  let filt = ['af', 'heli', 'hp'].includes(params.get('f')) ? params.get('f') : 'all';
  input.value = params.get('q') || '';
  const draw = () => {
    $$('.seg button').forEach(b => b.setAttribute('aria-pressed', b.dataset.f === filt));
    const q = norm(input.value || '');
    const keep = rows.filter(r => (filt === 'all' || filt === r.dataset.t) && (!q || r.dataset.k.includes(q)));
    if (keep.length) { body.replaceChildren(...keep); list.replaceChildren(table); }
    else { const d = document.createElement('div'); d.className = 'empty'; d.textContent = 'Nothing matches this filter.'; list.replaceChildren(d); }
  };
  $$('.seg button').forEach(b => b.addEventListener('click', () => { filt = b.dataset.f; draw(); }));
  input.addEventListener('input', draw);
  draw();
}

// ---------- viewer
function viewerOf(items, k, pdf, opener) {
  const n = items.length;
  const w = document.createElement('div'); w.className = 'lb-wrap'; w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'true');
  const draw = () => {
    const it = items[k];
    w.innerHTML = `<div class="lb-top"><span class="cd">${esc(it.cd)}</span><span class="lb">${esc(it.lb)}${n > 1 ? ' · ' + (k + 1) + ' / ' + n : ''}</span><span class="sp"></span>
      <a class="btn" href="${esc(pdf)}" style="background:none;color:#fff;border-color:#4A5670">PDF</a>
      <button aria-label="Close" data-x>✕</button></div>
      <div class="lb-body"><button class="lb-nav" data-d="-1" aria-label="Previous page" ${k ? '' : 'disabled'}>‹</button>
      <img src="${esc(it.im)}" alt="${esc(it.alt)}">
      <button class="lb-nav" data-d="1" aria-label="Next page" ${k < n - 1 ? '' : 'disabled'}>›</button></div>`;
    $('[data-x]', w).focus();
  };
  const close = () => { w.remove(); document.removeEventListener('keydown', key); if (opener) opener.focus(); };
  const go = d => { if (k + d >= 0 && k + d < n) { k += d; draw(); } };
  const key = ev => { if (ev.key === 'Escape') close(); else if (ev.key === 'ArrowLeft') go(-1); else if (ev.key === 'ArrowRight') go(1); };
  w.addEventListener('click', ev => {
    const t = ev.target.closest('[data-d],[data-x]');
    if (t && t.dataset.x !== undefined) close(); else if (t) go(+t.dataset.d);
    else if (ev.target === w || ev.target.classList.contains('lb-body')) close();
  });
  let x0 = null;
  w.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, {passive: true});
  w.addEventListener('touchend', e => { if (x0 !== null) { const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1); } x0 = null; });
  document.addEventListener('keydown', key); document.body.appendChild(w); draw();
}
const itemOf = el => ({cd: el.dataset.cd, lb: el.dataset.lb, im: el.dataset.l, alt: el.dataset.alt});
function mountViewer() {
  const pages = $('.pages');
  if (pages) {
    const cards = $$('.pg-card', pages), items = cards.map(itemOf);
    cards.forEach((c, k) => c.addEventListener('click', () => viewerOf(items, k, pages.dataset.pdf, c)));
  }
  const fig = $('[data-v]');
  if (fig) fig.addEventListener('click', () => viewerOf([itemOf(fig)], 0, fig.dataset.pdf, fig));
}

const box = $('.search');
if (box) mountSearch(box);
mountList();
mountViewer();
})();
