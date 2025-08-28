
from __future__ import annotations
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Any, Literal, Dict

class Utterance(BaseModel):
    id: str
    conversation_id: str
    speaker: str | None = None
    text: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

class TaskModel(BaseModel):
    title: str
    description: str = ""
    priority: Literal["LOW","MEDIUM","HIGH"] = "MEDIUM"
    due: Optional[str] = None
    confidence: float | None = None

class Summary(BaseModel):
    id: str
    conversation_id: str
    text: str
    bullets: List[str] = []
    quality_score: float | None = None

class Task(BaseModel):
    id: str
    conversation_id: str
    title: str
    description: str
    priority: Literal["LOW","MEDIUM","HIGH"] = "MEDIUM"
    due: Optional[str] = None
    status: Literal["REVIEW","READY","RUNNING","DONE","FAILED"] = "REVIEW"
    confidence: float | None = None

class ToolCall(BaseModel):
    id: str
    run_id: str
    tool: str
    input_json: Dict[str, Any]
    output_json: Dict[str, Any] | None = None
    ok: bool = False

class AgentRun(BaseModel):
    id: str
    task_id: str
    agent: str
    model: str
    status: Literal["RUNNING","DONE","FAILED"] = "RUNNING"
    manifest_json: Dict[str, Any] = Field(default_factory=dict)

class Artifact(BaseModel):
    id: str
    run_id: str
    kind: str
    path: str
    sha256: str | None = None

class ExtractedTasks(BaseModel):
    summary: str
    bullets: List[str]
    tasks: List[TaskModel]
