// Polling the list and the system line, live reload, keeping the login fresh.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

// CPU/RAM: taken from /api/library when it carries `_system`; otherwise from
// the old status endpoint (read for `_system` only — nothing slot-related).
let _sysTick = 0;
async function pollSystem() {
  try {
    const r = await fetch('/api/terminal-status');
    if (!r.ok) return;
    const d = await r.json();
    if (d && d._system) setSystem(d._system);
  } catch {}
}

/* A new install's first visit (GET says first_run: no registry yet): the
   server makes one "Getting started" terminal, the list gets the Tasks and
   Server rows, and the terminal is what the screen shows (owner 02.11.2026).
   {id: null} = another tab already did it: change nothing here. */
let _welcomeAsked = false;
async function welcome() {
  try {
    const s = await jpost('/api/library/welcome', {});
    if (!s || !ID_RE.test(s.id || '')) return;
    mutSeq++;
    if (!byId(s.id)) sessions.push(Object.assign({}, s, {name: String(s.name ?? '')}));
    tasksOpen = true; saveTasksOpen();
    serverOpen = true; saveServerOpen();
    render();
    pick(s.id);
    setTimeout(poll, 1500);                // pick up "loaded" once ttyd has started it
  } catch {}
}

async function poll() {
  const seq = mutSeq;
  try {
    const r = await fetch('/api/library?archived=1',
                          {cache: 'no-store', credentials: 'same-origin'});
    if (!r.ok) return;                       // 401/5xx → skip this tick, don't churn
    const d = await r.json();
    if (d && d._system) setSystem(d._system);
    else if (_sysTick++ % 2 === 0) pollSystem();
    const sh = d && d.shell;
    if (seq === mutSeq) shellState = sh && typeof sh === 'object'
      ? {active: sh.active === true, attached: sh.attached === true,
         status: sh.status === 'working' ? 'working' : 'idle', locked: sh.locked === true}
      : null;
    // a guest's rules: the sandbox is Claude-only, no command line (the server refuses it)
    document.getElementById('cmdBtn').hidden = !!(shellState && shellState.locked);
    // the open shell ended (`exit` typed in it): drop its frame. Only once it
    // had been seen running — right after cmd it is still starting.
    if (seq === mutSeq && sel === SHELL) {
      if (shellState && shellState.active) _shellSeenUp = true;
      else if (_shellSeenUp) showPlaceholder();
    }
    if (!d || !Array.isArray(d.sessions)) { render(); syncSel(); return; }
    if (seq !== mutSeq) return;              // a local change raced this poll — next one is fresh
    sessions = d.sessions
      .filter(s => s && ID_RE.test(s.id))
      .map(s => Object.assign({}, s, {name: String(s.name ?? '')}));
    render();
    syncSel();
    // the open terminal took a new number: follow it (a tmux re-attach —
    // the terminal's process is not touched). All the way: a background tab
    // (its timers slowed to one a minute) can see two switches in one poll
    // (/clear, work, /clear); stopping at the first would open that earlier
    // row anew. Each step is a switch newer than the one before, so /resume
    // back and forth cannot loop.
    if (ID_RE.test(sel || '')) {
      let cur = sel, seen = _selSwitchSeen;
      for (let i = 0; i < 8; i++) {
        const sw = switchedFrom(cur);
        if (!sw || (cur === sel ? sw.switched_at <= seen : sw.switched_at < seen)) break;
        cur = sw.id; seen = sw.switched_at;
      }
      if (cur !== sel) pick(cur);
    }
    if (_pendingOpen) { const id = _pendingOpen; _pendingOpen = null; openTerminalById(id); }
    if (d.first_run === true && !SOLO && !_welcomeAsked) { _welcomeAsked = true; welcome(); }
    if (showTrash) loadTrash();
  } catch {}
}
poll(); setInterval(poll, 4000);

// Live-reload: reload ONLY when the page file's version genuinely changes.
// Never reload on a non-OK response (e.g. a 401 HTML body) or a non-numeric
// body — doing that turned a single transient auth hiccup into a reload +
// login-prompt storm ("dozens of prompts, everything hangs"). Keep polling so
// it self-heals when auth recovers; only a real, changed mtime triggers a reload.
let _v = null;
setInterval(async () => {
  try {
    const r = await fetch('/api/page-version', {cache: 'no-store'});
    if (!r.ok) return;                            // 401/5xx → never reload
    const v = (await r.text()).trim();
    if (!/^[0-9]+(\.[0-9]+)?$/.test(v)) return;   // only a real mtime is a valid version
    if (_v && v !== _v) location.reload();
    _v = v;
  } catch { /* network blip — ignore, never reload on error */ }
}, 1500);

// Sliding session: while the panel is open, trade the login cookie for a fresh 30-day
// one — on load, then every 6 h — so daily use never reaches the expiry. A 401 means
// already signed out: ignore it, the next request lands on /login as usual.
const _renewLogin = () => fetch('/login?renew=1', {cache: 'no-store', credentials: 'same-origin'}).catch(() => {});
_renewLogin(); setInterval(_renewLogin, window.__ad_renew_ms || 6 * 3600 * 1000);
