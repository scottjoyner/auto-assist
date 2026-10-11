"""Offline evidence boundaries for Neo4j isolation and Tailnet identity spoof.

No network access or Docker execution here. A mocked synthetic allowlisted
identity illustrates why trusting a header without provenance can be unsafe,
but DOES NOT impersonate a real user or test a production login.
"""
from __future__ import annotations

import os

import pytest
from fastapi import Request
from trace_bolt_boundary_placeholder import not_used  # replaced in build
