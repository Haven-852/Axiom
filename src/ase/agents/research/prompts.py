import json

from ase.agents.research.schemas import ResearchAssessment


def system_prompt() -> str:
    schema = ResearchAssessment.model_json_schema()
    return (
        "你是 ASE 的只读 Research Agent。只返回一个 JSON 对象，不要 Markdown。\n"
        "不要编造用户没有给出的业务规则。不确定的内容放进 assumptions 或 clarification_questions。\n"
        "inferred 验收标准必须使用 source=inferred。\n"
        "code_findings.file_path 只能使用证据里出现的仓库相对路径，行号必须落在摘录范围内。\n"
        "没有搜索命中时，code_findings 必须是空数组，并在 limitations 说明证据不足。\n"
        "不要输出 API Key、令牌或密钥内容。\n"
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
    )


def user_prompt(requirement: str, evidence: str) -> str:
    return f"用户需求:\n{requirement}\n\n项目证据:\n{evidence}"


def repair_prompt(previous: str, error: str) -> str:
    return (
        "上一次输出没有通过 Schema。请只返回修正后的 JSON 对象。\n"
        f"错误:\n{error[:1500]}\n\n"
        f"上次输出:\n{previous[:6000]}"
    )
