# 六方法 × 四 agent × 两数据集

48 个逻辑组合、52 份 TOML。文件名为 `<method>_<agent>_<dataset>.toml`。Verified 示例前 10 条；DeepSWE run_free 为 Python 34 条，run_free_multilingual 为另外 79 条，其余方法为完整 113 条。所有配置均提供 original/corrected/overhead 接线，兼容版本与验收边界见 [交付文档](../../docs/architecture.md)。

模板中的 localhost 端点、`/opt/tokenana` 执行器/head、OpenCode context/output 上限仅为配置示例，须按已准备环境明确替换。SWE-Pruner 只允许 Qwen3-Coder-Next + 对应现有 head，同一 SGLang 为主模型和 hidden-state 请求服务。AgentDiet 辅助模型固定 gpt-5-mini；AttnCompress 使用原 /compress；EET 默认使用组件中的已有经验库。

```bash
conda activate tokenAna
python -B tokenAna.py run experiments/method_matrix/swe_pruner_pro_codex_deepswe.toml --dry-run
```

本地缺少方法依赖或 head 时明确 blocked，不会安装、启动服务、调用模型或退回 run_free。Codex 需要独立会话构建，当前仅提供源码和补丁；不能把文件存在当作可执行器。实际 run/resume 还需要所选数据集的预备 runtime。

此前 52 份 CLI 规划均为 plan_only，开发期批量验证脚本已清理。真实组件验收范围见 [开发流程](../../docs/development-workflow.md)，配置展开不代表运行验收。不要将不同 DeepSWE 语言集合直接比较节省率；比较时显式使用相同任务集或先按语言变体合并统一集合。
