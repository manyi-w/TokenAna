# DeepSWE Mac 单条、65 组合预检（2026-09-20）

当前结论：不能直接启动该实验。完成了只读检查和本地填写模板，尚未执行任何组合、真实 API、容器或评测；没有安装、下载、构建或提交 Git。

用户已确认本次范围：run_free、turn_control、AgentDiet、AttnCompress、EET；Codex 仅 gpt-5.6-sol，mini/Trae/OpenCode 各使用 gpt-5.6-sol、claude-opus-5、deepseek-v4.1-flash、qwen3.8-max。同一条数据共 5 × (1 + 3 × 4) = 65 个组合，先不扩至 20 条。用户明确允许本次使用真实 API，并选择自行填写项目内配置；这仅替代本次先期实验的假模型限制，不扩展下载、构建、原源码修改等授权。

## 填写位置

- [settings.toml](../config/local/deepswe-pilot/settings.toml)：服务地址、模型 ID、OpenCode 模型上限、辅助服务、镜像和容器内路径。
- [secrets.env](../config/local/deepswe-pilot/secrets.env)：密钥值。权限为 0600，Git 忽略规则已验证。
- [填写说明](../config/local/deepswe-pilot/README.md)：缺项和用途。

以上是配置收集表，尚未接入 CLI。现有 runtime 会完整写入 runtime.json，不能将真实密钥直接填入 runtime.environment 后运行；应先完成不进入快照的凭据注入。不会因为填表而自动调用服务。

## 已检查的实际阻塞

| 项目 | 证据与影响 |
| --- | --- |
| Docker | 本机 Darwin arm64、tokenAna Python 3.12.14。沙箱外只读 docker info 仍返回 Cannot connect to the Docker daemon；当前不能检查已有镜像，不能把失败输出中的 Images=0 当作镜像清单。 |
| Mac 通道 | `src/model_channel.py:validate_channel` 实际调用报 isolated model channel requires a Linux host with Unix sockets and local Docker。不是只改 TOML 就能运行。 |
| 单条任务枚举 | `plan_experiment` 选择 Python、显式 adaptix-name-mapping-aliases、limit=1 仍失败。读取器在筛选前校验所有任务，eicrud-keyset-pagination-cursor 的原始 base_commit_hash 为 68dafce，40 位校验拒绝它。应修适配层短 SHA/选择语义，不能改原数据或猜测补齐提交。 |
| 方法/会话 | 工作区正在出现比交接文档更新的实现：预检快照时 AgentDiet、AttnCompress 已有 manifest/adapter，EET 尚无；mini/Trae/OpenCode 声明通用会话，Codex 没有 run_session。新增文件不是已完成运行验收的证明，本次不改动这些其他工作产生的文件。精确快照见 preflight.json。 |
| Qwen | 现有 qwen3.8-max 模板地址为空；即使补地址，mini 拒绝非 OpenAI Responses，OpenCode 无 dashscope/responses 映射。Trae 有配置映射但未验证真实服务。不能静默换协议或换模型。 |
| AttnCompress | 原压缩服务使用 GPU 配置，注意力计算路径含无条件 torch.cuda.synchronize()，未提供可验收的 Mac CPU/MPS 路径。需要已有 CUDA 服务及其模型配置；失败回退不能算作真实压缩验收。其 log/compressed_messages.json 还会逐请求覆盖，后续留存需单独覆盖此行为。 |
| 运行配置 | 未提供/验证四类 agent 任务镜像和独立 verifier 镜像；Codex 独立构建产物未验收。OpenCode 缺模型 context/output 上限。任务要求每个容器 2 CPU、8192 MB 内存、20480 MB 存储配额；不能为 Mac 私自取消配额。 |
| 预算 | turn_control 的现有 gpt/claude/gemini 档位不能自动解释为 DeepSeek/Qwen 的分位数；需要确认映射。 |

单条候选为 Python 任务 `adaptix-name-mapping-aliases`，公开基准为 `a691069fcadf9131e5f7a5a130a022dc678f3e1d`。没有读取参考解或用隐藏测试驱动生成。

## 统计、计时和留存差距

现有双口径报告框架提供 original/corrected 的 JSON、TXT、CSV、Markdown 及 overhead 分项；本次没有生成 token 实验结果。original 继续按方法原筛选/公式及明确兼容版本，corrected 从原始 usage 纳入全部尝试、失败、主任务、agent 辅助和方法辅助。缓存与 reasoning 是按 API 语义拆出的明细，不能再次加入总量。未提供字段保持 null；AttnCompress 未暴露推理 usage 时必须保持未知，不能用文本压缩前后 token 差替代推理消耗。turn_control original mean 继续为 null。

已有 AgentResult.duration_sec 和 HTTP/service 操作 duration；service_seconds 是操作耗时之和，可能重叠，不是 case 墙钟时间。尚缺每个 case/attempt 的准备、生成、收集、评测、归档起止时间及方法本地 callback 开销；异常退出也须落盘。必须保存单调时钟区间和 UTC 时间、阶段/父子关联，并把归档等测量开销单列，不能用总时长减几个服务时长猜测 overhead。

当前 DeepSWE 结束时会强制删除容器；共享目录外文件会丢失。untracked-files.nul 仅是文件名，没有内容；uncommitted.diff 也不是全部文件归档。普通 Codex 路径的原生 session 留存同样需要确认。应在任何删除前完成可恢复的工作区、Git、未跟踪文件、原生会话/日志与 verifier 产物留存，失败保留现场；每次模型与压缩输入输出、工具输出、方法状态变更及各次尝试必须独立保存，避免只保留最终状态或覆盖上次结果。最终文件快照不能恢复已删除的中间版本，须明确增量快照/事件留存范围。凭据值排除在实验归档外。

## 建议的后续实施批次（尚未执行）

1. 公共运行证据：修改手写执行/记录/分析与 DeepSWE 适配层，新增逐阶段计时、完整留存和不进入快照的凭据注入；修任务选择/短 SHA 兼容。仅定向离线测试和单条 dry-run，先交付 review。
2. 完成并验收五方法与四 agent 的会话/统计路径、Qwen provider 映射及 65 组合配置；Codex 原生补丁仍须展示具体差异后获批，保留原 upstream。服务不可用不得默默退化并报告通过。
3. 准备实际运行环境：Mac 上使用 Linux 控制进程与任务容器的方案需验证 Docker socket/共享卷、Unix socket 与配额，不是删掉平台检查；AttnCompress 接已有 CUDA 服务或将全矩阵移至有 GPU 的 Linux。镜像下载/构建单独列明后执行。所有前提满足后才启动同一 case 的 65 次真实运行及独立评测，保持每组合独立输出目录。

先期单条运行成功不证明模型效果稳定，也不证明 Linux 多容器并行安全；之后扩到 20 条为 1300 次运行，需在共享压缩服务的并发日志隔离、归属、时间区间和资源约束验证后进行。

## 本批证据

- [结构化预检](../runs/preflight-deepswe-mac-20260920/preflight.json)：13 个 agent/model 配置映射检查、方法入口存在性、平台与枚举失败。
- [65 组合表](../runs/preflight-deepswe-mac-20260920/combinations.csv)：全部标记 blocked_not_started，非实验成绩。
- [单条 dry-run 失败记录](../runs/preflight-deepswe-mac-20260920/single-case-dry-run.json)：保留选择条件、错误和 traceback。
- TOML 解析成功；组合数量和唯一性检查通过；密钥文件权限/Git 忽略检查通过。没有运行完整测试套件、服务连通性检查或真实组件，0 个实验组合已执行。

检查期间工作区存在持续新增的组件实现。此记录只说明本批读取到的快照和上述实际检查，不替代其他工作线的交付/验收记录；运行前必须重新预检。
