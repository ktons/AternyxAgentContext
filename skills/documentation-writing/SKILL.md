---
name: documentation-writing
description: 维护架构总览与模块文档，或需要确定文档落位、文件名、生命周期与章节写法时使用；写设计文档、开发文档或 roadmap 分别用 design-document、dev-document、roadmap-planning，落位与写法按本 skill。
---

# 文档体系与写法

本 skill 是文档体系的唯一约定：类型、文件名、落位、生命周期、通用写法与模板。
临时文档的流程与内容由各自 skill 负责，本 skill 不替它们决定内容。

## 文档体系

文档总目录为 `.agents/docs/`，分两类：入库的长期文档，与统一放在 `.agents/docs/temp/` 的临时文档。

### 入库文档

| 类型 | 内容 | 文件名 | 位置 | 生命周期 | 模板 | 产出 skill |
|---|---|---|---|---|---|---|
| 架构总览 | 模块清单、分层依赖与跨层规则；文档入口 | `overview-architecture.md` | `.agents/docs/` | 永久，随代码同步 | [架构总览](templates/architecture-overview.md) | 本 skill |
| 模块文档 | 模块当前事实 | `module-<模块>.md`；拆分后为 `<模块>/overview.md` 加主题文档 | `.agents/docs/modules/` | 永久，随代码同步 | [模块入口](templates/module-overview.md)、[主题文档](templates/module-topic.md) | 本 skill |

### 临时文档

统一放在 `.agents/docs/temp/`，都不入库。

| 类型 | 内容 | 文件名 | 生命周期 | 模板 | 产出 skill |
|---|---|---|---|---|---|
| 设计文档 | 方案调研、候选与推荐 | `design-<主题>.md` | 相关工作收口删除 | [设计文档](templates/design-doc.md) | `design-document` |
| 开发文档 | 改动落点、实施步骤与验收 | `dev-<主题>.md` | 任务收口删除 | [开发文档](templates/dev-doc.md) | `dev-document` 或 `roadmap-planning` |
| roadmap | 当前迭代执行表 | `roadmap.md` | 收口重置为空模板 | [roadmap](templates/roadmap-template.md) | `roadmap-planning` |
| 迭代文档 | 目标与每阶段验收 | `iteration-<主题>.md` | 收口删除 | [迭代文档](templates/iteration-doc.md) | `roadmap-planning` |
| 过程记录 | 排查、交接、讨论 | `debug-<主题>.md`、`handoff-<主题>.md`、`discuss-<主题>.md` | 任务收口清理 | 无固定模板 | 各 skill 按需 |

- 临时文档统一命名 `<用途>-<主题>.md`，`roadmap.md` 是唯一例外；同一工作的各类文档用同一主题名，按前缀即可聚齐。
- 只有入库文档进 Git；项目 `.gitignore` 忽略 `.agents/docs/temp/`。
- 收口时先把结论沉淀到模块文档与架构总览，再删除对应临时文档；未沉淀就删除视为丢失结论。
- `.agents/docs/temp/` 放文档形态的过程记录；抓帧、日志、截图、一次性脚本等运行产物放 `<workspace>/temp/`。
- 用户指定路径时按用户路径落位，其余约定不变。

需要产出某类文档时，按类型表「产出 skill」列选择；执行已规划的 roadmap 用 `development-workflow`。
本 skill 只维护落位与写法，不规定各 skill 的流程与内容。

## 通用写法

- 叙述完整：给足背景、动机与推导，关键内容用完整段落陈述，读者单独阅读即可跟上。
- 信息充实：覆盖从零理解所需的背景、术语、依赖与示例，按理解顺序组织。
- 表达自然：以通顺陈述为主，列表与表格只用于并列信息，保留必要的细节与特例。
- 落到代码：涉及实现时给 `path:line` 与符号名，注明代码快照；行号漂移后以代码为准。
- 自包含：设计文档与开发文档各自独立成篇、互不引用；迭代文档与 roadmap 合起来自包含，不引用设计文档与开发文档。
  临时文档只以架构总览与模块文档为事实依据，不要求读者跳转到另一份临时文档。
- 面向人的文档按上述要求写；给 agent 执行的迭代文档与 roadmap 以可执行、可判定为先，少叙述、多命令与判据。

## 架构总览

架构总览是文档入口与模块文档索引；结构、导航与维护约定见[架构总览模板](templates/architecture-overview.md)。

## 模块文档

模块文档维护模块当前事实；只保留当前仓库的唯一事实，历史与日期留版本控制。
小模块用 `.agents/docs/modules/module-<模块>.md`；大模块或文档达到 500 行时拆为
`.agents/docs/modules/<模块>/overview.md` 与同目录主题文档。
六章结构、每章写法与主题文档索引见[模块入口模板](templates/module-overview.md)、[主题文档模板](templates/module-topic.md)。
