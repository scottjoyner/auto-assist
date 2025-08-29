import json
from typing import Dict, Any, List, Tuple
from .llm import tool_json

SYSTEM_CYPHER = """You are a senior software engineer who writes Cypher for Neo4j.
Given (1) a user question and (2) the database schema (node labels, properties, relationship types),
produce a single Cypher query that returns the minimal fields needed to answer the question.
- Prefer explicit labels and relationship types.
- Always alias columns with simple snake_case.
- Use LIMIT reasonably if data can explode.
Respond as JSON: {"cypher": "...", "notes":"brief rationale"}."""

SYSTEM_REPAIR = """You repair Cypher queries for Neo4j based on error messages.
Given the prior query, error string, and schema, return a corrected Cypher.
Respond JSON: {"cypher": "...", "fix":"what changed and why"}."""

def draft_cypher(question: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    msg = [
        {"role": "system", "content": SYSTEM_CYPHER},
        {"role": "user", "content": json.dumps({"question": question, "schema": schema}, ensure_ascii=False)}
    ]
    return tool_json(msg)

def repair_cypher(prev_cypher: str, error: str, schema: Dict[str, Any], question: str) -> Dict[str, Any]:
    msg = [
        {"role": "system", "content": SYSTEM_REPAIR},
        {"role": "user", "content": json.dumps({
            "question": question, "schema": schema,
            "previous_cypher": prev_cypher, "error": error
        }, ensure_ascii=False)}
    ]
    return tool_json(msg)
