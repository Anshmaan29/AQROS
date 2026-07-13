"""Subprocess-based implementation of the ``GitInfoProvider`` port.

Shells out to ``git rev-parse HEAD`` for the current commit SHA recorded in
``Reproducibility_Metadata`` (Requirement 12.3). Degrades to ``None``
(commit unavailable) rather than raising when there is no ``.git`` directory
— a copy of ``aqros_dataset_builder.adapters.git_info``'s pattern.
"""

from __future__ import annotations

import asyncio

from aqros_training_pipeline.domain.ports import GitInfoProvider


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
