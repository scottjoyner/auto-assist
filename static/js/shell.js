// AssistX shell: data connection + topbar/footer widgets.
//
// This file runs on every page. It owns the snapshot connection (one fetch and
// one SSE stream), the topbar indicators, and the quick-actions / export
// controls that live in the shell chrome. Page-specific rendering lives in
// control_room.js and subscribes through window.AssistXShell.
//
// Pages read the bus; they never open their own connection.
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
  // The shell (topbar/footer) is rendered on every page, but the control-room
  // sections are not. Anything page-scoped must no-op when its container is
  // absent so this script can be loaded once, globally, without breaking the
  // other pages.
  const has = (id) => Boolean($(id));

  const esc = (value) => {
    const node = document.createElement('span');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML;
  };

  const compactTime = (timestamp) => {
    if (!timestamp) return '--:--:--';
    return new Date(Number(timestamp)).toLocaleTimeString([], { hour12: false });
  };

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
        // The DETAILS column is only useful if it says *why* a component is
        // unhealthy; it rendered empty for every row until now.
        detail: dep.detail || '',
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


  const getCriticalAlerts = (snapshot) => {
    const alerts = [];
    const dependencies = snapshot?.dependencies || [];
    dependencies
      .filter((item) => item && item.required && item.status !== 'healthy' && item.status !== 'disabled')
      .forEach((item) => {
        alerts.push(`Required dependency ${item.name || item.key || 'unknown'}: ${item.status || 'unknown'}`);
      });
    const runtimes = snapshot?.runtimes || [];
    const failing = runtimes.filter((item) => item && item.status === 'failing');
    if (failing.length) {
      alerts.push(`${failing.length} runtime(s) reporting failures`);
    }
    const errorPercent = Number((snapshot?.summary || {}).error_percent);
    if (Number.isFinite(errorPercent) && errorPercent >= 25) {
      alerts.push(`Error rate ${errorPercent.toFixed(1)}% across measured runs`);
    }
    return alerts;
  };

  // (container id, renderer) pairs that only run when the page owns them.

// renderShellWrites: topbar + component health + trends (moved from render()).
  function renderShellWrites(snapshot) {

    const streamState = $('stream-state');
    const overall = snapshot.overall_status || 'unknown';
    if (streamState) {
      streamState.className = `state state-${overall}`;
      streamState.textContent = overall.toUpperCase();
    }
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
    const collectedAt = $('collected-at');
    if (collectedAt) {
      collectedAt.textContent = `snapshot ${compactTime(snapshot.collected_at_ts)}`;
    }
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
    const componentHealthElement = document.querySelector('.component-health-body');
    if (componentHealthElement) {
      const components = getComponentHealth(snapshot);
      componentHealthElement.innerHTML = components.map(comp => `
        <div class="component-health-item">
          <span class="component-name">${comp.name}</span>
          <span class="component-status ${comp.status}">${comp.status.toUpperCase()}</span>
          <span class="component-score">${comp.score}</span>
          <span class="component-issues">${comp.issues}</span>
          ${comp.category ? `<span class="component-category">${comp.category}</span>` : ''}
          <span class="component-details">${esc(comp.detail || comp.runtime || '--')}</span>
        </div>
      `).join('');
    }
    const trendDataElement = document.querySelector('.trend-body');
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


  async function fetchOnce() {
    const response = await fetch('/api/control-room/overview', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    handleSnapshot(await response.json());
  }

  function connectStream() {
    if (state.source) state.source.close();
    const source = new EventSource('/api/control-room/stream');
    state.source = source;
    source.addEventListener('snapshot', (event) => {
      state.reconnects = 0;
      handleSnapshot(JSON.parse(event.data));
    });
    source.addEventListener('error', () => {
      const badge = $('stream-state');
      if (badge) {
        badge.className = 'state state-degraded';
        badge.textContent = 'RECONNECTING';
      }
      source.close();
      state.reconnects += 1;
      window.setTimeout(connectStream, Math.min(15000, 1000 * (2 ** state.reconnects)));
    });
  }

  // ---- bus -------------------------------------------------------------
  const listeners = [];
  const bus = {
    snapshot: null,
    onSnapshot(callback) {
      if (typeof callback === 'function') listeners.push(callback);
      if (bus.snapshot) callback(bus.snapshot);
    },
    refresh() { return fetchOnce(); },
  };
  window.AssistXShell = bus;

  function handleSnapshot(snapshot) {
    state.snapshot = snapshot;
    state.lastReceivedAt = Date.now();
    state.lastDataUpdate = Date.now();
    state.healthScore = healthScore(snapshot);
    state.criticalAlerts = getCriticalAlerts(snapshot);
    renderShellWrites(snapshot);
    bus.snapshot = snapshot;
    listeners.slice().forEach((callback) => {
      try { callback(snapshot); } catch (error) { console.error('shell subscriber failed', error); }
    });
  }

  // ---- shell chrome wiring ---------------------------------------------
  function init() {
    // Guard: without navigation.js this resolves to the browser's built-in
    // Navigation API, which has no init(), and throws before the rest of the
    // quick-actions wiring runs.
    if (window.Navigation && typeof window.Navigation.init === 'function') {
      window.Navigation.init();
    }
    const refreshButton = $('manual-refresh');
    if (refreshButton) {
      refreshButton.addEventListener('click', () => fetchOnce().catch(() => undefined));
    }
    const quickActionsBtn = $('quick-actions');
    if (quickActionsBtn) {
      quickActionsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        showQuickActions();
      });
    }
    const exportBtn = $('export-data');
    if (exportBtn) {
      exportBtn.addEventListener('click', exportData);
    }
    // A single connection for the whole page.
    fetchOnce().catch(() => undefined).finally(connectStream);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
