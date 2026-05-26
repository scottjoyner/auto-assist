(() => {
  function escapeHtml(value) {
    return (value ?? "").toString().replace(/[&<>"']/g, (c) => (
      c === "&" ? "&amp;" :
      c === "<" ? "&lt;" :
      c === ">" ? "&gt;" :
      c === '"' ? "&quot;" : "&#39;"
    ));
  }

  function shortId(value, max = 8) {
    const s = (value ?? "").toString();
    if (!s) return "";
    return s.length <= max ? s : `${s.slice(0, max)}…`;
  }

  function renderBadgeLine(parts) {
    if (!Array.isArray(parts) || parts.length === 0) return "";
    return `<div class="summary-line">${parts.filter(Boolean).join(" ")}</div>`;
  }

  function renderMetaLine(text, cls = "muted mt-2") {
    const safe = escapeHtml(text || "");
    if (!safe) return "";
    return `<div class="${escapeHtml(cls)}">${safe}</div>`;
  }

  window.UIUtils = {
    escapeHtml,
    shortId,
    renderBadgeLine,
    renderMetaLine,
  };
})();
