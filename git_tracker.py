from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitActivity:
    repo_name: str | None
    branch: str | None
    commit_hash: str | None
    modified_files: list[str]


class GitTracker:
    """Tracks active Git repository, branch, and recent commits."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd or Path.cwd()

    def get_activity(self) -> GitActivity:
        """Detect current git repo and activity."""
        repo_path = self._find_repo_root()
        if not repo_path:
            return GitActivity(None, None, None, [])

        repo_name = self._get_repo_name(repo_path)
        branch = self._get_current_branch(repo_path)
        commit_hash = self._get_head_commit(repo_path)
        modified_files = self._get_modified_files(repo_path)

        return GitActivity(
            repo_name=repo_name,
            branch=branch,
            commit_hash=commit_hash,
            modified_files=modified_files,
        )

    def _find_repo_root(self) -> Path | None:
        """Find the root of a git repository by walking up from cwd."""
        current = self.cwd
        for _ in range(20):  # limit to 20 levels up
            if (current / ".git").exists():
                return current
            if current.parent == current:  # reached filesystem root
                return None
            current = current.parent
        return None

    def _run_git(self, args: list[str], repo_path: Path) -> str | None:
        """Run a git command and return output."""
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path)] + args,
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass
        return None

    def _get_repo_name(self, repo_path: Path) -> str:
        """Get the repository name from the directory."""
        return repo_path.name

    def _get_current_branch(self, repo_path: Path) -> str | None:
        """Get the current branch name."""
        output = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
        return output if output and output != "HEAD" else None

    def _get_head_commit(self, repo_path: Path) -> str | None:
        """Get the hash of the current HEAD commit."""
        output = self._run_git(["rev-parse", "HEAD"], repo_path)
        return output[:7] if output else None  # short hash

    def _get_modified_files(self, repo_path: Path) -> list[str]:
        """Get list of modified files in the working directory."""
        output = self._run_git(["status", "--porcelain"], repo_path)
        if not output:
            return []

        files = []
        for line in output.split("\n"):
            if not line.strip():
                continue
            status, filepath = line[:2], line[3:]
            if status != "??":  # ignore untracked files
                files.append(filepath)
        return files[:5]  # limit to top 5 modified files
