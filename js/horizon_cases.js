/* 固定输入，用来对照 Python 的超短线 / 中线 / 长线。 */
const Ind = require('./indicators.js');

function pack(name, result) {
  return {
    name: name,
    score: result.score,
    call: result.call,
    strength: result.strength,
    conflict: result.conflict
  };
}

const cases = [
  pack('scalp-hot', Ind.horizonScalp({
    m15: 4, m15Text: '做多', funding: 0.0006, srUnit: 0.7, srText: '靠近支撑'
  })),
  pack('scalp-cold', Ind.horizonScalp({
    m15: -6, m15Text: '做空', funding: -0.0004, srUnit: -0.85, srText: '刚跌破'
  })),
  pack('mid-up', Ind.overallCall({
    tech: 2.5,
    timeframes: [{ name: '1小时', score: 4, signal: '做多' }],
    sr: { brokeDown: false, brokeUp: true, nearSupport: false, nearResistance: false, supportDist: -1.2, resistDist: 3.4 },
    funding: 0.0001,
    fng: 18
  })),
  pack('mid-down', Ind.overallCall({
    tech: -4,
    timeframes: [{ name: '1小时', score: -5, signal: '做空' }],
    funding: 0.0008,
    fng: 82
  })),
  pack('long-up', Ind.horizonLong({
    weeklyScore: 6,
    dailyScore: 3,
    weeklyRsi: 62,
    aboveEma200: true,
    srUnit: 0.25,
    fng: 12
  })),
  pack('long-flat', Ind.horizonLong({
    weeklyScore: 1,
    dailyScore: -1,
    weeklyRsi: 48,
    aboveEma200: false,
    fng: 55
  }))
];

process.stdout.write(JSON.stringify(cases));
