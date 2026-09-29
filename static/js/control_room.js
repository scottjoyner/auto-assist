(() => {
  'use strict';

  const state = {
    snapshot: null,
    source: null,
    reconnects: 0,
    lastReceivedAt: 0,
    lastDataUpdate: 0,
    healthScore: 0,
    criticalAlerts: [],
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
  const healthScore = (snapshot) => {
    if (!snapshot) return 0;
    let score = 100;
    const doctor = snapshot.doctor || {};
    const counts = doctor.counts || {};
    if (counts.fail) score -= Math.min(30, counts.fail * 10);
    if (counts.warn) score -= Math.min(20, counts.warn * 5);
    const dependencies = snapshot.dependencies || [];
    for (const dep of dependencies) {
      if (dep.status === 'degraded') score -= 5;
      if (dep.status === 'failed') score -= 15;
    }
    const runtimes = snapshot.runtimes || [];
    for (const runtime of runtimes) {
      if (runtime.status === 'offline') score -= 10;
      if (runtime.status === 'error') score -= 20;
    }
    const power = snapshot.power || {};
    if (power.error) score -= 25;
    if (power.stale) score -= 10;
    return Math.max(0, Math.min(100, score));
  };
  const getComponentHealth = (snapshot) => {
    const components = [];
    const doctor = snapshot.doctor || {};
    const counts = doctor.counts || {};
    components.push({
      name: 'System',
      status: counts.fail > 0 || counts.warn > 0 ? 'critical' : 'healthy',
      score: Math.max(0, 100 - (counts.fail * 10 + counts.warn * 5)),
      issues: counts.fail > 0 ? `${counts.fail} failed` : counts.warn > 0 ? `${counts.warn} warnings` : 'none'
    });
    const dependencies = snapshot.dependencies || [];
    for (const dep of dependencies) {
      components.push({
        name: dep.name,
        status: dep.status,
        score: dep.status === 'healthy' ? 100 : dep.status === 'degraded' ? 50 : 0,
        issues: dep.status === 'degraded' ? 'degraded' : dep.status === 'failed' ? 'failed' : 'none',
        category: dep.category,
        required: dep.required
      });
    }
    const runtimes = snapshot.runtimes || [];
    for (const runtime of runtimes) {
      components.push({
        name: runtime.node_id || runtime.runtime_instance_id || 'unknown',
        status: runtime.status,
        score: runtime.status === 'online' ? 100 : runtime.status === 'offline' ? 0 : 50,
        issues: runtime.status === 'offline' ? 'offline' : runtime.status === 'error' ? 'error' : 'none',
        runtime: runtime.runtime_kind,
        transport: runtime.selected_transport
      });
    }
    const power = snapshot.power || {};
    components.push({
      name: 'Power',
      status: power.error ? 'critical' : power.stale ? 'warning' : 'healthy',
      score: power.error ? 0 : power.stale ? 50 : 100,
      issues: power.error ? `error: ${power.error}` : power.stale ? 'stale' : 'none',
      plugCount: (power.plugs || {}).length
    });
    return components;
  };
  const getTrendData = (snapshot) => {
    const trends = [];
    const now = Date.now();
    if (snapshot.doctor?.counts?.fail) {
      trends.push({
        metric: 'Component Failures',
        current: snapshot.doctor.counts.fail,
        trend: 'increasing',
        change: '+1',
        severity: snapshot.doctor.counts.fail > 2 ? 'critical' : snapshot.doctor.counts.fail > 0 ? 'warning' : 'stable'
      });
    }
    const dependencies = snapshot.dependencies || [];
    const failedDeps = dependencies.filter(d => d.status === 'failed').length;
    if (failedDeps > 0) {
      trends.push({
        metric: 'Failed Dependencies',
        current: failedDeps,
        trend: 'increasing',
        change: `+${failedDeps}`,
        severity: failedDeps > 2 ? 'critical' : failedDeps > 0 ? 'warning' : 'stable'
      });
    }
    const offlineRuntimes = (snapshot.runtimes || []).filter(r => r.status === 'offline').length;
    if (offlineRuntimes > 0) {
      trends.push({
        metric: 'Offline Runtimes',
        current: offlineRuntimes,
        trend: 'increasing',
        change: `+${offlineRuntimes}`,
        severity: offlineRuntimes > 2 ? 'critical' : offlineRuntimes > 0 ? 'warning' : 'stable'
      });
    }
    const errorRuntimes = (snapshot.runtimes || []).filter(r => r.status === 'error').length;
    if (errorRuntimes > 0) {
      trends.push({
        metric: 'Error Runtimes',
        current: errorRuntimes,
        trend: 'increasing',
        change: `+${errorRuntimes}`,
        severity: errorRuntimes > 1 ? 'critical' : errorRuntimes > 0 ? 'warning' : 'stable'
      });
    }
    return trends;
  };

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
    // Collapse duplicates per physical node: prefer status=online, then the
    // richer loaded-model set. Backend sources disagree on instance identity.
    const byNode = {};
    const runtimes = [];
    for (const runtime of snapshot.runtimes || []) {
      const key = String(runtime.node_id || runtime.runtime_instance_id || Math.random());
      const prev = byNode[key];
      if (!prev) {
        byNode[key] = runtime;
        runtimes.push(runtime);
        continue;
      }
      const preferNew =
        (runtime.status === 'online' && prev.status !== 'online') ||
        (runtime.status === prev.status &&
          (runtime.loaded_models || []).length > (prev.loaded_models || []).length);
      if (preferNew) {
        Object.assign(prev, runtime);
      }
    }
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

  function fmtAge(ms) {
    if (ms == null) return '--';
    if (ms < 60000) return `${Math.round(ms / 1000)}s`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}m`;
    return `${Math.round(ms / 3600000)}h`;
  }

  function renderRecovery(snapshot) {
    const grid = $('recovery-grid');
    const rec = snapshot.recovery || { present: false, stale: true };
    if (!rec.present) {
      grid.innerHTML = '<div class="empty">NO ISLAND HEARTBEAT RECEIVED</div>';
      return;
    }
    const p = rec.payload || {};
    const gates = [];
    if (p.replication_pending === 0 && p.journal_entries != null) gates.push(`replication ok (${p.journal_entries})`);
    if (p.warm_state) gates.push(`warm ${p.warm_state}`);
    if (p.containers) gates.push(`tier0 ${p.containers}`);
    const ca = p.carve_a_eta_h != null ? 'active' : 'done/stopped';
    const cb = p.carve_b_eta_h != null ? 'active' : 'idle';
    gates.push(`carve A ${ca}`, `carve B ${cb}`);
    const fmtStream = (key, label) => {
      const tb = p[`carve_${key}_tb`], pct = p[`carve_${key}_pct`],
            rate = p[`carve_${key}_rate`], eta = p[`carve_${key}_eta_h`];
      if (tb == null) return [label, '--', ''];
      return [label, `${tb} TB (${pct ?? '--'}%)`,
        rate ? `${rate} MB/s${eta != null ? ` · ETA ${eta}h` : ''}` : 'no advance'];
    };
    const cells = [
      ['HEARTBEAT AGE', fmtAge(rec.age_ms), rec.stale ? 'STALE' : 'live'],
      ['ISLAND MODE', esc(p.mode || '--'), ''],
      ['JOURNAL', integer(p.journal_entries ?? 0), `${p.journal_pending ?? '--'} pending`],
      ['PROJECTION GEN', p.projection_generation != null ? integer(p.projection_generation) : '--',
        p.projection_expires_in_s != null ? `expires in ${p.projection_expires_in_s}s` : 'expired/none'],
      ['WARM TIER', esc(p.warm_state || '--'), ''],
      ['TIER-0 CONTAINERS', esc(p.containers || '--'), ''],
      fmtStream('a', 'CARVE A'),
      fmtStream('b', 'CARVE B'),
      ['GATES', esc(gates.join(' · ')), ''],
    ];
    grid.innerHTML = cells.map(([label, value, note]) => `
      <div class="dependency-item${rec.stale ? ' degraded' : ''}>
        <span class="dep-label">${label}</span>
        <strong class="mono">${value}</strong>
        <small>${note}</small>
      </div>`).join('');
  }

  const token = (value) => String(value == null ? '' : value).toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
  const modelList = (models, limit = 4) => {
    const items = Array.isArray(models) ? models.filter((model) => model != null) : [];
    if (!items.length) return '<span class="dependency-category">none reported</span>';
    const shown = items.slice(0, limit).map((model) => `<span class="model-token">${esc(model)}</span>`);
    const more = items.length - shown.length;
    return shown.join('<span class="model-separator"> / </span>') + (more ? ` <span class="dependency-category">+${more} more</span>` : '');
  };
  const sourceLabel = (node) => {
    const source = String(node.model_source || '').toLowerCase();
    if (source === 'live' || source === 'graph') return 'live graph';
    if (source === 'manifest-primary') return 'manifest primary';
    if (source === 'router') return 'router report';
    if (source === 'live-empty') return 'live empty';
    if (node.model_observed_at != null) return 'live graph';
    return '';
  };
  const modelEvidence = (node) => {
    const loaded = Array.isArray(node.loaded_models) ? node.loaded_models : [];
    const source = sourceLabel(node);
    const sourceHtml = source ? `<span class="source-badge source-${token(source)}">${esc(source)}</span>` : '';
    const opacity = token(node.opacity);
    const opacityHtml = opacity ? `<span class="clue-badge opacity-${opacity}">${esc(String(node.opacity || '').toUpperCase())}</span>` : '';
    const lastKnown = node.last_known && Array.isArray(node.last_known.models) && node.last_known.models.length
      ? `<div class="last-known"><span class="dependency-category">last known</span> ${modelList(node.last_known.models, 3)} <span class="dependency-category">${fmtAge(node.last_known.age_ms)} ago</span></div>`
      : '';
    const clue = node.model_clue ? `<div class="fleet-clue">${esc(node.model_clue)}</div>` : '';
    return `<div class="fleet-models">${modelList(loaded)}${sourceHtml}${opacityHtml}${lastKnown}${clue}</div>`;
  };

  function renderFleetNodes(snapshot) {
    const body = $('fleet-nodes-body');
    const chip = $('doctor-chip');
    const doctor = snapshot.doctor || {};
    if (chip) {
      const counts = doctor.counts || {};
      chip.textContent = `${(doctor.overall || 'unknown').toUpperCase()} (${counts.fail ?? '?'} fail / ${counts.warn ?? '?'} warn)`;
    }
    const nodes = snapshot.fleet_nodes || [];
    if (!nodes.length) {
      body.innerHTML = '<tr><td colspan="11" class="empty">NO NODE REGISTRY DATA</td></tr>';
      return;
    }
    body.innerHTML = nodes.map((n) => {
      const status = String(n.status || 'UNKNOWN').toUpperCase();
      const instrumentRaw = n.instrument == null || n.instrument === '' ? 'UNKNOWN' : String(n.instrument);
      let instrumentClass = 'instrument-tag';
      if (/\bdGPU\b/.test(instrumentRaw)) instrumentClass += ' tag-dgpu';
      else if (/\bNPU\b/.test(instrumentRaw)) instrumentClass += ' tag-npu';
      else if (/\biGPU\b/.test(instrumentRaw)) instrumentClass += ' tag-igpu';
      else instrumentClass += ' tag-cpu';
      const roles = Array.isArray(n.roles) ? n.roles.filter(Boolean) : [n.role].filter(Boolean);
      const roleText = roles.length ? roles.map((role) => esc(role)).join(' · ') : '';
      const capabilityText = (Array.isArray(n.capabilities) ? n.capabilities : []).filter(Boolean).slice(0, 4).map((cap) => esc(cap)).join(', ') || '--';
      let loadChip = '';
      if (n.load_tag) {
        const tag = token(n.load_tag);
        loadChip = ` <span class="mono load-chip ${tag}" title="operator-assigned soft load">${esc(String(n.load_tag).toUpperCase())}</span>`;
      }
      if (typeof n.slot_cap === 'number') {
        loadChip += ` <span class="mono cap-chip" title="soft concurrency cap">cap ${n.slot_cap}</span>`;
      }
      const nodeName = n.canonical_name || n.hostname || 'unresolved';
      const opacityClass = token(n.opacity) ? `opacity-${token(n.opacity)}` : '';
      return `<tr class="fleet-row ${token(status)} ${opacityClass}">
        <td class="fleet-node-cell"><strong>${esc(nodeName)}</strong>${roleText ? ` <span class="dependency-category">${roleText}</span>` : ''}${loadChip}${n.note ? ` <small class="mono fleet-note">${esc(n.note)}</small>` : ''}</td>
        <td><span class="${instrumentClass}">${esc(instrumentRaw)}</span></td>
        <td><span class="state state-${token(status)}">${status}</span></td>
        <td class="mono">${esc(n.ip || '--')}</td>
        <td class="mono">${fmtAge(n.last_seen_age_ms)}</td>
        <td>${modelEvidence(n)}</td>
        <td><div class="fleet-available">${modelList(n.available_models, 3)}</div></td>
        <td>${n.model_clue ? `<span class="clue-badge">${esc(String(n.opacity || 'opacity').toUpperCase())}</span>` : '--'}</td>
        <td class="mono">${n.model_observed_at == null ? '--' : fmtAge(Number(n.model_observed_at))}</td>
        <td class="mono">${n.watchdog_us ? `${Math.round(n.watchdog_us / 1000000)}s wd` : '--'}</td>
        <td><div class="fleet-caps">${roleText}${capabilityText ? (roleText ? ' · ' : '') + capabilityText : ''}</div></td>
      </tr>`;
    }).join('');
  }

  function renderPower(snapshot) {
    const grid = $('power-grid');
    const power = snapshot.power || {};
    if (power.error) {
      grid.innerHTML = `<div class="empty">POWER PROBE ERROR: ${esc(power.error)}</div>`;
      return;
    }
    if (power.stale) {
      grid.innerHTML = '<div class="empty">POWER PROBES STALE — host timer down?</div>';
      return;
    }
    const last = power.last_mutation;
    const cells = ['A', 'B', 'C', 'D'].map((pid) => {
      const plug = (power.plugs || {})[pid] || { ok: false, gates: [] };
      const state = plug.ok ? 'online' : 'degraded';
      const gateBits = (plug.gates || []).map((g) =>
        `${g.ok ? '✓' : '✗'} ${esc(g.gate)}`).join(' · ') || 'no gates';
      return `<div class="dependency-item${plug.ok ? '' : ' degraded'}">
        <span class="dep-label">PLUG ${pid}</span>
        <strong class="mono">${state.toUpperCase()}</strong>
        <small>${gateBits}</small>
      </div>`;
    });
    let mutation = '';
    if (last && last.ts) {
      const age = Math.round((Date.now() - last.ts * 1000) / 1000);
      mutation = `<div class="dependency-item">
        <span class="dep-label">LAST MUTATION</span>
        <strong class="mono">${esc(last.op)} ${esc(last.plug)}</strong>
        <small>${fmtAge(age * 1000)} ago</small>
      </div>`;
    }
    grid.innerHTML = cells.join('') + mutation;
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
    state.lastDataUpdate = Date.now();
    state.healthScore = healthScore(snapshot);
    state.criticalAlerts = getCriticalAlerts(snapshot);
    renderSummary(snapshot);
    renderRuntimes(snapshot);
    renderDependencies(snapshot);
    renderPerformance(snapshot);
    renderRecovery(snapshot);
    renderFleetNodes(snapshot);
    renderPower(snapshot);
    renderActivity(snapshot);
    const streamState = $('stream-state');
    const overall = snapshot.overall_status || 'unknown';
    streamState.className = `state state-${overall}`;
    streamState.textContent = overall.toUpperCase();
    const dataAge = state.lastReceivedAt ? Date.now() - state.lastReceivedAt : 0;
    const dataAgeElement = $('data-age');
    if (dataAgeElement) {
      if (dataAge < 5000) {
        dataAgeElement.textContent = `LIVE`;
        dataAgeElement.className = 'mono live-indicator';
      } else if (dataAge < 30000) {
        dataAgeElement.textContent = `${Math.round(dataAge / 1000)}s ago`;
        dataAgeElement.className = 'mono warning-indicator';
      } else {
        dataAgeElement.textContent = `${Math.round(dataAge / 60000)}m ago`;
        dataAgeElement.className = 'mono stale-indicator';
      }
    }
    $('collected-at').textContent = `snapshot ${compactTime(snapshot.collected_at_ts)}`;
    const healthScoreElement = document.querySelector('.health-score');
    if (healthScoreElement) {
      healthScoreElement.textContent = `${state.healthScore}`;
      healthScoreElement.className = `health-score ${state.healthScore >= 90 ? 'healthy' : state.healthScore >= 70 ? 'warning' : 'critical'}`;
    }
    const alertsElement = document.querySelector('.critical-alerts');
    if (alertsElement) {
      if (state.criticalAlerts.length > 0) {
        alertsElement.innerHTML = state.criticalAlerts.map(alert => `<span class="alert-badge critical">${alert}</span>`).join('');
        alertsElement.style.display = 'flex';
      } else {
        alertsElement.style.display = 'none';
      }
    }
    const alertsBanner = document.getElementById('critical-alerts-banner');
    if (alertsBanner) {
      if (state.criticalAlerts.length > 0) {
        alertsBanner.style.display = 'block';
        const alertText = alertsBanner.querySelector('.alert-text');
        if (alertText) {
          alertText.textContent = `${state.criticalAlerts.length} critical issue(s) detected`;
        }
      } else {
        alertsBanner.style.display = 'none';
      }
    }
    const componentHealthElement = document.querySelector('.component-health');
    if (componentHealthElement) {
      const components = getComponentHealth(snapshot);
      componentHealthElement.innerHTML = components.map(comp => `
        <div class="component-health-item">
          <span class="component-name">${comp.name}</span>
          <span class="component-status ${comp.status}">${comp.status.toUpperCase()}</span>
          <span class="component-score">${comp.score}</span>
          <span class="component-issues">${comp.issues}</span>
          ${comp.category ? `<span class="component-category">${comp.category}</span>` : ''}
          ${comp.runtime ? `<span class="component-runtime">${comp.runtime}</span>` : ''}
        </div>
      `).join('');
    }
    const trendDataElement = document.querySelector('.trend-data');
    if (trendDataElement) {
      const trends = getTrendData(snapshot);
      trendDataElement.innerHTML = trends.map(trend => `
        <div class="trend-item ${trend.severity}">
          <span class="trend-metric">${trend.metric}</span>
          <span class="trend-value">${trend.current}</span>
          <span class="trend-change ${trend.trend}">${trend.change}</span>
          <span class="trend-severity ${trend.severity}">${trend.severity.toUpperCase()}</span>
        </div>
      `).join('');
    }
  }

  function showQuickActions() {
    const actions = [];
    if (state.criticalAlerts.length > 0) {
      actions.push({
        label: 'View Critical Alerts',
        action: () => {
          const alertsBanner = document.getElementById('critical-alerts-banner');
          if (alertsBanner) alertsBanner.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    actions.push({
      label: 'Refresh Data',
      action: () => fetchOnce().catch((error) => {
        $('stream-state').className = 'state state-unhealthy';
        $('stream-state').textContent = error.message;
      })
    });
    if (state.snapshot?.fleet_nodes?.some(n => n.status === 'offline' || n.status === 'error')) {
      actions.push({
        label: 'Check Offline Nodes',
        action: () => {
          const fleetSection = document.getElementById('fleet-nodes');
          if (fleetSection) fleetSection.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    if (state.snapshot?.dependencies?.some(d => d.status === 'failed')) {
      actions.push({
        label: 'Resolve Dependencies',
        action: () => {
          const depsSection = document.getElementById('dependencies');
          if (depsSection) depsSection.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    if (actions.length === 0) return;
    const menu = document.createElement('div');
    menu.className = 'quick-actions-menu';
    menu.innerHTML = `
      <div class="quick-actions-header">Quick Actions</div>
      ${actions.map(action => `
        <button class="quick-action-btn" onclick="this.closest('.quick-actions-menu').remove(); ${action.action.toString()}">
          ${action.label}
        </button>
      `).join('')}
    `;
    document.body.appendChild(menu);
    menu.style.position = 'fixed';
    menu.style.top = '60px';
    menu.style.right = '20px';
    menu.style.backgroundColor = 'white';
    menu.style.border = '1px solid var(--line-hot)';
    menu.style.borderRadius = '8px';
    menu.style.padding = '8px';
    menu.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
    menu.style.zIndex = '1000';
    const header = menu.querySelector('.quick-actions-header');
    if (header) {
      header.style.fontWeight = '600';
      header.style.paddingBottom = '8px';
      header.style.borderBottom = '1px solid var(--line)';
      header.style.marginBottom = '8px';
    }
    const buttons = menu.querySelectorAll('.quick-action-btn');
    buttons.forEach(btn => {
      btn.style.display = 'block';
      btn.style.width = '100%';
      btn.style.padding = '8px 12px';
      btn.style.marginBottom = '4px';
      btn.style.textAlign = 'left';
      btn.style.border = 'none';
      btn.style.backgroundColor = 'transparent';
      btn.style.cursor = 'pointer';
      btn.style.borderRadius = '4px';
      btn.addEventListener('mouseover', (e) => {
        e.target.style.backgroundColor = 'var(--hover-bg, #f5f5f5)';
      });
      btn.addEventListener('mouseout', (e) => {
        e.target.style.backgroundColor = 'transparent';
      });
    });
    setTimeout(() => {
      if (menu.parentNode) menu.parentNode.removeChild(menu);
    }, 5000);
  }

  function exportData() {
    if (!state.snapshot) return;
    
    const data = {
      timestamp: new Date().toISOString(),
      snapshot: state.snapshot,
      healthScore: state.healthScore,
      criticalAlerts: state.criticalAlerts,
      lastDataUpdate: state.lastDataUpdate,
      reconnects: state.reconnects
    };
    
    const dataStr = JSON.stringify(data, null, 2);
    const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
    
    const exportName = `assistx-dashboard-data-${new Date().toISOString().split('T')[0]}.json`;
    
    const linkElement = document.createElement('a');
    linkElement.setAttribute('href', dataUri);
    linkElement.setAttribute('download', exportName);
    linkElement.click();
  }

  function init() {
    Navigation.init();
    fetchOnce();
    connectStream();
    const quickActionsBtn = document.getElementById('quick-actions');
    if (quickActionsBtn) {
      quickActionsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        showQuickActions();
      });
    }
    const exportBtn = document.getElementById('export-data');
    if (exportBtn) {
      exportBtn.addEventListener('click', exportData);
    }
  }

  document.addEventListener('DOMContentLoaded', init);

  function showQuickActions() {
    const actions = [];
    if (state.criticalAlerts.length > 0) {
      actions.push({
        label: 'View Critical Alerts',
        action: () => {
          const alertsBanner = document.getElementById('critical-alerts-banner');
          if (alertsBanner) alertsBanner.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    actions.push({
      label: 'Refresh Data',
      action: () => fetchOnce().catch((error) => {
        $('stream-state').className = 'state state-unhealthy';
        $('stream-state').textContent = error.message;
      })
    });
    if (state.snapshot?.fleet_nodes?.some(n => n.status === 'offline' || n.status === 'error')) {
      actions.push({
        label: 'Check Offline Nodes',
        action: () => {
          const fleetSection = document.getElementById('fleet-nodes');
          if (fleetSection) fleetSection.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    if (state.snapshot?.dependencies?.some(d => d.status === 'failed')) {
      actions.push({
        label: 'Resolve Dependencies',
        action: () => {
          const depsSection = document.getElementById('dependencies');
          if (depsSection) depsSection.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }
    if (actions.length === 0) return;
    const menu = document.createElement('div');
    menu.className = 'quick-actions-menu';
    menu.innerHTML = `
      <div class="quick-actions-header">Quick Actions</div>
      ${actions.map(action => `
        <button class="quick-action-btn" onclick="this.closest('.quick-actions-menu').remove(); ${action.action.toString()}">
          ${action.label}
        </button>
      `).join('')}
    `;
    document.body.appendChild(menu);
    menu.style.position = 'fixed';
    menu.style.top = '60px';
    menu.style.right = '20px';
    menu.style.backgroundColor = 'white';
    menu.style.border = '1px solid var(--line)';
    menu.style.borderRadius = '8px';
    menu.style.padding = '8px';
    menu.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
    menu.style.zIndex = '1000';
    const header = menu.querySelector('.quick-actions-header');
    if (header) {
      header.style.fontWeight = '600';
      header.style.paddingBottom = '8px';
      header.style.borderBottom = '1px solid var(--line)';
      header.style.marginBottom = '8px';
    }
    const buttons = menu.querySelectorAll('.quick-action-btn');
    buttons.forEach(btn => {
      btn.style.display = 'block';
      btn.style.width = '100%';
      btn.style.padding = '8px 12px';
      btn.style.marginBottom = '4px';
      btn.style.textAlign = 'left';
      btn.style.border = 'none';
      btn.style.backgroundColor = 'transparent';
      btn.style.cursor = 'pointer';
      btn.style.borderRadius = '4px';
      btn.addEventListener('mouseover', (e) => {
        e.target.style.backgroundColor = 'var(--hover-bg, #f5f5f5)';
      });
      btn.addEventListener('mouseout', (e) => {
        e.target.style.backgroundColor = 'transparent';
      });
    });
    setTimeout(() => {
      if (menu.parentNode) menu.parentNode.removeChild(menu);
    }, 5000);
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
  const quickActionsBtn = document.getElementById('quick-actions');
  if (quickActionsBtn) {
    quickActionsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      showQuickActions();
    });
  }
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
