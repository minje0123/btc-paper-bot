# -*- coding: utf-8 -*-
"""
A7. 최종 시험 B 코인 교체 (사전 등록서 v3 변경 기록 2026-11-17 09:00)

  python research/checks/a7_holdout_b_replace.py replace   # SHELL 대체 코인 선정 + 5분봉 받기(해시만)
  python research/checks/a7_holdout_b_replace.py shellgap  # SHELL 결측 원인 표본 조사(시각·개수만, 가격 안 봄)

규칙(결과를 보기 전 고정, 변경 기록 그대로)
  - 품질 기준: 5분봉 결측률(빈 5분 칸 ÷ 기대 칸) 10% 이하.
  - 대체 코인: selection.json 의 원래 순위(2026-11-17 08:09 거래대금 순)에서 SHELL 다음부터 차례로,
    원래 제외 조건(BTC·ETH·XRP·SOL, 스테이블코인, 18개월 시작 시점 미상장) + 결측률 10% 이하를 통과하는 첫 코인.
  - 후보 데이터는 a5_holdout_fetch.fetch() 로 받는다 → 받는 즉시 메모리에서 행 수·결측·SHA-256 만 계산(가격 안 봄).
  - 기준 미달 후보 파일은 사용 금지로 manifest 에 excluded 표시(파일은 지우지 않음, 해시로 추적).
표준 라이브러리만 사용.
"""
import datetime as dt
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import a5_holdout_fetch as A5  # noqa: E402

MAX_MISSING = 0.10
OUT = os.path.join(A5.HOLD, "B", "replacement.json")


def replace():
    with open(os.path.join(A5.HOLD, "B", "selection.json"), encoding="utf-8") as f:
        sel = json.load(f)
    log = []
    chosen = None
    for r in sel["top30"]:
        if r["rank"] <= 12:          # SHELL(12위)까지는 원래 선정 과정에서 이미 판정됨
            continue
        coin = r["market"].split("-")[1]
        row = dict(rank=r["rank"], market=r["market"])
        if coin in A5.USED:
            row["result"] = "제외: 18개월 연구 코인"
        elif coin in A5.STABLE:
            row["result"] = "제외: 스테이블코인"
        else:
            near = A5.first_bar_near(r["market"], A5.DATA_START + dt.timedelta(minutes=5))
            row["bar_before_start"] = near
            if near is None or near[:16] < "2025-05-18T12:00":
                row["result"] = "제외: 18개월 시작 시점 5분봉 없음(상장 전)"
            else:
                A5.fetch("B", coin)                          # 파일 저장 + _meta (해시·행 수만)
                with open(os.path.join(A5.HOLD, "B", f"_meta_{coin}.json"), encoding="utf-8") as f:
                    meta = json.load(f)
                rate = meta["missing_slots"] / meta["expected_5m_slots"]
                row.update(rows=meta["rows"], missing_slots=meta["missing_slots"], missing_rate=round(rate, 5),
                           sha256=meta["sha256"])
                if rate <= MAX_MISSING:
                    row["result"] = "선정(결측률 10% 이하)"
                    chosen = coin
                else:
                    row["result"] = f"제외: 결측률 {rate:.1%} > 10%"
                    meta["excluded"] = True
                    meta["excluded_reason"] = f"결측률 {rate:.2%} > 10% (변경 기록 2026-11-17 09:00 품질 기준). 사용 금지."
                    with open(os.path.join(A5.HOLD, "B", f"_meta_{coin}.json"), "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
        log.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if chosen:
            break
    out = dict(done_at_kst=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S"),
               rule="사전 등록서 v3 변경 기록 2026-11-17 09:00: B 후보 5분봉 결측률 10% 이하. SHELL(41%) 제외, "
                    "selection.json 원래 순위에서 같은 제외 조건 + 결측률 기준을 통과하는 첫 코인으로 대체.",
               original_check=dict(WLD=round(509 / 157536, 5), ENA=round(11010 / 157536, 5), SHELL=round(64896 / 157536, 5)),
               candidates=log, replacement=chosen)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("대체 코인:", chosen)


# ------------------------------------------------------------ SHELL 결측 원인
def _bars(unit, market, to, count=200):
    q = urllib.parse.urlencode({"market": market, "count": count, "to": to.strftime("%Y-%m-%dT%H:%M:%S")})
    d = A5.get(f"https://api.bithumb.com/v1/candles/minutes/{unit}?" + q) or []
    out = []
    for x in d:   # 시각과 거래량만 꺼낸다(가격은 쓰지 않음)
        t = dt.datetime.strptime(x["candle_date_time_kst"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=A5.KST)
        out.append((t, float(x["candle_acc_trade_volume"])))
    return sorted(out)


def shellgap():
    """표본 창마다 5분봉 200개를 다시 받아 빈 칸을 찾고, 그 칸을 1분봉 API 로 다시 확인.
    + 데이터 끝(2026-11-16) 근처는 체결 내역(/v1/trades/ticks)으로 빈 칸에 체결이 있었는지 확인.
    출력은 시각·개수·거래량 0 여부뿐(가격 안 봄)."""
    mk = "KRW-SHELL"
    s, e = A5.DATA_START, A5.DATA_END_EXCL
    span = (e - s) / 7
    windows = [s + span * k + dt.timedelta(days=3) for k in range(7)]   # 기간 전체에 고르게 7개 창(각 200×5분)
    res = []
    for w_end in windows:
        w_end = w_end.replace(minute=0, second=0, microsecond=0)
        b5 = _bars(5, mk, w_end)
        have = {t for t, _ in b5}
        start = w_end - dt.timedelta(minutes=5 * 200)
        slots = [start + dt.timedelta(minutes=5 * k) for k in range(200)]
        miss = [t for t in slots if t not in have]
        zero_vol = sum(1 for _, v in b5 if v == 0)
        # 빈 칸 표본 최대 8개를 1분봉으로 재확인: 그 5분 안에 1분봉이 있으면 '5분봉 수집 누락', 없으면 '체결 없음'
        checked = []
        for t in miss[:: max(1, len(miss) // 8)][:8]:
            b1 = _bars(1, mk, t + dt.timedelta(minutes=5), count=5)
            inside = [x for x in b1 if t <= x[0] < t + dt.timedelta(minutes=5)]
            checked.append(dict(slot=t.strftime("%Y-%m-%d %H:%M"), one_min_bars_inside=len(inside)))
        row = dict(window_end=w_end.strftime("%Y-%m-%d %H:%M"), slots=200, got_5m=len(have & set(slots)),
                   missing=len(miss), zero_volume_bars=zero_vol, one_min_recheck=checked)
        res.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    # 최근 체결 내역(가능한 범위) — 빈 5분 칸에 체결이 실제로 있었는지
    ticks_check = None
    try:
        allt = []
        cursor = None
        for _ in range(40):
            q = {"market": mk, "count": 500}
            if cursor:
                q["cursor"] = cursor
            d = A5.get("https://api.bithumb.com/v1/trades/ticks?" + urllib.parse.urlencode(q)) or []
            if not d:
                break
            for x in d:
                t = dt.datetime.strptime(f"{x['trade_date_utc']}T{x['trade_time_utc']}", "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=dt.timezone.utc).astimezone(A5.KST)
                allt.append(t)
            cursor = d[-1].get("sequential_id")
            if min(allt) < dt.datetime(2026, 11, 16, 0, 0, tzinfo=A5.KST):
                break
        if allt:
            lo, hi = min(allt), max(allt)
            # 체결 내역이 덮는 구간 중 18개월 범위(13:00 봉까지) 안의 5분 칸
            a = max(lo, A5.DATA_START); b = min(hi, A5.DATA_END_EXCL)
            slot_set = set()
            for t in allt:
                if a <= t < b:
                    slot_set.add(t.replace(minute=t.minute - t.minute % 5, second=0))
            b5 = []
            to = b
            while to > a:
                part = _bars(5, mk, to)
                if not part:
                    break
                b5 += part
                to = part[0][0]
            have = {t for t, _ in b5 if a <= t < b}
            a5 = a.replace(minute=a.minute - a.minute % 5, second=0, microsecond=0) + dt.timedelta(minutes=5)
            slots = []
            t = a5
            while t + dt.timedelta(minutes=5) <= b:
                slots.append(t); t += dt.timedelta(minutes=5)
            miss = [t for t in slots if t not in have]
            ticks_check = dict(ticks=len(allt), ticks_range_kst=[lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")],
                               full_slots_in_range=len(slots), slots_with_ticks=len([s_ for s_ in slots if s_ in slot_set]),
                               missing_5m=len(miss), missing_5m_with_ticks=len([s_ for s_ in miss if s_ in slot_set]),
                               present_5m_without_ticks=len([s_ for s_ in slots if s_ in have and s_ not in slot_set]))
    except Exception as ex:   # 체결 API 가 과거를 주지 않으면 기록만
        ticks_check = dict(error=repr(ex))
    print(json.dumps(dict(ticks_check=ticks_check), ensure_ascii=False))
    out = dict(done_at_kst=dt.datetime.now(A5.KST).strftime("%Y-%m-%d %H:%M:%S"), market=mk,
               method="18개월 범위 7개 창(각 5분봉 200칸)을 API 로 다시 받아 빈 칸을 세고, 빈 칸 표본을 1분봉 API 로 재확인. "
                      "데이터 끝(2026-11-16) 근처는 체결 내역과 5분봉 빈 칸을 대조. 시각·개수만 보고 가격은 보지 않음.",
               windows=res, ticks_check=ticks_check)
    with open(os.path.join(A5.HOLD, "B", "shell_gap_check.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def update_manifest():
    """holdout_manifest.json 갱신: SHELL excluded, 기준 미달 후보 excluded, 대체 코인 추가. 기존 항목 값은 그대로."""
    mp = os.path.join(A5.RESEARCH, "holdout_manifest.json")
    with open(mp, encoding="utf-8") as f:
        man = json.load(f)
    with open(OUT, encoding="utf-8") as f:
        rep = json.load(f)
    gap = None
    gp = os.path.join(A5.HOLD, "B", "shell_gap_check.json")
    if os.path.exists(gp):
        with open(gp, encoding="utf-8") as f:
            gap = json.load(f)
    now = dt.datetime.now(A5.KST).strftime("%Y-%m-%d %H:%M:%S")
    have = {x["file"] for x in man["files"]}
    for x in man["files"]:
        if x["coin"] == "SHELL" and x["set"] == "B":
            x["excluded"] = True
            x["excluded_at_kst"] = now
            x["excluded_reason"] = ("사전 등록서 v3 변경 기록 2026-11-17 09:00: 최종 시험 B 품질 기준(5분봉 결측률 10% 이하) 미달 — "
                                    "결측률 41.19%(64,896/157,536). 사용 금지. 결측 원인 조사(holdout/B/shell_gap_check.json): "
                                    "표본 빈 칸 56개 모두 1분봉도 없음 → 수집 누락이 아니라 체결 부재로 판단.")
    for c in rep["candidates"]:
        if "sha256" not in c:
            continue
        coin = c["market"].split("-")[1]
        with open(os.path.join(A5.HOLD, "B", f"_meta_{coin}.json"), encoding="utf-8") as f:
            meta = json.load(f)
        meta["missing_rate"] = round(meta["missing_slots"] / meta["expected_5m_slots"], 5)
        if coin != rep["replacement"]:
            meta["excluded"] = True
        else:
            meta["excluded"] = False
            meta["note"] = "SHELL 대체(변경 기록 2026-11-17 09:00). selection.json 원래 순위 18위."
        if meta["file"] not in have:
            man["files"].append(meta)
    sel = man["sets"]["B"]["selection"]
    sel["selected_original"] = sel.get("selected_original", list(sel["selected"]))
    sel["selected"] = [c for c in sel["selected_original"] if c != "SHELL"] + [rep["replacement"]]
    sel["quality_rule"] = "5분봉 결측률 10% 이하 (변경 기록 2026-11-17 09:00, B 후보 전체에 같은 기준)"
    sel["replacement_file"] = "holdout/B/replacement.json"
    with open(OUT, "rb") as f:
        import hashlib
        sel["replacement_sha256"] = hashlib.sha256(f.read()).hexdigest()
    man["sets"]["B"]["desc"] = "미사용 거래대금 상위 3코인(품질 기준 통과), 18개월 데이터와 같은 기간: WLD·ENA·" + rep["replacement"]
    man["updated_at_kst"] = now
    man["update_note"] = ("2026-11-17: SHELL 제외(결측률 41%), REQ 후보 제외(결측률 77%), " + rep["replacement"] +
                          " 추가. 기존 파일 해시는 바꾸지 않음. excluded=true 파일은 최종 시험에 쓰지 않는다.")
    if gap:
        man["sets"]["B"]["shell_gap_check"] = "holdout/B/shell_gap_check.json"
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    print("manifest 갱신:", sel["selected"])


if __name__ == "__main__":
    {"replace": replace, "shellgap": shellgap, "manifest": update_manifest}[sys.argv[1]]()
