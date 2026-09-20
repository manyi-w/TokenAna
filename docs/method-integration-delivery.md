# 六类方法接入、双口径与 overhead 交付

2026-09-20，用户“剩下的一次性完成吧，不用问我了”授权的剩余实现及离线验收。本页仅说明六方法工作线，不代替其他任务的 Mac 真实 API 试验授权或就绪判断。

## 交付与使用

六类方法为 run_free、turn_control、swe_pruner_pro、agent_diet、attn_compress、eet；四个 agent 为 Codex、mini-SWE-agent、Trae、OpenCode；数据集为 Verified（包括现有子集）及 DeepSWE。配置在 [experiments/method_matrix](../experiments/method_matrix/README.md)，共 48 个逻辑组合、52 份 TOML。DeepSWE 的 run_free 分为 Python 34 条和 run_free_multilingual 79 条，其他方法可选择全 113 条。Verified 示例选择前 10 条，删除 limit 可选完整集，已有 100 条子集组件也可替换 dataset.path。

```bash
source /Users/manyi/miniconda3/etc/profile.d/conda.sh
conda activate tokenAna
python tokenAna.py run experiments/method_matrix/agent_diet_mini_swe_agent_verified.toml --dry-run
# 只读检查整个矩阵；输出目录必须不存在。
python scripts/dry_run_method_matrix.py --output /absolute/new/plan-directory
TOKENANA_LOCAL_PROXY_TESTS=0 python -m unittest discover -s tests -q
```

模板中的端点、执行器、head、上下文上限均是显式配置示例，不能据此认为环境已就绪。当前宿主缺少 tiktoken/jinja2 等方法依赖，SWE-Pruner head 示例路径不存在；预检明确 blocked，不会装依赖或自动变成普通运行。未选方法不导入其模型、GPU 或 SDK 依赖。run/resume 的执行入口仍需要单独准备的 runtime 配置、Linux 镜像、通道和执行器。

## 公共能力与 agent 边界

Session 保留原 Method.run/Agent.run，并增加可选 run_session。initialize、before_model、after_model、after_tool、finish 通过共享产物目录同步交换；记录原始事件、返回历史、方法状态和实际采样输入。StepSummary 仅替换连续、完整且可编辑的步骤；系统/任务提示和协议字段受保护，工具调用/结果必须配对。摘要使用适配器构造的原生 assistant 消息，不直接伪造工具结果或完成状态。

- mini：手写子类在原 query/execute_actions 边界回调；保留原生循环、限额和完成检测。original-trajectory.json 保留追加原消息，压缩后 trajectory.json 与原始轨迹分开。
- Trae：手写客户端及 agent 子类，在原 SDK 请求前回写 message_history，在工具批次完成后更新方法；Lakeview 使用独立 agent_auxiliary 通道。原始 trajectory 的 llm_interactions 不因压缩而删除。系统经验后缀由 get_system_prompt 子类注入。
- OpenCode：依赖本地 session.mjs/accounting.mjs 插件；按原生主会话 pending assistant 判定模型边界，重试不重复回调，子任务/压缩请求另分类。上下文改动通过下一次 messages.transform 返回，原生数据库和 session 导出保留。after_model/after_tool 在下一次采样或 session.idle 时交付已完成批次；不是工具执行前拦截。
- Codex：只修改独立 controlled 副本，保留 upstream 和原 Responses caller。新增非默认 tokenana-session feature 与文件回调，原生 sampling/tool 批次结束后交付 after_model/after_tool。保留原 rollout，替换模型历史时保留未改原生 envelope 元数据。独立 native Chat Completions 客户端直接访问相同 SGLang /chat/completions，不使用代理翻译。

Codex 的 [session-chat.patch](../agents/codex/control-build/session-chat.patch) 独立于 turn-control.patch，涉及 11 个路径：采样接线、会话模块/feature、wire_api 枚举及本地 schema、HTTP client 分支、API 模块和新 Chat 客户端、仅测试用远端枚举处理。Chat 客户端处理普通/自定义/namespace 工具的可逆映射、SSE/非流式响应、失败和实际 usage；不支持的服务端专用工具类型明确拒绝。保留原 Responses 路径。构建入口顺序应用两个补丁并启用两个 feature，**本轮没有执行构建**。补丁适用性 dry-run 通过；新增 Rust 单测也未执行。

会话失败、缺失 hook/plugin、回调超时或未结束时，正式补丁不得提交，已有 diff 留作诊断。终止请求不伪装成成功完成。真实客户端的退出落盘时序、进程清理、网络门禁及协议端到端行为仍待真实组件验证。

## 方法行为

| 方法 | 接入行为与原规则 |
| --- | --- |
| run_free | 保留既定静态阅读/编辑限制；Python 与多语言版本按任务语言选择；复用 DeepSWE Git 提交要求。 |
| turn_control | 按原生主采样计轮，单会话一次 P50→P75：GPT 50→67、Claude 52→64、Gemini 29→45；原生较小限额优先。已开始中断不重新生成，诊断补丁禁止提交。original 均值仍为 null。 |
| SWE-Pruner Pro | 复用原 PrunerClient.prune、历史清理、KEEP_ALL、focus question、过滤标记及空剪枝提示。阈值 .5、最短 2000 字符；仅 Qwen3-Coder-Next 和现有匹配 head；主 Chat 地址与 hidden-state backend 必须是同一显式 SGLang 地址。 |
| AgentDiet | 复用原 MessageManager 和分析函数、prompt、触发及替换收益判定；前文 1、后文 2、阈值 500 token，gpt-5-mini 辅助请求；整个可编辑步骤替换为摘要。gpt-5 请求映射为 max_completion_tokens、low effort，宿主辅助通道保存每次真实请求。 |
| AttnCompress | 复用原动态 full 模式函数和历史恢复规则；/compress、ratio .20、保护末尾 2 步、只压缩工具结果。保留源函数服务失败回退，记录失败；拒绝服务修改工具协议或受保护文本。 |
| EET | 原现有经验库检索与模板；mini 按原工作流及 ≥81 分提示提交；Trae 原经验注入和原生结束；Codex/OpenCode 移植 mini 提示并使用其原生完成方式。DeepSWE 保留提交约定，不用 mini echo 标记代替原生结束。记录命中、未命中和未触发情况，不生成经验、不实现额外候选选择、不声称完整论文机制。 |

方法代码集中在各组件 adapter.py；src/source_declarations.py 仅从原文件选取未改写声明，在隔离命名空间提供显式依赖，避开原 CLI/GPU 顶层副作用。没有对已导入第三方模块做 monkey patch。跨 agent 的步骤序列化/完成提示是兼容映射，版本写入 method-state；这些版本不冒充作者历史结果或完全相同运行环境。

SWE-Pruner 使用新增 [service.py](../methods/swe_pruner_pro/service.py) 的 create_app(checkpoint=..., tokenizer_path=..., backend_base_url=..., hidden_size=...)，由预先准备的服务环境调用。它加载现有 head 和本地 Qwen3-Coder-Next tokenizer，依赖原剪枝算法，不训练 head。包装服务返回 tokenana-swe-pruner-recorded-v1 描述及 backend_requests 原始计数证据；未匹配 wrapper/backbone/head_model/backend 地址或缺少证据时明确失败。普通服务请求失败沿用原 raw-output 回退，潜在 backend 消耗标为未知。没有由 TokenAna 自动启动 GPU 服务。服务/head 的真实兼容性尚未验证。

## 统计、归属与恢复

记录 purpose、model、case/attempt/call/parent_call_id，区分 main、agent_auxiliary、method_auxiliary、compression_service、unknown。辅助方法在宿主显式配置通道执行，不获得隔离任务的额外网络权限；任务侧主模型仍复用数据集的独立模型通道。父调用关联到所属 agent call，不猜测无法观测的 provider response ID。

- original：选单次原生轨迹；run_free 保留非空 patch 筛选/整除均值；turn_control 只取原生最终摘要且 mean=null。四个新增方法使用作者取数意图的原生日志兼容投影，规则有版本后缀。AgentDiet 仅对实际观察的 APIStatusError 类型作作者 +200000 input 补偿，original overhead 的输入按每次 analysis 假定缓存 492 扣减；helper HTTP 失败不冒充该原生异常。AttnCompress 保留作者 common-prefix 字符数/4 的 original 缓存估计和作者为零的 analysis token counters；不把这些当实际服务 usage。SWE/EET 未报告的作者 overhead 为未知。
- corrected：重读所有保存尝试、失败、辅助请求和压缩服务的原始 usage。缓存、reasoning 是 input/output 内的分项，不再次相加。SWE hidden-state 请求逐次统计；Attn 未暴露推理 usage 时保持未知。压缩前后文本计数只保存在 effects。注入提示已经包含主请求 input，不另加；已有经验库历史生成成本未知，不摊入实验。
- overhead：input/output/total、cache read/write、reasoning、calls、service_seconds 均提供总和、统一实际调用 case 分母均值、完整性和原因。method_overhead 是 corrected all 的分项，不能再加一次。calls 包括 HTTP 尝试和服务操作；嵌套服务耗时相加不代表墙钟耗时。
- run/resume/analyze/compare 的 JSON、终端、CSV、Markdown 同步输出。恢复从 records 重建并按稳定身份去重；不把旧累计再加一遍。旧记录缺失归属/usage/作者状态时保留不完整，不能推断为零。

DeepSWE 原任务允许 7–40 位 Git SHA；任务适配器保留原值，进入预备仓库时用 rev-parse --verify 解析并与 HEAD 比较。/app 的原 collect hook 采集 base..HEAD 的已提交补丁；未提交差异和 untracked 文件名另存诊断。Verified 沿用工作区 git diff。原数据、评测器与 collect 命令未改写。

## 验收与清理

125 项 TokenAna 离线测试：119 通过，6 项本地网络测试关闭而跳过。涵盖源方法触发/收益/失败、EET 80/81、无经验、协议保护/多工具/摘要、mini 和 Trae 手写边界、OpenCode 实际本地插件的模拟客户端、三组预算及重复扩展拒绝、归属/缺失 usage/缓存/恢复、DeepSWE 短 SHA/语言筛选/空补丁/捕获失败。Codex 原生预算测试读取模拟 journal，**不代表 Rust hook 已执行**。

矩阵测试对 48 个逻辑组合及 4 个多语言配置加载真实适配器/规划器/原生统计 reader，以模拟 trace/usage 重建 original、corrected、overhead，验证导出/比较与恢复不重复。单项方法算法及各 agent 手写边界另做模拟；不是 48 次真实 agent 端到端执行。52 份 CLI dry-run 见 [规划证据](../runs/offline-method-matrix-20260920/summary.json)，全部 plan_only/runtime_ready=false，缺依赖/head 的预检阻塞为预期。

原源码复制单独执行：四方法/三 agent/DeepSWE 副本已有有效文件；turn_control 剩余源码、配置及结果归档完整移入 methods/turn_control/upstream。离线验收后，删除根目录 agentDiet、AttnCompress、EET、swe-pruner-pro、mini-swe-agent、trae-agent、opencode、turn_control、deepswe_data 九个重复目录。未做哈希核验、来源清单或 provenance。运行入口只依赖组件目录；历史第三方归档中保留作者原相对路径文本。

没有安装依赖、下载、构建、启动容器、监听模型服务、真实模型/API/GPU 调用、远程执行或 Git commit。Rust 编译/格式化/原生 Rust 测试、真实组件连接假服务、Linux/Docker、GPU、head 加载、模型效果与真实评测均未执行。上述范围以外不报告验收通过。

清理后复查结果与测试日志：[acceptance.json](../runs/offline-method-matrix-after-cleanup-20260920/acceptance.json)、[tests.log](../runs/offline-method-matrix-after-cleanup-20260920/tests.log)。52 份清理后规划保存在同目录；没有新增运行环境就绪声明。
