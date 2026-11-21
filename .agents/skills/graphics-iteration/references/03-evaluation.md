# 判读口径与验收

本册给「看什么数字、怎么判达标、什么不算门」。窗口数值是当前 lookdev 编排下的**标定目标**，不是
跨场景通用阈值；换场景/换编排/换 post 口径后先重新标定，达不到就登记偏差，不改判据凑绿。

> 窗口/权重/色板阈值的**单一真源是 design tokens**（`lookdev.window.*` / `lookdev.weight.*`，
> 见 [设计 token](06-design-tokens.md)）。本册记录这些值的来源、标定过程与例外条款；token 与
> 本册冲突时以 token 为准，并同步本册。复合分（`score_trial.py`）只是把本册窗口折成搜索目标，
> 不新增艺术阈值。

## 一、ROI 与指标定义（ag_lookdev 母本口径）

- **躯干 ROI**：`x 640..1280, y 290..580 @1920×1080`（等比缩放）；天空掩码
  `b-r>0.06·255 && b>0.70·255 && r>0.47·255` 且排除 `luma≤0.05`。输出 p15/p50/p98 luma、
  `p98/p15` 行内比、暗部 RGB 均值与色序（`R>G>B` 计数）、clip 比例（任一通道 ≥250）。
- **face ROI**：`x 820..1100, y 330..620`；`shadow_coverage` = ROI 内 luma<0.7 占比（排除天空与
  近黑）；`face_lr` = left45 vs right45 同 ROI 逐像素通道最大差的 mean 与 >8 占比（不排除天空）。
- **close-up**：全帧非天空掩码 p15/p50/p98 + 中心 40% 区域 luma<0.7 占比 + 主体包围盒高度占比。
- **luma** = `0.2126r + 0.7152g + 0.0722b`（sRGB 值域 0..1）；所有分位按 ROI 内有效像素统计。
- 判读前先确认 `eval_metrics.json` 的 `metric_definitions` 与当前脚本一致；口径变了的旧数字只能参考。

**贴图通道审计**（缺数据/怀疑通道语义时先做，不重烘）：

- 逐通道统计（p50/std/通道间相关性）+ 单通道在屏探针出图（`ag_probe` 类材质），确认每个通道在模型上
  是什么；不能靠缩略图或旧注释判断。
- 教训：normal 曾按「R 恒 255、G/B 重复」判成不可用，漏掉 **A=Y** 的第二通道，导致本轮之前完全没采
  法线；实际打包是 `R=Z、G=X、B≈G 副本、A=Y`，且导入必须保持 RGBA/BC7（设 `textureKind=normal` 走
  BC5 会把 A 丢掉）。细节见 `docs/modules/module-mesh-asset.md`。
- 审计结论写成通道表（通道/极值/分布/消费点/是否可用），再接 shading；判为无可用通道就回退关闭，
  不补烘替代图。

## 二、亮度窗

**日景窗（写实落点，角色渲染迭代拍板）**

| 指标 | 窗口 |
|---|---|
| 亮部 p98 luma | `[0.82, 0.93]`（tonemap+sRGB，不糊白、保结构） |
| 暗部 p15 luma | `[0.18, 0.35]`，暖色序 `R>G>B` |
| 行内亮暗比 p98/p15 | `[2.5, 4.0]` |

例外与限制：

- **face/skinback 两行行内比不作门**：窄坡 SDF 是 12px 量级≈二值形，背光暗部种群=掩码形本体，
  行内比被暗部色深钉在 4.5–4.7；压到 4.0 只能冲淡暗部或压亮部（出窗）。两行按方向性判据 +
  亮暗各自落点窗验收。
- **p15 窗与 LUT 内禀对比度冲突时登记偏差**：ag 白布 `body01_Ramp` 布料带 u=0≈(183,195,204) sRGB，
  LUT 内禀亮暗比≈1.8、ACES 后 sRGB 比 1.1–1.5，p98 窗与该 LUT 下的 p15 窗互斥（ag_lookdev T2
  实测，D8 修订）。此时以 p98 落窗 + 对比度提升 + 暗部色相为准，并登记「不可达」原因。
- **face L/R >15% 在 ag_face 单材质下物理不可达**（ag_face 仅占 face ROI 8.8%，半脸影把覆盖上限
  压到 ~0.10）；`shadow_coverage` 会把 4px 描边暗线算进去，T7 后处理标定后与旧值不同量纲——
  单项覆盖数字不作门，按方向性判据（左右换边、正面基本全亮、背面全影）+ 亮暗窗判。

**夜景窗（vc_lookdev 矩阵专用，V10 判读拍板）**

| 指标 | 窗口 |
|---|---|
| 亮部 p98 luma | `[0.72, 0.93]`（下限放宽：夜景主秀光背位落 ACES 肩部更低） |
| 暗部 p15 luma | `[0.05, 0.20]`（黑衣/夜发 × 低环境光） |
| 行内比 | 不作门，只观测（暗部地板下移后派生上界失去判别力） |

暗部结构用色序辅助：skin 行保持 `R>B` 暖序，stockings 行微冷是织物设计色。face/skinback 例外条款
继续适用。

## 三、光向与方向性判读

- **同行互镜像**：同机位 left/right 两张的左右半区亮度比应互镜像且方向相反；不镜像说明灯位或
  相机键摆错。
- **行内趋势**：正面机位行 front 最亮、back 最暗（逆光入影）；背面机位行 back 最亮（背部受光）；
  背面机位看武器时「back」才是照到可见面的灯（机位几何，不是缺陷）。
- **换边**：左右光向的阴影应换到相反半脸/半身，而不是两边同时变暗。
- 掩码/半区统计要说明是否排除天空与近黑；不同掩码口径的数字不能直接比。

## 四、中性门

改动只应影响目标区域，其余区域必须可证明不变。按改动面选门：

| 改动面 | 中性门 |
|---|---|
| 新增材质槽/参数 | 参数置中性值（如 `gNormalScale=0`、`gAoParams=0`）时与改前逐 leg sha 相同 |
| shader 新分支 | 关闭分支的专项材质与基线 ROI 逐像素 sha 相同 |
| 后处理/描边/地面 | 用 `--post neutral`（关 bloom/outline）判 shading，`--post scene` 单独判 beauty；角色像素 ROI sha 不变 |
| 引擎 pass/绑定变更 | `Lookdev`/`vc_lookdev` 稳态帧、DebugId 帧逐字节一致；`frame_diff.py` 全 IDENTICAL |
| 全量行为中性 | 改前/改后同参数 `frame_capture.py` + `frame_diff.py` 逐 seq IDENTICAL |

- 逐字节相同用 `sha256`（解码后裸像素 / ROI 裁剪字节），不用 PNG 文件字节（zlib 版本会变）。
- 「只加槽位不加代码」也要跑中性门：FMA 收缩可能带来 1/255 的 1–4 px 差，如实记录并解释。
- 中性门不过先查：二进制是否重建、资产是否重导、是否采到冻帧、两轮是否同 `--post`。

## 五、验收分级（lookdev 效果 vs agent 正确性）

- **lookdev 效果验收 = 人工出图/目检 + 量化落点**：固定场景、机位、光照条件、分辨率，按轮廓/
  亮暗窗/材质细节/接触影判读；**golden diff / 帧哈希不作为效果通过条件**。
- **复合分不是效果门**：`score_trial.py` 的 `score=0` 只说明「token 窗口内」，不说明色板/构图/
  接触影好看；搜索最优也不直接验收（见 [闭环参数搜索](08-search.md)）。复合分用于排序/搜索/
  回归报警，最终仍由人判。
- **golden diff / 帧读回基线只用于 agent 正确性验证**：行为中性、回归拦截、跨实例稳定性；不得写成
  lookdev 效果门，也不因 lookdev 效果调整而要求重采。
- 资产定位：`Test/Projects/General/Tests/cases/frame_diff_kunino_baseline.json` 与
  `Test/golden/framereadback/ag_kunino_baseline.json` 是 agent 正确性回归资产，其平台/重采/材质
  替换问题按该线单独处理，不阻塞效果评审。新增/保留的 lookdev golden 必须在 case/manifest 描述中
  标「agent 正确性」。
- 效果证据走 `temp/` PNG/JSON + 人工目检，不入库 golden。

## 六、基线接管与重采

- 图形域定帧基线由 `Test/Projects/GraphicsTest`（ctest `Graphics_<场景>`）接管：feature 判别场景 ×
  集中基线库（manifest + PNG 双入库，判定权威 = 解码后裸像素 sha256）；工程定位、场景矩阵与
  重采政策全文见该工程 README。

  | 旧基线（`Test/golden/framereadback/`） | 题材 | GraphicsTest 接管 |
  |---|---|---|
  | `t10_lights_baseline.json` | 多光源叠加 | `02_lights` |
  | `t10_normalmap_baseline.json` | 法线贴图 | `04_normalmap` |
  | `t10_transparency_baseline.json` | 半透明排序混合 | `05_transparency` |
  | `t16_material_defaults_baseline.json` | 材质缺省值判别 | `06_pbr` |
  | skin_solo / t17 | 蒙皮塌缩判别 | `09_skinning`（含 mac 首采基线） |
  | `t16_lookdev_baseline.json` | lookdev 风格光照定标 | **不收编**（风格定标 ≠ 验证工程，归 General/lookdev 自域） |
  | `t10_stage1_baseline.json` | T10 行为中性底座 | 无逐场景接管（历史底座封存） |
  | t6.13_animation + t7.x 五份 | UI 域帧基线 | 不属图形域 |
  | —（自建） | 旗舰整帧对拍 | `10_all_features`（判别归因回单 feature 场景） |

- 旧 manifest（`<主题>_baseline.json`，`baselines[]` 嵌套，二进制帧不入库）与新基线（顶层
  `frames` + PNG 双入库）两代并存、各有权威消费方，不互相迁移、不互读；图形域新增一律走
  GraphicsTest 口径。
- **重采 = 显式例外流程**：`--recapture`/`--ingest` 两步分离 + `--reason` 凭据，报告与变更说明先
  评审，再连同 manifest + 新 PNG 一并提交；默认没有重采概念，首采 ≠ 重采。跨工程重采需要明确
  授权，不能顺手带上。

## 七、单轮判读 checklist

1. 基线目录存在且与当前二进制/资产同版本；`--post` 与分辨率记录在 `eval_metrics.json`；
   token 文件 `design_tokens.py lint` 通过且与基线同版本。
2. 本轮只改了一个变量；A/B 的参数与落点写进轮次记录（计划值 vs 实测值分开）。搜索轮另记
   spec/seed/budget 与 train/holdout 划分。
3. `compare_evals.py` 看：缺 leg、clip 非零、p98 超限、最差 Δp98；再看 contact sheet 与关键特写。
   `score_trial.py` 看：`score`、`hard_failures`、罚分落在哪些 leg/指标。
4. 中性门：目标区域外逐字节相同，或差异逐项归因（FMA/描边/地面等）。
5. 不可达目标登记原因与替代判据，不删窗、不改断言。
6. 收口：机制进模块文档、操作进本 skill、证据留 `temp/`、一次性脚本删除。
