# data

这个分支只放研究用的历史库：OKX 公共 K 线缓存、资金费率、DuckDB 和信号 parquet。

页面读取的是 main 上的 `data/backtest/*.json` 和 `reports/`。这里的文件给 Actions 续跑用，避免每次都从头下载。
