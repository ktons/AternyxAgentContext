# 设计 token 与美术规范

美术规范不是散落在会议记录/截图里的形容词，而是一份可被脚本消费的 **design tokens** 文件。
本册给 Aternyx 的落地面：文件放哪、字段怎么命名、哪些值可判、怎么校验、改动怎么追溯。格式
跟 W3C Design Tokens Community Group（DTCG）Format 2025.10 的可落地子集，官方规范见
[designtokens.org/tr/drafts/format](https://www.designtokens.org/tr/drafts/format/) 与
[github.com/design-tokens/community-group](https://github.com/design-tokens/community-group/)。

> 工具：`scripts/design_tokens.py`（lint / resolve / palette / check-image）。
> 模板：`templates/lookdev.tokens.json`（含当前日景/夜景窗与一个示例色板）。

## 一、文件位置与生命周期

- **项目级唯一真源**：`Test/Projects/<project>/Fixtures/lookdev/art.tokens.json`（或按
  `$description` 拆分同目录多文件，由消费脚本显式列出）。不进 `Assets/`、不参与资产导入。
- 模板带 `TODO` 占位（示例色板与画质基线）；复制后必须替换成项目美术规范，`--strict` 会拒绝
  未替换的占位。
- token 变更随代码分支提交；改窗口/权重/色板阈值必须在提交信息写明「为什么改、旧基线是否
  重标定」。token 不是运行时资产，不会自动同步到 `.mat`/场景，映射关系见 §六。
- 一个 token 文件对应一个「判读 profile」（日景/夜景/角色/场景……）；不同 profile 用
  `lookdev.window.<profile>.*` 分组，不复制整份文件。

## 二、命名约定（本仓库投影）

| 路径 | 类型 | 含义 | 消费方 |
|---|---|---|---|
| `color.palette.<name>` | color | 色板：画面必须能落到的颜色（皮肤/头发/布料/金属/天空/强调色） | `image_metrics.py`、`design_tokens.py check-image` |
| `lookdev.window.<profile>.<group>.<metric>` | number | 验收窗口（min/max 各一条 token） | `score_trial.py` |
| `lookdev.weight.<group>.<metric>` | number | 复合分权重（缺省 1） | `score_trial.py` |
| `lookdev.paletteThreshold` | number | 色板命中 ΔE2000 上限（缺省 10） | `image_metrics.py` / `score_trial.py` |
| `quality.<model>.*` | number / 任意 | 画质模型基线/容差（只作参考，不作门） | 人工/后续脚本 |

`<group>` 用 leg 分组：`body` / `face` / `hair` / `weapon` / `condition` / `close`。没有专门窗口的
组回退 `body`；`face` 只判亮部（行内比与 p15 窗按 [判读与验收](03-evaluation.md) 的例外条款）。

**必须成对**：所有 `*Min` 都要有 `*Max`（`clipMax`/`centerOffsetMax` 这类单侧指标除外）。lint
不强制成对，但 `score_trial.py` 只有单侧时会用 `DEFAULT_SCALE` 归一化——成对窗口更可解释。

## 三、DTCG 子集（本仓库实现边界）

`design_tokens.py` 支持：

- 组（无 `$value`）与 token（有 `$value`）；`$type` 写在组上向子 token 继承；
- 别名 `{group.token}`（含 `$root` 路径）、链式引用、循环检测；
- `color` / `number` / `dimension` / `duration` / `fontFamily` / `fontWeight` / `cubicBezier` 的
  值校验；其他类型原样保留并告警；
- color 值：`{colorSpace, components, alpha?, hex?}`，本仓库只消费 `srgb` 与 `srgb-linear`；
- `$description` / `$extensions`（`$extensions.aternyx.*` 为本仓库保留命名空间）。

**未实现**（不要写进项目 token）：`$extends` 组扩展、`$ref` JSON Pointer 引用、非 sRGB 色彩空间
（display-p3/rec2020 等）。需要复用就显式写别名或重复 token。

### color 值规则

```json
{
  "skin_base": {
    "$type": "color",
    "$description": "皮肤受光基色（sRGB 为消费真源）",
    "$value": {"colorSpace": "srgb", "components": [0.949, 0.776, 0.706], "hex": "#f2c6b4"}
  }
}
```

- `components` 是 0..1 的 sRGB（或 srgb-linear）三分量；`hex` 可省略，工具会补。
- `hex` 与 `components` 不一致会告警（按 components 消费）；差 ≤1/255 不告警（量化）。
- 色板 token 的 `$extensions.aternyx.oklch` 可写 `[L, C, H]` 供审阅；与 sRGB 计算值不一致会告警。
  正常流程是**工具算**、不回填，避免手抄漂移。

### 别名与 `$root`

```json
{
  "color": {
    "palette": {
      "skin_base": {"$type": "color", "$value": {"colorSpace": "srgb", "components": [0.949, 0.776, 0.706]}},
      "skin_primary": {"$type": "color", "$value": "{color.palette.skin_base}"}
    }
  }
}
```

别名 token 不计入色板（否则同一颜色覆盖率权重翻倍）；它只是第二个名字。`$root` 路径按规范写成
`{color.accent.$root}`。

## 四、色板与 OKLCH

- **sRGB 是消费真源**：`hex`/`components` 是材质/灯光能直接落的值；OKLCH 是感知审阅视图
  （L 0..1、C 0..0.4、H 0..360°），由 `image_metrics.py` 计算。
- **色板不是「平均色」**：它描述「画面上应该出现的颜色家族」。一个 token 的覆盖率 = 有效像素中
  最近 token 的 ΔE2000 ≤ `lookdev.paletteThreshold` 的占比；`paletteDeltaE` = 最近 token 距离的
  均值。覆盖率低不必然是缺陷（画面有渐变/高光），但**持续 0 覆盖**说明色板 token 与画面脱节，
  先查 token 是否写错、再查材质/灯光是否偏色。
- **ΔE2000 参考尺**：≈1 为 JND，≈2–3 可察觉，≈5 明显，≈10 已属不同颜色家族。阈值 10 是
  「命中」而非「合格」；项目可按风格收紧到 5–8。
- 夜景/暖光 LUT 会整体旋转色相：色板匹配是对**最终帧**，不是 albedo；判「材质颜色对不对」要
  在 `--post neutral` 面看，判「成片色板」在 `--post scene` 面看。

## 五、窗口（可量化验收面）

当前模板把 [判读与验收](03-evaluation.md) 的窗搬到 token：

| profile | group | 窗口 |
|---|---|---|
| day | body | p98 `[0.82,0.93]`、p15 `[0.18,0.35]`、ratio `[2.5,4.0]`、clip 0 |
| day | face | p98 `[0.82,0.93]`、clip 0（行内比/p15 不作门） |
| day | condition | 沿用 body 窗（同曝光口径） |
| day | close | 只观测暗部覆盖/主体占高，暂无标定窗 |
| night | body | p98 `[0.72,0.93]`、p15 `[0.05,0.20]`、clip 0；ratio 不作门 |
| night | face | p98 `[0.72,0.93]`、clip 0 |

构图指标（`subjectHeightMin/Max`、`centerOffsetMax`、`thirdsDistanceMax`、`headroomMin/Max`）已
在评分器支持，但**没有标定值就不要写**：先在若干基线帧上观测分布，再登记窗；不要把「三分法」
直接写成硬门。

## 六、token → 引擎的映射（手工登记，不做运行时同步）

token 是规范，不是资产。映射写在模块文档/材质说明里，至少覆盖：

| token | 消费点 | 说明 |
|---|---|---|
| `color.palette.*` | `.mat` 的 `gBaseColor`/`gTintColor`/LUT 生成参数 | 改色板要同时改材质或 LUT 生成脚本，不能只改 token |
| `lookdev.window.*` | `score_trial.py` / `ag_lookdev_shading_eval.py` 报告 | 只影响判读，不影响渲染 |
| `lookdev.paletteThreshold` | `image_metrics.py` | 只影响「命中」统计 |
| `quality.*` | `score_trial.py --quality`（参考项） | 不参与 `score=0` 判定 |

若后续把 token 接到材质生成（例如 `gen_lookdev_lut.py` 读 token 色板），映射必须走该工具的
参数面并留 `--tokens` 记录；不要在 shader 里硬编码第二份颜色。

## 七、lint 与改动流程

```bash
# 1) 先校验（--strict：TODO/告警即失败；--require-palette：色板必须非空）
#    以下路径假定项目已把模板复制到 Fixtures/lookdev/art.tokens.json
python .agents/skills/graphics-iteration/scripts/design_tokens.py lint \
  Test/Projects/General/Fixtures/lookdev/art.tokens.json --strict --require-palette

# 2) 看一眼解析结果（窗口/权重/色板 → JSON）
python .../design_tokens.py resolve <file> --out temp/tokens.resolved.json

# 3) 色板 vs 实际帧（覆盖率/ΔE/主导色；同时给 luma 与构图）
python .../design_tokens.py check-image <file> --image temp/<任务>/capture_face_front_1001.png \
  --roi face --stride 1 --out temp/<任务>/palette_face.json
```

- **改窗的前置**：先跑一轮全量 `ag_lookdev_shading_eval.py` 基线，确认现有 token 下
  `score_trial.py --metrics .../eval_metrics.json` 的分解（哪些 leg/指标在罚分）。改窗后再跑同一
  基线，差异只能来自窗口本身。
- **改色板的前置**：`check-image` 在基线帧上逐 leg 看覆盖率；新增 token 后原 token 覆盖率不应
  大幅下降（说明只是拆细，不是改色）。
- **禁止**：把 token 当「许愿池」——为了 score 落 0 而放宽窗口。窗口放宽必须与「场景/编排/口径
  变了」或「艺术目标变更」一起登记；否则就是改判据凑绿。
- lint 报告字段：`errors`（必须修）、`warnings`（TODO/hex 漂移/未知指标/空色板）、`stats`。
  `--strict` 把 warnings 并入 errors，供 CI/开工门使用。

## 八、已知坑

- **Windows 记事本/PowerShell 会写 UTF-8 BOM**：工具按 `utf-8-sig` 读，但手写文件尽量无 BOM，
  避免其它消费方（引擎/第三方）不认。
- **JSON 不支持注释**：说明写 `$description`，不要把 `//` 写进文件。
- **组名/ token 名不能含 `.`、`{}`、`$` 前缀**（`$root` 除外）；别名路径靠 `.` 分段。
- **别把色板写成「平均色」**：大面积背景会主导 mean，导致皮肤 token 覆盖率永远低；色板应写
  「应该出现的颜色」，覆盖率按最近 token 统计，不做聚类平均。
- **token 文件不是导入资产**：放 `Fixtures/` 不会被 `asset.import` 扫到；不需要 `.meta`。
