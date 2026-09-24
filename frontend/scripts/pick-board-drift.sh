#!/usr/bin/env bash
# pick-board-drift：资料页移植的 RealShort 文件，自移植基准以来在 origin/main 上有没有变（P4-3）。
#
#   RS_REPO=/path/to/realshort frontend/scripts/pick-board-drift.sh
#
# 操作员先在 RealShort 检出里更新 origin/main；本脚本只读，不联网、不改检出。
#
# 读 src/server/pick-board/PORTED_FROM（可用 PORTED_FROM 环境变量换一个文件）：第一行 `commit <sha>` 是移植基准，
# 其余每行一个 RealShort 路径。对这些路径执行 git diff --stat <sha>..origin/main（路径按字面，不当通配符：
# 有的路径带 (protected) 或 [resource]）。
# 退出码：0 没有变化；1 有变化（打印 diff --stat）；2 参数或仓库不对（缺 RS_REPO、PORTED_FROM 格式错、
# 基准 commit 或 origin/main 在检出里不存在）。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
list="${PORTED_FROM:-$here/../src/server/pick-board/PORTED_FROM}"

die() {
  printf 'pick-board-drift：%s\n' "$1" >&2
  exit 2
}

[ -n "${RS_REPO:-}" ] || die "没有设 RS_REPO（RealShort 检出的路径）"
[ -d "$RS_REPO" ] || die "RS_REPO 不是目录"
[ -f "$list" ] || die "找不到 PORTED_FROM：$list"

first="$(head -n 1 "$list")"
[[ "$first" =~ ^commit\ ([0-9a-f]{7,40})$ ]] || die "PORTED_FROM 第一行应是 'commit <sha>'"
base="${BASH_REMATCH[1]}"

paths=()
while IFS= read -r line || [ -n "$line" ]; do
  [ -n "$line" ] && paths+=("$line")
done < <(tail -n +2 "$list")
[ "${#paths[@]}" -gt 0 ] || die "PORTED_FROM 里没有路径"

rs_git() {
  git -C "$RS_REPO" --literal-pathspecs "$@"
}

rs_git rev-parse --verify --quiet "${base}^{commit}" >/dev/null ||
  die "RealShort 检出里没有移植基准 ${base}：先在检出里更新远端引用"
rs_git rev-parse --verify --quiet "refs/remotes/origin/main^{commit}" >/dev/null ||
  die "RealShort 检出里没有 origin/main：先在检出里更新远端引用"

stat="$(rs_git diff --stat=200 "${base}..refs/remotes/origin/main" -- "${paths[@]}")"
if [ -n "$stat" ]; then
  printf '%s\n' "$stat"
  exit 1
fi
