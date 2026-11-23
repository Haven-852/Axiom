# Project Reader

`ProjectReader` 只绑定一个 project id 和一个仓库根目录。

它提供目录树、按相对路径读文件、关键字搜索、`git rev-parse`、`git status` 和 `git diff --stat`。没有写文件和 git commit。

约束：

- 拒绝另一个 project id。
- 拒绝绝对路径和 `..`。
- 解析符号链接后，目标必须仍在仓库根目录内。
- 单文件默认不超过 100000 字节。
- 目录深度、文件数和搜索条数都有上限。
- 跳过 `.git`、`.venv`、`node_modules`、构建目录和缓存目录。
- 拒绝 `.env`、私钥、证书和 `credentials.json`。
- 读取前后修改时间不一致时，结果标记 `stale`。

搜索没有命中时，报告的 `limitations` 会写明后续摘录只是目录采样。不会把不完整搜索说成全仓分析。

证据发送给模型前按 `max_context_chars` 截断，默认不超过 50000 字符。
