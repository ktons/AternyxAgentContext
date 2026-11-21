# 确定性图像指标

判读使用**确定性指标**：同一张 PNG、同一组参数，换机器或换时间得到同一组数字；口径（ROI/掩码/stride/
阈值/分辨率）写进输出，跨轮只比同口径。工具：`scripts/image_metrics.py`；评分聚合：`scripts/score_trial.py`；
场景与 ROI 的采集口径由项目的评估入口提供。

## 一、输入与解码纪律

- 输入 **8bit、非隔行 PNG**（RGB/RGBA）；裸帧先走无损转换，截屏与 JPEG 中转会改色。
- 像素按 **sRGB 编码值**消费：luma 在 sRGB 值域计算，色彩科学先转线性再转 OKLab/XYZ；
  两套值域分开使用。
- 依赖标准库的实现按标准公式自算色彩科学（sRGB→线性→OKLab/XYZ、ΔE2000）。
  项目已有指标管线时，替换实现后用 `--self-test` 的 ΔE2000 样对与合成图用例对齐，并保持
  `metrics_version` 与口径字段一致；同一轮对比使用一套实现。
- `--stride` 是口径的一部分：默认 2；逐像素判小结构用 1；全帧构图统计可用 4。
  跨轮 stride 不同时只作参考。
- 掩码三选一：`nonsky`（非天空且 luma>0.05）、`subject`（仅 luma>0.05）、`all`（全 ROI）。
  夜景深蓝天空用 `nonsky` 时确认排除逻辑；close-up 无天空时三种掩码一致。
- 有效像素为 0 时所有指标输出 `null`，报告里保留该状态。

## 二、tone

| 指标 | 定义 |
|---|---|
| `luma.p15/p50/p98` | 有效像素 luma 的线性插值分位（q=15/50/98） |
| `luma.p98_over_p15` | 行内亮暗比；p15=0 时为 null |
| `clip_frac` | 任一通道 ≥250 的像素占有效像素比 |
| `dark.rgb` / `dark.order` | luma≤p15 像素的 RGB 均值与 `R>G>B` 判定 |
| `shadow_coverage` | face ROI 内 luma<0.7 占比（排除天空与近黑） |

亮度窗与例外条款（face 行内比、LUT 内禀对比度冲突等）见[判读与验收](03-evaluation.md)；
窗值从 design tokens 的 `lookdev.window.*` 读取。

## 三、色彩科学

### 3.1 sRGB → 线性

```
c_lin = c/12.92                              , c <= 0.04045
c_lin = ((c+0.055)/1.055)^2.4                , c >  0.04045
```

### 3.2 OKLab / OKLCH

线性 RGB → LMS → 立方根 → OKLab（Björn Ottosson 2020）：

```
l = 0.4122214708 r + 0.5363325363 g + 0.0514459929 b
m = 0.2119034982 r + 0.6806995451 g + 0.1073969566 b
s = 0.0883024619 r + 0.2817188376 g + 0.6299787005 b
L = 0.2104542553 l' + 0.7936177850 m' - 0.0040720468 s'
a = 1.9779984951 l' - 2.4285922050 m' + 0.4505937099 s'
b = 0.0259040371 l' + 0.7827717662 m' - 0.8086757660 s'
```

`L` ∈ [0,1]；`C = hypot(a,b)`；`H = atan2(b,a)` ∈ [0,360)。OKLCH 感知均匀、色相稳定，
适合看“皮肤是否偏青/偏红”“暗部是否落紫”。

**色相平均用圆均值**：直接平均 350° 与 10° 会得到 180°。`image_metrics.py` 输出
`color.oklch_mean` 时 H 用 `atan2(Σsin, Σcos)`；`oklch_p50` 的分位 H 是逐通道排序值，只作粗看，
判断色相家族用 `dominant_colors`。

### 3.3 CIELAB 与 ΔE2000

线性 RGB → XYZ（D65）→ Lab（D65 白点 `0.95047, 1.0, 1.08883`）；ΔE2000 按 Sharma 2005 实现
（kL=kC=kH=1，`image_metrics.py --self-test` 用标准样对校验）。

参考尺：ΔE2000 ≈1 JND、2–3 可察觉、5 明显、10 不同颜色家族。阈值是“命中/未命中”的工程口径；
项目可在 tokens 收紧到 5–8。

### 3.4 色板指标

- **coverage**：有效像素中最近 token 的 ΔE2000 ≤ `lookdev.paletteThreshold` 的占比。
- **mean/p90/p99 ΔE**：所有有效像素到最近 token 的距离分位；p90 高说明有一撮颜色游离在色板外
  （常见于高光、天空反射、LUT 色偏）。
- **per-token coverage**：每个 token 命中的像素占比；持续 0 时先查 token 与材质/灯光。
- **dominant_colors**：OKLab 固定 8×8×8 桶 + 桶内 RGB 均值，按占比排序；用固定桶统计保证确定性。
  用于对照“画面实际主色家族”与色板。

**限制**：统计在 tonemap+LUT 之后的成片 sRGB 上，不是 albedo；暖 LUT 会把整幅色相往暖推，
色板覆盖率下降可能来自 LUT。判材质颜色用中性面，判成片色板用成片面。

## 四、显著性（saliency）

`image_metrics.py` 用谱残差（Hou & Zhang 2007）的确定性实现：

1. 灰度图按 `stride` 采样，块平均到 ≤64×64；
2. 2D FFT → 幅度谱取 log，3×3 均值平滑，残差 = logA − smooth(logA)；
3. 残差 + 原相位 → IFFT → |·|² → 3×3 模糊 → 归一化到 [0,1]；
4. 阈值 = mean + 1σ；取“总显著质量最大”的 4 邻域连通块作为显著框（压住边框伪影）；
5. 双线性回放到输入尺寸；构图统计用回放前的网格坐标（归一化后等价）。

用途与边界：

- 用于**构图**（主体在哪、占多大、是否压边/顶天）；分割交给专用工具或掩码流程，细发丝/武器边缘会被平滑。
- 纯色/渐变背景上谱残差仍有边框伪影；全平画面会退化为无显著框（`bbox: null`），此时看 `subject_bbox`。
- 显著性对分辨率/降采样敏感：跨轮保持同 `stride`、同 `SALIENCY_SIZE`（当前 64）。

## 五、构图指标

`composition(bbox, width, height)` 对主体框或显著框输出：

| 指标 | 公式 | 用途 |
|---|---|---|
| `width_frac` / `height_frac` | 框宽/高 ÷ 画面宽/高 | 主体占画幅；特写看高度占比 |
| `area_frac` | 框面积 ÷ 画面面积 | 主体量感（框是轴的，不是像素掩码） |
| `center` | 框中心归一化坐标 | 构图中心 |
| `center_offset` | `2·hypot(cx−0.5, cy−0.5)` | 0=正中，1=角 |
| `thirds_distance` | 中心到最近三分点（1/3,2/3 交点）的归一化距离 | 三分法靠近度 |
| `headroom_frac` | 框顶边 y ÷ 画面高 | 头顶留白 |
| `bottom/left/right_margin_frac` | 对应边距 ÷ 画面宽/高 | 压边检查 |

**按 leg 选框**：

- close-up / 特写：`subject_bbox`（非天空主体）→ 高度占比、中心偏移、头顶留白；
- 全身矩阵：`saliency_composition` → 主体质量中心/显著框；`subject_bbox` 会被地面/背景撑满，构图用显著框。
- 构图窗（`subjectHeightMin` 等）先观测基线分布，再把窗写进 token；三分法先作为观察项。

## 六、画质模型（MUSIQ / CLIP-IQA）

两类都是**无参考**画质/美学倾向模型，输出不是“正确性”：

- MUSIQ（Multi-scale Image Quality Transformer）：在 KonIQ/SPAQ 等数据集训练，输出约 0–100 的质量分；
  对分辨率/压缩/细节敏感。
- CLIP-IQA：用 CLIP 图文相似度做 “good/bad photo” 等提示词打分，输出 0–1；对提示词与裁剪敏感。

接入协议（参考项）：

1. **固定模型与权重**：记录模型名、revision/权重哈希、预处理（短边 resize、中心裁剪/ROI）；
2. **固定输入**：使用同一 leg 的同一 PNG；contact sheet/缩略图用于浏览；ROI 与 `image_metrics` 一致；
3. **确定性检查**：同图连跑 2 次，分数差 ≤1e-4 才可用；关闭 GPU 非确定性/半精度漂移；
4. **只报相对基线差**：`delta = score(本轮 leg) − score(基线 leg)`；跨场景/跨模型比较绝对值时注明口径；
5. **作为参考项**：`score_trial.py --quality` 把“相对基线下降超过 tolerance”变成小罚分并单列；
   画质模型单列，`score=0` 由窗口指标决定；
6. 外部命令契约：`--quality-cmd` 接受图片路径，stdout 输出
   `{"model": "musiq", "score": 62.1}`；批量脚本按 leg 汇总后写进 `--quality` JSON。

已知偏差：模型在游戏 CG/风格化画面上域偏移明显（过曝/描边/纯色背景会被压低分）；分数作为辅助，
与亮度窗、色板及人工判读一起使用。

## 七、输出与对比

`image_metrics.py` 输出带 `metrics_version`、`roi`、`mask`、`stride`、`definitions`；`trial_metrics.json`
里每条 leg 带 `palette`/`subject_composition`/`saliency_composition`。评估 JSON 缺这两类时，
用 `score_trial.py --images <eval 目录>` 从 PNG 现算补齐；不补时只评 tone 窗。

- 只比**同版本、同口径**的数字；`metrics_version` 变了就重新建基线。
- `compare_evals.py` 比 luma/哈希；色板/构图差用 `score_trial.py` 的 `components` 与 `per_leg` 看，
  权重以 token 为准。
- 报告写明：哪条 leg、哪个指标、窗口是多少、超了多少、归一化尺度是多少。

## 八、已知假信号

- **stride 不同**：p15/p98 会小幅漂移；色板覆盖率变化更明显。
- **ROI 缩放取整**：非基准分辨率时 ROI 按比例 round，边缘 1–2px 差异可让 p98 跳 0.01。
- **天空掩码误伤**：夜景深蓝天空仍满足 `b-r>0.06·255 && b>0.70·255 && r>0.47·255`；冷色蓝衣可能被
  当天空排除——先看 `sky_frac` 与掩码可视化，再信数字。
- **近黑排除**：`luma<=0.05` 的描边/黑布被排除，p15 会被抬高；判暗部结构时结合 `dark.order`。
- **色板阈值过紧**：阈值 5 时高光/阴影自然漂移就会大面积不命中；先看 p90 ΔE 再调阈值。
- **saliency 边框**：全平/渐变画面显著框可能贴边；看 `mass_frac` 与 `bbox` 是否合理，`center_offset`
  作为观察项。
- **PNG 解码路径**：输入 8bit 非隔行；16bit/隔行 PNG 先转成支持格式。
