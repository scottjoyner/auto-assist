(() => {
  'use strict';

  const state = {
    snapshot: null,
    source: null,
    reconnects: 0,
    lastReceivedAt: 0,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => {
    const node = document.createElement('span');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML;
  };
  const number = (value, digits = 1) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : '--';
  };
  const integer = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.round(parsed).toLocaleString() : '--';
  };
  const compactTime = (timestamp) => {
    if (!timestamp) return '--:--:--';
    return new Date(Number(timestamp)).toLocaleTimeString([], { hour12: false });
  };
  const duration = (milliseconds) => {
    const value = Number(milliseconds);
    if (!Number.isFinite(value)) return '--';
    if (value < 1000) return `${Math.round(value)}ms`;
    if (value < 60000) return `${(value / 1000).toFixed(1)}s`;
    return `${Math.floor(value / 60000)}m ${Math.round((value % 60000) / 1000)}s`;
  };
  const statusClass = (status) => `status-${String(status || 'unknown').toLowerCase()}`;
  const modelName = (model) => model.model_key || model.served_name || model.model_id || 'unknown';

  function renderSummary(snapshot) {
    const summary = snapshot.summary || {};
    $('summary-fleet').textContent = `${integer(summary.healthy_runtime_count)}/${integer(summary.runtime_count)}`;
    $('summary-fleet-note').textContent = 'healthy runtimes';
    $('summary-requests').textContent = integer(summary.active_requests);
    $('summary-queue').textContent = `${integer(summary.queued_requests)} queued`;
    $('summary-capacity').textContent = `${integer(summary.available_slots)}/${integer(summary.parallel_slots)}`;
    $('summary-models').textContent = integer(summary.loaded_model_count);
    $('summary-tps').textContent = summary.average_tokens_per_second == null ? '--' : number(summary.average_tokens_per_second, 1);
    $('summary-errors').textContent = summary.error_percent == null ? '--' : `${number(summary.error_percent, 1)}%`;
    $('summary-dependencies').textContent = integer(summary.required_dependency_failures);
    $('summary-inference').textContent = summary.required_dependency_failures ? 'CHECK' : 'LOCAL';
  }

  function renderRuntimes(snapshot) {
    const runtimes = snapshot.runtimes || [];
    const body = $('runtime-body');
    if (!runtimes.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">NO PHYSICAL RUNTIME RECORDS</td></tr>';
      return;
    }
    body.innerHTML = runtimes.map((runtime, index) => {
      const models = (runtime.loaded_models || []).map(modelName);
      const transport = runtime.selected_transport || 'unknown';
      const mode = runtime.runtime_mode || 'UNKNOWN';
      const slots = `${integer(runtime.active)}/${integer(runtime.parallel_slots)}`;
      return `<tr class="runtime-row ${statusClass(runtime.status)}" data-runtime-index="${index}">
        <td><strong>${esc(runtime.node_id || 'unresolved')}</strong><div class="dependency-category">${esc(runtime.runtime_instance_id)}</div></td>
        <td>${esc(runtime.runtime_kind || 'unknown')}<div class="dependency-category">${esc(runtime.runtime_version || '')}</div></td>
        <td class="mode-${esc(mode.toLowerCase())}">${esc(mode)}</td>
        <td>${models.length ? models.map((name) => `<div>${esc(name)}</div>`).join('') : '<span class="dependency-category">none reported</span>'}</td>
        <td class="path-${esc(transport)}">${esc(transport.toUpperCase())}<div class="dependency-category">${esc(runtime.selected_access_url || 'not selected')}</div></td>
        <td>${esc(slots)}</td>
        <td>${integer(runtime.queued)}/${integer(runtime.queue_limit)}</td>
        <td><span class="status-dot"></span>${esc(String(runtime.status || 'unknown').toUpperCase())}</td>
      </tr>`;
    }).join('');

    body.querySelectorAll('.runtime-row').forEach((row) => {
      row.addEventListener('click', () => {
        const existing = row.nextElementSibling;
        if (existing && existing.classList.contains('runtime-detail')) {
          existing.remove();
          return;
        }
        body.querySelectorAll('.runtime-detail').forEach((item) => item.remove());
        const runtime = runtimes[Number(row.dataset.runtimeIndex)];
        const template = $('runtime-detail-template').content.cloneNode(true);
        template.querySelector('pre').textContent = JSON.stringify(runtime, null, 2);
        row.after(template);
      });
    });
  }

  function renderDependencies(snapshot) {
    const dependencies = snapshot.dependencies || [];
    const grid = $('dependency-grid');
    if (!dependencies.length) {
      grid.innerHTML = '<div class="empty">NO DEPENDENCY DATA</div>';
      return;
    }
    grid.innerHTML = dependencies.map((dependency) => {
      const latency = dependency.latency_ms == null ? '' : `<span class="dependency-latency">${number(dependency.latency_ms, 1)}ms</span>`;
      return `<article class="dependency-item ${statusClass(dependency.status)}">
        <div class="dependency-name"><span><span class="status-dot"></span>${esc(dependency.name)}</span>${latency}</div>
        <div class="dependency-category">${esc(dependency.category)} // ${dependency.required ? 'required' : 'optional'} // ${esc(dependency.status)}</div>
        <div class="dependency-detail">${esc(dependency.detail)}</div>
      </article>`;
    }).join('');
  }

  function renderPerformance(snapshot) {
    const rows = snapshot.performance || [];
    const body = $('performance-body');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">NO MODEL PERFORMANCE SAMPLES</td></tr>';
      return;
    }
    body.innerHTML = rows.map((row) => `<tr>
      <td>${esc(row.model)}</td>
      <td>${esc(row.node_id)}</td>
      <td>${integer(row.runs)}</td>
      <td>${number(row.tps_avg, 1)}</td>
      <td>${row.ttft_ms_avg ? `${number(row.ttft_ms_avg, 0)}ms` : '--'}</td>
      <td>${row.latency_ms_avg ? `${number(row.latency_ms_avg, 0)}ms` : '--'}</td>
      <td>${number(row.error_percent, 1)}%</td>
      <td>${number(row.quality_avg, 3)}</td>
    </tr>`).join('');
  }

  function activityVisible(event) {
    if (!$('only-active').checked) return true;
    return ['READY', 'CLAIMED', 'RUNNING', 'PAUSING'].includes(String(event.status || '').toUpperCase());
  }

  function renderActivity(snapshot) {
    const activity = (snapshot.activity || []).filter(activityVisible);
    $('activity-count').textContent = `${activity.length} events`;
    const feed = $('activity-feed');
    if (!activity.length) {
      feed.innerHTML = '<div class="empty">NO MATCHING FLEET ACTIVITY</div>';
      return;
    }
    feed.innerHTML = activity.map((event, index) => {
      const runtime = [event.runtime_node_id, event.runtime_kind, event.selected_transport].filter(Boolean).join(' / ') || 'runtime unresolved';
      const metrics = [
        event.tokens_per_second == null ? null : `${number(event.tokens_per_second, 1)} t/s`,
        event.ttft_ms == null ? null : `${integer(event.ttft_ms)}ms ttft`,
        event.duration_ms == null ? null : duration(event.duration_ms),
      ].filter(Boolean).join(' · ') || '--';
      const context = [event.repository, event.stage, event.task_kind].filter(Boolean).join(' · ');
      return `<article class="activity-row" data-activity-index="${index}">
        <div class="activity-time">${esc(compactTime(event.created_at_ts))}</div>
        <div class="activity-status ${esc(event.status || '')}">${esc(event.status || 'UNKNOWN')}</div>
        <div><div class="activity-title">${esc(event.display_title)}</div><div class="activity-context">${esc(context || 'no task context')}</div></div>
        <div class="activity-route"><strong>${esc(event.agent || 'unassigned')}</strong> → ${esc(event.model || 'model unresolved')} → ${esc(runtime)}</div>
        <div class="activity-metrics">${esc(metrics)}</div>
        <pre class="activity-ids">${esc(JSON.stringify({
          task_id: event.task_id,
          run_id: event.run_id,
          runtime_instance_id: event.runtime_instance_id,
          selected_access_url: event.selected_access_url,
          prompt_tokens: event.prompt_tokens,
          completion_tokens: event.completion_tokens,
          error_class: event.error_class,
          result_preview: event.result_preview,
        }, null, 2))}</pre>
      </article>`;
    }).join('');
    feed.querySelectorAll('.activity-row').forEach((row) => {
      row.addEventListener('click', () => row.classList.toggle('expanded'));
    });
  }

  function render(snapshot) {
    state.snapshot = snapshot;
    state.lastReceivedAt = Date.now();
    renderSummary(snapshot);
    renderRuntimes(snapshot);
    renderDependencies(snapshot);
    renderPerformance(snapshot);
    renderActivity(snapshot);
    const streamState = $('stream-state');
    const overall = snapshot.overall_status || 'unknown';
    streamState.className = `state state-${overall}`;
    streamState.textContent = overall.toUpperCase();
    $('collected-at').textContent = `snapshot ${compactTime(snapshot.collected_at_ts)}`;
  }

  async function fetchOnce() {
    const response = await fetch('/api/control-room/overview', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  }

  function connectStream() {
    if (state.source) state.source.close();
    const source = new EventSource('/api/control-room/stream');
    state.source = source;
    source.addEventListener('snapshot', (event) => {
      state.reconnects = 0;
      render(JSON.parse(event.data));
    });
    source.addEventListener('error', () => {
      $('stream-state').className = 'state state-degraded';
      $('stream-state').textContent = 'RECONNECTING';
      source.close();
      state.reconnects += 1;
      window.setTimeout(connectStream, Math.min(15000, 1000 * (2 ** state.reconnects)));
    });
  }

  $('manual-refresh').addEventListener('click', () => fetchOnce().catch((error) => {
    $('stream-state').className = 'state state-unhealthy';
    $('stream-state').textContent = error.message;
  }));
  $('only-active').addEventListener('change', () => {
    if (state.snapshot) renderActivity(state.snapshot);
  });

  window.setInterval(() => {
    if (!state.lastReceivedAt) return;
    const ageSeconds = Math.max(0, Math.round((Date.now() - state.lastReceivedAt) / 1000));
    $('data-age').textContent = `age ${ageSeconds}s`;
    if (ageSeconds > 10) {
      $('stream-state').className = 'state state-degraded';
      $('stream-state').textContent = 'STALE';
    }
  }, 1000);

  fetchOnce().catch(() => undefined).finally(connectStream);
})();
