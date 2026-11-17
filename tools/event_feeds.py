# -*- coding: utf-8 -*-
"""
이벤트 감지 A안 (2026-11-17) — Gemini 검색 없이 공개 피드로 data/events.csv 를 채운다. 진입 금지 판단은 engine 이 함
(eventBlock=true 일 때만, high 만, added_at 이후 봉만 — 기존 로직 그대로).

1) 정기 일정 (eventScheduleHours, 기본 6시간마다): 무료 경제 캘린더 JSON(이번 주 + 다음 주 파일이 있으면 함께)
   - USD 만. impact High → high, Medium → medium(기록만, 차단 안 함). 시각은 원본 오프셋(미국 동부) → KST.
   - 시각이 00:00(현지)인 항목은 '하루 종일/미정'일 수 있어 medium 으로 낮춤(시각 불확실한 창으로 막지 않게).
   - 같은 시각의 여러 지표(예: Retail Sales + Core Retail Sales)는 한 줄로 합침. coins=ALL.
   - 검증·중복 제거·events.csv 쓰기는 tools/gemini_events.py 의 validate_schedule / append_events 재사용
     (미래 7일 안, 근거 URL 필수 → 캘린더 URL).
2) 돌발 (eventBreakingMin, 기본 30분마다): 코인 뉴스 RSS 의 최근 2시간 기사 제목·요약을 아래 키워드 규칙으로 분류.
   - 코인: 제목에 BTC/Bitcoin, ETH/Ethereum/Ether, XRP/Ripple, SOL/Solana(한글 이름 포함). 제목에 대상 코인이 없고
     시장 전체 사건(대형 거래소 장애·입출금 중단, 규제 집행, 대규모 청산)이면 ALL. 그 밖(다른 코인 사건)은 버림.
   - 점수 = 분류 가중치 + 보너스(대형 거래소 이름, 1억 달러 이상 금액) − 감점 단어(전망·분석·질문형 등).
     점수 ≥ HIGH_SCORE → high, ≥ MEDIUM_SCORE → medium, 그 밖은 버림.
   - 검증은 validate_breaking 재사용: 시작 = 다음 5분봉, 길이 60~180분, 하루 high 최대 eventBreakingMaxPerDay,
     12시간 안 같은 이름 중복 제거, source URL 필수. 추가로 같은 분류·같은 코인은 DEDUP_HOURS 안에 한 번만.
3) 상태: state/event_feed.json — 소스별 last_attempt·last_success·ok·error·count, 모드별 last_attempt·last_success.
   status.json event_data = {schedule, breaking: OK|STALE|UNKNOWN, last_success_*} (data_status()).
   정기 일정이 0건이어도 '이벤트 없음'이 아니라 '받기 성공, 해당 일정 0건'으로만 기록한다.
네트워크: 요청마다 타임아웃 ≤ 15초, 한 번 실행에서 수집 전체 eventFetchBudgetSec(기본 30초) 이하. 실패는 경고만.
표준 라이브러리만.
"""
import datetime as dt
import email.utils
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from engine import KST, parse_kst

# ---------------- 소스 ----------------
CALENDAR_SOURCES = [
    # (이름, URL, 필수 여부) — 다음 주 파일은 없을 때가 많아 실패해도 정기 일정 상태에 영향 없음
    ("ff_thisweek", "https://nfs.faireconomy.media/ff_calendar_thisweek.json", True),
    ("ff_nextweek", "https://nfs.faireconomy.media/ff_calendar_nextweek.json", False),
]
RSS_SOURCES = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock", "https://www.theblock.co/rss.xml"),
]
CAL_COUNTRY = "USD"
IMPACT_MAP = {"high": "high", "medium": "medium"}
MAX_BYTES = 3_000_000
REQ_TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; btc-paper-bot/1.0; read-only)"

# ---------------- 주기·상태 판정 ----------------
SCHEDULE_HOURS = 6.0
BREAKING_MIN = 30.0
RETRY_MIN = 60.0                    # 정기 일정 실패 시 재시도 간격
SCHEDULE_STALE_HOURS = 24.0         # 정기 일정: 마지막 성공이 이보다 오래면 STALE
BREAKING_STALE_MIN = 120.0          # 돌발: 마지막 성공이 이보다 오래면 STALE
BUDGET_SEC = 30.0
LOOKBACK_H = 2.0

# ---------------- 돌발 키워드 규칙 (소문자 비교, 정규식) ----------------
# (분류 키, 한국어 이름, 가중치, 시장 전체 가능 여부, 창 길이(분), 키워드 정규식 목록)
CATEGORIES = [
    ("hack", "해킹/익스플로잇", 3, False, 120,
     [r"\bhack(ed|er|ers|s)?\b", r"\bexploit(ed|s)?\b", r"\bdrain(ed)?\b", r"\bstolen\b", r"\bbreach\b",
      r"\battack(er|ed)?\b.*\b(protocol|bridge|exchange)", r"해킹", r"익스플로잇", r"탈취"]),
    ("halt", "입출금 중단", 3, True, 120,
     [r"\b(halt|halts|halted|suspend|suspends|suspended|pause|pauses|paused|freeze|freezes|froze)\b.{0,40}"
      r"\b(withdrawal|withdrawals|deposit|deposits|trading)\b",
      r"\b(withdrawal|withdrawals|deposit|deposits)\b.{0,30}\b(halt|halted|suspended|paused|frozen)\b",
      r"출금\s?중단", r"입금\s?중단", r"입출금\s?중단", r"거래\s?중단"]),
    ("outage", "거래소 장애", 3, True, 90,
     [r"\boutage\b", r"\b(goes|went) down\b", r"\bdowntime\b", r"\bservice disruption\b", r"\bdegraded\b",
      r"서버\s?장애", r"접속\s?장애", r"거래소\s?장애"]),
    ("delist", "상장폐지/유의 종목", 3, False, 120,
     [r"\bdelist(s|ed|ing)?\b", r"\bdelisting\b", r"\binvestment warning\b", r"\bcaution (designation|flag)\b",
      r"상장\s?폐지", r"유의\s?종목", r"투자\s?유의", r"거래\s?지원\s?종료"]),
    ("regulation", "SEC·규제 조치", 2, True, 120,
     [r"\bsec\b.{0,40}\b(sue|sues|sued|charges|charged|lawsuit|enforcement|rejects|rejected|denies|denied|bans?)\b",
      r"\b(doj|cftc)\b.{0,40}\b(charges|charged|sues|sued|indict|arrest)", r"\b(ban|bans|banned)\b.{0,30}\bcrypto",
      r"\bcrackdown\b", r"\benforcement action\b", r"규제\s?조치", r"금지\s?조치", r"기소", r"제재"]),
    ("liquidation", "대규모 청산", 2, True, 90,
     [r"\bliquidat(ed|ion|ions)\b", r"\bwipe(d|s)? out\b.{0,30}\b(longs|shorts|positions)", r"대규모\s?청산", r"강제\s?청산"]),
    ("network", "네트워크 중단", 4, False, 180,
     [r"\b(network|chain|mainnet|blockchain)\b.{0,30}\b(halt|halts|halted|outage|down|stall|stalled|stops?)\b",
      r"\b(halt|halts|halted|stall|stalled)\b.{0,30}\b(block production|network|chain)\b",
      r"\bstopped producing blocks\b", r"네트워크\s?중단", r"블록\s?생성\s?중단", r"메인넷\s?중단"]),
]
MAJOR_EXCHANGES = [r"\bbinance\b", r"\bcoinbase\b", r"\bupbit\b", r"\bbithumb\b", r"\bokx\b", r"\bbybit\b", r"\bkraken\b",
                   r"업비트", r"빗썸", r"바이낸스", r"코인베이스"]
BIG_AMOUNT = r"\$\s?(\d{3,}(\.\d+)?\s?(m|million)|\d+(\.\d+)?\s?(b|bn|billion))\b"
NEGATIVE = [r"\?", r"\bwhat\b", r"\bhow\b", r"\bwhy\b", r"\bcould\b", r"\bmight\b", r"\bprediction\b", r"\bforecast\b",
            r"\banalysis\b", r"\banalyst", r"\bexplained\b", r"\bguide\b", r"\bopinion\b", r"\brecap\b", r"\bweekly\b",
            r"\bresolved\b", r"\bresume[sd]?\b", r"\brestored\b", r"\bfiled? to\b", r"\byears? ago\b", r"\banniversary\b",
            r"\bpleads? guilty\b", r"\bsentenced\b", r"\bacquitted\b", r"\btrial\b", r"전망", r"분석", r"재개"]
W_MAJOR_EXCHANGE = 1
W_BIG_AMOUNT = 1
W_NEGATIVE = -2                    # 감점 단어 하나라도 있으면
HIGH_SCORE = 4
MEDIUM_SCORE = 2
DEDUP_HOURS = 3                     # 같은 분류·같은 코인 돌발은 이 시간 안에 한 번만
COIN_PATTERNS = {
    "BTC": [r"\bbtc\b", r"\bbitcoin\b", r"비트코인"],
    "ETH": [r"\beth\b", r"\bethereum\b", r"\bether\b", r"이더리움"],
    "XRP": [r"\bxrp\b", r"\bripple\b", r"리플"],
    "SOL": [r"\bsol\b", r"\bsolana\b", r"솔라나"],
}
SCHEDULE_PREFIX = "[일정] "
LAST_FETCH = {}       # 이번 실행 돌발 RSS 결과(news_v1 재사용용): at, articles, sources_ok, error


# ============================== 네트워크 ==============================

def http_get(url, timeout):
    """바이트 반환. 실패하면 예외(HTTPError 포함)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=max(1.0, min(REQ_TIMEOUT, timeout))) as r:
        b = r.read(MAX_BYTES + 1)
    if len(b) > MAX_BYTES:
        raise ValueError("응답이 너무 큼")
    return b


# ============================== 정기 일정 ==============================

def parse_calendar(raw, country=CAL_COUNTRY):
    """캘린더 JSON(바이트·문자열·목록) → [dict(time=KST datetime, title, importance, all_day)] (그 나라만, Low 제외)"""
    d = json.loads(raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else raw) if not isinstance(raw, list) else raw
    out = []
    for x in d if isinstance(d, list) else []:
        try:
            if not isinstance(x, dict) or str(x.get("country", "")).upper() != country:
                continue
            imp = IMPACT_MAP.get(str(x.get("impact", "")).strip().lower())
            if imp is None:
                continue
            t0 = dt.datetime.fromisoformat(str(x["date"]).strip().replace("Z", "+00:00"))
            if t0.tzinfo is None:
                continue
            all_day = t0.hour == 0 and t0.minute == 0
            out.append(dict(time=t0.astimezone(KST).replace(second=0, microsecond=0),
                            title=re.sub(r"\s+", " ", str(x.get("title", ""))).strip()[:60],
                            importance="medium" if all_day else imp, all_day=all_day))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def calendar_items(entries, source_url):
    """같은 시각·중요도를 합쳐 validate_schedule 입력 형식으로"""
    by = {}
    for e in sorted(entries, key=lambda e: (e["time"], e["importance"], e["title"])):
        k = (e["time"], e["importance"])
        by.setdefault(k, []).append(e["title"] + (" (하루 종일/미정)" if e["all_day"] else ""))
    items = []
    for (t, imp), names in sorted(by.items()):
        nm = " · ".join(dict.fromkeys(names))
        items.append(dict(time_kst=t.strftime("%Y-%m-%d %H:%M"), name=f"{SCHEDULE_PREFIX}{nm} ({CAL_COUNTRY})"[:80],
                          importance=imp, coins="ALL", before_min=None, after_min=None, source=source_url))
    # high 가 같은 시각에 있으면 medium 은 버림(중복 창)
    hi = {it["time_kst"] for it in items if it["importance"] == "high"}
    return [it for it in items if it["importance"] == "high" or it["time_kst"] not in hi]


# ============================== 돌발 ==============================

def _text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or "").strip():
            return x.text
        if x is not None and x.get("href"):
            return x.get("href")
    return ""


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_rss(raw):
    """RSS 2.0 / Atom → [dict(title, summary, link, published=KST datetime 또는 None)]"""
    root = ET.fromstring(raw)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    for it in items:
        title = strip_html(_text(it, "title", f"{atom}title"))
        summ = strip_html(_text(it, "description", f"{atom}summary", f"{atom}content"))[:500]
        link = (_text(it, "link", f"{atom}link") or _text(it, "guid")).strip()
        pub = _text(it, "pubDate", f"{atom}published", f"{atom}updated", "{http://purl.org/dc/elements/1.1/}date").strip()
        t = None
        if pub:
            try:
                t = email.utils.parsedate_to_datetime(pub)
            except (TypeError, ValueError, IndexError):
                try:
                    t = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except ValueError:
                    t = None
            if t is not None:
                t = t.astimezone(KST) if t.tzinfo else None
        if title:
            out.append(dict(title=title, summary=summ, link=link, published=t))
    return out


def _any(pats, s):
    return any(re.search(p, s) for p in pats)


def coins_in(title):
    s = title.lower()
    return [c for c, pats in COIN_PATTERNS.items() if _any(pats, s)]


def classify(title, summary=""):
    """기사 → None(해당 없음) 또는 dict(cat, cat_ko, score, importance, coins('ALL' 또는 'BTC|ETH'), duration)"""
    t = (title or "").lower()
    s = t + " " + (summary or "").lower()
    best = None
    for key, ko, w, wide, dur, pats in CATEGORIES:
        if _any(pats, t) or _any(pats, s):
            in_title = _any(pats, t)
            sc = w if in_title else w - 1           # 요약에만 있으면 1점 깎음
            if best is None or sc > best[2]:
                best = (key, ko, sc, wide, dur)
    if best is None:
        return None
    key, ko, sc, wide, dur = best
    major = _any(MAJOR_EXCHANGES, s)
    if major:
        sc += W_MAJOR_EXCHANGE
    if re.search(BIG_AMOUNT, s):
        sc += W_BIG_AMOUNT
    if _any(NEGATIVE, t):
        sc += W_NEGATIVE
    cs = coins_in(title)
    if cs:
        coins = "|".join(cs)
    elif wide and (major or key in ("regulation", "liquidation")):
        coins = "ALL"
    else:
        return None                                 # 대상 코인이 아닌 개별 사건
    imp = "high" if sc >= HIGH_SCORE else ("medium" if sc >= MEDIUM_SCORE else None)
    if imp is None:
        return None
    return dict(cat=key, cat_ko=ko, score=sc, importance=imp, coins=coins, duration=dur)


def breaking_items(articles, now, existing, lookback_h=LOOKBACK_H):
    """최근 기사 → validate_breaking 입력 목록(점수 높은 순, 같은 분류·코인은 하나만)"""
    a = now - dt.timedelta(hours=lookback_h)
    cands = []
    for x in articles:
        p = x.get("published")
        if p is None or not (a <= p <= now + dt.timedelta(minutes=10)):
            continue
        if not re.match(r"^https?://", x.get("link") or ""):
            continue
        c = classify(x["title"], x.get("summary", ""))
        if c:
            cands.append((c, x))
    cands.sort(key=lambda cx: (-cx[0]["score"], cx[1]["published"]))
    items, seen = [], set()
    recent_cut = now - dt.timedelta(hours=DEDUP_HOURS)
    for e in existing:          # 최근에 이미 넣은 같은 분류·코인
        if len(e) > 6 and e[6] is not None and e[6] >= recent_cut:
            m = re.match(r"^\[돌발\] (.+?) \((.+?)\)", str(e[1]))
            if m:
                seen.add((m.group(1), m.group(2)))
    for c, x in cands:
        k = (c["cat_ko"], c["coins"])
        if k in seen:
            continue
        seen.add(k)
        items.append(dict(name=f"{c['cat_ko']} ({c['coins']}): {x['title'][:50]}", importance=c["importance"],
                          coins=c["coins"], source=x["link"], duration_min=c["duration"],
                          happened_at=x["published"].strftime("%Y-%m-%d %H:%M"), _score=c["score"]))
    return items


# ============================== 상태 ==============================

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    st["note"] = "이벤트 피드 수집 기록(봇이 갱신). 정기 일정 0건 = 받기는 성공했지만 해당 일정이 없다는 뜻이지, 데이터가 없다는 뜻이 아님."
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _ts(now):
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _due(rec, now, every_min, retry_min):
    last = (rec or {}).get("last_attempt")
    if not last:
        return True
    try:
        t = parse_kst(last)
    except ValueError:
        return True
    gap = retry_min if (rec or {}).get("ok") is False else every_min
    return now - t >= dt.timedelta(minutes=gap)


def judge(last_success, now, stale_td):
    """OK / STALE / UNKNOWN"""
    if not last_success:
        return "UNKNOWN"
    try:
        t = parse_kst(last_success)
    except ValueError:
        return "UNKNOWN"
    return "OK" if now - t <= stale_td else "STALE"


def data_status(st, now, cfg=None):
    """state(event_feed.json 또는 gemini_events.json dict) → status.json event_data"""
    cfg = cfg or {}
    sch = (st or {}).get("schedule") or {}
    brk = (st or {}).get("breaking") or {}
    sh = float(cfg.get("eventScheduleStaleHours", SCHEDULE_STALE_HOURS))
    bm = float(cfg.get("eventBreakingStaleMin", BREAKING_STALE_MIN))
    return dict(
        source=cfg.get("eventSource", "feeds"),
        schedule=judge(sch.get("last_success"), now, dt.timedelta(hours=sh)),
        breaking=judge(brk.get("last_success"), now, dt.timedelta(minutes=bm)),
        last_success_schedule=sch.get("last_success"), last_success_breaking=brk.get("last_success"),
        last_attempt_schedule=sch.get("last_attempt"), last_attempt_breaking=brk.get("last_attempt"),
        schedule_items_last=sch.get("received"), breaking_items_last=brk.get("received"),
        schedule_error=sch.get("error"), breaking_error=brk.get("error"),
        stale_rule=f"정기 일정 {sh:g}시간·돌발 {bm:g}분 넘게 성공 없으면 STALE, 한 번도 성공 없으면 UNKNOWN",
        sources={k: dict(ok=v.get("ok"), last_success=v.get("last_success"), count=v.get("count"), error=v.get("error"),
                         optional=bool(v.get("optional")))
                 for k, v in ((st or {}).get("sources") or {}).items()},
    )


# ============================== 실행 ==============================

def _fetch_source(st, name, url, now, deadline, get):
    rec = st.setdefault("sources", {}).setdefault(name, {})
    rec["last_attempt"] = _ts(now)
    left = deadline - time.monotonic()
    if left < 1.0:
        rec.update(ok=False, error="이번 실행 시간 예산 초과로 건너뜀")
        return None
    try:
        b = get(url, left)
        return b
    except urllib.error.HTTPError as e:
        rec.update(ok=False, error=f"HTTP {e.code}")
    except Exception as e:
        rec.update(ok=False, error=f"{type(e).__name__}: {str(e)[:120]}")
    return None


def run_schedule(now, cfg, events_path, st, deadline, get, log=None):
    from engine import load_events
    from tools.gemini_events import append_events, validate_schedule
    rec = st.setdefault("schedule", {})
    rec["last_attempt"] = _ts(now)
    entries, ok_req, errs = [], False, []
    used_url = None
    for name, url, required in CALENDAR_SOURCES:
        b = _fetch_source(st, name, url, now, deadline, get)
        srec = st["sources"][name]
        srec["optional"] = not required          # 다음 주 파일은 없을 때가 많음(404) — 상태 판정에 안 씀
        if b is None:
            if required:
                errs.append(f"{name}: {srec.get('error')}")
            continue
        try:
            got = parse_calendar(b)
            srec.update(ok=True, last_success=_ts(now), error=None, count=len(got))
            entries += got
            if required:
                ok_req = True
                used_url = url
        except Exception as e:
            srec.update(ok=False, error=f"파싱 실패: {str(e)[:120]}")
            if required:
                errs.append(f"{name}: 파싱 실패")
    if not ok_req:
        rec.update(ok=False, error=" / ".join(errs)[:300] or "알 수 없음")
        if log:
            log(f"경제 캘린더 받기 실패(일정 상태가 24시간 뒤 STALE 로 바뀜): {rec['error']}", warn=True)
        return []
    items = calendar_items(entries, used_url)
    existing = load_events(events_path)
    ok, bad = validate_schedule(items, now, existing, def_before=float(cfg.get("eventBeforeMin", 30)),
                                def_after=float(cfg.get("eventAfterMin", 30)))
    append_events(events_path, ok)
    rec.update(ok=True, last_success=_ts(now), error=None, received=len(entries), added=len(ok),
               skipped=[f"{(b0 or {}).get('name', '?')}: {why}" for b0, why in bad if why != "중복"][:10])
    if log:
        log(f"경제 캘린더: USD high/medium {len(entries)}건 받음, 새로 추가 {len(ok)}건")
    return ok


def run_breaking(now, cfg, events_path, st, deadline, get, log=None):
    from engine import load_events
    from tools.gemini_events import append_events, validate_breaking
    rec = st.setdefault("breaking", {})
    rec["last_attempt"] = _ts(now)
    arts, n_ok = [], 0
    for name, url in RSS_SOURCES:
        b = _fetch_source(st, name, url, now, deadline, get)
        if b is None:
            continue
        srec = st["sources"][name]
        try:
            got = parse_rss(b)
            srec.update(ok=True, last_success=_ts(now), error=None, count=len(got))
            for g in got:
                g["outlet"] = name          # news_v1(기록 전용)이 매체 구분에 씀 — 이 모듈 판정에는 안 쓰임
            arts += got
            n_ok += 1
        except Exception as e:
            srec.update(ok=False, error=f"파싱 실패: {str(e)[:120]}")
    # news_v1 기록 전용 파이프라인이 같은 실행에서 추가 요청 없이 재사용(판정·events.csv 에는 영향 없음)
    LAST_FETCH.clear()
    LAST_FETCH.update(at=now, articles=list(arts), sources_ok=n_ok,
                      error=None if n_ok else "모든 RSS 실패")
    if n_ok == 0:
        rec.update(ok=False, error="모든 RSS 실패")
        if log:
            log("코인 뉴스 RSS 모두 실패(돌발 감지 상태 STALE 가능)", warn=True)
        return []
    existing = load_events(events_path)
    items = breaking_items(arts, now, existing)
    ok, bad = validate_breaking(items, now, existing, per_day=int(cfg.get("eventBreakingMaxPerDay", 6)))
    append_events(events_path, ok)
    recent = sum(1 for x in arts if x.get("published") and x["published"] >= now - dt.timedelta(hours=LOOKBACK_H))
    rec.update(ok=True, last_success=_ts(now), error=None, received=len(arts), recent=recent, matched=len(items),
               added=len(ok), sources_ok=n_ok, skipped=[f"{(b0 or {}).get('name', '?')}: {why}" for b0, why in bad][:10])
    if log:
        log(f"코인 뉴스 RSS {n_ok}/{len(RSS_SOURCES)}곳: 최근 2시간 {recent}건, 규칙 해당 {len(items)}건, 추가 {len(ok)}건")
    return ok


def maybe_update(now, cfg, events_path, state_path, log=None, get=None):
    """시간이 되었으면 정기 일정·돌발을 받아 events.csv 에 추가. 반환: 추가된 줄 목록. 예외를 밖으로 내보내지 않음."""
    try:
        if cfg.get("eventAutoFetch", True) is False:
            return []
        get = get or http_get
        st = load_state(state_path)
        budget = min(float(cfg.get("eventFetchBudgetSec", BUDGET_SEC)), 60.0)
        deadline = time.monotonic() + budget
        added = []
        sch = float(cfg.get("eventScheduleHours", SCHEDULE_HOURS)) * 60
        brk = float(cfg.get("eventBreakingMin", BREAKING_MIN))
        try:
            if _due(st.get("schedule"), now, sch, RETRY_MIN):
                added += run_schedule(now, cfg, events_path, st, deadline, get, log)
                st["schedule"]["ok"] = bool(st["schedule"].get("ok"))
        except Exception as e:
            st.setdefault("schedule", {}).update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
            if log:
                log(f"경제 캘린더 처리 오류(무시): {str(e)[:200]}", warn=True)
        try:
            if _due(st.get("breaking"), now, brk, brk):
                added += run_breaking(now, cfg, events_path, st, deadline, get, log)
        except Exception as e:
            st.setdefault("breaking", {}).update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")
            if log:
                log(f"뉴스 RSS 처리 오류(무시): {str(e)[:200]}", warn=True)
        save_state(state_path, st)
        return added
    except Exception as e:
        if log:
            log(f"이벤트 피드 수집 오류(무시하고 계속): {str(e)[:200]}", warn=True)
        return []
