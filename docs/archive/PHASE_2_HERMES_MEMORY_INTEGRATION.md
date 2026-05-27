# Phase 2 - Hermes Memory Provider Integration

## Overview

The Hermes memory provider is the bridge that allows Hermes agents to:
1. Prefetch bounded graph context before executing tasks
2. Write observations and outcomes back to Neo4j
3. Query session state and task status
4. Participate in multi-agent orchestration

In the graph-first flow, Hermes agents discover executable work by polling
`/api/agent/tasks`, claim `Task` nodes with `/api/tasks/{task_id}/claim`, then
prefetch the latest graph context before executing. `Intent` records classify
incoming input; `Task` records trigger agent work.

---

## 1. Architecture

### Data Flow

```
Before Hermes Turn:
  Hermes Agent
    → /api/agent/tasks (poll READY task triggers)
    → /api/tasks/{task_id}/claim
    ↓
  AssistX Brain API
    → /api/brain/context (query)
    → Returns ContextPacket with references
    ↓
  Hermes Memory Provider
    → Stores context in turn state
    ↓
  Hermes Agent
    → Processes turn with context available
    ↓

After Hermes Turn:
  Hermes Agent
    → Calls memory.write_memory()
    ↓
  Memory Provider
    → POST /api/memory/items
    ↓
  AssistX
    → MemoryItem persisted to Neo4j
    ↓
  Next Hermes Turn
    → Context includes fresh memory
```

---

## 2. Hermes Provider Implementation

### File Structure

```
src/assistx/agents/
  __init__.py
  hermes_memory_provider.py  ← Main provider implementation
  orchestrator.py            ← Session orchestration
  llm.py                     ← LLM configuration
```

### Key Classes

**HermesMemoryProvider**

```python
from typing import Optional, Dict, List, Any

class HermesMemoryProvider:
    """
    External memory provider for Hermes agents.
    
    Connects to AssistX Brain APIs and Neo4j.
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        auth: Optional[tuple[str, str]] = None,
        api_token: Optional[str] = None,
    ):
        """Initialize with AssistX API connection details."""
        
    def system_prompt_block(self) -> str:
        """
        Return system prompt snippet telling Hermes about graph memory availability.
        
        Example:
        "You have access to a shared graph-backed memory system. Before acting,
        call graph_context_search() to retrieve relevant context. After completing
        important tasks, call graph_memory_write() to record observations."
        """
        
    def prefetch(
        self,
        query: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_items: int = 20,
    ) -> Dict[str, Any]:
        """
        Prefetch context before a turn.
        
        Called before Hermes starts processing a turn.
        
        Args:
            query: Context query (from task title or explicit request)
            task_id: Current task ID
            session_id: Current Hermes session ID
            max_items: Max context references to return
            
        Returns:
            Bounded context packet with citations
        """
        # Calls POST /api/brain/context
        # Returns context with source IDs and confidence scores
        
    def write_memory(
        self,
        kind: str,
        text: str,
        source: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Write a memory item to graph.
        
        Args:
            kind: memory type (observation, outcome, decision, etc.)
            text: memory content
            source: "hermes" or specific agent identity
            session_id: Hermes session ID
            task_id: Related task ID
            metadata: additional context
            
        Returns:
            Memory item ID
        """
        # Calls POST /api/memory/items
        # Returns ID of created MemoryItem
        
    def signal_event(
        self,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        paperclip_issue_id: Optional[str] = None,
    ) -> str:
        """
        Record a signal event (execution milestone).
        
        Args:
            event_id: Unique event identifier
            event_type: "task_started", "code_executed", "decision_made", etc.
            payload: Event details
            session_id: Session context
            paperclip_issue_id: If tied to Paperclip dispatch
            
        Returns:
            Signal event ID
        """
        # Calls POST /api/brain/signals
        
    def update_session(
        self,
        session_id: str,
        paperclip_agent_id: Optional[str] = None,
        hermes_session_id: Optional[str] = None,
        agent_identity: Optional[str] = None,
        device_id: Optional[str] = None,
        platform: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Update AgentSession record in graph.
        
        Called when session starts or resumes.
        """
        # Calls POST /api/sessions/{session_id}
```

---

## 3. Integration with Hermes Lifecycle

### Hermes Hooks (Architecture)

```python
# In Hermes agent configuration:
from assistx.agents.hermes_memory_provider import HermesMemoryProvider

memory_provider = HermesMemoryProvider()

# Register with Hermes
agent_config = {
    "memory": {
        "provider": memory_provider,
        "prefetch_on_turn": True,
        "sync_on_completion": True,
    },
    "tools": {
        "enabled": ["graph_context_search", "graph_memory_write"],
    },
}
```

### Turn Lifecycle

1. **Session Init**: Hermes spawns or resumes session
   - Calls `update_session()` with session IDs
   - Stores session state in AssistX

2. **Turn Start**: Before processing user input
   - Calls `prefetch()` with task context
   - Injects context into system message
   - Makes context searchable via tools

3. **Turn Execution**: Hermes processes with context
   - Can call `graph_context_search()` tool to refine context
   - Can call `graph_memory_write()` to record observations mid-turn
   - Executes code, calls APIs, etc.

4. **Turn Completion**: After producing response
   - Calls `sync_turn()` to write turn summary
   - Calls `signal_event()` for important milestones
   - Updates session state

5. **Delegation**: If Hermes delegates to child agent
   - Calls `on_delegation()` with child task
   - Waits for child completion
   - Calls `write_memory()` with child outcome

### Tool Definitions

**graph_context_search**

```python
{
    "type": "function",
    "function": {
        "name": "graph_context_search",
        "description": "Search shared graph memory for relevant context",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What context do you need?"
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by source: 'memory', 'knowledge', 'orchestration'"
                }
            },
            "required": ["query"]
        }
    }
}
```

**graph_memory_write**

```python
{
    "type": "function",
    "function": {
        "name": "graph_memory_write",
        "description": "Write an observation or outcome to shared memory",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["observation", "outcome", "decision", "fact", "note"]
                },
                "text": {
                    "type": "string",
                    "description": "What to remember"
                },
                "metadata": {
                    "type": "object",
                    "description": "Additional context (optional)"
                }
            },
            "required": ["kind", "text"]
        }
    }
}
```

---

## 4. Configuration and Setup

### Environment Variables

```bash
# For Hermes to use memory provider
ASSISTX_API_URL=http://localhost:8000
ASSISTX_API_TOKEN=<optional-token>

# Hermes session persistence
HERMES_SESSION_PERSIST_DIR=./hermes-sessions
HERMES_MEMORY_PROVIDER_ENABLED=true
```

### Hermes Configuration File

```yaml
# hermes-config.yaml (or env-based config)
agents:
  local:
    model: gpt-4
    provider: openai
    memory:
      provider: "assistx"  # Use AssistX memory provider
      url: "http://assistx-api:8000"
    tools:
      enabled:
        - graph_context_search
        - graph_memory_write
        - python_exec
        - web_search
      disabled:
        - dangerous_file_delete  # Safety rules
    execution:
      persist_sessions: true
      session_dir: ./hermes-sessions
      checkpoint_on_tool_call: true
```

---

## 5. Usage Examples

### Example 1: Task with Context Prefetch

```python
# Hermes starts a task for "Analyze recent support tickets"
session = hermes.Session(
    task_id="task-support-analysis",
    task_title="Analyze recent support tickets",
)

# Memory provider prefetches context
context = memory_provider.prefetch(
    query="recent support tickets and customer preferences",
    task_id="task-support-analysis",
    session_id=session.id,
)

# Context is injected into system prompt:
# "Available context: 5 recent tickets from Jane, 3 from Bob. 
#  Customer Jane prefers email responses."

# Hermes processes task with context
response = session.run(
    "Prioritize the tickets and draft responses."
)

# After completion, write outcome
memory_provider.write_memory(
    kind="outcome",
    text="Successfully prioritized 8 support tickets and drafted responses. Jane's 5 tickets were HIGH priority.",
    source="hermes",
    session_id=session.id,
    task_id="task-support-analysis",
)
```

### Example 2: Mid-Turn Context Search

```python
# During execution, Hermes needs more specific context
def my_tool_handler():
    # Hermes calls graph_context_search tool
    context = memory_provider.prefetch(
        query="What preferences does customer Jane have?",
        session_id=session.id,
    )
    
    # Uses context to refine response
    # "Jane prefers evening calls, email summaries"
    return "Calling Jane at preferred time..."
```

### Example 3: Delegation with Memory

```python
# Hermes delegates to a subagent
child_task = hermes.create_subtask(
    title="Generate expense report",
    context=context,
)

child_session = hermes.Session(
    task_id=child_task.id,
    parent_session_id=session.id,
)

child_result = child_session.run()

# After child completes, write observation
memory_provider.write_memory(
    kind="observation",
    text=f"Child agent generated expense report. Status: {child_result['status']}",
    source="hermes",
    session_id=session.id,
    metadata={
        "child_task_id": child_task.id,
        "child_result": child_result,
    },
)
```

---

## 6. Testing

### Unit Tests

**File**: `tests/test_hermes_memory_provider.py`

```python
def test_memory_provider_prefetch(monkeypatch):
    """Test context prefetch from AssistX"""
    from assistx.agents.hermes_memory_provider import HermesMemoryProvider
    
    mp = HermesMemoryProvider(
        base_url="http://localhost:8000",
        auth=("neo4j", "livelongandprosper"),
    )
    
    # Mock AssistX response
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {
            "context_packet": {
                "id": "packet-1",
                "query": "test",
                "references": [
                    {"node": {"id": "mem-1", "text": "Customer prefers email"}}
                ],
            }
        }
        
        context = mp.prefetch("customer preferences")
        assert "references" in context
        assert len(context["references"]) > 0

def test_memory_provider_write_memory(monkeypatch):
    """Test writing memory to AssistX"""
    mp = HermesMemoryProvider(
        base_url="http://localhost:8000",
        auth=("neo4j", "livelongandprosper"),
    )
    
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {
            "memory_item_id": "mem-new-1"
        }
        
        mem_id = mp.write_memory(
            kind="observation",
            text="Task completed successfully",
            source="hermes",
        )
        assert mem_id == "mem-new-1"
```

### Integration Tests

1. **Full Turn**: Hermes starts, fetches context, executes, writes memory
2. **Session Resume**: Hermes resumes existing session, context is updated
3. **Cross-Agent**: Multiple Hermes sessions, each writes memory, all can read

---

## 7. Monitoring and Debugging

### Logs to Monitor

```
[hermes] Session started: session-xyz
[memory_provider] Prefetch called: query="task context", sources=['memory', 'orchestration']
[memory_provider] Context packet created: packet-abc (5 references)
[hermes] Tool called: graph_context_search
[memory_provider] Memory write: kind=observation, source=hermes
[hermes] Session completed: session-xyz
```

### Debug Endpoints

```bash
# Check if AssistX memory provider is configured
curl http://hermes:8001/debug/memory-provider

# Check recent memory writes
curl http://localhost:8000/api/memory?limit=20

# Check session state
curl http://localhost:8000/api/sessions/{session_id}
```

---

## 8. Troubleshooting

### Provider not connecting to AssistX

**Symptom**: `requests.ConnectionError` from memory provider

**Causes**:
- ASSISTX_API_URL not set or wrong
- AssistX API not running
- Network/DNS issue

**Fix**:
```bash
# Test connectivity
curl -I http://localhost:8000/health

# Check env var
echo $ASSISTX_API_URL
```

### Context not available in Hermes

**Symptom**: Hermes can't access context, gets empty references

**Causes**:
- No memory items or tasks matching query
- Context sources not enabled in config
- Prefetch not called before turn

**Fix**:
- Check /api/memory and /api/tasks for existing data
- Enable all sources in config: `include_sources: ['memory', 'knowledge', 'orchestration']`
- Verify prefetch() called in turn lifecycle

### Memory writes failing

**Symptom**: `graph_memory_write()` tool fails or returns error

**Causes**:
- Required fields missing (kind, text)
- Session ID invalid
- Task ID not found

**Fix**:
- Verify kind is one of: observation, outcome, decision, fact, note
- Check session_id and task_id exist
- Review /api/sessions and /api/tasks

---

## 9. Future Enhancements

- [ ] Streaming context updates (WebSocket)
- [ ] Memory caching at agent level
- [ ] Context ranking by relevance score
- [ ] Automatic memory cleanup (stale item removal)
- [ ] Multi-language memory summarization
- [ ] Memory search suggestions (auto-completion)
- [ ] Cross-agent memory sharing policies
- [ ] Memory encryption at rest
