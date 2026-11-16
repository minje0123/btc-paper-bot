# -*- coding: utf-8 -*-
"""
A3. 비용 검증 — 실제 거래 3건을 봉 데이터에서 독립적으로 손계산(수수료 편도 0.05% ×2 + 슬리피지 1틱 1,000원)해 엔진 손익·R 과 대조.
    거래: 운영 장부(메모리 재계산)의 청산 거래 2건 + 18개월 BTC 5분봉(E1 설정)에서 '본절' 또는 '돌파 실패' 같은 다른 청산 유형 1건.
    손계산 규칙: 진입가 = 진입 봉 시가 ± 1틱 / 손절 = min(시가, 손절가) − 1틱(롱) / T1·다음 스윙 = 지정가(슬리피지 없음, 갭이면 시가) /
                 시장가 청산(다음 봉 시가) = 시가 ∓ 1틱 / 손익 = Σ[방향×(청산가−진입가)×수량 − 청산가×수량×0.05% − 진입가×수량×0.05%]
                 R = 손익 ÷ (최초 수량 × |진입가 − 최초 손절|)
A4. 같은 봉에서 손절·목표 동시 도달 → 손절 우선. 코드 위치 + 18개월 4코인 5분봉 E1 실행에서 실제 사례 전수 확인.
결과: research/checks/a3_a4_result.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import COINS, REPO, engine, load_5m, make_data  # noqa: E402
from e1_params import e1_params  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a3_a4_result.json")
FEE, SLIP = 0.0005, 1000.0


def hand(t, idx, O, H, L):
    """봉 데이터만으로 거래 손익 손계산"""
    s = t["side"]
    eb = idx[t["entry_time"]]
    entry = O[eb] + s * SLIP
    lines = [f"진입가 = 진입 봉 시가 {O[eb]:,.0f} {'+' if s == 1 else '−'} 1틱 1,000 = {entry:,.0f}"]
    total = 0.0
    stop = t["stop0"]
    for f in t["fills"]:
        b = idx[f["time"]]
        r = f["reason"]
        if r in ("손절", "본절"):
            sp = stop if r == "손절" else entry
            px = (min(O[b], sp) - SLIP) if s == 1 else (max(O[b], sp) + SLIP)
            how = f"{r}: {'min' if s == 1 else 'max'}(시가 {O[b]:,.0f}, 손절가 {sp:,.2f}) {'−' if s == 1 else '+'} 1,000"
        elif r in ("T1 반익절", "다음 스윙", "1.5R 익절"):
            tg = f["price"] if r != "T1 반익절" else t["t1"]
            if r == "T1 반익절":
                px = max(O[b], t["t1"]) if s == 1 else min(O[b], t["t1"])
            else:
                px = f["price"]        # 다음 스윙 목표는 봉마다 바뀌므로 엔진 기록 목표가를 쓰되, 봉 범위 안인지 확인
                assert L[b] <= px <= H[b] or px == O[b], "다음 스윙 체결가가 봉 범위 밖"
            how = f"{r}: 지정가 {tg:,.2f} (갭이면 시가), 슬리피지 없음"
        else:                        # 시장가(다음 봉 시가) 청산
            px = O[b] - s * SLIP
            how = f"{r}: 시가 {O[b]:,.0f} {'−' if s == 1 else '+'} 1,000"
        q = f["qty"]
        gross = s * (px - entry) * q
        fee = px * q * FEE + entry * q * FEE
        pnl = gross - fee
        total += pnl
        lines.append(f"{how} = {px:,.2f}; 수량 {q} → 가격손익 {gross:,.2f} − 수수료({px:,.0f}×{q}×0.05% + {entry:,.0f}×{q}×0.05%"
                     f" = {fee:,.2f}) = {pnl:,.2f}")
    R = total / (t["qty"] * abs(entry - stop))
    lines.append(f"합계 손익 {total:,.2f}원, 1R = {t['qty']} × |{entry:,.0f} − {stop:,.2f}| = {t['qty'] * abs(entry - stop):,.2f}원 → R = {R:.6f}")
    return entry, total, R, lines


def a3():
    out = []
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
    b = engine.load_csv(os.path.join(REPO, "data", "candles_5m.csv"))
    R = engine.run(engine.Data(*b), engine.load_params(cfg), start_time=engine.parse_kst(cfg["start_time"]))
    picks = [("운영 장부", t, b) for t in R["trades"][:2]]
    b5 = load_5m("BTC")
    R2 = engine.run(make_data(b5, 5), e1_params())
    other = next(t for t in R2["trades"] if t["exit_reason"] in ("본절", "돌파 실패", "반대 장악형", "거래량 급증")
                 and t["t1Hit"])
    picks.append(("18개월 BTC 5m (E1 설정, 비용 동일)", other, b5))
    for src, t, bars in picks:
        T, O, H, L = bars[0], bars[1], bars[2], bars[3]
        idx = {x: i for i, x in enumerate(T)}
        entry, pnl, r, lines = hand(t, idx, O, H, L)
        ok = abs(entry - t["entry"]) < 1e-6 and abs(pnl - t["pnl"]) < 1e-6 and abs(r - t["R"]) < 1e-9
        out.append(dict(source=src, id=t["id"], side="롱" if t["side"] == 1 else "숏", setup=t["tag"] + t["grade"],
                        exits=t["exits"], engine_entry=t["entry"], hand_entry=entry, engine_pnl=t["pnl"], hand_pnl=pnl,
                        engine_R=t["R"], hand_R=r, match=ok, calc=lines))
        print(src, t["id"], "엔진", round(t["pnl"], 4), round(t["R"], 6), "손계산", round(pnl, 4), round(r, 6), "일치" if ok else "불일치")
    return out


def a4():
    """전 4코인 5분봉 E1 실행: 손절·목표가 같은 봉에 닿은 모든 경우 확인"""
    both_stop, both_target, examples = 0, 0, []
    for coin in COINS:
        b5 = load_5m(coin)
        T, O, H, L = b5[0], b5[1], b5[2], b5[3]
        idx = {x: i for i, x in enumerate(T)}
        R = engine.run(make_data(b5, 5), e1_params())
        for t in R["trades"]:
            s = t["side"]
            for f in t["fills"]:
                b = idx[f["time"]]
                if f["reason"] == "손절" and not any(g["reason"] == "T1 반익절" for g in t["fills"]):
                    tgt_touched = (H[b] >= t["t1"]) if s == 1 else (L[b] <= t["t1"])
                    if tgt_touched:
                        both_stop += 1
                        if len(examples) < 3:
                            examples.append(dict(coin=coin, id=t["id"], bar=str(T[b]), O=O[b], H=H[b], L=L[b],
                                                 stop=t["stop0"], t1=t["t1"], recorded=f["reason"], fill=f["price"]))
                if f["reason"] == "T1 반익절":
                    stop_touched = (L[b] <= t["stop0"]) if s == 1 else (H[b] >= t["stop0"])
                    if stop_touched:
                        both_target += 1
    print("A4: 같은 봉 동시 도달 → 손절 처리", both_stop, "건 / 목표 처리(위반)", both_target, "건")
    return dict(code="engine.py run(): stop_hit 을 먼저 검사하고 'if stop_hit: … elif lim_hit:' 로 목표는 손절이 안 닿았을 때만 처리",
                same_bar_both_recorded_as_stop=both_stop, same_bar_both_recorded_as_target=both_target,
                examples=examples, pass_=both_target == 0 and both_stop > 0)


if __name__ == "__main__":
    r3 = a3()
    r4 = a4()
    res = dict(method=__doc__.strip(), A3=r3, A3_pass=all(x["match"] for x in r3), A4=r4, A4_pass=r4["pass_"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("A3", "PASS" if res["A3_pass"] else "FAIL", "/ A4", "PASS" if res["A4_pass"] else "FAIL")
