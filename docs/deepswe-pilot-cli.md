# DeepSWE 本地试跑

在项目根目录执行。脚本激活已有 `tokenAna` conda 环境，不安装本机依赖。

```bash
# 只检查选择和配置，不构建、不访问模型
bash scripts/run-deepswe-pilot.sh --dry-run

# 先试一个组合
bash scripts/run-deepswe-pilot.sh --method agent_diet --agent mini --model gpt-5.6-sol

# 默认：同一条任务，四方法 × 13 agent/model 配对 = 52 个组合
bash scripts/run-deepswe-pilot.sh

# Linux：固定清单前 20 条，4 路并发
bash scripts/run-deepswe-pilot.sh --cases 20 --jobs 4

# 保留结果并接续；不重新生成已开始的模型尝试
bash scripts/run-deepswe-pilot.sh --resume /absolute/path/to/run --jobs 1
```

`--method`、`--agent`、`--model` 接受逗号分隔的多个值。方法为 `run_free,turn_control,agent_diet,eet`；agent 为 `codex,mini,trae,opencode`；模型为 `gpt-5.6-sol,claude-opus-5,deepseek-v4.1-flash,qwen3.8-max`。Codex 仅保留 GPT，跳过项会打印，无有效组合时报错。`--case` 接受多个 task ID，与 `--cases` 互斥；默认 `adaptix-name-mapping-aliases`。`--output` 指定一个不存在的结果目录，默认在 `runs/` 创建独立目录。固定清单见 `config/deepswe-pilot-cases.txt`，只使用原数据标为 Python 的任务；选择其他语言时不能包含 run_free。

配置读取 `config/local/deepswe-pilot/settings.toml` 和 `secrets.env`。已有误填到 api_key_env 的字面密钥会被识别，在内存中换成环境变量引用；不会进入结果快照、构建上下文或命令参数。建议后续按模板将密钥放入 secrets.env。两个本地文件均应保持 0600。不要把 credentials 放到 endpoint URL 中。

空镜像字段会自动准备选定 agent、任务和 verifier 镜像，构建日志在 `build/`。自备镜像可填入 images 字段，支持 `{task_id}` 占位，必须提供配置中的绝对执行器路径。Python agent、Pier 各有隔离依赖环境。构建上下文只包含明确选定的源码，排除配置、密钥、运行结果。镜像 ID 固定在 images.json，恢复时复用，不静默更换镜像。首次构建可能较久，终端显示当前镜像，详细进度写入日志。

Mac 使用 linux/amd64 Docker 镜像仿真，串行默认至少需要 Docker 分配 **8.5 GiB**（建议 10–12 GiB），任务本身仍是 2 CPU / 8 GiB。入口不会修改 Docker Desktop 设置。Linux `--jobs N` 要求至少 `2N` CPU 和 `8.5N` GiB。Linux 必须支持任务的 storage-opt 磁盘配额；Mac 按本次约定仅放宽这一项，并在 pilot.json 中标注。两平台应创建独立运行目录，耗时不能直接混合比较。Linux conda 路径不在默认位置时，先激活 tokenAna，或设置 `TOKENANA_CONDA_SH`。

OpenCode 上下文/输出字段为 0 时，使用明确的实验上限 131072/8192，并在启动时提示；它们不是服务商最大能力声明。mini 使用原生 `cost_tracking=ignore_errors` 接受自定义网关模型名，缺少单价不会阻止运行，token 仍按原生及 HTTP 两套记录统计；不能将未知价格解释为免费。实际模型 ID、协议保持用户配置，不静默转换协议或替换模型。

turn_control 的官网基线迁移预算为 GPT 53→74、Claude 91→123、DeepSeek 148→185、Qwen 102→134。来源为 `https://deepswe.datacurve.ai/artifacts/v1.1/trials.json` 中 full/deep-swe、排除 errored 的 n_agent_steps，线性 P50/P75 向上取整。参考配置为 mini-swe-agent 下 GPT/Claude/DeepSeek 的 max、Qwen 的 xhigh；DeepSeek 参考版本是 v4-flash。这是跨 agent/版本迁移预算，非本次四 agent 的实测分位数；不会静默改变各 agent 的原生推理设置。

## 结果和留存

`summary.csv` / `summary.json` 每个组合与 case 一行，明确 original/corrected input、output、total、overhead、评测状态和耗时。完整统计及缺失原因保留在 `combinations/<组合>/run/accounting.*`，后续可以用已有 `python -B -m tokenAna analyze <run目录>` 离线重算。缺失 token 不填零。

保留全部尝试的 HTTP 请求/响应、辅助调用、原生轨迹、工具记录、实时 stdout/stderr、方法状态版本、补丁、评测报告。每次主录制通道转发请求前保存 `/app,/tmp,/root,/home,/logs` 的 GNU tar 增量快照（包括删除记录），记录 snapshot 耗时；这是采样边界快照，不是每条 shell 命令内瞬时文件的版本追踪。完整容器文件系统在结束时停止容器后导出；另行保存可写挂载卷。输出挂载已直接写入结果目录。快照恢复应按顺序使用 GNU tar 的 listed-incremental 语义，不能把后一个增量当完整目录。

只有验证归档可读取后才删除任务／评测容器。归档失败保留容器，retention.json 标明状态。控制器失败也保留，其名称在 controller.json；共享模型通道卷名称保存在 channel-volumes.jsonl，不自动删除。运行中断后若容器仍活跃，恢复入口拒绝开始另一次生成；先检查并停止对应容器。恢复保留 failed/agent_failed 结果，不自动重新花费模型调用；如需重新生成，使用新的 output。评测失败可以从保存补丁重试。

timing.jsonl 保存事件原始时间与时长，覆盖环境准备、生成、收集、评测、归档、清理、分析、方法准备与回调；turn_control 自有控制 hook 另外测量 control_callback。方法回调耗时含辅助 API 等待；HTTP duration_sec 不含快照时间。嵌套阶段和并发请求耗时不可直接相加，原始文件可用于后续 calibration。强制杀进程可能只留下 start 事件，不能把缺失结束时间算作零。

## 验证边界

实现已做离线测试和 CLI dry-run；未在本轮下载／构建镜像、启动真实 agent 或调用真实模型。首次由用户运行时才验证镜像、Rust/Bun 构建、服务和任务的真实兼容性。遇到问题保留日志和全部已产生结果，不自动切换模型或降低实验资源限制。
