# DeepSWE 实验矩阵（静态交付）

本目录包含 20 份独立实验配置：四个 agent × 五组任务/方法，以及一个 runtime 模板。代码与接口接线已交付，运行兼容性未验证；本轮未运行任何配置、dry-run、测试、构建、容器、评测或模型。

| 配置前缀 | 方法 | 任务集合 | 数量 |
| --- | --- | --- | --- |
| run_free_python | run_free | Python | 34 |
| run_free_multilingual | run_free_multilingual | Go / TypeScript / JavaScript / Rust | 79 |
| turn_control_all | turn_control | 全部语言 | 113 |
| turn_control_python | turn_control | Python | 34 |
| turn_control_nonpython | turn_control | Go / TypeScript / JavaScript / Rust | 79 |

每行都有 `_codex.toml`、`_mini.toml`、`_trae.toml`、`_opencode.toml` 四个文件。所有配置开启 record_raw_usage，使用已有 GPT Responses profile 和示例本地服务地址；服务需另行准备及授权，配置不启动服务。更换模型须遵守 agent 原有协议支持，预算档位须在 method.options 中显式选择；gpt 50→67、claude 52→64、gemini 29→45 都不是 DeepSWE 实测分位数。选 Gemini 预算并不提供 Google 协议。

任务选择按原 task_id 排序，先语言/ID 交集再 limit；模板未设置 limit，保留完整组。静态元数据计数：Python 34、Go 34、TypeScript 35、JavaScript 5、Rust 5。Python 与非 Python 不重叠，合计 113。完整 113 条控制运行不能与 34/79 条 run_free 直接计算节省比例。

## 准备项

复制 runtime.template.toml 为自己的 runtime 配置，填入所选任务的 images 和 verifier_images；模板两张表均列出 113 个任务但值为空，预检会拒绝。不同 agent 用各自预备镜像映射，不能认为一套 Codex 镜像自带全部 agent。

- Linux、同机 Docker；agent/verifier 固定 network=none，分别满足原任务资源、时间限制。存储配额不可用则报错，不自动放宽。
- agent 镜像：干净 /app 位于原 base_commit；含选定 agent、依赖、GNU timeout，以及 model_channel.python 指定的现成 Python 3；不含 /tests、/solution 或其他隐藏评测资料。
- verifier 镜像：干净 /app 和原样 /tests/test.sh、grader.py、test.patch、config.json 及依赖；root Python 3.12+ 和 Pier distribution metadata。不能包含参考补丁。评测器不挂模型 socket、不接收 runtime 的 agent 凭据。
- 通道：容器内 loopback → 挂载 Unix socket → 宿主 usage proxy → 配置的模型地址。不要把模型示例地址改成容器宿主普通网络要求；字节中继不转换协议。
- mini/Trae 控制路径须填写 python_executable；Codex 控制须准备独立 feature 构建并填 controlled_executable/control_version，普通 executable 仍是公共配置必填项。构建尚未执行，见 agents/codex/control-build/README.md。
- OpenCode 的 context_limit/output_limit 有意未填写，须按实际模型填写正整数；config_root/opencode 预建为只读且仅含 .gitignore，镜像中已有 rg。缺项预检报错，不猜测限制或自动安装。
- 模型密钥由 runtime.environment 提供给 agent，勿把含密钥的实际 runtime 文件公开；verifier 不使用该表。示例未填密钥，也不验证服务访问权限。

未覆盖以上条件的模板不代表可直接运行。入口不安装、下载、拉取或构建镜像。配置未覆盖 agent timeout，沿用各任务原 timeout；如自行设置 timeout，只能缩短任务时限。

## 操作入口（示例未执行）

从 TokenAna 根目录，在获准运行后使用：

```bash
source /Users/manyi/miniconda3/etc/profile.d/conda.sh
conda activate tokenAna
python -B -m tokenAna run experiments/deepswe/run_free_python_codex.toml --runtime /absolute/runtime-codex.toml --output runs/deepswe/run_free_python_codex
python -B -m tokenAna run experiments/deepswe/turn_control_python_codex.toml --runtime /absolute/runtime-codex.toml --output runs/deepswe/turn_control_python_codex
python -B -m tokenAna analyze runs/deepswe/run_free_python_codex
python -B -m tokenAna evaluate runs/deepswe/run_free_python_codex local
```

local 默认只展示计划，显式追加 --execute 才执行原 Pier Verifier；不重新生成或收集补丁。reward 1/0 映射 solved/unsolved；-1、超时、缺失 reward 或 CTRF 报告均为异常，resolved=null。保存原 reward、CTRF、日志及检查点，恢复仅重试异常评测，不重算评分公式。

run/resume/analyze 均输出 JSON、CSV、Markdown；恢复从原 HTTP 与原生日志重建，不累加旧汇总。turn_control 在生成已经开始但调用结果未完整保存时拒绝重新生成；它只能恢复已保存结果的后续收集/评测。使用相同配置、runtime 和输出路径进行 resume，不改预算后续跑。

## 八组同集合比较

以相同 agent、模型/协议、任务筛选及环境生成下表成对运行，再调用 compare。左列为基线；用配置文件名去掉 .toml 作为示例运行目录名。

| agent | Python 34 基线 → 控制 | 非 Python 79 基线 → 控制 |
| --- | --- | --- |
| Codex | run_free_python_codex → turn_control_python_codex | run_free_multilingual_codex → turn_control_nonpython_codex |
| mini | run_free_python_mini → turn_control_python_mini | run_free_multilingual_mini → turn_control_nonpython_mini |
| Trae | run_free_python_trae → turn_control_python_trae | run_free_multilingual_trae → turn_control_nonpython_trae |
| OpenCode | run_free_python_opencode → turn_control_python_opencode | run_free_multilingual_opencode → turn_control_nonpython_opencode |

```bash
python -B -m tokenAna compare runs/deepswe/run_free_python_codex runs/deepswe/turn_control_python_codex --output comparisons/deepswe-python-codex
python -B -m tokenAna compare runs/deepswe/run_free_multilingual_codex runs/deepswe/turn_control_nonpython_codex --output comparisons/deepswe-nonpython-codex
```

compare 校验保存的 dataset/case_id/repo/base_commit 完整集合及实际纳入集合。集合、统计规则不一致，生成未结束或指标不完整时不计算变化比例，不暗中取交集或补零。原口径 run_free 与 turn_control 纳入规则不同，因此只并列展示，原口径跨规则百分比为 null；turn_control 的 original mean 始终 null。corrected 在上述可比条件都满足时提供相同规则的差值和变化比例。输出 change_pct 为（当前−基线）/基线，负值才表示减少，不自动当作解决率不变的“节省”。

## 统计和补丁约定

DeepSWE 原补丁命令是 git diff --binary BASE HEAD；agent 必须自行按 instruction 提交。原 model.patch 每调用保存，未提交修改只作诊断；没有自动 commit 或从回答文本补 patch。Trae 通过原生 base_commit 参数检查已提交差异，保留测试文件过滤和 task_done；最终提交资格仍使用退出后的公共捕获产物。控制失败/预算耗尽即使有诊断补丁也不提交。

run_free 两版本 original 保留原非空 patch、原生日志及向下取整均值，Git 提示变更版本分别为 run-free-git-v2 / run-free-multilingual-v1。turn_control original 使用 agent 原生最终摘要纳入规则，不按成功或补丁筛选，只输出 input/output/total 总和，均值 null。新增原生日志兼容口径不能称为作者历史复现。

corrected-v1 仅使用原 HTTP usage，包含可观察失败、超时、所有尝试及辅助调用；cache/reasoning 不重复相加，缺失字段不当零。Codex 原生日志缺 usage 的默认零不作为证据；压缩或采样覆盖无法佐证时原生 token 保持 null。新的 accounting_context、逐 case prompt_metadata/native_final_summary 记录实际提示版本、补丁规则及摘要来源，辅助静态审阅和离线重建。其他工作线的 overhead 分组原样保留，没有推断尚未映射的调用用途。

完整静态审查和未验证项见 docs/development-workflow.md；本目录没有实验结果。
