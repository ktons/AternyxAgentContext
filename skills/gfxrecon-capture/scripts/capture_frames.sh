#!/usr/bin/env bash
# gfxrecon 窗口模式抓帧包装: 固化窗口捕获的常用约束。
# 用法: capture_frames.sh <帧区间如 100-130> <输出前缀(绝对路径)> <程序(绝对路径)> [程序参数...]
#   例: capture_frames.sh 120-122 "$PWD/temp/gfxr/shadow" \
#         "$PWD/build/bin/<target>" --project "$PWD/<project>" --frames 200
# 约束 (细节见 SKILL.md):
#   - 必须窗口模式 (不带 --offscreen), capture 按 vkQueuePresentKHR 计帧
#   - 程序作为第一个参数且为绝对路径, 输出前缀也为绝对路径
#   - 帧区间必传 (缺省抓全部帧, 文件巨大)
#   - stdout 出现 "Finished recording" 才算抓取成功 (注入失败是静默的)
set -euo pipefail

FRAMES=${1:?用法: capture_frames.sh <帧区间> <输出前缀> <程序> [程序参数...]}
PREFIX=${2:?用法: capture_frames.sh <帧区间> <输出前缀> <程序> [程序参数...]}
shift 2
PROGRAM=${1:?用法: capture_frames.sh <帧区间> <输出前缀> <程序> [程序参数...]}
LOG="${TMPDIR:-/tmp}/gfxr_capture_$$.log"

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
[[ "$PROGRAM" = /* ]] || { echo "错误: 程序必须作为第一个参数且为绝对路径" >&2; exit 1; }
mkdir -p "$(dirname "$PREFIX")"
CWD="$(dirname "$PROGRAM")"

python3 "$SDK_ROOT/macOS/bin/gfxrecon-capture-vulkan.py" \
  -w "$CWD" -f "$FRAMES" -o "$PREFIX" --no-file-timestamp "$@" >"$LOG" 2>&1 || true

grep -qE "Finished recording" "$LOG" \
  || { echo "错误: 未检测到抓取完成标记, 注入可能失败 (日志: $LOG)" >&2; tail -n 20 "$LOG" >&2; exit 1; }

# --no-file-timestamp 下产物可能不带 .gfxr 后缀, 统一补齐
for f in "$PREFIX"_frames_*; do
  [[ -f "$f" && "$f" != *.gfxr ]] && mv "$f" "$f.gfxr" && echo "renamed: $(basename "$f") -> $(basename "$f").gfxr"
done
ls -la "$PREFIX"_frames_*.gfxr
