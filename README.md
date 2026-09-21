# TokenAna

TokenAna 将代码 agent 的执行、原生统计和统一 API 统计分开，保存可重建的实验记录。支持六方法、baseline、四 agent，以及 DeepSWE 和 SWE-bench Verified。论文运行范围由 [paper.toml](experiments/paper.toml) 单独选择。

## 论文矩阵

| 组 | 配置数 | 每配置任务 | 总任务运行 |
| --- | ---: | ---: | ---: |
| DeepSWE 五方法＋baseline 通用组 | 78 | 113 | 8,814 |
| DeepSWE 六方法＋baseline 共同 Qwen3-Coder-Next 组 | 28 | 113 | 3,164 |
| Verified 三组补充实验，不运行 baseline | 3 | 100 | 300 |
| 合计 | 109 | — | 12,278 |

DeepSWE 固定清单为 Python 34、Go 34、TypeScript 35、JavaScript 5、Rust 5。每配置每题一次；HTTP 原生重试不等于重新生成任务。Verified 不为预算校准新增 baseline，也不计算缺少相容既有 baseline 的节省率。

## 使用

先激活已有的 Conda 环境，Python 必须为 3.12.14：

```bash
conda activate tokenAna
python -B -m tokenAna study experiments/paper.toml --output runs/paper-selection
bash scripts/run-experiments.sh --study experiments/paper.toml --dry-run
```

复制并填写 [设置模板](config/examples/deepswe-settings.toml) 和 [凭据模板](config/examples/deepswe-secrets.env.example)，保存到 config/local/deepswe-pilot。真实模型 ID、协议、主/辅助服务、执行镜像和现有剪枝 head 必须符合配置。dry-run 不联系服务，不代表真实验收。

环境准备和验收完成后，操作者运行：

```bash
bash scripts/run-experiments.sh --study experiments/paper.toml --output runs/paper-run --jobs 1
bash scripts/run-experiments.sh --resume runs/paper-run --jobs 1
python -B -m tokenAna study experiments/paper.toml --runs runs/paper-run --output runs/paper-report --jobs 4
```

第一条会进行实际生成和评测。study 先执行已有矩阵中的 baseline，再按原分位数算法冻结对应 turn_control 预算；固定任务集合不受历史 next-N 筛选影响。恢复保留原配置、价格、镜像和预算，不因生成失败自动重新生成。

离线 study 报告重读保存记录，输出新目录，不覆盖旧报告。PDF/SVG 使用 [分析依赖](requirements/analysis.txt)；无绘图环境时明确记录 figures=unavailable，JSON/CSV/Markdown 仍可生成。显式 --no-figures 用于仅导出表格。

## 产物与解释

- original、corrected-v1 保留原规则；corrected-v2-api 汇总实际生成 API usage，并按固定 selected 任务数计算均值。缺失字段与零分开。
- input/output 包含关系由协议确定；cache/reasoning 子项不重复加总。本地压缩和剪枝 forward 单列，费用显示不计价。
- 内容类别和重复历史仅作结构诊断，不新增研究 tokenizer，不从字符数估算 token。
- 研究报告包含逐请求、逐任务、口径桥接、方法排名、节省率、配对仓库 bootstrap、Pareto、四组结果诊断及证据索引。无法隔离的统计变化保持未知。
- 默认 research 留存保存最终工作区增量及共享基础镜像身份；full 模式增加逐请求文件系统快照。二者均保留模型请求/响应、原生轨迹、方法前后快照和评测记录。时间对比须统一留存模式。

## 验证与文档

```bash
python -B scripts/validate_study.py --jobs 4 --output runs/study-validation
python -B scripts/validate_retention.py --jobs 4
```

[使用说明](docs/usage.md) · [统计协议](docs/accounting-protocol.md) · [架构](docs/architecture.md) · [开发流程与当前验收](docs/development-workflow.md) · [协作规则](AGENTS.md)

实现、离线验证、真实组件接假模型和正式论文运行是四种不同状态。当前未完成整个真实组件验收矩阵；精确通过项与缺口见开发流程，不能将已有单任务试跑解释为全矩阵验收。

第三方原源码及许可证保留在各组件 upstream 目录；独立 Codex 构建补丁保留在 agents/codex/control-build。引用研究方法与数据集时使用各原仓库的引用说明。
