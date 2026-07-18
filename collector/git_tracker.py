from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil


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

    @staticmethod
    def _cwd_for_pid(pid: int | None) -> Path | None:
        """Return the working directory of a process, or None if unavailable."""
        if not pid:
            return None
        try:
            return Path(psutil.Process(pid).cwd())
        except (psutil.Error, OSError):
            return None

    @staticmethod
    def _extract_editor_project_name(window_title: str) -> str | None:
        """Extract the project/folder name from an IDE window title.

        Handles titles like:
          'file.py - projectname - Visual Studio Code'
          '● file.py - projectname - Visual Studio Code'
          'projectname - Visual Studio Code'
        """
        # Strip leading modified indicator (●, *, etc.)
        title = window_title.strip().lstrip("●* ").strip()
        parts = [p.strip() for p in title.split(" - ")]
        # Drop the last part if it names the IDE
        ide_suffixes = {
            "visual studio code",
            "code",
            "pycharm",
            "intellij idea",
            "sublime text",
            "vim",
            "nvim",
        }
        if parts and parts[-1].lower() in ide_suffixes:
            parts = parts[:-1]
        if not parts:
            return None
        # The last remaining segment is the project/folder name
        return parts[-1]

    @staticmethod
    def _find_project_cwd_by_name(project_name: str) -> Path | None:
        """Search all Code.exe (and similar) child processes for one whose cwd
        directory name matches *project_name* (case-insensitive)."""
        for proc in psutil.process_iter(["name", "cwd"]):
            try:
                name = proc.info["name"] or ""
                if "code" not in name.lower():
                    continue
                cwd = proc.info["cwd"]
                if cwd and Path(cwd).name.lower() == project_name.lower():
                    return Path(cwd)
            except (psutil.Error, OSError):
                pass
        return None

    def _resolve_cwd(
        self,
        pid: int | None,
        app_name: str | None,
        window_title: str | None,
    ) -> Path:
        """Best-effort resolution of the project directory for a given window."""
        # For VS Code (and similar Electron editors), the foreground window process
        # always has cwd = the VS Code installation directory, not the project.
        # Rely on the window title to extract the project name, then find a
        # language-server child process that has the real project cwd.
        is_editor = app_name and any(
            kw in app_name.lower()
            for kw in ("code", "pycharm", "idea", "sublime", "vim")
        )
        if is_editor and window_title:
            project_name = self._extract_editor_project_name(window_title)
            if project_name:
                cwd = self._find_project_cwd_by_name(project_name)
                if cwd:
                    return cwd

        # Fallback: use the process's own cwd (works for terminal apps, etc.)
        return self._cwd_for_pid(pid) or self.cwd

    def get_activity(
        self,
        pid: int | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> GitActivity:
        """Detect current git repo and activity for the given window."""
        cwd = self._resolve_cwd(pid, app_name, window_title)
        repo_path = self._find_repo_root(cwd)
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

    def _find_repo_root(self, cwd: Path) -> Path | None:
        """Find the root of a git repository by walking up from cwd."""
        current = cwd
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
