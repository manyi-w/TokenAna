# DeepSWE 静态交付说明

2026-09-20，按用户“这次一次性做完”授权完成本工作线剩余第 6 批。范围为已确认的 run_free、run_free_multilingual、turn_control × Codex/mini/Trae/OpenCode；不包含另一工作线尚待接入的四个压缩/经验方法。代码与接口接线交付完毕，运行兼容性未验证，不能宣称已保证可运行。

## 交付入口

- 实验矩阵：[experiments/deepswe/README.md](../experiments/deepswe/README.md)，20 个独立 TOML：run_free Python 34 × 4、run_free_multilingual 非 Python 79 × 4、turn_control 全部 113 × 4，以及控制 Python 34 × 4、非 Python 79 × 4 的公平比较配置。
- runtime.template.toml：113 个 agent 镜像键和 113 个 verifier 镜像键，空值故意阻止未准备运行。每种 agent 使用自己的预备镜像配置；不自动安装、拉取或构建。
- datasets/deepswe/：任务映射、语言/ID/limit、独立 /app、原补丁捕获、隔离模型通道与原 Pier 本地评测。
- 四个 agent 的 turn_control 与公共 FinalSummary 已接线；Codex 原副本保留，review 后补丁只应用到 controlled/，未构建。
- JSON/CSV/Markdown/终端继续并列 original/corrected；新增 accounting_context、逐 case prompt_metadata/native_final_summary。analyze 与 compare 复用同一重建/导出，不累加旧汇总。

## 本批静态发现及修正

1. Trae 原 must_patch 依赖 plain git diff，DeepSWE 提交后会空。手写 adapter 将 Workspace 的公开 patch_base_commit 传入原生 base_commit 参数，内部检查改看已提交差异，保留原生测试文件过滤、task_done 和其他完成逻辑。最终提交资格仍依赖退出后公共捕获的同一份产物。普通 Verified 未提供该属性时保持原 plain diff，不修改上游。
2. Trae 较低的原生 max_steps 现在仍会提前停止，不因 P50/P75 控制而被放宽；延长仅一次，并受原限制约束。最终摘要独立 reader 将缺失 usage/失配标为未知，不再把缺失字段补成零；run_free 原 reader 保留。
3. 原 Pier 返回 reward 0/1 后仍须有可读的原生 CTRF results/tests；缺失或损坏报告为异常 resolved=null。恢复/附加报告也检查已完成记录的 CTRF，不能仅凭奖励文件当作已完成。没有重新计算评分公式。
4. DeepSWE 运行预检新增 verifier_python 绝对路径要求；prepare_submission 本身也拒绝 submission_eligible=false，避免绕过调度层后把诊断补丁送评。
5. 本批控制行为/摘要边界变更以 turn-control-native-summary-v3 标记；已有旧版本可用当前适配器离线 analyze（报告会说明使用当前适配器重建），不允许不同方法版本恢复继续生成。

## 静态审查记录

| 项目 | 静态证据/结论 | 运行状态 |
| --- | --- | --- |
| 语言分组 | 直接读取 task.toml：113 个唯一 task_id；Python 34、Go 34、TS 35、JS 5、Rust 5；配置分为 34/79/113，组件路径均存在 | 未执行配置或枚举适配器 |
| 任务输入隔离 | 公共 Task 仅 ID/repo/base/problem；agent 不挂载任务包；verifier 单独镜像/日志，只评测侧访问测试/评分资料 | 镜像内容和实际挂载未验证 |
| 原补丁约定 | 113 个原收集命令均为 git diff --binary BASE HEAD；四 agent 调公共捕获；model.patch 原文保存，未提交单独诊断 | 实际 Git/容器交接未验证 |
| 控制边界 | 主采样/query/消息转换处计轮；底层 HTTP 重试和同轮工具不另计轮；一次扩展、原生完成优先、最终耗尽禁止提交 | 四 agent 实际停止边界未验证 |
| 模型网络 | agent network none，仅回环/socket/宿主代理；verifier 不配置通道或 agent 凭据 | 流式协议、并发、超时、Linux socket 未验证 |
| 原生摘要 | 控制统计不按成功/补丁筛选；只报三项总和，mean=null；原生未知保持未知，兼容来源进入报告 | 原始真实轨迹与各版本行为未验证 |
| corrected | 原 HTTP usage，全部尝试/失败/可观察辅助调用；缓存和 reasoning 明细不重复加总；缺失与显式零区分 | 服务 usage/缓存语义未验证 |
| 恢复 | 从所有原始尝试和调用身份重建；控制开始后不自动重新生成；评测只使用已保存补丁并保留已完成检查点 | 中断/损坏/重复分析场景未运行 |
| 评测异常 | reward 1/0 映射 bool，-1/超时/缺失 reward 或 CTRF 归异常 null；检查 run_id、任务和保存补丁 | Pier 容器与原评分脚本未运行 |
| 同集合比较 | 8 对 34/79 配置，比较器校验完整任务和实际纳入集合/规则/完整性；不拿 34 对 113 算比例 | 未生成或比较实验结果 |

静态读取仅用文本工具和标准库解析任务/配置数据，没有导入项目组件、调用 CLI、执行 dry-run 或做源代码语法/编译检查。上述“静态证据”不是测试通过结果。

## 统计兼容边界

run_free 原始规则保留非空补丁筛选、原生日志取数和向下取整均值。Git 禁令移除及多语言策略有独立方法版本，不能作为作者历史运行复现。各 agent 兼容规则/最终摘要 source 和 DeepSWE deepswe-committed-v1 记录在报告中。

turn_control 的 original 均值一直为 null；与 run_free 原口径规则不同，不自动给跨规则节省百分比。corrected-v1 才在选择集合、纳入集合、规则及指标完整性一致时计算变化。不同解决率不能仅凭 token 变化解释为相同效果节省。

Codex 原生摘要当前只解析本地副本未压缩的根 rollout；缺失/压缩文件、usage 缺失、压缩后采样覆盖无法佐证等保持未知，不安装解码依赖、不拿 HTTP 填原口径。原生摘要不能证明全部辅助消费；corrected 包含经过已录制模型通道的可观察调用，绕过通道的调用仍不可见。已有 overhead 字段由另一工作线提供，本批不伪造尚未映射的主/辅助归属。

## 明确未执行

本工作线本轮没有写测试、跑测试、dry-run、模块导入检查、语法检查、格式化、编译、依赖安装、源码/镜像下载、容器、远程操作、真实或假模型调用、评测或 Git commit。另一个工作线既有离线测试记录不能视为 DeepSWE 的验证结果。

以后实际运行前仍须由用户准备镜像、模型服务/协议、密钥、OpenCode 模型限制和 Codex 控制版构建。若要进行构建、测试、容器或模型验证，需要另行授权；没有以本交付偷偷执行这些步骤。本次授权内的静态实现工作已完成，没有等待下一实现批次。
