#!/usr/bin/env bash
# gfxrecon 窗口模式抓帧包装: 固化窗口捕获的常用约束。
# 用法: capture_frames.sh <帧区间如 100-130> <输出前缀(绝对路径)> [程序及参数...]
#   例: capture_frames.sh 120-122 "$PWD/temp/gfxr/shadow" \
#         "$PWD/build/bin/<target>" --project "$PWD/<project>" --frames 200
# 约束 (细节见 SKILL.md):
#   - 必须窗口模式 (不带 --offscreen), capture 按 vkQueuePresentKHR 计帧
#   - 程序与 -o 前缀必须绝对路径 (包装脚本切 CWD 前解析)
#   - 帧区间必传 (缺省抓全部帧, 文件巨大)
#   - 引擎 stdout 出现 "[gfxrecon] INFO - Finished recording" 才算抓取成功 (注入失败是静默的)
set -euo pipefail

FRAMES=${1:?用法: capture_frames.sh <帧区间> <输出前缀> [引擎参数...]}
PREFIX=${2:?用法: capture_frames.sh <帧区间> <输出前缀> [引擎参数...]}
shift 2

# SDK 路径发现式: GFXRECON_SDK_ROOT 显式指定 > ~/VulkanSDK 下遍历取最高版本; 找不到即报错
SDK_ROOT=${GFXRECON_SDK_ROOT:-}
if [[ -z "$SDK_ROOT" ]]; then
  for d in "$HOME"/VulkanSDK/*/; do
    [[ -x "$d/macOS/bin/gfxrecon-capture-vulkan.py" ]] && SDK_ROOT=${d%/}
  done
fi
[[ -n "$SDK_ROOT" && -x "$SDK_ROOT/macOS/bin/gfxrecon-capture-vulkan.py" ]] \
  || { echo "错误: 未找到可用 VulkanSDK (缺 gfxrecon-capture-vulkan.py); 可用 GFXRECON_SDK_ROOT 显式指定" >&2; exit 1; }
echo "SDK_ROOT=$SDK_ROOT"

case "$PREFIX" in /*) ;; *) echo "错误: 输出前缀必须绝对路径" >&2; exit 1;; esac
mkdir -p "$(dirname "$PREFIX")"

# 从参数里找程序 (第一个不以 - 开头的参数), 校验绝对路径
PROGRAM=""
for a in "$@"; do
  if [[ "$a" != -* ]]; then PROGRAM="$a"; break; fi
done
[[ -n "$PROGRAM" && "$PROGRAM" = /* ]] || { echo "错误: 程序必须绝对路径" >&2; exit 1; }
CWD="$(dirname "$PROGRAM")"

python3 "$SDK_ROOT/macOS/bin/gfxrecon-capture-vulkan.py" \
  -w "$CWD" -f "$FRAMES" -o "$PREFIX" --no-file-timestamp "$@" 2>&1 | tee /tmp/gfxr_capture.log | grep -E "Finished recording|Cannot find" || true

grep -q "Finished recording graphics API capture" /tmp/gfxr_capture.log \
  || { echo "错误: 未检测到抓取完成标记, 注入可能失败 (查 /tmp/gfxr_capture.log)" >&2; exit 1; }

# --no-file-timestamp 下产物可能不带 .gfxr 后缀, 统一补齐
for f in "$PREFIX"_frames_*; do
  [[ -f "$f" && "$f" != *.gfxr ]] && mv "$f" "$f.gfxr" && echo "renamed: $(basename "$f") -> $(basename "$f").gfxr"
done
ls -la "$PREFIX"_frames_*.gfxr
