"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import json

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
    repos: str = ""

    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def get_repo_sources(self) -> list[RepoSource]:
        """
        Parse the repos config into a list of RepoSource objects.

        Falls back to a single repo from ``repo_path`` if ``repos`` is empty.
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
        # Fallback: single-repo mode
        return [RepoSource(name="default", path=self.repo_path)]


settings = Settings()
