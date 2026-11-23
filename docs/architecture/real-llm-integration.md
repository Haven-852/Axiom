# 真实模型接入

编码代理和诊断代理共用同一套 `Settings`，密钥只从环境变量或 `.env` 读取。

| 配置 | 环境变量 | 作用 |
| --- | --- | --- |
| provider | `ASE_LLM_PROVIDER` | 目前按 `deepseek` 映射 OpenAI 兼容端点 |
| model | `ASE_LLM_MODEL` | 官方模型名 `deepseek-flash` |
| api base | `ASE_LLM_API_BASE` | `https://api.deepseek.com` |
| api key | `ASE_LLM_API_KEY` | `SecretStr`，不写入源码、数据库或日志 |
| temperature | `ASE_LLM_TEMPERATURE` | 默认 0 |
| max tokens | `ASE_LLM_MAX_TOKENS` | 诊断请求用 `max_tokens`，OpenHands 用 `max_output_tokens` |
| timeout | `ASE_LLM_TIMEOUT` | 单次模型请求超时，默认 180 秒 |

`src/ase/adapters/openhands.py` 的 `build_llm()` 对照 `openhands-sdk==1.63.1` 的 `LLM` 字段构造模型。`deepseek` 会变成 LiteLLM 模型名 `openai/deepseek-flash`，并设置 `base_url`。DeepSeek 默认开启思考模式，编码会话额外传入 `litellm_extra_body={"thinking": {"type": "disabled"}}`，避免工具调用和思考字段互相干扰。

诊断不导入 OpenHands。`src/ase/agents/llm_client.py` 用 httpx 调用 `{base}/chat/completions`。HTTP 错误只保留状态码。返回文本或日志里如果出现密钥，调用直接失败。没有密钥、请求失败或 JSON 无法解析时，诊断退回确定性规则，并在结果里标记 `source=rules`。

模型调用记录包含 provider、模型名、耗时和接口返回的 usage。密钥在写入执行事件前经过 `redact()`。

两件事不能互相代替：

- `tests/test_llm_config.py` 是一次真实 chat completion。
- `tests/test_openhands_sdk.py` 使用 SDK 的 TestLLM，不访问外部模型。
