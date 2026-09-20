# DeepSWE 单条先期实验：本机填写位置

**当前入口已接线（2026-09-20）：** `bash scripts/run-deepswe-pilot.sh`，先用 `--dry-run` 检查。默认四方法、52组合，AttnCompress/SWE-Pruner Pro不跑。筛选、镜像准备、留存计时和恢复说明见 [运行说明](../../../docs/deepswe-pilot-cli.md)。下面“未接入/65组合”等为初始预检历史，已被当前入口替代。配置与密钥文件请保持0600；不要把文件内容贴到日志或对话。

先填写同目录 `settings.toml` 和 `secrets.env`。这两份本地文件被 `.gitignore` 忽略，密钥文件权限设置为仅当前用户可读写。

- `settings.toml`：四个主模型端点/模型 ID、OpenCode 上下文与输出上限、AgentDiet 辅助服务、AttnCompress CUDA 服务、镜像及容器内路径。空字符串或 0 表示缺项，已有值仍需按实际账户核对。
- `secrets.env`：密钥值。主模型和 AgentDiet 可以使用同一账户，但辅助通道需显式配置。无认证的压缩服务可以留对应密钥为空。
- DeepSeek/Qwen 的 turn_control 预算映射尚未确定，不能默认宣称 GPT 分位数适用于这些模型。

这些文件目前是配置收集表，**尚未接入运行命令，不是可执行实验配置**。不要直接将 settings.toml 传给 `tokenAna run`，也不要把密钥抄入 runtime TOML：现有运行器会原样保存 runtime.json，后续凭据注入必须避开该快照。

已确认目标：同一条 DeepSWE 数据、run_free/turn_control/AgentDiet/AttnCompress/EET、四个 agent；Codex 仅 gpt-5.6-sol，其余 agent 各跑四模型，共 65 个组合。候选任务为按 ID 排序的首条 Python 任务 `adaptix-name-mapping-aliases`，尚未生成补丁或调用 API。先不扩至 20 条。

运行前仍需补齐方法接入、Codex 会话能力、Qwen 协议兼容、DeepSWE 任务读取问题、Mac 容器运行架构、完整留存及计时。原压缩服务存在 CUDA 调用，不能保证全部服务只在 Mac 本机运行。Docker daemon 当前也无法连接，镜像尚未验证。

预检详情见 `docs/deepswe-mac-pilot-readiness.md`（项目根目录下）；结构化检查记录保存在 `runs/preflight-deepswe-mac-20260920/`。填写服务配置不代表这些实现与运行条件已满足。
