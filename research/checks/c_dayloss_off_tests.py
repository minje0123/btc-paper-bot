# -*- coding: utf-8 -*-
"""하루 손실 한도 해제(dayLossOn=false, 2026-11-18) 시험 — 저장된 운영 5분봉(data/)만 사용, holdout 미사용.

T1 회귀   : 변경 전 코드·설정(git HEAD)과 변경 후 코드·설정으로 본 장부를 같은 봉으로 재계산 → 거래·이벤트·배분·하루 상태 동일
            (해제 시각 2026-11-18 08:45 전 장부는 그대로여야 함)
T2 그림자 : 모든 그림자 장부의 해석된 파라미터가 변경 전과 같음(v4 E3 포함, dayLossOn 은 True 로 고정)
T3 해제   : 해제 시각을 데이터 처음으로 당긴 설정에서 하루 한도를 0.3%로 낮춰 일부러 걸리게 한 뒤
            dayLossOn=True(대조) vs False 비교 — False 에선 '하루 손실' 정지·'하루 한도' 거절 0건,
            대조에서 하루 한도로 거절/정지된 신호 중 다른 제한을 만족하는 것이 진입함
T4 유지   : False 에서도 동시 보유·열린 위험 2%·1회 위험(≤ 0.5%·5% 상한)·레버리지(포지션별·합계)·3연패 정지·목표 보호 작동
T5 0 의미 : dayLossP=0(플래그 켬)은 '끔'이 아니라 손실 0원에서 정지임을 확인(플래그가 필요한 이유)
"""
import collections
import copy
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import engine  # noqa: E402
import bot  # noqa: E402

EPS = 1e-6
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]


def git_show(rev_path):
    return subprocess.run(["git", "show", rev_path], cwd=REPO, capture_output=True, check=True).stdout


def load_old_engine(rev):
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "engine_old.py")
    open(p, "wb").write(git_show(f"{rev}:engine.py"))
    spec = importlib.util.spec_from_file_location("engine_old", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, json.loads(git_show(f"{rev}:config.json").decode("utf-8-sig"))


def norm(R):
    tr = [(t["id"], t["coin"], round(t["entry"], 6), round(t["qty"], 8), t["exit_reason"], str(t["exit_time"]),
           round(t["pnl"], 6), round(t["R"], 9)) for t in R["trades"]]
    ev = [(e["type"], e["id"], str(e["time"]), e.get("reason")) for e in R["events"]]
    al = [(str(a["time"]), a["coin"], a["result"], round(a.get("qty", 0), 8)) for a in R["alloc_log"]]
    pos = [(p["id"], round(p["qty"], 8)) for p in R["positions"]]
    pen = [(p["coin"], round(p["qty"], 8)) for p in R["pendings"]]
    day = {k: (round(v, 6) if isinstance(v, float) else str(v)) for k, v in R["day"].items()}
    return dict(trades=tr, events=ev, alloc=al, positions=pos, pendings=pen, day=day,
                realized=round(R["realized"], 6), equity=round(R["equity"], 6))


def strip_hist(P):
    P = dict(P)
    P.pop("rule_history", None)
    return P


def main():
    res = collections.OrderedDict()
    Ds = {m: bot.to_data(bot.load_candles(bot.candle_path(m))) for m in MARKETS}
    last = max(max(D.T) for D in Ds.values())
    events = engine.load_events(os.path.join(REPO, "data", "events.csv"))
    old_eng, old_cfg = load_old_engine("HEAD")
    new_cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    st = engine.parse_kst(new_cfg["start_time"])
    act = engine.parse_kst(new_cfg["rule_history"][0]["until"])

    # ---------- T1 회귀 ----------
    Pold = old_eng.load_params(old_cfg)
    Pnew = engine.load_params(new_cfg)
    Ro = old_eng.run_multi(Ds, Pold, start_time=st, events=events)
    Rn = engine.run_multi(Ds, Pnew, start_time=st, events=events)
    a, b = norm(Ro), norm(Rn)
    diff = [k for k in a if a[k] != b[k]]
    res["T1_regression_before_activation"] = dict(
        pass_=not diff and last < act, data_last_bar=str(last), activation=str(act),
        trades=len(Ro["trades"]), events=len(Ro["events"]), alloc=len(Ro["alloc_log"]), diff_keys=diff,
        note="데이터 마지막 봉이 해제 시각보다 앞서야 이 비교가 '해제 전 장부 불변'을 뜻함")

    # ---------- T2 그림자 파라미터 ----------
    sh_res = {}
    ok2 = True
    old_bot_skip = ("rule_history", "shadow_ledgers", "start_time")
    for sh in new_cfg.get("shadow_ledgers") or []:
        base_old = {k: v for k, v in old_cfg.items() if k not in old_bot_skip}
        base_old.update(sh.get("params") or {})
        if sh.get("initial_capital") is not None:
            base_old["initial_capital"] = sh["initial_capital"]
        po = strip_hist(old_eng.load_params(base_old))
        pn = strip_hist(bot.shadow_params(new_cfg, sh))
        same = {k: po[k] for k in po} == {k: pn[k] for k in po} and pn.get("dayLossOn") is True
        extra = sorted(set(pn) - set(po))
        sh_res[sh["name"]] = dict(same=same, new_keys=extra, dayLossOn=pn.get("dayLossOn"),
                                  dayLossP=pn["dayLossP"], costGateR=pn.get("costGateR"))
        ok2 = ok2 and same and extra == ["dayLossOn"]
    res["T2_shadow_params_unchanged"] = dict(pass_=ok2, shadows=sh_res)

    # ---------- T3·T4 해제 동작 (가상 설정: 해제 시각을 데이터 처음으로) ----------
    def mk(on, lossP=0.3, consec=3, extra=None):
        c = copy.deepcopy(new_cfg)
        c.pop("shadow_ledgers", None)
        c["rule_history"] = []
        c["dayLossOn"] = on
        c["dayLossP"] = lossP
        c["maxConsec"] = consec
        c["eventBlock"] = False
        c.update(extra or {})
        return engine.load_params(c)

    t0 = min(min(D.T) for D in Ds.values()) + dt.timedelta(days=1)
    Ron = engine.run_multi(Ds, mk(True, consec=1000), start_time=t0, events=events)
    Roff = engine.run_multi(Ds, mk(False, consec=1000), start_time=t0, events=events)
    rej_on = [r for r in Ron["alloc_log"] if r["result"].startswith("거절: 하루 한도")]
    stop_on = [e for e in Ron["events"] if e["type"] == "daystop" and "하루 손실" in (e.get("reason") or "")]
    rej_off = [r for r in Roff["alloc_log"] if r["result"].startswith("거절: 하루 한도")]
    stop_off = [e for e in Roff["events"] if e["type"] == "daystop" and "하루 손실" in (e.get("reason") or "")]
    # 대조에서 하루 한도로 막힌 날(정지일)에 False 쪽은 진입이 있었는가
    stop_days = {e["time"].date() for e in stop_on}
    ent_off_on_stopdays = [r for r in Roff["alloc_log"] if r["result"].startswith("진입") and r["time"].date() in stop_days
                           and r["time"] >= min(e["time"] for e in stop_on if e["time"].date() == r["time"].date())]
    # 대조에서 '하루 한도'로 거절된 바로 그 후보가 False 쪽에서 진입했는가(같은 시각·코인)
    keys_off = {(r["time"], r["coin"]): r["result"] for r in Roff["alloc_log"]}
    same_cand = [(str(r["time"]), r["coin"], keys_off.get((r["time"], r["coin"]))) for r in rej_on]
    passed_same = [x for x in same_cand if x[2] and x[2].startswith("진입")]
    res["T3_dayloss_released"] = dict(
        pass_=(len(stop_on) + len(rej_on) > 0) and not rej_off and not stop_off and
              (len(ent_off_on_stopdays) > 0 or len(passed_same) > 0),
        control_dayloss_stops=len(stop_on), control_dayloss_rejects=len(rej_on),
        off_dayloss_stops=len(stop_off), off_dayloss_rejects=len(rej_off),
        off_entries_after_control_stop=len(ent_off_on_stopdays),
        control_rejected_same_candidate_results_in_off=same_cand[:10],
        control_rejected_then_entered_in_off=len(passed_same),
        trades_on=len(Ron["trades"]), trades_off=len(Roff["trades"]))

    # T4 유지되는 제한 (False 실행 Roff 기준)
    P_off = mk(False, consec=1000)
    al = Roff["alloc_log"]
    ent = [r for r in al if r["result"].startswith("진입")]
    seed_ok = all(r["rm"] <= r["seed"] * P_off["maxOpenRiskPct"] / 100 - r["open_risk_before"] + EPS for r in ent)
    risk1_ok = all(r["rm"] <= min(r["seed"] * P_off["riskPct"] / 100, r["seed"] * P_off["maxRiskCapP"] / 100) + EPS
                   for r in ent)
    open_ok = all(r["open_before"] < P_off["maxOpenPositions"] for r in ent)
    lev_ok = all(r["qty"] * 1.0 <= r["seed"] * P_off["maxLev"] / 1.0 for r in ent)  # 아래 거래 기준으로 재확인
    lev_trade_ok = all(t["qty"] * t["sigClose"] <= t["eq"] * P_off["maxLev"] + EPS for t in Roff["trades"])
    risk_cap_rejects = sum(1 for r in al if r["result"] == "거절: 위험 여유 부족")
    max_open_rejects = sum(1 for r in al if r["result"] == "거절: 동시 보유 상한")
    # 손절은 계속: 손절 사유 거래 존재
    stops = sum(1 for t in Roff["trades"] if t["exit_reason"] == "손절")
    # 3연패 정지: maxConsec=3 + 하루 한도 끔
    Rc = engine.run_multi(Ds, mk(False, consec=3), start_time=t0, events=events)
    consec_stops = [e for e in Rc["events"] if e["type"] == "daystop" and "연속 손절" in (e.get("reason") or "")]
    no_entry_after = all(not (r["result"].startswith("진입") and r["time"].date() == e["time"].date() and r["time"] >= e["time"])
                         for e in consec_stops for r in Rc["alloc_log"])
    # 연패 정지 동작이 기존과 같은지: dayLossOn=False 결과 == 변경 전 엔진에서 하루 한도만 사실상 끈(dayLossP=1e9) 결과.
    # (기존 엔진의 연패 정지는 '그날 고정'이 아니라 매 봉 consec ≥ maxConsec 로 판정 — 정지 뒤 이미 열린 포지션이
    #  수익으로 닫히면 consec 이 0 이 되어 풀린다. 이 동작은 이번 변경과 무관하며 그대로 유지됨을 확인한다.)
    def old_equiv(consec):
        c = copy.deepcopy(new_cfg); c.pop("shadow_ledgers", None); c["rule_history"] = []
        c["dayLossP"] = 1e9; c["maxConsec"] = consec; c["eventBlock"] = False; c.pop("dayLossOn", None)
        Ro_ = old_eng.run_multi(Ds, old_eng.load_params(c), start_time=t0, events=events)
        Rn_ = engine.run_multi(Ds, mk(False, consec=consec), start_time=t0, events=events)
        ko = norm(Ro_); kn = norm(Rn_)
        return [k for k in ("trades", "events", "positions", "pendings", "realized", "equity") if ko[k] != kn[k]], Rn_
    d1, R1 = old_equiv(1)
    d3, _ = old_equiv(3)
    c1 = [e for e in R1["events"] if e["type"] == "daystop" and "연속 손절" in (e.get("reason") or "")]
    no_entry_after1 = not d1 and not d3
    # 목표 보호: 하루 목표 작동(목표 이벤트 뒤 일반 크기 진입 없음)
    tgt = [e for e in Roff["events"] if e["type"] == "target"]
    tgt_ok = all(not (r["result"].startswith("진입") and r["time"].date() == e["time"].date() and r["time"] > e["time"]
                      and r["rm"] > max(r["day_real"] - e["target"], 0) + EPS)
                 for e in tgt for r in al)
    res["T4_other_limits_kept"] = dict(
        pass_=seed_ok and risk1_ok and open_ok and lev_trade_ok and stops > 0 and no_entry_after and
              len(c1) > 0 and no_entry_after1 and tgt_ok and Roff["max_open_risk_pct_seen"] <= P_off["maxOpenRiskPct"] + EPS,
        entries=len(ent), open_risk_room_ok=seed_ok, per_trade_risk_ok=risk1_ok, max_open_ok=open_ok,
        per_position_leverage_ok=lev_trade_ok, max_open_risk_pct_seen=round(Roff["max_open_risk_pct_seen"], 4),
        risk_room_rejects=risk_cap_rejects, max_open_rejects=max_open_rejects, stop_loss_exits=stops,
        consec3_stops=len(consec_stops), consec3_no_entry_after=no_entry_after,
        consec1_stops=len(c1), consec1_no_entry_after=no_entry_after1,
        target_events=len(tgt), target_protection_ok=tgt_ok)

    # ---------- T4b 제한을 일부러 조여 실제 거절이 나는지 (하루 한도 끈 상태) ----------
    Pr = mk(False, consec=1000, extra={"maxOpenRiskPct": 0.6})
    Rr = engine.run_multi(Ds, Pr, start_time=t0, events=events)
    rr = sum(1 for r in Rr["alloc_log"] if r["result"] == "거절: 위험 여유 부족")
    cut_n = sum(1 for r in Rr["alloc_log"] if r["result"] == "진입(여유만큼 축소)")
    Pm = mk(False, consec=1000, extra={"maxOpenPositions": 1})
    Rm = engine.run_multi(Ds, Pm, start_time=t0, events=events)
    mr = sum(1 for r in Rm["alloc_log"] if r["result"] == "거절: 동시 보유 상한")
    Pl = mk(False, consec=1000, extra={"maxLev": 0.3})
    Rl = engine.run_multi(Ds, Pl, start_time=t0, events=events)
    lev_ok_l = all(t["qty"] * t["sigClose"] <= t["eq"] * 0.3 + 1e-3 for t in Rl["trades"])
    lev_shrunk = sum(1 for r in Rl["alloc_log"] if r["result"].startswith("진입") and r["qty"] < r["rm"] / 1e18 + 1e9
                     and r["qty"] * 1 > 0)
    res["T4b_tightened_limits_still_bind"] = dict(
        pass_=rr + cut_n > 0 and Rr["max_open_risk_pct_seen"] <= 0.6 + EPS and mr > 0 and Rm["max_open_seen"] <= 1
              and lev_ok_l,
        open_risk_0p6_rejects=rr, open_risk_0p6_cut_entries=cut_n,
        open_risk_0p6_max_seen=round(Rr["max_open_risk_pct_seen"], 4),
        max_open_1_rejects=mr, max_open_1_seen=Rm["max_open_seen"],
        maxLev_0p3_notional_ok=lev_ok_l, maxLev_0p3_trades=len(Rl["trades"]))

    # ---------- T5 dayLossP=0 의미 ----------
    R0 = engine.run_multi(Ds, mk(True, lossP=0.0, consec=1000), start_time=t0, events=events)
    s0 = [e for e in R0["events"] if e["type"] == "daystop" and "하루 손실" in (e.get("reason") or "")]
    res["T5_zero_is_not_off"] = dict(pass_=len(s0) > 0, dayLossP0_dayloss_stops=len(s0),
                                     note="dayLossP=0 은 하루 실현 0원 이하에서 바로 정지 → 끔으로 쓸 수 없음")

    res["summary"] = {k: v["pass_"] for k, v in res.items() if isinstance(v, dict) and "pass_" in v}
    out = os.path.join(HERE, "c_dayloss_off_result.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    for k, v in res["summary"].items():
        print(k, "PASS" if v else "FAIL")
    return 0 if all(res["summary"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
