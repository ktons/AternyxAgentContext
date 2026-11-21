# 闭环参数搜索

当曝光、调色、灯位这类连续参数需要定值、人工试参数又太慢时，把「提议 → 出图 → 打分」交给搜索器。
本册给协议、预算、防过拟合和验收边界；工具是 `scripts/param_search.py`（CMA-ES/TPE）、
`scripts/score_trial.py`（token 窗口 → 复合分），trial 适配器按项目场景实现。搜索是**定值工具**，
验收由固定口径复验与人工判读完成。

## 一、适用条件

使用搜索：

- 参数连续（曝光、环境光、灯位角度、材质数值），组合多到网格扫不完；
- 有稳定的判读面（固定场景/机位/leg 集/分辨率/post）与 token 窗口；
- 用户明确批准搜索预算（trial 次数 × 单 trial 时长）。

改用其他手段：

- 单参数：网格/二分更快，也更好解释；
- 结构/风格决策（换 LUT 家族、换打光方案）：先人工 A/B；
- 判读面还在漂（场景/ROI/post 未定）：先固定口径，否则搜索会把噪声当目标；
- 缺少 token 窗口：先把目标写成可量化窗口。

搜索单独一轮，与 shader/材质结构改动分开；trial 只动 spec 列出的参数。

## 二、参数空间

`search spec` 的 `space` 是扁平参数名，`mapping` 把每个名字落到评估器参数（Volume 字段 / 灯光 Transform /
材质 numbers 槽）：

```json
{
  "space": {
    "exposure": {"kind": "float", "min": 0.8, "max": 1.4, "default": 1.0},
    "ambient": {"kind": "float", "min": 0.08, "max": 0.45, "log": false},
    "keyAzimuth": {"kind": "float", "min": -70, "max": 70, "step": 5},
    "keyElevation": {"kind": "float", "min": 5, "max": 70, "step": 5},
    "gradeV": {"kind": "float", "min": 0.0, "max": 2.0}
  },
  "mapping": {
    "exposure": {"target": "volume", "field": "exposure"},
    "ambient": {"target": "volume", "field": "ambientIntensity"},
    "keyAzimuth": {"target": "light", "entity": "KeyLight", "component": "azimuthDeg"},
    "keyElevation": {"target": "light", "entity": "KeyLight", "component": "elevationDeg"},
    "gradeV": {"target": "material", "path": "<material path>",
               "property": "<property>", "index": 1}
  }
}
```

- `kind`：`float` / `int` / `choice`。`step` 四舍五入到步长（曝光 0.01、角度 5° 等）；
  `log: true` 适合跨数量级的强度，跨度小的参数用线性。
- **边界**：给物理/资产允许的范围；越界值是坏 trial。缺省值作为 CMA-ES 初始均值。
- **可辨识性**：exposure 与 ambientIntensity 都抬 luma，搜索会沿等值线漂移；一次搜索只放一个
  “整体亮度”自由度，其余固定或用材质/灯光局部量。skyTop/skyBottom 与 ambient 会互相补偿，
  分批放开。
- **灯位语义**：`azimuthDeg/elevationDeg` 是灯光**传播方向**（Transform +Z），由 trial 适配器换算四元数；
  正负号先在基线 leg 上实测确认，再放进搜索。
- **材质数值**：`set` 只改 `index` 指定的分量，其余分量读回保留。材质写入接口会落盘并把材质资源
  升级到当前格式：trial 适配器显式接收材质补丁，结束后停编辑器、按材质文件与元数据的字节快照还原并校验 sha。
  搜索前保持 `git status` 干净，trial 后核对；并发搜索串行执行。
- **离散选择**：`kind: "choice"` 适合开关类；LUT 资产选择需要项目侧适配器（见 §八），
  把 guid 放进专用参数而非浮点参数。
- **固定项**：`fixed` 原样并进每个 trial；用来冻结无关字段（bloom/描边/材质补丁），保证 trial 之间
  只有一个搜索面在动。

## 三、复合目标

`score_trial.py` 把每条 leg 的 token 窗口折成标量：

1. 逐 leg、逐指标算归一化偏差：窗口内 0；越下限 `(min−v)/scale`；越上限 `(v−max)/scale`。
   `scale` = 窗口宽（成对时）或 `DEFAULT_SCALE`（单侧时）。
2. 指标权重来自 `lookdev.weight.<group>.<metric>`（缺省 1），leg 内加权平均得该 leg 罚分。
3. 全体 leg：`score = 0.65·mean(leg 罚分) + 0.35·max(leg 罚分)`——mean 拉整体，max 防“平均好但某个机位崩”。
4. 硬门（`feasible=false`，分数按 `1e6` 计）：缺 required leg、clip 超 0.01、编辑器 ERROR。
5. 画质模型（`--quality`）只加小罚分并单列，`score=0` 由窗口指标决定。

`score=0` = 全部窗口内，不等于“好看”；人工判读、中性门、色板审阅仍要做。

**平台期**：所有 leg 落窗后 score 是一片 0 平原，搜索失去梯度。此时二选一：

- 把艺术偏好写成**有方向**的窗口/指标（例如 `paletteCoverageMin` 从 0.6 提到 0.7），重跑一轮；
- 在可行域内做第二轮搜索，目标改为“离窗中心最近”或“最差 leg 余量最大”，并记录这是次级目标。

## 四、优化器选择与预算

| 场景 | 选择 | 预算 |
|---|---|---|
| 4–12 维连续、评估较贵、地形平滑 | **CMA-ES**（缺省） | 30–60 trials；λ=4+3ln(n)，σ 初值 0.25 |
| 混合/离散、评估噪声大、维度 ≤8 | **TPE** | 40–100 trials；gamma 0.25、每代 24 候选 |
| 1–2 维 | 网格/二分 | 网格或二分 |

- `budget.trials` 是**评估器调用次数**（CMA-ES 按代提议，最后不完整代只评估剩余个体；
  未评估个体用当代最差分参与更新，trial log 只记录已评估个体）。
- `budget.max_seconds` 是墙钟上限；到点停止，保留已评估结果。
- `budget.seed` 固定；同 spec 重跑命中缓存（按最终参数 JSON 的 sha256）。
- 单 trial 成本 = 起离屏编辑器 + 逐 leg seek/capture；按 leg 数与分辨率估总时长，预算按可完成量设置。
- 停止条件：预算用尽、`max_seconds`、连续若干代最优无改进；脚本无早停时按 trial 数控制。

## 五、评估器契约

`param_search.py` 接受最小契约，项目可以换自己的适配器：

1. 读 `{params}` 指向的 JSON（trial 适配器 schema：`volume` / `lights` / `materials` / `fixed`）；
2. 写 `{out}/trial_score.json`，至少 `{"score": number, "feasible": bool}`，建议带
   `components` / `per_leg` / `error`；
3. 失败（超时/异常/写不出文件）时框架记 `score=null`、`feasible=false`，按 `1e6` 参与搜索并继续；
4. 评估器内部还原现场（Volume/相机/灯光/材质还原与读回校验）。

适配器命令（骨架见 `templates/lookdev_trial_template.py`）：

```bash
python <adapter>.py \
  --params-file {params} --out {out} \
  --legs <train legs> --tokens <tokens.json> --profile day
```

项目适配器应保证：

- Volume 字段未知时直接拒绝；写后读回校验；
- 灯光覆盖发生在预设应用 seek 之后，capture 前复读 Transform；被动画写回则整 trial 失败
  （搜索到的灯位必须是画面实际灯位）；
- 采后 leg 间 sha 相同 → 记 `freeze_suspects`（冻帧/未 seek），trial 不可信；
- 结束恢复 Volume/相机/灯光/材质并读回；`git status` 新增项写进 `trial_metrics.json` 告警。

## 六、防过拟合与验收

搜索最优只是“在 train legs 上分最低”，需要复验：

1. **划分 train / holdout**：按机位×光向整组留出（随机抽 leg 会把同源画面分到两侧）。holdout 仅用于复验。
2. **复验最优**：用最优参数跑全量判读面（成片面 + 中性面），再用 `score_trial.py` 分别对
   train/holdout/全量算分，看 `holdout − train` 的差距。差距大 = 过拟合到 train 机位。
3. **确定性**：最优参数连跑 2 次，逐 leg sha 相同；不同则说明有 TAA/时钟/资产重导噪声，
   搜索重复评估取中位数。
4. **中性门**：搜索只应改目标区域；非目标区域按[判读与验收](03-evaluation.md)的中性门判。
5. **人工判读**：色板/构图/接触影/描边等 token 未覆盖的项看图确认；搜索分作为辅助。
6. **登记**：`search_result.json`（best、trials、seed、spec）与复验的 `score.json` 一起留 `temp/`；
   机制与坑进本 skill，参数终值进模块文档或材质说明。

**记录边界**：把搜索最优直接改材质资源前先复验；报告同时给 train 与 holdout 分；用 holdout 调参后
重新划分验收集；`score=0` 只表示窗口内。

## 七、日志与复现

产物（都在 `--out`）：

| 文件 | 内容 |
|---|---|
| `trials.jsonl` | 每行 `{trial, params, score, feasible, cached, duration, error}` |
| `trial_XXXX_params.json` | 该 trial 的最终参数（可直接喂 trial 适配器） |
| `trial_XXXX/trial_metrics.json` | 逐 leg 指标 + 还原/git/editor 状态 |
| `trial_XXXX/trial_score.json` | 复合分与分解（评估器契约） |
| `search_result.json` | space、best、全部 history、seed、elapsed |
| `best_params.json` / `best_trial.json` | 最优参数 / 最优 trial 记录 |
| `cache/<hash>.json` | 参数哈希 → 评估结果（重跑复用） |

复现一轮搜索：同 spec（同 space/mapping/seed/budget）+ 同二进制/资产 + 同 token；缓存会命中。
换 token/leg 集/场景即新搜索，并使用新的 `cache_dir`。

## 八、扩展点与已知坑

- **LUT/调色盘选择**：当前浮点适配器没有 LUT 资产引用参数；项目侧写一个 wrapper，读扁平参数 →
  选 LUT guid → 调 trial 适配器（或直接写 Volume 的 lutTexture 字段）。wrapper 自己校验读回。
- **矩阵工作点（clip 键）**：trial 的灯光覆盖只对“seek 后单帧”有效；改矩阵设计值走描述 JSON →
  重产 clip → 导入，再搜索。搜索参数与 clip 设计值分开记录。
- **材质落盘**：材质写入接口会写材质文件；trial 之间串行，结束后核对 `git status`。
- **灯位被动画写回**：适配器检测后令 trial 失败；频繁出现说明该场景的灯是每帧采样，先把灯改为
  clip 参数化，再在 capture 前覆盖。
- **冻帧**：同参数两次 sha 相同可能只是静态场景；不同 leg sha 相同才是冻帧签名。搜索前跑一次基线
  确认 leg 间哈希互异。
- **缓存污染**：缓存键只含参数；二进制/token/leg 集版本变化时换 `cache_dir`。
- **评估器超时**：`evaluator.timeout` 大于“启动 + 全部 leg 采集”；超时按失败计，会浪费预算。
- **评估器命令路径**：命令模板含 `{params}`/`{out}`，路径带空格时用引号或短目录名；`temp/<任务>`
  用短横线命名。
- **编排/资产新鲜度**：搜索前确认二进制晚于相关提交、clip 已重导。
- **进程残留**：trial 适配器复用评估母本的清残留逻辑；中断后确认无编辑器残留。

## 九、示例

```bash
# 1) 准备 spec（复制模板，改路径/leg/预算）
cp <skill>/templates/search_spec.json temp/<任务>/search.json

# 2) 先跑基线，确认 leg 哈希互异、score 可解释
python .../score_trial.py --metrics temp/<任务>_base/eval_metrics.json \
  --tokens <tokens> --profile day --out temp/<任务>_base/score.json

# 3) 搜索（后台跑，30–60 trials）
python .../param_search.py --spec temp/<任务>/search.json --out temp/<任务>/search

# 4) 复验最优：全量 leg + holdout + 两次确定性 + 中性门 + 人工
python <adapter>.py --params-file temp/<任务>/search/best_params.json \
  --out temp/<任务>_best --legs <all legs> --tokens <tokens> --profile day
#    适配器会写材质时，先确认 git status 干净并在复验后核对
python .../score_trial.py --metrics temp/<任务>_best/trial_metrics.json \
  --tokens <tokens> --profile day --out temp/<任务>_best/score.json
```
