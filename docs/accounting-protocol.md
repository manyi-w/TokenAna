# TokenAna 统计协议

## 三个口径

original 由各方法自身的 original_accounting 负责，保留原取数、任务筛选、缓存处理、分母与舍入。新 agent 是带版本名的兼容投影，不冒充作者历史实验。

RQ1 与通用实验均以 `corrected-v2-api` 对照 original：从原始 HTTP 请求/响应重建全部实际生成调用，包含主模型、agent 辅助、方法辅助和失败/重试。本地压缩、剪枝 forward 不混入生成 token。通用旧 corrected 计算、双视图读取和派生报告已移除。

RQ1 论文值来自 `RQ1/paper-values.toml`，印刷费用直接参与对照运算，不以归档复算值覆盖。Gemini 原生 promptTokenCount 包含 cachedContentTokenCount，输出为 candidatesTokenCount + thoughtsTokenCount；网关协议须由实际字段及 total 校验，不能套用原生省略字段语义。cache_only 保留原输出和非缓存计价规则，完整 v2 才校正调用覆盖与长上下文档位。论文与新轨迹的差额单独报告，不作为纯缓存效应。

`cache_only` 是独立诊断：保持 original 任务、try、调用范围、输出、分母和价表，仅修正缓存。三方表同时给出 cache_only−original、v2−original、v2−cache_only；后者含其他口径及交互，不能当作独立因果分解。没有一一对应的调用身份时不猜测缓存差额。历史汇总可证明的缓存不变量或假定扣除恢复量仍可报告，但不能提升为完整 v2 usage。

按协议规范化 input/output，total=input+output。cache read/write 是 input 子项，reasoning 是 output 子项，不能再次相加。Anthropic 的普通 input 与 cache input 相加后才是统一 input；缺少必要字段则保持未知。原始 usage 和未知扩展字段保留。

每个响应以 task/attempt/call/response 身份去重；累计流式快照取最终明确累计值，不按快照求和。独立 HTTP 重试是独立发生的调用，不因响应 ID 相似而合并。冲突身份拒绝重建，不选一个值掩盖冲突。prepared 与 forwarded 请求分开；未转发不是已发生调用，已转发而无 usage 不是零。

## 分母与完整性

研究层每配置使用固定 selected 任务数。报告并列 selected、attempted、model_called、evaluated、resolved；逐指标输出 sum、known_subtotal、mean、complete、missing/reasons。只要一个应计调用的该指标未知，完整 sum/mean 就为 null，已知小计仍保留。

original 的 complete 只表示原规则可重现，不能证明实际 API 覆盖完整。original 筛掉失败或空补丁得到零，是原规则的结果，不能替代 corrected 的未知量。成功率为 resolved/selected，未完成评测时另标完整性，不悄悄删题。

## 有序偏差桥接

按 original → 字段规范化 → 任务筛选 → 调用覆盖 → 聚合规则的固定顺序导出中间结果、纳入任务和分母。只有原生日志 response ID 与原始 HTTP 有完整一对一映射时，才隔离字段规范化。无法建立映射的桥接为 null，禁止仅凭总数相等猜测同一调用范围。邻接两步均有证据时才计算差额；顺序变化有交互，不是因果分解。

报告同时提供可直接观测的主/辅助消费、缓存、失败 HTTP、成功任务筛选和无缓存折扣费用敏感性。没有证据证明某方法统计错误时允许差异为零或未知。

每个有 baseline 的方法重新使用它自己的 original reader/filter/rounding 投影 baseline，不能使用通用 baseline-native 总数充当所有原口径分母。原指标含义不同（如缓存估计）、缺失或范围不一致时，不生成共同排名。

## 费用

价表为冻结的 config/pricing.toml 快照，包含 provider/model alias、地区、币种、汇率与来源。按逐调用已知 usage 用 Decimal 计算；这是配置价格估算，不是账户发票。本地算力不计价；自托管生成仍进入 token，但无匹配价表时费用未知。

历史归档仅部分请求有缓存字段时，可单列“已知缓存命中的局部费用补算”：从论文印刷费用减去已知命中的 `缓存 token ×（原普通输入价 − 补充缓存价）`，其余请求维持原计价而非判为零命中。须注明补充价格来源、缺失范围和印刷值舍入差异，不将结果填入完整 cache_only/v2。turn_control 的两种价格情景、数字及完整公式见 [RQ1 文档](../RQ1/README.md#turn_control-已知缓存命中的费用补算)。

无缓存折扣敏感性保留原调用，只把缓存输入按普通输入计价；不代表实际冷缓存运行，也不改变观测时延。一次性经验准备有独立 ledger 时才可摊销；当前无实测记录时保持未知。

可选历史准备 ledger 位于运行目录 `preparation/experience.json`，schema 为 version=1、kind=historical_experience_preparation、configuration_ids（明确共用该库的 EET 配置 ID）、records（各项包含唯一 id、api_records 相对路径）。原始 HTTP 记录须一并保存在 preparation 下，不接受手填总 token 或重复引用目录。按冻结价表重建一次性费用，再除以声明范围的固定 selected 总次数；结果仅在 diagnostics.preparation 单列，不加进任务排名。历史准备的 timing.jsonl 可按相同区间协议留存。缺失、损坏和无法定价分别保持未知；该入口不构建经验库，也不允许用评测轨迹造经验。

## 时间和本地计算

执行墙钟、模型请求、工具、方法阻塞、观测留存、准备和评测分开。相同阶段的重叠区间取并集；强制中断缺结束事件则不完整，已知时段另列。阶段间嵌套不可相加。原始端点记录及 native 工具事件未提供足够区间时，不能用墙钟减其他值猜测工具耗时。

本地计算记录模型/tokenizer、真实 input_ids、forward 次数、状态和耗时。wrapper 发送输入长度仅是 submitted_input_tokens；需要后端模型观测才可报告实际 forward 输入。保留文本不是生成输出。PyTorch hook 的 host duration 不是 GPU kernel duration。

## 排名与统计

在 dataset/group/agent/model 可比单元内独立按 token、API cost、执行时间排序，并列解决率、触发率和缺项。六方法共同排名只用 Qwen3-Coder-Next，通用组比较五方法。缺价格不影响完整 token 排名。Verified 无论文 baseline，不产生节省率。

默认 savings=1-method_total/baseline_total，零分母不定义。跨单元只等权汇总所有方法共同完整单元的相对节省率，不混加不同模型 token。Pareto 最小化资源、最大化解决率，不合成加权总分。

使用固定 seed=20260921、10,000 次按仓库成组的配对 bootstrap，95% 区间与排名概率。方法与 baseline 重采样相同仓库块；并列排名共享名次。该区间不是模型重复采样方差。原口径排名变化只在共同完整、含义一致的指标范围计算。

## 内容、诊断和输出

不新增研究 tokenizer。字符量与 API token 分栏；结构化角色、工具类型与明确命令确定内容类别，混合 shell 输出或未知来源留 unclassified。稳定消息 ID 优先，否则采用精确内容摘要和出现次序标识重复，明确该后备标识不是语义身份。隐藏 reasoning 只报告 provider usage。

导出 classification-rules.json 和分层 review-samples.csv；样本是原始文件与位置指针。method-changes.csv 是同一次方法事件的直接前后观测，跨方法轨迹差异不作因果解释。四组结果、语言、任务类别（来源未提供则 unknown）、baseline 消耗四分位与机制触发诊断陪同全量结果。

单运行 accounting.json 使用 schema_version=2，只保留 original 与 v2；accounting/cases、费用和请求导出全部使用 v2，版本化 v2 明细及 policy-bridge 继续可读。study 输出 research.json/md、cases/aggregates/rankings/requests.csv、原口径投影、分组/资源诊断、时间/本地计算、内容/重复历史、paper-table.tex 和 PDF/SVG。图表缺依赖或被明确省略时记录原因，不宣称全套论文产物已生成。


## 逐题计算依赖

每次重建额外导出 `accounting-comparison.csv`、`accounting-steps.csv`、`accounting-trace.json`。比较表按 method/configuration_id、case_id、scope、metric 索引；`__all__` 表示当前集合汇总。main、agent_auxiliary、method_auxiliary 分栏，reported 对照作者原主列与完整生成总量，费用 all 包含所有生成用途。原方法未定义的费用不补造。

计算表达式同时生成统计值与步骤。JSON 保存 step_id、运算、操作数引用、数值类型、完整性、来源文件/归档成员与字段、证据类型及规则。CSV 每个操作数一行，formula 展示代入数值；多个口径可共享同一节点，应按比较表的 *_step 引用追踪，不能再次把步骤行求和。汇集多个配置时 CSV 后续新增字段保存在 extra_fields，仍与同一 JSON 节点一致。Decimal 费用保存十进制字符串；成功率引用保存的评测，不重新认证 pass@1。

来源区分 API usage、原生汇总、作者估计/假设、规则默认零与缺项。JSONL 使用原行号，SSE 的 event[n] 是从零开始的已解码 data 事件序号，归档使用 archive::member。原始数据不复制进图，只有用于计算的字段；不包含提示或凭据。缺少来源和无法隔离的桥接显式标注未知。耗时及本地 forward 本次不新增逐步表达式。
