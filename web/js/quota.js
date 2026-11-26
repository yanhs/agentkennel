// Topbar status (syncTop, CPU/RAM) and the Claude allowance chip.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

function syncTop(s) {
  const el = document.getElementById('tInfo');
  const st = s.archived ? 'archived' : !s.active ? (s.id === SHELL ? 'starting' : 'unloaded')
           : s.status === 'working' ? 'working' : 'idle';
  el.innerHTML = s.name ? '<b>' + esc(s.name) + '</b> &mdash; ' + st : '';
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function setSystem(s) {
  document.getElementById('cpuVal').textContent = s.cpu_pct + '%';
  const gb = mb => (mb / 1000).toFixed(1);       // 13829/23460 MB -> 13.8/23.5G
  document.getElementById('ramVal').textContent = gb(s.ram_used_mb) + '/' + gb(s.ram_total_mb) + 'G';
  document.getElementById('cpuStat').className = 'stat' + (s.cpu_pct > 90 ? ' crit' : s.cpu_pct > 70 ? ' warn' : '');
  document.getElementById('ramStat').className = 'stat' + (s.ram_pct > 90 ? ' crit' : s.ram_pct > 70 ? ' warn' : '');
}

// The Claude allowance like Claude's /usage: "5h 7%" / "wk 9%" next to cmd (GET
// /api/quota, 30.10.2026) — a guest's own limits in a sandbox, the subscription on the
// owner's install, where the title also names today's busiest terminals. The server
// says {available: false} anywhere else (and before its first fetch): no chip.
function fmtTok(n) {
  if (!isFinite(n) || n <= 0) return '0';
  if (n >= 1e6) { const m = n / 1e6; return (m >= 10 || Number.isInteger(m) ? Math.round(m) : m.toFixed(1)) + 'M'; }
  if (n >= 1e3) return Math.round(n / 1e3) + 'K';
  return String(Math.round(n));
}
const QUOTA_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
function quotaReset(t, withDay) {          // 21:30, or Sun 10:00 when not today
  const d = new Date(t * 1000), p = n => String(n).padStart(2, '0');
  const day = withDay || d.toDateString() !== new Date().toDateString();
  return (day ? QUOTA_DAYS[d.getDay()] + ' ' : '') + p(d.getHours()) + ':' + p(d.getMinutes());
}
let _quotaTerms = [], _quotaTermsAt = 0, _quotaPolls = 0;
async function quotaJSON(url) {
  try {
    const r = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
    if (r.ok && (r.headers.get('content-type') || '').includes('json')) return await r.json();
  } catch {}
  return null;
}
async function pollQuota() {
  const chip = document.getElementById('quotaChip');
  const d = await quotaJSON('/api/quota');
  const ok = w => w && isFinite(w.utilization) && isFinite(w.resets_at);
  if (!d || d.available !== true || !ok(d.five_hour) || !ok(d.seven_day)) {
    chip.hidden = true;
    renderClaude(null);
    if (++_quotaPolls === 1) setTimeout(pollQuota, 5000);   // the server's first fetch runs now
    return;
  }
  _quotaPolls++;
  // the owner: which terminals spent the most today (counted at most every 5 minutes)
  if (d.source === 'subscription' && !_isMobile
      && (!_quotaTermsAt || Date.now() - _quotaTermsAt >= 300000)) {
    const t = await quotaJSON('/api/quota/terminals');
    if (t && Array.isArray(t.terminals)) {
      _quotaTerms = t.terminals;
      if (_quotaTerms.length) _quotaTermsAt = Date.now();   // empty: still counting, ask again
    }
  }
  if (d.source !== 'subscription') _quotaTerms = [];
  const parts = [['5 hours', d.five_hour, false], ['week', d.seven_day, true]].map(([name, w, withDay], i) => {
    const pct = Math.round(w.utilization * 100);
    const line = chip.children[i];
    // no 5-hour window (no reset time): Anthropic counts only the week now (26.11.2026)
    if (i === 0 && !(w.resets_at > 0) && w.utilization <= 0) {
      line.className = '';
      line.querySelector('b').textContent = '—';
      return `${name}: no limit counted now (only the week)`;
    }
    line.className = pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : '';
    line.querySelector('b').textContent = pct + '%';
    return `${name}: ${pct}%` + (w.resets_at > 0 ? ` · resets ${quotaReset(w.resets_at, withDay)}` : '');
  });
  const terms = _quotaTerms.filter(t => t && t.tokens_today > 0)
    .map(t => `${t.name} ${fmtTok(t.tokens_today)}`).join(', ');
  chip.title = parts.join('; ')
    + (d.source === 'guest' ? '\nYour Claude limits on this server' : '\nClaude subscription, as in /usage')
    + (terms ? `\nToday by terminal: ${terms}` : '');
  _quotaLast = d;
  chip.hidden = renderClaude(d);       // the owner's key or their own: the label says it
}
