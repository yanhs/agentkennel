// The account menu and the light/dark switch.
// Classic script: shares one global scope with the other web/js files, loaded in
// the order web/index.html lists them; top-level code may use only this file and earlier ones.

/* ── ⋯ account menu: opens on click/Enter/Space, closes on outside click,
   Esc (focus back to ⋯), Tab out of it, or when focus goes into the terminal. ── */
(function accountMenu() {
  const btn = document.getElementById('tMenu');
  const menu = document.getElementById('tMenuList');
  const items = () => [...menu.querySelectorAll('[role=menuitem]')];
  const isOpen = () => !menu.hidden;
  function open(focusFirst) {
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    if (focusFirst) items()[0].focus();
  }
  function close(refocus) {
    if (!isOpen()) return;
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    if (refocus) btn.focus();
  }
  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (isOpen()) close(false);
    else open(e.detail === 0);            // keyboard "click" (Enter/Space) → focus first item
  });
  btn.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); open(true); }
  });
  menu.addEventListener('keydown', e => {
    const list = items(), i = list.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); list[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); list[list.length - 1].focus(); }
  });
  document.getElementById('tAcct').addEventListener('focusout', e => {
    if (!e.relatedTarget || !document.getElementById('tAcct').contains(e.relatedTarget)) close(false);
  });
  document.addEventListener('click', e => {
    if (isOpen() && !document.getElementById('tAcct').contains(e.target)) close(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && isOpen()) close(true);
  });
  window.addEventListener('blur', () => close(false));   // focus went into the terminal iframe
})();

/* ── Dark / Light theme (⋯ menu). Stored in localStorage 'agentkennel-theme'
   (same origin as the Tasks and Server pages, which follow it live through
   the storage event); the <head> script applies it before first paint. ── */
(function themeSwitch() {
  const KEY = 'agentkennel-theme', OLD_KEY = 'agentdeck-theme';   // OLD_KEY: before v2.1.0
  const item = document.getElementById('tTheme');
  const current = () => document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  function apply(t) {
    document.documentElement.dataset.theme = t;
    item.textContent = t === 'light' ? 'Dark theme' : 'Light theme';
  }
  apply(current());
  item.addEventListener('click', e => {
    e.stopPropagation();
    const t = current() === 'light' ? 'dark' : 'light';
    apply(t);
    try { localStorage.setItem(KEY, t); } catch (err) {}
  });
  window.addEventListener('storage', e => {          // switched in another tab
    if (e.key === KEY || e.key === OLD_KEY) apply(e.newValue === 'light' ? 'light' : 'dark');
  });
})();
