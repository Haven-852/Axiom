"""Turn an approved task into an instruction. The model does not choose the next task."""

import subprocess
from pathlib import Path

from ase.execution.eligibility import EligibilityError
from ase.schemas.coding import CodingInput

_CONSTRAINTS = [
    "只修改当前任务列出的文件。",
    "不修改 ASE 自身生产代码。",
    "不访问仓库外文件。",
    "不读取密钥或环境变量文件。",
    "不修改 Git 主分支，也不执行 git merge。",
    "不降低测试标准。",
    "不改变用户已经确认的功能范围。",
    "遇到需求歧义或证据和当前代码不一致时停止并报告。",
    "只读取和修改上面列出的相对路径。不要列出或读取工作区父目录，也不要使用工作区之外的绝对路径。",
    "先读取修改范围内的文件并完成修改，然后调用 finish。不要把迭代用在查看 Git 历史、父目录或其他仓库上。",
    "保持列出的函数签名。不要引入第三方依赖。不要为了通过验收而修改测试，除非任务明确要求修改测试。",
]


def build_instruction(payload: CodingInput, workspace: Path) -> str:
    for relative in payload.related_files:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise EligibilityError("path_rejected", f"related file is not inside the repository: {relative}")
    if payload.repository_revision:
        current = _head(workspace)
        if current != payload.repository_revision:
            raise EligibilityError("revision_changed", "repository revision changed; research evidence is stale")
    decisions = "\n".join(
        f"- {item.get('question')}: {item.get('answer')}（来源 {item.get('source', 'user')}）" for item in payload.user_decisions
    )
    criteria = "\n".join(f"- {item}" for item in payload.acceptance_criteria)
    files = "\n".join(f"- {item}" for item in payload.related_files) or "- 未限定具体文件"
    findings = "\n".join(f"- {item}" for item in payload.source_findings) or "- 无"
    constraints = "\n".join(f"- {item}" for item in [*_CONSTRAINTS, *payload.execution_constraints])
    return (
        f"当前任务：{payload.task_description}\n"
        f"任务类型：{payload.task_type}\n"
        f"不要做：不要领取其他任务，不要扩大范围，不要自行决定未确认的需求。\n"
        f"修改范围：\n{files}\n"
        f"验收标准：\n{criteria}\n"
        f"研究证据 id：\n{findings}\n"
        f"用户决策：\n{decisions or '- 无'}\n"
        f"约束：\n{constraints}\n"
    )


def _head(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise EligibilityError("revision_changed", "workspace has no git revision")
    return completed.stdout.strip()
