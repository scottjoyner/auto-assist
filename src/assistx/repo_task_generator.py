"""Repository-based task generator for the fleet.

Scans git repositories and creates LLM tasks for:
- Code analysis & understanding
- Documentation generation
- Refactoring suggestions
- Test generation
- Security auditing
- Dependency analysis
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

from .neo4j_client import Neo4jClient

# Task generation configuration
REPO_ROOTS = [
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_auto-assist",
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_auto-router",
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_hermes-paperclip-adapter",
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_neo4j-mcp-server",
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_paperclip",
    "/media/scott/SSD_4TB/hermes-home/home_scott_git_tts",
    "/media/scott/SSD_4TB/hermes-home/.hermes/sessions",
]

REPO_TASK_INTERVAL = int(os.getenv("REPO_TASK_INTERVAL", "300"))  # 5 minutes
MAX_TASKS_PER_CYCLE = int(os.getenv("REPO_MAX_TASKS_PER_CYCLE", "20"))
TASK_KINDS = [
    "code_analysis",
    "doc_generation",
    "refactor_suggestion",
    "test_generation",
    "security_audit",
    "dependency_audit",
    "performance_review",
    "architecture_review",
]

# File patterns to include/exclude
INCLUDE_PATTERNS = [
    "*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.go", "*.rs", "*.java",
    "*.cpp", "*.h", "*.c", "*.cs", "*.php", "*.rb", "*.swift", "*.kt",
    "*.yaml", "*.yml", "*.json", "*.toml", "*.ini", "*.cfg",
    "Dockerfile*", "docker-compose*", "*.sh", "*.bash", "*.zsh",
    "*.sql", "*.md", "*.rst", "*.txt",
]
EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    "dist", "build", ".next", ".cache", "target", "bin", "obj",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "htmlcov", "coverage", ".coverage", "*.egg-info",
}

# Prompt templates for different task kinds
PROMPTS = {
    "code_analysis": """Analyze the following code file and provide a comprehensive analysis:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Provide:
1. **Purpose & Functionality** - What does this code do?
2. **Architecture** - Key classes, functions, data flow
3. **Complexity** - Cyclomatic complexity, nesting depth
4. **Potential Issues** - Bugs, edge cases, error handling gaps
5. **Dependencies** - External/internal imports, coupling
6. **Recommendations** - Improvements, modernization opportunities""",

    "doc_generation": """Generate comprehensive documentation for the following code:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Create:
1. **Module/Class Docstring** - Purpose, usage, examples
2. **Function/Method Docs** - Args, returns, raises, examples
3. **Type Hints** - Add missing type annotations
4. **README Section** - How this fits in the project
5. **Architecture Notes** - Design patterns, data flow""",

    "refactor_suggestion": """Review the following code for refactoring opportunities:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Identify:
1. **Code Smells** - Duplication, long functions, god classes, feature envy
2. **SOLID Violations** - Single responsibility, open/closed, etc.
3. **Performance** - Inefficient algorithms, N+1 queries, memory leaks
4. **Modernization** - Outdated patterns, deprecated APIs, type hints
5. **Concrete Refactors** - Extract method/class, compose over inherit, etc.
6. **Risk Assessment** - Breaking changes, test coverage needed""",

    "test_generation": """Generate comprehensive unit tests for the following code:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Create tests covering:
1. **Happy Path** - Normal usage scenarios
2. **Edge Cases** - Empty inputs, boundaries, None/null handling
3. **Error Conditions** - Exceptions, invalid inputs, network failures
4. **Property-Based** - Invariants that should always hold
5. **Integration Points** - Mock external dependencies
6. **Fixtures** - Reusable test data setup

Use pytest (Python), jest (JS/TS), or appropriate framework.""",

    "security_audit": """Perform a security audit of the following code:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Check for:
1. **Injection Risks** - SQL, command, LDAP, template injection
2. **Authentication/Authorization** - Weak auth, missing checks, privilege escalation
3. **Data Exposure** - Secrets in code, PII logging, insecure serialization
4. **Crypto Issues** - Weak algorithms, hardcoded keys, improper IVs
5. **Input Validation** - Missing sanitization, path traversal, SSRF
6. **Dependencies** - Known vulnerabilities, supply chain risks
7. **Configuration** - Debug mode, default secrets, exposed ports

Rate each finding: CRITICAL / HIGH / MEDIUM / LOW / INFO""",

    "dependency_audit": """Analyze dependencies and imports in this file:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Identify:
1. **External Dependencies** - Package names, versions if specified
2. **Internal Coupling** - Cross-module imports, circular dependencies
3. **Unused Imports** - Dead code, unnecessary dependencies
4. **Version Risks** - Unpinned, deprecated, or vulnerable packages
5. **License Compatibility** - GPL, MIT, Apache conflicts
6. **Supply Chain** - Transitive dependency risks
7. **Modern Alternatives** - Better maintained libraries""",

    "performance_review": """Review": """Performance review of the following code:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Analyze:
1. **Algorithmic Complexity** - Big-O time/space for key operations
2. **I/O Patterns** - Blocking calls, N+1 queries, missing batching
3. **Memory Usage** - Object allocation, leaks, cache efficiency
4. **Concurrency** - Thread safety, lock contention, async opportunities
5. **Database** - Query plans, indexing, connection pooling
6. **Caching** - Missing caches, invalidation strategies
7. **Profiling Targets** - Functions to benchmark""",

    "architecture_review": """Architectural review of this module in context:

**File:** {rel_path}
**Repository:** {repo_name}
**Language:** {language}

```{language}
{code}
```

Evaluate:
1. **Module Boundaries** - Cohesion, coupling, separation of concerns
2. **Design Patterns** - Used correctly? Over-engineered? Missing?
3. **Scalability** - Horizontal scaling, state management, statelessness
4. **Observability** - Logging, metrics, tracing, health checks
5. **Configuration** - Externalized config, feature flags, secrets
6. **Error Handling** - Circuit breakers, retries, fallback, degradation
7. **Deployment** - Container readiness, migrations, rollback strategy""",
}

# Git repo cache
_repo_cache: Dict[str, Dict] = {}
_cache_lock = threading.Lock()


def _run(cmd: List[str], cwd: str, timeout: int = 30) -> Optional[str]:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_repo_info(repo_path: Path) -> Optional[Dict]:
    """Get git repo metadata."""
    if not (repo_path / ".git").exists():
        return None

    with _cache_lock:
        if str(repo_path) in _repo_cache:
            return _repo_cache[str(repo_path)]

    name = repo_path.name
    url = _run(["git", "config", "--get", "remote.origin.url"], str(repo_path))
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], str(repo_path))
    commit = _run(["git", "rev-parse", "--short", "HEAD"], str(repo_path))
    changed = _run(["git", "status", "--porcelain"], str(repo_path))

    info = {
        "path": str(repo_path),
        "name": name,
        "url": url,
        "branch": branch,
        "commit": commit,
        "has_changes": bool(changed),
    }

    with _cache_lock:
        _repo_cache[str(repo_path)] = info
    return info


def _find_code_files(repo_path: Path) -> List[Path]:
    """Find all code files in a repository."""
    files = []
    for pattern in INCLUDE_PATTERNS:
        files.extend(repo_path.rglob(pattern))
    
    # Filter out excluded directories
    filtered = []
    for f in files:
        if any(excl in f.parts for excl in EXCLUDE_DIRS):
            continue
        if f.is_file() and f.stat().st_size < 500_000:  # Skip huge files
            filtered.append(f)
    return filtered


def _detect_language(file_path: Path) -> str:
    """Detect programming language from file extension."""
    suffix = file_path.suffix.lower()
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".go": "go", ".rs": "rust",
        ".java": "java", ".cpp": "cpp", ".h": "cpp", ".c": "c",
        ".cs": "csharp", ".php": "php", ".rb": "ruby", ".swift": "swift",
        ".kt": "kotlin", ".yaml": "yaml", ".yml": "yaml",
        ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
        ".sql": "sql", ".md": "markdown", ".rst": "rst", ".txt": "text",
    }
    return lang_map.get(suffix, "text")


def _create_task_payload(kind: str, repo_info: Dict, file_path: Path, code: str) -> Dict:
    """Create task payload for a repo analysis task."""
    rel_path = file_path.relative_to(repo_info["path"])
    language = _detect_language(file_path)
    
    prompt = PROMPTS.get(kind, PROMPTS["code_analysis"]).format(
        rel_path=rel_path,
        repo_name=repo_info["name"],
        language=language,
        code=code[:8000],  # Limit code size
    )
    
    return {
        "kind": f"repo_{kind}",
        "repo": repo_info["name"],
        "repo_path": repo_info["path"],
        "file": str(rel_path),
        "language": language,
        "prompt": prompt,
        "model": "",  # Let fleet executor pick best model
        "harvester": f"repo_task_generator:{kind}",
    }


def _create_tasks_for_repo(repo_path: Path, max_per_repo: int = 5) -> List[Dict]:
    """Generate tasks for a single repository."""
    repo_info = _get_repo_info(repo_path)
    if not repo_info:
        return []
    
    code_files = _find_code_files(repo_path)
    if not code_files:
        return []
    
    tasks = []
    import random
    random.shuffle(code_files)
    
    for file_path in code_files[:max_per_repo]:
        try:
            code = file_path.read_text(encoding="utf-8", errors="ignore")
            if len(code.strip()) < 50:  # Skip tiny files
                continue
            
            # Pick a random task kind
            kind = random.choice(TASK_KINDS)
            payload = _create_task_payload(kind, repo_info, file_path, code)
            
            tasks.append({
                "title": f"[{kind}] {repo_info['name']}: {file_path.relative_to(repo_path)}",
                "kind": f"repo_{kind}",
                "status": "READY",
                "required_capabilities": ["llm"],
                "payload": payload,
            })
        except Exception as e:
            print(f"Error creating task for {file_path}: {e}")
    
    return tasks


def create_repo_tasks(max_total: int = 20) -> List[Dict]:
    """Create tasks across all configured repositories."""
    all_tasks = []
    per_repo = max(1, max_total // len(REPO_ROOTS))
    
    for root_str in REPO_ROOTS:
        root = Path(root_str)
        if not root.exists():
            continue
        
        if root.name == "sessions":
            # Sessions dir - scan subdirectories as repos
            for subdir in root.iterdir():
                if subdir.is_dir() and (subdir / ".git").exists():
                    all_tasks.extend(_create_tasks_for_repo(subdir, per_repo))
        else:
            all_tasks.extend(_create_tasks_for_repo(root, per_repo))
        
        if len(all_tasks) >= max_total:
            break
    
    return all_tasks[:max_total]


# Neo4j task creation
def _persist_tasks(tasks: List[Dict]) -> int:
    """Persist tasks to Neo4j."""
    if not tasks:
        return 0
    
    neo = Neo4jClient()
    try:
        with neo._session() as s:
            created = 0
            for task in tasks:
                task_id = task.get("id") or f"repo-{int(time.time()*1000)}-{created}"
                # Check if already exists
                existing = s.run(
                    "MATCH (t:Task {id:$id}) RETURN t.id", {"id": task_id}
                ).single()
                if existing:
                    continue
                
                s.run(
                    """
                    CREATE (t:Task {
                        id: $id,
                        title: $title,
                        kind: $kind,
                        status: $status,
                        required_capabilities: $caps,
                        payload_json: $payload,
                        created_at: datetime(),
                        created_at_ts: timestamp(),
                        updated_at: datetime(),
                        updated_at_ts: timestamp()
                    })
                    """,
                    {
                        "id": task_id,
                        "title": task["title"],
                        "kind": task["kind"],
                        "status": task["status"],
                        "caps": json.dumps(task["required_capabilities"]),
                        "payload": json.dumps(task["payload"]),
                    },
                )
                created += 1
            return created
    finally:
        neo.close()


# Background daemon
_repo_task_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _repo_task_loop() -> None:
    """Background loop to generate repo tasks."""
    time.sleep(60)  # Initial delay
    
    while not _stop_event.is_set():
        try:
            # Check current backlog
            neo = Neo4jClient()
            try:
                with neo._session() as s:
                    result = s.run(
                        "MATCH (t:Task) WHERE t.status='READY' AND t.kind STARTS WITH 'repo_' RETURN count(t) as cnt"
                    ).single()
                    backlog = result["cnt"] if result else 0
            finally:
                neo.close()
            
            # Only generate if backlog is low
            if backlog < 50:
                tasks = create_repo_tasks(max_total=MAX_TASKS_PER_CYCLE)
                if tasks:
                    created = _persist_tasks(tasks)
                    print(f"[repo_task_generator] Created {created} repo tasks (backlog was {backlog})")
            
        except Exception as e:
            print(f"[repo_task_generator] Error: {e}")
        
        # Wait for next cycle
        for _ in range(REPO_TASK_INTERVAL):
            if _stop_event.is_set():
                break
            time.sleep(1)


def start_repo_task_generator() -> None:
    """Start the repo task generator daemon thread."""
    global _repo_task_thread
    if _repo_task_thread and _repo_task_thread.is_alive():
        return
    
    _stop_event.clear()
    _repo_task_thread = threading.Thread(target=_repo_task_loop, name="repo-task-generator", daemon=True)
    _repo_task_thread.start()
    print("[repo_task_generator] Daemon started")


def stop_repo_task_generator() -> None:
    """Stop the repo task generator."""
    _stop_event.set()
    if _repo_task_thread:
        _repo_task_thread.join(timeout=5)


# Manual trigger for testing
def trigger_repo_tasks_once(max_tasks: int = 20) -> int:
    """Manually trigger one cycle of repo task generation."""
    tasks = create_repo_tasks(max_total=max_tasks)
    return _persist_tasks(tasks)