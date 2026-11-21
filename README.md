# .agents：通用开发约定与技能

本仓库保存与项目无关的开发准则和可复用技能：根目录的 `AGENTS.md` 是全局规则，`skills/` 下每个目录是一个技能，
以 `SKILL.md` 为入口，`references/`、`templates/`、`scripts/` 按需加载。

## 目录结构

仓库根对应工作区的 `.agents` 目录，目标结构（技能迁移后）：

```text
.agents/
  AGENTS.md                 # 全局开发准则（全局唯一入口）
  skills/<技能>/SKILL.md    # 技能入口：触发条件、流程、约束
  skills/<技能>/references/ # 经验、操作资料、口径细节
  skills/<技能>/templates/  # 可复制的模板与骨架
  skills/<技能>/scripts/    # 通用脚本（仅依赖标准库）
  docs/                     # 工作区文档（模块、设计、开发、迭代、临时）
```

`skills/` 与本仓库根目录对齐：仓库根对应 `.agents`，因此克隆到 `~/.agents` 即用户级技能，
克隆到某项目的 `.agents/` 即项目级技能。

## 安装与落位

DSH 默认扫描两个技能根：

- 项目级：`<project>/.agents/skills`
- 用户级：`~/.agents/skills`

把本仓库放到 `~/.agents`（或项目根的 `.agents/`）下即可被发现；不修改全局配置。
（迁移中：当前工作副本的技能仍在 `.agents/skills/`，移动到 `skills/` 后与本说明一致。）

## 技能一览

| 技能 | 用途 |
|---|---|
| `documentation-writing` | 模块文档结构、设计/开发文档落位与模板 |
| `complex-task-planning` | roadmap、任务依赖与实施计划 |
| `development-workflow` | 按已有 roadmap 串行派发、实现、审查、收口 |
| `frame-verification` | 最终帧逐字节验收、差异掩膜与作用域量化 |
| `graphics-debug-methodology` | 图形异常抓帧、帧内定位、归因与复验 |
| `graphics-iteration` | lookdev/材质/光照迭代的判读闭环与参数搜索 |
| `renderdoc-capture` | Windows RenderDoc 抓帧与帧内分析 |
| `gfxrecon-capture` | macOS Vulkan GFXReconstruct 抓帧与读取 |
| `win-crash-debug` | Windows 崩溃、minidump 取证与修复 |

## 约定

- 文档：工作区的 `.agents/docs/` 为总目录（本仓库根即 `.agents`，对应仓库内 `docs/`），细分见 `documentation-writing`；架构总览固定为 `docs/overview-architecture.md`。
- 临时产物：`<workspace>/temp/`，不进版本库；二进制帧、抓帧文件只留哈希 manifest。
- 验证：按改动风险分级。行为中性/回归敏感改动要求精确证据；图形效果迭代允许固定口径出图 + 指标/目检作为中间判据，允许中间态失败并记录。
- 版本控制：本地提交，远端写操作（push 等）由用户执行。

## 脚本

技能脚本用 Python 3.10+，只依赖标准库，从工作区根执行。Windows 下中文输出乱码时设置
`PYTHONUTF8=1`（或 `chcp 65001`）。可用 `python <脚本> --self-test` 自检的脚本：
`design_tokens.py`、`image_metrics.py`、`score_trial.py`、`param_search.py`。

## 新增或修改技能

- 一个技能一个目录，`SKILL.md` 只写触发条件、主流程与约束；长资料放 `references/`，一次性资料不入库。
- frontmatter 的 `name` 与目录名保持一致；描述写“何时使用”，不写实现细节。
- 脚本改动后同步 `SKILL.md` 的参数说明，并跑 `--self-test`；新增脚本复用现有 PNG 解码与指标口径。
