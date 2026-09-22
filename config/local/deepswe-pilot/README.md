# DeepSWE 正式入口的本地配置

`bash scripts/run-experiments.sh` 读取此目录的 `settings.toml` 和可选的 `secrets.env`。两文件被本目录 `.gitignore` 排除；公开模板在 [config/examples](../../examples/)，初始化命令见 [根 README](../../../README.md)。已有配置请直接编辑，不要覆盖。

- 主模型的 `name` 必须匹配入口内置实验标识；`model_id` 填服务实际接受的 ID。只校验本次所选模型。
- `provider`、`protocol` 必须匹配 agent 支持；不静默翻译协议。模板使用 OpenAI Responses、Anthropic Messages、DeepSeek/DashScope Chat Completions。
- `base_url` 填 API 基址；不要附带认证信息或生成路由。`api_key_env` 只填环境变量名，值放 secrets.env 或进程环境。同名进程变量优先。
- AgentDiet 需额外填写 `agent_diet_auxiliary`，模型固定为 `gpt-5-mini`，辅助基址后追加 `/chat/completions`。
- OpenCode 的上下文/输出上限应与实验约定一致；0/缺省时入口用 131072/8192 并提示，这是实验上限，不是服务商能力声明。
- `images` 空值触发按选定组合准备镜像；自备镜像可用 `{task_id}` 占位。`runtime_paths` 是容器内路径，不是宿主路径。

```bash
chmod 600 config/local/deepswe-pilot/settings.toml config/local/deepswe-pilot/secrets.env
bash scripts/run-experiments.sh --method run_free --agent mini --model deepseek-v4.1-flash --dry-run
```

这些设置专供矩阵入口使用，不能直接传给 `python -m tokenAna run`。不要提交凭据或把本地文件内容贴入日志。参数、恢复与默认 52 组合说明见 [运行手册](../../../docs/usage.md)。
