# -*- coding: utf-8 -*-
"""
A1. 집계 대조 — 18개월 5분봉을 15m·30m·1H·4H 로 묶은 봉과 빗썸이 직접 주는 같은 시간봉을 겹치는 시각에서 비교.
빗썸 API: /v1/candles/minutes/{unit}?market=KRW-{coin}&count=200 (최신 200봉, 페이지 1개 + to 로 1페이지 더)
비교 대상은 '완전한' 묶음봉만: 5분봉이 빠짐없이(15m=3개 …) 있는 구간. 빠진 5분봉이 있는 구간은 따로 센다.
일치 기준: 시가·고가·저가·종가는 정확히 같음, 거래량은 상대오차 1e-6 이하.
결과: research/checks/a1_result.json
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import COINS, KST, aggregate, load_5m, engine  # noqa: E402

UA = {"Accept": "application/json", "User-Agent": "paper-research (read-only candles)"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a1_result.json")


def fetch(coin, unit, pages=2):
    rows = {}
    to = None
    for _ in range(pages):
        q = {"market": f"KRW-{coin}", "count": 200}
        if to:
            q["to"] = to.strftime("%Y-%m-%dT%H:%M:%S")
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.bithumb.com/v1/candles/minutes/{unit}?" + urllib.parse.urlencode(q), headers=UA), timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not d:
            break
        for x in d:
            t = dt.datetime.strptime(x["candle_date_time_kst"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST)
            rows[t] = (float(x["opening_price"]), float(x["high_price"]), float(x["low_price"]),
                       float(x["trade_price"]), float(x["candle_acc_trade_volume"]))
        to = min(rows)
        time.sleep(0.15)
    return rows


def main():
    res = dict(method=__doc__.strip(), results=[])
    tot = dict(n=0, ohlc_bad=0, vol_bad=0)
    for coin in COINS:
        bars5 = load_5m(coin)
        T5 = bars5[0]
        have = set(T5)
        last5 = T5[-1]
        for unit in (15, 30, 60, 240):
            agg = aggregate(*bars5, unit)
            A = {agg[0][i]: (agg[1][i], agg[2][i], agg[3][i], agg[4][i], agg[5][i]) for i in range(len(agg[0]))}
            ex = fetch(coin, unit)
            k = unit // 5
            n = bad_o = bad_v = incomplete = inc_bad = 0
            examples = []
            common_t = sorted(t for t in ex if t in A and t + dt.timedelta(minutes=unit) <= last5 + dt.timedelta(minutes=5))
            for t in common_t:
                full = all((t + dt.timedelta(minutes=5 * j)) in have for j in range(k))
                a, e = A[t], ex[t]
                if not full:          # 5분봉이 빠진(거래 없던) 구간: 참고로만 따로 비교
                    incomplete += 1
                    inc_bad += any(a[j] != e[j] for j in range(4)) or abs(a[4] - e[4]) > 1e-6 * max(abs(e[4]), 1e-12)
                    continue
                n += 1
                ob = any(a[j] != e[j] for j in range(4))
                vb = abs(a[4] - e[4]) > 1e-6 * max(abs(e[4]), 1e-12)
                bad_o += ob; bad_v += vb
                if (ob or vb) and len(examples) < 5:
                    examples.append(dict(t=t.strftime("%Y-%m-%d %H:%M"), agg=a, exch=e))
            r = dict(coin=coin, unit=unit, overlap_from=common_t[0].strftime("%Y-%m-%d %H:%M") if common_t else None,
                     overlap_to=common_t[-1].strftime("%Y-%m-%d %H:%M") if common_t else None,
                     compared=n, incomplete_skipped=incomplete, incomplete_mismatch=int(inc_bad),
                     ohlc_mismatch=bad_o, vol_mismatch=bad_v,
                     ohlc_mismatch_pct=round(bad_o / n * 100, 3) if n else None,
                     vol_mismatch_pct=round(bad_v / n * 100, 3) if n else None, examples=examples)
            res["results"].append(r)
            tot["n"] += n; tot["ohlc_bad"] += bad_o; tot["vol_bad"] += bad_v
            print(coin, unit, r["overlap_from"], "~", r["overlap_to"], "n=", n, "OHLC불일치", bad_o, "거래량불일치", bad_v,
                  "불완전", incomplete, "불완전 중 불일치", inc_bad)
    # 엔진 내부 상위봉(htf)과 연구용 aggregate 가 같은 경계를 쓰는지도 확인
    b = load_5m("BTC")
    hb, _ = engine.htf(*b, 240)
    ag = aggregate(*b, 240)
    res["engine_htf_vs_aggregate_240"] = (len(hb) == len(ag[0]) and all(
        abs(hb[i][j] - [ag[1], ag[2], ag[3], ag[4], ag[5]][j][i]) < 1e-9 for i in range(len(hb)) for j in range(5)))
    tot["ohlc_mismatch_pct"] = round(tot["ohlc_bad"] / tot["n"] * 100, 3) if tot["n"] else None
    tot["vol_mismatch_pct"] = round(tot["vol_bad"] / tot["n"] * 100, 3) if tot["n"] else None
    res["total"] = tot
    res["pass"] = tot["n"] > 0 and tot["ohlc_bad"] == 0 and tot["vol_bad"] == 0
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("합계", tot, "엔진 htf=aggregate:", res["engine_htf_vs_aggregate_240"], "PASS" if res["pass"] else "FAIL")


if __name__ == "__main__":
    main()
