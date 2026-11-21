# 工具清单

图形迭代涉及的脚本按职责分四层：**判读母本**（本 skill 的 eval/compare/score）、**规范与搜索**
（本 skill 的 tokens/metrics/search/trial）、**采集/对拍母本**（acceptance-verify）、
**内容生成工具**（Tools，产出资产或场景）。缺能力时扩对应母本并补参数说明，不在 `temp/` 复制一份
改路径——fork 会丢锚定/配对/恢复逻辑（acceptance-verify 已因 fork 收编过一轮）。

## 本 skill 母本

| 脚本 | 用途 | 入口 |
|---|---|---|
| `scripts/ag_lookdev_shading_eval.py` | ag_lookdev 全量判读面：43 leg 出图 + ROI 指标 + contact sheet + 中性 override/恢复 + git 快照 | `python .../ag_lookdev_shading_eval.py --post neutral --out temp/<任务>` |
| `scripts/compare_evals.py` | 两份 `eval_metrics.json` 逐 leg 对比（Δluma、clip、sha、缺 leg、分组汇总） | `python .../compare_evals.py <base> <final> --out <json>` |
| `scripts/design_tokens.py` | DTCG token lint/resolve/palette/check-image（规范单一真源） | `python .../design_tokens.py lint <file> --strict` |
| `scripts/image_metrics.py` | 单图确定性指标：OKLCH/ΔE2000/色板覆盖、saliency、构图、luma | `python .../image_metrics.py --image <png> --tokens <file> --out <json>` |
| `scripts/score_trial.py` | token 窗口 → 复合分/硬门/逐 leg 分解（搜索目标与判读报告） | `python .../score_trial.py --metrics <eval_metrics.json> --tokens <file> --profile day` |
| `scripts/param_search.py` | CMA-ES / TPE 闭环搜索：参数提议、缓存、trial log、best | `python .../param_search.py --spec <search.json> --out temp/<任务>/search` |
| `scripts/lookdev_trial.py` | 单 trial 适配器：应用 Volume/灯光/材质参数 → 逐 leg 出图 → 指标 → 还原 → 评分 | `python .../lookdev_trial.py --params-file <json> --out <dir> --legs <list> --tokens <file>` |

- 以上脚本都只依赖标准库；`lookdev_trial.py` 复用 `ag_lookdev_shading_eval.py` 的解码/ROI/启动
  逻辑，`image_metrics.py` 复用其 PNG 解码，`design_tokens.py` 复用其色彩数学。
- 场景/预设/口径变更时改母本常量（`SCENE`、`TORSO_ROI_REF`、`FACE_ROI_REF`、`CLOSE_LEGS` 等），
  不改临时副本。
- 需要新的判读量（新 ROI、新分组、新掩码）先加进母本并写进 `metric_definitions`，再跑基线。
- `templates/lookdev.tokens.json` 是 token 骨架；`templates/search_spec.json` 是搜索 spec 骨架。
  复制到项目/temp 后改路径，不直接在模板里跑正式轮次。

## 采集与对拍（acceptance-verify）

| 脚本 | 用途 |
|---|---|
| `frame_capture.py` | 起编辑器/运行时、订阅帧通道、按 seq 窗口落裸帧 + meta |
| `frame_diff.py` | 两目录/两 bin 逐 seq 对拍，IDENTICAL/DIFF + SUMMARY + 退出码 |
| `frame_to_png.py` | 裸帧转 PNG（纯 zlib），支持 `--crop`/`--zoom` |
| `frame_diff_mask.py` | 差异掩膜着色 + diff_px/bbox/质心量化 |

路径 `.agents/skills/acceptance-verify/scripts/`；参数与判读细节见该 skill 的 `references/capture.md`。

## 内容生成工具（Tools，产物入库前走导入）

| 脚本 | 用途 | 备注 |
|---|---|---|
| `Tools/scripts/gateway_client.py` | Gateway TCP 客户端母本（握手/懒升级/续席/延迟响应配对/会话发现） | 其它脚本 import 它，不复制握手代码 |
| `Tools/scripts/gen_lookdev_lut.py` | 参数化 grade LUT（`--preset warm|night`，`--identity` 恒等表自检） | 32bit TGA，通道序见脚本头注 |
| `Tools/scripts/gen_vc_skin_lut.py` | vc 皮肤预积分 LUT（剖面有界 + 逐行亮端归一） | 与 `gen_lookdev_lut.py` 同 TGA 口径 |
| `Tools/scripts/gen_skin_pore.py` / `gen_stocking_weave.py` | 程序化皮肤孔隙 / 丝袜织纹贴图 | 生成后走导入，不手拼 `.textureasset` |
| `Tools/scripts/vc_lookdev_clip.py` | vc_lookdev 观察 clip 键生成与标定（look-at 四元数） | `--calibrate-only` 只校验 |
| `Tools/scripts/vc_lookdev_scene.py` | vc_lookdev 场景编排 + 落盘 + 命令面验证 + 出图 | `--skip-capture` 只编排验证 |
| `Tools/scripts/vc_sanhua_prefab.py` | `SanhuaVC.prefab` 装配（vc 材质换绑，幂等） | `--stage-only` 跳过实例化验证 |
| `Tools/bake/bake_character_all.py` | 角色 MeshMap 烘焙（SDF/thickness/AO 等配方） | 长任务，后台跑；产物体积大 |

- 资产写盘一律走所属 Codec/导入出口，不手拼 `.mat`/`.scene`/`.prefab` 字段。
- 生成/重导后按 [判读与验收](03-evaluation.md) 的中性门与验收分级复验；不要顺手重采 golden。

## 取证（异常归因）

画面异常、GPU 状态、pass/绑定问题不归本 skill：Windows 用 `renderdoc-debug`，macOS 用
`gfxrecon-debug`，方法顺序见 `graphics-debug-methodology`。本 skill 的判读脚本只回答「最终画面
是否达标」，不回答「哪个 draw/pass 写错了」。
