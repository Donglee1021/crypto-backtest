#!/usr/bin/env bash
# 把 DuckDB、parquet 和行情缓存推到 data 分支。沿用当前仓库的凭据，避免再克隆一次丢掉登录信息。
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
if [ ! -d data/cache ] && [ ! -f data/history.duckdb ]; then
  echo "没有可发布的历史库"
  exit 0
fi
git fetch origin data || true
wt=$(mktemp -d)
cleanup() { git worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"; }
trap cleanup EXIT
if git rev-parse --verify origin/data >/dev/null 2>&1; then
  git worktree add --force -B data "$wt" origin/data
else
  git worktree add --detach "$wt" HEAD
  git -C "$wt" checkout --orphan data
  git -C "$wt" rm -rf . >/dev/null 2>&1 || true
fi
rm -rf "$wt/data"
mkdir -p "$wt/data/history" "$wt/data/cache"
if [ -f "$root/data/history.duckdb" ]; then
  cp -a "$root/data/history.duckdb" "$wt/data/history.duckdb"
fi
if [ -f "$root/data/paper.duckdb" ]; then
  cp -a "$root/data/paper.duckdb" "$wt/data/paper.duckdb"
fi
if [ -d "$root/data/history" ]; then
  cp -a "$root/data/history/." "$wt/data/history/"
fi
if [ -d "$root/data/cache" ]; then
  cp -a "$root/data/cache/." "$wt/data/cache/"
fi
find "$wt/data/cache" -type f -size +40M -delete || true
cat > "$wt/README.md" << 'EOF'
# data

这个分支只放研究用的历史库：OKX 公共 K 线缓存、资金费率、DuckDB 和信号 parquet。

页面读取的是 main 上的 `data/backtest/*.json` 和 `reports/`。这里的文件给 Actions 续跑用，避免每次都从头下载。
EOF
git -C "$wt" add -A
if git -C "$wt" diff --cached --quiet; then
  echo "data 分支无变化"
  exit 0
fi
git -C "$wt" -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" commit -m "data: 更新行情缓存与信号库"
git -C "$wt" push origin HEAD:data
echo "pushed data branch"
