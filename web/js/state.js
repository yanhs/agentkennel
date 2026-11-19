// Shared state and the pinned rows: the topic list's state, page elements, the
// Tasks / Server / command-line rows, jpost().
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

// the empty screen as the page has it: showPlaceholder() puts back exactly this
const PH_HTML = document.getElementById('ph').outerHTML;
/* ── Session library ──
   The left column is the list of topics (sessions) from GET /api/library,
   in a fixed order — the same as library.display_order: the manual order
   (a new topic on top, dragging changes it). Loaded ones show a status dot,
   unloaded ones are greyed; opening, using, loading or unloading a topic
   never moves it (owner 27.10.2026: the list must not jump).
   A topic is addressed ONLY by its 8-hex code; there are no slot numbers.
   Opening one = iframe /sess/?arg=<code> (the server loads it if needed). */
const ID_RE = /^[0-9a-f]{8}$/;
// ?solo=<id>: «Open in tab» — this page with only that terminal, no left column, but
// with every copy/paste/📎 helper of the dashboard (the bare /sess/ page has none).
// The parameter stays in the URL, so reloading the tab reopens the same terminal.
const SOLO = (() => { try { const v = new URL(location.href).searchParams.get('solo') || '';
                            return ID_RE.test(v) ? v : null; } catch { return null; } })();
if (SOLO) document.body.classList.add('solo');
const _isMobile = window.matchMedia('(max-width: 700px)').matches;
let sel = null;            // code of the open topic
let sessions = [];         // last /api/library payload (+ local edits)
let editing = null;        // code whose name is being edited — freezes re-render
let mutSeq = 0;            // bumped on every local change; a poll that started
                           // before it is stale and must not overwrite it
const rows = new Map();    // code -> row element (kept across polls)
const _restarting = new Set();   // codes with a ⟳ restart in flight
const _trashing = new Set();     // codes with a 🗑 Move to Trash in flight
const list = document.getElementById('list');
const search = document.getElementById('search');
const libTools = document.getElementById('libTools');
const newBtn = document.getElementById('newBtn');
const newForm = document.getElementById('newForm');
const newName = document.getElementById('newName');
const newOk = document.getElementById('newOk');
const libEmpty = document.getElementById('libEmpty');
const archToggle = document.getElementById('showArchived');
const listFoot = document.getElementById('listFoot');
const trashToggle = document.getElementById('showTrash');
const trashView = document.getElementById('trashView');
/* ── Tasks: the task board as a tab in the viewer (the T button). While it
   is open a "Tasks" row is pinned at the very top; ✕ closes it. Open/closed
   is remembered per browser. `sel === TASKS` = the board is shown. */
const TASKS = 'tasks';
const TASKS_PATH = '/tasks/';
let tasksOpen = false;
try { tasksOpen = !SOLO && localStorage.getItem('lib-tasks-open') === '1'; } catch {}
const tasksRow = document.createElement('div');
tasksRow.className = 'card tasks-row';
tasksRow.dataset.tasks = '1';
tasksRow.innerHTML = `
        <div class="card-top">
          <div class="proj"><svg class="row-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 4l1.5 1.5L6 3"/><path d="M2 10l1.5 1.5L6 9"/><path d="M8.5 4.5H14"/><path d="M8.5 10.5H14"/></svg>Tasks</div>
          <div class="card-btns">
            <button type="button" class="tasks-close" title="Close task board" aria-label="Close task board">✕</button>
            <span class="dot idle" title="open"></span>
          </div>
        </div>`;
tasksRow.title = 'Task board — click to show it';
function saveTasksOpen() {
  try { localStorage.setItem('lib-tasks-open', tasksOpen ? '1' : '0'); } catch {}
}
/* ── Server: the server status page (web/server.html) as a tab, exactly like
   Tasks — the gauge button opens it, a pinned "Server" row stays while it is
   open, ✕ closes it; remembered per browser. `sel === SERVER` = it is shown. */
const SERVER = 'server';
const SERVER_PATH = '/server.html';
let serverOpen = false;
try { serverOpen = !SOLO && localStorage.getItem('lib-server-open') === '1'; } catch {}
const serverRow = document.createElement('div');
serverRow.className = 'card server-row';
serverRow.dataset.server = '1';
serverRow.innerHTML = `
        <div class="card-top">
          <div class="proj"><svg class="row-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="M2.5 12a5.5 5.5 0 0 1 11 0"/><path d="M8 12l2.8-3.6"/></svg>Server</div>
          <div class="card-btns">
            <button type="button" class="server-close" title="Close server status" aria-label="Close server status">✕</button>
            <span class="dot idle" title="open"></span>
          </div>
        </div>`;
serverRow.title = 'Server status — click to show it';
function saveServerOpen() {
  try { localStorage.setItem('lib-server-open', serverOpen ? '1' : '0'); } catch {}
}
/* ── cmd: the one plain command line (tmux cmd-shell, /sess/?arg=shell).
   GET /api/library reports it in `shell` (not in sessions[]); while it is
   loaded it is a row pinned above the topics. `sel === SHELL` = it is open. */
const SHELL = 'shell';
const SHELL_PATH = '/sess/?arg=shell';
let shellState = null;     // {active, attached, status} from the last poll
const shellRow = document.createElement('div');
shellRow.className = 'card shell-row';
shellRow.dataset.shell = '1';
shellRow.innerHTML = `
        <div class="card-top">
          <div class="proj"><svg class="row-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6.5 8 3 11.5"/><path d="M8.5 12H13"/></svg>Command line</div>
          <div class="card-btns">
            <button type="button" class="shell-close" title="Close command line" aria-label="Close command line">✕</button>
            <span class="dot off"></span>
          </div>
        </div>`;
shellRow.title = 'Command line (bash) — click to open';
shellRow.addEventListener('click', e => {
  if (e.target.closest('.shell-close')) return;
  pickShell();
});
shellRow.querySelector('.shell-close').addEventListener('click', e => {
  e.stopPropagation();
  closeShell();
});

// ✕ on the row: one click, no confirm; the row goes at once, back on error
async function closeShell() {
  const was = shellState;
  shellState = {active: false, attached: false, status: 'off'};
  mutSeq++;                                // a poll in flight still sees it running
  if (sel === SHELL) showPlaceholder();
  render();
  try {
    await jpost('/api/library/shell-close', {});
  } catch (err) {
    shellState = was;
    mutSeq++;
    render();
    _toast('⚠️ Command line not closed: ' + String(err && err.message || err).slice(0, 60), 5000);
  }
}
document.getElementById('cmdBtn').addEventListener('click', () => pickShell());
if (_isMobile) search.placeholder = 'Search';   // 16px font, ~130px wide on a phone
// "Show archived" is remembered per browser (storage may be unavailable)
let showArch = false;
try { showArch = localStorage.getItem('lib-show-archived') === '1'; } catch {}
if (localStorage.getItem('stats-hidden') === 'true') document.getElementById('sysStats').classList.add('hidden');

async function jpost(path, body) {
  const r = await fetch(path, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  let d = null;
  try { d = await r.json(); } catch {}
  if (!r.ok) {
    const err = new Error((d && d.error) || ('HTTP ' + r.status));
    err.status = r.status;                 // the caller may act on a 409's reason
    err.data = d;
    throw err;
  }
  return d;
}
