// The terminals list: lookup, search and order, rows and render(), new topic,
// rename, archive, Restart and its busy questions.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

function byId(id) { return sessions.find(s => s.id === id); }

/* One number per terminal: when the conversation in a terminal changes
   (Claude's consent relaunch, /clear, /resume) the terminal takes the new
   conversation's number. Its row then carries prev_id (the number it had),
   switched_at, and aliases — old numbers that were never a conversation of
   their own, so an old link (?open=, the task board) still finds it. */
function byIdOrAlias(id) {
  return byId(id) || sessions.find(s => Array.isArray(s.aliases) && s.aliases.includes(id));
}
// the latest row that took over from terminal `id`, or null
function switchedFrom(id) {
  let best = null;
  for (const s of sessions) {
    if (s.prev_id === id && typeof s.switched_at === 'number' && s.id !== id
        && (!best || s.switched_at > best.switched_at)) best = s;
  }
  return best;
}
// switched_at of the last switch away from the open terminal already seen:
// a switch newer than that is followed; opening an earlier row on purpose
// (a /clear leaves the old conversation in the list) does not jump back
let _selSwitchSeen = 0;

function norm(q) { return (q || '').trim().toLocaleLowerCase(); }

// same rule as library.search: name substring (any case) or code prefix
function matches(s, q) {
  return !q || s.name.toLocaleLowerCase().includes(q) || s.id.startsWith(q);
}

// Archived topics are listed when "Show archived" is on, and also whenever
// the search box is non-empty (search covers the archive). The poll always
// fetches them (?archived=1), so both switch instantly from the last fetch.
function wantArch() { return showArch || !!norm(search.value); }

// Same rule as library.display_order: the manual order (pos) first, then
// entries from before that rule newest-created first. Never by activity —
// the list must not jump when a topic is opened, loaded or unloaded.
function hasPos(s) { return typeof s.pos === 'number' && isFinite(s.pos); }
function byOrder(a, b) {
  return (hasPos(b) - hasPos(a))
      || (hasPos(a) ? a.pos - b.pos : (b.created || 0) - (a.created || 0));
}

function ordered() {
  const q = norm(search.value);
  const arch = wantArch();
  return sessions
    .map((s, i) => [s, i])
    .filter(([s]) => (arch || !s.archived) && matches(s, q))
    .sort((a, b) => (!!a[0].archived - !!b[0].archived)     // archived last
                 || byOrder(a[0], b[0])
                 || (a[1] - b[1]))
    .map(([s]) => s);
}

function makeRow(id) {
  const c = document.createElement('div');
  c.className = 'card';
  c.dataset.sid = id;
  c.innerHTML = `
        <div class="card-top">
          <div class="proj"></div>
          <div class="card-btns">
            <button type="button" class="card-btn restart" title="Restart Claude in this terminal (same conversation)" aria-label="Restart Claude in this terminal">⟳</button>
            <button type="button" class="card-btn edit" title="Rename">✎</button>
            <span class="dot off"></span>
          </div>
        </div>
        <div class="card-row2">
          <span class="sid" title="Show this terminal's tasks"></span>
          <button type="button" class="mv-btn up" title="Move up" aria-label="Move up">▲</button>
          <button type="button" class="mv-btn down" title="Move down" aria-label="Move down">▼</button>
          <button type="button" class="arch-btn">Archive</button>
          <button type="button" class="trash-btn" title="Move to Trash" aria-label="Move to Trash"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 4.5h11M6 4.5V2.8h4v1.7M4 4.5l.8 9h6.4l.8-9M6.8 7v4M9.2 7v4"/></svg></button>
        </div>`;
  c.querySelector('.sid').textContent = id;
  c.addEventListener('click', e => {
    if (e.target.closest('.card-btn, .arch-btn, .trash-btn, .mv-btn, input')) return;
    if (Date.now() - _dragEndedAt < 300) return;     // the end of a drag is not a click
    if (e.target.closest('.sid')) { openTasks(id); return; }   // code → its tasks (archived too)
    const s = byId(id);
    if (s && s.archived) { restoreAndOpen(id); return; }   // one click: restore + open
    pick(id);
  });
  c.querySelector('.card-btn.edit').addEventListener('click', e => {
    e.stopPropagation();
    startEdit(id);
  });
  c._restartBtn = c.querySelector('.card-btn.restart');   // in the row only while loaded (fill)
  c._restartBtn.addEventListener('click', e => {
    e.stopPropagation();
    restart(id);
  });
  c.querySelector('.arch-btn').addEventListener('click', e => {
    e.stopPropagation();
    archive(id, true);                    // one click; archived rows have no button
  });
  c.querySelector('.mv-btn.up').addEventListener('click', e => { e.stopPropagation(); step(id, -1); });
  c.querySelector('.mv-btn.down').addEventListener('click', e => { e.stopPropagation(); step(id, +1); });
  c.draggable = true;
  c.addEventListener('dragstart', e => {
    if (editing) { e.preventDefault(); return; }
    _dragId = id;
    c.classList.add('dragging');
    try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', id); } catch {}
  });
  c.addEventListener('dragover', e => {
    if (!_dragId || _dragId === id) return;
    e.preventDefault();
    const after = dropAfter(c, e);
    c.classList.toggle('drop-before', !after);
    c.classList.toggle('drop-after', after);
  });
  c.addEventListener('dragleave', () => c.classList.remove('drop-before', 'drop-after'));
  c.addEventListener('drop', e => {
    e.preventDefault();
    c.classList.remove('drop-before', 'drop-after');
    const from = _dragId;
    if (!from || from === id) return;
    const order = ordered().map(s => s.id).filter(x => x !== from);
    const at = order.indexOf(id);
    if (at < 0) return;
    order.splice(dropAfter(c, e) ? at + 1 : at, 0, from);
    applyOrder(order);
  });
  c.addEventListener('dragend', () => {
    c.classList.remove('dragging');
    _dragId = null;
    _dragEndedAt = Date.now();
    for (const el of rows.values()) el.classList.remove('drop-before', 'drop-after');
  });
  c.querySelector('.trash-btn').addEventListener('click', e => {
    e.stopPropagation();
    moveToTrash(id);                      // one question, then the server unloads it
  });
  rows.set(id, c);
  return c;
}

function fill(c, s, firstOff, firstArch) {
  c.classList.toggle('sel', s.id === sel);
  c.draggable = true;
  c.classList.toggle('unloaded', !s.active);
  c.classList.toggle('archived', !!s.archived);
  c.classList.toggle('first-off', firstOff);
  c.classList.toggle('first-arch', firstArch);
  const ab = c.querySelector('.arch-btn');
  // archived rows: no Archive (🗑 takes its place); a click on the row restores it
  ab.hidden = !!s.archived;
  ab.title = 'Archive and unload this terminal (the conversation stays on disk)';
  // ⟳ only on a loaded terminal of our own (not one still in an old numbered
  // terminal); taken out of the row otherwise, not just hidden. "In the row" is
  // rb.parentNode, not isConnected: a new row is filled before it is in the list
  const rb = c._restartBtn;
  if (s.active && !s.archived && typeof s.legacy_path !== 'string') {
    if (!rb.parentNode) c.querySelector('.card-btn.edit').before(rb);
  } else if (rb.parentNode) rb.remove();
  setBtnBusy(rb, _restarting.has(s.id));
  setBtnBusy(c.querySelector('.trash-btn'), _trashing.has(s.id));
  const pe = c.querySelector('.proj');
  if (!pe.querySelector('input')) pe.textContent = s.name;
  pe.title = s.name;
  const st = !s.active ? 'off' : s.status === 'working' ? 'working' : 'idle';
  const dot = c.querySelector('.dot');
  dot.className = 'dot ' + st;
  dot.title = s.archived ? 'archived — click to restore and open'
            : st === 'off' ? 'unloaded — click to load'
            : st === 'working' ? 'loaded, working' : 'loaded, idle';
}

function shellShown() {
  if (!shellState || !shellState.active) return false;
  const q = norm(search.value);
  return !q || 'command line cmd shell bash'.includes(q);
}

function fillShell() {
  shellRow.classList.toggle('sel', sel === SHELL);
  const st = shellState && shellState.status === 'working' ? 'working' : 'idle';
  const dot = shellRow.querySelector('.dot');
  dot.className = 'dot ' + st;
  dot.title = st === 'working' ? 'loaded, working' : 'loaded, idle';
}

function tasksShown() {
  if (!tasksOpen) return false;
  const q = norm(search.value);
  return !q || 'tasks task board'.includes(q);
}

function serverShown() {
  if (!serverOpen) return false;
  const q = norm(search.value);
  return !q || 'server status'.includes(q);
}

function render() {
  if (editing) return;                    // never pull the input out from under the user
  // pinned rows, in this order, above the terminals: Tasks, Server, Command line
  const showTasks = tasksShown();
  const showServer = serverShown();
  const showShell = shellShown();
  tasksRow.classList.toggle('sel', sel === TASKS);
  serverRow.classList.toggle('sel', sel === SERVER);
  if (showShell) fillShell();
  let pin = null;
  for (const [el, on] of [[tasksRow, showTasks], [serverRow, showServer], [shellRow, showShell]]) {
    if (!on) { if (el.parentNode) el.remove(); continue; }
    const ref = pin ? pin.nextSibling : list.firstChild;
    if (el !== ref) list.insertBefore(el, ref);
    pin = el;
  }
  const want = ordered();
  const keep = new Set(want.map(s => s.id));
  for (const [id, el] of rows) if (!keep.has(id)) { el.remove(); rows.delete(id); }
  const anyOn = want.some(s => s.active && !s.archived);
  const anyLive = want.some(s => !s.archived);
  let prev = pin, seenOff = false, seenArch = false;
  for (const s of want) {
    const c = rows.get(s.id) || makeRow(s.id);
    fill(c, s, anyOn && !s.active && !s.archived && !seenOff,
         anyLive && !!s.archived && !seenArch);
    if (!s.active && !s.archived) seenOff = true;
    if (s.archived) seenArch = true;
    const ref = prev ? prev.nextSibling : list.firstChild;
    if (c !== ref) list.insertBefore(c, ref);
    prev = c;
  }
  libEmpty.hidden = want.length > 0;
  libEmpty.textContent = norm(search.value) ? 'No matches'
                                            : 'No terminals yet — click “＋ New terminal”';
  archToggle.textContent = showArch ? 'Hide archived' : 'Show archived';
  archToggle.classList.toggle('on', showArch);
  // always at the bottom: the toggles, then the trash view
  if (list.lastChild !== trashView || trashView.previousSibling !== listFoot) {
    list.appendChild(listFoot);
    list.appendChild(trashView);
  }
}

archToggle.addEventListener('click', () => {
  showArch = !showArch;
  try { localStorage.setItem('lib-show-archived', showArch ? '1' : '0'); } catch {}
  mutSeq++;                                // a poll in flight has the other view
  render();
  poll();
});

function syncSel() {
  for (const [i, el] of rows) el.classList.toggle('sel', i === sel);
  shellRow.classList.toggle('sel', sel === SHELL);
  tasksRow.classList.toggle('sel', sel === TASKS);
  serverRow.classList.toggle('sel', sel === SERVER);
  if (sel === TASKS) {
    document.getElementById('tInfo').innerHTML = '<b>Task board</b>';
  } else if (sel === SERVER) {
    document.getElementById('tInfo').innerHTML = '<b>Server status</b>';
  } else if (sel === SHELL) syncTop(Object.assign({id: SHELL, name: 'Command line'}, shellState || {}));
  else if (sel) syncTop(byId(sel) || {id: sel, name: ''});
}

let _searching = false;
search.addEventListener('input', () => {
  render();                               // instant, from the last poll
  const now = !!norm(search.value);
  if (now && !_searching) poll();         // then refresh the archive in the background
  _searching = now;
});
search.addEventListener('keydown', e => {
  if (e.key === 'Enter') {                 // Enter = open the top match
    e.preventDefault();
    const first = ordered().find(s => !s.archived);
    if (first) pick(first.id);
  } else if (e.key === 'Escape') {
    search.value = ''; _searching = false; render();
  }
});

/* ── ＋ New topic: ask for a name inline, create, open ── */
let _creating = false;
function openNewForm() {
  libTools.classList.add('creating');
  newForm.hidden = false;
  newName.value = '';
  newName.focus();
}
function closeNewForm() {
  libTools.classList.remove('creating');
  newForm.hidden = true;
}
newBtn.addEventListener('click', openNewForm);
document.getElementById('newCancel').addEventListener('click', closeNewForm);
newName.addEventListener('keydown', e => {
  if (e.key === 'Escape') { e.preventDefault(); closeNewForm(); }
});
newForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (_creating) return;
  _creating = true; newOk.disabled = true;
  const name = newName.value.trim();       // empty → the server names it «Terminal DD.MM HH:MM»
  try {
    const d = await jpost('/api/library/new', {name});
    const s = d && (d.session || d);
    if (!s || !ID_RE.test(s.id)) throw new Error('the server returned no terminal code');
    mutSeq++;
    if (!byId(s.id)) sessions.push(Object.assign({}, s, {name: String(s.name ?? name)}));
    closeNewForm();
    search.value = ''; _searching = false;
    render();
    pick(s.id);
    setTimeout(poll, 1500);                // pick up "loaded" once ttyd has started it
  } catch (err) {
    _toast('⚠️ New terminal: ' + String(err && err.message || err).slice(0, 80), 5000);
  } finally {
    _creating = false; newOk.disabled = false;
  }
});

/* ── ✎ rename (inline, like the old project-name editor) ── */
function startEdit(id) {
  const c = rows.get(id);
  if (!c) return;
  const pe = c.querySelector('.proj');
  if (pe.querySelector('input')) return;
  const s = byId(id);
  const cur = s ? s.name : pe.textContent;
  const inp = document.createElement('input');
  inp.className = 'proj-edit';
  inp.value = cur;
  inp.maxLength = 200;
  inp.placeholder = 'Terminal name';
  pe.textContent = '';
  pe.appendChild(inp);
  c.draggable = false;                     // let the mouse select text in the input
  editing = id;
  inp.focus();
  inp.select();

  let done = false;
  function finish(save) {
    if (done) return;
    done = true;
    editing = null;
    const val = inp.value.trim();
    const changed = save && val && val !== cur;
    pe.textContent = changed ? val : cur;  // also drops the input
    if (changed) {
      mutSeq++;
      if (s) s.name = val;
      jpost('/api/library/rename', {id, name: val}).catch(err => {
        if (s && s.name === val) s.name = cur;
        render(); syncSel();
        _toast('⚠️ Not renamed: ' + String(err && err.message || err).slice(0, 60), 5000);
      });
      if (id === sel) syncSel();
    }
    render();
  }
  inp.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  inp.addEventListener('blur', () => finish(true));
  inp.addEventListener('click', e => e.stopPropagation());
}

/* ── Archive / Restore: one click. Archive also unloads the terminal (the
   server keeps a working one loaded until it finishes and says so); if it
   is the one in the viewer, the viewer goes back to the placeholder.
   Archived topics stay reachable via "Show archived" and search; a click
   on an archived row restores it and opens it. No confirm step. ── */
async function archive(id, archived) {
  const s = byId(id);
  const was = s ? !!s.archived : !archived;
  if (s) s.archived = archived;
  if (archived && sel === id) showPlaceholder();
  mutSeq++;
  render();
  try {
    const d = await jpost('/api/library/archive', {id, archived});
    const nm = (s ? s.name : id).slice(0, 40);
    if (s && d && typeof d.active === 'boolean') {
      s.active = d.active;
      if (typeof d.status === 'string') s.status = d.status;
      render();
    }
    _toast(!archived ? 'Restored “' + nm + '”'
           : d && d.unloaded ? 'Archived and unloaded “' + nm + '”'
           : d && d.unload_pending ? "Archived — it's busy, it will unload when it finishes"
           : 'Archived “' + nm + '” — the conversation is kept', 3000);
    return true;
  } catch (err) {
    if (s) s.archived = was;
    render();
    _toast('⚠️ ' + (archived ? 'Not archived: ' : 'Not restored: ')
           + String(err && err.message || err).slice(0, 60), 5000);
    return false;
  }
}

/* ── ⟳ Restart (loaded rows): Claude starts again in the same conversation,
   so new launch settings take effect and a hung Claude comes back. ONE
   question (owner: two in a row was awkward): ask the server first whether
   it is busy (check:true), then a plain "Restart?" for an idle terminal or a
   red one with "!" naming what it is doing, which sends force:true. Only if
   it turned busy after the plain question does the server's 409 bring the
   red one up. If it is the terminal in the viewer, its frame is opened again
   so the tab attaches cleanly. ── */
// lib_busy_reason's reasons in words; the red title says what the terminal is
// doing, not always "working"
const BUSY_WORDS = {printing: 'it is printing right now', hold: 'a timer is pending',
                    'background-task': 'a background task is running',
                    working: 'it is working'};
const BUSY_TITLE = {printing: 'is working right now', working: 'is working right now',
                    hold: 'is waiting on a timer',
                    'background-task': 'has a background task running'};
// one at a time (⟳, 🗑, Empty trash): a 2nd question must not replace the 1st, and
// nothing new starts while one is still running on the server. The refusal says
// which: a question on screen, or what is still running.
let _busyWith = null;            // null | 'restart' | 'trash' | 'empty'
const BUSY_WAIT = {restart: 'Wait for the restart to finish',
                   trash: 'Wait for Move to Trash to finish',
                   empty: 'Wait for the trash to be emptied'};
function oneAtATime() {
  if (!_busyWith) return false;
  const d = document.getElementById('confirmDlg');
  _toast(d && d.open ? 'One question at a time' : BUSY_WAIT[_busyWith], 2500);
  return true;
}
function setBtnBusy(b, on) {
  if (!b) return;
  b.classList.toggle('busy', on);
  b.setAttribute('aria-busy', on ? 'true' : 'false');
}
// the page's own question (not confirm(): it can't look different when it matters).
// q = {title, text, ok, danger}: danger = red with "!", and Cancel has the focus,
// so Enter never interrupts work or erases anything
function ask(q) {
  const d = document.getElementById('confirmDlg');
  const ok = d.querySelector('.dlg-ok'), no = d.querySelector('.dlg-cancel');
  d.classList.toggle('danger', !!q.danger);
  d.setAttribute('role', q.danger ? 'alertdialog' : 'dialog');
  d.querySelector('#confirmDlgTitle').textContent = q.title;
  d.querySelector('#confirmDlgText').textContent = q.text;
  ok.textContent = q.ok;
  return new Promise(resolve => {
    const done = yes => {
      ok.removeEventListener('click', onOk);
      no.removeEventListener('click', onNo);
      d.removeEventListener('cancel', onEsc);
      if (d.open) d.close();
      resolve(yes);
    };
    const onOk = () => done(true), onNo = () => done(false);
    const onEsc = e => { e.preventDefault(); done(false); };
    ok.addEventListener('click', onOk);
    no.addEventListener('click', onNo);
    d.addEventListener('cancel', onEsc);
    d.showModal();
    (q.danger ? no : ok).focus();
  });
}
function askRestart(nm, reason) {
  return ask(reason
    ? {danger: true, title: '“' + nm + '” ' + (BUSY_TITLE[reason] || 'is busy right now'),
       text: 'Restarting now will interrupt it (' + (BUSY_WORDS[reason] || 'it is busy')
             + '). Claude starts again in the same conversation.',
       ok: 'Restart anyway'}
    : {title: 'Restart “' + nm + '”?', text: 'Claude starts again in the same conversation.',
       ok: 'Restart'});
}
async function restart(id) {
  if (_restarting.has(id)) return;         // busy: a double click is one restart
  if (oneAtATime()) return;
  const s = byId(id);
  const nm = s ? s.name : id;
  _restarting.add(id);
  _busyWith = 'restart';
  setBtnBusy(rows.get(id) && rows.get(id)._restartBtn, true);
  try {
    const c = await jpost('/api/library/restart-check', {id});
    const reason = (c && c.busy) || null;
    if (!await askRestart(nm, reason)) return;
    let d;
    try {
      d = await jpost('/api/library/restart', reason ? {id, force: true} : {id});
    } catch (err) {
      const why = !reason && err && err.status === 409 && err.data && BUSY_WORDS[err.data.reason];
      if (!why) throw err;
      if (!await askRestart(nm, err.data.reason)) return;   // turned busy since we asked
      d = await jpost('/api/library/restart', {id, force: true});
    }
    const nid = d && ID_RE.test(d.id || '') ? d.id : id;
    if (s && d && typeof d.active === 'boolean') {
      s.active = d.active;
      if (typeof d.status === 'string') s.status = d.status;
    }
    mutSeq++;
    if (sel === id || sel === nid) pick(nid);   // a fresh frame attaches to the new Claude
    render();
    _toast('Restarted “' + nm.slice(0, 40) + '”', 3000);
    poll();
  } catch (err) {
    _toast('⚠️ Not restarted: ' + String(err && err.message || err).slice(0, 60), 5000);
  } finally {
    _restarting.delete(id);
    _busyWith = null;
    setBtnBusy(rows.get(id) && rows.get(id)._restartBtn, false);
  }
}

// an archived row / board link: restore (same API as before), then open it
// like a normal row — the terminal endpoint refuses archived ids, so only
// after the server has said yes
const _restoring = new Set();
async function restoreAndOpen(id) {
  if (_restoring.has(id)) return;          // a double click is one restore
  _restoring.add(id);
  try {
    if (await archive(id, false)) pick(id);
  } finally {
    _restoring.delete(id);
  }
}
