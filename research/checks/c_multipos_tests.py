# -*- coding: utf-8 -*-
"""
C 검증 2 — 4코인 · 동시 여러 포지션(engine.run_multi) 단위 테스트. 오프라인, 로컬 data/ 의 4코인 5분봉(2026-11-14~)만 읽음.
결과는 research/checks/c_tests_result.json 에만 쓴다(ledger/ 는 건드리지 않음). research/holdout 사용 안 함.

  T1 실데이터 시뮬레이션(운영 config, 4코인 단계를 2026-11-15 00:00 부터 켰다고 가정):
     - 어느 봉에서도 동시 보유(진입 대기 포함) ≤ 2, 열린 위험 합계 ≤ 시드 1.0%
     - 거래 보유 구간 [진입, 청산) 이 3개 이상 겹치는 시각 없음, 한 코인에 겹치는 포지션 없음
     - 배분 기록: 같은 봉 후보 처리 순서 = (등급 A > B+ > B > C, BTC > ETH > SOL > XRP)
     - 모든 진입: 위험 ≤ 여유, 축소 진입이면 위험 = 여유 ≥ 1R×0.25, 명목가 합계 ≤ 평가 잔고×10, 하루 실현−열린 위험 > −한도
     - 하루 정지(daystop) 뒤 그날 신규 진입 없음, 2026-11-17 15:00 활성화 시나리오(운영 config 그대로)에선 15:00 전 BTC 만
  T2~T7 신호를 고정해서 넣는 규칙 테스트(_CoinSim.signal 을 바꿔 정해진 봉에 정해진 후보만 냄):
     T2 우선순위   : 같은 봉에 BTC C, ETH B+, SOL A, XRP A → SOL·XRP 진입, BTC·ETH 는 '동시 보유 상한'
     T3 축소 진입  : 상한 0.8%, 1R 0.5% → 두 번째는 0.3% 로 축소 진입
     T4 여유 부족  : 상한 0.6% → 여유 0.1% < 1R×0.25 → 두 번째 거절
     T5 하루 한도  : dayLossP 0.3% → 실현 0 − 열린 위험 0.5% ≤ −0.3% → 두 번째 거절
     T6 2연속 손절 : BTC·ETH 가 같은 봉에서 손절 → 계좌 2연패 → 그날 SOL 후보는 아예 배분 안 됨 (대조: maxConsec 큼이면 진입)
     T7 레버리지 합계: maxLev 1 → 첫 포지션 명목가 ≈ 잔고 → 두 번째 수량 0 거절
"""
import copy
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import engine  # noqa: E402
import bot  # noqa: E402

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
EPS = 1e-6
RANK = engine.SETUP_RANK
CP = {c: k for k, c in enumerate(engine.COIN_PRIORITY)}


def base_cfg():
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    cfg = {k: v for k, v in cfg.items() if k not in ("shadow_ledgers",)}
    return cfg


def load_Ds():
    return {m: bot.to_data(bot.load_candles(bot.candle_path(m))) for m in MARKETS}


def invariants(R, P, label):
    """실데이터 결과의 불변식 검사 → (ok, 세부)"""
    out = {}
    out["max_open_seen"] = R["max_open_seen"]
    out["max_open_risk_pct_seen"] = round(R["max_open_risk_pct_seen"], 4)
    out["open_le_2"] = R["max_open_seen"] <= P["maxOpenPositions"]
    out["risk_le_1pct"] = R["max_open_risk_pct_seen"] <= P["maxOpenRiskPct"] + EPS
    # 보유 구간 겹침
    iv = [(t["entry_time"], t["exit_time"], t["coin"]) for t in R["trades"]]
    iv += [(p["entry_time"], dt.datetime.max.replace(tzinfo=engine.KST), p["coin"]) for p in R["positions"]]
    pts = sorted({a for a, _, _ in iv})
    worst, same_coin = 0, False
    for t in pts:
        on = [c for a, b, c in iv if a <= t < b]
        worst = max(worst, len(on))
        same_coin = same_coin or len(on) != len(set(on))
    out["max_concurrent_held"] = worst
    out["held_le_2"] = worst <= P["maxOpenPositions"]
    out["no_same_coin_overlap"] = not same_coin
    # 배분 순서·조건
    al = R["alloc_log"]
    by_t = {}
    for r in al:
        by_t.setdefault(r["time"], []).append(r)
    order_ok = all([(x["rank"], CP[x["coin"]]) for x in v] == sorted((x["rank"], CP[x["coin"]]) for x in v)
                   for v in by_t.values())
    out["same_bar_multi_candidates"] = sum(1 for v in by_t.values() if len(v) > 1)
    out["priority_order_ok"] = order_ok
    ent = [r for r in al if r["result"].startswith("진입")]
    cut = [r for r in ent if "축소" in r["result"]]
    lev = P["maxLev"]
    out["entries"] = len(ent)
    out["cut_entries"] = len(cut)
    out["entry_rules_ok"] = all(
        r["rm"] <= r["seed"] * P["maxOpenRiskPct"] / 100 - r["open_risk_before"] + EPS
        and r["open_before"] < P["maxOpenPositions"]
        and r["day_real"] - r["open_risk_before"] > -r["loss_amt"]
        for r in ent)
    out["cut_rules_ok"] = all(r["rm"] >= r["base_risk"] * P["minRoomFrac"] - EPS for r in cut)
    rej = {}
    for r in al:
        if not r["result"].startswith("진입"):
            rej[r["result"]] = rej.get(r["result"], 0) + 1
    out["rejections"] = rej
    # 하루 정지 뒤 진입 없음
    stops = {e["time"].date(): e["time"] for e in R["events"] if e["type"] == "daystop"}
    out["daystops"] = {str(k): v.strftime("%m-%d %H:%M") for k, v in stops.items()}
    out["no_entry_after_daystop"] = all(not (r["time"].date() in stops and r["time"] >= stops[r["time"].date()])
                                        for r in ent)
    # 레버리지(명목가 합계 ≤ 평가 잔고 × maxLev): 진입 수량까지 포함해 다시 계산
    out["notional_ok"] = all(r["notional_before"] + r["qty"] * r_c(R, r) <= r["equity"] * lev + 1.0 for r in ent)
    per = {}
    for t in R["trades"]:
        d = per.setdefault(t["coin"], [0, 0.0])
        d[0] += 1; d[1] += t["pnl"]
    out["trades_by_coin"] = {k: dict(n=v[0], pnl=round(v[1])) for k, v in per.items()}
    out["n_trades"] = len(R["trades"]); out["realized"] = round(R["realized"]); out["equity"] = round(R["equity"])
    keys = ("open_le_2", "risk_le_1pct", "held_le_2", "no_same_coin_overlap", "priority_order_ok", "entry_rules_ok",
            "cut_rules_ok", "no_entry_after_daystop", "notional_ok")
    ok = all(out[k] for k in keys)
    return ok, out


def r_c(R, r):
    return r.get("sig_close", 0.0)


# ---------------- 신호 고정 테스트 도구 ----------------
_orig_signal = engine._CoinSim.signal


def scripted(script):
    """script: {(coin, time): (tag, grade, side, stop_frac)} — 그 봉에만 후보를 내고 나머지 봉은 신호 없음"""
    def sig(self, P, fcap, baseRisk, redRisk):
        x = self.cur
        key = (self.coin, self.D.T[x["i"]])
        if key not in script:
            return None
        tag, grade, side, sf = script[key]
        c = x["c"]
        stop = c * (1 - side * sf)
        dist = abs(c - stop)
        return dict(side=side, tag=tag, grade=grade, stop=stop, t1=c + side * 1.5 * dist,
                    rm=fcap(baseRisk), dist=dist, dp=dist / c * 100, c=c, dir15=side, dir60=0)
    return sig


def run_script(Ds, cfg, script, start):
    engine._CoinSim.signal = scripted(script)
    try:
        P = engine.load_params(cfg)
        R = engine.run_multi(Ds, P, start_time=engine.parse_kst(start))
    finally:
        engine._CoinSim.signal = _orig_signal
    return R, P


def common_times(Ds, a, b):
    s = None
    for D in Ds.values():
        ts = {t for t in D.T if a <= t < b}
        s = ts if s is None else s & ts
    return sorted(s)


def main():
    res = dict(method=__doc__.strip(), tests={})
    Ds = load_Ds()
    cfg0 = base_cfg()

    # ---------- T1 실데이터 ----------
    c1 = {k: v for k, v in cfg0.items() if k != "rule_history"}
    c1["start_time"] = "2026-11-15 00:00"
    P1 = engine.load_params(c1)
    R1 = engine.run_multi(Ds, P1, start_time=engine.parse_kst(c1["start_time"]))
    ok, det = invariants_with_close(R1, P1, Ds)
    res["tests"]["T1_real_4coin_from_11-15"] = dict(pass_=ok, **det)

    # 운영 config 그대로(15:00 활성화): 15:00 전 거래는 모두 BTC, 결과는 run() 과 같은 BTC 장부
    P0 = engine.load_params(cfg0)
    R0 = engine.run_multi(Ds, P0, start_time=engine.parse_kst(cfg0["start_time"]))
    act = engine.parse_kst("2026-11-17 15:00")
    pre = [t for t in R0["trades"] if t["entry_time"] < act]
    res["tests"]["T1b_activation_config"] = dict(
        pass_=all(t["coin"] == "BTC" for t in pre) and all(r["coin"] == "BTC" for r in R0["alloc_log"] if r["time"] < act),
        trades_before_activation=len(pre), note="데이터가 활성화 시각 전에 끝나므로 모두 BTC 여야 함")

    # ---------- T2~T7 신호 고정 ----------
    ts =common_times(Ds, engine.parse_kst("2026-11-16 02:00"), engine.parse_kst("2026-11-16 12:00"))
    sc = {k: v for k, v in cfg0.items() if k != "rule_history"}
    sc.update(start_time="2026-11-16 00:00", dayTargetP=0, firstWinHalf=False, withdrawPct=0)
    t0 = ts[0]

    # T2 우선순위
    script = {("BTC", t0): ("C", "", 1, 0.006), ("ETH", t0): ("B+", "", 1, 0.006),
              ("SOL", t0): ("구간", "A", 1, 0.006), ("XRP", t0): ("구간", "A", 1, 0.006)}
    R, P = run_script(Ds, sc, script, "2026-11-16 00:00")
    al = [r for r in R["alloc_log"] if r["time"] == t0]
    got = [(r["coin"], r["result"]) for r in al]
    exp = [("SOL", "진입"), ("XRP", "진입"), ("ETH", "거절: 동시 보유 상한"), ("BTC", "거절: 동시 보유 상한")]
    res["tests"]["T2_priority"] = dict(pass_=got == exp, got=got, expected=exp)

    # T3 축소 진입 (상한 0.8%)
    s3 = dict(sc, maxOpenRiskPct=0.8)
    script = {("BTC", t0): ("구간", "B", 1, 0.006), ("ETH", t0): ("구간", "B", 1, 0.006)}
    R, P = run_script(Ds, s3, script, "2026-11-16 00:00")
    al = [r for r in R["alloc_log"] if r["time"] == t0]
    seed = al[0]["seed"]
    ok3 = (len(al) == 2 and al[0]["result"] == "진입" and abs(al[0]["rm"] - seed * 0.005) < 1e-6
           and al[1]["result"] == "진입(여유만큼 축소)" and abs(al[1]["rm"] - seed * 0.003) < 1e-6)
    pend = {q["coin"]: q for q in R["pendings"]}
    ents = [e for e in R["events"] if e["type"] == "entry" and e["time"] > t0][:2]
    res["tests"]["T3_cut_entry"] = dict(pass_=ok3, log=[(r["coin"], r["result"], round(r.get("rm", 0))) for r in al],
                                        expected="BTC 진입 위험 0.5%, ETH 축소 진입 위험 0.3%",
                                        entries_next_bar=[(e["coin"], e["qty"]) for e in ents], pendings=list(pend))

    # T4 여유 부족 (상한 0.6% → 여유 0.1% < 0.125%)
    s4 = dict(sc, maxOpenRiskPct=0.6)
    R, P = run_script(Ds, s4, script, "2026-11-16 00:00")
    al = [r for r in R["alloc_log"] if r["time"] == t0]
    ok4 = len(al) == 2 and al[0]["result"] == "진입" and al[1]["result"] == "거절: 위험 여유 부족"
    res["tests"]["T4_room_too_small"] = dict(pass_=ok4, log=[(r["coin"], r["result"], round(r.get("room", 0))) for r in al])

    # T5 하루 한도(실현 − 열린 위험)
    s5 = dict(sc, dayLossP=0.3)
    R, P = run_script(Ds, s5, script, "2026-11-16 00:00")
    al = [r for r in R["alloc_log"] if r["time"] == t0]
    ok5 = len(al) == 2 and al[0]["result"] == "진입" and al[1]["result"] == "거절: 하루 한도(실현−열린 위험)"
    res["tests"]["T5_day_limit_open_risk"] = dict(pass_=ok5, log=[(r["coin"], r["result"]) for r in al])

    # T6 2연속 손절(서로 다른 코인) → 그날 신규 금지
    def next_bar(D, t):
        i = D.T.index(t)
        return D.O[i + 1], D.L[i + 1], D.C[i]
    tt = None
    for t in ts[:-12]:
        ok_ = True
        for m in ("KRW-BTC", "KRW-ETH"):
            o1, l1, c = next_bar(Ds[m], t)
            if not (l1 < o1 * (1 - 0.0012) and o1 <= c * 1.001):
                ok_ = False
        if ok_:
            tt = t; break
    if tt is None:
        res["tests"]["T6_consec_daystop"] = dict(pass_=False, note="조건에 맞는 봉을 못 찾음")
    else:
        t_sol = [t for t in ts if t > tt + dt.timedelta(minutes=30) and t.date() == tt.date()][0]
        script = {("BTC", tt): ("구간", "B", 1, 0.0005), ("ETH", tt): ("구간", "B", 1, 0.0005),
                  ("SOL", t_sol): ("구간", "A", 1, 0.006)}
        R, P = run_script(Ds, sc, script, "2026-11-16 00:00")
        stops = [e for e in R["events"] if e["type"] == "daystop"]
        sol_alloc = [r for r in R["alloc_log"] if r["coin"] == "SOL"]
        lost = [(t["coin"], round(t["pnl"]), t["exit_reason"]) for t in R["trades"]]
        Rc, _ = run_script(Ds, dict(sc, maxConsec=1000, dayLossP=100), script, "2026-11-16 00:00")
        ctrl = [r["result"] for r in Rc["alloc_log"] if r["coin"] == "SOL"]
        ok6 = (bool(stops) and "2연속" in stops[0]["reason"] and not sol_alloc and ctrl == ["진입"])
        res["tests"]["T6_consec_daystop"] = dict(pass_=ok6, signal_bar=tt.strftime("%m-%d %H:%M"),
                                                 trades=lost, daystop=[(e["time"].strftime("%H:%M"), e["reason"]) for e in stops],
                                                 sol_alloc=sol_alloc, control_sol=ctrl)

    # T7 레버리지 합계 (maxLev 1)
    s7 = dict(sc, maxLev=1)
    script = {("BTC", t0): ("구간", "B", 1, 0.001), ("ETH", t0): ("구간", "B", 1, 0.001)}
    R, P = run_script(Ds, s7, script, "2026-11-16 00:00")
    al = [r for r in R["alloc_log"] if r["time"] == t0]
    cE = Ds["KRW-ETH"].C[Ds["KRW-ETH"].T.index(t0)]
    want = (al[1]["rm"] / (cE * 0.001)) if len(al) == 2 and "rm" in al[1] else None     # 레버리지 상한 없을 때 수량
    tot = al[1]["notional_before"] + al[1]["qty"] * cE if len(al) == 2 and "qty" in al[1] else None
    ok7 = (len(al) == 2 and al[0]["result"] == "진입" and
           (al[1]["result"].startswith("거절: 수량 0") or
            (tot is not None and tot <= al[1]["equity"] * 1 + 1.0 and al[1]["qty"] < want * 0.1)))
    res["tests"]["T7_total_leverage"] = dict(
        pass_=ok7, log=[(r["coin"], r["result"], r.get("qty")) for r in al],
        eth_notional_total_after=round(tot) if tot else None, equity_x_maxLev=round(al[1]["equity"]) if len(al) == 2 else None,
        eth_qty_without_cap=want,
        note="maxLev 1: BTC 가 잔고 거의 전부를 쓰고, ETH 는 남은 명목가 여유만큼만(또는 0이면 거절)")

    res["pass"] = all(t["pass_"] for t in res["tests"].values())
    with open(os.path.join(HERE, "c_tests_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    for k, t in res["tests"].items():
        print(k, "PASS" if t["pass_"] else "FAIL", {kk: v for kk, v in t.items() if kk != "pass_"})
    print("검증 2:", "PASS" if res["pass"] else "FAIL")
    return 0 if res["pass"] else 1


def invariants_with_close(R, P, Ds):
    # 배분 기록에 신호 봉 종가를 붙여 명목가(진입 포함) 검사
    for r in R["alloc_log"]:
        D = Ds[f"KRW-{r['coin']}"]
        r["sig_close"] = D.C[D.T.index(r["time"])]
    return invariants(R, P, "")


if __name__ == "__main__":
    sys.exit(main())
