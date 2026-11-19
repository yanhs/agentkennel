// The file button, touch scrolling and selection, copying a terminal's selection.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

/* ── 📎 Image: the reliable path — read the clipboard image directly on a
   button click (no fragile paste-event interception inside the ttyd iframe). ── */
// 📎 File: pick files → upload → their paths into the terminal (a clipboard image
// still pastes with Ctrl+V / ⌘V, and files can be dropped onto the terminal)
const _fileInput = document.getElementById('tFileInput');
document.getElementById('tPasteImg').addEventListener('click', () => _fileInput.click());
_fileInput.addEventListener('change', () => {
  const files = Array.from(_fileInput.files || []);
  _fileInput.value = '';                          // the same file again fires change again
  _uploadFiles(files);
});

/* ── Mobile: touch → xterm.js's own selection + vertical scroll ──
   Claude's fullscreen renderer tracks the mouse, so tmux hands it every mouse event
   (a fake mousedown on each touchstart made a tap Claude's double click).
   - a tap sends nothing synthetic: the browser's own click is one press + release;
   - a long press (LONG_MS within SLOP px), then a drag in any direction, sends mouse
     events xterm.js takes as "force selection" (Shift while the pane tracks the mouse;
     Alt + macOptionClickForcesSelection where xterm.js sees a Mac, as on iPadOS), so
     nothing reaches tmux; touchend copies term.getSelection() right there, inside the
     gesture, and shows the "Copied" chip; touchcancel never copies;
   - a move before the long press scrolls: wheel events at the finger, one per three
     cell heights; two fingers do nothing. ── */
// fn(win, screen) once the terminal iframe's xterm.js screen exists
function onXtermScreen(iframe, fn) {
  iframe.addEventListener('load', () => {
    let attempts = 0;
    const poll = setInterval(() => {
      if (++attempts > 50) { clearInterval(poll); return; }
      try {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        const screen = doc.querySelector('.xterm-screen');
        if (!screen) return;
        clearInterval(poll);
        fn(iframe.contentWindow, screen);
      } catch(e) { clearInterval(poll); }
    }, 100);
  });
}

function enableIframeTouchScroll(iframe) {
  onXtermScreen(iframe, (win, screen) => attachTouchSelect(win, screen));
}

/* ── Touchpad: a calmer terminal scroll ──
   xterm.js sends the app (tmux, Claude) a wheel report for EVERY wheel event, and each
   report scrolls several lines (tmux copy mode: 5), so a touchpad's stream of small
   deltas flew past. Small pixel deltas are summed here and one report goes through per
   WHEEL_CELLS cell heights of travel, as a phone swipe does. A mouse-wheel notch is one
   big delta (>= NOTCH_PX) and stays one report. Ctrl+wheel (pinch zoom), line/page
   deltas and our own synthetic events pass untouched. ── */
function attachWheelThrottle(win, screen) {
  const WHEEL_CELLS = 3, NOTCH_PX = 50;
  const cellH = () => {
    const rows = win.term && win.term.rows;
    return rows ? screen.getBoundingClientRect().height / rows : 20;
  };
  let accum = 0;
  // capture on the window: runs before xterm.js's own listener on .xterm
  win.addEventListener('wheel', e => {
    if (!e.isTrusted || e.ctrlKey || e.deltaMode !== 0 || !e.deltaY) return;
    if (!screen.contains(e.target)) return;
    e.preventDefault();
    e.stopPropagation();
    if (accum && Math.sign(accum) !== Math.sign(e.deltaY)) accum = 0;   // turned around
    accum += e.deltaY;
    if (Math.abs(e.deltaY) < NOTCH_PX && Math.abs(accum) < WHEEL_CELLS * cellH()) return;
    accum = 0;
    screen.dispatchEvent(new win.WheelEvent('wheel', {
      deltaY: e.deltaY > 0 ? 120 : -120, deltaMode: 0, bubbles: true, cancelable: true,
      clientX: e.clientX, clientY: e.clientY
    }));
  }, {capture: true, passive: false});
}

function attachTouchSelect(win, screen) {
  const doc = screen.ownerDocument;
  const LONG_MS = 450, SLOP = 8;
  // xterm.js's own Mac test (navigator.platform); iPadOS "desktop site" says MacIntel
  const mac = /Mac/.test(win.navigator.platform);
  // xterm.js 5 (ttyd 1.7) sets options on term.options; 4 (ttyd 1.6) only has setOption
  const setOpt = (k, v) => {
    try { if (win.term.options) win.term.options[k] = v; else win.term.setOption(k, v); }
    catch (_) {}
  };
  if (mac) {
    setOpt('macOptionClickForcesSelection', true);
    // a long press lifted without a drag is a short Alt click: xterm.js would type
    // arrow keys into the app to move its cursor there
    setOpt('altClickMovesCursor', false);
  }
  // no native text selection or iOS callout on a long press
  const st = doc.createElement('style');
  st.textContent = '.xterm-screen{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}';
  (doc.head || doc.documentElement).appendChild(st);
  // Shift on a pane that does NOT track the mouse would extend the old selection
  const tracking = () => !!doc.querySelector('.xterm.enable-mouse-events');
  // detail: 1 — xterm.js's mousedown ignores an event without a click count
  const fire = (type, x, y) => screen.dispatchEvent(new win.MouseEvent(type, {
    clientX: x, clientY: y, button: 0, detail: 1, buttons: type === 'mouseup' ? 0 : 1,
    shiftKey: !mac && tracking(), altKey: mac, bubbles: true, cancelable: true, view: win }));
  const cellH = () => {
    const rows = win.term && win.term.rows;
    return rows ? screen.getBoundingClientRect().height / rows : 20;
  };
  let startX = 0, startY = 0, lastX = 0, lastY = 0, accum = 0, timer = 0;
  let mode = null; // null | 'scroll' | 'select' | 'multi'

  screen.addEventListener('touchstart', e => {
    clearTimeout(timer);
    if (e.touches.length !== 1) {                // a second finger ends a selection
      if (mode === 'select') fire('mouseup', lastX, lastY);
      mode = 'multi';
      return;
    }
    const t = e.touches[0];
    startX = lastX = t.clientX;
    startY = lastY = t.clientY;
    mode = null;
    accum = 0;
    try { win.term.clearSelection(); } catch (_) {}
    timer = setTimeout(() => {
      if (mode !== null) return;
      mode = 'select';
      fire('mousedown', startX, startY);
      try { win.navigator.vibrate && win.navigator.vibrate(15); } catch (_) {}
    }, LONG_MS);
  }, {passive: true});

  screen.addEventListener('touchmove', e => {
    if (mode === 'multi' || e.touches.length !== 1) return;
    const t = e.touches[0];
    if (mode === null) {
      if (Math.abs(t.clientX - startX) <= SLOP && Math.abs(t.clientY - startY) <= SLOP) return;
      clearTimeout(timer);
      mode = 'scroll';
    }
    e.preventDefault();
    if (mode === 'select') {
      lastX = t.clientX; lastY = t.clientY;
      fire('mousemove', lastX, lastY);
      return;
    }
    accum += lastY - t.clientY;
    lastY = t.clientY;
    const step = 3 * cellH();
    const lines = Math.trunc(accum / step);
    if (!lines) return;
    accum -= lines * step;
    for (let i = 0; i < Math.abs(lines); i++) {
      screen.dispatchEvent(new win.WheelEvent('wheel', {
        deltaY: lines > 0 ? 120 : -120, deltaMode: 0, bubbles: true, cancelable: true,
        clientX: t.clientX, clientY: t.clientY
      }));
    }
  }, {passive: false});

  const end = (e, copy) => {
    clearTimeout(timer);
    if (mode === 'multi') { if (!e.touches.length) mode = null; return; }
    if (mode === 'select') {
      const t = e.changedTouches[0] || {clientX: lastX, clientY: lastY};
      fire('mouseup', t.clientX, t.clientY);
      if (e.cancelable) e.preventDefault();      // no compat click into the app
      let txt = '';
      try { txt = win.term.getSelection(); } catch (_) {}
      // the clipboard of whichever document has focus accepts the write
      if (copy && txt) _copyText(doc, txt, doc.hasFocus() ? win.navigator : navigator);
    }
    mode = null;
  };
  screen.addEventListener('touchend', e => end(e, true), {passive: false});
  screen.addEventListener('touchcancel', e => end(e, false), {passive: false});
}

/* ── Clipboard: after mouse selection in tmux, copy buffer to system clipboard ── */
// Right-clicking the terminal popped the browser's native context menu — newer ttyd
// (xterm.js 5.x client) doesn't preventDefault it the way the older client did. Quiet it
// on the same-origin terminal iframe (capturing listener on its document) so right-click
// stays out of the way, regardless of the installed ttyd version.
function suppressContextMenu(iframe) {
  iframe.addEventListener('load', () => {
    try {
      const doc = iframe.contentDocument || iframe.contentWindow.document;
      doc.addEventListener('contextmenu', e => e.preventDefault(), true);
    } catch (e) { /* cross-origin (shouldn't happen) — ignore */ }
  });
}

function enableClipboardCopy(iframe) {
  iframe.addEventListener('load', () => {
    let attempts = 0;
    const poll = setInterval(() => {
      if (++attempts > 50) { clearInterval(poll); return; }
      try {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        if (!doc.querySelector('.xterm')) return;
        clearInterval(poll);
        const bufUrl = location.origin + '/api/tmux-buffer/?session=global';
        const readBuf = async () => {
          try { return (await (await fetch(bufUrl)).json()).text || ''; } catch { return ''; }
        };
        // Snapshot the tmux buffer as the selection STARTS. `tmux show-buffer`
        // lags one selection behind the mouseup (browser→ttyd→tmux round-trip),
        // so on copy we must wait until the buffer differs from this pre-selection
        // anchor — otherwise we'd grab the PREVIOUS selection (the off-by-one bug
        // where you had to copy twice to paste the current text).
        let anchorPromise = Promise.resolve(''), _downX = 0, _downY = 0, _gen = 0;
        doc.addEventListener('mousedown', (e) => { _downX = e.clientX; _downY = e.clientY; anchorPromise = readBuf(); _tmuxSel = null; });
        doc.addEventListener('mouseup', (e) => {
          // only a real drag is a selection — a plain click shouldn't touch the clipboard
          if (Math.abs(e.clientX - _downX) < 4 && Math.abs(e.clientY - _downY) < 4) return;
          const myGen = ++_gen;                          // supersede any older poll loop
          try { window.focus(); } catch (_) {}           // clipboard.write needs a focused doc
          // ClipboardItem with a Promise lets write() run inside the gesture while the
          // text resolves async. CRITICAL: it must resolve FAST — real Chrome aborts the
          // write if the promise hangs ~>1s. So poll the tmux buffer only briefly (~700ms),
          // take the first value that differs from the pre-selection anchor (the NEW
          // selection), and ALWAYS resolve with something so copy never silently fails.
          const textPromise = new Promise((resolve) => {
            (async () => {
              const anchor = await anchorPromise;        // buffer BEFORE this selection
              let last = '';
              for (let i = 0; i < 14; i++) {             // ~700ms max, 50ms steps
                const t = await readBuf();
                if (t && t.trim()) {
                  last = t;
                  if (t !== anchor) { resolve(new Blob([t], {type: 'text/plain'})); return; }  // new selection landed
                }
                if (myGen !== _gen) break;               // a newer selection took over
                await new Promise(r => setTimeout(r, 50));
              }
              resolve(new Blob([last], {type: 'text/plain'}));   // fallback: copy current buffer (never reject)
            })();
          });
          // Ctrl+C right after this drag copies it again instead of sending ^C
          const sel = { text: null, doc: doc, at: Date.now(),
                        promise: textPromise.then(b => b.text()).catch(() => '') };
          sel.promise.then(t => { sel.text = (t && t.trim()) ? t : ''; });
          _tmuxSel = sel;
          // the "Copied" chip only when non-empty text really reached the clipboard
          const copied = () => textPromise.then(b => b.text())
            .then(t => { if (t && t.trim()) showCopyHint(); }).catch(() => {});
          const fallback = () => textPromise.then(b => b.text())
            .then(t => t && t.trim() && navigator.clipboard.writeText(t).then(() => showCopyHint()))
            .catch(() => {});
          try {
            navigator.clipboard.write([
              new ClipboardItem({'text/plain': textPromise})
            ]).then(copied, fallback);   // no Promise ClipboardItem support → writeText
          } catch (_) {
            fallback();
          }
        });
      } catch(e) { clearInterval(poll); }
    }, 100);
  });
}
