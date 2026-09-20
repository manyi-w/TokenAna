# 六方法 × 四 agent × 两数据集

48 个逻辑组合、52 份 TOML。文件名为 `<method>_<agent>_<dataset>.toml`。Verified 示例前 10 条；DeepSWE run_free 为 Python 34 条，run_free_multilingual 为另外 79 条，其余方法为完整 113 条。所有配置均提供 original/corrected/overhead 接线，兼容版本与验收边界见 [交付文档](../../docs/method-integration-delivery.md)。

模板中的 localhost 端点、`/opt/tokenana` 执行器/head、OpenCode context/output 上限仅为配置示例，须按已准备环境明确替换。SWE-Pruner 只允许 Qwen3-Coder-Next + 对应现有 head，同一 SGLang 为主模型和 hidden-state 请求服务。AgentDiet 辅助模型固定 gpt-5-mini；AttnCompress 使用原 /compress；EET 默认使用组件中的已有经验库。

```bash
source /Users/manyi/miniconda3/etc/profile.d/conda.sh
conda activate tokenAna
python tokenAna.py run experiments/method_matrix/swe_pruner_pro_codex_deepswe.toml --dry-run
python scripts/dry_run_method_matrix.py --output /absolute/new/plan-directory
```

本地缺少方法依赖或 head 时明确 blocked，不会安装、启动服务、调用模型或退回 run_free。Codex 需要独立会话构建，当前仅提供源码和补丁；不能把文件存在当作可执行器。实际 run/resume 还需要所选数据集的预备 runtime。

离线测试覆盖配置、原生统计读取、两套报告与恢复；52 份 CLI 规划为 plan_only。真实组件、Rust、GPU 和效果验证未执行。不要将不同 DeepSWE 语言集合直接比较节省率；比较时显式使用相同任务集或先按语言变体合并统一集合。
