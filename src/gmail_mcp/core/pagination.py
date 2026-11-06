"""Server-side Gmail query helpers and pagination collector (`nextPageToken`)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


def build_gmail_search_query(
    raw_query: str | None = None,
    *,
    sender: str | None = None,
    recipient: str | None = None,
    subject: str | None = None,
    label: str | None = None,
    after: str | None = None,
    before: str | None = None,
    newer_than: str | None = None,
    older_than: str | None = None,
    has_attachment: bool | None = None,
    filename: str | None = None,
    is_unread: bool | None = None,
    in_folder: str | None = None,
    rfc822msgid: str | None = None,
) -> str:
    """
    Combine structured search filters with native Gmail search query syntax.
    Never unnecessarily rewrites valid raw Gmail query syntax.
    """
    parts: list[str] = []
    if raw_query and raw_query.strip():
        parts.append(raw_query.strip())
    if sender:
        parts.append(f"from:{sender.strip()}")
    if recipient:
        parts.append(f"to:{recipient.strip()}")
    if subject:
        escaped = subject.replace('"', '\\"')
        parts.append(f'subject:"{escaped}"' if " " in subject else f"subject:{escaped}")
    if label:
        parts.append(f"label:{label.strip()}")
    if after:
        parts.append(f"after:{after.strip()}")
    if before:
        parts.append(f"before:{before.strip()}")
    if newer_than:
        parts.append(f"newer_than:{newer_than.strip()}")
    if older_than:
        parts.append(f"older_than:{older_than.strip()}")
    if has_attachment is True:
        parts.append("has:attachment")
    if filename:
        parts.append(f"filename:{filename.strip()}")
    if is_unread is True:
        parts.append("is:unread")
    elif is_unread is False:
        parts.append("is:read")
    if in_folder:
        parts.append(f"in:{in_folder.strip()}")
    if rfc822msgid:
        clean_id = rfc822msgid.strip().strip("<>")
        parts.append(f"rfc822msgid:{clean_id}")

    return " ".join(parts).strip()


async def collect_paginated_items(
    fetch_page_fn: Callable[[str | None, int], Awaitable[dict[str, Any]]],
    *,
    items_key: str,
    page_size: int = 500,
    max_results: int | None = None,
    start_page_token: str | None = None,
    auto_paginate_all: bool = False,
) -> dict[str, Any]:
    """
    Collect paginated items from a Gmail list endpoint (`messages.list`, `threads.list`, `drafts.list`)
    without silently truncating results.
    """
    effective_page_size = max(1, min(500, page_size))
    if max_results is not None and not auto_paginate_all:
        effective_page_size = max(1, min(500, max_results))

    collected: list[dict[str, Any]] = []
    next_token: str | None = start_page_token
    estimated_total = 0
    pages_fetched = 0

    while True:
        remaining = (max_results - len(collected)) if max_results is not None else effective_page_size
        req_size = max(1, min(500, remaining)) if max_results is not None else effective_page_size

        page_resp = await fetch_page_fn(next_token, req_size)
        pages_fetched += 1

        if "resultSizeEstimate" in page_resp:
            estimated_total = max(estimated_total, int(page_resp.get("resultSizeEstimate") or 0))

        batch = page_resp.get(items_key) or []
        collected.extend(batch)
        next_token = page_resp.get("nextPageToken")

        if max_results is not None and len(collected) >= max_results:
            collected = collected[:max_results]
            break

        if not next_token:
            break

        if not auto_paginate_all and max_results is None:
            # Single page requested unless auto_paginate_all or max_results > len(collected)
            break

    has_more = bool(next_token is not None)
    return {
        "results": collected,
        "returned_count": len(collected),
        "estimated_total": max(estimated_total, len(collected)),
        "has_more": has_more,
        "next_page_token": next_token,
        "pages_fetched": pages_fetched,
    }
