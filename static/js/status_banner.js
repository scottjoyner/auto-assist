(() => {
  'use strict';

  // Global status banner: answers "is the fleet OK?" on every page.
  // Data source: /api/control-room/overview (same data the Control Room uses).

  const esc = (v) => {
    const n = document.createElement('span');
    n.textContent = v == null ? '' : String(v);
    return n.innerHTML;
  };

  let pollTimer = null;
  let lastStatus = null;

  function apply(snapshot) {
    const banner = document.getElementById('ax-status-banner');
    if (!banner) return;
    const summary = snapshot.summary || {};
    const deps = Array.isArray(snapshot.dependencies) ? snapshot.dependencies : [];
    const failing = deps.filter((d) => d && d.required && d.status && d.status !== 'healthy');
    const overall = String(snapshot.overall_status || 'unknown').toLowerCase();

    const statusPill = document.getElementById('ax-status-pill');
    statusPill.className = `ax-pill ax-${overall}`;
    statusPill.textContent = overall.toUpperCase();
    if (overall !== lastStatus) {
      statusPill.classList.remove('ax-flash');
      void statusPill.offsetWidth; // restart animation
      statusPill.classList.add('ax-flash');
      lastStatus = overall;
    }

    document.getElementById('ax-status-detail').innerHTML = [
      `${esc(summary.runtime_count ?? '--')} runtimes (${esc(summary.healthy_runtime_count ?? '--')} healthy)`,
      `${esc(summary.loaded_model_count ?? '--')} models loaded`,
      `${esc(summary.active_requests ?? 0)} active / ${esc(summary.queued_requests ?? 0)} queued`,
      failing.length
        ? `<span class="ax-bad-text">${esc(failing.length)} required dep failure${failing.length > 1 ? 's' : ''}: ${esc(failing.map((d) => d.name).join(', '))}</span>`
        : `dependencies ok`,
      `<a href="/control-room">Control Room →</a>`,
    ].join('<span class="ax-sep">·</span>');
  }

  async function refresh() {
    try {
      const res = await fetch('/api/control-room/overview', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(String(res.status));
      apply(await res.json());
    } catch {
      const pill = document.getElementById('ax-status-pill');
      if (pill) {
        pill.className = 'ax-pill ax-unknown';
        pill.textContent = 'STATUS UNAVAILABLE';
      }
    }
  }

  function init() {
    const banner = document.getElementById('ax-status-banner');
    if (!banner) return;
    refresh();
    pollTimer = setInterval(refresh, 30000);
    window.addEventListener('beforeunload', () => clearInterval(pollTimer));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
