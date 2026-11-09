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

  function light(step, outcome, last) {
    clearHot();
    const node = document.querySelector('.node[data-step="' + step + '"]');
    const agent = document.querySelector('.agent[data-agent="' + AGENT_FOR[step] + '"]');
    if (node) node.classList.add('on');
    if (last && outcome === 'block') {
      if (node) { node.classList.remove('on'); node.classList.add('hot-bad'); }
      if (agent) agent.classList.add('active', 'bad');
    } else if (last && outcome === 'exec' && (step === 'exec' || step === 'log')) {
      const exec = document.querySelector('.node[data-step="exec"]');
      if (exec) { exec.classList.add('hot-ok'); }
      const who = document.querySelector('.agent[data-agent="executor"]');
      if (who) who.classList.add('active', 'good');
      if (step === 'log' && agent) agent.classList.add('active');
    } else if (agent) {
      agent.classList.add('active');
    }
    moveToken(step);
  }

  function outcomeOf(data) {
    const actions = (data.log || []).slice(-4).map(row => row.action);
    if (actions.indexOf('开仓') >= 0 || actions.indexOf('平仓') >= 0) return 'exec';
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

  function drawCurve(animate) {
    const host = $('eqChart');
    const points = (pub.equity_curve || []).filter(p => Number.isFinite(p.value));
    if (points.length < 2) {
      host.innerHTML = '<p class="help">权益曲线会在任务多跑几次之后出现。</p>';
      return;
    }
    const w = 600;
    const h = 180;
    const pad = 16;
    let min = points[0].value;
    let max = points[0].value;
    points.forEach(p => { if (p.value < min) min = p.value; if (p.value > max) max = p.value; });
    if (max - min < 1) { min -= 1; max += 1; }
    const x = i => pad + (i / (points.length - 1)) * (w - pad * 2);
    const y = v => pad + (1 - (v - min) / (max - min)) * (h - pad * 2);
    let d = '';
    points.forEach((p, i) => { d += (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.value).toFixed(1) + ' '; });
    const area = d + 'L' + x(points.length - 1).toFixed(1) + ' ' + (h - pad) + ' L' + x(0).toFixed(1) + ' ' + (h - pad) + ' Z';
    host.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><path class="eq-area" d="' + area + '"></path><path class="eq-line" id="eqLine" d="' + d + '"></path></svg>';
    const line = $('eqLine');
    const len = line.getTotalLength();
    line.style.strokeDasharray = String(len);
    line.style.strokeDashoffset = animate && !reduced ? String(len) : '0';
    if (animate && !reduced) {
      requestAnimationFrame(() => {
        line.style.transition = 'stroke-dashoffset 1.15s ease';
        line.style.strokeDashoffset = '0';
      });
    }
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
      await wait(reduced ? 0 : 460);
    }
    if (token !== replayToken) return;
    slideTrade();
    drawCurve(true);
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

  setInterval(() => { if (pub) $('uptime').textContent = uptime(pub.started_at); }, 1000);
  connectSocket();
  seedPrices();
  poll();
  setInterval(poll, 60000);
  window.addEventListener('resize', () => {
    if (!pub) return;
    const plan = pathFor(pub);
    moveToken(plan.steps[plan.steps.length - 1]);
  });
})();
