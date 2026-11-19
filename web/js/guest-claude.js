// A guest sandbox's "Your Claude" label and dialog; starts the allowance poll.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

// ── "Your Claude": a guest's sandbox only (task 11, 13.11.2026) ──
// /api/quota carries the turnstile's `claude` {mode, owner_mode, last4, usd, error}
// behind a turnstile. The label replaces the chip for owner_key / own (their limits
// are dollars or none); for owner_sub the chip stays and a small "Claude" sits below.
// An older turnstile (no `claude`) and the owner's own install: nothing new.
// The dialog posts {"secret"} / {"back": true} to /api/quota/claude; the key field
// is emptied after the POST whatever the answer, and answers are shown as text.
let _quotaLast = null, _claudeBusy = false;
const claudeEl = id => document.getElementById(id);
const usdUsed = n => '$' + Number(n).toFixed(2);
const usdLimit = n => '$' + (Number.isInteger(n) ? String(n) : Number(n).toFixed(2));
function claudeOf(d) {
  const c = d && d.source === 'guest' ? d.claude : null;
  return c && typeof c === 'object' && ['owner_key', 'own', 'owner_sub'].includes(c.mode) ? c : null;
}
function claudeLevel(c) {                 // the owner's key: amber from 70 %, red from 90 %
  const u = c.usd;
  if (c.error || c.mode !== 'owner_key' || !u || !(u.limit > 0)) return '';
  const r = u.used / u.limit;
  return r >= 0.9 ? 'crit' : r >= 0.7 ? 'warn' : '';
}
function claudeMore(c) {
  if (c.error) return ' · needs the owner';
  if (c.mode === 'owner_key') return " · owner's key" + (c.usd ? ` ${usdUsed(c.usd.used)}/${usdLimit(c.usd.limit)}` : '');
  if (c.mode === 'own') return ' · own key' + (c.last4 ? ' …' + c.last4 : '');
  return '';
}
function claudeNowText(c) {
  if (c.mode === 'owner_key') return "The owner's key" + (c.usd ? ` · ${usdUsed(c.usd.used)} of ${usdLimit(c.usd.limit)} this week` : '');
  if (c.mode === 'own') return 'Your own key' + (c.last4 ? ' · …' + c.last4 : '');
  return "The owner's Claude subscription";
}
// the label (returns true when it takes the chip's place)
const CLAUDE_BTNS = ['claudeBtn', 'claudeTop'];   // the sidebar's; the phone's topbar
function renderClaude(d) {
  const c = claudeOf(d);
  for (const id of CLAUDE_BTNS) {
    const btn = claudeEl(id);
    if (!c) { btn.hidden = true; continue; }
    const word = document.createElement('span'), more = document.createElement('span');
    word.className = 'cl-word'; word.textContent = 'Claude';
    more.className = 'cl-more'; more.textContent = claudeMore(c);
    btn.replaceChildren(word, more);
    btn.classList.remove('warn', 'crit');
    if (claudeLevel(c)) btn.classList.add(claudeLevel(c));
    btn.title = c.error ? 'Claude needs the owner; they have been told'
      : claudeNowText(c) + (c.mode === 'owner_key' && c.usd && c.usd.resets_at > 0
          ? ` · resets ${quotaReset(c.usd.resets_at, true)}` : '') + '\nOpen "Your Claude"';
    btn.hidden = false;
  }
  if (!c) return false;
  if (claudeEl('claudeDlg').open) fillClaudeDlg(c);
  return !!c.error || c.mode !== 'owner_sub';
}
function fillClaudeDlg(c) {
  const broken = !!c.error;
  claudeEl('claudeBroken').hidden = !broken;
  claudeEl('claudeNowSec').hidden = broken;
  claudeEl('claudeEdit').hidden = broken;
  claudeEl('claudeSave').hidden = broken;
  claudeEl('claudeBack').hidden = broken || !(c.mode === 'own' && c.owner_mode);
  claudeEl('claudeBack').textContent = c.owner_mode === 'owner_sub'
    ? "Back to the owner's subscription" : "Back to the owner's key";
  if (broken) return;
  const now = claudeEl('claudeNow'), line = document.createElement('div');
  line.textContent = claudeNowText(c);
  const parts = [line];
  if (c.mode === 'owner_key' && c.usd && c.usd.limit > 0) {
    const bar = document.createElement('div'), fill = document.createElement('i');
    bar.className = 'cl-bar' + (claudeLevel(c) ? ' ' + claudeLevel(c) : '');
    fill.style.width = Math.min(100, Math.max(0, c.usd.used / c.usd.limit * 100)).toFixed(1) + '%';
    bar.appendChild(fill);
    parts.push(bar);
  }
  now.replaceChildren(...parts);
  claudeEl('claudeKindLegend').textContent = c.mode === 'own' ? 'Use another key instead' : 'Use your own instead';
}
function claudeKind() {
  const sub = claudeEl('claudeKindSub').checked;
  claudeEl('claudeHintApi').hidden = sub;
  claudeEl('claudeHintSub').hidden = !sub;
  claudeEl('claudeKey').placeholder = sub ? 'sk-ant-oat01-…' : 'sk-ant-api03-…';
}
function claudeSay(text, cls) {
  const m = claudeEl('claudeMsg');
  m.textContent = text || '';
  m.className = cls || '';
}
function claudeAnswerText(r, j) {
  if (r.ok && j && typeof j.message === 'string') return [j.message, j.ok ? 'good' : 'bad'];
  const e = j && j.error;
  const text = e && typeof e === 'object' && typeof e.message === 'string' ? e.message
    : typeof e === 'string' ? e : `The server answered HTTP ${r.status}`;
  return [text, 'bad'];
}
// POST answers 202 {"job"} at once (the panel's server never waits on the check);
// the answer is asked for every second, up to 35 s
const CLAUDE_POLL_MS = 1000, CLAUDE_WAIT_MS = 35000;
const claudeSleep = ms => new Promise(res => setTimeout(res, ms));
async function claudeJSON(r) { try { return await r.json(); } catch { return null; } }
async function claudeJob(job) {          // -> [Response-like, body] or null on time-out
  const until = Date.now() + CLAUDE_WAIT_MS;
  while (Date.now() < until) {
    await claudeSleep(CLAUDE_POLL_MS);
    let r;
    try {
      r = await fetch('/api/quota/claude?job=' + encodeURIComponent(job),
                      {cache: 'no-store', credentials: 'same-origin'});
    } catch { continue; }                 // a blip: ask again
    const j = await claudeJSON(r);
    if (r.ok && j && j.pending) continue;
    if (r.ok && j && Number.isInteger(j.status))
      return [{ok: j.status >= 200 && j.status < 300, status: j.status}, j.body];
    return [r, j];                        // 404: the check expired or was lost
  }
  return null;
}
async function claudePost(body) {
  if (_claudeBusy) return;
  const save = claudeEl('claudeSave'), back = claudeEl('claudeBack'), key = claudeEl('claudeKey');
  _claudeBusy = true;
  save.disabled = back.disabled = true;
  claudeSay('Checking with Anthropic…', '');
  let said = ['The server did not answer. Try again.', 'bad'];
  try {
    const r = await fetch('/api/quota/claude', {
      method: 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    key.value = '';                      // the page never keeps the key
    const j = await claudeJSON(r);
    if (r.status === 202 && j && typeof j.job === 'string') {
      const got = await claudeJob(j.job);
      said = got ? claudeAnswerText(...got)
        : ['No answer from Anthropic yet. Look again in a minute, or try again.', 'bad'];
      const a = got && got[1];
      if (got && got[0].ok && a && a.claude && typeof a.claude === 'object' && _quotaLast) {
        _quotaLast = Object.assign({}, _quotaLast, {claude: a.claude});
        document.getElementById('quotaChip').hidden = renderClaude(_quotaLast);
      }
    } else {
      said = claudeAnswerText(r, j);
    }
  } catch {}
  finally {
    key.value = '';
    _claudeBusy = false;
    save.disabled = back.disabled = false;
  }
  claudeSay(...said);
}
function openClaudeDlg() {
  const c = claudeOf(_quotaLast);
  if (!c) return;
  fillClaudeDlg(c);
  if (!_claudeBusy) claudeSay('', '');
  claudeEl('claudeDlg').showModal();
  if (!c.error && !_isMobile) claudeEl('claudeKey').focus();
}
for (const id of CLAUDE_BTNS) claudeEl(id).addEventListener('click', openClaudeDlg);
claudeEl('claudeKindApi').addEventListener('change', claudeKind);
claudeEl('claudeKindSub').addEventListener('change', claudeKind);
claudeEl('claudeForm').addEventListener('submit', e => {
  e.preventDefault();
  if (_claudeBusy || claudeEl('claudeSave').hidden) return;
  const secret = claudeEl('claudeKey').value.trim();
  if (!secret) { claudeSay('Paste the key first.', 'bad'); claudeEl('claudeKey').focus(); return; }
  claudePost({secret});
});
claudeEl('claudeBack').addEventListener('click', () => claudePost({back: true}));
claudeEl('claudeClose').addEventListener('click', () => {
  claudeEl('claudeKey').value = '';
  claudeEl('claudeDlg').close();
});
claudeEl('claudeDlg').addEventListener('close', () => {
  if (!_claudeBusy) claudeEl('claudeKey').value = '';
});
pollQuota();
setInterval(pollQuota, 60000);
