/* Fleet Dashboard — modular UI layer for unified fleet + inference state */
(() => {
  'use strict';

  const esc = window.UIUtils?.escapeHtml || ((s) => (s ?? '').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
  const short = window.UIUtils?.shortId || ((v, n = 8) => (v || '').toString().slice(0, n));

  function fmtDuration(s) {
    if (s == null) return '--';
    return s < 60 ? s.toFixed(1) + 's' : Math.round(s / 60) + 'm';
  }

  // Inference state rendering: show active sessions + quick start/stop controls
  function renderInferenceState(data, wrapId) {
    const wrap = document.getElementById(wrapId);
    if (!wrap || !data?.inference_state) return;
    const inf = data.inference_state;
    const sessions = (inf.active_sessions || []);
    const models = (inf.models_active || []);
    let html = `<div class="metric-grid">
      <div class="metric"><div class="metric-label">Active Inference</div><div class="metric-value">${inf.session_count || 0}</div></div>
      <div class="metric"><div class="metric-label">Models Serving</div><div class="metric-value">${models.length}</div></div>
    </div>`;
    if (models.length) {
      html += `<table class="dashboard-table mt-2"><thead><tr><th>Model</th><th>Node / URL</th><th>Sessions</th><th>Control</th></tr></thead><tbody>`;
      for (const m of models) {
        const label = esc(m.model_id || '?');
        html += `<tr>
          <td><code>${label}</code></td>
          <td>${esc(m.base_url || m.node_id || '--')}</td>
          <td>${m.active_sessions || 0}</td>
          <td><button class="btn danger inference-stop" data-model="${esc(m.model_id)}" data-url="${esc(m.base_url || '')}">Stop</button></td>
        </tr>`;
      }
      html += '</tbody></table>';
    }
    if (sessions.length) {
      html += `<div class="mt-2"><strong>Sessions</strong></div><table class="dashboard-table"><thead><tr><th>Session</th><th>Model</th><th>Started</th><th>Node</th></tr></thead><tbody>`;
      for (const s of sessions.slice(0, 20)) {
        html += `<tr><td><code>${esc(s.session_id || '')}</code></td><td>${esc(s.model_id)}</td><td>${fmtDuration(s.duration_s || 0)}</td><td>${esc(s.node_id || '--')}</td></tr>`;
      }
      html += '</tbody></table>';
    }
    wrap.innerHTML = html;
    wrap.querySelectorAll('.inference-stop').forEach(btn => {
      btn.addEventListener('click', async () => {
        const model = btn.dataset.model;
        const url = btn.dataset.url;
        if (!model || !url) return;
        if (!confirm(`Stop inference for ${model} on ${url}?`)) return;
        try {
          const r = await fetch('/api/fleet/inference/stop-model', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({model_id: model, base_url: url, status: 'stopped'})
          });
          alert(r.ok ? `Stopped inference for ${model}` : (await r.json()).reason || 'Stop failed');
          window.dispatchEvent(new CustomEvent('fleet-refresh'));
        } catch (e) { alert('Network error: ' + e.message); }
      });
    });
  }

  // Fast start button: discover node from loaded_models and register start
  function bindInferenceStart() {
    document.querySelectorAll('.inference-start').forEach(btn => {
      btn.addEventListener('click', async () => {
        const model = btn.dataset.model;
        const url = btn.dataset.url || btn.dataset.baseUrl || '';
        if (!model || !url) return alert('Model and URL required');
        try {
          const r = await fetch('/api/fleet/inference/start', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({model_id: model, base_url: url, node_id: btn.dataset.node || ''})
          });
          const res = await r.json();
          alert(r.ok ? `Started inference: ${res.session_id}` : (res.detail || res.reason || 'Failed'));
          window.dispatchEvent(new CustomEvent('fleet-refresh'));
        } catch (e) { alert('Network error: ' + e.message); }
      });
    });
  }

  window.FleetDashboardUI = {
    renderInferenceState,
    bindInferenceStart,
  };
})();
