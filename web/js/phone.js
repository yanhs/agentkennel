// Phone input bar (text, Esc, arrows) and the topbar Esc button.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

/* ── Mobile input bar ── */
function sendToTerminal(text) {
  try {
    const f = document.querySelector('#wrap iframe');
    if (!f) return;
    const term = f.contentWindow.term;
    if (!term) return;
    if (typeof term.input === 'function') { term.input(text); return; }
    try { term._core.coreService.triggerDataEvent(text); return; } catch(e) {}
    try { term._core._onData.fire(text); return; } catch(e) {}
  } catch(e) {}
}

const mobText = document.getElementById('mobText');
document.getElementById('mobSend').addEventListener('click', () => {
  const text = mobText.value;
  sendToTerminal(text ? text + '\r' : '\r');
  mobText.value = '';
  mobText.focus();
});
mobText.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); document.getElementById('mobSend').click(); }
});
document.getElementById('mobEsc').addEventListener('click', () => {
  sendToTerminal('\x03');
  mobText.value = '';
  mobText.focus();
});
/* ── Tab + cursor arrows: send the key's bytes. preventDefault stops the click
   from stealing focus from the iframe, so repeated taps keep working. ── */
const ARROW_KEYS = {
  mobTab:   '\t',
  mobLeft:  '\x1b[D',
  mobRight: '\x1b[C',
  mobUp:    '\x1b[A',
  mobDown:  '\x1b[B',
};
for (const [id, seq] of Object.entries(ARROW_KEYS)) {
  const btn = document.getElementById(id);
  if (!btn) continue;
  btn.addEventListener('mousedown', e => e.preventDefault());
  btn.addEventListener('click', e => {
    e.preventDefault();
    sendToTerminal(seq);
  });
}

/* ── Esc button: send Escape key to terminal iframe ── */
document.getElementById('tEsc').addEventListener('click', () => {
  try {
    const f = document.querySelector('#wrap iframe');
    if (!f) return;
    const win = f.contentWindow;
    const term = win.term;
    if (term) {
      if (typeof term.input === 'function') { term.input('\x1b'); return; }
      try { term._core.coreService.triggerDataEvent('\x1b'); return; } catch(e) {}
      try { term._core._onData.fire('\x1b'); return; } catch(e) {}
    }
    const doc = f.contentDocument || win.document;
    const el = doc.querySelector('.xterm-helper-textarea') || doc.activeElement || doc.body;
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',code:'Escape',keyCode:27,which:27,bubbles:true}));
  } catch(e) {}
});
