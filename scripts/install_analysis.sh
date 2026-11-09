#!/usr/bin/env bash
# TA-Lib 0.6 以上自带二进制轮子；没有轮子时再编译 C 库。
set -euo pipefail
python3 -m pip install --upgrade pip
grep -v '^TA-Lib' requirements.txt > /tmp/crypto-backtest-reqs.txt
python3 -m pip install -r /tmp/crypto-backtest-reqs.txt
if python3 -m pip install 'TA-Lib>=0.6.0'; then
  python3 - << 'PY'
import talib
print("TA-Lib", talib.__version__)
PY
  exit 0
fi
echo "没有 TA-Lib 轮子，改为编译 C 库"
sudo apt-get update
sudo apt-get install -y build-essential wget
tmpdir=$(mktemp -d)
wget -O "$tmpdir/ta-lib.tar.gz" http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xzf "$tmpdir/ta-lib.tar.gz" -C "$tmpdir"
cd "$tmpdir/ta-lib"
./configure --prefix=/usr
make -j"$(nproc)"
sudo make install
cd /
python3 -m pip install 'TA-Lib>=0.4.28'
