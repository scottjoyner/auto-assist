// Chat-first AssistX workbench.
// Chat uses the existing authenticated Hermes agent boundary. The context drawer
// is read-only and consumes the same shell snapshot plus existing list endpoints.
(() => {
  'use strict';

  const shell = window.AssistXShell;
  const layout = document.querySelector('.workbench-layout');
  const drawer = document.getElementById('workbench-drawer');
  const toggle = document.getElementById('workbench-drawer-toggle');
  const close = document.getElementById('workbench-drawer-close');
  const composer = document.getElementById('workbench-composer');
  const input = document.getElementById('workbench-input');
  const send = document.getElementById('workbench-send');
  const sendState = document.getElementById('workbench-send-state');
  const messagesEl = document.getElementById('workbench-messages');

  if (!layout || !drawer || !composer || !input || !messagesEl) return;

  const SESSION_ID_KEY = 'assistx.workbench.hermesSessionId';
  const CONVERSATION_KEY = 'assistx.workbench.conversationKey';
  const state = {
    messages: [],
    sessionId: sessionStorage.getItem(SESSION_ID_KEY) || '',
    conversationKey: sessionStorage.getItem(CONVERSATION_KEY) || '',
    busy: false,
    snapshot: null,
  };

  if (!state.conversationKey) {
    const suffix = (window.crypto && typeof window.crypto.randomUUID === 'function')
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.conversationKey = `web-workbench:${suffix}`;
    sessionStorage.setItem(CONVERSATION_KEY, state.conversationKey);
  }

  const text = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value == null || value === '' ? '—' : String(value);
  };

  const short = (value, size = 12) => {
    const raw = String(value || '');
    return raw.length > size ? `${raw.slice(0, size)}…` : raw || '—';
  };

  const appendMessage = (role, content, extraClass = '') => {
    const item = document.createElement('article');
    item.className = `chat-message ${role} ${extraClass}`.trim();

    const roleNode = document.createElement('div');
    roleNode.className = 'chat-role';
    roleNode.textContent = role === 'user' ? 'YOU' : 'ASSISTX';

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = content;

    item.append(roleNode, bubble);
    messagesEl.appendChild(item);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return { item, bubble };
  };

  const feedItem = (title, detail) => {
    const row = document.createElement('div');
    row.className = 'drawer-feed-item';
    const strong = document.createElement('strong');
    strong.textContent = title;
    const note = document.createElement('span');
    note.textContent = detail;
    row.append(strong, note);
    return row;
  };

  const setDrawerOpen = (open) => {
    layout.classList.toggle('drawer-collapsed', !open);
    toggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
  };

  toggle?.addEventListener('click', () => {
    setDrawerOpen(layout.classList.contains('drawer-collapsed'));
  });
  close?.addEventListener('click', () => setDrawerOpen(false));

  if (window.matchMedia('(max-width: 900px)').matches) {
    setDrawerOpen(false);
  }

  document.querySelectorAll('.drawer-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const name = tab.dataset.tab;
      document.querySelectorAll('.drawer-tab').forEach((candidate) => {
        const active = candidate === tab;
        candidate.classList.toggle('active', active);
        candidate.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      document.querySelectorAll('.drawer-panel').forEach((panel) => {
        const active = panel.dataset.panel === name;
        panel.classList.toggle('active', active);
        panel.hidden = !active;
      });
    });
  });

  const renderActivity = (activity) => {
    const feed = document.getElementById('ctx-activity');
    if (!feed) return;
    feed.replaceChildren();

    const rows = Array.isArray(activity) ? activity.slice(0, 6) : [];
    if (!rows.length) {
      feed.textContent = 'No execution activity reported.';
      return;
    }

    rows.forEach((event) => {
      const title = event.display_title || event.task_title || event.task_kind || short(event.task_id);
      const detail = [
        event.status,
        event.agent,
        event.model,
        event.runtime_node_id,
      ].filter(Boolean).join(' · ');
      feed.appendChild(feedItem(title || 'execution', detail || 'context unavailable'));
    });
  };

  const renderFleet = (snapshot) => {
    const feed = document.getElementById('ctx-fleet');
    if (!feed) return;
    feed.replaceChildren();

    const runtimes = Array.isArray(snapshot.runtimes) ? snapshot.runtimes : [];
    if (!runtimes.length) {
      feed.textContent = 'No runtime snapshot available.';
      return;
    }

    runtimes.slice(0, 8).forEach((runtime) => {
      const models = (runtime.loaded_models || [])
        .map((model) => model.model_key || model.served_name || model.model_id)
        .filter(Boolean)
        .slice(0, 2)
        .join(', ');
      feed.appendChild(feedItem(
        runtime.node_id || runtime.runtime_instance_id || 'runtime',
        [runtime.status, runtime.runtime_kind, models].filter(Boolean).join(' · ') || 'no model detail'
      ));
    });
  };

  const renderSnapshot = (snapshot) => {
    state.snapshot = snapshot;
    const activity = Array.isArray(snapshot.activity) ? snapshot.activity : [];
    const active = activity.find((event) =>
      ['READY', 'CLAIMED', 'RUNNING', 'PAUSING'].includes(String(event.status || '').toUpperCase())
    ) || activity[0] || null;

    const status = active?.status || 'idle';
    const agent = active?.agent || 'Hermes';
    const model = active?.model || 'auto';
    const node = active?.runtime_node_id || 'unresolved';

    text('ctx-status', status);
    text('ctx-agent', agent);
    text('ctx-model', model);
    text('ctx-node', node);
    text('ctx-repository', active?.repository);
    text('ctx-task', short(active?.task_id));
    text('ctx-run', short(active?.run_id));
    text('ctx-transport', active?.selected_transport);
    text('workbench-execution-chip', `execution ${String(status).toLowerCase()}`);

    const route = [agent, model, node].filter(Boolean).join(' → ');
    text('workbench-route', route || 'Hermes → AssistX Router');
    renderActivity(activity);
    renderFleet(snapshot);
  };

  const loadRecentSessions = async () => {
    const feed = document.getElementById('ctx-sessions');
    if (!feed) return;

    try {
      const [sessionsResponse, dispatchesResponse] = await Promise.all([
        fetch('/api/sessions?limit=8', { credentials: 'same-origin' }),
        fetch('/api/dispatches?limit=8', { credentials: 'same-origin' }),
      ]);
      if (!sessionsResponse.ok || !dispatchesResponse.ok) throw new Error('context unavailable');

      const sessions = await sessionsResponse.json();
      const dispatches = await dispatchesResponse.json();
      feed.replaceChildren();

      (sessions.items || []).slice(0, 5).forEach((session) => {
        feed.appendChild(feedItem(
          short(session.id || session.hermes_session_id || 'session'),
          [session.paperclip_agent_id, session.hermes_session_id, session.platform].filter(Boolean).join(' · ') || 'session'
        ));
      });
      (dispatches.items || []).slice(0, 3).forEach((dispatch) => {
        feed.appendChild(feedItem(
          `dispatch ${short(dispatch.id || dispatch.dispatch_id)}`,
          [dispatch.status, dispatch.agent_id, dispatch.task_id].filter(Boolean).join(' · ') || 'dispatch'
        ));
      });

      if (!feed.children.length) feed.textContent = 'No recent sessions or dispatches.';
    } catch (error) {
      feed.textContent = 'Recent session context unavailable.';
    }
  };

  const consumeSSE = async (response, bubble) => {
    if (!response.body) throw new Error('Streaming response unavailable');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let content = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        for (const line of event.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === '[DONE]') continue;
          const parsed = JSON.parse(payload);
          const delta = parsed?.choices?.[0]?.delta?.content || '';
          if (delta) {
            content += delta;
            bubble.textContent = content;
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
        }
      }
    }
    return content.trim();
  };

  const sendMessage = async (prompt) => {
    const normalized = prompt.trim();
    if (!normalized || state.busy) return;

    state.busy = true;
    send.disabled = true;
    input.disabled = true;
    sendState.textContent = 'Hermes working…';

    appendMessage('user', normalized);
    state.messages.push({ role: 'user', content: normalized });
    if (state.messages.length > 40) state.messages = state.messages.slice(-40);

    const pending = appendMessage('assistant', 'Working…', 'pending');

    try {
      const headers = {
        'Content-Type': 'application/json',
        'X-Hermes-Session-Key': state.conversationKey,
      };
      if (state.sessionId) headers['X-Hermes-Session-Id'] = state.sessionId;

      const response = await fetch('/api/v1/agent/chat/completions', {
        method: 'POST',
        credentials: 'same-origin',
        headers,
        body: JSON.stringify({
          model: 'agent:auto',
          messages: state.messages,
          stream: true,
        }),
      });

      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          detail = payload?.detail?.error || payload?.detail || detail;
        } catch (_) {
          // Keep the status-only message.
        }
        throw new Error(String(detail));
      }

      const sessionId = response.headers.get('X-Hermes-Session-Id');
      if (sessionId) {
        state.sessionId = sessionId;
        sessionStorage.setItem(SESSION_ID_KEY, sessionId);
        text('workbench-session-chip', `session ${short(sessionId, 10)}`);
      }

      pending.item.classList.remove('pending');
      pending.bubble.textContent = '';
      const answer = await consumeSSE(response, pending.bubble);
      if (!answer) pending.bubble.textContent = 'Hermes returned no visible response.';
      else state.messages.push({ role: 'assistant', content: answer });
    } catch (error) {
      pending.item.classList.remove('pending');
      pending.bubble.textContent = `Request failed: ${error.message || error}`;
    } finally {
      state.busy = false;
      send.disabled = false;
      input.disabled = false;
      sendState.textContent = 'Hermes / agent:auto';
      input.focus();
      loadRecentSessions();
    }
  };

  composer.addEventListener('submit', (event) => {
    event.preventDefault();
    const prompt = input.value;
    input.value = '';
    sendMessage(prompt);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  if (state.sessionId) {
    text('workbench-session-chip', `session ${short(state.sessionId, 10)}`);
  }
  if (shell && typeof shell.onSnapshot === 'function') {
    shell.onSnapshot(renderSnapshot);
  }
  loadRecentSessions();
})();
