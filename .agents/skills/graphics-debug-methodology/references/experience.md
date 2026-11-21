# 图形 debug 操作经验

保留适用环境、工具版本、构建/提交与证据；重复条目可合并，被证伪条目补更正与出处。
现行顺序与证据标准见 [方法论](../SKILL.md)，管线定位线索随方法论正文。
经验不新增流程、职责、权限或验收门槛，不自动修改 SKILL.md。

## 出图与精确读值

- 浮点附件经系统看图工具转换可能产生假色（暖白转青）。工具出图用于看形态，读值须核对格式、
  通道和转换。目测“逐像素相同”曾被脚本证伪：100% 字节不同、整体暗化约 13%；定性定位与精确
  一致性需要不同证据。出处：GFXReconstruct 场景阴影案例与工具操作资料。
- RGBA16F 中间 RT 的 8bit PNG 不能反推浮点原值。光影 capture 中 b 通道目测“大片 1.0”，
  96 点网格采样实际为 0.64–0.76 连续渐变；8bit 量化与色彩映射会失真。
  证据：探针帧 656 PNG 与 sample_pixel_region；出处：`docs/temp/debug-20261022-光影异常.md`。

## 映射内存与 buffer 快照

- GFXReconstruct 对持久映射内存的逐帧 CPU 写入可能只留下陈旧 buffer 快照，不能仅凭 dump
  推出“参数不在绑定里”。按工具能力选择可靠读值，shader 探针只补具体工具缺口。
- RenderDoc 的 coherent 映射写入在已验证 capture 中有记录，不能套用上述限制。
  证据：General 工程 1080p 编辑器帧，get_draw_call_state 预览 hex、get_buffer_data hex/base64 与
  rd_probe buffer 三路逐位一致，视图矩阵行符合语义；capture 含
  `Internal::Coherent Mapped Memory Write` chunk。出处：commit `25fa9682` 的 RenderDoc 验证。
  新 capture 仍需核对工具版本、布局、事件时点和读数可靠性。

## 运行态可模仿资产/管线异常

- 批模式采集（frame_capture_batch `scene.load` 换世界）会触发 OriginShiftService 世界原点
  平移（相机离原点超阈值即全体实体搬移；换世界重反序列化复位坐标后是否再平移随新场景相机
  而异）——任何世界对齐的寻址态（RVT 页寻址 origin、世界空间 uv）跨世界不跟随就呈"采样面
  失联恒定单色"且无自愈。取证口：引擎日志 OriginShift 事件行 × 资产就绪事件流（同键资产
  全走缓存零事件 = 断点在逐帧装配面）；判读口：恒定单色 × 光照/几何正常 → 页 uv 出域
  clamp。证据：GraphicsTest 批模式 far→mid 复现，mid 帧深绿均匀 MISS、修复后同口两场景
  逐字节回基线；出处：commit `c9095369` 登记缺陷的修复轮（module-rendering 解析链节）。
  这是 RVT 解析门控的特定根因，不能直接套作其他恒定单色场景。

## 资产数据可模仿管线异常

- 帧内证据锁定输入/几何疑点后，还要核对源资产语义与索引。OBJ 案例中 vn 块 24 条拷贝与面行
  `/1../6` 引用错位使法线全水平，ndotl=0，diffuse 全灭，仅剩 ambient；表现近似光照/阴影失效。
  证据：1246 帧管线检查正常，修复后 1240 帧 lighting RT 直射亮度 0.62–1.14、影子内 ambient
  为 0.137–0.26，swapchain 影子可见。出处：`docs/temp/debug-20261022-光影异常.md`。
  这是特定资产的原因，不能直接套作其他场景根因。
- 源资产或 shader 修复后须经正常导入出口更新运行时产物。General/Library/mesh、Builtin/shaderlib
  与 Builtin/Library 的新鲜度决定运行时是否消费修改；导入和构建日志、产物与提交状态可用于核对。
  光影案例在 ShaderImporter 重导入后，1240 帧才呈现修复行为。操作见平台工具 reference。
