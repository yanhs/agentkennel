// Pasting / dropping images and files into a terminal: upload, then type the path.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

/* ── Paste / drop an image into the terminal: a ttyd/tmux terminal is text-only,
   so an image can't be pasted. Upload it, then type the saved file PATH into the
   terminal — Claude Code auto-attaches the image path as [Image]. Visible toasts
   so it's obvious what happened; handlers on BOTH the iframe and the parent doc
   (the paste fires wherever focus is). ── */
function _toast(msg, ms) {
  let t = document.getElementById('_imgToast');
  if (!t) {
    t = document.createElement('div'); t.id = '_imgToast';
    t.style.cssText = 'position:fixed;bottom:18px;left:50%;transform:translateX(-50%);z-index:99999;' +
      'background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px 14px;' +
      'font:13px/1.3 Inter,system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.5);opacity:0;transition:opacity .15s;pointer-events:none';
    document.body.appendChild(t);
  }
  t.textContent = msg; t.style.opacity = '1';
  clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = '0'; }, ms || 2600);
}

// diagnostics disabled now that image paste works (kept as a no-op so the call
// sites stay harmless; flip the body back on to debug again)
function _clientlog() {}

function _imageFromData(dt) {
  const items = (dt && dt.items) || [];
  for (const it of items) { if (it.kind === 'file' && (it.type || '').startsWith('image/')) return it.getAsFile(); }
  const files = (dt && dt.files) || [];
  for (const fl of files) { if ((fl.type || '').startsWith('image/')) return fl; }
  return null;
}

// read the clipboard image directly (used by the 📎 button AND the Ctrl+V hotkey)
async function _pasteImageFromClipboard(src) {
  _clientlog({ ev: 'read-start', src: src || '?', hasAPI: !!(navigator.clipboard && navigator.clipboard.read), focused: document.hasFocus() });
  if (!navigator.clipboard || !navigator.clipboard.read) {
    _toast('⚠️ Browser can\'t read the clipboard — drag an image file onto the terminal', 5000); return;
  }
  try { window.focus(); } catch (_) {}              // clipboard.read() needs a focused doc
  _toast('📎 Reading clipboard…', 8000);
  try {
    const items = await navigator.clipboard.read();
    let blob = null; const allTypes = [];
    for (const it of items) {
      (it.types || []).forEach(t => allTypes.push(t));
      const type = (it.types || []).find(t => t.startsWith('image/'));
      if (type) { blob = await it.getType(type); break; }
    }
    _clientlog({ ev: 'read-done', src: src || '?', types: allTypes, gotImage: !!blob });
    if (!blob) {
      _toast('⚠️ No image in clipboard. Types: ' + (allTypes.join(', ') || 'none'), 6000);
      return;
    }
    await _uploadAndInsertImage(blob);
  } catch (e) {
    _clientlog({ ev: 'read-error', src: src || '?', error: String(e && e.message || e) });
    _toast('⚠️ Clipboard: ' + String(e && e.message || e).slice(0, 70), 6000);
  }
}

// Ctrl+V (no Shift) is the standard paste, like any app: we only stop xterm from
// turning it into a ^V byte and let the BROWSER paste — the native 'paste' event
// carries the clipboard's text and files (no permission prompt, works on http).
// Text goes to the terminal through xterm's own paste listener (exactly once);
// an image is caught by _onImagePaste and uploaded. That is also what makes
// dictation apps work that put text on the clipboard and press Ctrl+V for you
// (Wispr Flow). On a Mac, Ctrl+V is not the paste key (⌘V is, and it is left to
// the browser), so there Ctrl+V keeps reading an image straight off the clipboard.
// Ctrl+Shift+V stays the terminal's text paste.
// Ctrl+C (no Shift) in the terminal copies a selection, like Windows Terminal,
// and only sends the interrupt (^C) when nothing is selected. Not on a Mac (⌘C).
// tmux cancels its selection on mouseup (copy-selection-and-cancel), so nothing stays
// highlighted: the drag counts for Ctrl+C only RIGHT after it (a few seconds), in the
// same terminal — otherwise Claude's interrupt would be silently swallowed later.
let _tmuxSel = null;          // {text|null, doc, at} — the last mouse selection copied from tmux
const TMUX_SEL_MS = 3000;
// Ctrl+<letter> by the key's CODE when the layout isn't Latin (Greek: Ctrl+V is 'ω'; Russian, Ukrainian … likewise)
function _ctrlLetter(e) {
  const k = (e.key || '').toLowerCase();
  if (/^[a-z]$/.test(k)) return k;
  const m = /^Key([A-Z])$/.exec(e.code || '');
  if (m) return m[1].toLowerCase();
  // simulated keys (Wispr Flow and other dictation apps press Ctrl+V for you) come
  // without a physical code on a non-Latin layout: only keyCode tells the letter
  const kc = e.keyCode || e.which || 0;
  return (kc >= 65 && kc <= 90) ? String.fromCharCode(kc + 32) : k;
}
function _isModifierKey(e) { return /^(Control|Shift|Alt|Meta|AltGraph|CapsLock)$/.test(e.key || ''); }
// nav: the navigator whose clipboard to write (default: the dashboard's)
function _copyText(doc, t, nav) {
  const viaExec = () => {
    try {
      const ta = doc.createElement('textarea'); ta.value = t;
      ta.style.cssText = 'position:fixed;opacity:0;left:0;top:0';
      const back = doc.activeElement;
      doc.body.appendChild(ta); ta.select();
      const ok = doc.execCommand('copy'); ta.remove();
      try { back && back.focus && back.focus(); } catch (_) {}
      if (ok) showCopyHint();
    } catch (_) {}
  };
  const clip = (nav || navigator).clipboard;
  if (clip && clip.writeText) clip.writeText(t).then(showCopyHint, viaExec);
  else viaExec();
}
function _copySelectionOnCtrlC(e) {
  const doc = (e.target && e.target.ownerDocument) || document;
  const term = doc.defaultView && doc.defaultView.term;
  let text = '';
  try { if (term && term.hasSelection && term.hasSelection()) text = term.getSelection() || ''; } catch (_) {}
  if (text) {
    try { term.clearSelection(); } catch (_) {}
    _tmuxSel = null;
  } else if (_tmuxSel && _tmuxSel.text !== '' && _tmuxSel.doc === doc
             && Date.now() - _tmuxSel.at < TMUX_SEL_MS) {
    const sel = _tmuxSel; _tmuxSel = null;
    if (sel.text) text = sel.text;
    else {                                         // the buffer read is still running
      e.preventDefault(); e.stopPropagation(); if (e.stopImmediatePropagation) e.stopImmediatePropagation();
      sel.promise.then(t => { if (t && t.trim()) _copyText(doc, t); });
      return true;
    }
  }
  if (!text) return false;
  e.preventDefault(); e.stopPropagation(); if (e.stopImmediatePropagation) e.stopImmediatePropagation();
  _copyText(doc, text);
  return true;
}
function _onTermKeydown(e, scope) {
  const key = _ctrlLetter(e);
  const ctrlC = scope !== 'parent' && !_isMac && e.ctrlKey && !e.shiftKey && !e.altKey
                && !e.metaKey && key === 'c';
  if (ctrlC) { if (!_copySelectionOnCtrlC(e)) _tmuxSel = null; return; }
  if (scope !== 'parent' && !_isModifierKey(e)) _tmuxSel = null;   // typing ends the selection
  if (!e.ctrlKey || e.altKey || e.metaKey) return;
  if (key !== 'v') return;
  _clientlog({ ev: 'ctrlv-seen', scope: scope || 'iframe', shift: e.shiftKey });
  if (e.shiftKey) return;                         // Ctrl+Shift+V = text paste, leave it
  // on the parent, don't touch Ctrl+V in real text fields (project name, etc.)
  if (scope === 'parent') {
    const t = e.target, tag = (t && t.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || (t && t.isContentEditable)) return;
  }
  if (_isMac) {                                   // Mac: Ctrl+V = image hotkey (⌘V pastes)
    e.preventDefault(); e.stopPropagation();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
    _pasteImageFromClipboard('ctrlv-' + (scope || 'iframe'));
    return;
  }
  // keep xterm from sending ^V, but NOT preventDefault: the browser's paste follows
  e.stopPropagation();
  if (e.stopImmediatePropagation) e.stopImmediatePropagation();
}
// page-level Ctrl+V (focus on the dashboard / a button, not inside the terminal)
document.addEventListener('keydown', (e) => _onTermKeydown(e, 'parent'), true);

// type text into the open terminal as a paste; returns how ('none' = not ready)
function _insertIntoTerminal(f, text) {
  // ttyd's xterm has no .input(); term.paste() is the proper API, _onData.fire fallback
  let term = null;
  try { term = f.contentWindow.term; } catch (_) {}
  let how = 'none';
  if (term && typeof term.paste === 'function') { term.paste(text); how = 'paste'; }
  else if (term && typeof term.input === 'function') { term.input(text); how = 'input'; }
  else if (term && term._core && term._core._onData) { try { term._core._onData.fire(text); how = 'onData'; } catch (_) {} }
  if (how === 'none') return how;
  // restore focus to the terminal's INPUT (not just the iframe window) so Enter
  // sends immediately — the button click / Ctrl+V had moved focus away.
  const refocus = () => {
    try { f.contentWindow.focus(); } catch (_) {}
    try { if (term && typeof term.focus === 'function') term.focus(); } catch (_) {}
    try {
      const ta = (f.contentDocument || f.contentWindow.document).querySelector('.xterm-helper-textarea');
      if (ta) ta.focus();
    } catch (_) {}
  };
  refocus();
  setTimeout(refocus, 60);                        // again after the gesture settles
  return how;
}

async function _uploadAndInsertImage(blob) {
  const f = document.querySelector('#wrap iframe');
  _clientlog({ ev: 'upload-start', size: blob && blob.size, type: blob && blob.type, hasTerminal: !!f });
  if (!f) { _toast('⚠️ Select a terminal first'); return; }
  _toast('📎 Uploading image…', 10000);
  try {
    const r = await fetch('/api/paste-image', {
      method: 'POST', headers: { 'Content-Type': blob.type || 'image/png' },
      body: blob, credentials: 'same-origin',
    });
    if (!r.ok) { _clientlog({ ev: 'upload-http-fail', status: r.status }); _toast('⚠️ Upload error (HTTP ' + r.status + ')'); return; }
    const d = await r.json();
    if (!d || !d.path) { _toast('⚠️ Server returned no path'); return; }
    if (_insertIntoTerminal(f, d.path + ' ') === 'none') { _toast('⚠️ Terminal not ready'); return; }
    _toast('✅ Image pasted');
  } catch (e) { _clientlog({ ev: 'upload-error', error: String(e && e.message || e) }); _toast('⚠️ ' + String(e && e.message || e).slice(0, 50)); }
}

/* ── 📎 File: any file → a private folder on the server → its PATH into the terminal ──
   A path, not a link: the agent runs on the server and reads the file there. XHR, not
   fetch, for the upload progress. Several files go one after another; the paths are
   typed into the open terminal space-separated and copied to the clipboard. */
function _uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/upload?name=' + encodeURIComponent(file.name || 'file'));
    x.withCredentials = true;
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = e => { if (e.lengthComputable && e.total) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let d = null;
      try { d = JSON.parse(x.responseText); } catch (_) {}
      if (x.status === 200 && d && d.path) return resolve(d);
      // 413 from status_server carries its cap; one from a proxy in front (nginx,
      // Cloudflare) is an HTML page with a smaller limit we don't know
      if (x.status === 413) return reject(new Error(d && d.max_mb ? 'too large (max ' + d.max_mb + ' MB)'
                                                                   : 'too large for this server'));
      if (x.status === 401) return reject(new Error('signed out — reload the page'));
      reject(new Error((d && d.error) || ('HTTP ' + x.status)));
    };
    x.onerror = () => reject(new Error('network error'));
    x.send(file);
  });
}

async function _copyQuiet(text, f) {
  // the clipboard of whichever document has focus accepts the write
  const navs = [];
  try { if (f && f.contentDocument && f.contentDocument.hasFocus()) navs.push(f.contentWindow.navigator); } catch (_) {}
  navs.push(navigator);
  for (const n of navs) {
    try { await n.clipboard.writeText(text); return true; } catch (_) {}
  }
  return false;
}

let _uploading = false;
async function _uploadFiles(files) {
  files = Array.from(files || []).filter(Boolean);
  if (!files.length) return;
  if (_uploading) { _toast('⏳ Still uploading — wait a moment'); return; }
  _uploading = true;
  const paths = [], failed = [];
  try {
    for (let i = 0; i < files.length; i++) {
      const fl = files[i], of = files.length > 1 ? ' (' + (i + 1) + '/' + files.length + ')' : '';
      _toast('📎 Uploading ' + fl.name + of + '…', 120000);
      try {
        const d = await _uploadFile(fl, p =>
          _toast('📎 Uploading ' + fl.name + of + '… ' + Math.round(p * 100) + '%', 120000));
        paths.push(d.path);
      } catch (e) { failed.push(fl.name + ': ' + (e && e.message || e)); }
    }
  } finally { _uploading = false; }
  if (!paths.length) { _toast('⚠️ ' + failed.join(' · '), 8000); return; }
  const text = paths.join(' ');
  const f = document.querySelector('#wrap iframe');
  const typed = !!f && _insertIntoTerminal(f, text + ' ') !== 'none';
  const copied = await _copyQuiet(text, f);
  const what = paths.length > 1 ? paths.length + ' files' : 'File';
  const where = typed ? (copied ? 'path in the terminal and copied' : 'path in the terminal')
                      : (copied ? 'path copied' : text);
  _toast('✅ ' + what + ' uploaded — ' + where + (failed.length ? ' · ⚠️ ' + failed.join(' · ') : ''),
         failed.length ? 8000 : 4000);
}

// files dragged over the dashboard or the terminal: show the drop frame
let _dropTimer = 0;
function _isFileDrag(e) { return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')); }
function _dropHint(on) {
  const w = document.getElementById('wrap');
  clearTimeout(_dropTimer);
  if (!w) return;
  w.classList.toggle('drop-files', on);
  if (on) _dropTimer = setTimeout(() => w.classList.remove('drop-files'), 400);
}
function _onFilesDragOver(e) {
  if (!_isFileDrag(e)) return;
  e.preventDefault();
  _dropHint(true);
}
function _onFilesDrop(e) {
  const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  _dropHint(false);
  if (!files.length) return;
  e.preventDefault(); e.stopPropagation();
  _uploadFiles(files);
}

function _onImagePaste(e) {
  const blob = _imageFromData(e.clipboardData);
  _clientlog({ ev: 'paste-event', gotImage: !!blob, types: e.clipboardData ? Array.from(e.clipboardData.types || []) : 'null' });
  if (blob) { e.preventDefault(); e.stopPropagation(); _uploadAndInsertImage(blob); return; }
  const dt = e.clipboardData, text = dt ? dt.getData('text/plain') : '';
  if (text) return;                               // text → xterm's own paste listener, once
  // neither text nor an image file in the event (some image formats aren't exposed
  // as files): read the clipboard directly, as the Ctrl+V hotkey used to
  if (dt && Array.from(dt.types || []).some(t => /^image\//.test(t))) {
    e.preventDefault(); e.stopPropagation();
    _pasteImageFromClipboard('paste-event');
  }
}

// a paste on the dashboard itself (focus on a button / the page, not in a text
// field): an image uploads as before; text goes into the open terminal.
function _onParentPaste(e) {
  if (_imageFromData(e.clipboardData)) return _onImagePaste(e);
  const t = e.target, tag = (t && t.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || (t && t.isContentEditable)) return;
  const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
  const f = document.querySelector('#wrap iframe');
  if (!text || !f || !f.contentWindow || !('term' in f.contentWindow)) return;
  e.preventDefault(); e.stopPropagation();
  _insertIntoTerminal(f, text);
}

// catch the paste wherever focus is: parent document … (text fields on the
// dashboard keep their normal text paste — see _onParentPaste).
document.addEventListener('paste', _onParentPaste, true);
window.addEventListener('drop', _onFilesDrop, true);
window.addEventListener('dragover', _onFilesDragOver, true);

// … and inside the terminal iframe (focus is usually there). Attach on load AND
// immediately (the iframe may already be loaded when this runs), and mark the doc
// so we never double-bind.
function enablePasteImage(iframe) {
  const attach = () => {
    let attempts = 0;
    const poll = setInterval(() => {
      if (++attempts > 80) { clearInterval(poll); return; }
      try {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        if (!doc || !doc.querySelector('.xterm')) return;
        clearInterval(poll);
        if (doc.__pasteImgBound) return;
        doc.__pasteImgBound = true;
        _clientlog({ ev: 'iframe-bound' });
        doc.addEventListener('paste', _onImagePaste, true);
        doc.addEventListener('keydown', (e) => _onTermKeydown(e, 'iframe'), true);   // Ctrl+V paste / Ctrl+C copy inside the terminal
        doc.addEventListener('drop', _onFilesDrop, true);
        doc.addEventListener('dragover', _onFilesDragOver, true);
      } catch (e) { clearInterval(poll); }
    }, 100);
  };
  iframe.addEventListener('load', attach);
  attach();   // in case it's already loaded
}
