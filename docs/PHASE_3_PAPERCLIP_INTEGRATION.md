# Phase 3 - Paperclip Dispatch Integration

## Overview

Phase 3 adds Paperclip as an optional assignment transport around the
graph-first Task trigger flow:
1. AssistX creates or updates a `Task` in Neo4j
2. Agents can poll and claim `READY` tasks directly from AssistX
3. AssistX may also create a Paperclip issue for cross-device assignment
4. Hermes executes → writes results back
5. AssistX ingests results → updates graph

Neo4j remains the source of truth. Paperclip issue state should reconcile back
to `Task`, `Dispatch`, `AgentRun`, `SignalEvent`, and `MemoryItem` nodes.

---

## 1. Implementation Checklist

### 1.1 Paperclip API Client

**Goal**: Create a helper class to interact with Paperclip API

**Current status**: implemented in `src/assistx/paperclip_client.py`.

**File**: `src/assistx/paperclip_client.py` (NEW)

```python
import os
import requests
from typing import Optional, Dict, Any, List

class PaperclipClient:
    def __init__(
        self,
        api_url: Optional[str] = None,
        api_token: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        self.api_url = api_url or os.getenv("PAPERCLIP_API_URL")
        self.api_token = api_token or os.getenv("PAPERCLIP_API_TOKEN")
        self.workspace_id = workspace_id or os.getenv("PAPERCLIP_WORKSPACE_ID")
        
        if not (self.api_url and self.api_token and self.workspace_id):
            raise ValueError(
                "Paperclip requires PAPERCLIP_API_URL, PAPERCLIP_API_TOKEN, "
                "and PAPERCLIP_WORKSPACE_ID environment variables."
            )
        
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
    
    def create_issue(
        self,
        title: str,
        description: str,
        task_id: str,
        context_packet_id: str,
        capabilities: List[str],
        priority: str = "normal",
        assignee_id: Optional[str] = None,
    ) -> str:
        """Create a Paperclip issue from an AssistX task."""
        payload = {
            "workspace_id": self.workspace_id,
            "title": title,
            "description": description,
            "assignee_id": assignee_id,
            "priority": priority,
            "metadata": {
                "assistx_task_id": task_id,
                "assistx_context_packet_id": context_packet_id,
                "required_capabilities": capabilities,
                "source": "assistx-migration",
            },
        }
        resp = requests.post(
            f"{self.api_url}/issues",
            json=payload,
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    
    def get_issue(self, issue_id: str) -> Dict[str, Any]:
        """Fetch issue details."""
        resp = requests.get(
            f"{self.api_url}/issues/{issue_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    
    def list_agents(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List registered agents."""
        params = {"workspace_id": workspace_id or self.workspace_id}
        resp = requests.get(
            f"{self.api_url}/agents",
            params=params,
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("agents", [])
    
    def assign_issue(self, issue_id: str, agent_id: str) -> bool:
        """Assign issue to an agent."""
        payload = {"agent_id": agent_id}
        resp = requests.post(
            f"{self.api_url}/issues/{issue_id}/assign",
            json=payload,
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("success", False)
```

### 1.2 Enhanced Neo4j Dispatch Methods

**Goal**: Update dispatch creation to optionally call Paperclip

**Current status**: implemented. The live API creates local Neo4j dispatch
records, creates a Paperclip issue when `PAPERCLIP_*` configuration is present,
falls back to local-only dispatch when Paperclip is not configured, and ingests
Paperclip events via `/api/paperclip/events`.

**File**: `src/assistx/neo4j_client.py` (UPDATE)

```python
def create_dispatch_with_paperclip(
    self,
    task_id: str,
    context_packet_id: str,
    target: Dict[str, Any],
    priority: str = "MEDIUM",
    idempotency_key: Optional[str] = None,
    paperclip_client: Optional["PaperclipClient"] = None,
) -> str:
    """
    Create dispatch and optionally create a Paperclip issue.
    
    If paperclip_client is provided and no paperclip_issue_id in target,
    creates an issue and links it.
    """
    # Get task details for Paperclip issue creation
    task = self.get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    
    paperclip_issue_id = target.get("paperclip_issue_id")
    
    # Create Paperclip issue if client provided
    if paperclip_client and not paperclip_issue_id:
        try:
            paperclip_issue_id = paperclip_client.create_issue(
                title=task.get("title", "Unnamed Task"),
                description=task.get("payload", {}).get("description", ""),
                task_id=task_id,
                context_packet_id=context_packet_id,
                capabilities=target.get("capabilities", []),
                priority=priority.lower(),
                assignee_id=target.get("paperclip_agent_id"),
            )
            target = {**target, "paperclip_issue_id": paperclip_issue_id}
        except Exception as e:
            # Log but don't fail; dispatch can still be created locally
            import logging
            logging.getLogger(__name__).warning(f"Failed to create Paperclip issue: {e}")
    
    # Create dispatch in Neo4j
    dispatch_id = self.create_dispatch(
        task_id=task_id,
        target=target,
        priority=priority,
        idempotency_key=idempotency_key,
    )
    
    # Link context packet to dispatch
    with self._session() as s:
        s.run(
            "MATCH (d:Dispatch {id:$did}), (p:ContextPacket {id:$pid}) "
            "MERGE (d)-[:USES_CONTEXT]->(p)",
            {"did": dispatch_id, "pid": context_packet_id},
        )
    
    return dispatch_id
```

### 1.3 Enhanced API Endpoint

**File**: `src/assistx/api.py` (UPDATE)

Add Paperclip client initialization and update dispatch endpoint:

```python
from .paperclip_client import PaperclipClient

_paperclip_client: Optional[PaperclipClient] = None

def get_paperclip_client() -> Optional[PaperclipClient]:
    global _paperclip_client
    if _paperclip_client is None:
        try:
            _paperclip_client = PaperclipClient()
        except (ValueError, KeyError):
            # Paperclip not configured; dispatch remains local-only
            return None
    return _paperclip_client

@app.post("/api/dispatch")
def api_create_dispatch(body: DispatchIn, user: str = Depends(auth)):
    neo = _neo()
    pc = get_paperclip_client()
    try:
        # Create context packet first if not provided
        if not body.task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        
        # Get or create context packet
        task = neo.get_task(body.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        packet = neo.create_context_packet(
            query=task.get("title", "Task context"),
            task_id=body.task_id,
            max_items=20,
            include_sources=["memory", "orchestration", "knowledge"],
        )
        
        # Create dispatch (with optional Paperclip issue)
        dispatch_id = neo.create_dispatch_with_paperclip(
            task_id=body.task_id,
            context_packet_id=packet["id"],
            target=body.target.model_dump(),
            priority=body.priority,
            idempotency_key=body.idempotency_key,
            paperclip_client=pc,
        )
        return {"dispatch_id": dispatch_id, "context_packet_id": packet["id"]}
    finally:
        neo.close()
```

### 1.4 Webhook Handler for Paperclip Events

**File**: `src/assistx/api.py` (ADD)

```python
class PaperclipWebhookIn(BaseModel):
    """Webhook payload from Paperclip → AssistX"""
    event_type: str  # issue_created, assigned, run_started, run_completed, commented
    issue_id: str
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    timestamp: str
    payload: Dict[str, Any] = Field(default_factory=dict)

@app.post("/api/webhooks/paperclip")
async def webhook_paperclip(
    body: PaperclipWebhookIn,
    x_webhook_signature: Optional[str] = Header(None),
):
    """
    Receive webhook events from Paperclip.
    
    Signature validation recommended (using HMAC-SHA256 if PAPERCLIP_WEBHOOK_SECRET set).
    """
    # TODO: Validate webhook signature if secret available
    
    neo = _neo()
    try:
        # Route by event type
        if body.event_type == "run_completed":
            # Extract result and update dispatch
            result = body.payload.get("result", {})
            summary = body.payload.get("summary", "")
            
            issue_id = neo.ingest_paperclip_event(
                event_type=body.event_type,
                paperclip_issue_id=body.issue_id,
                paperclip_agent_id=body.agent_id,
                paperclip_run_id=body.run_id,
                event_id=f"{body.issue_id}-{body.run_id}",
                payload={
                    "result": result,
                    "summary": summary,
                    "timestamp": body.timestamp,
                },
            )
            
            # Update related task if result indicates completion
            with neo.driver.session() as s:
                rec = s.run(
                    "MATCH (d:Dispatch {paperclip_issue_id:$iid})-[:DISPATCHED_AS]-(t:Task) "
                    "RETURN t.id AS task_id",
                    {"iid": body.issue_id},
                ).single()
                if rec:
                    task_id = rec["task_id"]
                    if result.get("success"):
                        neo.update_task_status(task_id, "DONE")
                        # Optionally log outcome
                        neo.upsert_memory_item(
                            kind="outcome",
                            text=summary or f"Task completed: {result}",
                            source="paperclip",
                            task_id=task_id,
                            metadata={"result": result},
                        )
                    else:
                        neo.update_task_status(task_id, "FAILED")
            
            return {"ok": True, "processed": True}
        else:
            # Other event types (assigned, run_started, etc.)
            neo.ingest_paperclip_event(
                event_type=body.event_type,
                paperclip_issue_id=body.issue_id,
                paperclip_agent_id=body.agent_id,
                paperclip_run_id=body.run_id,
                event_id=f"{body.issue_id}-{body.event_type}-{body.timestamp}",
                payload=body.payload,
            )
            return {"ok": True, "processed": True}
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"Webhook processing failed: {e}")
        return {"ok": False, "error": str(e)}, 500
    finally:
        neo.close()
```

### 1.5 Agent Discovery and Capability Matching

**File**: `src/assistx/neo4j_client.py` (ADD)

```python
def find_agent_by_capabilities(
    self,
    required_capabilities: List[str],
    agent_devices: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Find the best-matching agent device ID based on required capabilities.
    
    For now, simple matching: returns first device with all required capabilities.
    Can be enhanced with scoring/ranking logic.
    """
    with self._session() as s:
        q = "MATCH (d:AgentDevice)"
        if agent_devices:
            q += f" WHERE d.id IN {agent_devices}"
        q += " RETURN d.id AS device_id, d.capabilities AS caps"
        
        res = s.run(q)
        for r in res:
            device_caps = r["caps"] or []
            if all(cap in device_caps for cap in required_capabilities):
                return r["device_id"]
    
    return None
```

---

## 2. Configuration and Secrets

### Environment Variables

```bash
# Paperclip integration
PAPERCLIP_API_URL=https://paperclip.example.com/api
PAPERCLIP_API_TOKEN=<token>
PAPERCLIP_WORKSPACE_ID=<workspace-id>
PAPERCLIP_WEBHOOK_SECRET=<hmac-secret-for-validation>  # optional
```

### Docker Compose Example

Add to `docker-compose.yml`:

```yaml
services:
  assistx:
    environment:
      - PAPERCLIP_API_URL=${PAPERCLIP_API_URL}
      - PAPERCLIP_API_TOKEN=${PAPERCLIP_API_TOKEN}
      - PAPERCLIP_WORKSPACE_ID=${PAPERCLIP_WORKSPACE_ID}
```

---

## 3. Testing Strategy

### Unit Tests

**File**: `tests/test_paperclip_integration.py` (NEW)

```python
import pytest
from unittest.mock import patch, MagicMock
from assistx.paperclip_client import PaperclipClient
from assistx.neo4j_client import Neo4jClient

def test_paperclip_client_create_issue():
    """Test Paperclip issue creation"""
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"id": "issue-123"}
        
        pc = PaperclipClient(
            api_url="https://api.example.com",
            api_token="token",
            workspace_id="ws-1",
        )
        issue_id = pc.create_issue(
            title="Test task",
            description="Do something",
            task_id="task-1",
            context_packet_id="packet-1",
            capabilities=["code_execution"],
        )
        
        assert issue_id == "issue-123"
        mock_post.assert_called_once()

def test_create_dispatch_with_paperclip(seeded_neo4j, monkeypatch):
    """Test dispatch creation with Paperclip issue"""
    neo = seeded_neo4j
    
    # Mock Paperclip client
    mock_pc = MagicMock()
    mock_pc.create_issue.return_value = "paperclip-issue-456"
    
    task = neo.get_ready_tasks()[0]
    packet = neo.create_context_packet(
        query="test",
        task_id=task["id"],
        include_sources=["memory"],
    )
    
    dispatch_id = neo.create_dispatch_with_paperclip(
        task_id=task["id"],
        context_packet_id=packet["id"],
        target={"capabilities": ["code"]},
        paperclip_client=mock_pc,
    )
    
    assert dispatch_id
    mock_pc.create_issue.assert_called_once()

def test_webhook_run_completed(seeded_neo4j, monkeypatch):
    """Test Paperclip webhook for run_completed"""
    from assistx.api import app
    from fastapi.testclient import TestClient
    
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    
    client = TestClient(app)
    auth = ("neo4j", "livelongandprosper")
    
    webhook_payload = {
        "event_type": "run_completed",
        "issue_id": "paperclip-issue-1",
        "agent_id": "agent-1",
        "run_id": "run-1",
        "timestamp": "2026-05-22T15:00:00Z",
        "payload": {
            "result": {"success": True, "output": "Task done"},
            "summary": "Successfully processed",
        },
    }
    
    r = client.post(
        "/api/webhooks/paperclip",
        json=webhook_payload,
        auth=auth,
    )
    assert r.status_code == 200
```

### Integration Tests

1. **Task → Dispatch → Paperclip Issue**: Create task, dispatch, verify issue created
2. **Paperclip Webhook → Result Sync**: Send webhook, verify task status updated
3. **Agent Capability Matching**: Register agent, dispatch with required caps, verify assignment

---

## 4. Deployment Steps

### Pre-Deployment

1. [ ] Confirm Paperclip API is accessible and authenticated
2. [ ] Set `PAPERCLIP_API_URL`, `PAPERCLIP_API_TOKEN`, `PAPERCLIP_WORKSPACE_ID`
3. [ ] Register Paperclip webhook endpoint with AssistX (configure in Paperclip UI)
4. [ ] List available agents and register their capabilities in Neo4j
5. [ ] Test PaperclipClient connectivity

### Deployment

1. Deploy `paperclip_client.py` to AssistX
2. Deploy updated `neo4j_client.py` with `create_dispatch_with_paperclip`
3. Deploy updated `api.py` with webhook handler
4. Restart AssistX API
5. Run integration tests

### Post-Deployment

1. [ ] Monitor webhook delivery logs
2. [ ] Verify task → dispatch → issue → completion flow
3. [ ] Check Neo4j for dispatch state consistency
4. [ ] Monitor error rates and latency

---

## 5. Rollback Plan

If Paperclip integration fails:
1. Set `PAPERCLIP_API_URL=""` to disable client initialization
2. Dispatch creation reverts to local-only mode
3. Existing tasks/dispatches remain intact
4. No data loss; can re-enable later

---

## 6. Future Enhancements

- [ ] Agent capability scoring and ranking
- [ ] Round-robin agent assignment for load balancing
- [ ] Webhook signature validation (HMAC-SHA256)
- [ ] Retry logic for failed Paperclip API calls
- [ ] Batch issue creation for bulk dispatches
- [ ] Cost tracking and agent utilization metrics
