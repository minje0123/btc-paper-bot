# -*- coding: utf-8 -*-
"""results.json → research/E1/report.md, research/experiments.csv (사전 등록서 v3 8장 열)"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
TFN = ["5m", "15m", "30m", "1H", "4H"]
COINS = ["BTC", "ETH", "XRP", "SOL"]


def f(x, d=3, sign=True):
    if x is None:
        return "-"
    return f"{x:+.{d}f}" if sign else f"{x:.{d}f}"


def ci(c):
    return "-" if not c else f"[{c[0]:+.3f}, {c[1]:+.3f}]"


def main():
    r = json.load(open(os.path.join(HERE, "results.json"), encoding="utf-8"))
    C = r["cells"]
    L = []
    L.append("> ⚠️ **개발·검증 구간 결과는 증거가 아니다.** (사전 등록서 v3 4장 선택 절차 3 — 선택 과정의 과최적화가 이 숫자에 그대로 남아 있음. "
             "성과로 인정하는 숫자는 최종 시험 A·B와 전진 시험뿐)\n")
    L.append("# E1 결과 — v1.4.1 규칙의 실행 시간봉 비교 (5m·15m·30m·1H·4H)\n")
    L.append(f"- 실행: {r['run_at_kst']} KST · 가상 매매 연구(실제 주문 없음) · 총 {r['configurations_run']}개 구성(시간봉) 실행")
    fx = r["fixed"]
    L.append(f"- 구간: 개발 {fx['split']['dev_start'][:16]} ~ {fx['split']['val_start'][:16]} 직전 / 검증 {fx['split']['val_start'][:16]} ~ "
             f"{fx['split']['val_end'][:16]} (거래는 진입 시각으로 구분, 진입 시작 {fx['trade_start'][:16]} = 데이터 시작 + {fx['warmup_days']}일)")
    L.append("- 자금 관리 끔: 1R 고정, 하루 한도·목표·첫 승 절반·출금 없음, 코인당 동시 포지션 1개. 지표 = 거래당 순수 R(수수료 편도 0.05% + 슬리피지 1틱 반영)")
    L.append("- 최대 손절폭(개발 구간 ATR% 중앙값 비율로 고정): " + ", ".join(
        f"{tf} {fx['max_stop_pct'][str(k)]:.2f}%" for tf, k in zip(TFN, (5, 15, 30, 60, 240))) + " · 최소 0.40%")
    L.append("- 상위봉: " + ", ".join(f"{k} → 방향 {v['direction_min']}분·보조 {v['aux_min']}분" for k, v in fx["htf"].items()))
    if r.get("open_positions_excluded"):
        L.append(f"- 데이터 끝에 열린 채 남아 제외한 포지션: {', '.join(r['open_positions_excluded'])}")
    L.append("")

    L.append("## 1. 4코인 합계\n")
    L.append("| 시간봉 | 구간 | 거래 수 | 평균 R | R 표준편차 | 승률 | 일 블록 95% CI | 주 블록 95% CI | 비용×1.5 평균 R | 최대 낙폭(R) | 최대 연속 손실 | t | p(단측) | Holm p |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tf in TFN:
        for per, pk in (("개발", "dev"), ("검증", "val")):
            a = C[f"{tf}|{pk}"]["ALL"]
            if not a["n"]:
                L.append(f"| {tf} | {per} | 0 | - | - | - | - | - | - | - | - | - | - | - |"); continue
            L.append(f"| {tf} | {per} | {a['n']} | {f(a['mean_R'])} | {f(a['sd_R'], 3, False)} | {a['win_rate']:.1f}% | "
                     f"{ci(a['ci95_day'])} | {ci(a['ci95_week'])} | {f(a['mean_R_cost15'])} | {a['mdd_R']:.1f} | "
                     f"{a['max_consec_loss']} | {f(a.get('t'), 2)} | {f(a.get('p_one_sided'), 4, False)} | {f(a.get('p_holm'), 4, False)} |")
    L.append("\np값은 거래 간·코인 간 독립을 가정한 t 검정이라 참고용(Holm 보정은 5개 시간봉을 한 묶음으로, 구간별로 따로).\n")

    L.append("## 2. 코인별\n")
    L.append("| 시간봉 | 구간 | " + " | ".join(f"{c} 거래·평균 R·일 CI" for c in COINS) + " | 플러스 코인 수 |")
    L.append("|---|---|" + "---|" * (len(COINS) + 1))
    for tf in TFN:
        for per, pk in (("개발", "dev"), ("검증", "val")):
            cells, pos = [], 0
            for c in COINS:
                a = C[f"{tf}|{pk}"][c]
                if not a["n"]:
                    cells.append("0"); continue
                pos += a["mean_R"] > 0
                cells.append(f"{a['n']} · {f(a['mean_R'])} · {ci(a['ci95_day'])}")
            L.append(f"| {tf} | {per} | " + " | ".join(cells) + f" | {pos}/4 |")
    L.append("\n코인별 상세(표준편차·승률·주 CI·비용×1.5·낙폭·연속 손실)는 results.json `cells.<시간봉>|<구간>.<코인>`.\n")

    L.append("## 3. 국면별 (4코인 합계: 거래 수 · 평균 R)\n")
    L.append("국면 = 진입 직전 완성 일봉 기준. 일봉 20SMA 5봉 기울기 ±0.5% 초과 = 상승/하락, 아니면 횡보, 일봉 ATR%가 코인별 개발 구간 80백분위 초과면 고변동(우선).\n")
    L.append("| 시간봉 | 구간 | 상승 | 하락 | 횡보 | 고변동 |")
    L.append("|---|---|---|---|---|---|")
    for tf in TFN:
        for per, pk in (("개발", "dev"), ("검증", "val")):
            g = C[f"{tf}|{pk}"]["ALL"].get("regimes", {})
            L.append(f"| {tf} | {per} | " + " | ".join(
                f"{g[k]['n']} · {f(g[k]['mean_R'])}" if g.get(k) and g[k]["n"] else "0" for k in ("상승", "하락", "횡보", "고변동")) + " |")
    L.append("")

    L.append("## 4. 검정력 (효과 +0.15R, 단측 5%, 검정력 80%)\n")
    L.append("n ≈ (1.645 + 0.84)² × σ² ÷ 0.15², σ = 개발 구간 E1 거래당 R 표준편차(4코인 합계)\n")
    L.append("| 시간봉 | σ(개발) | 필요 거래 수 | 검증 거래 수 |")
    L.append("|---|---|---|---|")
    for tf in TFN:
        p = r["power"][tf]
        L.append(f"| {tf} | {f(p['sigma_dev'], 3, False)} | {p['n_required']} | {C[f'{tf}|val']['ALL']['n']} |")
    L.append("")

    s = r["selection"]
    L.append("## 5. 선택 (사전 등록서 4장 절차)\n")
    L.append(f"- 규칙: {s['rule']}")
    L.append(f"- 표본 하한 통과: " + (", ".join(f"{nm} ({m:+.3f}R)" for nm, m in s["eligible"]) or "없음"))
    if s.get("excluded_small_n"):
        L.append(f"- 표본 하한 미달로 제외: {', '.join(s['excluded_small_n'])}")
    L.append(f"- **결과: {s['status']}**" + (f" → **{s['selected']}**" if s.get("selected") else "")
             + (f" (동률 후보: {', '.join(s['tied'])})" if s.get("tied") else ""))
    L.append("- 이 선택은 다음 단계(E2~)의 기준 구성을 정할 뿐, 우위의 증거가 아니다. 총 실행 구성 수는 experiments.csv 에 누적 기록.\n")

    L.append("## 6. 참고: 5장 채택 기준과의 비교(검증 구간, 판정 아님)\n")
    L.append("| 시간봉 | 평균 R ≥ +0.10 | n ≥ 150 | 일·주 CI 하단 > 0 | 비용×1.5 > 0 | 코인 3/4 플러스 |")
    L.append("|---|---|---|---|---|---|")
    for tf in TFN:
        a = C[f"{tf}|val"]["ALL"]
        if not a["n"]:
            continue
        pos = sum(1 for c in COINS if C[f"{tf}|val"][c]["n"] and C[f"{tf}|val"][c]["mean_R"] > 0)
        yn = lambda b: "예" if b else "아니오"   # noqa: E731
        L.append(f"| {tf} | {yn(a['mean_R'] >= 0.10)} | {yn(a['n'] >= 150)} | "
                 f"{yn(a['ci95_day'] and a['ci95_week'] and a['ci95_day'][0] > 0 and a['ci95_week'][0] > 0)} | "
                 f"{yn((a['mean_R_cost15'] or -1) > 0)} | {yn(pos >= 3)} ({pos}/4) |")
    L.append("")

    L.append("## 7. 구현 해석(사전 등록서에 수치가 없어 실행 전에 정한 것)\n")
    L.append("- ATR 비율: ATR(14)(Wilder)/종가 = ATR% 의 중앙값, 4코인 개발 구간 합산, 시간봉별 1개 값")
    L.append("- 국면의 일봉 ATR 도 ATR%(가격 수준 변화 영향 제거), 80백분위는 코인별 개발 구간에서")
    L.append("- 슬리피지 1틱 = 체결가의 원화 호가 단위(업비트 표). BTC·ETH 1,000원, SOL 50원(10만 원 미만 10원), XRP 1원")
    L.append("- 비용×1.5 = 수수료 0.075% + 슬리피지 1.5틱")
    L.append(f"- 처음 {fx['warmup_days']}일은 지표 준비(진입 안 함), 모든 시간봉 공통. 4H 의 보조(48시간봉) 20SMA 는 약 52일이 지나야 생기며 그 전엔 '중립'으로 처리됨(엔진 원래 동작)")
    L.append("- 상위봉 경계: KST 00:00 기준(빗썸 봉과 같음, A1 확인). 15m·1H 는 기존 엔진(UTC 기준)과 결과 동일(B 검증)")
    L.append("- 블록 부트스트랩: 거래가 있는 날/주를 블록으로 같은 개수 복원 추출, 2,000회, 고정 시드. 4코인 합계는 같은 날 4코인 거래를 한 블록으로")
    L.append("- 데이터: 빗썸(운영 봇은 업비트). 업비트 과거 5분봉은 이 노트북에서 API 차단으로 확보 못 함 → 거래소 차이는 별도 항목으로 남김")
    L.append("- 경과 시간 고정 비교(보조 자료)는 이번에 실행하지 않음\n")
    with open(os.path.join(HERE, "report.md"), "w", encoding="utf-8") as fo:
        fo.write("\n".join(L) + "\n")

    # experiments.csv (8장 열 그대로, 시간봉마다 한 줄)
    path = os.path.join(RESEARCH, "experiments.csv")
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fi:
            rows = [x for x in csv.reader(fi)][1:]
        rows = [x for x in rows if x and x[0] != "E1"]
    for tf in TFN:
        d, v = C[f"{tf}|dev"]["ALL"], C[f"{tf}|val"]["ALL"]
        cn = lambda cell: "/".join(str(C[cell][c]["n"]) for c in COINS)   # noqa: E731
        if s.get("selected") == tf:
            res_, why = "선택", "검증 평균 R 최고, 표본 하한 통과(증거 아님)"
        elif s.get("tied") and tf in s["tied"]:
            res_, why = "동률 보고", "검증 평균 R 1위와 0.03R 이내 — 송민제 결정 대기"
        elif v["n"] < 150:
            res_, why = "비선택", "검증 표본 150건 미만"
        else:
            res_, why = "비선택", "검증 평균 R 1위 아님"
        rows.append(["E1", r["run_at_kst"][:10], "실행 시간봉(v1.4.1 규칙, 봉 개수 고정, 방향 3k·보조 12k, 최대 손절 ATR 비례)", tf,
                     "개발 / 검증",
                     f"개발 {d['n']}({cn(f'{tf}|dev')}) / 검증 {v['n']}({cn(f'{tf}|val')})",
                     f"개발 {f(d.get('mean_R'))} / 검증 {f(v.get('mean_R'))}",
                     f"검증 일 {ci(v.get('ci95_day'))} 주 {ci(v.get('ci95_week'))}; 개발 일 {ci(d.get('ci95_day'))} 주 {ci(d.get('ci95_week'))}",
                     f"개발 {f(d.get('mean_R_cost15'))} / 검증 {f(v.get('mean_R_cost15'))}",
                     f"개발 {d.get('mdd_R', 0):.1f}R / 검증 {v.get('mdd_R', 0):.1f}R",
                     res_, why + " · 개발·검증 결과는 증거 아님"])
    with open(path, "w", newline="", encoding="utf-8-sig") as fo:
        w = csv.writer(fo)
        w.writerow(["번호", "실행일", "바꾼 것", "시간봉", "데이터 구간", "거래 수(합계/코인별)", "평균 R", "95% 신뢰구간",
                    "비용×1.5 평균 R", "최대 낙폭", "결과", "사유"])
        w.writerows(rows)
    print("report.md, experiments.csv 작성")


if __name__ == "__main__":
    main()
