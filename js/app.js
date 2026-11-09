(function () {
  'use strict';
  const TZ = 'Asia/Shanghai';
  const state = { summary: null, charts: null, signals: null, exits: null, liveExit: null, leverage: null, coin: 'ALL', group: 'ALL', pick: null, levCoin: 'BTC', levEntry: '页面规则' };
  const charts = {};

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function pct(x) {
    if (x == null || Number.isNaN(Number(x))) return '—';
    const n = Number(x) * 100;
    const text = (n > 0 ? '+' : '') + n.toFixed(1) + '%';
    const cls = n > 0 ? 'up' : n < 0 ? 'down' : '';
    return '<span class="' + cls + '">' + text + '</span>';
  }
  function num(x, d) {
    if (x == null || Number.isNaN(Number(x))) return '—';
    return Number(x).toFixed(d == null ? 2 : d);
  }
  function hold(h) {
    if (h == null || Number.isNaN(Number(h))) return '—';
    if (Number(h) < 48) return Number(h).toFixed(1) + ' 小时';
    return (Number(h) / 24).toFixed(1) + ' 天';
  }
  function tone(text) {
    text = text || '';
    if (text.indexOf('多') >= 0 && text.indexOf('空') < 0) return 'up';
    if (text.indexOf('空') >= 0) return 'down';
    return 'flat';
  }
  function when(iso) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: TZ, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).format(t);
  }
  async function loadJson(path) {
    const res = await fetch(path + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error(path + ' ' + res.status);
    return res.json();
  }
  function rows() {
    const list = (state.summary && state.summary.leaderboard) || [];
    return list.filter(row => (state.coin === 'ALL' || row.coin === state.coin) && (state.group === 'ALL' || row.group === state.group));
  }
  function seg(id, key) {
    $(id).addEventListener('click', ev => {
      const btn = ev.target.closest('button');
      if (!btn) return;
      state[key] = btn.dataset.v;
      Array.from($(id).querySelectorAll('button')).forEach(el => el.classList.toggle('on', el === btn));
      renderBoard();
    });
  }

  function renderOverview() {
    const box = $('overview');
    const latest = (state.summary && state.summary.latest) || {};
    const coins = ['BTC', 'ETH'];
    box.innerHTML = coins.map(coin => {
      const block = latest[coin] || {};
      const horizons = block.horizons || {};
      const order = ['超短线', '中线', '长线', '15分钟', '1小时', '4小时', '日线'];
      const chips = order.filter(name => horizons[name]).map(name => {
        const item = horizons[name];
        return '<div class="chip"><b>' + esc(name) + '</b><span class="tag ' + tone(item.verdict) + '">' + esc(item.verdict) + '</span> ' +
          esc(item.score > 0 ? '+' + item.score : item.score) + '</div>';
      }).join('');
      const price = block.price == null ? '—' : Number(block.price).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
      const fng = block.fng == null ? '—' : block.fng;
      return '<article class="card"><div class="muted">' + coin + '-USDT 永续 · 最近一根已收盘</div>' +
        '<div class="price">' + price + '</div>' +
        '<div class="muted">恐惧贪婪 ' + esc(fng) + (block.funding_points ? ' · 资金费率样本 ' + block.funding_points + ' 次' : '') + '</div>' +
        '<div class="hz">' + (chips || '<span class="muted">还没有分数</span>') + '</div></article>';
    }).join('');
  }

  function renderBoard() {
    const list = rows();
    const head = '<tr><th>币</th><th>策略</th><th>周期</th><th>参数</th><th class="n">样本内收益</th><th class="n">样本外收益</th><th class="n">样本外回撤</th><th class="n">样本外夏普</th><th class="n">胜率</th><th class="n">交易</th><th class="n">盈亏比</th><th class="n">平均持仓</th><th class="n">样本外买入持有</th><th></th></tr>';
    const body = list.map((row, i) => {
      const test = row.test || {};
      const train = row.train || {};
      const sel = state.pick === row ? ' sel' : '';
      const flag = row.overfit ? '<span class="tag warn">可能过拟合</span>' : '';
      return '<tr class="click' + sel + '" data-i="' + i + '"><td>' + esc(row.coin) + '</td><td>' + esc(row.name) + ' · ' + esc(row.variant) + '</td><td>' + esc(row.timeframe) + '</td><td>' + esc(row.params) + '</td>' +
        '<td class="n">' + pct(train.total_return) + '</td><td class="n">' + pct(test.total_return) + '</td><td class="n">' + pct(test.max_drawdown) + '</td><td class="n">' + num(test.sharpe) + '</td>' +
        '<td class="n">' + (test.win_rate == null ? '—' : (test.win_rate * 100).toFixed(1) + '%') + '</td><td class="n">' + (test.trades == null ? '—' : test.trades) + '</td>' +
        '<td class="n">' + (test.profit_factor == null ? '—' : num(test.profit_factor)) + '</td><td class="n">' + hold(test.avg_hold_hours) + '</td><td class="n">' + pct(test.bench_return) + '</td><td>' + flag + '</td></tr>';
    }).join('');
    $('board').innerHTML = head + (body || '<tr><td colspan="14">还没有回测结果。</td></tr>');
    if (!state.pick && list.length) state.pick = list.find(row => row.coin === 'BTC' && row.name === '中线' && row.variant === '默认') || list[0];
  }

  function destroy(name) {
    if (charts[name]) {
      charts[name].remove();
      delete charts[name];
    }
  }
  function baseOptions(el) {
    return {
      width: el.clientWidth,
      height: el.clientHeight || 280,
      layout: { background: { type: 'solid', color: '#161b22' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false }
    };
  }
  function mountLine(el, equity, bench) {
    destroy(el.id);
    if (!equity || equity.length < 2 || typeof LightweightCharts === 'undefined') {
      el.innerHTML = '<div class="muted">这条曲线还没有生成。</div>';
      return;
    }
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, baseOptions(el));
    const a = chart.addLineSeries({ color: '#58a6ff', lineWidth: 2, priceLineVisible: false });
    const b = chart.addLineSeries({ color: '#8b949e', lineWidth: 1, priceLineVisible: false });
    a.setData(equity);
    b.setData(bench || []);
    chart.timeScale().fitContent();
    charts[el.id] = chart;
  }
  function mountPrice(el, candles, markers) {
    destroy(el.id);
    if (!candles || !candles.length || typeof LightweightCharts === 'undefined') {
      el.innerHTML = '<div class="muted">价格图还没有生成。</div>';
      return;
    }
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, baseOptions(el));
    const series = chart.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350'
    });
    series.setData(candles);
    if (markers && markers.length) series.setMarkers(markers);
    chart.timeScale().fitContent();
    charts[el.id] = chart;
  }

  function renderDetail() {
    const row = state.pick;
    const title = $('detailTitle');
    const meta = $('detailMeta');
    const table = $('detailTable');
    if (!row) {
      title.textContent = '曲线';
      meta.textContent = '';
      table.innerHTML = '';
      return;
    }
    title.textContent = row.coin + ' · ' + row.name + ' · ' + row.variant;
    const wf = row.walk_forward;
    meta.innerHTML = esc(row.params) + ' · ' + esc(row.start) + ' 至 ' + esc(row.end) + ' · ' + row.bars + ' 根' +
      (row.train_end ? ' · 样本内到 ' + esc(row.train_end) : '') +
      (wf ? ' · 走步 ' + wf.folds + ' 段，样本外平均夏普 ' + num(wf.avg_test_sharpe) + '，平均收益 ' + (wf.avg_test_return == null ? '—' : (wf.avg_test_return * 100).toFixed(1) + '%') : '');
    function line(label, item) {
      if (!item) return '';
      return '<tr><td>' + label + '</td><td class="n">' + pct(item.total_return) + '</td><td class="n">' + pct(item.max_drawdown) + '</td><td class="n">' + num(item.sharpe) + '</td><td class="n">' +
        (item.win_rate == null ? '—' : (item.win_rate * 100).toFixed(1) + '%') + '</td><td class="n">' + (item.trades == null ? '—' : item.trades) + '</td><td class="n">' +
        (item.profit_factor == null ? '—' : num(item.profit_factor)) + '</td><td class="n">' + hold(item.avg_hold_hours) + '</td><td class="n">' + pct(item.bench_return) + '</td></tr>';
    }
    table.innerHTML = '<table><tr><th></th><th class="n">收益</th><th class="n">回撤</th><th class="n">夏普</th><th class="n">胜率</th><th class="n">交易</th><th class="n">盈亏比</th><th class="n">持仓</th><th class="n">买入持有</th></tr>' +
      line('样本内', row.train) + line('样本外', row.test) + '</table>' +
      (row.overfit ? '<p class="warn">样本内明显好于样本外，这条参数可能过拟合。</p>' : '');
    const coinCharts = state.charts && state.charts.coins && state.charts.coins[row.coin];
    const view = coinCharts && row.view && coinCharts.views[row.view];
    const fallback = coinCharts && coinCharts.views && (coinCharts.views['中线'] || Object.values(coinCharts.views)[0]);
    const shown = view || fallback;
    mountLine($('equityChart'), shown && shown.equity, shown && shown.bench);
    mountPrice($('priceChart'), coinCharts && coinCharts.candles, shown && shown.markers);
    if (!view && shown) {
      meta.innerHTML += ' · 这张净值图是「' + esc(Object.keys(coinCharts.views).find(k => coinCharts.views[k] === shown) || '中线') + '」，不是当前这一行。';
    }
  }

  function renderAlloc() {
    const box = $('alloc');
    const alloc = state.summary && state.summary.allocation;
    if (!alloc) { box.innerHTML = '<div class="muted">配置结果还没有生成。</div>'; return; }
    if (alloc.error) { box.innerHTML = '<div class="warn">' + esc(alloc.error) + '</div>'; return; }
    const cards = (alloc.portfolios || []).map(item => {
      const weights = Object.keys(item.weights || {}).map(name => {
        const w = item.weights[name];
        return '<div class="barline"><span>' + esc(name) + '</span><div class="track"><b style="width:' + Math.max(0, Math.min(100, w * 100)) + '%"></b></div><span>' + (w * 100).toFixed(1) + '%</span></div>';
      }).join('');
      const test = item.test || {};
      return '<div class="card" style="padding:10px"><b>' + esc(item.name) + '</b>' +
        '<div class="weights">' + weights + '</div>' +
        '<div class="muted">样本外年化 ' + pct(test.annualized_return) + ' · 波动 ' + pct(test.annualized_vol) + ' · 夏普 ' + num(test.sharpe) + ' · 回撤 ' + pct(test.max_drawdown) + '</div>' +
        '<div class="chart short" data-alloc="' + esc(item.name) + '"></div></div>';
    }).join('');
    box.innerHTML = '<div class="muted">' + esc(alloc.start || '') + ' 至 ' + esc(alloc.end || '') + ' · 样本内到 ' + esc(alloc.split || '') + '</div>' +
      '<p>' + esc(alloc.note || '') + '</p><div class="grid2">' + cards + '</div>';
    (alloc.portfolios || []).forEach((item, index) => {
      const el = box.querySelector('[data-alloc="' + item.name + '"]');
      if (!el) return;
      el.id = 'allocChart' + index;
      mountLine(el, item.equity, []);
    });
  }

  function renderReports() {
    const list = (state.summary && state.summary.reports) || [];
    $('reports').innerHTML = list.length
      ? list.map(item => '<a href="' + esc(item.href) + '">' + esc(item.title) + '</a>').join('')
      : '<span class="muted">报告还没有生成。</span>';
  }

  function hitLine(stat) {
    if (!stat || !stat.n) return '—';
    return (stat.hit * 100).toFixed(0) + '% / ' + stat.n + ' 次';
  }
  function renderSignals() {
    const data = state.signals || {};
    const stats = (data.stats && data.stats.by_horizon) || {};
    const bits = Object.keys(stats).map(name => name + ' 24小时命中 ' + hitLine(stats[name]['24h']) + '（1小时 ' + hitLine(stats[name]['1h']) + '，4小时 ' + hitLine(stats[name]['4h']) + '）');
    $('hitRate').textContent = bits.length ? bits.join('；') : '命中率要等信号过后才有。';
    const head = '<tr><th>时间（北京）</th><th>币</th><th>周期</th><th class="n">分数</th><th>结论</th><th class="n">价格</th><th class="n">1小时后</th><th class="n">4小时后</th><th class="n">24小时后</th></tr>';
    function cell(ret, hit) {
      if (ret == null) return '—';
      const mark = hit == null ? '' : (hit ? ' 对' : ' 错');
      const n = Number(ret);
      const cls = n > 0 ? 'up' : n < 0 ? 'down' : '';
      return '<span class="' + cls + '">' + (n > 0 ? '+' : '') + n.toFixed(2) + '%</span>' + mark;
    }
    const body = (data.items || []).map(item => '<tr><td>' + when(item.ts) + '</td><td>' + esc(item.coin) + '</td><td>' + esc(item.horizon) + '</td><td class="n">' +
      esc(item.score) + '</td><td><span class="tag ' + tone(item.verdict) + '">' + esc(item.verdict) + '</span></td><td class="n">' +
      (item.price == null ? '—' : Number(item.price).toLocaleString('zh-CN', { maximumFractionDigits: 2 })) + '</td><td class="n">' +
      cell(item.ret_1h, item.hit_1h) + '</td><td class="n">' + cell(item.ret_4h, item.hit_4h) + '</td><td class="n">' + cell(item.ret_24h, item.hit_24h) + '</td></tr>').join('');
    $('signals').innerHTML = head + (body || '<tr><td colspan="9">还没有信号。</td></tr>');
  }

  function renderNotes() {
    const notes = (state.summary && state.summary.notes) || [];
    $('notes').innerHTML = notes.map(item => '<li>' + esc(item) + '</li>').join('');
  }

  function renderAll() {
    const gen = state.summary && state.summary.generated;
    $('generated').textContent = gen ? '回测生成于 ' + when(gen) + '（北京时间）' : '';
    renderOverview();
    renderBoard();
    renderDetail();
    renderAlloc();
    renderReports();
    renderSignals();
    renderExits();
    renderLeverage();
    renderNotes();
  }

  function pctPlain(x, digits) {
    if (x == null || Number.isNaN(Number(x))) return '—';
    const n = Number(x) * 100;
    return (n > 0 ? '+' : '') + n.toFixed(digits == null ? 2 : digits) + '%';
  }
  function renderExits() {
    const data = state.exits;
    const liveSrc = (state.liveExit && state.liveExit.coins) || (data && data.live) || {};
    const coins = ['BTC', 'ETH'];
    $('exitLive').innerHTML = coins.map(coin => {
      const block = liveSrc[coin] && liveSrc[coin].live ? liveSrc[coin] : { live: liveSrc[coin] };
      const live = block.live || liveSrc[coin] || {};
      const card = (key, title) => {
        const item = live[key];
        if (!item) return '';
        return '<div class="chip"><b>假设' + title + '</b><span class="tag ' + (item.action === '平仓' ? 'down' : item.action === '减仓' ? 'warn' : 'up') + '">' +
          esc(item.action) + '</span><div>' + esc(item.reason) + '</div><div class="muted">吊灯止损 ' + item.trail_stop +
          ' · 按 25 倍粗算的 -15% 权益价 ' + item.account_stop_25x + '</div></div>';
      };
      return '<div class="card" style="margin-top:8px"><b>' + coin + '</b><div class="hz">' + card('long', '多单') + card('short', '空单') + '</div></div>';
    }).join('');
    const rows = (data && data.leaderboard) || [];
    const head = '<tr><th>币</th><th>入场</th><th>规则</th><th>参数</th><th>样本</th><th class="n">胜率</th><th class="n">平均盈利</th><th class="n">平均亏损</th><th class="n">盈亏比</th><th class="n">每笔期望</th><th class="n">盈亏因子</th><th class="n">回撤</th><th class="n">笔数</th><th></th></tr>';
    const body = rows.filter(row => row.role === '样本外' || row.role === '对照' || row.flag).map(row => {
      const o = row.oos || {};
      const flag = row.flag ? '<span class="tag warn">高胜率但期望为负</span>' : '';
      return '<tr><td>' + esc(row.coin) + '</td><td>' + esc(row.entry) + '</td><td>' + esc(row.rule) + '</td><td>' + esc(row.params) + '</td><td>' + esc(row.role) +
        '</td><td class="n">' + (o.win_rate == null ? '—' : (o.win_rate * 100).toFixed(1) + '%') + '</td><td class="n">' + pctPlain(o.avg_win) +
        '</td><td class="n">' + pctPlain(o.avg_loss) + '</td><td class="n">' + num(o.payoff) + '</td><td class="n">' + pctPlain(o.expectancy) +
        '</td><td class="n">' + num(o.profit_factor) + '</td><td class="n">' + pct(o.max_drawdown) + '</td><td class="n">' + (o.trades == null ? '—' : o.trades) +
        '</td><td>' + flag + '</td></tr>';
    }).join('');
    $('exitBoard').innerHTML = head + (body || '<tr><td colspan="14">出场结果还没有生成。</td></tr>');
    $('exitScatter').innerHTML = scatterSvg((data && data.scatter) || []);
  }
  function scatterSvg(points) {
    const rows = points.filter(p => p.win_rate != null && p.expectancy != null && p.trades >= 20);
    if (rows.length < 2) return '<div class="muted">散点还没有生成。</div>';
    const w = 720, h = 300, pad = 42;
    const xs = rows.map(p => p.win_rate);
    const ys = rows.map(p => p.expectancy);
    let x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (x1 === x0) x1 = x0 + 0.01;
    if (y1 === y0) y1 = y0 + 0.001;
    const X = v => pad + (v - x0) / (x1 - x0) * (w - pad * 2);
    const Y = v => pad + (1 - (v - y0) / (y1 - y0)) * (h - pad * 2);
    const zero = (0 >= y0 && 0 <= y1) ? '<line x1="' + pad + '" x2="' + (w - pad) + '" y1="' + Y(0) + '" y2="' + Y(0) + '" stroke="#30363d"/>' : '';
    const dots = rows.map(p => '<circle cx="' + X(p.win_rate).toFixed(1) + '" cy="' + Y(p.expectancy).toFixed(1) + '" r="' + (p.flag ? 5 : 3.5) + '" class="' + (p.flag ? 'dot-flag' : '') + '" fill="' + (p.flag ? '#d29922' : '#58a6ff') + '"><title>' +
      esc(p.coin + ' ' + p.entry + ' ' + p.rule + ' ' + p.params) + '</title></circle>').join('');
    return '<svg class="scatter" viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="胜率与每笔期望">' + zero + dots +
      '<text x="' + pad + '" y="' + (h - 8) + '" fill="#8b949e" font-size="12">胜率 ' + (x0 * 100).toFixed(0) + '%</text>' +
      '<text x="' + (w - pad - 70) + '" y="' + (h - 8) + '" fill="#8b949e" font-size="12">' + (x1 * 100).toFixed(0) + '%</text>' +
      '<text x="8" y="' + pad + '" fill="#8b949e" font-size="12">期望</text></svg>';
  }
  function levRows() {
    const data = state.leverage;
    if (!data || !data.coins || !data.coins[state.levCoin]) return null;
    const strat = data.coins[state.levCoin].strategies[state.levEntry];
    return strat || null;
  }
  function renderLeverage() {
    const data = state.leverage;
    const note = $('levNote');
    if (data && data.note && note) note.textContent = data.note;
    const body = $('levBody');
    if (!data || !data.coins) {
      body.innerHTML = '<div class="muted">杠杆模拟还没有生成。</div>';
      return;
    }
    const strat = levRows();
    const coinMeta = data.coins[state.levCoin] || {};
    const rows = (strat && strat.rows) || [];
    const metrics = [
      ['期末权益（起始 10000）', r => r.final_equity == null ? '—' : Number(r.final_equity).toLocaleString('zh-CN', { maximumFractionDigits: 0 })],
      ['最大回撤', r => pct(r.max_drawdown)],
      ['触及日内 +20% 的天数', r => r.pct_days_plus20 == null ? '—' : (r.pct_days_plus20 * 100).toFixed(1) + '%'],
      ['出现单笔 -15% 的天数', r => r.pct_days_minus15 == null ? '—' : (r.pct_days_minus15 * 100).toFixed(1) + '%'],
      ['单笔 -15% 平均每天次数', r => r.minus15_per_day == null ? '—' : String(r.minus15_per_day)],
      ['有触发的日子里平均次数', r => r.minus15_when_hit == null ? '—' : String(r.minus15_when_hit)],
      ['最长连亏笔数', r => r.longest_loss_streak == null ? '—' : String(r.longest_loss_streak)],
      ['强平次数', r => r.liquidations == null ? '—' : String(r.liquidations)],
      ['重抽样后权益减半的比例', r => r.p_drop_50 == null ? '—' : (r.p_drop_50 * 100).toFixed(1) + '%'],
      ['重抽样后权益只剩一成的比例', r => r.p_drop_90 == null ? '—' : (r.p_drop_90 * 100).toFixed(1) + '%'],
      ['减半的中位天数（没碰到则空）', r => r.median_days_to_50 == null ? '样本长度内中位数没碰到' : String(r.median_days_to_50)],
      ['成交笔数', r => r.trades == null ? '—' : String(r.trades)]
    ];
    const head = '<tr><th>项目</th>' + rows.map(r => '<th class="n">' + r.leverage + ' 倍</th>').join('') + '</tr>';
    const table = '<table>' + head + metrics.map(pair => '<tr><td>' + pair[0] + '</td>' + rows.map(r => '<td class="n">' + pair[1](r) + '</td>').join('') + '</tr>').join('') + '</table>';
    body.innerHTML = '<div class="controls"><div class="seg" id="levCoin">' +
      ['BTC', 'ETH'].map(c => '<button type="button" data-v="' + c + '"' + (c === state.levCoin ? ' class="on"' : '') + '>' + c + '</button>').join('') +
      '</div><div class="seg" id="levEntry">' +
      ['页面规则', 'RSI均值回归'].map(c => '<button type="button" data-v="' + c + '"' + (c === state.levEntry ? ' class="on"' : '') + '>' + c + '</button>').join('') +
      '</div></div><div class="muted">' + esc(state.levCoin) + ' · ' + esc(state.levEntry) +
      (strat ? ' · 各段出场：' + esc(strat.rows && strat.rows[0] ? strat.rows[0].exit_rule : strat.exit_rule) : '') +
      ' · 棒内用 ' + esc(coinMeta.intrabar || '') + '，覆盖 ' + (coinMeta.intrabar_coverage == null ? '—' : (coinMeta.intrabar_coverage * 100).toFixed(0) + '%') +
      ' 的小时。账户从 10000 起算，三段样本外首尾相接。</div><div class="scroll">' + table + '</div><div id="levChart" class="chart"></div>' +
      '<div class="legend"><span><i style="background:#ef5350"></i>25 倍</span><span><i style="background:#d29922"></i>10 倍</span><span><i style="background:#58a6ff"></i>5 倍</span><span><i style="background:#26a69a"></i>3 倍</span></div>';
    const el = $('levChart');
    destroy('levChart');
    if (!rows.length || typeof LightweightCharts === 'undefined') return;
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, baseOptions(el));
    const colors = { 25: '#ef5350', 10: '#d29922', 5: '#58a6ff', 3: '#26a69a' };
    rows.forEach(row => {
      const series = chart.addLineSeries({ color: colors[row.leverage] || '#8b949e', lineWidth: 2, priceLineVisible: false });
      series.setData(row.equity || []);
    });
    chart.timeScale().fitContent();
    charts.levChart = chart;
  }

  $('levBody').addEventListener('click', ev => {
    const btn = ev.target.closest('button');
    if (!btn || !btn.dataset.v) return;
    if (btn.parentElement.id === 'levCoin') state.levCoin = btn.dataset.v;
    if (btn.parentElement.id === 'levEntry') state.levEntry = btn.dataset.v;
    renderLeverage();
  });

  $('board').addEventListener('click', ev => {
    const tr = ev.target.closest('tr.click');
    if (!tr) return;
    state.pick = rows()[Number(tr.dataset.i)];
    renderBoard();
    renderDetail();
    $('detailCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  window.addEventListener('resize', () => renderDetail());
  seg('coinSeg', 'coin');
  seg('groupSeg', 'group');

  Promise.all([
    loadJson('data/backtest/summary.json').catch(() => null),
    loadJson('data/backtest/charts.json').catch(() => null),
    loadJson('data/backtest/signals.json').catch(() => null),
    loadJson('data/backtest/exits.json').catch(() => null),
    loadJson('data/backtest/leverage.json').catch(() => null),
    loadJson('data/backtest/exit_live.json').catch(() => null)
  ]).then(([summary, chartsData, signals, exits, leverage, liveExit]) => {
    state.summary = summary;
    state.charts = chartsData;
    state.signals = signals;
    state.exits = exits;
    state.leverage = leverage;
    state.liveExit = liveExit;
    if (!summary) {
      $('generated').textContent = '还没有回测文件。每日任务或手动运行之后，这里会填上数字。';
    }
    renderAll();
  });
})();
