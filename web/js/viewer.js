// The viewer: opening a terminal, the command line, the Tasks and Server tabs,
// the "Copied" chip, openFrame().
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

let _frameFocus = null;
document.addEventListener('click', e => {
  const wrap = document.getElementById('wrap');
  if (_frameFocus && wrap.contains(e.target)) _frameFocus();
});

function pick(id) {
  if (!ID_RE.test(id)) return;
  sel = id;
  const sw = switchedFrom(id);
  _selSwitchSeen = sw ? sw.switched_at : 0;
  document.getElementById('topbar').classList.remove('no-term');
  setLabel(id, true);
  syncSel();
  // a topic still running in an old numbered terminal opens there (the
  // server refuses a second Claude on the same conversation)
  const row = sessions.find(s => s.id === id);
  const legacy = row && typeof row.legacy_path === 'string' && /^\/terminal\d*\/$/.test(row.legacy_path) ? row.legacy_path : null;
  openFrame(legacy || ('/sess/?arg=' + id));
  if (SOLO && row && row.name) document.title = row.name + ' · AgentKennel';
}

// cmd button / "Command line" row: the server starts the shell if needed
// and every tab attaches to the same one
let _shellSeenUp = false;  // the open shell frame's shell has been reported running

// back to the empty screen (the shell was closed or exited)
function showPlaceholder() {
  sel = null;
  _shellSeenUp = false;
  const wrap = document.getElementById('wrap');
  const f = wrap.querySelector('iframe'); if (f) f.remove();
  if (!document.getElementById('ph')) wrap.insertAdjacentHTML('beforeend', PH_HTML);
  document.getElementById('topbar').classList.add('no-term');
  _frameFocus = null;
  syncSel();
}

function pickShell() {
  if (shellState && shellState.locked) {
    _toast('The command line is off here: ask Claude in a terminal.', 5000);
    return;
  }
  sel = SHELL;
  _shellSeenUp = !!(shellState && shellState.active);
  document.getElementById('topbar').classList.remove('no-term');
  setLabel('cmd');
  syncSel();
  openFrame(SHELL_PATH);
  if (!(shellState && shellState.active)) setTimeout(poll, 1500);   // show its row
}

// session: a terminal's 8-hex id -> the board opens filtered to its tasks
function openTasks(session) {
  tasksOpen = true;
  saveTasksOpen();
  sel = TASKS;
  document.getElementById('topbar').classList.remove('no-term');
  setLabel('tasks');
  render();
  syncSel();
  openFrame(ID_RE.test(session || '') ? TASKS_PATH + '?session=' + session : TASKS_PATH, true);
}

// top-bar label; a terminal's id there is a link to that terminal's tasks
function setLabel(text, isTerminal) {
  const l = document.getElementById('tNum');
  l.textContent = text;
  l.classList.toggle('link', !!isTerminal);
  if (isTerminal) l.title = "Show this terminal's tasks"; else l.removeAttribute('title');
}
document.getElementById('tNum').addEventListener('click', () => {
  if (ID_RE.test(sel || '')) openTasks(sel);
});

// The task board (same origin, in our iframe or standalone via /?open=<id>)
// asks to open a terminal by its code. Same rule as a list click: a known
// terminal opens even if the search hides its row; an archived one is
// restored first.
function openTerminalById(id) {
  if (!ID_RE.test(id || '')) return;
  const s = byIdOrAlias(id);
  if (!s) { _toast('Terminal ' + id + ' not found', 3000); return; }
  if (s.archived) { restoreAndOpen(s.id); return; }
  pick(s.id);
}
window.addEventListener('message', e => {
  if (e.origin !== location.origin) return;
  const d = e.data;
  // 'agentdeck:open-terminal': the same message from a board page before v2.1.0
  if (!d || typeof d !== 'object' || (d.type !== 'agentkennel:open-terminal' && d.type !== 'agentdeck:open-terminal')) return;
  if (typeof d.id !== 'string' || !ID_RE.test(d.id)) return;
  openTerminalById(d.id);
});
// ?open=<id>: honoured once the first list arrives, then dropped from the URL
let _pendingOpen = SOLO;
try {
  const u = new URL(location.href);
  if (u.searchParams.has('open')) {
    const v = u.searchParams.get('open') || '';
    if (ID_RE.test(v)) _pendingOpen = v;
    u.searchParams.delete('open');
    history.replaceState(history.state, '', u.pathname + (u.search === '?' ? '' : u.search) + u.hash);
  }
} catch {}

function closeTasks() {
  tasksOpen = false;
  saveTasksOpen();
  if (sel === TASKS) showPlaceholder();
  render();
}

document.getElementById('tasksBtn').addEventListener('click', e => {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;   // new tab/window: browser default
  e.preventDefault();
  openTasks();
});
tasksRow.addEventListener('click', e => {
  if (e.target.closest('.tasks-close')) return;
  openTasks();
});
tasksRow.querySelector('.tasks-close').addEventListener('click', e => {
  e.stopPropagation();
  closeTasks();
});

function openServer() {
  serverOpen = true;
  saveServerOpen();
  sel = SERVER;
  document.getElementById('topbar').classList.remove('no-term');
  setLabel('server');
  render();
  syncSel();
  openFrame(SERVER_PATH, true);
}

function closeServer() {
  serverOpen = false;
  saveServerOpen();
  if (sel === SERVER) showPlaceholder();
  render();
}

document.getElementById('serverBtn').addEventListener('click', e => {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;   // new tab/window: browser default
  e.preventDefault();
  openServer();
});
serverRow.addEventListener('click', e => {
  if (e.target.closest('.server-close')) return;
  openServer();
});
serverRow.querySelector('.server-close').addEventListener('click', e => {
  e.stopPropagation();
  closeServer();
});

/* ── "Copied" chip: after a mouse selection in a terminal was actually copied
   to the clipboard (enableClipboardCopy), a small chip in the terminal's
   top-right corner confirms it and says how to paste. The paste shortcut is
   spelled out for the first HINT_MAX copies in this browser (count in
   localStorage; broken storage = keep spelling it out), then just "Copied". ── */
const HINT_KEY = 'agentkennel-copy-hint', HINT_MAX = 5, HINT_MS = 1500;
const HINT_KEY_OLD = 'agentdeck-copy-hint';   // the count before v2.1.0, still read
const _isMac = (() => {
  const n = navigator;
  return /mac|iphone|ipad/i.test([n.userAgentData && n.userAgentData.platform,
                                  n.platform, n.userAgent].join(' '));
})();
let _hintTimer = null;
function hideCopyHint() {
  clearTimeout(_hintTimer); _hintTimer = null;
  const h = document.getElementById('copyHint'); if (h) h.classList.remove('show');
}
function showCopyHint() {
  let n = 0;
  try { n = parseInt(localStorage.getItem(HINT_KEY) || localStorage.getItem(HINT_KEY_OLD), 10) || 0; } catch (e) {}
  if (n < HINT_MAX) { try { localStorage.setItem(HINT_KEY, String(n + 1)); } catch (e) {} }
  const wrap = document.getElementById('wrap');
  let h = document.getElementById('copyHint');
  if (!h) {
    h = document.createElement('div');
    h.id = 'copyHint'; h.className = 'copy-hint';
    h.setAttribute('role', 'status'); h.setAttribute('aria-live', 'polite');
  }
  if (h.parentNode !== wrap) wrap.appendChild(h);
  h.textContent = n < HINT_MAX
    ? 'Copied · ' + (_isMac ? '⌘V' : 'Ctrl+Shift+V') + ' to paste' : 'Copied';
  h.classList.remove('show');
  void h.offsetWidth;                      // restart the fade-in on a quick re-copy
  h.classList.add('show');
  clearTimeout(_hintTimer);
  _hintTimer = setTimeout(hideCopyHint, HINT_MS);
}

// plain = a web page (the task board), not a terminal: no ttyd helpers
function openFrame(path, plain) {
  // a terminal opens in a tab as this dashboard in solo mode (copy/paste work there);
  // a plain page (the task board) opens as itself
  document.getElementById('tOpen').href = (!plain && ID_RE.test(sel || ''))
    ? location.pathname + '?solo=' + sel : path;
  const wrap = document.getElementById('wrap');
  const ph = document.getElementById('ph'); if (ph) ph.remove();
  const old = wrap.querySelector('iframe'); if (old) old.remove();
  _tmuxSel = null;                                // a selection belongs to the old terminal
  const f = document.createElement('iframe'); f.src = path;
  wrap.appendChild(f);
  hideCopyHint();
  if (plain) { _frameFocus = null; return; }
  if (_isMobile) enableIframeTouchScroll(f);
  onXtermScreen(f, attachWheelThrottle);       // touchpads: desktop and tablet alike
  if (!_isMobile) enableClipboardCopy(f);
  enablePasteImage(f);
  suppressContextMenu(f);
  const doFocus = () => { try { f.focus(); f.contentWindow?.focus(); } catch(e) {} };
  f.addEventListener('load', doFocus);
  f.addEventListener('mousedown', doFocus);
  _frameFocus = doFocus;
}
