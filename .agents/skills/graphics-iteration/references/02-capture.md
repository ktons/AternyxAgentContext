# 出图与留档

判读的第一步是拿到**真实输出帧**并保持同一口径。本册给标准命令与产物纪律；采集契约、缺帧判定与
参数细节归 [acceptance-verify](../../acceptance-verify/SKILL.md)，不在此复制。

## 一、离屏采真实帧（通用）

```bash
mkdir -p temp/<任务名>-view1
python3 .agents/skills/acceptance-verify/scripts/frame_capture.py \
  --exe "$PWD/build/bin/AternyxEditorMain" --cwd "$PWD/build/bin" \
  --project "$PWD/Test/Projects/General" \
  --token deadbeef12345678 --engine-arg=--no-cef-ui \
  --view 1 --want-seq 1000 --span 200 --max-frames 20 --frames 4000 \
  --out-prefix "$PWD/temp/<任务名>-view1/cap"
```

Windows：可执行名加 `.exe`，路径用绝对路径（PowerShell 下 `$PWD` 亦可），建目录用
`New-Item -ItemType Directory -Force`；其余参数一致。

- `--view 1` = Game 视图（场景相机画面），`--view 2` = Scene 视图（编辑器相机）；**两个视图分别采集**，
  缺省不过滤时同 seq 先到先存会混视图。
- `--cwd` 必须 `build/bin`；`--exe` 必须绝对路径（Popen 相对路径按子进程 cwd 解析会失配）。
- `--want-seq` 锚 ≥1000：帧通道不缓冲订阅前帧，离屏 fps 高，锚低会整窗冲过（0 帧保存）。
  不限帧口径由脚本保障：对编辑器宿主（本册标准命令即 `AternyxEditorMain`）显式追加
  `--maxfps 0`，AternyxMain 宿主不传（其 CLI 不认该参数）。
- `--frames` 留足余量，防跑完拆 server 的 connection closed 假故障。
- `--token` 显式传（脚本化拉起首选）；`--engine-arg=--no-cef-ui` 跳过 CEF UI，采帧不需要 dev server。
- 产物：`cap_frame<seq>.bin`（裸像素）+ `cap_meta.json`（seq/尺寸/格式）+ `cap_engine.log`。
  **输出目录必须先存在**（脚本不会自建）。
- 脚本拉起的编辑器跑完自动退出；用完确认无 `AternyxEditorMain` 残留。

## 二、ag_lookdev 出图判读（专用矩阵母本）

`.agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py` 把「ag_lookdev 场景的可量化
判读面」一条命令跑完：起离屏编辑器（`--offscreen --no-cef-ui`）→ `scene.load ag_lookdev.scene` →
可选 Volume override → `editor.captureAll` 逐 leg 出图 → close-up 补拍 → 逐 leg ROI 指标 → 汇总
JSON + contact sheet。每轮迭代重跑同一口径做前后对比。

```bash
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py                  # 中性基线 43 leg
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --post scene --out temp/ag_scene
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --skip-close --out temp/ag_neutral
python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --legs detail --width 1280 --height 720
```

- **`--post neutral|scene`**（缺省 neutral）：neutral = 经 `component.set` 临时把 Volume 的
  bloom/描边关断（其余字段按 `components()` 读回原值回写，不虚构 lutTexture 引用），出图后恢复并
  读回校验；scene = 场景原样（当前 HEAD 画面）。中性 override 同时归零 `bloomStrength` 与
  `bloomThreshold`：强度归零即真关断，双零对打包序不敏感（打包序历史互换、已修）；
  `--neutral-bloom field-only` 只写 `bloomStrength=0`，作字面对照。
- **`--out`**：产物目录（缺省 `temp/ag_t0`，相对仓库根）。**每轮显式给 `temp/<任务名>`**，`temp/`
  已 gitignore，产物不入库。
- **`--skip-close`**：跳过 4 向 close-up。全量共 **43 leg = 39 预设 + 4 向 close-up**
  （39 = 36 矩阵（9 机位 × 4 光向）+ 3 条光照条件，由 `ag_lookdev_matrix` clip 键驱动）；close-up =
  同一 face 机位沿相机前向推到距头心 `--close-distance`（缺省 **0.85 m**）后按正/左45/右45/背四光向
  各拍一张，并与对应预设帧、彼此之间做哈希比对防冻帧。
- **`--width/--height`**（缺省 **1920×1080**）：必须等于 game view 实际渲染尺寸（离屏缺省 1920；
  `editor.capture` 只校验尺寸、不改渲染分辨率），改尺寸时 ROI 按等比缩放。
  `--legs matrix|detail|all|close` 控制分析子集（批次仍出全量）。
- **产物**：`capture_<preset>_<seq>.png`、`capture_close_<向>_<seq>.png`（编辑器原始 PNG，不改写）、
  `eval_metrics.json`（配置/口径/逐 leg/汇总/冻帧自检/git 快照）、`contact_sheet.png`（4 列缩略图，
  顺序见 JSON `contact_sheet_order`）、`editor.log`、`eval.log`。
- **纪律**：采后核 `git status` 新增项（脚本对运行前后做差并写进 JSON 告警）——编辑器起停可能回写
  `.mat.meta` 或回滚重导产物，非预期改动要还原；重跑前脚本按 `GatewaySession-<pid>.txt` 清本工程
  残留编辑器；结束时恢复 Volume/MainCamera 原值并读回校验。
- 预设数不是 39 时脚本只 WARN；场景改过就先核对 ROI 与 contact sheet 口径，再信数字。

## 三、单 trial 参数化出图（闭环搜索用）

`lookdev_trial.py` 是「应用参数 → 逐 leg 出图 → 指标 → 还原」的试跑适配器，供
[闭环参数搜索](08-search.md) 调用；手工验参也用它，比改 `.mat`/`.scene` 再全量 eval 快。

```bash
python .agents/skills/graphics-iteration/scripts/lookdev_trial.py \
  --params-file temp/<任务>/p1.json --out temp/<任务>/trial1 \
  --legs front_front,left45_left45,back180_back,face_front,face_left45 \
  --tokens .agents/skills/graphics-iteration/templates/lookdev.tokens.json --profile day
```

参数 JSON：

```json
{
  "volume": {"exposure": 1.15, "ambientIntensity": 0.30},
  "lights": {"KeyLight": {"azimuthDeg": 35, "elevationDeg": 40}},
  "materials": [
    {"path": "Assets/material/char/char_skin.mat", "property": "gSkinSSSParams",
     "set": {"1": 2.0, "2": 0.1}}
  ]
}
```

- **Volume/灯光覆盖在每次 `preset.apply` seek 之后重新落**：clip 会把 exposure/ambient/灯位写回
  键值；脚本在 capture 前复读，若被写回则整 trial 失败（避免搜到「画面实际没变」）。
- `lights` 用 `azimuthDeg/elevationDeg` 描述传播方向（+Z），或直接给 `rotation:[x,y,z,w]`；
  仅为「seek 后单帧」口径，不改 clip 真源。
- `materials` 的 `set` 只改指定下标，其余分量读回保留；`numbers` 可整组替换。**`material.set` 会
  落盘并把 `.mat` 升级到当前格式**：必须显式加 `--allow-material-write`，脚本在 trial 结束停编辑器
  后按 `.mat`/`.mat.meta` 字节快照还原并校验 sha。采后仍要核 `git status`；异常中断手工
  `git checkout -- <path>`。
- 产物：`capture_<leg>_<seq>.png`、`trial_metrics.json`（逐 leg 指标 + 还原/git/editor 状态）、
  `trial_score.json`（给 `--tokens` 时）、`editor.log`、`trial.log`。
- 成本：单 leg ≈ 5–8 s（起会话后），3 leg ≈ 23 s、7 leg ≈ 30–40 s；搜索预算按此估。
- 不替代全量 eval：trial 只采指定 leg；验收/回归仍用 `ag_lookdev_shading_eval.py` 全量 43 leg。

## 四、裸帧转 PNG

帧格式码见 `Source/Runtime/Function/Gateway/FrameMessage.h`：`1 = RGBA8`（字节序 R,G,B,A，紧凑
行序、顶行在前），`2 = BGRA8`。统一用
`.agents/skills/acceptance-verify/scripts/frame_to_png.py`（纯 zlib，无 PIL）：

```bash
python .agents/skills/acceptance-verify/scripts/frame_to_png.py temp/<任务>/cap_frame1010.bin \
  --out temp/<任务>/cap_frame1010.png
```

- 尺寸自动从同名 `_meta.json` 读取；散 bin 用 `--width/--height` 显式给。
- 细节判读用 `--crop x,y,w,h` **裁局部**原生分辨率，不要先降采样；`--zoom N` 最近邻放大。
- 读图上限的真实口径是**长边像素**不是文件字节数：1280×720 预览 PNG 直读无损；长边超
  ~1500–2000px 会被内部降采样，小字/1px 纹理可能失真；要 1px 级细节先裁剪。
- macOS 上的 `sips` 只作历史经验：`sips --cropOffset 0 0` 会退化为居中裁剪，非零偏移才按预期裁；
  新脚本不再依赖它。

## 五、改前改后对比与留档

- **指标对比**：`compare_evals.py <基线目录> <本轮目录>` 配对两份 `eval_metrics.json`，输出逐 leg
  p15/p50/p98 差、亮度比、clip、哈希相同数、缺 leg、p98 超限、face_lr 对称性与分组汇总。先看它，
  再回到 `contact_sheet.png` 与原图目检。
- **逐像素对拍**：行为中性/回归用 `acceptance-verify/scripts/frame_diff.py`，目录按 seq 配对判
  IDENTICAL/DIFF，缺帧按失败处理；不要用 Eval 的哈希代替对拍。
- **基线入库**：走 `Test/golden/framereadback/` 的 manifest 形态；旧 manifest（`baselines[]` 嵌套）
  与新基线（顶层 `frames` 扁平 + PNG 双入库）两代并存、互不迁移，图形域新增一律走 GraphicsTest
  口径（见 [判读与验收](03-evaluation.md)「基线接管」）。
- 只看画面内容、不判差异时采一帧代表帧转 PNG 即可；证据目录命名 `temp/<任务名>[_neutral|_scene]`，
  每轮一个目录，不覆盖历史（需要时保留基线目录到收口）。
- 产物新鲜度：判读前确认二进制构建时间晚于相关源码提交（`git log -S "<布点日志串>"` 定位特性
  落地提交；运行日志缺布点行 = 二进制过旧）。
