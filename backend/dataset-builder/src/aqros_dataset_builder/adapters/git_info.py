"""Subprocess-based implementation of the ``GitInfoProvider`` port.

Shells out to ``git rev-parse HEAD`` to find the current commit SHA for the
dataset manifest. Behind the real ``GitInfoProvider`` interface so a
container image built without a ``.git`` directory (a common, deliberate
choice for slim production images) degrades to "commit unavailable" rather
than crashing the build pipeline — a dataset manifest is still valid and
useful without a git commit; CLAUDE.md's "fail-open on the alpha path"
applies here (this is research tooling, not the money path).
"""

from __future__ import annotations

import asyncio

from aqros_dataset_builder.domain.ports import GitInfoProvider


class SubprocessGitInfoProvider(GitInfoProvider):
    """Looks up the current git commit SHA by shelling out to git."""

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root

    async def get_commit_sha(self) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "HEAD",
                cwd=self._repo_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await process.communicate()
            if process.returncode != 0:
                return None
            sha = stdout.decode().strip()
            return sha or None
        except (OSError, FileNotFoundError):
            return None
