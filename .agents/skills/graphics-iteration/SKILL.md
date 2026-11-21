---
name: graphics-iteration
description: 图形效果迭代（lookdev、风格化着色、材质/光照/后处理标定）的判读闭环：美术规范落 design tokens、固定观察面与基线、确定性图像指标（色彩/色板/构图/画质参考）、单变量改动、闭环参数搜索、中性门与验收分级。调整 shader/材质/场景光照并需要判断画面是否达标时使用；画面异常归因用 graphics-debug-methodology，行为中性帧对拍用 acceptance-verify。
---

# 图形效果迭代

职责：把「定规范 → 改画面 → 出图 → 判读 → 定值」做成可复现的闭环，并让每轮结论可追溯。迭代对象是
shader、材质、场景光照/观察编排与后处理；引擎行为正确性、协议与构建分别归 `acceptance-verify`、
Gateway 模块文档与 `project-workflow`。先确认资产是否可改（游戏实际资产默认不重烘、不重导、
不替换），再按「规范落 token → 判读面固定 → 单变量/参数化改动 → 确定性指标 + 同口径复验」推进。

## 标准迭代循环

0. **规范落 token**：美术规范先写成 DTCG 文件（项目内 `<project>/Fixtures/lookdev/*.tokens.json`，
   骨架见 `templates/lookdev.tokens.json`），至少包含色板 `color.palette.*`、亮度窗
   `lookdev.window.<profile>.<group>.*`、权重 `lookdev.weight.*`。窗口/色板/阈值只在这里改，
   脚本不写第二份。先 `design_tokens.py lint --strict` 通过，再开始出图。
1. **定判读面**：固定工程/场景/机位/光向/分辨率/后处理口径，先跑基线落 `temp/<任务名>/`。
   有专用矩阵用 `ag_lookdev_shading_eval.py`（39 预设 + 4 向 close-up = 43 leg）；没有就按
   [出图与留档](references/02-capture.md) 用 `frame_capture.py` 采 Game/Scene 视图真实帧。
2. **一次只改一个变量**：工作点、单条材质参数、单个 shading 项分开改。参数落点用网格/二分实测
   （工作点扫 27 组、shininess 试 24/48/64），不确定的符号/公式/手性做 A/B 同口径出图；计划值不等于
   终值——过冲、不可见、几何不可达都按实测登记，不把计划值写成结论。
3. **确定性指标**：除既有 ROI luma/clip/face_lr 外，按 [确定性指标](references/07-metrics.md) 取
   色板覆盖率/ΔE2000、OKLCH 色相与彩度、saliency/主体框构图；同一 PNG 同参数必须逐位复现。
   MUSIQ/CLIP-IQA 只作参考项（pinned 权重 + 固定 ROI/缩放，报相对基线差），不作验收门。
4. **同口径复验**：同一命令、同一参数、同一 `--out` 口径重跑，逐 leg 指标 + 截图 + 哈希一起看；
   跨轮只比同脚本同参数的 `eval_metrics.json`/`trial_metrics.json`，先排除冻帧、旧二进制、资产
   未重导，再解释差异。
5. **闭环搜索（按需，明确授权）**：连续参数（曝光/调色/灯位）需要定值时，用
   [闭环参数搜索](references/08-search.md)：`lookdev_trial.py` 做单 trial 应用+出图，
   `score_trial.py` 把 token 窗口折成复合分，`param_search.py` 跑 CMA-ES（缺省，≤12 维）或 TPE
   （离散/噪声大）。搜索最优不是验收：必须在**留出 leg + 全量 43 leg** 复验，再走中性门与人工判读。
6. **中性门**：不属于本任务的区域必须可证明不变（shader 中性开关、关后处理的中性面、ROI 逐像素
   sha、行为中性帧对拍）。「画面看起来没怎么变」不是证据。
7. **归因**：把变化像素/指标落到具体通道、参数或 pass；归不了因的差异先查采集口径，不先改代码。
8. **收口**：机制写模块文档、操作与坑写本 skill 的 references、证据留 `temp/`（不入库）；删一次性
   脚本，母本不 fork；迭代结束删计划、重置 roadmap。token 变更随本轮记录版本与原因。

## 判读纪律

- **token 是唯一真源**：窗口、色板、权重、阈值都从 DTCG 读；口头目标先落 token 再判。
  token 改了就重新标定基线，旧数字只参考。`design_tokens.py lint --strict` 失败不开工。
- **lookdev 效果不以 golden 判**：效果验收 = 固定口径出图 + 人工/量化判读；golden diff 只承担
  agent 正确性、行为中性与回归拦截，两类门不混用（见 [判读与验收](references/03-evaluation.md)）。
- **工作点真源是 clip 键**：观察矩阵的机位/灯光/曝光由 `ObservationRig` 的动画 clip 驱动，只改
  `.scene` YAML 对预设出图无效；改描述 JSON 后用工具重产 clip，再 `asset.import {force:true}`。
  搜索里的灯位覆盖只对「seek 后单帧」有效，不可拿去反推 clip 设计值。
- **口径变了数字就不可比**：ROI、掩码、阈值、分辨率、`--post`、stride、色板阈值任一变化，历史
  指标只能参考；跨轮先核对二进制与导入产物新鲜度，再比同口径 JSON。
- **资产不动**：缺数据先做通道审计（贴图各通道是什么），不擅自重烘/替换游戏资产；确认是引擎/
  材质消费口径问题时改消费侧。
- **进程与工作区**：脚本自起的编辑器/工具用完即收；采后核 `git status`（编辑器可能回写
  `.mat.meta` 或回滚重导产物），非任务改动要还原。
- **搜索纪律**：一次搜索只动 spec 列出的参数；train legs 与 holdout legs 预先划分；缓存按参数
  哈希，换 token/leg 集即新搜索；最优解复验不过就登记失败，不把搜索分当验收分。

## 常用入口

```bash
# 规范先行（模板带 TODO 占位；项目落地时复制到 <project>/Fixtures/lookdev/art.tokens.json
# 并去掉 TODO，再加 --strict 作为开工门）
python .agents/skills/graphics-iteration/scripts/design_tokens.py lint \
  .agents/skills/graphics-iteration/templates/lookdev.tokens.json

# 全量判读面（43 leg：PNG + eval_metrics.json + contact sheet，落 temp/<任务名>）
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py \
  --post neutral --out temp/<任务名>_neutral
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py \
  --post scene --out temp/<任务名>_scene

# 两轮结果对比 + token 复合分（0 = 全部落窗）
python .agents/skills/graphics-iteration/scripts/compare_evals.py \
  temp/<基线目录> temp/<本轮目录> --out temp/<任务名>_compare.json
python .agents/skills/graphics-iteration/scripts/score_trial.py \
  --metrics temp/<任务名>_scene/eval_metrics.json \
  --tokens .agents/skills/graphics-iteration/templates/lookdev.tokens.json --profile day \
  --images temp/<任务名>_scene --out temp/<任务名>_score.json

# 单图确定性指标（色彩/色板/显著性/构图）
python .agents/skills/graphics-iteration/scripts/image_metrics.py \
  --image temp/<任务名>_scene/capture_front_front_1001.png \
  --tokens .agents/skills/graphics-iteration/templates/lookdev.tokens.json \
  --roi torso --stride 1 --out temp/<任务名>_front_metrics.json

# 闭环搜索（spec 见 templates/search_spec.json）
python .agents/skills/graphics-iteration/scripts/param_search.py \
  --spec temp/<任务名>/search.json --out temp/<任务名>/search

# 通用真实帧采集与行为中性对拍（契约归 acceptance-verify）
python .agents/skills/acceptance-verify/scripts/frame_capture.py --help
```

脚本从仓库根执行（macOS 用 `python3`，Windows 用 `python`）；脚本内部按平台解析
`build/bin/AternyxEditorMain(.exe)`。产物目录必须先存在（`frame_capture.py` 不自建目录），
`temp/` 已 gitignore。新脚本只依赖标准库；`image_metrics.py` 复用 eval 母本的 PNG 解码口径。

## 按需参考

| 任务 | 资料 |
|---|---|
| 设计 token / 美术规范字段、DTCG 子集、落地与 lint | [设计 token](references/06-design-tokens.md) |
| 色彩/色板/显著性/构图指标定义与画质模型接入 | [确定性指标](references/07-metrics.md) |
| 参数空间、复合分、CMA-ES/TPE、预算、留出复验 | [闭环参数搜索](references/08-search.md) |
| 默认工程/场景、观察装配、预设与机位、Gateway 命令面 | [观察面与场景编排](references/01-observation.md) |
| 离屏出图、裸帧转 PNG、改前后对比与留档 | [出图与留档](references/02-capture.md) |
| ROI 指标、亮度窗、光向判读、验收分级与基线 | [判读口径与验收](references/03-evaluation.md) |
| 冻结/剔除/bloom/材质热更/产物新鲜度等已知坑 | [已知坑](references/04-pitfalls.md) |
| 脚本母本清单与扩展纪律 | [工具清单](references/05-tools.md) |

## 约束

- 不把 `temp/` 里的一次性脚本当母本复制；缺能力就扩 `scripts/` 母本并说明参数。
- 不因 lookdev 效果改值而重采 golden；重采走 GraphicsTest 的显式例外流程。
- 不在同一轮同时改 shading 与后处理判读面；中性面与 beauty 面分开出图、分别验收。
- 本 skill 的流程与验收门槛仅在用户明确要求时调整；references 可随任务维护。
