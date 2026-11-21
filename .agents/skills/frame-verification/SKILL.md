---
name: frame-verification
description: 最终帧读回验收：用改前/改后两组裸帧按 seq 配对，做逐字节对比、差异掩膜、作用域与质心量化，判定行为中性或预期画面变化。比较两次构建的画面、验证重构无回归或确认特性只作用于目标区域时使用；抓帧工具见 renderdoc-capture、gfxrecon-capture。
---

# 最终帧读回验收

职责：把“改前/改后画面是否一样、差异是否只在预期范围”变成可复现的字节级判定。
本 skill 消费采集器产出的裸帧，不绑定具体引擎或抓帧协议。

## 输入契约

采集器输出一组帧文件：

```
<out-prefix>_frame<seq>.bin      # 裸像素，4 字节/像素，行主序
<out-prefix>_meta.json           # 尺寸、格式、seq 清单
<out-prefix>_engine.log          # 可选：目标 stdout
```

`meta.json` 至少包含：

```json
{"width": 1920, "height": 1080, "format": "RGBA8", "frames": [1000, 1001]}
```

采集器可以是引擎内置帧通道、RenderDoc 导出、GFXReconstruct dump 后的转换，或其他能产出
4 字节/像素裸帧的工具。把“拉起目标 → 等待就绪 → 保存 [want, want+span) → 回收进程”留在
项目适配器；本 skill 只处理已落盘的帧。

## 验收流程

1. **定验收目标**：区分两类任务：
   - **行为中性**：重构、数据链、默认关断等不改变画面的改动，要求全部公共 seq 逐字节一致；
   - **预期变化**：特性生效、参数调整、风格变化，要求差异落在事先定义的作用域内，且作用域外保持一致。
2. **固定采集条件**：改前/改后使用相同目标、构建配置、工程/场景、相机、分辨率、视图、帧区间和
   时间源；动态场景按相位对齐，静态场景按稳态窗口取帧。
3. **核对元数据**：`frame_diff.py` 目录模式自动核对同名 `<prefix>_meta.json` 的 `width`、`height`、
   `format`、`frames`；缺 meta 或仅一侧有 meta 时跳过并注明，需人工核对。尺寸/格式不同直接判不可比；
   seq 集合不相等先查采集窗口和 `--frames` 余量，缺帧按失败处理。
4. **逐字节对比**：用 [frame_diff.py](scripts/frame_diff.py) 对目录或单帧比较。
5. **预期变化取证**：用 [frame_diff_mask.py](scripts/frame_diff_mask.py) 出差异掩膜、bbox 和质心；
   用 [frame_to_png.py](scripts/frame_to_png.py) 出裁剪放大图供目检。
6. **判定**：行为中性组全部 `IDENTICAL` 才通过；预期变化组同时满足“差异在作用域内”和
   “作用域外无差异”。存在跳过帧时结论记为 `SKIP`，不作为通过证据。判读责任在使用方：
   DIFF 帧先做同构建同帧复现，再决定是否豁免。
7. **留证**：保存两组帧的哈希 manifest、差异 JSON、掩膜/裁剪图和结论；二进制帧留在临时目录，
   哈希 manifest 入库。

## 逐字节对比

```bash
# 目录模式：按 seq 配对
python3 '<skill>/scripts/frame_diff.py' <dir_before> <dir_after>

# 单文件
python3 '<skill>/scripts/frame_diff.py' <a_frame1000.bin> <b_frame1000.bin>

# 只标注豁免 seq：该 seq 不参与判定，但本轮结论记为 SKIP
python3 '<skill>/scripts/frame_diff.py' <dir_before> <dir_after> --skip-seq 0,7
```

输出：

- 单帧：最大通道差、非零差像素数、差值分布；
- 目录：`meta` 核对结果、公共 seq 逐帧结果和 `SUMMARY: N/M IDENTICAL (compared=..., diff=[...], skipped=[...])`。

退出码：`0` PASS（全部一致）；`1` FAIL（有 DIFF、缺帧或 meta 不一致）；`2` 无公共 seq；
`3` SKIP（其余一致但存在被跳过的 seq）。

`--skip-seq` 把指定 seq 标为 `SKIPPED`：不参与字节判定，但本轮结论记为 `SKIP` 而非 PASS，
不能当作通过证据。跳过前先做同构建同帧复现，判读结论与依据写进报告；用 `SKIP` 交付时必须说明缺口。

## 差异掩膜与量化

```bash
python3 '<skill>/scripts/frame_diff_mask.py' \
  <base_frame1000.bin> <a_frame1000.bin> <b_frame1000.bin> \
  --mask-out temp/mask.png --json temp/mask.json
```

输出每个 overlay 的：

- 差异像素计数；
- 差异 bbox；
- 差异像素质心；
- 可选扫描线游程列表与众数（`--runs-y`, `--runs-thr`）。

用途：

- **作用域判定**：bbox 应落在事先定义的特征区域内；越界差异需要解释。
- **方向响应**：改变光向/输入后，质心应随之迁移；两组足迹重叠度递减是响应正确的证据。
- **带宽测量**：描边、高光带等特征用游程众数核对像素宽度。

差异判定为任通道 `|a-b| > --thr`，缺省 `0` 即逐字节不同。阈值在采集前定，避免事后给数字找解释。

## 转 PNG 与裁剪

```bash
python3 '<skill>/scripts/frame_to_png.py' <frame1000.bin> --out temp/frame1000.png
python3 '<skill>/scripts/frame_to_png.py' <frame1000.bin> --out temp/ \
  --crop 400,600,320,240 --zoom 3
```

尺寸从同目录 `<prefix>_meta.json` 读取，`format` 为 BGRA 时脚本会提示；散 bin 用 `--width/--height`。
PNG 按字节直写，BGRA 源会出现 R/B 互换；目检时按通道序解释（或加 `--swap-rb` 交换 R/B），
量化脚本按字节语义消费不受影响。

## 判定纪律

- **行为中性组按逐字节判定**：全部公共 seq 的哈希一致才算通过；“看起来没变”不作为证据。
- **预期变化组按作用域判定**：先定义特征区域和阈值，再看差异 bbox、质心和计数；作用域外的差异按回归处理。
- **缺帧即失败**：两侧 seq 集合不一致先查采集条件；公共 seq 之外不推定一致。
- **SKIP 不是 PASS**：`--skip-seq` 只把缺口显式化，结论按 SKIP 记录；交付时说明跳过原因与同构建复现结果。
- **同条件才可比**：分辨率、视图、场景、相机、时间源、构建配置任一变化，历史结果只作参考。
- **基线 manifest 入库**：逐帧 SHA-256 + 尺寸/格式/seq + 溯源（版本、工程、采集脚本、判读结论）；
  二进制帧不入库。
- **同构建复现**：DIFF 帧先在相同构建上重复采集，确认差异稳定；跨构建同帧同差才讨论豁免。

## 参考

- [特性帧证据方法](references/feature-evidence.md)
