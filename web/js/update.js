// Updates (owner 19.11.2026): "Update available: vX.Y.Z" above the version at the bottom of
// the sidebar (on phones: an item in the ⋯ menu), a dialog with what changed and the Update
// button, the night-time patch setting, and the result of the last update until it is read.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.
(function updates() {
  const $ = id => document.getElementById(id);
  const box = $('sideUpd'), line = $('updLine'), dlg = $('updDlg');
  if (!box || !dlg) return;
  let st = null, busy = null, note = '', timer = null;

  async function load() {
    try {
      const r = await fetch('/api/update', {cache: 'no-store'});
      if (!r.ok) return null;
      const d = await r.json();
      return d && ['git', 'docker', 'manual', 'off'].includes(d.kind) ? d : null;
    } catch (e) { return null; }
  }
  async function post(what, body) {
    try {
      const r = await fetch('/api/update/' + what, {method: 'POST', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
      let d = {};
      try { d = await r.json(); } catch (e) {}
      return {ok: r.ok, status: r.status, d};
    } catch (e) { return {ok: false, status: 0, d: {error: 'the panel did not answer'}}; }
  }

  // the one line: [text, class] or null
  function summary(d) {
    if (!d || d.kind === 'off') return null;
    if (d.job) return d.job.state === 'lost' ? ['Update to v' + d.job.to + ' stopped', 'bad']
                                             : ['Updating to v' + d.job.to + '…', 'run'];
    if (d.last) return d.last.state === 'done' ? ['Updated to v' + d.last.to, 'good']
                                               : ['Update to v' + d.last.to + ' failed', 'bad'];
    if (d.pending) return ['Update to ' + d.pending.tag + ' waits for idle agents', 'run'];
    if (d.available) return ['Update available: v' + d.latest, ''];
    return null;
  }

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  // CHANGELOG sections -> HTML: escaped first, then ### headings, - bullets, **bold**, `code`
  function notesHtml(notes) {
    let out = '';
    for (const s of notes || []) {
      out += '<h3>v' + esc(s.version) + (s.title ? ' — ' + esc(s.title) : '') + '</h3>';
      let inList = false;
      for (const raw of String(s.body || '').split('\n')) {
        const l = raw.trim();
        const li = /^[-*] /.test(l);
        if (inList && !li) { out += '</ul>'; inList = false; }
        if (!l) continue;
        if (/^#{3,} /.test(l)) out += '<h4>' + inline(l.replace(/^#+ /, '')) + '</h4>';
        else if (li) { if (!inList) { out += '<ul>'; inList = true; } out += '<li>' + inline(l.slice(2)) + '</li>'; }
        else out += '<p>' + inline(l) + '</p>';
      }
      if (inList) out += '</ul>';
    }
    return out;
  }

  function menuItem(sum) {
    let it = $('tUpd');
    if (!sum) { if (it) it.remove(); return; }
    if (!it) {
      it = document.createElement('button');
      it.type = 'button'; it.className = 'btn'; it.id = 'tUpd'; it.setAttribute('role', 'menuitem');
      it.addEventListener('click', e => { e.stopPropagation(); $('tMenuList').hidden = true; $('tMenu').setAttribute('aria-expanded', 'false'); open(); });
      $('tMenuList').insertBefore(it, $('tLogout'));
    }
    it.textContent = sum[0];
  }

  function message(d) {
    const j = d.job, last = d.last;
    if (j && j.state === 'lost') return [j.error || 'The update stopped.', 'bad'];
    if (j) return ['Updating: ' + (j.step || 'starting') + '. The panel restarts by itself; your terminals keep running.', 'warn'];
    if (last) {
      if (last.state === 'done') return ['Updated from v' + last.from + ' to v' + last.to + '.' +
        (last.todo && last.todo.length ? ' ' + last.todo.join(' ') : ''), 'good'];
      let t = (last.state === 'refused' ? 'Not updated: ' : 'The update failed at ' + (last.step || 'a step') + ': ') + (last.error || 'unknown error') + '.';
      if (last.state === 'refused') t += ' Nothing was changed.';
      if (last.state === 'rolled-back') t += ' Rolled back: v' + last.from + ' runs as before.';
      if (last.state === 'rollback-failed') t += ' The rollback failed too (' + (last.rollback_error || '?') + '): fix it by hand; the backup is ' + (last.backup || 'in .sessions/update/backups') + '.';
      return [t, 'bad'];
    }
    if (d.pending) return ['Waiting until no agent is working, then it updates to ' + d.pending.tag + '.', 'warn'];
    if (busy && busy.length) return [(busy.length === 1 ? '1 agent is' : busy.length + ' agents are') + ' working: ' + busy.join(', ') +
      '. Updating restarts the panel only, but not while they work.', 'warn'];
    if (note) return [note, ''];
    if (d.error) return ['Could not check for a new release: ' + d.error, 'bad'];
    return ['', ''];
  }

  function renderDialog(d) {
    const avail = d.available, job = d.job, last = d.last;
    const to = (job && job.to) || (last && last.to) || (avail ? d.latest : d.current);
    $('updTitle').textContent = 'AgentKennel v' + to;
    $('updSub').textContent = job || last ? 'You had v' + ((job || last).from || d.current)
      : avail ? 'You have v' + d.current + (d.patch ? ' · a patch release' : '')
      : 'You have the latest release' + (d.latest ? ' (v' + d.latest + ')' : '');
    $('updNotes').innerHTML = notesHtml(last && !job ? last.notes : (avail ? d.notes : []));
    const cmd = avail && (d.kind === 'docker' || d.kind === 'manual') ? d.command : '';
    $('updCmd').hidden = !cmd;
    $('updCmdText').textContent = cmd || '';
    const steps = (job || last || {}).steps || [];
    $('updSteps').hidden = !steps.length && !job;
    $('updSteps').innerHTML = steps.map(s => '<li class="' + (s.ok ? 'ok' : 'bad') + '">' + esc(s.step) + (s.ok ? '' : ': ' + esc(s.detail)) + '</li>').join('') +
      (job && job.state !== 'lost' ? '<li class="now">' + esc(job.step || 'starting') + '…</li>' : '');
    const [m, cls] = message(d);
    $('updMsg').textContent = m; $('updMsg').className = cls;
    const git = d.kind === 'git';
    $('updAutoRow').hidden = !git;
    $('updAuto').checked = !!d.auto;
    $('updAutoSeries').textContent = String(d.current || '').split('.').slice(0, 2).join('.') + '.x';
    const quiet = !job && !d.pending;
    $('updGo').hidden = !(git && avail && quiet && !(busy && busy.length));
    $('updIdle').hidden = !(git && avail && quiet && busy && busy.length);
    $('updCancel').hidden = !d.pending;
    $('updCheck').disabled = !!job;
  }

  function render() {
    const sum = summary(st);
    box.hidden = !sum;
    if (sum) { line.textContent = sum[0]; line.className = 'upd-line' + (sum[1] ? ' ' + sum[1] : ''); }
    menuItem(sum);
    if (dlg.open && st) renderDialog(st);
  }

  async function refresh() {
    const d = await load();
    if (d) st = d;
    render();
    clearTimeout(timer);
    timer = setTimeout(refresh, st && (st.job || dlg.open) ? 2000 : 60000);
  }

  function open() {
    if (!st || st.kind === 'off') return;
    note = '';
    renderDialog(st);
    if (!dlg.open) dlg.showModal();
    refresh();
  }

  line.addEventListener('click', open);
  $('updClose').addEventListener('click', () => dlg.close());
  dlg.addEventListener('close', () => {
    busy = null; note = '';
    if (st && st.last && !st.job) post('seen').then(r => { if (r.ok) { st = r.d; render(); } });
  });
  $('updCopy').addEventListener('click', () => {
    navigator.clipboard.writeText($('updCmdText').textContent).then(
      () => { note = 'Copied: run it in the folder with docker-compose.yml.'; render(); }, () => {});
  });
  async function act(what, body, btn) {
    if (btn) btn.disabled = true;
    const r = await post(what, body);
    if (btn) btn.disabled = false;
    if (r.ok) { st = r.d; busy = null; }
    else if (r.status === 409 && r.d.busy) busy = r.d.busy;
    else note = 'Not done: ' + (r.d.error || 'HTTP ' + r.status);
    render();
    clearTimeout(timer);
    timer = setTimeout(refresh, 2000);
    return r;
  }
  $('updGo').addEventListener('click', e => act('start', {tag: st.tag}, e.currentTarget));
  $('updIdle').addEventListener('click', e => act('start', {tag: st.tag, when_idle: true}, e.currentTarget));
  $('updCancel').addEventListener('click', e => act('cancel', {}, e.currentTarget));
  $('updAuto').addEventListener('change', e => act('auto', {enabled: e.currentTarget.checked}));
  $('updCheck').addEventListener('click', async e => {
    const r = await act('check', {}, e.currentTarget);
    if (r.ok && !r.d.available && !r.d.error) { note = 'No new release: v' + r.d.current + ' is the latest.'; render(); }
  });

  refresh();
})();
