# -*- coding: utf-8 -*-
"""
비트코인 5분봉 모의투자 원칙 v1.4.1 — 신호·체결·장부 계산 엔진

tasks\\btc-backtest-v14\\backtest_v14.py 의 run() 로직을 그대로 옮기고 아래만 추가했다.
  1) start_time 이전 봉은 지표 준비용(워밍업)으로만 쓰고 진입하지 않는다.
  2) 데이터 끝에 남은 포지션은 청산하지 않고 '현재 포지션'으로 돌려준다(close_at_end=False).
  3) 알림용 이벤트(진입·T1 반익절·청산·하루 한도 발동)를 기록한다.
  4) 파라미터는 config.json 에서 읽는다(load_params).
  5) [v1.4.1-mm] 쉽알남 자금 관리: 거래 시드 기준 고정 손실(1R), 하루 목표·목표 후 축소, 첫 매매 수익 시
     절반, 주간 출금, 1회 위험 5% 절대 상한. 신호·손절·T1·반익반본·잔량 청산 로직은 손대지 않았다.
     새 기능을 모두 끄면(dayTargetP=0, firstWinHalf=false, withdrawPct=0) 기존 v1.4.1 과 거래가 완전히 같다.

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
    # ---- 쉽알남 자금 관리 (v1.4.1-mm) — 아래 기본값은 '모두 끔' = 기존 v1.4.1 과 완전히 같은 결과 ----
    dayTargetP=0.0,          # 하루 목표 % (0 = 끔). 그날 실현손익 ≥ 목표면 일반 크기 신규 진입 금지
    afterTargetSizing=True,  # 목표 달성 후: True = '손절돼도 목표가 남는 크기'로만 허용, False = 그날 진입 안 함
    afterTargetMinFrac=0.2,  # 목표 후 허용 위험이 1R(고정 손실)의 이 비율 미만이면 진입 안 함
    firstWinHalf=False,      # 그날 첫 매매가 수익이면 그날 남은 매매 수량 절반
    withdrawPct=0.0,         # 매주 월요일 00:00(KST) 지난주 실현 수익의 이 % 출금 (0 = 끔)
    maxRiskCapP=5.0,         # 1회 위험 절대 상한 % (5% 초과는 코드로 금지 — 설정해도 5로 잘림)
    # ---- v2.0 기반 시설 이식(2026-11-17) — 아래 기본값이면 기존과 완전히 같은 결과 ----
    slipMode="fixed",        # "fixed" = slip 원(기존) / "tick" = 체결가의 호가 단위 × slipTicks (여러 코인용)
    slipTicks=1.0,           # slipMode "tick" 일 때 틱 수
    eventBlock=False,        # True 면 경제 일정(events) 발표 eventBeforeMin 전 ~ eventAfterMin 뒤 신규 진입 금지
    eventBeforeMin=30,
    eventAfterMin=15,
)


def tick_size(price):
    """원화 마켓 호가 단위(업비트 기준표). BTC(200만 원 이상)는 항상 1,000원 = 기존 slip 기본값과 같음"""
    p = abs(price)
    for lo, t in ((2_000_000, 1000.0), (1_000_000, 500.0), (500_000, 100.0), (100_000, 50.0), (10_000, 10.0),
                  (1_000, 1.0), (100, 0.1), (10, 0.01), (1, 0.001), (0.1, 0.0001)):
        if p >= lo:
            return t
    return 0.00001


def _pxs(x):
    """일지용 가격 문자열: 1,000 이상은 정수(천 단위 쉼표), 그 아래는 유효숫자 8자리"""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.8g}"


def load_events(path):
    """경제 일정 CSV(time_kst,name[,importance]) → [(KST 시각, 이름, 중요도)]. 빈 줄·'#' 줄 무시. 파일 없으면 []"""
    out = []
    try:
        f = open(path, encoding="utf-8-sig")
    except FileNotFoundError:
        return out
    with f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    for r in csv.DictReader(lines):
        t = (r.get("time_kst") or "").strip()
        if not t:
            continue
        out.append((parse_kst(t), (r.get("name") or "").strip(), (r.get("importance") or "").strip()))
    return sorted(out, key=lambda x: x[0])

HARD_MAX_RISK_P = 5.0        # 어떤 설정으로도 넘을 수 없는 1회 위험 상한 %


def load_params(config):
    """config.json(dict) → 엔진 파라미터. '_'로 시작하는 키와 모르는 키는 무시."""
    P = dict(DEFAULTS)
    for k, v in config.items():
        if k in P:
            P[k] = v
    if "initial_capital" in config:
        P["capital"] = float(config["initial_capital"])
    for k in ("riskPct", "redPct", "maxLev", "minStopP", "maxStopP", "bufP", "zoneTolP", "slopeThr",
              "chaseP", "t1MinR", "t1MaxR", "t1FbR", "volExitX", "dayLossP", "fee", "slip", "capital",
              "dayTargetP", "afterTargetMinFrac", "withdrawPct", "maxRiskCapP", "slipTicks", "eventBeforeMin",
              "eventAfterMin"):
        P[k] = float(P[k])
    P["eventBlock"] = bool(P["eventBlock"])
    P["swLen"] = int(P["swLen"]); P["maxConsec"] = int(P["maxConsec"])
    for k in ("firstWinHalf", "afterTargetSizing"):
        P[k] = bool(P[k])
    # 5% 초과 위험은 절대 금지: 상한 자체도 5%를 넘을 수 없고, riskPct·redPct 는 상한으로 자른다.
    P["maxRiskCapP"] = min(max(P["maxRiskCapP"], 0.0), HARD_MAX_RISK_P)
    P["riskPct"] = min(P["riskPct"], P["maxRiskCapP"])
    P["redPct"] = min(P["redPct"], P["maxRiskCapP"])
    P["withdrawPct"] = min(max(P["withdrawPct"], 0.0), 100.0)
    # 규칙 변경 이력: [{"until": "YYYY-MM-DD HH:MM", "rules": 설명, "params": {...}}, ...] (시간순)
    # 각 항목의 params 는 'until 이전' 봉에 적용, 마지막 until 이후엔 위의 현재 설정을 적용.
    # 이미 지나간 거래는 그때 규칙으로 기록되고, 보유 중이던 포지션은 그대로 이어진다.
    hist = []
    for h in config.get("rule_history") or []:
        base = dict(config); base.pop("rule_history", None); base.update(h.get("params", {}))
        hp = load_params(base)
        hist.append((parse_kst(h["until"]), hp))
    P["rule_history"] = sorted(hist, key=lambda x: x[0])
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


KST_OFFSET_SEC = 9 * 3600


def htf(T, O, H, L, C, V, minutes):
    """실행봉 → 상위봉(KST 00:00 기준 정렬). 각 실행봉이 속한 상위봉 번호도 반환.
    15분·1시간처럼 9시간을 나누어떨어지게 하는 길이는 UTC 기준 정렬(이전 코드)과 결과가 완전히 같다.
    4시간·12시간 등은 거래소(빗썸·업비트) 봉처럼 KST 자정 기준으로 묶인다."""
    sec = minutes * 60
    bars, idx = [], []
    cur = None
    for i, t in enumerate(T):
        b = (int(t.timestamp()) + KST_OFFSET_SEC) // sec
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

    def __init__(self, T, O, H, L, C, V, dir_min=15, aux_min=60, bar_min=5):
        """dir_min: 방향 상위봉(분, 기본 15), aux_min: 보조 상위봉(분, 기본 60), bar_min: 실행봉 길이(분).
        기본값(5분 실행, 15분 방향, 1시간 보조)이면 기존과 완전히 같다. 속성 이름 slope15/slope60/fvg15 는
        '방향/보조 상위봉' 뜻으로 그대로 쓴다(다른 시간봉 실행 연구용, 사전 등록서 v3 E1)."""
        self.T, self.O, self.H, self.L, self.C, self.V = T, O, H, L, C, V
        self.n = len(T)
        self.dir_min, self.aux_min, self.bar_min = dir_min, aux_min, bar_min
        self.sma20 = sma(C, 20)
        self.rsi = rsi_pine(C, 14)
        self.volAvg = sma(V, 20)
        self.vol3 = sma(V, 3)
        b15, i15 = htf(T, O, H, L, C, V, dir_min)
        b60, i60 = htf(T, O, H, L, C, V, aux_min)
        self.slope15 = htf_slope_prev(b15, i15)
        self.slope60 = htf_slope_prev(b60, i60)
        self.fvg15 = htf_fvg_prev(b15, i15)
        self.gaps = sum(1 for i in range(1, self.n) if (T[i] - T[i - 1]).total_seconds() != bar_min * 60)

    @classmethod
    def from_csv(cls, path):
        return cls(*load_csv(path))

# ============================== 시뮬레이션 ==============================

def trade_id(t_entry, side):
    """거래 고유 ID (알림 중복 방지용). 진입 시각 + 방향"""
    return f"{t_entry.strftime('%Y%m%d%H%M')}{'L' if side == 1 else 'S'}"


def run(D, P, start_time=None, close_at_end=False, events=None):
    """
    events: [(KST 시각, 이름, ...)] 경제 일정(load_events). P["eventBlock"] 이 True 일 때만 쓰인다(기본 꺼짐).
            진입 체결 시각(다음 봉 시가)이 [발표 − eventBeforeMin, 발표 + eventAfterMin) 안이면 신호를 내지 않는다.
    D: Data, P: 파라미터(dict), start_time: 이 시각 이후 봉부터 진입 허용(None이면 처음부터)
    close_at_end: True면 백테스트처럼 마지막 종가로 남은 포지션 정리('기간 종료')

    반환 dict
      trades     : 청산 완료된 거래 목록 (dict)
      position   : 현재 보유 포지션(dict) 또는 None
      pending    : 다음 봉 시가 진입 예정 신호(dict) 또는 None
      pending_close : 다음 봉 시가 청산 예정 사유 또는 None
      curve      : 봉별 '총자산'(거래 계좌 평가 잔고 + 출금 누적) — 출금은 손실이 아니므로 MDD 는 이걸로 잰다
      curve_acct : 봉별 거래 계좌 평가 잔고(출금 뺀 값)
      curve_time : curve 각 값의 봉 시각
      events     : 알림용 이벤트 목록
      realized, final(실현 잔고 = 초기 자금 + 실현손익 − 출금), equity(평가 잔고), unrealized
      withdrawn, withdrawals : 출금 누적액, 출금 내역
      seed       : 거래 시드(= 초기 자금 + 실현손익 − 출금 누적) — 1R(고정 손실) 계산 기준
      day        : 마지막 봉 기준 하루 상태 (date, realized, start_equity, consec, stop, reason, 목표·한도·다음 크기)
      dayStopDays: 신규 금지가 발동한 날짜 집합 (lossLimitDays: 그중 손실 한도, consecDays: 연속 손절)
      targetDays : 하루 목표를 달성한 날짜 집합

    자금 관리 (쉽알남 방식, P 로 켜고 끔)
      - 1R = 거래 시드 × riskPct%(1시간 반대·B+·C 는 redPct%). 수량 = 1R ÷ (진입가 − 손절가), 레버리지는 결과값.
        손실 뒤에도 같은 비율(마틴게일 없음). 1회 위험은 시드의 maxRiskCapP%(≤5%)를 절대 넘지 않음.
      - 하루 실현손익 ≤ −dayLossP% 또는 maxConsec 연속 손절 → 그날 신규 금지 (기존과 같음)
      - 하루 실현손익 ≥ dayTargetP% → 일반 크기 금지. 이후엔 위험 ≤ (오늘 실현 − 목표)인 크기만,
        그 값이 1R × afterTargetMinFrac 미만이면 진입 안 함
      - firstWinHalf: 그날(진입일 기준) 첫 매매가 수익으로 끝나면 그날 남은 매매 수량 절반
      - withdrawPct: 매주 월요일 00:00 KST 에 지난주 실현 수익(플러스일 때만)의 N% 를 출금 → 거래 시드에서 제외
      - 하루 기준 금액(목표·손실 한도) = 그날 첫 봉의 거래 계좌 평가 잔고 (기존 v1.4.1 과 같은 정의)
    """
    T, O, H, L, C, V = D.T, D.O, D.H, D.L, D.C, D.V
    P_final = P
    phases = list(P.get("rule_history") or [])     # [(until, params)] — until 이전 봉에 그 params 적용
    if phases:
        P = phases[0][1]
    n, sw, tol = D.n, P["swLen"], P["zoneTolP"] / 100
    fee, slip, eq0 = P["fee"], P["slip"], P["capital"]
    capP = P["maxRiskCapP"]
    tgtP = P["dayTargetP"]
    if P.get("slipMode", "fixed") == "tick":
        _ticks = P.get("slipTicks", 1.0)

        def sl(px):
            return tick_size(px) * _ticks
    else:
        def sl(px):
            return slip
    bar_td = dt.timedelta(minutes=getattr(D, "bar_min", 5))
    ev_win = []
    if P.get("eventBlock") and events:
        b4, af = dt.timedelta(minutes=P["eventBeforeMin"]), dt.timedelta(minutes=P["eventAfterMin"])
        ev_win = [(e[0] - b4, e[0] + af, e[1]) for e in events]

    def event_blocked(t_fill):
        for a, b, _nm in ev_win:
            if a <= t_fill < b:
                return True
        return False

    withdrawn = 0.0
    withdrawals = []
    weekKey = None; weekStartNP = 0.0
    dayFirst = None              # 그날 진입한 첫 매매의 결과: "수익" / "손실" / "본전"
    tgtReached = False
    targetDays, lossLimitDays, consecDays = set(), set(), set()
    nextFactor = 1.0; nextMode = "보통"; nextRisk = None; lastBaseRisk = 0.0; lastTargetAmt = 0.0
    lastLossAmt = 0.0
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
    curve_acct = []
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
        # 규칙 변경 시각을 지나면 다음 규칙으로 (자금 관리 값만 바뀜, 보유 포지션·오늘 기록은 이어짐)
        while phases and T[i] >= phases[0][0]:
            phases.pop(0)
            P = phases[0][1] if phases else P_final
            capP = P["maxRiskCapP"]; tgtP = P["dayTargetP"]
        closed_now = []
        # ---------- 0) 주간 출금 (월요일 00:00 KST, 이번 봉의 체결보다 먼저) ----------
        wk = T[i].date() - dt.timedelta(days=T[i].weekday())
        if wk != weekKey:
            if weekKey is not None and P["withdrawPct"] > 0:
                wpnl = realized - weekStartNP          # 지난주 실현손익 (출금 전)
                if wpnl > 0:
                    amt = wpnl * P["withdrawPct"] / 100
                    withdrawn += amt
                    withdrawals.append(dict(time=T[i], week_start=weekKey, week_pnl=wpnl, amount=amt,
                                            total=withdrawn, seed_after=eq0 + realized - withdrawn))
                    events.append(dict(type="withdraw", id=f"W{wk.strftime('%Y%m%d')}", time=T[i],
                                       amount=amt, week_pnl=wpnl, pct=P["withdrawPct"], total=withdrawn,
                                       principal_done=withdrawn >= eq0))
            weekKey = wk; weekStartNP = realized
        # ---------- 1) 이번 봉 안에서의 체결 ----------
        if pending is not None:
            pd = pending; pending = None
            fill = o + pd["side"] * sl(o)
            pos = dict(pd, entry=fill, open=pd["qty"], pnl=0.0, exits=[], fills=[], entry_bar=i,
                       entry_time=T[i], t1Done=False, t1Hit=False, nxt=None, stop0=pd["stop"],
                       lev=pd["qty"] * fill / pd["eq"], id=trade_id(T[i], pd["side"]))
            events.append(dict(type="entry", id=pos["id"] + ":E", time=T[i], side=pos["side"], tag=pos["tag"],
                               grade=pos["grade"], price=fill, stop=pos["stop"], t1=pos["t1"],
                               qty=pos["qty"], lev=pos["lev"]))
        if pos is not None and pending_close is not None:
            s = pos["side"]
            tr = pos
            if close_part(pos["open"], o - s * sl(o), pending_close, i):
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
                px = (min(o, stp) - sl(min(o, stp))) if s == 1 else (max(o, stp) + sl(max(o, stp)))
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
        seed = eq0 + realized - withdrawn          # 거래 시드 (출금 0 이면 eq0 + realized 와 정확히 같음)
        equity = seed + unreal
        dk = T[i].date()
        if dk != dayKey:
            dayKey = dk; dayStartNP = realized; dayStartEq = equity; consec = 0
            dayFirst = None; tgtReached = False
        for tr in closed_now:
            consec = consec + 1 if tr["pnl"] < 0 else 0
            if dayFirst is None and tr["entry_time"].date() == dk:
                dayFirst = "수익" if tr["pnl"] > 0 else ("손실" if tr["pnl"] < 0 else "본전")
        dayReal = realized - dayStartNP
        lossAmt = dayStartEq * P["dayLossP"] / 100
        hitLoss = dayReal <= -lossAmt
        hitConsec = consec >= P["maxConsec"]
        dayStop = hitLoss or hitConsec
        if dayStop:
            if dk not in dayStopDays and can_trade:
                why = f"{consec}연속 손절" if hitConsec else f"하루 손실 {P['dayLossP']:g}% 도달"
                events.append(dict(type="daystop", id=f"D{dk.strftime('%Y%m%d')}", time=T[i], reason=why,
                                   day_pnl=dayReal))
            dayStopDays.add(dk)
            if can_trade:
                (consecDays if hitConsec else lossLimitDays).add(dk)
        # 하루 목표 (dayTargetP > 0 일 때만)
        targetAmt = dayStartEq * tgtP / 100
        if tgtP > 0 and dayReal >= targetAmt:
            if not tgtReached and can_trade:
                events.append(dict(type="target", id=f"G{dk.strftime('%Y%m%d')}", time=T[i], day_pnl=dayReal,
                                   target=targetAmt, after=P["afterTargetSizing"]))
                targetDays.add(dk)
            tgtReached = True
        baseRisk = seed * P["riskPct"] / 100
        redRisk = seed * P["redPct"] / 100
        hardCap = seed * capP / 100                # 1회 위험 절대 상한 (≤ 시드 5%)
        lockCap = dayReal - baseRisk if dayReal >= 2 * baseRisk else None   # v1.4.1 수익 잠금(기존 규칙)
        halfNow = P["firstWinHalf"] and dayFirst == "수익"
        tgtCap = None                              # 목표 후 허용 위험 = 오늘 실현 − 목표
        tgtBlock = False
        if tgtReached:
            tgtCap = dayReal - targetAmt
            if not P["afterTargetSizing"] or tgtCap < baseRisk * P["afterTargetMinFrac"]:
                tgtBlock = True

        def fcap(r):
            r = r if lockCap is None else min(r, lockCap)
            if halfNow:
                r = r * 0.5
            if tgtCap is not None:
                r = min(r, tgtCap)
            return min(r, hardCap)

        def size_tag():
            if tgtCap is not None:
                return "목표후"
            return "0.5" if halfNow else "1"

        # 상태 표시용: 다음 매매 크기 (기본 셋업 기준)
        lastBaseRisk, lastTargetAmt, lastLossAmt = baseRisk, targetAmt, lossAmt
        if dayStop:
            nextMode, nextFactor, nextRisk = "진입 금지(하루 한도)", 0.0, 0.0
        elif tgtBlock:
            nextMode, nextFactor, nextRisk = "진입 안 함(목표 보호)", 0.0, 0.0
        else:
            nextRisk = fcap(baseRisk)
            nextFactor = nextRisk / baseRisk if baseRisk > 0 else 0.0
            nextMode = "목표 후 축소" if tgtCap is not None else ("절반(첫 매매 수익)" if halfNow else "보통")

        # ---------- 4) 진입 판단 (봉 마감, 다음 봉 시가 체결) ----------
        if (pos is None and pending is None and not dayStop and not tgtBlock and can_trade
                and not (ev_win and event_blocked(T[i] + bar_td))):
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
                    q = math.floor(min(q, seed * P["maxLev"] / c) * 10000) / 10000   # 레버리지 상한(안전장치)
                    if q > 0:
                        pending = dict(side=side, tag=sig, grade=grd, stop=stp, t1=t1, qty=q, eq=seed,
                                       sigClose=c, rm=rm, dir60=dir60, signal_time=T[i], sizeTag=size_tag(),
                                       baseR=baseRisk,
                                       bpLine=(bpLineNextL if side == 1 else bpLineNextS) if sig == "B+" else None)
                        # 매매 일지용 진입 근거(계산에는 쓰지 않음)
                        if sig == "구간":
                            zparts = ([f"스윙레벨 {_pxs(lvlL)}"] if lvlL is not None else []) + (["5분 FVG"] if fvgTL else []) + (["OB"] if obTL else []) \
                                if side == 1 else ([f"스윙레벨 {_pxs(lvlS)}"] if lvlS is not None else []) + (["5분 FVG"] if fvgTS else []) + (["OB"] if obTS else [])
                            sparts = ((["거래량"] if volSup else []) + (["RSI"] if (rsiSupL if side == 1 else rsiSupS) else [])
                                      + (["1시간 방향"] if dir60 == side else []) + (["15분 FVG"] if (htfL if side == 1 else htfS) else []))
                        elif sig == "B+":
                            zparts, sparts = ["거래량 단계 돌파선"], ["거래량 ≥ 20봉 평균 2배"]
                        else:
                            zparts, sparts = ["직전 스윙 가짜 이탈"], []
                        pending["why"] = dict(dir15=dir15, dir60=dir60, zone=zparts, support=sparts,
                                              stop_pct=round(dp, 4), t1_R=round(abs(t1 - c) / dist, 3) if dist > 0 else None)
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
            curve.append(equity + withdrawn); curve_acct.append(equity); curve_time.append(T[i])

    # 데이터 끝에 남은 포지션: 백테스트 비교용일 때만 마지막 종가로 정리
    if pos is not None and close_at_end:
        close_part(pos["open"], C[-1], "기간 종료", n - 1)
        unreal = 0.0
        equity = eq0 + realized - withdrawn
    if pos is not None:
        unreal = pos["side"] * (C[-1] - pos["entry"]) * pos["open"] - pos["entry"] * pos["open"] * fee
        equity = eq0 + realized - withdrawn + unreal

    day = dict(date=dayKey, realized=realized - dayStartNP, start_equity=dayStartEq, consec=consec,
               stop=dayStop, reason=None,
               target_reached=tgtReached, target_amount=lastTargetAmt if tgtP > 0 else None,
               loss_limit_amount=lastLossAmt, first_trade_result=dayFirst,
               next_size_factor=nextFactor, next_size_mode=nextMode, next_risk=nextRisk,
               risk_per_trade=lastBaseRisk)
    if dayStop:
        day["reason"] = (f"{consec}연속 손절" if consec >= P["maxConsec"]
                         else f"하루 손실 {P['dayLossP']:g}% 도달")
    seed_now = eq0 + realized - withdrawn
    return dict(trades=trades, position=pos, pending=pending, pending_close=pending_close,
                curve=curve, curve_acct=curve_acct, curve_time=curve_time, events=events,
                dayStopDays=dayStopDays, lossLimitDays=lossLimitDays, consecDays=consecDays,
                targetDays=targetDays, withdrawn=withdrawn, withdrawals=withdrawals, seed=seed_now,
                realized=realized, final=seed_now, equity=equity, unrealized=unreal, day=day,
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
