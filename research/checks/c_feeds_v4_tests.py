# -*- coding: utf-8 -*-
"""
검증 4 — 이벤트 감지 A안 · 상관 신호 묶음 · v4 30m 짝 그림자 · 손절 분해 (2026-11-17)

F1 캘린더 JSON: USD 만, High→high·Medium→medium, Low·다른 나라 제외, 미국 동부 → KST, 00:00(하루 종일) → medium,
   같은 시각 합치기, validate_schedule 로 과거 제외·URL·added_at 기록
F2 RSS: 예시 응답(RSS 2.0 4곳 + Atom 합성) 파싱, 키워드 분류 양성 7·음성 6, 최근 2시간·다음 5분봉 시작·60~180분·하루 한도
F3 상태: UNKNOWN(한 번도 없음)·OK·STALE 경계, 캘린더 0건이어도 OK(이벤트 없음 ≠ 데이터 없음), 실패 시 last_success 유지,
   수집 시간 예산 초과 시 남은 소스 건너뜀, gemini 상태 파일 형식도 판정
F4 묶음 ID: 같은 봉·±1봉 사슬은 한 묶음, 2봉 떨어지거나 반대 방향이면 별개, 독립 묶음 수
F5 30m 재현: bot.tf_data + v4 그림자 설정(engine.run)이 E2E8 의 30m 기준(gate=0)·E3(gate=1) 거래를 신호 ID 까지 재현
   (18개월 개발·선택 구간 데이터 — 최종 시험 데이터 아님). R 차이는 자본·수량 내림 차이만큼 작아야 함
F6 같은 신호 같은 signal_id: 두 장부 모두 진입한 신호는 진입·청산·R 이 같음, pairs.csv 생성(한쪽만 진입한 신호 포함, 국면 라벨)
F7 손절 분해 합 = 실제 R (±0.001R): 본 장부 + baseline + F5 의 30m 거래 전부. 손절은 계획R = −1
F8 검정력 계산 예시: σ_d=1 → 785쌍, power_stats 합성 예시, power_prior.json 형식
F9 Gemini 해설 429 → 24시간 백오프(그동안 호출 0), 503 은 기존처럼 거래 실패로 셈
F10 bot 연결: 임시 폴더에서 run_shadows(오프라인) → v4 두 장부 같은 시작 시각, pairs.csv·pair_summary·일지 새 열,
    tf_data 완성 봉만, event_data_fields 상태
결과: research/checks/c_feeds_v4_result.json
"""
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
sys.path.insert(0, os.path.join(REPO, "research"))
import bot                                   # noqa: E402
import engine                                # noqa: E402
from engine import KST                       # noqa: E402
from tools import event_feeds as ef          # noqa: E402
from tools import extras as X                # noqa: E402
from tools import gemini_client as gc        # noqa: E402
from tools import regime as RG               # noqa: E402
from tools import review as rvw              # noqa: E402
from tools import v4pairs as VP              # noqa: E402

SAMPLES = os.path.normpath(os.path.join(REPO, "..", "tasks", "btc-eventsA-v4", "samples"))
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]


def k(s):
    return engine.parse_kst(s)


def rb(n):
    with open(os.path.join(SAMPLES, n), "rb") as f:
        return f.read()

# ---------------- F1 ----------------

def f1(tmp):
    syn = json.dumps([
        {"title": "CPI m/m", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "High"},
        {"title": "Core CPI m/m", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "High"},
        {"title": "Bank Holiday", "country": "USD", "date": "2026-12-11T00:00:00-05:00", "impact": "High"},
        {"title": "ECB Rate", "country": "EUR", "date": "2026-12-10T08:15:00-05:00", "impact": "High"},
        {"title": "Crude Oil", "country": "USD", "date": "2026-12-10T10:30:00-05:00", "impact": "Low"},
        {"title": "Claims", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "Medium"},
        {"title": "FOMC Statement", "country": "USD", "date": "2026-12-16T14:00:00-05:00", "impact": "High"},
        {"bad": 1}, "x"])
    e = ef.parse_calendar(syn)
    out = dict(parsed=[(x["time"].strftime("%Y-%m-%d %H:%M"), x["title"], x["importance"]) for x in e])
    chk = {}
    chk["kst_high"] = ("2026-12-10 22:30", "CPI m/m", "high") in out["parsed"]
    chk["all_day_medium"] = ("2026-12-11 14:00", "Bank Holiday", "medium") in out["parsed"]
    chk["fomc_kst"] = ("2026-12-17 04:00", "FOMC Statement", "high") in out["parsed"]
    chk["no_eur_low"] = not any("ECB" in p[1] or "Crude" in p[1] for p in out["parsed"])
    items = ef.calendar_items(e, "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    out["items"] = [(i["time_kst"], i["name"], i["importance"]) for i in items]
    # 같은 시각 high 2개 → 한 줄, 같은 시각 medium(Claims)은 버림
    at = [i for i in items if i["time_kst"] == "2026-12-10 22:30"]
    chk["merged_same_time"] = len(at) == 1 and at[0]["importance"] == "high" and "Core CPI m/m" in at[0]["name"]
    # 실제 예시 응답
    real = ef.parse_calendar(rb("ff_thisweek.json"))
    out["sample_usd_high_medium"] = len(real)
    ri = ef.calendar_items(real, "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    now = k("2026-11-17 16:00")
    from tools.gemini_events import validate_schedule, append_events
    good, bad = validate_schedule(ri, now, [])
    p = os.path.join(tmp, "events_f1.csv")
    append_events(p, good)
    ev = engine.load_events(p)
    out["sample_added"] = [(r["time_kst"], r["name"], r["importance"]) for r in good]
    out["sample_rejected"] = [(b[0]["name"], b[1]) for b in bad]
    chk["sample_retail"] = ("2026-11-17 22:30", "[일정] Core Retail Sales m/m · Retail Sales m/m (USD)", "high") in out["sample_added"]
    chk["sample_fomc"] = ("2026-11-19 04:00", "[일정] FOMC Meeting Minutes (USD)", "high") in out["sample_added"]
    chk["past_rejected"] = any(b[1] == "과거 시각" and "Warsh" in b[0]["name"] for b in bad)
    chk["csv_rows"] = len(ev) == len(good) and all(e_[6] == now and str(e_[7]).startswith("https://") and e_[3] is None
                                                   for e_ in ev)
    # 다시 돌리면 중복으로 0건
    g2, _b2 = validate_schedule(ri, now, ev)
    out["second_run_added"] = len(g2)
    chk["no_dup_second_run"] = not g2
    out["checks"] = chk
    return all(chk.values()), out

# ---------------- F2 ----------------

POS = [("Hackers drain $120 million from Solana DeFi protocol", "high", "SOL"),
       ("Binance suspends withdrawals amid network congestion", "high", "ALL"),
       ("업비트, 입출금 중단 공지", "high", "ALL"),
       ("XRP Ledger halts block production for 40 minutes", "high", "XRP"),
       ("Upbit designates XRP as investment warning", "high", "XRP"),
       ("SEC sues Coinbase over unregistered securities", "medium", "ALL"),
       ("$1.2 billion in longs liquidated as bitcoin plunges", "medium", "BTC")]
NEG = ["Bitcoin price prediction: could BTC hit $100K?",
       "Cardano bridge hack drains $5 million",
       "Ethereum developers discuss Fusaka upgrade timeline",
       "What the Solana outage of 2022 taught us",
       "Ex-Robinhood engineer pleads guilty to $125K Hyperliquid trading scheme",
       "Ripple CEO says XRP adoption is growing"]
ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title>Ethereum mainnet stalled: network halts after bug</title><link href="https://example.org/a"/>
<published>2026-11-17T06:30:00Z</published><summary>&lt;p&gt;Blocks stopped.&lt;/p&gt;</summary></entry></feed>"""


def f2(tmp):
    out = dict(pos=[], neg=[])
    ok = True
    for t, imp, cs in POS:
        c = ef.classify(t)
        out["pos"].append((t, c and c["importance"], c and c["coins"], c and c["cat"]))
        ok = ok and c is not None and c["importance"] == imp and c["coins"] == cs
    for t in NEG:
        c = ef.classify(t)
        out["neg"].append((t, c and c["importance"]))
        ok = ok and (c is None or c["importance"] != "high")
    ok = ok and all(ef.classify(t) is None for t in NEG[:4])
    # 예시 응답 파싱
    arts = []
    for n in ("coindesk.xml", "cointelegraph.xml", "theblock.xml", "decrypt.xml"):
        a = ef.parse_rss(rb(n))
        out[f"rss_{n}"] = len(a)
        ok = ok and len(a) >= 10 and all(x["title"] and x["published"] is not None for x in a)
        arts += a
    at = ef.parse_rss(ATOM)
    out["atom"] = [(x["title"], x["link"], str(x["published"]), x["summary"]) for x in at]
    ok = ok and at[0]["link"] == "https://example.org/a" and at[0]["published"] == k("2026-11-17 15:30") and at[0]["summary"] == "Blocks stopped."
    # 돌발 → 검증(시작 = 다음 5분봉, 60~180분, 하루 한도, 2시간 밖 제외)
    now = k("2026-11-17 16:02")
    syn = [dict(title=t, summary="", link=f"https://example.org/{i}", published=now - dt.timedelta(minutes=20 + i))
           for i, (t, _imp, _c) in enumerate(POS)]
    syn.append(dict(title="Kraken outage hits BTC traders", summary="", link="https://example.org/old",
                    published=now - dt.timedelta(hours=3)))
    syn += at
    items = ef.breaking_items(syn + arts, now, [])
    from tools.gemini_events import validate_breaking
    good, bad = validate_breaking(items, now, [], per_day=3)
    out["breaking_added"] = [(r["time_kst"], r["name"][:40], r["importance"], r["coins"], r["after_min"]) for r in good]
    out["breaking_rejected"] = [(b[0]["name"][:40], b[1]) for b in bad]
    hi = [r for r in good if r["importance"] == "high"]
    ok = ok and len(hi) == 3 and all(r["time_kst"] == "2026-11-17 16:05" and r["before_min"] == "0"
                                     and 60 <= int(r["after_min"]) <= 180 for r in good) \
        and any(b[1] == "하루 돌발 한도" for b in bad) and not any("Kraken" in r["name"] for r in good) \
        and all(r["source"].startswith("https://") for r in good)
    # 같은 분류·코인은 한 번만
    out["dedup_same_cat"] = len(ef.breaking_items(syn[:1] * 3, now, []))
    ok = ok and out["dedup_same_cat"] == 1
    return ok, out

# ---------------- F3 ----------------

def f3(tmp):
    now = k("2026-11-18 12:00")
    out = {}
    j = ef.judge
    out["judge"] = [j(None, now, dt.timedelta(hours=24)), j("2026-11-17 13:00:00", now, dt.timedelta(hours=24)),
                    j("2026-11-17 11:59:00", now, dt.timedelta(hours=24)), j("bad", now, dt.timedelta(hours=1))]
    ok = out["judge"] == ["UNKNOWN", "OK", "STALE", "UNKNOWN"]
    # 오케스트레이션: 가짜 get
    cal_empty = json.dumps([{"title": "x", "country": "EUR", "date": "2026-11-18T08:00:00-05:00", "impact": "High"}]).encode()
    st_p = os.path.join(tmp, "event_feed.json"); evp = os.path.join(tmp, "events_f3.csv")
    calls = []

    def get_ok(url, timeout):
        calls.append(url)
        if "nextweek" in url:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", None, None)
        if "faireconomy" in url:
            return cal_empty
        if "coindesk" in url:
            return rb("coindesk.xml")
        raise TimeoutError("timed out")
    cfg = {"eventFetchBudgetSec": 30}
    ef.maybe_update(now, cfg, evp, st_p, get=get_ok)
    st = ef.load_state(st_p)
    ds = ef.data_status(st, now, cfg)
    out["after_first"] = dict(schedule=ds["schedule"], breaking=ds["breaking"], sources=ds["sources"],
                              schedule_items=ds["schedule_items_last"])
    ok = ok and ds["schedule"] == "OK" and ds["schedule_items_last"] == 0 and ds["breaking"] == "OK" \
        and ds["sources"]["ff_nextweek"]["ok"] is False and ds["sources"]["cointelegraph"]["ok"] is False
    # 5분 뒤: 둘 다 아직 주기 전 → 호출 0
    n = len(calls)
    ef.maybe_update(now + dt.timedelta(minutes=5), cfg, evp, st_p, get=get_ok)
    out["calls_after_5min"] = len(calls) - n
    ok = ok and len(calls) == n
    # 31분 뒤 돌발만, 모두 실패 → last_success 유지, 2시간 넘으면 STALE
    def get_fail(url, timeout):
        calls.append(url); raise TimeoutError("x")
    n = len(calls)
    ef.maybe_update(now + dt.timedelta(minutes=31), cfg, evp, st_p, get=get_fail)
    st = ef.load_state(st_p)
    out["calls_after_31min"] = len(calls) - n
    ds2 = ef.data_status(st, now + dt.timedelta(minutes=31), cfg)
    ds3 = ef.data_status(st, now + dt.timedelta(minutes=125), cfg)
    ds4 = ef.data_status(st, now + dt.timedelta(hours=25), cfg)
    out["fail_states"] = [ds2["breaking"], ds3["breaking"], ds4["schedule"], st["breaking"]["ok"]]
    ok = ok and out["calls_after_31min"] == len(ef.RSS_SOURCES) and out["fail_states"] == ["OK", "STALE", "STALE", False]
    # 예산: 소스마다 0.4초, 예산 1초 → 일부 건너뜀
    st_b = os.path.join(tmp, "event_feed_b.json"); calls.clear()

    def slow(url, timeout):
        calls.append(url); time.sleep(0.4); raise TimeoutError("slow")
    t0 = time.monotonic()
    ef.maybe_update(now, {"eventFetchBudgetSec": 1}, evp, st_b, get=slow)
    out["budget"] = dict(elapsed=round(time.monotonic() - t0, 2), calls=len(calls),
                         skipped=[k_ for k_, v in ef.load_state(st_b)["sources"].items() if "예산" in (v.get("error") or "")])
    ok = ok and out["budget"]["elapsed"] < 2.0 and out["budget"]["skipped"]
    # gemini 상태 파일 형식
    gs = {"schedule": {"last_attempt": "2026-11-18 11:00:00", "last_success": "2026-11-18 11:00:00", "last_ok": True},
          "breaking": {"last_attempt": "2026-11-18 11:50:00", "last_ok": False}}
    dg = ef.data_status(gs, now, {"eventSource": "gemini"})
    out["gemini_state"] = [dg["source"], dg["schedule"], dg["breaking"]]
    ok = ok and out["gemini_state"] == ["gemini", "OK", "UNKNOWN"]
    return bool(ok), out

# ---------------- F4 ----------------

def f4():
    b = k("2026-11-17 10:00")
    m = dt.timedelta(minutes=5)
    tr = [dict(coin="BTC", side=1, signal_time=b), dict(coin="ETH", side=1, signal_time=b),
          dict(coin="SOL", side=1, signal_time=b + m), dict(coin="XRP", side=1, signal_time=b + 2 * m),   # 사슬(±1봉)
          dict(coin="BTC", side=1, signal_time=b + 5 * m),                                              # 2봉+ 떨어짐
          dict(coin="XRP", side=-1, signal_time=b)]                                                     # 반대 방향
    X.assign_clusters(tr, 5)
    out = dict(ids=[(t["coin"], t["side"], t["cluster"]["id"], t["cluster"]["size"]) for t in tr],
               independent=X.independent_count(tr))
    ok = (len({t["cluster"]["id"] for t in tr[:4]}) == 1 and tr[0]["cluster"]["size"] == 4
          and tr[0]["cluster"]["id"] == "M202611171000L" and tr[4]["cluster"]["id"] != tr[0]["cluster"]["id"]
          and tr[5]["cluster"]["id"] == "M202611171000S" and out["independent"] == 3)
    # 30m 봉 기준(±30분)
    t2 = [dict(coin="BTC", side=1, signal_time=b), dict(coin="ETH", side=1, signal_time=b + dt.timedelta(minutes=30)),
          dict(coin="SOL", side=1, signal_time=b + dt.timedelta(minutes=90))]
    X.assign_clusters(t2, 30)
    out["tf30"] = [t["cluster"]["id"] for t in t2]
    ok = ok and t2[0]["cluster"]["id"] == t2[1]["cluster"]["id"] != t2[2]["cluster"]["id"]
    # 신호 ID
    out["signal_id"] = X.signal_id(dict(coin="SOL", side=-1, signal_time=b)), X.signal_id(dict(side=1, entry_time=b), 30)
    ok = ok and out["signal_id"] == ("SOL-202611171000S", "BTC-202611170930L")
    return ok, out

# ---------------- F5/F6/F7 (연구 데이터 30m 재현) ----------------

def v4_runs():
    from common import load_5m, load_split   # 18개월 개발·선택 구간 데이터(최종 시험 폴더 아님)
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    shs = {s["name"]: s for s in cfg["shadow_ledgers"]}
    out = {}
    datas = {}
    for c in ("BTC", "ETH", "XRP", "SOL"):
        T, O, H, L, C, V = load_5m(c)
        cand = {t: [O[i], H[i], L[i], C[i], V[i], ""] for i, t in enumerate(T)}
        D = bot.tf_data(cand, 30)
        datas[c] = D
        st = T[0] + dt.timedelta(days=14)          # E1 과 같은 시작(첫 봉 + 14일)
        for nm in ("v4_30m_fixed", "v4_30m_cost"):
            P = bot.shadow_params(cfg, shs[nm])
            R = engine.run(D, P, start_time=st, close_at_end=False, events=None, coin=c)
            for t in R["trades"]:
                t["coin"] = c
            op = [x for x in (R["position"], R["pending"]) if x is not None]
            for x in op:
                x["coin"] = c
            a = out.setdefault(nm, ([], []))
            a[0].extend(R["trades"]); a[1].extend(op)
    return out, datas, load_split()


def f5(runs):
    ref = {}
    with open(os.path.join(REPO, "research", "E2E8", "trades_E2E8.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["cost_mult"] == "1.0" and r["config"] in ("30m|gate=0|core=0|filt=-", "30m|gate=1|core=0|filt=-"):
                nm = "v4_30m_fixed" if "gate=0" in r["config"] else "v4_30m_cost"
                ref.setdefault(nm, {})[(r["coin"], r["id"])] = float(r["R"])
    out = {}
    ok = True
    for nm in ("v4_30m_fixed", "v4_30m_cost"):
        mine = {(t["coin"], t["id"]): t["R"] for t in runs[nm][0]}
        common = set(mine) & set(ref[nm])
        d = [abs(mine[x] - ref[nm][x]) for x in common]
        big = sorted(((abs(mine[x] - ref[nm][x]), x) for x in common), reverse=True)[:3]
        out[nm] = dict(research=len(ref[nm]), shadow_engine=len(mine), same_ids=len(common),
                       only_research=len(set(ref[nm]) - set(mine)), only_shadow=len(set(mine) - set(ref[nm])),
                       mean_abs_dR=sum(d) / len(d), max_abs_dR=big[0][0] if big else None,
                       share_dR_over_0_01=sum(1 for x in d if x > 0.01) / len(d),
                       mean_R_shadow=sum(mine[x] for x in common) / len(common),
                       mean_R_research=sum(ref[nm][x] for x in common) / len(common),
                       largest=[(round(a, 4), f"{b[0]} {b[1]}") for a, b in big])
        ok = ok and out[nm]["only_research"] == 0 and out[nm]["only_shadow"] == 0 and out[nm]["mean_abs_dR"] < 0.01 \
            and abs(out[nm]["mean_R_shadow"] - out[nm]["mean_R_research"]) < 0.005
    return ok, out


def f6(tmp, runs, datas, split):
    rows = VP.build_rows({"A": runs["v4_30m_fixed"], "B": runs["v4_30m_cost"]}, datas, 30, None)
    sid_ok = all(r["signal_id"] == f"{r['coin']}-{dt.datetime.strptime(r['signal_time'], '%Y-%m-%d %H:%M'):%Y%m%d%H%M}{r['side']}"
                 for r in rows)
    both = [r for r in rows if r["A_status"] == "closed" and r["B_status"] == "closed"]
    # 둘 다 진입한 신호: 진입·청산 시각·가격·사유가 같고, R 차이는 계좌 잔고가 달라 생기는 수량 내림(T1 절반 0.0001 단위)뿐
    same = all(r["A_entry_time"] == r["B_entry_time"] and r["A_exit_time"] == r["B_exit_time"] and r["A_entry"] == r["B_entry"]
               and r["A_exit_reason"] == r["B_exit_reason"] and abs(float(r["d_both"])) < 0.05 for r in both)
    max_d_both = max((abs(float(r["d_both"])) for r in both), default=0.0)
    onlyA = sum(1 for r in rows if r["A_status"] == "closed" and r["B_status"] == "none")
    onlyB = sum(1 for r in rows if r["B_status"] == "closed" and r["A_status"] == "none")
    # 국면 라벨(18개월 BTC 일봉) + 파일 쓰기/읽기
    from common import load_5m
    T, O, H, L, C, V = load_5m("BTC")
    reg = RG.Regime(RG.daily_from_5m(T, C))
    rows = VP.build_rows({"A": runs["v4_30m_fixed"], "B": runs["v4_30m_cost"]}, datas, 30, reg)
    p = os.path.join(tmp, "v4", "pairs.csv")
    VP.write_pairs(p, rows)
    back = VP.read_pairs(p)
    lab = [r for r in back if r["regime"]]
    out = dict(rows=len(rows), both_closed=len(both), only_A=onlyA, only_B=onlyB,
               open_rows=sum(1 for r in back if "open" in (r["A_status"], r["B_status"])),
               header_ok=list(back[0].keys()) == VP.COLS, labeled=len(lab),
               regimes={g: sum(1 for r in lab if r["regime"] == g) for g in ("상승", "하락", "횡보")},
               high_vol=sum(1 for r in lab if r["high_vol"] == "1"),
               preR_filled=sum(1 for r in back if r["A_status"] == "closed" and r["A_preR"] != ""),
               example_onlyA=next((r for r in back if r["B_status"] == "none"), None),
               signal_id_format_ok=sid_ok, both_identical=same, max_abs_d_both=max_d_both,
               n_d_both_over_0_01=sum(1 for r in both if abs(float(r["d_both"])) > 0.01))
    ok = sid_ok and same and onlyA > 0 and onlyB > 0 and out["header_ok"] and len(lab) > 0.9 * len(rows) \
        and out["preR_filled"] == sum(1 for r in back if r["A_status"] == "closed") and len(back) == len(rows)
    # 국면 라벨이 미래를 보지 않음: 라벨 시각 직전 완성 일봉만
    t_chk = k("2026-01-10 10:00")
    l1 = reg.label(t_chk)
    reg2 = RG.Regime([(d_, c_) for d_, c_ in RG.daily_from_5m(T, C) if d_ + dt.timedelta(days=1) <= t_chk])
    out["no_lookahead"] = l1 == reg2.label(t_chk)
    return ok and out["no_lookahead"], out


def f7(runs, datas):
    """손절 분해 합 = R (±0.001R)"""
    out = {}
    ok = True
    worst = 0.0
    # 본 장부(로컬 저장 봉) + baseline 그림자
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    cands = {m: bot.load_candles(bot.candle_path(m)) for m in MARKETS}
    P = engine.load_params(cfg)
    R = bot.compute_main(P, cands, k(cfg["start_time"]), [])
    groups = [("main", R["trades"], R["datas"], 5)]
    sh = next(s for s in cfg["shadow_ledgers"] if s["name"] == "baseline_v141_5m")
    Ps = bot.shadow_params(cfg, sh)
    bt, bd = [], {}
    for m in MARKETS:
        D = bot.to_data(cands[m]); c = engine.coin_of(m); bd[c] = D
        Rs = engine.run(D, Ps, start_time=k(sh["start_time"]), close_at_end=False, coin=c)
        for t in Rs["trades"]:
            t["coin"] = c
        bt += Rs["trades"]
    groups.append(("baseline", bt, bd, 5))
    for nm in ("v4_30m_fixed", "v4_30m_cost"):
        groups.append((nm + "(18개월 30m)", runs[nm][0], datas, 30))
    for g, trades, ds, _tf in groups:
        X.attach_decomp(trades, ds, 0.0005)
        ds_ = [t["decomp"] for t in trades if t.get("decomp")]
        miss = sum(1 for t in trades if not t.get("decomp"))
        err = max((abs(d["sum_R"] - d["R"]) for d in ds_), default=0.0)
        stops = [t["decomp"] for t in trades if t.get("decomp") and t["exit_reason"] == "손절" and not t.get("t1Hit")]
        plan_ok = all(abs(d["plan_R"] + 1) < 1e-9 for d in stops)
        reasons = sorted({t["exit_reason"] for t in trades})
        out[g] = dict(trades=len(trades), decomposed=len(ds_), missing=miss, max_abs_err=err, stops=len(stops),
                      stop_plan_is_minus1=plan_ok, exit_reasons=reasons, stop_avg=X.stop_decomp_summary(trades))
        worst = max(worst, err)
        ok = ok and miss == 0 and err <= 0.001 and plan_ok
    out["worst_abs_err"] = worst
    # 실제 본 장부 손절 거래 상세(-1.2R 설명)
    out["main_detail"] = [dict(id=t["id"], coin=t["coin"], R=round(t["R"], 4), stop_pct=(t.get("why") or {}).get("stop_pct"),
                               rm=round(t["rm"]), rw=round(t["qty"] * abs(t["entry"] - t["stop0"])), pnl=round(t["pnl"]),
                               pnl_over_rm=round(t["pnl"] / t["rm"], 4),
                               **{kk: round(v, 4) for kk, v in t["decomp"].items() if isinstance(v, float) and kk != "R"})
                          for t in R["trades"]]
    return ok, out

# ---------------- F8 ----------------

def f8():
    out = dict(n_sd1=VP.n_required(1.0), n_sd_1_13078=VP.n_required(1.13078), n_none=VP.n_required(None))
    ok = out["n_sd1"] == 785 and out["n_sd_1_13078"] == 1004 and out["n_none"] is None
    rows = [dict(A_status="closed", B_status="closed", A_R="-1.2", B_R="-1.2", d_sig="0", d_both="0"),
            dict(A_status="closed", B_status="none", A_R="-1.2", B_R="", d_sig="1.2"),
            dict(A_status="none", B_status="closed", A_R="", B_R="2.0", d_sig="2.0"),
            dict(A_status="open", B_status="none", A_R="", B_R="")]
    ps = VP.power_stats(rows)
    out["synthetic"] = {kk: ps[kk] for kk in ("n_signals", "n_both", "n_only_A", "n_only_B", "n_open", "mean_d", "sd_d", "rho",
                                              "n_required", "progress")}
    sd = (sum((x - 3.2 / 3) ** 2 for x in (0, 1.2, 2.0)) / 2) ** 0.5
    ok = ok and ps["n_signals"] == 3 and ps["n_open"] == 1 and abs(ps["mean_d"] - 3.2 / 3) < 1e-9 and abs(ps["sd_d"] - sd) < 1e-9 \
        and ps["n_required"] == VP.n_required(sd)
    pp = os.path.join(REPO, "research", "v4", "power_prior.json")
    pr = json.load(open(pp, encoding="utf-8"))
    out["prior"] = {kk: pr["primary"][kk] for kk in ("n_signals", "mean_d", "sd_d", "rho", "n_required")}
    ok = ok and pr["primary"]["n_required"] == VP.n_required(pr["primary"]["sd_d"]) or \
        (ok and abs(pr["primary"]["n_required"] - VP.n_required(pr["primary"]["sd_d"])) <= 1)
    return ok, out

# ---------------- F9 ----------------

def f9(tmp, trades):
    p = os.path.join(tmp, "rv429.json"); calls = []

    def q(prompt, key=None, **kw):
        calls.append(1); raise gc.GeminiError("gemini-flash-latest: HTTP 429 RESOURCE_EXHAUSTED quota")
    now = k("2026-11-17 16:00")
    rvw.gemini_reviews(trades, p, now, key="FAKE", generate=q)
    d = rvw.load_reviews(p)
    n1 = len(calls)
    rvw.gemini_reviews(trades, p, now + dt.timedelta(hours=5), key="FAKE", generate=q)
    n2 = len(calls) - n1
    rvw.gemini_reviews(trades, p, now + dt.timedelta(hours=25), key="FAKE", generate=q)
    n3 = len(calls) - n1 - n2
    out = dict(first_calls=n1, backoff_until=d.get("backoff_until"), calls_5h_later=n2, calls_25h_later=n3,
               fails_recorded=sum(int(v.get("fails", 0)) for v in rvw.load_reviews(p)["reviews"].values()))
    ok = n1 == 1 and d.get("backoff_until") == "2026-11-18 16:00:00" and n2 == 0 and n3 == 1 and out["fails_recorded"] == 0
    return ok, out

# ---------------- F10 ----------------

def f10(tmp):
    out = {}
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    for s in cfg["shadow_ledgers"]:
        if s.get("pair_group"):
            s["start_time"] = None
    cands = {m: bot.load_candles(bot.candle_path(m)) for m in MARKETS}
    end = max(cands["KRW-BTC"]) + dt.timedelta(minutes=5)
    saved = (bot.SHADOW_DIR, bot.CONFIG, bot.PAIRS_CSV, bot.DAILY_CSV)
    try:
        bot.SHADOW_DIR = os.path.join(tmp, "shadow"); bot.CONFIG = os.path.join(tmp, "config.json")
        bot.PAIRS_CSV = os.path.join(tmp, "v4", "pairs.csv"); bot.DAILY_CSV = os.path.join(tmp, "btc_daily.csv")
        with open(bot.CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        t0 = time.perf_counter()
        bot.run_shadows(cfg, end, cands["KRW-BTC"], [], offline=True, preloaded=cands, review=True)
        out["run_shadows_s"] = round(time.perf_counter() - t0, 3)
        disk = json.load(open(bot.CONFIG, encoding="utf-8"))
        sts = {s["name"]: s.get("start_time") for s in disk["shadow_ledgers"]}
        out["start_times"] = sts
        first = min(min(c) for c in cands.values())
        exp = max(end, max(min(c) for c in cands.values()) + dt.timedelta(days=7))
        ok = sts["v4_30m_fixed"] == sts["v4_30m_cost"] and k(sts["v4_30m_fixed"]) >= exp \
            and k(sts["v4_30m_fixed"]).minute in (0, 30) and k(sts["v4_30m_fixed"]) - exp < dt.timedelta(minutes=30)
        out["first_5m"] = str(first)
        ok = ok and os.path.exists(bot.PAIRS_CSV)
        sj = json.load(open(os.path.join(bot.SHADOW_DIR, "v4_30m_cost", "status.json"), encoding="utf-8"))
        out["pair_summary"] = sj.get("pair_summary")
        ok = ok and sj.get("pair_summary", {}).get("n_required_prior") == 1004 and sj.get("timeframe") == 30
        hdr = next(csv.reader(open(os.path.join(bot.SHADOW_DIR, "baseline_v141_5m", "journal.csv"), encoding="utf-8-sig")))
        ok = ok and hdr[-len(X.DECOMP_COLS):] == X.DECOMP_COLS
        # 같은 설정으로 시작 시각을 과거로 당겨 실제 거래가 생기게 한 뒤 pairs 내용 확인
        for s in cfg["shadow_ledgers"]:
            if s.get("pair_group"):
                s["start_time"] = "2026-11-15 09:00"
        bot.run_shadows(cfg, end, cands["KRW-BTC"], [], offline=True, preloaded=cands, review=True)
        rows = VP.read_pairs(bot.PAIRS_CSV)
        out["pairs_rows_from_11_15"] = len(rows)
        out["pairs_example"] = rows[:3]
        sj = json.load(open(os.path.join(bot.SHADOW_DIR, "v4_30m_fixed", "status.json"), encoding="utf-8"))
        out["v4_fixed_status"] = {kk: sj.get(kk) for kk in ("trades_count", "avg_R", "independent_clusters", "start_time")}
        ok = ok and sj["pair_summary"]["n_signals"] + sj["pair_summary"]["n_open"] >= len([r for r in rows if r.get("d_sig")])
    finally:
        bot.SHADOW_DIR, bot.CONFIG, bot.PAIRS_CSV, bot.DAILY_CSV = saved
    # tf_data: 완성 봉만, research aggregate 와 같음
    from common import aggregate
    c = cands["KRW-BTC"]
    ts = sorted(c)
    D = bot.tf_data(c, 30)
    A = aggregate(ts, [c[t][0] for t in ts], [c[t][1] for t in ts], [c[t][2] for t in ts], [c[t][3] for t in ts],
                  [c[t][4] for t in ts], 30)
    last_complete = ts[-1] >= A[0][-1] + dt.timedelta(minutes=25)
    n_exp = len(A[0]) - (0 if last_complete else 1)
    same = D.T == A[0][:n_exp] and D.C == A[4][:n_exp] and D.H == A[2][:n_exp] and D.V == A[5][:n_exp]
    c2 = {t: v for t, v in c.items() if t <= A[0][-2] + dt.timedelta(minutes=20)}      # 마지막에서 두 번째 구간을 미완성으로
    D2 = bot.tf_data(c2, 30)
    out["tf_data"] = dict(bars=D.n, same_as_research_aggregate=same, incomplete_dropped=D2.T[-1] == A[0][-3],
                          dir_aux=(D.dir_min, D.aux_min, D.bar_min))
    ok = ok and same and D2.T[-1] == A[0][-3] and (D.dir_min, D.aux_min, D.bar_min) == (90, 360, 30)
    # event_data_fields: 상태 파일 없음 → UNKNOWN, 있음 → OK
    saved2 = bot.EVENT_FEED_STATE
    try:
        bot.EVENT_FEED_STATE = os.path.join(tmp, "nofile.json")
        e0 = bot.event_data_fields({"eventSource": "feeds"}, k("2026-11-18 12:00"), None, [])["event_data"]
        bot.EVENT_FEED_STATE = os.path.join(tmp, "ef.json")
        ef.save_state(bot.EVENT_FEED_STATE, {"schedule": {"last_success": "2026-11-18 10:00:00"},
                                             "breaking": {"last_success": "2026-11-18 09:00:00"}})
        evs = [(k("2026-11-18 22:30"), "[일정] CPI (USD)", "high", None, None, None, None, "https://x.org"),
               (k("2026-11-19 22:30"), "[일정] Claims (USD)", "medium", None, None, None, None, "https://x.org"),
               (k("2026-11-30 22:30"), "far", "high", None, None, None, None, "https://x.org")]
        e1_ = bot.event_data_fields({"eventSource": "feeds"}, k("2026-11-18 12:00"), None, evs)["event_data"]
    finally:
        bot.EVENT_FEED_STATE = saved2
    out["event_data"] = [(e0["schedule"], e0["breaking"]), (e1_["schedule"], e1_["breaking"]), [u["name"] for u in e1_["upcoming"]]]
    ok = ok and out["event_data"] == [("UNKNOWN", "UNKNOWN"), ("OK", "STALE"), ["[일정] CPI (USD)", "[일정] Claims (USD)"]]
    return ok, out


def main():
    res = dict(method=__doc__.strip(), tests={})
    tmp = tempfile.mkdtemp(prefix="feedsv4_")
    t_all = time.perf_counter()
    try:
        for name, fn in (("F1_calendar", lambda: f1(tmp)), ("F2_rss_keywords", lambda: f2(tmp)),
                         ("F3_stale_unknown", lambda: f3(tmp)), ("F4_clusters", f4)):
            ok, det = fn()
            res["tests"][name] = dict(pass_=ok, **det)
            print(name, "PASS" if ok else "FAIL", flush=True)
        t0 = time.perf_counter()
        runs, datas, split = v4_runs()
        res["v4_runs_s"] = round(time.perf_counter() - t0, 1)
        for name, fn in (("F5_reproduce_E2E8_30m", lambda: f5(runs)), ("F6_signal_id_pairs", lambda: f6(tmp, runs, datas, split)),
                         ("F7_decomp_sum", lambda: f7(runs, datas)), ("F8_power", f8),
                         ("F9_gemini_429_backoff", lambda: f9(tmp, runs["v4_30m_fixed"][0][:5])),
                         ("F10_bot_wiring", lambda: f10(tmp))):
            ok, det = fn()
            res["tests"][name] = dict(pass_=ok, **det)
            print(name, "PASS" if ok else "FAIL", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["total_s"] = round(time.perf_counter() - t_all, 1)
    res["pass"] = all(t["pass_"] for t in res["tests"].values())
    with open(os.path.join(HERE, "c_feeds_v4_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("검증 4:", "PASS" if res["pass"] else "FAIL", res["total_s"], "s")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
