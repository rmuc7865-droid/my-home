import os
from typing import Any

import httpx


JIRA_PROJECT_KEY = "HM"
JIRA_ISSUE_TYPE = "Bug"


class JiraConfigurationError(RuntimeError):
    pass


def _jira_config() -> tuple[str, str, str]:
    jira_url = os.environ.get("JIRA_URL", "").strip().rstrip("/")
    email = os.environ.get("JIRA_EMAIL", "").strip()
    api_token = os.environ.get("JIRA_API_TOKEN", "").strip()

    if not jira_url or not email or not api_token:
        raise JiraConfigurationError(
            "Jira is not configured. "
            "JIRA_URL, JIRA_EMAIL and JIRA_API_TOKEN are required."
        )

    return jira_url, email, api_token


def _auth() -> tuple[str, str]:
    _, email, api_token = _jira_config()
    return email, api_token


def _jira_url(path: str) -> str:
    jira_url, _, _ = _jira_config()
    return f"{jira_url}{path}"


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Do not expose Jira response bodies because they may contain
        # internal Jira data. The HTTP status is sufficient for logs/UI.
        raise RuntimeError(
            f"Jira request failed with HTTP {response.status_code}"
        ) from exc


def _description_adf(description: str) -> dict[str, Any]:
    text = str(description or "").strip()

    if not text:
        text = "No description provided."

    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ],
            }
        ],
    }


async def list_issues(
    *,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    max_results = max(
        1,
        min(int(max_results), 100),
    )

    # Jira Cloud enhanced JQL search.
    params = {
        "jql": (
            f'project = "{JIRA_PROJECT_KEY}" '
            "ORDER BY updated DESC"
        ),
        "maxResults": max_results,
        "fields": (
            "summary,status,issuetype,"
            "created,updated"
        ),
    }

    async with httpx.AsyncClient(
        auth=_auth(),
        timeout=30.0,
    ) as client:
        response = await client.get(
            _jira_url(
                "/rest/api/3/search/jql"
            ),
            params=params,
            headers={
                "Accept": "application/json",
            },
        )

    _raise_for_status(response)

    payload = response.json()
    issues = []

    for issue in payload.get("issues", []):
        fields = issue.get("fields") or {}

        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}

        issues.append(
            {
                "Key": str(
                    issue.get("key") or ""
                ),
                "Summary": str(
                    fields.get("summary") or ""
                ),
                "Status": str(
                    status.get("name") or ""
                ),
                "IssueType": str(
                    issue_type.get("name") or ""
                ),
                "Created": fields.get("created"),
                "Updated": fields.get("updated"),
            }
        )

    return issues


async def create_issue(
    summary: str,
    description: str,
) -> dict[str, Any]:
    summary = str(summary or "").strip()

    if not summary:
        raise ValueError(
            "Issue summary is required."
        )

    payload = {
        "fields": {
            "project": {
                "key": JIRA_PROJECT_KEY,
            },
            "summary": summary,
            "issuetype": {
                "name": JIRA_ISSUE_TYPE,
            },
            "description": _description_adf(
                description
            ),
        }
    }

    async with httpx.AsyncClient(
        auth=_auth(),
        timeout=30.0,
    ) as client:
        response = await client.post(
            _jira_url(
                "/rest/api/3/issue"
            ),
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    _raise_for_status(response)

    result = response.json()

    return {
        "key": str(
            result.get("key") or ""
        ),
        "id": str(
            result.get("id") or ""
        ),
        "self": str(
            result.get("self") or ""
        ),
    }


async def attach_file(
    issue_key: str,
    *,
    filename: str,
    content: bytes,
    content_type: str | None = None,
) -> dict[str, Any]:
    issue_key = str(issue_key or "").strip()
    filename = str(filename or "").strip()

    if not issue_key:
        raise ValueError(
            "Jira issue key is required."
        )

    if not filename:
        raise ValueError(
            "Attachment filename is required."
        )

    if not content:
        raise ValueError(
            "Attachment is empty."
        )

    files = {
        "file": (
            filename,
            content,
            content_type
            or "application/octet-stream",
        )
    }

    async with httpx.AsyncClient(
        auth=_auth(),
        timeout=60.0,
    ) as client:
        response = await client.post(
            _jira_url(
                f"/rest/api/3/issue/{issue_key}/attachments"
            ),
            files=files,
            headers={
                "Accept": "application/json",
                "X-Atlassian-Token": "no-check",
            },
        )

    _raise_for_status(response)

    payload = response.json()

    attachment = (
        payload[0]
        if isinstance(payload, list)
        and payload
        else {}
    )

    return {
        "id": str(
            attachment.get("id") or ""
        ),
        "filename": str(
            attachment.get("filename")
            or filename
        ),
    }
