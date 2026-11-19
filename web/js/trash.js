// Trash: Move to Trash, the Trash list, restore, Empty trash.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

let showTrash = false;
let trash = [];                  // last GET /api/library?trash=1, most recently trashed first
let trashSeq = 0;                // bumped on every local change: a load that started before is stale
let trashLoaded = false, trashKey = null;
let trashFailed = false;         // the last load failed and nothing was ever loaded
const trashList = document.getElementById('trashList');
const trashEmpty = document.getElementById('trashEmpty');
const emptyBtn = document.getElementById('emptyTrash');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function trashedAt(t) {
  const d = new Date(t * 1000), two = n => String(n).padStart(2, '0');
  return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + two(d.getHours()) + ':' + two(d.getMinutes());
}
// loud: the owner just opened the view, so a failure is also a toast (the poll's
// retries only keep the inline line). A failure never reads as "the trash is empty".
async function loadTrash(loud) {
  const seq = trashSeq;
  let why = null;
  try {
    const r = await fetch('/api/library?trash=1', {cache: 'no-store', credentials: 'same-origin'});
    const d = r.ok ? await r.json() : null;
    if (seq !== trashSeq) return;
    if (!d || !Array.isArray(d.sessions)) {
      why = r.ok ? 'a bad answer' : r.status === 401 ? 'signed out' : 'error ' + r.status;
    } else {
      trash = d.sessions
        .filter(s => s && ID_RE.test(s.id) && typeof s.trashed === 'number')
        .map(s => Object.assign({}, s, {name: String(s.name ?? '')}));
      trashLoaded = true;
      trashFailed = false;
    }
  } catch (err) {
    if (seq !== trashSeq) return;
    why = 'no connection';
  }
  if (why && !trashLoaded) {
    trashFailed = true;
    if (loud && showTrash) _toast('⚠️ Could not load the trash (' + why + ')', 5000);
  }
  renderTrash();
}
function renderTrash() {
  trashView.hidden = !showTrash;
  trashToggle.textContent = showTrash ? 'Hide trash' : 'Show trash';
  trashToggle.classList.toggle('on', showTrash);
  if (!showTrash) return;
  document.getElementById('trashTitle').textContent = trash.length ? 'Trash · ' + trash.length : 'Trash';
  trashEmpty.textContent = trashFailed ? 'Could not load the trash — trying again' : 'The trash is empty';
  trashEmpty.classList.toggle('failed', trashFailed);
  trashEmpty.hidden = !(trashFailed || (trashLoaded && trash.length === 0));
  emptyBtn.hidden = trash.length === 0;
  // rebuilt only when it changed: a poll must not swap a button under the pointer
  const key = JSON.stringify(trash.map(s => [s.id, s.name, s.trashed, !!s.archived]));
  if (key === trashKey) return;
  trashKey = key;
  trashList.textContent = '';
  for (const s of trash) {
    const r = document.createElement('div');
    r.className = 'trash-row';
    r.dataset.tid = s.id;
    r.innerHTML = '<div class="trash-info"><div class="trash-name"></div><div class="when"></div></div>'
      + '<button type="button" class="restore-btn" title="Put it back where it was">Restore</button>';
    const n = r.querySelector('.trash-name');
    n.textContent = s.name;
    n.title = s.name;
    r.querySelector('.when').textContent = s.id + ' · trashed ' + trashedAt(s.trashed)
      + (s.archived ? ' · from the archive' : '');
    r.querySelector('.restore-btn').addEventListener('click', () => untrash(s.id));
    trashList.appendChild(r);
  }
}
// bring the opened view into the list's sight once its rows are in: on a phone the
// list is a few rows tall, so the head goes to the top (the rows below it), never
// only the bottom edge — that showed just half of the red Empty trash
function revealTrash() {
  const l = document.getElementById('list').getBoundingClientRect();
  const v = trashView.getBoundingClientRect();
  if (v.top >= l.top && v.bottom <= l.bottom) return;    // already in full sight
  trashView.scrollIntoView({block: v.height > l.height ? 'start' : 'nearest'});
}
trashToggle.addEventListener('click', async () => {
  showTrash = !showTrash;
  renderTrash();
  if (!showTrash) return;
  revealTrash();                           // the head at once…
  await loadTrash(true);
  if (showTrash) revealTrash();            // …and again with the rows in
});

const _untrashing = new Set();
async function untrash(id) {
  if (_untrashing.has(id)) return;         // a double click is one restore
  _untrashing.add(id);
  const t = trash.find(x => x.id === id);
  const nm = (t ? t.name : id).slice(0, 40);
  try {
    const d = await jpost('/api/library/untrash', {id});
    trashSeq++;
    trash = trash.filter(x => x.id !== id);
    renderTrash();
    if (d && ID_RE.test(d.id || '') && !byId(d.id)) {    // back in the list at once
      const row = Object.assign({}, d, {name: String(d.name ?? '')});
      delete row.trashed;
      sessions.push(row);
      mutSeq++;
      render();
    }
    _toast('Restored “' + nm + '”' + (d && d.archived ? ' to the archive' : ''), 3000);
    poll();
  } catch (err) {
    _toast('⚠️ Not restored: ' + String(err && err.message || err).slice(0, 60), 5000);
  } finally {
    _untrashing.delete(id);
  }
}

// why Empty trash kept one (the server's skipped[].reason)
const KEPT_WHY = {running: 'still running', 'not-trashed': 'restored meanwhile',
                  unknown: 'already gone', corrupt: 'a broken entry', 'erase-failed': "couldn't erase it",
                  'left-on-disk': 'still on disk elsewhere'};
async function emptyTrash() {
  if (oneAtATime()) return;
  const ids = trash.map(s => s.id);        // exactly what this view shows
  if (!ids.length) return;
  _busyWith = 'empty';
  try {
    const n = ids.length;
    if (!await ask({danger: true, title: 'Empty the trash?',
                    text: n + (n === 1 ? ' conversation will' : ' conversations will')
                          + ' be erased for good. This cannot be undone.',
                    ok: 'Erase for good'})) return;
    const d = await jpost('/api/library/empty-trash', {ids});
    const gone = new Set(Array.isArray(d && d.erased) ? d.erased : []);
    const kept = Array.isArray(d && d.skipped) ? d.skipped : [];
    trashSeq++;
    trash = trash.filter(s => !gone.has(s.id));
    renderTrash();
    const why = [...new Set(kept.map(k => KEPT_WHY[k && k.reason] || 'kept'))].join(', ');
    _toast('Erased ' + gone.size + (kept.length ? ' · ' + kept.length + ' kept: ' + why : ''), 5000);
    loadTrash();
  } catch (err) {
    _toast('⚠️ Trash not emptied: ' + String(err && err.message || err).slice(0, 60), 5000);
  } finally {
    _busyWith = null;
  }
}
emptyBtn.addEventListener('click', emptyTrash);
