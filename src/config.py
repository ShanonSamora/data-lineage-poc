"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings


class RepoSource:
    """A single repository to include in multi-repo lineage analysis."""

    def __init__(self, name: str, path: str, repo_type: str = "auto"):
        self.name = name
        self.path = path
        self.repo_type = repo_type  # "auto" | "sql" | "python" | "adf"

    def __repr__(self) -> str:
        return f"RepoSource(name={self.name!r}, path={self.path!r}, type={self.repo_type!r})"


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    repo_path: str = "./sample_repo"

    # Multi-repo: JSON array of {"name": "...", "path": "...", "type": "auto"}
    # Example: REPOS='[{"name":"warehouse","path":"./repos/warehouse"},{"name":"etl","path":"./repos/etl"}]'
    # When unset, auto-detection looks for the POC's three sibling sample dirs.
    repos: str = ""

    # Convention for the POC: three sibling directories simulating three real repos.
    _AUTO_REPOS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("sql", "./sample_repo_sql", "sql"),
        ("python", "./sample_repo_python", "python"),
        ("adf", "./sample_repo_adf", "adf"),
    )

    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_repo_sources(self) -> list[RepoSource]:
        """
        Parse the repos config into a list of RepoSource objects.

        Resolution order:
        1. Explicit ``repos`` JSON env var (production / custom setups).
        2. Auto-detected POC sibling dirs ``sample_repo_sql``, ``sample_repo_python``,
           ``sample_repo_adf`` — at least one must exist.
        3. Legacy fallback to a single repo at ``repo_path``.
        """
        if self.repos.strip():
            raw = json.loads(self.repos)
            return [
                RepoSource(
                    name=r["name"],
                    path=r["path"],
                    repo_type=r.get("type", "auto"),
                )
                for r in raw
            ]

        auto = [
            RepoSource(name=name, path=path, repo_type=rtype)
            for name, path, rtype in self._AUTO_REPOS
            if Path(path).is_dir()
        ]
        if auto:
            return auto

        return [RepoSource(name="default", path=self.repo_path)]


settings = Settings()
