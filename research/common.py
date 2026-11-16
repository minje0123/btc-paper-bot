# -*- coding: utf-8 -*-
"""연구 공통 도구 — 데이터 읽기(최종 시험 폴더 차단), 시간봉 묶기, ATR, 구간 경계. 표준 라이브러리만."""
import csv
import datetime as dt
import json
import os
import sys

RESEARCH = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(RESEARCH)
sys.path.insert(0, REPO)
sys.path.insert(0, RESEARCH)
import engine                      # noqa: E402  운영 엔진(v1.4.1) 그대로 사용
from engine import KST, parse_kst  # noqa: E402
import holdout_guard               # noqa: E402

META_DATA = os.path.normpath(os.path.join(REPO, "..", "tasks", "btc-meta", "data"))
COINS = ["BTC", "ETH", "XRP", "SOL"]
TFS = [5, 15, 30, 60, 240]
TF_NAME = {5: "5m", 15: "15m", 30: "30m", 60: "1H", 240: "4H"}


def load_5m(coin, path=None, experiment=None):
    """18개월 연구 데이터(빗썸 5분봉) → (T,O,H,L,C,V). holdout 경로는 FINAL=1 없이 예외."""
    path = path or os.path.join(META_DATA, f"data_{coin}_5m.csv")
    holdout_guard.check_path(path, experiment)
    T, O, H, L, C, V = [], [], [], [], [], []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            T.append(parse_kst(r["time_kst"]))
            O.append(float(r["open"])); H.append(float(r["high"])); L.append(float(r["low"]))
            C.append(float(r["close"])); V.append(float(r["volume"]))
    return T, O, H, L, C, V


def aggregate(T, O, H, L, C, V, minutes):
    """5분봉 → k분봉. KST 00:00 기준 정렬(거래소 봉과 같은 경계), 봉 시각 = 구간 시작. 빈 구간은 봉 없음."""
    if minutes == 5:
        return list(T), list(O), list(H), list(L), list(C), list(V)
    sec = minutes * 60
    off = engine.KST_OFFSET_SEC
    t2, o2, h2, l2, c2, v2 = [], [], [], [], [], []
    cur = None
    for i, t in enumerate(T):
        b = (int(t.timestamp()) + off) // sec
        if b != cur:
            cur = b
            t2.append(dt.datetime.fromtimestamp(b * sec - off, KST))
            o2.append(O[i]); h2.append(H[i]); l2.append(L[i]); c2.append(C[i]); v2.append(V[i])
        else:
            h2[-1] = max(h2[-1], H[i]); l2[-1] = min(l2[-1], L[i]); c2[-1] = C[i]; v2[-1] += V[i]
    return t2, o2, h2, l2, c2, v2


def make_data(bars, tf):
    """E1 구현 세부: 실행 k분 → 방향 3k분, 보조 12k분"""
    return engine.Data(*bars, dir_min=3 * tf, aux_min=12 * tf, bar_min=tf)


def atr_wilder(H, L, C, n=14):
    out = [None] * len(C)
    trs = []
    a = None
    for i in range(len(C)):
        tr = H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
        if a is None:
            trs.append(tr)
            if len(trs) == n:
                a = sum(trs) / n
                out[i] = a
        else:
            a = (a * (n - 1) + tr) / n
            out[i] = a
    return out


def median(a):
    a = sorted(a)
    n = len(a)
    if not n:
        return None
    return a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2


def load_split():
    with open(os.path.join(RESEARCH, "data_split.json"), encoding="utf-8") as f:
        s = json.load(f)
    return parse_kst(s["dev"]["start"]), parse_kst(s["validation"]["start"]), parse_kst(s["validation"]["end"])
