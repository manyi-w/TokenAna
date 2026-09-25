# RQ1：论文 setting 的复现与 token / cost 审计

四个入口执行固定 setting，复用公共任务执行、恢复、留存和官方评测；每次生成同时保留原生统计与原始 HTTP usage，生成论文值、同次运行 original、完整 `corrected-v2-api` 及 `cache_only` 对照。**已实现并完成离线检查，尚未完成真实组件验收或正式模型实验；不能保证新采样与论文数字完全相同。** RQ1 独立于 `experiments/paper.toml`，不改变原 109 配置矩阵，不新增 baseline。

## 四个入口

在仓库根目录，使用本地 `RQ1/settings.toml`，填写端点与凭据环境变量名称；任务镜像映射可以留空，运行时自动准备。当前四组主模型与 AgentDiet 辅助模型共用 `config/local/deepswe-pilot/settings.toml` 中 Claude/GPT 的第三方端点和密钥，保持下表冻结的模型 ID；密钥通过本地 `config/local/rq1/secrets.env` 加载为 `RQ1_API_KEY`，不写入 RQ1 配置快照。AttnCompress 的 `RQ1_GOOGLE_OFFICIAL_API_KEYS` 仅用于可选 Google 官方密钥，本设置留空，不把第三方密钥发往官方端点。脚本自动激活 Conda `tokenAna`，要求 Python 3.12.14；镜像内依赖由构建安装，不修改宿主依赖，不自动降级模型。

```bash
source config/local/rq1/secrets.env
bash RQ1/run_free/run.sh --run
bash RQ1/turn_control/run.sh --run
bash RQ1/attn_compress/run.sh --run
bash RQ1/agent_diet/run.sh --run
```

四条命令分别启动一组真实实验，完成生成、官方 SWE-bench Verified 评测和报告。每组支持以下操作（替换方法目录即可）：

```bash
bash RQ1/turn_control/run.sh --check
bash RQ1/turn_control/run.sh --resume RQ1/runs/turn_control/<run-id>
bash RQ1/turn_control/run.sh --report RQ1/runs/turn_control/<run-id>
```

`--check` 检查配置、固定任务、Docker 可用性、宿主依赖及压缩服务；不要求镜像提前存在，不下载或构建镜像，不发模型请求。`--run` 自动准备全部镜像，冻结镜像 ID，再检查镜像内客户端/工具及评测依赖，全部通过后才调用模型。`--resume` 使用已保存配置、镜像 ID、价格和任务集合，恢复未完成的任务准备或评测；不重建、替换已冻结镜像，已开始生成的失败/中断任务不自动重新生成。`--report` 只重建已停止运行的报告。可用 `--settings <path>` 或 `RQ1_SETTINGS` 指定设置。

首次运行会拉取各题的 `swebench/sweb.eval.x86_64.*:latest` 基础镜像，并添加各组所需的客户端及工具 Python。默认 `tokenana-verified-evaluator:latest`（`verifier_image` 未填时也使用它）由项目内官方 SWE-bench 源码自动构建。`[images.<method>]` 可选逐题覆盖完整任务镜像；显式设置其他评测镜像时使用该镜像，本地缺失则拉取。所有任务镜像在生成前固定为不可变 ID；后续新运行复用 Docker 下载与构建缓存。

下载及构建每 15 秒显示进度，完整日志和逐题镜像记录先写入 `RQ1/runs/<method>/.preparation/<run-id>/`。正式运行目录创建后，退出时归入该运行的 `image-preparation/`。若镜像阶段失败，没有模型调用，日志保留在原处；修复原因后重执行 `--run`，复用 Docker 缓存继续准备。已有正式运行使用 `--resume`。

## 冻结 setting

| 方法 | 主模型及执行方式 | 任务与方法参数 |
| --- | --- | --- |
| run_free | Claude Code CLI **1.0.16**，`claude-sonnet-4-5-20250929`；原 PromptBuilder/AgentCaller 调用 `claude -p` | 完整 Verified 源文件顺序前 **100** 题；原 run_free 提示和 Git 限制，1200 秒/题 |
| turn_control | **`gemini-2.5-pro` 正式版**；已批准的现代 Trae 同会话控制适配 | 原 **100** 题；P50=28.5、P75=44.5 向上取整为 **29→45**，最多扩展一次；temperature=0、max output=8192 |
| AttnCompress | `gemini-3-flash-preview`；作者原 Expert；Qwen3-4B-Instruct-2507 压缩代理 | 原 eval **200** 题；block/ppl、IQR −2、greedy、最后层、ratio=.2、tail=2、rolling=10、refresh=1，max turn=50 |
| AgentDiet | `gemini-2.5-pro`；作者原 Expert；辅助 `gpt-5-mini-2025-08-07` | 原 eval **200** 题；threshold=500、ctx_before=1、ctx_after=2，max turn=50 |

每组 `experiment.toml` 与 `source-tasks.txt` 冻结规格和题序；不改成共同子集。AttnCompress/AgentDiet 的 approach100 不是本 setting。run_free 不启用通用实验的多语言扩展。原算法、提示、客户端重试和 original 计数规则保留；配置与观测位于适配层。

turn_control 历史归档实际响应标识是 `gemini-2.5-pro-preview-06-05`，用户指定的新运行采用正式版；报告保留这个区别。新入口复现方法与预算规则，**不宣称是历史 Trae 执行器及 preview 模型的逐位复现**。29→45 来自原分位数；50 是原始最大轮次参数，不是扩展目标。

正式生成统一串行 `measurement_jobs=1`；作者 AttnCompress 启动参数中的 worker_num=10 保留为原配置记录，不当作新测量并发。官方评测 jobs=4。墙钟与作者历史并发环境存在差异，不能只归因于方法。

## 实验环境

使用 Linux Docker 环境，镜像准备阶段需要网络。自动构建保留各题基础环境与 base commit：run_free 添加 CLI 1.0.16、nonroot 权限及配置脚本所需目录，turn_control 添加本项目支持的 Trae/Google SDK，AgentDiet/AttnCompress 添加原工具使用的 Python 路径，评测镜像包含本地官方 harness。AgentDiet/AttnCompress 宿主 `tokenAna` 仍需作者依赖（openai、docker、pexpect、tiktoken、lz4；AttnCompress 另需 google-genai、httpx），GPU 压缩服务也须可用。具体检查见入口；不以当前开发机缺项限制正式硬件配置。

AttnCompress 在准备好的 GPU 环境使用原压缩服务和独立 forward 观测：

```bash
TOKENANA_ATTN_MODEL_PATH=/models/Qwen3-4B-Instruct-2507 \
  uvicorn methods.attn_compress.service:create_app --factory --port 46405
```

服务 max_tokens=300000，保留原部署资源配置；健康检查拒绝误用通用 8B 配置。所有实际模型请求经过观测代理；不把本地 forward 或文本压缩长度当作生成 API usage。任务容器复用公共隔离模型通道；默认 research 留存保存最终工作区增量、删除记录、请求/响应、原生轨迹、方法变化、补丁、配置和评测日志。生成失败记录仍参与完整性判断。

## 论文数字与新报告

[paper-values.toml](paper-values.toml) 保留论文印刷精度和出处，与归档复算值分开。费用差额直接以论文数字为操作数：

| 论文 setting | 论文费用 | 报告的可比范围 |
| --- | --- | --- |
| run_free | 未报告美元费用 | 原 token 可按另列补充价表计价，不能称作论文美元值 |
| turn_control | **$98.05 / 100 题**；Fixed 75 为 $129.32 | 主模型 API；不用归档 $98.0485525 替换 $98.05 |
| AttnCompress | 主模型 **$0.1543/题**，本地折算 $0.0027/题，合计 $0.1570/题 | API 对照用 $0.1543×200；本地折算另列，不加入新 API 费用 |
| AgentDiet | **$0.285/题**，历史 baseline $0.385/题 | 主＋方法辅助 API；论文总量操作数为 $0.285×200=$57.000 |

新费用冻结论文/作者单价；缺失的 Gemini 缓存费率、长上下文规则和 run_free 美元价表作为补充来源单列。turn_control 正式版补充缓存价不声称是历史 preview 账单。AgentDiet 原 0.31/0.03 缓存价格与原计价假设保留；完整 v2 使用冻结单价计算实测分项，不擅自换成现行市场价格。original 中的原错误定价条件也保留，完整 v2 才修正；缓存专项不夹带非缓存规则变化。

每次运行保存在 `RQ1/runs/<method>/<run-id>/`：

- `rq1-report/summary.md`：论文原值、同单位论文对照、同轨迹 original/cache_only/v2；未知显示 null。
- `rq1-report/paper-comparison.csv`、`paper-values.csv`：论文印刷值与新结果；AgentDiet 论文 I/O 均除以历史 baseline input，新报告另列绝对 token，不新跑 baseline。
- `rq1-report/cases.csv`、`metrics.csv`、`results.json`：逐题状态、结果、统计和完整性。
- `rq1-report/accounting-comparison.csv`、`accounting-steps.csv`、`accounting-trace.json`：逐指标、逐操作数来源与计算依赖。
- 上一级 `accounting.json` 及公共导出：全部调用、主/辅助用途、重试/失败、本地计算和完整费用；任务目录保存原始证据。

`cache_only` 仅校正原选中响应范围的缓存处理，保留 original 的输出、筛选、补偿和价表规则。完整 v2 覆盖本次所有实际调用、失败重试、辅助模型及 reasoning，缓存/reasoning 子项不重复相加。无法唯一关联原生响应和 HTTP usage 的缓存专项保持未知。完整统计使用固定任务分母，缺失不填零；同一补丁的成功率不因记账校正改变。论文与新运行的差额还包含模型版本、采样和环境变化，不能表述成纯缓存效应。

## 实验前能确认什么

- **run_free**：原 Claude reader 只加 `input_tokens`，漏掉 Anthropic 独立的 cache read/write；实际差额须看新请求是否发生缓存。
- **turn_control**：历史 Gemini 最后 try 有 2,573 条 usage，其中 645 条明确缓存命中，已知命中合计 **11,910,948 token**。原函数未使用缓存折扣。因此问题是原费用计算忽略已存在的命中，不能概括为“完全没有开 cache”。未报告缓存字段的请求及完整重试覆盖仍未知。
- **AttnCompress**：主输入直接累加 API prompt；cache/cost 使用公共前缀字符数÷4 的估计。可确认其缓存费用不是实测，但不能预先证明本次差额方向或数额。原 analysis counter 为零不等于本地压缩没有开销。
- **AgentDiet**：按用户约定，将作者的 Gemini 接口视为标准 Gemini OpenAI 兼容接口；**该 setting 不把主模型重复加缓存列为问题**。标准 `prompt_tokens` 已含缓存，缓存分项位于 `prompt_tokens_details.cached_tokens`；原 Expert 额外读取的顶层 `cache_read_input_tokens` / `cache_creation_input_tokens` 不存在，故额外相加为零。审计重点为原主费用的“全部输入缓存价＋2% 普通价”，以及辅助模型每次假定扣除 492 token；这些是原计价/计数假设，具体偏差须用实测 usage 计算。

上述 AgentDiet 结论是在约定接口语义下对[作者 Trae Expert](../methods/agent_diet/upstream/code/trae_agent/agents/expert.py)及[原样传递 usage 的客户端](../methods/agent_diet/upstream/code/trae_agent/utils/llm_polytool.py)进行的静态判断，不是历史接口已实测的声明。原客户端使用 OpenAI 兼容协议，此处不切换到原生 generateContent 协议。非标准网关额外返回顶层缓存字段的情况不作为该 setting 的研究前提；新运行仍保留实际响应并据实统计，不能用本约定把历史缺失的缓存命中量或完整费用填零。

这些是源码与归档支持的结论，不预设四篇的每个数字都错误，也不以字符估计替代实际 usage。

### turn_control 已知缓存命中的费用补算

对原 Dynamic 50→75 setting 的 100 题，沿用作者最后 try 的选择，2,573 条 usage 中有 645 条明确记录缓存命中，合计 **11,910,948 token**；其余 **1,928 条缺少缓存字段**。这些请求均未超过 200,000 输入 token，因此普通输入/输出分别使用论文的 **$1.25/$10 每百万 token**。输入 **57,526,042**、输出 **2,614,100** 保持原值；本项只补算已知缓存折扣，不重新生成或评测。来源见[原 usage 日志](../methods/turn_control/upstream/experiments/gemini_50_75_total/output/)、[原费用函数](../methods/turn_control/upstream/extract_function_calls.py)和[论文 §2.2、Table 5](https://arxiv.org/html/2510.16786v2)。

论文没有给出缓存单价，以下分别列为两种补充价格情景，以**论文印刷值 $98.05** 为计算起点：

| 补充缓存价格情景 | 缓存单价（USD/百万 token） | 补算后的总费用 | 比论文少 | 相对论文下降 |
| --- | ---: | ---: | ---: | ---: |
| 按 2025 年官方公布的 75% 缓存折扣推算 | 0.3125 | **$86.88** | **$11.17** | **11.39%** |
| 按项目当前冻结的正式版缓存单价 | 0.125 | **$84.65** | **$13.40** | **13.67%** |

第一种情景依据 [Google 2025-05-08 公告](https://developers.googleblog.com/gemini-2-5-models-now-support-implicit-caching/)，缓存单价由 `1.25 × (1 − 75%) = 0.3125` 推得，不提前舍入为 0.31。第二种使用项目于 2026-09-24 冻结的[正式版 Gemini 2.5 Pro 价表](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-pro)。两者都不是论文直接报告的缓存价，也不能据此确认历史 preview 模型/网关的实际账单费率。

计算步骤（费用单位 USD；百分比以论文 $98.05 为分母）：

- 75% 折扣：`折扣额 = 11,910,948 / 1,000,000 × 1.25 × 75% = 11.16651375`；`总费用 = 98.05 − 11.16651375 = 86.88348625`；`下降比例 = 11.16651375 / 98.05 × 100% ≈ 11.39%`。
- 0.125 缓存单价：`折扣额 = 11,910,948 / 1,000,000 × (1.25 − 0.125) = 13.3998165`；`总费用 = 98.05 − 13.3998165 = 84.6501835`；`下降比例 = 13.3998165 / 98.05 × 100% ≈ 13.67%`。

**这两行仅是已知缓存命中的局部费用补算，不是完整 cache_only 或 corrected-v2-api 总费用。** 缺字段的 1,928 条请求维持原计价，不表示认定缓存命中为零；未审计的重试、辅助调用等费用范围保持原状。原轨迹逐项复算的未舍入费用为 $98.0485525，与论文印刷值相差 $0.0014475；上表按用户约定直接使用 $98.05，不以未舍入值替代。完整历史缓存费用仍为未知，这些情景数值不填入历史报告的完整费用栏，也不改变新运行的冻结价表。

## 历史归档复算与当前验收

```bash
bash RQ1/replay.sh
```

此命令不调用模型、不运行官方评测；重建 [历史报告](reports/summary.md)。run_free 没有对应历史轨迹；其余 500 题次复用原归档。Gemini turn_control 原费用复算为 $98.0485525，舍入为论文 $98.05；AttnCompress 和 AgentDiet 归档费用分别为 $30.8528408、$57.032897705，均与论文印刷值独立保留。

Python 3.12.14 下已完成历史复算、固定题集、4 worker 合成报告与缺失证据检查，以及原生 Gemini 代理回环假响应的路由、usage、调用关联和凭据脱敏检查。没有真实模型请求；没有验收四套真实客户端、GPU 压缩、完整镜像执行及官方评测。当前宿主原生依赖尚未备齐；实际运行前须完成上述环境准备。离线报告可复算不等于正式实验已验收。
