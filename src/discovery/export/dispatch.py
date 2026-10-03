"""Ask GitHub Actions to start pipeline.yml (architecture Section 21.4)."""

from __future__ import annotations

import httpx


class DispatchError(RuntimeError):
    """The workflow could not be started."""


def dispatch_workflow(
    *,
    token: str,
    repository: str,
    workflow: str = "pipeline.yml",
    ref: str = "main",
    inputs: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> None:
    """POST a workflow_dispatch. The token needs actions:write and nothing else."""
    if not token.strip():
        raise DispatchError(
            "GITHUB_TOKEN is not set. Add a fine-grained token with actions:write only."
        )
    if "/" not in repository:
        raise DispatchError("GITHUB_REPOSITORY must look like owner/name.")
    owner, repo = repository.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    body: dict[str, object] = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.post(url, headers=headers, json=body)
    finally:
        if owns_client:
            client.close()
    if response.status_code == 204:
        return
    if response.status_code in (401, 403):
        raise DispatchError(
            "GitHub refused the token. Use a fine-grained token with actions:write on this "
            "repository, and no other permissions."
        )
    if response.status_code == 404:
        raise DispatchError(
            f"Could not find {workflow} on {repository} at ref {ref}. "
            "Check GITHUB_REPOSITORY and that the workflow file is on that branch."
        )
    raise DispatchError(f"GitHub returned HTTP {response.status_code} starting {workflow}.")
