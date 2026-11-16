# -*- coding: utf-8 -*-
"""
비트코인 5분봉 모의투자 원칙 v1.4.1 — 신호·체결·장부 계산 엔진

tasks\\btc-backtest-v14\\backtest_v14.py 의 run() 로직을 그대로 옮기고 아래만 추가했다.
  1) start_time 이전 봉은 지표 준비용(워밍업)으로만 쓰고 진입하지 않는다.
  2) 데이터 끝에 남은 포지션은 청산하지 않고 '현재 포지션'으로 돌려준다(close_at_end=False).
  3) 알림용 이벤트(진입·T1 반익절·청산·하루 한도 발동)를 기록한다.
  4) 파라미터는 config.json 에서 읽는다(load_params).

체결 가정 (백테스트와 동일)
  - 봉 마감에 신호 → 다음 봉 시가 + 슬리피지로 진입
  - 손절·지정가는 고가/저가로 판정, 한 봉에서 둘 다 닿으면 손절 먼저(보수적)
  - 진입한 그 봉 안에서도 손절·T1 판정을 하고, 전량 청산되면 상태를 정상 초기화한다.
    (파인스크립트의 '진입 봉 즉시 청산 시 신규 진입이 영원히 막히는 버그'가 없음 — result.md 7장)
  - 수수료는 진입·청산 각각 fee(기본 0.05%), 슬리피지는 시장가·손절에만 slip(기본 1틱=1,000원)

표준 라이브러리만 사용.
"""
import csv
import datetime as dt
import json
import math

KST = dt.timezone(dt.timedelta(hours=9))

# v1.4.1 기본값 (config.json 에 없는 키는 이 값을 쓴다)
DEFAULTS = dict(
    riskPct=0.25,        # 기본 1회 위험 % (v1.4: 1.0 → v1.4.1: 0.25)
    redPct=0.15,         # 축소 위험 % — 1시간 방향 반대·시험 셋업(B+, C) (v1.4: 0.5 → 0.15)
    maxLev=10.0,         # 레버리지 상한(배)
    minStopP=0.40,       # 최소 손절폭 % (v1.4: 0.15 → v1.4.1: 0.40, 왕복 비용 0.25R 이하)
    maxStopP=1.5,        # 최대 손절폭 %
    bufP=0.05,           # 손절 여유 %
    gradeMode="A+B",     # 확인 캔들 등급: "A+B" 또는 "A만"
    useZone=True, useBp=True, useC=True,
    allowLong=True, allowShort=True,
    swLen=3,             # 스윙 판단 좌우 봉 수
    zoneTolP=0.10,       # 구간 허용 오차 %
    slopeThr=0.02,       # 방향 판단 20SMA 기울기 기준 %
    chaseP=0.8,          # 추격 금지 5분 20SMA 이격 %
    t1MinR=0.8, t1MaxR=3.0, t1FbR=1.5,
    volExitX=2.5,        # 잔량 청산 거래량 배수
    useOppEng=True,      # 잔량 청산: 반대 장악형
    exitMode="v14",      # "v14" 반익반본 / "v13" 1.5R 전량
    dayLossP=2.0,        # 하루 손실 한도 %
    maxConsec=2,         # 연속 손절 한도
    fee=0.0005,          # 수수료(편도) 0.05%
    slip=1000.0,         # 슬리피지(원) — KRW-BTC 1틱
    capital=1_000_000.0, # 초기 자금
)


def load_params(config):
    """config.json(dict) → 엔진 파라미터. '_'로 시작하는 키와 모르는 키는 무시."""
    P = dict(DEFAULTS)
    for k, v in config.items():
        if k in P:
            P[k] = v
    if "initial_capital" in config:
        P["capital"] = float(config["initial_capital"])
    for k in ("riskPct", "redPct", "maxLev", "minStopP", "maxStopP", "bufP", "zoneTolP", "slopeThr",
              "chaseP", "t1MinR", "t1MaxR", "t1FbR", "volExitX", "dayLossP", "fee", "slip", "capital"):
        P[k] = float(P[k])
    P["swLen"] = int(P["swLen"]); P["maxConsec"] = int(P["maxConsec"])
    return P


def parse_kst(s):
    """'2026-11-16 14:05' 또는 '2026-11-16 14:05:00' → KST aware datetime"""
    s = s.strip().replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(s[:19], f).replace(tzinfo=KST)
        except ValueError:
            pass
    raise ValueError(f"시각 형식 오류: {s!r}")


def load_csv(path):
    """time_kst,open,high,low,close,volume[,source] CSV → (T,O,H,L,C,V)"""
    T, O, H, L, C, V = [], [], [], [], [], []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            T.append(parse_kst(r["time_kst"]))
            O.append(float(r["open"])); H.append(float(r["high"])); L.append(float(r["low"]))
            C.append(float(r["close"])); V.append(float(r["volume"]))
    return T, O, H, L, C, V

# ============================== 지표 ==============================

def sma(a, n):
    out = [None] * len(a)
    s = 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi_pine(c, n=14):
    """ta.rsi (Wilder RMA, 첫 값은 SMA로 시작)"""
    out = [None] * len(c)
    up = dn = None
    gains, losses = [], []
    for i in range(1, len(c)):
        ch = c[i] - c[i - 1]
        g, l_ = max(ch, 0.0), max(-ch, 0.0)
        if up is None:
            gains.append(g); losses.append(l_)
            if len(gains) == n:
                up, dn = sum(gains) / n, sum(losses) / n
            else:
                continue
        else:
            up = (g + (n - 1) * up) / n
            dn = (l_ + (n - 1) * dn) / n
        out[i] = 100.0 if dn == 0 else (0.0 if up == 0 else 100 - 100 / (1 + up / dn))
    return out


def htf(T, O, H, L, C, V, minutes):
    """5분봉 → 상위봉(KST 정시 기준). 각 5분봉이 속한 상위봉 번호도 반환"""
    sec = minutes * 60
    bars, idx = [], []
    cur = None
    for i, t in enumerate(T):
        b = int(t.timestamp()) // sec
        if cur is None or b != cur:
            cur = b
            bars.append([O[i], H[i], L[i], C[i], V[i]])
        else:
            x = bars[-1]
            x[1] = max(x[1], H[i]); x[2] = min(x[2], L[i]); x[3] = C[i]; x[4] += V[i]
        idx.append(len(bars) - 1)
    return bars, idx


def htf_slope_prev(bars, idx):
    """20SMA 5봉 기울기 % — 진행 중 상위봉이 아니라 직전 확정봉 값 (파인 lookahead_on + [1])"""
    cl = [b[3] for b in bars]
    s = sma(cl, 20)
    v = [None] * len(cl)
    for j in range(len(cl)):
        if j >= 5 and s[j] is not None and s[j - 5] is not None:
            v[j] = (s[j] - s[j - 5]) / s[j - 5] * 100
    return [v[j - 1] if j >= 1 else None for j in idx]


def htf_fvg_prev(bars, idx):
    """상위봉 FVG 상태 — 직전 확정 상위봉까지의 FVG"""
    bT = bB = sT = sB = None
    after = []
    for j, (o, h, l, c, v) in enumerate(bars):
        if bB is not None and l < bB:
            bT = bB = None
        if sT is not None and h > sT:
            sT = sB = None
        if j >= 2:
            if l > bars[j - 2][1] and bars[j - 1][3] > bars[j - 1][0]:
                bT, bB = l, bars[j - 2][1]
            if h < bars[j - 2][2] and bars[j - 1][3] < bars[j - 1][0]:
                sT, sB = bars[j - 2][2], h
        after.append((bT, bB, sT, sB))
    return [after[j - 1] if j >= 1 else (None, None, None, None) for j in idx]


class Data:
    """봉 배열 + 미리 계산한 지표"""

    def __init__(self, T, O, H, L, C, V):
        self.T, self.O, self.H, self.L, self.C, self.V = T, O, H, L, C, V
        self.n = len(T)
        self.sma20 = sma(C, 20)
        self.rsi = rsi_pine(C, 14)
        self.volAvg = sma(V, 20)
        self.vol3 = sma(V, 3)
        b15, i15 = htf(T, O, H, L, C, V, 15)
        b60, i60 = htf(T, O, H, L, C, V, 60)
        self.slope15 = htf_slope_prev(b15, i15)
        self.slope60 = htf_slope_prev(b60, i60)
        self.fvg15 = htf_fvg_prev(b15, i15)
        self.gaps = sum(1 for i in range(1, self.n) if (T[i] - T[i - 1]).total_seconds() != 300)

    @classmethod
    def from_csv(cls, path):
        return cls(*load_csv(path))

# ============================== 시뮬레이션 ==============================

def trade_id(t_entry, side):
    """거래 고유 ID (알림 중복 방지용). 진입 시각 + 방향"""
    return f"{t_entry.strftime('%Y%m%d%H%M')}{'L' if side == 1 else 'S'}"


def run(D, P, start_time=None, close_at_end=False):
    """
    D: Data, P: 파라미터(dict), start_time: 이 시각 이후 봉부터 진입 허용(None이면 처음부터)
    close_at_end: True면 백테스트처럼 마지막 종가로 남은 포지션 정리('기간 종료')

    반환 dict
      trades     : 청산 완료된 거래 목록 (dict)
      position   : 현재 보유 포지션(dict) 또는 None
      pending    : 다음 봉 시가 진입 예정 신호(dict) 또는 None
      pending_close : 다음 봉 시가 청산 예정 사유 또는 None
      curve      : 봉별 평가 잔고 (start 이후 봉만)
      curve_time : curve 각 값의 봉 시각
      events     : 알림용 이벤트 목록
      realized, final(실현 잔고), equity(평가 잔고), unrealized
      day        : 마지막 봉 기준 하루 상태 (date, realized, start_equity, consec, stop, reason)
      dayStopDays: 신규 금지가 발동한 날짜 집합
    """
    T, O, H, L, C, V = D.T, D.O, D.H, D.L, D.C, D.V
    n, sw, tol = D.n, P["swLen"], P["zoneTolP"] / 100
    fee, slip, eq0 = P["fee"], P["slip"], P["capital"]

    realized = 0.0
    phV, phB, phVol, plV, plB, plVol = [], [], [], [], [], []
    fBT = fBB = fST = fSB = None
    oBT = oBB = oST = oSB = None
    lastPH = lastPL = None
    bpUsedL = bpUsedS = cUsedL = cUsedS = -1
    dayKey = None; dayStartNP = 0.0; dayStartEq = eq0; consec = 0
    dayStop = False
    dayStopDays = set()
    pos = None; pending = None; pending_close = None
    trades, curve, curve_time, events = [], [], [], []
    equity = eq0; unreal = 0.0

    def close_part(qty, price, reason, i):
        nonlocal realized, pos
        s = pos["side"]
        pnl = s * (price - pos["entry"]) * qty - price * qty * fee - pos["entry"] * qty * fee
        realized += pnl
        pos["pnl"] += pnl
        pos["open"] = round(pos["open"] - qty, 8)
        pos["exits"].append(f"{reason}@{price:,.0f}x{qty:.4f}({T[i].strftime('%m-%d %H:%M')})")
        pos["fills"].append(dict(reason=reason, price=price, qty=qty, pnl=pnl, time=T[i]))
        done = pos["open"] <= 1e-9
        events.append(dict(type="exit" if done else "t1", id=pos["id"] + (":X" if done else ":T1"),
                           time=T[i], side=s, tag=pos["tag"], grade=pos["grade"], reason=reason,
                           price=price, qty=qty, part_pnl=pnl, total_pnl=pos["pnl"]))
        if done:
            risk_won = pos["qty"] * abs(pos["entry"] - pos["stop0"])
            pos["R"] = pos["pnl"] / risk_won if risk_won > 0 else 0.0
            pos["exit_time"] = T[i]
            pos["exit_bar"] = i
            pos["exit_reason"] = reason
            trades.append(pos)
            pos = None
            return True
        return False

    def next_above(arr, px):
        b = None
        for x in arr:
            if x > px and (b is None or x < b):
                b = x
        return b

    def next_below(arr, px):
        b = None
        for x in arr:
            if x < px and (b is None or x > b):
                b = x
        return b

    for i in range(n):
        o, h, l, c, v = O[i], H[i], L[i], C[i], V[i]
        can_trade = start_time is None or T[i] >= start_time
        closed_now = []
        # ---------- 1) 이번 봉 안에서의 체결 ----------
        if pending is not None:
            pd = pending; pending = None
            fill = o + pd["side"] * slip
            pos = dict(pd, entry=fill, open=pd["qty"], pnl=0.0, exits=[], fills=[], entry_bar=i,
                       entry_time=T[i], t1Done=False, t1Hit=False, nxt=None, stop0=pd["stop"],
                       lev=pd["qty"] * fill / pd["eq"], id=trade_id(T[i], pd["side"]))
            events.append(dict(type="entry", id=pos["id"] + ":E", time=T[i], side=pos["side"], tag=pos["tag"],
                               grade=pos["grade"], price=fill, stop=pos["stop"], t1=pos["t1"],
                               qty=pos["qty"], lev=pos["lev"]))
        if pos is not None and pending_close is not None:
            s = pos["side"]
            tr = pos
            if close_part(pos["open"], o - s * slip, pending_close, i):
                closed_now.append(tr)
        pending_close = None
        if pos is not None:
            # 진입한 봉에서도 손절·T1을 판정한다. 전량 청산되면 pos=None 으로 정상 초기화
            # → 다음 봉부터 다시 신규 진입 가능 (파인스크립트 멈춤 버그 없음)
            s = pos["side"]; stp = pos["stop"]
            if P["exitMode"] == "v13":
                tgt = pos["t1"]
            else:
                tgt = pos["t1"] if not pos["t1Hit"] else pos["nxt"]
            stop_hit = (l <= stp) if s == 1 else (h >= stp)
            lim_hit = tgt is not None and ((h >= tgt) if s == 1 else (l <= tgt))
            tr = pos
            if stop_hit:                       # 같은 봉에서 둘 다 닿으면 손절 먼저 (보수적)
                px = (min(o, stp) - slip) if s == 1 else (max(o, stp) + slip)
                reason = "본절" if pos["t1Hit"] else "손절"
                if close_part(pos["open"], px, reason, i):
                    closed_now.append(tr)
            elif lim_hit:
                px = max(o, tgt) if s == 1 else min(o, tgt)
                if P["exitMode"] == "v13":
                    if close_part(pos["open"], px, "1.5R 익절", i):
                        closed_now.append(tr)
                elif not pos["t1Hit"]:
                    half = math.floor(pos["qty"] * 0.5 * 10000) / 10000
                    if half <= 0 or half >= pos["open"]:
                        half = pos["open"]
                    pos["t1Hit"] = True
                    if close_part(half, px, "T1 반익절", i):
                        closed_now.append(tr)
                else:
                    if close_part(pos["open"], px, "다음 스윙", i):
                        closed_now.append(tr)

        # ---------- 2) 봉 마감 지표 ----------
        sma5, rsi, volAvg = D.sma20[i], D.rsi[i], D.volAvg[i]
        body = abs(c - o)
        lowW = min(o, c) - l
        upW = h - max(o, c)
        if i >= 1:
            o1, c1 = O[i - 1], C[i - 1]
            bullEng = c > o and c1 < o1 and o <= c1 and c >= o1
            bearEng = c < o and c1 > o1 and o >= c1 and c <= o1
        else:
            bullEng = bearEng = False
        bullB = c > o or (lowW >= 2 * body and c >= (h + l) / 2)
        bearB = c < o or (upW >= 2 * body and c <= (h + l) / 2)
        AB = P["gradeMode"] == "A+B"
        okL = bullEng or (AB and bullB)
        okS = bearEng or (AB and bearB)

        def _dir(x):
            if x is None:
                return 0
            return 1 if x > P["slopeThr"] else (-1 if x < -P["slopeThr"] else 0)
        dir15, dir60 = _dir(D.slope15[i]), _dir(D.slope60[i])
        hBT, hBB, hST, hSB = D.fvg15[i]

        volSup = (i >= 1 and v > V[i - 1] and D.vol3[i - 1] is not None and D.volAvg[i - 1] is not None
                  and D.vol3[i - 1] < D.volAvg[i - 1])
        r1_ = D.rsi[i - 1] if i >= 1 else None
        rsiSupL = r1_ is not None and rsi is not None and 40 <= r1_ <= 55 and rsi > r1_
        rsiSupS = r1_ is not None and rsi is not None and 45 <= r1_ <= 60 and rsi < r1_
        htfL = hBT is not None and l <= hBT and h >= hBB
        htfS = hST is not None and h >= hSB and l <= hST
        supL = volSup or rsiSupL or dir60 == 1 or htfL
        supS = volSup or rsiSupS or dir60 == -1 or htfS
        noChase = (sma5 is not None and rsi is not None and abs(c - sma5) / sma5 * 100 < P["chaseP"]
                   and 25 < rsi < 75)

        # 스윙 (좌우 sw봉 피벗) — 확정은 sw봉 뒤
        if i >= 2 * sw:
            k = i - sw
            hk, lk = H[k], L[k]
            isPH = all(H[k + j] < hk for j in range(1, sw + 1)) and all(H[k - j] <= hk for j in range(1, sw + 1))
            isPL = all(L[k + j] > lk for j in range(1, sw + 1)) and all(L[k - j] >= lk for j in range(1, sw + 1))
            if isPH:
                phV.append(hk); phB.append(k); phVol.append(V[k])
                if len(phV) > 12:
                    phV.pop(0); phB.pop(0); phVol.pop(0)
            if isPL:
                plV.append(lk); plB.append(k); plVol.append(V[k])
                if len(plV) > 12:
                    plV.pop(0); plB.pop(0); plVol.pop(0)
        else:
            isPH = isPL = False
        prevPH, prevPL = lastPH, lastPL
        if isPH:
            lastPH = H[i - sw]
        if isPL:
            lastPL = L[i - sw]
        bosUp = (lastPH is not None and prevPH is not None and i >= 1 and c > lastPH and C[i - 1] <= prevPH)
        bosDn = (lastPL is not None and prevPL is not None and i >= 1 and c < lastPL and C[i - 1] >= prevPL)

        pfBT, pfBB, pfST, pfSB = fBT, fBB, fST, fSB
        poBT, poBB, poST, poSB = oBT, oBB, oST, oSB
        if i >= 2:
            if l > H[i - 2] and C[i - 1] > O[i - 1]:
                fBT, fBB = l, H[i - 2]
            if h < L[i - 2] and C[i - 1] < O[i - 1]:
                fST, fSB = L[i - 2], h
        if fBB is not None and c < fBB:
            fBT = fBB = None
        if fST is not None and c > fST:
            fST = fSB = None
        if bosUp:
            for j in range(1, 16):
                if i - j >= 0 and C[i - j] < O[i - j]:
                    oBT, oBB = H[i - j], L[i - j]
                    break
        if bosDn:
            for j in range(1, 16):
                if i - j >= 0 and C[i - j] > O[i - j]:
                    oST, oSB = H[i - j], L[i - j]
                    break
        if oBB is not None and c < oBB:
            oBT = oBB = None
        if oST is not None and c > oST:
            oST = oSB = None

        # 구간 판정
        sup = [x for x in phV + plV if x < c and l <= x * (1 + tol)]
        lvlL = max(sup) if sup else None
        fvgTL = pfBT is not None and l <= pfBT and c > pfBB
        obTL = poBT is not None and l <= poBT and c > poBB
        zoneL = lvlL is not None or fvgTL or obTL
        zoneBotL = min(lvlL * (1 - tol) if lvlL is not None else c, pfBB if fvgTL else c, poBB if obTL else c)
        res = [x for x in phV + plV if x > c and h >= x * (1 - tol)]
        lvlS = min(res) if res else None
        fvgTS = pfSB is not None and h >= pfSB and c < pfST
        obTS = poSB is not None and h >= poSB and c < poST
        zoneS = lvlS is not None or fvgTS or obTS
        zoneTopS = max(lvlS * (1 + tol) if lvlS is not None else c, pfST if fvgTS else c, poST if obTS else c)

        # 🅱️+ 거래량 단계 돌파
        bpL = bpS = False; bpLineNextL = bpLineNextS = None; bpKeyL = bpKeyS = -1
        if len(phV) >= 2 and volAvg is not None:
            v1, v2, b1, b2, vo1, vo2 = phV[-2], phV[-1], phB[-2], phB[-1], phVol[-2], phVol[-1]
            if v2 > v1 and b2 > b1 and b2 - b1 <= 100 and vo2 > vo1 and b2 != bpUsedL:
                sl_ = (v2 - v1) / (b2 - b1)
                ln = v2 + sl_ * (i - b2)
                bpL = c > ln and o < ln and v > vo2 and v >= 2 * volAvg
                bpLineNextL = ln + sl_; bpKeyL = b2
        if len(plV) >= 2 and volAvg is not None:
            w1, w2, c1_, c2_, wo1, wo2 = plV[-2], plV[-1], plB[-2], plB[-1], plVol[-2], plVol[-1]
            if w2 < w1 and c2_ > c1_ and c2_ - c1_ <= 100 and wo2 > wo1 and c2_ != bpUsedS:
                sl2 = (w2 - w1) / (c2_ - c1_)
                ln2 = w2 + sl2 * (i - c2_)
                bpS = c < ln2 and o > ln2 and v > wo2 and v >= 2 * volAvg
                bpLineNextS = ln2 + sl2; bpKeyS = c2_

        # 🅲 가짜 이탈 반전
        low3 = min(L[max(0, i - 2):i + 1]); high3 = max(H[max(0, i - 2):i + 1])
        lastPLv = plV[-1] if plV else None; lastPLb = plB[-1] if plB else -1
        lastPHv = phV[-1] if phV else None; lastPHb = phB[-1] if phB else -1
        cL = lastPLv is not None and low3 < lastPLv and c > lastPLv and lastPLb != cUsedL and i - 3 > lastPLb + sw
        cS = lastPHv is not None and high3 > lastPHv and c < lastPHv and lastPHb != cUsedS and i - 3 > lastPHb + sw

        # ---------- 3) 하루 한도 (KST 날짜 기준) ----------
        unreal = 0.0
        if pos is not None:
            unreal = pos["side"] * (c - pos["entry"]) * pos["open"] - pos["entry"] * pos["open"] * fee
        equity = eq0 + realized + unreal
        dk = T[i].date()
        if dk != dayKey:
            dayKey = dk; dayStartNP = realized; dayStartEq = equity; consec = 0
        for tr in closed_now:
            consec = consec + 1 if tr["pnl"] < 0 else 0
        dayReal = realized - dayStartNP
        hitLoss = dayReal <= -dayStartEq * P["dayLossP"] / 100
        hitConsec = consec >= P["maxConsec"]
        dayStop = hitLoss or hitConsec
        if dayStop:
            if dk not in dayStopDays and can_trade:
                why = f"{consec}연속 손절" if hitConsec else f"하루 손실 {P['dayLossP']:g}% 도달"
                events.append(dict(type="daystop", id=f"D{dk.strftime('%Y%m%d')}", time=T[i], reason=why,
                                   day_pnl=dayReal))
            dayStopDays.add(dk)
        baseRisk = equity * P["riskPct"] / 100
        redRisk = equity * P["redPct"] / 100
        lockCap = dayReal - baseRisk if dayReal >= 2 * baseRisk else None

        def fcap(r):
            return r if lockCap is None else min(r, lockCap)

        # ---------- 4) 진입 판단 (봉 마감, 다음 봉 시가 체결) ----------
        if pos is None and pending is None and not dayStop and can_trade:
            sig = grd = ""; stp = rm = None; side = 0
            if P["allowLong"] and P["useZone"] and dir15 == 1 and zoneL and okL and supL and noChase:
                sig, grd = "구간", ("A" if bullEng else "B")
                stp = (min(l, L[i - 1]) if bullEng else min(l, zoneBotL)) * (1 - P["bufP"] / 100)
                rm = fcap(redRisk if dir60 == -1 else baseRisk); side = 1
            elif P["allowLong"] and P["useBp"] and dir15 != -1 and bpL:
                sig = "B+"; stp = l * (1 - P["bufP"] / 100); rm = fcap(redRisk); side = 1
            elif P["allowLong"] and P["useC"] and dir15 != -1 and cL:
                sig = "C"; stp = low3 * (1 - P["bufP"] / 100); rm = fcap(redRisk); side = 1
            elif P["allowShort"] and P["useZone"] and dir15 == -1 and zoneS and okS and supS and noChase:
                sig, grd = "구간", ("A" if bearEng else "B")
                stp = (max(h, H[i - 1]) if bearEng else max(h, zoneTopS)) * (1 + P["bufP"] / 100)
                rm = fcap(redRisk if dir60 == 1 else baseRisk); side = -1
            elif P["allowShort"] and P["useBp"] and dir15 != 1 and bpS:
                sig = "B+"; stp = h * (1 + P["bufP"] / 100); rm = fcap(redRisk); side = -1
            elif P["allowShort"] and P["useC"] and dir15 != 1 and cS:
                sig = "C"; stp = high3 * (1 + P["bufP"] / 100); rm = fcap(redRisk); side = -1

            if side != 0:
                dist = abs(c - stp)
                dp = dist / c * 100
                t1 = next_above(phV, c) if side == 1 else next_below(plV, c)
                t1 = c + side * P["t1FbR"] * dist if t1 is None else t1
                gap = abs(t1 - c)
                if P["minStopP"] <= dp <= P["maxStopP"] and gap >= P["t1MinR"] * dist:
                    if gap > P["t1MaxR"] * dist:
                        t1 = c + side * P["t1FbR"] * dist
                    if P["exitMode"] == "v13":
                        t1 = c + side * 1.5 * dist
                    q = rm / dist if dist > 0 and rm > 0 else 0.0
                    q = math.floor(min(q, equity * P["maxLev"] / c) * 10000) / 10000
                    if q > 0:
                        pending = dict(side=side, tag=sig, grade=grd, stop=stp, t1=t1, qty=q, eq=equity,
                                       sigClose=c, rm=rm, dir60=dir60, signal_time=T[i],
                                       bpLine=(bpLineNextL if side == 1 else bpLineNextS) if sig == "B+" else None)
                        if sig == "B+":
                            if side == 1: bpUsedL = bpKeyL
                            else: bpUsedS = bpKeyS
                        if sig == "C":
                            if side == 1: cUsedL = lastPLb
                            else: cUsedS = lastPHb

        # ---------- 5) 보유 중 관리 ----------
        if pos is not None:
            s = pos["side"]
            if pos["tag"] == "B+" and pos["entry_bar"] == i and pos["bpLine"] is not None:
                if (s == 1 and c < pos["bpLine"]) or (s == -1 and c > pos["bpLine"]):
                    pending_close = "돌파 실패"
            if P["exitMode"] == "v14":
                if pos["t1Hit"] and not pos["t1Done"]:
                    pos["t1Done"] = True
                    pos["stop"] = pos["entry"]          # 본절 (position_avg_price)
                if pos["t1Done"]:
                    pos["nxt"] = next_above(phV, pos["t1"]) if s == 1 else next_below(plV, pos["t1"])
                    opp = bearEng if s == 1 else bullEng
                    if (P["useOppEng"] and opp) or (volAvg is not None and v >= P["volExitX"] * volAvg):
                        pending_close = pending_close or ("반대 장악형" if (P["useOppEng"] and opp) else "거래량 급증")

        if can_trade:
            curve.append(equity); curve_time.append(T[i])

    # 데이터 끝에 남은 포지션: 백테스트 비교용일 때만 마지막 종가로 정리
    if pos is not None and close_at_end:
        close_part(pos["open"], C[-1], "기간 종료", n - 1)
        unreal = 0.0
        equity = eq0 + realized
    if pos is not None:
        unreal = pos["side"] * (C[-1] - pos["entry"]) * pos["open"] - pos["entry"] * pos["open"] * fee
        equity = eq0 + realized + unreal

    day = dict(date=dayKey, realized=realized - dayStartNP, start_equity=dayStartEq, consec=consec,
               stop=dayStop, reason=None)
    if dayStop:
        day["reason"] = (f"{consec}연속 손절" if consec >= P["maxConsec"]
                         else f"하루 손실 {P['dayLossP']:g}% 도달")
    return dict(trades=trades, position=pos, pending=pending, pending_close=pending_close,
                curve=curve, curve_time=curve_time, events=events, dayStopDays=dayStopDays,
                realized=realized, final=eq0 + realized, equity=equity, unrealized=unreal, day=day,
                last_close=C[-1] if n else None)


def stats(trades, curve=None):
    """거래 수, 승률, 평균 R, 합계 손익, 최대낙폭(MDD, %)"""
    N = len(trades)
    out = dict(N=N, win=0.0, avgR=0.0, sumR=0.0, pnl=0.0, mdd=0.0, pf=0.0)
    if N:
        Rs = [t["R"] for t in trades]
        wins = sum(1 for r in Rs if r > 0)
        gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
        out.update(win=wins / N * 100, avgR=sum(Rs) / N, sumR=sum(Rs), pnl=sum(t["pnl"] for t in trades),
                   pf=(gp / gl) if gl > 0 else float("inf"))
    if curve:
        peak, mdd = -1e18, 0.0
        for e in curve:
            peak = max(peak, e)
            mdd = max(mdd, (peak - e) / peak * 100)
        out["mdd"] = mdd
    return out
