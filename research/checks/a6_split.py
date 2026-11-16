# -*- coding: utf-8 -*-
"""A6. research/data_split.json — 18개월 실제 시작·종료, 개발(앞 10개월)·검증(다음 8개월) 경계, holdout A·B 범위"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import COINS, RESEARCH, load_5m  # noqa: E402


def main():
    per = {}
    for c in COINS:
        T = load_5m(c)[0]
        per[c] = dict(first=T[0].strftime("%Y-%m-%d %H:%M"), last=T[-1].strftime("%Y-%m-%d %H:%M"), rows=len(T))
    start = min(v["first"] for v in per.values())
    end = max(v["last"] for v in per.values())
    with open(os.path.join(RESEARCH, "holdout_manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    out = dict(
        note=("18개월 빗썸 5분봉(BTC·ETH·XRP·SOL). 개발 = 시작일부터 달력 10개월(경계 2026-03-18 00:00 KST, 그 전 봉까지), "
              "검증 = 경계부터 데이터 끝까지(약 8개월). 거래는 '진입 시각'으로 구간을 나눈다. 지표 준비(워밍업)는 "
              "각 구간 앞의 데이터를 그대로 이어 쓴다(검증 구간 계산 때 개발 구간 봉을 지표에만 사용)."),
        data_18m=dict(source="tasks/btc-meta/data/data_{BTC,ETH,XRP,SOL}_5m.csv", start=start, end=end, per_coin=per),
        dev=dict(start=start, end="2026-03-17 23:55", end_exclusive="2026-03-18 00:00", months=10),
        validation=dict(start="2026-03-18 00:00", end=end, months="약 8 (2026-03-18 ~ 2026-11-16 13:00)"),
        holdout_A=dict(desc=man["sets"]["A"]["desc"], start=man["sets"]["A"]["range_kst"][0],
                       end=man["sets"]["A"]["range_kst"][1], coins=COINS, folder="research/holdout/A/"),
        holdout_B=dict(desc=man["sets"]["B"]["desc"], start=man["sets"]["B"]["range_kst"][0],
                       end=man["sets"]["B"]["range_kst"][1], coins=man["sets"]["B"]["selection"]["selected"],
                       folder="research/holdout/B/"),
        forward_test=dict(start="2026-11-17", desc="그림자 장부 전진 시험"),
    )
    with open(os.path.join(RESEARCH, "data_split.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
