# TokenAna 操作者说明

## 环境与配置

运行 Python、验证和实验前执行 conda activate tokenAna，使用 Python 3.12.14。宿主不自动安装依赖；controller、agent、评测器和 GPU 服务分别使用已准备环境。公共离线核心只依赖标准库；图表需要 requirements/analysis.txt。

将 config/examples/deepswe-settings.toml 和 deepswe-secrets.env.example 复制到 config/local/deepswe-pilot/settings.toml、secrets.env。端点与实际模型 ID 必须由操作者填写；api_key_env 只存变量名，密钥放 secrets.env，权限建议 0600。设置、快照和构建上下文不保存凭据值。

AttnCompress 使用 methods.attn_compress.service:create_app 包装原服务。SWE-Pruner 使用 methods.swe_pruner_pro.service:create_app(checkpoint, tokenizer_path, backend_base_url, hidden_size)，要求已有 Qwen3-Coder-Next backbone、匹配 head、GPU 环境和原依赖。TokenAna 不训练 head，也不把备用算法当作已验收。后端实际 forward telemetry 缺失时相应指标保持未知。

## 固定论文运行

```bash
python -B -m tokenAna study experiments/paper.toml --output runs/paper-selection
bash scripts/run-experiments.sh --study experiments/paper.toml --dry-run
# 仅在环境与真实组件验收完成后执行：
bash scripts/run-experiments.sh --study experiments/paper.toml --output runs/paper-run --jobs 1
```

检查清单应为 109 配置和 12,278 次任务。--study 不与独立筛选参数、--count、--rerun 或 --pricing 混用。study 先执行 baseline，再冻结 turn_control 预算；Verified 无 baseline。EET 原经验文件复制到 inputs 固定使用。

images 配置为空时，实际启动器按选中的 agent/task 准备镜像；自备镜像支持 task_id/dataset 占位。原任务配额及 timeout 不降低。主模型/压缩共享资源默认串行；离线 jobs=4 与正式 measurement_jobs=1 是不同参数。正式研究并发不可在恢复时随意变化。

## 恢复与独立实验

```bash
bash scripts/run-experiments.sh --resume runs/paper-run --jobs 1
bash scripts/run-experiments.sh --watch runs/paper-run
```

恢复保存的矩阵、profile、预算、价格、镜像和留存模式；只补齐未执行部分或恢复评测，不自动重新生成已开始失败的任务。仍活动的旧容器必须先由操作者处理。HTTP 原生重试保持不变。

独立实验可用 --dataset、--method、--agent、--model、--case 或 --count N。--count 是每组合下一批尚未登记的任务，允许各组合选择不同，因此不可拿它替代固定 study。默认独立入口为四方法×13 agent/model 配对；--method 可显式选择 baseline、AttnCompress 或 Pro。--rerun 明确创建新的生成尝试，不用于论文固定矩阵恢复。

普通单运行入口仍支持 python -B -m tokenAna run EXPERIMENT.toml --runtime RUNTIME.toml --output RUN_DIR，--dry-run 仅规划；评测恢复可用 python -B -m tokenAna evaluate RUN_DIR local --execute。DeepSWE 原 Pier 与 Verified 本地官方 harness 各自保存日志。

## 离线重建

```bash
python -B -m tokenAna analyze RUN_DIR --output NEW_ANALYSIS_DIR
python -B -m tokenAna study experiments/paper.toml --runs runs/paper-run --output runs/paper-report --jobs 4
```

目录必须是新的。study 从每个固定任务的原始记录重建到 reconstructed，不覆盖 RUN_DIR 中的旧报告；源数据缺失、profile/价格不匹配、仍在运行或重建错误会进入完整性/错误表。无需访问模型或评测器。

默认尝试生成 PDF/SVG。准备过 matplotlib 的分析环境可直接运行；--no-figures 明确只导表。可选择 scripts/analysis.Dockerfile 构建独立分析镜像，宿主无需安装；构建本身需要操作者授权与网络。映射源运行目录时须保持保存路径可访问，输出目录单独可写。

## 证据与工作区恢复

新运行默认 retention_mode=research：保存最终 /app 或 /testbed、/logs 的压缩增量，保留已提交/未提交修改、新文件与 Git 元数据；删除路径在 retention.json，基础镜像记录 image_id 和 tokenana-retained 标签。已保存挂载不重复归档，其他可写卷单列。迁移 Docker 前集中保存所需基础镜像。

恢复时以对应镜像创建隔离容器，处理 deleted 路径及同名类型冲突，再覆盖 workspace-delta.tar.gz，按挂载映射恢复日志/外部卷。research 只承诺最终工作区与研究证据，不保存每个中间文件或整个 OS。full 模式额外保存逐请求增量与容器文件系统；恢复增量需按顺序使用 listed-incremental 语义。

请求/响应、原生轨迹、方法前后快照、模型配置、价格、补丁和评测记录均保留。归档验证失败时容器保留；查看 retention.json、controller.json、channel-volumes.jsonl，不擅自清理尚未确认完整的证据。

## 验证

```bash
python -B scripts/validate_study.py --jobs 4 --output runs/study-validation
python -B scripts/validate_retention.py --jobs 4
```

第一条为独立临时目录中的离线单元与合成重建测试，第二条为留存边界验证。scripts/validate_mini_component.py 在准备好的 mini 任务镜像运行，使用容器内回环假模型和临时仓库；它验证实际 CLI，不衡量解题效果、不等于官方数据集端到端验收。

尚缺的真实验收按开发流程表补齐：四 agent、两数据集、五语言、六方法，包含失败重试、缓存、辅助调用、GPU forward、流式去重、中断与评测恢复。跳过、缺环境与未执行分别记录。正式运行不会由离线验证自动启动。
