# -*- coding: utf-8 -*-
"""
검증 5 — 뉴스 판정 v1 (tools/news_rules.py · tools/news_v1.py · bot 연결). 오프라인(가짜 RSS·가짜 Gemini), 임시 폴더만 씀.
ledger/·state/·data/ 는 읽기만(data/ 5분봉). research/holdout 사용 안 함.

N1  스키마 검증: 허용값·형식·근거 문장 대조(원문에 없으면 null), 등급 제안 키 무시, 객체 아님 → ok=False
N2  결정 결정성: 같은 입력 → 같은 결정·창·묶음 상태(2회·순서 바꿔도), 표 지문 고정
N3  unmapped: 신규 여부 UNKNOWN 인 신규 침해 → UNMAPPED(임의 판정 없음), 파이프라인 unmapped_count 증가
N4  재배포 2건 = 독립 1건(같은 제목·원출처 표기·같은 온체인 분석사 인용), 진짜 독립 2건 = INDEPENDENT
N5  공식 공지만 1건 → OFFICIAL 유지(교차 보도 없음으로 낮추지 않음)
N6  소급 없음: 시작 = ceil5(decided_at) ≥ decided_at, engine 은 결정 전 체결 신호를 막지 않음
N7  재배포로 연장 없음 · 사전 정의된 새 사실로만 연장
N8  진행 중 장애: 고정 시간 끝나도 해제 안 함(재확인 창 추가), 복구 확인 → 해제, 12시간 상한 → 수동 확인
N9  실패 구분: 수집 실패(collector FAIL, extractor 그대로) / Gemini 503(extractor FAIL, collector OK) / 429(BACKOFF·
    reviews.json 백오프·다음 실행 호출 0) / 키 없음(NO_KEY) / 대기 만료 → NOT_EXTRACTED 기록(이벤트 없음으로 안 바꿈),
    실행당 Gemini 최대 3건
N10 ETH 사례 재판정 재현: 저장된 기사·추출로 두 번(다른 폴더) 돌려 같은 decisions.csv, 결과 RECORD(D20),
    목적지 unknown 이어도 RECORD, 기존 판정(high ETH 18:35~20:35)과 차이 '기존만 차단'
N11 bot 연결: news_v1_5m 을 켜도 baseline·v4 거래 동일, news 창이 baseline 거래의 체결 시각을 덮으면 그 신호만 막히고
    filter_pairs.csv(같은 신호 ID, 차단 1, 필터 R 0)·status news_v1·filter_summary 생성, 활성 전 캘린더 창 소급 없음
결과: research/checks/c_news_v1_result.json
"""
import copy
import csv
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import bot                                   # noqa: E402
import engine                                # noqa: E402
from tools import gemini_client as gc        # noqa: E402
from tools import news_rules as NR           # noqa: E402
from tools import news_v1 as NV              # noqa: E402
from tools import review as rvw              # noqa: E402

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
FIX = os.path.join(REPO, "research", "news", "cases", "2026-11-17_eth_binance_hacker.fixture.json")


def k(s):
    return engine.parse_kst(s)


def ex(**kw):
    """근거 문장까지 갖춘 추출 예시 생성기(텍스트에 들어 있는 인용만 씀)"""
    base = {f: None for f, *_ in NR.EXTRACT_FIELDS}
    base.update({q: None for *_x, q in NR.EXTRACT_FIELDS})
    base.update(assets=[], sources_cited=[])
    base.update(kw)
    return base


def art(i, title, summary="", outlet="coindesk", link=None, facts=None):
    return dict(id=i, title=title, summary=summary, outlet=outlet, link=link or f"https://{outlet}.example/{i}", facts=facts)


HACK_T = "Solana validator exploit: hackers drain $150 million from core program, network halted"
HACK_RAW = ex(event_type="new_breach", event_type_quote="hackers drain $150 million", is_new_information=True,
              new_information_quote="hackers drain $150 million", entity_name="Solana", entity_kind="chain",
              entity_quote="Solana validator exploit", damage_usd=150e6, damage_quote="$150 million",
              assets=[dict(symbol="SOL", role="native_subject", quote="Solana validator exploit")],
              status="ongoing", status_quote="network halted")

# ---------------- N1 ----------------

def n1():
    text = HACK_T
    f, rep = NR.validate_extraction(dict(HACK_RAW, suggested_tier="S", destination="exchange", destination_quote="sent to Binance"), text)
    out = dict(rep=rep, facts={kk: f[kk] for kk in ("event_type", "damage_usd", "destination", "status")})
    ok = f["event_type"] == "new_breach" and f["damage_usd"] == 150e6 and f["destination"] is None \
        and "destination" in rep["dropped"] and rep["ignored"] == ["suggested_tier"] and f["assets"][0]["symbol"] == "SOL"
    f2, rep2 = NR.validate_extraction(dict(HACK_RAW, event_type="catastrophe", damage_usd="lots", event_time_utc="yesterday"), text)
    ok = ok and f2["event_type"] is None and f2["damage_usd"] is None and f2["event_time_utc"] is None and len(rep2["errors"]) == 3
    f3, rep3 = NR.validate_extraction("not json", text)
    ok = ok and rep3["ok"] is False and f3["event_type"] is None
    f4, _ = NR.validate_extraction(dict(HACK_RAW, assets=[dict(symbol="ETH", role="native_subject", quote="Ethereum core")]), text)
    ok = ok and f4["assets"] == []        # 근거 없는 대상 코인 → 버림(제목 언급으로 판정하지 않음)
    out["assets_without_quote_dropped"] = f4["assets"] == []
    return ok, out

# ---------------- N2 ----------------

def n2():
    a = dict(id="h1", link="https://coindesk.example/h1", outlet="coindesk", title=HACK_T, summary="", published_at="2026-11-18 10:00:00",
             received_at="2026-11-18 10:01:00", legacy=dict(block=False, text="-"))
    rec = dict(raw_text=json.dumps(HACK_RAW), model="m", prompt_version=NV.PROMPT_VERSION)
    now = k("2026-11-18 10:01")
    r1 = NV.judge_article(a, rec, [], {}, now)
    r2 = NV.judge_article(copy.deepcopy(a), copy.deepcopy(rec), [], {}, now)
    same = json.dumps(r1[:3], default=str, sort_keys=True) == json.dumps(r2[:3], default=str, sort_keys=True)
    # 결정표 전 조합 결정성
    combos = []
    for et in NR.EVENT_TYPES:
        for nov in (True, False, None):
            for role in ("native_subject", "stolen_or_moved", "mentioned", None):
                f = {ff: None for ff, *_ in NR.EXTRACT_FIELDS}
                f.update(event_type=et, is_new_information=nov, assets=[dict(symbol="ETH", role=role, quote="x")] if role else [],
                         sources_cited=[], evidence={}, damage_usd=2e8, moved_usd=2e8)
                for conf in NR.CONFIRM:
                    d1, d2 = NR.decide(f, conf), NR.decide(copy.deepcopy(f), conf)
                    combos.append(d1["action"] == d2["action"] and d1["targets"] == d2["targets"] and d1["rows"] == d2["rows"])
    out = dict(row=r1[0]["decision"], rows=r1[0]["rows"], window=[str(r1[1]["start"]), str(r1[1]["end"])] if r1[1] else None,
               combos=len(combos), table_hash=NR.table_hash())
    # 공식 확인 없는 단일 기사(SINGLE_CLAIM) · 자체 체인 · 1억 달러 이상 → D12(B), 공식 공지 링크면 D10(S·보류)
    r3 = NV.judge_article(dict(a, link="https://status.solana.com/incidents/1"), rec, [], {}, now)
    out["official_link"] = (r3[0]["rows"], r3[0]["confirmation"], r3[0]["release"])
    ok = same and all(combos) and r1[0]["decision"] == "BLOCK" and r1[0]["rows"] == "D12" \
        and r3[0]["rows"] == "D10" and r3[0]["confirmation"] == "OFFICIAL" and NR.table_hash() == NR.table_hash()
    return ok, out

# ---------------- N3 ----------------

def n3(tmp):
    f, _ = NR.validate_extraction(dict(HACK_RAW, is_new_information=None, new_information_quote=None), HACK_T)
    d = NR.decide(f, "OFFICIAL")
    f2 = dict(f, event_type=None)
    d2 = NR.decide(f2, "OFFICIAL")
    # 파이프라인: unmapped_count
    p = NV.paths_for(tmp + "/n3")
    now = k("2026-11-18 10:02")
    shared = dict(at=now, sources_ok=1, articles=[dict(title=HACK_T, summary="", link="https://coindesk.example/n3", outlet="coindesk",
                                                       published=now - dt.timedelta(minutes=5))])
    st = NV.run(now, {}, p, shared=shared, key="FAKE", call=lambda *a_: json.dumps(dict(HACK_RAW, is_new_information=None)))
    out = dict(decision=d["action"], sol=d["per_coin"]["SOL"]["note"], no_type=d2["action"], unmapped_count=st["unmapped_count"])
    ok = d["action"] == "UNMAPPED" and d2["action"] == "UNMAPPED" and st["unmapped_count"] == 1 \
        and not NV.read_csv(p["windows"])
    return ok, out

# ---------------- N4 / N5 ----------------

def n4():
    t = "Binance hacker moves $63 million in stolen ether after 2 weeks of silence"
    an = dict(sources_cited=[dict(name="MistTrack", type="onchain_analyst", quote="q")])
    reposts = [art("a", t, outlet="coindesk", facts=an), art("b", t, outlet="cryptonews.net", facts={})]
    k1, _ = NR.independent_origins(reposts)
    credited = [art("a", "Exchange X halts withdrawals", outlet="theblock", facts={}),
                art("b", "Withdrawals frozen at exchange X amid probe", outlet="cointelegraph",
                    facts=dict(credited_outlet="The Block"))]
    k2, _ = NR.independent_origins(credited)
    same_src = [art("a", "Hacker shifts ether to new wallets", outlet="coindesk", facts=an),
                art("b", "North Korean group moves stolen ETH, data shows", outlet="theblock", facts=an)]
    k3, _ = NR.independent_origins(same_src)
    indep = [art("a", "Hacker shifts ether to new wallets", outlet="coindesk", facts=an),
             art("b", "Exchange confirms losses in reserve audit", outlet="theblock",
                 facts=dict(sources_cited=[dict(name="company filing", type="other", quote="q")]))]
    c1 = NR.confirmation(reposts)
    c4 = NR.confirmation(indep)
    out = dict(reposts=k1, credited=k2, same_onchain_source=k3, independent=c4, reposts_conf=c1)
    return k1 == 1 and k2 == 1 and k3 == 1 and c1 == ("SINGLE_CLAIM", 1) and c4 == ("INDEPENDENT", 2), out


def n5():
    only = [art("u", "업비트, XRP 입출금 일시 중단 공지", outlet="upbit", link="https://upbit.com/service_center/notice?id=1", facts={})]
    c = NR.confirmation(only)
    relayed = [art("r", "Coinbase confirms outage", facts=dict(sources_cited=[dict(name="Coinbase", type="official", quote="q")]))]
    c2 = NR.confirmation(relayed)
    fake = [art("f", "Someone says Coinbase down", facts=dict(sources_cited=[dict(name="random account", type="official", quote="q")]))]
    c3 = NR.confirmation(fake)
    out = dict(official_notice_only=c, relayed_official=c2, unknown_official_name=c3)
    return c == ("OFFICIAL", 1) and c2[0] == "OFFICIAL" and c3[0] == "SINGLE_CLAIM", out

# ---------------- N6 / N7 ----------------

def n6():
    d = dict(action="BLOCK", minutes=120, targets=["ETH"], hold=False, recheck=None, key="A")
    res = []
    for s in ("2026-11-17 18:30:20", "2026-11-17 18:30:00", "2026-11-17 18:34:59", "2026-11-17 18:35:00"):
        t = k(s[:16]) + dt.timedelta(seconds=int(s[17:]))
        w, _e = NR.plan_window(d, t, [], None, dict(type="x", conf_rank=1, scale_rank=1, amount=1, persist="UNCLEAR", coins=["ETH"]))
        res.append((s, str(w["start"]), w["start"] >= t))
    # engine: 18:30:20 결정 → 18:30 체결(그 전) 신호는 안 막고 18:35 체결은 막음
    ev = [(k("2026-11-17 18:35"), "n", "high", frozenset({"ETH"}), 0.0, 120.0, k("2026-11-17 18:30") + dt.timedelta(seconds=20), "u")]
    wins = engine.event_windows(dict(eventBlock=True, eventBeforeMin=30, eventAfterMin=30), ev, "ETH")
    hit_before = engine.event_hit(wins, k("2026-11-17 18:30"))
    hit_after = engine.event_hit(wins, k("2026-11-17 18:35"))
    out = dict(starts=res, engine_before=hit_before, engine_after=hit_after)
    return all(r[2] for r in res) and res[0][1].startswith("2026-11-17 18:35") and res[1][1].startswith("2026-11-17 18:30") \
        and hit_before is None and hit_after == "n", out


def n7():
    a0 = dict(id="x1", link="https://coindesk.example/x1", outlet="coindesk", title=HACK_T, summary="", legacy={})
    rec = dict(raw_text=json.dumps(dict(HACK_RAW, status=None, status_quote=None)))
    t0 = k("2026-11-18 10:00")
    row, w, cl, f = NV.judge_article(a0, rec, [], {}, t0)
    # 30분 뒤 재배포(다른 매체, 같은 제목, 같은 사실 — Gemini 가 '새 정보'라 해도) → 연장 없음
    a1 = dict(a0, id="x2", outlet="cointelegraph", link="https://ct.example/x2")
    ca = [dict(a0, facts=f)]
    row1, w1, cl1, _ = NV.judge_article(a1, rec, ca, cl, t0 + dt.timedelta(minutes=30))
    # REPEAT(새 정보 아님) → D03
    rec_rep = dict(raw_text=json.dumps(dict(HACK_RAW, is_new_information=False, status=None, status_quote=None)))
    row2, w2, _cl2, _ = NV.judge_article(dict(a1, id="x3"), rec_rep, ca, cl, t0 + dt.timedelta(minutes=40))
    # 새 사실: 피해액 1.5배 이상(근거 있음) → 연장
    t3 = "Solana validator exploit losses rise to $400 million, hackers drain more"
    raw3 = dict(HACK_RAW, damage_usd=400e6, damage_quote="$400 million", event_type_quote="hackers drain more",
                new_information_quote="losses rise to $400 million", status=None, status_quote=None)
    a3 = dict(a0, id="x4", title=t3, outlet="theblock", link="https://tb.example/x4")
    row3, w3, cl3, _ = NV.judge_article(a3, dict(raw_text=json.dumps(raw3)), ca, cl, t0 + dt.timedelta(minutes=50))
    out = dict(first=(row["decision"], row["extension"], row["window_end"]), repost=(row1["decision"], row1["extension"]),
               repeat=(row2["rows"], row2["extension"]), new_fact=(row3["extension"], row3["effective_at"], row3["window_end"]))
    ok = w is not None and w1 is None and row1["extension"].startswith("no_extend") and w2 is None and "D03" in row2["rows"] \
        and w3 is not None and row3["extension"].startswith("extend:") and w3["start"] >= t0 + dt.timedelta(minutes=50)
    return ok, out

# ---------------- N8 ----------------

def n8():
    t0 = k("2026-11-18 10:00")
    a0 = dict(id="o1", link="https://status.solana.com/incidents/o1", outlet="solana", title=HACK_T, summary="", legacy={})
    row, w, cl, f = NV.judge_article(a0, dict(raw_text=json.dumps(HACK_RAW)), [], {}, t0)
    st = dict(clusters={row["cluster"]: dict(cl, first_article="o1")})
    abi = {"o1": a0}
    steps = []
    t = t0
    for _ in range(12):                       # 3시간 창(D10=S) 끝까지 + 그 뒤
        t += dt.timedelta(minutes=30)
        rows, wins = NV.recheck_holds(t, st, abi)
        steps.append((t.strftime("%H:%M"), [r["decision"] for r in rows], [x["time_kst"][11:] + "+" + x["after_min"] for x in wins]))
    still = st["clusters"][row["cluster"]]["hold"]["active"]
    covered_until = st["clusters"][row["cluster"]]["hold"]["last_end"]
    # 복구 확인 기사 → 해제
    t_rec = "Solana network recovered after validators restart, exploit losses at $150 million"
    raw_r = dict(HACK_RAW, status="recovered", status_quote="network recovered", event_type_quote="exploit losses",
                 new_information_quote="network recovered", entity_quote="Solana network")
    a1 = dict(a0, id="o2", title=t_rec, link="https://y.example/o2")
    row2, _w2, cl2, _ = NV.judge_article(a1, dict(raw_text=json.dumps(raw_r)), [dict(a0, facts=f)], st["clusters"][row["cluster"]], t)
    st["clusters"][row["cluster"]] = cl2
    rows_rel, _ = NV.recheck_holds(t + dt.timedelta(minutes=5), st, abi)
    # 상한: 복구 소식 없이 12시간
    st2 = dict(clusters={row["cluster"]: dict(cl, first_article="o1")})
    tt = t0
    capped = None
    while tt < t0 + dt.timedelta(hours=13):
        tt += dt.timedelta(minutes=30)
        rr, _ = NV.recheck_holds(tt, st2, abi)
        if any(r["decision"] == "HOLD_CAP" for r in rr):
            capped = tt
            break
    out = dict(first_window=(row["effective_at"], row["window_end"], row["release"]), steps=steps, still_active=still,
               covered_until=covered_until, release_rows=[r["decision"] for r in rows_rel], cap_at=str(capped))
    ok = still and k(covered_until[:16]) > t0 + dt.timedelta(hours=5) and any("HOLD_CONTINUE" in s[1] for s in steps) \
        and rows_rel and rows_rel[0]["decision"] == "HOLD_RELEASE" and capped is not None \
        and capped - t0 >= dt.timedelta(hours=NR.MAX_HOLD_H)
    return ok, out

# ---------------- N9 ----------------

def n9(tmp):
    out = {}
    now = k("2026-11-18 11:00")
    arts = [dict(title=f"Exchange Q hacked, attacker drains {i} wallets", summary="", link=f"https://coindesk.example/q{i}",
                 outlet="coindesk", published=now - dt.timedelta(minutes=10 + i)) for i in range(5)]
    # (a) 수집 실패: 공유 결과 없음 + 직접 받기 모두 실패
    p = NV.paths_for(tmp + "/n9a")

    def get_fail(url, timeout):
        raise TimeoutError("timed out")
    calls = []
    st_a = NV.run(now, {}, p, shared={}, key="FAKE", get=get_fail, call=lambda *a_: calls.append(1))
    out["collector_fail"] = (st_a["collector"]["state"], st_a["extractor"]["state"], len(calls))
    ok = st_a["collector"]["state"] == "FAIL" and st_a["extractor"]["state"] == "IDLE" and not calls
    # (b) Gemini 503: 수집 OK, 추출 FAIL, 실행당 최대 3건
    p = NV.paths_for(tmp + "/n9b")
    calls = []

    def c503(prompt, key, model, timeout):
        calls.append(timeout)
        raise gc.GeminiError("HTTP 503 UNAVAILABLE")
    st_b = NV.run(now, {}, p, shared=dict(at=now, sources_ok=3, articles=arts), key="FAKE", call=c503)
    out["gemini_503"] = (st_b["collector"]["state"], st_b["extractor"]["state"], len(calls), max(calls))
    ok = ok and st_b["collector"]["state"] == "OK" and st_b["extractor"]["state"] == "FAIL" and len(calls) == 3 and max(calls) <= 15
    # (c) 429 → BACKOFF, reviews.json backoff_until, 다음 실행 호출 0
    p = NV.paths_for(tmp + "/n9c")
    calls = []

    def c429(prompt, key, model, timeout):
        calls.append(1)
        raise gc.GeminiError("gemini-flash-latest: HTTP 429 RESOURCE_EXHAUSTED quota")
    st_c = NV.run(now, {}, p, shared=dict(at=now, sources_ok=3, articles=arts), key="FAKE", call=c429)
    n1_ = len(calls)
    st_c2 = NV.run(now + dt.timedelta(minutes=5), {}, p, shared={}, key="FAKE", call=c429)
    bo = rvw.load_reviews(p["reviews"]).get("backoff_until")
    out["gemini_429"] = (st_c["extractor"]["state"], n1_, len(calls) - n1_, bo, st_c2["extractor"]["state"])
    ok = ok and st_c["extractor"]["state"] == "BACKOFF" and n1_ == 1 and len(calls) == 1 and bo == "2026-11-19 11:00:00" \
        and st_c2["extractor"]["state"] == "BACKOFF"
    # (d) 키 없음 → NO_KEY, 6시간 뒤 대기 만료 → NOT_EXTRACTED 기록(이벤트 없음으로 안 바꿈), 창 없음
    p = NV.paths_for(tmp + "/n9d")
    st_d = NV.run(now, {}, p, shared=dict(at=now, sources_ok=3, articles=arts), key="", call=c429)
    st_d2 = NV.run(now + dt.timedelta(hours=7), {}, p, shared={}, key="", get=get_fail, call=c429)
    dec = NV.read_csv(p["decisions"])
    out["no_key"] = (st_d["extractor"]["state"], st_d["extractor"]["queue"], [r["decision"] for r in dec],
                     st_d2["collector"]["state"])
    ok = ok and st_d["extractor"]["state"] == "NO_KEY" and len(dec) == 5 and all(r["decision"] == "NOT_EXTRACTED" for r in dec) \
        and not NV.read_csv(p["windows"]) and st_d2["collector"]["state"] == "FAIL"
    # (e) 시간 예산: 남은 시간 < 5초면 호출 안 함
    p = NV.paths_for(tmp + "/n9e")
    calls = []
    NV.run(now, {}, p, shared=dict(at=now, sources_ok=3, articles=arts), key="FAKE", call=c503, budget_s=3)
    out["budget_3s_calls"] = len(calls)
    ok = ok and not calls
    return ok, out

# ---------------- N10 ----------------

def eth_run(root, destination="own_wallets"):
    fx = json.load(open(FIX, encoding="utf-8"))
    p = NV.paths_for(root)
    os.makedirs(os.path.dirname(p["events_csv"]), exist_ok=True)
    with open(p["events_csv"], "w", encoding="utf-8") as f:       # 판정 당시 data/events.csv 의 그 줄(복사본)
        f.write(",".join(engine.EVENT_COLS) + "\n" + fx["legacy_row"] + "\n")
    now = k(fx["decided_at"][:16]) + dt.timedelta(seconds=int(fx["decided_at"][17:]))
    a = fx["article"]
    shared = dict(at=now, sources_ok=3, articles=[dict(title=a["title"], summary=a["summary"], link=a["link"], outlet=a["outlet"],
                                                       published=k(a["published"][:16]) + dt.timedelta(seconds=19))])
    raw = dict(fx["extraction_example"], destination=destination)
    cands = {m: bot.load_candles(bot.candle_path(m)) for m in ("KRW-ETH",)}
    st = NV.run(now, {}, p, candles=cands, shared=shared, key="FAKE", call=lambda *x: json.dumps(raw))
    return st, NV.read_csv(p["decisions"]), p


def n10(tmp):
    st1, d1, p1 = eth_run(tmp + "/eth1")
    st2, d2, _ = eth_run(tmp + "/eth2")
    st3, d3, _ = eth_run(tmp + "/eth3", "unknown")
    r = d1[0]
    # 저장본으로 재판정(Gemini 다시 안 부름): extractions.jsonl + articles.jsonl → judge_article
    recs = NV.read_jsonl(p1["extractions"])
    arts = NV.read_jsonl(p1["articles"])
    rj, w, _cl, _f = NV.judge_article(arts[0], recs[0], [], {}, k("2026-11-17 18:30") + dt.timedelta(seconds=20), r["observed_reaction"])
    keys = [c for c in NV.DEC_COLS]
    out = dict(decision=r["decision"], rows=r["rows"], relations=r["relations"], scale=r["scale"], confirmation=r["confirmation"],
               destination=r["destination"], legacy=r["legacy_decision"], diff=r["diff"], ignored=r["ignored_keys"],
               reaction=r["observed_reaction"], times=(r["published_at"], r["received_at"], r["decided_at"], r["effective_at"]),
               unknown_dest=(d3[0]["decision"], d3[0]["rows"]), status=st1["extractor"]["state"])
    ok = d1 == d2 and r["decision"] == "RECORD" and "D20" in r["rows"] and "ETH:MOVED_ASSET" in r["relations"] \
        and r["confirmation"] == "SINGLE_CLAIM" and r["diff"] == "기존만 차단" and "high ETH 11-17 18:35~20:35" in r["legacy_decision"] \
        and r["ignored_keys"] == "suggested_tier" and d3[0]["decision"] == "RECORD" and w is None \
        and all(str(rj.get(c, "")) == str(r.get(c, "")) for c in keys if c not in ("legacy_decision", "diff"))
    return ok, out

# ---------------- N11 ----------------

def n11(tmp):
    out = {}
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    cands = {m: bot.load_candles(bot.candle_path(m)) for m in MARKETS}
    end = max(cands["KRW-BTC"]) + dt.timedelta(minutes=5)
    for s in cfg["shadow_ledgers"]:
        if s.get("pair_group"):
            s["start_time"] = "2026-11-15 09:00"
    saved = (bot.SHADOW_DIR, bot.CONFIG, bot.PAIRS_CSV, bot.DAILY_CSV, bot.NEWS_FILTER_PAIRS, bot.news_paths)
    np_ = NV.paths_for(tmp + "/n11")
    try:
        bot.CONFIG = os.path.join(tmp, "config.json"); bot.PAIRS_CSV = os.path.join(tmp, "v4", "pairs.csv")
        bot.DAILY_CSV = os.path.join(tmp, "btc_daily.csv"); bot.NEWS_FILTER_PAIRS = os.path.join(tmp, "news", "filter_pairs.csv")
        bot.news_paths = lambda: np_
        os.makedirs(os.path.dirname(np_["events_csv"]), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, "data", "events.csv"), np_["events_csv"])     # 읽기 전용 복사본
        with open(bot.CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        res = {}
        for lab, on in (("off", False), ("on", True)):
            c2 = copy.deepcopy(cfg)
            for s in c2["shadow_ledgers"]:
                if s["name"] == "news_v1_5m":
                    s["enabled"] = on
            bot.SHADOW_DIR = os.path.join(tmp, "shadow_" + lab)
            if on:
                # baseline 의 첫 거래 체결 시각을 덮는 news_v1 창(결정은 그 신호 전) — 그 신호만 막혀야 함
                bt = list(csv.DictReader(open(os.path.join(tmp, "shadow_off", "baseline_v141_5m", "trades.csv"), encoding="utf-8-sig")))
                t0 = bt[0]
                fill = k(t0["진입시각"])
                os.makedirs(np_["store"], exist_ok=True)
                with open(np_["windows"], "w", encoding="utf-8") as f:
                    f.write(",".join(NV.WIN_COLS) + "\n")
                    f.write(f"{(fill - dt.timedelta(minutes=10)):%Y-%m-%d %H:%M},[news_v1 D10] {t0['코인']}: test,high,{t0['코인']},0,60,"
                            f"{(fill - dt.timedelta(minutes=15)):%Y-%m-%d %H:%M:%S},https://x.example/t\n")
                NV.save_state(np_, dict(active_from=(fill - dt.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")))
                out["window_over"] = (t0["코인"], t0["진입시각"], t0["R"])
            bot.run_shadows(c2, end, cands["KRW-BTC"], engine.load_events(os.path.join(REPO, "data", "events.csv")),
                            offline=True, preloaded=cands, review=False)
            res[lab] = {n: open(os.path.join(bot.SHADOW_DIR, n, "trades.csv"), encoding="utf-8-sig").read()
                        for n in ("baseline_v141_5m", "v4_30m_fixed", "v4_30m_cost")}
        same = all(res["off"][n] == res["on"][n] for n in res["off"])
        rows = list(csv.DictReader(open(bot.NEWS_FILTER_PAIRS, encoding="utf-8-sig")))
        blk = [r for r in rows if r["blocked"] == "1"]
        sj = json.load(open(os.path.join(tmp, "shadow_on", "news_v1_5m", "status.json"), encoding="utf-8"))
        nt = list(csv.DictReader(open(os.path.join(tmp, "shadow_on", "news_v1_5m", "trades.csv"), encoding="utf-8-sig")))
        out.update(baseline_v4_identical=same, filter_rows=len(rows), blocked=blk, news_trades=len(nt),
                   filter_summary=sj.get("filter_summary"), news_v1_keys=sorted((sj.get("news_v1") or {}).keys()),
                   cols_ok=list(rows[0].keys()) == NV.FP_COLS if rows else False)
        tid = out["window_over"]
        ok = same and len(blk) == 1 and blk[0]["coin"] == tid[0] and blk[0]["R_filter"] == "0.0000" \
            and abs(float(blk[0]["R_nofilter"]) - float(tid[2])) < 1e-3 and blk[0]["base_status"] == "closed" \
            and "unmapped_count" in out["news_v1_keys"] and sj.get("blocked_signals") == 1 and out["cols_ok"]
        # 활성 전 캘린더 창 소급 없음: ledger_events 의 캘린더 줄 added_at ≥ active_from
        le = NV.ledger_events(np_)
        af = k(NV.load_state(np_)["active_from"][:16])
        cal = [e for e in le if "[일정]" in e[1]]
        out["calendar_rows"] = [(str(e[0]), e[1][:40], e[4], e[5], str(e[6])) for e in cal]
        ok = ok and cal and all(e[6] >= af for e in cal) and any(e[4] == 30.0 for e in cal)
    finally:
        (bot.SHADOW_DIR, bot.CONFIG, bot.PAIRS_CSV, bot.DAILY_CSV, bot.NEWS_FILTER_PAIRS, bot.news_paths) = saved
    return bool(ok), out


def main():
    res = dict(method=__doc__.strip(), rules_version=NR.RULES_VERSION, table_hash=NR.table_hash(), tests={})
    tmp = tempfile.mkdtemp(prefix="newsv1_")
    t_all = time.perf_counter()
    try:
        for name, fn in (("N1_schema", n1), ("N2_determinism", n2), ("N3_unmapped", lambda: n3(tmp)),
                         ("N4_repost_independence", n4), ("N5_official_only", n5), ("N6_no_retro", n6),
                         ("N7_extension", n7), ("N8_ongoing_hold", n8), ("N9_failures", lambda: n9(tmp)),
                         ("N10_eth_rejudge", lambda: n10(tmp)), ("N11_bot_wiring", lambda: n11(tmp))):
            try:
                ok, det = fn()
            except Exception as e:
                import traceback
                ok, det = False, dict(error=traceback.format_exc())
            res["tests"][name] = dict(pass_=ok, **det)
            print(name, "PASS" if ok else "FAIL", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["total_s"] = round(time.perf_counter() - t_all, 1)
    res["pass"] = all(t["pass_"] for t in res["tests"].values())
    with open(os.path.join(HERE, "c_news_v1_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("검증 5:", "PASS" if res["pass"] else "FAIL", res["total_s"], "s")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
