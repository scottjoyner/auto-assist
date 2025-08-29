(() => {
    const tbody = document.getElementById("tbody");
    const filterQ = document.getElementById("filterQ");
    const filterStatus = document.getElementById("filterStatus");
    const btnRefresh = document.getElementById("btnRefresh");
    const btnLoadMore = document.getElementById("btnLoadMore");
    const liveBadge = document.getElementById("liveBadge");
  
    let nextCursor = null;
    const byId = new Map();
  
    function fmtTime(ms) {
      const d = new Date(ms || Date.now());
      return d.toLocaleString();
    }
  
    function renderRow(obj) {
      const tr = document.createElement("tr");
      tr.id = `row_${obj.id}`;
      const updated = obj.updated_at || obj.created_at || Date.now();
      tr.innerHTML = `
        <td>${fmtTime(updated)}</td>
        <td><span class="status ${obj.status||''}">${obj.status||''}</span></td>
        <td>${(obj.question || "").replace(/</g,"&lt;")}</td>
        <td><code>${obj.id}</code></td>
        <td>
          <a href="/api/answers/${obj.id}" target="_blank">JSON</a>
          &nbsp;|&nbsp;
          <a href="/api/answers/${obj.id}/events" target="_blank">SSE</a>
        </td>
      `;
      return tr;
    }
  
    function passesFilters(obj) {
      const qf = (filterQ.value || "").toLowerCase();
      const sf = (filterStatus.value || "");
      if (qf && !(obj.question || "").toLowerCase().includes(qf)) return false;
      if (sf && obj.status !== sf) return false;
      return true;
    }
  
    function upsert(obj) {
      if (!obj || !obj.id) return;
      if (!passesFilters(obj)) return;
  
      const existing = document.getElementById(`row_${obj.id}`);
      const tr = renderRow(obj);
      if (existing) {
        tbody.replaceChild(tr, existing);
      } else {
        tbody.insertBefore(tr, tbody.firstChild);
      }
      byId.set(obj.id, obj);
    }
  
    async function fetchPage(reset=false) {
      const params = new URLSearchParams();
      params.set("limit", "50");
      if (filterStatus.value) params.set("status", filterStatus.value);
      if (filterQ.value) params.set("q", filterQ.value);
      if (!reset && nextCursor) params.set("cursor", nextCursor);
  
      const r = await fetch(`/api/answers?${params.toString()}`, { credentials: "include" });
      const data = await r.json();
      nextCursor = data.next_cursor || null;
  
      if (reset) {
        tbody.innerHTML = "";
        byId.clear();
      }
      (data.items || []).forEach(upsert);
      btnLoadMore.disabled = !nextCursor;
    }
  
    // initial load
    fetchPage(true);
  
    // controls
    btnRefresh.addEventListener("click", () => fetchPage(true));
    btnLoadMore.addEventListener("click", () => fetchPage(false));
    filterQ.addEventListener("input", () => fetchPage(true));
    filterStatus.addEventListener("change", () => fetchPage(true));
  
    // ---------------------------
    // Live updates: WS first, SSE fallback + reconnect
    // ---------------------------
    let transport = null;  // "ws" | "sse"
    let ws = null;
    let es = null;
    let reconnectDelay = 1000; // backoff to 8s max
  
    function handleLiveEvent(payload) {
      // payload is {"type":"new|update|ping|welcome","data":{...}}
      if (!payload || !payload.type) return;
      if (payload.type === "new" || payload.type === "update") {
        upsert(payload.data);
      }
      if (payload.type === "ping") {
        // noop; keepalive
      }
    }
  
    function connectWS() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws/answers`;
      try {
        ws = new WebSocket(url);
      } catch (e) {
        return false;
      }
  
      ws.onopen = () => {
        transport = "ws";
        reconnectDelay = 1000;
        liveBadge.textContent = "Live: WebSocket";
      };
  
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          handleLiveEvent(msg);
        } catch {
          // ignore malformed
        }
      };
  
      ws.onclose = () => {
        if (transport === "ws") {
          liveBadge.textContent = "Live: reconnecting…";
          ws = null;
          setTimeout(() => {
            if (!connectWS()) connectSSE();
          }, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 8000);
        }
      };
  
      ws.onerror = () => {
        // immediate fallback to SSE
        try { ws.close(); } catch {}
        ws = null;
        connectSSE();
      };
  
      return true;
    }
  
    function connectSSE() {
      try {
        es = new EventSource("/api/answers/events", { withCredentials: true });
      } catch {
        liveBadge.textContent = "Live: unavailable";
        return false;
      }
      transport = "sse";
      reconnectDelay = 1000;
      liveBadge.textContent = "Live: SSE";
  
      es.addEventListener("welcome", () => {});
      es.addEventListener("new", (e) => handleLiveEvent({type:"new", data: JSON.parse(e.data)}));
      es.addEventListener("update", (e) => handleLiveEvent({type:"update", data: JSON.parse(e.data)}));
      es.addEventListener("ping", () => {});
  
      es.onerror = () => {
        // try to reconnect with backoff
        try { es.close(); } catch {}
        es = null;
        liveBadge.textContent = "Live: reconnecting…";
        setTimeout(() => {
          // try WS again first (maybe network changed)
          if (!connectWS()) connectSSE();
        }, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 8000);
      };
  
      return true;
    }
  
    // Start: try WS, fallback to SSE
    if (!connectWS()) connectSSE();
  })();
  