# TokenAna 首版架构

## 六方法原始 token 口径复查（2026-09-20）

本轮按用户“最后再检查一遍”静态阅读本地原源码、原分析脚本与现有兼容统计；未运行实验、测试或模型，未修改统计公式和第三方源码。结论不应收窄为“缓存加减错误、未提交 patch 的 case 漏算”，也不能认定六方法都存在这两项：

- 样本筛选须准确描述：当前 run_free original 要求 trace 存在且原 patch 非空，不要求评测通过或实际提交成功（`methods/run_free/accounting.py:16`）；turn_control 原脚本要求最后摘要有 function_calls，不按 patch 筛选（`methods/turn_control/upstream/extract_function_calls.py:219`）。AgentDiet exporter 和 AttnCompress analysis 均纳入已有日志中的失败结果，不能称其统一丢弃失败 case。
- 同一 case 的早期尝试也可能漏算：turn_control 原脚本按最大 try_num 选一个文件，Gemini 只解析最后 current_try（同文件第 31、195 行）。这与整个失败 case 被过滤不同；当前 TokenAna turn_control 已约定单次尝试，不把历史多尝试问题说成本轮必然发生。
- 主轨迹不等于全部推理开销：SWE-Pruner 原统计仅读主响应 usage，剪枝服务另有 SGLang `/generate` 请求（`methods/swe_pruner_pro/upstream/utils/stats/swebench.py:44`、`src/swe_pruner_pro/serving/pruner_server.py:117`，后一路径相对于该方法 upstream）；AttnCompress attention 分支不更新 analysis token counters，服务 old/new_tokens 是文本压缩量；EET 的 Trae 来源启用 Lakeview 时另有辅助调用及格式重试，不进入主 execution 的 token 累加。AgentDiet 已单列辅助开销，不能称其完全没有统计 overhead。
- 非实测值与未知混淆：AgentDiet exporter 对 APIStatusError 固定给 prompt_tokens 加 200000，辅助输入按每次 analysis 假定缓存 492 扣减（`methods/agent_diet/upstream/result/exporter.ipynb:84`、`:207`）；AttnCompress 用公共前缀字符数/4 估算缓存（`methods/attn_compress/upstream/code/trae_agent/agents/expert.py:625`）。SWE-Pruner 缺 usage 时跳过/字段默认零，EET 的 Trae recorder 无 usage 时写 input/output=0；这些都不等于确认实际消耗为零。
- 聚合/展示另有差异：run_free original 均值整除且只以纳入 case 作分母；AgentDiet 分语言 `export_cat` 的 O 比例除以 baseline input，而非 baseline output（上述 notebook 第 727 行），不能解释为输出 token 相对基线比例。该局部报表问题不影响其他表的所有统计，也未被本轮改动。

reasoning 包含关系、累计快照去重和历史经验生成成本仍须按既定 corrected 规则处理；此次没有证据把它们统称为六方法均已发生的错误，也未量化各问题对真实结果的影响。新 agent 的 original 是已标注版本的兼容投影，不能冒充作者历史实验的完整复现。

## DeepSWE 可筛选试跑入口（2026-09-20）

用户已明确授权实现默认／筛选启动入口，并自行启动真实实验。本批新增 `scripts/run-deepswe-pilot.sh`，配置接入、52 个有效组合、固定 Python 清单、并发／恢复、选定镜像准备、生成→独立评测→双口径汇总。排除 AttnCompress 和 SWE-Pruner Pro；不再以旧 65 组合预检作为当前入口状态。用法和限制见 [deepswe-pilot-cli.md](deepswe-pilot-cli.md)。

主录制通道转发前保存工具批次后的采样边界增量快照；完整任务／verifier 容器停止后导出，验证归档可读后才删除。补充追加式 timing.jsonl、方法状态历史、控制 hook 开销、流式日志；original/corrected 公式不变。Mac 明确放宽 storage-opt，内存/CPU不放宽。新预算表来自已确认的官网基线迁移。Codex 只扩展 TokenAna 自有 hook（新增 deepswe-pilot.patch），未改 upstream；原 agent/method/dataset/evaluator 来源保持不变。

132/138 项离线测试通过，6 项本机 socket 测试跳过；单组合及默认 52 组合 dry-run 已检查。真实服务、Docker 镜像与 Rust/Bun 构建尚未执行，由用户首次启动验证；不能据 dry-run 宣称真实链路已跑通。本机 Docker 原分配不足 8.5 GiB，需用户提高 Docker Desktop 内存。没有安装本机依赖、运行真实模型或提交 Git。

## 六方法工作线：剩余实现与离线验收完成（2026-09-20）

用户授权“剩下的一次性完成吧，不用问我了”，本工作线已完成六方法/四 agent/两数据集的兼容接入、双口径和 overhead 报告，并在离线验收后清理九个根目录重复来源。详细实现、兼容边界、Codex 独立补丁和使用入口见 [交付文档](method-integration-delivery.md) 与 [配置矩阵](../experiments/method_matrix/README.md)。本节覆盖下方本工作线旧的“仅源码/未接入/下一批等待”状态；其他任务的 Mac 真实实验需求和授权保持独立。

125 项离线测试中 119 通过、6 项网络测试按约定跳过；48 个逻辑组合及 4 个多语言变体完成配置/原生统计夹具/双口径导出与恢复测试，52 份 CLI dry-run 为 plan_only。DeepSWE 全量读取的短 SHA 阻塞已修复，原数据及 collect hook 不变。新 agent 的方法接入均标明兼容版本。Codex/Trae/mini/OpenCode 的实际进程、Rust 构建、GPU/head、真实 API 和效果仍未验收；不能据此认为下方 Mac 65 组合预检已就绪。

## Mac 单条 DeepSWE 先期实验预检（2026-09-20）

用户本次确认同一条数据跑五方法（排除 SWE-Pruner Pro）×〔Codex 仅 gpt-5.6-sol，其余三个 agent 各四模型〕，共 65 个组合；四模型为 gpt-5.6-sol、claude-opus-5、deepseek-v4.1-flash、qwen3.8-max，并明确允许本次真实 API。先不扩到 20 条。已建立 `config/local/deepswe-pilot/` 供用户填写端点与密钥；它是待接线的本地配置收集表，不能直接运行。

实际只读预检未通过：Docker daemon 无法连接；隔离通道要求 Linux；单条任务枚举被未选任务的 7 位 commit 校验阻塞；方法/会话接入、Qwen 映射、CUDA 压缩服务和预备镜像尚不具备完整矩阵验收。现有双口径/overhead 框架不能代替完整 case 分阶段计时和容器文件留存，当前删除容器会丢失未挂载文件。当前代码还在出现更新的组件实现，后续历史“仅原源码”描述不能覆盖该工作区快照。

本批未修改执行器或第三方源码，未安装、下载、构建、启动容器、调用模型或提交 Git；0 个组合执行。详细证据、留存/计时要求及后续批次见 [Mac 先期实验预检](deepswe-mac-pilot-readiness.md)，结构化记录在 `runs/preflight-deepswe-mac-20260920/`。真实 API 许可仅适用于本次明确范围，不自动批准原源码补丁或其他实施批次。

## 六类方法与 overhead 工作线（2026-09-20）

本工作线按用户批准的计划分批交付，与下文 DeepSWE 工作线并行衔接。**第 1 批完成源码准备；第 2 批完成公共会话契约、调用归属和 overhead 报告扩展，已做离线验证。mini 的通用会话接线已于第 3A 批实现并通过模拟验证；新增方法与其他 agent 的通用会话接入仍待实施。** DeepSWE 的已有进度及未验证边界保持下文记录。

四个来源分别复制到 `methods/swe_pruner_pro/upstream/`、`methods/agent_diet/upstream/`、`methods/attn_compress/upstream/`、`methods/eet/upstream/`。保留原目录结构、源码、配置、许可（来源已有部分）、经验库及结果归档；排除 `.git`、`.DS_Store` 和 Python/工具缓存，不解包结果归档，不修改原方法源码。根目录原件保留。新增目录目前没有组件 manifest 或适配器，不能作为可运行方法选择。

后续目标为六类方法（run_free、turn_control、SWE-Pruner Pro、AgentDiet、AttnCompress、EET）× 四个 agent（Codex、mini、Trae、OpenCode）× 两个数据集（Verified 含子集、DeepSWE），每种组合提供 original、corrected 与 overhead；run_free_multilingual 计入 run_free 的语言变体。

已批准的设计约束：可选会话回调保留现有 Method.run/Agent.run 接口；适配层映射原生事件并保护协议字段及工具配对；mini/Trae 优先子类、OpenCode 插件、Codex 独立补丁先展示具体差异，原始副本不改。Codex 后续增加原生 Chat Completions 路径以连接同一 SGLang backend，保留 Responses。缺少执行器、能力、服务或 head 时预检报错，未选方法不加载依赖。

方法配置约定：SWE-Pruner Pro 仅 Qwen3-Coder-Next 与已有匹配 head，阈值 0.5、最小 2000 字符；AgentDiet 辅助模型 gpt-5-mini，前 1 步/后 2 步、触发 500 token，保留原收益判断；AttnCompress 原动态设置比例 0.20、保护末尾 2 步、仅工具结果，保留失败回退并记录；EET 严格源码兼容，mini 的 ≥81 分提交提示移植至 Codex/OpenCode，Trae 保留经验注入和原生结束，不补造论文机制、不生成新经验。新 agent 接入均标注兼容版本。turn_control 保持主循环 LLM 计轮、同会话一次 P50→P75 扩展及现有恢复/诊断补丁限制。

统计约定（第 2 批已实现公共记录/聚合/报告，方法特有 original 规则待对应方法批次）：按 case/attempt、父调用、模型及用途区分主任务、agent 自带辅助、方法辅助和压缩服务操作。original 保留作者筛选/聚合及已有 overhead，AgentDiet 缓存假定与错误补偿仅属于 original，turn_control original 均值仍为 null。corrected 重算所有尝试、失败及辅助调用的原始 usage；overhead 是总量分项，不重复累加。input/output/total、cache read/write、reasoning、调用次数和服务耗时提供总和、统一 case 分母均值及完整性原因。SWE 额外 backend 请求计入；Attn 未暴露实际推理消耗时标未知，文本压缩量不能替代 usage；提示注入不重复计费，历史经验生成成本未知。run/resume/analyze/compare 的 JSON、终端、CSV、Markdown 同步扩展，恢复从记录重算。

验收目标为 48 种组合及各自双口径，覆盖上下文替换、多工具配对、边界与失败、usage 缺失及恢复、两数据集补丁语义和旧结果回归。本工作线只允许获批批次的离线测试、模拟与 dry-run；第 2 批验证见下节，不改变 DeepSWE 独立工作线的历史未验证记录。真实组件、GPU 服务、模型效果及 Rust 编译均未验收。完整离线验收后才单独清理符合迁移条件的根目录重复目录。

### 第 2 批：公共会话、归属与 overhead（已交付）

- `src/session.py` 提供 initialize、before_model、after_model、after_tool、finish 回调，返回历史替换、方法状态、提醒和终止请求。`SessionAgent.run_session` 为可选扩展，经 BoundAgent/RecordingAgent 传递，原 Method.run/Agent.run 不变。方法声明 required_session_capabilities；预检及调用边界拒绝缺失能力。目前四个真实 agent 均未声明这套通用会话能力；已有 turn_control 专用接线不等于通用接入。
- 公共 Message 仅暴露可编辑 text；native、角色、消息/工具 ID、工具参数、签名及不透明内容由适配器保存在不可改字段。替换不得新增/重排原生消息或删除受保护消息；删除可编辑步骤必须保留工具配对，下一次采样前不能存在缺失结果。多工具结果可按返回顺序出现。回调输入深拷贝，不能通过原地修改绕过校验。会话保存事件前后快照、失败类别和方法状态；原轨迹仍由 agent 原生保存。
- 原生适配器后续须使用返回历史、注入提醒、遵守终止控制，并在真正发送前调用 record_model_input 保存最终请求载荷。公共层不会改写 HTTP 上下文；本批只用模拟适配器验证这些契约，不宣称真实 agent 已能压缩。
- calls.json 升为 v2，保存 case/attempt/call 关联；旧 v1 仍可重建。HTTP metadata 增加显式 attribution、请求 model 和单次 elapsed duration；每次请求均保留独立 UsageOperation，usage 缺失也不丢失请求次数。main、agent_auxiliary、method_auxiliary、compression_service、unknown 由适配层显式提供；当前未分类真实路径保持 unknown，不猜测 Lakeview/标题/子任务归属。
- 主通道沿用 api-records；辅助通道通过 recording_model_channel 的唯一 channel_name 使用 auxiliary-records，并要求调用方显式传入服务地址，复用原隔离通道。每个辅助通道有独立就绪/停止文件。辅助通道不改变主模型配置，不默认路由外网。record_service 增量保存压缩操作、失败及实际服务 usage；effects 中的文本 token 变化只供诊断，不参与消耗。inference=False 仅供推理已经由独立通道记录的编排操作，不能再写 raw_usage，以免重复计费。
- overhead-v1 是 corrected-v1 的分项投影：main、agent_auxiliary、method_auxiliary、compression_service、unknown，以及重叠汇总 method_overhead 和 all；不得把分项再加到 all。每项提供 token 六字段及可得细项、calls、service_seconds 的总和/均值/完整性/原因，均值使用同一实际调用 case 分母。calls 是记录到的 HTTP 尝试及服务操作数量，不是 agent 轮数；service_seconds 是各操作耗时之和，可含嵌套调用时间，不是实验墙钟时长。缺失用途/记录保留未知和已知小计；完整性受录制覆盖限制。
- original token 公式保持不变；方法后续可在 original_accounting 结果的 overhead 节提供作者分项，框架独立展示，不套用 corrected 规则、不填造零值。目前作者 overhead 未提供时显示未统计；AgentDiet 特有缓存/错误补偿和其他新方法规则仍待方法批次。turn_control original 均值继续 null。
- run/resume/analyze 从全部保存尝试重建主、辅助和服务记录，稳定 identity 去重并拒绝冲突；新增 overhead.csv、overhead-cases.csv，JSON、终端与 Markdown 同步展示。compare 输出同样分项及 overhead.txt，保留各运行分母，不推断跨口径 overhead 节省率。旧报告无 overhead 时提示重新 analyze；正在运行的快照统一标记不完整。新字段为兼容性追加，报告 schema_version 保留 1。
- 验证：tokenAna Python 3.12.14 下 88 项测试，通过 82 项、跳过 6 项需独立开启的本地网络测试；新增 16 项为会话/overhead 模拟测试，覆盖状态/终止、协议保护、多工具配对、历史实际回写、辅助通道隔离、失败与未知、作者口径分离、去重重建和导出比较。同步修正四份旧测试夹具，使其匹配此前已交付的 Git 提示、多协议及可选 Workspace 接口。10 条 Verified dry-run 为 plan_only、runtime_ready=false。无真实 agent、端口监听、模型调用、容器、依赖安装、构建或 Rust 编译；48 组合矩阵仍未验收。

### 第 3A 批：mini 通用会话兼容接入（已交付）

- mini 新增可选 run_session 及 mini-session-compatible-v1，普通 run 和原 turn_control 入口保留。通过原 CLI 的 --agent-class 加载手写子类，不修改 upstream。仅该路径要求镜像内绝对 python_executable、record_raw_usage=true、受支持且显式配置的模型协议/地址；缺项在公共预检及调用入口拒绝。其他 agent 尚未实现本工作线通用会话能力。
- 新增 src/session_channel.py，在既有共享产物目录中以同步 JSON 请求/响应传递事件与决策；不新增网络端口、不序列化 Python callback 或在容器执行宿主方法依赖。宿主执行公共 Session 校验，worker 同步等待，超时/失败/未完成均使 session outcome 不完整并禁止提交。主模型 recorder 以会话初始化为就绪门禁，缺失子类不能退化成普通模型运行。宿主 callback 应自行限制辅助操作耗时；通道超时不代表能抢占正在执行的 Python callback，真实异常清理尚未验收。
- 初始化发生在原系统/任务消息创建后的首次 query；before_model 在原生主循环边界执行，after_model 覆盖返回/异常，after_tool 在原 execute_actions 输出批次（含未执行工具占位）写入后执行，finish 在原 run 结束时执行。没有重新执行工具或复制原工具循环；同轮多个工具依旧一轮。已有原生 step/cost/wall-time 限制优先，预算已尽不额外触发压缩回调。
- session_mapping 为原生消息分配稳定 ID；Chat/Anthropic 的 content 字符串及 Responses function_call_output.output 字符串可回写。工具 ID、参数、extra、Responses envelope/usage、推理签名及多模态等不透明结构原样保留；不透明内容不能用普通文本替换。初始系统/任务和 exit 受保护；已完成步骤可以成组删除，未完成工具调用不得被回调删除，下一次采样前必须配对齐全。after_model/after_tool 的提醒延迟到下一次 query，避免插入未配齐的工具结果之间。
- worker 将最终 prepared_messages 保存到 session 模型输入快照；它是原生模型边界的输入，SDK 进一步转换后的实际线请求仍以 api-records/*/request.body 为准。callbacks 的历史替换实际赋回 self.messages。trajectory.json 保留当前压缩历史，original-trajectory.json 用追加留存的原生消息及原序列化元数据保存未压缩证据；会话 reader 与 tokens_used/exec_count 读取后者，不能因裁剪历史漏算。缺少原轨迹不得退回压缩轨迹。session-version.json 标记兼容版本和证据位置。
- mini 原生模型通道显式标记 purpose=main；后续方法辅助请求仍须使用独立辅助通道，不能借此混入 main。original/corrected 公式未改；EET/AgentDiet 等方法特有规则仍待各方法批次。组合 ControlledSessionMini 复用现有 ControlledMini，同会话最多一次扩展；最终工具计数从未压缩记录计算，压缩不会减少控制/统计证据。
- 提交条件：会话完整、无错误、原生 Submitted 且公共捕获补丁非空。回调终止仅记录 MethodTerminated，不伪装成原生完成；其补丁保留 diagnostic.diff、正式 patch.diff 清空。EET 的阈值提示后续应使用 reminder 引导原生提交，不通过 terminate 冒充成功。
- 验证：tokenAna Python 3.12.14 下 97 项测试，通过 91 项、跳过 6 项本地网络测试；新增 9 项为 hand-written mini 层/文件通道与模拟原生循环的离线测试，覆盖上下文回写、原轨迹与双口径证据、多工具、提醒、终止、限额、回调错误/超时、缺失子类门禁、turn_control 组合及 /app、/testbed 补丁交接。mini 10 条 Verified dry-run 成功，plan_only/runtime_ready=false。没有导入/启动真实 mini/LiteLLM、容器、端口或模型服务，不代表真实组件/完整矩阵验收。

## 状态与目标

最新工作线：DeepSWE 第 6 批已按“一次性做完”完成剩余静态实现：四个 agent 的三种方法路径、20 份实验配置、同集合比较、双口径分析和本地评测接口均已接线。本次授权内没有待实施批次；控制版 Codex 未构建，所有新增运行兼容性仍未验证。完整边界见 [deepswe-delivery.md](deepswe-delivery.md) 和 [实验矩阵](../experiments/deepswe/README.md)。

最新补全（2026-09-19）：按用户“剩下的一次做完”授权，已实现下列剩余适配与报告入口；本文后续批次段落是历史记录，旧的“未实现/下一批”描述以本段为准。使用入口和支持矩阵见 [usage.md](usage.md)。本批仅静态阅读，未写/跑测试或执行组件，不代表运行验收通过。

- 新增 `src/control.py`、`methods/turn_control/` 和 Trae 手写控制 worker：原版类的子类钩子实现同会话 P50→P75 一次扩展，记录预算、轮数、终止原因和诊断 diff；仅有效完成且非空的补丁进入提交。开始后中断不自动重新生成；已保存完整调用结果可恢复收集。原组件源码保持不变。
- 用户明确确认：turn_control original 沿用最终摘要纳入规则的原生 Trae 兼容统计，只保留 input/output/total 总和，所有 mean 为 `null`，不新增兼容均值。corrected 继续使用独立原始 HTTP usage；兼容规则不冒充历史复现。
- Trae 增加 Anthropic Messages、DeepSeek/DashScope Chat Completions 和 DashScope Responses 显式映射，选定客户端依赖由适配层注入；Anthropic 系统提示添加原生请求支持的缓存标记。unsupported 组合明确拒绝，非 OpenAI 和控制 worker 需要镜像内 `python_executable`。预算组 Gemini 不代表已经实现 Google 模型协议。
- 新增原 100 条 Verified 子集组件、turn_control 配置，以及显式 JSON 输入的 `repository-tasks` 通用仓库任务适配器；后者复用预备 `/testbed` 工作区，没有擅自指定评测器或下载新数据集。
- 新增 `evaluate submit/fetch/attach`，默认只展示保存的命令，执行须显式 `--execute`；保存远程动作日志，防止不确定提交自动重试。报告关联检查 run ID、任务 ID 和计数，缺失结果保持未知；analyze/compare 增加逐 case resolved、控制状态、evaluation.csv，以及 selected/completed 两种明确分母的解决率。
- corrected 补充 DeepSeek cache hit/miss 和 Chat 音频/预测细分字段，不重复加到总量；Anthropic 流必须观察到 output usage 才认为输出完整。

尚未验收：真实 agent、协议与缓存行为、进程清理、Linux 镜像和远程评测服务。模型地址、OpenCode context/output 上限及只读运行配置仍须按实际环境填写；未安装、构建、下载、启动容器或调用模型。Codex 非 Responses、mini/OpenCode 的 DashScope Responses 等未支持组合仍不在兼容范围。

当前已完成原文件准备、配置与显式组件选择、run_free/Codex/Verified 适配、只读 dry-run，以及从容器准备、源码版 Codex 调用、原始产物保存到原 `sb-cli` 预测与提交命令生成的串行运行链路。任务边界恢复、损坏产物拒绝和未选中组件隔离已有模拟验证；新增 run/resume 双口径自动汇总和离线 analyze/compare 尚未测试。真实 Linux 容器、源码构建、假模型服务和远程提交尚未执行。
目标是在不修改原源码的前提下接通：原 run_free → 本地 Codex → SWE-bench Verified → 官方评测器。
TokenAna 接管实验调度，直接复用原方法；不要求继续执行原 runner 的整条调用链。
首版使用 Python 3.11+ CLI 与 TOML，真实组件验收平台为 Linux x86_64。
首版保证兼容的 Python 仓库修复任务可扩展；不支持的 method/agent/dataset 组合明确报错。

## DeepSWE 接入

用户已确认以下设计；本节区分目标与已交付状态，不将计划记为现有能力。

| 方法 | 任务范围 | 目标 agent |
| --- | --- | --- |
| run_free | 34 个 Python 任务 | Codex、mini、Trae、OpenCode |
| run_free_multilingual | 79 个非 Python 任务 | Codex、mini、Trae、OpenCode |
| turn_control | 全部 113 个任务 | Codex、mini、Trae、OpenCode |

数据还包含 Go 34、TypeScript 35、JavaScript 5、Rust 5。各 agent 的模型协议限制继续适用，接入目标不等于全部模型组合已支持。

### 数据、环境与评测约定（第 2、3 批已接线，运行未验证）

- 从任务 metadata 的 task_id、repository_url、base_commit_hash 及原 instruction.md 映射四个公共 Task 字段；保留任务中的分支和提交指令。按任务 ID 排序，先按语言/显式 ID 筛选，再应用 limit。隐藏测试、参考补丁和评分配置不进入 agent 输入或挂载。
- 工作区为 `/app`，分别使用用户已准备的 agent/verifier 镜像；遵循原任务的资源和超时配置。运行入口不安装、下载或构建，现有 Verified `/testbed` 路径保持不变。
- 公共补丁捕获接口由四个 agent 调用，执行任务原收集命令 `git diff --binary <base_commit> HEAD`，保存 model.patch 并关联当前调用。收集在 agent 停止后、容器删除前完成；已提交修改不能被普通 git diff 误判为空。未提交修改只作诊断，不由框架代为提交，收集失败与空补丁分开处理。
- 新增本地评测计划和显式 kind/run_id，计划入口为 `evaluate <run> local [--execute]`；通过薄适配层调用原 Pier Verifier 和任务原评分脚本，保留原始 reward、CTRF 及日志。reward 1/0 分别映射解决/未解决，-1、超时或缺失报告保持 resolved=null；仅恢复评测不重新生成。现有远程评测入口保留。
- agent 和 verifier 保持无普通网络访问；仅 agent 配置回环端口→挂载 Unix socket→宿主 usage 代理的模型通道，字节透明转发。公共预检新增隔离通道能力，现有 host 模式保留；该通道第 3 批已接线，尚未运行验证。

### 方法、控制与统计约定（第 6 批静态接线完成，运行未验证）

- run_free 在适配层取消禁止 Git 的条目，保留原源码及其他 Python 限制；数据集的公开提交说明经公共绑定层追加到最终提示词，解释 DeepSWE 使用 base_commit..HEAD。多语言方法禁止各语言测试、构建、程序执行及安装，允许文件操作和 Git；保存实际提示词与方法版本。上述提示词行为变更在第 3 批应用，原源码保持不变。
- turn_control 在同会话按主执行循环 LLM 交互计轮，采样前提醒，仅一次 P50→P75 扩展；最终未完成不提交、不重新生成。预算沿用 GPT 50→67、Claude 52→64、Gemini 29→45，明确不是 DeepSWE 实测分位数，不授予 Google 协议支持。
- Trae 延续现有子类逻辑；mini 新增 query 边界子类；OpenCode 使用本地消息转换 hook；Codex 采用可选采样边界 hook 的独立构建补丁。原始 Codex 副本保留，具体补丁与构建入口须单独展示、review 后应用；不用 HTTP 请求数近似替代 LLM 轮数，不使用 monkey patch。
- run_free 两版本沿用非空补丁筛选、原生日志及向下取整均值，明确 DeepSWE 补丁约定与兼容版本；turn_control 新增 agent 无关的最终摘要接口，沿用摘要纳入规则，original 只报 input/output/total 总和，均值仍为 null。corrected 复用 corrected-v1 与原始 HTTP usage，保留全部尝试、失败和辅助消耗，恢复重建而非重复累加。
- turn_control 的比较配置分别选择 Python 34 条和非 Python 79 条，与对应 run_free 实验使用相同任务集合；不把不同任务集合的均值直接换算为节省比例。

### 第 1 批交付与边界

- `deepswe_data/tasks/` 原样复制到 `datasets/deepswe/data/tasks/`：1,134 个文件条目（含 1 个符号链接），普通文件合计 30,453,862 字节；包含 113 个任务及原包附带文件。
- `deepswe_data/pier/` 原样复制到 `datasets/deepswe/upstream/pier/`：246 个普通文件，合计 3,132,579 字节；包含原许可文件、依赖声明与原测试源码，未运行其中任何代码。
- 保留目录结构、权限和符号链接，排除 `.git`、`.DS_Store` 与缓存；根目录来源保留。原任务包 README.md 指向缺失的 `../README.md`，该断链原样保留，未补造或重写来源文件。
- 本批仅复制与文档同步，没有新增适配代码、manifest、配置或测试，没有改动原方法、agent 或统计规则，没有来源核验、哈希比对或 provenance 记录。复制完成不代表 DeepSWE 可运行。
- 下一批为任务映射、工作区、公共补丁捕获和本地评测入口；批次安排见 [development-workflow.md](development-workflow.md)。后续实施仍逐批授权，不写、不跑测试，所有运行验收保持未验证。

### 第 2 批交付与边界

- 新增 manifest、tasks/adapter、workspace、evaluation 和 verifier_worker；任务筛选使用小写语言名称，显式 ID 与语言取交集，按 ID 排序后截取。公共 Task 只包含四个公开字段，agent 不挂载任务包、隐藏测试或参考解。
- `src/patches.py` 提供数据集可选捕获接口，四个 agent 的手写适配层接入；普通工作区保留 git diff，DeepSWE 原始 model.patch、兼容 patch.diff、未提交 diff、未跟踪文件列表及捕获标记关联当前调用。缺失标记与成功捕获的空补丁分别处理，不从文本回答补齐。任务超时限制通过公共执行时间上限传入四个适配层。
- 公共 SubmissionPlan 增加 kind/run_id；Verified 新计划显式保存 run_id，旧计划仍支持从原命令读取。local 默认只读计划，显式 --execute 才创建离线 verifier；不触发生成或重新收集补丁。
- worker 在预备 verifier 镜像内导入未修改的 Pier Verifier，保留 reward、CTRF、原日志与执行记录。reward 1/0 映射布尔结果，其余奖励、缺失报告及执行异常为 null。每个任务完成后保存检查点；再次显式执行跳过已完成且补丁一致的任务，重试异常任务。先保留全部已完成结果，再开始重试，避免再次中断丢失后续任务结果。
- 镜像需预装依赖，verifier 以 root 运行 Python 3.12+，包含 Pier distribution metadata 和任务原测试；agent 需 GNU timeout。Docker 必须支持 storage_mb 对应的可写层配额，不支持则失败；挂载日志不属于该配额。模型通道尚未接入，validate_runtime 明确拒绝 DeepSWE 生成。
- 仅审阅代码与文本差异；未执行导入、语法检查、测试、dry-run、构建、容器、模型或评测。超时后的进程清理、挂载、资源约束和原 Pier 运行均未验证；静态接线不代表运行验收。下一批为提示词 Git 调整、多语言方法和隔离模型通道。

### 第 3 批交付与边界

- Python `RunFree` 在适配层从原 prompt 移除 Git 禁令，其余 Python 限制与原始源码保留；版本为 `run-free-git-v2`。该行为影响所有使用当前 run_free 适配器的新运行，不能当作原版实验复现。新增 `run_free_multilingual`（`run-free-multilingual-v1`）继承相同原生 token 聚合规则，以静态阅读、推理和编辑为策略，禁止各语言的测试、构建、程序执行、依赖安装，允许文件操作与 Git。限制是提示词策略，不是命令沙箱。
- DeepSWE 根据方法的语言声明校验已选任务：run_free 仅 Python，多语言版本仅其余四种语言；不会自动替用户改变任务集合。规划和执行共用校验。turn_control 的四 agent 目标仍待后续批次完成，当前能力检查继续生效。
- `src/prompts.py` 在 BoundAgent 最终绑定处追加工作区公开提交说明，明确 base_commit..HEAD 覆盖旧的 plain git diff 要求，且不放宽所选方法执行限制。每次调用保存 final-prompt.txt、prompt-metadata.json；运行 state 保存 method_version，恢复拒绝不同版本，旧无版本运行仍可离线分析，不混用更新后的 run_free 继续生成。
- `src/model_channel.py` 组合既有 usage proxy 与工作区可选通道；`src/byte_relay.py` 在两端只转发字节：agent 回环随机 TCP 端口 → 只读挂载目录内的 Unix socket → 宿主回环 usage proxy → 明确配置的模型地址。中继固定目标，无请求解析、改写、重试、协议转换或任意目的地址转发；原 proxy 的协议和 usage 行为保持不变。
- DeepSWE 仅向 agent 容器挂载每任务临时 socket 目录及只读中继源码；verifier 没有该挂载、模型通道或 agent 运行时凭据。容器仍为 network none，宿主中继仅连本次 recorder。每调用保存就绪信息/日志，关闭通道时停止中继、关闭连接，失败由调用记录与后续容器清理处理；连接、并发流、信号和超时清理均未运行验证。
- 预检同时支持原 host 模式和声明支持的 unix_socket 隔离模式。DeepSWE 要求 record_raw_usage=true、Linux 同机 Docker、model_channel.python 指向镜像内现成 Python 3；缺少配置或能力时拒绝启动。运行入口不安装 Python、依赖或镜像，通道启动也不调用模型。
- 未改 corrected-v1 或 original 筛选/整除均值公式，四个 agent 只改手写录制上下文接线。未写/跑测试、语法检查、dry-run、导入检查、构建、网络监听、容器或模型；本批运行兼容性保持未验证。下一批按 agent 分别 review mini/OpenCode 的 turn_control 及原生摘要。

### 第 4A 批：mini 控制与公共最终摘要

- 新增 `agents/mini_swe_agent/controlled_runner.py`，通过原 CLI 的 `--agent-class` 加载 InteractiveAgent 子类。只在 query 边界提醒和检查预算，沿用原生 n_calls 计数：一次主循环模型交互一轮，底层 HTTP 重试、同轮多个工具调用不另计轮。原模型、环境、工具、Submitted 完成判断和轨迹序列化均保留；没有 monkey patch 或第三方源码改动。
- 初始预算用尽且原循环还需要 query 时，同会话扩展一次到最终预算；在最终预算后的下一次 query 前抛出原异常体系的 TurnBudgetExceeded，原循环保存退出轨迹。不新增会话、不重新生成。原费用、step_limit、wall_time 和连续格式错误限制仍优先有效，可能提前停止；不会为用满 P75 而放宽原限制。
- `control.json` 记录 mini-query-boundary-v1、预算、已用轮数、扩展和终止原因；采样前保存请求轮次，返回后以原生 n_calls 校正。适配层将控制记录与原轨迹 api_calls/Submitted 核对，只允许无错误、原生完成且公共捕获补丁非空的结果提交。诊断 diff 和 DeepSWE 原 model.patch 保留，未完成的正式 patch.diff 清空且 submission_eligible=false。
- `FinalSummary` 为 agent 无关字段；turn_control 通过 read_final_summary_case 读取，而非匹配 trae.summary 事件。Trae 读取器迁移原结束时间、工具计数及既有 token 投影，控制行为不变；mini 从原生最终 exit、动作列表、model_stats.api_calls 和逐消息 usage 构造摘要。
- 纳入规则仍为最终摘要存在且有函数调用计数，不按成功、补丁或评测结果筛选，只报 input/output/total 总和、mean=null。mini 原生 usage 缺失或响应数与 api_calls 不一致时标记未知/不完整，不能把缺失消费计成零；明确零次调用与缺失计数分开。run_free 继续使用自己的既有 reader 和公式，corrected-v1 不变，失败及重试仍从原 HTTP 记录重建。
- turn_control 版本为 turn-control-native-summary-v2；沿用方法版本恢复检查，无版本旧运行可离线分析，但不能混入新版继续生成。mini 控制路径需要镜像内 python_executable 能导入当前 mini 包及其依赖，普通 mini CLI 路径不启用子类。预算仍为沿用档位，不是 DeepSWE 实测分位数。
- 仅静态阅读子类边界、原 CLI 扩展点、格式错误/完成处理、摘要与恢复接线；未写/跑测试、语法检查、dry-run、导入项目模块、容器、模型或评测。原生运行兼容性、信号/超时清理与协议行为未验证。本批结束；下一批单独处理 OpenCode 控制插件及摘要。

### 第 4B 批：OpenCode 控制插件与原生摘要

- 新增本地 `agents/opencode/turn_control.mjs`，仅使用 Node 内置模块，由原插件加载器通过 file URL 加载；无需插件依赖安装。普通运行的 plugin 列表仍为空，控制运行只加载此插件。原 OpenCode 源码、build agent、工具、压缩、重试及原有限制不改动。
- 原 messages.transform 同时用于主循环和压缩。插件通过原 SDK 读取会话 parentID 和消息列表，只对根会话中已持久化、未完成、非 summary 的 build assistant 占位消息计轮，按 message ID 去重；子会话和压缩转换不计轮。原 processor 的内部 HTTP 重试不重复触发该主循环 hook，同轮工具数不加轮。
- 每次主采样前追加瞬时预算提醒，并把文字及 message ID 保存到 control.json。P50 用尽且主循环仍需下一轮时扩展一次，P75 之后保存阻止的占位 ID 并直接从 hook 抛出终止异常；不使用异步事件追赶停止，也不通过请求数近似轮数。原生完成判断在下一轮 hook 前生效，完成后不扩展。
- 原插件加载错误可能被框架吞掉，因此控制运行强制 record_raw_usage=true。公共 recorder 新增可选插件就绪门禁，只检查本次控制插件初始化与预算身份；未就绪请求在访问模型前拒绝并留下原始失败记录。门禁不计轮、不重试、不改协议或 usage，非控制路径默认不启用。
- agent 退出后使用原 CLI export 导出同一 XDG 数据目录的根会话到 session.json；导出配置禁用插件，避免重置控制状态，不重新生成。保存 process.json、原控制记录、control-result.json 和导出日志。超时未确认进程结束时不导出，未知/缺失结果不伪造完成。
- summary.py 从原会话消息/parts 读取完成、工具和 step-finish token，并核对控制轮次/预算/根会话身份。原生终止且无错误、捕获补丁非空才提交；预算耗尽补丁保留 diagnostic.diff/原 model.patch，正式 patch.diff 清空且不提交。最终摘要纳入与成功/补丁无关，均值仍 null。
- 原生摘要包含根会话中可观察的 step-finish（含原生压缩步骤），按 part ID 去重；子会话消费仍由 corrected 的原 HTTP 记录覆盖。native input 加缓存读写、output 加 reasoning，沿用 OpenCode 原兼容语义；缺失步骤/usage 保持未知，不借 HTTP 字段补原生摘要。run_free 的 JSONL reader 和 corrected-v1 公式不变。
- 公共控制预检识别插件运行时，OpenCode 无需 python_executable；mini/Trae 仍要求其预备 Python。OpenCode 仍需原镜像、只读 config_root、rg、显式上下文/输出上限和现有协议支持。
- 本批仅阅读原源码和文本差异；未写/跑测试、语法检查、dry-run、构建、插件加载、export、容器、代理或模型调用。插件加载、原 SDK 会话读取、异常传播、原生导出、流式/超时行为保持未验证。下一批为 Codex 可选 hook 的具体补丁与构建入口 review，不自动应用或构建。

### 第 5A 批：Codex 补丁 review 产物（历史）

- `agents/codex/control-build/turn-control.patch` 仅为拟议源码差异，未应用：非默认 Cargo feature、主执行循环采样边界 hook、预算日志与原生 ContextualUserFragment 提醒。原 upstream、普通 caller、adapter 和 Dockerfile 不变。
- hook 仅针对显式启用的 Exec 主会话；内部 HTTP 重试、同轮多个工具、子会话及压缩不额外计主轮数。原生完成先于下一轮扩展；最多扩展一次，最终预算耗尽返回明确错误并保存事件，交由原错误路径终止。
- 控制日志独占创建、采样前落盘；复用目录拒绝重置预算。原生 history 保存实际提醒。日志不提供完成或 token 结论，下一批须与原 JSONL 和进程状态交叉核对，禁止把原错误之后的生命周期结束事件当作成功。
- `build.sh` 将应用与构建分成显式步骤：应用只作用于新副本；构建要求预备原生工具链和缓存，使用 offline/locked，输出独立二进制。本轮两个入口均未执行，也未做 patch check、格式化、编译或测试。
- 第 5B 批须在 review 获批后接入控制版身份/就绪门禁、公共最终摘要与提交资格。当前 Codex 控制预检继续拒绝，不能称为已接通。构建、异常传播、停止边界和运行兼容性均未验证。

### 第 5B 批：Codex 控制构建副本与原生摘要

- 用户 review 后以“ok继续”授权；原 upstream 保留，已批准补丁实际应用到独立 `agents/codex/controlled/`。应用脚本由 git apply 改用 patch -p1，修复仓库子目录下跳过路径却返回成功的问题；未构建，不改普通 Dockerfile、原 caller 或补丁文本。
- 新增 `controlled_runner.py` 和 `summary.py`，Codex 手写 adapter 声明 native_sampling_hook 控制运行时与 agent-final-summary-v1 兼容 reader。普通 run_free 路径/reader 不变；新增接口不代表该工作线已支持通用会话回调。
- 配置要求独立 controlled_executable、明确 control_version、record_raw_usage 和既有 Responses 模型地址；初始化日志经原 usage proxy 可选门禁确认后才转发请求，不以 HTTP 数计轮。无控制版构建或无 hook 初始化不会静默运行无预算模型请求。
- 每调用独立原生 CODEX_HOME 保存 rollout，核对控制 turn ID、根 thread ID、逐轮提醒与原生结束事件；错误/失败/abort 优先，不把生命周期结束误作完成。原生工具按身份去重计数，不把流式更新当成多次调用。
- 原生 token_count 与 response usage 互相佐证，避免 JSONL 缺 usage 的默认零；字段缺失、覆盖无法确认（包括压缩/采样与响应数不一致）则 null。原生兼容版本 codex-native-summary-v1；未知不是零，cache/reasoning 不重复相加。只读未压缩根 rollout；缺失或压缩产物报未知。corrected 仍直接读取全部原 HTTP 尝试及可观察辅助调用。
- 容器内 GNU timeout 加终止缓冲，确认返回后才收集数据集补丁；外层传输超时不确定时不收集。capture-result.json 区分空、失败与未收集；原 model.patch 保留，未完成只保留诊断、正式 patch.diff 清空，不自动提交或重新生成。汇总从原产物重建，不累加 control-result.json。
- 已应用补丁的六处文件与原生日志定义完成静态阅读；未写/跑测试、语法检查、dry-run、构建、容器、模型、远程或安装。Rust 编译、门禁、原生日志落盘、压缩覆盖、超时清理与运行兼容性均未验证。本批停止，下一批为第 6 批配置矩阵和统计收尾。

### 第 6 批：矩阵、统计与静态收尾

- 用户“一次性做完”授权本工作线剩余交付；新增 experiments/deepswe 下 20 个实验 TOML、runtime.template.toml 及说明。四个 agent 分别有 Python run_free 34、非 Python多语言 79、控制全部 113、控制 Python 34、控制非 Python 79；预算默认显式 gpt，保留另两档说明。模型及协议沿用已有支持。
- 静态读取原 task.toml 与配置数据，确认 113 个唯一 ID、语言计数 34/34/35/5/5、113 个原命令及双 no-network 约定；模板组件路径存在，两个 runtime 映射覆盖所有任务。未执行适配器、配置 CLI 或 dry-run；空镜像/OpenCode 限制等未填项明确阻塞。
- Trae 将 Workspace 公开 patch_base_commit 传入原生 base_commit，内部非空检查读取已提交差异，保留测试文件过滤及 task_done；最终提交仍判断退出后的公共捕获产物。较低原 max_steps 仍约束控制，缺失原生 usage 保持 null，普通 run_free reader 不变。控制方法版本升为 turn-control-native-summary-v3，旧版本不混入恢复生成。
- 本地评测要求原 CTRF 可读，缺失/损坏不凭 reward 0/1 记完成；恢复/附加校验同样约束。运行预检要求 verifier_python；提交适配器再次检查 submission_eligible，不把诊断补丁送评。原任务、grader、Pier 与四 agent 上游均未修改。
- 报告增加 accounting_context 与逐 case prompt_metadata/native_final_summary，展示方法版本、补丁规则、agent 原生兼容来源；JSON/CSV/Markdown/终端接通。原统计公式、corrected-v1、失败消费及重建规则保持；compare 已有同完整任务集合/纳入集合/规则/完整性门禁继续生效，未添加隐式交集或跨规则节省率。
- [交付说明](deepswe-delivery.md) 逐项记录静态证据和未验证项。未写/跑测试、导入/语法检查、dry-run、构建、安装、容器、远程或模型调用；其他工作线离线验证不代表 DeepSWE 验收。静态实现已交付，实际运行前提与运行正确性均未验证。

## 目录职责

| 位置                             | 职责                                                     |
| -------------------------------- | -------------------------------------------------------- |
| `methods/run_free/`            | 原方法副本、允许 Git 的 Python 方法适配层、组件 manifest |
| `methods/run_free_multilingual/` | 静态多语言方法；复用 run_free 原生统计聚合 |
| `agents/codex/`                | 本地 Codex 源码副本、原调用/统计兼容代码、构建与启动适配 |
| `datasets/swe_bench_verified/` | 原数据副本、固定版评测器、任务工作区与评测适配           |
| `datasets/deepswe/` | DeepSWE 原任务包、Pier 副本、任务/工作区/本地评测适配 |
| `src/`                | 配置、显式组件加载、调度、产物与恢复；不放具体方法实现   |
| `experiments/`                 | TOML 实验配置，明确选择 method、agent、dataset           |
| `tests/`                       | TokenAna 测试、临时测试组件和假模型服务                  |
| `runs/`                        | 配置快照、状态、尝试记录、trace、补丁和评测产物          |

每个组件的原文件放在 `upstream/` 或专用兼容目录，新增 `adapter.py` 与 `manifest.toml` 分开存放。
manifest 使用 kind、name 和可选 entrypoint 声明组件身份与入口；核心只加载配置指定的组件，不维护集中式方法名单。首版不在 manifest 中安装依赖，运行依赖由选中组件的预构建镜像提供。
根目录 `datasets/` 不建成 Python 顶层包，避免遮蔽 Hugging Face 的 `datasets`。

## 已实现的配置入口

配置样例：[run_free_codex_verified.toml](../experiments/run_free_codex_verified.toml)。
必须提供 `[method]`、`[agent]`、`[dataset]` 三个表，各用 `path` 指定组件目录；相对路径以配置文件所在目录为基准，也支持绝对路径。
每个表可以包含 `options` 子表，读取器原样传递选项，不解释组件特有参数。Verified 适配器支持 `limit=10`；只读 config 命令不枚举数据，`run --dry-run` 加载选中组件并枚举任务，真实 `run` 还要求 agent executable、运行时 TOML 和全新输出目录。
读取器拒绝未知顶层表和未知组件配置字段，并检查选中 manifest 的名称与类型；不扫描兄弟目录、不导入适配器、不安装依赖。
配置读取仅检查 entrypoint 的标识符格式，不验证实际入口、依赖可用性或跨组件能力兼容性；配置读取成功不等于实验运行就绪。

核心代码直接放在 `src/`，目前不规划其他同级 Python 包；根目录 `tokenAna.py` 仅调用 `src/cli.py` 的命令入口，以 `python -m tokenAna` 启动，无需安装项目或修改模块搜索路径。
任何任务运行前先激活 `tokenAna` conda 环境（Python 3.12.14，已获用户确认），禁止在本机安装依赖或软件，包括该 conda 环境。
在项目根目录运行，无需安装项目：

```bash
conda activate tokenAna
python -B -m tokenAna config experiments/run_free_codex_verified.toml
```

命令输出解析后的配置、组件名称、绝对目录和选项；配置错误输出到 stderr 并返回状态码 2。
此命令仅展示配置，不是 `run --dry-run`，不会创建运行目录或启动 agent/评测器。

## 公共模型配置（2026-09-19）

模型配置独立于方法和 agent，模板位于 `models/`。本批接入配置选择、CLI 展示、调度传参、运行快照及兼容性检查；没有完成所有 agent 与所有模型的真实直连验收。

示例见 [run_free_codex_model.toml](../experiments/run_free_codex_model.toml)。在实验中添加：

```toml
[model]
path = "../models/gpt-5.6-sol.toml"
# base_url = "http://127.0.0.1:8000/v1" # 经授权的假服务可在此覆盖
```

相对路径以实验文件为基准；只读取选中模型文件。`[model]` 可覆盖 `base_url`、`model_id`、`api_key_env`。模型文件包含 `name`（展示名称）、`provider`、`model_id`（实际请求 ID）、`protocol`、`base_url`、`api_key_env` 和 `note`。密钥只配置环境变量名称，不读取或展示其值；实际 agent 进程所在任务环境必须另行提供该变量，宿主机变量不会因此自动传入容器。

| 模板名称 | 厂商直连协议 | 当前 Codex 配置接入 |
| --- | --- | --- |
| claude-opus-5 | Anthropic Messages | 不支持，运行前报错 |
| gpt-5.6-sol | Responses | 已映射配置，未验证真实服务 |
| deepseek-v4.1-flash | Chat Completions | 不支持，运行前报错 |
| qwen3.8-max | Responses | 需补填地域/Workspace 地址，未验证真实服务 |
| qwen3-coder-next | Chat Completions | 不支持，且需补填地域/Workspace 地址 |

DeepSeek 展示名称保持用户指定的 `deepseek-v4.1-flash`，实际请求 ID 按 [DeepSeek 直连文档](https://api-docs.deepseek.com/) 使用 `deepseek-flash`；这是当前指向 V4.1 Flash 的浮动别名，不是固定快照。其他配置参考 [Claude Opus 5](https://platform.claude.com/docs/en/models/opus-5/whats-new-opus-5)、[GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[百炼 Codex 接入](https://help.aliyun.com/zh/model-studio/codex) 和 [Qwen3-Coder-Next](https://help.aliyun.com/zh/model-studio/qwen3-coder-next)。Qwen 不预设用户地域及 Workspace；在实验中填写相应按量计费地址，例如将 `https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` 中的 ID 替换为实际值。不同套餐的地址、密钥和能力不能混用。

`config` 输出解析后的模型配置且不导入 agent；dry-run 输出 `model_compatibility`、具体阻塞原因和 `service_verified=false`。`configuration_compatible` 仅表示配置可映射，不表示账户权限、工具调用、cache、usage 或服务行为已经验收。运行会在创建输出目录和容器之前拒绝不支持的模型协议或缺失地址。

agent 适配器声明 `model_protocols`，实现无副作用的 `configure_model(model, options) -> dict`，将公共模型配置转换为自己的原生参数，并检查模型特有能力限制。核心通过 `BoundAgent` 调用它，方法无需按模型名分支。Codex 将模型、地址、协议及密钥环境变量名传入原生 provider 配置；选择公共 `[model]` 后拒绝在 agent.options 或单次调用中覆盖这些字段，防止运行身份与实际模型不一致。

新方法必须复用绑定 agent 的公共模型选择；新 agent 以这五个模板为接入目标，逐模型检查协议、工具调用、缓存及 trace/usage。仅声明协议支持不能视为五模型接入完成；不兼容项要明确列出，不能默换模型或引入协议代理。当前 Codex 源码仅支持 Responses，其他协议仍需后续单独设计与授权。原方法逻辑、逐轮预算和统计口径本批不变。

解析后的模型配置进入 `config.json`，恢复时更换模型 ID、地址或密钥变量名会产生配置不一致。旧实验未选择 `[model]` 时保持原参数行为及快照结构，旧样例未改动。本批不扩展原预测文件的 `model_name_or_path` 语义。

验证记录：用户停止测试前新增 9 项模型离线测试全部通过，包含五模型计划、命令映射、新 agent 复用、冲突拒绝和快照身份。全量 63 项中 58 项通过，另 5 项已有 usage proxy 测试因沙箱禁止本地监听而报错；用户要求不再测试，未重跑。最终做静态检查，未调用真实 API、构建或启动容器。

## 选中适配器加载

`src/loading.py` 的 `load_adapter(component)` 仅加载该组件目录的 adapter.py，并无参数调用 manifest.entrypoint 指定的类或工厂，返回适配器对象。构造器不得启动实验或安装依赖。未声明入口时明确报错。
每次加载创建独立的私有 Python 包名，支持 `.upstream` 等相对导入，无需修改 sys.path 或创建顶层 datasets 包；不扫描兄弟目录。失败时清理本次加载的私有模块并传播原异常，成功模块保留至进程结束；不提供热重载或依赖环境隔离。
选项由调用层显式传递，例如 `dataset.tasks(**config.dataset.options)` 和 `method.run(..., config.method.options)`；agent 可通过 `BoundAgent(load_adapter(config.agent), config.agent.options)` 绑定配置。导入隔离只适用于私有包内的相对导入，不是安全沙箱，第三方绝对导入仍使用当前 Python 环境。
模拟测试已连接配置、Verified 任务、run_free、Codex 原调用器、容器生命周期、原始补丁和预测生成；环境准备由数据集适配器按运行时配置执行，远程评测提交仍保持为显式命令计划。

## 已实现的只读 dry-run

在项目根目录激活 tokenAna 后执行：

```bash
python -B -m tokenAna run experiments/run_free_codex_verified.toml --dry-run
```

`src/planning.py` 只加载三个选中组件、枚举 dataset.tasks 并调用 agent/dataset 的只读 `plan(options)`。不调用 method.run、agent.run，不创建 Workspace、运行目录或预测文件，不执行子进程、安装或检查凭据。适配器的构造器和 plan 必须无执行副作用；加载器并不是安全沙箱。

输出包含组件信息、任务 ID/仓库/基准提交、组件准备事项、缺失配置和明确标注的命令模板。样例实际列出原顺序前 10 条任务，status=plan_only、runtime_ready=false、environment_checked=false；模板中的占位符不是可直接执行的命令。runtime_ready 当前固定为 false，配置齐全也不代表真实运行能力已实现或检查通过。

不带 `--dry-run` 的 `run` 必须同时提供 `--runtime` 与 `--output`。dry-run 满足 10 条任务的计划验收，但不等于 Linux 真实组件或完整实验验收通过。新增 agent/dataset 若要支持计划入口，需在自身目录实现 `plan(options)`，无需在核心添加组件名称分支。

## 串行运行与恢复

真实运行配置必须在 `[agent.options]` 中填写任务环境内的源码构建产物，例如 `executable = "/opt/tokenana/codex"`。该路径必须来自 `agents/codex/upstream/` 的构建，不能指向宿主机或 npm 安装的其他 Codex。运行时 TOML 独立于实验配置，最小格式如下；`images` 必须显式覆盖本次选中的每个 instance ID：

```toml
network = "host"
command_prefix = ["/opt/miniconda3/bin/conda", "run", "--no-capture-output", "-n", "testbed"]

[images]
"astropy__astropy-12907" = "prepared-image-containing-source-built-codex:tag"

[environment]
OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
```

框架不构建或拉取镜像。每个镜像必须已存在，仓库位于 `/testbed`、HEAD 等于任务的 `base_commit`、工作区干净，并包含上述 Codex 可执行文件及测试环境。运行命令为：

```bash
conda activate tokenAna
python -B -m tokenAna run <experiment.toml> --runtime <runtime.toml> --output runs/<run-name>
python -B -m tokenAna resume <experiment.toml> --runtime <runtime.toml> --output runs/<run-name>
```

仓库提供可直接使用的 live 文件：`agents/codex/Dockerfile.source-agent` 从 `agents/codex/upstream/codex-rs` 构建源码版 Codex，`scripts/pull_tokenana_base_images.sh` 拉取 10 个官方基础镜像，`scripts/build_tokenana_codex_images.sh` 构建对应 `-agent` 镜像，`experiments/run_free_codex_verified_live.toml` 与 `experiments/runtime_verified_10.toml` 已配套选择这 10 条任务，`scripts/check_tokenana_live_runtime.sh` 只读检查镜像和 Docker daemon。拉取和构建脚本都不会由 `tokenAna run` 隐式调用。本机 macOS 不作为真实执行环境。

`run` 只接受不存在的输出目录。它逐任务创建临时容器，将产物父目录挂载到 `/workspace/output`，检查提交与干净状态，调用方法和 agent，保存 prompt、trace、原始 `patch.diff`、结构化 generation/result，再删除容器。全部任务完成后，它按原 `generate_predictions.py` 生成预测，并保存原 `sb-cli submit` 与 `get-report` 命令；状态为 `submission_prepared`，不会自行访问远程服务。

输出目录保存 `config.json`、`runtime.json` 和原子替换的 `state.json`。每次任务尝试位于 `tasks/<instance>/attempt-NNNN/`。恢复要求实验与运行时快照完全一致；`collected` 任务从已有 generation 产物恢复，不再次调用 agent；中断任务创建新尝试并保留旧目录；预测准备使用新的 `submission-NNNN/`。已记录为 collected 但缺少 generation/result 的任务会明确失败，不静默重跑。恢复粒度是任务/提交准备边界，不恢复 agent 的中途对话。

## 接口与数据流

最小公共接口已实现于 `src/interfaces.py`，使用数据类和 Protocol，无运行时类型检查或自动重试。当前调度按所选 dataset 适配器的 tasks/validate_runtime/prepare/collect_saved/prepare_submission 生命周期运行。

- Method：`run(task, agent, workspace, options)`；保留完整执行能力，不把所有方法限制为提示词转换。
- Agent：`run(prompt, workspace, options)`；负责指定源码产物的启动、trace 和兼容统计。
- Dataset：`tasks()`、`prepare()`、`collect()`、`evaluate()`；负责任务枚举、工作区、补丁和评测。
- Task：提供任务 ID、仓库、基准提交与问题描述；数据集特有信息由 dataset 适配。
- Workspace：提供工作目录、命令执行与文件访问能力；method 不判断数据集名，agent 不导入特定 dataset。
- Core：选择组件 → 枚举任务 → 准备工作区 → method 调用 agent → 收集补丁 → dataset 评测 → 汇总。

### 当前公共类型

- `Task`：仅包含 `instance_id`、`repo`、`base_commit`、`problem_statement`；评测专用数据留在 dataset 内，不通过通用 metadata 字段传给方法。
- `AgentResult`：保留原调用器的 agent_type、prompt、output、tokens_used、exec_count、duration_sec、raw_trace、error 字段，不重新解释统计值。
- `MethodResult.calls`：按顺序保留每次 agent 调用，不预先合并统计，不将生成结果当作官方 resolved。补丁结果使用 PatchResult；官方评测结果尚未定义。
- `Workspace`：root 是执行环境内部的仓库路径；execute 接受 argv，在 root 下无 shell 执行并返回文本 CompletedProcess，非零退出码保留（包括 Docker CLI 报告的错误），超时和进程启动失败抛出异常。read_text/write_text 使用仓库相对路径和 UTF-8；容器实现见下文。
- 方法 options 只属于方法；`src/agents.py` 的 BoundAgent 合并实验 agent 配置和单次调用覆盖项（后者优先），不修改原配置。CLI 调度层已连接 run_free 的单调用执行与恢复；多调用方法仍需显式的提交调用选择协议。

模拟测试验证多次 agent 调用、工作区读写/命令参数传递、逐次统计与错误保留，以及工作区异常传播；不代表真实组件接入或静态类型检查已经通过。

### Codex 启动适配与工作区扩展

用户已确认：允许原调用器直接执行 Workspace 生成的外部启动命令，不要求所有进程经 `Workspace.execute()`。Workspace 新增 `launch_command(argv)` 与 `new_artifacts()`；前者仅生成宿主机启动 argv，保证在已准备并激活的任务环境、仓库 root 下执行，且不依赖宿主机 cwd；后者为每次调用分配独立共享产物目录，返回宿主机绝对 Path 和环境内部路径。产物目录位于仓库和评测数据目录之外。

`agents/codex/adapter.py` 的 WorkspaceCaller 继承原 AgentCaller，仅覆盖 `_build_codex_command`（另有构造器传入 workspace、产物目录和可执行路径），保留原 `_call_codex`、trace 读取、统计、错误过滤、回退和超时处理。适配器生成内部 bash 命令，Workspace 决定如何进入环境；不再按 SWE-bench 实例名查镜像或自行创建临时容器。原源码保持不变，生产代码不使用 monkey patch。

Codex.run 要求 options.executable 显式指定任务环境中从本项目源码构建的二进制路径，无默认系统 codex；timeout 默认 600 秒。构建位置未定，因此样例配置暂不填写虚构路径。BoundAgent 负责实验配置与单次调用选项的合并。

每次调用写入 prompt.txt，原调用器读取 trace.jsonl，命令另输出 patch.diff；AgentTrace 字段映射到 AgentResult，不重新计算统计。保留原命令替换读取 prompt（尾部换行会被 shell 去除）及 `; git diff` 行为（最终退出码可能掩盖 Codex 退出码），本批不修正原行为。Workspace 准备失败直接向外抛出，进入原 caller 后的异常遵循原有处理。

模拟测试覆盖已有 trace fixture、stdout/token 回退、stderr 过滤、非零退出码、超时、配置覆盖以及含空格的命令路径。只在测试中替换 subprocess.run，未运行 Docker 或 Codex。真实挂载、testbed 激活、构建产物、模型服务配置和超时后的任务进程清理仍需在工作区及真实组件批次实现与验证。宿主进程的超时不等于容器内部进程已终止。

### 已准备容器的 Workspace

`src/workspaces.py` 的 DockerWorkspace 接收 container、容器内绝对 root、ArtifactDirectory 产物父目录映射，以及可选 command_prefix；构造时不执行命令。准备层必须事先启动容器、准备仓库和建立产物绑定挂载，并保证挂载目录位于仓库和隐藏评测数据之外。

launch_command 生成 `docker exec -i --workdir <root> <container> <command_prefix...> <argv...>`，不依赖宿主 cwd；例如准备层可传入容器内 conda 的 `run --no-capture-output -n testbed` 前缀。空前缀使用容器现有环境。此处不决定镜像、网络、凭据、Codex 路径或数据集名称，不进行 Docker 状态探测。

execute 保留 stdout、stderr、退出码并传递超时；read_text 用 cat 读取，write_text 通过 stdin 写入，两者遇到非零退出码抛错，不自动建文件父目录。文件路径拒绝绝对路径和 `..`；这只是接口路径约束，不隔离容器内符号链接。UTF-8 文本采用 subprocess text 模式（读取时有通用换行转换）。

new_artifacts 在已有宿主挂载父目录内创建独立 call-* 子目录，映射到容器同名子目录，保留历次文件。数据集的 `workspace.py` 负责容器 create/start/rm、挂载、基准提交和干净状态检查；异常路径也进入强制删除。上述行为已由模拟进程验证，真实挂载、权限和容器访问仍待 Linux 验证。

### run_free 方法适配

`methods/run_free/adapter.py` 中的 `RunFree.run` 将 Task 的 repo、base_commit、problem_statement 传给原 `PromptBuilder.build_run_free_prompt`，将返回字符串原样交给 agent，调用一次，并将原 AgentResult 对象放入 MethodResult.calls。
方法不读取数据集、不操作工作区、不合并统计、不重试，也不添加硬性执行限制。run_free 当前不使用方法 options；agent 调用传入空覆盖项，保留 agent 自身配置。
仅导入该方法的 prompt_builder，不导入原 runner。模拟测试覆盖多行文本、Unicode、空基准提交、错误结果与异常传播；已支持通过配置与加载 API 动态创建适配器。

### Verified 任务枚举

`datasets/swe_bench_verified/adapter.py` 中的 `SweBenchVerified` 默认读取同目录下的原 JSON，也可显式传入 data_path。`tasks(limit=...)` 按文件原顺序返回公共 Task 列表；省略 limit 返回全部 500 条，0 返回空列表，超过总数返回全部，负数或非整数报错。不随机抽样、不修改数据文件。
映射显式选取四个公共字段，不携带 patch、test_patch、hints_text 或评测字段。JSON 只在控制进程内读取；任务容器仅挂载独立产物目录，不挂载数据文件或评测源码。挂载命令已模拟验证，真实权限与隔离仍待 Linux 验证。
测试直接按文件路径加载该适配器，不将根目录 datasets 建成 Python 包，也不导入 Hugging Face datasets 或官方 harness。Dataset prepare/evaluate 仍未实现；collect 已实现，规则见下文。

### Verified 补丁收集

`SweBenchVerified.collect(workspace, trace)` 接收调用方明确指定的一个 AgentResult，通过 workspace.execute 执行 `git diff`，优先采用去掉首尾空白后的非空结果。命令失败或超时直接抛出，不能将收集失败当成空补丁并回退。当前 run_free 调度使用 `collect_saved(trace)`，在容器删除后读取原 Codex 命令写入的 `patch.diff`，从而让生成统计与原预测提交共享同一原始产物；存活工作区的 collect 接口仍保留给其他生命周期。

若 git diff 成功但为空，按原 extract_patch 的规则，从 agent 输出中第一个以 diff --git、--- 或 +++ 开头的行截取到文本末尾，再 strip。不额外清理代码围栏或尾部说明，不验证补丁语法；普通 prose 不作补丁。该等价规则写在 dataset 适配器中，不导入带依赖和输出目录副作用的原 runner，也不让 dataset 依赖某个方法目录。

`PatchResult` 保存 patch、success、error；success 严格采用原 `bool(patch) and not trace.error`，error 使用原 `trace.error or ""`。这是旧的生成成功标记，不代表官方 resolved，当前不产生任何官方评测结论。收集器不选择多调用方法的最终 trace，也不聚合多次调用错误；此类方法的提交选择仍需后续明确。

## 原实现与忠实性

### run_free 评测链路更正（以原提交源码为准）

用户明确要求评测实验过程也以原源码为准。run_free 的接入依据是 `execution_control/scripts/submit_to_swebench.sh` 的实际调用链：先调用 `generate_predictions.py`，再执行 `sb-cli submit swe-bench_verified test --predictions_path <JSON> --run_id <ID>`。两个原脚本已原样复制到 `datasets/swe_bench_verified/compatibility/execution_control/scripts/`；原 Python 预测生成函数已接入并以临时夹具验证；shell 脚本未执行。

预测生成按实例目录名排序，仅读取存在的 patch.diff，保留读取出的补丁文本（不 strip、不使用 runner 文本回退结果），空文件仍加入预测，缺失文件告警并跳过。不依据 result.success 或 trace.error 过滤。输出是 JSON 数组，model_name_or_path 为 agent_mode；提交脚本的默认 run_id 为 dataset_agent_mode。另一份 prepare_sbcli_predictions.py 的跨方法样本交集规则不在该调用链中，不混入此次复现。

之前建议的本地 `python -m swebench.harness.run_evaluation` 不能替代这条提交链。已复制的官方 v4.1.0 harness 保留为此前规划的本地验证工具；不能据此推断远端服务也运行同一版本。远程提交、报告获取及本地验证均未执行，后续仍逐批授权。

现有 collect 的 PatchResult 复现 runner 的生成结果口径，不可直接当作原提交脚本的输入：评测适配必须连接当前尝试实际生成的原始 patch.diff，区分空文件和缺失文件；不能用重新执行 git diff 或文本回退覆盖它。产物关联和预测准备已实现，实际提交及报告获取尚未实现。

原 shell 脚本包含读取 .env、检查 API key 和切换 swebench 环境的逻辑，当前只作为未修改的原实现保留；适配遵守用户的 tokenAna 环境及本机禁止安装约定，不直接执行该 shell。只生成预测时已调用原 Python 函数并显式传入目录；提交命令的实际执行另行授权。

新增 `AgentResult.artifacts` 可选字段关联本次调用的共享产物目录，原统计字段不变；Codex 在正常结果与原 caller 返回的错误结果中均保留关联。run_free 方法无需修改。

`SweBenchVerified.prepare_submission(results, directory, agent=..., mode=..., run_id=...)` 接收实例 ID 到明确选定 AgentResult 的映射。调用方须确认进程已停止写入；适配器不自行选取最后一次调用。它在全新目录下按原 output/swebenchverified/agent/mode/instance 布局复制所选调用的 patch.diff，调用未修改的 generate_predictions，并返回预测文件路径和 sb-cli 命令列表，不执行命令。不携带产物关联时报错；关联目录内 patch.diff 缺失则保留缺失，由原脚本告警跳过。

原文件及既有准备目录不覆盖；目录已存在时报错，重新准备须使用新目录，避免旧尝试补丁混入。保留原默认 run_id（swebenchverified_agent_mode），是否使用唯一 run_id 由调用方显式指定，准备函数不保证远端 ID 唯一。空预测也按原脚本生成，不擅自增加 success/error 过滤。CLI 调度已连接预测准备；远程提交仍不自动执行。已支持读取明确指定的本地报告 JSON。

SubmissionPlan 已包含原脚本提示的 `sb-cli get-report swe-bench_verified test <run_id>` 命令，与提交使用同一 run_id；只生成命令，不执行，也不假定 CLI stdout 是纯 JSON 或报告保存位置。

`SweBenchVerified.read_report(path)` 读取指定本地报告 JSON，保留原始计数、ID 列表及其他字段，仅检查已出现的常用计数和 ID 列表类型。文件缺失、JSON 损坏或字段类型错误直接报错；字段缺失时不补 0，不根据 submitted_ids 推断失败、远端状态或已完成。解析成功不代表报告完整、与当前提交匹配或评测完成，报告关联和状态管理尚未实现。测试使用合成报告，不是真实评测结果。

原源码存在不同通过率口径：`execution_control/figures/fig1_passrate_ci.py` 使用 resolved_instances / completed_instances，`execution_control/analysis/common/data_loader.py` 的 load_pass_rates 固定 total=100。目前不自动计算通过率；汇总指标采用哪种口径仍需用户确认，不根据当前样本数擅自修改原公式。

来源已由用户人工检查，不再由助手核验或生成来源记录。run_free、Codex、Verified 数据和评测器原文件均已就位。

Codex 源码位于 `agents/codex/upstream/`，原调用器和 trace 样例位于 `agents/codex/compatibility/`。本次按 Git 文件列表复制本地文件，不包含 `.git/`、未跟踪文件或缓存；未修改源码、安装依赖或构建。

Verified 数据位于 `datasets/swe_bench_verified/data/swe_bench_verified.json`，评测器仓库位于 `datasets/swe_bench_verified/upstream/swebench/`；其中内层 `swebench/` 才是原 Python 包。评测器仅下载解压，尚未安装或运行。

- 方法使用用户提供的 `execution_control/`，agent 使用根目录中的 `codex/` 源码。
- Verified 使用当前 `execution_control/data/swe_bench_verified.json` 的 500 条原数据。
- 本地验证工具为已复制的官方 [SWE-bench v4.1.0](https://github.com/SWE-bench/SWE-bench/tree/v4.1.0)；run_free 原实验提交链路使用 sb-cli，按上文接入，远端服务版本尚未确认。
- 直接调用原 `prompt_builder.py` 的 run_free 实现，提示词逐字保持，不添加 hard-limit 限制。
- Codex 兼容层保留原 `agent_caller.py`；新适配只连接启动环境，复用原结果处理、统计和回退行为。
- 补丁优先取真实 `git diff`，再按原 runner 规则处理文本 diff；原 runner 用作行为对照。
- 保留原 token/exec_count 字段及其语义；另外按下文统一规则重新计算 corrected token，不覆盖原统计。原 `success` 与官方评测 `resolved` 分别保存。
- 从本地 Codex 源码副本构建，不以 npm 或系统中其他版本替代；不承诺复现原论文分数。

## Corrected token accounting

本节是 TokenAna 所有方法必须遵守的统一统计规则（`corrected-v1`），适用于 run_free、turn_control 及之后接入的每一个方法。每个方法必须同时提供其 original token accounting，并接入框架公共统计层，依据本节从执行记录重新计算 corrected token accounting；不能直接把原方法的 token 总量或均值改名为 corrected，也不能按方法另定 corrected 公式或筛选条件。

当前状态：公共统计类型、计算器、OpenAI Responses usage 规范化、run_free/Codex 原分析口径及双列报告函数已有离线验证。记录代理与 Codex 可选接线已实现；本轮新增 run/resume 自动统计及双列落盘，见下节。用户要求不新增或运行测试，本轮代码仅作静态阅读与差异检查；真实网络转发及真实组件尚未验收。

### run/resume 自动汇总（2026-09-19）

- `src/records.py` 包装绑定 agent 和 Workspace，在每次调用开始及分配产物目录时原子保存 `calls.json`，返回后另存逐调用结果；原方法、caller、提示词和返回对象语义不变。调用被中断也可通过尝试目录找到已落盘 API 记录。
- `src/run_accounting.py` 从全部选定任务及其全部尝试重建 corrected，不按补丁、错误或评测状态筛除；按 case 汇合后交给既有 `corrected-v1` 计算器。同一次重建中每个尝试和产物只读取一次，不对旧汇总继续累加。未开始任务不进入分母；调用开始但没有 API 记录时标为未知。
- original 使用任务状态明确指向的当前尝试及唯一调用，读取原始 `trace.jsonl` 和 `patch.diff`，由既有方法统计规则计算。当前仅声明 run_free/Codex 的 `codex-jsonl` 格式；其他组合显示 original 不可用，不能误交给 Codex 解析器。
- 正常完成、失败或可处理的中断均保存 `accounting.json` 和 `accounting.txt`，包含各项和、均值、完整性、case 及 API 记录关联。CLI 向 stderr 打印双列表格，stdout 保留 JSON 状态；`state.json.accounting` 记录报告位置或汇总错误，汇总失败不掩盖先发生的生成异常。强制终止不能保证当场生成报告，后续 resume 会重建。
- 新样例 `experiments/run_free_codex_accounting.toml` 显式开启 `record_raw_usage=true` 并指向假 Responses 服务，需已准备的 Linux host 网络运行时。创建输出目录前检查本批双口径组合及显式 Responses 地址。旧配置不隐式开启代理，缺少原始 usage 时只能输出不完整 corrected；旧运行缺少调用关联也会标记覆盖缺口。
- API 读取现在也包含缺少 metadata 的请求目录，避免忽略损坏记录后错误标为完整。原始文件保持不变。统计完整性仅针对已声明的 Responses HTTP 采集范围，不代表已覆盖远端压缩或隐藏子会话。
- 第 1 批仍是单调用调度；多调用提交选择、其他协议、mini/Trae/OpenCode 的 original 及真实组件验收按后续批次交付。`analyze`/`compare` 和 CSV/Markdown 已在下述第 2 批接入；两批均未新增或运行测试。

### 离线分析与跨实验比较（2026-09-19，第 2 批）

入口：`python -B -m tokenAna analyze runs/example`；`python -B -m tokenAna compare runs/baseline runs/candidate --output comparisons/example`。先激活 tokenAna；本轮仅实现，未执行这些命令。

- `src/analysis.py` 统一导出 `accounting.json`、`accounting.txt`、`accounting.csv`、`cases.csv`、`accounting.md`；run/resume 也自动使用该导出器。JSON 保留配置、逐 case 双口径及原始 usage 关联；CSV/Markdown 展示各指标和、均值、分母、完整性、原因及 corrected−original 口径差异，不将其解释为方法节省。
- 新运行在生成前保存公共任务 `tasks.json`，resume 校验任务快照。analyze 读取已保存的 config/state/tasks，旧运行可以使用 accounting.json 中的任务身份；两者都没有时明确报错，不重新枚举当前数据集。只加载快照所选 method/agent 的当前本地适配器做统计，不调用 agent、容器或评测器；报告标注当前适配器重算及分析版本。
- analyze 默认创建 `runs/example/analysis/<UTC时间戳>/`，也支持 `--output` 指定全新目录，不覆盖原报告、原始记录或 state。状态仍为 running（包括强制终止未更新状态）的记录可导出，但所有指标标为不完整，说明快照可能变化。
- compare 读取运行目录、独立分析目录或 accounting JSON；首个输入为基线，输出 `comparison.json`、`summary.csv`、`cases.csv`、`comparison.md`。按 dataset 名称、instance ID、repo、base commit 全外连接，缺失 case 显示 present=false/null，不取交集、不补零。保留额外 token 明细以及模型、方法、agent 身份。
- 仅同任务集合、同实际纳入 case 集合、同统计规则、双方生成完成且指标完整时计算同口径差额和百分比；零基线或未知均值不计算对应百分比，并记录原因。两套口径分别比较，不推断 resolved。旧报告缺少逐 case 口径时保留汇总值并阻止变化率，可先 analyze 重建。
- 输出目录必须不存在。compare 不读取原始轨迹或导入适配器；遇到运行目录的 state 标记 running 会拒绝读取可能陈旧的顶层报告。以上均只经静态阅读与差异检查，用户要求不写、不跑测试。

### 多协议采集与能力预检（2026-09-19，第 3 批）

- 新增 `src/usage_protocols.py`，公共代理显式选择 Responses、Chat Completions 或 Anthropic Messages，分别转发 `/v1/responses`（含 `/compact`）、`/v1/chat/completions`、`/v1/messages`。请求正文不改写，不进行协议转换。Anthropic 返回代理根地址供 SDK 拼接 `/v1/messages`；上游 base URL 已含 `/v1` 时不重复添加。
- metadata 记录协议、provider、实际请求中是否观察到 cache_control、Chat 是否请求 include_usage；保留原始请求可检查具体 cache TTL。拒绝的路径/HTTP 方法和不支持的请求编码保存缺口记录。旧 metadata 未声明协议时沿用原 Responses 解释；未知协议拒绝规范化。
- Responses 与 compact 使用同一 usage 结构；Chat 映射 prompt/completion 字段，SSE 使用终止标记和 usage；Anthropic 合并 message_start 与累计 message_delta，message_stop 完结，累计量不重复求和。重复终态去重、冲突转未知，截断保留已知小计并标记不完整。Anthropic input 包含普通输入、cache read/write，TTL 子项另列并校验，不再加入 total。缺失细项保持 null；其他字段保留原始 usage，尚未规范化的厂商扩展不擅自求和。
- 新增 `src/capabilities.py`，dry-run 展示 `accounting_compatibility`，run/resume 在启动前复用同一组合、原始采集协议、reader、地址校验和 host 网络检查。新运行保存 preflight，报告带采集边界与规范化版本。recording_disabled 不表示完整双口径；不隐式改变旧配置。
- cache 策略区分 OpenAI 自动缓存、Anthropic 需显式 cache_control、未知 provider 默认行为未确认；配置兼容和观察到标记均不代表实际命中或服务已验收。本批不修改 agent 请求以启用缓存；Anthropic 的显式启用由后续 agent 适配批次交付。
- Codex 声明 Responses 采集能力并传递真实 provider 标识，压缩路径纳入公共代理。本批只完成公共三协议能力；mini 的多协议接线和 original 仍待下一批，Trae/OpenCode 尚未适配。绕过代理的请求、隐藏子会话、WebSocket、重定向和 chunked 请求均未覆盖。
- 仅人工阅读和本批差异检查，未写或运行测试、dry-run、真实 agent、网络代理或容器；未改第三方源码、原方法和统计公式。实际转发及各厂商行为未验证。

协议依据：[OpenAI Chat](https://developers.openai.com/api/reference/resources/chat)、[Responses compact](https://developers.openai.com/api/reference/java/resources/responses/methods/compact)、[OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching)、[Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)、[Anthropic caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。

### API 原始 usage 记录代理

用户最新约定替代此前“corrected 只读取 agent trace”的选择：original 完全保留原方法的读取和统计；corrected 从 API 原始响应提取 usage，不回退到 agent 的 tokens_used 或 trace usage。agent 日志仍保留用于诊断和 original 统计。

`src/usage_proxy.py` 提供每次 agent 调用独立的 HTTP 记录代理；Codex 在 `[agent.options]` 设置 `record_raw_usage = true` 时，将已选择模型的 Responses base URL 作为上游，临时将 Codex provider 指向代理。无需改变模型名、提示词或第三方源码。代理仅监听 127.0.0.1 的动态端口；容器须使用 Linux `network = "host"`，运行入口拒绝其他网络配置。API key 仍由既有 model 配置的环境变量提供，代理转发但不记录认证 header。

每个调用的 artifacts/api-records/<request-id>/ 保存 request.body、逐块落盘的 response.body 和 metadata.json（状态码、脱敏 headers、时间与完成/错误状态）。保留 HTTP 实体正文的原始字节，HTTP chunk framing 不属于正文；解析不会覆盖原始文件。每次实际 HTTP 请求分配新 ID，代理不自行重试、不跟随重定向、不修改请求 JSON 或 cache 策略。`proxy_timeout` 默认 60 秒，为网络读写超时；agent 总超时仍由原 caller 控制。

`src/raw_usage.py` 的 `read_raw_usage(directory, case_id=..., attempt_id=..., call_id=...)` 将记录转成公共 CaseUsage，可直接传给 corrected_accounting。首轮支持 OpenAI Responses 的 JSON 和 SSE 终态 response.completed/incomplete/failed usage，支持 identity/gzip 响应正文；相同 HTTP 请求内的重复终态 usage 去重，冲突转为未知，不把实际重试合并。截断或缺失 usage 保留已知小计及不完整说明；未知 usage 字段原样保留。它不会读取 original/agent trace，也不能观察网关转换前未暴露的厂商字段。

首轮历史限制（第 3 批已扩展协议与 compact 路径）：当时只转发 POST /v1/responses，显式禁用该代理 provider 的 WebSocket；不支持其他 API 路径（包括远端 compaction）、chunked 请求或其他厂商协议。不能将该能力宣称为所有模型请求均已覆盖；采用这些接口的组合须另行适配。未开启代理时沿用既有执行，不生成原始 usage；不能据此声称 corrected 已完整。

验证状态：按用户最新要求不再运行本地监听端口/假服务测试，也不以此申请额外权限。3 项文件夹具测试及 5 项 Codex 模拟测试通过，包含重复/重试/截断/未知 usage 和代理接线保留原统计；静态语法检查通过。端口测试初次因沙箱禁止绑定而未执行成功，后续权限请求被取消；端口测试现默认跳过，仅在另获授权并显式设置 TOKENANA_LOCAL_PROXY_TESTS=1 时运行。实际 HTTP 转发、Linux 容器连通与真实 Codex 尚未验证。

第一批实现位于 `src/accounting.py`：`UsageObservation` 表示适配器选出的不重叠 usage 范围，`CaseUsage` 汇集一个 case 的全部尝试并明确调用状态及覆盖缺口，`OriginalCase` 表示原口径选定尝试的 trace 和原始 patch 文本。`corrected_accounting` 按统一分母汇总每项指标，`comparison_report` 检查方法提供 original_accounting 后生成可 JSON 序列化的报告，`render_accounting` 逐项展示和、均值、完整性及原因。已新增显式开启原始采集时的组合检查，完整能力矩阵仍待后续批次。

run_free 新增 `original_accounting` 方法，委托方法目录内的统计模块复现原 Codex 分析：trace 存在且原始 patch 非空才纳入，累加 turn.completed 的 input/output，均值整除。不改原方法执行、原 caller 或 AgentResult 字段。当前仅支持该 Codex 原口径，不能将其他 agent 的 trace 交给它。

公共计算器要求 trace 适配器先按已验证语义选择逐 response 或累计范围；它只去重相同 case/attempt/call/observation 标识，不自行推断跨文件或累计快照的覆盖关系。冲突标识、非法 token 和不一致 total 明确报错，下一批适配器须保留原记录并把不能确认的 usage 转为未知及完整性说明。OpenAI 规范化保留缺失细项为未知；原始 usage 和来源由 observation 保留，尚未实现原始文件归档。

验证：tokenAna Python 3.12.14 下新增 9 项离线统计测试通过，项目共 48 项测试通过。覆盖全量 case/尝试、重复与冲突、逐项完整性、零分母、额外指标、原 patch 筛选/整数均值和报告序列化；未启动真实 agent、容器或模型服务。

### Cache 与 token 明细

- 实验须启用所用 API 支持的 prompt cache。使用自动缓存的接口保留其启用行为；需要显式设置的接口由新增适配层配置，记录实际缓存策略。启用不等于命中，不为制造命中额外调用模型。接口不支持或无法确认启用时明确报告，不能声称 cache 已开启。
- 尽可能细粒度保留 usage：input、output、total、cache read、cache write、reasoning，以及接口提供的缓存写入 TTL、文本/图像/音频、其他 token 明细和未知字段。未提供的明细标记未知，不能填零；只能在包含关系明确且所需字段齐全时推导普通 input/output 等细项。
- 按实际厂商、API 协议和 trace 版本的字段语义规范化，不能只凭模型名或字段名推断。经网关转译的 usage 按实际返回语义处理；接入新协议必须明确字段包含关系并验证，不能盲目套用已有厂商公式。

| API 语义 | corrected input | corrected output 与细项 |
| --- | --- | --- |
| OpenAI Responses | 返回的 `input_tokens`；cache read/write 已包含其中，不再加一次 | 返回的 `output_tokens`；reasoning 已包含其中，仅另列明细 |
| Anthropic Messages | `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` | 返回的 `output_tokens`；不重复加入已包含的 thinking；cache creation 的 TTL 子项不再加到总量 |
| 其他接口 | 接入时按该接口的 usage 定义明确规范化公式 | 所有可获得细项保留；必须区分独立消耗与总量的子项 |

上述 API 语义依据：[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)、[OpenAI reasoning usage](https://developers.openai.com/api/docs/guides/reasoning)、[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。规则版本随结果保存，后续语义变更须明确记录，不能静默改变已有实验口径。

### 全量汇总与均值

- corrected 必须分别汇报各 token 指标，不能只汇报一个总量。`total = input + output` 仅定义 total 指标的计算方式；input、output、cache read、cache write、reasoning 及其他可获得细项各自独立汇总、单独展示。单位均为 token 数，不按价格、cache 折扣或计费倍率加权。cache 和 reasoning 按上述包含关系计入一次，不将全部明细字段机械相加。
- 所有 case 的实际消耗都纳入，不按 patch 是否存在或非空、agent error、生成 success、官方 resolved 或是否进入评测筛选。失败、超时、中断和空补丁 case 已产生的 token 同样累加。
- 同一实验内的所有尝试、重试、方法辅助 LLM 调用、压缩及子会话消耗都须归入对应 case。该要求不授予重试权限，也不改变各方法已有的重试和恢复约定。
- corrected 均值为总量除以实际发生 LLM 调用的不同 case 数；同一 case 多次调用或重试只计一个分母。已确认调用且 usage 为零的 case 也进入分母；确认未调用的 case 保留状态但不进入分母，分母为零时均值为 `null`。
- 只有启动记录、无法确认是否发生 LLM 调用的 case 单独列出。已知调用的 case 即使 usage 缺失也保留在分母中；总量或分母存在未知时，均值同样标记不完整并列出已知分母，不呈现为完整实验均值。
- 恢复后重新汇总全部尝试，不能只保留最新或有 patch 的尝试；仅恢复评测不增加 token。原始记录重复加载或重复生成报告不能再次增加统计值。

### 必须单独汇报的指标

以下指标须在每次实验的 corrected 汇总中分别列出，终端与结果文件都必须展示，不能只隐藏在原始 trace 中。每项分别给出跨全部 case/尝试的和、按上述统一 case 分母计算的均值及该项完整性，并保留逐 case、逐调用的可得明细。

| 指标 | 汇报内容 |
| --- | --- |
| Total tokens | 规范化 input 与 output 的和 |
| Input tokens | 规范化后的全部输入 token，包含按接口语义归入输入的 cache token |
| Output tokens | 规范化后的全部输出 token，包含按接口语义归入输出的 reasoning token |
| Cache read tokens | 从缓存读取的输入 token，不能与 cache write 合并后省略此项 |
| Cache write tokens | 写入缓存的输入 token，不能与 cache read 合并后省略此项 |
| Reasoning tokens | 推理/thinking token，作为独立指标展示，并注明其与 output 的包含关系 |
| 其他可获得 token 细项 | 缓存写入 TTL 分项、普通 input/output、文本/图像/音频等，凡可获得且语义明确的指标均单独汇报 |

各指标使用相同的全量纳入规则，不能只对 total 纳入失败 case，而让 input/output/cache/reasoning 继续沿用原方法的筛选。指标完全未知时展示 `null` 和原因，不省略该项、不填零；部分已知时展示该项已知小计、对应均值并标记不完整，不能改用“该字段已知的 case 数”作为均值分母。语义尚未明确的原始 usage 字段仍逐项保留并说明尚未规范化，不强行求和。

### Trace 保留、去重与缺失处理

- 按用户最新选择，corrected 使用 API 记录代理保存的原始 usage，original 继续使用原方法的 trace/日志。尽可能完整保留执行期间可获得的 prompt、模型输出、工具调用和结果、usage、时间、错误、退出状态、会话记录及部分文件，不按成功与否清理或截断记录。
- 原始 trace 文件原样保留，解析失败的行、未知事件和不完整尾行也不能删除。规范化明细另存，并关联 experiment、case、attempt、agent call、thread/response 标识及原文件位置，方便离线重算和分析；标识不可获得时明确记录缺失。
- 同一消耗只计一次：优先使用可唯一关联的逐 response usage；累计快照、最后一次 usage 和最终汇总只能用于补充或核对，不能与其覆盖的明细重复相加。只存在累计记录时按已验证的会话范围、基线及重置语义计算，不逐条累加累计值。
- 缺失、冲突、无法解析或范围不明的 usage 不猜测补齐。保存可确认消耗的已知小计，标记 `incomplete` 并说明缺失字段、受影响 case 和原因；缺失调用数量只能在可确定时填写，否则保持未知。原 caller 的文本长度估算或错误时返回的零不能代替 corrected 实测值。
- 总量完整性与明细完整性分别说明：缺少 reasoning 等子项不必然导致已知 input/output 总量缺失；反之，仅有部分 usage 不得标为总量完整。若 agent 已把 API 缺失字段默认填零，应记录这一来源限制，不能宣称已确认服务端实际返回零。
- “完整”仅指在声明的 trace 能力和覆盖范围内完成核对，不能据此宣称保留了 trace 未暴露的全部网络请求或 usage。后续取得更完整记录时可按同一规则重算，并保留原记录和统计版本。

首轮仅接入 Codex：使用根目录 Codex 源码的现有 `agents/codex/upstream/` 副本，在 Linux x86_64 验收；本机 macOS Codex 不作为实验执行器。original 保留原 `--json` trace 路径；corrected 按最新约定读取 API 记录，不使用 TokenUsageRecord、token_count 或 turn.completed 替代 API usage。原生 session/rollout 可用于诊断，其额外归档尚未接入。Anthropic 规则已确定，不代表其代理协议或 agent 接入已实现。

### 每个方法的接入与结果要求

- 方法适配层复现 original 的取数、筛选、聚合及均值规则；agent 适配层提供完整可用 trace、usage 字段语义和能力限制；框架公共统计层负责 corrected 规范化、去重和全量汇总。新方法复用公共规则，不复制一套可能漂移的 corrected 计算逻辑。
- 每次实验结束在终端和结果文件中并排给出 `original token accounting` 与 `corrected token accounting`；corrected 必须逐项展示上表的 total、input、output、cache read、cache write、reasoning 及其他可得细项，每项包含和、均值及完整性，并明确两套口径各自的 case 分母。两列使用同一次实验记录；原方法未统计的细项显示未统计，不伪装成零。
- 正常结束、失败和可处理的中断均保存已有统计，原始明细可供恢复后重建结果。original、corrected 与原 `AgentResult.tokens_used` 分开保存，不覆盖原方法或原 caller 字段。
- 每个新方法必须通过双口径接入验收：原规则可复现，corrected 纳入所有消耗并遵守统一公式，原记录可追溯。未接入该能力时不得将该方法标记为已完成 TokenAna 接入；统计能力检查及报错属于后续公共接口实现。

## 隔离、运行与恢复

核心依赖保持最小；只导入选中组件。当前框架不自动安装依赖，组合环境与 Codex 构建由运行时镜像承担，实际镜像名和环境配置写入运行快照。
未选中的方法不参与导入、依赖解析或运行身份计算，不能因其语法错误或坏依赖阻止当前实验。
运行恢复身份由解析后的选中组件配置和显式运行时快照决定，不用整个仓库 commit 或全目录哈希。
实例分别记录尝试、生成与补丁收集状态，校验结构化产物；空补丁不等同于成功。
恢复到实例/提交准备边界：生成中断新建尝试并保留日志，已收集任务不重跑，提交准备中断则用新目录重建。
当前提供 `config`、`run --dry-run`、`run`、`resume` 及离线 `analyze`/`compare`；dry-run 不安装依赖、启动容器或调用模型，run/resume 不自动安装、构建、拉取镜像或远程提交。
环境就绪后单条 run 命令覆盖生成和原提交输入准备；远程 `sb-cli` 命令由产物明确给出并另行执行。

## 首版验收

- 小规模10条数据可以dry run跑通。
- 临时新增方法只增加方法目录与配置；临时新增兼容数据集只增加数据集目录与配置。
- 未选中方法出现语法错误、缺失依赖或源码变化，当前实验的启动、安装计划和恢复仍有效。
- Linux 上以真实 Codex 连接假模型服务，验证实际文件编辑、trace、补丁与原统计。
- 首个真实评测实例为 `django__django-11099`，固定 gold patch 做正对照、无效补丁做负对照。
- 串联真实 Codex、预设模型响应和真实评测器；覆盖中断恢复、损坏产物和补丁变化。
- fixture 结果单独标记；gold patch 和隐藏评测信息不进入正常方法输入或 agent 可访问的数据目录。
- 真实模型 API、真实解决率和论文分数不在本轮验收中；目前这些检查均未执行。

10 条 dry-run、动态组件目录加载、未选中坏组件隔离与恢复已由离线/模拟测试覆盖；真实新增组件兼容和其余真实链路需要用户提供 Linux x86_64 运行环境、预构建镜像和假模型服务后执行。本机未安装依赖、未构建 Codex、未启动容器、未调用模型或远程评测。

## turn_control 平行方法计划与当前状态

turn_control 是与 run_free 平行的新增方法，本任务同时负责 Trae 接入及必要的公共接口扩展；不修改 run_free 的源码、适配器、配置和行为。本节为独立补充，不替换上述 run_free 约定。

用户已确认只复现动态 P50→P75 控制策略，底层使用当前原版 Trae，保留其提示词和工具行为；不承诺还原历史实验的完整 agent 行为或论文分数。两份本地 Trae 的比较已完成，未发现 turn_control 专属控制逻辑，逐轮提醒和一次性预算扩展将由新增适配层实现，原源码保持不变，不使用 monkey patch。

- 预算配置：Claude 52→64（追加 12）、Gemini 29→45（追加 16）、GPT 50→67（追加 17）；50、75 表示原实验基线轮数的分位数。
- 每个任务只尝试一次。初始预算耗尽且未完成时，在同一会话、同一工作区追加一次预算，不重启 agent。
- 最终预算耗尽仍未完成时，diff 仅保留作诊断，不作为正式补丁、不进入评测、不重新生成；执行错误同样保留记录，不自动重试。这是本次相对原实验最多三次尝试的明确差异。
- 正式补丁须满足当前 Trae 的有效完成判定并且非空，随后进入官方评测；评测结果不触发再次生成。
- 逐轮控制按一次 LLM 交互步骤计数，同轮多个工具调用不额外消耗轮数；另存工具调用次数及原始 usage，不重新解释已有统计字段。
- 保存初始/最终预算、已用轮数、扩展事件、终止原因、trace、usage、耗时、正式补丁或诊断 diff 及评测状态。未评测的 resolved 保持未评测状态，汇总保留全部选定任务。
- turn_control 已开始但中断的生成不自动重跑；恢复仅处理尚未开始的任务，以及已完成生成后的收集或评测阶段。此限制不改变 run_free 的恢复约定。
- 使用原实验 100 条 Verified 子集；dry-run 按原文件顺序选择前 10 条。复用已有数据映射和官方评测器，保持默认 500 条配置不变，评测专用字段不进入 agent 输入。
- 公共接口拟增加可选逐轮控制能力和结构化状态/产物记录；保留现有 Method.run、Agent.run 及结果字段语义。不支持该能力的 agent 组合明确报错，具体接口在独立批次 review。
- 三组预算均做离线测试；首批真实组件验收仅覆盖 Linux 上真实 Trae 连接 OpenAI 假模型服务，其他模型接口标记为尚未验收。本轮不调用真实模型 API。

已完成 `agents/trae/upstream/` 原文件复制：按根目录 `trae-agent/` 的 Git 文件列表原样复制 108 个文件，共 5,191,138 字节，保留文件权限及目录结构；未复制 `.git/`、未跟踪文件或缓存。原有两个二进制工具随原目录保留，未运行。不生成来源清单、哈希记录或 provenance。
第 2 批已将 `turn_control/swebench_verified_subset.json` 原样复制到 `datasets/swe_bench_verified/data/swebench_verified_subset.json`（1,392,291 字节），包含 100 条记录及 100 个唯一 instance ID，保持原文件顺序。默认 500 条数据文件、数据集适配器和现有配置未改动；子集尚未通过实验配置接入。原数据中的评测专用字段保留在数据副本中，后续仅由控制/评测侧读取，不将完整文件交给 agent。
当时 Trae 仅完成复制；第 5 批已新增 run_free 的 Trae manifest/适配器/配置，见文末。turn_control 方法及控制接口仍未实现。

## run_free 多 agent 接入工作线

当前目标是在不修改 `methods/run_free/` 行为的前提下，新增 mini-SWE-agent、Trae 和 OpenCode 三个可显式选择的 agent。三个 agent 均通过公共 `Agent.run`、Workspace 和公共模型配置接入；适配层优先使用上游现成 CLI 与机器可读 trajectory/JSON，原源码保持不变。

源码准备批次已完成：`agents/trae/upstream/` 沿用此前复制的 108 个文件；本批将根目录 mini-SWE-agent 的 221 个 Git 跟踪文件复制到 `agents/mini_swe_agent/upstream/`，将 OpenCode 的 6,631 个 Git 跟踪文件复制到 `agents/opencode/upstream/`。未复制三个来源仓库的 `.git/`、未跟踪文件或缓存，未生成来源清单、哈希或 provenance 记录。

本批没有新增 manifest、适配器、实验配置或测试，也没有运行第三方程序、安装依赖、构建、启动容器或调用模型。根目录三个来源仓库暂时保留；只有三个 agent 完成适配和经授权验证后才删除。后续按 agent 分批实现启动、模型映射、trace/usage、补丁产物与错误映射，并在每批完成后停止等待 review。

### mini-SWE-agent 启动适配批次

已新增 `agents/mini_swe_agent/adapter.py`、manifest、`experiments/run_free_mini_swe_agent_verified.toml` 和定向离线测试。只读加载不导入上游或 LiteLLM。运行使用任务环境内显式指定的源码版 CLI，叠加上游 `mini.yaml` 与单次 JSON-as-YAML 配置，完整 run_free prompt 作为 task 传入；保留上游系统/实例模板、工具和默认成本限制（3 美元），不将其称为无预算的原生 agent。通过 `--yolo --exit-immediately` 和空 stdin 无人值守执行，关闭首次交互配置并使用每次调用独立的全局配置目录。模型密钥变量在任务进程内映射为厂商变量，不读取宿主凭据、不写入配置。

每次保存 prompt、配置、完整 trajectory、stdout/stderr 与 patch.diff；正常及非零退出后通过 Workspace 采集 git diff。超时保留部分 trajectory/日志并写空 patch，不读取仍可能被内部进程修改的工作区；容器内进程终止依赖既有数据集清理。缺失/损坏轨迹、缺失完成状态和非 Submitted 状态均报告错误。tokens_used 是轨迹可得 total_tokens（或 input+output）的已知和，exec_count 是轨迹 actions 条目数，既非 corrected，也非已经复现的 run_free original 分析口径；缺失 usage 不表示零实际消耗。

GPT Responses、Claude Messages 和 DeepSeek Chat Completions 已做配置映射；Qwen3-Coder-Next 需显式地址，Qwen3.8-Max 的 Responses 映射暂拒绝。配置兼容不代表 LiteLLM/厂商真实兼容。mini 启动批次当时只接线 Responses，original 尚缺；第 4 批已补齐三协议接线与 original 兼容统计，见下节。未开代理的 trajectory 不作为 corrected 输入；公共自动汇总现已接入 Codex 和 mini，mini 尚未经运行验证。

验证：tokenAna 环境中 5 项定向离线测试通过；10 条 CLI dry-run 成功，标记 plan_only/runtime_ready=false，明确缺少 executable。未运行真实 CLI、网络代理、模型服务或容器；未安装、构建、修改第三方源码或删除根目录来源。

### mini 双口径补全（2026-09-19，第 4 批）

- mini 适配器接通公共 Responses、Chat Completions、Anthropic Messages 代理和 raw usage reader；启用采集时预检协议、地址和 model_class，original 不再因未接入而阻塞。协议支持仍受现有 provider 映射限制，Qwen Responses 等未支持组合继续拒绝。
- original 从原生 trajectory 的 messages[].usage 或 extra.response.usage 提取 input/output（兼容 prompt/completion 字段），投影为现有方法统计输入。保留 run_free 的非空原 patch、trace 存在筛选、单次尝试及向下取整均值；不读取 API 记录，不改变 AgentResult.tokens_used。缺失 usage 沿用该兼容规则忽略，非法数值/损坏轨迹标记不完整。
- 公共接入新增 original_compatible_trace_formats 和 original_accounting_variant；报告规则带 `+mini-trajectory-compatible-v1`，明确是新 agent 兼容口径，并非作者历史结果。原方法文件及公式未修改；跨规则比较不会输出节省比例。
- Anthropic 配置启用上游已有 `set_cache_control="default_end"`，由原生逻辑添加显式缓存标记；preflight 区分已配置和已命中。保留上游提示词、工具、成本限制与重试行为。实际 LiteLLM 转发和缓存标记待真实组件验收，corrected 仍只读取原始 HTTP usage。
- 新增 `experiments/run_free_mini_swe_agent_accounting.toml`：10 条任务、开启采集、假服务地址及预期镜像内 executable；路径须匹配用户准备的 Linux 镜像。此文件不是镜像准备或执行授权。
- 按要求未写/跑测试、dry-run 或真实组件；仅阅读源码与改动文本，未安装、构建、启动容器或调用模型。下一批候选为 Trae 适配，需单独授权。

### Trae 启动与双口径接入（2026-09-19，第 5 批）

- 新增 `agents/trae/adapter.py`、manifest 和 `experiments/run_free_trae_accounting.toml`。调用任务镜像中原版 `trae-cli run --file ... --config-file ...`，只读导入不加载上游依赖。原方法、原 Trae 源码与公共统计公式均未修改。
- 本批限定 OpenAI provider + Responses；其他模型/provider/协议在配置阶段明确拒绝，不能据此宣称五模型已兼容。Anthropic 原客户端尚无本接入所需显式缓存配置，Chat provider 映射也待后续。未翻译协议或静默切换模型。
- 保留原系统提示、四个默认工具、默认 200 步（可显式配置 max_steps）、示例中的输出上限/采样参数/重试设置和 Lakeview。主任务与 Lakeview 使用用户选定的同一模型及代理端点，但分别实例化模型配置，避免 Lakeview 修改温度影响主任务；两者共享 native provider 配置以解析同一密钥变量。不配置可启动外部服务的可选 MCP，不使用 Trae 内层 Docker。
- 每次调用保存 prompt、无密钥 config.yaml、trajectory、stdout/stderr、原生 native.patch.diff 以及供既有收集链使用的 git diff。超时保存部分日志与空正式 patch，容器内子进程清理仍待真实运行验收。非零退出、无结束时间或 success 非 true 都产生 AgentResult.error；run_free 的现有结果筛选不变。
- original 从原生 llm_interactions 的 response.usage 读取 input/output，不重复读取 agent_steps 中的同一 usage；投影后复用原方法的非空 patch 筛选与整除均值。规则后缀 `+trae-trajectory-compatible-v1` 标记兼容口径。AgentResult.tokens_used 为原生日志 input+output 已知和，exec_count 为已记录 tool_results 数量；两者都不冒充 corrected。
- corrected 从代理原始 HTTP usage 汇总，包括经过同一代理的主任务、Lakeview 和实际重试；original 的原生交互日志不包含 Lakeview 独立消费。未观察到的路径/会话仍按公共覆盖边界说明，不能宣称所有消耗均已验收。
- 示例固定前 10 条、假服务地址、开启采集和预期 `/opt/tokenana/trae-cli`；须由用户准备对应 Linux 镜像。未写/跑测试或 dry-run，未运行真实 CLI、代理、容器、模型或评测；只做原接口及新增文本审阅，未安装、下载、构建或提交 Git。
- 本批不实现 turn_control；下一候选批次为 OpenCode 启动/统计适配。待用户授权后继续，根目录来源副本继续保留。

### OpenCode 启动与双口径接入（2026-09-19，第 6 批）

- 新增 `agents/opencode/adapter.py`、manifest 和 `experiments/run_free_opencode_accounting.toml`；只读导入不加载上游。通过预构建 `opencode run --format json --agent build --auto` 从 stdin 传入完整 prompt，保留原生 build 提示词、工具、子任务和压缩逻辑。未改原方法、原 OpenCode 源码或统一统计公式。
- 显式映射 OpenAI/Responses、Anthropic/Messages、DeepSeek 与 DashScope/Chat Completions 到原版已内置 SDK；其他组合拒绝。Anthropic 按 AI SDK 的 base URL 约定补 `/v1`，保留原生 cache_control 逻辑。主模型和 small_model 都使用选中模型；具体厂商兼容、usage 完整性和缓存命中均未运行验证。
- 必须提供正整数 `context_limit`、`output_limit`（后者不大于前者），不猜测模型限制或依赖自动下载模型目录。示例有意未填写这两个未知值，预检会报告阻塞；也须准备 executable 和 config_root，并不能直接宣称一键运行就绪。
- 原版配置初始化可能调用 Npm.install。适配器启动前要求 `config_root/opencode` 为只读目录且仅含预先准备的 `.gitignore`，HOME 中无 `.opencode`，PATH 中已有 rg；不满足则不启动 CLI。依据原版逻辑，不可写目录直接跳过依赖安装，已有 rg 避免自动下载。SDK 只用内置包；用户须在另获授权的镜像准备批次提供这些条件，本批不创建镜像或安装依赖。
- 每次调用隔离 XDG data/cache/state，禁用自动更新、模型目录抓取、项目配置、默认外部插件/技能、LSP 下载、formatter、FFF 和分享；不配置 MCP。原生运行范围因此为上述受限配置，不能称为完整默认桌面环境。认证仅以 `{env:变量名}` 写入配置，变量值不由适配器读取或写入参数。
- JSONL stdout 和 stderr 直接落盘，保留超时/中断记录；完成后采集 git diff，超时保留空正式 patch。原生 session error、缺少 stop step 和非零退出分别记录错误。实际进程清理、只读挂载及网络行为待 Linux 验收。
- original 只读取主会话 JSONL 的 step_finish，按 sessionID/part ID 去重；根据原源码，input = ordinary input + cache read + cache write，output = ordinary output + reasoning。复用方法的非空补丁筛选及整除均值，规则后缀 `+opencode-step-compatible-v1`。损坏记录/重复冲突明确报告不完整；不把原生日志当 corrected。
- corrected 使用公共原始 HTTP reader，包含经过同一代理的子任务、标题/压缩及重试请求；原生 stdout 不保证包含这些辅助请求的 original usage。未经过代理的调用不可见，仍保留公共覆盖限制。
- 按用户要求未写/跑测试、语法检查、dry-run 或真实组件，仅阅读原接口和新增文本。未安装、下载、构建、监听端口、启动容器、调用模型或提交 Git。两份文档同步，根目录三个来源副本继续保留。
- 本批结束；下一候选批次为 turn_control 公共逐轮控制接口设计与实现，须先展示接口及兼容性并获得用户确认，不顺带实现方法或执行真实组件验证。
