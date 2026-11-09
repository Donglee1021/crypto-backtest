#!/usr/bin/env bash
# 把 GitHub Pages 要发布的静态文件收进 _site。不含行情缓存和 DuckDB。
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf _site
mkdir -p _site/data/backtest _site/reports
cp index.html report.html favicon.svg .nojekyll _site/
cp -a css js _site/
if [ -d data/backtest ]; then cp -a data/backtest/. _site/data/backtest/; fi
if [ -d data/paper ]; then mkdir -p _site/data/paper; cp -a data/paper/. _site/data/paper/; fi
if [ -d reports ] && compgen -G "reports/*" > /dev/null; then
  cp -a reports/. _site/reports/
fi
echo "assembled $(find _site -type f | wc -l) files"
