/* 指标与规则打分。移植自原先的 monitor.py / server.py，计算方式保持一致。 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.Ind = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function ema(xs, n) {
    const k = 2 / (n + 1);
    const out = [];
    let e = null;
    for (let i = 0; i < xs.length; i++) {
      const x = xs[i];
      if (i < n - 1) { out.push(null); continue; }
      if (e === null) {
        let s = 0;
        for (let j = 0; j < n; j++) s += xs[j];
        e = s / n;
      } else {
        e = x * k + e * (1 - k);
      }
      out.push(e);
    }
    return out;
  }

  function sma(xs, n) {
    const out = [];
    for (let i = 0; i < xs.length; i++) {
      if (i < n - 1) { out.push(null); continue; }
      let s = 0;
      for (let j = i - n + 1; j <= i; j++) s += xs[j];
      out.push(s / n);
    }
    return out;
  }

  function rsi(xs, n) {
    n = n || 14;
    const gains = [0];
    const losses = [0];
    for (let i = 1; i < xs.length; i++) {
      const d = xs[i] - xs[i - 1];
      gains.push(Math.max(d, 0));
      losses.push(Math.max(-d, 0));
    }
    if (xs.length <= n) return xs.map(() => null);
    let ag = 0;
    let al = 0;
    for (let i = 1; i <= n; i++) { ag += gains[i]; al += losses[i]; }
    ag /= n; al /= n;
    const out = Array(n + 1).fill(null);
    out[n] = al ? 100 - 100 / (1 + ag / al) : 100;
    for (let i = n + 1; i < xs.length; i++) {
      ag = (ag * (n - 1) + gains[i]) / n;
      al = (al * (n - 1) + losses[i]) / n;
      out.push(al ? 100 - 100 / (1 + ag / al) : 100);
    }
    return out.slice(0, xs.length);
  }

  function macd(xs) {
    const e12 = ema(xs, 12);
    const e26 = ema(xs, 26);
    const line = e12.map((a, i) => (a != null && e26[i] != null ? a - e26[i] : null));
    const valid = line.filter(v => v != null);
    const sigV = ema(valid, 9);
    const pad = line.length - valid.length;
    const sig = Array(pad).fill(null).concat(sigV);
    const hist = line.map((l, i) => (l != null && sig[i] != null ? l - sig[i] : null));
    return [line, sig, hist];
  }

  function boll(xs, n, k) {
    n = n || 20; k = k == null ? 2 : k;
    const mid = sma(xs, n);
    const up = [];
    const lo = [];
    for (let i = 0; i < xs.length; i++) {
      if (mid[i] == null) { up.push(null); lo.push(null); continue; }
      const w = xs.slice(i - n + 1, i + 1);
      let ss = 0;
      for (let j = 0; j < w.length; j++) ss += (w[j] - mid[i]) ** 2;
      const sd = Math.sqrt(ss / n);
      up.push(mid[i] + k * sd);
      lo.push(mid[i] - k * sd);
    }
    return [mid, up, lo];
  }

  function kdj(h, l, c, n) {
    n = n || 9;
    let K = 50;
    let D = 50;
    const ks = [];
    const ds = [];
    const js = [];
    for (let i = 0; i < c.length; i++) {
      if (i < n - 1) { ks.push(null); ds.push(null); js.push(null); continue; }
      let hh = -Infinity;
      let ll = Infinity;
      for (let j = i - n + 1; j <= i; j++) {
        if (h[j] > hh) hh = h[j];
        if (l[j] < ll) ll = l[j];
      }
      const rsv = hh !== ll ? (c[i] - ll) / (hh - ll) * 100 : 50;
      K = 2 / 3 * K + 1 / 3 * rsv;
      D = 2 / 3 * D + 1 / 3 * K;
      ks.push(K); ds.push(D); js.push(3 * K - 2 * D);
    }
    return [ks, ds, js];
  }

  function atr(h, l, c, n) {
    n = n || 14;
    if (c.length < n) return c.map(() => null);
    const tr = [h[0] - l[0]];
    for (let i = 1; i < c.length; i++) {
      tr.push(Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
    }
    const out = Array(n - 1).fill(null);
    let a = 0;
    for (let i = 0; i < n; i++) a += tr[i];
    a /= n;
    out.push(a);
    for (let i = n; i < c.length; i++) {
      a = (a * (n - 1) + tr[i]) / n;
      out.push(a);
    }
    return out;
  }

  function swings(h, l, c, look, window) {
    look = look || 5;
    window = window || 120;
    const start = Math.max(look, c.length - window);
    const highs = [];
    const lows = [];
    for (let i = start; i < c.length - look; i++) {
      let maxH = -Infinity;
      let minL = Infinity;
      for (let j = i - look; j <= i + look; j++) {
        if (h[j] > maxH) maxH = h[j];
        if (l[j] < minL) minL = l[j];
      }
      if (h[i] === maxH) highs.push(h[i]);
      if (l[i] === minL) lows.push(l[i]);
    }
    const p = c[c.length - 1];
    let res = highs.filter(x => x > p).sort((a, b) => a - b).slice(0, 2);
    let sup = lows.filter(x => x < p).sort((a, b) => b - a).slice(0, 2);
    const sliceH = h.slice(-window);
    const sliceL = l.slice(-window);
    if (!res.length) res = [Math.max.apply(null, sliceH)];
    if (!sup.length) sup = [Math.min.apply(null, sliceL)];
    return [sup, res];
  }

  function scoreTf(ind) {
    const f = [];
    const p = ind.close, e20 = ind.ema20, e50 = ind.ema50, e200 = ind.ema200;
    if (e20 > e50 && e50 > e200) f.push(['EMA 排列', 2, 'EMA20>50>200 多头排列']);
    else if (e20 < e50 && e50 < e200) f.push(['EMA 排列', -2, 'EMA20<50<200 空头排列']);
    else f.push(['EMA 排列', 0, '均线交织，无明确排列']);
    f.push(['价格 vs EMA200', p > e200 ? 1 : -1, '价格' + (p > e200 ? '高于' : '低于') + ' EMA200']);
    f.push(['价格 vs EMA20', p > e20 ? 1 : -1, '价格' + (p > e20 ? '站上' : '跌破') + ' EMA20']);
    const m = ind.macd, s = ind.macd_signal, hst = ind.macd_hist, hprev = ind.macd_hist_prev;
    const v = (m > s ? 1 : -1) + (hst > hprev ? 1 : -1);
    const barTxt = hst > hprev
      ? (hst < 0 ? '绿柱缩短' : '红柱放大')
      : (hst > 0 ? '红柱缩短' : '绿柱放大');
    f.push(['MACD', v, 'DIF在DEA' + (m > s ? '上方' : '下方') + '，' + barTxt]);
    const r = ind.rsi;
    if (r >= 70) f.push(['RSI', -1, 'RSI ' + r.toFixed(0) + ' 超买']);
    else if (r <= 30) f.push(['RSI', 1, 'RSI ' + r.toFixed(0) + ' 超卖']);
    else if (r > 55) f.push(['RSI', 1, 'RSI ' + r.toFixed(0) + ' 偏强']);
    else if (r < 45) f.push(['RSI', -1, 'RSI ' + r.toFixed(0) + ' 偏弱']);
    else f.push(['RSI', 0, 'RSI ' + r.toFixed(0) + ' 中性']);
    const k = ind.k, d = ind.d, j = ind.j;
    if (j > 100) f.push(['KDJ', -1, 'J=' + j.toFixed(0) + ' 超买']);
    else if (j < 0) f.push(['KDJ', 1, 'J=' + j.toFixed(0) + ' 超卖']);
    else f.push(['KDJ', k > d ? 1 : -1, 'K' + (k > d ? '>' : '<') + 'D']);
    const bu = ind.boll_up, bl = ind.boll_lo;
    const pb = bu !== bl ? (p - bl) / (bu - bl) : 0.5;
    if (pb > 1) f.push(['布林带', -1, '突破上轨，短线过热']);
    else if (pb < 0) f.push(['布林带', 1, '跌破下轨，短线超跌']);
    else if (pb > 0.5) f.push(['布林带', 1, '位于中轨上方 (%B ' + pb.toFixed(2) + ')']);
    else f.push(['布林带', -1, '位于中轨下方 (%B ' + pb.toFixed(2) + ')']);
    const vr = ind.vol_ratio, up = ind.vol_bull;
    if (vr > 1.3) f.push(['成交量', up ? 1 : -1, '放量 ' + vr.toFixed(1) + 'x，' + (up ? '阳线' : '阴线') + '确认']);
    else f.push(['成交量', 0, '量能 ' + vr.toFixed(1) + 'x 均量']);
    let sc = 0;
    for (let i = 0; i < f.length; i++) sc += f[i][1];
    return [sc, f];
  }

  function label(s) {
    if (s >= 6) return '强烈做多';
    if (s >= 3) return '做多';
    if (s <= -6) return '强烈做空';
    if (s <= -3) return '做空';
    return '观望';
  }

  function sentimentAdj(fng, der) {
    const f = [];
    const v = fng;
    if (v <= 20) f.push(['恐惧贪婪指数', 1, v + ' 极度恐惧，逆向偏多']);
    else if (v >= 80) f.push(['恐惧贪婪指数', -1, v + ' 极度贪婪，逆向偏空']);
    else f.push(['恐惧贪婪指数', 0, v + '，未到极端']);
    const fr = der && der.funding;
    if (fr != null) {
      const pct = (fr * 100).toFixed(4) + '%';
      if (fr > 0.0003) f.push(['资金费率', -1, pct + ' 偏高，多头拥挤']);
      else if (fr < 0) f.push(['资金费率', 1, pct + ' 为负，空头付费']);
      else f.push(['资金费率', 0, pct + ' 正常']);
    }
    const ls = der && der.ls_ratio;
    if (ls != null) {
      const t = Number(ls).toFixed(2);
      if (ls > 2.5) f.push(['多空人数比', -1, t + ' 多头人数过多，反向偏空']);
      else if (ls < 0.8) f.push(['多空人数比', 1, t + ' 空头人数过多，反向偏多']);
      else f.push(['多空人数比', 0, t + ' 未到极端']);
    }
    return f;
  }

  function newsAdj(coin, news, nowSec) {
    const cut = nowSec - 12 * 3600;
    const rel = (news || []).filter(x => x.ts >= cut && (!x.coins || !x.coins.length || x.coins.indexOf(coin) >= 0));
    let bull = 0, bear = 0;
    for (let i = 0; i < rel.length; i++) {
      if (rel[i].tag === '利多') bull++;
      else if (rel[i].tag === '利空') bear++;
    }
    const net = bull - bear;
    const adj = net >= 6 ? 2 : net >= 3 ? 1 : net <= -6 ? -2 : net <= -3 ? -1 : 0;
    return [adj, { bull: bull, bear: bear, neutral: rel.length - bull - bear, net: net }];
  }

  function analyzeRows(rows) {
    if (!rows || rows.length < 60) return null;
    const ts = [], o = [], h = [], l = [], c = [], v = [], confirm = [];
    for (let i = 0; i < rows.length; i++) {
      const x = rows[i];
      ts.push(Number(x[0]));
      o.push(Number(x[1]));
      h.push(Number(x[2]));
      l.push(Number(x[3]));
      c.push(Number(x[4]));
      v.push(Number(x[5]));
      confirm.push(String(x[8]));
    }
    let closed = -1;
    for (let i = 0; i < confirm.length; i++) if (confirm[i] === '1') closed = i;
    if (closed < 20) closed = Math.max(0, c.length - 2);
    const e20 = ema(c, 20), e50 = ema(c, 50), e200 = ema(c, 200);
    const md = macd(c), rs = rsi(c), bb = boll(c), kd = kdj(h, l, c), at = atr(h, l, c);
    const ml = md[0], ms = md[1], mh = md[2];
    const bm = bb[0], bu = bb[1], bl = bb[2];
    const ks = kd[0], ds = kd[1], js = kd[2];
    const vavg = sma(v, 20);
    if (e200[e200.length - 1] == null || ml[ml.length - 1] == null || ms[ms.length - 1] == null ||
        mh[mh.length - 1] == null || mh[mh.length - 2] == null || rs[rs.length - 1] == null ||
        ks[ks.length - 1] == null || at[at.length - 1] == null || vavg[closed] == null || !vavg[closed]) {
      return null;
    }
    const sr = swings(h, l, c);
    const levelBook = buildLevelBook(h, l, c, v, at[at.length - 1], closed);
    const ind = {
      close: c[c.length - 1], open: o[o.length - 1],
      ema20: e20[e20.length - 1], ema50: e50[e50.length - 1], ema200: e200[e200.length - 1],
      macd: ml[ml.length - 1], macd_signal: ms[ms.length - 1],
      macd_hist: mh[mh.length - 1], macd_hist_prev: mh[mh.length - 2],
      rsi: rs[rs.length - 1], k: ks[ks.length - 1], d: ds[ds.length - 1], j: js[js.length - 1],
      boll_up: bu[bu.length - 1], boll_mid: bm[bm.length - 1], boll_lo: bl[bl.length - 1],
      atr: at[at.length - 1],
      vol_ratio: v[closed] / vavg[closed],
      vol_bull: c[closed] > o[closed]
    };
    const scored = scoreTf(ind);
    const t = ts.map(ms => Math.floor(ms / 1000));
    return {
      score: scored[0],
      signal: label(scored[0]),
      factors: scored[1],
      ind: ind,
      support: sr[0],
      resistance: sr[1],
      levels: levelBook,
      closedIndex: closed,
      stop_long: c[c.length - 1] - 1.5 * at[at.length - 1],
      stop_short: c[c.length - 1] + 1.5 * at[at.length - 1],
      chart: {
        t: t, o: o, h: h, l: l, c: c, v: v,
        ema20: e20, ema50: e50, ema200: e200,
        bu: bu, bm: bm, bl: bl,
        macd: ml, sig: ms, hist: mh,
        rsi: rs, k: ks, d: ds, j: js
      }
    };
  }

  function buildLevelBook(h, l, c, v, atrValue, closed) {
    const price = c[c.length - 1];
    const look = 5;
    const window = 120;
    const thresh = Math.max(price * 0.0015, (atrValue || 0) * 0.25);
    const candidates = [];
    function add(px, source, priority) {
      if (px == null || !isFinite(px) || px <= 0) return;
      candidates.push({ price: px, source: source, priority: priority });
    }
    const start = Math.max(look, c.length - window);
    for (let i = start; i < c.length - look; i++) {
      let maxH = -Infinity, minL = Infinity;
      for (let j = i - look; j <= i + look; j++) {
        if (h[j] > maxH) maxH = h[j];
        if (l[j] < minL) minL = l[j];
      }
      if (h[i] === maxH) add(h[i], '摆动高点', 4);
      if (l[i] === minL) add(l[i], '摆动低点', 4);
    }
    const H = h[closed], L = l[closed], C = c[closed];
    const P = (H + L + C) / 3;
    [
      ['枢轴 P', P],
      ['枢轴 R1', 2 * P - L],
      ['枢轴 S1', 2 * P - H],
      ['枢轴 R2', P + (H - L)],
      ['枢轴 S2', P - (H - L)],
      ['枢轴 R3', H + 2 * (P - L)],
      ['枢轴 S3', L - 2 * (H - P)]
    ].forEach(x => add(x[1], x[0], 3));

    const w0 = Math.max(0, c.length - window);
    let hi = -Infinity, lo = Infinity;
    for (let i = w0; i < c.length; i++) {
      if (h[i] > hi) hi = h[i];
      if (l[i] < lo) lo = l[i];
    }
    if (hi > lo) {
      const bins = 24;
      const step = (hi - lo) / bins;
      const vol = Array(bins).fill(0);
      for (let i = w0; i < c.length; i++) {
        const tp = (h[i] + l[i] + c[i]) / 3;
        let b = Math.floor((tp - lo) / step);
        if (b < 0) b = 0;
        if (b >= bins) b = bins - 1;
        vol[b] += v[i];
      }
      const nodes = [];
      for (let b = 1; b < bins - 1; b++) {
        if (vol[b] > 0 && vol[b] >= vol[b - 1] && vol[b] > vol[b + 1]) {
          nodes.push({ price: lo + (b + 0.5) * step, vol: vol[b] });
        }
      }
      nodes.sort((a, b) => b.vol - a.vol);
      nodes.slice(0, 3).forEach(n => {
        if (Math.abs(n.price - price) / price > 0.0015) add(n.price, '成交密集', 2);
      });
    }

    let major, minor;
    if (price >= 10000) { major = 1000; minor = 500; }
    else if (price >= 1000) { major = 100; minor = 50; }
    else if (price >= 100) { major = 10; minor = 5; }
    else { major = 1; minor = 0.5; }
    const n0 = Math.ceil((price * 0.96) / major);
    const n1 = Math.floor((price * 1.04) / major);
    for (let n = n0; n <= n1; n++) add(n * major, '整数关口', 1);
    const m0 = Math.ceil((price * 0.98) / minor);
    const m1 = Math.floor((price * 1.02) / minor);
    for (let n = m0; n <= m1; n++) {
      const px = n * minor;
      if (Math.abs(px / major - Math.round(px / major)) > 1e-6) add(px, '半数关口', 1);
    }

    candidates.sort((a, b) => a.price - b.price);
    const clusters = [];
    candidates.forEach(item => {
      const last = clusters[clusters.length - 1];
      if (last && item.price - last.minPrice <= thresh) {
        last.items.push(item);
        last.maxPrice = item.price;
      } else {
        clusters.push({ items: [item], minPrice: item.price, maxPrice: item.price });
      }
    });
    const outClusters = clusters.map(cl => {
      let bestPri = -1;
      cl.items.forEach(it => { if (it.priority > bestPri) bestPri = it.priority; });
      const same = cl.items.filter(it => it.priority === bestPri);
      let sum = 0;
      same.forEach(it => { sum += it.price; });
      const px = sum / same.length;
      const src = [];
      cl.items.forEach(it => { if (src.indexOf(it.source) < 0) src.push(it.source); });
      src.sort((a, b) => {
        const pa = cl.items.find(it => it.source === a).priority;
        const pb = cl.items.find(it => it.source === b).priority;
        return pb - pa;
      });
      let touches = 0;
      for (let i = w0; i < c.length; i++) {
        if (l[i] <= px + thresh && h[i] >= px - thresh) touches++;
      }
      let crossed = null;
      for (let i = Math.max(1, c.length - 3); i < c.length; i++) {
        if (c[i - 1] <= px && c[i] > px) crossed = 'up';
        else if (c[i - 1] >= px && c[i] < px) crossed = 'down';
      }
      return { price: px, sources: src, touches: touches, crossed: crossed };
    });

    function ensureExtreme(list, px, source) {
      if (list.some(cl => Math.abs(cl.price - px) <= thresh)) return;
      list.push({ price: px, sources: [source], touches: 1, crossed: null });
    }
    const below = outClusters.filter(cl => cl.price < price);
    const above = outClusters.filter(cl => cl.price > price);
    if (!below.length) ensureExtreme(outClusters, Math.min.apply(null, l.slice(-window)), '区间低点');
    if (!above.length) ensureExtreme(outClusters, Math.max.apply(null, h.slice(-window)), '区间高点');

    const rStart = Math.max(0, closed - 19);
    let rh = -Infinity, rl = Infinity;
    for (let i = rStart; i <= closed; i++) {
      if (h[i] > rh) rh = h[i];
      if (l[i] < rl) rl = l[i];
    }
    return {
      clusters: outClusters,
      atr: atrValue,
      rangeHeight: rh - rl,
      prevClose: c[closed],
      thresh: thresh
    };
  }

  function presentLevels(book, price) {
    const empty = { supports: [], resistances: [], testing: [], rangeHeight: 0, atr: null };
    if (!book || !book.clusters || price == null || !isFinite(price)) return empty;
    const atrValue = book.atr;
    const rangeHeight = book.rangeHeight;
    const thresh = book.thresh || price * 0.0015;
    const band = price * 0.0005;
    const supports = [];
    const resistances = [];
    const testing = [];
    book.clusters.forEach(cl => {
      const distPct = (cl.price - price) / price * 100;
      const gap = Math.abs(price - cl.price) / cl.price;
      let breakFlag = null;
      if (cl.crossed === 'up' && price > cl.price && gap <= 0.015) breakFlag = '刚刚突破';
      else if (cl.crossed === 'down' && price < cl.price && gap <= 0.015) breakFlag = '刚刚跌破';
      else if (book.prevClose != null && gap <= 0.015) {
        if (book.prevClose <= cl.price && price > cl.price) breakFlag = '刚刚突破';
        else if (book.prevClose >= cl.price && price < cl.price) breakFlag = '刚刚跌破';
      }
      const row = {
        price: cl.price,
        sources: cl.sources,
        touches: cl.touches,
        distPct: distPct,
        proximity: Math.abs(distPct) <= 0.5,
        breakFlag: breakFlag
      };
      if (cl.price < price - band) supports.push(row);
      else if (cl.price > price + band) resistances.push(row);
      else testing.push(row);
    });
    supports.sort((a, b) => b.price - a.price);
    resistances.sort((a, b) => a.price - b.price);

    function decorate(list, side) {
      return list.slice(0, 3).map((cl, idx) => {
        const id = (side === 'support' ? 'S' : 'R') + (idx + 1);
        const next = list[idx + 1] || null;
        let measured = null;
        let atrTarget = null;
        if (side === 'support') {
          const mpx = cl.price - rangeHeight;
          if (rangeHeight > 0 && mpx > 0 && mpx < cl.price - thresh) measured = { price: mpx, distPct: (mpx - price) / price * 100 };
          const apx = cl.price - 1.5 * atrValue;
          if (atrValue > 0 && apx > 0 && apx < cl.price) atrTarget = { price: apx, distPct: (apx - price) / price * 100 };
        } else {
          const mpx = cl.price + rangeHeight;
          if (rangeHeight > 0 && mpx > cl.price + thresh) measured = { price: mpx, distPct: (mpx - price) / price * 100 };
          const apx = cl.price + 1.5 * atrValue;
          if (atrValue > 0 && apx > cl.price) atrTarget = { price: apx, distPct: (apx - price) / price * 100 };
        }
        let nextLabel = null;
        if (next) nextLabel = idx < 2 ? ((side === 'support' ? 'S' : 'R') + (idx + 2)) : (side === 'support' ? '更低一档' : '更高一档');
        return {
          id: id,
          side: side,
          price: cl.price,
          distPct: cl.distPct,
          sources: cl.sources,
          touches: cl.touches,
          nearest: idx === 0,
          proximity: cl.proximity,
          breakFlag: cl.breakFlag,
          next: next ? { price: next.price, distPct: (next.price - price) / price * 100, label: nextLabel } : null,
          measured: measured,
          atrTarget: atrTarget
        };
      });
    }
    return {
      supports: decorate(supports, 'support'),
      resistances: decorate(resistances, 'resistance'),
      testing: testing,
      rangeHeight: rangeHeight,
      atr: atrValue
    };
  }

  function swingIndexes(h, l, look, startAt) {
    const highs = [], lows = [];
    const start = Math.max(look, startAt);
    for (let i = start; i < h.length - look; i++) {
      let maxH = -Infinity, minL = Infinity;
      for (let j = i - look; j <= i + look; j++) {
        if (h[j] > maxH) maxH = h[j];
        if (l[j] < minL) minL = l[j];
      }
      if (h[i] === maxH) highs.push(i);
      if (l[i] === minL) lows.push(i);
    }
    return { highs: highs, lows: lows };
  }

  function bullishDivergence(idxs, series, priceAt, n) {
    const pts = idxs.filter(i => i >= n - 70 && series[i] != null);
    if (pts.length < 2) return false;
    const i2 = pts[pts.length - 1], i1 = pts[pts.length - 2];
    if (n - 1 - i2 > 30) return false;
    return priceAt(i2) < priceAt(i1) && series[i2] > series[i1];
  }

  function bearishDivergence(idxs, series, priceAt, n) {
    const pts = idxs.filter(i => i >= n - 70 && series[i] != null);
    if (pts.length < 2) return false;
    const i2 = pts[pts.length - 1], i1 = pts[pts.length - 2];
    if (n - 1 - i2 > 30) return false;
    return priceAt(i2) > priceAt(i1) && series[i2] < series[i1];
  }

  function structureShift(h, l, c, dir) {
    const sw = swingIndexes(h, l, 5, Math.max(0, c.length - 90));
    const n = c.length;
    function judge(i1, i2, laterExtreme, pivotOf) {
      if (n - 1 - i2 > 20) return { state: 'none' };
      let pivot = pivotOf(i1, i2);
      let brokeAt = -1;
      for (let i = i2 + 1; i < n; i++) {
        if ((dir === 'up' && c[i] > pivot) || (dir === 'down' && c[i] < pivot)) { brokeAt = i; break; }
      }
      if (brokeAt >= 0 && n - 1 - brokeAt <= 10) return { state: 'confirmed' };
      if (brokeAt < 0) return { state: 'forming' };
      return { state: 'none' };
    }
    if (dir === 'up') {
      const lows = sw.lows.filter(i => i >= n - 80);
      if (lows.length < 2) return { state: 'none' };
      const i1 = lows[lows.length - 2], i2 = lows[lows.length - 1];
      if (!(l[i2] > l[i1])) return { state: 'none' };
      return judge(i1, i2, l[i2], (a, b) => {
        let pivot = -Infinity;
        for (let i = a; i <= b; i++) if (h[i] > pivot) pivot = h[i];
        return pivot;
      });
    }
    const highs = sw.highs.filter(i => i >= n - 80);
    if (highs.length < 2) return { state: 'none' };
    const i1 = highs[highs.length - 2], i2 = highs[highs.length - 1];
    if (!(h[i2] < h[i1])) return { state: 'none' };
    return judge(i1, i2, h[i2], (a, b) => {
      let pivot = Infinity;
      for (let i = a; i <= b; i++) if (l[i] < pivot) pivot = l[i];
      return pivot;
    });
  }

  function wickShape(o, h, l, c, dir) {
    const range = h - l;
    if (!(range > 0)) return false;
    const body = Math.abs(c - o);
    if (dir === 'lower') {
      const w = Math.min(o, c) - l;
      return w >= body * 2 && w >= range * 0.5;
    }
    const w = h - Math.max(o, c);
    return w >= body * 2 && w >= range * 0.5;
  }

  function bandReclaim(ch, side) {
    const band = side === 'lower' ? ch.bl : ch.bu;
    const n = ch.c.length;
    if (band[n - 1] == null) return false;
    const insideNow = side === 'lower' ? ch.c[n - 1] > band[n - 1] : ch.c[n - 1] < band[n - 1];
    if (!insideNow) return false;
    for (let i = Math.max(0, n - 8); i < n - 1; i++) {
      if (band[i] == null) continue;
      const outside = side === 'lower' ? ch.c[i] <= band[i] : ch.c[i] >= band[i];
      if (!outside) continue;
      for (let k = i + 1; k < Math.min(n, i + 4); k++) {
        if (band[k] == null) continue;
        const back = side === 'lower' ? ch.c[k] > band[k] : ch.c[k] < band[k];
        if (back) return true;
      }
    }
    return false;
  }

  function tfName(tf) { return tf === '15m' ? '15分钟' : tf === '1H' ? '1小时' : tf === '4H' ? '4小时' : '日线'; }

  function levelPool(tfMap, price, side) {
    const out = [];
    ['1H', '4H'].forEach(tf => {
      const an = tfMap[tf];
      if (!an || !an.levels) return;
      const view = presentLevels(an.levels, price);
      const arr = side === 'long' ? view.supports : view.resistances;
      arr.forEach(lv => out.push({
        price: lv.price, id: lv.id, tf: tfName(tf), distPct: lv.distPct, breakFlag: lv.breakFlag
      }));
    });
    out.sort((a, b) => side === 'long' ? b.price - a.price : a.price - b.price);
    return out;
  }

  function chooseInvalidation(pool, price, side) {
    const inBand = pool.filter(lv => {
      const d = side === 'long' ? (price - lv.price) / price : (lv.price - price) / price;
      return d >= 0.0015 && d <= 0.025;
    });
    return inBand[0] || pool[0] || null;
  }

  function collectTriggers(tfMap, price, dir) {
    const found = [];
    ['15m', '1H', '4H'].forEach(tf => {
      const an = tfMap[tf];
      if (!an || !an.chart) return;
      const ch = an.chart;
      const zh = tfName(tf);
      const hist = ch.hist;
      for (let i = hist.length - 1; i >= Math.max(1, hist.length - 5); i--) {
        if (hist[i] == null || hist[i - 1] == null) continue;
        if (dir === 'long' && hist[i - 1] <= 0 && hist[i] > 0) { found.push(zh + ' MACD 金叉'); break; }
        if (dir === 'short' && hist[i - 1] >= 0 && hist[i] < 0) { found.push(zh + ' MACD 死叉'); break; }
      }
      const rsi = ch.rsi;
      const cur = rsi[rsi.length - 1];
      let mn = Infinity, mx = -Infinity;
      for (let i = Math.max(0, rsi.length - 5); i < rsi.length; i++) {
        if (rsi[i] == null) continue;
        if (rsi[i] < mn) mn = rsi[i];
        if (rsi[i] > mx) mx = rsi[i];
      }
      if (dir === 'long' && mn <= 35 && cur >= 45) found.push(zh + ' RSI 从超卖区收回');
      if (dir === 'short' && mx >= 65 && cur <= 55) found.push(zh + ' RSI 从超买区回落');
      for (let i = rsi.length - 1; i >= Math.max(1, rsi.length - 5); i--) {
        if (rsi[i] == null || rsi[i - 1] == null) continue;
        if (dir === 'long' && rsi[i - 1] < 50 && rsi[i] >= 50) { found.push(zh + ' RSI 上穿 50'); break; }
        if (dir === 'short' && rsi[i - 1] > 50 && rsi[i] <= 50) { found.push(zh + ' RSI 下穿 50'); break; }
      }
      const view = presentLevels(an.levels, price);
      if (dir === 'long') {
        if (view.supports.some(lv => lv.breakFlag === '刚刚突破')) {
          const retest = view.supports.some(lv => lv.breakFlag === '刚刚突破' && (price - lv.price) / price <= 0.006);
          found.push(zh + (retest ? ' 突破阻力后回踩' : ' 刚刚突破阻力'));
        }
        const s1 = view.supports[0];
        if (s1 && price > s1.price && (price - s1.price) / price <= 0.006) {
          let bounce = false;
          for (let i = Math.max(0, ch.c.length - 3); i < ch.c.length; i++) {
            if (ch.c[i] > ch.o[i] || wickShape(ch.o[i], ch.h[i], ch.l[i], ch.c[i], 'lower')) bounce = true;
          }
          if (bounce) found.push(zh + ' 支撑附近反弹');
        }
      } else {
        if (view.resistances.some(lv => lv.breakFlag === '刚刚跌破')) {
          const retest = view.resistances.some(lv => lv.breakFlag === '刚刚跌破' && (lv.price - price) / price <= 0.006);
          found.push(zh + (retest ? ' 跌破支撑后反抽' : ' 刚刚跌破支撑'));
        }
        const r1 = view.resistances[0];
        if (r1 && price < r1.price && (r1.price - price) / price <= 0.006) {
          let reject = false;
          for (let i = Math.max(0, ch.c.length - 3); i < ch.c.length; i++) {
            if (ch.c[i] < ch.o[i] || wickShape(ch.o[i], ch.h[i], ch.l[i], ch.c[i], 'upper')) reject = true;
          }
          if (reject) found.push(zh + ' 阻力附近受阻');
        }
      }
    });
    ['4H', '1D'].forEach(tf => {
      const an = tfMap[tf];
      if (!an || !an.chart) return;
      const st = structureShift(an.chart.h, an.chart.l, an.chart.c, dir === 'long' ? 'up' : 'down');
      if (st.state === 'confirmed') found.push(tfName(tf) + (dir === 'long' ? ' 更高低点已确认' : ' 更低高点已确认'));
    });
    return found.filter((x, i) => found.indexOf(x) === i);
  }

  function assessEntry(tfMap, price) {
    const emptySide = {
      trend: { ok: false, detail: '周期数据未齐' },
      trigger: { ok: false, detail: '没有触发' },
      location: { ok: false, detail: '没有可用价位' },
      invalidation: null, target: null, rr: null, riskPct: null, rewardPct: null
    };
    if (!tfMap['1H'] || !tfMap['4H'] || !tfMap['1D'] || price == null) {
      return { verdict: '暂不入场（观望）', long: emptySide, short: emptySide, ready: false };
    }
    function trend(dir) {
      let up = 0, down = 0;
      const bits = [];
      if (tfMap['15m']) {
        const s15 = tfMap['15m'].score;
        const word15 = s15 >= 3 ? '做多' : s15 <= -3 ? '做空' : '观望';
        bits.push('15分钟 ' + (s15 > 0 ? '+' : '') + s15 + ' ' + word15 + '（只展示，不计入方向门槛）');
      } else bits.push('15分钟 数据未就绪');
      ['1H', '4H', '1D'].forEach(tf => {
        const s = tfMap[tf].score;
        if (s >= 3) up++;
        else if (s <= -3) down++;
        const word = s >= 3 ? '做多' : s <= -3 ? '做空' : '观望';
        bits.push(tfName(tf) + ' ' + (s > 0 ? '+' : '') + s + ' ' + word);
      });
      const ok = dir === 'long' ? (up >= 2 && down === 0) : (down >= 2 && up === 0);
      const need = dir === 'long'
        ? '方向门槛只看 1小时、4小时、日线：要至少两个 ≥ 3，且这三个里没有 ≤ −3。'
        : '方向门槛只看 1小时、4小时、日线：要至少两个 ≤ −3，且这三个里没有 ≥ 3。';
      return { ok: ok, detail: bits.join('，') + '。' + (ok ? '方向一致。' : need) };
    }
    function geometry(dir) {
      const opp = dir === 'long' ? 'short' : 'long';
      const mine = levelPool(tfMap, price, dir);
      const other = levelPool(tfMap, price, opp);
      const inv = chooseInvalidation(mine, price, dir);
      const target = other[0] || null;
      const nearestOpp = other[0] || null;
      const block = nearestOpp && Math.abs(nearestOpp.distPct) <= 0.5;
      const dist = inv ? Math.abs(inv.distPct) / 100 : null;
      let ok = false;
      let detail;
      if (!inv) detail = dir === 'long' ? '下方没有支撑，缺少失效位。' : '上方没有阻力，缺少失效位。';
      else if (block) detail = '现价距离' + nearestOpp.tf + nearestOpp.id + ' 只有 ' + Math.abs(nearestOpp.distPct).toFixed(2) + '%，贴着' + (dir === 'long' ? '阻力' : '支撑') + '，不做这一侧。';
      else if (dist < 0.0015) detail = '最近的' + inv.tf + inv.id + ' 离现价不足 0.15%，失效距离太近。';
      else if (dist > 0.025) detail = '最近合适的失效位 ' + inv.tf + inv.id + ' 离现价 ' + (dist * 100).toFixed(2) + '%，超过 2.5%。';
      else { ok = true; detail = '失效位用 ' + inv.tf + ' ' + inv.id + '，距离 ' + (dist * 100).toFixed(2) + '%。'; }
      let riskPct = null, rewardPct = null, rr = null;
      if (inv && target) {
        if (dir === 'long') {
          riskPct = (price - inv.price) / price * 100;
          rewardPct = (target.price - price) / price * 100;
        } else {
          riskPct = (inv.price - price) / price * 100;
          rewardPct = (price - target.price) / price * 100;
        }
        if (riskPct > 0 && rewardPct > 0) rr = rewardPct / riskPct;
      }
      return {
        location: { ok: ok, detail: detail },
        invalidation: inv,
        target: target,
        rr: rr,
        riskPct: riskPct,
        rewardPct: rewardPct
      };
    }
    function side(dir) {
      const tr = trend(dir);
      const triggers = collectTriggers(tfMap, price, dir);
      const geo = geometry(dir);
      return {
        trend: tr,
        trigger: { ok: triggers.length > 0, detail: triggers.length ? triggers.join('；') : '近 5 根没有金叉/死叉、RSI 收回、突破回踩或结构确认。' },
        location: geo.location,
        invalidation: geo.invalidation,
        target: geo.target,
        rr: geo.rr,
        riskPct: geo.riskPct,
        rewardPct: geo.rewardPct
      };
    }
    const long = side('long');
    const short = side('short');
    const longOk = long.trend.ok && long.trigger.ok && long.location.ok;
    const shortOk = short.trend.ok && short.trigger.ok && short.location.ok;
    let verdict = '暂不入场（观望）';
    if (longOk && !shortOk) verdict = '可以做多';
    else if (shortOk && !longOk) verdict = '可以做空';
    return { verdict: verdict, long: long, short: short, ready: true };
  }

  function item(name, ok, points, detail) {
    return { name: name, ok: !!ok, points: ok ? points : 0, detail: detail };
  }

  function estimateLiqZones(price, entries, oiUsd, oiChg) {
    const MMR = 0.004;
    const LEVERS = [10, 25, 50, 100];
    const SHARES = { 10: 0.4, 25: 0.3, 50: 0.2, 100: 0.1 };
    const intensity = 1 + Math.max(-0.5, Math.min(1, (oiChg || 0) / 100));
    const pts = (entries && entries.length) ? entries.filter(e => e && e.price > 0) : [{ price: price, vol: 1 }];
    let volSum = 0;
    pts.forEach(e => { volSum += e.vol > 0 ? e.vol : 1; });
    if (!volSum) volSum = pts.length || 1;
    const longZones = [];
    const shortZones = [];
    LEVERS.forEach(lev => {
      let wpxL = 0, wL = 0, wpxS = 0, wS = 0;
      pts.forEach(e => {
        const w = (e.vol > 0 ? e.vol : 1) / volSum;
        const longPx = e.price * (1 - 1 / lev + MMR);
        const shortPx = e.price * (1 + 1 / lev - MMR);
        if (longPx < price) { wpxL += longPx * w; wL += w; }
        if (shortPx > price) { wpxS += shortPx * w; wS += w; }
      });
      const notion = oiUsd > 0 ? oiUsd * SHARES[lev] * intensity : null;
      if (wL > 0) {
        const px = wpxL / wL;
        longZones.push({ lev: lev, price: px, usd: notion == null ? null : notion * wL, distPct: (px - price) / price * 100 });
      }
      if (wS > 0) {
        const px = wpxS / wS;
        shortZones.push({ lev: lev, price: px, usd: notion == null ? null : notion * wS, distPct: (px - price) / price * 100 });
      }
    });
    function nearestLarge(zones) {
      if (!zones.length) return null;
      const maxUsd = Math.max.apply(null, zones.map(z => z.usd || 0));
      const large = maxUsd > 0 ? zones.filter(z => (z.usd || 0) >= maxUsd * 0.6) : zones.slice();
      large.sort((a, b) => Math.abs(a.distPct) - Math.abs(b.distPct));
      return large[0];
    }
    return {
      mmr: MMR,
      intensity: intensity,
      longZones: longZones.sort((a, b) => b.price - a.price),
      shortZones: shortZones.sort((a, b) => a.price - b.price),
      nearestLong: nearestLarge(longZones.slice()),
      nearestShort: nearestLarge(shortZones.slice())
    };
  }

  function summarizeLiquidations(events, nowMs) {
    const windows = { h1: 3600000, h4: 4 * 3600000, h24: 24 * 3600000 };
    const out = {
      h1: { long: 0, short: 0 }, h4: { long: 0, short: 0 }, h24: { long: 0, short: 0 },
      spanMs: 0, count: (events || []).length, oldest: null, newest: null
    };
    if (!events || !events.length) return out;
    let minT = Infinity, maxT = 0;
    events.forEach(e => {
      if (e.ts < minT) minT = e.ts;
      if (e.ts > maxT) maxT = e.ts;
      const age = nowMs - e.ts;
      const side = e.posSide === 'short' ? 'short' : 'long';
      Object.keys(windows).forEach(k => {
        if (age >= 0 && age <= windows[k]) out[k][side] += e.usd || 0;
      });
    });
    out.spanMs = maxT > minT ? maxT - minT : 0;
    out.oldest = minT;
    out.newest = maxT;
    return out;
  }

  function liqBuckets(events, nowMs) {
    const hour = 3600000;
    const start = nowMs - 24 * hour;
    const buckets = [];
    for (let i = 0; i < 24; i++) buckets.push({ t: start + i * hour, long: 0, short: 0 });
    (events || []).forEach(e => {
      if (e.ts < start || e.ts > nowMs + 1000) return;
      let i = Math.floor((e.ts - start) / hour);
      if (i < 0) i = 0;
      if (i > 23) i = 23;
      buckets[i][e.posSide === 'short' ? 'short' : 'long'] += e.usd || 0;
    });
    return buckets;
  }

  function assessExtreme(tfMap, price, ctx) {
    ctx = ctx || {};
    function pack(dir) {
      const items = [];
      const up = dir === 'bottom';
      ['4H', '1D'].forEach(tf => {
        const an = tfMap[tf];
        const zh = tfName(tf);
        if (!an) {
          items.push(item(zh + (up ? ' RSI≤30' : ' RSI≥70'), false, 1, '无数据'));
          items.push(item(zh + (up ? ' RSI 底背离' : ' RSI 顶背离'), false, 2, '无数据'));
          items.push(item(zh + (up ? ' MACD 底背离' : ' MACD 顶背离'), false, 2, '无数据'));
          return;
        }
        const rsi = an.ind.rsi;
        if (up) items.push(item(zh + ' RSI≤30', rsi <= 30, 1, 'RSI ' + rsi.toFixed(1)));
        else items.push(item(zh + ' RSI≥70', rsi >= 70, 1, 'RSI ' + rsi.toFixed(1)));
        const sw = swingIndexes(an.chart.h, an.chart.l, 5, Math.max(0, an.chart.c.length - 80));
        const n = an.chart.c.length;
        const rsiDiv = up
          ? bullishDivergence(sw.lows, an.chart.rsi, i => an.chart.l[i], n)
          : bearishDivergence(sw.highs, an.chart.rsi, i => an.chart.h[i], n);
        const macDiv = up
          ? bullishDivergence(sw.lows, an.chart.macd, i => an.chart.l[i], n)
          : bearishDivergence(sw.highs, an.chart.macd, i => an.chart.h[i], n);
        items.push(item(zh + (up ? ' RSI 底背离' : ' RSI 顶背离'), rsiDiv, 2, rsiDiv ? '价格新低/新高，RSI 没有跟随' : '最近两个摆动点没有背离'));
        items.push(item(zh + (up ? ' MACD 底背离' : ' MACD 顶背离'), macDiv, 2, macDiv ? '价格新低/新高，MACD 没有跟随' : '最近两个摆动点没有背离'));
      });
      let bands = 0;
      const bandNotes = [];
      ['4H', '1D'].forEach(tf => {
        const an = tfMap[tf];
        if (!an) return;
        const hit = bandReclaim(an.chart, up ? 'lower' : 'upper');
        if (hit) { bands++; bandNotes.push(tfName(tf)); }
      });
      const bandPts = bands === 0 ? 0 : bands === 1 ? 2 : 3;
      items.push(item(up ? '布林轨外收回' : '布林轨外收回', bands > 0, bandPts, bands ? bandNotes.join('、') + (up ? ' 下轨外后收回' : ' 上轨外后收回') : '近 6 根没有出现刺出后收回'));

      let wick = false;
      const wickNotes = [];
      ['15m', '1H', '4H', '1D'].forEach(tf => {
        const an = tfMap[tf];
        if (!an) return;
        const view = presentLevels(an.levels, price);
        const levels = up ? view.supports.concat(view.testing) : view.resistances.concat(view.testing);
        const ch = an.chart;
        for (let i = Math.max(0, ch.c.length - 3); i < ch.c.length; i++) {
          const shaped = wickShape(ch.o[i], ch.h[i], ch.l[i], ch.c[i], up ? 'lower' : 'upper');
          const px = up ? ch.l[i] : ch.h[i];
          const near = levels.some(lv => Math.abs(px - lv.price) / price <= 0.004);
          if (shaped && near) { wick = true; wickNotes.push(tfName(tf)); break; }
        }
      });
      items.push(item(up ? '支撑位长下影' : '阻力位长上影', wick, 2, wick ? wickNotes.filter((x, i, a) => a.indexOf(x) === i).join('、') + ' 影线至少是实体的 2 倍，且碰到价位' : '近 3 根没有这种 K 线'));

      let volHit = null;
      ['4H', '1H', '15m'].forEach(tf => {
        if (volHit) return;
        const an = tfMap[tf];
        if (!an || an.closedIndex == null) return;
        const ci = an.closedIndex;
        const ch = an.chart;
        const spike = an.ind.vol_ratio >= 2;
        const climax = up
          ? (ch.c[ci] < ch.o[ci] || wickShape(ch.o[ci], ch.h[ci], ch.l[ci], ch.c[ci], 'lower'))
          : (ch.c[ci] > ch.o[ci] || wickShape(ch.o[ci], ch.h[ci], ch.l[ci], ch.c[ci], 'upper'));
        if (spike && climax) volHit = tfName(tf) + ' 量比 ' + an.ind.vol_ratio.toFixed(2);
      });
      items.push(item(up ? '放量下跌或长下影' : '放量上涨或长上影', !!volHit, 2, volHit || '已收盘 K 线量比未到 2'));

      const funding = ctx.funding, avg = ctx.fundingAvg;
      const liq = ctx.liq || {};
      const liqMin = ctx.liqMin || 0;
      const fundTxt = funding == null ? '暂无' : ((funding * 100).toFixed(4) + '% / 8小时');
      if (up) {
        const extreme = funding != null && funding < -0.0003;
        const neg = funding != null && funding < 0;
        const pts = extreme ? 2 : neg ? 1 : 0;
        items.push(item('资金费率偏空', neg, pts, fundTxt + (extreme ? '，低于 −0.03%，空头拥挤' : neg ? '，空头付费' : '')));
        const cascade = (liq.long1h || 0) > 0 && (liq.long1h || 0) >= (liq.short1h || 0) * 1.5 && (liq.long1h || 0) >= liqMin;
        const reset = funding != null && (funding < 0 || (avg != null && funding < avg));
        items.push(item('多头清算瀑布且费率回落', cascade && reset, 2, cascade ? '近 1 小时多头被清算明显多于空头，且资金费率转负或低于均值' : '还没有同时出现大量多头清算和费率回落'));
        const near = !!ctx.nearSupport && neg;
        items.push(item('负资金费率且靠近支撑', near, 1, near ? '现价离 4 小时最近支撑不超过 1%，负费率作逆向多头提示' : '费率不为负，或现价不在支撑 1% 内'));
        const lsOk = ctx.ls != null && ctx.ls < 0.8;
        items.push(item('多空人数比 < 0.8', lsOk, 1, ctx.ls == null ? '暂无' : Number(ctx.ls).toFixed(2)));
        const fOk = ctx.fng != null && ctx.fng < 20;
        items.push(item('恐惧贪婪 < 20', fOk, 1, ctx.fng == null ? '暂无' : String(ctx.fng)));
      } else {
        const extreme = funding != null && funding > 0.0005;
        const hot = funding != null && funding > 0.0003;
        const pts = extreme ? 2 : hot ? 1 : 0;
        items.push(item('资金费率偏高', hot, pts, fundTxt + (extreme ? '，高于 0.05%，多头拥挤，有轧多风险' : hot ? '，多头拥挤' : '')));
        const cascade = (liq.short1h || 0) > 0 && (liq.short1h || 0) >= (liq.long1h || 0) * 1.5 && (liq.short1h || 0) >= liqMin;
        items.push(item('空头清算配合高费率', cascade && hot, 2, cascade && hot ? '近 1 小时空头被清算偏多，且资金费率偏高' : '没有同时出现空头清算潮和高费率'));
        const lsOk = ctx.ls != null && ctx.ls > 2.5;
        items.push(item('多空人数比 > 2.5', lsOk, 1, ctx.ls == null ? '暂无' : Number(ctx.ls).toFixed(2)));
        const fOk = ctx.fng != null && ctx.fng >= 80;
        items.push(item('恐惧贪婪 ≥ 80', fOk, 1, ctx.fng == null ? '暂无' : String(ctx.fng)));
      }

      let confirmed = false, forming = false;
      const structNotes = [];
      ['4H', '1D'].forEach(tf => {
        const an = tfMap[tf];
        if (!an) return;
        const st = structureShift(an.chart.h, an.chart.l, an.chart.c, up ? 'up' : 'down');
        if (st.state === 'confirmed') { confirmed = true; structNotes.push(tfName(tf) + ' 已突破中间' + (up ? '高点' : '低点')); }
        else if (st.state === 'forming') { forming = true; structNotes.push(tfName(tf) + ' 结构正在形成'); }
      });
      const stPts = confirmed ? 3 : forming ? 1 : 0;
      items.push(item(up ? '更高低点确认' : '更低高点确认', confirmed || forming, stPts, structNotes.length ? structNotes.join('；') : '最近两个摆动点还没有这种结构'));

      let score = 0;
      items.forEach(it => { score += it.points; });
      let labelText;
      if (up) {
        if (score >= 6 && confirmed) labelText = '底部已确认';
        else if (score >= 6) labelText = '底部信号较强';
        else if (score >= 3) labelText = '可能在筑底';
        else labelText = '未见底';
      } else if (score >= 6 && confirmed) labelText = '顶部已确认';
      else if (score >= 6) labelText = '顶部信号较强';
      else if (score >= 3) labelText = '可能在筑顶';
      else labelText = '未见顶';
      return { label: labelText, score: score, confirmed: confirmed, items: items };
    }
    return { bottom: pack('bottom'), top: pack('top') };
  }

  function overallCall(src) {
    const factors = [];
    function add(name, weight, unit, reading) {
      if (unit == null || Number.isNaN(unit)) return;
      factors.push({
        name: name,
        weight: weight,
        unit: Math.max(-1, Math.min(1, unit)),
        reading: reading || ''
      });
    }
    if (src.tech != null && src.timeframes && src.timeframes.length) {
      const reading = src.timeframes.map(t => t.name + ' ' + t.signal + ' ' + (t.score > 0 ? '+' : '') + t.score).join('，');
      add('多周期技术', 0.22, src.tech / 10, reading);
    }
    if (src.sr) {
      const s = src.sr;
      let unit = null;
      let reading = '';
      if (s.brokeDown && !s.brokeUp) {
        unit = -0.85;
        reading = '跌破支撑' + (s.nextDown != null ? '，下探参考已在支撑阻力一节' : '');
      } else if (s.brokeUp && !s.brokeDown) {
        unit = 0.85;
        reading = '向上突破，位置偏多';
      } else if (s.nearSupport && s.nearResistance) {
        unit = 0;
        reading = '同时贴近支撑和阻力';
      } else if (s.nearSupport) {
        unit = 0.75;
        reading = '靠近支撑，偏多';
      } else if (s.nearResistance) {
        unit = -0.75;
        reading = '靠近阻力，偏空';
      } else if (s.supportDist != null && s.resistDist != null) {
        const ds = Math.abs(s.supportDist);
        const dr = Math.abs(s.resistDist);
        unit = ds < dr ? Math.min(0.35, (dr - ds) / 8) : -Math.min(0.35, (ds - dr) / 8);
        reading = ds < dr ? '离支撑更近' : '离阻力更近';
      }
      if (unit != null) add('支撑阻力', 0.14, unit, reading);
    }
    if (src.entry && src.entry.ready) {
      let unit = 0;
      let reading = src.entry.verdict;
      if (src.entry.verdict === '可以做多') unit = 1;
      else if (src.entry.verdict === '可以做空') unit = -1;
      else {
        unit = ((src.entry.longHits || 0) - (src.entry.shortHits || 0)) / 3 * 0.4;
        reading = '暂不入场（做多 ' + (src.entry.longHits || 0) + '/3，做空 ' + (src.entry.shortHits || 0) + '/3）';
      }
      add('入场清单', 0.14, unit, reading);
    }
    if (src.extreme && src.extreme.bottom && src.extreme.top) {
      const bmap = { '底部已确认': 1, '底部信号较强': 0.7, '可能在筑底': 0.35, '未见底': 0 };
      const tmap = { '顶部已确认': -1, '顶部信号较强': -0.7, '可能在筑顶': -0.35, '未见顶': 0 };
      const bu = bmap[src.extreme.bottom];
      const tu = tmap[src.extreme.top];
      if (bu != null || tu != null) add('见底见顶', 0.12, (bu || 0) + (tu || 0), '底部' + src.extreme.bottom + '，顶部' + src.extreme.top);
    }
    if (src.funding != null) {
      const pct = src.funding * 100;
      let unit = 0;
      let reading = pct.toFixed(4) + '% / 8小时';
      if (pct > 0.05) { unit = -1; reading += '，多头拥挤，逆向偏空'; }
      else if (pct > 0.03) { unit = -0.55; reading += '，多头偏拥挤'; }
      else if (pct < -0.03) { unit = 1; reading += '，空头拥挤，逆向偏多'; }
      else if (pct < 0) { unit = 0.35; reading += '，空头付费'; }
      else if (pct > 0) { unit = -0.25; reading += '，多头付费'; }
      add('资金费率', 0.08, unit, reading);
    }
    if (src.liq && ((src.liq.long1h || 0) + (src.liq.short1h || 0) > 0 || src.liq.longZoneDist != null || src.liq.shortZoneDist != null)) {
      const L = src.liq.long1h || 0;
      const S = src.liq.short1h || 0;
      let imb = L + S > 0 ? (S - L) / (L + S) : 0;
      let cluster = 0;
      const bits = [];
      if (L + S > 0) bits.push('近1小时多头清算额 ' + Math.round(L) + '，空头 ' + Math.round(S));
      if (src.liq.shortZoneDist != null && src.liq.shortZoneDist > 0 && src.liq.shortZoneDist <= 3) {
        cluster += 0.5;
        bits.push('上方空头清算区 ' + src.liq.shortZoneDist.toFixed(1) + '%');
      }
      if (src.liq.longZoneDist != null && src.liq.longZoneDist < 0 && src.liq.longZoneDist >= -3) {
        cluster -= 0.5;
        bits.push('下方多头清算区 ' + src.liq.longZoneDist.toFixed(1) + '%');
      }
      add('清算', 0.10, imb * 0.7 + cluster * 0.3, bits.join('；') || '清算数据不足');
    }
    if (src.fng != null) {
      const v = src.fng;
      let unit = 0;
      let reading = String(v);
      if (v <= 20) { unit = 1; reading += ' 极度恐惧，逆向偏多'; }
      else if (v <= 30) { unit = 0.45; reading += ' 恐惧，略偏多'; }
      else if (v >= 80) { unit = -1; reading += ' 极度贪婪，逆向偏空'; }
      else if (v >= 70) { unit = -0.45; reading += ' 贪婪，略偏空'; }
      else reading += '，未到极端';
      add('恐惧贪婪', 0.05, unit, reading);
    }
    if (src.hasNews && src.newsAdj != null) {
      const st = src.newsStat || {};
      add('新闻', 0.05, src.newsAdj / 2, '近12小时 利多' + (st.bull || 0) + ' / 利空' + (st.bear || 0));
    }
    if (src.influence && src.influence.bias != null && !Number.isNaN(src.influence.bias)) {
      const inf = src.influence;
      add('美股影响', 0.12, inf.bias, (inf.line || '美股') + (inf.stale ? '（数据偏旧，权重已降低）' : ''));
    }
    return finishFactors(factors);
  }

  function finishFactors(factors) {
    if (!factors.length) {
      return { score: null, call: '观望', strength: '观望', confidence: '低', rationale: '数据还没到齐。', factors: [], conflict: false };
    }
    const wsum = factors.reduce((a, f) => a + f.weight, 0);
    let score = 0;
    factors.forEach(f => {
      f.contribution = Math.round((f.weight / wsum) * f.unit * 10 * 10) / 10;
      score += (f.weight / wsum) * f.unit * 10;
    });
    score = Math.round(score * 10) / 10;
    const strongLong = factors.filter(f => f.unit >= 0.45);
    const strongShort = factors.filter(f => f.unit <= -0.45);
    const conflict = strongLong.length > 0 && strongShort.length > 0;
    let call = '观望';
    let strength = '观望';
    if (!conflict) {
      if (score >= 6) { call = '做多'; strength = '强烈做多'; }
      else if (score >= 2.5) { call = '做多'; strength = '偏多'; }
      else if (score <= -6) { call = '做空'; strength = '强烈做空'; }
      else if (score <= -2.5) { call = '做空'; strength = '偏空'; }
    }
    const signed = factors.filter(f => Math.abs(f.unit) >= 0.2);
    let confidence = '低';
    if (!conflict && signed.length && factors.length >= 3 && call !== '观望') {
      const maj = signed.filter(f => call === '做多' ? f.unit > 0 : f.unit < 0).length;
      const ratio = maj / signed.length;
      confidence = ratio >= 0.75 ? '高' : ratio >= 0.55 ? '中' : '低';
    }
    const tops = factors.slice().sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution)).slice(0, 2);
    let rationale;
    if (conflict) rationale = '多空因素对着干，先观望。';
    else if (strength === '观望') rationale = '各方力度都不大，先观望。';
    else rationale = '主要看' + tops.map(f => f.name).join('和') + '，综合 ' + (score > 0 ? '+' : '') + score + '。';
    return { score: score, call: call, strength: strength, confidence: confidence, rationale: rationale, factors: factors, conflict: conflict };
  }

  function shortPulse(an, px) {
    if (!an || !an.chart || !an.chart.c.length) return null;
    const ch = an.chart;
    const n = ch.c.length;
    const last = px != null ? px : ch.c[n - 1];
    const prev = ch.c[Math.max(0, n - 11)];
    const chg = prev ? last / prev - 1 : 0;
    const rsi = an.ind.rsi;
    let unit = Math.max(-1, Math.min(1, chg / 0.004));
    if (rsi >= 70) unit -= 0.25;
    else if (rsi <= 30) unit += 0.25;
    else if (rsi > 55) unit += 0.15;
    else if (rsi < 45) unit -= 0.15;
    unit = Math.max(-1, Math.min(1, unit));
    let pv = 0;
    let vv = 0;
    const start = Math.max(0, n - 60);
    for (let i = start; i < n; i++) {
      const tp = (ch.h[i] + ch.l[i] + ch.c[i]) / 3;
      const vol = ch.v[i] || 0;
      pv += tp * vol;
      vv += vol;
    }
    let vwapUnit = null;
    let vwapText = '';
    if (vv > 0) {
      const vwap = pv / vv;
      const dist = (last - vwap) / last;
      vwapUnit = Math.max(-1, Math.min(1, dist / 0.003));
      vwapText = '价格' + (dist >= 0 ? '高于' : '低于') + '近段成交均价 ' + Math.abs(dist * 100).toFixed(2) + '%';
    }
    return {
      mom: unit,
      momText: '近10根 ' + (chg * 100).toFixed(2) + '%，RSI ' + (rsi == null ? '—' : rsi.toFixed(0)),
      vwap: vwapUnit,
      vwapText: vwapText
    };
  }

  function horizonScalp(src) {
    const factors = [];
    function add(name, weight, unit, reading) {
      if (unit == null || Number.isNaN(unit)) return;
      factors.push({ name: name, weight: weight, unit: Math.max(-1, Math.min(1, unit)), reading: reading || '' });
    }
    if (src.pulse) {
      add('1分钟动量', 0.22, src.pulse.mom, src.pulse.momText);
      if (src.pulse.vwap != null) add('短时均价', 0.12, src.pulse.vwap, src.pulse.vwapText);
    }
    if (src.m5 != null) add('5分钟技术', 0.18, src.m5 / 10, src.m5Text || '');
    if (src.m15 != null) add('15分钟技术', 0.14, src.m15 / 10, src.m15Text || '');
    if (src.book != null) add('盘口买卖', 0.14, src.book, src.bookText || '');
    if (src.liqUnit != null) add('近15分钟清算', 0.12, src.liqUnit, src.liqText || '');
    if (src.funding != null) {
      const pct = src.funding * 100;
      let unit = 0;
      let reading = pct.toFixed(4) + '%';
      if (pct > 0.05) { unit = -0.8; reading += '，多头拥挤'; }
      else if (pct < -0.03) { unit = 0.8; reading += '，空头拥挤'; }
      else if (pct > 0) { unit = -0.2; reading += '，多头付费'; }
      else if (pct < 0) { unit = 0.2; reading += '，空头付费'; }
      add('当前资金费率', 0.08, unit, reading);
    }
    if (src.srUnit != null) add('短线位置', 0.12, src.srUnit, src.srText || '');
    return finishFactors(factors);
  }

  function horizonLong(src) {
    const factors = [];
    function add(name, weight, unit, reading) {
      if (unit == null || Number.isNaN(unit)) return;
      factors.push({ name: name, weight: weight, unit: Math.max(-1, Math.min(1, unit)), reading: reading || '' });
    }
    if (src.weeklyScore != null) add('周线趋势', 0.26, src.weeklyScore / 10, src.weeklyText || '');
    if (src.dailyScore != null) add('日线趋势', 0.18, src.dailyScore / 10, src.dailyText || '');
    if (src.weeklyRsi != null) {
      const r = src.weeklyRsi;
      let unit = 0;
      if (r >= 70) unit = 0.55;
      else if (r >= 55) unit = 0.35;
      else if (r <= 30) unit = -0.55;
      else if (r <= 45) unit = -0.35;
      add('周线RSI', 0.10, unit, 'RSI ' + r.toFixed(0));
    }
    if (src.aboveEma200 != null) add('周线EMA200', 0.12, src.aboveEma200 ? 0.7 : -0.7, src.aboveEma200 ? '价格在周线 EMA200 上方' : '价格在周线 EMA200 下方');
    if (src.srUnit != null) add('大级别位置', 0.12, src.srUnit, src.srText || '');
    if (src.influence && src.influence.bias != null) add('宏观与相关性', 0.12, src.influence.bias, src.influence.line || '');
    if (src.fng != null) {
      const v = src.fng;
      let unit = 0;
      let reading = String(v);
      if (v <= 20) { unit = 1; reading += ' 极度恐惧，逆向偏多'; }
      else if (v >= 80) { unit = -1; reading += ' 极度贪婪，逆向偏空'; }
      else reading += '，未到极端，长线不计方向';
      add('恐惧贪婪', 0.05, unit, reading);
    }
    if (src.extreme && src.extreme.bottom && src.extreme.top) {
      const bmap = { '底部已确认': 1, '底部信号较强': 0.65, '可能在筑底': 0.3, '未见底': 0 };
      const tmap = { '顶部已确认': -1, '顶部信号较强': -0.65, '可能在筑顶': -0.3, '未见顶': 0 };
      const bu = bmap[src.extreme.bottom];
      const tu = tmap[src.extreme.top];
      if (bu != null || tu != null) add('见底见顶', 0.05, (bu || 0) + (tu || 0), '底部' + src.extreme.bottom + '，顶部' + src.extreme.top);
    }
    return finishFactors(factors);
  }

  return {
    ema: ema, sma: sma, rsi: rsi, macd: macd, boll: boll, kdj: kdj, atr: atr, swings: swings,
    scoreTf: scoreTf, label: label, sentimentAdj: sentimentAdj, newsAdj: newsAdj,
    analyzeRows: analyzeRows, presentLevels: presentLevels, assessEntry: assessEntry, assessExtreme: assessExtreme,
    estimateLiqZones: estimateLiqZones, summarizeLiquidations: summarizeLiquidations, liqBuckets: liqBuckets,
    overallCall: overallCall, shortPulse: shortPulse, horizonScalp: horizonScalp, horizonLong: horizonLong
  };
});
