# -*- coding: utf-8 -*-
"""
E1 — 현재 v1.4.1 규칙을 5m·15m·30m·1H·4H 실행 시간봉에서, 4코인, 개발·검증 구간 각각 (사전 등록서 v3 4장 + 'E1 구현 세부')

고정 사항(결과 보기 전, 사전 등록서 그대로)
  - 봉 개수 고정: 20SMA·스윙 좌우 3봉·기울기 5봉 등은 실행봉 기준 그대로. 방향 3k분·보조 12k분 (5m: 15m·1H)
  - 손절폭: 최소 0.40% 유지, 최대 1.5% × (시간봉 ATR(14) 중앙값 ÷ 5m ATR(14) 중앙값), 개발 구간에서 1회 계산
  - 나머지 값(구간 오차·기울기 기준·추격 금지 이격·T1 규칙·수수료 0.05%·슬리피지 1틱)은 5m 과 동일
  - 자금 관리 끔(1R 고정, 하루 한도·목표·첫 승 절반·출금 없음), 코인당 동시 포지션 1개, 거래당 순수 R(비용 반영)
  - 국면: 일봉 20SMA 5봉 기울기 ±0.5% 초과 = 상승/하락, 아니면 횡보. 일봉 ATR(14)이 개발 구간 80백분위 초과면 고변동(우선)
  - 선택: 검증 구간 평균 R 최고 + 표본 하한(4코인 합계 150건). 0.03R 이내 동률이면 자동 선택하지 않고 모두 보고

이 스크립트에서 정한 해석(사전 등록서에 수치가 없는 부분, 결과 보기 전 결정 — report.md 에도 기록)
  - ATR 비율: ATR(14)(Wilder)을 종가로 나눈 ATR% 의 중앙값, 4코인 개발 구간을 합쳐 시간봉별 1개 값
  - 국면의 일봉 ATR: ATR% (가격 수준 변화 영향 제거), 80백분위는 코인별 개발 구간 일봉에서 계산.
    거래의 국면 = 진입 시각 직전에 '완성된' 일봉(KST 하루)의 국면(미래 참조 없음)
  - 슬리피지 1틱 = 체결가의 원화 호가 단위(업비트 표: BTC·ETH 1,000원, SOL 50/10원, XRP 1원). BTC 는 기존 1,000원과 같음
  - 비용×1.5 = 수수료 0.075% + 슬리피지 1.5틱
  - 지표 준비: 18개월 데이터 첫 봉부터 계산하되 처음 14일은 진입 안 함(모든 시간봉 공통, 4H 의 12H 방향 20SMA+기울기 준비 기간)
  - 구간 구분은 진입 시각 기준. 데이터 끝에 남은 포지션은 제외(청산 안 된 거래)
  - 블록 부트스트랩: 거래가 있는 날(주) 블록을 같은 개수만큼 복원 추출, 평균 R = 합계 R ÷ 거래 수, 2,000회, 고정 시드
  - 4코인 합계의 블록은 같은 날(주)의 4코인 거래를 한 블록으로(코인 간 상관 유지)
  - p값: 거래당 R 의 1표본 t 검정(평균 > 0, 단측). 거래 간·코인 간 독립 가정이 약하므로 참고용
"""
import bisect
import csv
import datetime as dt
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import (COINS, KST, RESEARCH, TF_NAME, TFS, aggregate, atr_wilder, engine, load_5m,  # noqa: E402
                    load_split, make_data, median)
from e1_params import e1_params  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
NBOOT = 2000
WARMUP_DAYS = 14
MIN_N = 150
TIE_R = 0.03
REGIMES = ["상승", "하락", "횡보", "고변동"]


# ------------------------------------------------------------ 통계 도구
def betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(c) > 1e-300 else 1e300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(c) > 1e-300 else 1e300
        de = d * c
        h *= de
        if abs(de - 1) < 1e-12:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * betacf(a, b, x) / a
    return 1 - math.exp(lb) * betacf(b, a, 1 - x) / b


def t_pvalue_one_sided(Rs):
    """H1: 평균 > 0. 반환 (t, p)"""
    n = len(Rs)
    if n < 2:
        return None, None
    m = sum(Rs) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in Rs) / (n - 1))
    if sd == 0:
        return None, None
    t = m / (sd / math.sqrt(n))
    df = n - 1
    tail = 0.5 * betai(df / 2, 0.5, df / (df + t * t))   # P(T > |t|)
    p = tail if t > 0 else 1 - tail
    return t, p


def holm(ps):
    """[(이름, p)] → {이름: 보정 p}"""
    items = sorted([x for x in ps if x[1] is not None], key=lambda x: x[1])
    k = len(items)
    out, run = {}, 0.0
    for i, (nm, p) in enumerate(items):
        run = max(run, min(1.0, (k - i) * p))
        out[nm] = run
    for nm, p in ps:
        out.setdefault(nm, None)
    return out


def block_boot(trades, keyf, seed):
    """블록 부트스트랩 95% CI (평균 R)"""
    blocks = {}
    for t in trades:
        k = keyf(t["entry_time"])
        s = blocks.setdefault(k, [0.0, 0])
        s[0] += t["R"]; s[1] += 1
    B = list(blocks.values())
    nb = len(B)
    if nb < 2:
        return None
    rnd = random.Random(seed)
    ms = []
    for _ in range(NBOOT):
        sr = sn = 0
        for _j in range(nb):
            b = B[rnd.randrange(nb)]
            sr += b[0]; sn += b[1]
        ms.append(sr / sn)
    ms.sort()
    return [ms[int(0.025 * NBOOT)], ms[int(0.975 * NBOOT) - 1]], nb


def day_key(t):
    return t.date()


def week_key(t):
    d = t.date()
    return d - dt.timedelta(days=d.weekday())


def mdd_R(trades):
    cum = peak = mdd = 0.0
    for t in sorted(trades, key=lambda x: (x["exit_time"], x["coin"])):
        cum += t["R"]
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def max_consec_loss(trades):
    best = cur = 0
    for t in sorted(trades, key=lambda x: (x["exit_time"], x["coin"])):
        if t["R"] < 0:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def metrics(trades, trades15, seed):
    n = len(trades)
    out = dict(n=n)
    if n == 0:
        return out
    Rs = [t["R"] for t in trades]
    m = sum(Rs) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in Rs) / (n - 1)) if n > 1 else None
    bd = block_boot(trades, day_key, seed)
    bw = block_boot(trades, week_key, seed + 1)
    out.update(mean_R=m, sd_R=sd, win_rate=sum(1 for r in Rs if r > 0) / n * 100, sum_R=sum(Rs),
               ci95_day=bd[0] if bd else None, n_day_blocks=bd[1] if bd else None,
               ci95_week=bw[0] if bw else None, n_week_blocks=bw[1] if bw else None,
               mean_R_cost15=sum(t["R"] for t in trades15) / len(trades15) if trades15 else None,
               n_cost15=len(trades15), mdd_R=mdd_R(trades), max_consec_loss=max_consec_loss(trades))
    reg = {}
    for g in REGIMES:
        rs = [t["R"] for t in trades if t["regime"] == g]
        reg[g] = dict(n=len(rs), mean_R=sum(rs) / len(rs) if rs else None)
    rs = [t["R"] for t in trades if t["regime"] is None]
    if rs:
        reg["미정(일봉 지표 준비 전)"] = dict(n=len(rs), mean_R=sum(rs) / len(rs))
    out["regimes"] = reg
    return out


# ------------------------------------------------------------ 국면
def regime_table(b5, val_start):
    """코인 일봉 국면: [(일봉 완성 시각, 국면)] 과 고변동 기준값"""
    d = aggregate(*b5, 1440)
    T, H, L, C = d[0], d[2], d[3], d[4]
    s = engine.sma(C, 20)
    atr = atr_wilder(H, L, C, 14)
    atrp = [a / C[j] * 100 if a is not None else None for j, a in enumerate(atr)]
    dev = sorted(x for j, x in enumerate(atrp) if x is not None and T[j] + dt.timedelta(days=1) <= val_start)
    p80 = dev[int(math.ceil(0.8 * len(dev))) - 1]
    rows = []
    for j in range(len(T)):
        g = None
        if j >= 5 and s[j] is not None and s[j - 5] is not None:
            sl = (s[j] - s[j - 5]) / s[j - 5] * 100
            g = "상승" if sl > 0.5 else ("하락" if sl < -0.5 else "횡보")
            if atrp[j] is not None and atrp[j] > p80:
                g = "고변동"
        rows.append((T[j] + dt.timedelta(days=1), g))
    return rows, p80


def regime_of(rows, keys, t):
    k = bisect.bisect_right(keys, t) - 1
    return rows[k][1] if k >= 0 else None


# ------------------------------------------------------------ 본 실행
def main():
    t0 = time.time()
    dev_start, val_start, val_end = load_split()
    bars5 = {c: load_5m(c) for c in COINS}

    # 1) 최대 손절폭 비율 (개발 구간 ATR% 중앙값, 4코인 합침)
    atr_med = {}
    for tf in TFS:
        vals = []
        for c in COINS:
            b = aggregate(*bars5[c], tf)
            a = atr_wilder(b[2], b[3], b[4], 14)
            vals += [a[j] / b[4][j] * 100 for j in range(len(a)) if a[j] is not None and b[0][j] < val_start]
        atr_med[tf] = median(vals)
    max_stop = {tf: 1.5 * atr_med[tf] / atr_med[5] for tf in TFS}
    print("최대 손절폭:", {TF_NAME[k]: round(v, 3) for k, v in max_stop.items()}, flush=True)

    # 2) 국면표
    regimes, p80s = {}, {}
    for c in COINS:
        rows, p80 = regime_table(bars5[c], val_start)
        regimes[c] = (rows, [r[0] for r in rows])
        p80s[c] = p80

    # 3) 실행
    start_time = bars5["BTC"][0][0] + dt.timedelta(days=WARMUP_DAYS)
    allt = {}   # (tf, cost) → [trade]
    open_end = {}
    for tf in TFS:
        for c in COINS:
            b = aggregate(*bars5[c], tf)
            D = make_data(b, tf)
            for cm in (1.0, 1.5):
                R = engine.run(D, e1_params(max_stop[tf], cm), start_time=start_time, close_at_end=False)
                rows, keys = regimes[c]
                for t in R["trades"]:
                    t["coin"] = c
                    t["period"] = "dev" if t["entry_time"] < val_start else "val"
                    t["regime"] = regime_of(rows, keys, t["entry_time"])
                allt.setdefault((tf, cm), []).extend(R["trades"])
                if cm == 1.0:
                    open_end[(tf, c)] = R["position"] is not None
        print(TF_NAME[tf], "완료", round(time.time() - t0), "s", flush=True)

    # 4) 지표
    res = dict(experiment="E1", run_at_kst=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
               note="개발·검증 구간 결과는 증거가 아니다(사전 등록서 v3 4장 선택 절차 3). 성과로 인정하는 숫자는 최종 시험·전진 시험뿐.",
               fixed=dict(atr_median_pct=atr_med, max_stop_pct=max_stop, min_stop_pct=0.40, warmup_days=WARMUP_DAYS,
                          trade_start=str(start_time), split=dict(dev_start=str(dev_start), val_start=str(val_start),
                                                                    val_end=str(val_end)),
                          regime_highvol_p80_daily_atr_pct=p80s, n_boot=NBOOT, min_n=MIN_N, tie_R=TIE_R,
                          htf=dict((TF_NAME[tf], dict(direction_min=3 * tf, aux_min=12 * tf)) for tf in TFS)),
               open_positions_excluded=dict((f"{TF_NAME[k[0]]} {k[1]}", v) for k, v in open_end.items() if v),
               cells={})
    seed = 1117
    for tf in TFS:
        for per in ("dev", "val"):
            base = [t for t in allt[(tf, 1.0)] if t["period"] == per]
            c15 = [t for t in allt[(tf, 1.5)] if t["period"] == per]
            cell = {}
            for c in COINS + ["ALL"]:
                tr = base if c == "ALL" else [t for t in base if t["coin"] == c]
                tr15 = c15 if c == "ALL" else [t for t in c15 if t["coin"] == c]
                seed += 7
                cell[c] = metrics(tr, tr15, seed)
            tt, p = t_pvalue_one_sided([t["R"] for t in base])
            cell["ALL"]["t"] = tt; cell["ALL"]["p_one_sided"] = p
            res["cells"][f"{TF_NAME[tf]}|{per}"] = cell
            a = cell["ALL"]
            print(TF_NAME[tf], per, "n", a["n"], "평균R", round(a.get("mean_R") or 0, 4), "CI일", a.get("ci95_day"),
                  "비용1.5", round(a.get("mean_R_cost15") or 0, 4), flush=True)

    # 5) Holm
    for per in ("dev", "val"):
        ps = [(TF_NAME[tf], res["cells"][f"{TF_NAME[tf]}|{per}"]["ALL"].get("p_one_sided")) for tf in TFS]
        h = holm(ps)
        for tf in TFS:
            res["cells"][f"{TF_NAME[tf]}|{per}"]["ALL"]["p_holm"] = h[TF_NAME[tf]]

    # 6) 검정력: n ≈ (1.645+0.84)^2 σ^2 / 0.15^2  (σ = 개발 구간 E1 거래당 R 표준편차)
    power = {}
    for tf in TFS:
        sd = res["cells"][f"{TF_NAME[tf]}|dev"]["ALL"].get("sd_R")
        power[TF_NAME[tf]] = dict(sigma_dev=sd, n_required=math.ceil((1.645 + 0.84) ** 2 * sd ** 2 / 0.15 ** 2) if sd else None)
    res["power"] = power

    # 7) 선택 (검증 평균 R 최고 + 4코인 합계 150건 이상, 0.03R 이내 동률이면 자동 선택 안 함)
    elig = [(TF_NAME[tf], res["cells"][f"{TF_NAME[tf]}|val"]["ALL"]) for tf in TFS]
    ok = [(nm, a["mean_R"]) for nm, a in elig if a["n"] >= MIN_N and a.get("mean_R") is not None]
    sel = dict(rule="검증 구간 4코인 합계 평균 R 최고, 검증 거래 수 ≥ 150. 1위와 0.03R 이내가 여럿이면 자동 선택하지 않음",
               eligible=ok, excluded_small_n=[nm for nm, a in elig if a["n"] < MIN_N])
    if not ok:
        sel.update(selected=None, status="선택 불가(표본 하한을 넘는 시간봉 없음)")
    else:
        best = max(ok, key=lambda x: x[1])
        ties = [nm for nm, m in ok if best[1] - m <= TIE_R]
        if len(ties) > 1:
            sel.update(selected=None, status="동률(0.03R 이내) — 자동 선택하지 않음, 송민제 결정 필요", tied=ties, best=best[0])
        else:
            sel.update(selected=best[0], status="자동 선택")
    res["selection"] = sel
    res["configurations_run"] = len(TFS)
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    # 거래 목록(재현·검토용)
    with open(os.path.join(OUT_DIR, "trades_E1.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tf", "cost_mult", "coin", "period", "regime", "id", "side", "setup", "entry_time", "exit_time",
                    "entry", "stop0", "t1", "exit_reason", "R"])
        for (tf, cm), ts in allt.items():
            for t in ts:
                w.writerow([TF_NAME[tf], cm, t["coin"], t["period"], t["regime"], t["id"], t["side"], t["tag"] + t["grade"],
                            t["entry_time"].strftime("%Y-%m-%d %H:%M"), t["exit_time"].strftime("%Y-%m-%d %H:%M"),
                            repr(t["entry"]), repr(t["stop0"]), repr(t["t1"]), t["exit_reason"], f"{t['R']:.6f}"])
    print("선택:", sel, "총", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
