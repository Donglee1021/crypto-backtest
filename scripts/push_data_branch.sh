#!/usr/bin/env bash
# 把 DuckDB、parquet 和行情缓存推到 data 分支。页面用的 JSON 留在 main。
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
if [ ! -d data/cache ] && [ ! -f data/history.duckdb ]; then
  echo "没有可发布的历史库"
  exit 0
fi
remote=$(git remote get-url origin)
work=$(mktemp -d)
git clone --quiet --origin origin "$remote" "$work"
cd "$work"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
if git ls-remote --heads origin data | grep -q 'refs/heads/data$'; then
  git fetch --quiet origin data
  git checkout -B data origin/data
else
  git checkout --orphan data
  git rm -rf . >/dev/null 2>&1 || true
fi
rm -rf data
mkdir -p data/history data/cache
if [ -f "$root/data/history.duckdb" ]; then
  cp -a "$root/data/history.duckdb" data/history.duckdb
fi
if [ -d "$root/data/history" ]; then
  cp -a "$root/data/history/." data/history/
fi
if [ -d "$root/data/cache" ]; then
  cp -a "$root/data/cache/." data/cache/
fi
find data/cache -type f -size +40M -delete || true
cat > README.md << 'EOF'
# data

这个分支只放研究用的历史库：OKX 公共 K 线缓存、资金费率、DuckDB 和信号 parquet。

页面读取的是 main 上的 `data/backtest/*.json` 和 `reports/`。这里的文件给 Actions 续跑用，避免每次都从头下载。
EOF
git add -A
if git diff --cached --quiet; then
  echo "data 分支无变化"
  exit 0
fi
git commit -m "data: 更新行情缓存与信号库"
git push origin HEAD:data
echo "pushed data branch"
