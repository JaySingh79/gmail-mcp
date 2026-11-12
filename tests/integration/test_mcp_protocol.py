"""Layer 3 MCP Protocol Tests: Server startup, tool discovery, schemas, invocation, concurrency, and stdio transport."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.server import create_gmail_mcp_server
from tests.conftest import MockGmailBackend

MANDATORY_TOOLS = {
    # A. Account & diagnostics
    "gmail_get_profile",
    "gmail_auth_status",
    "gmail_get_runtime_limits",
    "gmail_health_check",
    "gmail_get_mailbox_stats",
    # B. Search & retrieval
    "gmail_search_messages",
    "gmail_search_threads",
    "gmail_get_message",
    "gmail_get_thread",
    "gmail_list_unread",
    "gmail_get_attachment",
    "gmail_export_messages",
    # C. Drafts
    "gmail_create_draft",
    "gmail_get_draft",
    "gmail_list_drafts",
    "gmail_duplicate_draft",
    "gmail_update_draft",
    "gmail_delete_draft",
    "gmail_bulk_create_drafts",
    "gmail_bulk_attach_to_drafts",
    # D. Sending & replies
    "gmail_send_draft",
    "gmail_send_email",
    "gmail_send_reply",
    "gmail_send_reply_all",
    "gmail_forward_message",
    "gmail_prepare_bulk_send",
    "gmail_execute_bulk_send",
    "gmail_bulk_send_drafts",
    "gmail_get_bulk_status",
    "gmail_prepare_bulk_reply",
    "gmail_execute_bulk_reply",
    # E. Templates
    "gmail_create_template",
    "gmail_get_template",
    "gmail_list_templates",
    "gmail_update_template",
    "gmail_delete_template",
    "gmail_preview_template",
    # F. Attachments
    "gmail_list_attachments",
    "gmail_attach_files_to_message",
    # G. Labels
    "gmail_list_labels",
    "gmail_create_label",
    "gmail_update_label",
    "gmail_delete_label",
    "gmail_apply_label",
    "gmail_remove_label",
    "gmail_bulk_label_messages",
    # H. Mailbox organization & settings
    "gmail_modify_messages",
    "gmail_get_mailbox_settings",
    "gmail_update_vacation_settings",
    # I. Filters
    "gmail_list_filters",
    "gmail_get_filter",
    "gmail_create_filter",
    "gmail_delete_filter",
    # J. Scheduling & sync
    "gmail_schedule_send",
    "gmail_list_scheduled_sends",
    "gmail_cancel_scheduled_send",
    "gmail_process_due_scheduled_sends",
    "gmail_start_watch",
    "gmail_sync_history",
    "gmail_stop_watch",
}


def _extract_tool_dict(call_result: Any) -> dict[str, Any]:
    """Extract structured dictionary from FastMCP `call_tool` return value."""
    if isinstance(call_result, dict):
        return call_result
    if isinstance(call_result, tuple) and len(call_result) == 2 and isinstance(call_result[1], dict):
        return call_result[1]
    if isinstance(call_result, (list, tuple)) and call_result:
        first = call_result[0]
        text = getattr(first, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"Unexpected tool call result format: {type(call_result)} -> {call_result}")


@pytest.mark.asyncio()
async def test_mcp_tool_discovery_schemas_and_annotations(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify all 42 mandatory tools are registered with valid descriptions, input schemas, and annotations."""
    mcp_server, _ = create_gmail_mcp_server(client=gmail_client, start_scheduler=False)
    tools = await mcp_server.list_tools()
    tool_map = {t.name: t for t in tools}

    missing = MANDATORY_TOOLS - set(tool_map.keys())
    assert not missing, f"Missing mandatory MCP tools: {missing}"
    assert len(tools) == len(MANDATORY_TOOLS)

    for name, tool in tool_map.items():
        assert tool.description and len(tool.description) > 15, f"Tool {name} lacks a clear description"
        assert isinstance(tool.inputSchema, dict), f"Tool {name} lacks a valid inputSchema"
        assert tool.inputSchema.get("type") == "object"
        assert tool.annotations is not None, f"Tool {name} lacks ToolAnnotations"


@pytest.mark.asyncio()
async def test_mcp_tool_invocation_error_propagation_and_concurrency(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """Invoke tools through FastMCP protocol layer, verify structured JSON outputs, error propagation, and concurrency."""
    mock_backend.seed_message(msg_id="mcp_m1", sender="alice@example.com", subject="MCP Protocol Test")
    mcp_server, _ = create_gmail_mcp_server(client=gmail_client, start_scheduler=False)

    # 1. Invoke gmail_get_profile
    prof_raw = await mcp_server.call_tool("gmail_get_profile", {})
    prof = _extract_tool_dict(prof_raw)
    assert prof["success"] is True
    assert prof["email_address"] == "testuser@gmail.com"

    # 2. Invoke gmail_create_template and gmail_preview_template via MCP
    tpl_raw = await mcp_server.call_tool(
        "gmail_create_template",
        {
            "name": "MCP Welcome",
            "subject": "Welcome {{user}}",
            "body_text": "Hi {{user}}, welcome aboard!",
        },
    )
    tpl = _extract_tool_dict(tpl_raw)
    assert tpl["success"] is True

    prev_raw = await mcp_server.call_tool(
        "gmail_preview_template",
        {"template_id": "mcp_welcome", "variables": {"user": "Binit"}},
    )
    prev = _extract_tool_dict(prev_raw)
    assert prev["success"] is True
    assert prev["rendered_subject"] == "Welcome Binit"

    # 3. Error propagation for missing template variable
    err_raw = await mcp_server.call_tool(
        "gmail_preview_template",
        {"template_id": "mcp_welcome", "variables": {}},
    )
    err = _extract_tool_dict(err_raw)
    assert err["success"] is False
    assert err["error_code"] == "MISSING_TEMPLATE_VARIABLE"

    # 4. Concurrent tool calls
    concurrent_calls = [
        mcp_server.call_tool("gmail_search_messages", {"query": "from:alice@example.com"}),
        mcp_server.call_tool("gmail_get_runtime_limits", {}),
        mcp_server.call_tool("gmail_list_labels", {}),
        mcp_server.call_tool("gmail_list_templates", {}),
    ]
    results = await asyncio.gather(*concurrent_calls)
    parsed_results = [_extract_tool_dict(r) for r in results]
    assert all(r["success"] is True for r in parsed_results)


@pytest.mark.asyncio()
async def test_mcp_stdio_subprocess_handshake_and_clean_shutdown(
    temp_workspace: dict[str, Any],
) -> None:
    """Spawn the actual MCP server over STDIO transport using `stdio_client`, verify handshake, tool listing, and call."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gmail_mcp.server"],
        env={
            "GMAIL_DB_PATH": str(temp_workspace["root"] / "stdio_test.db"),
            "GMAIL_TOKEN_PATH": str(temp_workspace["root"] / "nonexistent_token.json"),
            "GMAIL_CREDENTIALS_PATH": str(temp_workspace["root"] / "credentials.json"),
            "GMAIL_ALLOWED_DIRS": str(temp_workspace["attachments"]),
        },
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "gmail-automation-mcp"

            tools_resp = await session.list_tools()
            tool_names = {t.name for t in tools_resp.tools}
            assert MANDATORY_TOOLS.issubset(tool_names)

            # Call gmail_auth_status and gmail_get_runtime_limits over real STDIO JSON-RPC
            status_call = await session.call_tool("gmail_auth_status", {"verify_connectivity": False})
            assert not status_call.isError
            payload = json.loads(status_call.content[0].text)  # type: ignore[union-attr]
            assert payload["success"] is True
            assert payload["status_code"] == "TOKEN_MISSING"

            limits_call = await session.call_tool("gmail_get_runtime_limits", {})
            assert not limits_call.isError
            limits_payload = json.loads(limits_call.content[0].text)  # type: ignore[union-attr]
            assert limits_payload["success"] is True
            assert limits_payload["runtime_limits"]["cloud_project"]["project_number"] in ("542762227233", "187614359153")
