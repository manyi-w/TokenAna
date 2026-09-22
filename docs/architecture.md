# TokenAna 架构与研究约定

## 分层

- 方法目录负责执行适配、original 统计和特有状态。原方法、agent、数据和评测器源码保持不变；不在生产路径 monkey patch 原算法。
- agents 目录负责原生客户端、工具循环、协议映射、会话边界和原始 usage 录制。Codex 的已批准 controlled 副本通过独立补丁构建，upstream 保持原样。
- agent 的机械性补丁/会话产物处理复用 `src/agent_artifacts.py`；原生完成条件、超时边界和控制规则仍由各适配器决定。
- datasets 目录负责公共任务映射、隔离工作区、提交与原评测器。Task 仅暴露 ID、仓库、基准提交和任务说明；隐藏测试和参考补丁只进入评测侧。
- `src/containers.py` 管理共用的任务容器生命周期；数据集保留工作目录、网络、配额、挂载及提交规则。src/execution.py 复用统一执行、记录、恢复；src/pilot.py 分开处理选择/恢复、runtime 构造、阶段调度和控制器运行。
- src/study.py 管理研究选择；src/accounting_v2.py、research_evidence.py、research_diagnostics.py、study_report.py 和 research_figures.py 负责公共研究统计、分析及产物，不复制执行引擎。
- usage 校验不计算统计报表；同一 HTTP 证据只解码一次，再按 legacy 与真正转发口径生成视图。v1/v2 的冲突规则、分母、unknown 和完整性分别保留。`src/tabular.py` 共用表格输出，保留旧报表的 null/bool 文本与原子写入，以及 study 的追加列约定。

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

[统计协议](accounting-protocol.md) 定义 original/corrected-v1/corrected-v2-api、逐指标完整性、偏差桥接、费用、排名与 bootstrap。细粒度 token 来自 API；按任务、请求、模型、用途、HTTP 结果拆分。内容仅按结构分类与消息版本诊断，用户已确认不恢复研究 tokenizer。

本地 forward 与生成 API usage 分账。AttnCompress 保留文本不是生成输出；SWE-Pruner wrapper 与后端分别记录但不重复归账。SGLang 后端只有提供逐 forward telemetry 时才能报告真实 forward 数；仅有提交 input_ids 的请求不能充当模型 forward 证据，缺失时明确 unknown。自托管主生成按 main 用途计 usage，没有冻结价表则费用不排名。

## 留存与恢复

默认 research 保存停止后的最终工作区（DeepSWE /app、Verified /testbed）和 /logs 的压缩增量、删除列表、Git 元数据和新增/忽略文件；已持久化挂载不重复归档，外部可写挂载单列。基础镜像使用共享层和固定 ID 标签，不按题导出。迁移时集中备份这些镜像。

full 增加逐请求文件系统增量和完整容器归档。原始 HTTP、原生轨迹、方法前后快照、配置、价格、补丁、评测和失败记录在两种模式都保留。research 不能还原整个 OS 或每个中间文件；最终工作区恢复依赖原镜像与记录的挂载。归档可读性及条目完整性验证成功后才清理容器。

用户已要求清空旧试跑的 runs/ 输出及本地 Git 中对应副本，包括中断试跑的环境归档。当前保留源码、运行配置、任务数据、原方法资源和评测器，旧运行记录与环境不可再恢复；新实验仍按上述策略生成并留存结果。

凭据不进入配置快照、请求头日志或构建上下文。研究图表和抽查样本使用证据路径，不复制完整提示。time 区间按阶段取并集；墙钟已经包含阻塞、方法和观测开销，嵌套阶段不能相加。

## 当前状态

软件实现与实际验收分别记录在 [开发流程](development-workflow.md)。真实组件矩阵、GPU 后端逐 forward 观测和完整论文运行不能由配置展开、模块导入或假模型测试推断为完成。无差异、未触发和证据不足均是合法研究结果。

开发期测试与专用验证产物确认通过后清理，不作为仓库长期组成部分；主实验所需的配置、统计与留存校验及官方评测器继续保留。
