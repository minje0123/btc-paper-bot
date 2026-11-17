# -*- coding: utf-8 -*-
"""
v4 30m 짝 비교 검정력 계산 (기록용 — PREREG v4 문서는 송민제가 따로 작성)

  python research/v4/power.py            # research/v4/pairs.csv(전진 시험, 봇이 매 실행 갱신) → 화면 + power_live.json
  python research/v4/power.py --prior    # 개발 구간 사전 추정 → research/v4/power_prior.json

짝: 같은 신호(signal_id = 코인-신호봉시각-방향)를 A = v4_30m_fixed(고정 최소 손절 0.40%),
    B = v4_30m_cost(PREREG v3 E3 비용 기준 손절, 왕복 비용 ÷ 손절 거리 ≤ 15%)에서 본다.
주 지표 d = R_B − R_A (신호마다, 진입 안 한 쪽 0R). 최소 효과 δ = +0.10R, 양측 α 0.05, 검정력 80%:
    n = ((z_0.975 + z_0.80) × σ_d / δ)²   (σ_d² = σ_A² + σ_B² − 2ρσ_Aσ_B, ρ 는 같은 신호의 A·B R 상관)

사전 추정(--prior): research/E2E8/trades_E2E8.csv 의 30m 기준 구성(gate=0, filt=-)과 E3 구성(gate=1, filt=-), 비용 ×1.0,
  '개발' 구간(진입 2026-03-17 이전, research/data_split.json) 거래만. 같은 신호 ID 로 짝지음.
  - 이 거래들은 E1/E2E8 연구 엔진(자본 1조 원, 수량 반올림 영향 없음, 1R 고정)으로 계산된 것 → 그림자 장부(자본 100만 원,
    1R 0.5%, 수량 0.0001 내림)와 R 이 아주 조금 다를 수 있음.
  - 국면 라벨(tools/regime.py 정의)은 18개월 BTC 5분봉(빗썸, 개발·선택 구간 데이터, 최종 시험 데이터 아님)으로 만든 일봉으로 붙임.
  - 참고로 선택 구간(2026-03-18 ~ 2026-11-16) 값도 함께 적는다(이미 E1~E8에서 본 데이터, 증거 아님).
최종 시험(holdout) 데이터는 읽지 않는다. 표준 라이브러리만.
"""
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
from tools import v4pairs as VP          # noqa: E402
from tools import regime as RG           # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
E2E8 = os.path.join(REPO, "research", "E2E8", "trades_E2E8.csv")
CFG_A = "30m|gate=0|core=0|filt=-"
CFG_B = "30m|gate=1|core=0|filt=-"
BAR = dt.timedelta(minutes=30)


def _t(s):
    return dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST)


def prior_rows(period, regime=None, path=E2E8, cost="1.0"):
    """E2E8 거래 → pairs 행 형식(dict, 문자열 값)"""
    by = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["cost_mult"] != cost or r["period"] != period or r["config"] not in (CFG_A, CFG_B):
                continue
            k = "A" if r["config"] == CFG_A else "B"
            et = _t(r["entry_time"])
            st = et - BAR
            side = "L" if int(r["side"]) == 1 else "S"
            sid = f"{r['coin']}-{st:%Y%m%d%H%M}{side}"
            row = by.setdefault(sid, dict(signal_id=sid, coin=r["coin"], signal_time=st.strftime("%Y-%m-%d %H:%M"),
                                          side=side, A_status="none", B_status="none", _st=st))
            row[f"{k}_status"] = "closed"
            row[f"{k}_R"] = r["R"]
            row[f"{k}_exit_reason"] = r["exit_type"]
    rows = sorted(by.values(), key=lambda x: (x["_st"], x["coin"]))
    for row in rows:
        a = float(row["A_R"]) if row["A_status"] == "closed" else 0.0
        b = float(row["B_R"]) if row["B_status"] == "closed" else 0.0
        row["d_sig"] = f"{b - a:.6f}"
        if row["A_status"] == "closed" and row["B_status"] == "closed":
            row["d_both"] = f"{b - a:.6f}"
        if regime is not None:
            lab = regime.label(row["_st"] + BAR)
            row["regime"] = lab["trend"] if lab else ""
            row["high_vol"] = "" if not lab or lab["high_vol"] is None else int(lab["high_vol"])
    return rows


def by_regime(rows):
    out = {}
    for g in ("상승", "하락", "횡보", ""):
        rs = [r for r in rows if r.get("regime", "") == g]
        ds = [float(r["d_sig"]) for r in rs]
        out[g or "라벨 없음"] = dict(n=len(rs), mean_d=VP._mean(ds), sd_d=VP._sd(ds))
    hv = [float(r["d_sig"]) for r in rows if str(r.get("high_vol")) == "1"]
    out["고변동(플래그)"] = dict(n=len(hv), mean_d=VP._mean(hv), sd_d=VP._sd(hv))
    return out


def btc_regime():
    """18개월 BTC 5분봉(개발·선택 구간 데이터) → 일봉 → Regime. 최종 시험 폴더는 열지 않음(holdout_guard 가 막음)."""
    try:
        sys.path.insert(0, os.path.join(REPO, "research"))
        from common import load_5m      # noqa: E402  (holdout_guard.check_path 포함)
        T, O, H, L, C, V = load_5m("BTC")
        return RG.Regime(RG.daily_from_5m(T, C))
    except Exception as e:
        print("국면 라벨 생략:", repr(e)[:200])
        return None


def rnd(d):
    return {k: (round(v, 5) if isinstance(v, float) else (rnd(v) if isinstance(v, dict) else v)) for k, v in d.items()}


def prior():
    reg = btc_regime()
    out = dict(
        made_at=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        source="research/E2E8/trades_E2E8.csv (E2~E8 실행 기록, 비용 ×1.0)",
        A=CFG_A + " (= v4_30m_fixed: 30m 실행, 최소 손절 0.40%)", B=CFG_B + " (= v4_30m_cost: E3 비용 기준 손절 15%)",
        delta_R=VP.DELTA, alpha_two_sided=VP.ALPHA, power=VP.POWER,
        formula="n = ((z_0.975 + z_0.80) × σ_d / δ)², d = R_B − R_A (신호마다, 진입 안 한 쪽 0R)",
        note="개발 구간 수치는 증거가 아니라 필요 표본 계획용. 연구 엔진(자본 1조 원)과 그림자 장부(100만 원, 수량 내림)의 R 은 조금 다를 수 있음.",
    )
    for per, nm in (("dev", "primary"), ("sel", "selection_period_reference")):
        rows = prior_rows(per, reg)
        ps = VP.power_stats(rows)
        ps["period"] = "개발 2025-05-18 ~ 2026-03-17" if per == "dev" else "선택 구간 2026-03-18 ~ 2026-11-16 (참고)"
        ps["by_regime"] = by_regime(rows) if reg else None
        ps["by_coin"] = {c: dict(n=len(x), mean_d=VP._mean(x), sd_d=VP._sd(x))
                         for c in ("BTC", "ETH", "XRP", "SOL")
                         for x in [[float(r["d_sig"]) for r in rows if r["coin"] == c]]}
        # 한 장부당 거래 수로 환산(필요 쌍 수 ÷ (신호 수 / 그 기간 A 거래 수)) — 전진 시험 기간 추정용
        days = (dt.date(2026, 3, 17) - dt.date(2025, 5, 18)).days + 1 if per == "dev" else \
            (dt.date(2026, 11, 16) - dt.date(2026, 3, 18)).days + 1
        ps["days"] = days
        ps["signals_per_day"] = len(rows) / days
        if ps["n_required"]:
            ps["days_needed_at_this_rate"] = round(ps["n_required"] / (len(rows) / days), 1)
        out[nm] = rnd(ps)
    p = os.path.join(HERE, "power_prior.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def live():
    p = os.path.join(HERE, "pairs.csv")
    if not os.path.exists(p):
        return dict(error="research/v4/pairs.csv 없음(봇이 v4 그림자 장부를 한 번 이상 돌린 뒤 생김)")
    ps = rnd(VP.power_stats(VP.read_pairs(p)))
    pr = os.path.join(HERE, "power_prior.json")
    if os.path.exists(pr):
        with open(pr, encoding="utf-8") as f:
            n0 = (json.load(f).get("primary") or {}).get("n_required")
        ps["n_required_prior"] = n0
        if n0:
            ps["progress_vs_prior"] = round(ps["n_signals"] / n0, 4)
    with open(os.path.join(HERE, "power_live.json"), "w", encoding="utf-8") as f:
        json.dump(ps, f, ensure_ascii=False, indent=2)
    return ps


if __name__ == "__main__":
    r = prior() if "--prior" in sys.argv else live()
    print(json.dumps(r, ensure_ascii=False, indent=1))
