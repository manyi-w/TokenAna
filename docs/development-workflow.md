# TokenAna 扩展开发与会话交接

## 六方法原统计口径复查（2026-09-20）

用户要求最终检查问题是否只有缓存加减和未提交 patch 的 case 漏算。本轮完成本地原源码、分析脚本、notebook 代码单元与兼容统计的静态审阅，结论与具体证据见 [架构文档复查记录](architecture.md#六方法原始-token-口径复查2026-09-20)。另外确认最后一次尝试筛选、辅助推理统计范围、固定补偿/缺失 usage 当零，以及局部报表比例分母问题；这些问题按方法/agent/路径分别适用，不是六方法共有错误。

特别更正：run_free 是非空 patch 筛选，不等于成功提交/解决；turn_control 是最后摘要筛选；AgentDiet 和 AttnCompress 的上述原分析保留已有失败日志。AgentDiet 已有独立辅助开销统计；AttnCompress 服务文本 old/new_tokens 不代表推理消耗。AgentDiet 分语言 O 比例错误只在对应 export_cat 表确认，不泛化到所有报表。

本轮只更新这两份交接文档，没有修改方法、agent、统计代码、数据或评测器，没有执行测试、dry-run、容器或 API。未量化真实实验偏差，未扩大后续执行授权；若继续实施修复，须遵守原源码保护和批次约定，保留 original 与 corrected 的既定区分。

## DeepSWE 单命令入口交付（2026-09-20）

本批授权为用户明确的 PLEASE IMPLEMENT THIS PLAN：实现参数化入口并测试，真实实验由用户启动。入口为 `bash scripts/run-deepswe-pilot.sh`，支持 method/model/agent/case/cases/jobs/output/resume/dry-run。默认四方法、同一任务、52 个有效组合；Linux 可指定20条与并发。配置已接线，填入 api_key_env 的字面凭据仅在内存转换成引用；本地配置和 secrets 文件权限均为0600，构建上下文不包含它们。

本批完成镜像准备代码、运行/评测/汇总编排、失败留存、采样边界工作目录增量快照、容器归档后清理、阶段与方法耗时、双口径结果、固定任务清单、恢复锁及活动容器检查。详细使用/恢复边界见 [deepswe-pilot-cli.md](deepswe-pilot-cli.md)。未修改原来源；Codex 自有控制 hook 扩展已单独保存可重建补丁。

验证为 tokenAna Python 3.12.14 下138项离线测试，132通过、6跳过；CLI dry-run、shell语法与Python AST检查通过。尝试回环假服务测试被沙箱禁止bind，提权请求被用户拒绝，没有继续；因此网络测试不算通过。尚未构建镜像、编译Rust/Bun、启动实际agent/评测容器或调用真实API，无Git commit。后续以用户实际命令的 launcher.log、build日志及保存产物排查，不自动替用户启动52组合实验。

## 六方法工作线本次连续授权已完成（2026-09-20）

用户“剩下的一次性完成吧，不用问我了”已授权完成剩余实现、离线测试/dry-run 和符合条件的根目录清理，无需在剩余批次间再次确认。详细交付见 [method-integration-delivery.md](method-integration-delivery.md)。已完成源码归档补全、公共/agent 会话适配、四方法组件、48 组合/52 配置、双口径与 overhead、文档和九个根目录重复目录清理。没有 Git commit。

验证：tokenAna Python 3.12.14 下 125 项离线测试，119 通过、6 项本地网络测试未开启；52 份 CLI dry-run 成功但 runtime_ready=false，缺少依赖/head 按预检报错。Codex 独立 session-chat.patch 的适用性 dry-run 通过。根目录清理后重新运行同一离线套件和矩阵规划。此工作线不再有待授权的离线实现批次；Rust 编译/格式化/原生 Rust 测试、真实组件、Linux/容器、GPU 和模型效果是明确未执行项。

下方其他 Mac 先期实验任务记录保留；其短 SHA 读取阻塞已由本工作线修复，其他真实环境阻塞及授权不能从本轮离线结果中推导。不要安装依赖或自动启动真实试验。

## 当前先期实验请求：Mac 单条、65 组合预检（2026-09-20）

用户明确选择同一条 DeepSWE 数据的全部 65 个组合及真实 API；四模型、五方法和 Codex 限制见 [预检说明](deepswe-mac-pilot-readiness.md)。本批只检查运行前提、执行单条只读规划并建立用户自行填写的 `config/local/deepswe-pilot/settings.toml`、`secrets.env`；两文件 Git 忽略已检查，密钥文件权限 0600，尚未接入 CLI。

检查结论：不能直接运行。Docker daemon 不可达、Mac 通道被预检拒绝、原任务短 SHA 在筛选前被适配器拒绝；完整矩阵、服务/镜像、完整文件归档及逐 case/阶段计时仍未验收。检查期间 AgentDiet/AttnCompress 等新实现已出现在共享目录，不能按旧交接文档断言它们仍只有原源码，也不能将文件存在视为验收通过。本批保留这些其他工作产生的改动。

证据：`runs/preflight-deepswe-mac-20260920/` 保存单条失败 traceback、13 对模型/agent 配置检查及 65 行组合表；组合数/唯一性、本地设置 TOML 解析、权限与 Git 忽略检查通过，实际运行数为 0。未安装、下载、构建、启动容器、模型调用、评测或 Git commit。下一步先 review 公共留存/计时/凭据与任务读取修复批次，随后完成适配与实际环境；具体范围见预检说明，未自动实施后续批次。

## 六类方法与 overhead：第 3A 批（mini 会话）已交付

授权：用户回复“ok继续”，执行上一批提出的 mini 通用会话适配。本批到此停止，不自动进入 Trae/OpenCode/Codex 或新方法实现。

- 新增 agents/mini_swe_agent/session_runner.py、session_mapping.py、src/session_channel.py 和 tests/test_mini_session.py；修改 mini 手写 adapter、公共 Session 的未完成工具配对校验及能力预检。原 mini 源码、原 ControlledMini 文件、方法、数据和评测器均未修改。
- 原 CLI 子类实际应用上下文替换，原工具/结束行为保留；宿主 callback 经已有共享目录同步，不新增端口。缺少依赖运行配置或 callback 初始化时拒绝普通运行。未压缩原轨迹独立保留，用于原统计及控制工具计数。
- 兼容路径 mini-session-compatible-v1；与现有 turn_control 组合仍按原主循环计轮、只扩展一次。方法终止不当作 Submitted，失败/未完成补丁仅诊断，/app 与 /testbed 继续走已有公共捕获接口。
- 离线验证：`TOKENANA_LOCAL_PROXY_TESTS=0 python -B -m unittest discover -s tests` 共 97 项，通过 91 项、跳过 6 项本地网络测试；本批新增 9 项全部通过。测试使用模拟原生循环/模型和 fake 依赖基类，执行真实手写 mixin、控制子类及文件回调通道，不声称真实 mini 包已验证。
- `python -B -m tokenAna run experiments/run_free_mini_swe_agent_verified.toml --dry-run` 成功列出 10 条，plan_only/runtime_ready=false；公开能力列表包含五类 session 能力，但此 run_free 配置未启用 callback。新方法配置尚未提供。
- 未安装、下载、构建、启动容器、监听端口、调用模型或提交 Git；真实共享目录可见性、SDK 请求、进程/阻塞 callback 清理及 48 组合矩阵未验收。源目录继续保留。

已同步架构、流程和使用文档，保留 DeepSWE 并行工作线状态。下一批待授权：Trae 通用会话子类接入及原生辅助调用归属；之后 OpenCode/Codex 和各方法仍单独交付。

## 六类方法与 overhead：第 2 批已交付（2026-09-20）

授权：用户第 1 批交付后回复“ok”，中断后回复“继续”。范围为公共会话能力、调用归属、overhead 聚合与报告；本批完成后停止，未进入真实 agent 或各方法适配。

- 新增 src/session.py、overhead.py、service_usage.py 和两个定向离线测试文件；扩展公共接口、BoundAgent/RecordingAgent、预检、HTTP recorder、辅助隔离通道、原始 usage reader、重建与报告/CLI。只改框架与测试，原方法/agent/数据集/评测器源码未修改。
- 可选 run_session 保留旧调用方式；事件快照、状态、提醒、终止、不可编辑协议字段与工具配对均有契约。能力未声明时明确拒绝。目前真实 agent 通用会话映射仍未实现，下一批须按 agent 分批接入；Codex 独立补丁仍需先展示具体差异。
- corrected-v1 token 公式及现有 original 公式不变；新增用途、模型、父调用、case/attempt、服务耗时及请求计数。unknown 不当作无 overhead，服务未暴露的 usage 不以文本压缩量替代。方法特有 original overhead 保留接口，尚未伪造实现。
- 主请求、辅助通道、服务记录从全部尝试重建并去重；辅助通道隔离状态文件。新增 overhead.csv/overhead-cases.csv，以及 compare overhead.txt；JSON、终端、Markdown 同步，旧报告无分项标记未知。
- 验证命令：`TOKENANA_LOCAL_PROXY_TESTS=0 python -B -m unittest discover -s tests`，88 项中通过 82 项、跳过 6 项本地网络测试。新增 16 项定向测试全部通过。全量回归初次发现旧夹具与已交付接口不符，已修正 tests/test_codex.py、test_models.py、test_mini_swe_agent.py、test_run_free.py 的过时预期/Mock；没有为通过测试改回原生产行为。
- `python -B -m tokenAna run experiments/run_free_codex_verified.toml --dry-run` 成功列出 10 条任务，plan_only/runtime_ready=false；未运行真实组件、网络服务或模型，不安装、下载、构建、启动容器或提交 Git。现有缺失 trace 的两条模拟测试输出预期警告，不影响通过。
- 与下文 DeepSWE 工作线的静态交付记录分开：本工作线的新计划已授权离线测试，但这些结果不构成 DeepSWE 真实链路或 48 组合矩阵验收。根目录来源仍保留，不清理。

下一批待授权：按 agent 接入通用会话事件和原生历史回写，优先 mini 的子类适配；随后其他 agent，各方法依次独立交付。完整接口/统计边界见 architecture.md，使用与产物说明见 usage.md。

## 六类方法与 overhead：第 1 批已交付（2026-09-20）

用户以“PLEASE IMPLEMENT THIS PLAN”批准从原文件准备批次开始；按同一计划“每批……交付差异后停止等待下一批授权”，本批完成后停止。以下 DeepSWE 工作线独立保留其当前阶段与验证限制。

本批仅复制四个来源到方法组件的 upstream 目录，并同步架构、流程和使用文档，没有手写适配实现：

| 新目录 | 普通文件数 | 文件字节合计 |
| --- | ---: | ---: |
| methods/swe_pruner_pro/upstream | 277 | 4,187,001 |
| methods/agent_diet/upstream | 31 | 46,895,757 |
| methods/attn_compress/upstream | 38 | 192,539,513 |
| methods/eet/upstream | 372 | 34,963,067 |

原样保留源码、配置、已有许可、经验库、结果归档及原文件权限；排除 Git 元数据、系统及 Python/工具缓存。AgentDiet 的 trajs.7z 和 AttnCompress 的两个 trajs 归档保留且未解包；原数据目录中的 tokenizer 缓存文件保留。根目录原件没有修改或删除，不做来源核验、哈希比对或 provenance 清单。

验证仅为复制过程成功及目录结构检查：四个目标原先不存在，复制拒绝覆盖；检查文件数量/总大小、README 和结果归档存在，未导入或运行原方法。未执行测试、dry-run、模型请求、下载、依赖安装、构建、容器或 Rust 编译。源码已准备不等于方法可运行，head/服务/真实组件仍未验收。

下一批待授权：公共会话事件接口、调用归属、双口径 overhead 与报告扩展；随后按 agent 分批接入，Codex 补丁先展示具体差异；再依次 SWE-Pruner Pro、AgentDiet、AttnCompress、EET 方法批次及离线矩阵验收。每批维护三份文档并交付可审阅差异，不提交 Git commit。清理须在全部离线验收通过后另成批次，先保存有效未迁移文件并确认入口无根目录依赖。

## 当前工作线：DeepSWE 第 6 批已完成，静态交付收尾

用户授权“ok继续 这次一次性做完”。已完成本工作线剩余配置、比较、统计与文档，不再等待另一实施批次。运行验证不在本次授权中。

- 新增 experiments/deepswe/ 的 20 份实验 TOML、113 任务 runtime 映射模板及使用说明；八对 34/79 同集合比较入口覆盖四 agent，完整 113 控制单独运行。
- 新增 docs/deepswe-delivery.md 静态审查记录；同步架构和使用文档。报告接入实际提示/补丁版本和原生摘要来源，保留已有 overhead 工作线改动。
- 静态发现修正：Trae 已提交补丁被 plain diff 判空、原步数限制被控制放宽、缺失原生 usage 变零；缺失 CTRF 被奖励文件误当完成；补充 verifier_python 预检与直接提交资格检查。只改手写适配层/框架，原组件源码不变。控制方法版本为 turn-control-native-summary-v3。
- 实际静态读取结果：113 个唯一 task ID，Python/Go/TS/JS/Rust=34/34/35/5/5；113 个原补丁命令和双 no-network 约定一致；20 份配置的组件路径均存在，两张 runtime 表各覆盖 113 个 ID。没有生成来源/哈希清单。
- 未写/跑测试、导入/语法检查、dry-run、格式化、构建、安装、下载、容器、评测、远程或模型调用，没有 Git commit。运行兼容性保持未验证；Codex 构建、预备镜像、模型服务和 OpenCode 模型限制仍须实际准备，不能宣称保证可运行。

### 第 5B 批交付记录（历史）

用户以“ok继续”批准第 5A 批具体补丁后实施本批。原始 upstream 保持不变，独立 controlled/ 副本已应用补丁；构建未执行。

- 新增 Codex controlled_runner.py、summary.py，手写 adapter 接入能力声明/控制预检/原生最终摘要；修正 build.sh 在仓库子目录跳过补丁的问题，改用 patch -p1 实际应用六个文件。补丁内容未扩展，普通 caller 与路径不变。
- 已接入显式控制二进制、初始化代理门禁、独立原生 rollout、错误优先完成判断、同一产物补丁资格及最终摘要。缺失 usage 不当默认零，预算耗尽/失败/空补丁不提交；失败消费由 corrected 原记录重建覆盖。
- 仅文件操作与静态源码审阅；未写/跑测试、导入/语法检查、dry-run、格式化、构建、安装、下载、容器、远程、模型或 Git commit。运行兼容性未验证，尤其 Rust feature 构建、hook/门禁、原生事件顺序和进程清理。
- 本批结束停止；下一批待授权为第 6 批配置矩阵、Python 34 / 非 Python 79 同集合比较、双口径分析和文档收尾，不隐含构建/运行授权。

### 第 5A 批交付记录（历史）

用户以“继续”授权准备 Codex 的具体 hook 补丁及独立构建入口；按已约定的 review 边界，本批不应用补丁、不启用 adapter、不构建。

- 新增 `agents/codex/control-build/turn-control.patch`、`build.sh`、`README.md`，同步三份文档；原源码与运行入口未改动。补丁涉及 6 个上游路径，只有主采样边界和受非默认 feature 控制的小模块。
- 静态阅读确认 hook 位于主循环与内部重试循环之间，原生完成判断在下一轮前生效；独占控制日志阻止重启重置预算，最终预算错误沿原错误路径结束。上述为源码推导，运行未验证。
- 未应用补丁（包括 git apply --check）、未写/跑测试、语法检查、dry-run、构建、安装、下载、容器、远程操作、模型调用或 Git commit。
- 本批完成后停止。下一批需用户 review 本目录具体差异后授权第 5B 批：副本补丁应用及手写控制/摘要适配；构建和运行仍需另行明确授权，不因 patch review 自动获准。

### 第 4B 批交付记录（历史）

用户在 mini 第 4A 批交付后以“ok”授权 OpenCode 控制插件与原生摘要。本批完成后停止，未进入 Codex 源码补丁或配置矩阵。

- 新增 `agents/opencode/turn_control.mjs`、`summary.py`；修改 OpenCode 手写 adapter、公共控制运行时预检和 recorder 的可选插件就绪门禁。第三方源码、任务数据及评分器未修改。
- 插件在原 messages.transform hook 读取原生根会话及待采样 assistant ID，排除子会话和压缩转换、按 ID 去重，采样前提醒并同步阻止超预算请求；同会话只扩展一次，不依赖事件回调叫停，不以 HTTP 数计轮。
- 就绪门禁用于防止原插件加载失败后继续无控制请求；它不参与计轮或改写 usage。原生 CLI export 只在 agent 进程返回后保存最终会话，禁用导出进程插件；从原生 step-finish/工具/完成状态接入公共 FinalSummary，缺失记录保持未知。
- 完成判断与控制记录均通过且补丁非空才允许提交；未完成补丁仅作诊断。run_free 原 reader、corrected-v1、预算档位和恢复不重新生成规则保持不变。
- 仅静态阅读原 hook/加载器/CLI export 与改动文本；未运行测试、语法检查、dry-run、插件、导出命令、容器、网络服务或模型，未安装、下载、构建或 Git commit。运行兼容性仍未验证。
- 已同步三份文档。本批停止；下一批先展示 Codex 可选采样 hook 的具体补丁和独立构建入口，review 后才应用，不能直接改原副本或执行构建。

### 第 4A 批交付记录（历史）

用户在第 3 批交付后以“ok”授权先实施 mini 的 turn_control 与原生摘要；OpenCode 保留独立 review，本批未实施。

- 新增 mini `controlled_runner.py`、`summary.py`；修改 mini 手写 adapter、公共 OriginalCase/FinalSummary 与 reader 选择、turn_control 聚合及 Trae 原摘要读取接线。原 mini/Trae/方法来源源码、任务包和 Pier 不变；无 monkey patch。
- 原 CLI --agent-class 加载 InteractiveAgent 子类，query 前提醒并以原 n_calls 计轮；同会话一次 P50→P75 扩展，最终停止前不发额外请求。保留费用、原步数/时间限制和格式错误处理；原限制可能提前停止。只提交原生完成且公共捕获非空的补丁，其他补丁保留诊断，不自动提交或重新生成。
- 公共最终摘要接口替代方法对 trae.summary 的依赖；mini/Trae 共用纳入规则和总和/null 均值。mini 缺失 usage 保持未知，零次调用与缺失分开。run_free reader、corrected-v1 和从原记录重建规则不变。
- 静态审阅完成；未写/跑测试、语法检查、dry-run、模块导入、构建、容器、模型或远程操作。未安装依赖或 Git commit。运行兼容性、时间限制与进程清理未验证。
- 已同步架构、开发流程和使用文档。本批停止；下一批须授权 OpenCode 消息转换 hook 控制与摘要，不直接进入 Codex 补丁或配置矩阵。

### 第 3 批交付记录（历史）

用户在第 2 批交付后以“ok”授权第 3 批。本批只实施 run_free Git 调整、多语言方法、最终提示词绑定与隔离模型通道；完成后停止，未进入 mini/OpenCode/Codex 逐轮控制批次。

- 新增 `methods/run_free_multilingual/`、`src/prompts.py`、`src/model_channel.py`、`src/byte_relay.py`；调整 run_free 手写 adapter、BoundAgent、公共接口/预检/规划/调度、DeepSWE 手写 workspace/adapter 和四个 agent 的录制上下文。
- Python prompt 只移除原 Git 禁令；多语言版本禁止各语言执行/测试/构建/安装，允许文件和 Git。原 PromptBuilder、agent、任务数据和 Pier 源码不变。保存最终提示词与方法版本，恢复拒绝跨版本混跑；已有无版本运行可继续离线分析。
- 通道使用 agent 容器回环 TCP、只读挂载 Unix socket 和宿主已有 usage proxy，中继固定目标、只转发字节。verifier 无模型通道。预检取消上一批“通道未实现”的硬阻塞，改为要求显式隔离配置、支持能力、record_raw_usage 和 Linux；环境仍未检查。
- 静态审阅涵盖语言选择、最终提示词覆盖关系、四 agent 端点接线、固定目的地址、socket/中继生命周期和方法版本恢复边界。没有执行任何项目模块、测试、语法检查、dry-run、监听端口、容器、构建、下载、远程或模型调用；没有安装或 Git commit。
- 未改变 original/corrected 统计公式。通道运行、流式响应、并发/超时和清理均未验证，不能宣称兼容性已保证。仍待第 4 批逐 agent 控制/摘要、第 5 批 Codex hook 和第 6 批配置/统计收尾。

### 第 2 批交付记录（历史）

用户在第 1 批交付后以“ok”授权第 2 批。本批范围为数据映射/筛选、`/app` 工作区、公共补丁捕获及本地评测入口；交付后停止，不连续实施第 3 批。原样复制在第 1 批独立完成，原任务和 Pier 源码本批保持不变。

- 新增 `datasets/deepswe/{manifest.toml,tasks.py,adapter.py,workspace.py,evaluation.py,verifier_worker.py}` 和 `src/patches.py`。
- 调整公共 interfaces/workspaces/evaluation/CLI/planning、四个 agent 的手写 adapter，以及 Verified 评测计划的公共类型接线。未改变原方法/agent/评测器源码或 original/corrected 统计公式。
- 已接入排序后筛选截取、公开任务字段隔离、已提交补丁原文与诊断产物、捕获失败/空补丁区分、显式本地计划/执行、奖励异常映射、逐任务评测检查点及恢复。恢复校验已完成任务的原补丁，不重新生成；再次中断不丢弃后续已完成结果。
- 已同步架构和使用文档。仅静态阅读和文本差异审阅；未新增测试、运行测试、语法检查、导入、dry-run、构建、容器、远程或模型调用，没有安装依赖或提交 Git。
- DeepSWE 生成仍由预检明确阻止，等待第 3 批隔离模型通道。run_free Git 调整、多语言方法、其他 agent 的 turn_control、配置矩阵与统计收尾仍未实施；运行兼容性保持未验证。
- 第 1 批历史：复制 113 个任务及附带文件（1,134 个条目，含 1 个符号链接），Pier 246 个普通文件；未做来源核验、哈希或 provenance。原包 README 断链保留，语言分布的静态目录检查结果见架构文档。

### 已确认的后续范围

设计细节见 [architecture.md](architecture.md#deepswe-接入)，使用边界见 [usage.md](usage.md#deepswe-准备状态)。目标为 Python 34 条使用 run_free，非 Python 79 条使用新增多语言方法，turn_control 覆盖全部 113 条；三种方法目标均支持 Codex、mini、Trae、OpenCode，模型协议保持既有限制。

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 原样复制任务包、Pier 与文档同步 | 已交付，仅静态检查 |
| 2 | 数据映射/筛选、/app 工作区、公共补丁捕获、本地评测入口 | 已交付，仅静态检查 |
| 3 | 适配层取消 run_free Git 禁令、多语言方法、隔离模型通道 | 已交付，仅静态检查 |
| 4A | mini 逐轮控制、公共最终摘要及 Trae reader 迁移 | 已交付，仅静态检查 |
| 4B | OpenCode 逐轮控制插件及原生摘要 | 已交付，仅静态检查 |
| 5 | Codex 可选采样 hook 补丁及控制适配，单独展示、review 后应用 | 5A review、5B 副本应用及适配已交付；未构建/运行 |
| 6 | 配置矩阵、双口径分析接线、文档收尾 | 已交付，仅静态审查，运行未验证 |

全部批次继续“不写、不跑测试”；不执行 dry-run、语法检查、构建、容器、下载、远程操作或模型调用。后续静态审查应覆盖任务筛选与隐藏字段隔离、base_commit..HEAD 补丁、同会话预算边界、失败消费、未知 usage、恢复不重复计数和评测异常映射；静态检查不能记为运行验收通过。

## 最新交付：剩余补全合并批次（2026-09-19）

- 授权依据：用户“剩下的一次做完”，本批合并剩余实现；继续遵守“不写测试、不跑测试、节省 token”。后续历史批次的停止/待实现描述不代表当前状态。
- 已实现：turn_control 能力接口与同会话一次预算扩展、单次尝试恢复限制和诊断补丁隔离；Trae 多协议客户端适配；100 条子集和通用 JSON 仓库任务组件；远程评测命令执行/日志/显式报告关联；逐 case 解决状态及双分母汇总；usage 细分字段补全。入口与运行前提见 [usage.md](usage.md)。
- 用户最新统计决定：turn_control original 只保留总和，mean 固定为 `null`；没有按不同 case 数新增均值。原生 Trae 摘要仅为兼容口径，规则明确标识，不宣称复现历史实验。
- 文件范围：新增 `src/control.py`、`src/evaluation.py`、`methods/turn_control/`、`agents/trae/controlled_runner.py`、两个数据集适配目录、turn_control 示例和使用文档；修改公共调度/记录/预检/分析/usage 及 Trae 适配器。没有修改原方法、agent、数据集或评测器源码，没有提交 Git。
- 检查结果：仅在 tokenAna 环境中读取源码与修改文本；未执行新增模块、语法检查、测试、dry-run、真实 agent、代理、容器、模型或评测。新实现的运行正确性与服务兼容性尚未验证。
- 外部前提：用户预备 Linux 镜像与 CLI/worker Python、实际模型地址/密钥变量、OpenCode 模型上下限与只读配置；真实组件与远程评测需另行授权执行。本轮没有安装、下载、构建或远程操作，根目录来源副本仍保留。
- 本次已完成授权范围内的代码补全；下一阶段是用户安排运行环境及授权验收，不再把尚未执行的验证写成已通过。支持范围以 usage.md 矩阵为准，不承诺任意 agent×model 组合。

## 双口径补全：第 3 批交付（2026-09-19）

- 授权：用户“继续”执行当前协议/预检批次；继续遵守不写、不跑测试、精简实现的要求。
- 新增 `src/usage_protocols.py` 和 `src/capabilities.py`；修改公共 proxy/raw_usage、planning/execution/run_accounting 和 Codex 适配器。三种协议按 metadata 显式解析，Responses compact 纳入采集；Anthropic 累计 usage 合并、cache 输入及 TTL 规范化；拒绝请求留下覆盖缺口。未修改原组件源码、run_free 或统一 corrected 公式。
- dry-run 与 run/resume 共用统计组合预检，区分配置兼容、采集关闭和阻塞；展示 API 路径、cache 策略及服务未验证状态。新运行保存 preflight，原始记录保留 cache 标记证据。
- 检查方式：tokenAna 环境中文本读取与本批手写文件差异审阅；未导入/执行新增模块，未运行语法检查、测试、dry-run、真实 agent、监听端口、容器或模型。没有安装、下载、构建或提交 Git。
- 待完成：mini 的 reader/多协议接线、显式缓存配置与 original 兼容统计；后续按 Trae、OpenCode、turn_control 分批。公共协议实现不代表这些 agent 已接通。DeepSeek 等厂商扩展字段暂保留原文，未确认语义的字段仍未知；真实传输和服务兼容均未验收。
- 本批结束停止等待下一批授权；不得因总体计划已获认可而继续执行其他 agent 接入。

## 当前进度

- 最新批次：剩余补全合并批次已交付，详情见文首；第 6 批及以下为历史进度。尚未执行新代码的运行验收。

- 已完成：设计文档、用户 review 反馈同步、run_free 原文件复制，以及 run_free 的串行执行、原预测准备和任务边界恢复链路。
- Codex 复制已完成：8,282 个源码条目放入 `agents/codex/upstream/`；原 `agent_caller.py` 和 `codex_trace.json` 放入 `agents/codex/compatibility/` 及其 `fixtures/`。保留原目录结构与符号链接，未复制 Git 仓库元数据或本地缓存。
- Dataset 原文件准备已完成：本地 Verified JSON 放入 `datasets/swe_bench_verified/data/`；官方 SWE-bench v4.1.0 源码包下载解压到 `datasets/swe_bench_verified/upstream/swebench/`。未裁剪数据、修改源码或创建来源记录。
- 当前约定：批次以便于 review 为准，无 150 行硬限制；不再要求保留根目录来源副本；首版 dry-run 验收采用小规模 10 条数据。
- 来源已由用户人工检查；取消来源核验与记录要求，不做逐文件哈希比对，不生成来源清单或 provenance 文件，已有相关辅助文件已移除。
- 配置批次已完成：三个组件的最小 manifest、TOML 配置读取、显式目录选择、只读 `config` CLI 及配置样例。当前使用相对于配置文件的目录路径，组件 options 原样传递。
- 离线验证：Python 3.13.11 下 7 项 unittest 通过，涵盖样例配置、选项传递、错误提示、跨工作目录 CLI 和未选中坏组件隔离；未测试依赖安装隔离或断点恢复。
- 目录调整已完成：核心代码由 `src/tokenana/` 移至 `src/`，入口改为 `python -m tokenAna`；导入路径、跨目录 CLI 测试和文档同步更新。激活 `tokenAna` 后使用 Python 3.12.14 再次运行 7 项测试，全部通过；未安装任何依赖。
- 命令入口已整理：根目录 `tokenAna.py` 调用 `src/cli.py`，支持 `python -m tokenAna`。已审查核心防御性代码，保留 TOML 输入边界检查和明确的配置错误提示，未发现需要删除的重复校验或重试层；原组件源码未改动。
- 公共接口批次已完成：`src/interfaces.py` 定义 Task、AgentResult、MethodResult、Workspace、Agent、Method；`tests/test_interfaces.py` 使用内存模拟组件验证多次调用和异常传播。tokenAna 环境 Python 3.12.14 下共 9 项离线测试通过；无安装、真实命令执行或原源码改动。
- run_free 适配批次已完成：新增 `methods/run_free/adapter.py`，直接调用原 prompt_builder，再调用一次公共 agent 接口，原样保留结果对象。新增 `tests/test_run_free.py`，共 11 项离线测试在 tokenAna Python 3.12.14 环境通过；未改原源码、安装依赖或启动真实 agent。
- Verified 枚举批次已完成：新增数据集 adapter.py 和 tests/test_verified.py，按原顺序映射四个公共字段，支持 limit。验证全量 500 条、前 10 条选择、边界值和评测字段排除；tokenAna Python 3.12.14 下共 14 项离线测试通过。未修改原数据或原组件源码。
- 动态加载批次已完成：src/loading.py 按选中目录加载 adapter.py，manifest.entrypoint 指定无参类/工厂。当时 run_free、Verified 已声明入口；Codex 入口在后续适配批次补齐。16 项离线测试通过，覆盖配置连接前 10 条任务与模拟 agent、相对导入、同名目录隔离、未选中坏组件和失败清理。未实现依赖安装隔离或恢复验收。
- Codex 适配批次已完成：用户同意原调用器执行 Workspace 生成的启动命令。新增 agents/codex/adapter.py、src/agents.py 和 tests/test_codex.py；Workspace 增加独立共享产物目录与 launch_command。只覆盖原命令构建，复用原调用、trace、统计和超时逻辑；Codex manifest 入口已补齐。tokenAna Python 3.12.14 下 19 项测试通过，进程全部模拟，无原源码修改或安装。
- 容器 Workspace 批次已完成：新增 src/workspaces.py 和 tests/test_workspaces.py，为已准备容器提供 Docker exec 命令、可配置环境前缀、文本读写及独立产物目录映射；全部 Docker 进程均模拟，tokenAna Python 3.12.14 下 24 项测试通过。未创建或启动容器；真实挂载和超时后清理尚未验证。
- 补丁收集批次已完成：Verified collect 通过 Workspace 获取 git diff，保留原文本回退与 success/error 口径；新增公共 PatchResult 和 tests/test_collection.py。命令失败和超时直接抛出。tokenAna Python 3.12.14 下 28 项测试通过，全部工作区调用模拟；未改原 runner 或运行容器/评测。
- 未执行：真实 Linux Workspace/容器验证、依赖安装、Codex 构建、镜像准备、假模型服务实验或真实组件评测。
- 评测方向已更正：用户要求全部以原源码为准，采用 submit_to_swebench.sh → generate_predictions.py → sb-cli 的实际提交链；不采用另一脚本的跨方法样本交集，不以本地 harness 替代原提交过程。两个原脚本已原样复制到 datasets/swe_bench_verified/compatibility/execution_control/scripts/，本批无手写业务代码、来源核验、安装或评测执行。
- 预测准备批次已完成：AgentResult 增加可选 artifacts 关联，Codex 保留当前调用产物；Verified prepare_submission 在新目录复制原始 patch.diff，调用未修改的 generate_predictions 并返回 sb-cli 命令。验证排序、原文、空/缺失文件、agent 错误不筛除、跨尝试隔离及原默认/指定 run_id。tokenAna Python 3.12.14 下 30 项测试通过；没有提交或安装。
- 报告读取批次已完成：SubmissionPlan 增加与原脚本一致的 get-report 命令，Verified.read_report 读取明确指定的本地 JSON，保留计数、ID 列表及未知字段；不推断远端状态或填补未评测结果。tokenAna Python 3.12.14 下 32 项测试通过，新增报告均为合成夹具，未访问远程服务。
- 待确认：原图表脚本通过率为 resolved/completed，公共分析脚本则固定 resolved/100；尚未选定汇总指标。实际报告保存路径、提交关联和远端状态管理仍未验证。
- CLI dry-run 已完成：python -m tokenAna run experiments/run_free_codex_verified.toml --dry-run 实际列出原顺序 10 条任务、准备事项及启动/提交命令模板，明确 plan_only 与环境未检查，不运行任何组件。真实 run 缺少 runtime/output 时会拒绝启动。
- 最小运行与恢复链路已完成：`src/execution.py`、Verified `workspace.py` 和 CLI 串联容器 create/start/rm、提交与干净状态检查、run_free、原 Codex caller、原始产物、原预测生成及提交命令。每个任务和提交准备使用递增尝试目录；恢复校验配置/运行时快照，跳过已收集任务，拒绝损坏产物。模拟测试在第二个任务中断后恢复，只重新调用第二个任务；中途修改未选中的坏方法不影响恢复。未执行任何真实子进程。
- 早期最小运行链路已完成模拟接线；此后新增的统计与多组件工作以下方最新记录为准。真实验收仍需用户提供 Linux x86_64、预构建且包含 `agents/codex/upstream/` 构建产物的任务镜像与假模型服务；远程 `sb-cli submit` 仍需单独明确执行。禁止默认在本机安装或构建。
- 已补齐直接可用的 Linux 运行材料：`agents/codex/Dockerfile.source-agent` 从本地 `codex-rs` 源码构建 `/opt/tokenana/codex`；`scripts/build_tokenana_codex_images.sh` 构建 live 配置的 10 个 `-agent` 镜像；`experiments/run_free_codex_verified_live.toml` 已填写源码版 Codex 和假 Responses 服务配置；`experiments/runtime_verified_10.toml` 已填写 10 个 Verified 镜像名；`scripts/check_tokenana_live_runtime.sh` 只读检查 Docker、镜像和运行时文件。
- 当前没有授权调用真实模型 API；框架也不会在 run/resume 中自动执行远程提交。
- Corrected token 文档批次已完成：用户要求将统一规则写入框架文档，供每个后续方法强制遵循。规则正文见 [Corrected token accounting](architecture.md#corrected-token-accounting)，包含 cache 与厂商语义、全部 case/尝试消耗、均值分母、trace 保留与去重、缺失 usage 标记和双口径结果要求。
- 前述文档批次仅更新两份文档并检查差异、链接和口径一致性；当时未实现统计代码。当前实现进展以下方第一批记录为准。
- Corrected 指标汇报要求已补充：不能只给 `input + output` 总量；终端和结果文件必须分别汇报 total、input、output、cache read、cache write、reasoning 及其他可得 token 细项，每项列出和、按统一 case 分母计算的均值及完整性。已同步方法验收要求；本次仍只改文档，统计实现状态不变。
- Corrected 第一批实现已完成：新增 `src/accounting.py` 的公共类型、OpenAI Responses usage 规范化、逐项全量汇总及双列报告函数；新增 run_free 原 Codex 分析统计模块，并通过 adapter.original_accounting 暴露。保持原执行逻辑、原 caller、第三方源码和现有运行链路不变。
- 第一批验证：新增 `tests/test_accounting.py` 的 9 项离线测试，tokenAna Python 3.12.14 下执行 `python -B -m unittest discover -s tests -p 'test_*.py' -v`，共 48 项通过。验证各指标和/均值/完整性、失败 case 与多尝试、重复与冲突记录、额外指标、原 patch 筛选及双列文本/JSON 结果。未安装依赖、构建、启动真实 Codex/容器或调用模型。
- Corrected 数据源变更：用户已授权新增 API 记录代理，original 仍按原方法读取/统计，corrected 改为读取 API 原始 usage，不再依赖 agent trace 的统计字段。该授权替代早先仅用 trace 的约定。
- 代理批次已实现：新增 usage_proxy/raw_usage 模块，Codex 通过 record_raw_usage 可选接线；记录请求/响应正文与脱敏元数据，按 HTTP 请求区分重试并解析 JSON/SSE usage，转换为公共 CaseUsage。运行入口要求 Linux host 网络；未改第三方源码或原 caller 统计。
- 本批验证：初次本地端口测试因沙箱限制失败，提权请求被取消；用户随后要求不要再跑此类测试。已将端口测试设为默认跳过，未再启动服务。3 项文件夹具测试、5 项模拟 Codex 测试及静态语法检查通过；未验证真实 HTTP 转发、Linux 网络或真实 Codex，未安装依赖或调用模型。
- 统计接线的最新进展见下方第 1 批；额外协议/路径和 cache 策略仍待后续批次，不以本批代码交付代替真实组件验收。

## 一键生成与双口径分析：第 1 批（2026-09-19）

- 用户已选择：TOML 指定 dataset/model/agent/method，单条 run 完成生成和双口径分析；官方评测独立执行。后续提供单次与跨实验 JSON/CSV/Markdown 表格。新增 agent 的 original 使用方法筛选/聚合规则和 agent 原生日志，标明兼容口径。
- 本批新增 `src/records.py`、`src/run_accounting.py` 和启用假 Responses 服务采集的实验样例；接入 execution/CLI，增加 Codex 原始统计读取接口及 run_free 支持格式声明，保留原源码和原统计公式。
- 调用开始及产物分配时持久化关联；run/resume 在正常、失败和可处理的中断后重新读取全部尝试，写出 `accounting.json`、`accounting.txt`。旧配置无原始 usage 时标为不完整，不使用 agent token 估算补齐。统计异常写入状态，已有生成异常继续传播。
- 本批仅覆盖 run_free/Codex/Responses；显式采集模式在启动前拒绝不具备双口径能力的组合。其他 agent 原有未开启采集的路径保留，original 显示不可用。
- 用户中途明确要求“不用写测试，也不用跑测试了”；因此没有新增或执行测试、dry-run、网络监听、容器、构建或模型调用。仅静态阅读代码并检查文件差异，不能记为通过测试或真实运行验收。
- 本批交付后停止。下一候选批次：统一分析产物及 `analyze`/`compare`，随后协议与能力检查、mini 完整统计、Trae/OpenCode 分批接入、turn_control 控制接口与方法，最后按授权进行 Linux 真实组件验收。每批仍需独立授权；恢复测试须另行确认。

## 一键生成与双口径分析：第 2 批（2026-09-19）

- 用户“ok继续”授权本批；沿用不写、不跑测试及精简实现要求。新增 `src/analysis.py`，接入 CLI、公共汇总及公共任务快照，未改第三方源码或原统计公式。
- `analyze <run> [--output <new-directory>]` 根据保存的配置、任务和原始记录重算，创建独立版本，不修改原报告/state；使用当前所选适配器并记录这一限制。旧运行有 accounting.json 时可从中恢复任务身份，否则明确缺少任务快照。
- `compare <baseline> <candidate> [...] --output <new-directory>` 仅读取保存报告，输出全量汇总及逐 case 外连接表；同口径、同选定/纳入集合、生成完成且统计完整才计算变化率，缺失/不完整/零基线说明原因。
- run/resume 与 analyze 统一导出 JSON、TXT、CSV、Markdown；增加逐 case 的 original/corrected 指标。新 run 保存 tasks.json，resume 拒绝任务快照变化。运行状态为 running 的独立分析统一标记不完整；compare 拒绝仍在运行或状态已变化的顶层旧报告。
- 本批仅静态阅读代码和检查差异，没有新增/运行测试或执行 analyze、compare、dry-run、agent、容器、网络服务；运行正确性尚未验证。具体产物与边界见 architecture.md 的“离线分析与跨实验比较”。
- 本批交付后停止。下一候选批次为协议与能力检查，须单独授权；不自动恢复测试或真实组件验证。

## 公共模型配置批次（2026-09-19）

- 用户确认五个模型全部保留、分别连接各厂商，并授权实施公共配置与展示批次。
- 已新增 `src/models.py`、`models/` 下五个模板、`experiments/run_free_codex_model.toml` 和 `tests/test_models.py`；更新配置读取、CLI、BoundAgent、计划、运行快照以及 Codex 新增适配层。原源码、旧实验文件、原统计口径不变；保留工作区并行出现的 usage 记录改动。
- 公共 `[model]` 可供新增方法和 agent 复用。新增 agent 必须声明 `model_protocols` 并实现无副作用的 `configure_model(model, options)`，逐一验收这五个模型，不能以模板存在代替实际支持。详见 [公共模型配置](architecture.md#公共模型配置2026-09-19)。
- 当前 Codex 只支持 Responses：GPT 配置已接通；Qwen3.8-Max 需补填厂商地域/Workspace 地址；Claude、DeepSeek 和 Qwen3-Coder-Next 模板所选协议不兼容，dry-run 显示原因，运行前报错。真实模型服务、工具调用、缓存及 usage 完整性均未验收。
- 已执行 tokenAna 环境中的 `python -B -m unittest discover -s tests -p 'test_*.py' -v`：63 项中 58 项通过（本批新增 9 项全部通过），5 项 usage proxy 测试因沙箱禁止监听本地端口失败。随后用户明确要求“不用测试了”，已停止测试和 dry-run 执行，仅做静态检查与文档收尾；没有将全量测试记为通过。
- 下一步需单独确认不兼容 agent 的直连适配方案及 Qwen 实际服务地址；当前不承诺所有 agent 已能运行全部五个模型。未调用真实 API、安装依赖、下载源码或镜像、构建、启动容器或提交 Git commit。

## 新会话开始

1. 阅读根目录 `AGENTS.md`、`docs/architecture.md` 和本文，检查 Git 状态及用户已有改动。
2. 先确认当前任务与授权批次；历史计划只说明目标，不构成执行授权。
3. 只读检查相关接口、选中组件、已有测试和版本记录；不要自动安装、构建或试跑。
4. 给出本批目标、文件清单、预计行数、验证命令和验收点，等待用户同意。

任何任务运行前必须先激活 `tokenAna` conda 环境（Python 3.12.14，已获用户确认）；禁止在本机安装任何依赖或软件，包括该环境。非交互 shell 必要时先执行 `source /Users/manyi/miniconda3/etc/profile.d/conda.sh`。
核心代码直接放在 `src/`，没有额外的 `tokenana/` 包目录。当前已有检查命令，在项目根目录运行：

```bash
conda activate tokenAna
python -B -m tokenAna config experiments/run_free_codex_verified.toml
python -B -m tokenAna run experiments/run_free_codex_verified.toml --dry-run
python -B -m tokenAna run <execution-config.toml> --runtime <runtime.toml> --output runs/<name>
python -B -m tokenAna resume <execution-config.toml> --runtime <runtime.toml> --output runs/<name>
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

Linux x86_64 上的直接使用顺序如下。以下命令会在镜像构建阶段下载 Rust 工具链和编译源码，因此只应在用户明确准备好的 Linux 构建机执行；本机 macOS 不执行这些命令：

```bash
conda activate tokenAna
bash scripts/pull_tokenana_base_images.sh
bash scripts/build_tokenana_codex_images.sh
bash scripts/check_tokenana_live_runtime.sh
# 另起服务监听宿主机 0.0.0.0:8000，提供 OpenAI Responses API 的假模型响应
python -B -m tokenAna run experiments/run_free_codex_verified_live.toml \
  --runtime experiments/runtime_verified_10.toml --output runs/run-free-live-001
```

`build_tokenana_codex_images.sh` 不自动 pull 基础镜像；缺失时会打印准确的 `docker pull` 命令并退出。镜像中的 `/opt/tokenana/codex` 来自当前仓库副本，适配层在每次调用中通过 `-c model_providers...`、`model_provider` 和 `model` 将假服务地址传给原 Codex CLI。假服务和模型响应格式属于真实 Linux 验收条件，框架不会伪造或替换它们。

测试发现限定在 TokenAna 自己的 `tests/`，不要从项目根目录递归收集第三方测试。配置命令不代表 dry-run 或真实执行验收。

## 每批交付规则

- 按职责分批，大小以便于 review 为准；完整实现需要时可以超过 150 行，较大的批次先说明范围，复杂步骤仍拆分并逐批审批。
- 使用用户已确认的原文件，原样复制单独成批，不附加来源检查或记录任务；不得借复制修改原源码。
- 新逻辑写在适配层；需修改任何原源码时先停止，说明必要性与拟议差异，等待明确批准。
- 仅运行本批已授权的检查；不顺带启动原仓库中可能调用真实 API 的 integration tests。
- 交付文件链接、差异概述、实际验证结果和未验证项，更新本文进度后停止，不自动继续或 commit。

## 新增方法

1. 使用用户指定的原实现，明确支持的任务与 agent 能力、预期输入输出、原 token 计算及样本筛选/均值规则和忠实性标准；不确定项问用户。
2. 经审批将原文件原样复制到 `methods/<name>/` 的源码目录。
3. 在该目录新增 adapter.py，通过公共 task/agent/workspace 接口连接原方法；manifest 的 entrypoint 填写适配器类或工厂名称，入口须能无参创建对象且无执行副作用。组件内代码使用相对导入，无需修改核心名单。
4. 在实验配置中选择新方法；方法依赖留在自己的声明中，不加入全局强制依赖。
5. 实现该方法的 original token accounting，并将全部执行记录接入框架公共统计层，按 [Corrected token accounting](architecture.md#corrected-token-accounting) 重新计算 corrected；这是每个方法的必需能力，不能仅沿用原方法统计或为 corrected 另设筛选条件。
6. 验证与原实现的输入、方法行为及输出一致，完成下文双口径统计验收，再验证它未被选中时不会影响已有实验。只接通方法执行而未接通 corrected 统计，不算完成方法接入。
7. 若现有公共接口不足，先提出独立设计批次，不把临时方法分支塞进核心或其他组件。

### 每个方法必需的 token 统计验收

统计规则以架构文档的 `corrected-v1` 为准；方法负责 original 兼容，agent 负责 trace 与协议语义，框架统一重算 corrected，不在各方法中复制或修改框架公式。既有 run_free 和在接入中的 turn_control 同样适用；保留原统计字段不构成免除 corrected 的理由。

- 确认所用接口的 cache 启用策略、usage 字段包含关系及 trace 能力限制，细项越完整越好；不支持或未知的字段与零区分。
- 用离线夹具覆盖成功、失败、超时、空/缺失 patch、多次调用和多次尝试，确认 corrected 不因结果状态筛除已知消耗，均值按实际发生 LLM 调用的不同 case 计算。
- 验证 cache read/write、reasoning 等子项不重复相加，逐 response、累计快照和最终汇总不重复计数；原始 usage、未知字段及损坏尾行仍可追溯。
- 覆盖 usage 缺失、调用情况未知、零 token、零分母和部分明细，确认输出已知小计及完整性说明，不混入原 caller 的估算值。
- 验证终端和结果文件并排展示 original/corrected；corrected 必须分别列出 total、input、output、cache read、cache write、reasoning 及其他可得细项，各项均有和、按统一 case 分母计算的均值及完整性，不能仅保留在 trace 中。未知项显示 `null` 和原因，部分已知项标记不完整；所有指标均纳入失败 case，不能只修正 total。
- 验证恢复后各项指标均保留旧尝试消耗，仅恢复评测或重复汇总不增加任何 token 指标。
- 实际不支持重试等能力的方法用框架统计夹具覆盖相应场景，不为统计测试改变原方法行为。真实 agent trace 的验证另按授权批次连接假模型服务，离线夹具通过不能代替真实组件验收。

## 新增数据集

1. 使用用户指定的数据集，明确任务类型、环境准备及官方评测方式；先判断已有方法是否兼容。
2. 在 `datasets/<name>/` 原样保存数据和所需运行/评测框架。
3. 在 adapter.py 中提供无参入口，并在 manifest.entrypoint 声明其名称；由 dataset 适配层完成任务映射、运行时校验、工作区准备、补丁收集和提交/评测准备，方法不增加数据集名称分支。核心按这些接口调用，不增加数据集名称分支。
4. 将 gold patch、隐藏测试及评测专用字段与 agent 可见任务信息分开，禁止把完整数据目录挂入任务容器。
5. 仅新增该目录与实验配置，验证已有方法可运行；不兼容组合明确报错，不默改原提示词。
6. 使用固定补丁验证评测接入，检查报告、错误分类、产物完整性与恢复行为。

## 新增 agent

1. 使用用户指定的源码，了解构建平台、调用协议与输出格式，原样复制到 `agents/<name>/`。
2. 在 agent 目录维护构建与启动适配、组件依赖和 trace 处理，实现无执行副作用的 plan(options) 供 dry-run 使用；不从其他方法目录隐式借用可变环境。
3. 通过 workspace 接口运行，不绑定具体数据集名称；可调用 execute，也可为保留原调用链使用 launch_command 生成命令交给原调用器执行。共享产物使用 new_artifacts 分配。agent 实验选项用 BoundAgent 绑定，方法无需知道 agent 配置；保持原统计字段及其语义。
4. 提供 [corrected 统一规则](architecture.md#corrected-token-accounting) 所需的原生 trace、完整可用 usage、协议/版本语义、逐次产物关联和 cache 策略；保留失败及部分记录，明确不能观察到的消耗。不得只返回一个原 token 总值作为 corrected 的输入。
5. 先模拟命令与 trace，再经审批构建真实组件并接入假模型服务；真实 API 另需明确授权。

## 验证顺序与验收证据

1. 配置检查、纯离线单元测试；只导入选中组件。
2. dry-run：首版用小规模 10 条数据验收，列出任务、组件、准备事项、启动及评测命令，不把环境缺失报告为运行成功。
3. 模拟流程：已覆盖空补丁、异常退出、中断、损坏产物、仅恢复提交准备和未选中组件隔离。
4. Linux 真实组件：假模型服务驱动真实 Codex 编辑，再用固定补丁检查真实 SWE-bench 评测器。
5. 无真实 API 的端到端验证：保留配置、源码/数据/环境标识、原始 trace、补丁、报告和恢复记录。
6. 测试夹具的成功不是模型解题能力；报告分别说明已验证的集成能力与仍未验证的真实模型行为。

## 后续实施顺序

分组件复制（已完成） → 配置读取与显式选择（已完成） → 最小公共接口（已完成） → run_free 适配（已完成） → 数据映射（已完成） → 动态适配器加载（已完成） → Codex 启动与统计（已完成模拟验证） → 容器 Workspace（已完成模拟验证） → 补丁收集与原预测准备（已完成） → 调度、恢复与扩展隔离（已完成模拟验证） → Linux 环境 → 真实组件 → 端到端验收。
每个箭头仅表示依赖顺序，不授予执行权限；每项按 review 需要拆成单独审批的批次，不以固定行数机械切分。

## turn_control 独立工作线

- 用户已认可整体计划：新增平行方法 turn_control 及 Trae 接入，必要时扩展公共接口，保留 run_free 的源码、适配器、配置和行为。公共组件继续开发时先重读最新实现，复用已完成能力，不覆盖其他工作。
- 已确认范围：只实现 P50→P75（Claude 52→64、Gemini 29→45、GPT 50→67），使用原 100 条子集；首个 dry-run 取原顺序前 10 条，真实 Trae 加假模型服务先验收 OpenAI。
- 已确认单次尝试：会话内最多扩展一次；最终未完成只保留诊断 diff 和统计记录，不提交评测、不自动重试。生成中断也不自动重跑；该约定仅适用于 turn_control。
- 第 1 批授权与交付：仅原样复制根目录 Trae 到 `agents/trae/upstream/`，并在两份文档追加本工作线约定和进度；未混入手写适配代码。
- 复制完成：108 个文件，5,191,138 字节；包含原有两个二进制工具。未复制 `.git/`、未跟踪文件或缓存，未修改根目录 Trae 或 turn_control 内的原文件。
- 本批检查：在 tokenAna 环境中使用 `git ls-files -z` 确定复制条目，以 `shutil.copy2(..., follow_symlinks=False)` 复制；目标条目集合、文件大小、权限及链接目标检查通过，未生成来源记录或做内容哈希比对。
- 文档验证：检查本批新增段落的格式、目录引用及与已确认计划的一致性；本批不运行单元测试、第三方测试、agent 或二进制工具。
- 第 2 批已完成：将 `turn_control/swebench_verified_subset.json` 原样复制到 `datasets/swe_bench_verified/data/swebench_verified_subset.json`，共 1,392,291 字节；没有重新序列化 JSON、裁剪字段或修改默认 500 条数据及现有配置。
- 第 2 批检查：在 tokenAna 环境下确认 JSON 为 100 条记录、100 个唯一且非空的 instance ID，实例顺序与原文件一致，文件大小及权限保留；文档新增/更新段落格式和目录引用检查通过。未导入适配器、运行实验或生成来源/哈希记录。
- 未执行：公共控制接口、方法与 Trae 适配、专属配置、任务 dry-run、依赖安装、构建、容器、评测及真实模型调用；尚无 turn_control 实验结果。
- 下一候选批次：公共逐轮控制接口及模拟测试；先核对最新公共类型和 BoundAgent，展示具体接口、兼容性及文件范围后单独获批，不顺带实现方法或 Trae 执行代码。

后续顺序：Trae 原样复制（已完成） → 子集原样准备（已完成） → 公共逐轮控制接口及模拟测试 → 方法控制逻辑 → Trae 适配 → 配置/调度/产物及 10 条 dry-run → Linux 上 OpenAI 假服务验收 → 官方评测闭环。
每批开始前提供具体文件范围、预计规模、验证命令和验收点；获批后执行，完成后报告差异与验证结果并停止。整体计划认可不授予后续批次连续执行权限。

## run_free 多 agent 接入工作线

- 用户要求让现有 run_free 方法可分别选择 mini-SWE-agent、Trae 和 OpenCode，并在全部集成确认后删除根目录中的三个来源仓库。run_free 方法、原 agent 源码和现有 Codex 路径保持不变。
- 源码准备批次已完成：Trae 继续使用已有 `agents/trae/upstream/`；新增 `agents/mini_swe_agent/upstream/`（221 个 Git 跟踪文件）和 `agents/opencode/upstream/`（6,631 个 Git 跟踪文件）。复制不包含 `.git/`、未跟踪文件或缓存，未做来源核验、哈希比对或 provenance 记录。
- 本批只复制第三方源码并同步文档，没有新增手写适配代码、manifest、配置或测试；没有安装依赖、执行第三方 CLI、构建、启动容器或调用模型。三个根目录来源仓库仍保留。
- 后续按 mini-SWE-agent → Trae → OpenCode 分批实现最小适配层。每个 agent 复用公共模型配置、Workspace 产物目录和现有 run_free 调用，保留上游 trace/usage 语义，并完成离线命令/解析模拟后再申请下一批。
- 根目录来源仓库的删除安排在三个 agent 均完成适配和经授权验证之后；删除前不以源码已复制代替可运行验收。
- mini-SWE-agent 启动适配批次已交付：新增 adapter/manifest、10 条实验配置和 `tests/test_mini_swe_agent.py`；公共 planning 提示从 Codex 改为所选 agent。5 项定向离线测试通过，CLI 10 条 dry-run 为 plan_only，明确缺少 executable。测试模拟所有进程及代理，不运行网络监听、上游程序、容器或模型。
- 当前 mini 支持 CLI 配置、密钥变量映射、轨迹/补丁/错误产物和 Responses 原始 usage 代理接线；其他协议代理、run_free mini original 分析及 mini 的双口径汇总仍未完成。公共自动汇总已接入 Codex；当前 run/resume 会在启动前拒绝 mini 的显式双口径采集模式，不宣称 mini 完整集成验收。上游 mini.yaml 的提示词与 3 美元成本限制保留；超时只保存诊断轨迹与空 patch，真实进程清理待 Linux 验收。详细边界见 architecture.md 的 mini-SWE-agent 启动适配批次。
- 本批结束停止；下一批可 review Trae 启动适配范围。三个根目录来源继续保留，不能以本批模拟测试替代真实组件验收。

## mini 双口径补全（2026-09-19，第 4 批）

- mini 适配器接通公共 Responses、Chat Completions、Anthropic Messages 代理和 raw usage reader；启用采集时预检协议、地址和 model_class，original 不再因未接入而阻塞。协议支持仍受现有 provider 映射限制，Qwen Responses 等未支持组合继续拒绝。
- original 从原生 trajectory 的 messages[].usage 或 extra.response.usage 提取 input/output（兼容 prompt/completion 字段），投影为现有方法统计输入。保留 run_free 的非空原 patch、trace 存在筛选、单次尝试及向下取整均值；不读取 API 记录，不改变 AgentResult.tokens_used。缺失 usage 沿用该兼容规则忽略，非法数值/损坏轨迹标记不完整。
- 公共接入新增 original_compatible_trace_formats 和 original_accounting_variant；报告规则带 `+mini-trajectory-compatible-v1`，明确是新 agent 兼容口径，并非作者历史结果。原方法文件及公式未修改；跨规则比较不会输出节省比例。
- Anthropic 配置启用上游已有 `set_cache_control="default_end"`，由原生逻辑添加显式缓存标记；preflight 区分已配置和已命中。保留上游提示词、工具、成本限制与重试行为。实际 LiteLLM 转发和缓存标记待真实组件验收，corrected 仍只读取原始 HTTP usage。
- 新增 `experiments/run_free_mini_swe_agent_accounting.toml`：10 条任务、开启采集、假服务地址及预期镜像内 executable；路径须匹配用户准备的 Linux 镜像。此文件不是镜像准备或执行授权。
- 按要求未写/跑测试、dry-run 或真实组件；仅阅读源码与改动文本，未安装、构建、启动容器或调用模型。下一批候选为 Trae 适配，需单独授权。

## Trae 启动与双口径接入（2026-09-19，第 5 批）

- 新增 `agents/trae/adapter.py`、manifest 和 `experiments/run_free_trae_accounting.toml`。调用任务镜像中原版 `trae-cli run --file ... --config-file ...`，只读导入不加载上游依赖。原方法、原 Trae 源码与公共统计公式均未修改。
- 本批限定 OpenAI provider + Responses；其他模型/provider/协议在配置阶段明确拒绝，不能据此宣称五模型已兼容。Anthropic 原客户端尚无本接入所需显式缓存配置，Chat provider 映射也待后续。未翻译协议或静默切换模型。
- 保留原系统提示、四个默认工具、默认 200 步（可显式配置 max_steps）、示例中的输出上限/采样参数/重试设置和 Lakeview。主任务与 Lakeview 使用用户选定的同一模型及代理端点，但分别实例化模型配置，避免 Lakeview 修改温度影响主任务；两者共享 native provider 配置以解析同一密钥变量。不配置可启动外部服务的可选 MCP，不使用 Trae 内层 Docker。
- 每次调用保存 prompt、无密钥 config.yaml、trajectory、stdout/stderr、原生 native.patch.diff 以及供既有收集链使用的 git diff。超时保存部分日志与空正式 patch，容器内子进程清理仍待真实运行验收。非零退出、无结束时间或 success 非 true 都产生 AgentResult.error；run_free 的现有结果筛选不变。
- original 从原生 llm_interactions 的 response.usage 读取 input/output，不重复读取 agent_steps 中的同一 usage；投影后复用原方法的非空 patch 筛选与整除均值。规则后缀 `+trae-trajectory-compatible-v1` 标记兼容口径。AgentResult.tokens_used 为原生日志 input+output 已知和，exec_count 为已记录 tool_results 数量；两者都不冒充 corrected。
- corrected 从代理原始 HTTP usage 汇总，包括经过同一代理的主任务、Lakeview 和实际重试；original 的原生交互日志不包含 Lakeview 独立消费。未观察到的路径/会话仍按公共覆盖边界说明，不能宣称所有消耗均已验收。
- 示例固定前 10 条、假服务地址、开启采集和预期 `/opt/tokenana/trae-cli`；须由用户准备对应 Linux 镜像。未写/跑测试或 dry-run，未运行真实 CLI、代理、容器、模型或评测；只做原接口及新增文本审阅，未安装、下载、构建或提交 Git。
- 本批不实现 turn_control；下一候选批次为 OpenCode 启动/统计适配。待用户授权后继续，根目录来源副本继续保留。

## OpenCode 启动与双口径接入（2026-09-19，第 6 批）

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
