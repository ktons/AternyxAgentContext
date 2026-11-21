# 工具清单

图形迭代脚本按职责分四层：**判读母本**（出图、对比、评分）、**规范与搜索**（tokens、metrics、search、trial）、
**采集与对拍**（项目采集工具与帧通道）、**内容生成**（项目资产生成工具）。缺能力时扩展对应母本并补参数说明，
一次性脚本留在 `temp/` 作为临时产物。

## 本 skill 脚本

| 脚本 | 用途 | 入口 |
|---|---|---|
| `scripts/design_tokens.py` | DTCG token lint / resolve / palette / check-image | `python .../design_tokens.py lint <file> --strict` |
| `scripts/image_metrics.py` | 单图确定性指标：OKLCH/ΔE2000/色板覆盖、saliency、构图、luma | `python .../image_metrics.py --image <png> --tokens <file> --out <json>` |
| `scripts/compare_evals.py` | 两份 `eval_metrics.json` 逐 leg 对比（Δluma、clip、sha、缺 leg、分组汇总） | `python .../compare_evals.py <base> <final> --out <json>` |
| `scripts/score_trial.py` | token 窗口 → 复合分/硬门/逐 leg 分解 | `python .../score_trial.py --metrics <json> --tokens <file> --profile day` |
| `scripts/param_search.py` | CMA-ES / TPE 闭环搜索：参数提议、缓存、trial log、best | `python .../param_search.py --spec <search.json> --out <dir>` |
| `scripts/lookdev_trial.py` | 单 trial 适配器母本：应用 Volume/灯光/材质参数 → 逐 leg 出图 → 指标 → 还原 → 评分 | `python .../lookdev_trial.py --params-file <json> --out <dir> --legs <list> --tokens <file>` |

- 以上脚本只依赖标准库；`lookdev_trial.py` 复用评估母本的解码/ROI/启动逻辑，`image_metrics.py`
  复用其 PNG 解码，`design_tokens.py` 复用其色彩数学。评估母本由项目提供，负责场景加载、leg 编排与帧采集。
- 场景、预设与口径变更时改母本常量（场景入口、ROI 参考、leg 集），并同步 `metric_definitions`。
- 新增判读量（新 ROI、新分组、新掩码）先进母本并写入 `metric_definitions`，再跑基线。
- `templates/lookdev.tokens.json` 与 `templates/search_spec.json` 是骨架；复制到项目路径后改参数，
  正式轮次从项目副本运行。

## 采集与对拍

- 采集、裸帧转 PNG、逐像素对拍使用项目提供的采集工具链；帧格式、seq 锚定、视图过滤与缺帧判定以该工具链为准。
- 采集脚本拉起会话、订阅帧通道、落裸帧与 meta；对拍脚本按 seq 配对并输出 IDENTICAL/DIFF 与汇总。
- 共享的握手、会话发现与延迟响应配对放在项目客户端母本中，由各脚本复用。

## 内容生成工具

- 资产写盘走所属 Codec/导入出口，生成后按正常导入链更新运行时产物。
- 生成长任务后台运行并保留日志；产物入库前核对导入与依赖。
- 生成或重导后按[判读与验收](03-evaluation.md)的中性门与验收分级复验。

## 取证

画面异常、GPU 状态、pass/绑定问题按 `graphics-debug-methodology` 排查；本 skill 的判读脚本回答
“最终画面是否达标”，异常归因交给图形 debug 流程。
