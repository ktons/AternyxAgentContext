---
name: documentation-writing
description: 维护架构总览与模块文档，或需要确定文档落位、文件名、生命周期与章节写法时使用；写设计文档、开发文档或 roadmap 分别用 design-document、dev-document、roadmap-planning，落位与写法按本 skill。
---

# 文档体系与写法

本 skill 是文档体系的唯一约定：类型、文件名、落位、生命周期、通用写法与模板。
一次性文档的流程与必含项由各自 skill 负责，本 skill 不替它们决定内容。

## 文档体系

文档总目录为 `.agents/docs/`；架构总览固定为 `.agents/docs/overview-architecture.md`，是文档入口。

| 类型 | 内容 | 文件名 | 位置 | 生命周期 | 模板 | 产出 skill |
|---|---|---|---|---|---|---|
| 架构总览 | 模块清单、分层依赖与跨层规则；文档入口 | `overview-architecture.md` | `.agents/docs/` | 永久，入库 | [架构总览](templates/architecture-overview.md) | 本 skill |
| 模块文档 | 模块当前事实 | `module-<模块>.md`；拆分后为 `<模块>/overview.md` 加主题文档 | `.agents/docs/modules/` | 永久，随代码同步，入库 | [模块入口](templates/module-overview.md)、[主题文档](templates/module-topic.md) | 本 skill |
| 设计文档 | 方案调研、候选与推荐 | `<主题>.md` | `.agents/docs/design/` | 一次性，不入库；方案定稿后保留到相关工作收口，收口删除 | [设计文档](templates/design-doc.md) | `design-document` |
| 开发文档 | 改动落点、实施步骤与验收 | `<主题>.md` | `.agents/docs/dev/` | 一次性，不入库；任务收口删除 | [开发文档](templates/dev-doc.md) | `dev-document` 或 `roadmap-planning` |
| roadmap | 当前迭代执行表 | `roadmap.md` | `.agents/docs/iteration/` | 迭代期有效，不入库；收口重置为空模板 | [roadmap](templates/roadmap-template.md) | `roadmap-planning` |
| 迭代文档 | 目标与每阶段验收 | `<主题>.md` | `.agents/docs/iteration/` | 一次性，不入库；收口删除 | [迭代文档](templates/iteration-doc.md) | `roadmap-planning` |
| 临时记录 | 排查、交接、讨论记录 | `<用途>-<主题>.md`（`debug-`、`handoff-`、`discuss-`） | `.agents/docs/temp/` | 临时，不入库；任务收口清理 | 无固定模板 | 各 skill 按需 |

只有架构总览与模块文档入库，其余目录在项目 `.gitignore` 中忽略：

```gitignore
.agents/docs/design/
.agents/docs/dev/
.agents/docs/iteration/
.agents/docs/temp/
```

- 收口时先把结论沉淀到模块文档与架构总览，再删除对应的一次性文档；未沉淀就删除视为丢失结论。
- 文档形态的临时记录放 `.agents/docs/temp/`；抓帧、日志、截图等运行产物放 `<workspace>/temp/`。
- 用户指定路径时按用户路径落位，其余约定不变。

## 选型

| 用户要做的事 | 用哪个 skill |
|---|---|
| 讨论或比较技术方案、做选型、写设计文档 | `design-document` |
| 说明代码怎么改、定开发计划与验收、写开发文档 | `dev-document` |
| 规划多阶段任务的执行流程、整理 roadmap 与任务依赖 | `roadmap-planning` |
| 执行已有 roadmap：派发、实现、审查、验证、收口 | `development-workflow` |
| 维护架构总览与模块文档、整理文档结构 | 本 skill |

## 通用写法

- 叙述完整：给足背景、动机与推导，关键内容用完整段落陈述，读者单独阅读即可跟上。
- 信息充实：覆盖从零理解所需的背景、术语、依赖与示例，按理解顺序组织。
- 表达自然：以通顺陈述为主，列表与表格只用于并列信息，保留必要的细节与特例。
- 落到代码：涉及实现时给 `path:line` 与符号名，注明代码快照；行号漂移后以代码为准。
- 自包含：设计文档与开发文档各自独立成篇、互不引用；迭代文档与 roadmap 合起来自包含，不引用设计文档与开发文档。
  一次性文档只以架构总览与模块文档为事实依据，不要求读者跳转到另一份一次性文档。
- 面向人的文档按上述要求写；给 agent 执行的迭代文档与 roadmap 以可执行、可判定为先，少叙述、多命令与判据。

## 架构总览

维护模块清单、分层与依赖方向、跨层规则与文档导航；它是模块文档的索引，新增、拆分或下线模块时同步更新，
模块文档入口变更时更新清单。只保留当前事实，写法见[架构总览模板](templates/architecture-overview.md)。

## 模块文档

模块文档维护模块的定位、边界、契约、关键入口与当前事实；深层机制和长示例下沉到同目录主题文档。
编写或更新前先从架构总览定位所属模块，再读现有模块文档，并用必要源码核对当前事实。
小模块用 `.agents/docs/modules/module-<模块>.md`；大模块或文档达到 500 行时拆为
`.agents/docs/modules/<模块>/overview.md` 与同目录主题文档，overview 是唯一对外入口并索引各主题文档。
模块文档只保留当前仓库的唯一事实，历史状态与日期留版本控制。

模块入口沿用六章，信息不足的章节简述现状：

```text
一、定位与边界
二、设计与机制
三、迭代操作
四、关键约束
五、环境坑
六、已知缺陷与方向
```

写法要点：

- **定位与边界**：职责、目标目录与依赖边；同层直接依赖/被依赖各一行链接，跨层关系指向架构总览。
- **设计与机制**：当前设计摘要、API 契约与主题索引。
- **迭代操作**：构建、运行、验证等常用操作与命令。
- **关键约束**：不变量、契约前置条件、线程与生命周期规则、容量与性能边界；每条写清内容、依据与违反后果。
- **环境坑**：设备环境带来的适用条件与处置，覆盖平台、驱动、编译器等；能通过流程规避的不要写进来。
- **已知缺陷与方向**：真实且未解决的缺陷、当前不支持的能力与远期方向。
