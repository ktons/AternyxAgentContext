# 闭环参数搜索

当「曝光/调色/灯位」这类连续参数需要定值、而人工试参数又太慢时，把「提议 → 出图 → 打分」交给
搜索器。本册给协议、预算、防过拟合和验收边界；工具是 `scripts/param_search.py`（CMA-ES/TPE）、
`scripts/lookdev_trial.py`（单 trial 应用+出图+还原）与 `scripts/score_trial.py`（token 窗口 →
复合分）。搜索是**定值工具**，不是验收门。

## 一、何时用 / 不用

用：

- 参数是连续的（曝光、环境光、灯位角度、材质数值），且组合多到网格扫不完；
- 有稳定的判读面（固定场景/机位/leg 集/分辨率/post）与 token 窗口；
- 用户明确批准搜索预算（trial 次数 × 单 trial 时长）。

不用：

- 单参数（网格/二分更快，也更好解释）；
- 结构/风格决策（换 LUT 家族、换打光方案）——先人工 A/B；
- 判读面还在漂（场景/ROI/post 未定）——搜索会把噪声当目标；
- 没有 token 窗口——没有可优化的目标，只有「看起来」。

搜索必须**单独一轮**：不与 shader/材质结构改动同轮；trial 里只动 spec 列出的参数。

## 二、参数空间

`search spec` 的 `space` 是扁平参数名，`mapping` 把每个名字落到评估器参数（当前支持 Volume 字段 /
灯光 Transform / 材质 numbers 槽）：

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
    "gradeV": {"target": "material", "path": "Assets/material/char/char_skin.mat",
               "property": "gSkinSSSParams", "index": 1}
  }
}
```

- `kind`：`float` / `int` / `choice`。`step` 会四舍五入到步长（曝光 0.01、角度 5° 这类）；
  `log: true` 适合跨数量级的强度（环境光 0.05–0.5 不建议 log，跨度太小）。
- **边界**：只给物理/资产允许的范围；越界值不是「更大胆的艺术」，是坏 trial。缺省值作为
  CMA-ES 初始均值（不是硬约束）。
- **可辨识性**：exposure 与 ambientIntensity 都抬 luma，搜索会沿等值线漂移；一次搜索只放一个
  「整体亮度」自由度，其余固定或用材质/灯光局部量。同理 skyTop/skyBottom 与 ambient 会互相
  补偿，别同时放开。
- **灯位语义**：`azimuthDeg/elevationDeg` 是灯光**传播方向**（Transform +Z），由
  `lookdev_trial.py` 换算四元数；不是「灯在角色左前方」这种艺术描述。正负号以实测帧为准，
  先在基线 leg 上打印/对比一次，再放进搜索。
- **材质数值**：`set` 只改 `index` 指定的分量，其余分量读回保留。**`material.set` 会落盘并把
  `.mat` 升级到当前格式**：`lookdev_trial.py` 必须显式加 `--allow-material-write` 才接受材质补丁；
  结束后停编辑器、按 `.mat`/`.mat.meta` 的字节快照还原并校验 sha。搜索前确保 `git status` 干净，
  trial 后核对；异常中断可能留下改动，用 `git checkout -- <path>` 还原。并发搜索会互相踩材质，禁止。
- **离散选择**：`kind: "choice"` 适合开关类；当前 `lookdev_trial` 没有 LUT 资产引用参数化落点，
  LUT 选择需要项目侧适配器（见 §八），不要硬把 guid 塞进浮点参数。
- **固定项**：`fixed` 原样并进每个 trial；用来冻结无关字段（bloom/描边/材质补丁），保证 trial
  之间只有一个搜索面在动。

## 三、复合目标

`score_trial.py` 把每条 leg 的 token 窗口折成标量：

1. 逐 leg、逐指标算归一化偏差：窗口内 0；越下限 `(min−v)/scale`；越上限 `(v−max)/scale`。
   `scale` = 窗口宽（成对时）或 `DEFAULT_SCALE`（单侧时）。
2. 指标权重来自 `lookdev.weight.<group>.<metric>`（缺省 1），leg 内加权平均得该 leg 罚分。
3. 全体 leg：`score = 0.65·mean(leg 罚分) + 0.35·max(leg 罚分)`——mean 拉整体，max 防「平均好
   但某个机位崩」。
4. 硬门（`feasible=false`，分数按 `1e6` 计）：缺 required leg、clip 超 0.01、编辑器 ERROR。
5. 画质模型（`--quality`）只加小罚分并单列，不参与 `score=0`。

`score=0` = 全部窗口内，不等于「好看」；人工判读、中性门、色板审阅仍要做（见 §六）。

**平台期**：所有 leg 都落窗后，score 是一片 0 平原，搜索失去梯度。此时不要加拍脑袋的 tie-break，
而是二选一：

- 把艺术偏好写成**有方向**的窗口/指标（例如 `paletteCoverageMin` 从 0.6 提到 0.7），重跑一轮；
- 在可行域内做第二轮搜索，目标改为「离窗中心最近」或「最差 leg 余量最大」，并明确记录这是
  次级目标，不是验收。

## 四、优化器选择与预算

| 场景 | 选择 | 预算 |
|---|---|---|
| 4–12 维连续、评估较贵、地形平滑 | **CMA-ES**（缺省） | 30–60 trials；λ=4+3ln(n)，σ 初值 0.25 |
| 混合/离散、评估噪声大、维度 ≤8 | **TPE** | 40–100 trials；gamma 0.25、每代 24 候选 |
| 1–2 维 | 网格/二分 | 不需要搜索器 |

- `budget.trials` 是**评估器调用次数**（CMA-ES 按代提议，最后不完整代只评估剩余个体；未评估
  个体用当代最差分参与更新，不写 trial log）。
- `budget.max_seconds` 是墙钟上限；到点停止，保留已评估结果。
- `budget.seed` 固定；同 spec 重跑命中缓存（按最终参数 JSON 的 sha256），不会重复烧 GPU。
- 单 trial 成本：起离屏编辑器 + 逐 leg seek/capture。当前 3 leg ≈ 23 s、7 leg ≈ 30–40 s（本机
  实测，随 leg 数/分辨率变化）；30 trials ≈ 15–20 min。预算按「一夜能跑完」估，不要开 200 trials
  却没人看。
- 停止条件：预算用尽 / `max_seconds` / 连续若干代最优无改进（当前脚本没有早停，用 trial 数控制；
  需要早停时看 `trials.jsonl` 手动停）。

## 五、评估器契约

`param_search.py` 只认这个最小契约，项目可以换自己的适配器：

1. 读 `{params}` 指向的 JSON（`lookdev_trial` schema：`volume` / `lights` / `materials` / `fixed`）；
2. 写 `{out}/trial_score.json`，至少 `{"score": number, "feasible": bool}`，建议带
   `components` / `per_leg` / `error`；
3. 失败（超时/异常/写不出文件）时框架记 `score=null`、`feasible=false`，按 `1e6` 参与搜索并继续，
   不中断整轮；
4. 评估器内部必须自己还原现场（`lookdev_trial` 已做 Volume/相机/灯光/材质还原与读回校验）。

参考实现命令：

```bash
python .agents/skills/graphics-iteration/scripts/lookdev_trial.py \
  --params-file {params} --out {out} \
  --legs front_front,left45_left45,back180_back,face_front,face_left45 \
  --tokens .agents/skills/graphics-iteration/templates/lookdev.tokens.json --profile day
```

`lookdev_trial.py` 的额外保证：

- Volume 字段未知直接拒绝（防拼错静默无效）；写后读回校验；
- 灯光覆盖发生在 `preset.apply` seek 之后，capture 前复读 Transform；被动画写回则整 trial 失败
  （搜索到的「灯位」必须是画面实际灯位）；
- 采后 leg 间 sha 相同 → 记 `freeze_suspects`（冻帧/未 seek），trial 不可信；
- 结束恢复 Volume/相机/灯光/材质并读回；`git status` 新增项写进 `trial_metrics.json` 告警。

## 六、防过拟合与验收

搜索最优只是「在 train legs 上分最低」，必须复验：

1. **划分 train / holdout**：按机位×光向整组留出，不随机抽 leg。建议 train = 正/左45/背180/face
   正光，holdout = face 左45、hair、weapon、condition；holdout 不参与评分。
2. **复验最优**：用最优参数跑 `ag_lookdev_shading_eval.py --post scene`（全量 43 leg），再跑
   `--post neutral` 判 shading；用 `score_trial.py` 分别对 train/holdout/全量算分，看
   `holdout − train` 的差距。差距大 = 过拟合到 train 机位。
3. **确定性**：最优参数连跑 2 次，逐 leg sha 应相同；不同则说明有 TAA/时钟/资产重导噪声，
   搜索需要重复评估取中位数（当前脚本没有 replica，需人工加）。
4. **中性门**：搜索只应改目标区域；非目标区域按 [判读与验收](03-evaluation.md) 的中性门判。
5. **人工判读**：色板/构图/接触影/描边等 token 没覆盖的项，必须看图；搜索分不能替代审阅。
6. **登记**：`search_result.json`（best、trials、seed、spec）与复验的 `score.json` 一起留
   `temp/`；机制/坑进本 skill，参数终值进模块文档或材质说明。

**反模式**：拿搜索最优直接改 `.mat` 提交；只报 train 分；用 holdout 调参后又拿 holdout 当验收；
把 `score=0` 当「艺术通过」。

## 七、日志与复现

产物（都在 `--out`）：

| 文件 | 内容 |
|---|---|
| `trials.jsonl` | 每行 `{trial, params, score, feasible, cached, duration, error}` |
| `trial_XXXX_params.json` | 该 trial 的最终参数（可直接喂 `lookdev_trial.py`） |
| `trial_XXXX/trial_metrics.json` | 逐 leg 指标 + 还原/git/editor 状态 |
| `trial_XXXX/trial_score.json` | 复合分与分解（评估器契约） |
| `search_result.json` | space、best、全部 history、seed、elapsed |
| `best_params.json` / `best_trial.json` | 最优参数（可直接喂 `lookdev_trial.py`）/ 最优 trial 记录 |
| `cache/<hash>.json` | 参数哈希 → 评估结果（重跑复用） |

复现一轮搜索：同 spec（同 space/mapping/seed/budget）+ 同二进制/资产 + 同 token；缓存会命中。
换 token/leg 集/场景就是**新搜索**，不要复用旧缓存目录。

## 八、扩展点与已知坑

- **LUT/调色盘选择**：当前适配器没有 LUT 资产引用参数；项目侧写一个 wrapper，读扁平参数 →
  选 LUT guid → 调 `lookdev_trial`（或直接 `component.set` Volume.lutTexture）。wrapper 必须自己
  校验读回，不能只改 `.scene`。
- **矩阵工作点（clip 键）**：`lookdev_trial` 的灯光覆盖只对「seek 后单帧」有效；要改矩阵的
  设计值，走 `ag_lookdev_matrix` 描述 JSON → 重产 clip → `asset.import {force:true}`，再搜。
  两件事不要混：搜索是找一张/一组画面的参数，clip 是编排真源。
- **材质落盘**：`material.set` 会写 `.mat`；trial 之间串行、结束后核对 `git status`；并发搜索会
  互相踩材质，禁止。
- **灯位被动画写回**：`lookdev_trial` 已检测并失败；如果频繁出现，说明该场景的灯是每帧采样，
  需要改为 clip 参数化，而不是在 capture 前抢写。
- **冻帧**：同参数两次 sha 相同不必然是冻帧（静态场景正常）；不同 leg sha 相同才是。搜索前先跑
  一次基线确认 leg 间哈希互异。
- **缓存污染**：缓存键只有参数，不含二进制/token/leg 集版本；换其中任何一个就换 `cache_dir`。
- **评估器超时**：`evaluator.timeout` 要大于「启动 + 全部 leg 采集」；超时按失败计，会浪费预算。
- **评估器命令路径不要含空格**：命令模板用 `shell=True` 拼接 `{params}`/`{out}`，含空格的路径会拆词；
  `temp/<任务>` 尽量用短横线命名。
- **编排/资产新鲜度**：搜索前确认二进制晚于相关提交、clip 已重导；否则搜的是旧画面。
- **进程残留**：`lookdev_trial` 复用 eval 母本的清残留/杀编辑器逻辑；中断后手动确认无
  `AternyxEditorMain` 残留。

## 九、示例

```bash
# 1) 准备 spec（复制模板，改路径/leg/预算）
cp .agents/skills/graphics-iteration/templates/search_spec.json temp/<任务>/search.json

# 2) 先跑基线，确认 leg 哈希互异、score 可解释
python .../ag_lookdev_shading_eval.py --post scene --out temp/<任务>_base
python .../score_trial.py --metrics temp/<任务>_base/eval_metrics.json \
  --tokens <tokens> --profile day --out temp/<任务>_base/score.json

# 3) 搜索（后台跑，30–60 trials）
python .../param_search.py --spec temp/<任务>/search.json --out temp/<任务>/search

# 4) 复验最优：全量 43 leg + holdout + 两次确定性 + 中性门 + 人工
python .../lookdev_trial.py --params-file temp/<任务>/search/best_params.json \
  --out temp/<任务>_best --legs front_front,left45_left45,back180_back,face_front,face_left45 \
  --tokens <tokens> --profile day
#    若 spec.mapping 含 material，复验命令加 --allow-material-write（先确认 git status 干净）
python .../ag_lookdev_shading_eval.py --post scene --out temp/<任务>_best_scene
python .../score_trial.py --metrics temp/<任务>_best_scene/eval_metrics.json \
  --tokens <tokens> --profile day --out temp/<任务>_best_scene/score.json
```
