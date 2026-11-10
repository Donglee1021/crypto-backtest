(function () {
  'use strict';
  const TZ = 'Europe/Vienna';
  const INST = { BTC: 'BTC-USDT-SWAP', ETH: 'ETH-USDT-SWAP' };
  const STEPS = ['scan', 'risk', 'size', 'exec', 'log'];
  const AGENT_FOR = { scan: 'scanner', risk: 'risk', size: 'sizer', exec: 'executor', log: 'logger' };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function wait(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
  function fmt(n, digits) {
    return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: digits == null ? 2 : digits });
  }
  function uptime(iso) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return '已运行 —';
    const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (d > 0) return '已运行 ' + d + ' 天 ' + h + ' 小时 ' + m + ' 分';
    if (h > 0) return '已运行 ' + h + ' 小时 ' + m + ' 分 ' + s + ' 秒';
    return '已运行 ' + m + ' 分 ' + s + ' 秒';
  }
  function lineText(row) {
    return row.text || (row.coin + '  ' + row.action + '  原因：' + row.reason);
  }

  const shown = new WeakMap();
  function countTo(el, to, format) {
    if (!el || !Number.isFinite(to)) return;
    const from = shown.has(el) ? shown.get(el) : 0;
    shown.set(el, to);
    const id = (el._anim || 0) + 1;
    el._anim = id;
    if (Math.abs(to - from) > 1e-8) {
      el.classList.remove('flash-up', 'flash-down');
      void el.offsetWidth;
      el.classList.add(to >= from ? 'flash-up' : 'flash-down');
    }
    if (reduced || Math.abs(to - from) < 1e-8) {
      el.textContent = format(to);
      return;
    }
    const t0 = performance.now();
    (function frame(now) {
      if (el._anim !== id) return;
      const t = Math.min(1, (now - t0) / 420);
      const v = from + (to - from) * (1 - Math.pow(1 - t, 3));
      el.textContent = format(v);
      if (t < 1) requestAnimationFrame(frame);
    })(t0);
  }

  let pub = null;
  let marks = {};
  let replayToken = 0;
  let seenGenerated = '';

  function liveEquity() {
    if (!pub) return 0;
    if (!pub.positions || !pub.positions.length) return Number(pub.equity);
    let equity = Number(pub.cash);
    pub.positions.forEach(pos => {
      const px = marks[pos.coin] || pos.mark || pos.entry;
      equity += pos.side_sign * pos.qty * (px - pos.entry);
    });
    return equity;
  }

  function paintNumbers() {
    if (!pub) return;
    const equity = liveEquity();
    const start = Number(pub.start_equity || 10000);
    const diff = equity - start;
    countTo($('equityNum'), equity, v => fmt(v) + ' USDT');
    countTo($('tradeNum'), Number(pub.trades || 0), v => String(Math.round(v)));
    countTo($('winNum'), Number(pub.wins || 0), v => String(Math.round(v)));
    countTo($('lossNum'), Number(pub.losses || 0), v => String(Math.round(v)));
    countTo($('nowNum'), equity, v => fmt(v));
    const diffEl = $('diffNum');
    diffEl.className = diff >= 0 ? 'up' : 'down';
    countTo(diffEl, diff, v => (v >= 0 ? '+' : '') + fmt(v));
    $('nowNum').className = diff >= 0 ? 'up' : 'down';
    $('startNum').textContent = fmt(start, 0);
  }

  function paintAgents() {
    const root = $('agents');
    const order = ['scanner', 'risk', 'sizer', 'executor', 'logger', 'equity'];
    if (!root.dataset.ready) {
      root.innerHTML = order.map((key, i) => (
        '<div class="agent" data-agent="' + key + '"><div class="idx">' + String(i + 1).padStart(2, '0') +
        '</div><div><h2><span data-title></span> <span class="muted" data-en></span></h2><p data-line></p></div><div class="pill" data-badge></div></div>'
      )).join('');
      root.dataset.ready = '1';
    }
    order.forEach(key => {
      const agent = (pub.agents || {})[key] || {};
      const el = root.querySelector('[data-agent="' + key + '"]');
      el.querySelector('[data-title]').textContent = agent.title || '';
      el.querySelector('[data-en]').textContent = agent.en || '';
      el.querySelector('[data-line]').textContent = agent.line || '';
      const badge = el.querySelector('[data-badge]');
      badge.textContent = agent.badge || '—';
      badge.className = 'pill ' + (agent.tone || 'idle');
    });
  }

  function clearHot() {
    document.querySelectorAll('.agent').forEach(el => el.classList.remove('active', 'bad', 'good'));
    document.querySelectorAll('.node').forEach(el => el.classList.remove('on', 'hot-ok', 'hot-bad'));
  }

  function moveToken(step) {
    const rail = $('rail');
    const node = rail.querySelector('[data-step="' + step + '"]');
    const token = $('token');
    if (!node || !token) return;
    const left = node.offsetLeft + node.offsetWidth / 2;
    token.style.left = left + 'px';
  }

  let gazeStep = 'scan';
  let pointer = null;
  const finePointer = window.matchMedia('(pointer: fine)').matches;

  function setMood(mood) {
    const el = $('mascot');
    if (!el) return;
    el.classList.remove('mood-idle', 'mood-block', 'mood-exec');
    el.classList.add(mood === 'block' ? 'mood-block' : mood === 'exec' ? 'mood-exec' : 'mood-idle');
  }

  function aimEyes() {
    const face = document.querySelector('.mascot');
    const look = document.querySelector('.look');
    if (!face || !look) return;
    const box = face.getBoundingClientRect();
    const cx = box.left + box.width / 2;
    const cy = box.top + box.height / 2;
    let tx = 0;
    let ty = 0;
    if (pointer && finePointer && window.innerWidth >= 960) {
      tx = pointer.x - cx;
      ty = pointer.y - cy;
    } else {
      const node = document.querySelector('.node[data-step="' + gazeStep + '"]');
      if (!node) return;
      const r = node.getBoundingClientRect();
      tx = (r.left + r.width / 2) - cx;
      ty = (r.top + r.height / 2) - cy;
    }
    const len = Math.hypot(tx, ty) || 1;
    const dx = (tx / len) * Math.min(2.6, Math.abs(tx) / 70);
    const dy = (ty / len) * Math.min(1.6, Math.abs(ty) / 90);
    look.setAttribute('transform', 'translate(' + dx.toFixed(2) + ' ' + dy.toFixed(2) + ')');
  }

  function light(step, outcome, last) {
    clearHot();
    const node = document.querySelector('.node[data-step="' + step + '"]');
    const agent = document.querySelector('.agent[data-agent="' + AGENT_FOR[step] + '"]');
    if (node) node.classList.add('on');
    gazeStep = step;
    if (last && outcome === 'block') {
      if (node) { node.classList.remove('on'); node.classList.add('hot-bad'); }
      if (agent) agent.classList.add('active', 'bad');
      setMood('block');
    } else if (last && outcome === 'exec' && (step === 'exec' || step === 'log')) {
      const exec = document.querySelector('.node[data-step="exec"]');
      if (exec) { exec.classList.add('hot-ok'); }
      const who = document.querySelector('.agent[data-agent="executor"]');
      if (who) who.classList.add('active', 'good');
      if (step === 'log' && agent) agent.classList.add('active');
      setMood('exec');
      gazeStep = 'exec';
    } else if (agent) {
      agent.classList.add('active');
      if (outcome !== 'block') setMood('idle');
    }
    moveToken(step);
    aimEyes();
  }

  function outcomeOf(data) {
    const actions = (data.log || []).slice(-4).map(row => row.action);
    if (actions.some(name => name === '开仓' || name === '开多' || name === '开空' || name === '平仓' || name === '减仓')) return 'exec';
    if (actions.indexOf('过滤') >= 0 || data.flow_step === 'risk') return 'block';
    return 'pass';
  }

  function pathFor(data) {
    const outcome = outcomeOf(data);
    if (outcome === 'block') return { steps: ['scan', 'risk'], outcome: outcome };
    if (outcome === 'exec') return { steps: STEPS.slice(), outcome: outcome };
    const step = data.flow_step === 'equity' ? 'log' : (data.flow_step || 'log');
    const at = Math.max(0, STEPS.indexOf(step));
    const steps = STEPS.slice(0, at + 1);
    if (steps[steps.length - 1] !== 'log') steps.push('log');
    return { steps: steps, outcome: outcome };
  }

  function tradeHtml() {
    const pos = (pub.positions || [])[0];
    const closed = pub.last_closed;
    if (pos) {
      const px = marks[pos.coin] || pos.mark;
      const upnl = pos.side_sign * pos.qty * (px - pos.entry);
      return kv([
        ['品种', pos.coin + ' ' + pos.side],
        ['开仓价', fmt(pos.entry)],
        ['现价', '<span id="livePx">' + fmt(px) + '</span>' + (marks[pos.coin] ? '（OKX）' : '（上次收盘）')],
        ['止损价', fmt(pos.stop)],
        ['第一目标', pos.target1 ? fmt(pos.target1) : '—'],
        ['模拟名义', fmt(pos.notional, 0) + ' USDT'],
        ['滑点', ((pub.slippage || 0.0002) * 100).toFixed(2) + '%'],
        ['浮动盈亏', '<span id="livePnl" class="' + (upnl >= 0 ? 'up' : 'down') + '">' + (upnl >= 0 ? '+' : '') + fmt(upnl) + '</span>'],
        ['原因', pos.reason || '—']
      ]);
    }
    if (closed) {
      return '<p class="help">当前没有持仓。上一笔：</p>' + kv([
        ['品种', closed.coin + ' ' + closed.side],
        ['开仓价', fmt(closed.entry)],
        ['平仓价', fmt(closed.exit)],
        ['模拟盈亏', '<span class="' + (closed.pnl >= 0 ? 'up' : 'down') + '">' + (closed.pnl >= 0 ? '+' : '') + fmt(closed.pnl) + '</span>'],
        ['原因', closed.reason || '—']
      ]);
    }
    return '<p class="help">还没有模拟成交。这一轮没有同时满足进场和风控。</p>';
  }

  function kv(rows) {
    return '<div class="kv">' + rows.map(pair => {
      const value = String(pair[1]);
      const html = value.indexOf('<') === 0 ? value : esc(value);
      return '<span>' + esc(pair[0]) + '</span><div>' + html + '</div>';
    }).join('') + '</div>';
  }

  function slideTrade() {
    const box = $('trade');
    box.classList.remove('in');
    box.innerHTML = tradeHtml();
    if (reduced) return;
    void box.offsetWidth;
    box.classList.add('in');
  }

  function refreshOpenPnl() {
    const pos = (pub && pub.positions || [])[0];
    if (!pos) return;
    const px = marks[pos.coin] || pos.mark;
    const upnl = pos.side_sign * pos.qty * (px - pos.entry);
    const pxEl = $('livePx');
    const pnlEl = $('livePnl');
    if (pxEl) countTo(pxEl, px, v => fmt(v));
    if (pnlEl) {
      pnlEl.className = upnl >= 0 ? 'up' : 'down';
      countTo(pnlEl, upnl, v => (v >= 0 ? '+' : '') + fmt(v));
    }
    paintNumbers();
  }

  async function typeLogs(token) {
    const box = $('log');
    const rows = pub.log || [];
    const typed = rows.slice(-4);
    const older = rows.slice(0, -4);
    box.innerHTML = older.map(row => '<div class="line">' + esc(lineText(row)) + '</div>').join('');
    if (!rows.length) box.innerHTML = '<div class="line">还没有记录。</div>';
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    box.appendChild(cursor);
    for (let i = 0; i < typed.length; i++) {
      if (token !== replayToken) return;
      const row = document.createElement('div');
      row.className = 'line in';
      box.insertBefore(row, cursor);
      const text = lineText(typed[i]);
      if (reduced) {
        row.textContent = text;
      } else {
        const step = text.length > 48 ? 3 : 2;
        for (let n = step; n < text.length; n += step) {
          if (token !== replayToken) return;
          row.textContent = text.slice(0, n);
          box.scrollTop = box.scrollHeight;
          await wait(14);
        }
        row.textContent = text;
      }
      box.scrollTop = box.scrollHeight;
      await wait(reduced ? 0 : 90);
    }
    box.scrollTop = box.scrollHeight;
  }

  const TFS = {
    '1m': { okx: '1m', binance: '1m', sec: 60 },
    '5m': { okx: '5m', binance: '5m', sec: 300 },
    '15m': { okx: '15m', binance: '15m', sec: 900 },
    '1h': { okx: '1H', binance: '1h', sec: 3600 },
    '4h': { okx: '4H', binance: '4h', sec: 14400 },
    '1D': { okx: '1D', binance: '1d', sec: 86400 }
  };
  let tf = '15m';
  const kcharts = {};

  function candleBars(rows, pick) {
    return rows.map(pick).filter(bar => [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite))
      .sort((a, b) => a.time - b.time);
  }
  async function fetchOkx(coin, spec) {
    const url = 'https://www.okx.com/api/v5/market/candles?instId=' + coin + '-USDT-SWAP&bar=' + spec.okx + '&limit=200';
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('okx');
    const data = await res.json();
    if (!data || data.code !== '0' || !data.data) throw new Error('okx');
    return candleBars(data.data, row => ({
      time: Math.floor(Number(row[0]) / 1000),
      open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4])
    }));
  }
  async function fetchBinance(coin, spec) {
    const url = 'https://fapi.binance.com/fapi/v1/klines?symbol=' + coin + 'USDT&interval=' + spec.binance + '&limit=200';
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('binance');
    const data = await res.json();
    if (!Array.isArray(data)) throw new Error('binance');
    return candleBars(data, row => ({
      time: Math.floor(Number(row[0]) / 1000),
      open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4])
    }));
  }
  function markersFor(coin, sec) {
    const rows = ((pub && pub.log) || []).filter(row => row.coin === coin && (row.action === '开仓' || row.action === '开多' || row.action === '开空' || row.action === '平仓' || row.action === '减仓'));
    const seen = {};
    const out = [];
    rows.forEach(row => {
      const ts = Math.floor(Date.parse(row.ts) / 1000);
      if (!Number.isFinite(ts)) return;
      const time = Math.floor(ts / sec) * sec;
      const key = time + row.action;
      if (seen[key]) return;
      seen[key] = 1;
      const text = row.reason || '';
      const long = text.indexOf('做多') >= 0;
      const short = text.indexOf('做空') >= 0;
      if (row.action === '开仓' || row.action === '开多' || row.action === '开空') {
        out.push({
          time: time,
          position: short && !long ? 'aboveBar' : 'belowBar',
          color: short && !long ? '#d64545' : '#128a52',
          shape: short && !long ? 'arrowDown' : 'arrowUp',
          text: long ? '开多' : short ? '开空' : '开仓'
        });
      } else if (row.action === '减仓') {
        out.push({ time: time, position: 'aboveBar', color: '#e08a12', shape: 'circle', text: '减仓' });
      } else {
        out.push({ time: time, position: 'aboveBar', color: '#1d6fe8', shape: 'circle', text: '平仓' });
      }
    });
    out.sort((a, b) => a.time - b.time);
    return out;
  }
  function ensureChart(coin) {
    if (kcharts[coin] || typeof LightweightCharts === 'undefined') return kcharts[coin];
    const el = $('chart' + coin);
    if (!el) return null;
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 280,
      height: 240,
      layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#667085' },
      grid: { vertLines: { color: '#eef1f4' }, horzLines: { color: '#eef1f4' } },
      rightPriceScale: { borderColor: '#e3e7ee' },
      timeScale: { borderColor: '#e3e7ee', timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: '#c5ccd6' }, horzLine: { color: '#c5ccd6' } }
    });
    const series = chart.addCandlestickSeries({
      upColor: '#128a52', downColor: '#d64545',
      borderUpColor: '#128a52', borderDownColor: '#d64545',
      wickUpColor: '#128a52', wickDownColor: '#d64545'
    });
    kcharts[coin] = { chart: chart, series: series, bars: [], sec: 900, stopLine: null };
    return kcharts[coin];
  }
  function paintMarks(coin) {
    const slot = kcharts[coin];
    if (!slot) return;
    slot.series.setMarkers(markersFor(coin, slot.sec));
    if (slot.stopLine) {
      slot.series.removePriceLine(slot.stopLine);
      slot.stopLine = null;
    }
    const pos = ((pub && pub.positions) || []).filter(item => item.coin === coin)[0];
    if (pos && Number.isFinite(Number(pos.stop))) {
      slot.stopLine = slot.series.createPriceLine({
        price: Number(pos.stop),
        color: '#d64545',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: '止损'
      });
    }
  }
  async function loadCandles(coin, fit) {
    const spec = TFS[tf];
    const el = $('chart' + coin);
    try {
      let bars;
      try { bars = await fetchOkx(coin, spec); }
      catch (e) { bars = await fetchBinance(coin, spec); }
      if (!bars.length) throw new Error('empty');
      const slot = ensureChart(coin);
      if (!slot) return;
      slot.bars = bars;
      slot.sec = spec.sec;
      slot.series.setData(bars);
      paintMarks(coin);
      if (fit) slot.chart.timeScale().fitContent();
      slot.chart.applyOptions({ width: el.clientWidth || 280 });
    } catch (e) {
      if (el && !kcharts[coin]) el.innerHTML = '<p class="help">K 线暂时取不到。</p>';
    }
  }
  function refreshCandles(fit) {
    return Promise.all(['BTC', 'ETH'].map(coin => loadCandles(coin, fit)));
  }
  function resizeCharts() {
    ['BTC', 'ETH'].forEach(coin => {
      const slot = kcharts[coin];
      const el = $('chart' + coin);
      if (slot && el) slot.chart.applyOptions({ width: el.clientWidth || 280 });
    });
  }
  function touchCandle(coin, price) {
    const slot = kcharts[coin];
    if (!slot || !slot.bars.length || !Number.isFinite(price)) return;
    const last = slot.bars[slot.bars.length - 1];
    const bucket = Math.floor(Date.now() / 1000 / slot.sec) * slot.sec;
    if (last.time !== bucket) return;
    last.close = price;
    last.high = Math.max(last.high, price);
    last.low = Math.min(last.low, price);
    slot.series.update(last);
  }

  function callTone(name) {
    if (name === '做多') return 'tag-long';
    if (name === '做空') return 'tag-short';
    return 'tag-flat';
  }
  function callLine(label, row) {
    row = row || {};
    const score = Number(row.score || 0);
    const reasons = (row.reasons || []).slice(0, 3).join('；');
    return '<p>' + esc(label) + ' <span class="' + callTone(row.call) + '">' + esc(row.call || '—') + '</span> ' +
      esc((score > 0 ? '+' : '') + score) + (reasons ? ' · ' + esc(reasons) : '') + '</p>';
  }
  function paintCalls() {
    const box = $('calls');
    if (!box) return;
    const calls = (pub && pub.calls) || {};
    const coins = Object.keys(calls);
    if (!coins.length) {
      box.innerHTML = '';
      return;
    }
    const cards = coins.map(coin => {
      const item = calls[coin] || {};
      return '<div class="call"><b>' + esc(coin) + '</b>' + callLine('日内', item.intraday) + callLine('1 小时', item.hour) + '</div>';
    }).join('');
    const test = pub.backtest;
    let note = '';
    if (test && test.coins) {
      const bits = Object.keys(test.coins).map(coin => {
        const row = test.coins[coin];
        const wr = row.win_rate == null ? '—' : (row.win_rate * 100).toFixed(1) + '%';
        const ret = (row.return * 100).toFixed(1) + '%';
        const dd = (row.max_drawdown * 100).toFixed(1) + '%';
        return coin + ' ' + row.trades + ' 笔，胜率 ' + wr + '，收益 ' + ret + '，最大回撤 ' + dd;
      });
      note = '<p class="help">最近 ' + esc(test.days) + ' 天、每个币单独 1 万的回测：' + esc(bits.join('；')) + '。这段是亏的。</p>';
    }
    box.innerHTML = cards + note;
  }

  async function replay() {
    const token = ++replayToken;
    const plan = pathFor(pub);
    $('candidate').textContent = pub.candidate || '';
    $('honesty').textContent = pub.honesty || '';
    $('uptime').textContent = uptime(pub.started_at);
    paintAgents();
    paintNumbers();
    clearHot();
    moveToken(plan.steps[0]);
    $('trade').innerHTML = '';
    for (let i = 0; i < plan.steps.length; i++) {
      if (token !== replayToken) return;
      const last = i === plan.steps.length - 1;
      light(plan.steps[i], plan.outcome, last || (plan.outcome === 'exec' && plan.steps[i] === 'exec'));
      await wait(reduced ? 0 : 230);
    }
    if (token !== replayToken) return;
    slideTrade();
    paintCalls();
    paintMarks('BTC');
    paintMarks('ETH');
    await typeLogs(token);
  }

  function setPrice(coin, price) {
    if (!Number.isFinite(price)) return;
    const prev = marks[coin];
    marks[coin] = price;
    const el = $(coin === 'BTC' ? 'tickBTC' : 'tickETH');
    if (!el) return;
    const b = el.querySelector('b');
    countTo(b, price, v => fmt(v, price >= 1000 ? 1 : 2));
    if (Number.isFinite(prev) && prev !== price) {
      el.classList.remove('flash-up', 'flash-down');
      void el.offsetWidth;
      el.classList.add(price >= prev ? 'flash-up' : 'flash-down');
    }
    if (pub && pub.positions && pub.positions.length) refreshOpenPnl();
    touchCandle(coin, price);
  }

  function connectSocket() {
    let ws;
    let timer;
    function open() {
      try { ws = new WebSocket('wss://ws.okx.com:8443/ws/v5/public'); }
      catch (e) { return; }
      ws.onopen = function () {
        ws.send(JSON.stringify({
          op: 'subscribe',
          args: [
            { channel: 'tickers', instId: INST.BTC },
            { channel: 'tickers', instId: INST.ETH }
          ]
        }));
        timer = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 20000);
      };
      ws.onmessage = function (ev) {
        if (ev.data === 'pong') return;
        let msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        const row = (msg.data || [])[0];
        if (!row || !msg.arg) return;
        const coin = msg.arg.instId === INST.ETH ? 'ETH' : (msg.arg.instId === INST.BTC ? 'BTC' : '');
        if (coin) setPrice(coin, Number(row.last));
      };
      ws.onclose = function () {
        clearInterval(timer);
        setTimeout(open, 4000);
      };
      ws.onerror = function () { try { ws.close(); } catch (e) { /* 改走下面的轮询 */ } };
    }
    open();
  }

  async function loadJson(path) {
    const res = await fetch(path + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error(path);
    return res.json();
  }

  async function poll() {
    try {
      const raw = await loadJson('data/paper/state.json');
      const next = raw.public || raw;
      if (!next || next.generated === seenGenerated) return;
      const first = !pub;
      pub = next;
      seenGenerated = next.generated || '';
      (pub.positions || []).forEach(pos => {
        if (!Number.isFinite(marks[pos.coin])) marks[pos.coin] = pos.mark;
      });
      if (!first) {
        (['BTC', 'ETH']).forEach(coin => { if (!Number.isFinite(marks[coin]) && pub.marks) marks[coin] = pub.marks[coin]; });
      } else if (pub.marks) {
        Object.keys(pub.marks).forEach(coin => { if (!Number.isFinite(marks[coin])) marks[coin] = pub.marks[coin]; });
      }
      await replay();
    } catch (e) {
      if (!pub) $('honesty').textContent = '模拟账本还没有生成。定时任务跑过一次之后，这里会出现决定记录。';
    }
  }

  async function seedPrices() {
    await Promise.all(Object.keys(INST).map(async coin => {
      try {
        const res = await fetch('https://www.okx.com/api/v5/market/ticker?instId=' + INST[coin], { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const row = (data.data || [])[0];
        if (row) setPrice(coin, Number(row.last));
      } catch (e) { /* 行情以套接字为准 */ }
    }));
  }

  if (reduced) {
    document.querySelectorAll('#mascot animate').forEach(node => node.remove());
  }
  document.addEventListener('pointermove', ev => {
    if (!finePointer || window.innerWidth < 960) return;
    pointer = { x: ev.clientX, y: ev.clientY };
    aimEyes();
  });
  document.addEventListener('pointerleave', () => { pointer = null; aimEyes(); });
  setInterval(() => { if (pub) $('uptime').textContent = uptime(pub.started_at); }, 1000);
  connectSocket();
  seedPrices();
  poll();
  function paintCountdown() {
    const el = $('nextRound');
    if (!el) return;
    const slot = 5 * 60 * 1000;
    const left = slot - (Date.now() % slot);
    if (left < 1500) {
      el.textContent = '正在等这一轮';
      return;
    }
    const sec = Math.ceil(left / 1000);
    const m = Math.floor(sec / 60);
    const s = String(sec % 60).padStart(2, '0');
    el.textContent = '下一轮 ' + m + ':' + s;
  }
  setInterval(paintCountdown, 1000);
  paintCountdown();
  setInterval(poll, 30000);
  const tfSeg = $('tfSeg');
  if (tfSeg) {
    tfSeg.addEventListener('click', ev => {
      const button = ev.target.closest('button');
      if (!button || !TFS[button.dataset.tf]) return;
      tf = button.dataset.tf;
      tfSeg.querySelectorAll('button').forEach(item => item.classList.toggle('on', item === button));
      refreshCandles(true);
    });
  }
  refreshCandles(true);
  setInterval(() => refreshCandles(false), 20000);
  window.addEventListener('resize', () => {
    resizeCharts();
    if (!pub) return;
    const plan = pathFor(pub);
    moveToken(plan.steps[plan.steps.length - 1]);
  });
})();
