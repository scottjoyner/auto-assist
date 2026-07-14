(() => {
    const badge = document.getElementById("live-badge");
    const feed = document.getElementById("live-feed");
    const askBtn = document.getElementById("ask-btn");
    const askClear = document.getElementById("ask-clear");
    const askQ = document.getElementById("ask-q");
    const askStatus = document.getElementById("ask-status");
    const askOutput = document.getElementById("ask-output");
    const esc = window.UIUtils?.escapeHtml || ((s) => (s ?? "").toString());
    const shortId = window.UIUtils?.shortId || ((v, n = 8) => (v || "").toString().slice(0, n));
  
    function el(tag, cls, txt){
      const e=document.createElement(tag); if(cls) e.className=cls; if(txt) e.textContent=txt; return e;
    }
  
    function addFeedRow(obj) {
      if (!obj || !obj.id) return;
      const row = el("div", "row");
      const meta = el("div", "muted");
      meta.innerHTML = `<code>${esc(shortId(obj.id, 12))}</code> · ${esc(new Date(obj.updated_at||obj.created_at||Date.now()).toLocaleString())}`;
      const q = el("div");
      q.innerHTML = `<b>Q:</b> ${esc(obj.question||"")} <span class="status">${esc(obj.status||"")}</span>`;
      row.appendChild(meta);
      row.appendChild(q);
      feed.insertBefore(row, feed.firstChild);
      // prune
      while (feed.childElementCount > 200) feed.removeChild(feed.lastChild);
    }
  
    // Live global stream: WS -> SSE fallback
    (function liveConnect(){
      let connected = false;
      function setBadge(state){
        if(state==="ws") { badge.textContent="live: WebSocket"; badge.classList.add("ok"); badge.classList.remove("err"); }
        else if(state==="sse") { badge.textContent="live: SSE"; badge.classList.add("ok"); badge.classList.remove("err"); }
        else if(state==="reconnect") { badge.textContent="live: reconnecting…"; badge.classList.remove("ok"); badge.classList.remove("err"); }
        else { badge.textContent="live: disconnected"; badge.classList.add("err"); badge.classList.remove("ok"); }
      }
  
      function startWS(){
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${location.host}/ws/answers`);
        ws.onopen = () => { connected = true; setBadge("ws"); };
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type === "new" || msg.type === "update") addFeedRow(msg.data);
          } catch {}
        };
        ws.onclose = () => { if (connected) setBadge("reconnect"); setTimeout(()=>startSSE(), 800); };
        ws.onerror = () => { connected = false; try{ws.close();}catch{}; setTimeout(()=>startSSE(), 200); };
      }
      function startSSE(){
        try {
          const es = new EventSource("/api/answers/events", { withCredentials: true });
          setBadge("sse");
          es.addEventListener("new", (e)=> addFeedRow(JSON.parse(e.data)));
          es.addEventListener("update", (e)=> addFeedRow(JSON.parse(e.data)));
          es.onerror = () => { try{es.close();}catch{}; setBadge("reconnect"); setTimeout(()=>startWS(), 1000); };
        } catch { setBadge("disconnected"); }
      }
      startWS();
    })();
  
    async function loadHomeSnapshot() {
      const [health, ops, router, voice, memory] = await Promise.all([
        loadJSON('/health'),
        loadJSON('/api/ops/status'),
        loadJSON('/api/router/status'),
        loadJSON('/api/intents?source=voice&limit=5'),
        loadJSON('/api/memory?view=durable&limit=5'),
      ]);

      const setMetric = (id, noteId, value, note) => {
        const v = document.getElementById(id);
        const n = document.getElementById(noteId);
        if (v) v.textContent = value;
        if (n) n.textContent = note;
      };

      if (health) {
        setMetric(
          'home-runtime',
          'home-runtime-note',
          health.status || (health.ok ? 'ok' : 'degraded'),
          `${health.service || 'assistx'} · ${health.profile || 'unknown'}${health.timestamp ? ` · ${new Date(Number(health.timestamp)).toLocaleString()}` : ''} · source /health`,
        );
        setMetric(
          'home-neo4j',
          'home-neo4j-note',
          health.dependencies?.neo4j?.status || 'unknown',
          health.dependencies?.neo4j?.uri ? `${health.dependencies.neo4j.uri} / ${health.dependencies.neo4j.database || 'default'} · source /health` : 'graph endpoint not reported · source /health',
        );
      } else {
        setMetric('home-runtime', 'home-runtime-note', 'offline', 'health endpoint unavailable');
        setMetric('home-neo4j', 'home-neo4j-note', 'unknown', 'health endpoint unavailable');
      }

      if (router) {
        const taskCount = router.graph?.tasks?.total ?? 0;
        const readyCount = router.graph?.tasks?.ready ?? 0;
        setMetric(
          'home-router',
          'home-router-note',
          router.graph?.neo4j || router.graph?.status || 'unknown',
          `tasks ${taskCount} · ready ${readyCount} · source /api/router/status`,
        );
      } else {
        setMetric('home-router', 'home-router-note', 'unavailable', 'router status unavailable');
      }

      if (voice) {
        const count = voice.count ?? voice.items?.length ?? 0;
        const latest = voice.items?.[0]?.text || voice.items?.[0]?.intent || voice.items?.[0]?.source || 'voice feed';
        setMetric(
          'home-voice',
          'home-voice-note',
          String(count),
          `voice intents · ${latest.substring(0, 80)} · source /api/intents?source=voice`,
        );
      } else {
        setMetric('home-voice', 'home-voice-note', 'unavailable', 'voice intents unavailable');
      }

      if (memory) {
        const count = memory.count ?? 0;
        setMetric(
          'home-memory',
          'home-memory-note',
          String(count),
          `${memory.view || 'durable'} view · ${count === 0 ? 'no durable items yet' : `${count} items visible`} · source /api/memory?view=durable`,
        );
      } else {
        setMetric('home-memory', 'home-memory-note', '—', 'memory endpoint unavailable');
      }

      const validation = document.getElementById('validation-summary');
      if (validation) {
        const checks = [
          { label: 'AssistX health', pass: !!health?.ok, note: health ? `${health.status || 'unknown'} · /health` : 'unreachable' },
          { label: 'Neo4j', pass: (health?.dependencies?.neo4j?.status || '').toLowerCase() === 'ok', note: health?.dependencies?.neo4j?.uri ? `${health.dependencies.neo4j.status || 'unknown'} · ${health.dependencies.neo4j.database || 'default'} · /health` : 'not reported · /health' },
          { label: 'Ops status', pass: !!ops?.neo4j, note: ops ? `queue ${ops.queue?.depth ?? 0} · failed ${ops.dispatches?.failed_or_cancelled ?? 0} · /api/ops/status` : 'unreachable' },
          { label: 'Router graph', pass: (router?.graph?.neo4j || '').toLowerCase() === 'online', note: router ? `tasks ${router.graph?.tasks?.total ?? 0} · ready ${router.graph?.tasks?.ready ?? 0} · /api/router/status` : 'unreachable' },
          { label: 'Voice intents', pass: !!voice, note: voice ? `${voice.count ?? 0} items · /api/intents?source=voice` : 'unreachable' },
          { label: 'Durable memory', pass: !!memory, note: memory ? `${memory.count ?? 0} items · /api/memory?view=durable` : 'unreachable' },
        ];
        const failed = checks.filter((c) => !c.pass);
        const passing = checks.length - failed.length;
        const ordered = [...failed, ...checks.filter((c) => c.pass)];
        validation.innerHTML = [
          `<div class="summary-line"><span class="badge ${failed.length ? 'err' : 'ok'}">${failed.length ? 'ATTENTION' : 'ALL GREEN'}</span><span class="muted">${passing} passing · ${failed.length} failing</span></div>`,
          ...ordered.map(({ label, pass, note }) => `
            <div class="summary-line">
              <span class="badge ${pass ? 'ok' : 'err'}">${pass ? 'PASS' : 'FAIL'}</span>
              <span class="badge">${label}</span>
              <span class="muted">${note}</span>
            </div>
          `),
        ].join('');
      }
    }

    loadHomeSnapshot();

    // Ask panel (auto mode with short wait; if 202, follow per-answer SSE until DONE)
    askBtn?.addEventListener("click", async () => {
      const question = (askQ.value||"").trim();
      if (!question) return;
      askStatus.textContent = "submitting…";
      askOutput.innerHTML = "";
  
      try {
        const r = await fetch("/api/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ question, mode: "auto", timeout_s: 6 })
        });
  
        const data = await r.json();
  
        // 200 with final data
        if (r.status === 200 && data && data.answer) {
          askStatus.textContent = "done";
          renderAnswer(data);
          return;
        }
  
        // 202 Accepted → follow the answer id
        if (r.status === 202 && data && data.answer_id) {
          askStatus.textContent = `queued: ${data.answer_id}`;
          followAnswer(data.answer_id);
          return;
        }
  
        // or envelope from auto mode (DONE/FAILED)
        if (data && data.status) {
          askStatus.textContent = data.status;
          if (data.data && data.data.answer) renderAnswer(data.data);
          return;
        }
  
        askStatus.textContent = "unexpected response";
        askOutput.textContent = JSON.stringify(data, null, 2);
      } catch (e) {
        askStatus.textContent = "error";
        askOutput.textContent = String(e);
      }
    });
  
    askClear?.addEventListener("click", () => {
      askQ.value = ""; askStatus.textContent = ""; askOutput.innerHTML = "";
    });
  
    function renderAnswer(payload){
      // payload is the pipeline output {answer, data_preview, cypher, analysis_code, ...}
      const wrap = el("div");
      const ans = el("div");
      ans.innerHTML = `<h3>Answer</h3><div class="prewrap">${esc(payload.answer||"")}</div>`;
      wrap.appendChild(ans);
  
      if (payload.data_preview) {
        const pre = el("pre"); pre.textContent = JSON.stringify(payload.data_preview, null, 2);
        const h = el("h4", null, "Data preview");
        wrap.appendChild(h); wrap.appendChild(pre);
      }
      if (payload.cypher) {
        const pre = el("pre"); pre.textContent = payload.cypher;
        const h = el("h4", null, "Cypher");
        wrap.appendChild(h); wrap.appendChild(pre);
      }
      if (payload.analysis_code) {
        const pre = el("pre"); pre.textContent = payload.analysis_code;
        const h = el("h4", null, "Analysis code");
        wrap.appendChild(h); wrap.appendChild(pre);
      }
      askOutput.innerHTML = "";
      askOutput.appendChild(wrap);
    }
  
    function followAnswer(id){
      // prefer per-answer SSE for simplicity
      const es = new EventSource(`/api/answers/${id}/events`, { withCredentials: true });
      askStatus.textContent = `waiting… (${id})`;
      es.addEventListener("init", (e) => {
        try {
          const obj = JSON.parse(e.data);
          askStatus.textContent = obj.status || "QUEUED";
        } catch {}
      });
      es.addEventListener("update", (e) => {
        const obj = JSON.parse(e.data);
        askStatus.textContent = obj.status || "";
        if (obj.status === "DONE" && obj.data) {
          renderAnswer(obj.data);
          es.close();
        }
        if (obj.status === "FAILED" && obj.error) {
          askOutput.textContent = obj.error;
          es.close();
        }
      });
      es.onerror = () => { try{es.close();}catch{}; askStatus.textContent = "stream error"; };
    }
  })();
  
