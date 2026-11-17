# -*- coding: utf-8 -*-
"""
v4 짝 비교용 시장 국면 라벨 (사전 정의, BTC 일봉) — 기록용, 매매 규칙에 쓰지 않음.

- 일봉 = UTC 00:00(KST 09:00) 기준 하루(업비트 일봉과 같은 경계). 라벨 시각 t 에는 t 이전에 '완성된' 일봉만 쓴다(미래 참조 없음).
- 추세: 30일 수익률 = 마지막 완성 일봉 종가 ÷ 30일 전 종가 − 1
        > +5% 상승, < −5% 하락, 그 사이 횡보
- 고변동 플래그: 7일 실현 변동성(일간 로그수익률 7개의 표준편차)이 지난 1년(완성 일봉 365개)의
  7일 실현 변동성 분포에서 상위 25%(75백분위 이상)이면 True. 1년이 안 되면 있는 만큼(최소 MIN_HIST_DAYS) 쓰고
  hist_days 에 실제 길이를 남긴다.
- 실시간: data/btc_daily.csv (업비트 KRW-BTC 일봉, 공개 API). 6시간마다·필요할 때만 받는다. BTC 외 코인은 받지 않는다.
표준 라이브러리만.
"""
import bisect
import csv
import datetime as dt
import json
import math
import os
import time
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
DAY = dt.timedelta(days=1)
TREND_DAYS = 30
TREND_UP, TREND_DN = 0.05, -0.05
VOL_DAYS = 7
VOL_HIST_DAYS = 365
VOL_TOP_Q = 0.75
MIN_HIST_DAYS = 60
NEED_DAYS = VOL_HIST_DAYS + VOL_DAYS + TREND_DAYS + 5
UPBIT_DAYS = "https://api.upbit.com/v1/candles/days"
FETCH_EVERY_H = 6
TIMEOUT = 15


def day_start_kst(t):
    """t 가 속한 일봉(KST 09:00 시작)의 시작 시각"""
    t = t.astimezone(KST)
    d = t.replace(hour=9, minute=0, second=0, microsecond=0)
    return d if t >= d else d - DAY


def daily_from_5m(T, C):
    """5분봉 시각·종가 → [(일봉 시작 KST, 종가)] (그 날 마지막 5분봉 종가)"""
    out = {}
    for t, c in zip(T, C):
        out[day_start_kst(t)] = c          # 시각순이므로 마지막 값이 종가
    return sorted(out.items())


class Regime:
    def __init__(self, daily):
        """daily: [(일봉 시작 KST, 종가)] 시각순"""
        self.ds = [d for d, _ in daily]
        self.cl = [c for _, c in daily]
        lr = [None] + [math.log(self.cl[i] / self.cl[i - 1]) if self.cl[i - 1] > 0 else None for i in range(1, len(self.cl))]
        self.vol = [None] * len(self.cl)
        for i in range(VOL_DAYS, len(self.cl)):
            xs = lr[i - VOL_DAYS + 1:i + 1]
            if None in xs:
                continue
            m = sum(xs) / len(xs)
            self.vol[i] = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

    def label(self, t):
        """시각 t → dict(trend, ret30, vol7, vol_pct, high_vol, hist_days) 또는 None(일봉 부족)"""
        # t 이전에 완성된 마지막 일봉: 시작 + 1일 ≤ t
        k = bisect.bisect_right(self.ds, t - DAY) - 1
        if k < TREND_DAYS:
            return None
        r30 = self.cl[k] / self.cl[k - TREND_DAYS] - 1
        trend = "상승" if r30 > TREND_UP else ("하락" if r30 < TREND_DN else "횡보")
        v = self.vol[k]
        hist = [x for x in self.vol[max(0, k - VOL_HIST_DAYS + 1):k + 1] if x is not None]
        pct = hv = None
        if v is not None and len(hist) >= MIN_HIST_DAYS:
            pct = sum(1 for x in hist if x <= v) / len(hist)
            srt = sorted(hist)
            q = srt[min(len(srt) - 1, int(math.ceil(VOL_TOP_Q * len(srt))) - 1)]
            hv = v >= q
        return dict(trend=trend, ret30=r30, vol7=v, vol_pct=pct, high_vol=hv, hist_days=len(hist))


# ---------------- 실시간 일봉 (data/btc_daily.csv) ----------------

def load_daily(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                t = dt.datetime.strptime(r["day_start_kst"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                out.append((t, float(r["close"])))
            except (KeyError, ValueError):
                continue
    return sorted(dict(out).items())


def save_daily(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day_start_kst", "close", "source"])
        for t, c in rows:
            w.writerow([t.strftime("%Y-%m-%d %H:%M"), repr(c) if c != int(c) else str(int(c)), "upbit KRW-BTC days"])
    os.replace(tmp, path)


def _fetch_days(count, to=None, timeout=TIMEOUT):
    q = f"?market=KRW-BTC&count={count}" + (f"&to={to.astimezone(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}" if to else "")
    req = urllib.request.Request(UPBIT_DAYS + q, headers={"Accept": "application/json",
                                                         "User-Agent": "btc-paper-bot (read-only candles)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    out = []
    for x in d if isinstance(d, list) else []:
        t = dt.datetime.strptime(x["candle_date_time_kst"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST)
        out.append((t, float(x["trade_price"])))
    return out


def update_daily(path, now, log=None, fetch=None, budget_s=20):
    """완성된 일봉만 저장(진행 중 일봉 제외, 이미 있는 날은 덮어쓰지 않음). 실패해도 예외 없이 기존 값 반환."""
    rows = dict(load_daily(path))
    fetch = fetch or _fetch_days
    cur = day_start_kst(now)                      # 진행 중 일봉 시작
    last = max(rows) if rows else None
    try:
        mt = os.path.getmtime(path) if os.path.exists(path) else 0
    except OSError:
        mt = 0
    fresh = last is not None and last >= cur - DAY       # 어제 일봉까지 있음
    if fresh and len(rows) >= NEED_DAYS:
        return sorted(rows.items())
    if mt and time.time() - mt < FETCH_EVERY_H * 3600:   # 최근 6시간 안에 이미 시도함(성공·실패 모두 파일 시각으로 표시)
        return sorted(rows.items())
    t0 = time.time()
    try:
        to = None
        got = 0
        for _ in range(3):
            if time.time() - t0 > budget_s:
                break
            part = fetch(200, to)
            if not part:
                break
            for t, c in part:
                if t < cur and t not in rows:
                    rows[t] = c
                    got += 1
            oldest = min(t for t, _ in part)
            if len(rows) >= NEED_DAYS or (last is not None and oldest <= last):
                break
            to = oldest
        save_daily(path, sorted(rows.items()))
        if log:
            log(f"BTC 일봉(국면 라벨용) {got}개 추가 (총 {len(rows)}개)")
    except Exception as e:
        if log:
            log(f"BTC 일봉 받기 실패(국면 라벨 빈칸 가능, 6시간 뒤 다시): {str(e)[:160]}", warn=True)
        try:
            save_daily(path, sorted(rows.items()))     # 시도 시각 표시(실패해도 매 실행 다시 부르지 않게)
        except Exception:
            pass
    return sorted(rows.items())
