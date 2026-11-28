"""Unit tests for non_blocking_practice pacing, domain clustering, and 400/day cap."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gmail_mcp.config import GmailConfig
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.pacing import NonBlockingPacer, extract_domain, interleave_by_domain
from gmail_mcp.tools.sending import (
    gmail_bulk_send_drafts,
    gmail_execute_bulk_reply,
    gmail_execute_bulk_send,
    gmail_prepare_bulk_reply,
    gmail_prepare_bulk_send,
)


def _make_virtual_clock(start: float = 1000000.0):
    now = [start]

    def time_fn() -> float:
        return now[0]

    async def sleep_fn(sec: float) -> None:
        now[0] += max(0.0, float(sec))

    return now, time_fn, sleep_fn


def test_daily_cap_increased_to_400() -> None:
    cfg = GmailConfig()
    assert cfg.daily_send_hard_cap == 400
    assert cfg.daily_send_soft_cap == 350
    assert cfg.non_blocking_max_per_hour == 40
    assert cfg.non_blocking_max_per_domain_per_hour == 2
    assert cfg.non_blocking_domain_window_seconds == 3600.0


def test_extract_domain_and_interleave() -> None:
    assert extract_domain("Alice <alice@Acme.COM>") == "acme.com"
    assert extract_domain("bob@ltimindtree.com") == "ltimindtree.com"
    assert extract_domain(["c@corp.io", "d@other.io"]) == "corp.io"

    items = [
        ("1", "a.com"),
        ("2", "a.com"),
        ("3", "a.com"),
        ("4", "b.com"),
        ("5", "b.com"),
        ("6", "c.com"),
    ]
    ordered = interleave_by_domain(items, lambda x: x[1])
    assert [x[1] for x in ordered] == ["a.com", "b.com", "c.com", "a.com", "b.com", "a.com"]


@pytest.mark.asyncio()
async def test_pacer_enforces_40_per_hour_and_2_per_domain_per_hour_without_dropping() -> None:
    """Verify 10 emails to one domain + 45 total across domains all send while respecting 40/hr and 2/hr/domain."""
    _, time_fn, sleep_fn = _make_virtual_clock(start=10000.0)
    pacer = NonBlockingPacer(
        max_per_hour=40,
        max_per_domain_per_hour=2,
        window_seconds=3600.0,
        min_jitter_seconds=5.0,
        max_jitter_seconds=5.0,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
    )

    # 10 emails to same_corp.com + 35 emails to unique domains = 45 total
    items = [f"u{i}@samecorp.com" for i in range(10)] + [f"r{i}@co{i}.com" for i in range(35)]
    sent_log: list[tuple[str, float]] = []

    async def _send(item: str) -> None:
        sent_log.append((item, time_fn()))

    await pacer.run_paced_queue(items, domain_fn=extract_domain, send_fn=_send)

    # All 45 must be sent (none dropped)
    assert len(sent_log) == 45

    # Check global rolling 3600s window never exceeds 40
    all_ts = [ts for _, ts in sent_log]
    for i, ts in enumerate(all_ts):
        in_window = [t for t in all_ts if ts <= t < ts + 3600.0]
        assert len(in_window) <= 40, f"Exceeded 40/hr at index {i}: {len(in_window)}"

    # Check samecorp.com rolling 3600s window never exceeds 2
    corp_ts = [ts for addr, ts in sent_log if addr.endswith("@samecorp.com")]
    assert len(corp_ts) == 10
    for ts in corp_ts:
        in_window = [t for t in corp_ts if ts <= t < ts + 3600.0]
        assert len(in_window) <= 2, f"Exceeded 2/hr for samecorp.com: {in_window}"


@pytest.mark.asyncio()
async def test_bulk_send_tools_support_non_blocking_practice(
    gmail_client: GmailClientWrapper,
    mock_backend: Any,
) -> None:
    """Verify gmail_bulk_send_drafts, gmail_execute_bulk_send, and gmail_execute_bulk_reply enforce and toggle non_blocking_practice."""
    _, time_fn, sleep_fn = _make_virtual_clock(start=50000.0)

    # 1. gmail_bulk_send_drafts with non_blocking_practice=True
    orig_execute = gmail_client.execute_api
    draft_recipients = {
        "d1": "a1@alpha.com",
        "d2": "a2@alpha.com",
        "d3": "a3@alpha.com",
        "d4": "b1@beta.com",
    }
    sent_order: list[tuple[str, float]] = []

    async def _mock_execute(endpoint: str, params: dict[str, Any] | None = None, **kwargs: Any):
        if endpoint == "drafts.get" and params and params.get("id") in draft_recipients:
            did = params["id"]
            return {
                "id": did,
                "message": {"payload": {"headers": [{"name": "To", "value": draft_recipients[did]}]}},
            }, {"quota_estimate": 5}
        if endpoint == "drafts.send" and params:
            did = params["body"]["id"]
            sent_order.append((draft_recipients.get(did, did), time_fn()))
            return {"id": f"msg_{did}", "threadId": f"th_{did}"}, {"quota_estimate": 100}
        return await orig_execute(endpoint, params, **kwargs)

    gmail_client.execute_api = _mock_execute  # type: ignore[method-assign]

    pacer = NonBlockingPacer(
        max_per_hour=40,
        max_per_domain_per_hour=2,
        window_seconds=3600.0,
        min_jitter_seconds=1.0,
        max_jitter_seconds=1.0,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
    )
    res_drafts = await gmail_bulk_send_drafts(
        gmail_client,
        ["d1", "d2", "d3", "d4"],
        non_blocking_practice=True,
        _pacer=pacer,
    )
    assert res_drafts["success"] is True
    assert res_drafts["sent"] == 4
    assert res_drafts["non_blocking_practice"] is True
    # Interleaved: beta.com runs before 3rd alpha.com, and 3rd alpha.com waits >=3600s
    alpha_ts = [ts for addr, ts in sent_order if addr.endswith("@alpha.com")]
    assert alpha_ts[2] - alpha_ts[0] >= 3600.0

    # 2. gmail_execute_bulk_send with non_blocking_practice=False
    gmail_client.execute_api = orig_execute  # type: ignore[method-assign]
    prep = await gmail_prepare_bulk_send(
        gmail_client,
        recipients=[
            {"to": "x1@gamma.com", "name": "X1"},
            {"to": "x2@gamma.com", "name": "X2"},
        ],
        subject="Hello {name}",
        body_text="Hi {name}",
        dry_run=False,
    )
    assert prep["success"] is True
    exec_res = await gmail_execute_bulk_send(
        gmail_client,
        plan_id=prep["plan_id"],
        confirmation_phrase=prep["required_confirmation_phrase"],
        non_blocking_practice=False,
    )
    assert exec_res["success"] is True
    assert exec_res["sent"] == 2

    # 3. gmail_execute_bulk_reply with non_blocking_practice=True
    mock_backend.seed_message(
        thread_id="thread_1",
        sender="lead@delta.com",
        to="testuser@gmail.com",
        subject="Intro",
    )
    prep_reply = await gmail_prepare_bulk_reply(
        gmail_client,
        replies=[
            {"thread_id": "thread_1", "body_text": "Follow up 1"},
        ],
        dry_run=False,
    )
    assert prep_reply["success"] is True
    pacer_reply = NonBlockingPacer(
        min_jitter_seconds=0.0,
        max_jitter_seconds=0.0,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
    )
    reply_res = await gmail_execute_bulk_reply(
        gmail_client,
        plan_id=prep_reply["plan_id"],
        confirmation_phrase=prep_reply["required_confirmation_phrase"],
        non_blocking_practice=True,
        _pacer=pacer_reply,
    )
    assert reply_res["success"] is True
    assert reply_res["sent"] == 1
    assert reply_res["non_blocking_practice"] is True
