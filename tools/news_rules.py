# -*- coding: utf-8 -*-
"""
뉴스 기반 진입 금지 판단 v1 — 순수 규칙 모듈(네트워크·파일 없음, 표준 라이브러리만). 정의 문서: research/news/SCHEMA.md

역할 분리(2026-11-17 검토 결론):
  - Gemini 는 기사에서 '사실 + 근거 문장'만 뽑는다(EXTRACT_FIELDS). 등급 제안·올림/내림 같은 키는 읽지 않고 버린다.
  - 이 모듈(Python)이 근거 문장 대조(원문에 없는 인용이면 그 값은 null), 독립 출처 판정, 확인 수준, 대상 관계,
    결정표 적용, 차단 시작·종료·연장·보류(진행 중 장애)를 결정한다. 같은 입력 + 같은 RULES_VERSION → 같은 출력.
  - 차단 시간은 CANDIDATE_MIN 의 '후보값(미검증)'. 확정 기준이 아니며 research/news/PREREG_news_draft.md 측정 대상.
  - 표에 없는 조합은 'UNMAPPED' 로 기록만 한다(임의 판정 금지). UNKNOWN 은 표에 명시된 줄에서만 맞는다.
"""
import datetime as dt
import hashlib
import json
import re

RULES_VERSION = "news_rules_v1.0"
SCHEMA_VERSION = "news_extract_v1"
COINS = ("BTC", "ETH", "XRP", "SOL")

# ---------------- 독립 항목 값 ----------------
EVENT_TYPES = ("new_breach", "stolen_fund_move", "exchange_inflow", "withdrawal_restriction", "chain_outage",
               "regulatory_decision", "delisting_warning", "mass_liquidation", "macro_schedule", "other")
ASSET_ROLES = ("native_subject", "stolen_or_moved", "restricted", "delisted", "mentioned")
DESTINATIONS = ("own_wallets", "exchange", "mixer", "bridge_or_swap", "unknown")
SOURCE_TYPES = ("official", "onchain_analyst", "media", "anonymous", "other")
STATUSES = ("ongoing", "recovered", "ended", "unclear")
ENTITY_KINDS = ("exchange", "protocol", "chain", "issuer", "regulator", "person", "other")
NOVELTY = ("NEW", "REPEAT", "UNKNOWN")
RELATIONS = ("NATIVE", "MOVED_ASSET", "VENUE_OURS", "VENUE_MAJOR", "MARKET", "MENTION", "NONE", "UNKNOWN")
CONFIRM = ("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM", "UNVERIFIABLE")
CONFIRM_RANK = {"UNVERIFIABLE": 0, "SINGLE_CLAIM": 1, "INDEPENDENT": 2, "OFFICIAL": 3}
PERSIST = ("ONGOING", "RECOVERED", "UNCLEAR")
SCALE = ("LARGE", "MID", "SMALL", "UNKNOWN")
SCALE_RANK = {"UNKNOWN": -1, "SMALL": 0, "MID": 1, "LARGE": 2}
LARGE_USD = 100_000_000
MID_USD = 10_000_000

# ---------------- 차단 시간 후보값 (검증 전 · 확정 아님) ----------------
CANDIDATE_MIN = {"S": 180, "A": 120, "B": 60, "MACRO_IND": (30, 30), "FOMC": (60, 90)}
CANDIDATE_NOTE = "후보값(미검증) — PREREG_news 측정 전. 확정 기준으로 쓰지 않음"
RECHECK_MIN = 30          # 진행 중 장애 재확인 주기(후보값)
MAX_HOLD_H = 12           # 복구 확인 없이 보류를 이어 가는 상한(후보값) — 넘으면 '수동 확인 필요'로 멈춤
CLUSTER_DAYS = 14         # 같은 사건 묶음 조회 범위
TITLE_SIM = 0.6           # 제목 단어 Jaccard 이 이상이면 같은 원문 재배포로 본다

# ---------------- 출처 목록 ----------------
OUR_VENUES = ("upbit", "업비트", "bithumb", "빗썸")
MAJOR_VENUES = ("binance", "바이낸스", "coinbase", "코인베이스", "okx", "bybit", "kraken", "bitget", "kucoin",
                "hyperliquid", "htx", "gate.io")
# 공식 공지 출처: 기사 링크 도메인이 여기면 그 자체가 공식 공지. 인용 출처 이름이 OFFICIAL_ENTITIES 이고 type=official·
# 근거 문장이 원문에 있으면 '보도로 전달된 공식 확인'(OFFICIAL)로 본다.
OFFICIAL_DOMAINS = ("upbit.com", "bithumb.com", "binance.com", "coinbase.com", "kraken.com", "okx.com", "bybit.com",
                    "ethereum.org", "blog.ethereum.org", "solana.com", "status.solana.com", "ripple.com", "xrpl.org",
                    "sec.gov", "cftc.gov", "justice.gov", "fbi.gov", "fsc.go.kr", "fss.or.kr", "federalreserve.gov")
OFFICIAL_ENTITIES = ("upbit", "업비트", "bithumb", "빗썸", "binance", "coinbase", "kraken", "okx", "bybit",
                     "ethereum foundation", "solana foundation", "solana labs", "anza", "ripple", "xrpl foundation",
                     "sec", "cftc", "doj", "justice department", "department of justice", "fbi", "금융위원회",
                     "금융감독원", "federal reserve")
WIRE_PAT = re.compile(r"\((reuters|bloomberg|ap|afp|yonhap|연합뉴스)\)|\b(reuters|bloomberg) reported\b|"
                      r"\b(as first reported by|according to a report (by|from)|first reported by)\b", re.I)

# ---------------- Gemini 추출 스키마 ----------------
# (필드, 종류, 허용값|None, 근거 문장 필드|None) — 근거 필드가 있으면 근거가 원문에 있어야 값이 살아남는다.
EXTRACT_FIELDS = [
    ("event_type", "enum", EVENT_TYPES, "event_type_quote"),
    ("is_new_information", "bool", None, "new_information_quote"),
    ("event_time_utc", "time", None, "event_time_quote"),
    ("entity_name", "str", None, "entity_quote"),
    ("entity_kind", "enum", ENTITY_KINDS, "entity_quote"),
    ("venue_name", "str", None, "venue_quote"),
    ("damage_usd", "num", None, "damage_quote"),
    ("moved_usd", "num", None, "moved_quote"),
    ("exchange_inflow_usd", "num", None, "inflow_quote"),
    ("same_funds_as_earlier", "bool", None, "same_funds_quote"),
    ("destination", "enum", DESTINATIONS, "destination_quote"),
    ("status", "enum", STATUSES, "status_quote"),
    ("scheduled_in_advance", "bool", None, "scheduled_quote"),
    ("market_wide", "bool", None, "market_wide_quote"),
    ("credited_outlet", "str", None, "credited_quote"),
]
LIST_FIELDS = {"assets": ("symbol", "role", ASSET_ROLES), "sources_cited": ("name", "type", SOURCE_TYPES)}
IGNORED_KEYS = ("suggested_tier", "tier", "severity", "recommendation", "block", "should_block", "risk_level")


def _norm(s):
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def quote_ok(quote, text):
    """근거 문장이 원문(제목+요약[+본문])에 그대로 있는지(대소문자·공백·따옴표 정규화). 4자 미만 인용은 불인정."""
    q = _norm(quote)
    return isinstance(quote, str) and len(q) >= 4 and q in _norm(text)


def _num(x):
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def validate_extraction(raw, text):
    """Gemini JSON(dict) + 원문 → (facts dict, report). 형식이 틀리거나 근거가 원문에 없으면 그 값은 None.
    report = dict(ok, dropped[...], ignored[...], errors[...]). raw 가 dict 가 아니면 ok=False."""
    rep = dict(ok=True, dropped=[], ignored=[], errors=[])
    facts = {f: None for f, *_ in EXTRACT_FIELDS}
    facts.update(assets=[], sources_cited=[], evidence={})
    if not isinstance(raw, dict):
        rep.update(ok=False, errors=["추출 결과가 JSON 객체가 아님"])
        return facts, rep
    for k in raw:
        if k in IGNORED_KEYS:
            rep["ignored"].append(k)          # 등급 제안 등은 최종 판정에 쓰지 않음
    for f, kind, allowed, qf in EXTRACT_FIELDS:
        v = raw.get(f)
        if v is None:
            continue
        if kind == "enum":
            v = str(v).strip().lower()
            if v not in allowed:
                rep["errors"].append(f"{f}: 허용값 아님({v})")
                continue
        elif kind == "bool":
            if not isinstance(v, bool):
                rep["errors"].append(f"{f}: true/false 아님")
                continue
        elif kind == "num":
            v = _num(v)
            if v is None:
                rep["errors"].append(f"{f}: 숫자 아님")
                continue
        elif kind == "time":
            try:
                t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
                v = (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            except ValueError:
                rep["errors"].append(f"{f}: 시각 형식 아님")
                continue
        else:
            v = str(v).strip()[:120]
            if not v:
                continue
        q = raw.get(qf)
        if not quote_ok(q, text):
            rep["dropped"].append(f)           # 근거가 원문에 없음 → 모름(null)
            continue
        facts[f] = v
        facts["evidence"][f] = str(q).strip()[:300]
    for lf, (kn, kr, allowed) in LIST_FIELDS.items():
        items = raw.get(lf) or []
        if not isinstance(items, list):
            rep["errors"].append(f"{lf}: 목록 아님")
            continue
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            name = str(it.get(kn) or "").strip()
            role = str(it.get(kr) or "").strip().lower()
            if not name or role not in allowed:
                rep["errors"].append(f"{lf}: 항목 형식 오류")
                continue
            if not quote_ok(it.get("quote"), text):
                rep["dropped"].append(f"{lf}:{name}")
                continue
            facts[lf].append({kn: name.upper() if lf == "assets" else name, kr: role, "quote": str(it["quote"]).strip()[:300]})
    return facts, rep


# ---------------- 파생 항목 (Python 판정) ----------------

def novelty(facts):
    v = facts.get("is_new_information")
    return "NEW" if v is True else ("REPEAT" if v is False else "UNKNOWN")


def persistence(facts):
    s = facts.get("status")
    return {"ongoing": "ONGOING", "recovered": "RECOVERED", "ended": "RECOVERED"}.get(s, "UNCLEAR")


def _has(names, s):
    s = _norm(s)
    return bool(s) and any(re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", s) for n in names)


def venue_class(facts):
    v = facts.get("venue_name") or (facts.get("entity_name") if facts.get("entity_kind") == "exchange" else None)
    if not v:
        return "NONE"
    if _has(OUR_VENUES, v):
        return "OURS"
    if _has(MAJOR_VENUES, v):
        return "MAJOR"
    return "OTHER"


def effective_type(facts):
    """탈취 자금 이동인데 목적지가 거래소면 거래소 유입으로 본다(근거 문장 있는 destination 일 때만)."""
    et = facts.get("event_type")
    if et == "stolen_fund_move" and facts.get("destination") == "exchange":
        return "exchange_inflow"
    return et


def relation(facts, coin):
    """대상 관계 — 제목에 코인 이름이 있는지는 보지 않는다. 근거 있는 assets 역할·장소·시장 전체 여부로만."""
    et = facts.get("event_type")
    if et is None:
        return "UNKNOWN"
    roles = {a["role"] for a in facts.get("assets") or [] if a.get("symbol") == coin}
    specific = any(a["role"] in ("native_subject", "restricted", "delisted", "stolen_or_moved") for a in facts.get("assets") or [])
    vc = venue_class(facts)
    if roles & {"native_subject", "restricted", "delisted"}:
        return "NATIVE"
    if "stolen_or_moved" in roles:
        return "MOVED_ASSET"
    if vc == "OURS" and (et == "new_breach" or not specific):
        return "VENUE_OURS"
    if facts.get("market_wide") is True:
        return "MARKET"
    if vc == "MAJOR" and not specific:
        return "VENUE_MAJOR"
    if "mentioned" in roles:
        return "MENTION"
    return "NONE"


def scale(facts):
    """(종류, 금액, 등급). 피해액/이동액/유입액을 구분하고 섞거나 합산하지 않는다."""
    et = effective_type(facts)
    key = {"new_breach": "damage_usd", "stolen_fund_move": "moved_usd",
           "exchange_inflow": "exchange_inflow_usd"}.get(et)
    if et == "exchange_inflow" and facts.get("exchange_inflow_usd") is None and facts.get("event_type") == "stolen_fund_move":
        key = "moved_usd"     # 목적지가 거래소로 확인된 이동액 = 유입액
    if key is None:
        return None, None, "UNKNOWN"
    v = facts.get(key)
    if v is None:
        return key, None, "UNKNOWN"
    return key, v, "LARGE" if v >= LARGE_USD else ("MID" if v >= MID_USD else "SMALL")


def _tokens(title):
    return set(re.findall(r"[a-z0-9$]+|[가-힣]+", _norm(title))) - {"the", "a", "an", "in", "of", "to", "after", "for", "and", "on"}


def title_sim(a, b):
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _domain(url):
    m = re.match(r"^https?://([^/:?#]+)", url or "")
    return re.sub(r"^www\.", "", m.group(1).lower() if m else "")


def independent_origins(arts):
    """arts: [dict(id, outlet, title, summary, link, facts)] → (독립 출처 묶음 수, {id: 묶음번호}).
    같은 원문 재배포로 묶는 규칙(하나라도 맞으면 같은 묶음): 제목 Jaccard ≥ TITLE_SIM / 한쪽이 다른 쪽 매체를 원출처로 표기
    (credited_outlet) / 통신사·'first reported by' 표기가 같은 원출처 / 공식이 아닌 같은 1차 출처(온체인 분석사 등)를 인용."""
    n = len(arts)
    par = list(range(n))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    def u(i, j):
        par[f(i)] = f(j)
    keys = []
    squash = lambda s: re.sub(r"[^a-z0-9가-힣]+", "", _norm(s))    # noqa: E731  'The Block' == 'theblock'
    for a in arts:
        fa = a.get("facts") or {}
        src = {squash(s["name"]) for s in fa.get("sources_cited") or [] if s.get("type") != "official"}
        cred = squash(fa.get("credited_outlet") or "")
        wire = WIRE_PAT.search((a.get("title") or "") + " " + (a.get("summary") or ""))
        keys.append((src, cred, wire.group(0).lower() if wire else ""))
    for i in range(n):
        for j in range(i + 1, n):
            ai, aj = arts[i], arts[j]
            si, ci, wi = keys[i]
            sj, cj, wj = keys[j]
            same = title_sim(ai.get("title"), aj.get("title")) >= TITLE_SIM
            same = same or (ci and ci in squash(aj.get("outlet") or "")) or (cj and cj in squash(ai.get("outlet") or ""))
            same = same or (ci and ci == cj) or (wi and wi == wj) or bool(si & sj)
            if same:
                u(i, j)
    roots = {}
    comp = {}
    for i, a in enumerate(arts):
        comp[a["id"]] = roots.setdefault(f(i), len(roots))
    return len(roots), comp


def is_official(a):
    if _has(OFFICIAL_DOMAINS, _domain(a.get("link"))):
        return True
    fa = a.get("facts") or {}
    if any(s.get("type") == "official" and _has(OFFICIAL_ENTITIES, s.get("name")) for s in fa.get("sources_cited") or []):
        return True
    return False


def confirmation(arts):
    """공식 공지(또는 근거 있는 공식 인용)가 하나라도 있으면 OFFICIAL — 교차 보도가 없어도 낮추지 않는다.
    아니면 독립 출처 묶음 2개 이상 INDEPENDENT, 1개 SINGLE_CLAIM. 모든 기사가 익명 출처만 인용하면 UNVERIFIABLE."""
    if not arts:
        return "UNVERIFIABLE", 0
    if any(is_official(a) for a in arts):
        return "OFFICIAL", independent_origins(arts)[0]
    k, _ = independent_origins(arts)
    anon_only = all((a.get("facts") or {}).get("sources_cited") and
                    all(s.get("type") == "anonymous" for s in a["facts"]["sources_cited"]) for a in arts)
    if anon_only:
        return "UNVERIFIABLE", k
    return ("INDEPENDENT" if k >= 2 else "SINGLE_CLAIM"), k


FAMILY = {"new_breach": "security", "stolen_fund_move": "security", "exchange_inflow": "security",
          "withdrawal_restriction": "restriction", "chain_outage": "outage", "regulatory_decision": "regulatory",
          "delisting_warning": "delist", "mass_liquidation": "liquidation", "macro_schedule": "macro", "other": "other"}


def cluster_key(facts, article_id):
    """같은 사건 묶음 키 = 사건 계열 + 주체(정규화). 주체가 없으면 그 기사만의 묶음(합치지 않음)."""
    et = facts.get("event_type")
    ent = facts.get("entity_name") or facts.get("venue_name")
    ent = re.sub(r"[^a-z0-9가-힣]+", "", _norm(ent or ""))
    if not et or not ent:
        return f"solo:{article_id}"
    return f"{FAMILY.get(et, 'other')}:{ent}"


# ---------------- 결정표 ----------------
ANY = "*"
OK_CONF = ("OFFICIAL", "INDEPENDENT")
# 각 줄: (줄ID, 조건 dict, 결과 dict). 위에서부터 처음 맞는 줄. 조건 값 ANY 는 '알려진 값 아무거나'(UNKNOWN 제외).
# 조건 키: type(effective_type), novelty, relation, conf, scale, venue, persist, scheduled, dest
# 결과: action BLOCK|RECORD, key(후보 시간 키), hold(진행 중이면 복구 확인까지 보류), recheck(분)
TABLE = [
    ("D00", dict(type=("other",)), dict(action="RECORD")),
    ("D01", dict(type=("macro_schedule",)), dict(action="RECORD", note="거시 일정은 캘린더로 처리(MACRO_IND/FOMC)")),
    ("D02", dict(type=("mass_liquidation",)), dict(action="RECORD", note="청산은 가격 반응(관측치)이지 원인 아님")),
    ("D03", dict(novelty=("REPEAT",)), dict(action="RECORD", note="새 사실 없음 — 차단·연장 없음")),
    ("D04", dict(novelty=("NEW",), relation=("MENTION", "NONE")), dict(action="RECORD")),
    # 신규 침해
    ("D10", dict(type=("new_breach",), novelty=("NEW",), relation=("NATIVE",), conf=OK_CONF, scale=("LARGE",)),
     dict(action="BLOCK", key="S", hold=True, recheck=RECHECK_MIN)),
    ("D11", dict(type=("new_breach",), novelty=("NEW",), relation=("NATIVE",), conf=OK_CONF, scale=("MID", "SMALL", "UNKNOWN")),
     dict(action="BLOCK", key="A", hold=True, recheck=RECHECK_MIN)),
    ("D12", dict(type=("new_breach",), novelty=("NEW",), relation=("NATIVE",), conf=("SINGLE_CLAIM",), scale=("LARGE",)),
     dict(action="BLOCK", key="B", recheck=RECHECK_MIN)),
    ("D13", dict(type=("new_breach",), novelty=("NEW",), relation=("NATIVE",), conf=("SINGLE_CLAIM", "UNVERIFIABLE"),
                 scale=("MID", "SMALL", "UNKNOWN")), dict(action="RECORD", recheck=RECHECK_MIN)),
    ("D13b", dict(type=("new_breach",), novelty=("NEW",), relation=("NATIVE",), conf=("UNVERIFIABLE",), scale=("LARGE",)),
     dict(action="RECORD", recheck=RECHECK_MIN)),
    ("D14", dict(type=("new_breach",), novelty=("NEW",), relation=("VENUE_OURS",), conf=("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM")),
     dict(action="BLOCK", key="S", hold=True, recheck=RECHECK_MIN)),
    ("D15", dict(type=("new_breach",), novelty=("NEW",), relation=("MOVED_ASSET",), conf=OK_CONF, scale=("LARGE",)),
     dict(action="BLOCK", key="B")),
    ("D16", dict(type=("new_breach",), novelty=("NEW",), relation=("MOVED_ASSET", "VENUE_MAJOR", "MARKET")),
     dict(action="RECORD")),
    # 탈취 자금 이동(거래소가 아닌 곳으로) — 지갑 간 이동은 매도 증거가 아님
    ("D20", dict(type=("stolen_fund_move",), novelty=("NEW",), dest=("own_wallets", "mixer", "bridge_or_swap", "unknown", None)),
     dict(action="RECORD", recheck=RECHECK_MIN, note="지갑 간 이동·믹서·브리지 — 거래소 유입 확인 전까지 기록만")),
    # 거래소 유입(또는 목적지=거래소인 이동)
    ("D30", dict(type=("exchange_inflow",), novelty=("NEW",), relation=("MOVED_ASSET", "NATIVE"), conf=OK_CONF, scale=("LARGE",)),
     dict(action="BLOCK", key="B")),
    ("D31", dict(type=("exchange_inflow",), novelty=("NEW",)), dict(action="RECORD", recheck=RECHECK_MIN)),
    # 출금 제한
    ("D40", dict(type=("withdrawal_restriction",), novelty=("NEW",), scheduled=(True,)),
     dict(action="RECORD", note="사전 공지된 정기 점검")),
    ("D41", dict(type=("withdrawal_restriction",), novelty=("NEW",), relation=("NATIVE", "VENUE_OURS"), venue=("OURS",),
                 conf=("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM"), persist=("ONGOING", "UNCLEAR")),
     dict(action="BLOCK", key="A", hold=True, recheck=RECHECK_MIN)),
    ("D42", dict(type=("withdrawal_restriction",), novelty=("NEW",), relation=("VENUE_MAJOR",), conf=OK_CONF,
                 persist=("ONGOING", "UNCLEAR")), dict(action="BLOCK", key="A", hold=True, recheck=RECHECK_MIN)),
    ("D43", dict(type=("withdrawal_restriction",), novelty=("NEW",)), dict(action="RECORD")),
    # 체인 장애
    ("D50", dict(type=("chain_outage",), novelty=("NEW",), relation=("NATIVE",), conf=("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM"),
                 persist=("ONGOING", "UNCLEAR")), dict(action="BLOCK", key="S", hold=True, recheck=RECHECK_MIN)),
    ("D51", dict(type=("chain_outage",), novelty=("NEW",), relation=("NATIVE",), conf=("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM"),
                 persist=("RECOVERED",)), dict(action="BLOCK", key="B", note="복구 확인 뒤 꼬리 창")),
    ("D52", dict(type=("chain_outage",), novelty=("NEW",)), dict(action="RECORD", recheck=RECHECK_MIN)),
    # 규제 결정
    ("D60", dict(type=("regulatory_decision",), novelty=("NEW",), relation=("NATIVE",), conf=OK_CONF), dict(action="BLOCK", key="A")),
    ("D61", dict(type=("regulatory_decision",), novelty=("NEW",), relation=("MARKET",), conf=OK_CONF), dict(action="BLOCK", key="B")),
    ("D62", dict(type=("regulatory_decision",), novelty=("NEW",)), dict(action="RECORD")),
    # 상장폐지·유의
    ("D70", dict(type=("delisting_warning",), novelty=("NEW",), relation=("NATIVE",), venue=("OURS",),
                 conf=("OFFICIAL", "INDEPENDENT", "SINGLE_CLAIM")), dict(action="BLOCK", key="S")),
    ("D71", dict(type=("delisting_warning",), novelty=("NEW",), relation=("NATIVE",), venue=("MAJOR",), conf=OK_CONF),
     dict(action="BLOCK", key="B")),
    ("D72", dict(type=("delisting_warning",), novelty=("NEW",)), dict(action="RECORD")),
]
UNKNOWNISH = ("UNKNOWN", None)


def _match(cond, row):
    for k, allowed in cond.items():
        v = row.get(k)
        if allowed == ANY:
            if v in UNKNOWNISH:
                return False
        elif v not in allowed:
            return False
    return True


def features(facts, coin, conf):
    _k, _v, sc = scale(facts)
    return dict(type=effective_type(facts), novelty=novelty(facts), relation=relation(facts, coin), conf=conf, scale=sc,
                venue=venue_class(facts), persist=persistence(facts), scheduled=facts.get("scheduled_in_advance"),
                dest=facts.get("destination"))


def decide_coin(facts, coin, conf):
    """한 코인에 대한 결정 → dict(row, action, key, minutes, hold, recheck, note, feat). 맞는 줄 없으면 UNMAPPED."""
    feat = features(facts, coin, conf)
    if feat["type"] is None:
        return dict(row="-", action="UNMAPPED", key=None, minutes=0, hold=False, recheck=None, note="사건 종류 모름", feat=feat)
    for rid, cond, res in TABLE:
        if _match(cond, feat):
            key = res.get("key")
            return dict(row=rid, action=res["action"], key=key, minutes=CANDIDATE_MIN.get(key, 0) if key else 0,
                        hold=bool(res.get("hold")), recheck=res.get("recheck"), note=res.get("note", ""), feat=feat)
    return dict(row="-", action="UNMAPPED", key=None, minutes=0, hold=False, recheck=None, note="결정표에 없는 조합", feat=feat)


KEY_RANK = {None: 0, "B": 1, "A": 2, "S": 3}


def decide(facts, conf, coins=COINS):
    """기사(사건 묶음 확인 수준 반영) → 묶은 결정 dict(action, rows, targets, key, minutes, hold, recheck, per_coin).
    여러 코인이 BLOCK 이면 가장 긴 후보 키 하나로 묶고 대상 코인을 모은다. BLOCK 이 없고 UNMAPPED 가 하나라도 있으면 UNMAPPED."""
    per = {c: decide_coin(facts, c, conf) for c in coins}
    blocks = {c: d for c, d in per.items() if d["action"] == "BLOCK"}
    if blocks:
        best = max(blocks.values(), key=lambda d: (KEY_RANK[d["key"]], d["row"]))
        return dict(action="BLOCK", rows=sorted({d["row"] for d in blocks.values()}), targets=sorted(blocks, key=COINS.index),
                    key=best["key"], minutes=best["minutes"], hold=any(d["hold"] for d in blocks.values()),
                    recheck=min([d["recheck"] for d in blocks.values() if d["recheck"]] or [None]) if any(d["recheck"] for d in blocks.values()) else None,
                    per_coin=per)
    act = "UNMAPPED" if any(d["action"] == "UNMAPPED" for d in per.values()) else "RECORD"
    rec = [d["recheck"] for d in per.values() if d["recheck"]]
    return dict(action=act, rows=sorted({d["row"] for d in per.values()}), targets=[], key=None, minutes=0, hold=False,
                recheck=min(rec) if rec else None, per_coin=per)


# ---------------- 시각·창 ----------------

def ceil5(t):
    """다음 5분 경계(이미 경계면 그대로). effective_at ≥ decided_at 보장 → 소급 없음."""
    t = t.replace(second=0, microsecond=0) + (dt.timedelta(minutes=1) if (t.second or t.microsecond) else dt.timedelta(0))
    r = t.minute % 5
    return t + dt.timedelta(minutes=(5 - r) % 5)


def fact_signature(facts, conf):
    """연장 판단용 사실 요약 — 이 중 하나가 '올라가야' 새 사실로 본다."""
    _k, v, sc = scale(facts)
    return dict(type=effective_type(facts), conf_rank=CONFIRM_RANK.get(conf, 0), scale_rank=SCALE_RANK[sc], amount=v,
                persist=persistence(facts),
                coins=sorted({a["symbol"] for a in facts.get("assets") or [] if a["role"] != "mentioned"}))


EXT_TYPE_UP = {("stolen_fund_move", "exchange_inflow"), ("new_breach", "exchange_inflow")}


def extension_reason(prev_sig, new_sig):
    """사전 정의된 새 사실(EXTENSION_FACTS)이 있으면 그 이름, 없으면 None. 재배포(같은 사실)는 None."""
    if prev_sig is None:
        return "first"
    if new_sig["conf_rank"] > prev_sig["conf_rank"]:
        return "확인 수준 상승"
    if new_sig["scale_rank"] > prev_sig["scale_rank"]:
        return "규모 등급 상승"
    if prev_sig["amount"] and new_sig["amount"] and new_sig["amount"] >= 1.5 * prev_sig["amount"]:
        return "금액 1.5배 이상 증가"
    if set(new_sig["coins"]) - set(prev_sig["coins"]):
        return "새 대상 코인"
    if (prev_sig["type"], new_sig["type"]) in EXT_TYPE_UP:
        return "거래소 유입으로 전환"
    if prev_sig["persist"] == "ONGOING" and new_sig["persist"] == "RECOVERED":
        return "복구 확인"
    return None


EXTENSION_FACTS = ("확인 수준 상승", "규모 등급 상승", "금액 1.5배 이상 증가", "새 대상 코인", "거래소 유입으로 전환", "복구 확인")


def plan_window(decision, decided_at, cluster_windows, prev_sig, new_sig):
    """결정 → 새 창(dict(start, end, coins, hold)) 또는 None, 그리고 연장 표시.
    cluster_windows: 이 사건 묶음의 기존 창 목록 [dict(start, end, ...)] (시각순).
    - 시작 = ceil5(decided_at) (소급 없음)
    - 활성 창이 있으면 EXTENSION_FACTS 에 해당하는 새 사실이 있을 때만 새 창(연장), 아니면 'no_extend'."""
    if decision["action"] != "BLOCK":
        return None, "-"
    start = ceil5(decided_at)
    active = [w for w in cluster_windows if w["end"] > start]
    reason = extension_reason(prev_sig, new_sig) if cluster_windows else "first"
    if active and reason in (None, "first"):
        return None, "no_extend(새 사실 없음)"
    if cluster_windows and not active and reason is None:
        return None, "no_new_fact(같은 사실 재보도)"
    w = dict(start=start, end=start + dt.timedelta(minutes=decision["minutes"]), coins=decision["targets"],
             hold=decision["hold"], recheck=decision["recheck"] or RECHECK_MIN, key=decision["key"])
    return w, ("extend:" + reason) if active else ("new" if reason == "first" else "new:" + reason)


def hold_step(hold, now):
    """진행 중 장애 보류 한 단계. hold = dict(started, last_end, recovered(bool), recheck, coins)
    → ('release', 사유) | ('cap', 사유) | ('append', 새 창 dict) | ('wait', None).
    고정 시간이 끝났다고 자동 해제하지 않는다: 복구 확인(recovered) 또는 상한(MAX_HOLD_H, 수동 확인 필요)만 끝낸다."""
    if hold.get("recovered"):
        return "release", "복구 확인"
    if now - hold["started"] >= dt.timedelta(hours=MAX_HOLD_H):
        return "cap", f"보류 {MAX_HOLD_H}시간 상한 — 복구 확인 없음, 수동 확인 필요"
    rc = dt.timedelta(minutes=hold.get("recheck") or RECHECK_MIN)
    if hold["last_end"] - now <= rc:
        start = max(hold["last_end"], ceil5(now))
        return "append", dict(start=start, end=start + rc, coins=hold["coins"], hold=True, recheck=hold.get("recheck"),
                              key="HOLD")
    return "wait", None


# ---------------- 거시 일정(캘린더) → 후보 창 ----------------
FOMC_PAT = re.compile(r"federal funds rate|fomc statement|fomc press conference|interest rate decision", re.I)


def macro_window(name):
    """캘린더 high 일정 이름 → (키, before, after). FOMC 결정·기자회견만 FOMC, 나머지(의사록 포함)는 지표."""
    if FOMC_PAT.search(name or ""):
        b, a = CANDIDATE_MIN["FOMC"]
        return "FOMC", b, a
    b, a = CANDIDATE_MIN["MACRO_IND"]
    return "MACRO_IND", b, a


def table_hash():
    """결정표·후보값 지문(규칙 버전과 함께 저장 — 표가 바뀌었는데 버전을 안 올린 실수를 잡기 위함)."""
    s = json.dumps([RULES_VERSION, [(r, {k: list(v) if isinstance(v, tuple) else v for k, v in c.items()}, x) for r, c, x in TABLE],
                    CANDIDATE_MIN, RECHECK_MIN, MAX_HOLD_H, TITLE_SIM], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]
