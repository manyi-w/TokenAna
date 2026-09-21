# 依赖与环境分层

根目录 `requirements.txt` 有意不列第三方包：正式入口的宿主编排、查看和离线汇总使用 Python 标准库。Python 3.12.14 与 Docker CLI/daemon 是外部前提，不能通过 pip 安装本项目就绪。

| 层级 | 依赖来源与准备方式 |
| --- | --- |
| 宿主 | `environment.yml` 声明基础 Conda 环境；在已有 tokenAna 环境运行，无需安装本项目 |
| 控制器 | `controller.txt` 对应 `src/pilot_images.py` 当前直接依赖；正式运行在 Docker 构建中安装并预热 tiktoken |
| mini / Trae | 各自 `agents/<agent>/upstream/pyproject.toml`；构建器在独立 `/opt/tokenana` 环境安装所选源码包 |
| DeepSWE verifier | `datasets/deepswe/upstream/pier/pyproject.toml`；独立 verifier 工具环境 |
| Codex | `agents/codex/controlled/` 的 Cargo 清单与锁文件；镜像中编译带 TokenAna hook 的独立版本 |
| OpenCode | `agents/opencode/upstream/` 的 Bun 清单与锁文件；镜像中构建 |
| 任务仓库 | 原任务镜像与原环境，不混入宿主 pip 依赖 |
| 离线图表 | `analysis.txt`；在独立分析环境准备 matplotlib，核心统计不依赖它 |
| GPU / 其他方法 | 专用环境和服务配置，参见 `experiments/method_matrix/README.md`；不包含在默认正式入口 |

不要把所有 upstream 的 requirements 合并安装到宿主；原 agent、评测器的环境各自隔离。`controller.txt` 是当前构建依赖的说明清单，构建器目前仍内嵌安装列表，不会自动读取这个文件。调整文件不会改变现有镜像或运行行为。

当前构建没有统一 Python 依赖锁；不要把未固定版本的清单视作完整可复现环境。Python 工具镜像保存 `/opt/tokenana/installed-packages.txt`，每个正式运行保存 `images.json` 的镜像 ID。恢复需要保留对应本地镜像；若依赖解析或构建失败，应先保存构建日志再处理，不自动升级依赖。

若在宿主使用通用 CLI 直接执行 AgentDiet/AttnCompress，所选方法会检查 tiktoken 等额外条件；根 requirements 不承诺支持这一执行模式。缺项应按该组件独立准备，不改变宿主启动器的最小依赖约定。
