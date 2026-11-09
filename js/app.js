(function () {
  'use strict';
  const TZ = 'Asia/Shanghai';
  const state = { summary: null, charts: null, signals: null, coin: 'ALL', group: 'ALL', pick: null };
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
    renderNotes();
  }

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
    loadJson('data/backtest/signals.json').catch(() => null)
  ]).then(([summary, chartsData, signals]) => {
    state.summary = summary;
    state.charts = chartsData;
    state.signals = signals;
    if (!summary) {
      $('generated').textContent = '还没有回测文件。每日任务或手动运行之后，这里会填上数字。';
    }
    renderAll();
  });
})();
