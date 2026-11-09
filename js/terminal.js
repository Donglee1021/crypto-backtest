(function () {
  'use strict';
  const TZ = 'Europe/Vienna';
  const INST = { BTC: 'BTC-USDT-SWAP', ETH: 'ETH-USDT-SWAP' };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function when(iso) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '—';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: TZ, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).format(t);
  }
  function uptime(iso) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '—';
    const mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
    const h = Math.floor(mins / 60);
    const d = Math.floor(h / 24);
    if (d > 0) return d + ' 天 ' + (h % 24) + ' 小时';
    if (h > 0) return h + ' 小时 ' + (mins % 60) + ' 分';
    return mins + ' 分';
  }
  async function loadJson(path) {
    const res = await fetch(path + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error(path);
    return res.json();
  }
  async function livePrice(inst) {
    const res = await fetch('https://www.okx.com/api/v5/market/ticker?instId=' + inst, { cache: 'no-store' });
    if (!res.ok) throw new Error('ticker');
    const data = await res.json();
    const row = (data.data || [])[0];
    return row ? Number(row.last) : null;
  }

  function render(pub, marks) {
    const equity = liveEquity(pub, marks);
    $('honesty').textContent = pub.honesty || '';
    $('stats').innerHTML = [
      ['<i class="dot"></i>运行中', '已运行 ' + uptime(pub.started_at)],
      [Number(equity).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' USDT', '模拟权益'],
      [String(pub.trades || 0), '总成交'],
      [(pub.wins || 0) + ' / ' + (pub.losses || 0), '盈 / 亏']
    ].map(pair => '<div class="stat"><b>' + pair[0] + '</b><span>' + esc(pair[1]) + '</span></div>').join('');
    const order = ['scanner', 'risk', 'sizer', 'executor', 'logger', 'equity'];
    $('agents').innerHTML = order.map((key, i) => {
      const agent = (pub.agents || {})[key] || {};
      return '<div class="agent"><div class="idx">' + String(i + 1).padStart(2, '0') + '</div><div><h2>' + esc(agent.title) + ' <span class="muted">' + esc(agent.en || '') + '</span></h2><p>' + esc(agent.line) + '</p></div><div class="pill ' + esc(agent.tone || 'idle') + '">' + esc(agent.badge || '—') + '</div></div>';
    }).join('');
    const steps = [['scan', '扫描'], ['risk', '风控'], ['size', '仓位'], ['exec', '执行'], ['log', '记录']];
    const current = pub.flow_step === 'equity' ? 'log' : pub.flow_step;
    $('flow').innerHTML = steps.map((step, i) => {
      const node = '<div class="node' + (step[0] === current ? ' on' : '') + '">' + step[1] + '</div>';
      return node + (i < steps.length - 1 ? '<div class="arrow">→</div>' : '');
    }).join('') + '<div class="arrow"></div><div class="node on">' + esc(pub.candidate || '无') + '</div>';
    const pos = (pub.positions || [])[0];
    const closed = pub.last_closed;
    if (pos) {
      const px = marks[pos.coin] || pos.mark;
      const upnl = pos.side_sign * pos.qty * (px - pos.entry);
      $('trade').innerHTML = kv([
        ['品种', pos.coin + ' ' + pos.side],
        ['开仓价', fmt(pos.entry)],
        ['现价', fmt(px) + (marks[pos.coin] ? '（OKX）' : '（上次收盘）')],
        ['止损价', fmt(pos.stop)],
        ['模拟名义', Number(pos.notional).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) + ' USDT'],
        ['滑点', ((pub.slippage || 0.0002) * 100).toFixed(2) + '%'],
        ['浮动盈亏', money(upnl)],
        ['原因', pos.reason || '—']
      ]);
    } else if (closed) {
      $('trade').innerHTML = '<p class="help">当前没有持仓。上一笔：</p>' + kv([
        ['品种', closed.coin + ' ' + closed.side],
        ['开仓价', fmt(closed.entry)],
        ['平仓价', fmt(closed.exit)],
        ['模拟盈亏', money(closed.pnl)],
        ['原因', closed.reason || '—']
      ]);
    } else {
      $('trade').innerHTML = '<p class="help">还没有模拟成交。这一轮没有同时满足进场和风控。</p>';
    }
    const start = pub.start_equity || 10000;
    const diff = equity - start;
    $('result').innerHTML = '<div><span class="help">起始</span><div>' + start.toLocaleString('zh-CN') + '</div></div><div><span class="help">现在</span><strong class="' + (diff >= 0 ? 'up' : 'down') + '">' + equity.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '</strong></div><div><span class="help">变化</span><div class="' + (diff >= 0 ? 'up' : 'down') + '">' + (diff >= 0 ? '+' : '') + diff.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '</div></div>';
    draw(pub.equity_curve || []);
    const lines = (pub.log || []).map(row => '<div>' + esc(row.text || (when(row.ts) + '  ' + row.coin + '  ' + row.action + '  原因：' + row.reason)) + '</div>').join('');
    $('log').innerHTML = lines || '<div>还没有记录。</div>';
    $('log').scrollTop = $('log').scrollHeight;
  }

  function kv(rows) {
    return '<div class="kv">' + rows.map(pair => '<span>' + esc(pair[0]) + '</span><div>' + (String(pair[1]).indexOf('<') === 0 ? pair[1] : esc(pair[1])) + '</div>').join('') + '</div>';
  }
  function fmt(n) { return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 }); }
  function money(n) {
    const v = Number(n);
    return '<span class="' + (v >= 0 ? 'up' : 'down') + '">' + (v >= 0 ? '+' : '') + v.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '</span>';
  }
  function liveEquity(pub, marks) {
    let equity = Number(pub.cash);
    (pub.positions || []).forEach(pos => {
      const px = marks[pos.coin] || pos.mark || pos.entry;
      equity += pos.side_sign * pos.qty * (px - pos.entry);
    });
    if (!pub.positions || !pub.positions.length) return Number(pub.equity);
    return equity;
  }
  let chart;
  function draw(points) {
    const el = $('eqChart');
    if (!points || points.length < 2 || typeof LightweightCharts === 'undefined') {
      el.innerHTML = '<p class="help">权益曲线会在任务多跑几次之后出现。</p>';
      return;
    }
    el.innerHTML = '';
    if (chart) chart.remove();
    chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 320,
      height: 220,
      layout: { background: { type: 'solid', color: '#10151c' }, textColor: '#8b98a8' },
      grid: { vertLines: { color: '#1c2633' }, horzLines: { color: '#1c2633' } },
      rightPriceScale: { borderColor: '#243041' },
      timeScale: { borderColor: '#243041', timeVisible: true, secondsVisible: false }
    });
    const series = chart.addLineSeries({ color: '#7eb6ff', lineWidth: 2, priceLineVisible: false });
    const data = [];
    const seen = {};
    points.forEach(p => {
      if (seen[p.time]) return;
      seen[p.time] = 1;
      data.push({ time: p.time, value: p.value });
    });
    data.sort((a, b) => a.time - b.time);
    series.setData(data);
    chart.timeScale().fitContent();
  }

  let pub = null;
  let marks = {};
  async function boot() {
    const raw = await loadJson('data/paper/state.json');
    pub = raw.public || raw;
    (pub.positions || []).forEach(pos => { marks[pos.coin] = pos.mark; });
    render(pub, marks);
    refreshMarks();
  }
  async function refreshMarks() {
    if (!pub) return;
    const next = Object.assign({}, marks);
    await Promise.all(Object.keys(INST).map(async coin => {
      try { next[coin] = await livePrice(INST[coin]); } catch (e) { /* 用上次收盘价 */ }
    }));
    marks = next;
    render(pub, marks);
  }
  boot().catch(() => {
    $('honesty').textContent = '模拟账本还没有生成。定时任务跑过一次之后，这里会出现决定记录。';
  });
  setInterval(refreshMarks, 15000);
  window.addEventListener('resize', () => { if (pub) draw(pub.equity_curve || []); });
})();
