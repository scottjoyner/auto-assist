(() => {
    const badge = document.getElementById("live-badge");
    const feed = document.getElementById("live-feed");
    const askBtn = document.getElementById("ask-btn");
    const askClear = document.getElementById("ask-clear");
    const askQ = document.getElementById("ask-q");
    const askStatus = document.getElementById("ask-status");
    const askOutput = document.getElementById("ask-output");
  
    function el(tag, cls, txt){
      const e=document.createElement(tag); if(cls) e.className=cls; if(txt) e.textContent=txt; return e;
    }
  
    function addFeedRow(obj) {
      if (!obj || !obj.id) return;
      const row = el("div", "row");
      const meta = el("div", "muted");
      meta.innerHTML = `<code>${obj.id}</code> · ${new Date(obj.updated_at||obj.created_at||Date.now()).toLocaleString()}`;
      const q = el("div");
      q.innerHTML = `<b>Q:</b> ${(obj.question||"").replace(/</g,"&lt;")} <span class="status">${obj.status||""}</span>`;
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
        ws.onerror = () => { try{ws.close();}catch{}; setTimeout(()=>startSSE(), 200); };
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
      ans.innerHTML = `<h3>Answer</h3><div style="white-space:pre-wrap">${(payload.answer||"").replace(/</g,"&lt;")}</div>`;
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
  