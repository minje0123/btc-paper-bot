# -*- coding: utf-8 -*-
"""
A2. 미래 참조 검사 — 데이터를 시각 t 에서 잘라 엔진을 돌린 결과가 전체 데이터로 돌린 결과의 t 이전 부분과 완전히 같은지.

각 (코인, 실행 시간봉) 마다 무작위 절단점 20개(고정 시드)에서:
  1) 잘린 실행의 청산 완료 거래 = 전체 실행에서 t 까지 청산된 거래 (진입·손절·T1·수량·청산 내역·손익·R 모두)
  2) 잘린 실행의 보유 포지션 = 전체 실행의 같은 거래, t 까지의 부분 체결 내역도 같음
  3) 잘린 실행의 진입 대기 신호(마지막 봉 마감 신호) = 전체 실행에서 바로 다음 봉에 진입한 거래
  4) 잘린 실행의 청산 대기 사유 = 전체 실행의 다음 봉 청산 사유
시간봉을 묶은 실행(15m~4H)은 '묶은 봉' 경계에서 자른다(진행 중인 묶음봉은 실전에서도 신호에 쓰지 않음).
방향·보조 상위봉(3k·12k분)은 잘린 데이터에서 다시 묶으므로 마지막 상위봉이 미완성인 상황까지 검사된다.
추가: 운영 설정(config.json, 자금 관리·rule_history 포함)으로 운영 데이터 data/candles_5m.csv 도 같은 검사.
결과: research/checks/a2_result.json
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import COINS, REPO, TFS, TF_NAME, aggregate, engine, load_5m, make_data  # noqa: E402
from e1_params import e1_params  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a2_result.json")
NCUT = 20


def ident(t):
    return (t["entry_time"], t["side"], t["tag"], t["grade"], round(t["stop0"], 6), round(t["t1"], 6),
            round(t["qty"], 6), t["signal_time"], round(t["entry"], 6))


def full_t(t):
    return ident(t) + (t["exit_time"], tuple(t["exits"]), round(t["pnl"], 4), round(t["R"], 9), t["exit_reason"])


def fills_upto(t, tl):
    return [(f["reason"], round(f["price"], 6), round(f["qty"], 6), f["time"]) for f in t["fills"] if f["time"] <= tl]


def compare(F, R, m, T):
    """F: 전체 결과, R: bars[:m] 결과. 다르면 사유 목록"""
    errs = []
    tl = T[m - 1]
    allF = F["trades"] + ([F["position"]] if F["position"] else [])
    closedF = [full_t(t) for t in F["trades"] if t["exit_bar"] <= m - 1]
    closedR = [full_t(t) for t in R["trades"]]
    if closedF != closedR:
        errs.append(f"청산 거래 불일치 (전체 {len(closedF)} vs 잘림 {len(closedR)})")
    openF = [t for t in allF if t["entry_bar"] <= m - 1 and (t.get("exit_bar") is None or t["exit_bar"] > m - 1)]
    if R["position"] is None:
        if openF:
            errs.append("잘린 실행엔 포지션 없음, 전체 실행엔 보유 중")
    else:
        if len(openF) != 1 or ident(openF[0]) != ident(R["position"]):
            errs.append("보유 포지션 불일치")
        elif fills_upto(openF[0], tl) != fills_upto(R["position"], tl):
            errs.append("보유 포지션 부분 체결 불일치")
    if m < len(T):
        nxt = [t for t in allF if t["entry_bar"] == m]
        pd = R["pending"]
        if pd is None:
            if nxt:
                errs.append("전체 실행은 다음 봉 진입, 잘린 실행엔 대기 신호 없음")
        else:
            if len(nxt) != 1:
                errs.append("잘린 실행 대기 신호가 전체 실행에서 진입하지 않음")
            else:
                a = nxt[0]
                if (a["side"], a["tag"], a["grade"], round(a["stop0"], 6), round(a["t1"], 6), round(a["qty"], 6),
                        a["signal_time"]) != (pd["side"], pd["tag"], pd["grade"], round(pd["stop"], 6),
                                              round(pd["t1"], 6), round(pd["qty"], 6), pd["signal_time"]):
                    errs.append("대기 신호 내용 불일치")
        if R["pending_close"] is not None:
            ok = any(f["reason"] == R["pending_close"] and f["time"] == T[m] for t in openF for f in t["fills"])
            if not ok:
                errs.append("청산 대기 사유 불일치")
    return errs


def check(bars, mk_data, P, label, rnd, start_time=None):
    T = bars[0]
    n = len(T)
    F = engine.run(mk_data(bars), P, start_time=start_time)
    cuts = sorted(rnd.sample(range(max(50, n // 10), n), NCUT))
    bad = []
    nonempty = 0
    for m in cuts:
        sub = tuple(x[:m] for x in bars)
        R = engine.run(mk_data(sub), P, start_time=start_time)
        nonempty += bool(R["trades"])
        e = compare(F, R, m, T)
        if e:
            bad.append(dict(cut=str(T[m - 1]), errors=e))
    r = dict(label=label, bars=n, trades_full=len(F["trades"]), cuts=len(cuts), cuts_with_pending=None,
             mismatched=len(bad), details=bad[:5], cut_times=[str(T[m - 1]) for m in cuts])
    print(label, "봉", n, "거래", len(F["trades"]), "절단", len(cuts), "불일치", len(bad), flush=True)
    return r


def main():
    rnd = random.Random(20261117)
    res = dict(method=__doc__.strip(), results=[])
    for coin in COINS:
        b5 = load_5m(coin)
        for tf in TFS:
            bars = aggregate(*b5, tf)
            P = e1_params(maxStopP=1.5 * (1 if tf == 5 else 3))   # 검사용(값은 결과 동일성에 무관)
            res["results"].append(check(bars, lambda b, tf=tf: make_data(b, tf), P, f"{coin} {TF_NAME[tf]}", rnd))
    # 운영 설정 + 운영 데이터
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
    P = engine.load_params(cfg)
    D0 = engine.load_csv(os.path.join(REPO, "data", "candles_5m.csv"))
    res["results"].append(check(D0, lambda b: engine.Data(*b), P, "운영 config + candles_5m.csv", rnd,
                                start_time=engine.parse_kst(cfg["start_time"])))
    res["total_cuts"] = sum(r["cuts"] for r in res["results"])
    res["total_mismatched"] = sum(r["mismatched"] for r in res["results"])
    res["pass"] = res["total_mismatched"] == 0
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("PASS" if res["pass"] else "FAIL", res["total_cuts"], res["total_mismatched"])


if __name__ == "__main__":
    main()
