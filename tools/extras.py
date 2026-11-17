# -*- coding: utf-8 -*-
"""
기록용 부가 계산 (2026-11-17) — 장부 계산 결과(거래 dict)를 읽기만 하고 매매 규칙에는 영향 없음.

(2) 상관 신호 묶음: 같은 방향 신호가 여러 코인에서 같은 봉(또는 ±1봉) 안에 나면 같은 묶음.
    - 신호 봉 시각(signal_time) 기준. 같은 방향끼리 시각순으로 놓고, 앞 신호와 간격이 1봉 이하이면 같은 묶음(사슬 연결).
    - 묶음 ID = M{묶음 첫 신호 봉 시각 YYYYMMDDHHMM}{L|S}, 묶음 크기 = 묶음 안 거래(신호) 수.
    - 4코인 동시 손절을 독립 4건으로 세지 않도록 '독립 묶음 수'를 함께 보고한다.
(4) 손익 분해(R 단위, 분모 = 엔진의 R 분모 = 수량 × |진입 체결가 − 최초 손절가|):
    R = 계획R + 갭R + 슬리피지R + 진입수수료R + 청산수수료R
    - 계획R   : 각 청산의 '기준가'(손절=최초 손절가, 본절=진입가, T1=T1 가격, 그 밖=실제 기준 체결가)까지의 손익. 손절이면 −1.000
    - 갭R     : 봉 시가가 기준가를 건너뛰어 기준가보다 불리(손절)·유리(지정가)하게 시작한 만큼
    - 슬리피지R: 시장가·손절 체결의 틱 슬리피지(체결가 − 슬리피지 전 가격)
    - 수수료R : 진입가 × 수량 × 수수료, 청산가 × 수량 × 수수료
    부가 정보: 실제위험/계획위험 = R 분모 ÷ 계획 위험금액(rm), 진입 슬리피지(원)
    '다음 스윙' 지정가 청산은 목표가가 거래 기록에 없어 기준가 = 체결가(갭 0)로 둔다.
신호 ID(v4 짝 비교): {코인}-{신호 봉 시각 YYYYMMDDHHMM}{L|S}
표준 라이브러리만.
"""
import datetime as dt

STOP_REASONS = ("손절", "본절")
LIMIT_REASONS = ("T1 반익절", "다음 스윙", "1.5R 익절")
NO_SLIP_REASONS = ("기간 종료",)


def coin_of_trade(t):
    return t.get("coin") or "BTC"


def signal_time(t, bar_min=5):
    st = t.get("signal_time")
    if st is not None:
        return st
    et = t.get("entry_time")
    return et - dt.timedelta(minutes=bar_min) if et is not None else None


def signal_id(t, bar_min=5):
    st = signal_time(t, bar_min)
    return f"{coin_of_trade(t)}-{st:%Y%m%d%H%M}{'L' if t['side'] == 1 else 'S'}"


def assign_clusters(items, bar_min=5):
    """items(거래·보유 포지션 dict 목록)에 cluster=dict(id, size) 를 붙인다. 반환: {묶음ID: 크기}"""
    gap = dt.timedelta(minutes=bar_min)
    out = {}
    for side in (1, -1):
        xs = sorted([x for x in items if x.get("side") == side and signal_time(x, bar_min) is not None],
                    key=lambda x: (signal_time(x, bar_min), coin_of_trade(x)))
        groups, cur, last = [], [], None
        for x in xs:
            st = signal_time(x, bar_min)
            if cur and st - last <= gap:
                cur.append(x)
            else:
                if cur:
                    groups.append(cur)
                cur = [x]
            last = st
        if cur:
            groups.append(cur)
        for g in groups:
            cid = f"M{signal_time(g[0], bar_min):%Y%m%d%H%M}{'L' if side == 1 else 'S'}"
            for x in g:
                x["cluster"] = dict(id=cid, size=len(g))
            out[cid] = len(g)
    return out


def independent_count(trades):
    """거래 목록의 독립 묶음 수(cluster 가 없으면 거래 수)"""
    ids = set()
    for k, t in enumerate(trades):
        c = t.get("cluster")
        ids.add(c["id"] if c else f"_{k}")
    return len(ids)


def _index(D):
    m = getattr(D, "_t_index", None)
    if m is None:
        m = {t: i for i, t in enumerate(D.T)}
        try:
            D._t_index = m
        except Exception:
            pass
    return m


def decompose(t, D=None, fee=0.0005):
    """거래 t(청산 완료) → R 분해 dict. D(그 코인 Data)가 없으면 봉 시가를 몰라 갭·슬리피지를 나누지 못함(None 반환)."""
    if D is None or not t.get("fills"):
        return None
    idx = _index(D)
    s = t["side"]; E = t["entry"]; S0 = t["stop0"]; Q = t["qty"]
    rw = Q * abs(E - S0)
    if rw <= 0:
        return None
    plan = gap = slip = fx = 0.0
    raw_sum = 0.0            # 비용 전(슬리피지 없는 가격) 손익 계산용
    for f in t["fills"]:
        F, q, why = f["price"], f["qty"], f["reason"]
        i = idx.get(f["time"])
        o = D.O[i] if i is not None else F
        if why in STOP_REASONS:
            ref = S0 if why == "손절" else E
            raw = min(o, ref) if s == 1 else max(o, ref)
        elif why == "T1 반익절":
            ref = t["t1"]; raw = F
        elif why in LIMIT_REASONS or why in NO_SLIP_REASONS:
            ref = F; raw = F
        else:                                   # 다음 봉 시가 시장가 청산(돌파 실패·반대 장악형·거래량 급증)
            ref = o; raw = o
        plan += s * (ref - E) * q
        gap += s * (raw - ref) * q
        slip += s * (F - raw) * q
        fx += F * q * fee
        raw_sum += s * raw * q
    fe = E * Q * fee
    out = dict(plan_R=plan / rw, gap_R=gap / rw, slip_R=slip / rw, fee_entry_R=-fe / rw, fee_exit_R=-fx / rw)
    out["sum_R"] = sum(out.values())
    out["R"] = t["R"]
    rm = t.get("rm")
    out["risk_ratio"] = rw / rm if rm else None          # 실제위험(R 분모) ÷ 계획 위험금액
    ie = idx.get(t["entry_time"])
    oe = D.O[ie] if ie is not None else E
    out["entry_slip_won"] = s * (E - oe) * Q             # 진입 슬리피지(원, 양수 = 불리)
    rw0 = Q * abs(oe - S0)
    out["pre_cost_R"] = (raw_sum - s * oe * Q) / rw0 if rw0 > 0 else None   # 슬리피지·수수료 없는 R(진입=봉 시가)
    return out


def attach_decomp(trades, datas, fee=0.0005, log=None):
    """거래마다 t['decomp'] 를 붙인다(실패한 거래는 None). datas: {코인: Data}"""
    bad = 0
    for t in trades:
        try:
            t["decomp"] = decompose(t, (datas or {}).get(coin_of_trade(t)), fee)
        except Exception:
            t["decomp"] = None
            bad += 1
    if bad and log:
        log(f"손익 분해 실패 {bad}건(일지 빈칸)", warn=True)


DECOMP_COLS = ["신호ID", "묶음ID", "묶음크기", "분해_계획R", "분해_갭R", "분해_슬리피지R", "분해_진입수수료R", "분해_청산수수료R",
               "실제위험/계획위험"]


def journal_cols(t, bar_min=5):
    c = t.get("cluster") or {}
    d = t.get("decomp") or {}

    def f(k, nd=3):
        v = d.get(k)
        return "" if v is None else f"{v:.{nd}f}"
    try:
        sid = signal_id(t, bar_min)
    except Exception:
        sid = ""
    return [sid, c.get("id", ""), c.get("size", ""), f("plan_R"), f("gap_R"), f("slip_R"), f("fee_entry_R"),
            f("fee_exit_R"), f("risk_ratio")]


def stop_decomp_summary(trades):
    """T1 전에 손절된 거래의 평균 분해 → dict 또는 None"""
    xs = [t["decomp"] for t in trades if t.get("decomp") and t.get("exit_reason") == "손절" and not t.get("t1Hit")]
    if not xs:
        return None
    keys = ("plan_R", "gap_R", "slip_R", "fee_entry_R", "fee_exit_R", "sum_R", "R")
    out = {k: sum(x[k] for x in xs) / len(xs) for k in keys}
    rr = [x["risk_ratio"] for x in xs if x.get("risk_ratio")]
    out["risk_ratio"] = sum(rr) / len(rr) if rr else None
    out["n"] = len(xs)
    return out
