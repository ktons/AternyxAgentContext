# 已知坑（图形迭代）

按症状查，不按时间线读。修复状态以代码与模块文档为准；本册只保留仍会影响判读的操作经验与适用条件。

## 出图与采集

- **近距/俯仰机位出图冻结（根因未定位）**：`editor.capture` 相机 z 越近越易触发，pitch 旋转同样
  触发；冻结帧跨会话逐字节相同、换探针材质也不更新。缓解：判读机位 z≤−1.5，capture 后与上帧比
  哈希（同哈希重试/挪机位），关键图用两个独立机位交叉。空帧先按「先加距试剔、再排冻结」排查。
- **`frame_capture.py` 输出目录需预创建**：否则 `cap_engine.log` 打开直接 FileNotFoundError。
- **哈希相同 ≠ 冻结**：同相机静态内容下同哈希可能只是材质 rebake 落地窗，重试即成功；跨预设/
  跨光向仍同哈希才是冻结签名。
- **探针判读三坑**：① 内容检测判据误匹配天空（`b-r>25` 命中蓝天渐变）——深色目标用纯暗判据
  （`r<130 ∧ g<130`），并先在空探针基线上校零；② 阶梯采样后相机停在最后一档，选完档必须显式
  回移，否则后续 capture 全是空背景；③ 采前核 `preset.apply` 的 `frame` 锚与相机/灯四元数读回
  （d<1e-4）。
- **读图降到 1500–2000px 长边会丢细节**：1px 级纹理/小字先 `--crop` 原生分辨率局部；不要用降采样
  后的图判细节。
- **Windows 上 `os.kill(pid, 0)` 会直接杀进程**（不是探测）：会话存活判断必须走
  `OpenProcess`+`GetExitCodeProcess`，`gateway_client.py` 已内置，不要另写。
- **编辑器退出可能回滚重导产物**：采帧会话退出后，部分重导产物与 `.importstamp` 可能被回滚，
  下次会话重新导入；不影响出图，但不要把这批产物当已入库资产。

## 材质/资产热更边界

- **手改 `.mat`（含纹理槽换绑）后 `asset.refresh` 不热更运行中场景**：refresh 只入册/重建资产侧；
  材质实例 rebake 的唯一可达出口是材质保存链（`material.set` 落盘后 `OnMaterialAssetChanged`）。
  改绑定后要采到新画面：走 `material.set` live 推送或 `scene.load` 重载，否则出图仍是旧绑定。
- **live `material.set` 探针遇材质源变更会静默失效**：源落盘触发实体重挂后，旧句柄上的后续
  `material.set` 无报错、画面不更新。自动判读/参数推送走「文件态 + 整会话轮」（改 `.mat` → refresh
  或重载会话验证），不依赖跨源变更的旧句柄。
- **`asset.refresh` 新资产入册（v29 起）**：落盘新资产 → refresh（响应 `registered` ≥1 即入册，
  连刷归 0 幂等）→ component 引其 guid，无需重启。边界：`.prefab`/`.timeline` 等无导入路由的源
  只入索引不产导入产物；**改 `.prefab` 内容（换绑材质）运行中不更新，必须重启会话重载 prefab**。
- **shader 源内容变更在下次编辑器启动扫描时自动重导**：采帧前确认本次会话已含新变体，防判读到
  旧管线。
- **clip 内容迭代 = `asset.import {force:true}`**：编辑器 `IsSourceUpToDate` 判真（refresh 恒
  changed:0），AnimationSystem 不跟踪 clipAsset 字段变更；force 重导后按产物路径分发
  `OnClipAssetChanged` 原位重建，直接 seek 即可，无需 `component.remove/add` 重挂。
- **apply 后的组件读回可作快速判据**：首读四元数与预期逐分量 d<1e-4 即到位；200ms 轮询 3s
  不收敛才判滞留。出图权威场合仍以 `captureAll` 按响应 `frame` 锚定为准。
- **prefab 内骨节点与渲染器实体同名**：gateway 按名寻址命中不定，操作前用
  `world.entity.components` 核验 `MeshRenderer`/`SkinnedMeshRenderer` 在哪侧。
- **`gateway.schema` 的 commands 是数组不是映射**：按条目 `name` 字段定位，勿按键名直查。

## 渲染/着色

- **bloom 参数打包序（历史缺陷，已修）**：`gBloomTexelAndParams` 的 z/w 曾与 shader 消费位互换
  （运行时阈值 = `bloomStrength`、强度 = `bloomThreshold`）。已按 `postprocess.hlsl` 契约对齐
  （z = 阈值 / w = 强度），默认档同取修正前运行组合（强度 0.55 / 阈值 0.9）故旧基线不变；
  `12_volume` 按语义重标定重采。判读脚本中性面仍建议双归零（对打包序不敏感），完整修法见
  [module-rendering](../../../../docs/modules/module-rendering.md) §五 缺陷 12 历史条。
- **近距头部机位角色整体消失/胸口镂空（已修，旧二进制仍会踩）**：根因是蒙皮剔除盒对已处缩放空间的
  顶点二次施加层级缩放（盒只剩渲染几何 57.57%）；修复为 palette 逐骨变换包络。**detail 机位 ≥1.3
  的规避口径只适用修复前的旧二进制**，新二进制任意近距可拍；空帧判读纪律仍有效。
- **face 的 SDF 与 shadow map 不要双乘**：face 自阴影由 SDF 面影承担，`shadowAttenuation` 恒 1；
  hair 的 alpha-test 投影是实心黑块（未接，P2）。
- **`Volume.shadowBias` 是归一化深度**：shadowOrtho ±45（90 m）下 0.0035 ≈ 31.5 cm，会吃掉接触
  自阴影；ag_lookdev 标定取 0.0003。
- **静帧哈希跨平台不可比**：基线 manifest 绑平台/后端（`win_vulkan`/`mac_vulkan`），跨平台不逐字节
  可比；本机自洽判据 = 稳态静态场景同窗多帧哈希收敛。

## 判读口径

- **只改 `.scene` YAML 对预设出图无效**：观察矩阵的工作点由 clip 键驱动（YAML 只等于 t=0 键）。
- **`--post neutral` 与 `--post scene` 的数字不同量纲**：bloom/描边会整体压暗或加暗线，跨 post 比
  覆盖率/暗部会得出错误结论；同一轮只用同一 post 比较。
- **ROI 覆盖率会把描边暗线算进去**：T7 后处理标定后旧覆盖率数字不再可比；单项覆盖数字不作门。
- **场景/预设数变了，ROI 与 contact sheet 口径先失效**：脚本只 WARN，不替作者判断。
- **效果验收不要拿 golden 当门**：golden 是 agent 正确性/回归线，见
  [判读与验收](03-evaluation.md)「验收分级」。

## 指标与搜索

- **clip seek 会覆写观察参数**：`preset.apply` 的 seek 会把 clip 键上的 exposure/ambient/灯位写回
  Volume/Transform，搜索若只在会话开头设一次参数，后面每个 leg 都会恢复成 clip 值——实测不同
  exposure 采出逐字节相同的图、score 也相同。`lookdev_trial.py` 在每次 seek 后重落参数并在 capture
  后复读，写回不一致即整 trial 失败。
- **同参数两次 trial 的 sha 可能不同**：TAA 抖动、时钟相位、资产重导窗口都会影响非静态帧；搜索前
  先连跑两次基线比 sha，不同则搜索需要重复评估取中位数（当前 `param_search.py` 无 replica，需人工
  加）。静态中性面应稳定；不稳定先查采集口径。
- **score=0 是平原**：所有 leg 落窗后复合分恒 0，搜索没有梯度；不要加拍脑袋 tie-break，改用有方向
  的 token 指标（如 `paletteCoverageMin`）或在可行域内做第二轮次级目标。
- **缓存键只有参数**：`cache/<hash>.json` 不含二进制/token/leg 集版本；换任一项就换 `cache_dir`，
  否则拿到旧口径的分数。
- **`material.set` 会落盘**：trial 的材质补丁写 `.mat`，结束时写回原值；异常中断可能留下改动，
  采后 `git status` 核对，必要时 `git checkout -- <path>`。并发 trial 会互相踩材质，必须串行。
- **灯光 az/el 是传播方向**：正负号以实测为准；先在同一机位 A/B 两个角度确认换边/换向，再放进
  搜索，否则搜的是「反方向灯」。
- **色板覆盖率对阈值敏感**：阈值 5 时高光/阴影自然漂移就会大面积不命中；先看 p90 ΔE 与
  `dominant_colors`，再调 `lookdev.paletteThreshold`。
- **saliency 在平画面/渐变背景上会贴边**：`bbox` 可能失真；看 `mass_frac`，构图判读优先用
  `subject_bbox`（特写）。
- **Windows 写 JSON 带 BOM**：PowerShell `Set-Content -Encoding utf8` 会写 BOM；脚本按
  `utf-8-sig` 读，但手工排查时注意别让其它工具拒收。

## 环境与工具

- **产物新鲜度**：判读前确认二进制构建时间晚于相关源码提交（`git log -S "<布点日志串>"` 定位特性
  落地提交；运行日志缺布点行 = 二进制过旧），本机先重建编辑器。
- **macOS `sips` 两坑**（历史脚本用；新脚本走 `frame_to_png.py`）：裸 BMP 读图链路不收；
  `sips --cropOffset 0 0` 退化为居中裁剪，非居中裁剪必须给非零偏移。
- **采帧会触碰工程内 `.mat.meta` 的 hash 字段**（编辑器资产库行为），非源内容变更；取证后按
  `git status` 还原，不算迭代改动。
