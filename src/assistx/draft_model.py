from __future__ import annotations

import os
from typing import Any, Dict

import requests


class DraftModelUnavailable(RuntimeError):
    pass


def generate_draft(prompt: str, max_tokens: int = 256) -> Dict[str, Any]:
    """Generate advisory draft text through an explicitly configured endpoint."""
    base_url = os.getenv("DRAFT_MODEL_BASE_URL", "").rstrip("/")
    model = os.getenv("DRAFT_MODEL_NAME", "").strip()
    if not base_url or not model:
        raise DraftModelUnavailable("Draft model endpoint is not configured")

    api_key = os.getenv("DRAFT_MODEL_API_KEY", "not-needed")
    timeout_s = int(os.getenv("DRAFT_MODEL_TIMEOUT_S", "20"))
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Produce a concise draft for operator review. "
                    "Do not claim to have taken actions or accessed systems."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise DraftModelUnavailable(f"Draft model request failed: {exc}") from exc
    if not isinstance(content, str) or not content.strip():
        raise DraftModelUnavailable("Draft model returned no text")
    return {"text": content, "model": model, "source": "configured_draft_endpoint"}
