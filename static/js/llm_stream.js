(() => {
    const $ = (id) => document.getElementById(id);
    const model   = $("llm-model");
    const system  = $("llm-system");
    const prompt  = $("llm-prompt");
    const messages= $("llm-messages");
    const start   = $("llm-start");
    const stopBtn = $("llm-stop");
    const out     = $("llm-out");
    const status  = $("llm-status");
  
    let controller = null;
  
    function append(txt){
      out.textContent += txt;
      out.scrollTop = out.scrollHeight;
    }
  
    function writeLine(obj){
      out.textContent += (typeof obj === "string" ? obj : JSON.stringify(obj)) + "\n";
      out.scrollTop = out.scrollHeight;
    }
  
    start?.addEventListener("click", async () => {
      if (controller) controller.abort();
      controller = new AbortController();
      out.textContent = "";
      status.textContent = "connecting…";
  
      // build body
      let msgs = null;
      const mraw = (messages.value||"").trim();
      if (mraw) {
        try { msgs = JSON.parse(mraw); }
        catch { writeLine({error:"messages JSON invalid"}); return; }
      }
  
      const body = {
        model: (model.value||"").trim() || undefined,
        system: (system.value||"").trim() || undefined,
        prompt: (prompt.value||"").trim() || undefined,
        messages: msgs || undefined,
        options: undefined, // add temp/top_p etc if desired
      };
  
      try {
        const resp = await fetch("/api/llm/stream", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body),
          signal: controller.signal
        });
  
        if (!resp.ok) {
          status.textContent = `HTTP ${resp.status}`;
          writeLine(await resp.text());
          return;
        }
  
        status.textContent = "streaming…";
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const dispatch = (event, data) => {
          if (event === "model")   { writeLine(data); return; }
          if (event === "delta")   { append(typeof data === "string" ? data : (data?.text || "")); return; }
          if (event === "error")   { writeLine(data); status.textContent = "error"; }
          if (event === "done")    { writeLine(data); status.textContent = "done"; }
        };
  
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          // parse SSE frames
          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = frame.split("\n").map(l => l.trim()).filter(Boolean);
            let event = "message"; let data = "";
            for (const line of lines) {
              if (line.startsWith("event:")) event = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim();
            }
            try { data = data ? JSON.parse(data) : data; } catch {}
            dispatch(event, data);
          }
        }
      } catch (e) {
        if (e.name === "AbortError") {
          status.textContent = "stopped";
        } else {
          status.textContent = "error";
          writeLine({error:String(e)});
        }
      }
    });
  
    stopBtn?.addEventListener("click", () => {
      if (controller) controller.abort();
    });
  })();
  