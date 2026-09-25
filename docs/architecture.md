# TokenAna 架构与研究约定

## 分层

- 方法目录负责执行适配、original 统计和特有状态。原方法、agent、数据和评测器源码保持不变；不在生产路径 monkey patch 原算法。
- agents 目录负责原生客户端、工具循环、协议映射、会话边界和原始 usage 录制。Codex 的已批准 controlled 副本通过独立补丁构建，upstream 保持原样。
- agent 的机械性补丁/会话产物处理复用 `src/agent_artifacts.py`；原生完成条件、超时边界和控制规则仍由各适配器决定。
- datasets 目录负责公共任务映射、隔离工作区、提交与原评测器。Task 仅暴露 ID、仓库、基准提交和任务说明；隐藏测试和参考补丁只进入评测侧。
- `src/containers.py` 管理共用的任务容器生命周期；数据集保留工作目录、网络、配额、挂载及提交规则。src/execution.py 复用统一执行、记录、恢复；src/pilot.py 分开处理选择/恢复、runtime 构造、阶段调度和控制器运行。
- src/study.py 管理研究选择；src/accounting_v2.py、research_evidence.py、research_diagnostics.py、study_report.py 和 research_figures.py 负责公共研究统计、分析及产物，不复制执行引擎。
- usage 校验不计算统计报表；同一 HTTP 证据只解码一次，只保留实际转发的 v2 统计视图。`accounting_trace.py` 的算术表达式直接产生结果与可复算步骤；`accounting_explain.py`、`rq1_explain.py` 负责三方对照导出，不复制统计公式。`src/tabular.py` 共用表格输出，保留旧报表的 null/bool 文本与原子写入，以及 study 的追加列约定。

框架支持六方法、四 agent、已确定模型和两数据集；论文只选择 experiments/paper.toml 的 109 配置、12,278 次任务。DeepSWE 为 106×113，Verified 为 3×100，无 Verified baseline。任务清单保留原 34 条 Python 顺序后补齐五语言。

## 执行与可比性

study 使用相同有序任务集合，保存 baseline_id、实际模型配置、agent/method 配置、价格和分析设置。先完成 baseline 阶段，再从对应原生最终摘要冻结 turn_control P50/P75；线性分位数向上取整，筛选和排除证据保存在 budgets。Verified 沿用 gpt/claude 既有预算，不新增 baseline。

非 study 的历史 DeepSWE 迁移预算来自 [原 trials](https://deepswe.datacurve.ai/artifacts/v1.1/trials.json) 中 full/deep-swe、排除 errored 的 n_agent_steps，GPT 53→74、Claude 91→123、DeepSeek 148→185、Qwen 102→134。它们是原 mini 配置的跨 agent/版本迁移值，不能冒充当前对应 baseline 的实测预算；论文 study 会冻结本矩阵 baseline 的结果。

主模型 HTTP 重试沿用原客户端；失败、中断、空补丁和方法未触发都保存。never_regenerate 将评测恢复与任务重新生成分开。恢复须使用已保存的选择、配置、镜像和留存模式。论文 measurement_jobs 默认 1；调整并发必须建立明确的独立研究配置，不能混入原计时单元。离线重建与验收默认 4 worker，耗时不用于论文比较。

离线报告核对实际 config.json、pricing.json 与冻结 profile，核对 method/baseline 的 agent、model、dataset 基础设置。每项资源指标独立判断完整性，缺失配置保留 selected 分母但不获得该指标排名。未冻结并发的旧运行不进入时间排名。

## 方法忠实性

| 方法 | 保留行为 |
| --- | --- |
| run_free | 原静态阅读/编辑限制；Verified 保留原 Git 禁令；DeepSWE 仅放开原分支/提交约定需要的 Git 操作；非 Python 使用已批准多语言扩展。 |
| turn_control | 同会话一次 P50→P75 扩展、原生更小限额优先；原最终摘要筛选与 mean=null 保留。 |
| AgentDiet | 原 MessageManager、上下文前 1 后 2、阈值 500、gpt-5-mini 辅助分析、原 SDK 与外层 HTTP 重试；完整步骤替换。 |
| AttnCompress | 原 dynamic full、ratio .20、末尾 2 步保护、工具结果压缩和失败回退；服务 wrapper 通过 PyTorch 公共 hook 观察真实 forward，不改张量。 |
| SWE-Pruner Pro | 原 PrunerClient、阈值 .5、最短 2000 字符、KEEP_ALL/focus question/后处理；仅 Qwen3-Coder-Next 与匹配现有 head，主生成与 hidden-state 使用同一 SGLang 服务。 |
| EET | 冻结原经验库，DeepSWE 使用原跨仓库检索分支，不传仓库过滤；保留相似度、阈值、top-k、提示和原终止行为，不用评测题轨迹建库。 |

EET 只声称当前源码可提供的经验检索/指导，不声称补完论文缺失机制。冻结经验库按字节复制到 study inputs，复用副本，不生成来源哈希清单。AgentDiet 的原 +200000 APIStatusError 补偿和每次分析假定缓存 492，仅属于 original。AttnCompress 的原 common-prefix 字符/4 估计与零 analysis counters 保留，不当成真实推理 usage。

Session 的 initialize/before_model/after_model/after_tool/finish 保存原始事件、返回历史和实际请求。系统说明、协议字段、工具 ID 不可变；摘要必须替换完整可编辑工具步骤。mini 使用原 query/action 子类边界；Trae 使用适配客户端/agent 子类，Lakeview 单独计 agent_auxiliary；OpenCode 使用本地插件；Codex 使用已批准的独立 session/turn-control hook。

## 数据与评测

DeepSWE 使用本地原 Pier 和原提交收集规则，要求 base..HEAD 的已提交补丁；未提交差异另存诊断。任务的短 SHA 在仓库内解析，不能修改原数据。原 resource 配额保留：每题依其 task.toml，典型为 2 CPU、8192 MB、20480 MB；agent/prepare/verifier timeout 各按原配置。

Verified 使用本地官方 swebench.harness.run_evaluation，保存命令、stdout/stderr、原报告和容器。评测通过共享 Docker daemon 执行，任务镜像与评测依赖独立。框架仍支持完整 500 题和其他合法组合；不代表纳入论文。

## 统计与证据

[统计协议](accounting-protocol.md) 定义 original/cache_only/corrected-v2-api、逐指标完整性、偏差桥接、费用、排名与 bootstrap。细粒度 token 来自 API；按任务、请求、模型、用途、HTTP 结果拆分。内容仅按结构分类与消息版本诊断，用户已确认不恢复研究 tokenizer。

本地 forward 与生成 API usage 分账。AttnCompress 保留文本不是生成输出；SWE-Pruner wrapper 与后端分别记录但不重复归账。SGLang 后端只有提供逐 forward telemetry 时才能报告真实 forward 数；仅有提交 input_ids 的请求不能充当模型 forward 证据，缺失时明确 unknown。自托管主生成按 main 用途计 usage，没有冻结价表则费用不排名。

## 留存与恢复

默认 research 保存停止后的最终工作区（DeepSWE /app、Verified /testbed）和 /logs 的压缩增量、删除列表、Git 元数据和新增/忽略文件；已持久化挂载不重复归档，外部可写挂载单列。基础镜像使用共享层和固定 ID 标签，不按题导出。迁移时集中备份这些镜像。

full 增加逐请求文件系统增量和完整容器归档。原始 HTTP、原生轨迹、方法前后快照、配置、价格、补丁、评测和失败记录在两种模式都保留。research 不能还原整个 OS 或每个中间文件；最终工作区恢复依赖原镜像与记录的挂载。归档可读性及条目完整性验证成功后才清理容器。

用户已要求清空旧试跑的 runs/ 输出及本地 Git 中对应副本，包括中断试跑的环境归档。当时清理的旧运行不可恢复；随后新生成的运行及其原始证据按上述策略保留。本次仅重建/清理旧统计派生产物。

凭据不进入配置快照、请求头日志或构建上下文。研究图表和抽查样本使用证据路径，不复制完整提示。time 区间按阶段取并集；墙钟已经包含阻塞、方法和观测开销，嵌套阶段不能相加。

## 当前状态

RQ1 本地端点及凭据按用户约定复用现有 Claude/GPT 第三方服务，覆盖四组主模型及 AgentDiet 辅助模型；固定模型 ID 与协议不变。凭据通过本地环境文件加载，第三方密钥不用于 AttnCompress 的可选 Google 官方分支。配置解析已检查，真实服务兼容性及完整环境验收仍待完成。

RQ1 的四个 `run.sh` 通过 `src/rq1_run.py` 复用公共执行器、恢复、research 留存和官方评测。`src/rq1_images.py` 复用公共构建与进度记录，自动拉取逐题 Verified 基础镜像、添加原生客户端/工具并构建官方评测镜像；全部完成后冻结镜像 ID 并检查运行时，才进入生成。显式镜像覆盖本地缺失时自动拉取；恢复沿用已冻结 ID，不重新构建替换。`--check` 只查配置及前置环境，不要求镜像已存在。`src/rq1_native.py` / `src/rq1_expert.py` 负责原调用传输、配置与观测；run_free 保留原 Claude CLI 1.0.16 和提示/Git 约束，AgentDiet/AttnCompress 使用原 Expert，turn_control 使用已批准的现代 Trae 同会话控制器。原算法不修改，不另建执行引擎。Gemini 原生 generateContent 与 OpenAI 兼容协议分别记录 usage，缓存/reasoning 按字段包含关系规范化。

独立 [RQ1](../RQ1/README.md) 固定 run_free/Sonnet 4.5 前 100 题、turn_control/正式版 Gemini 2.5 Pro 原 100 题、AttnCompress/Gemini 3 Flash 及 AgentDiet/Gemini 2.5 Pro 各 200 题。turn_control 为 29→45，历史 preview 06-05 与新正式版分栏。`paper-values.toml` 保存论文印刷操作数，`src/rq1_report.py` 输出论文值、新 original、同轨迹完整 v2 和 cache_only；方法 historical reader 保留原筛选与计价规则，`replay.sh` 仅复算历史。历史 Gemini 确有缓存命中，但完整历史 API 覆盖仍不足；新入口已接线，四组真实组件与正式运行尚未验收。

turn_control 已知缓存命中的两套补充价格情景及费用数字保存在 [RQ1 文档](../RQ1/README.md#turn_control-已知缓存命中的费用补算)，直接以论文印刷费用为操作数。局部补算与完整缓存校正分开：缺字段维持未知，情景费用不代填完整 cache_only/v2，也不覆盖新运行的冻结价格。

RQ1 AgentDiet 按用户约定将作者接口视为标准 Gemini OpenAI 兼容接口；该语义下原 Trae 主模型统计不会因额外读取顶层缓存字段而重复相加。研究重点为原费用假设及辅助模型的固定缓存扣除。此约定不更换原客户端协议，不改原算法，也不替代缺失的逐调用证据；实际新响应始终据实留存和统计。
