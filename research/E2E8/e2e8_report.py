# -*- coding: utf-8 -*-
"""E2~E8 보고서(report.md)와 실험 기록부(research/experiments.csv) 작성 — results.json 만 읽는다.
experiments.csv 는 E1 줄을 그대로 두고(새 열은 빈 칸) E2~E8 줄을 다시 쓴다(여러 번 돌려도 중복 없음)."""
import csv
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
COINS = ["BTC", "ETH", "XRP", "SOL"]
REG = ["상승", "하락", "횡보", "고변동"]
PER_KO = dict(dev="개발", sel="선택")

with open(os.path.join(HERE, "results.json"), encoding="utf-8") as f:
    RES = json.load(f)
with open(os.path.join(RESEARCH, "E1", "results.json"), encoding="utf-8") as f:
    E1R = json.load(f)
CAND = RES["candidates"]


def f3(x, sign=True):
    return "—" if x is None else (f"{x:+.3f}" if sign else f"{x:.3f}")


def ci(x):
    return "—" if not x else f"[{x[0]:+.3f}, {x[1]:+.3f}]"


def A(key, per):
    return CAND[key]["metrics"][per]["ALL"]


def coin_pos(key, per):
    return sum(1 for c in COINS if (CAND[key]["metrics"][per][c].get("mean_R") or 0) > 0)


def coins_str(key, per):
    m = CAND[key]["metrics"][per]
    return " / ".join(f"{c} {m[c]['n']}·{f3(m[c].get('mean_R'))}" for c in COINS)


def reg_str(key, per):
    r = A(key, per).get("regimes", {})
    return " / ".join(f"{g} {r[g]['n']}·{f3(r[g]['mean_R'])}" for g in REG if g in r)


def exit_str(key, per, top=6):
    e = A(key, per).get("exit_types", {})
    return " / ".join(f"{k} {v['share'] * 100:.0f}%·{f3(v['mean_R'])}" for k, v in list(e.items())[:top])


STAGE_NO = {"E2": "E2", "E3": "E3", "E4": "E4", "E5~E8": "E5~E8"}


def stage_label(stage, key, prev_key):
    if key == prev_key:
        return f"{stage} (바꾸지 않음)"
    if stage != "E5~E8":
        return stage
    filt = key.split("filt=")[1]
    return "E5~E8 조합 " + filt if "+" in filt else filt


def criteria(key):
    a = A(key, "sel")
    base = E1R["cells"]["5m|val"]["ALL"]
    sig_e1 = E1R["power"]["30m"]["sigma_dev"]
    n_req_e1 = math.ceil((1.645 + 0.84) ** 2 * sig_e1 ** 2 / 0.15 ** 2)
    sd_dev = A(key, "dev").get("sd_R")
    n_req_own = math.ceil((1.645 + 0.84) ** 2 * sd_dev ** 2 / 0.15 ** 2) if sd_dev else None
    rows = []
    m = a.get("mean_R")
    rows.append(("거래당 순평균 R ≥ +0.10R", f"{f3(m)}R", m is not None and m >= 0.10))
    rows.append(("표본: 150건 이상 + 검정력 필요 수", f"{a['n']}건 / 필요 {n_req_e1}건(σ E1 30m 개발 {sig_e1:.3f}); "
                 f"참고: 이 구성 개발 σ {sd_dev:.3f} → {n_req_own}건", a["n"] >= 150 and a["n"] >= n_req_e1))
    cd, cw = a.get("ci95_day"), a.get("ci95_week")
    rows.append(("일·주 블록 부트스트랩 95% CI 하단 > 0", f"일 {ci(cd)} · 주 {ci(cw)}", bool(cd and cw and cd[0] > 0 and cw[0] > 0)))
    c15 = a.get("mean_R_cost15")
    rows.append(("비용 ×1.5에서도 평균 R 플러스", f"{f3(c15)}R", c15 is not None and c15 > 0))
    cp = coin_pos(key, "sel")
    rows.append(("코인 4개 중 3개 이상 플러스", f"{cp}/4 ({coins_str(key, 'sel')})", cp >= 3))
    r = a.get("regimes", {})
    big = {g: r[g]["n"] * r[g]["mean_R"] for g in REG if g in r and r[g]["n"] >= 30}
    small = [g for g in REG if g in r and r[g]["n"] < 30]
    pos = {g: s for g, s in big.items() if s > 0}
    if not pos:
        reg_ok, reg_txt = False, "표본 30건 이상 국면 중 플러스 국면 없음(성과 자체가 없음) → 충족 못 함으로 셈"
    else:
        share = max(pos.values()) / sum(pos.values())
        reg_ok = share < 0.8
        reg_txt = f"플러스 기여 최대 국면 비중 {share * 100:.0f}% (80% 이상이면 몰림)"
    reg_txt += f"; 결론 없음(30건 미만): {', '.join(small) or '없음'} · {reg_str(key, 'sel')}"
    rows.append(("국면: 한 국면에만 몰리지 않음", reg_txt, reg_ok))
    mdd_lim = 1.2 * base["mdd_R"]
    cl_lim = base["max_consec_loss"] + 2
    rows.append(("위험: MDD(R) ≤ 기준선(v1.4.1 5m)×1.2, 최대 연속 손실 ≤ 기준선+2",
                 f"MDD {a['mdd_R']:.1f}R (한도 {mdd_lim:.1f}R) · 연속 손실 {a['max_consec_loss']} (한도 {cl_lim})",
                 a["mdd_R"] <= mdd_lim and a["max_consec_loss"] <= cl_lim))
    rows.append(("다중 검정: E1~E8 전체를 하나의 탐색으로", f"E1 5개 + E2~E8 {RES['n_distinct_configs'] - 1}개 새 구성 "
                 f"= 총 {5 + RES['n_distinct_configs'] - 1}개 구성 중 1개 선택. 판정은 최종 시험·전진 시험만", None))
    return rows


def write_report():
    L = []
    L.append("> ⚠️ **개발·선택 구간 결과는 증거가 아니다.** (사전 등록서 v3 4장 선택 절차 3 — 선택 과정의 과최적화가 이 숫자에 그대로 남아 있음. "
             "성과로 인정하는 숫자는 최종 시험 A·B와 전진 시험뿐. '선택 구간' = 변경 기록 2026-11-17 09:00의 새 이름, 예전 '검증 구간')")
    L.append("")
    L.append("# E2~E8 결과 — 30m 출발 구성에서 순차 탐색")
    L.append("")
    L.append(f"- 실행: {RES['run_at_kst']} KST · 가상 매매 연구(실제 주문 없음) · 결과 보기 전 결정: `research/E2E8/decisions.md`")
    L.append("- 구간: 개발 2025-06-01 13:05(데이터 시작+14일) ~ 2026-03-18 00:00 직전 / 선택 2026-03-18 00:00 ~ 2026-11-16 13:00 (진입 시각 기준)")
    L.append("- 4코인(BTC·ETH·XRP·SOL) 빗썸 5분봉. 자금 관리 끔, 1R 고정, 코인당 동시 포지션 1개, 수수료 편도 0.05% + 슬리피지 1틱(코인별 호가). 비용×1.5 = 0.075% + 1.5틱")
    L.append("- 연구 엔진 `engine_research.py`(운영 engine.py 사본 + 훅)는 기본 설정에서 E1 30m 거래 5,882건(비용 1.0·1.5)을 ID·R·청산 사유까지 그대로 재현함(`repro_check.json`)")
    th = RES["fixed"]["thresholds"]
    L.append(f"- 고정값: 최대 손절 30m {RES['fixed']['max_stop_pct']['30m']:.3f}% · E2 {RES['fixed']['max_stop_pct']['E2']:.3f}%(1H 값), 최소 0.40%(E3 채택 시 비용 비율로 대체)")
    L.append(f"- E6 기준(개발 구간 실행봉 |종가−20SMA|/ATR 80백분위): 30m {th['30m']['chase_atr_p80']:.3f} · 15m(E2) {th['E2']['chase_atr_p80']:.3f}")
    L.append("- E7 기준(코인별 개발 구간 실행봉 ATR% 80백분위): 30m " +
             ", ".join(f"{c} {th['30m']['atr_pct_p80'][c]:.3f}%" for c in COINS) + " · 15m " +
             ", ".join(f"{c} {th['E2']['atr_pct_p80'][c]:.3f}%" for c in COINS))
    L.append("")
    L.append("## 1. 단계별 후보와 선택 (4코인 합계)")
    L.append("")
    L.append("채택 규칙(사전 등록서 4장 1항): 선택 구간 순평균 R 최고 + 선택 구간 거래 수 ≥ 150, 최고와 0.03R 이내면 더 단순한 안('바꾸지 않음' 포함).")
    L.append("")
    prev = "30m|gate=0|core=0|filt=-"
    for s in RES["stages"]:
        L.append(f"### {s['stage']}")
        L.append("")
        L.append("| 후보 | 개발 n · 평균 R | 선택 n · 평균 R | 선택 σ | 선택 승률 | 선택 일 CI | 선택 주 CI | 선택 비용×1.5 | 선택 MDD(R) · 연속 손실 | 선택 플러스 코인 | 손절폭 중앙값(선택) |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for k in s["candidates"]:
            d, v = A(k, "dev"), A(k, "sel")
            mark = " **← 선택**" if k == s["chosen"] else ""
            L.append(f"| {CAND[k]['desc']}{mark} | {d['n']} · {f3(d.get('mean_R'))} | {v['n']} · {f3(v.get('mean_R'))} | "
                     f"{f3(v.get('sd_R'), False)} | {v.get('win_rate', 0):.1f}% | {ci(v.get('ci95_day'))} | {ci(v.get('ci95_week'))} | "
                     f"{f3(v.get('mean_R_cost15'))} | {v.get('mdd_R', 0):.1f} · {v.get('max_consec_loss')} | {coin_pos(k, 'sel')}/4 | "
                     f"{v.get('stop_pct_median', 0):.2f}% |")
        L.append("")
        L.append(f"- 결과: **{CAND[s['chosen']]['desc']}** — {s['status']}"
                 + (f" (최고: {CAND[s['best']]['desc']} {f3(s['best_mean'])}R, 0.03R 이내: "
                    + ", ".join(CAND[k]['desc'] for k in s['within_tie']) + ")" if s.get("best") else ""))
        if s.get("excluded_small_n"):
            L.append("- 표본 하한(150) 미달로 제외: " + ", ".join(CAND[k]["desc"] for k in s["excluded_small_n"]))
        if s.get("combo"):
            cb = s["combo"]
            L.append(f"- 조합 규칙: 직전 구성보다 0.03R 넘게 높고 n≥150인 단일 필터 = "
                     f"{', '.join(CAND[k]['desc'] for k in cb['better_than_prev']) or '없음'} → "
                     + (f"상위 2개 조합 계산: {CAND[cb['combo']]['desc']}" if cb["combo"] else "2개 미만이라 조합 계산 안 함"))
        L.append("")
        prev = s["chosen"]
    fin = RES["final"]
    L.append("## 2. 후보별 상세 (선택 구간, 개발 구간)")
    L.append("")
    for k in CAND:
        L.append(f"**{CAND[k]['desc']}** (`{k}`)")
        for per in ("dev", "sel"):
            a = A(k, per)
            if not a["n"]:
                L.append(f"- {PER_KO[per]}: 거래 없음"); continue
            L.append(f"- {PER_KO[per]}: 코인별(n·평균 R) {coins_str(k, per)}")
            L.append(f"  - 국면별 {reg_str(k, per)}")
            L.append(f"  - 청산 유형(비율·평균 R) {exit_str(k, per)}")
            L.append(f"  - 셋업 " + " / ".join(f"{s} {v['n']}·{f3(v['mean_R'])}" for s, v in sorted(a['setups'].items())))
        if CAND[k]["open_end_excluded"]:
            L.append(f"- 데이터 끝 미청산 제외: {', '.join(CAND[k]['open_end_excluded'])}")
        L.append("")
    L.append("## 3. 최종 후보와 5장 채택 기준 (선택 구간 값, 판정 아님)")
    L.append("")
    L.append(f"최종 후보: **{CAND[fin]['desc']}** (`{fin}`)")
    L.append("")
    L.append("| 5장 항목 | 선택 구간 값 | 충족 |")
    L.append("|---|---|---|")
    rows = criteria(fin)
    for nm, val, ok in rows:
        L.append(f"| {nm} | {val} | {'예' if ok else ('아니오' if ok is False else '기록')} |")
    allok = all(ok for _, _, ok in rows if ok is not None)
    L.append("")
    L.append("- 결론: " + ("**5장 기준을 선택 구간에서 모두 충족** — 단, 최종 시험 실행은 송민제 결정 사항이라 실행하지 않음."
                          if allok else "**최종 시험으로 보내지 않음(기준 미달)**."))
    L.append("- 기준선(위험 비교): v1.4.1 5m, E1 검증(=선택) 구간 MDD "
             f"{E1R['cells']['5m|val']['ALL']['mdd_R']:.1f}R, 최대 연속 손실 {E1R['cells']['5m|val']['ALL']['max_consec_loss']}")
    L.append("")
    L.append("## 4. 후보 수")
    L.append("")
    L.append(f"- E2~E8 후보 줄 수(단계별 '바꾸지 않음' 포함): {RES['n_candidate_rows']}개 · 서로 다른 구성: {RES['n_distinct_configs']}개"
             f"(그중 1개는 E1 30m과 같은 구성)")
    L.append(f"- E1~E8 전체: E1 5개 + E2~E8 새 구성 {RES['n_distinct_configs'] - 1}개 = **총 {5 + RES['n_distinct_configs'] - 1}개 구성 중 1개 선택**")
    L.append("")
    L.append("## 5. 참고")
    L.append("")
    L.append("- 모든 후보의 거래: `trades_E2E8.csv`(config 열 = 후보 ID), 모든 지표: `results.json`, 실험 기록부: `research/experiments.csv`")
    L.append("- 결과 보기 전 결정(정의·기준값·선택 절차): `decisions.md`. 결과를 본 뒤 바꾼 것은 없음(사후 기록 칸 참고).")
    L.append("- 최종 시험(holdout) 데이터는 읽지 않음(FINAL=1 미사용). 최종 시험 B는 WLD·ENA·NEAR(SHELL 제외, `research/holdout_manifest.json`).")
    with open(os.path.join(HERE, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return rows, allok


def write_experiments():
    p = os.path.join(RESEARCH, "experiments.csv")
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    head = rows[0][:12]
    extra = ["후보 ID", "단계", "σ(개발/선택)", "승률(개발/선택)", "최대 연속 손실(개발/선택)", "코인별 n·평균 R",
             "국면별 n·평균 R", "청산 유형 비율·평균 R", "손절폭 중앙값 %(개발/선택)", "선택 구간 n≥150"]
    keep = [r + [""] * (len(head) + len(extra) - len(r)) for r in rows[1:] if r and r[0] == "E1"]
    new = []
    run_date = RES["run_at_kst"][:10]
    prev = "30m|gate=0|core=0|filt=-"
    for s in RES["stages"]:
        for k in s["candidates"]:
            d, v = A(k, "dev"), A(k, "sel")
            tf = "15m 실행(1H 구간·4H 방향)" if k.startswith("E2") else "30m"
            res_ = "선택" if k == s["chosen"] else "비선택"
            if k == s["chosen"]:
                why = s["status"]
            elif v["n"] < 150:
                why = "선택 구간 표본 150건 미달"
            else:
                why = "선택 구간 평균 R 1위 아님 또는 0.03R 이내에서 더 단순한 안이 있음"
            nd = "/".join(str(CAND[k]["metrics"]["dev"][c]["n"]) for c in COINS)
            ns = "/".join(str(CAND[k]["metrics"]["sel"][c]["n"]) for c in COINS)
            new.append([
                stage_label(s["stage"], k, prev).split(" ")[0] if not k == prev else s["stage"],
                run_date, CAND[k]["desc"] + (" (바꾸지 않음)" if k == prev else ""), tf, "개발 / 선택",
                f"개발 {d['n']}({nd}) / 선택 {v['n']}({ns})",
                f"개발 {f3(d.get('mean_R'))} / 선택 {f3(v.get('mean_R'))}",
                f"선택 일 {ci(v.get('ci95_day'))} 주 {ci(v.get('ci95_week'))}; 개발 일 {ci(d.get('ci95_day'))} 주 {ci(d.get('ci95_week'))}",
                f"개발 {f3(d.get('mean_R_cost15'))} / 선택 {f3(v.get('mean_R_cost15'))}",
                f"개발 {d.get('mdd_R', 0):.1f}R / 선택 {v.get('mdd_R', 0):.1f}R",
                res_, why + " · 개발·선택 결과는 증거 아님",
                k, s["stage"],
                f"{f3(d.get('sd_R'), False)} / {f3(v.get('sd_R'), False)}",
                f"{d.get('win_rate', 0):.1f}% / {v.get('win_rate', 0):.1f}%",
                f"{d.get('max_consec_loss')} / {v.get('max_consec_loss')}",
                f"개발 {coins_str(k, 'dev')} | 선택 {coins_str(k, 'sel')}",
                f"개발 {reg_str(k, 'dev')} | 선택 {reg_str(k, 'sel')}",
                f"개발 {exit_str(k, 'dev', 8)} | 선택 {exit_str(k, 'sel', 8)}",
                f"{d.get('stop_pct_median', 0):.3f} / {v.get('stop_pct_median', 0):.3f}",
                "예" if v["n"] >= 150 else "아니오"])
        prev = s["chosen"]
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head + extra)
        w.writerows(keep + new)
    return len(new)


if __name__ == "__main__":
    rows, ok = write_report()
    n = write_experiments()
    print("report.md 작성, experiments.csv E2~E8", n, "줄, 기준 모두 충족:", ok)
