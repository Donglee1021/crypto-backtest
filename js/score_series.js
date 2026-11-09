/* 用 indicators.js 的 scoreTf 给整段 K 线打分，供 Python 对照。 */
const fs = require('fs');
const Ind = require('./indicators.js');

function scoreSeries(rows) {
  const o = [];
  const h = [];
  const l = [];
  const c = [];
  const v = [];
  for (let i = 0; i < rows.length; i++) {
    const x = rows[i];
    o.push(Number(x[1]));
    h.push(Number(x[2]));
    l.push(Number(x[3]));
    c.push(Number(x[4]));
    v.push(Number(x[5]));
  }
  const e20 = Ind.ema(c, 20);
  const e50 = Ind.ema(c, 50);
  const e200 = Ind.ema(c, 200);
  const md = Ind.macd(c);
  const rs = Ind.rsi(c);
  const bb = Ind.boll(c);
  const kd = Ind.kdj(h, l, c);
  const vavg = Ind.sma(v, 20);
  const ml = md[0];
  const ms = md[1];
  const mh = md[2];
  const bu = bb[1];
  const bl = bb[2];
  const ks = kd[0];
  const ds = kd[1];
  const js = kd[2];
  const scores = [];
  for (let i = 0; i < c.length; i++) {
    const need = [e20[i], e50[i], e200[i], ml[i], ms[i], mh[i], rs[i], ks[i], ds[i], js[i], bu[i], bl[i], vavg[i]];
    if (need.some(x => x == null) || i === 0 || mh[i - 1] == null || !vavg[i]) {
      scores.push(null);
      continue;
    }
    const scored = Ind.scoreTf({
      close: c[i],
      open: o[i],
      ema20: e20[i],
      ema50: e50[i],
      ema200: e200[i],
      macd: ml[i],
      macd_signal: ms[i],
      macd_hist: mh[i],
      macd_hist_prev: mh[i - 1],
      rsi: rs[i],
      k: ks[i],
      d: ds[i],
      j: js[i],
      boll_up: bu[i],
      boll_lo: bl[i],
      vol_ratio: v[i] / vavg[i],
      vol_bull: c[i] > o[i]
    });
    scores.push(scored[0]);
  }
  return scores;
}

if (require.main === module) {
  const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  process.stdout.write(JSON.stringify(scoreSeries(rows)));
}

module.exports = { scoreSeries };
