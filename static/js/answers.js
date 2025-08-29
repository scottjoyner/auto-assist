(() => {
    const tbody = document.getElementById("tbody");
    const filterQ = document.getElementById("filterQ");
    const filterStatus = document.getElementById("filterStatus");
    const btnRefresh = document.getElementById("btnRefresh");
    const btnLoadMore = document.getElementById("btnLoadMore");
    const liveBadge = document.getElementById("liveBadge");
  
    let nextCursor = null;
    let rows = []; // local cache by id
    const byId = new Map();
  
    function fmtTime(ms) {
      const d = new Date(ms);
      return d.toLocaleString();
    }
  
    function renderRow(obj) {
      const tr = document.createElement("tr");
      tr.id = `row_${obj.id}`;
      tr.innerHTML = `
        <td>${fmtTime(obj.updated_at || obj.created_at || Date.now())}</td>
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
  
    function upsert(obj) {
      // optional client-side filters
      const qf = (filterQ.value || "").toLowerCase();
      const sf = (filterStatus.value || "");
      if (qf && !(obj.question || "").toLowerCase().includes(qf)) return;
      if (sf && obj.status !== sf) return;
  
      const existing = document.getElementById(`row_${obj.id}`);
      const tr = renderRow(obj);
      if (existing) {
        tbody.replaceChild(tr, existing);
      } else {
        // prepend newest at top
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
  
    // initial
    fetchPage(true);
  
    // controls
    btnRefresh.addEventListener("click", () => fetchPage(true));
    btnLoadMore.addEventListener("click", () => fetchPage(false));
    filterQ.addEventListener("input", () => fetchPage(true));
    filterStatus.addEventListener("change", () => fetchPage(true));
  
    // live SSE
    try {
      const es = new EventSource("/api/answers/events", { withCredentials: true });
      liveBadge.textContent = "Live: connected";
      es.addEventListener("welcome", e => {});
      es.addEventListener("new", e => { upsert(JSON.parse(e.data)); });
      es.addEventListener("update", e => { upsert(JSON.parse(e.data)); });
      es.addEventListener("ping", e => {});
      es.onerror = () => { liveBadge.textContent = "Live: disconnected"; };
    } catch (e) {
      liveBadge.textContent = "Live: unsupported";
    }
  })();
  