# -*- coding: utf-8 -*-
"""
v4 30m 짝지은 그림자 장부 비교 — research/v4/pairs.csv 만들기와 검정력 계산(기록용, 매매 규칙과 무관).

짝: 같은 신호(signal_id = 코인-신호봉시각-방향)를 두 장부(A = v4_30m_fixed 고정 손절, B = v4_30m_cost 비용 기준 손절)에서 본다.
- 한쪽만 진입한 신호도 한 줄로 남긴다(다른 쪽 status = none).
- 상태: closed(청산 완료) / open(보유 중 또는 진입 대기) / none(진입 안 함)
- 차이 정의(둘 다 '열림'이 아닐 때만):
    d_sig  = R_B − R_A, 진입 안 한 쪽은 0R  ← 주 지표('신호 하나에서 규칙 B 가 A 보다 몇 R 더 벌었나')
    d_both = R_B − R_A, 둘 다 청산된 신호만 ← 같은 신호·같은 손절·같은 목표라 거의 0(수량 반올림 차이뿐)
- pre-cost R: 슬리피지·수수료 없이(진입 = 진입 봉 시가, 청산 = 슬리피지 전 가격) 계산한 R (tools/extras.decompose)
검정력: 짝지은 t 근사 n = ((z_{1−α/2} + z_{power}) × σ_d / δ)², δ = 0.10R, 양측 α 0.05, 검정력 80%.
        σ_d² = σ_A² + σ_B² − 2ρσ_Aσ_B 와 같다(직접 계산한 σ_d 를 쓰고 ρ 도 함께 보고).
표준 라이브러리만.
"""
import csv
import datetime as dt
import math
import os
from statistics import NormalDist

from tools.extras import decompose, signal_id, signal_time

DELTA = 0.10
ALPHA = 0.05
POWER = 0.80
Z_A = NormalDist().inv_cdf(1 - ALPHA / 2)      # 1.95996
Z_B = NormalDist().inv_cdf(POWER)              # 0.84162

COLS = ["signal_id", "coin", "signal_time", "side", "regime", "high_vol", "ret30", "vol7_pct",
        "A_status", "A_entry_time", "A_entry", "A_stop0", "A_exit_time", "A_exit_reason", "A_R", "A_preR",
        "B_status", "B_entry_time", "B_entry", "B_stop0", "B_exit_time", "B_exit_reason", "B_R", "B_preR",
        "d_sig", "d_both"]


def _f(x, nd=6):
    return "" if x is None else f"{x:.{nd}f}"


def _px(x):
    return "" if x is None else (f"{x:.0f}" if abs(x) >= 1000 else f"{x:.8g}")


def build_rows(ledgers, datas, bar_min=30, regime=None, fee=0.0005):
    """ledgers: {"A": (청산 거래 목록, 열린 것 목록), "B": (...)} — 열린 것 = 보유 포지션·진입 대기 dict
    datas: {코인: Data} (pre-cost R 계산용). regime: tools.regime.Regime 또는 None. 반환: 시각순 dict 목록"""
    by = {}
    for k, (closed, opened) in ledgers.items():
        for t, st in [(t, "closed") for t in closed] + [(p, "open") for p in opened]:
            sid = signal_id(t, bar_min)
            r = by.setdefault(sid, dict(signal_id=sid, coin=t.get("coin") or "BTC", side=t["side"],
                                        _st=signal_time(t, bar_min)))
            r[k] = (t, st)
    rows = []
    for sid, r in by.items():
        row = dict(signal_id=sid, coin=r["coin"], signal_time=r["_st"].strftime("%Y-%m-%d %H:%M"),
                   side="L" if r["side"] == 1 else "S", _st=r["_st"])
        lab = regime.label(r["_st"] + dt.timedelta(minutes=bar_min)) if regime else None   # 신호 봉 마감 시점 기준
        row.update(regime=lab["trend"] if lab else "", high_vol="" if not lab or lab["high_vol"] is None else int(lab["high_vol"]),
                   ret30=_f(lab["ret30"], 4) if lab else "", vol7_pct=_f(lab["vol_pct"], 3) if lab and lab["vol_pct"] is not None else "")
        Rv = {}
        for k in ("A", "B"):
            if k not in r:
                row.update({f"{k}_status": "none"})
                Rv[k] = 0.0
                continue
            t, st = r[k]
            row[f"{k}_status"] = st
            row[f"{k}_entry_time"] = t["entry_time"].strftime("%Y-%m-%d %H:%M") if t.get("entry_time") else ""
            row[f"{k}_entry"] = _px(t.get("entry"))
            row[f"{k}_stop0"] = _px(t.get("stop0", t.get("stop")))
            if st == "closed":
                row[f"{k}_exit_time"] = t["exit_time"].strftime("%Y-%m-%d %H:%M")
                row[f"{k}_exit_reason"] = ("T1 후 " if t.get("t1Hit") and t["exit_reason"] != "T1 반익절" else "") + t["exit_reason"]
                row[f"{k}_R"] = _f(t["R"])
                d = t.get("decomp")
                if d is None:
                    try:
                        d = decompose(t, (datas or {}).get(row["coin"]), fee)
                    except Exception:
                        d = None
                row[f"{k}_preR"] = _f(d.get("pre_cost_R")) if d and d.get("pre_cost_R") is not None else ""
                Rv[k] = t["R"]
            else:
                Rv[k] = None
        if Rv["A"] is not None and Rv["B"] is not None:
            row["d_sig"] = _f(Rv["B"] - Rv["A"])
        if row["A_status"] == "closed" and row["B_status"] == "closed":
            row["d_both"] = _f(Rv["B"] - Rv["A"])
        rows.append(row)
    return sorted(rows, key=lambda x: (x["_st"], x["coin"], x["side"]))


def write_pairs(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLS)
        for r in rows:
            w.writerow([r.get(k, "") for k in COLS])
    os.replace(tmp, path)


def read_pairs(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------- 통계 ----------------

def _mean(a):
    return sum(a) / len(a) if a else None


def _sd(a):
    if len(a) < 2:
        return None
    m = _mean(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / (len(a) - 1))


def _corr(a, b):
    if len(a) < 3:
        return None
    sa, sb = _sd(a), _sd(b)
    if not sa or not sb:
        return None
    ma, mb = _mean(a), _mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / ((len(a) - 1) * sa * sb)


def n_required(sd_d, delta=DELTA):
    """짝지은 차이 σ_d 로 필요한 쌍 수(정규 근사, 올림)"""
    if sd_d is None:
        return None
    return int(math.ceil(((Z_A + Z_B) * sd_d / delta) ** 2))


def power_stats(rows, delta=DELTA):
    """pairs 행(dict, 문자열 값 가능) → 검정력 요약 dict"""
    def num(x):
        return None if x in (None, "") else float(x)
    sig = [r for r in rows if num(r.get("d_sig")) is not None]
    A = [num(r.get("A_R")) or 0.0 for r in sig]
    B = [num(r.get("B_R")) or 0.0 for r in sig]
    d = [b - a for a, b in zip(A, B)]
    both = [r for r in rows if num(r.get("d_both")) is not None]
    A2 = [num(r["A_R"]) for r in both]
    B2 = [num(r["B_R"]) for r in both]
    d2 = [b - a for a, b in zip(A2, B2)]
    onlyA = sum(1 for r in sig if r.get("A_status") == "closed" and r.get("B_status") == "none")
    onlyB = sum(1 for r in sig if r.get("B_status") == "closed" and r.get("A_status") == "none")
    sdd = _sd(d)
    out = dict(
        delta_R=delta, alpha_two_sided=ALPHA, power=POWER, z_alpha=round(Z_A, 5), z_power=round(Z_B, 5),
        primary="d_sig (신호마다 B−A, 진입 안 한 쪽 0R)",
        n_signals=len(sig), n_both=len(both), n_only_A=onlyA, n_only_B=onlyB,
        n_open=sum(1 for r in rows if "open" in (r.get("A_status"), r.get("B_status"))),
        mean_d=_mean(d), sd_d=sdd, sd_A=_sd(A), sd_B=_sd(B), rho=_corr(A, B),
        mean_A=_mean(A), mean_B=_mean(B),
        n_required=n_required(sdd, delta),
        both=dict(n=len(both), mean_d=_mean(d2), sd_d=_sd(d2), rho=_corr(A2, B2), sd_A=_sd(A2), sd_B=_sd(B2)),
        trades_A=sum(1 for r in rows if r.get("A_status") == "closed"),
        trades_B=sum(1 for r in rows if r.get("B_status") == "closed"),
    )
    tA = [num(r["A_R"]) for r in rows if r.get("A_status") == "closed"]
    tB = [num(r["B_R"]) for r in rows if r.get("B_status") == "closed"]
    out["mean_R_per_trade_A"], out["mean_R_per_trade_B"] = _mean(tA), _mean(tB)
    out["sd_R_per_trade_A"], out["sd_R_per_trade_B"] = _sd(tA), _sd(tB)
    sA, sB = _sd(tA), _sd(tB)
    # 참고: 거래당 평균 R 을 독립 두 표본으로 비교할 때 한 장부당 필요한 거래 수(ρ=0 가정)
    out["n_per_arm_unpaired"] = (int(math.ceil((Z_A + Z_B) ** 2 * (sA ** 2 + sB ** 2) / delta ** 2))
                                 if sA and sB else None)
    if out["n_required"]:
        out["progress"] = round(len(sig) / out["n_required"], 4)
    return out
