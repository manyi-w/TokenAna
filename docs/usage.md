# TokenAna 使用与当前边界

## 六方法接入完成：当前入口（2026-09-20）

六方法、四 agent、Verified/DeepSWE 的兼容实现及双口径/overhead 已接线，参见 [完整交付与限制](method-integration-delivery.md) 和 [52 份配置模板](../experiments/method_matrix/README.md)。下方第 2/3A 批“仍待接入”的描述仅保留为历史。本轮 125 项离线测试中 119 通过、6 项网络测试跳过；48 逻辑组合的统计夹具与 52 份 CLI dry-run 通过，不等于真实组件或 GPU 就绪。

当前方法依赖、SWE head 和执行器须由已准备环境提供；缺项预检明确 blocked，未选择的组件不加载。使用 `python tokenAna.py run experiments/method_matrix/<组合>.toml --dry-run` 检查配置，或 `python scripts/dry_run_method_matrix.py --output /absolute/new/plan-directory` 批量规划。先激活 tokenAna；本轮不安装、构建、启动容器或调用模型。Codex 新 Chat Completions 路径需要独立 session 构建，源码/补丁已提供但 Rust 未编译。

## mini 通用会话兼容接入（第 3A 批）

mini 已接入 `run_session(prompt, workspace, options, callback)`，版本 `mini-session-compatible-v1`，可与既有 turn_control 组合；其他 agent 待后续批次。普通 run_free/turn_control 入口保持可用，四个新方法仍无可运行适配器，本批没有新增方法实验配置。

会话路径要求预备镜像内的绝对 `agent.options.python_executable`，以及 `record_raw_usage=true` 和显式模型协议/地址；可选 `session_timeout` 为 worker 等待一次 callback 的秒数（默认 60，范围 >0 且 ≤3600）。不满足则预检/执行拒绝，不安装依赖。callback 在宿主执行，借已有共享目录传递同步事件，不向 agent 开放额外网络。callback 的辅助请求须有自己的超时，worker 等待超时不能强制抢占宿主 Python 函数。

新增调用产物：

- `session-version.json`：兼容版本与证据位置；`session-channel/`：文件请求、响应和关闭状态；`session-outcome.json`：完整性/终止结果。
- `session/`：原始事件、方法状态、历史替换前后及原生 prepared_messages。SDK 发送的真实请求以 `api-records/*/request.body` 为准。
- `trajectory.json`：实际使用的压缩历史；`original-trajectory.json`：未压缩原生消息及统计证据。会话的 original reader/调用计数读取原轨迹，缺失时报告异常，不从压缩历史补猜。

工具输出普通文本可改写，工具 ID、参数、协议/不透明内容受保护；可删除已配对的完整步骤，不能移除尚未执行的工具调用。after_tool 在原生一批动作的结果全部入历史后触发；提醒在下一次模型请求前添加。terminate 会产生非成功结束状态，补丁仅作诊断；需要有效提交时应通过 reminder 提示原生完成。

本批通过 91 项离线测试，另 6 项网络测试跳过；mini 10 条 dry-run 成功。真实 mini/LiteLLM、容器共享目录与模型服务未验收，/app、/testbed 只做了模拟交接验证。详见 [开发流程](development-workflow.md)。

## 公共会话与 overhead（第 2 批）

公共能力已实现并通过离线测试；四个新增方法仍只有 upstream 原源码，mini 已在第 3A 批完成兼容接线；其余真实 agent 的通用会话映射待逐批接入。不要据此配置新方法运行。旧 Method.run/Agent.run 调用保持可用，要求通用会话能力的方法会在预检拒绝未接入的 agent。

run/resume 自动报告及离线 analyze 新增：

- accounting.json 的 overhead 节（整体及逐 case），包含 original 作者分项和 corrected 用途分组。作者规则尚未提供或旧记录无法判定用途时显示未知，不能读作零。
- overhead.csv、overhead-cases.csv；accounting.txt 和 accounting.md 同步展示。compare 另有 overhead.txt，终端、comparison.json/Markdown 及两个 overhead CSV 同步。
- corrected 分组 main、agent_auxiliary、method_auxiliary、compression_service、unknown；method_overhead 是方法辅助与压缩操作的汇总，all 已含全部分项，不能重复相加。input/output/total/cache_read/cache_write/reasoning，以及 calls/service_seconds 均有 sum/mean/complete/reasons，均值沿用统一实际调用 case 分母。

calls 指 HTTP 尝试和服务操作，不能当成 agent 轮数；service_seconds 为操作耗时加总，嵌套操作可能重叠，不能当成墙钟耗时。压缩前后文本 token 仅为效果信息，不替代推理 usage。旧实验可 analyze 重建已有记录；缺少归属证据仍保持 unknown。所有分组完整性都受实际录制覆盖限制，原生辅助调用分类要等待 agent 适配。

开发接口：方法声明 required_session_capabilities 并调用 agent.run_session(prompt, workspace, options, callback)；原生适配器通过 Session.emit 映射五类事件，应用 SessionDecision 后保存 record_model_input，再向模型发送原生请求。Message.native 等协议字段不可编辑，只有明确开放的 text 可替换。辅助模型使用 recording_model_channel(..., channel_name="唯一名称", attribution={"purpose": "method_auxiliary", "parent_call_id": "父调用ID"}) 并显式传入服务地址；框架没有新增默认辅助地址或自动加载依赖。

压缩操作用 record_service 保存实际 raw_usage/protocol/provider；没有实际 usage 就保持未知。inference=False 只允许推理已经通过独立通道记录的编排操作，禁止同时写 raw_usage。效果数值写 effects，不能放入 token 消耗。原生轨迹、session 事件、实际请求载荷与压缩前后快照分开保留。

本批验证：88 项离线测试中通过 82 项、跳过 6 项本地网络测试；10 条 Verified dry-run 成功且 runtime_ready=false。真实 agent、模型服务、GPU/Rust 编译、DeepSWE 真实组件及完整矩阵均未验收。批次记录见 [开发流程](development-workflow.md)。

## 四个新增方法：仅源码准备完成（2026-09-20）

SWE-Pruner Pro、AgentDiet、AttnCompress、EET 的原始实现已分别置于 `methods/swe_pruner_pro/upstream/`、`methods/agent_diet/upstream/`、`methods/attn_compress/upstream/`、`methods/eet/upstream/`。配置、已有许可、经验库及结果归档保留；根目录原件暂时保留。

**这些目录尚未提供 TokenAna manifest、适配器或可用实验配置，请勿作为 method.path 运行。** 公共 overhead 已于第 2 批实现；六类方法 × 四个 agent × 两个数据集的具体接入、作者特有统计及 48 组合离线验收仍待后续批次。本批只检查复制结构，没有运行方法、测试或 dry-run；不代表已具备 Qwen head、GPU 服务或真实组件验证。完整约束与下一批范围见 [架构](architecture.md) 和 [开发流程](development-workflow.md)。

本页描述已实现入口，不代表已经完成运行验收。最近的补全代码没有写或运行测试。

## DeepSWE 准备状态

第 6 批静态交付已完成：[20 份实验配置与操作说明](../experiments/deepswe/README.md)、[静态审查与未验证清单](deepswe-delivery.md)。覆盖四个 agent 的 Python run_free、多语言 run_free_multilingual、全部任务 turn_control，并提供 Python 34 / 非 Python 79 的同集合控制比较配置。runtime.template.toml 镜像值为空、OpenCode 模型限制待填，均明确阻塞未准备运行。

Codex 第 5B 批已将获批补丁应用到独立 `agents/codex/controlled/`，控制运行与原生摘要已接线；原 upstream 保留。构建尚未执行，须另行准备控制版二进制；具体入口见 `agents/codex/control-build/README.md`。初始化代理门禁防止普通二进制忽略控制变量后直接请求模型。所有新增运行兼容性保持未验证。

已完成原样复制及第 2、3 批适配：任务位于 `datasets/deepswe/data/tasks/`，原 Pier 位于 `datasets/deepswe/upstream/pier/`。共 113 个任务（Python 34、Go 34、TypeScript 35、JavaScript 5、Rust 5）。

**已有任务/工作区/补丁/本地评测、Python/多语言方法和隔离模型通道接线；运行兼容性未验证。** 缺少通道配置时预检拒绝启动；四个 agent 的 turn_control 已接线；配置矩阵已交付，原生运行仍未验证。接口存在不代表镜像或服务已可用。

数据集配置片段（不是完整可运行实验）：

```toml
[dataset]
path = "../datasets/deepswe"
[dataset.options]
languages = ["python"]
# task_ids = ["实际任务 ID"]
# limit = 10
# data_path = "/absolute/path/to/tasks"
```

语言与 task_ids 取交集，按 ID 排序后应用 limit；未设置筛选时选择全部任务。未知语言/ID 会报错。agent 只接收公开指令及 `/app` 仓库，不挂载完整任务包。DeepSWE 收集 `git diff --binary <base_commit> HEAD`：agent 必须自行提交；未提交修改只作诊断，不自动提交，也不用回答文本补丁回退。

预备运行时需要 `network = "none"`、按任务 ID 配置的 `images` 和 `verifier_images`，以及镜像内绝对路径 `verifier_python`。镜像不可自动拉取或构建。verifier 需 root、Python 3.12+、Pier distribution metadata/依赖和原 `/tests` 文件；agent 需 GNU timeout。Docker 需支持任务 storage_mb 的可写层配额，挂载日志不计入该配额。镜像应包含干净的任务 base commit；agent 不得内置隐藏测试或参考解。

方法选择：Python 使用 `methods/run_free`，非 Python 使用 `methods/run_free_multilingual`，并将 languages 设置为 `["go", "typescript", "javascript", "rust"]`。语言与方法不匹配时明确报错。当前 run_free 允许 Git（版本 `run-free-git-v2`），其他 Python 限制保留；多语言版本禁止测试、构建、程序执行和依赖安装。这些是提示词约束，不是执行工具级封锁。

DeepSWE 要求 agent.options.record_raw_usage=true 和明确模型地址。运行时还须提供以下片段（不是完整配置；Python 路径需按预备镜像填写）：

```toml
[model_channel]
kind = "unix_socket"
python = "/opt/tokenana/bin/python3"
```

必须使用 Linux 上同机 Docker，容器保持 network none。agent 通过回环端口和只读挂载目录内的 Unix socket 访问宿主 recorder；中继只转发字节，保留原 recorder 的协议限制，verifier 不获得该通道。镜像需已具备所指定 Python 3，不会自动安装。原 Verified host 模式保持原路径。

每次调用新增 `final-prompt.txt` 和 `prompt-metadata.json`，记录实际完整提示词、方法版本和数据集提交说明；中继另存 `model-channel.ready.json`、日志及停止标记。新运行 state 保存方法版本，旧 run_free 无版本运行不能用新规则继续生成，可离线 analyze 或新建运行，避免混合 Git 行为。

已有运行保存本地 submission 计划后，可使用以下入口；本轮未执行这些命令：

```bash
python -B -m tokenAna evaluate <run> local
python -B -m tokenAna evaluate <run> local --execute
```

第一条仅读取计划，第二条显式启动独立离线 verifier，调用未修改的 Pier Verifier。保存原 reward、CTRF、日志和逐任务结果；reward 1/0 对应 true/false，-1、超时、报告缺失等异常对应 null。再次显式执行复用相同补丁的已完成结果，重试失败任务，不重新生成补丁。强制终止留下 `evaluation/local.lock` 时，应先检查并停止残留容器，再人工移除锁。关联评测后需重新 `analyze` 才得到包含新结果的分析版本。

本批没有测试、语法检查、dry-run、监听端口、构建、容器或模型调用；隔离通道、流式/并发转发、资源限制、超时清理、镜像及 Pier 运行均未验证。后续比较将为 Python 34 条和非 Python 79 条分别提供相同任务集合配置。详见 [批次状态](development-workflow.md) 与 [接入设计](architecture.md#deepswe-接入)。

## 一次运行

先激活环境：

```bash
source /Users/manyi/miniconda3/etc/profile.d/conda.sh
conda activate tokenAna
```

在实验 TOML 中选择 dataset、model、agent、method；已准备 Linux 镜像和假模型服务后：

```bash
python -m tokenAna run experiments/run_free_trae_accounting.toml --runtime experiments/runtime_verified_10.toml --output runs/example
```

该命令完成生成、original/corrected 统计及提交材料准备，不自动执行评测。运行时镜像必须包含所选 agent；现有 Codex 镜像不能自动视为 Trae/mini/OpenCode 镜像。

- `run --dry-run`：列出组件、模型/方法/统计兼容性与缺项，不调用模型。
- `resume <实验TOML> --runtime <运行时TOML> --output <已有运行>`：校验快照后恢复。
- `analyze runs/example`：依据已存记录创建新的分析目录，不覆盖原产物。
- `compare runs/baseline runs/candidate --output comparisons/example`：基线为第一个输入，输出 JSON、CSV、Markdown；也接受独立分析目录。
- 开启 `agent.options.record_raw_usage=true` 才采集 corrected 所需的原始 API usage；不开启时缺失项标为不完整。

## 可选择的组件

| 组件 | 当前实现 |
| --- | --- |
| method/run_free_multilingual | 静态多语言修复，允许 Git，复用 run_free 聚合规则；运行未验证 |
| method/run_free | 单次 agent 调用，适配层允许 Git，保留其他 Python 限制 |
| method/turn_control | mini/Trae/OpenCode 同会话 P50→P75 一次扩展，逐轮提醒；仅完成且非空补丁可提交，运行未验证 |
| dataset/swe_bench_verified | 默认 Verified 数据，独立官方提交链 |
| dataset/swe_bench_verified_subset | 原样保存的 100 条子集，可用 limit 取前 10 条 |
| dataset/repository_tasks | 用户本地 JSON 修复任务；复用 /testbed 预备容器，输出本地 patches.json，不假设评测器 |
| dataset/deepswe | 113 条本地任务、/app、已提交补丁、隔离通道及本地 Pier 评测接线；运行未验证 |

repository_tasks 配置需提供绝对路径 `dataset.options.data_path`；JSON 数组的每项必须包含 `instance_id`、`repo`、`base_commit`、`problem_statement`。其他字段不进入 Task 或 agent 输入。该适配器不是另一个已下载并验收的 benchmark。

| agent | Responses | Anthropic Messages | Chat Completions |
| --- | --- | --- | --- |
| Codex | 已有直接协议接线 | 不支持 | 不支持 |
| mini | OpenAI | Anthropic，显式缓存 | DeepSeek / DashScope 等已映射 provider |
| Trae | OpenAI / DashScope | Anthropic，系统前缀缓存 | DeepSeek / DashScope，直接复用原 Chat 客户端 |
| OpenCode | OpenAI | Anthropic，原生缓存 | DeepSeek / DashScope |

表格只表示实现的配置映射，所有新增链路尚未运行验收。缺少地址、服务不支持工具调用、模型或协议不匹配均不能视为可用；不做协议转换。mini/OpenCode 的 DashScope Responses 组合仍明确拒绝。Codex 原生不支持的协议不通过修改原源码强行接入。

## turn_control

Trae 示例：`experiments/turn_control_trae_accounting.toml`。mini 可选择同一方法与预算，并将 agent 指向 `agents/mini_swe_agent`；完整 DeepSWE 配置矩阵见 experiments/deepswe。两者都需 `agent.options.python_executable` 指向镜像内已安装对应原 agent 包及依赖的 Python。

mini 控制路径将子类复制到本次 artifacts，通过原 CLI --agent-class 加载，沿用原模型、工具、配置合并、完成判断和轨迹逻辑。普通 mini 路径仍走原 CLI。镜像无需安装 TokenAna；不会在运行入口安装任何依赖。

mini 的主循环 query 计一轮，HTTP 重试和同轮工具调用不额外计轮；保留原费用、step_limit、wall_time 和连续格式错误限制，所以它们可能在 P50/P75 前停止。仅原生 Submitted、控制记录一致且补丁非空时提交；预算耗尽和失败补丁只作诊断。control.json 保存预算、轮数、一次扩展和退出原因，恢复遵循 turn_control 的不重新生成规则。

turn_control 原生统计通过公共最终摘要读取器接入四个 agent，有最终摘要与函数调用计数即纳入，不再绑定 trae.summary；只报三项总和，均值固定 null。mini 缺失原生日志 usage 记为未知，不用 HTTP 统计替代；corrected 仍独立读取全部原 HTTP 记录。当前方法版本 turn-control-native-summary-v3，无版本旧运行可 analyze，但不能混用新版本继续生成。以上均只静态审阅，运行未验证。

OpenCode 控制运行使用本地 `turn_control.mjs` 插件，不需要 python_executable；仍须提供原 executable、只读 config_root、context_limit、output_limit 和显式模型配置，并开启 record_raw_usage。只有该控制插件会被加入 plugin 列表，普通运行仍为空；不自动安装插件或包。

插件通过原生会话数据区分主循环、子会话和压缩，只在主采样前计轮、提醒、扩展或阻止请求。control.json 保存主会话 ID、每轮 assistant ID 与提醒；运行结束后原 CLI export 保存 session.json，另存 process.json、control-result.json、export-stderr.txt。超时未确认进程停止时不导出，恢复仍不重新生成。

OpenCode 最终摘要来自导出的原生根会话 step-finish 和工具记录（根会话压缩步骤包含在原生 token 总数中）；按 part ID 去重，缺失 usage 保持未知。子会话、标题等可观察辅助消费继续进入 corrected 原 HTTP 统计。就绪门禁仅阻止插件未加载时的模型转发，不用 HTTP 请求数替代轮数。

插件 hook、原 SDK 查询、终止异常传播、原生 export 和完整运行均未执行验收。

Codex 控制路径使用获批补丁的独立构建，不需 python_executable。以下仅是选项片段，路径必须对应用户预备镜像；不是构建或执行授权：

```toml
[agent.options]
executable = "/opt/tokenana/codex"
controlled_executable = "/opt/tokenana/codex-controlled"
control_version = "codex-sampling-boundary-v1"
record_raw_usage = true
```

公共预检仍要求 executable；控制调用只使用 controlled_executable。预算继续在 method.options.budget_profile 显式选 gpt / claude / gemini，不扩展原模型协议。运行时不构建、不安装，镜像需 GNU timeout；原生 CODEX_HOME 独立放入本次 artifacts/codex-home 保存日志。

Codex 控制日志、原生根 rollout、CLI JSONL 和进程状态共同判定完成；error/turn.failed/abort 或预算耗尽不能因结束事件而变为成功。原生摘要避免读取缺 usage 的默认零；无法佐证覆盖、压缩或响应数失配时保留 null，corrected 仍读取全部可观察 HTTP 尝试。只支持此源码副本的未压缩根 JSONL；文件缺失/压缩/损坏报未知。

capture-result.json 区分空补丁、捕获失败及进程停止未确认。只有原生完成、控制记录一致且公共捕获非空时提交；model.patch 与诊断保留，不自动 Git 提交或重新生成。构建、原生 hook/日志、初始化门禁、超时清理和全部运行兼容性仍未验证。

- `budget_profile` 显式选择 `gpt`（50→67）、`claude`（52→64）、`gemini`（29→45）。预算档位不自动改选模型；Gemini 档位存在不等于 Google 协议已接入。
- 控制新增子类 hook，复用原执行循环/提示词/工具/完成判断；逐轮附加预算消息，初始预算耗尽且未完成只扩展一次。
- 中断生成不自动重跑。已保存 generation 或已返回的 call 可继续收集；未开始任务继续执行。未完成 patch 保留为诊断，不进入预测文件。
- `control.json` 保存初始/最终/当前预算、已用轮数、扩展事件、工具调用数和终止原因。
- original：以最终原生 summary 兼容原脚本的最新日志纳入逻辑，不筛补丁/成功；只输出 token 总和，**均值始终 null**。并非作者历史日志逐字复现。corrected 保持统一全量纳入和 case 分母。

## 评测与联合分析

```bash
python -m tokenAna evaluate runs/example submit
python -m tokenAna evaluate runs/example submit --execute
python -m tokenAna evaluate runs/example fetch --execute
python -m tokenAna evaluate runs/example attach --report /absolute/path/report.json --run-id SAVED_RUN_ID
python -m tokenAna analyze runs/example
```

`submit/fetch` 不带 `--execute` 仅显示已存命令；`--execute` 才执行远程命令。本轮未执行任何这些命令。`attach` 是本地写入关联，不访问网络。新运行生成唯一 run_id，实际值在 submission.json 中。

- 各次命令保存 stdout/stderr、退出码及状态；不把命令返回成功当作评测完成。不自动重试可能已提交的请求。
- fetch 保留原 sb-cli 输出及生成文件；输出格式未经真实服务验收，不猜测哪段日志就是评测 JSON。使用 attach 显式关联报告。
- attach 校验 run_id（报告包含该字段时）、任务 ID、重复/矛盾结果及计数；报告缺少 run_id 时关联依据是用户显式指定的 run_id，不宣称已验证服务签名。
- 未报告的 case 保持 resolved=null；分别展示 resolved/selected 与 resolved/completed，绝不混合分母。报告变更后旧 accounting 会被 compare 拒绝，先 analyze 重建。
- 分析导出 accounting.json/txt/csv/md、cases.csv、evaluation.csv；比较导出 comparison.json/md、summary.csv、cases.csv、evaluation.csv。

## 必须由运行环境提供的条件

- 每个任务镜像已有对应 base commit 的干净 `/testbed`、所选源码版 agent 及依赖；框架不安装、构建或拉取。
- API 采集支持原 Linux host 网络和 DeepSWE 的 Unix socket 隔离通道；任务进程内提供配置指定的密钥环境变量。Qwen 服务地址由用户地域/Workspace 决定。
- OpenCode 还需填写模型 `context_limit` 与 `output_limit`，准备只读 `config_root/opencode`（仅 .gitignore）、PATH 中的 rg 和内置 SDK。示例不猜测这些值。
- WebSocket、请求分块编码、绕过代理的路径及未暴露 usage 仍不在采集覆盖内。未知细项保持 null；没有实际 API 记录就不能保证消费完整。
- 原生组件行为、端口转发、镜像与服务兼容以及评测链尚未验收；这轮未写/跑测试、安装依赖、构建、启动容器或调用真实模型。

## DeepSWE 第 6 批补充边界

Trae 通过原生 base_commit 检查已提交差异，保留测试文件过滤及 task_done，最终资格使用公共捕获；较低的原生步数限制不会被预算放宽。缺失原生 usage 不当零，实际提示版本、patch rule 和 native summary source 会写入报告及逐 case CSV。run_free 原统计规则、corrected-v1 和其他工作线 overhead 规则不变。

本地评测即使得到 reward 0/1，也须存在可读原 CTRF；缺失或损坏报告为评测异常，resolved=null，恢复/附加报告不能绕过这一检查。模型 socket 和 agent 凭据不进入 verifier。

本工作线本次授权内的代码/接口和静态收尾已完成，未执行测试、dry-run、构建、容器或模型；实际运行兼容性仍未验证。
