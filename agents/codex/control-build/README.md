# Codex 控制版补丁与适配（第 5B 批）

## 通用会话与原生 Chat Completions 补丁（2026-09-20）

新增 `session-chat.patch`，在 turn-control.patch 之后应用，独立 controlled 副本已包含改动，upstream 不变。涉及 11 个路径；具体 diff 在同目录 patch 中。新增 tokenana-session 非默认 feature、共享文件回调、原生历史回写、wire_api=chat_completions、直接 Chat HTTP/SSE 客户端及相关本地 schema。处理普通/custom/namespace 工具、流式与非流式响应、usage 和错误；不支持的服务端工具 schema 明确拒绝。Responses 路径保留。

build.sh 现在顺序应用两个补丁，使用 codex-core/tokenana-turn-control,codex-core/tokenana-session features。运行会话构建还需显式 session_executable、session_version=codex-session-compatible-v1 和 record_raw_usage=true。完整交付见 [方法接入文档](../../../docs/method-integration-delivery.md)。

补丁适用性仅用 patch --dry-run 检查；本轮未执行 Rust 格式化、编译、构建或 Rust 单测。Chat 客户端新增请求/工具/usage/失败单测源码，尚未运行。Python 消息映射、文件回调、原生摘要夹具及报告已离线测试，不能替代该 Rust 验证。下方未接通会话的描述是前一批历史状态。

状态：用户 review 后授权应用，补丁已写入独立 `agents/codex/controlled/`；手写控制运行与原生摘要已接线，未构建、未验证运行。没有改写 upstream、原 caller 或普通 Dockerfile。第 5A 批的补丁文本保持不变。

## 已应用的差异

`turn-control.patch` 涉及 6 个路径：core Cargo.toml 增加非默认 feature；session/mod.rs 和 context/mod.rs 注册受 feature 控制的模块；session/turn.rs 在 run_turn 初始化及主采样前调用 hook；新增两个小模块负责预算持久化和 ContextualUserFragment 提醒。没有新增依赖、协议字段或 HTTP 拦截计轮。

仅带 `codex-core/tokenana-turn-control` feature 的构建包含 hook；还需显式环境变量 `TOKENANA_CONTROL_DIRECTORY` 才启用。仅处理原生 SessionSource::Exec，子 agent / internal 调用不占主循环轮数。目录中预先提供：

```json
{"version":"codex-sampling-boundary-v1","profile":"gpt"}
```

文件名为 `control-request.json`；profile 只能为 gpt / claude / gemini，对应沿用预算 50→67 / 52→64 / 29→45，不代表 DeepSWE 实测分位数，也不增加模型协议支持。

hook 在主循环记录 world state / reasoning override 后、构建 sampling input 前运行；下层 run_sampling_request 的网络重试与同轮工具调用不重复计轮。每轮提醒进入原生 history，重试重建输入仍可见。压缩、辅助调用不在主预算内，其可观察 HTTP usage 应由 corrected 纳入。返回主循环的原生恢复若再次采样，会消耗下一轮。

P50 最后一轮原生完成则直接结束；仍需主采样时扩展一次。最终轮之后如果还需主采样，记录 budget_exhausted 并返回 TOKENANA_TURN_BUDGET_EXHAUSTED，由原错误分支发出事件并结束循环。hook 不改原生完成判断、工具、stop hooks、重试及其他限制，不提交 Git 或生成补丁。

`control-events.jsonl` 使用 create_new 独占创建，逐条落盘 initialized / before_sampling / extended / budget_exhausted。每轮在采样调用前持久化，失败交互仍计轮；这些是逻辑采样记录，不是 HTTP 请求完成证明。写入失败中止，不静默绕过。第二个原生 turn 或重启复用目录会失败，防止重置预算；TokenAna 后续适配只能恢复分析，不恢复生成。实际提醒文本同时保存在原生 history。

## 两阶段入口（本批仅完成应用）

第 5B 批已按授权应用到 controlled/；下列入口仅用于以后另行授权的新副本，不要对当前副本重复执行：

```bash
bash agents/codex/control-build/build.sh --apply-reviewed-patch /absolute/new/controlled-codex
```

目标父目录须存在，目标目录必须不存在。脚本复制原源码后仅在副本执行 patch -p1；失败保留副本供诊断，不清理、不重试、不构建。不会修改 upstream。原入口 git apply 在仓库子目录中返回成功但跳过路径；本批通过文件阅读发现后改为 patch -p1，实际应用输出列出全部 6 个文件。未做来源/哈希核验，也未执行 dry-run。

构建需要另行授权、已准备好的原生 Rust 工具链、依赖缓存及系统构建依赖。指定 TOKENANA_CARGO / RUSTC / RUSTDOC 为工具链实际二进制绝对路径（不是 rustup shim），再执行：

```bash
bash agents/codex/control-build/build.sh --build-offline /absolute/new/controlled-codex
```

入口使用 cargo --offline --locked，不安装、不主动下载、不制作镜像；依赖缺失则失败。原生依赖的 build script 行为未审计，构建环境应由调用方隔离网络。输出只在副本 tokenana-control-target/release/codex，不覆盖普通二进制。Cargo feature 传播、锁文件、工具链及编译均未验证；未运行格式化、语法检查或任何测试。

## 第 5B 批运行与分析接口

`adapter.py` 仅在存在 turn_control 预算时委派给 `controlled_runner.py`；普通 run_free 仍走原 WorkspaceCaller 和原 reader。控制预检要求 `controlled_executable` 为已准备的镜像内绝对路径、`control_version="codex-sampling-boundary-v1"`、record_raw_usage=true；公共原有 executable 配置仍须提供。控制路径只支持原 Responses 协议。

运行时才创建每调用原生 CODEX_HOME（artifacts/codex-home）与 control-request.json，显式关闭 ephemeral 并保存原生 rollout；不加载宿主个人配置、认证或旧会话。模型配置通过原 -c provider 参数指定。初始化日志作为 usage proxy 的就绪门禁，没有本次 hook 初始化就拒绝转发，不用请求次数计轮。环境变量和版本配置本身不是二进制能力证明；实际门禁及运行后原生提醒序列核对才提供证据。

容器内 GNU timeout 约束 agent 进程组，宿主 transport timeout 留出终止缓冲；真实信号/子进程清理未验证。确认命令返回后才调用公共数据集补丁捕获；外层超时/通信状态不确定时不收集，保留日志待 workspace 清理。capture-result.json 区分 nonempty / empty / failed / not_attempted_unconfirmed_stop。原 model.patch 和未提交诊断由数据集接口保存，不自动提交 Git；失败、预算耗尽和空补丁不提交，正式 patch.diff 清空，diagnostic.diff 保留捕获内容。

`summary.py` 从原 trace.jsonl、原生根 rollout、control-events.jsonl 和 process.json 重建结果，核对 root thread / turn ID、逐轮提醒及一次扩展，原生 error/turn.failed/abort 优先于生命周期完成。失配、缺失或截断不当成功。控制版身份及原生完成、无执行错误和非空补丁均满足时才能提交。

原生 FinalSummary 使用已结束的根会话、按原生 call_id/id 去重的工具调用数及累计 input/output；cache/reasoning 不再次相加。原 JSONL 在缺 usage 时会写默认零，所以要求 rollout 的 token_count 和逐 response token_usage_record 互相佐证，不能用默认零或 HTTP 补原生统计。采样数与已观察 response 数不对应、发生压缩无法确认完整覆盖、字段缺失或汇总冲突时 token 为 null；真实零值必须有原生记录证明。只支持该副本的未压缩根 .jsonl rollout；缺失/已压缩文件报未知，不装解码依赖或猜测。失败但存在最终原生摘要仍沿用方法纳入规则，均值为 null。

control-result.json 是可 review 的派生产物；analyze/resume 每次重读原始记录，不累加旧汇总，不重新生成。corrected-v1 保留全部可观察 HTTP 尝试、失败与辅助调用，不读取原生摘要替代 usage。本批没有接通其他工作线的通用会话回调，也没有更改其 overhead 归属规则。

## 未验证与下一批

本批只做文件复制、已批准补丁应用及静态源码阅读；没有写/跑测试、模块导入、语法检查、dry-run、格式化、构建、安装、下载、容器、远程操作或模型调用。实际构建、hook 加载、结束事件落盘顺序、网络门禁、timeout 清理和双口径运行兼容性均未验证。原生源码内测试/格式化指令服从用户本轮“仅静态检查”限制。

本批完成后停止；第 6 批配置矩阵、相同任务集合比较与统计文档收尾需另行授权。当前交付是接口接线，不是可运行保证。
