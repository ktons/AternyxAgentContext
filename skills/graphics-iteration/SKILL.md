---
name: graphics-iteration
description: 图形效果迭代（lookdev、材质/光照/后处理标定）的判读闭环：规范落 token、固定观察面、确定性指标、单变量改动、参数搜索与验收分级；判断画面是否达标时使用。
---

# 图形效果迭代

职责：把「定规范 → 改画面 → 出图 → 判读 → 定值」做成可复现的闭环，并让每轮结论可追溯。迭代对象是
shader、材质、场景光照/观察编排与后处理；行为正确性、协议与构建按项目对应流程处理。
先确认资产可改范围，再按「规范落 token → 判读面固定 → 单变量/参数化改动 → 确定性指标 + 同口径复验」推进。

## 标准迭代循环

0. **规范落 token**：把美术规范写成 DTCG 文件（`<project>/design/lookdev/*.tokens.json` 或项目约定位置，
   骨架见 `templates/lookdev.tokens.json`），至少包含色板 `color.palette.*`、亮度窗
   `lookdev.window.<profile>.<group>.*`、权重 `lookdev.weight.*`。窗口、色板与阈值只在 token 里改，
   脚本读取同一份。先让 `design_tokens.py lint --strict` 通过，再开始出图。
1. **定判读面**：固定工程/场景/机位/光向/分辨率/后处理口径，先跑基线落 `temp/<任务名>/`。
   有专用观察矩阵时用项目采集脚本逐 leg 出图；没有时按[出图与留档](references/02-capture.md)采集真实帧。
2. **一次只改一个变量**：工作点、单条材质参数、单个 shading 项分开改。参数落点用网格/二分实测；
   不确定的符号/公式/手性做 A/B 同口径出图。计划值经实测后登记为终值。
3. **确定性指标**：除 ROI luma/clip/face_lr 外，按[确定性指标](references/07-metrics.md)取
   色板覆盖率/ΔE2000、OKLCH 色相与彩度、saliency/主体框构图；同一 PNG 同参数逐位复现。
   画质模型作为参考项（固定权重、固定 ROI/缩放，报相对基线差）。
4. **同口径复验**：用同一命令、同一参数、同一 `--out` 口径重跑，逐 leg 指标、截图与哈希一起看；
   跨轮只比同脚本同参数的 metrics JSON。先排除冻帧、旧二进制、资产未重导，再解释差异。
5. **闭环搜索（按需，明确授权）**：连续参数（曝光/调色/灯位）需要定值时使用[闭环参数搜索](references/08-search.md)：
   trial 应用参数并出图，score 把 token 窗口折成复合分，search 跑 CMA-ES（连续、低维）或 TPE（离散、噪声大）。
   搜索最优经留出 leg + 全量复验后，再走中性门与人工判读。
6. **中性门**：把不属于本任务的区域证明为不变——shader 中性开关、关后处理的中性面、ROI 逐像素哈希、
   行为中性帧对拍。逐字节帧对拍用 [frame-verification](../frame-verification/SKILL.md)。
   “画面看起来没怎么变”作为观察记录；证据使用哈希、逐像素对比或状态读数。
7. **归因**：把变化像素/指标落到具体通道、参数或 pass；差异先查采集口径，再改代码。
8. **收口**：机制写模块文档，操作与坑写本 skill 的 references，证据留 `temp/`；一次性脚本按临时产物清理，
   扩展能力时改 `scripts/` 母本并说明参数；本期计划与 roadmap 按 `documentation-writing` 的临时文档规则清理。
   token 变更随本轮记录版本与原因。

## 判读纪律

- **token 是唯一真源**：窗口、色板、权重、阈值都从 DTCG 读；口头目标先落 token 再判。
  token 改了就重新标定基线，旧数字只作参考。`design_tokens.py lint --strict` 通过后开工。
- **lookdev 效果按固定口径出图 + 人工/量化判读**；golden diff 用于 agent 正确性、行为中性与回归拦截，
  两类门分开记录（见[判读与验收](references/03-evaluation.md)）。
- **工作点真源是 clip 键**：观察矩阵的机位/灯光/曝光由动画 clip 驱动，只改场景描述文件对预设出图无效；
  改描述 JSON 后重产 clip，再走导入。搜索里的灯位覆盖只对“seek 后单帧”有效，clip 设计值通过描述 JSON 维护。
- **口径变了数字就不可比**：ROI、掩码、阈值、分辨率、`--post`、stride、色板阈值任一变化，历史指标只作参考；
  跨轮先核对二进制与导入产物新鲜度，再比同口径 JSON。
- **资产按项目约定修改**：缺数据先做通道审计（贴图各通道是什么）；确认是引擎/材质消费口径问题时改消费侧。
- **进程与工作区**：脚本自起的编辑器/工具用完即收；采后核 `git status`，编辑器回写的 `.meta` 或重导产物按需还原。
- **搜索纪律**：一次搜索只动 spec 列出的参数；train/holdout legs 预先划分；缓存按参数哈希，换 token/leg 集即新搜索；
  最优解复验不过就登记失败，搜索分与验收分分开记录。

## 常用入口

```bash
# 规范先行（模板带 TODO 占位；项目落地时复制到项目 token 路径并去掉 TODO，再加 --strict 作为开工门）
python <skill>/scripts/design_tokens.py lint <tokens.json> --strict

# 两轮结果对比 + token 复合分（0 = 全部落窗）
python <skill>/scripts/compare_evals.py \
  temp/<基线目录> temp/<本轮目录> --out temp/<任务名>_compare.json
python <skill>/scripts/score_trial.py \
  --metrics temp/<任务名>_scene/eval_metrics.json --tokens <tokens.json> --profile day \
  --images temp/<任务名>_scene --out temp/<任务名>_score.json

# 单图确定性指标（色彩/色板/显著性/构图）
python <skill>/scripts/image_metrics.py \
  --image temp/<任务名>/capture_front.png --tokens <tokens.json> \
  --roi torso --stride 1 --out temp/<任务名>_front_metrics.json

# 闭环搜索（spec 见 templates/search_spec.json）
python <skill>/scripts/param_search.py \
  --spec temp/<任务名>/search.json --out temp/<任务名>/search
```

脚本从工作区根执行（macOS 用 `python3`，Windows 用 `python`）；产物目录先创建；`temp/` 是工作区临时目录，不入库。
新脚本依赖标准库，并复用现有 PNG 解码与指标口径。

## 按需参考

| 任务 | 资料 |
|---|---|
| 设计 token / 美术规范字段、DTCG 子集、落地与 lint | [设计 token](references/06-design-tokens.md) |
| 色彩/色板/显著性/构图指标定义与画质模型接入 | [确定性指标](references/07-metrics.md) |
| 参数空间、复合分、CMA-ES/TPE、预算、留出复验 | [闭环参数搜索](references/08-search.md) |
| 观察面组成、rig 装配、光向矩阵与相机编排 | [观察面与场景编排](references/01-observation.md) |
| 离屏出图、裸帧转 PNG、改前后对比与留档 | [出图与留档](references/02-capture.md) |
| ROI 指标、亮度窗、光向判读、验收分级与基线 | [判读口径与验收](references/03-evaluation.md) |
| 冻结/剔除/bloom/材质热更/产物新鲜度等已知坑 | [已知坑](references/04-pitfalls.md) |
| 脚本母本清单与扩展纪律 | [工具清单](references/05-tools.md) |

## 约束

- 扩展能力时改 `scripts/` 母本并补参数说明；一次性脚本留在 `temp/` 并按临时产物处理。
- lookdev 效果改值后，按项目显式流程重采基线。
- 同一轮把 shading 与后处理判读面分开出图、分别验收。
- 本 skill 的流程与验收门槛在用户明确要求时调整；references 随任务维护。
