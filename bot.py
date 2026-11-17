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
v2.0 기반 시설 이식 (2026-11-17, 사전 등록서 v3 전진 시험용) — 기본 설정이면 기존과 완전히 같은 장부
  - 여러 코인: 코인별 5분봉 data/candles_{코인}_5m.csv (BTC 는 기존 data/candles_5m.csv 그대로)
  - 그림자 장부: config.json "shadow_ledgers" (기본 없음/꺼짐). 본 장부와 별도 규칙·별도 파일 ledger/shadow/{이름}/
  - 매매 일지: ledger/journal.csv (본 장부), ledger/shadow/{이름}/journal.csv — 거래마다 진입 근거·결과
  - 경제 일정: data/events.csv 읽기. 진입 금지 규칙은 eventBlock=true 일 때만(기본 꺼짐)
v2.1 1계좌 · 여러 코인 · 동시 여러 포지션 (2026-11-17) — 본 장부는 engine.run_multi 로 계산
  - config "markets"·"maxOpenPositions"·"maxOpenRiskPct" (rule_history 로 활성화 시각 이후에만 적용)
  - markets=[KRW-BTC], maxOpenPositions=1 이면 기존 run() 과 같은 장부(research/checks/c_multipos_equivalence.py)
  - trades.csv·journal.csv 에 '코인' 열, status.json 에 positions·pending_entries 등, 알림 앞에 [코인]
v2.2 자동 복기 · Gemini 이벤트 회피 (2026-11-17) — 기본 설정(eventBlock=false)이면 장부 계산은 기존과 1원도 같음
  - (A) tools/review.py: 청산 거래마다 보유봉수·MFE_R·MAE_R·원인 태그 → journal.csv 끝 열(본 장부는 Gemini 복기해설 열도,
        state/reviews.json 캐시). KST 날짜가 바뀐 뒤 첫 실행에 ledger/review/전날.md + ntfy 요약 1회(ID R{날짜})
  - (B) tools/gemini_events.py: GEMINI_API_KEY 가 있으면 정기 일정(6시간)·돌발(30분)을 data/events.csv 에 추가.
        eventBlock=true 일 때만 high 일정 창 동안 그 코인 신규 진입 금지(engine), status.json event_block·upcoming_events,
        새 창 ntfy "⛔ 이벤트 회피" 1회. 막힌 신호는 ledger/blocked_signals.csv·status.json event_blocked_signals.
  - 복기·이벤트 기능의 어떤 오류도 경고 로그만 남기고 봇은 계속 실행.
v2.3 이벤트 감지 A안 · 상관 묶음 · v4 30m 짝 그림자 · 손절 분해 (2026-11-17) — 본 장부·기존 그림자 매매 규칙은 그대로
  - eventSource "feeds"(기본): tools/event_feeds.py 경제 캘린더 JSON(6시간)·코인 뉴스 RSS(30분) → data/events.csv,
    state/event_feed.json, status.json event_data(OK/STALE/UNKNOWN). "gemini" 면 예전 Gemini 수집. Gemini 는 복기 해설만,
    429·할당량 오류면 하루 쉼.
  - tools/extras.py: 일지에 신호ID·묶음ID·묶음크기·손익 분해(계획/갭/슬리피지/수수료 R) 열, status 에 독립 묶음 수·손절 분해 평균
  - 그림자 장부 timeframe(기본 5) — 30 이면 5분봉을 30분봉으로 묶어(완성된 봉만) 실행. pair_group 이 같은 두 장부는
    research/v4/pairs.csv 를 매 실행 다시 쓴다(tools/v4pairs.py, 국면 라벨 tools/regime.py).
표준 라이브러리만 사용.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
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
JOURNAL = os.path.join(LEDGER_DIR, "journal.csv")
SHADOW_DIR = os.path.join(LEDGER_DIR, "shadow")
EVENTS_DEFAULT = os.path.join(DATA_DIR, "events.csv")
REVIEW_DIR = os.path.join(LEDGER_DIR, "review")              # 하루 복기 리포트 YYYY-MM-DD.md
BLOCKED_CSV = os.path.join(LEDGER_DIR, "blocked_signals.csv")  # 이벤트 회피로 막힌 신호(있을 때만)
REVIEWS_STATE = os.path.join(STATE_DIR, "reviews.json")       # Gemini 복기 해설 캐시
GEM_EVENTS_STATE = os.path.join(STATE_DIR, "gemini_events.json")
EVENT_FEED_STATE = os.path.join(STATE_DIR, "event_feed.json")     # 이벤트 피드(캘린더·RSS) 수집 상태
PAIRS_CSV = os.path.join(HERE, "research", "v4", "pairs.csv")      # v4 30m 짝 비교(봇이 매 실행 다시 씀)
DAILY_CSV = os.path.join(DATA_DIR, "btc_daily.csv")                # 국면 라벨용 BTC 일봉(업비트 공개 API)
POWER_PRIOR = os.path.join(HERE, "research", "v4", "power_prior.json")   # 개발 구간 사전 검정력(필요 쌍 수)
NEWS_FILTER_PAIRS = os.path.join(HERE, "research", "news", "filter_pairs.csv")   # news_v1 vs baseline 같은 신호 ID 비교
NEWS_BUDGET_SEC = 30.0       # 실행당 뉴스 관련(event_feeds + news_v1) 총 시간 상한


def news_paths():
    from tools import news_v1 as NV
    return NV.paths_for(HERE)


def news_v1_enabled(cfg):
    return bool((cfg.get("newsV1") or {}).get("enabled"))

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


def candle_path(market):
    """코인별 5분봉 파일. KRW-BTC 는 기존 data/candles_5m.csv (결정적 재계산 유지)"""
    coin = market.split("-")[-1]
    return CANDLES if coin == "BTC" else os.path.join(DATA_DIR, f"candles_{coin}_5m.csv")


def load_candles(path=None):
    """기존 CSV → {시각: [o,h,l,c,v,source]}"""
    path = path or CANDLES
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = engine.parse_kst(r["time_kst"])
            out[t] = [float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                      float(r["volume"]), r.get("source") or ""]
    return out


def _num(x):
    """가격: 정수면 정수로, 아니면 그대로 (CSV 를 다시 읽어도 값이 바뀌지 않게)"""
    return str(int(x)) if x == int(x) else repr(x)


def save_candles(c, path=None):
    path = path or CANDLES
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_kst", "open", "high", "low", "close", "volume", "source"])
        for t in sorted(c):
            o, h, l, cl, v, s = c[t]
            w.writerow([t.strftime(TFMT), _num(o), _num(h), _num(l), _num(cl), repr(v), s])
    os.replace(tmp, path)


def update_candles(cfg, start_time, end, market=None, path=None):
    """새로 닫힌 봉을 받아 붙인다. 반환: (candles dict, 이번에 쓴 출처, 주 출처)
    market·path 를 주면 그 코인 파일을 갱신(그림자 장부용). 기본은 본 장부(cfg market, data/candles_5m.csv)."""
    candles = load_candles(path)
    src_state = read_json(SOURCE_STATE, {}) or {}
    primary = src_state.get("primary")
    market = market or cfg.get("market", "KRW-BTC")
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


def coin_tr(t):
    """거래·포지션의 코인 이름 (run_multi 는 'coin' 을 넣음, 없으면 BTC)"""
    return t.get("coin") or "BTC"


def _cp(x, coin):
    """trades.csv 가격: BTC·ETH(100만 원 이상)는 기존과 같은 정수, 그 아래(SOL·XRP)는 유효숫자 10자리"""
    return f"{x:.0f}" if coin == "BTC" or abs(x) >= 1_000_000 else f"{x:.10g}"


def _r(x):
    """status.json 가격: 10만 원 이상은 정수(기존과 같음), 그 아래(XRP 등)는 소수 넷째 자리"""
    return round(x) if abs(x) >= 100_000 else round(x, 4)


def write_trades(trades):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(TRADES, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        # '코인' 열은 2026-11-17 다중 코인 확장 때 번호 다음에 추가. '수량BTC' 열 이름은 웹사이트 호환을 위해 그대로 두고
        # 그 코인의 수량을 적는다(ETH 거래면 ETH 수량).
        w.writerow(["번호", "코인", "거래ID", "진입시각", "방향", "셋업", "등급", "진입가", "최초손절", "T1", "수량BTC",
                    "레버리지", "위험금액", "1시간방향", "청산내역", "마지막청산", "청산시각", "손익원", "R",
                    "수량배율"])          # 수량배율: 1 / 0.5(그날 첫 매매 수익 뒤) / 목표후(목표가 남는 크기) — 기존 열 순서 유지, 끝에 추가
        for k, t in enumerate(trades, 1):
            cn = coin_tr(t)
            w.writerow([k, cn, t["id"], t["entry_time"].strftime("%Y-%m-%d %H:%M"), side_ko(t["side"]), t["tag"],
                        t["grade"], _cp(t["entry"], cn), _cp(t["stop0"], cn), _cp(t["t1"], cn), f"{t['qty']:.4f}",
                        f"{t['lev']:.2f}", f"{t['rm']:.0f}", t["dir60"], " | ".join(t["exits"]), t["exit_reason"],
                        t["exit_time"].strftime("%Y-%m-%d %H:%M"), f"{t['pnl']:.0f}", f"{t['R']:.3f}",
                        t.get("sizeTag", "1")])


DIR_KO = {1: "상승", -1: "하락", 0: "중립", None: "-"}


def _fp(x):
    """가격 표시: 큰 값은 정수, 작은 값(알트코인)은 유효숫자 8자리"""
    if x is None:
        return ""
    return f"{x:.0f}" if abs(x) >= 1000 else f"{x:.8g}"


def _mp(x):
    """사람용 가격 표시(ACCOUNT.md·알림): 1,000 이상은 쉼표 정수, 그 아래는 유효숫자 6자리"""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.6g}"


def result_ko(t):
    if t["pnl"] > 0:
        return "익절"
    return "본절" if t.get("t1Hit") else "손절"


def near_events(t, events, hours=2):
    """진입 시각 앞뒤 hours 시간 안의 경제 일정 이름 (일지 참고용)"""
    if not events:
        return ""
    a, b = t["entry_time"] - dt.timedelta(hours=hours), t["entry_time"] + dt.timedelta(hours=hours)
    return " / ".join(f"{e[0]:%m-%d %H:%M} {e[1]}" for e in events if a <= e[0] <= b)


REVIEW_COLS = ["보유봉수", "MFE_R", "MAE_R", "원인태그"]


def write_journal(trades, path, coin_of=None, events=None, review=False, texts=None, extras=None, bar_min=5):
    """매매 일지: 거래마다 진입 근거(셋업·구간·보조 근거·상위봉 방향·손절폭·T1 배수)와 결과.
    review=True 면 끝에 자동 복기 열(보유봉수, MFE_R, MAE_R, 원인태그)을, texts(dict 거래ID→해설)가 있으면 '복기해설' 열도
    덧붙인다. extras(기본 = review 와 같음)면 그 뒤에 신호ID·묶음ID·묶음크기·손익 분해 열(tools/extras.py).
    review=False(기본)면 기존 형식 그대로."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with_text = texts is not None
    extras = review if extras is None else extras
    if review:
        try:
            from tools.review import journal_cols
        except Exception as e:
            log(f"복기 모듈을 못 읽어 일지에 복기 열을 뺍니다: {e!r}", warn=True)
            review = False
    xcols = []
    if extras:
        try:
            from tools.extras import DECOMP_COLS, journal_cols as x_cols
            xcols = DECOMP_COLS
        except Exception as e:
            log(f"부가 계산 모듈을 못 읽어 일지에 묶음·분해 열을 뺍니다: {e!r}", warn=True)
            extras = False
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "코인", "거래ID", "진입시각", "청산시각", "방향", "셋업", "등급", "진입 구간 근거", "보조 근거",
                    "15분 방향", "1시간 방향", "손절폭%", "T1까지(R)", "진입가", "최초손절", "T1", "결과", "마지막청산",
                    "청산내역", "손익원", "R", "진입 앞뒤 2시간 경제 일정"]
                   + ((REVIEW_COLS + (["복기해설"] if with_text else [])) if review else []) + xcols)
        for k, t in enumerate(trades, 1):
            why = t.get("why") or {}
            extra = []
            if review:
                try:
                    extra = journal_cols(t, with_text, texts)
                except Exception:
                    extra = [""] * (len(REVIEW_COLS) + (1 if with_text else 0))
            if extras:
                try:
                    extra = extra + x_cols(t, bar_min)
                except Exception:
                    extra = extra + [""] * len(xcols)
            w.writerow([k, coin_of(t) if coin_of else "BTC", t["id"], t["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        t["exit_time"].strftime("%Y-%m-%d %H:%M"), side_ko(t["side"]), t["tag"], t["grade"],
                        " + ".join(why.get("zone", [])), " + ".join(why.get("support", [])),
                        DIR_KO.get(why.get("dir15")), DIR_KO.get(why.get("dir60", t.get("dir60"))),
                        why.get("stop_pct", ""), why.get("t1_R", ""), _fp(t["entry"]), _fp(t["stop0"]), _fp(t["t1"]),
                        result_ko(t), t["exit_reason"], " | ".join(t["exits"]), f"{t['pnl']:.0f}", f"{t['R']:.3f}",
                        near_events(t, events)] + extra)


def attach_extras(trades, datas, bar_min=5, opened=None, fee=0.0005):
    """(2) 상관 신호 묶음·(4) 손익 분해를 거래 dict 에 붙인다(cluster, decomp). 계산 결과는 바꾸지 않음. 실패는 경고만.
    opened: 보유 포지션·진입 대기 목록 — 묶음 ID 가 나중에 바뀌지 않게 함께 묶는다(일지에는 청산 거래만)."""
    try:
        from tools import extras as X
        X.assign_clusters(list(trades) + list(opened or []), bar_min)
        X.attach_decomp(trades, datas, fee, log)
    except Exception as e:
        log(f"묶음·손익 분해 계산 실패(장부는 정상): {e!r}", warn=True)


def extras_fields(trades):
    """status.json 에 넣을 독립 묶음 수·손절 분해 평균(정보가 없으면 빈 dict — 기존 형식 그대로)"""
    if not any("cluster" in t or "decomp" in t for t in trades):
        return {}
    try:
        from tools import extras as X
        sd = X.stop_decomp_summary(trades)
        return dict(independent_clusters=X.independent_count(trades),
                    stop_decomp=None if sd is None else {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sd.items()},
                    r_definition="R = 거래 손익(수수료 포함) ÷ (수량 × |진입 체결가 − 최초 손절가|)")
    except Exception as e:
        log(f"묶음·분해 요약 실패(무시): {e!r}", warn=True)
        return {}


def event_data_fields(cfg, now, R=None, events=None):
    """status.json event_data: 이벤트 데이터 수집 상태(OK/STALE/UNKNOWN)와 다가오는 일정(high·medium, 7일 안 8개).
    일정 0건을 '이벤트 없음'으로 표시하지 않도록 상태를 그대로 싣는다. 실패해도 UNKNOWN 기본값."""
    src = cfg.get("eventSource", "feeds")
    out = dict(source=src, schedule="UNKNOWN", breaking="UNKNOWN", last_success_schedule=None,
               last_success_breaking=None, upcoming=[])
    try:
        from tools import event_feeds as ef
        st = read_json(GEM_EVENTS_STATE if src == "gemini" else EVENT_FEED_STATE, {}) or {}
        out.update(ef.data_status(st, now, cfg))
        if events is None:
            try:
                events = engine.load_events(os.path.join(HERE, cfg["events_file"]) if cfg.get("events_file")
                                            else EVENTS_DEFAULT)
            except Exception:
                events = []
        up = []
        for e in events or []:
            imp = (e[2] or "").lower()
            if imp not in ("high", "medium") or e[0] < now - dt.timedelta(hours=1) or e[0] > now + dt.timedelta(days=7):
                continue
            up.append(dict(time=e[0].strftime(TFMT), name=e[1], importance=imp,
                           coins="ALL" if e[3] is None else "|".join(sorted(e[3])), source=e[7] or None))
        out["upcoming"] = sorted(up, key=lambda x: x["time"])[:8]
    except Exception as e:
        log(f"이벤트 데이터 상태 계산 실패(UNKNOWN 으로 둠): {e!r}", warn=True)
    return dict(event_data=out)


def write_blocked(blocked, path):
    """이벤트 회피로 막힌 신호 목록(있을 때만 파일을 만든다 — 기본 설정이면 파일 없음)"""
    if not blocked and not os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["신호봉", "진입 예정 시각", "코인", "방향", "셋업", "등급", "막힌 이유"])
        for b in blocked:
            w.writerow([b["time"].strftime("%Y-%m-%d %H:%M"), b["fill_time"].strftime("%Y-%m-%d %H:%M"), b["coin"],
                        side_ko(b["side"]), b["tag"], b["grade"], b["reason"]])


def _coins_txt(cs):
    return "ALL" if cs is None else "·".join(c for c in engine.COIN_PRIORITY if c in cs) or "·".join(sorted(cs))


def event_fields(P, R, events, now):
    """status.json 에 넣을 이벤트 회피 상태. 실패해도 빈 기본값.
    event_block: {enabled, active, name, coins, start, until} — 지금(마지막 봉 마감 = 다음 진입 체결 시각) 기준
    upcoming_events: 끝나지 않은 다가오는 high 일정 5개, event_blocked_signals: 막힌 신호 최근 10개"""
    out = dict(event_block=dict(enabled=False, active=False, name=None, coins=[], start=None, until=None),
               upcoming_events=[], event_blocked_signals=[])
    try:
        last = engine.parse_kst(R["last_bar_time"]) if R.get("last_bar_time") else now
        t_ref = last + BAR
        Pn = engine.params_at(P, last)
        out["event_block"]["enabled"] = bool(Pn.get("eventBlock"))
        hits = {}
        for m in Pn["markets"]:
            cn = engine.coin_of(m)
            for a, b, added, nm in engine.event_windows(dict(Pn, eventBlock=True), events, cn):
                if a <= t_ref < b and (added is None or added <= t_ref):
                    h = hits.setdefault(nm, dict(start=a, until=b, coins=[]))
                    h["coins"].append(cn); h["until"] = max(h["until"], b)
        if hits and Pn.get("eventBlock"):
            nm, h = max(hits.items(), key=lambda x: x[1]["until"])
            allc = sorted({c for v in hits.values() for c in v["coins"]},
                          key=lambda c: engine.COIN_PRIORITY.index(c) if c in engine.COIN_PRIORITY else 9)
            out["event_block"].update(active=True, name=nm if len(hits) == 1 else f"{nm} 외 {len(hits) - 1}건",
                                      coins=allc, start=h["start"].strftime(TFMT), until=h["until"].strftime(TFMT))
        up = []
        for e in events or []:
            if (e[2] or "").lower() != "high":
                continue
            b4 = e[4] if e[4] is not None else Pn["eventBeforeMin"]
            af = e[5] if e[5] is not None else Pn["eventAfterMin"]
            a, b = e[0] - dt.timedelta(minutes=b4), e[0] + dt.timedelta(minutes=af)
            if b <= t_ref:
                continue
            up.append(dict(time=e[0].strftime(TFMT), name=e[1], coins="ALL" if e[3] is None else "|".join(sorted(e[3])),
                           window_start=a.strftime(TFMT), window_end=b.strftime(TFMT),
                           added_at=e[6].strftime(TFMT) if e[6] else None, source=e[7] or None))
        out["upcoming_events"] = sorted(up, key=lambda x: x["window_start"])[:5]
        out["event_blocked_signals"] = [dict(time=b["time"].strftime(TFMT), coin=b["coin"], side=side_ko(b["side"]),
                                             setup=b["tag"] + b["grade"], reason=b["reason"])
                                        for b in (R.get("blocked") or [])[-10:]]
    except Exception as e:
        log(f"이벤트 상태 계산 실패(무시): {e!r}", warn=True)
    return out


def window_events(P, events, now, last_bar):
    """새 차단 창 알림 이벤트(eventBlock 이 켜져 있을 때만): 끝나지 않았고 24시간 안에 시작하는 high 창마다 1회."""
    out = []
    try:
        Pn = engine.params_at(P, last_bar)
        if not Pn.get("eventBlock"):
            return out
        import hashlib
        act = {engine.coin_of(m) for m in Pn["markets"]}
        for e in events or []:
            if (e[2] or "").lower() != "high" or (e[3] is not None and not (e[3] & act)):
                continue
            b4 = e[4] if e[4] is not None else Pn["eventBeforeMin"]
            af = e[5] if e[5] is not None else Pn["eventAfterMin"]
            a, b = e[0] - dt.timedelta(minutes=b4), e[0] + dt.timedelta(minutes=af)
            if b <= now or a > now + dt.timedelta(hours=24):
                continue
            key = f"{e[0]:%Y%m%d%H%M}|{e[1]}|{_coins_txt(e[3])}"
            eid = "V" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
            out.append(dict(type="evwin", id=eid, time=last_bar,
                            msg=f"⛔ 이벤트 회피: {e[1]} {_coins_txt(e[3])} {a:%m-%d %H:%M}~{b:%H:%M}"
                                + (f"({b:%m-%d})" if b.date() != a.date() else "")))
    except Exception as ex:
        log(f"이벤트 창 알림 준비 실패(무시): {ex!r}", warn=True)
    return out


def position_dict(pos, last_close, fee, multi=False):
    """multi=True(본 장부, run_multi): market·coin 을 넣고 가격은 _r(10만 원 미만 코인도 소수까지).
    multi=False(그림자 장부): 기존 형식 그대로."""
    if pos is None:
        return None
    unreal = pos["side"] * (last_close - pos["entry"]) * pos["open"] - pos["entry"] * pos["open"] * fee
    if multi:
        return dict(
            market=pos.get("market", "KRW-BTC"), coin=coin_tr(pos),
            id=pos["id"], side=side_ko(pos["side"]), setup=pos["tag"], grade=pos["grade"],
            entry_time=pos["entry_time"].strftime(TFMT), entry=_r(pos["entry"]),
            stop=_r(pos["stop"]), stop0=_r(pos["stop0"]), t1=_r(pos["t1"]),
            next_target=_r(pos["nxt"]) if pos.get("nxt") else None,
            qty=pos["qty"], qty_open=pos["open"], leverage=round(pos["lev"], 2), risk_money=round(pos["rm"]),
            t1_done=pos["t1Hit"], realized_part=round(pos["pnl"]), last_price=_r(last_close),
            unrealized_pnl=round(unreal),
            note="미실현손익 = (현재가−진입가)×남은 수량 − 진입 수수료 (청산 수수료 미포함)",
        )
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


def pending_dict(pend):
    if pend is None:
        return None
    return dict(
        side=side_ko(pend["side"]), setup=pend["tag"], grade=pend["grade"],
        signal_time=pend["signal_time"].strftime(TFMT), signal_close=_r(pend["sigClose"]),
        stop=_r(pend["stop"]), t1=_r(pend["t1"]), qty=pend["qty"],
        note="다음 봉 시가에 진입 예정")


def multi_fields(P, R):
    """다중 코인·다중 포지션 필드 (status.json 에 추가, 기존 필드는 그대로)"""
    if "positions" not in R:          # run() 결과(다중 아님)
        return {}
    last_t = engine.parse_kst(R["last_bar_time"]) if R.get("last_bar_time") else None
    Pn = engine.params_at(P, last_t) if last_t else P
    lc = R["last_closes"]
    poss = []
    for p in R["positions"]:
        d = position_dict(p, lc.get(coin_tr(p), p["entry"]), P["fee"], multi=True)
        d["pending_close"] = R["pending_closes"].get(coin_tr(p))
        poss.append(d)
    pends = []
    for q in R["pendings"]:
        d = dict(market=q.get("market", "KRW-BTC"), coin=coin_tr(q), **pending_dict(q))
        d.update(risk_money=round(q["rm"]), risk_cut=bool(q.get("riskCut")))
        pends.append(d)
    open_risk = sum(p["rm"] for p in R["positions"]) + sum(q["rm"] for q in R["pendings"])
    seed = R["seed"]
    return dict(
        markets=[engine.coin_of(m) for m in Pn["markets"]],             # 지금(마지막 봉) 매매 대상 코인
        max_open_positions=Pn["maxOpenPositions"],
        max_open_risk_pct=Pn["maxOpenRiskPct"],
        open_positions_count=len(poss) + len(pends),                     # 보유 + 진입 대기
        open_risk_won=round(open_risk),
        open_risk_pct=round(open_risk / seed * 100, 3) if seed > 0 else 0.0,   # 열린 포지션(대기 포함) 최초 위험 합계 ÷ 시드
        positions=poss,                                                  # 진입 순
        pending_entries=pends,
        last_prices={k: _r(v) for k, v in lc.items()},
        last_bar_times={k: v.strftime(TFMT) for k, v in R["last_bar_times"].items()},
    )


def build_status(cfg, P, R, start_time, primary, used, now):
    trades = R["trades"]
    st = engine.stats(trades, R["curve"])
    day = R["day"]
    pend = R["pending"]
    eq0 = P["capital"]
    wd_total = R["withdrawn"]
    multi = "positions" in R
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
        # 호환 필드: 포지션이 여럿이면 가장 먼저 진입한 것(진입 대기도 첫 번째). 전체는 positions·pending_entries
        position=(position_dict(R["position"], R["last_closes"].get(coin_tr(R["position"]), R["last_close"]),
                                P["fee"], multi=True) if multi and R["position"]
                  else position_dict(R["position"], R["last_close"], P["fee"])),
        pending_entry=(dict(market=pend.get("market", "KRW-BTC"), coin=coin_tr(pend), **pending_dict(pend))
                       if multi and pend else pending_dict(pend)),
        pending_close=R["pending_close"],
        today=dict(date=str(day["date"]) if day["date"] else None, realized_pnl=round(day["realized"]),
                   consecutive_losses=day["consec"], no_new_entries=bool(day["stop"]), reason=day["reason"],
                   start_equity=round(day["start_equity"]),
                   target_pct=P["dayTargetP"],
                   target_amount=round(day["target_amount"]) if day["target_amount"] is not None else None,
                   target_reached=bool(day["target_reached"]),
                   loss_limit_pct=P["dayLossP"] if P["dayLossOn"] else None, max_consec=P["maxConsec"],
                   loss_limit_on=bool(P["dayLossOn"]),
                   loss_limit_amount=round(day["loss_limit_amount"]) if P["dayLossOn"] else None,   # 양수 금액. 실현손익이 −이 값 이하면 신규 금지
                   first_trade_result=day["first_trade_result"],         # 수익 / 손실 / 본전 / None(아직 없음)
                   next_size_factor=round(day["next_size_factor"], 3),   # 1 / 0.5 / 목표 후 축소 비율 / 0(진입 안 함)
                   next_size_mode=day["next_size_mode"],
                   next_risk_won=round(day["next_risk"] or 0)),
        last_bar_time=R["last_bar_time"], last_price=round(R["last_close"]) if R["last_close"] else None,
        data_source=primary, data_source_this_run=used,
        updated_at=now.strftime(TFMT),
        warnings=_warnings[:],
        **multi_fields(P, R),
        **extras_fields(trades),
    )


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def write_account(status, R):
    trades = R["trades"]
    p = status["position"]
    L = []
    mk = status.get("markets") or ["BTC"]
    L.append(f"# {'·'.join(mk)} 5분봉 가상 매매 계좌 (원칙 v1.4.1 + 쉽알남 자금 관리)\n")
    np_ = len(status.get("positions") or ([p] if p else []))
    L.append(f"## 포지션: {('있음 ' + str(np_) + '개' if np_ > 1 else '있음') if p else '없음'}\n")
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
    if td.get("loss_limit_pct") is not None and td.get("loss_limit_amount") is not None:
        L.append(f"| 오늘 손실 한도 (−{td['loss_limit_pct']:g}%) | −{td['loss_limit_amount']:,}원 |")
    else:
        L.append("| 오늘 손실 한도 | 없음 (2026-11-18 08:45부터 해제) |")
    L.append(f"| 오늘 첫 매매 결과 | {td['first_trade_result'] or '아직 없음'} |")
    L.append(f"| 다음 매매 크기 | {td['next_size_mode']} (×{td['next_size_factor']:g}, 위험 {td['next_risk_won']:,}원) |")
    L.append(f"| 연속 손절 | {td['consecutive_losses']}회 |")
    L.append(f"| 오늘 신규 진입 | {'⛔ 금지 (' + td['reason'] + ')' if td['no_new_entries'] else ('🎯 목표 보호로 진입 안 함' if td['next_size_factor'] == 0 else '✅ 가능')} |\n")

    poss = status.get("positions")
    if poss is None:                                   # 다중 필드가 없는 경우(기존 형식)
        poss = [dict(p, coin="BTC")] if p else []
    pends = status.get("pending_entries")
    if pends is None:
        pends = [dict(status["pending_entry"], coin="BTC")] if status["pending_entry"] else []
    if "max_open_positions" in status:
        L.append(f"## 현재 포지션 ({len(poss)}개 보유 · 대기 {len(pends)}개 / 동시 최대 {status['max_open_positions']}개, "
                 f"열린 위험 {status['open_risk_won']:,}원 = 시드의 {status['open_risk_pct']:.2f}%"
                 + (f" / 상한 {status['max_open_risk_pct']:g}%" if status["max_open_risk_pct"] else "")
                 + f" · 대상 코인 {', '.join(status['markets'])})\n")
    else:
        L.append("## 현재 포지션\n")
    if poss:
        L.append("| 코인 | 방향 | 셋업 | 등급 | 진입 시각 | 진입가 | 손절 | T1 | 수량(남은/처음) | 레버리지 | 위험 | 미실현손익 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for q in poss:
            stop_txt = f"{q['stop']:,}" + (" (본절)" if q["t1_done"] else "")
            L.append(f"| {q['coin']} | {q['side']} | {q['setup']} | {q['grade'] or '-'} | {q['entry_time'][5:16]} | {q['entry']:,} | "
                     f"{stop_txt} | {q['t1']:,}{' ✅' if q['t1_done'] else ''} | {q['qty_open']:.4f} / {q['qty']:.4f} {q['coin']} | "
                     f"{q['leverage']:.1f}배 | {q['risk_money']:,}원 | {won(q['unrealized_pnl'])} |")
        for q in poss:
            if q["t1_done"]:
                L.append(f"\n[{q['coin']}] T1 반익절 완료(실현 {won(q['realized_part'])}). 남은 물량은 본절 손절"
                         + (f", 다음 스윙 {q['next_target']:,}" if q["next_target"] else "") + ".")
            pc = q.get("pending_close") if "pending_close" in q else status["pending_close"]
            if pc:
                L.append(f"\n⏳ [{q['coin']}] 청산 대기: 다음 봉 시가에 남은 물량 청산 ({pc})")
    else:
        L.append("보유 포지션 없음.")
    for q in pends:
        L.append(f"\n⏳ [{q['coin']}] 진입 대기: {q['signal_time'][5:16]} 봉 마감 신호 → 다음 봉 시가에 **{q['side']} [{q['setup']}{q['grade']}]** "
                 f"진입 예정 (손절 {q['stop']:,} / T1 {q['t1']:,} / {q['qty']:.4f} {q['coin']}"
                 + (", 위험 여유만큼 축소" if q.get("risk_cut") else "") + ")")
    L.append("")

    L.append("## 최근 거래 20건 (최신순)\n")
    if trades:
        L.append("| # | 코인 | 진입 | 방향 | 셋업 | 진입가 | 손절 | T1 | 수량 | 레버리지 | 청산 | 청산 시각 | 손익 | R |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k in range(len(trades) - 1, max(-1, len(trades) - 21), -1):
            t = trades[k]
            L.append(f"| {k + 1} | {coin_tr(t)} | {t['entry_time']:%m-%d %H:%M} | {side_ko(t['side'])} | {setup_name(t['tag'], t['grade'])} | "
                     f"{_mp(t['entry'])} | {_mp(t['stop0'])} | {_mp(t['t1'])} | {t['qty']:.4f} | {t['lev']:.1f}배 | "
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
        byc = defaultdict(list)
        for t in trades:
            byc[coin_tr(t)].append(t)
        if len(byc) > 1 or "markets" in status and len(status["markets"]) > 1:
            L.append("\n## 코인별 성적\n")
            L.append("| 코인 | 거래 수 | 승률 | 평균 R | 손익 |\n|---|---|---|---|---|")
            for k in engine.market_order(byc):
                s = engine.stats(byc[k])
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
             + (f"T1 반익절 후 본절, 하루 −{td['loss_limit_pct']:g}% 또는 {td.get('max_consec', 2)}연속 손절 시 신규 금지"
                if td.get("loss_limit_pct") is not None
                else f"T1 반익절 후 본절, 하루 손실 한도 없음, {td.get('max_consec', 2)}연속 손절 시 신규 금지")
             + (f", 하루 +{td['target_pct']:g}% 달성 후엔 목표가 남는 크기만" if td["target_pct"] else "")
             + (", 그날 첫 매매 수익이면 다음 매매 절반" if status.get("first_win_half") else "")
             + (f", 매주 월요일 지난주 수익의 {status['withdraw_pct']:g}% 출금" if status["withdraw_pct"] else "")
             + (f". 1계좌 {len(mk)}코인({', '.join(mk)}) 각자 차트 기준 신호, 코인당 1포지션·동시 최대 {status['max_open_positions']}개, "
                f"열린 위험 합계 ≤ 시드 {status['max_open_risk_pct']:g}%(넘으면 여유만큼 축소, 1R의 25% 미만이면 안 함), "
                f"명목가 합계 ≤ 평가 잔고×10, 같은 봉 신호 우선순위 A > B+ > B > C · BTC > ETH > SOL > XRP, "
                f"자금 관리는 계좌 단위"
                if len(mk) > 1 or status.get("max_open_positions", 1) > 1 else "")
             + ". 자세한 거래 목록: [trades.csv](trades.csv), 기계용 상태: [status.json](status.json)")
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(ACCOUNT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

# ============================== 2-1) 그림자 장부 ==============================
# config.json 예:
#   "shadow_ledgers": [{"name": "v141_4coin", "enabled": true, "markets": ["KRW-BTC", "KRW-ETH"],
#                        "start_time": null, "initial_capital": 1000000,
#                        "params": {"slipMode": "tick", "dayTargetP": 0, "firstWinHalf": false, "withdrawPct": 0}}]
# - 본 장부와 완전히 별도: 별도 규칙(params 로 본 설정을 덮어씀), 별도 파일(ledger/shadow/{name}/), 알림 없음.
# - 코인마다 독립 계좌(코인당 포지션 1개, initial_capital 각각). rule_history 는 쓰지 않음.
# - start_time 이 비어 있으면 첫 실행 시각으로 채워 config.json 에 기록(이후 고정).
SHADOW_SKIP_KEYS = ("rule_history", "shadow_ledgers", "start_time", "dayLossOn")   # 하루 손실 한도 끔은 본 장부 전용


def shadow_params(cfg, sh):
    base = {k: v for k, v in cfg.items() if k not in SHADOW_SKIP_KEYS}
    base.update(sh.get("params") or {})
    if sh.get("initial_capital") is not None:
        base["initial_capital"] = sh["initial_capital"]
    return engine.load_params(base)


def write_trades_coin(trades, path, coin_of):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "코인", "거래ID", "진입시각", "방향", "셋업", "등급", "진입가", "최초손절", "T1", "수량",
                    "레버리지", "위험금액", "1시간방향", "청산내역", "마지막청산", "청산시각", "손익원", "R", "수량배율"])
        for k, t in enumerate(trades, 1):
            w.writerow([k, coin_of(t), t["id"], t["entry_time"].strftime("%Y-%m-%d %H:%M"), side_ko(t["side"]), t["tag"],
                        t["grade"], _fp(t["entry"]), _fp(t["stop0"]), _fp(t["t1"]), f"{t['qty']:.4f}", f"{t['lev']:.2f}",
                        f"{t['rm']:.0f}", t["dir60"], " | ".join(t["exits"]), t["exit_reason"],
                        t["exit_time"].strftime("%Y-%m-%d %H:%M"), f"{t['pnl']:.0f}", f"{t['R']:.3f}",
                        t.get("sizeTag", "1")])


def tf_data(c, tf):
    """5분봉 dict → tf분봉 engine.Data (KST 00:00 기준 정렬, 봉 시각 = 구간 시작 — research/common.aggregate 와 같은 묶기).
    마지막 구간은 그 구간의 마지막 5분봉(시작+tf−5분)까지 들어왔을 때만 '완성'으로 넣는다(진행 중 봉 제외).
    PREREG v3 E1 구현 세부: 방향 3×tf 분, 보조 12×tf 분."""
    ts = sorted(c)
    sec = tf * 60
    off = engine.KST_OFFSET_SEC
    T, O, H, L, C, V = [], [], [], [], [], []
    cur = None
    for t in ts:
        o, h, l, cl, v = c[t][:5]
        b = (int(t.timestamp()) + off) // sec
        if b != cur:
            cur = b
            T.append(dt.datetime.fromtimestamp(b * sec - off, KST))
            O.append(o); H.append(h); L.append(l); C.append(cl); V.append(v)
        else:
            H[-1] = max(H[-1], h); L[-1] = min(L[-1], l); C[-1] = cl; V[-1] += v
    if T and ts[-1] < T[-1] + dt.timedelta(minutes=tf - 5):
        for a in (T, O, H, L, C, V):
            a.pop()
    if not T:
        return None
    return engine.Data(T, O, H, L, C, V, dir_min=3 * tf, aux_min=12 * tf, bar_min=tf)


def regime_for(btc_candles, end, offline):
    """국면 라벨용 BTC 일봉: data/btc_daily.csv(온라인이면 6시간마다 필요할 때만 업비트에서 받음) + 저장된 5분봉으로 만든
    완성 일봉(파일에 없는 날만). 실패하면 None(라벨 빈칸)."""
    try:
        from tools import regime as RG
        rows = RG.load_daily(DAILY_CSV) if offline else RG.update_daily(DAILY_CSV, end, log=log, budget_s=15)
        rows = dict(rows)
        cur = RG.day_start_kst(end)
        if btc_candles:
            ts = sorted(btc_candles)
            for d, cl in RG.daily_from_5m(ts, [btc_candles[t][3] for t in ts]):
                if d < cur and d not in rows:          # 완성된 날만(종가 = 그날 마지막 5분봉 종가)
                    rows[d] = cl
        return RG.Regime(sorted(rows.items())) if rows else None
    except Exception as e:
        log(f"국면 라벨 준비 실패(라벨 빈칸): {e!r}", warn=True)
        return None


def write_pairs_all(results, btc_candles, end, offline):
    """pair_group 이 같은 두 그림자 장부(A·B) → research/v4/pairs.csv, 두 장부 status.json 에 짝 비교 요약(pair_summary)."""
    groups = {}
    for name, (trades, opened, datas, tf, sh) in results.items():
        g = sh.get("pair_group")
        if g and sh.get("pair_role") in ("A", "B"):
            groups.setdefault(g, {})[sh["pair_role"]] = (name, trades, opened, datas, tf)
    if not groups:
        return
    from tools import v4pairs as VP
    reg = None
    for g, m in groups.items():
        if set(m) != {"A", "B"}:
            log(f"짝 비교 '{g}': A·B 두 장부가 모두 있어야 함 — 건너뜀", warn=True)
            continue
        if reg is None:
            reg = regime_for(btc_candles, end, offline)
        tf = m["A"][4]
        datas = dict(m["B"][3]); datas.update(m["A"][3])
        rows = VP.build_rows({k: (m[k][1], m[k][2]) for k in ("A", "B")}, datas, tf, reg)
        path = PAIRS_CSV if g == "v4_30m" else os.path.join(os.path.dirname(PAIRS_CSV), f"pairs_{g}.csv")
        VP.write_pairs(path, rows)
        mirror = os.path.join(SHADOW_DIR, f"{g.replace('v4_30m', 'v4')}_pairs.csv")   # 워크플로가 ledger/ 만 커밋해도 보이게
        try:
            VP.write_pairs(mirror, rows)
        except Exception as e:
            log(f"짝 비교 사본(ledger) 쓰기 실패(무시): {e!r}", warn=True)
        ps = VP.power_stats(rows)
        summ = dict(group=g, A=m["A"][0], B=m["B"][0], n_signals=ps["n_signals"], n_both=ps["n_both"],
                    n_only_A=ps["n_only_A"], n_only_B=ps["n_only_B"], n_open=ps["n_open"],
                    mean_d=None if ps["mean_d"] is None else round(ps["mean_d"], 4),
                    sd_d=None if ps["sd_d"] is None else round(ps["sd_d"], 4),
                    rho=None if ps["rho"] is None else round(ps["rho"], 4),
                    n_required=ps["n_required"], progress=ps.get("progress"),
                    n_required_prior=None, delta_R=VP.DELTA,
                    definition="d = R_B − R_A (신호마다, 진입 안 한 쪽 0R). 필요 쌍 수 = ((1.96+0.84)·σ_d/0.10)²",
                    pairs_csv=os.path.relpath(path, HERE).replace("\\", "/"),
                    pairs_csv_ledger=os.path.relpath(mirror, HERE).replace("\\", "/"))
        try:
            pr = read_json(POWER_PRIOR, {}) or {}
            summ["n_required_prior"] = (pr.get("primary") or {}).get("n_required")
            if summ["n_required"] is None and summ["n_required_prior"]:
                summ["progress"] = round(ps["n_signals"] / summ["n_required_prior"], 4)
        except Exception:
            pass
        for k in ("A", "B"):
            p = os.path.join(SHADOW_DIR, m[k][0], "status.json")
            sj = read_json(p, None)
            if sj is not None:
                sj["pair_summary"] = summ
                write_json(p, sj)
        log(f"v4 짝 비교 '{g}': 신호 {ps['n_signals']}개(둘 다 {ps['n_both']}, A만 {ps['n_only_A']}, B만 {ps['n_only_B']}), "
            f"열림 {ps['n_open']} → {summ['pairs_csv']}")


def write_news_pairs(results, blocked_by, end):
    """events_source=news_v1 이고 filter_baseline 이 있는 그림자 장부 → research/news/filter_pairs.csv(+ ledger 사본),
    그 장부 status.json 에 news_v1 상태·최근 판정 10건·필터 요약."""
    from tools import news_v1 as NV
    for name, (trades, opened, _datas, tf, sh) in results.items():
        if sh.get("events_source") != "news_v1":
            continue
        base = sh.get("filter_baseline")
        if base not in results:
            log(f"news_v1 필터 비교: 기준 장부 '{base}' 결과 없음 — 건너뜀", warn=True)
            continue
        bt, bo = results[base][0], results[base][1]
        rows = NV.filter_pairs((bt, bo), (trades, opened), blocked_by.get(name) or [], tf)
        NV.write_filter_pairs(NEWS_FILTER_PAIRS, rows)
        mirror = os.path.join(SHADOW_DIR, name, "filter_pairs.csv")     # 워크플로가 research/news 를 커밋하지 않아도 남게
        NV.write_filter_pairs(mirror, rows)
        p = os.path.join(SHADOW_DIR, name, "status.json")
        sj = read_json(p, None)
        if sj is not None:
            np_ = news_paths()
            sj["news_v1"] = NV.status_of(NV.load_state(np_), end, np_)
            sj["filter_summary"] = dict(NV.filter_summary(rows), baseline=base,
                                        filter_pairs_csv=os.path.relpath(NEWS_FILTER_PAIRS, HERE).replace("\\", "/"),
                                        filter_pairs_csv_ledger=os.path.relpath(mirror, HERE).replace("\\", "/"))
            write_json(p, sj)


def run_shadows(cfg, end, main_candles, events, offline=False, preloaded=None, review=False):
    """활성 그림자 장부를 모두 계산·저장. 실패해도 본 장부에는 영향 없음.
    preloaded: {market: candles} 이번 실행에서 본 장부가 이미 받아 저장한 코인 봉(같은 데이터, 중복 요청 방지)
    review: True 면 journal.csv 에 자동 복기 열(보유봉수·MFE_R·MAE_R·원인태그)을 붙인다(해설은 본 장부만)."""
    preloaded = preloaded or {}
    shs = [s for s in (cfg.get("shadow_ledgers") or []) if s.get("enabled")]
    if not shs:
        return
    cfg_changed = False
    cache = {}
    results = {}                      # 이름 → (청산 거래, 열린 포지션·대기, datas, timeframe) — v4 짝 비교용
    blocked_by = {}                   # 이름 → engine 이 막은 신호 목록(news_v1 필터 비교용)

    def candles_of(market, st):
        if market in cache:
            return cache[market]
        path = candle_path(market)
        if path == CANDLES:
            c = main_candles
        elif market in preloaded:
            c = preloaded[market]
        elif offline:
            c = load_candles(path)
        else:
            c, used, _ = update_candles(cfg, st, end, market=market, path=path)
            if used:
                save_candles(c, path)
        cache[market] = c
        return c

    group_start = {}                  # pair_group → 같은 시작 시각
    for sh in shs:
        if sh.get("start_time") and sh.get("pair_group"):
            group_start.setdefault(sh["pair_group"], sh["start_time"])
    for sh in shs:
        name = sh["name"]
        try:
            tf = int(sh.get("timeframe", 5) or 5)
            if not sh.get("start_time"):
                g = sh.get("pair_group")
                if g and g in group_start:
                    sh["start_time"] = group_start[g]
                elif tf == 5:
                    sh["start_time"] = end.strftime("%Y-%m-%d %H:%M")
                else:                 # 큰 시간봉: 지표 준비(warmup_days)가 끝난 뒤부터, 시간봉 경계로 올림
                    mk = sh.get("markets") or [cfg.get("market", "KRW-BTC")]
                    firsts = [min(c) for c in (candles_of(m, end) for m in mk) if c]
                    t0 = max([end] + [f + dt.timedelta(days=float(sh.get("warmup_days", 7))) for f in firsts])
                    sec = tf * 60
                    b = -(-(int(t0.timestamp()) + engine.KST_OFFSET_SEC) // sec)
                    sh["start_time"] = dt.datetime.fromtimestamp(b * sec - engine.KST_OFFSET_SEC, KST).strftime("%Y-%m-%d %H:%M")
                if g:
                    group_start[g] = sh["start_time"]
                cfg_changed = True
                log(f"그림자 장부 '{name}' 시작 시각 = {sh['start_time']}")
            st = engine.parse_kst(sh["start_time"])
            P = shadow_params(cfg, sh)
            out_dir = os.path.join(SHADOW_DIR, name)
            all_trades, per, datas, opened = [], {}, {}, []
            sh_events = events
            if sh.get("events_source") == "news_v1":     # news_v1 전용 창만 읽음(data/events.csv 의 돌발 판정은 안 씀)
                from tools import news_v1 as NV
                sh_events = NV.ledger_events(news_paths())
            sh_blocked = blocked_by.setdefault(name, [])
            for market in sh.get("markets") or [cfg.get("market", "KRW-BTC")]:
                coin = market.split("-")[-1]
                c = candles_of(market, st)
                if not c:
                    log(f"그림자 '{name}' {coin}: 봉 없음 — 건너뜀", warn=True)
                    continue
                if tf == 5:
                    ts = sorted(c)
                    D = engine.Data(ts, [c[t][0] for t in ts], [c[t][1] for t in ts], [c[t][2] for t in ts],
                                    [c[t][3] for t in ts], [c[t][4] for t in ts])
                else:
                    D = tf_data(c, tf)
                    if D is None:
                        log(f"그림자 '{name}' {coin}: 완성된 {tf}분봉 없음 — 건너뜀", warn=True)
                        continue
                    ts = D.T
                R = engine.run(D, P, start_time=st, close_at_end=False, events=sh_events, coin=coin)
                sh_blocked.extend(R.get("blocked") or [])
                datas[coin] = D
                for t in R["trades"]:
                    t["coin"] = coin
                for x in (R["position"], R["pending"]):
                    if x is not None:
                        x["coin"] = coin
                        opened.append(x)
                all_trades += R["trades"]
                s = engine.stats(R["trades"], R["curve"])
                pend = R["pending"]
                per[coin] = dict(
                    market=market, initial_capital=P["capital"], balance=round(R["final"]), equity=round(R["equity"]),
                    realized_pnl=round(R["realized"]), withdrawn_total=round(R["withdrawn"]),
                    trades_count=s["N"], win_rate=round(s["win"], 1), avg_R=round(s["avgR"], 3), sum_R=round(s["sumR"], 3),
                    max_drawdown_pct=round(s["mdd"], 2), has_position=R["position"] is not None,
                    position=position_dict(R["position"], R["last_close"], P["fee"]),
                    pending_entry=None if pend is None else dict(side=side_ko(pend["side"]), setup=pend["tag"],
                                                                 grade=pend["grade"], signal_time=pend["signal_time"].strftime(TFMT),
                                                                 stop=pend["stop"], t1=pend["t1"]),
                    pending_close=R["pending_close"], last_bar_time=ts[-1].strftime(TFMT),
                    today=dict(date=str(R["day"]["date"]), realized_pnl=round(R["day"]["realized"]),
                               no_new_entries=bool(R["day"]["stop"]), reason=R["day"]["reason"]))
            all_trades.sort(key=lambda t: (t["exit_time"], t["coin"]))
            coin_of = lambda t: t.get("coin", "")   # noqa: E731
            write_trades_coin(all_trades, os.path.join(out_dir, "trades.csv"), coin_of)
            if review:
                try:
                    from tools.review import attach_reviews
                    attach_reviews(all_trades, datas, events, log)
                except Exception as e:
                    log(f"그림자 '{name}' 복기 계산 실패(장부는 정상): {e!r}", warn=True)
            attach_extras(all_trades, datas, tf, opened, P["fee"])
            write_journal(all_trades, os.path.join(out_dir, "journal.csv"), coin_of, events, review=review, bar_min=tf)
            tot = engine.stats(all_trades)
            stj = dict(
                mode="그림자 장부(가상 매매, 본 장부와 별도·알림 없음)", name=name, description=sh.get("description", ""),
                start_time=sh["start_time"], markets=sh.get("markets"), params_override=sh.get("params") or {},
                trades_count=tot["N"], win_rate=round(tot["win"], 1), avg_R=round(tot["avgR"], 3),
                sum_R=round(tot["sumR"], 3), coins=per, updated_at=dt.datetime.now(KST).strftime(TFMT))
            if tf != 5:
                stj["timeframe"] = tf
            if sh.get("pair_group"):
                stj.update(pair_group=sh["pair_group"], pair_role=sh.get("pair_role"))
            if sh.get("events_source") == "news_v1":
                stj["events_source"] = "news_v1"
                stj["blocked_signals"] = len(sh_blocked)
            stj.update(extras_fields(all_trades))
            write_json(os.path.join(out_dir, "status.json"), stj)
            results[name] = (all_trades, opened, datas, tf, sh)
            log(f"그림자 장부 '{name}': 거래 {tot['N']}건, 평균 {tot['avgR']:+.3f}R → ledger/shadow/{name}/")
        except Exception as e:
            log(f"그림자 장부 '{name}' 계산 실패(본 장부는 정상): {e!r}", warn=True)
    try:
        write_pairs_all(results, cache.get("KRW-BTC") or main_candles, end, offline)
    except Exception as e:
        log(f"v4 짝 비교 기록 실패(장부는 정상): {e!r}", warn=True)
    try:
        write_news_pairs(results, blocked_by, end)
    except Exception as e:
        log(f"news_v1 필터 비교 기록 실패(장부는 정상): {e!r}", warn=True)
    if cfg_changed:
        disk = read_json(CONFIG, {})
        for s in disk.get("shadow_ledgers") or []:
            for sh in shs:
                if s.get("name") == sh["name"] and not s.get("start_time"):
                    s["start_time"] = sh["start_time"]
        write_json(CONFIG, disk)

# ============================== 3) 알림 ==============================

def event_message(e):
    """알림 문구. 코인 이벤트(진입·T1·청산)는 앞에 [코인] (예: "[ETH] 🟢 롱 진입 …")"""
    cn = e.get("coin") or "BTC"
    pre = f"[{cn}] "
    if e["type"] == "entry":
        icon = "🟢" if e["side"] == 1 else "🔵"
        return (f"{pre}{icon} {side_ko(e['side'])} 진입 [{setup_name(e['tag'], e['grade'])}] {_mp(e['price'])} / "
                f"손절 {_mp(e['stop'])} / T1 {_mp(e['t1'])} / {e['qty']:.4f} {cn} ({e['lev']:.1f}배)")
    if e["type"] == "t1":
        return f"{pre}✅ T1 반익절 {won(e['part_pnl'])} ({_mp(e['price'])}, 남은 물량 손절은 진입가로)"
    if e["type"] == "exit":
        r = e["reason"]
        tot = e["total_pnl"]
        if r == "손절":
            return f"{pre}🔴 손절 {won(tot)} ({_mp(e['price'])})"
        icon = "✅" if tot > 0 else ("🔴" if tot < 0 else "⚪")
        part = f" / 이번 청산 {won(e['part_pnl'])}" if abs(e["part_pnl"] - tot) > 0.5 else ""
        return f"{pre}{icon} 청산({r}) 거래 합계 {won(tot)}{part} ({_mp(e['price'])})"
    if e["type"] == "daystop":
        return f"⛔ 오늘 신규 금지({e['reason']}, 오늘 실현 {won(e['day_pnl'])})"
    if e["type"] == "target":
        tail = "이후엔 목표가 남는 크기로만" if e.get("after", True) else "오늘은 더 진입하지 않음"
        return f"🎯 하루 목표 달성({won(e['day_pnl'])}) — {tail}"
    if e["type"] == "withdraw":
        msg = f"💸 출금 {e['amount']:,.0f}원(지난주 수익의 {e['pct']:g}%) — 출금 누적 {e['total']:,.0f}원"
        return msg + (" · 원금 회수 완료" if e.get("principal_done") else "")
    if e["type"] in ("review", "evwin"):          # 하루 복기 요약 / 이벤트 회피 창 (문구를 미리 만들어 둠)
        return e["msg"]
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
    topic = os.environ.get("NTFY_TOPIC", "").strip().strip('"').strip("'")
    # Secret 에 'https://ntfy.sh/토픽' 처럼 주소 전체를 넣었어도 토픽 이름만 쓴다(주소째 보내면 ntfy 가 404).
    if "/" in topic:
        topic = topic.rstrip("/").split("/")[-1]
    if topic and not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", topic):
        log(f"⚠️ NTFY_TOPIC 형식이 ntfy 토픽 규칙(영문·숫자·-·_ 1~64자)과 다름(길이 {len(topic)}) — 전송이 404 날 수 있음", warn=True)
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
            log(f"ntfy 전송 실패(다음 실행에서 다시 시도): {ex!r} (토픽 길이 {len(topic)}, 형식 "
                f"{'정상' if re.fullmatch(r'[-_A-Za-z0-9]{1,64}', topic) else '이상'})", warn=True)
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


def to_data(c):
    ts = sorted(c)
    return engine.Data(ts, [c[t][0] for t in ts], [c[t][1] for t in ts], [c[t][2] for t in ts],
                       [c[t][3] for t in ts], [c[t][4] for t in ts])


MAX_WAIT_LAG = dt.timedelta(minutes=60)


def compute_main(P, cands, start_time, events):
    """본 장부 계산(engine.run_multi). cands: {market: candles dict} (BTC 필수).
    마지막 봉 기준 매매 대상 코인 중 봉이 늦게 들어온 코인이 있으면(60분 이내 지연), 모든 코인을 그 코인의 마지막 봉까지만
    계산한다 → 다음 실행에서 늦은 봉이 들어와도 이미 계산·알림한 과거가 바뀌지 않게(결정적 재계산 유지)."""
    btc = cands.get("KRW-BTC") or cands[engine.market_order(cands)[0]]
    lasts = {m: max(c) for m, c in cands.items() if c}
    ref = max(lasts.values())
    act = engine.params_at(P, ref)["markets"]
    cut = ref
    for m in act:
        if m not in lasts:
            log(f"{engine.coin_of(m)} 봉 파일이 없어 이 코인은 빼고 계산합니다.", warn=True)
        elif lasts[m] < ref:
            if ref - lasts[m] <= MAX_WAIT_LAG:
                cut = min(cut, lasts[m])
            else:
                log(f"{engine.coin_of(m)} 봉이 {lasts[m]:%m-%d %H:%M} 에서 멈춤({int((ref - lasts[m]).total_seconds() // 60)}분 지연) — "
                    f"기다리지 않고 계산합니다(나중에 봉이 채워지면 과거 결과가 바뀔 수 있음).", warn=True)
    if cut < ref:
        log(f"일부 코인 봉이 늦어 {cut:%m-%d %H:%M} 봉까지만 계산합니다(다음 실행에서 이어감).")
    Ds = {}
    for m, c in cands.items():
        cc = {t: v for t, v in c.items() if t <= cut} if cut < ref else c
        if not cc:
            continue
        Ds[m] = to_data(cc)
        if Ds[m].gaps:
            log(f"{engine.coin_of(m)}: 5분 간격이 아닌 곳 {Ds[m].gaps}개(거래소 데이터 공백). 그대로 계산합니다.")
    R = engine.run_multi(Ds, P, start_time=start_time, events=events)
    bt = [t for t in btc if t <= cut]
    R["last_bar_time"] = max(bt).strftime(TFMT)
    R["datas"] = {engine.coin_of(m): D for m, D in Ds.items()}     # 자동 복기용(계산 결과에는 영향 없음)
    return R


def review_main(R, events, now, start_time, dry_run):
    """(A) 본 장부 자동 복기: 숫자 복기·원인 태그 → Gemini 해설(캐시) → 하루 리포트 이벤트. 어떤 실패도 경고만.
    반환: (해설 dict, 알림 이벤트 목록)"""
    texts, extra = {}, []
    try:
        from tools import review as rvw
    except Exception as e:
        log(f"복기 모듈 불러오기 실패(장부는 정상): {e!r}", warn=True)
        return texts, extra
    try:
        rvw.attach_reviews(R["trades"], R.get("datas") or {}, events, log)
    except Exception as e:
        log(f"복기 계산 실패(장부는 정상): {e!r}", warn=True)
    try:
        if dry_run:
            texts = {k: v.get("text", "") for k, v in rvw.load_reviews(REVIEWS_STATE)["reviews"].items() if v.get("text")}
        else:
            texts = rvw.gemini_reviews(R["trades"], REVIEWS_STATE, now, log)
    except Exception as e:
        log(f"Gemini 복기 해설 단계 실패(빈칸으로 둠): {e!r}", warn=True)
    try:
        ev = rvw.maybe_daily_report(now, R["trades"], REVIEW_DIR, start_time, texts, log)
        if ev:
            extra.append(ev)
    except Exception as e:
        log(f"하루 복기 리포트 실패(무시): {e!r}", warn=True)
    return texts, extra


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
    events_path = os.path.join(HERE, cfg["events_file"]) if cfg.get("events_file") else EVENTS_DEFAULT

    # (B) 이벤트 수집 — eventSource "feeds"(기본: 경제 캘린더 JSON + 코인 뉴스 RSS) 또는 "gemini"(예전 방식).
    #     시간 조건(정기 6시간·돌발 30분)은 모듈이 판단. 실패는 경고만, 봇은 계속. 수집 전체 eventFetchBudgetSec(30초) 이하.
    ev_src = str(cfg.get("eventSource", "feeds")).lower()
    t_news0 = time.monotonic()
    if a.dry_run:
        log("dry-run: 이벤트 수집은 건너뜁니다.")
    elif ev_src == "gemini":
        try:
            from tools import gemini_events
            gemini_events.maybe_update(now, cfg, events_path, GEM_EVENTS_STATE, log)
        except Exception as e:
            log(f"Gemini 이벤트 수집 건너뜀(오류): {e!r}", warn=True)
    else:
        try:
            from tools import event_feeds
            event_feeds.maybe_update(now, cfg, events_path, EVENT_FEED_STATE, log)
        except Exception as e:
            log(f"이벤트 피드 수집 건너뜀(오류): {e!r}", warn=True)
    news_left = max(0.0, NEWS_BUDGET_SEC - (time.monotonic() - t_news0))

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

    # 다른 코인(어느 규칙 단계에서든 매매 대상인 코인) 5분봉도 받아 둔다 — 그림자 장부와 같은 파일(candle_path)
    main_mkt = cfg.get("market", "KRW-BTC")
    cands = {main_mkt: candles}
    for m in engine.all_markets(P):
        if m == main_mkt:
            continue
        path = candle_path(m)
        c, u, _ = update_candles(cfg, start_time, end, market=m, path=path)
        if u:
            save_candles(c, path)
        if c:
            cands[m] = c
        else:
            log(f"{engine.coin_of(m)} 봉 없음 — 이 코인은 빼고 계산합니다.", warn=True)
    try:
        events = engine.load_events(events_path)
    except Exception as e:                      # 일정 파일이 깨져도 장부 계산은 계속(일정 없이)
        log(f"일정 파일 읽기 실패 — 일정 없이 계산합니다: {e!r}", warn=True)
        events = []
    # (C) news_v1 기록 전용 파이프라인(newsV1.enabled 일 때만). 본 장부·baseline·v4 의 events 는 그대로(이 결과를 안 읽음).
    news_st = None
    if news_v1_enabled(cfg) and not a.dry_run:
        try:
            from tools import event_feeds as _ef
            from tools import news_v1 as NV
            news_st = NV.run(now, cfg, news_paths(), candles=cands, budget_s=news_left, shared=_ef.LAST_FETCH, log=log)
        except Exception as e:
            log(f"news_v1 건너뜀(오류, 봇 계속): {e!r}", warn=True)
    R = compute_main(P, cands, start_time, events)
    log(f"재계산: 코인 {', '.join(engine.coin_of(m) for m in cands)}, 거래 {len(R['trades'])}건 "
        f"(매매 대상 봉 {len(R['curve'])}개), 포지션 {len(R['positions'])}개, 진입 대기 {len(R['pendings'])}개, "
        f"실현 {won(R['realized'])}")
    if R.get("blocked"):
        b = R["blocked"][-1]
        log(f"이벤트 회피로 막힌 신호 누적 {len(R['blocked'])}건 (최근: {b['time']:%m-%d %H:%M} {b['coin']} "
            f"{side_ko(b['side'])} {b['tag']}{b['grade']} — {b['reason']})")
    ts = [engine.parse_kst(R["last_bar_time"])]

    # (2)(4) 상관 신호 묶음·손익 분해 (기록용, 복기 리포트보다 먼저)
    attach_extras(R["trades"], R.get("datas") or {}, 5, list(R.get("positions") or []) + list(R.get("pendings") or []),
                  P["fee"])
    # (A) 자동 복기 (장부 계산이 끝난 뒤, 기록용)
    texts, extra_events = review_main(R, events, now, start_time, a.dry_run)

    prev = read_json(STATUS, None)
    status = build_status(cfg, P, R, start_time, primary, used, dt.datetime.now(KST))
    status.update(event_fields(P, R, events, now))
    status.update(event_data_fields(cfg, now, R, events))
    if news_st is not None:
        status["news_v1"] = {k: v for k, v in news_st.items() if k != "recent"}   # 상태만(본 장부 계산과 무관)
    write_trades(R["trades"])
    write_json(STATUS, status)
    write_account(status, R)
    write_journal(R["trades"], JOURNAL, coin_of=coin_tr, events=events, review=True, texts=texts)
    try:
        write_blocked(R.get("blocked") or [], BLOCKED_CSV)
    except Exception as e:
        log(f"막힌 신호 기록 실패(무시): {e!r}", warn=True)
    log("장부 저장: ledger/trades.csv, ledger/status.json, ledger/ACCOUNT.md, ledger/journal.csv")
    run_shadows(cfg, end, candles, events, preloaded=cands, review=True)

    if prev and prev.get("has_position") != status["has_position"]:
        log(f"포지션 변화: {'없음 → 있음' if status['has_position'] else '있음 → 없음'}")
    extra_events += window_events(P, events, now, ts[-1])
    notify(R["events"] + extra_events, ts[-1], a.dry_run)
    save_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
