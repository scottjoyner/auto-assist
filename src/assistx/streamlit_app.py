# streamlit_app.py
import os, requests, streamlit as st
from requests.auth import HTTPBasicAuth

API = os.getenv("ASSISTX_API", "http://localhost:8000")
USER = os.getenv("BASIC_AUTH_USER", "admin")
PASS = os.getenv("BASIC_AUTH_PASS", "change-me")
AUTH = HTTPBasicAuth(USER, PASS)

st.set_page_config(page_title="AssistX", layout="wide")
st.title("AssistX Control Panel")

tab1, tab2 = st.tabs(["Transcriptions", "Tasks"])

with tab1:
    c1, c2 = st.columns([2,1])
    with c1:
        q = st.text_input("Search (contains)", "")
    with c2:
        limit = st.slider("Limit", 5, 200, 50, 5)

    r = requests.get(f"{API}/api/transcriptions", params={"q": q or None, "limit": limit}, auth=AUTH)
    data = r.json() if r.ok else {"items": []}
    st.caption(f"{data.get('count', 0)} result(s)")
    for tr in data.get("items", []):
        with st.expander(f"{tr.get('key','(no-key)')} · {tr.get('id')}"):
            st.write(tr.get("text","")[:500] + ("..." if (tr.get("text") and len(tr["text"])>500) else ""))
            ttitle = st.text_input(f"Task title for {tr['id']}", f"Summarize: {tr.get('key','transcription')}", key=f"ttl_{tr['id']}")
            cols = st.columns(3)
            if cols[0].button("Create Task (REVIEW)", key=f"crt_{tr['id']}"):
                resp = requests.post(
                    f"{API}/api/transcriptions/{tr['id']}/task",
                    json={"title": ttitle, "status": "REVIEW", "kind": "transcription_summary"},
                    auth=AUTH
                )
                st.success(resp.json())
            if cols[1].button("Create Task (READY)", key=f"crt_r_{tr['id']}"):
                resp = requests.post(
                    f"{API}/api/transcriptions/{tr['id']}/task",
                    json={"title": ttitle, "status": "READY", "kind": "transcription_summary"},
                    auth=AUTH
                )
                st.success(resp.json())
            if cols[2].button("Embed (spawn task)", key=f"emb_{tr['id']}"):
                resp = requests.post(f"{API}/api/transcriptions/{tr['id']}/embed", auth=AUTH)
                st.info(resp.json())

with tab2:
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        status = st.selectbox("Status", ["", "READY", "REVIEW", "RUNNING", "DONE", "FAILED"], index=0)
    with c2:
        limit2 = st.slider("Limit", 5, 200, 50, 5, key="lim2")
    with c3:
        run_now = st.checkbox("Enqueue READY on click", value=False)

    r = requests.get(f"{API}/api/tasks", params={"status": (status or None), "limit": limit2}, auth=AUTH)
    tasks = r.json().get("items", []) if r.ok else []
    for t in tasks:
        with st.expander(f"{t.get('title','(no-title)')} · {t['id']} · {t.get('status')}"):
            st.json(t)
            cols = st.columns(3)
            if cols[0].button("Details", key=f"det_{t['id']}"):
                d = requests.get(f"{API}/api/tasks/{t['id']}", auth=AUTH).json()
                st.code(d, language="json")
            if cols[1].button("Enqueue", key=f"enq_{t['id']}"):
                r2 = requests.post(f"{API}/tasks/{t['id']}/enqueue", params={"dry_run": False}, auth=AUTH)
                st.success(r2.json())
            if cols[2].button("Mark READY", key=f"mrd_{t['id']}"):
                # quick status toggle using existing endpoint
                r3 = requests.post(f"{API}/tasks/{t['id']}/approve", auth=AUTH)
                if r3.ok: st.success("Task set to READY")
