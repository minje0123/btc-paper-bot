# -*- coding: utf-8 -*-
"""
뉴스 판정 v1 — 기록 전용 그림자 파이프라인 (수집 → Gemini 사실 추출 → tools/news_rules 결정). 2026-11-17

- 본 장부·baseline·v4 장부의 뉴스 정책(data/events.csv, event_feeds)은 건드리지 않는다. 이 모듈의 결정은
  news_v1 전용 창 파일(state/news_v1/windows.csv)에만 쓰이고, 그 파일은 그림자 장부 news_v1_5m 만 읽는다.
- 저장(재현성): state/news_v1/ (CI 가 커밋하는 폴더)
    articles.jsonl     받은 기사(제목·요약·링크·매체·published_at·received_at·기존 규칙 판정) — 같은 입력
    extractions.jsonl  Gemini 요청·응답 원문(모델·프롬프트 버전·프롬프트 해시) — 재판정은 이 저장본으로(다시 부르지 않음)
    decisions.csv      판정 기록(5종 시각, 추출 항목·근거 문장, 규칙 버전, 결정, 기존 판정과의 차이)
    windows.csv        news_v1 차단 창(engine.load_events 형식, added_at = decided_at)
    state.json         수집기·추출기 상태, 대기열, 사건 묶음(창·사실 요약·보류)
  매 실행 research/news/ 로 decisions.csv·news_events.csv(=windows.csv)를 복사한다.
- 실패 구분: 수집기 실패(collector) ≠ Gemini 실패(extractor). 어느 쪽도 '이벤트 없음'으로 바꾸지 않고, 추출 못 한 기사는
  NOT_EXTRACTED / EXTRACTOR_FAILED 로 기록한다(차단 등급을 임의로 주지 않음).
- 네트워크: 수집은 event_feeds 가 이번 실행에 받은 RSS 를 그대로 재사용(추가 요청 없음). event_feeds 가 이번에 안 받았고
  30분이 지났으면 직접 받는다. 요청마다 ≤15초, 전체는 호출자가 준 budget_s(뉴스 관련 실행당 30초 − event_feeds 사용분).
  Gemini 실행당 최대 3건, 429·할당량이면 state/reviews.json 의 backoff_until(복기 해설과 같은 키·같은 24시간 백오프)을 쓴다.
- 어떤 오류도 밖으로 내보내지 않는다(봇 실행을 멈추지 않음).
"""
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import time

from engine import KST, load_events, parse_kst
from tools import news_rules as NR

PROMPT_VERSION = "news_prompt_v1"
MAX_CALLS = 3
CALL_TIMEOUT = 15
MIN_LEFT_FOR_CALL = 5.0
MAX_FAILS = 3
QUEUE_MAX_H = 6            # 받은 지 이 시간이 지나도록 추출 못 한 기사는 NOT_EXTRACTED(늦은 차단 방지)
LOOKBACK_H = 2.0           # 받을 때 이보다 오래된 기사는 대기열에 넣지 않음(기록만)
COLLECT_MIN = 30.0
COLLECT_STALE_MIN = 120.0
TFMT = "%Y-%m-%d %H:%M:%S"
EXTRA_PREFILTER = [r"\bstolen\b", r"\blaunder", r"\btornado cash\b", r"\bmixer\b", r"\binflows?\b.{0,30}\bexchange",
                   r"\bdeposit(ed|s)?\b.{0,40}\b(binance|coinbase|okx|bybit|kraken|upbit|bithumb)\b",
                   r"\bmoves?\b.{0,40}\b(stolen|hacked|exploit)", r"\bseiz(e|ed|ure)\b", r"\bfrozen\b", r"\bsuspen"]
DEC_COLS = ["decided_at", "kind", "article_id", "url", "outlet", "title", "event_at", "published_at", "received_at",
            "effective_at", "window_end", "rules_version", "table_hash", "schema_version", "prompt_version", "model",
            "extractor_state", "event_type", "novelty", "relations", "scale", "confirmation", "independent_origins",
            "persistence", "destination", "observed_reaction", "evidence", "dropped_fields", "ignored_keys", "rows",
            "decision", "targets", "duration_key", "duration_min", "release", "recheck_min", "extension", "cluster",
            "legacy_decision", "diff"]
WIN_COLS = ["time_kst", "name", "importance", "coins", "before_min", "after_min", "added_at", "source"]


def paths_for(root):
    st = os.path.join(root, "state", "news_v1")
    rs = os.path.join(root, "research", "news")
    return dict(store=st, articles=os.path.join(st, "articles.jsonl"), extractions=os.path.join(st, "extractions.jsonl"),
                decisions=os.path.join(st, "decisions.csv"), windows=os.path.join(st, "windows.csv"),
                state=os.path.join(st, "state.json"), research=rs, events_csv=os.path.join(root, "data", "events.csv"),
                reviews=os.path.join(root, "state", "reviews.json"))


def _ts(t):
    return t.strftime(TFMT) if t else ""


def _p(s):
    try:
        return parse_kst(s) if s else None
    except ValueError:
        return None


def aid(link):
    return hashlib.sha1((link or "").strip().encode("utf-8")).hexdigest()[:12]


# ============================== 저장 ==============================

def load_state(p):
    try:
        with open(p["state"], encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(p, st):
    os.makedirs(p["store"], exist_ok=True)
    st["note"] = ("news_v1 기록 전용 파이프라인 상태(봇이 갱신). 본 장부·baseline·v4 뉴스 정책과 무관. "
                  "collector=뉴스 수집기, extractor=Gemini 사실 추출 — 두 실패는 따로 기록.")
    tmp = p["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, default=str)
        f.write("\n")
    os.replace(tmp, p["state"])


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def append_jsonl(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def append_csv(path, cols, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


# ============================== 수집 ==============================

def prefilter(title, summary):
    from tools import event_feeds as ef
    s = ((title or "") + " " + (summary or "")).lower()
    cats = [key for key, _ko, _w, _wide, _d, pats in ef.CATEGORIES if any(re.search(x, s) for x in pats)]
    if any(re.search(x, s) for x in EXTRA_PREFILTER):
        cats.append("fund_move")
    return cats


def collect(now, st, shared, deadline, get, log=None):
    """→ 기사 목록(이번에 받은 것 전부). st['collector'] 갱신. 실패는 상태로만."""
    from tools import event_feeds as ef
    col = st.setdefault("collector", {})
    if shared and shared.get("at") == now:
        col.update(last_attempt=_ts(now), mode="shared(event_feeds)")
        if shared.get("sources_ok", 0) > 0:
            col.update(ok=True, last_success=_ts(now), error=None, sources_ok=shared.get("sources_ok"))
            return list(shared.get("articles") or [])
        col.update(ok=False, error=shared.get("error") or "모든 RSS 실패")
        return []
    last = _p(col.get("last_attempt"))
    if last and now - last < dt.timedelta(minutes=COLLECT_MIN):
        return []
    col.update(last_attempt=_ts(now), mode="own")
    arts, n_ok, errs = [], 0, []
    for name, url in ef.RSS_SOURCES:
        left = deadline - time.monotonic()
        if left < 1.0:
            errs.append(f"{name}: 시간 예산 초과로 건너뜀")
            continue
        try:
            got = ef.parse_rss(get(url, min(15.0, left)))
            for g in got:
                g["outlet"] = name
            arts += got
            n_ok += 1
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}: {str(e)[:80]}")
    if n_ok:
        col.update(ok=True, last_success=_ts(now), error=" / ".join(errs)[:300] or None, sources_ok=n_ok)
    else:
        col.update(ok=False, error=" / ".join(errs)[:300] or "모든 RSS 실패")
        if log:
            log("news_v1 수집기: 모든 RSS 실패(추출기 실패와 별개로 기록)", warn=True)
    return arts


def legacy_for(link, title, summary, legacy_events):
    """기존 event_feeds 판정: data/events.csv 에 이 링크로 추가된 줄(=실제 차단 여부) + 키워드 규칙 결과."""
    from tools import event_feeds as ef
    rows = [e for e in legacy_events if (e[7] if len(e) > 7 else "") == link]
    c = ef.classify(title, summary)
    rule = f"{c['importance']}:{c['coins']}:{c['cat']}" if c else "해당 없음"
    if rows:
        e = rows[0]
        end = e[0] + dt.timedelta(minutes=e[5] or 0)
        cs = "ALL" if e[3] is None else "|".join(sorted(e[3]))
        return dict(block=e[2] == "high", text=f"{e[2]} {cs} {e[0]:%m-%d %H:%M}~{end:%H:%M} (규칙 {rule})")
    return dict(block=False, text=f"추가 안 됨 (규칙 {rule})")


def ingest(now, arts, st, p, legacy_events):
    """새 기사 → articles.jsonl. 사전 필터(사건 키워드)에 걸린 것만 저장, 받을 때 2시간 안 기사만 대기열."""
    known = st.setdefault("known_ids", [])
    ks = set(known)
    q = st.setdefault("queue", {})
    new = []
    for a in arts:
        link = (a.get("link") or "").strip()
        if not re.match(r"^https?://", link) or not a.get("title"):
            continue
        i = aid(link)
        if i in ks:
            continue
        cats = prefilter(a.get("title"), a.get("summary"))
        if not cats:
            continue
        ks.add(i)
        known.append(i)
        pub = a.get("published")
        fresh = pub is not None and now - dt.timedelta(hours=LOOKBACK_H) <= pub <= now + dt.timedelta(minutes=10)
        rec = dict(id=i, link=link, outlet=a.get("outlet") or _outlet(link), title=a["title"], summary=a.get("summary") or "",
                   published_at=_ts(pub), received_at=_ts(now), prefilter=cats,
                   legacy=legacy_for(link, a["title"], a.get("summary"), legacy_events))
        new.append(rec)
        q[i] = dict(status="queued" if fresh else "old_at_receipt", fails=0)
    del known[:-5000]
    append_jsonl(p["articles"], new)
    return new


def _outlet(link):
    m = re.match(r"^https?://(?:www\.)?([^/]+)", link or "")
    return m.group(1) if m else ""


# ============================== Gemini 추출 ==============================

PROMPT = """You extract FACTS from one crypto news item. Do NOT judge severity, do NOT recommend actions, do NOT add tiers.
Use ONLY the ARTICLE TEXT below. Every value must be backed by a quote copied VERBATIM (exact substring) from the
ARTICLE TEXT in the matching *_quote field. If the text does not state something, use null (and null quote).
Return ONE JSON object with exactly these keys:
{
 "event_type": one of ["new_breach","stolen_fund_move","exchange_inflow","withdrawal_restriction","chain_outage","regulatory_decision","delisting_warning","mass_liquidation","macro_schedule","other"] or null,
 "event_type_quote": str|null,
 "is_new_information": true if the text reports a fact that happened/was first disclosed now (not a recap of an earlier event), false if it only recaps, else null,
 "new_information_quote": str|null,
 "event_time_utc": "YYYY-MM-DDTHH:MMZ" only if an explicit time is stated, else null, "event_time_quote": str|null,
 "entity_name": main affected entity (exchange/protocol/chain/issuer) or null, "entity_kind": one of ["exchange","protocol","chain","issuer","regulator","person","other"] or null, "entity_quote": str|null,
 "venue_name": exchange where it happened or null, "venue_quote": str|null,
 "assets": [{"symbol": ticker like "ETH", "role": one of ["native_subject","stolen_or_moved","restricted","delisted","mentioned"], "quote": str}],
 "damage_usd": number|null (amount stolen/lost in THIS incident), "damage_quote": str|null,
 "moved_usd": number|null (amount moved in THIS transfer), "moved_quote": str|null,
 "exchange_inflow_usd": number|null (amount deposited to an exchange), "inflow_quote": str|null,
 "same_funds_as_earlier": true if the moved funds are from an earlier-reported theft, else false/null, "same_funds_quote": str|null,
 "destination": one of ["own_wallets","exchange","mixer","bridge_or_swap","unknown"] or null, "destination_quote": str|null,
 "sources_cited": [{"name": str, "type": one of ["official","onchain_analyst","media","anonymous","other"], "quote": str}],
 "status": one of ["ongoing","recovered","ended","unclear"] or null, "status_quote": str|null,
 "scheduled_in_advance": true/false/null (announced maintenance), "scheduled_quote": str|null,
 "market_wide": true/false/null (affects the whole crypto market), "market_wide_quote": str|null,
 "credited_outlet": other news outlet credited as original reporter or null, "credited_quote": str|null
}
ARTICLE TEXT:
"""


def article_text(a):
    return f"{a.get('title') or ''}\n{a.get('summary') or ''}"


def build_prompt(a):
    return PROMPT + article_text(a)


def default_call(prompt, key, model, timeout):
    from tools import gemini_client as gc
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 1500, "responseMimeType": "application/json"}}
    _st, d = gc._http("POST", f"{gc.API}/models/{model}:generateContent", key, body, timeout=timeout)
    return gc._text_of(d)


def _backoff_until(p, now):
    try:
        from tools import review as rvw
        bo = rvw.load_reviews(p["reviews"]).get("backoff_until")
        if bo and now < dt.datetime.strptime(bo[:19], TFMT).replace(tzinfo=now.tzinfo):
            return bo
    except Exception:
        pass
    return None


def _set_backoff(p, now, err):
    from tools import review as rvw
    d = rvw.load_reviews(p["reviews"])
    until = now + dt.timedelta(hours=rvw.GEMINI_BACKOFF_H)
    d["backoff_until"] = until.strftime(TFMT)
    d["backoff_reason"] = ("news_v1: " + str(err))[:200]
    rvw.save_reviews(p["reviews"], d)
    return d["backoff_until"]


def extract(now, st, p, arts_by_id, deadline, key, model, call, log=None):
    """대기열 → 최대 MAX_CALLS 건 추출. 반환: [(기사, 추출 기록)] 이번에 성공한 것. 실패·만료는 decisions 로 기록할 목록도 반환."""
    from tools import review as rvw
    ex = st.setdefault("extractor", {})
    q = st.setdefault("queue", {})
    done, failed_rows = [], []
    # 만료: 받은 지 QUEUE_MAX_H 넘은 대기 기사
    for i, qq in q.items():
        if qq.get("status") == "queued":
            a = arts_by_id.get(i)
            r = _p(a.get("received_at")) if a else None
            if r is None or now - r > dt.timedelta(hours=QUEUE_MAX_H):
                qq["status"] = "stale"
                if a:
                    failed_rows.append((a, "NOT_EXTRACTED", f"{QUEUE_MAX_H}시간 안에 추출 못 함({ex.get('state') or '-'})"))
    pend = sorted([arts_by_id[i] for i, qq in q.items() if qq.get("status") == "queued" and i in arts_by_id],
                  key=lambda a: (a.get("published_at") or "", a["id"]), reverse=True)
    ex["queue"] = len(pend)
    if not pend:
        ex.setdefault("state", "IDLE")
        return done, failed_rows
    if not key:
        ex.update(state="NO_KEY", last_attempt=_ts(now))
        return done, failed_rows
    bo = _backoff_until(p, now)
    if bo:
        ex.update(state="BACKOFF", backoff_until=bo)
        return done, failed_rows
    calls = 0
    for a in pend:
        left = deadline - time.monotonic()
        if calls >= MAX_CALLS or left < MIN_LEFT_FOR_CALL:
            break
        calls += 1
        prompt = build_prompt(a)
        rec = dict(id=a["id"], requested_at=_ts(now), model=model, prompt_version=PROMPT_VERSION,
                   prompt_sha=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16], schema_version=NR.SCHEMA_VERSION)
        ex["last_attempt"] = _ts(now)
        try:
            txt = call(prompt, key, model, min(float(CALL_TIMEOUT), left))
            rec["raw_text"] = (txt or "")[:6000]
            from tools import gemini_client as gc
            gc.extract_json(txt)              # 형식 확인(값 검증은 news_rules)
            rec["ok"] = True
            q[a["id"]]["status"] = "done"
            ex.update(state="OK", last_success=_ts(now), last_error=None)
            done.append((a, rec))
        except Exception as e:
            rec.update(ok=False, error=str(e)[:300])
            if rvw.QUOTA_RE.search(str(e)):
                ex.update(state="BACKOFF", last_error=str(e)[:200], backoff_until=_set_backoff(p, now, e))
                append_jsonl(p["extractions"], [rec])
                if log:
                    log(f"news_v1 추출기: 429/할당량 → {ex['backoff_until'][:16]} 까지 Gemini 쉼(복기 해설과 같은 백오프)", warn=True)
                break
            qq = q[a["id"]]
            qq["fails"] = int(qq.get("fails", 0)) + 1
            ex.update(state="FAIL", last_error=str(e)[:200])
            if qq["fails"] >= MAX_FAILS:
                qq["status"] = "failed"
                failed_rows.append((a, "EXTRACTOR_FAILED", f"{MAX_FAILS}회 실패: {str(e)[:120]}"))
        append_jsonl(p["extractions"], [rec])
    ex["queue"] = sum(1 for qq in q.values() if qq.get("status") == "queued")
    return done, failed_rows


# ============================== 관측 반응 ==============================

def observed_reaction(candles, at):
    """결정 시점까지 '닫힌' 5분봉만: 최근 3봉 (고가−저가)/종가 최대 ÷ 지난 7일 같은 시간대(±1시간) 봉의 중앙값.
    관측치일 뿐(원인 증거 아님) — v1 결정표에는 쓰지 않는다."""
    if not candles:
        return "UNKNOWN"
    ts = [t for t in sorted(candles) if t + dt.timedelta(minutes=5) <= at]
    if len(ts) < 50:
        return "UNKNOWN"
    rr = lambda t: (candles[t][1] - candles[t][2]) / candles[t][3] if candles[t][3] else 0.0   # noqa: E731
    last = ts[-3:]
    cur = max(rr(t) for t in last)
    vol = sum(candles[t][4] for t in last) / 3
    base = [t for t in ts if at - dt.timedelta(days=7) <= t < at - dt.timedelta(hours=3)
            and abs(((t.hour * 60 + t.minute) - (at.hour * 60 + at.minute) + 720) % 1440 - 720) <= 60]
    if len(base) < 30:
        return "UNKNOWN"
    rs = sorted(rr(t) for t in base)
    vs = sorted(candles[t][4] for t in base)
    med, vmed = rs[len(rs) // 2], vs[len(vs) // 2]
    if med <= 0:
        return "UNKNOWN"
    x = cur / med
    cls = "EXTREME" if x >= 4 else ("ELEVATED" if x >= 2 else "NORMAL")
    return f"{cls} range×{x:.2f} vol×{(vol / vmed if vmed else 0):.2f}"


# ============================== 판정 ==============================

def _coins_txt(cs):
    return "ALL" if set(cs) == set(NR.COINS) else "|".join(cs)


def judge_article(a, rec, cluster_arts, cl, decided_at, reaction="UNKNOWN"):
    """순수 함수: 기사 + 저장된 추출 기록 + 같은 묶음 기사들(facts 포함) + 묶음 상태 → (결정 행, 새 창|None, 새 묶음 상태).
    같은 입력 + 같은 RULES_VERSION → 같은 출력(재현성 테스트 대상)."""
    from tools import gemini_client as gc
    try:
        raw = gc.extract_json(rec.get("raw_text") or "")
    except Exception:
        raw = None
    facts, rep = NR.validate_extraction(raw, article_text(a))
    me = dict(a, facts=facts)
    arts = [x for x in cluster_arts if x["id"] != a["id"]] + [me]
    conf, k = NR.confirmation(arts)
    dec = NR.decide(facts, conf)
    cl = json.loads(json.dumps(cl or {}, default=str))
    wins = [dict(start=_p(w["start"]), end=_p(w["end"])) for w in cl.get("windows", [])]
    new_sig = NR.fact_signature(facts, conf)
    w, ext = NR.plan_window(dec, decided_at, wins, cl.get("sig"), new_sig)
    if dec["action"] == "BLOCK" or not cl.get("sig"):
        prev = cl.get("sig") or {}
        cl["sig"] = dict(new_sig, conf_rank=max(new_sig["conf_rank"], prev.get("conf_rank", -1)),
                         scale_rank=max(new_sig["scale_rank"], prev.get("scale_rank", -2)),
                         amount=max([x for x in (new_sig["amount"], prev.get("amount")) if x is not None], default=None),
                         coins=sorted(set(new_sig["coins"]) | set(prev.get("coins") or [])))
    pers = NR.persistence(facts)
    if w:
        cl.setdefault("windows", []).append(dict(start=_ts(w["start"]), end=_ts(w["end"]), coins=w["coins"], key=w["key"]))
        if w["hold"] and pers != "RECOVERED":
            h = cl.get("hold") or dict(started=_ts(decided_at), recovered=False)
            h.update(last_end=_ts(w["end"]), recheck=w["recheck"], coins=w["coins"], active=True)
            cl["hold"] = h
    if pers == "RECOVERED" and cl.get("hold", {}).get("active"):
        cl["hold"]["recovered"] = True
    k_, v_, sc = NR.scale(facts)
    rel = "|".join(f"{c}:{dec['per_coin'][c]['feat']['relation']}" for c in NR.COINS)
    release = ""
    if dec["action"] == "BLOCK":
        release = (f"복구 확인까지 보류(재확인 {w['recheck'] if w else NR.RECHECK_MIN}분, 상한 {NR.MAX_HOLD_H}시간)"
                   if dec["hold"] and pers != "RECOVERED" else "고정 후보 시간 종료")
    row = dict(decided_at=_ts(decided_at), kind="article", article_id=a["id"], url=a["link"], outlet=a.get("outlet"),
               title=a.get("title"), event_at=facts.get("event_time_utc") or "", published_at=a.get("published_at"),
               received_at=a.get("received_at"), effective_at=_ts(w["start"]) if w else "", window_end=_ts(w["end"]) if w else "",
               rules_version=NR.RULES_VERSION, table_hash=NR.table_hash(), schema_version=NR.SCHEMA_VERSION,
               prompt_version=rec.get("prompt_version"), model=rec.get("model"), extractor_state="OK" if raw is not None else "BAD_JSON",
               event_type=facts.get("event_type") or "UNKNOWN", novelty=NR.novelty(facts), relations=rel,
               scale=f"{k_ or '-'}:{'' if v_ is None else int(v_)}:{sc}", confirmation=conf, independent_origins=k,
               persistence=pers, destination=facts.get("destination") or "UNKNOWN", observed_reaction=reaction,
               evidence=json.dumps(dict(facts["evidence"], assets=[(x["symbol"], x["role"], x["quote"]) for x in facts["assets"]],
                                        sources=[(x["name"], x["type"], x["quote"]) for x in facts["sources_cited"]]),
                                   ensure_ascii=False)[:2000],
               dropped_fields="|".join(rep["dropped"]), ignored_keys="|".join(rep["ignored"]), rows="|".join(dec["rows"]),
               decision=dec["action"], targets=_coins_txt(dec["targets"]) if dec["targets"] else "",
               duration_key=dec["key"] or "", duration_min=dec["minutes"] or "", release=release, recheck_min=dec["recheck"] or "",
               extension=ext, cluster=NR.cluster_key(facts, a["id"]))
    row.update(_diff(a.get("legacy") or {}, row))
    return row, w, cl, facts


def _diff(legacy, row):
    lb = bool(legacy.get("block"))
    nb = bool(row.get("effective_at"))
    d = "둘 다 차단" if lb and nb else ("기존만 차단" if lb else ("새 판정만 차단" if nb else "둘 다 차단 없음"))
    return dict(legacy_decision=legacy.get("text", "-"), diff=d)


def _win_row(w, decided_at, a, rows_txt):
    return dict(time_kst=w["start"].strftime("%Y-%m-%d %H:%M"),
                name=f"[news_v1 {rows_txt}] {_coins_txt(w['coins'])}: {(a.get('title') or '')[:50]}",
                importance="high", coins=_coins_txt(w["coins"]), before_min="0",
                after_min=str(int((w["end"] - w["start"]).total_seconds() // 60)), added_at=_ts(decided_at), source=a.get("link", ""))


def fail_row(a, kind, why, now):
    row = dict(decided_at=_ts(now), kind="article", article_id=a["id"], url=a["link"], outlet=a.get("outlet"), title=a.get("title"),
               published_at=a.get("published_at"), received_at=a.get("received_at"), rules_version=NR.RULES_VERSION,
               table_hash=NR.table_hash(), schema_version=NR.SCHEMA_VERSION, prompt_version=PROMPT_VERSION,
               extractor_state=kind, decision=kind, release=why, extension="-")
    row.update(_diff(a.get("legacy") or {}, row))
    return row


def recheck_holds(now, st, arts_by_id):
    """진행 중 보류 묶음: 재확인 주기마다 다음 창을 미리(added_at=now ≤ 시작) 붙이거나, 복구 확인·상한이면 끝낸다."""
    rows, wins = [], []
    for ck, cl in sorted((st.get("clusters") or {}).items()):
        h = cl.get("hold")
        if not h or not h.get("active"):
            continue
        hh = dict(started=_p(h["started"]), last_end=_p(h["last_end"]), recovered=h.get("recovered"),
                  recheck=h.get("recheck"), coins=h.get("coins"))
        act, x = NR.hold_step(hh, now)
        a = arts_by_id.get(cl.get("first_article")) or dict(id="-", link="", title=ck)
        if act in ("release", "cap"):
            h["active"] = False
            rows.append(dict(decided_at=_ts(now), kind="hold", cluster=ck, title=a.get("title"), url=a.get("link"),
                             rules_version=NR.RULES_VERSION, table_hash=NR.table_hash(), decision="HOLD_" + act.upper(),
                             release=x, extension="-", diff="-"))
        elif act == "append":
            h["last_end"] = _ts(x["end"])
            cl.setdefault("windows", []).append(dict(start=_ts(x["start"]), end=_ts(x["end"]), coins=x["coins"], key="HOLD"))
            wins.append(_win_row(x, now, a, "HOLD"))
            rows.append(dict(decided_at=_ts(now), kind="hold", cluster=ck, title=a.get("title"), url=a.get("link"),
                             rules_version=NR.RULES_VERSION, table_hash=NR.table_hash(), decision="HOLD_CONTINUE",
                             effective_at=_ts(x["start"]), window_end=_ts(x["end"]), targets=_coins_txt(x["coins"]),
                             duration_key="HOLD", duration_min=int((x["end"] - x["start"]).total_seconds() // 60),
                             release="복구 확인 없음 — 재확인 주기만큼 연장", extension="hold", diff="-"))
    return rows, wins


def _cluster_articles(ck, st, arts_by_id, now):
    out = []
    for i, f in (st.get("facts") or {}).items():
        if f.get("cluster") == ck and i in arts_by_id:
            r = _p(arts_by_id[i].get("received_at"))
            if r and now - r <= dt.timedelta(days=NR.CLUSTER_DAYS):
                out.append(dict(arts_by_id[i], facts=f["facts"]))
    return out


def run(now, cfg, p, candles=None, budget_s=30.0, shared=None, log=None, key=None, call=None, get=None):
    """한 번 실행. 반환: status dict(news_v1). 예외를 밖으로 내보내지 않는다."""
    t0 = time.monotonic()
    deadline = t0 + max(0.0, min(30.0, float(budget_s)))
    st = load_state(p)
    try:
        from tools import event_feeds as ef
        from tools import gemini_client as gc
        if not st.get("active_from"):
            st["active_from"] = _ts(now)
        get = get or ef.http_get
        key = key if key is not None else gc.api_key()
        call = call or default_call
        model = (cfg.get("newsV1") or {}).get("model") or cfg.get("geminiModel") or gc.DEFAULT_MODEL
        legacy_events = load_events(p["events_csv"])
        try:
            arts = collect(now, st, shared, deadline, get, log)
        except Exception as e:
            st.setdefault("collector", {}).update(ok=False, error=f"{type(e).__name__}: {str(e)[:150]}")
            arts = []
        ingest(now, arts, st, p, legacy_events)
        arts_by_id = {a["id"]: a for a in read_jsonl(p["articles"])}
        try:
            done, failed = extract(now, st, p, arts_by_id, deadline, key, model, call, log)
        except Exception as e:
            st.setdefault("extractor", {}).update(state="FAIL", last_error=f"{type(e).__name__}: {str(e)[:150]}")
            done, failed = [], []
        dec_rows, win_rows = [], []
        for a, why_kind, why in failed:
            dec_rows.append(fail_row(a, why_kind, why, now))
        clusters = st.setdefault("clusters", {})
        facts_cache = st.setdefault("facts", {})
        for a, rec in done:
            coin_c = None
            try:
                row0, _w0, _c0, facts = judge_article(a, rec, [], {}, now)
                ck = NR.cluster_key(facts, a["id"])
                ca = _cluster_articles(ck, st, arts_by_id, now)
                tgt = [x["symbol"] for x in facts["assets"] if x["symbol"] in NR.COINS and x["role"] != "mentioned"]
                coin_c = (candles or {}).get(f"KRW-{tgt[0]}") if tgt else None
                react = observed_reaction(coin_c, now) if coin_c else "UNKNOWN"
                row, w, cl, facts = judge_article(a, rec, ca, clusters.get(ck), now, react)
                cl.setdefault("first_article", a["id"])
                clusters[ck] = cl
                facts_cache[a["id"]] = dict(cluster=ck, facts=facts)
                dec_rows.append(row)
                if w:
                    win_rows.append(_win_row(w, now, a, row["rows"]))
                if row["decision"] == "UNMAPPED":
                    st["unmapped_count"] = int(st.get("unmapped_count", 0)) + 1
            except Exception as e:
                dec_rows.append(fail_row(a, "JUDGE_ERROR", f"{type(e).__name__}: {str(e)[:150]}", now))
        hr, hw = recheck_holds(now, st, arts_by_id)
        dec_rows += hr
        win_rows += hw
        append_csv(p["decisions"], DEC_COLS, dec_rows)
        append_csv(p["windows"], WIN_COLS, win_rows)
        if dec_rows:
            last = dec_rows[-1]
            st["last_decision"] = {k: last.get(k) for k in ("decided_at", "title", "decision", "rows", "targets", "effective_at",
                                                             "window_end", "rules_version", "diff")}
        copy_research(p)
        if log and (dec_rows or win_rows):
            log(f"news_v1(기록 전용): 판정 {len(dec_rows)}건, 새 창 {len(win_rows)}건, 추출 {len(done)}건")
    except Exception as e:
        if log:
            log(f"news_v1 파이프라인 오류(무시, 봇 계속): {type(e).__name__}: {str(e)[:200]}", warn=True)
        st["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    st["elapsed_s"] = round(time.monotonic() - t0, 2)
    try:
        save_state(p, st)
    except Exception:
        pass
    return status_of(st, now, p)


def copy_research(p):
    os.makedirs(p["research"], exist_ok=True)
    for src, dst in ((p["decisions"], "decisions.csv"), (p["windows"], "news_events.csv")):
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(p["research"], dst))


def status_of(st, now, p, n_recent=10):
    col = dict(st.get("collector") or {})
    ls = _p(col.get("last_success"))
    cstate = "UNKNOWN" if not ls else ("OK" if now - ls <= dt.timedelta(minutes=COLLECT_STALE_MIN) else "STALE")
    if col.get("ok") is False:
        cstate = "FAIL" if cstate != "UNKNOWN" else "FAIL"
    ex = dict(st.get("extractor") or {})
    recent = []
    for r in read_csv(p["decisions"])[-n_recent:][::-1]:
        ev = r.get("evidence") or ""
        try:
            evd = json.loads(ev) if ev else {}
            q = evd.get("event_type") or evd.get("destination") or next((x[2] for x in evd.get("assets", [])), "")
        except ValueError:
            q = ev[:160]
        recent.append(dict(decided_at=r.get("decided_at"), title=r.get("title"), url=r.get("url"), legacy=r.get("legacy_decision"),
                           decision=r.get("decision"), rows=r.get("rows"), targets=r.get("targets"),
                           window=(f"{r.get('effective_at', '')[5:16]}~{r.get('window_end', '')[11:16]}" if r.get("effective_at") else ""),
                           evidence=(q or "")[:200], rules_version=r.get("rules_version"), diff=r.get("diff"),
                           event_type=r.get("event_type"), confirmation=r.get("confirmation")))
    return dict(rules_version=NR.RULES_VERSION, table_hash=NR.table_hash(), candidate_note=NR.CANDIDATE_NOTE,
                active_from=st.get("active_from"),
                collector=dict(state=cstate, mode=col.get("mode"), last_success=col.get("last_success"), error=col.get("error")),
                extractor=dict(state=ex.get("state") or "IDLE", last_success=ex.get("last_success"), last_error=ex.get("last_error"),
                               queue=ex.get("queue", 0), backoff_until=ex.get("backoff_until")),
                unmapped_count=int(st.get("unmapped_count", 0)), last_decision=st.get("last_decision"),
                holds_active=sum(1 for c in (st.get("clusters") or {}).values() if (c.get("hold") or {}).get("active")),
                recent=recent)


# ============================== 그림자 장부용 ==============================

def ledger_events(p, active_from=None):
    """news_v1_5m 이 읽는 차단 창 = news_v1 창(windows.csv) + 캘린더 high 일정의 후보 창(MACRO_IND ±30 / FOMC 60·90).
    data/events.csv 는 읽기만 한다. 캘린더 줄의 added_at 이 news_v1 활성 시각보다 이르면 활성 시각으로 올린다(소급 금지)."""
    af = _p(active_from) if isinstance(active_from, str) else active_from
    if af is None:
        af = _p(load_state(p).get("active_from"))
    out = list(load_events(p["windows"]))
    for e in load_events(p["events_csv"]):
        if not str(e[1]).startswith("[일정]") or str(e[2]).lower() != "high":
            continue
        key, b, a = NR.macro_window(e[1])
        added = e[6]
        if af is not None and (added is None or added < af):
            added = af
        if added is None:
            continue          # 활성 전이고 added_at 도 없으면 적용하지 않음
        out.append((e[0], f"[news_v1 {key}] {e[1]}", "high", e[3], float(b), float(a), added, e[7]))
    return sorted(out, key=lambda x: x[0])


FP_COLS = ["signal_id", "coin", "signal_time", "side", "blocked", "block_reason", "R_nofilter", "R_filter",
           "base_status", "filter_status", "base_entry_time", "filter_entry_time"]


def filter_pairs(base, news, blocked, bar_min=5):
    """base/news = (청산 거래, 열린 포지션·대기) — 같은 신호 ID 로 연결. blocked = news 장부 engine blocked 목록.
    필터 R: 막힌 신호는 0R(진입 안 함), 진행 중이면 빈칸. 무필터 R: baseline 의 R."""
    from tools import extras as X
    rows = {}

    def put(sid, **kw):
        rows.setdefault(sid, dict(signal_id=sid, blocked="0", block_reason="", R_nofilter="", R_filter="",
                                  base_status="none", filter_status="none", base_entry_time="", filter_entry_time=""))
        rows[sid].update(kw)
    for side_name, (trades, opened) in (("base", base), ("filter", news)):
        for t in trades:
            sid = X.signal_id(t, bar_min)
            put(sid, coin=t.get("coin"), signal_time=X.signal_time(t, bar_min).strftime("%Y-%m-%d %H:%M"),
                side="L" if t["side"] == 1 else "S", **{f"{side_name}_status": "closed",
                                                         ("R_nofilter" if side_name == "base" else "R_filter"): f"{t['R']:.4f}",
                                                         f"{side_name}_entry_time": t["entry_time"].strftime("%Y-%m-%d %H:%M")})
        for x in opened:
            st_ = X.signal_time(x, bar_min)
            if st_ is None:
                continue
            sid = X.signal_id(x, bar_min)
            put(sid, coin=x.get("coin"), signal_time=st_.strftime("%Y-%m-%d %H:%M"), side="L" if x["side"] == 1 else "S",
                **{f"{side_name}_status": "open"})
    for b in blocked:
        sid = X.signal_id(dict(coin=b["coin"], side=b["side"], signal_time=b["time"]), bar_min)
        put(sid, coin=b["coin"], signal_time=b["time"].strftime("%Y-%m-%d %H:%M"), side="L" if b["side"] == 1 else "S",
            blocked="1", block_reason=b.get("event") or b.get("reason") or "", filter_status="blocked", R_filter="0.0000")
    return sorted(rows.values(), key=lambda r: (r["signal_time"], r["coin"] or "", r["side"]))


def write_filter_pairs(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FP_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def filter_summary(rows):
    blk = [r for r in rows if r["blocked"] == "1"]
    blk_closed = [r for r in blk if r["base_status"] == "closed"]
    both = [r for r in rows if r["base_status"] == "closed" and r["filter_status"] in ("closed", "blocked")]
    d = [float(r["R_filter"]) - float(r["R_nofilter"]) for r in both if r["R_filter"] != "" and r["R_nofilter"] != ""]
    return dict(signals=len(rows), blocked=len(blk), blocked_base_closed=len(blk_closed),
                blocked_base_sumR=round(sum(float(r["R_nofilter"]) for r in blk_closed), 3),
                sum_d=round(sum(d), 3), n_d=len(d),
                definition="d = 필터 R − 무필터 R (같은 신호 ID, 막힌 신호 필터 R = 0). 계좌 경로 차이는 두 장부 status 로 비교")
