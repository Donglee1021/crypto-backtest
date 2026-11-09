# BTC / ETH 回测

静态页面，看 BTC-USDT 与 ETH-USDT 永续合约的历史研究。页面地址（Pages 设成 GitHub Actions 之后）：

<https://donglee1021.github.io/crypto-backtest/>

实时看板在另一个仓库，这里不改它：<https://donglee1021.github.io/crypto-dashboard/>

全部界面是简体中文。**只做分析，不下单，不使用交易所 API 密钥，也不读取账户。** 不是投资建议，不给出仓位或杠杆。

## 页面上有什么

- 每个币最近一根已收盘 K 线的单周期分数，以及超短线、中线、长线
- 策略对照表：胜率、总收益、最大回撤、夏普、交易次数、盈亏比、平均持仓，并和买入持有比；样本内和样本外分开
- 净值曲线和带进出场标记的价格图（TradingView Lightweight Charts）
- 样本内挑出的参数，以及走步检验的样本外平均
- BTC / ETH / 稳定币的最小方差、风险平价和等权，只供对照
- QuantStats 报告在 `reports/`
- 最近 50 条信号，以及 1 小时 / 4 小时 / 24 小时之后对不对
- 减仓 / 平仓：吊灯、分段止盈、枢轴、动量衰竭、资金费率、放量、时间出场和几组组合。只报告样本外，并画出胜率和每笔期望的关系
- 杠杆模拟：按全仓、日内 +20% 收工、单笔权益 -15% 平仓，把 25 / 10 / 5 / 3 倍放在一起。这是计算结果，不是倍数建议

## 规则从哪来

单周期打分和看板 `js/indicators.js` 的 `scoreTf` 相同：EMA 排列、价格相对 EMA、MACD、RSI、KDJ、布林、放量。任务里用 Node 对同一段 K 线做对照，对不上就失败。

三个持仓周期用的是页面上的 `horizonScalp`、`overallCall`、`horizonLong`。没有可靠长历史的因子不参与，剩下的权重按页面的办法重新归一。不计入的有：新闻、清算、盘口、1 分钟动量、5 分钟技术、美股影响、入场清单、见底见顶、多空人数比。恐惧贪婪用 alternative.me 的全部历史，并滞后一天。支撑阻力只用摆动高低点，页面上的枢轴、成交密集和整数关口没有逐根重算。

信号在上一根收盘后才用，下一根开盘成交。费用按 OKX 吃单 0.05% 每边。有资金费率时按持仓方向计入；OKX 公共接口目前大约只给最近三个月，更早的日子不计资金费。

默认阈值：单周期 ±3，三个持仓周期 ±2.5，止损 1.5 倍 ATR。参数搜索还有 EMA 交叉、RSI 均值回归、布林突破、MACD，用来对照。参数只在前 70% K 线上按夏普挑选，后 30% 是样本外。走步检验再把样本切成 4 段，每次只用前面的数据重选参数。样本内明显好于样本外，页面会标成可能过拟合。

TA-Lib 另算一套 RSI、MACD、布林、ATR、STOCH，只写在小时快照里。进出场不用 TA-Lib 的默认口径。

## 工具

| 工具 | 怎么用 |
| --- | --- |
| CCXT | 拉 OKX 公共 K 线和资金费率。BTC/ETH 永续，15 分钟约一年半，1 小时和 4 小时约两年，日线更长以便做周线。 |
| Cryptofeed | 不用。它要一条长期开着的行情流，这里没有服务器。 |
| TA-Lib | 快照里的指标读数。`pip` 优先装 0.6 以上的轮子，没有轮子再在任务里编译 C 库。 |
| VectorBT | 页面规则和经典策略的回测。 |
| skfolio | BTC / ETH / 稳定币的最小方差和风险平价。只展示历史权重。 |
| QuantStats | 相对买入持有的 HTML 报告。 |
| DuckDB | 信号和回测结果。体积大的库在 `data` 分支，页面只拿小 JSON。 |
| APScheduler | 不用。定时靠 GitHub Actions 的 cron。 |
| Lightweight Charts | 页面上的净值和价格图。 |

## 多久更新

| 任务 | 时间 |
| --- | --- |
| 信号快照，并回填 1 小时 / 4 小时 / 24 小时结果 | 每小时第 8 分钟，也可手动 |
| 全量回测和 QuantStats | 每天 01:20 UTC，推送到 main 时，也可手动 |
| 部署页面 | 上面两个任务结束之后，以及页面文件推到 main 时 |

K 线缓存、DuckDB 和 parquet 在 `data` 分支，避免 main 越来越大。页面用的 JSON 在 main 的 `data/backtest/`。

## 若页面没有打开

仓库所有者打开：

**Settings → Pages → Build and deployment → Source → GitHub Actions**

保存后再运行 Actions 里的「部署页面」。

若任务推送失败并提示权限不足：

**Settings → Actions → General → Workflow permissions → Read and write permissions**

## 本地

```bash
bash scripts/install_analysis.sh
python3 analysis/parity_check.py
python3 -u -m analysis.run_backtest
python3 -m http.server 8765
```

打开 <http://127.0.0.1:8765/> 。不要直接双击 HTML，浏览器会拦住本地数据请求。
