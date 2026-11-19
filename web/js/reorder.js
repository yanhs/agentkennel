// Manual order: drag a row (desktop) or the arrows (touch).
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

/* ── Manual order: drag a row (desktop) or ▲▼ (touch). Optimistic: the
   list moves at once, POST /api/library/reorder with the full visible
   order; on error the old order comes back with a toast. ── */
let _dragId = null, _dragEndedAt = 0;
try { if (navigator.maxTouchPoints > 0 || 'ontouchstart' in window)
        document.documentElement.classList.add('touch'); } catch {}

function dropAfter(c, e) {
  const r = c.getBoundingClientRect();
  return e.clientY > r.top + r.height / 2;
}

function step(id, d) {
  const order = ordered().map(s => s.id);
  const i = order.indexOf(id), j = i + d;
  if (i < 0 || j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  applyOrder(order);
}

async function applyOrder(ids) {
  const was = new Map(sessions.map(s => [s.id, s.pos]));
  ids.forEach((id, i) => { const s = byId(id); if (s) s.pos = i; });
  mutSeq++;
  render();
  try {
    await jpost('/api/library/reorder', {ids});
  } catch (err) {
    for (const s of sessions) { if (was.has(s.id)) s.pos = was.get(s.id); }
    mutSeq++;
    render();
    _toast('⚠️ Not reordered: ' + String(err && err.message || err).slice(0, 60), 5000);
  }
}

/* ── 🗑 Trash (owner 2026-10-23). Archive = needed, just not now; Trash = not
   needed, kept just in case. 🗑 on every terminal row (the list and the
   archive) asks ONE question — plain, or red with "!" naming what a busy
   terminal is doing, which then forces it; the server unloads a loaded one.
   Trashed terminals are in no list: "Show trash" lists them, each with
   Restore (back where it was — an archived one stays archived). Nothing is
   erased by itself: only "Empty trash", after a red question with the count,
   and only the ones this view showed. ── */
function askTrash(s, nm, reason) {
  return ask(reason
    ? {danger: true, title: '“' + nm + '” ' + (BUSY_TITLE[reason] || 'is busy right now'),
       text: 'Moving it to the trash now will interrupt it (' + (BUSY_WORDS[reason] || 'it is busy')
             + '). The terminal is unloaded; the conversation is kept in the trash.',
       ok: 'Move to Trash anyway'}
    : {title: 'Move “' + nm + '” to Trash?',
       text: (s && s.active ? 'The terminal is unloaded. ' : '')
             + 'The conversation is kept in the trash until you empty it.',
       ok: 'Move to Trash'});
}
async function moveToTrash(id) {
  if (_trashing.has(id)) return;           // a double click is one question
  if (oneAtATime()) return;
  const s = byId(id);
  const nm = s ? s.name : id;
  _trashing.add(id);
  _busyWith = 'trash';
  setBtnBusy(rows.get(id) && rows.get(id).querySelector('.trash-btn'), true);   // pulses while it unloads
  try {
    const c = await jpost('/api/library/trash-check', {id});
    const reason = (c && c.busy) || null;
    if (!await askTrash(s, nm, reason)) return;
    try {
      await jpost('/api/library/trash', reason ? {id, force: true} : {id});
    } catch (err) {
      const why = !reason && err && err.status === 409 && err.data && BUSY_WORDS[err.data.reason];
      if (!why) throw err;
      if (!await askTrash(s, nm, err.data.reason)) return;   // turned busy since we asked
      await jpost('/api/library/trash', {id, force: true});
    }
    const i = sessions.findIndex(x => x.id === id);
    if (i >= 0) sessions.splice(i, 1);
    mutSeq++;
    if (sel === id) showPlaceholder();
    render();
    _toast('Moved “' + nm.slice(0, 40) + '” to Trash', 3000);
    if (showTrash) loadTrash();
  } catch (err) {
    _toast('⚠️ Not moved to Trash: ' + String(err && err.message || err).slice(0, 70), 5000);
  } finally {
    _trashing.delete(id);
    _busyWith = null;
    setBtnBusy(rows.get(id) && rows.get(id).querySelector('.trash-btn'), false);
  }
}
