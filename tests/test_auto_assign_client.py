"""Unit tests for the auto-assign notification client."""

from __future__ import annotations

from unittest.mock import ANY, patch

import pytest
import requests

from assistx.auto_assign_client import notify_task_created
from assistx.config import settings


def test_notify_task_created_happy_path() -> None:
    with patch.object(settings, "auto_assign_base_url", "http://assign:8090"):
        with patch("assistx.auto_assign_client.requests.post") as mock_post:
            mock_post.return_value.__enter__.return_value.status_code = 200
            mock_post.return_value.__enter__.return_value.raise_for_status.return_value = None

            result = notify_task_created(
                task_id="task_abc123",
                correlation_id="corr_xyz",
                title="test task",
                required_capabilities=["terminal"],
                kind="sophia_voice",
            )

            assert result is True
            mock_post.assert_called_once_with(
                "http://assign:8090/api/events",
                json=ANY,
                timeout=10,
            )
            call_body = mock_post.call_args[1]["json"]
            assert call_body["event_type"] == "task.candidate.created"
            assert call_body["subject"]["task_id"] == "task_abc123"
            assert call_body["payload"]["task_id"] == "task_abc123"
            assert call_body["payload"]["title"] == "test task"
            assert call_body["payload"]["kind"] == "sophia_voice"
            assert call_body["payload"]["required_capabilities"] == ["terminal"]
            assert call_body["correlation_id"] == "corr_xyz"


def test_notify_task_created_no_base_url() -> None:
    with patch.object(settings, "auto_assign_base_url", ""):
        result = notify_task_created(task_id="t1", correlation_id="c1")
        assert result is False


def test_notify_task_created_http_error() -> None:
    with patch.object(settings, "auto_assign_base_url", "http://assign:8090"):
        with patch("assistx.auto_assign_client.requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("connection refused")

            result = notify_task_created(task_id="t1", title="fail")
            assert result is False


def test_notify_task_created_generates_correlation_id() -> None:
    with patch.object(settings, "auto_assign_base_url", "http://assign:8090"):
        with patch("assistx.auto_assign_client.requests.post") as mock_post:
            mock_post.return_value.__enter__.return_value.status_code = 200
            mock_post.return_value.__enter__.return_value.raise_for_status.return_value = None

            notify_task_created(task_id="t2")
            call_body = mock_post.call_args[1]["json"]
            assert call_body["correlation_id"] is not None
            assert isinstance(call_body["correlation_id"], str)
            assert len(call_body["correlation_id"]) > 0


def test_notify_task_created_defaults() -> None:
    with patch.object(settings, "auto_assign_base_url", "http://assign:8090"):
        with patch("assistx.auto_assign_client.requests.post") as mock_post:
            mock_post.return_value.__enter__.return_value.status_code = 200
            mock_post.return_value.__enter__.return_value.raise_for_status.return_value = None

            notify_task_created(task_id="t3")
            call_body = mock_post.call_args[1]["json"]
            assert call_body["source_repo"] == "auto-assist"
            assert call_body["source_service"] == "assistx"
            assert call_body["idempotency_key"].startswith("assistx-task-created:t3")
            assert call_body["links"]["task_id"] == "t3"
