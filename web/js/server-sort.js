// Server page: the order of the rows in each group — by CPU (smoothed, as before) or by
// memory, the biggest first (owner, 25.11.2026). The choice stays in this browser.
// In both: a stopped row goes last, and so does "Other" (the leftover small processes).
const ServerSort = (() => {
  const KEY = 'agentkennel-server-sort';
  const MODES = ['cpu', 'mem'];

  function load() {
    try { const m = localStorage.getItem(KEY); return MODES.includes(m) ? m : 'cpu'; }
    catch (e) { return 'cpu'; }                    // storage off: the default
  }

  function save(mode) {
    try { localStorage.setItem(KEY, mode); } catch (e) { /* storage off: not remembered */ }
  }

  // members: the group's rows; cpu: id -> smoothed CPU (keeps the CPU order steady)
  function order(members, mode, cpu) {
    const key = mode === 'mem' ? m => m.rss_mb || 0 : m => cpu.get(m.id) || 0;
    return members.slice().sort((a, b) =>
      ((a.status === 'stopped') - (b.status === 'stopped')) ||
      ((a.id === 'sys:other') - (b.id === 'sys:other')) ||
      (key(b) - key(a)) ||
      (mode === 'mem' ? (cpu.get(b.id) || 0) - (cpu.get(a.id) || 0) : (b.rss_mb - a.rss_mb)) ||
      a.id.localeCompare(b.id));
  }

  // the "Sort by CPU · Memory" buttons; onChange(mode) re-draws the groups
  function bind(bar, onChange) {
    let mode = load();
    const paint = () => bar.querySelectorAll('[data-sort]').forEach(b =>
      b.setAttribute('aria-pressed', String(b.dataset.sort === mode)));
    bar.addEventListener('click', e => {
      const b = e.target.closest('[data-sort]');
      if (!b || b.dataset.sort === mode) return;
      mode = b.dataset.sort; save(mode); paint(); onChange(mode);
    });
    paint();
    return () => mode;
  }

  return { load, save, order, bind };
})();
