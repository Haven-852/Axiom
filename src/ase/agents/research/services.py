"""Deterministic project evidence. The model does not invent the stack."""

import hashlib
import json
import re
from pathlib import Path

from ase.agents.research.reader import ProjectReader
from ase.agents.research.schemas import ProjectAnalysis
from ase.errors import ProjectReadError

_MANIFESTS = ("pyproject.toml", "requirements.txt", "package.json", "go.mod", "Cargo.toml", "pom.xml")
_LANGUAGES = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java"}
_FRAMEWORKS = (
    ("fastapi", "FastAPI"),
    ("flask", "Flask"),
    ("django", "Django"),
    ("pytest", "pytest"),
    ("sqlalchemy", "SQLAlchemy"),
    ("langgraph", "LangGraph"),
)
_DATABASES = (("psycopg", "PostgreSQL"), ("sqlite", "SQLite"), ("pymongo", "MongoDB"), ("sqlalchemy", "SQLAlchemy"))
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


def research_key(workflow_id: str, requirement: str, revision: str, version: str, options: dict) -> str:
    blob = json.dumps(
        {"requirement": requirement, "revision": revision, "version": version, "options": options},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(blob.encode()).hexdigest()[:32]
    return f"{workflow_id}:research:{digest}"


def research_options(payload: dict) -> dict:
    return {
        "include_tests": bool(payload.get("include_tests", True)),
        "include_dependencies": bool(payload.get("include_dependencies", True)),
        "max_files": _clamp(payload.get("max_files", 100), 1, 100),
        "max_context_chars": _clamp(payload.get("max_context_chars", 50_000), 1000, 50_000),
    }


async def collect_evidence(reader: ProjectReader, project_id: str, requirement: str, options: dict) -> dict:
    tree = await reader.get_project_tree(project_id, max_depth=4)
    git = await reader.get_git_status(project_id)
    revision = git.get("revision") or "unversioned"
    limitations: list[str] = []
    if tree["truncated"]:
        limitations.append("目录遍历达到文件数量上限，项目分析不是全仓扫描。")
    if not git.get("available"):
        limitations.append("无法读取 Git 修订，研究指纹使用 unversioned。")
    files = [item for item in tree["files"] if options["include_tests"] or not _is_test(item)]
    languages = _languages(files)
    manifests = await _manifests(reader, project_id, files, options["include_dependencies"])
    frameworks = _markers(manifests, _FRAMEWORKS)
    databases = _markers(manifests, _DATABASES)
    if any(path.endswith("pytest.ini") or path.endswith("conftest.py") for path in files) and "pytest" not in frameworks:
        frameworks.append("pytest")
    entries = [path for path in files if Path(path).name in {"main.py", "app.py", "__main__.py"}][:12]
    queries = _queries(requirement)
    matches: list[dict] = []
    search_truncated = False
    for query in queries:
        found = await reader.search_code(project_id, query)
        matches.extend(found["matches"])
        search_truncated = search_truncated or found["truncated"]
        if len(matches) >= 20:
            matches = matches[:20]
            search_truncated = True
            break
    if search_truncated:
        limitations.append("代码搜索达到结果上限。")
    excerpts, files_read = await _excerpts(reader, project_id, matches, files)
    if not matches:
        limitations.append("关键字搜索没有命中。摘录只是目录采样，不能当成完整检索。")
    analysis = ProjectAnalysis(
        project_summary=f"可见文件 {len(files)} 个，修订 {revision}。",
        languages=languages,
        frameworks=frameworks[:16],
        dependencies=_dependency_names(manifests)[:40],
        architecture_description=_architecture(languages, frameworks, manifests),
        entry_points=entries,
        test_frameworks=[item for item in frameworks if item == "pytest"][:8],
        database_technologies=databases[:8],
        relevant_modules=_modules(matches, files),
    )
    text = _render(tree, git, manifests, matches, excerpts, limitations)
    limit = options["max_context_chars"]
    if len(text) > limit:
        text = text[:limit]
        limitations.append("发送给模型的证据已按上下文字数截断。")
    return {
        "project_analysis": analysis,
        "evidence_text": text,
        "limitations": limitations,
        "files_read": files_read,
        "search_results": len(matches),
        "revision": revision,
        "queries": queries,
    }


def _clamp(value: object, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = high
    return max(low, min(high, number))


def _is_test(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py") or path.startswith("tests/")


def _languages(files: list[str]) -> list[str]:
    found = []
    for suffix, language in _LANGUAGES.items():
        if any(path.endswith(suffix) for path in files) and language not in found:
            found.append(language)
    return found[:8]


async def _manifests(reader: ProjectReader, project_id: str, files: list[str], include: bool) -> dict[str, str]:
    if not include:
        return {}
    bodies = {}
    for name in _MANIFESTS:
        matches = [path for path in files if path == name or path.endswith("/" + name)]
        for path in matches[:3]:
            try:
                loaded = await reader.read_file(project_id, path)
            except ProjectReadError:
                continue
            bodies[path] = loaded["content"][:8000]
    return bodies


def _markers(manifests: dict[str, str], catalog: tuple[tuple[str, str], ...]) -> list[str]:
    text = "\n".join(manifests.values()).lower()
    return [label for needle, label in catalog if needle in text]


def _dependency_names(manifests: dict[str, str]) -> list[str]:
    names: list[str] = []
    for path, body in manifests.items():
        if path.endswith("requirements.txt"):
            for line in body.splitlines():
                item = line.strip()
                if item and not item.startswith("#"):
                    names.append(item.split("==")[0].split(">=")[0][:80])
        elif path.endswith("pyproject.toml") or path.endswith("package.json"):
            names.append(path)
    return names


def _architecture(languages: list[str], frameworks: list[str], manifests: dict[str, str]) -> str:
    language = "、".join(languages) if languages else "未识别编程语言"
    framework = "、".join(frameworks) if frameworks else "未在清单中识别框架"
    source = "、".join(manifests) if manifests else "没有读取依赖清单"
    return f"语言来自文件扩展名：{language}。框架标记来自清单：{framework}。证据文件：{source}。"


def _queries(requirement: str) -> list[str]:
    tokens = []
    for match in _TOKEN.findall(requirement):
        if match not in tokens:
            tokens.append(match)
        if len(tokens) >= 6:
            break
    return tokens or [requirement.strip()[:40]]


def _modules(matches: list[dict], files: list[str]) -> list[str]:
    paths = []
    for item in matches:
        if item["path"] not in paths:
            paths.append(item["path"])
    if paths:
        return paths[:20]
    return [path for path in files if path.endswith(".py")][:10]


async def _excerpts(reader: ProjectReader, project_id: str, matches: list[dict], files: list[str]) -> tuple[list[str], int]:
    chosen = []
    for item in matches:
        if item["path"] not in chosen:
            chosen.append(item["path"])
        if len(chosen) >= 5:
            break
    if not chosen:
        chosen = [path for path in files if path.endswith((".py", ".md", ".toml"))][:3]
    blocks = []
    read = 0
    for path in chosen:
        try:
            loaded = await reader.read_file(project_id, path)
        except ProjectReadError as exc:
            blocks.append(f"{path}: {exc.code}")
            continue
        read += 1
        lines = "\n".join(loaded["content"].splitlines()[:80])
        blocks.append(f"FILE {loaded['path']} lines 1-{min(80, loaded['end_line'])}\n{lines}")
    return blocks, read


def _render(tree: dict, git: dict, manifests: dict[str, str], matches: list[dict], excerpts: list[str], limitations: list[str]) -> str:
    manifest_text = "\n".join(f"{path}\n{body[:1500]}" for path, body in manifests.items()) or "无"
    match_text = "\n".join(f"{item['path']}:{item['line']}: {item['excerpt']}" for item in matches) or "无命中"
    return "\n\n".join(
        [
            "目录:\n" + "\n".join(tree["files"][:100]),
            f"修订: {git.get('revision')}",
            "依赖清单:\n" + manifest_text,
            "搜索:\n" + match_text,
            "摘录:\n" + "\n\n".join(excerpts),
            "收集阶段限制:\n" + "\n".join(limitations),
        ]
    )
