# -*- coding: utf-8 -*-
"""
(A) 자동 복기 — 청산된 거래마다 숫자 복기 + 규칙 기반 원인 태그 + (본 장부만) Gemini 해설 캐시 + 하루 복기 리포트.

매매 판단에는 전혀 쓰지 않는다(장부 계산이 끝난 뒤 기록용으로만 계산). 표준 라이브러리만.

숫자 복기 (그 코인 5분봉, 진입 봉 ~ 청산 봉 포함)
  보유봉수 = 청산 봉 − 진입 봉 + 1
  MFE_R   = 유리한 쪽 최대 이동 ÷ 최초 위험폭(|진입가 − 최초 손절|)  (≥ 0)
  MAE_R   = 불리한 쪽 최대 이동 ÷ 최초 위험폭, 음수로 표시          (≤ 0)
  * 청산 봉은 봉 전체의 고가·저가를 쓴다(봉 안 체결 순서를 모르므로 근사).
"""
import bisect
import datetime as dt
import json
import os
import re
from collections import defaultdict

# ---------------- 원인 태그 기준값 (결과를 보기 전에 정한 값, 2026-11-17) ----------------
IMMEDIATE_BARS = 3          # '즉시 역행': 보유 봉 수가 이 값 이하에서 손절
NEAR_T1_MFE_R = 0.7         # 'T1 근접 후 반전': 손절인데 MFE 가 이 R 이상이었음
ATR_LEN = 14                # ATR 길이(단순 평균 TR)
ATR_LOOKBACK = 100          # '변동성 급등': 진입 직전(신호 봉) ATR 을 최근 이 봉 수와 비교
ATR_TOP_PCT = 20.0          #  … 상위 이 % 안이면 급등
ATR_MIN_SAMPLES = 30        #  … 비교 표본이 이보다 적으면 판단 안 함
EVENT_NEAR_HOURS = 2        # '이벤트 근처': 진입 앞뒤 이 시간 안에 그 코인(또는 ALL) 일정
TREND_EXT_MIN_R = 1.0       # '추세 연장 익절': T1 뒤 다음 스윙/반대 신호 청산으로 거래 합계 이 R 이상
TREND_EXIT_REASONS = ("다음 스윙", "반대 장악형", "거래량 급증")
GEMINI_MAX_CALLS_PER_RUN = 3   # 한 번 실행에서 새로 부르는 복기 해설 수(나머지는 다음 실행)
GEMINI_MAX_FAILS = 3           # 같은 거래에서 이만큼 실패하면 더는 부르지 않음(빈칸)
GEMINI_BACKOFF_H = 24          # 429·할당량 오류면 이 시간 동안 해설 호출을 쉼(실행 시간 낭비 방지, 거래 실패 횟수엔 안 셈)
QUOTA_RE = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|quota", re.I)

LOSS_TAGS = ("즉시 역행", "T1 근접 후 반전", "1시간 추세 역방향 진입", "변동성 급등", "이벤트 근처")
WIN_TAGS = ("T1 후 본절", "추세 연장 익절", "T1 반익절만")
DIR_KO = {1: "상승", -1: "하락", 0: "중립", None: "-"}


def _coin(t):
    return t.get("coin") or "BTC"


def _side_ko(s):
    return "롱" if s == 1 else "숏"


def atr_series(D):
    """단순 평균 TR(ATR_LEN). 앞쪽은 None"""
    H, L, C = D.H, D.L, D.C
    n = len(C)
    tr = [H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(n)]
    out = [None] * n
    s = 0.0
    for i in range(n):
        s += tr[i]
        if i >= ATR_LEN:
            s -= tr[i - ATR_LEN]
        if i >= ATR_LEN - 1:
            out[i] = s / ATR_LEN
    return out


def _cache(D):
    c = getattr(D, "_review_cache", None)
    if c is None:
        c = dict(idx={t: i for i, t in enumerate(D.T)}, atr=atr_series(D))
        try:
            D._review_cache = c
        except AttributeError:
            pass
    return c


def atr_spike(D, i_sig):
    """신호 봉 ATR 이 최근 ATR_LOOKBACK 봉(자기 포함) 중 상위 ATR_TOP_PCT% 안인가 → (bool, 백분위)"""
    atr = _cache(D)["atr"]
    if i_sig is None or i_sig < 0 or atr[i_sig] is None:
        return False, None
    win = [a for a in atr[max(0, i_sig - ATR_LOOKBACK + 1):i_sig + 1] if a is not None]
    if len(win) < ATR_MIN_SAMPLES:
        return False, None
    below = sum(1 for a in win if a < atr[i_sig])
    pct = below / len(win) * 100           # 자기보다 작은 값의 비율
    return pct >= 100 - ATR_TOP_PCT, round(pct, 1)


def near_events(t, events, hours=EVENT_NEAR_HOURS):
    """진입 앞뒤 hours 시간 안의 그 코인(또는 ALL) 일정 이름 목록 (중요도 무관, 기록용)"""
    if not events:
        return []
    a = t["entry_time"] - dt.timedelta(hours=hours)
    b = t["entry_time"] + dt.timedelta(hours=hours)
    cn = _coin(t)
    out = []
    for e in events:
        coins = e[3] if len(e) > 3 else None
        if a <= e[0] <= b and (coins is None or cn in coins):
            out.append(e[1])
    return out


def cause_tags(t, held, mfe_R, spike, ev_near):
    """규칙 기반 원인 태그(여러 개 가능)"""
    tags = []
    t1 = bool(t.get("t1Hit"))
    why = t.get("why") or {}
    dir60 = why.get("dir60", t.get("dir60"))
    reason = t.get("exit_reason")
    if t1:
        if reason == "본절":
            tags.append("T1 후 본절")
        elif reason in TREND_EXIT_REASONS and t["R"] >= TREND_EXT_MIN_R:
            tags.append("추세 연장 익절")
        else:
            tags.append("T1 반익절만")
        return tags
    if t["pnl"] < 0:
        if reason == "손절" and held <= IMMEDIATE_BARS:
            tags.append("즉시 역행")
        if reason == "손절" and mfe_R is not None and mfe_R >= NEAR_T1_MFE_R:
            tags.append("T1 근접 후 반전")
        if dir60 is not None and dir60 == -t["side"]:
            tags.append("1시간 추세 역방향 진입")
        if spike:
            tags.append("변동성 급등")
        if ev_near:
            tags.append("이벤트 근처")
        if reason == "돌파 실패":
            tags.append("돌파 실패")
        if not tags:
            tags.append("미분류 손절" if reason == "손절" else f"미분류 손실({reason})")
        return tags
    # T1 없이 수익·본전 (돌파 실패 청산·v13 1.5R 익절·기간 종료 등)
    tags.append(f"기타 청산({reason})")
    return tags


def review_trade(t, D, events=None):
    """거래 하나의 숫자 복기 → dict(held_bars, mfe_R, mae_R, entry_hour, atr_pct, tags, events_near). 실패 시 None"""
    c = _cache(D)
    i0 = c["idx"].get(t["entry_time"])
    i1 = c["idx"].get(t["exit_time"])
    if i0 is None or i1 is None or i1 < i0:
        # 시각이 정확히 없으면 가장 가까운 앞 봉
        i0 = bisect.bisect_right(D.T, t["entry_time"]) - 1 if i0 is None else i0
        i1 = bisect.bisect_right(D.T, t["exit_time"]) - 1 if i1 is None else i1
        if i0 < 0 or i1 < i0:
            return None
    s, entry = t["side"], t["entry"]
    risk = abs(entry - t["stop0"])
    hi, lo = max(D.H[i0:i1 + 1]), min(D.L[i0:i1 + 1])
    fav = (hi - entry) if s == 1 else (entry - lo)
    adv = (entry - lo) if s == 1 else (hi - entry)
    mfe = max(fav, 0.0) / risk if risk > 0 else None
    mae = -max(adv, 0.0) / risk if risk > 0 else None
    held = i1 - i0 + 1
    spike, pct = atr_spike(D, i0 - 1)
    evn = near_events(t, events)
    tags = cause_tags(t, held, mfe, spike, evn)
    return dict(held_bars=held, mfe_R=None if mfe is None else round(mfe, 3),
                mae_R=None if mae is None else round(mae, 3),
                entry_hour=t["entry_time"].hour, atr_pct=pct, tags=tags, events_near=evn)


def attach_reviews(trades, datas, events=None, log=None):
    """trades 에 t["review"] 를 붙인다. datas: {코인: engine.Data}. 거래 하나가 실패해도 나머지는 계속."""
    n = 0
    for t in trades:
        try:
            D = datas.get(_coin(t))
            rv = review_trade(t, D, events) if D is not None else None
            if rv is not None:
                t["review"] = rv
                n += 1
        except Exception as e:  # 기록용 기능 — 절대 봇을 멈추지 않음
            if log:
                log(f"복기 계산 실패({t.get('id')}): {e!r}", warn=True)
    return n


def journal_cols(t, with_text=False, texts=None):
    """일지에 덧붙일 열: 보유봉수, MFE_R, MAE_R, 원인태그 (+ 복기해설)"""
    rv = t.get("review") or {}
    row = [rv.get("held_bars", ""),
           "" if rv.get("mfe_R") is None else f"{rv['mfe_R']:.2f}",
           "" if rv.get("mae_R") is None else f"{rv['mae_R']:.2f}",
           " / ".join(rv.get("tags") or [])]
    if with_text:
        row.append((texts or {}).get(t["id"], "") or t.get("review_text", "") or "")
    return row

# ============================== Gemini 복기 해설 (공부용) ==============================

def review_prompt(t):
    rv = t.get("review") or {}
    why = t.get("why") or {}

    def px(x):
        return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.6g}"
    lines = [
        "너는 단타 매매 복기 코치다. 아래는 가상 매매(실제 돈 아님) 한 건의 숫자 기록이다.",
        "이 기록만 근거로, 한국어 2~3문장으로 '왜 이겼는지/졌는지'와 '다음에 볼 점'을 써라.",
        "가격 예측·매수/매도 추천·규칙 변경 제안은 하지 말고, 이 거래 자체의 복기만. 머리말·목록·마크다운 없이 문장만.",
        "",
        f"- 코인: {_coin(t)} / 방향: {_side_ko(t['side'])} / 셋업: {t['tag']}{t['grade']}",
        f"- 진입 근거: 구간 {' + '.join(why.get('zone') or []) or '-'}, 보조 {' + '.join(why.get('support') or []) or '-'}",
        f"- 15분 방향: {DIR_KO.get(why.get('dir15'))} / 1시간 방향: {DIR_KO.get(why.get('dir60', t.get('dir60')))}",
        f"- 진입 {t['entry_time']:%m-%d %H:%M} KST @ {px(t['entry'])}, 최초 손절 {px(t['stop0'])}, T1 {px(t['t1'])}"
        f" (손절폭 {why.get('stop_pct', '-')}%, T1까지 {why.get('t1_R', '-')}R)",
        f"- 청산 내역: {' | '.join(t.get('exits') or [])}",
        f"- 결과: 마지막 청산 '{t['exit_reason']}', 거래 합계 {t['R']:+.2f}R",
        f"- 보유 {rv.get('held_bars', '-')}봉(5분), MFE {rv.get('mfe_R', '-')}R, MAE {rv.get('mae_R', '-')}R,"
        f" 진입 직전 ATR 백분위 {rv.get('atr_pct', '-')}",
        f"- 규칙 기반 원인 태그: {', '.join(rv.get('tags') or []) or '-'}",
    ]
    if rv.get("events_near"):
        lines.append(f"- 진입 앞뒤 2시간 일정: {', '.join(rv['events_near'])}")
    return "\n".join(lines)


def _clean(txt, n=400):
    txt = re.sub(r"\s+", " ", (txt or "").replace("*", "")).strip()
    return txt[:n]


def load_reviews(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("reviews"), dict) else {"reviews": {}}
    except (OSError, ValueError):
        return {"reviews": {}}


def save_reviews(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d["note"] = "Gemini 복기 해설 캐시(거래 ID 키). 공부용 — 매매 판단에 쓰지 않음. 한 번 받은 거래는 다시 부르지 않음."
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def gemini_reviews(trades, path, now, log=None, key=None, generate=None, max_calls=GEMINI_MAX_CALLS_PER_RUN):
    """본 장부 청산 거래마다 해설 1회. 결과 {거래ID: 해설}. 이미 캐시에 있으면 다시 부르지 않는다.
    키가 없으면 부르지 않고(캐시도 안 바꿈) 캐시에 있는 것만 돌려준다. 실패는 경고만."""
    from tools import gemini_client as gc
    d = load_reviews(path)
    rv = d["reviews"]
    key = key if key is not None else gc.api_key()
    generate = generate or gc.generate
    calls, changed = 0, False
    bo = d.get("backoff_until")
    if key and bo:
        try:
            if now < dt.datetime.strptime(bo[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo):
                key = None                       # 할당량 백오프 중: 캐시만 돌려줌
                if log:
                    log(f"Gemini 복기 해설: 할당량 오류로 {bo[:16]} 까지 쉼(캐시만 사용)")
        except ValueError:
            pass
    if key:
        for t in sorted(trades, key=lambda x: x["exit_time"], reverse=True):     # 최근 거래부터
            ent = rv.get(t["id"])
            if ent and (ent.get("text") or ent.get("fails", 0) >= GEMINI_MAX_FAILS):
                continue
            if calls >= max_calls:
                break
            calls += 1
            try:
                txt, model = generate(review_prompt(t), key=key, search=False, state=d, temperature=0.4, max_tokens=600)
                txt = _clean(txt)
                if not txt:
                    raise ValueError("빈 답")
                rv[t["id"]] = dict(text=txt, model=model, at=now.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                if QUOTA_RE.search(str(e)):
                    until = now + dt.timedelta(hours=GEMINI_BACKOFF_H)
                    d["backoff_until"] = until.strftime("%Y-%m-%d %H:%M:%S")
                    d["backoff_reason"] = str(e)[:200]
                    changed = True
                    if log:
                        log(f"Gemini 복기 해설: 429/할당량 오류 → {until:%m-%d %H:%M} 까지 해설 호출 쉼", warn=True)
                    break
                ent = rv.get(t["id"]) or {}
                rv[t["id"]] = dict(text="", fails=int(ent.get("fails", 0)) + 1, last_error=str(e)[:200],
                                   at=now.strftime("%Y-%m-%d %H:%M:%S"))
                if log:
                    log(f"Gemini 복기 해설 실패({t['id']}): {str(e)[:160]}", warn=True)
            changed = True
        if changed:
            save_reviews(path, d)
    return {k: v.get("text", "") for k, v in rv.items() if v.get("text")}

# ============================== 하루 복기 리포트 ==============================

def _won(x):
    r = round(x)
    return "0원" if r == 0 else f"{'+' if r > 0 else '−'}{abs(r):,}원"


def daily_report(day, trades, texts=None, recent=None):
    """day(date)에 청산된 거래의 복기 리포트(마크다운)와 ntfy 요약(3~4줄). recent: 최근 7일 거래(반복 패턴용)"""
    texts = texts or {}
    ts = sorted([t for t in trades if t["exit_time"].date() == day], key=lambda t: t["exit_time"])
    L = [f"# 복기 리포트 {day} (KST, 청산 기준)\n",
         "> 가상 매매(실제 돈 아님) 기록의 자동 복기. 원인 태그는 규칙 기반, 해설은 Gemini(공부용 · 매매 판단에 쓰지 않음).\n"]
    n = len(ts)
    pnl = sum(t["pnl"] for t in ts)
    wins = sum(1 for t in ts if t["pnl"] > 0)
    sumR = sum(t["R"] for t in ts)
    L.append(f"- 거래 {n}건 · 이익 {wins}건 · 합계 {sumR:+.2f}R · 손익 {_won(pnl)}\n")
    try:                                    # (2) 상관 신호 묶음 · (4) 손절 손익 분해 — 정보가 있을 때만
        from tools.extras import independent_count, stop_decomp_summary
        if ts and any(t.get("cluster") for t in ts):
            L.append(f"- 독립 묶음 {independent_count(ts)}개 (같은 방향 신호가 여러 코인에 ±1봉 안에 동시에 나면 한 묶음 — "
                     f"거래 {n}건을 독립 {n}건으로 세지 않음)\n")
        sd = stop_decomp_summary(ts) if ts else None
        if sd:
            L.append(f"## 손절 거래 평균 손익 분해 (R, T1 전 손절 {sd['n']}건)\n")
            L.append("| 계획 손절 | 갭 | 슬리피지 | 진입 수수료 | 청산 수수료 | 합계 | 실제 R | 실제위험/계획위험 |")
            L.append("|---|---|---|---|---|---|---|---|")
            L.append(f"| {sd['plan_R']:+.3f} | {sd['gap_R']:+.3f} | {sd['slip_R']:+.3f} | {sd['fee_entry_R']:+.3f} | "
                     f"{sd['fee_exit_R']:+.3f} | {sd['sum_R']:+.3f} | {sd['R']:+.3f} | "
                     f"{'' if sd['risk_ratio'] is None else format(sd['risk_ratio'], '.3f')} |")
            L.append("\nR 분모 = 수량 × |진입 체결가 − 최초 손절가| (계획 손절까지 잃는 돈, 비용 제외). "
                     "진입 슬리피지는 분모(진입 체결가)에 들어가 있어 따로 빼지 않음.\n")
    except Exception:
        pass
    if not ts:
        L.append("이날 청산된 거래가 없습니다.\n")
    else:
        L.append("## 거래별\n")
        L.append("| # | 코인 | 진입 | 방향 | 셋업 | 마지막 청산 | R | 손익 | 보유봉 | MFE | MAE | 원인 태그 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, t in enumerate(ts, 1):
            rv = t.get("review") or {}
            L.append(f"| {k} | {_coin(t)} | {t['entry_time']:%H:%M} | {_side_ko(t['side'])} | {t['tag']}{t['grade']} | "
                     f"{t['exit_reason']} | {t['R']:+.2f} | {_won(t['pnl'])} | {rv.get('held_bars', '')} | "
                     f"{'' if rv.get('mfe_R') is None else format(rv['mfe_R'], '.2f')} | "
                     f"{'' if rv.get('mae_R') is None else format(rv['mae_R'], '.2f')} | {' / '.join(rv.get('tags') or [])} |")
        hs = [(t, texts.get(t["id"])) for t in ts if texts.get(t["id"])]
        if hs:
            L.append("\n### 복기 해설 (Gemini)\n")
            for t, x in hs:
                L.append(f"- **{_coin(t)} {t['entry_time']:%H:%M} {_side_ko(t['side'])} {t['tag']}{t['grade']} ({t['R']:+.2f}R)**: {x}")
        # 태그별
        bt = defaultdict(lambda: [0, 0.0, 0.0])
        for t in ts:
            for g in (t.get("review") or {}).get("tags") or ["(태그 없음)"]:
                bt[g][0] += 1; bt[g][1] += t["pnl"]; bt[g][2] += t["R"]
        L.append("\n## 원인 태그별\n")
        L.append("| 태그 | 건수 | 합계 R | 손익 |\n|---|---|---|---|")
        for g, (c, p, r) in sorted(bt.items(), key=lambda x: (-x[1][0], x[1][1])):
            L.append(f"| {g} | {c} | {r:+.2f} | {_won(p)} |")
        for title, keyf in (("코인별", _coin), ("진입 시간대별(KST)", lambda t: f"{t['entry_time'].hour:02d}시")):
            g2 = defaultdict(list)
            for t in ts:
                g2[keyf(t)].append(t)
            L.append(f"\n## {title}\n")
            L.append("| 구분 | 건수 | 승률 | 합계 R | 손익 |\n|---|---|---|---|---|")
            for k in sorted(g2):
                v = g2[k]
                L.append(f"| {k} | {len(v)} | {sum(1 for t in v if t['pnl'] > 0) / len(v) * 100:.0f}% | "
                         f"{sum(t['R'] for t in v):+.2f} | {_won(sum(t['pnl'] for t in v))} |")
    # 반복된 실패 패턴
    top = failure_patterns(ts)
    L.append("\n## 반복된 실패 패턴 상위 3개 (이날)\n")
    L.append("\n".join(f"{k}. {g} — {c}건, {_won(p)}" for k, (g, c, p) in enumerate(top, 1)) if top else "손실 거래 없음.")
    if recent is not None:
        top7 = failure_patterns(recent)
        L.append("\n## 반복된 실패 패턴 상위 3개 (최근 7일)\n")
        L.append("\n".join(f"{k}. {g} — {c}건, {_won(p)}" for k, (g, c, p) in enumerate(top7, 1)) if top7 else "손실 거래 없음.")
    L.append("")
    # ntfy 요약 3~4줄
    s = [f"📝 {day:%m-%d} 복기: 거래 {n}건 · 이익 {wins}건 · {sumR:+.2f}R · {_won(pnl)}"]
    if top:
        s.append("실패 패턴: " + ", ".join(f"{g} {c}건" for g, c, _p in top))
    if ts:
        best = max(ts, key=lambda t: t["R"]); worst = min(ts, key=lambda t: t["R"])
        s.append(f"최고 {_coin(best)} {best['tag']}{best['grade']} {best['R']:+.2f}R · 최저 {_coin(worst)} {worst['tag']}{worst['grade']} {worst['R']:+.2f}R")
    s.append(f"리포트: ledger/review/{day}.md")
    return "\n".join(L) + "\n", "\n".join(s[:4])


def failure_patterns(trades, k=3):
    """손실 거래의 원인 태그 빈도 상위 k → [(태그, 건수, 손익합)] (건수 많은 순, 같으면 손실 큰 순)"""
    c = defaultdict(lambda: [0, 0.0])
    for t in trades:
        if t["pnl"] < 0 and not t.get("t1Hit"):
            for g in (t.get("review") or {}).get("tags") or []:
                c[g][0] += 1; c[g][1] += t["pnl"]
    return [(g, v[0], v[1]) for g, v in sorted(c.items(), key=lambda x: (-x[1][0], x[1][1]))[:k]]


def maybe_daily_report(now, trades, out_dir, start_time, texts=None, log=None):
    """KST 날짜가 바뀐 뒤 첫 실행: 전날 리포트 out_dir/YYYY-MM-DD.md 생성(이미 있으면 그대로).
    반환: ntfy 용 이벤트 dict(type='review', id='R{날짜}') 또는 None — 이미 보낸 ID 는 notify 가 거른다."""
    day = (now - dt.timedelta(days=1)).date()
    if start_time is not None and start_time.date() > day:
        return None
    path = os.path.join(out_dir, f"{day}.md")
    recent = [t for t in trades if day - dt.timedelta(days=6) <= t["exit_time"].date() <= day]
    md, summary = daily_report(day, trades, texts, recent)
    if not os.path.exists(path):
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        if log:
            log(f"복기 리포트 생성: ledger/review/{day}.md")
    return dict(type="review", id=f"R{day:%Y%m%d}", time=now, msg=summary)
