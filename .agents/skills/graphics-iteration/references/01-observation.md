# 观察面与场景编排

图形迭代先固定「看什么」：同一工程、同一场景、同一机位/光向/后处理条件，才有可比的数字。
本册记录 Aternyx 当前的观察面构成与命令入口；机制契约归
[module-gateway](../../../../docs/modules/gateway/overview.md) 与
[module-rendering](../../../../docs/modules/module-rendering.md)，不在此复制。

## 一、工程、场景与默认画面

- **默认工程 = `Test/Projects/General`**（仓库内通用验证工程）。`do.sh --editor` / `--webEditor`
  不带工程参数时，存在 General 就自动带 `-p Test/Projects/General`。
- **默认启动场景 = `Assets/scenes/Lookdev.scene`**。解析顺序（`Source/Editor/EditorProjectState.cpp`）：
  `Cache/EditorCache.yaml` 的 `lastOpenedScene` → `ProjectSettings.yaml` 的 `defaultScene`
  （General 写的就是 Lookdev.scene）→ 内置缺省；显式空串 = 无默认场景。
- 当前 lookdev 迭代使用这三个场景，内容随迭代变化，以 `preset.list` 与场景 YAML 为准：
  - `Assets/scenes/ag_lookdev.scene`：角色 lookdev（ag_style 材质族 + ObservationRig 预设表 +
    光向矩阵 clip）；判读母本 `ag_lookdev_shading_eval.py` 针对它。
  - `Assets/scenes/vc_lookdev.scene`：vectorcell 家族夜景编排（ambient 低 + 夜景 grade LUT）。
  - `Assets/scenes/Lookdev.scene`：rig 子树 + `character_focus` clip（4 机位窗口 face/skin/
    stockings/skin_back × 正/左/右/背光矩阵，16s）+ 3 条 legacy 预设 + 16 条 `<机位>_<光向>` 矩阵
    预设；legacy 的 Ground/三 Cube/QuadAlbedoMap 五个体 `visible=false`，场景只留角色。

## 二、怎么看画面（按目的选）

| 路径 | 命令 | 用途 |
|---|---|---|
| 窗口模式（人眼） | `./do.sh --editor`（Windows `do.cmd`） | 日常实时看画面，默认 General |
| Web 编辑器 | `./do.sh --webEditor` | 离屏编辑器 + 浏览器，连接信息读会话文件 |
| 离屏采帧（证据） | `frame_capture.py` / `ag_lookdev_shading_eval.py` | 真实输出帧、可复现、可量化 |

窗口模式注意：编辑器 CEF UI 缺省走 Vite dev server（5173）；没有 dev server 时用 `--cef-dist`
走 WebEditor/dist 产物；纯看渲染用 `--no-cef-ui` 跳过 CEF 全链。

## 三、观察装配（ObservationRig）

观察 rig = 一个挂 `ObservationRigComponent`（预设表，随场景落盘）+ `AnimationComponent`
（观察时间轴，**playOnAwake=false、loop=false**）的实体；相机/光源/EnvironmentVolume/观察物
（MeshRenderer）统一收进 rig 子树，visibility 预设按子树内层级路径寻址。

- **子树即寻址边界**：`preset.apply` 的 visibility 条目 `{path, visible}` 按 `/` 分段从 rig 实体
  逐级下溯；子树外同名实体不命中——观察物必须挂在 rig 之下，路径写相对名（如 `Prop/Screen`）。
- **预设表结构**：每条 `{name, time, visibility[]}`；time 为 clip 秒数（seek 点）。纯 visibility
  预设（无 AnimationComponent 或无 clip 引用）合法，apply 响应 `seek:false` 标注未含 seek。
- **路径命中但无 MeshRendererComponent、路径未命中都是跳过 + 响应标注**（`applied:false`），
  不失败整单；编排侧用 `preset.list` 明细自查。
- **采帧锚定**：apply 响应带 `frame` 分发帧号（协议 v28）；帧通道等 seq ≥ frame 的帧必然包含
  seek 写回与可见性覆写效果，与 `--want-seq` 锚接。
- 寻址：`params.entity` 显式引用，或缺省 = 场景唯一挂 ObservationRigComponent 的实体
  （0/多个 kInvalidParams）——一个场景编排一个主 rig，多 rig 时显式指定。
- 常用命令：`preset.list` / `preset.apply`（一步原子施加 PreviewPause+PreviewSeek+visibility），
  批量出图 `editor.captureAll`，单次出图 `editor.capture`。

## 四、光向矩阵与 clip 标定

矩阵轮沉淀的编排模式（Lookdev.scene/character_focus 为 4 机位 × 4 光向；ag_lookdev 为 9 机位 ×
4 光向 + 3 条光照条件）：

- **每机位一个时间窗**（窗内相机键同值保持），窗内每秒一个光向键，预设 time **锚在光键点上**——
  锚落键间会把光摆稀释成斜方位（±40° 键被读成 ±25° 的实测教训）；窗间 1s 相机与光一起过渡，
  首尾帧同值便于循环。
- **clip 四元数生成**：相机/灯 look-at 键用脚本按
  [module-math](../../../../docs/modules/module-math.md) 约定推导；先用既有 clip 的已知「看向场景」
  键做标定校验，再生成新键（Lookdev 用 observation_loop t0 键标定，max|diff|≈6.6e-7）。
- **灯朝向语义**：灯实体 Transform +Z = 传播方向（光源指向，与相机 forward 同约定），
  `light_quat` 与相机 look-at 同公式直读、**不取反**（eye = 光源虚拟位、目标 = 受照体）。
  着色侧 L 的取反只在 RenderSystem 提取点，见 module-rendering 灯方向条目。
- **工作点真源**：39 预设的机位/灯光/曝光由 `Fixtures/ag_lookdev_matrix.json` 重产的 clip 键驱动，
  只改 `.scene` YAML 对 preset 出图无效（YAML 只等于 t=0 键）。改描述 JSON →
  `AternyxToolMain --animation-author <json>` 重产 `.animasset` → `asset.import {force:true}`。
- **光向效果定量判读**：背景渐变抠角色掩码 → 逐张算左右半区亮度比与角色均值亮度；判据：同行
  left/right 两张比值互镜像且方向相反、front 行最亮且左右近似对称、正面机位行 back 最暗（逆光
  入影）、背面机位行 back 最亮（背部受光）。实现轻（PNG→裸像素纯解码即可），复用判读脚本的 ROI
  统计，不按需重造。

## 五、摆相机

脚本化摆机位（确定性最好）走通用组件写：

1. **寻址**：`log.history` 查 `EditorSceneCamera: editor 相机实体已创建 (index=N gen=G)`；该实体带
   `HideInHierarchy`，`world.tree` 不输出，但按 id 直查/直写不受过滤。
2. **写位姿**：`component.set {id:{index,generation}, type:"TransformComponent",
   data:{position, rotation}}`（四元数 `[x,y,z,w]`，恒等 `[0,0,0,1]`；前向 = 局部 +Z）。
3. **不会被抢写**：SceneCameraController 只在环视/移动键按住时写 Transform，无输入帧不覆写。

交互面（本地悬停 Scene 视图或 `input.inject view:"scene"`）：**F = 聚焦**选中实体（包围球心 ×
1.5 摆距，无选中取视线与 y=0 交点）；F 后进入 **orbit**（右键拖旋转、W/S+滚轮 dolly、A/D/QE 切回
自由飞行，位姿无跳变）；默认自由飞行是 WASD/QE + 右键环视。

## 六、Gateway 命令速查（图形迭代常用面）

编辑器离屏或窗口起，改材质/改场景/导资源/摆相机全走 Gateway 命令，帧通道看结果——脚本化、可复现。
协议细节见 [module-gateway](../../../../docs/modules/gateway/overview.md)，命令 schema 以
`gateway.schema` 查询为单一事实源。

| 能力 | 命令 | 要点 |
|---|---|---|
| 编辑材质 | `material.get` / `material.set` | 管线在时实时预览推送 + 落盘 `.mat`；整组件写回 |
| 场景切换/保存 | `scene.load` / `scene.save` | play 态下 save 被核心面拒绝 |
| 实体/组件 | `entity.create/rename/delete/instantiatePrefab`、`component.add/set/remove` | `component.set` 整组件覆写，解码失败不半写 |
| 导入资源 | `asset.import`（+ `importerSettings.*`、`list/search/meta/dependencies/move/duplicate/delete`、`prefab.generate`） | 贴图重导自动通知 MaterialSystem 失效 rebake |
| 一键刷新 | `asset.refresh` | 增量重导 + 手改 `.mat` 拾取；响应 `{scanned, changed, imported[], materials[]}`，无变更空集快返回 |
| 观察预设 | `preset.list` / `preset.apply` | 见本册 §三/§四 |
| 单张出图 | `editor.capture` | `{refresh?, preset?, path?, width/height?}`；延迟响应，出图后同 id 补投终响应 |
| 批量出图 | `editor.captureAll` | 枚举 rig 预设表逐 leg 出图；`items[]` 给 preset↔path，单 leg 失败不中断 |
| 域态/播放 | `editor.state/select`、`playMode.set`、`pause.set`、`frameStep` | |
| 观察查询 | `world.tree`、`world.entity.get/components`、`log.history` | 先剥外层 `{components, unregistered}` |

- **观察 clip 生成**：`AternyxToolMain --animation-author <json> [-p <dir>] [--animation-output <dir>]`
  写 `<project>/Assets/<outputDir>/<name>.animasset` 并自动 Import；schema 见
  `docs/modules/animation/overview.md`。示例三件套 cam_orbit/light_pulse/volume_shift 在
  `Test/Projects/General/Assets/Animations/`，描述 JSON 存 `Test/Tool/AssetImporter/Animation/Fixtures/`。
- **编排脚本**统一 import `Tools/scripts/gateway_client.py`（仅标准库；显式 read 握手 / 懒升级
  mutate / 20s ping 续席 / 延迟响应按 id 配对 / requestId 单调不复用 / 未知 id WARN 丢弃 /
  会话文件发现）。不要在 `temp/` 复制握手与配对代码。

## 七、连接与席位

- 传输：TCP 24565 行 JSON（脚本直连）/ WS 24566（帧通道走 WS）；token 与实际端口读
  `build/bin/GatewaySession-<pid>.txt`；运行时入口（`AternyxMain`）读同目录
  `GatewaySessionRuntime-<pid>.txt`（消费方按前缀区分）。
- tier：只读订阅 hello 显式 `tier:"read"`；写前同连接懒升级 mutate（单写者，被占得
  `kMutateSessionExists`，不抢占）；持席每 20s 内 `gateway.ping` 续期。
- 现成消费者：`Tools/GatewayMcp/`（typed tools，含 screenshot）与 WebEditor；脚本用
  `gateway_client.py`。

## 八、出图交付

人工确认交付走本机飞书机器人脚本 `~/.zcode/workspace/default/feishu_send.py <文件> [chat_id]`
（配置 `~/.feishu_bot.json`，PNG ≤10MB 直发图片消息）；多图按判读序发送即阅读序（如矩阵按机位行
× 正/左/右/背逐张）。
