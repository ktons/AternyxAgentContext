# 设计 token 与美术规范

美术规范写成一份可被脚本消费的 **design tokens** 文件，让色板、窗口与权重都有确定来源，
替代会议记录或截图里的形容词。
本册约定文件落位、字段命名、可判值、校验方式与变更追溯。格式采用 W3C Design Tokens Community Group
（DTCG）Format 的可落地子集，官方规范见
[designtokens.org/tr/drafts/format](https://www.designtokens.org/tr/drafts/format/) 与
[github.com/design-tokens/community-group](https://github.com/design-tokens/community-group/)。

> 工具：`scripts/design_tokens.py`（lint / resolve / palette / check-image）。
> 模板：`templates/lookdev.tokens.json`（含日景/夜景窗与一个示例色板）。

## 一、文件位置与生命周期

- **项目级唯一真源**：`<project>/design/lookdev/art.tokens.json`（或按 `$description` 拆分同目录多文件，
  由消费脚本显式列出）。文件放在设计资料目录，与运行时资产导入链分离。
- 模板带 `TODO` 占位（示例色板与画质基线）；复制后替换为项目美术规范，`--strict` 会拒绝未替换的占位。
- token 变更随代码分支提交；改窗口/权重/色板阈值时在提交信息写明“为什么改、旧基线是否重标定”。
  token 是规范，不是运行时资产，不会自动同步到材质资源或场景，映射关系见 §六。
- 一个 token 文件对应一个“判读 profile”（日景/夜景/角色/场景等）；不同 profile 用
  `lookdev.window.<profile>.*` 分组，共用一份文件。

## 二、命名约定

| 路径 | 类型 | 含义 | 消费方 |
|---|---|---|---|
| `color.palette.<name>` | color | 色板：画面必须能落到的颜色（皮肤/头发/布料/金属/天空/强调色） | `image_metrics.py`、`design_tokens.py check-image` |
| `lookdev.window.<profile>.<group>.<metric>` | number | 验收窗口（min/max 各一条 token） | `score_trial.py` |
| `lookdev.weight.<group>.<metric>` | number | 复合分权重（缺省 1） | `score_trial.py` |
| `lookdev.paletteThreshold` | number | 色板命中 ΔE2000 上限（缺省 10） | `image_metrics.py` / `score_trial.py` |
| `quality.<model>.*` | number / 任意 | 画质模型基线/容差（参考项） | 人工与后续脚本 |

`<group>` 用 leg 分组（如 body / face / hair / weapon / condition / close）。没有专门窗口的组回退 `body`；
`face` 判亮部，行内比与 p15 窗按[判读与验收](03-evaluation.md)的例外条款处理。

**成对窗口**：所有 `*Min` 配一条 `*Max`（`clipMax`、`centerOffsetMax` 这类单侧指标除外）。
lint 允许单侧窗口；`score_trial.py` 对单侧指标用 `DEFAULT_SCALE` 归一化，成对窗口更可解释。

## 三、DTCG 子集

`design_tokens.py` 支持：

- 组（无 `$value`）与 token（有 `$value`）；`$type` 写在组上向子 token 继承；
- 别名 `{group.token}`（含 `$root` 路径）、链式引用、循环检测；
- `color` / `number` / `dimension` / `duration` / `fontFamily` / `fontWeight` / `cubicBezier` 的值校验；
  其他类型原样保留并告警；
- color 值：`{colorSpace, components, alpha?, hex?}`，消费端支持 `srgb` 与 `srgb-linear`；
- `$description` / `$extensions`（`$extensions.lookdev.*` 为图形迭代保留命名空间）。

**扩展顺序**：需要 `$extends`、`$ref`、display-p3/rec2020 等能力时，先扩展工具与消费端，再把对应写法
加入项目 token；复用现有颜色时显式写别名。

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
- 色板 token 可在 `$extensions.lookdev.oklch` 写 `[L, C, H]` 供审阅；与 sRGB 计算值不一致会告警。
  正常流程由工具计算，保证审阅值来自计算而非手抄。

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

别名 token 不计入色板（同一颜色覆盖率权重只算一次）；它提供第二个名字。`$root` 路径按规范写成
`{color.accent.$root}`。

## 四、色板与 OKLCH

- **sRGB 是消费真源**：`hex`/`components` 是材质与灯光能直接落的值；OKLCH 是感知审阅视图
  （L 0..1、C 0..0.4、H 0..360°），由 `image_metrics.py` 计算。
- **色板描述“画面上应该出现的颜色家族”**，不是平均色。一个 token 的覆盖率 = 有效像素中最近 token 的
  ΔE2000 ≤ `lookdev.paletteThreshold` 的占比；`paletteDeltaE` = 最近 token 距离的均值。
  覆盖率低可能来自渐变/高光；持续 0 覆盖说明 token 与画面脱节，先查 token 与材质/灯光。
- **ΔE2000 参考尺**：≈1 为 JND，≈2–3 可察觉，≈5 明显，≈10 属不同颜色家族。阈值 10 表示“命中”，
  项目可按风格收紧到 5–8。
- 夜景/暖光 LUT 会整体旋转色相：色板匹配针对**最终帧**，不是 albedo。判“材质颜色”看中性面，
  判“成片色板”看成片面。

## 五、窗口（可量化验收面）

窗口按 profile 与 group 分列，值来自基线观测。示例：

| profile | group | 窗口 |
|---|---|---|
| day | body | p98 `[0.82,0.93]`、p15 `[0.18,0.35]`、ratio `[2.5,4.0]`、clip 0 |
| day | face | p98 `[0.82,0.93]`、clip 0（行内比与 p15 作观察项） |
| night | body | p98 `[0.72,0.93]`、p15 `[0.05,0.20]`、clip 0 |

构图指标（`subjectHeightMin/Max`、`centerOffsetMax`、`thirdsDistanceMax`、`headroomMin/Max`）在评分器支持后，
先在若干基线帧上观测分布，再登记窗；“三分法”先作为观察项。

## 六、token → 引擎的映射（手工登记）

token 是规范，不是资产。映射写在模块文档或材质说明里，至少覆盖：

| token | 消费点 | 说明 |
|---|---|---|
| `color.palette.*` | 材质资源的基色/染色参数、LUT 生成参数 | 改色板时同步改材质或 LUT 生成脚本 |
| `lookdev.window.*` | `score_trial.py` / 评估报告 | 只影响判分，渲染由材质/灯光决定 |
| `lookdev.paletteThreshold` | `image_metrics.py` | 只影响“命中”统计 |
| `quality.*` | `score_trial.py --quality`（参考项） | 作为单列参考项 |

把 token 接到材质生成时（例如 LUT 脚本读 token 色板），沿该工具的参数面传入并留 `--tokens` 记录；
shader 内的颜色常量与 token 建立单一映射。

## 七、lint 与改动流程

```bash
# 1) 先校验（--strict：TODO/告警即失败；--require-palette：色板非空）
python .agents/skills/graphics-iteration/scripts/design_tokens.py lint \
  <project>/design/lookdev/art.tokens.json --strict --require-palette

# 2) 查看解析结果（窗口/权重/色板 → JSON）
python .../design_tokens.py resolve <file> --out temp/tokens.resolved.json

# 3) 色板 vs 实际帧（覆盖率/ΔE/主导色；同时给 luma 与构图）
python .../design_tokens.py check-image <file> --image temp/<任务>/capture_face.png \
  --roi face --stride 1 --out temp/<任务>/palette_face.json
```

- **改窗前**：先跑一轮全量基线，确认当前 token 下 `score_trial.py` 的分解（哪些 leg/指标在罚分）；
  改窗后再跑同一基线，差异只来自窗口本身。
- **改色板前**：用 `check-image` 在基线帧上逐 leg 看覆盖率；新增 token 后原 token 覆盖率保持稳定，
  说明只是拆细而非改色。
- **窗口放宽**：与“场景/编排/口径变化”或“艺术目标变更”一起登记，并写明旧基线是否重标定。
- lint 报告字段：`errors`（必须修）、`warnings`（TODO/hex 漂移/未知指标/空色板）、`stats`。
  `--strict` 把 warnings 并入 errors，供 CI 与开工门使用。

## 八、已知坑

- **Windows 记事本/PowerShell 会写 UTF-8 BOM**：工具按 `utf-8-sig` 读；手写文件保持无 BOM，
  让其他消费方也能直接解析。
- **JSON 不支持注释**：说明写 `$description`。
- **组名/token 名使用普通标识符**：`.`、`{}`、`$` 保留给路径与保留字（`$root` 除外）；别名路径按 `.` 分段。
- **色板写“应该出现的颜色”**：大面积背景会主导 mean，导致皮肤 token 覆盖率偏低；覆盖率按最近 token 统计，
  以逐像素命中替代聚类平均。
- **token 文件放在设计资料目录**：与资产导入链分离，保持纯文本与独立路径。
