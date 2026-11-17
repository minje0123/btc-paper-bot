# -*- coding: utf-8 -*-
"""
E2~E8 — 사전 등록서 v3 4장 순차 탐색 (개발 구간에서 후보 생성, 선택 구간에서 선택). 가상 매매 연구.

  python research/E2E8/e2e8_run.py repro   # 연구 엔진이 E1 30m 거래를 그대로 재현하는지 확인 (먼저 통과해야 함)
  python research/E2E8/e2e8_run.py run     # E2→E3→E4→E5~E8 실행, results.json · trades_E2E8.csv · experiments.csv

결정 사항(결과 보기 전): research/E2E8/decisions.md. 최종 시험(holdout) 데이터는 읽지 않는다(common.load_5m 이 차단).
표준 라이브러리만 사용.
"""
import bisect
import csv
import datetime as dt
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
sys.path.insert(0, RESEARCH)
sys.path.insert(0, os.path.join(RESEARCH, "E1"))
sys.path.insert(0, HERE)
from common import COINS, KST, aggregate, atr_wilder, load_5m, load_split, median  # noqa: E402
from e1_params import e1_params  # noqa: E402
import e1_run as E1  # noqa: E402
import engine_research as ER  # noqa: E402

MIN_N = 150
TIE_R = 0.03
WARMUP_DAYS = 14
with open(os.path.join(RESEARCH, "E1", "results.json"), encoding="utf-8") as _f:
    _E1 = json.load(_f)
MAXSTOP = {int(k): v for k, v in _E1["fixed"]["max_stop_pct"].items()}

# ------------------------------------------------------------ 구성
# cfg = dict(struct="30m"|"E2", gate=bool, core=bool, filt=tuple("E5".."E8"))


def cfg_key(cfg):
    return f"{cfg['struct']}|gate={int(cfg['gate'])}|core={int(cfg['core'])}|filt={'+'.join(cfg['filt']) or '-'}"


def cfg_desc(cfg):
    parts = ["30m(E1 선택)" if cfg["struct"] == "30m" else "E2: 1H 구간+15m 확인·4H 방향"]
    if cfg["gate"]:
        parts.append("E3 비용 기준 손절(≤15%)")
    if cfg["core"]:
        parts.append("E4 핵심 셋업만")
    names = dict(E5="E5 T1≤1.5R", E6="E6 20SMA 이격 과다 제외", E7="E7 ATR 상위 20% 제외", E8="E8 피보나치 확인")
    parts += [names[f] for f in cfg["filt"]]
    return " + ".join(parts)


def complexity(cfg):
    return (cfg["struct"] != "30m") + cfg["gate"] + cfg["core"] + len(cfg["filt"])


# ------------------------------------------------------------ 데이터 준비 (프로세스마다 1회)
_CACHE = {}


def _pct80(vals):
    s = sorted(vals)
    return s[int(math.ceil(0.8 * len(s))) - 1]


def _snap_index(T, bar_td, Tz, z_td, at_close):
    """실행봉별 '완성된 마지막 상위봉' 번호. at_close=False: 봉 시작 시점 기준, True: 봉 마감 시점 기준"""
    ends = [t + z_td for t in Tz]
    out = []
    for t in T:
        ref = t + bar_td if at_close else t
        out.append(bisect.bisect_right(ends, ref) - 1)
    return out


def _fib_list(snap):
    out = []
    for S in snap:
        if not S["phV"] or not S["plV"]:
            out.append(None); continue
        hv, hb, lv, lb = S["phV"][-1], S["phB"][-1], S["plV"][-1], S["plB"][-1]
        d = hv - lv
        if d <= 0:
            out.append(None)
        elif hb > lb:
            out.append((1, hv - 0.618 * d, hv - 0.5 * d))
        else:
            out.append((-1, lv + 0.5 * d, lv + 0.618 * d))
    return out


def prepare():
    if _CACHE:
        return _CACHE
    dev_start, val_start, val_end = load_split()
    bars5 = {c: load_5m(c) for c in COINS}
    start_time = bars5["BTC"][0][0] + dt.timedelta(days=WARMUP_DAYS)
    st = {}
    for c in COINS:
        b30 = aggregate(*bars5[c], 30)
        b15 = aggregate(*bars5[c], 15)
        b60 = aggregate(*bars5[c], 60)
        D30 = ER.Data(*b30, dir_min=90, aux_min=360, bar_min=30)
        D15 = ER.Data(*b15, dir_min=240, aux_min=1440, bar_min=15)
        for D in (D30, D15):
            D.atr = atr_wilder(D.H, D.L, D.C, 14)
        snap60 = ER.build_structure(b60[1], b60[2], b60[3], b60[4], b60[5], 3)
        fib60 = _fib_list(snap60)
        z_td = dt.timedelta(minutes=60)
        mtf = dict(snap=snap60, Tz=b60[0], zone_min=60,
                   idx=_snap_index(D15.T, dt.timedelta(minutes=15), b60[0], z_td, at_close=False))
        fidx30 = _snap_index(D30.T, dt.timedelta(minutes=30), b60[0], z_td, at_close=True)
        fidx15 = _snap_index(D15.T, dt.timedelta(minutes=15), b60[0], z_td, at_close=True)
        st[c] = {"30m": dict(D=D30, fib=[fib60[j] if j >= 0 else None for j in fidx30]),
                 "E2": dict(D=D15, fib=[fib60[j] if j >= 0 else None for j in fidx15], mtf=mtf)}
    # E6·E7 기준값(개발 구간 실행봉, 결과와 무관하게 미리 계산)
    thr = {}
    for s in ("30m", "E2"):
        pooled = []
        per = {}
        for c in COINS:
            D = st[c][s]["D"]
            vals = []
            for i in range(D.n):
                if D.T[i] >= val_start:
                    break
                a, sm = D.atr[i], D.sma20[i]
                if a and sm:
                    pooled.append(abs(D.C[i] - sm) / a)
                    vals.append(a / D.C[i] * 100)
            per[c] = _pct80(vals)
        thr[s] = dict(chase_atr_p80=_pct80(pooled), atr_pct_p80=per)
    regimes = {}
    for c in COINS:
        rows, _p80 = E1.regime_table(bars5[c], val_start)
        regimes[c] = (rows, [r[0] for r in rows])
    _CACHE.update(st=st, thr=thr, regimes=regimes, start_time=start_time, val_start=val_start,
                  dev_start=dev_start, val_end=val_end)
    return _CACHE


def params_for(cfg, coin, cm):
    C_ = prepare()
    s = cfg["struct"]
    P = e1_params(MAXSTOP[30] if s == "30m" else MAXSTOP[60], cm)
    if s == "E2":
        P["_mtf"] = C_["st"][coin]["E2"]["mtf"]
        P["_noDirFvg"] = True
        P["_cancelR"] = 0.3
    if cfg["gate"]:
        P["_costGateR"] = 0.15
    if cfg["core"]:
        P["_coreOnly"] = True
    if "E5" in cfg["filt"]:
        P["_t1MaxR"] = 1.5
    if "E6" in cfg["filt"]:
        P["_chaseAtr"] = C_["thr"][s]["chase_atr_p80"]
    if "E7" in cfg["filt"]:
        P["_atrPctMax"] = C_["thr"][s]["atr_pct_p80"][coin]
    if "E8" in cfg["filt"]:
        P["_fib"] = C_["st"][coin][s]["fib"]
    return P


def run_one(args):
    """(cfg, coin, cm) → (거래 목록(간단 dict), 미청산 여부)"""
    cfg, coin, cm = args
    C_ = prepare()
    D = C_["st"][coin][cfg["struct"]]["D"]
    R = ER.run(D, params_for(cfg, coin, cm), start_time=C_["start_time"], close_at_end=False)
    rows, keys = C_["regimes"][coin]
    out = []
    for t in R["trades"]:
        out.append(dict(coin=coin, id=t["id"], side=t["side"], tag=t["tag"], grade=t["grade"],
                        entry_time=t["entry_time"], exit_time=t["exit_time"], entry=t["entry"], stop0=t["stop0"],
                        t1=t["t1"], exit_reason=t["exit_reason"], t1Hit=t["t1Hit"], R=t["R"],
                        period="dev" if t["entry_time"] < C_["val_start"] else "sel",
                        regime=E1.regime_of(rows, keys, t["entry_time"])))
    return out, R["position"] is not None


# ------------------------------------------------------------ 지표
def exit_type(t):
    return ("T1 후 " if t["t1Hit"] and t["exit_reason"] != "T1 반익절" else "") + t["exit_reason"]


def metrics_full(tr, tr15, seed):
    m = E1.metrics(tr, tr15, seed)
    if not tr:
        return m
    n = len(tr)
    et = {}
    for t in tr:
        et.setdefault(exit_type(t), []).append(t["R"])
    m["exit_types"] = {k: dict(share=len(v) / n, n=len(v), mean_R=sum(v) / len(v)) for k, v in
                       sorted(et.items(), key=lambda x: -len(x[1]))}
    m["stop_pct_median"] = median([abs(t["entry"] - t["stop0"]) / t["entry"] * 100 for t in tr])
    m["setups"] = {}
    for t in tr:
        k = t["tag"] + t["grade"]
        s = m["setups"].setdefault(k, [0, 0.0])
        s[0] += 1; s[1] += t["R"]
    m["setups"] = {k: dict(n=v[0], mean_R=v[1] / v[0]) for k, v in m["setups"].items()}
    return m


def evaluate(trades1, trades15, seed0):
    res = {}
    seed = seed0
    for per in ("dev", "sel"):
        base = [t for t in trades1 if t["period"] == per]
        c15 = [t for t in trades15 if t["period"] == per]
        cell = {}
        for c in COINS + ["ALL"]:
            tr = base if c == "ALL" else [t for t in base if t["coin"] == c]
            t15 = c15 if c == "ALL" else [t for t in c15 if t["coin"] == c]
            seed += 7
            cell[c] = metrics_full(tr, t15, seed)
        tt, p = E1.t_pvalue_one_sided([t["R"] for t in base])
        cell["ALL"]["t"], cell["ALL"]["p_one_sided"] = tt, p
        res[per] = cell
    return res


# ------------------------------------------------------------ 실행 묶음
class Runner:
    def __init__(self, pool):
        self.pool = pool
        self.cache = {}
        self.order = []

    def run(self, cfgs):
        todo = [c for c in cfgs if cfg_key(c) not in self.cache]
        jobs = [(c, coin, cm) for c in todo for coin in COINS for cm in (1.0, 1.5)]
        outs = list(self.pool.map(run_one, jobs)) if jobs else []
        k = 0
        for c in todo:
            t1, t15, open_end = [], [], []
            for coin in COINS:
                for cm in (1.0, 1.5):
                    tr, op = outs[k]; k += 1
                    (t1 if cm == 1.0 else t15).extend(tr)
                    if cm == 1.0 and op:
                        open_end.append(coin)
            seed = 2000 + 97 * len(self.order)
            self.cache[cfg_key(c)] = dict(cfg=c, key=cfg_key(c), desc=cfg_desc(c), trades=t1, trades15=t15,
                                          open_end_excluded=open_end, m=evaluate(t1, t15, seed))
            self.order.append(cfg_key(c))
            a = self.cache[cfg_key(c)]["m"]
            print(f"  {cfg_desc(c)} | 개발 n={a['dev']['ALL']['n']} R={a['dev']['ALL'].get('mean_R', 0):+.3f}"
                  f" | 선택 n={a['sel']['ALL']['n']} R={a['sel']['ALL'].get('mean_R', 0):+.3f}", flush=True)
        return [self.cache[cfg_key(c)] for c in cfgs]


def select(cands, stage):
    """D0-4: 선택 구간 n ≥ 150 중 평균 R 최고, 0.03R 이내면 더 단순한 안(같은 복잡도면 평균 R 높은 쪽)"""
    elig = [x for x in cands if x["m"]["sel"]["ALL"]["n"] >= MIN_N and x["m"]["sel"]["ALL"].get("mean_R") is not None]
    if not elig:
        return cands[0], dict(stage=stage, status="표본 하한 통과 후보 없음 → 이전 구성 유지", eligible=[])
    best = max(elig, key=lambda x: x["m"]["sel"]["ALL"]["mean_R"])
    bm = best["m"]["sel"]["ALL"]["mean_R"]
    tied = [x for x in elig if bm - x["m"]["sel"]["ALL"]["mean_R"] <= TIE_R + 1e-12]
    chosen = sorted(tied, key=lambda x: (complexity(x["cfg"]), -x["m"]["sel"]["ALL"]["mean_R"]))[0]
    info = dict(stage=stage, best=best["key"], best_mean=bm, within_tie=[x["key"] for x in tied],
                chosen=chosen["key"], chosen_mean=chosen["m"]["sel"]["ALL"]["mean_R"],
                excluded_small_n=[x["key"] for x in cands if x["m"]["sel"]["ALL"]["n"] < MIN_N],
                status=("최고 평균 R" if chosen is best else "최고와 0.03R 이내 → 더 단순한 안"))
    return chosen, info


# ------------------------------------------------------------ 재현 확인
def repro():
    C_ = prepare()
    want = {}
    with open(os.path.join(RESEARCH, "E1", "trades_E1.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["tf"] == "30m":
                want[(float(r["cost_mult"]), r["coin"], r["id"])] = (r["R"], r["exit_reason"], r["setup"])
    got = {}
    base = dict(struct="30m", gate=False, core=False, filt=())
    with ProcessPoolExecutor(max_workers=4) as pool:
        outs = list(pool.map(run_one, [(base, c, cm) for c in COINS for cm in (1.0, 1.5)]))
    k = 0
    for c in COINS:
        for cm in (1.0, 1.5):
            for t in outs[k][0]:
                got[(cm, c, t["id"])] = (f"{t['R']:.6f}", t["exit_reason"], t["tag"] + t["grade"])
            k += 1
    miss = [k_ for k_ in want if k_ not in got]
    extra = [k_ for k_ in got if k_ not in want]
    diff = [k_ for k_ in want if k_ in got and want[k_] != got[k_]]
    ok = not miss and not extra and not diff
    out = dict(checked_at_kst=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), e1_trades=len(want), engine_trades=len(got),
               missing=len(miss), extra=len(extra), different=len(diff), pass_=ok,
               samples=dict(missing=[list(map(str, x)) for x in miss[:5]], extra=[list(map(str, x)) for x in extra[:5]],
                            different=[[list(map(str, x)), want[x], got[x]] for x in diff[:5]]),
               thresholds=C_["thr"])
    with open(os.path.join(HERE, "repro_check.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k_: v for k_, v in out.items() if k_ != "thresholds"}, ensure_ascii=False))
    return ok


# ------------------------------------------------------------ 본 실행
def main():
    t0 = time.time()
    with open(os.path.join(HERE, "repro_check.json"), encoding="utf-8") as f:
        if not json.load(f)["pass_"]:
            raise SystemExit("재현 확인 실패 — E2~E8 실행 안 함")
    stages = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        R = Runner(pool)
        cur = dict(struct="30m", gate=False, core=False, filt=())
        # E2
        print("E2", flush=True)
        cands = R.run([cur, dict(cur, struct="E2")])
        ch, info = select(cands, "E2"); stages.append(dict(info, candidates=[x["key"] for x in cands])); cur = ch["cfg"]
        # E3
        print("E3", flush=True)
        cands = R.run([cur, dict(cur, gate=True)])
        ch, info = select(cands, "E3"); stages.append(dict(info, candidates=[x["key"] for x in cands])); cur = ch["cfg"]
        # E4
        print("E4", flush=True)
        cands = R.run([cur, dict(cur, core=True)])
        ch, info = select(cands, "E4"); stages.append(dict(info, candidates=[x["key"] for x in cands])); cur = ch["cfg"]
        # E5~E8 단일 필터
        print("E5~E8", flush=True)
        prev = cur
        singles = [dict(prev, filt=(f,)) for f in ("E5", "E6", "E7", "E8")]
        cands = R.run([prev] + singles)
        pm = cands[0]["m"]["sel"]["ALL"]["mean_R"]
        better = [x for x in cands[1:] if x["m"]["sel"]["ALL"]["n"] >= MIN_N
                  and x["m"]["sel"]["ALL"]["mean_R"] - pm > TIE_R + 1e-12]
        combo_info = dict(rule="직전 구성보다 0.03R 넘게 높고 n≥150인 단일 필터가 2개 이상이면 상위 2개 조합을 후보로 추가",
                          better_than_prev=[x["key"] for x in better])
        if len(better) >= 2:
            top2 = sorted(better, key=lambda x: -x["m"]["sel"]["ALL"]["mean_R"])[:2]
            combo = dict(prev, filt=tuple(sorted(top2[0]["cfg"]["filt"] + top2[1]["cfg"]["filt"])))
            cands = cands + R.run([combo])
            combo_info["combo"] = cfg_key(combo)
        else:
            combo_info["combo"] = None
        ch, info = select(cands, "E5~E8")
        stages.append(dict(info, candidates=[x["key"] for x in cands], combo=combo_info))
        cur = ch["cfg"]
        final = R.cache[cfg_key(cur)]
        allc = [R.cache[k] for k in R.order]

    # 저장
    res = dict(experiment="E2~E8", run_at_kst=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
               note="개발·선택 구간 결과는 증거가 아니다(사전 등록서 v3 4장 선택 절차 3). 선택 구간 = 2026-03-18 ~ 2026-11-16 13:00(변경 기록 09:00).",
               fixed=dict(max_stop_pct={"30m": MAXSTOP[30], "E2": MAXSTOP[60]}, thresholds=prepare()["thr"],
                          min_n=MIN_N, tie_R=TIE_R, trade_start=str(prepare()["start_time"]),
                          decisions="research/E2E8/decisions.md"),
               stages=stages, final=final["key"], final_desc=final["desc"],
               n_candidate_rows=sum(len(s["candidates"]) for s in stages), n_distinct_configs=len(allc),
               candidates={x["key"]: dict(desc=x["desc"], complexity=complexity(x["cfg"]),
                                          open_end_excluded=x["open_end_excluded"], metrics=x["m"]) for x in allc})
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(HERE, "trades_E2E8.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["config", "cost_mult", "coin", "period", "regime", "id", "side", "setup", "entry_time", "exit_time",
                    "entry", "stop0", "t1", "exit_type", "R"])
        for x in allc:
            for cm, ts in ((1.0, x["trades"]), (1.5, x["trades15"])):
                for t in ts:
                    w.writerow([x["key"], cm, t["coin"], t["period"], t["regime"], t["id"], t["side"], t["tag"] + t["grade"],
                                t["entry_time"].strftime("%Y-%m-%d %H:%M"), t["exit_time"].strftime("%Y-%m-%d %H:%M"),
                                repr(t["entry"]), repr(t["stop0"]), repr(t["t1"]), exit_type(t), f"{t['R']:.6f}"])
    print("최종:", final["desc"], "총", round(time.time() - t0), "s")


if __name__ == "__main__":
    if sys.argv[1] == "repro":
        sys.exit(0 if repro() else 1)
    elif sys.argv[1] == "run":
        main()
