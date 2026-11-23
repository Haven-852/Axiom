"""Read-only access to one project repository."""

import subprocess
from pathlib import Path

from ase.errors import ProjectReadError

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_dsa", "credentials.json", "secrets.json"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".secret"}


class ProjectReader:
    def __init__(
        self,
        project_id: str,
        root: Path,
        *,
        max_depth: int = 4,
        max_bytes: int = 100_000,
        max_files: int = 100,
        max_results: int = 20,
    ) -> None:
        self.project_id = project_id
        resolved = root.resolve()
        if not resolved.is_dir():
            raise ProjectReadError("project_root_missing", "project repository does not exist")
        self.root = resolved
        self.max_depth = max_depth
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.max_results = max_results

    async def get_project_tree(self, project_id: str, max_depth: int | None = None) -> dict:
        self._authorize(project_id)
        depth_limit = self.max_depth if max_depth is None else min(max_depth, self.max_depth)
        files: list[str] = []
        truncated = False
        for path in self._walk(depth_limit):
            if len(files) >= self.max_files:
                truncated = True
                break
            files.append(path.relative_to(self.root).as_posix())
        return {"files": files, "truncated": truncated}

    async def read_file(self, project_id: str, relative_path: str, max_bytes: int | None = None) -> dict:
        self._authorize(project_id)
        path = self._resolve(relative_path)
        self._reject_sensitive(path)
        if not path.is_file():
            raise ProjectReadError("file_not_found", "file does not exist")
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        size = path.stat().st_size
        if size > limit:
            raise ProjectReadError("file_too_large", "file exceeds the read limit")
        before = self._stamp(path)
        content = path.read_text(encoding="utf-8", errors="replace")
        after = self._stamp(path)
        lines = content.splitlines()
        return {
            "path": path.relative_to(self.root).as_posix(),
            "content": content,
            "start_line": 1 if lines else 0,
            "end_line": len(lines),
            "truncated": False,
            "stale": before != after,
        }

    async def search_code(self, project_id: str, query: str, file_globs: list[str] | None = None) -> dict:
        self._authorize(project_id)
        needle = query.strip()
        if not needle:
            raise ProjectReadError("empty_query", "search query is empty")
        matches: list[dict] = []
        truncated = False
        for path in self._walk(self.max_depth):
            relative = path.relative_to(self.root).as_posix()
            if file_globs and not any(path.match(pattern) for pattern in file_globs):
                continue
            if self._sensitive(path):
                continue
            if path.stat().st_size > self.max_bytes:
                continue
            if "\0" in path.read_bytes()[:1024].decode("utf-8", errors="ignore"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = needle.lower()
            if lowered not in text.lower() and lowered not in path.name.lower():
                continue
            line_no = 1
            excerpt = path.name
            for number, line in enumerate(text.splitlines(), start=1):
                if lowered in line.lower():
                    line_no = number
                    excerpt = line.strip()[:180]
                    break
            matches.append({"path": relative, "line": line_no, "excerpt": excerpt})
            if len(matches) >= self.max_results:
                truncated = True
                break
        return {"query": needle, "matches": matches, "truncated": truncated}

    async def get_git_status(self, project_id: str) -> dict:
        self._authorize(project_id)
        revision = self._git(["rev-parse", "HEAD"])
        status = self._git(["status", "--porcelain"])
        if revision is None:
            return {"available": False, "revision": None, "changed_files": [], "error": "git revision is unavailable"}
        return {"available": True, "revision": revision.strip(), "changed_files": [line for line in status.splitlines() if line][:50]}

    async def get_git_diff_summary(self, project_id: str) -> dict:
        self._authorize(project_id)
        summary = self._git(["diff", "--stat", "HEAD"])
        if summary is None:
            return {"available": False, "summary": "", "error": "git diff is unavailable"}
        return {"available": True, "summary": summary[:4000]}

    def _authorize(self, project_id: str) -> None:
        if project_id != self.project_id:
            raise ProjectReadError("project_forbidden", "reader is bound to a different project")

    def _resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or ".." in raw.parts or relative_path.startswith(("/", "\\")):
            raise ProjectReadError("path_escape", "path escapes the repository")
        candidate = (self.root / raw).resolve()
        if not candidate.is_relative_to(self.root):
            raise ProjectReadError("path_escape", "path escapes the repository")
        return candidate

    def _walk(self, max_depth: int):
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if any(part in _SKIP_DIRS for part in relative.parts):
                continue
            if len(relative.parts) - 1 > max_depth:
                continue
            if self._sensitive(path):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            yield path

    def _sensitive(self, path: Path) -> bool:
        name = path.name.lower()
        return name in _SENSITIVE_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES or name.startswith(".env")

    def _reject_sensitive(self, path: Path) -> None:
        if self._sensitive(path):
            raise ProjectReadError("sensitive_file", "sensitive files cannot be read")

    def _stamp(self, path: Path) -> int:
        return path.stat().st_mtime_ns

    def _git(self, args: list[str]) -> str | None:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout
