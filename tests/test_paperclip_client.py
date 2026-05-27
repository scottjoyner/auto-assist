from assistx.paperclip_client import PaperclipClient


def test_resolve_agent_id_name_alias(monkeypatch):
    client = PaperclipClient(
        api_url="http://paperclip.local/api",
        api_token="test-token",
        workspace_id="company-1",
    )
    monkeypatch.setattr(
        client,
        "list_agents",
        lambda company_id=None: [
            {"id": "cfecc886-befc-4fa9-a91e-3e9a707b4a4f", "name": "hermes local"},
            {"id": "11111111-1111-4111-8111-111111111111", "name": "other-agent"},
        ],
    )

    assert (
        client.resolve_agent_id("hermes-local")
        == "cfecc886-befc-4fa9-a91e-3e9a707b4a4f"
    )
    assert (
        client.resolve_agent_id("hermes local")
        == "cfecc886-befc-4fa9-a91e-3e9a707b4a4f"
    )
    assert (
        client.resolve_agent_id("cfecc886-befc-4fa9-a91e-3e9a707b4a4f")
        == "cfecc886-befc-4fa9-a91e-3e9a707b4a4f"
    )


def test_create_issue_uses_resolved_assignee(monkeypatch):
    client = PaperclipClient(
        api_url="http://paperclip.local/api",
        api_token="test-token",
        workspace_id="company-1",
    )

    monkeypatch.setattr(
        client,
        "list_agents",
        lambda company_id=None: [
            {"id": "cfecc886-befc-4fa9-a91e-3e9a707b4a4f", "name": "hermes local"},
        ],
    )

    sent = {}

    def fake_request(method, path, **kwargs):
        sent["method"] = method
        sent["path"] = path
        sent["json"] = kwargs.get("json")
        return {"id": "issue-1"}

    monkeypatch.setattr(client, "_request", fake_request)

    issue_id = client.create_issue(
        title="Task",
        description="desc",
        task_id="task-1",
        context_packet_id="ctx-1",
        capabilities=["terminal"],
        priority="normal",
        assignee_id="hermes-local",
    )

    assert issue_id == "issue-1"
    assert sent["method"] == "POST"
    assert sent["json"]["assigneeAgentId"] == "cfecc886-befc-4fa9-a91e-3e9a707b4a4f"


def test_list_agents_accepts_list_shape(monkeypatch):
    client = PaperclipClient(
        api_url="http://paperclip.local/api",
        api_token="test-token",
        workspace_id="company-1",
    )

    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: [
            {"id": "a1", "name": "agent one"},
            {"id": "a2", "name": "agent two"},
        ],
    )
    agents = client.list_agents()
    assert len(agents) == 2
    assert agents[0]["id"] == "a1"


def test_get_run_output_accepts_paperclip_content_shape(monkeypatch):
    client = PaperclipClient(
        api_url="http://paperclip.local/api",
        api_token="test-token",
        workspace_id="company-1",
    )
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, **kwargs: {"content": "run log content"},
    )

    assert client.get_run_output("run-1") == "run log content"
