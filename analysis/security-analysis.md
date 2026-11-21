# 安全与权限

## SDK：动作确认和风险分析是分开的

策略：`openhands-sdk/openhands/sdk/security/confirmation_policy.py`

- `ConfirmationPolicyBase.should_confirm(risk)`
- `AlwaysConfirm`、`NeverConfirm`、`ConfirmRisky`

分析器：`security/analyzer.py` 的 `SecurityAnalyzerBase.security_risk(action) -> SecurityRisk`，结果类型 `SecurityAnalysis`。实现包括：

- `LLMSecurityAnalyzer`（`security/llm_analyzer.py`）
- `PatternSecurityAnalyzer`（`security/defense_in_depth/pattern.py`）
- `PolicyRailSecurityAnalyzer`（`defense_in_depth/policy_rails.py`）
- `EnsembleSecurityAnalyzer`（`security/ensemble.py`）
- `GraySwanAnalyzer`（`security/grayswan/analyzer.py`）
- `ToolShieldLLMSecurityAnalyzer`（`security/toolshield_llm_analyzer.py`），对应可选依赖 `toolshield`

`Agent._analyze_for_confirmation` 先分析，再由策略决定是否把会话置为 `waiting_for_confirmation`。分析器缺失时，每个动作得到 `SecurityRisk.UNKNOWN`。`ConfirmRisky.confirm_unknown` 默认 True，因此未知风险仍会等人。

Shell 语义：`security/shell_parser.py` 与 `security/_shell_ast.py`（`ShellProgram`、`ShellPipeline`）给模式分析器用，测试在 `tests/sdk/security/defense_in_depth/test_shell_semantics.py`。

密钥：`conversation/secret_registry.py` 的 `SecretRegistry` 能把秘密注入命令环境变量，并在输出和 Pydantic 模型里掩码（`mask_secrets_in_output`、`mask_secrets_in_model`）。`ConversationState._save_base_state` 在没有 cipher 时把秘密打成占位符并打日志，避免明文进 `base_state.json`。cipher 在 `utils/cipher.py` 的 `Cipher`。

服务认证：`agent_server/config.py` 读取 `OH_SECRET_KEY` 与 `OH_SESSION_API_KEYS_0`。未设置密钥时日志警告秘密无法保持。`auth_router` 前缀 `/auth`。

隔离：`DockerWorkspace` 和 docker runtime 把命令放进容器。本地 `LocalWorkspace` 没有额外的 seccomp 或用户降权。worktree 隔离的是 git 目录，不是操作系统用户。

ACP 路径另有文件型凭证生命周期 `ACPFileCredentialLifecycle`（`agent/acp_file_credentials.py`），用于把外部编码代理的凭证放进会话目录并在结束时清理。这和 `SecretRegistry` 不是同一套。

## Agent Canvas

浏览器侧确认 UI：`src/hooks/mutation/use-respond-to-confirmation.ts`、`src/components/shared/buttons/conversation-confirmation-buttons.tsx`。设置里有 `confirmation_mode`（`src/components/features/chat/confirmation-mode-enabled.tsx`）。权限决定仍在服务端策略。Canvas 自己不再分析 shell。

`src/utils/vscode-origin.ts` 讨论编辑器反代来源，避免 vscode 静态资源被指到错误的 canvas origin。这是部署时的来源校验，不是命令策略。

## automation

数据库里的运行身份是 `Automation.run_as_kind`（`models.py` 的 `AutomationRunAsKind`）。CORS 使用 `ApiKeyAwareCORSMiddleware`（`app.py`）：带 API key 的浏览器调用放宽，cookie 会话使用白名单。`/ready` 检查数据库连通性，失败返回 503。

webhook 有 `webhook_policy.py` 与 `webhook_router.py`。已读路径没有 `SecurityAnalyzer`。自动化一旦跑起来，命令风险由它调用的 agent-server 决定。

索引使用 `FOR UPDATE SKIP LOCKED` 避免多个调度器重复建 run。这是并发正确性，不是安全沙箱。

## mini-swe-agent

`InteractiveAgent` 的 `confirm` / `yolo` / `human` 是唯一的人工门。`yolo` 直接执行模型给出的 bash。`LocalEnvironment` 在宿主机跑命令，环境变量是 `os.environ` 再叠加配置。`DockerEnvironment` 提供容器边界，但 `execute` 把命令追加给 `bash -lc`，容器里的权限等于镜像用户的权限。没有风险枚举，没有秘密掩码类。

## aider

`Coder.allowed_to_edit` 拒绝聊天外的路径。`GitRepo.ignored_file` 结合 gitignore 与 `.aiderignore`。shell 通过 `run_cmd` 在本机执行，用户靠自己不输入 `/run` 来避免危险命令，代码里没有策略引擎。

分析用的 `docker/Dockerfile` 以 uid 1000 创建 `appuser`，后续阶段是否降权要看镜像后半段；它保护的是“运行 aider 这个进程”，不是拦截模型生成的命令。

## MetaGPT

未找到确认策略或 shell AST 分析。`Role.is_human` 只标记角色。密钥放在 `config2.yaml` 的 `llm.api_key`。执行动作继承普通 Python 进程权限。

## cline

工具策略在 `AgentRuntime`：`autoApprove === false` 时必须有 `requestToolApproval`，否则该工具调用被拒绝。hook `beforeTool` 可以 `skip`。命令护栏测试在 `sdk/packages/core/src/extensions/tools/command-guard.test.ts`，其中 `git worktree add` 会被挡住、`git worktree list` 不会。这是命令字符串规则，不是 SDK 那种可插拔分析器。

任务批准是另一层：`SqliteAgendaTaskStore` 把代理建议的任务留在 `pending_approval`。会话存储使用 SQLite 文件，不是集中式权限服务。

## ASE 应保留的控制点

1. 命令和写文件必须经过 SDK 的 `ConfirmationPolicy`。Web 与 Cursor 只提交“批准 / 拒绝 action id”，不在前端重算风险。
2. 默认分析器用 `ConfirmRisky` 加 `PatternSecurityAnalyzer` 或 `EnsembleSecurityAnalyzer`。LLM 分析器作为可选增强，不作为唯一闸门。
3. 秘密只进 `SecretRegistry`，ASE 的 PostgreSQL 存秘密的引用名，不存明文。加密沿用 SDK `Cipher`，密钥来自环境变量，对齐 `OH_SECRET_KEY` 的做法。
4. 不可信仓库的 `execute_command` 走 `DockerWorkspace` 或未来的远程执行器。Git worktree 只隔离分支，不算安全边界。
5. automation 的 API key CORS 分裂策略值得抄到 ASE 的 FastAPI：浏览器会话和编程调用分开。
6. 不把 aider 的本机 `run_cmd` 或 MetaGPT 的进程内写文件引进默认路径。
