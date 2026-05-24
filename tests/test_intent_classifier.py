from __future__ import annotations

from assistx.intent_classifier import (
    classify_text,
    CLASSIFICATION_MEMORY,
    CLASSIFICATION_TASK,
    CLASSIFICATION_CANCEL,
    CLASSIFICATION_QUERY,
    CLASSIFICATION_UNKNOWN,
)


def test_classify_cancel():
    assert classify_text("cancel that") == CLASSIFICATION_CANCEL
    assert classify_text("stop what you're doing") == CLASSIFICATION_CANCEL
    assert classify_text("never mind, forget it") == CLASSIFICATION_CANCEL
    assert classify_text("scratch that idea") == CLASSIFICATION_CANCEL
    assert classify_text("please don't do that") == CLASSIFICATION_CANCEL


def test_classify_query():
    assert classify_text("what time is it?") == CLASSIFICATION_QUERY
    assert classify_text("how does this work?") == CLASSIFICATION_QUERY
    assert classify_text("who is the president?") == CLASSIFICATION_QUERY
    assert classify_text("when is the meeting?") == CLASSIFICATION_QUERY
    assert classify_text("where is the file?") == CLASSIFICATION_QUERY
    assert classify_text("why is the server down?") == CLASSIFICATION_QUERY


def test_classify_memory():
    assert classify_text("remember that I like coffee") == CLASSIFICATION_MEMORY
    assert classify_text("I prefer dark mode") == CLASSIFICATION_MEMORY
    assert classify_text("my favorite color is blue") == CLASSIFICATION_MEMORY
    assert classify_text("I think we should use postgres") == CLASSIFICATION_MEMORY
    assert classify_text("just a quick note") == CLASSIFICATION_MEMORY


def test_classify_task():
    assert classify_text("please check the server status") == CLASSIFICATION_TASK
    assert classify_text("create a new user account") == CLASSIFICATION_TASK
    assert classify_text("find the latest sales report") == CLASSIFICATION_TASK
    assert classify_text("update the database schema") == CLASSIFICATION_TASK
    assert classify_text("send an email to the team") == CLASSIFICATION_TASK
    assert classify_text("schedule a meeting for tomorrow") == CLASSIFICATION_TASK
    assert classify_text("make a backup of the system") == CLASSIFICATION_TASK
    assert classify_text("fix the login bug") == CLASSIFICATION_TASK
    assert classify_text("I need you to review the PR") == CLASSIFICATION_TASK
    assert classify_text("write a script to list all users") == CLASSIFICATION_TASK


def test_classify_short_text_as_memory():
    assert classify_text("blue") == CLASSIFICATION_MEMORY
    assert classify_text("nice idea") == CLASSIFICATION_MEMORY
    assert classify_text("remember this") == CLASSIFICATION_MEMORY


def test_classify_empty():
    assert classify_text("") == CLASSIFICATION_UNKNOWN
    assert classify_text("   ") == CLASSIFICATION_UNKNOWN
    assert classify_text(None) == CLASSIFICATION_UNKNOWN  # type: ignore


def test_classify_unknown():
    assert classify_text("hello there") in (CLASSIFICATION_MEMORY, CLASSIFICATION_UNKNOWN)


def test_question_mark_triggers_query():
    assert classify_text("can you write a script for me?") == CLASSIFICATION_QUERY
    assert classify_text("will you check the logs?") == CLASSIFICATION_QUERY
    assert classify_text("can you help with this?") == CLASSIFICATION_QUERY


def test_query_takes_precedence_over_task():
    assert classify_text("how do I fix the server?") == CLASSIFICATION_QUERY
