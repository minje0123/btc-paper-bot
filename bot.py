# -*- coding: utf-8 -*-
"""
BTC 5분봉 가상 매매 봇 (원칙 v1.4.1 + 쉽알남 자금 관리 v1.4.1-mm) — GitHub Actions에서 5분마다 실행

⚠️ 실제 주문은 절대 하지 않는다. 거래소에는 '공개 시세 조회(캔들)'만 요청하고,
   매매는 봇 자체 가상 장부(ledger/)에만 기록한다. API 키도 쓰지 않는다.

실행할 때마다
  1) data/candles_5m.csv 를 읽고, 업비트(실패 시 빗썸) KRW-BTC 5분봉 중 새로 닫힌 봉을 이어 붙인다.
     - 처음 정한 출처(state/source.json)를 계속 쓰고, 그 출처가 실패할 때만 다른 쪽으로 받는다(경고 기록).
     - 한 번의 실행에서 받는 봉은 한 출처에서만 받는다(섞지 않음). 진행 중인 봉은 저장하지 않는다.
  2) 처음 실행이면 과거 warmup_days(기본 3일)를 받아 지표 준비용으로만 쓰고,
     가상 매매는 config.json 의 start_time(비어 있으면 첫 실행 시각으로 채움) 이후 봉부터 한다.
  3) 저장된 전체 봉으로 엔진을 처음부터 다시 돌린다(결정적 재계산) → 거래 목록·현재 포지션·잔고
  4) ledger/trades.csv, ledger/status.json, ledger/ACCOUNT.md 저장
  5) 새 이벤트(진입·T1 반익절·청산·하루 한도·하루 목표 달성·주간 출금)를 ntfy.sh 로 푸시(환경변수 NTFY_TOPIC 이 있을 때만).
     보낸 이벤트는 state/notified.json 에 기록해 두 번 보내지 않는다.

사용법
  python bot.py              # 실제 실행 (알림 전송)
  python bot.py --dry-run    # 알림을 보내지 않고 화면에만 출력 (notified.json 도 바꾸지 않음)
표준 라이브러리만 사용.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import engine
from engine import KST

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
DATA_DIR = os.path.join(HERE, "data")
LEDGER_DIR = os.path.join(HERE, "ledger")
STATE_DIR = os.path.join(HERE, "state")
CANDLES = os.path.join(DATA_DIR, "candles_5m.csv")
TRADES = os.path.join(LEDGER_DIR, "trades.csv")
STATUS = os.path.join(LEDGER_DIR, "status.json")
ACCOUNT = os.path.join(LEDGER_DIR, "ACCOUNT.md")
SOURCE_STATE = os.path.join(STATE_DIR, "source.json")
NOTIFIED = os.path.join(STATE_DIR, "notified.json")
BOT_LOG = os.path.join(STATE_DIR, "bot.log")

BAR = dt.timedelta(minutes=5)
TFMT = "%Y-%m-%d %H:%M:%S"

# 출처별 공개 캔들 API (둘 다 같은 응답 형식). to 파라미터: 업비트는 +09:00 포함, 빗썸은 KST 그대로
SOURCES = {
    "upbit": ("https://api.upbit.com/v1/candles/minutes/5?", "%Y-%m-%dT%H:%M:%S+09:00"),
    "bithumb": ("https://api.bithumb.com/v1/candles/minutes/5?", "%Y-%m-%dT%H:%M:%S"),
}
SOURCE_KO = {"upbit": "업비트", "bithumb": "빗썸"}

_logs = []
_warnings = []


def log(msg, warn=False):
    line = f"[{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S}] {'⚠️ 경고: ' if warn else ''}{msg}"
    print(line, flush=True)
    _logs.append(line)
    if warn:
        _warnings.append(msg)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def floor5(t):
    t = t.replace(second=0, microsecond=0)
    return t - dt.timedelta(minutes=t.minute % 5)

# ============================== 1) 데이터 ==============================

def fetch_range(source, start, end, market="KRW-BTC"):
    """[start, end) 구간의 닫힌 5분봉을 한 출처에서만 받아 {시각: (o,h,l,c,v)} 로 돌려준다. 실패하면 예외."""
    base, tfmt = SOURCES[source]
    rows = {}
    to = end
    max_pages = int((end - start) / (BAR * 200)) + 3
    for _ in range(max_pages):
        if to <= start:
            break
        q = urllib.parse.urlencode({"market": market, "count": 200, "to": to.strftime(tfmt)})
        req = urllib.request.Request(base + q, headers={"Accept": "application/json",
                                                        "User-Agent": "btc-paper-bot (read-only candles)"})
        data, err = None, None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                err = e
                if e.code == 429 or e.code >= 500:
                    time.sleep(1.0 + attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = e
                time.sleep(1.0 + attempt)
        if data is None:
            raise RuntimeError(f"{source} 요청 실패: {err!r}")
        if isinstance(data, dict):
            raise RuntimeError(f"{source} 오류 응답: {data}")
        if not data:
            break
        oldest = None
        for d in data:
            t = dt.datetime.strptime(d["candle_date_time_kst"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST)
            oldest = t if oldest is None or t < oldest else oldest
            if start <= t < end:                 # end 이상 = 진행 중인 봉 → 저장 안 함
                rows[t] = (float(d["opening_price"]), float(d["high_price"]), float(d["low_price"]),
                           float(d["trade_price"]), float(d["candle_acc_trade_volume"]))
        if oldest is None or oldest >= to:
            break
        to = oldest
        time.sleep(0.15)
    return rows


def load_candles():
    """기존 CSV → {시각: [o,h,l,c,v,source]}"""
    out = {}
    if not os.path.exists(CANDLES):
        return out
    with open(CANDLES, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = engine.parse_kst(r["time_kst"])
            out[t] = [float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                      float(r["volume"]), r.get("source") or ""]
    return out


def _num(x):
    """가격: 정수면 정수로, 아니면 그대로 (CSV 를 다시 읽어도 값이 바뀌지 않게)"""
    return str(int(x)) if x == int(x) else repr(x)


def save_candles(c):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CANDLES + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_kst", "open", "high", "low", "close", "volume", "source"])
        for t in sorted(c):
            o, h, l, cl, v, s = c[t]
            w.writerow([t.strftime(TFMT), _num(o), _num(h), _num(l), _num(cl), repr(v), s])
    os.replace(tmp, CANDLES)


def update_candles(cfg, start_time, end):
    """새로 닫힌 봉을 받아 붙인다. 반환: (candles dict, 이번에 쓴 출처, 주 출처)"""
    candles = load_candles()
    src_state = read_json(SOURCE_STATE, {}) or {}
    primary = src_state.get("primary")
    market = cfg.get("market", "KRW-BTC")
    warm = dt.timedelta(days=float(cfg.get("warmup_days", 3)))
    max_back = dt.timedelta(days=float(cfg.get("max_backfill_days", 30)))

    if candles:
        last = max(candles)
        fetch_start = last + BAR
        if end - fetch_start > max_back:
            log(f"마지막 봉({last:%m-%d %H:%M})이 너무 오래돼 최근 {max_back.days}일만 받습니다(중간 공백 생김).", warn=True)
            fetch_start = end - max_back
    else:
        fetch_start = start_time - warm          # 첫 실행: 워밍업 3일
        log(f"첫 실행: 워밍업 데이터 {fetch_start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} 받기")

    used = None
    if fetch_start >= end:
        log("새로 닫힌 봉 없음")
        return candles, None, primary

    order = [primary] + [s for s in SOURCES if s != primary] if primary else list(SOURCES)
    for k, src in enumerate(order):
        try:
            rows = fetch_range(src, fetch_start, end, market)
        except Exception as e:
            log(f"{SOURCE_KO[src]} 데이터 받기 실패: {e!r}", warn=True)
            continue
        used = src
        if primary is None:
            primary = src
            write_json(SOURCE_STATE, {"primary": src, "set_at": dt.datetime.now(KST).strftime(TFMT),
                                      "note": "처음 데이터를 받은 출처. 이후 계속 이 출처를 쓰고, 실패할 때만 다른 쪽으로 대체"})
            log(f"데이터 출처를 {SOURCE_KO[src]}(으)로 정했습니다.")
        elif src != primary:
            log(f"주 출처 {SOURCE_KO[primary]} 실패로 이번 실행의 봉 {len(rows)}개는 {SOURCE_KO[src]}에서 받았습니다"
                f"(candles_5m.csv 의 source 열에 표시).", warn=True)
        added = 0
        for t, v in rows.items():
            if t not in candles:                   # 이미 저장된 봉은 절대 덮어쓰지 않음(결정적 재계산)
                candles[t] = list(v) + [src]
                added += 1
        log(f"{SOURCE_KO[src]}에서 닫힌 봉 {added}개 추가 (총 {len(candles)}개)")
        break
    else:
        log("모든 데이터 출처 실패 — 저장된 봉으로만 장부를 다시 계산합니다.", warn=True)
    return candles, used, primary

# ============================== 2) 장부 ==============================

def won(x):
    """+1,234원 / −1,234원 / 0원 (마이너스 기호는 −)"""
    r = round(x)
    if r == 0:
        return "0원"
    return f"{'+' if r > 0 else '−'}{abs(r):,}원"


def side_ko(s):
    return "롱" if s == 1 else "숏"


def setup_name(tag, grade):
    return f"{tag}{grade}"


def write_trades(trades):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(TRADES, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "거래ID", "진입시각", "방향", "셋업", "등급", "진입가", "최초손절", "T1", "수량BTC",
                    "레버리지", "위험금액", "1시간방향", "청산내역", "마지막청산", "청산시각", "손익원", "R",
                    "수량배율"])          # 수량배율: 1 / 0.5(그날 첫 매매 수익 뒤) / 목표후(목표가 남는 크기) — 기존 열 순서 유지, 끝에 추가
        for k, t in enumerate(trades, 1):
            w.writerow([k, t["id"], t["entry_time"].strftime("%Y-%m-%d %H:%M"), side_ko(t["side"]), t["tag"],
                        t["grade"], f"{t['entry']:.0f}", f"{t['stop0']:.0f}", f"{t['t1']:.0f}", f"{t['qty']:.4f}",
                        f"{t['lev']:.2f}", f"{t['rm']:.0f}", t["dir60"], " | ".join(t["exits"]), t["exit_reason"],
                        t["exit_time"].strftime("%Y-%m-%d %H:%M"), f"{t['pnl']:.0f}", f"{t['R']:.3f}",
                        t.get("sizeTag", "1")])


def position_dict(pos, last_close, fee):
    if pos is None:
        return None
    unreal = pos["side"] * (last_close - pos["entry"]) * pos["open"] - pos["entry"] * pos["open"] * fee
    return dict(
        id=pos["id"], side=side_ko(pos["side"]), setup=pos["tag"], grade=pos["grade"],
        entry_time=pos["entry_time"].strftime(TFMT), entry=round(pos["entry"]),
        stop=round(pos["stop"]), stop0=round(pos["stop0"]), t1=round(pos["t1"]),
        next_target=round(pos["nxt"]) if pos.get("nxt") else None,
        qty=pos["qty"], qty_open=pos["open"], leverage=round(pos["lev"], 2), risk_money=round(pos["rm"]),
        t1_done=pos["t1Hit"], realized_part=round(pos["pnl"]), last_price=round(last_close),
        unrealized_pnl=round(unreal),
        note="미실현손익 = (현재가−진입가)×남은 수량 − 진입 수수료 (청산 수수료 미포함)",
    )


def build_status(cfg, P, R, start_time, primary, used, now):
    trades = R["trades"]
    st = engine.stats(trades, R["curve"])
    day = R["day"]
    pend = R["pending"]
    eq0 = P["capital"]
    wd_total = R["withdrawn"]
    return dict(
        mode="가상 매매(paper) — 실제 주문 없음",
        rules="원칙 v1.4.1 + 쉽알남 자금 관리(v1.4.1-mm)",
        account_type="단타 계좌 (쉽알남 단타:스윙:장투 = 2:3:5 중 2)",
        start_time=start_time.strftime(TFMT),
        initial_capital=eq0,
        balance=round(R["final"]),                       # 거래 계좌 잔고(실현 기준, 출금 뺀 값) = 거래 시드
        trading_seed=round(R["seed"]),                   # 1R 계산 기준 금액
        risk_per_trade_won=round(day["risk_per_trade"]), # 지금 1R(기본 셋업 1회 고정 손실) 금액
        risk_pct=P["riskPct"], reduced_risk_pct=P["redPct"],
        withdrawn_total=round(wd_total),
        withdraw_pct=P["withdrawPct"],
        first_win_half=P["firstWinHalf"],
        withdrawals=[dict(time=w["time"].strftime(TFMT), week_start=str(w["week_start"]),
                          week_pnl=round(w["week_pnl"]), amount=round(w["amount"]), total=round(w["total"]),
                          seed_after=round(w["seed_after"])) for w in R["withdrawals"]],
        total_value=round(R["final"] + wd_total),        # 거래 계좌 잔고 + 출금 누적
        principal_recovered=wd_total >= eq0,             # 출금 누적이 원금 이상이면 '원금 회수 완료'
        principal_recovered_pct=round(wd_total / eq0 * 100, 1) if eq0 else 0.0,
        equity=round(R["equity"]),
        realized_pnl=round(R["realized"]),
        unrealized_pnl=round(R["unrealized"]) if R["position"] else 0,
        trades_count=st["N"], win_rate=round(st["win"], 1), avg_R=round(st["avgR"], 3),
        max_drawdown_pct=round(st["mdd"], 2),
        has_position=R["position"] is not None,
        position=position_dict(R["position"], R["last_close"], P["fee"]),
        pending_entry=None if pend is None else dict(
            side=side_ko(pend["side"]), setup=pend["tag"], grade=pend["grade"],
            signal_time=pend["signal_time"].strftime(TFMT), signal_close=round(pend["sigClose"]),
            stop=round(pend["stop"]), t1=round(pend["t1"]), qty=pend["qty"],
            note="다음 봉 시가에 진입 예정"),
        pending_close=R["pending_close"],
        today=dict(date=str(day["date"]) if day["date"] else None, realized_pnl=round(day["realized"]),
                   consecutive_losses=day["consec"], no_new_entries=bool(day["stop"]), reason=day["reason"],
                   start_equity=round(day["start_equity"]),
                   target_pct=P["dayTargetP"],
                   target_amount=round(day["target_amount"]) if day["target_amount"] is not None else None,
                   target_reached=bool(day["target_reached"]),
                   loss_limit_pct=P["dayLossP"],
                   loss_limit_amount=round(day["loss_limit_amount"]),   # 양수 금액. 실현손익이 −이 값 이하면 신규 금지
                   first_trade_result=day["first_trade_result"],         # 수익 / 손실 / 본전 / None(아직 없음)
                   next_size_factor=round(day["next_size_factor"], 3),   # 1 / 0.5 / 목표 후 축소 비율 / 0(진입 안 함)
                   next_size_mode=day["next_size_mode"],
                   next_risk_won=round(day["next_risk"] or 0)),
        last_bar_time=R["last_bar_time"], last_price=round(R["last_close"]) if R["last_close"] else None,
        data_source=primary, data_source_this_run=used,
        updated_at=now.strftime(TFMT),
        warnings=_warnings[:],
    )


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def write_account(status, R):
    trades = R["trades"]
    p = status["position"]
    L = []
    L.append(f"# BTC 5분봉 가상 매매 계좌 (원칙 v1.4.1 + 쉽알남 자금 관리)\n")
    L.append(f"## 포지션: {'있음' if p else '없음'}\n")
    L.append("> ⚠️ 실제 돈을 쓰지 않는 **가상 매매** 기록입니다. 거래소에 주문하지 않습니다.\n")
    L.append(f"> 계좌 성격: **{status['account_type']}**. 수익 일부는 매주 장투 계좌로 '출금'한 것으로 칩니다.\n")
    L.append(f"- 업데이트: {status['updated_at']} (KST) · 마지막 봉: {status['last_bar_time']} · "
             f"현재가 {status['last_price']:,}원 · 데이터: {SOURCE_KO.get(status['data_source'], status['data_source'])}"
             if status["last_price"] else f"- 업데이트: {status['updated_at']}")
    L.append(f"- 시작: {status['start_time']} · 초기 자금 {status['initial_capital']:,.0f}원\n")
    if status["warnings"]:
        L.append("> ⚠️ 이번 실행 경고: " + " / ".join(status["warnings"]) + "\n")

    eq0 = status["initial_capital"]
    L.append("| 항목 | 값 |\n|---|---|")
    wd = status["withdrawn_total"]
    L.append(f"| 거래 시드(잔고, 실현 기준·출금 제외) | **{status['trading_seed']:,}원** |")
    L.append(f"| 지금 1R(1회 고정 손실) | {status['risk_per_trade_won']:,}원 (시드의 {status['risk_pct']:g}%"
             + (f", 1시간 반대·B+·C {status['reduced_risk_pct']:g}%" if status['reduced_risk_pct'] != status['risk_pct'] else "")
             + ") |")
    L.append(f"| 출금 누적(장투 계좌로) | {wd:,}원 ({len(status['withdrawals'])}회, 지난주 수익의 {status['withdraw_pct']:g}%) |")
    L.append(f"| 잔고 + 출금 합계 | {status['total_value']:,}원 ({(status['total_value'] - eq0) / eq0 * 100:+.2f}%) |")
    rec = "✅ 완료 (출금 누적 ≥ 원금)" if status["principal_recovered"] else f"{status['principal_recovered_pct']:.1f}% 회수"
    L.append(f"| 원금 회수 | {rec} |")
    L.append(f"| 평가 잔고(미실현 포함) | {status['equity']:,}원 |")
    L.append(f"| 누적 실현손익 | {won(status['realized_pnl'])} ({status['realized_pnl'] / eq0 * 100:+.2f}%) |")
    L.append(f"| 미실현손익 | {won(status['unrealized_pnl'])} |")
    L.append(f"| 거래 수 / 승률 | {status['trades_count']}건 / {status['win_rate']:.1f}% |")
    L.append(f"| 평균 R | {status['avg_R']:+.3f}R |")
    L.append(f"| 최대낙폭(MDD, 잔고+출금 기준) | {status['max_drawdown_pct']:.2f}% |")
    td = status["today"]
    L.append(f"| 오늘({td['date']}) 실현손익 | {won(td['realized_pnl'])} |")
    if td["target_amount"] is not None:
        L.append(f"| 오늘 목표 (+{td['target_pct']:g}%) | {td['target_amount']:,}원 → "
                 f"{'🎯 달성' if td['target_reached'] else '진행 ' + format(td['realized_pnl'] / td['target_amount'] * 100 if td['target_amount'] else 0, '.0f') + '%'} |")
    L.append(f"| 오늘 손실 한도 (−{td['loss_limit_pct']:g}%) | −{td['loss_limit_amount']:,}원 |")
    L.append(f"| 오늘 첫 매매 결과 | {td['first_trade_result'] or '아직 없음'} |")
    L.append(f"| 다음 매매 크기 | {td['next_size_mode']} (×{td['next_size_factor']:g}, 위험 {td['next_risk_won']:,}원) |")
    L.append(f"| 연속 손절 | {td['consecutive_losses']}회 |")
    L.append(f"| 오늘 신규 진입 | {'⛔ 금지 (' + td['reason'] + ')' if td['no_new_entries'] else ('🎯 목표 보호로 진입 안 함' if td['next_size_factor'] == 0 else '✅ 가능')} |\n")

    L.append("## 현재 포지션\n")
    if p:
        L.append("| 방향 | 셋업 | 등급 | 진입 시각 | 진입가 | 손절 | T1 | 수량(남은/처음) | 레버리지 | 미실현손익 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        stop_txt = f"{p['stop']:,}" + (" (본절)" if p["t1_done"] else "")
        L.append(f"| {p['side']} | {p['setup']} | {p['grade'] or '-'} | {p['entry_time'][5:16]} | {p['entry']:,} | "
                 f"{stop_txt} | {p['t1']:,}{' ✅' if p['t1_done'] else ''} | {p['qty_open']:.4f} / {p['qty']:.4f} BTC | "
                 f"{p['leverage']:.1f}배 | {won(p['unrealized_pnl'])} |")
        if p["t1_done"]:
            L.append(f"\nT1 반익절 완료(실현 {won(p['realized_part'])}). 남은 물량은 본절 손절"
                     + (f", 다음 스윙 {p['next_target']:,}" if p["next_target"] else "") + ".")
    else:
        L.append("보유 포지션 없음.")
    if status["pending_entry"]:
        q = status["pending_entry"]
        L.append(f"\n⏳ 진입 대기: {q['signal_time'][5:16]} 봉 마감 신호 → 다음 봉 시가에 **{q['side']} [{q['setup']}{q['grade']}]** "
                 f"진입 예정 (손절 {q['stop']:,} / T1 {q['t1']:,} / {q['qty']:.4f} BTC)")
    if status["pending_close"]:
        L.append(f"\n⏳ 청산 대기: 다음 봉 시가에 남은 물량 청산 ({status['pending_close']})")
    L.append("")

    L.append("## 최근 거래 20건 (최신순)\n")
    if trades:
        L.append("| # | 진입 | 방향 | 셋업 | 진입가 | 손절 | T1 | 수량 | 레버리지 | 청산 | 청산 시각 | 손익 | R |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k in range(len(trades) - 1, max(-1, len(trades) - 21), -1):
            t = trades[k]
            L.append(f"| {k + 1} | {t['entry_time']:%m-%d %H:%M} | {side_ko(t['side'])} | {setup_name(t['tag'], t['grade'])} | "
                     f"{t['entry']:,.0f} | {t['stop0']:,.0f} | {t['t1']:,.0f} | {t['qty']:.4f} | {t['lev']:.1f}배 | "
                     f"{'T1 반익절 → ' if t['t1Hit'] and t['exit_reason'] != 'T1 반익절' else ''}{t['exit_reason']} | "
                     f"{t['exit_time']:%m-%d %H:%M} | {won(t['pnl'])} | {t['R']:+.2f} |".replace("| -", "| −"))
    else:
        L.append("아직 청산된 거래가 없습니다.")
    L.append("")

    L.append("## 주간 손익 (청산 기준, 월요일 시작)\n")
    if trades:
        wk = defaultdict(list)
        for t in trades:
            wk[week_start(t["exit_time"].date())].append(t)
        L.append("| 주 시작 | 거래 수 | 승률 | 합계 R | 손익 |\n|---|---|---|---|---|")
        for k in sorted(wk, reverse=True):
            s = engine.stats(wk[k])
            L.append(f"| {k} | {s['N']} | {s['win']:.0f}% | {s['sumR']:+.2f} | {won(s['pnl'])} |")
        L.append("\n## 셋업별 성적\n")
        L.append("| 셋업 | 거래 수 | 승률 | 평균 R | 손익 |\n|---|---|---|---|---|")
        by = defaultdict(list)
        for t in trades:
            by[setup_name(t["tag"], t["grade"])].append(t)
        for k in sorted(by):
            s = engine.stats(by[k])
            L.append(f"| {k} | {s['N']} | {s['win']:.0f}% | {s['avgR']:+.3f} | {won(s['pnl'])} |")
    else:
        L.append("아직 청산된 거래가 없습니다.")
    if status["withdrawals"]:
        L.append("\n## 출금 내역 (장투 계좌로 이동)\n")
        L.append("| 시각 | 지난주 실현 수익 | 출금 | 출금 누적 | 출금 뒤 거래 시드 |\n|---|---|---|---|---|")
        for w in reversed(status["withdrawals"]):
            L.append(f"| {w['time'][:16]} | {won(w['week_pnl'])} | {w['amount']:,}원 | {w['total']:,}원 | {w['seed_after']:,}원 |")
    td = status["today"]
    L.append(f"\n---\n규칙: 1R = 거래 시드의 {status['risk_pct']:g}%(1시간 반대·B+·C {status['reduced_risk_pct']:g}%), "
             f"1회 위험 상한 5%, 최소 손절폭 0.40%, 최대 1.5%, 레버리지 상한 10배, 수수료 0.05%·슬리피지 1틱, "
             f"T1 반익절 후 본절, 하루 −{td['loss_limit_pct']:g}% 또는 2연속 손절 시 신규 금지"
             + (f", 하루 +{td['target_pct']:g}% 달성 후엔 목표가 남는 크기만" if td["target_pct"] else "")
             + (", 그날 첫 매매 수익이면 다음 매매 절반" if status.get("first_win_half") else "")
             + (f", 매주 월요일 지난주 수익의 {status['withdraw_pct']:g}% 출금" if status["withdraw_pct"] else "")
             + ". 자세한 거래 목록: [trades.csv](trades.csv), 기계용 상태: [status.json](status.json)")
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(ACCOUNT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

# ============================== 3) 알림 ==============================

def event_message(e):
    if e["type"] == "entry":
        icon = "🟢" if e["side"] == 1 else "🔵"
        return (f"{icon} {side_ko(e['side'])} 진입 [{setup_name(e['tag'], e['grade'])}] {e['price']:,.0f} / "
                f"손절 {e['stop']:,.0f} / T1 {e['t1']:,.0f} / {e['qty']:.4f} BTC ({e['lev']:.1f}배)")
    if e["type"] == "t1":
        return f"✅ T1 반익절 {won(e['part_pnl'])} ({e['price']:,.0f}, 남은 물량 손절은 진입가로)"
    if e["type"] == "exit":
        r = e["reason"]
        tot = e["total_pnl"]
        if r == "손절":
            return f"🔴 손절 {won(tot)} ({e['price']:,.0f})"
        icon = "✅" if tot > 0 else ("🔴" if tot < 0 else "⚪")
        part = f" / 이번 청산 {won(e['part_pnl'])}" if abs(e["part_pnl"] - tot) > 0.5 else ""
        return f"{icon} 청산({r}) 거래 합계 {won(tot)}{part} ({e['price']:,.0f})"
    if e["type"] == "daystop":
        return f"⛔ 오늘 신규 금지({e['reason']}, 오늘 실현 {won(e['day_pnl'])})"
    if e["type"] == "target":
        tail = "이후엔 목표가 남는 크기로만" if e.get("after", True) else "오늘은 더 진입하지 않음"
        return f"🎯 하루 목표 달성({won(e['day_pnl'])}) — {tail}"
    if e["type"] == "withdraw":
        msg = f"💸 출금 {e['amount']:,.0f}원(지난주 수익의 {e['pct']:g}%) — 출금 누적 {e['total']:,.0f}원"
        return msg + (" · 원금 회수 완료" if e.get("principal_done") else "")
    return str(e)


def send_ntfy(topic, msg):
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(f"{server}/{urllib.parse.quote(topic)}", data=msg.encode("utf-8"), method="POST",
                                 headers={"Title": "BTC paper bot (v1.4.1)", "Tags": "chart_with_upwards_trend"})
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def notify(events, last_bar, dry_run):
    """notified.json 에 없는 이벤트만 보낸다. 오래된 이벤트(봇이 오래 멈췄다 재개)는 한 줄 요약으로."""
    st = read_json(NOTIFIED, None)
    sent = list(st.get("sent", [])) if st else []
    sent_set = set(sent)
    new = [e for e in events if e["id"] not in sent_set]
    if not new:
        log("새 알림 없음")
        return
    guard = (last_bar - dt.timedelta(hours=3)) if last_bar else None
    recent = [e for e in new if guard is None or e["time"] >= guard]
    old = [e for e in new if e not in recent]
    msgs = [(e["id"], f"{e['time']:%m-%d %H:%M} " + event_message(e)) for e in recent]
    if old:
        msgs.insert(0, (None, f"ℹ️ 봇이 쉬는 동안 생긴 이벤트 {len(old)}건은 알림 생략 — ledger/ACCOUNT.md 확인"))
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    ok_ids = [e["id"] for e in old]
    for eid, m in msgs:
        if dry_run or not topic:
            log(f"[알림{'(dry-run, 안 보냄)' if dry_run else '(NTFY_TOPIC 없음, 안 보냄)'}] {m}")
            if eid:
                ok_ids.append(eid)
            continue
        try:
            send_ntfy(topic, m)
            log(f"[알림 보냄] {m}")
            if eid:
                ok_ids.append(eid)
        except Exception as ex:
            log(f"ntfy 전송 실패(다음 실행에서 다시 시도): {ex!r}", warn=True)
    if dry_run:
        log("dry-run: notified.json 은 바꾸지 않습니다.")
        return
    sent.extend(ok_ids)
    write_json(NOTIFIED, {"sent": sent[-1000:], "note": "이미 보낸(또는 생략한) 알림 이벤트 ID. 지우면 최근 3시간 이벤트가 다시 갈 수 있음"})

# ============================== 메인 ==============================

def save_log():
    os.makedirs(STATE_DIR, exist_ok=True)
    old = []
    if os.path.exists(BOT_LOG):
        with open(BOT_LOG, encoding="utf-8") as f:
            old = f.read().splitlines()
    lines = (old + _logs)[-500:]
    with open(BOT_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="BTC 5분봉 가상 매매 봇 (원칙 v1.4.1, 실제 주문 없음)")
    ap.add_argument("--dry-run", action="store_true", help="알림을 보내지 않고 화면에만 출력")
    a = ap.parse_args()

    now = dt.datetime.now(KST)
    end = floor5(now - dt.timedelta(seconds=3))      # end 이전에 시작한 봉 = 닫힌 봉
    log(f"===== 실행 시작 {'(dry-run)' if a.dry_run else ''} =====")

    cfg = read_json(CONFIG, {})
    if not cfg.get("start_time"):
        cfg["start_time"] = end.strftime("%Y-%m-%d %H:%M")
        write_json(CONFIG, cfg)
        log(f"첫 실행: config.json start_time = {cfg['start_time']} (이 시각에 시작하는 봉부터 가상 매매)")
    start_time = engine.parse_kst(cfg["start_time"])
    P = engine.load_params(cfg)

    candles, used, primary = update_candles(cfg, start_time, end)
    if not candles:
        log("저장된 봉이 하나도 없어 계산할 수 없습니다.", warn=True)
        save_log()
        return 1
    if used:
        save_candles(candles)
    first = min(candles)
    if first > start_time - dt.timedelta(days=1):
        log(f"워밍업 데이터가 1일 미만입니다(첫 봉 {first:%m-%d %H:%M}). 초반 지표가 부정확할 수 있습니다.", warn=True)

    ts = sorted(candles)
    D = engine.Data(ts, [candles[t][0] for t in ts], [candles[t][1] for t in ts], [candles[t][2] for t in ts],
                    [candles[t][3] for t in ts], [candles[t][4] for t in ts])
    if D.gaps:
        log(f"5분 간격이 아닌 곳 {D.gaps}개(거래소 데이터 공백). 그대로 계산합니다.")
    R = engine.run(D, P, start_time=start_time, close_at_end=False)
    R["last_bar_time"] = ts[-1].strftime(TFMT)
    log(f"재계산: 봉 {D.n}개(매매 대상 {len(R['curve'])}개), 거래 {len(R['trades'])}건, "
        f"포지션 {'있음' if R['position'] else '없음'}, 실현 {won(R['realized'])}")

    prev = read_json(STATUS, None)
    status = build_status(cfg, P, R, start_time, primary, used, dt.datetime.now(KST))
    write_trades(R["trades"])
    write_json(STATUS, status)
    write_account(status, R)
    log("장부 저장: ledger/trades.csv, ledger/status.json, ledger/ACCOUNT.md")

    if prev and prev.get("has_position") != status["has_position"]:
        log(f"포지션 변화: {'없음 → 있음' if status['has_position'] else '있음 → 없음'}")
    notify(R["events"], ts[-1], a.dry_run)
    save_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
