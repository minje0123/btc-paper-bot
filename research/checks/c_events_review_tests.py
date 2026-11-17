# -*- coding: utf-8 -*-
"""
C 검증 3 — (A) 자동 복기 · (B) 이벤트 회피 단위 테스트. 오프라인(네트워크·API 키 없이 가짜 Gemini 응답), 로컬 data/ 의
5분봉만 읽음. 결과는 research/checks/c_events_review_result.json 에만 쓴다(ledger/·state/·data/ 는 건드리지 않음,
임시 폴더 사용). research/holdout 사용 안 함.

  E1 added_at 이후만 차단 : 실제 BTC 첫 거래의 진입 시각에 high 일정 → added_at 없음/이전이면 그 진입이 막히고(blocked 기록),
                            added_at 이 진입 뒤면 결과가 이벤트 없는 계산과 완전히 같음
  E2 코인별 차단          : 4코인 run_multi — coins=ETH 일정은 ETH 진입만 막고, coins=BTC 일정은 그 ETH 거래를 안 막음.
                            run() 도 coin 인자로 같은 필터. medium 일정·eventBlock=false 는 아무것도 안 막음
  E3 옛 3열 파일 호환      : time_kst,name,importance 파일 → coins=ALL·창 기본값·added_at 없음(늘 적용), 잘못된 줄은 건너뜀,
                            옛 3열 파일에 Gemini 결과를 덧붙이면 8열 머리글로 바뀌고 기존 줄 보존
  E4 Gemini 가짜 응답     : 정기 일정(잘못된 시각·과거·7일 밖·과도한 창·URL 없음·중복 거름, medium 은 기록), 돌발(시작<다음 봉·
                            창>240분·오래된 뉴스 거름, 시작=다음 봉, 하루 high 6건 한도), 모델 대체(404 → 목록의 flash),
                            키 없음이면 호출 안 함, 응답이 JSON 아니면 실패 기록만
  E5 원인 태그 예시        : 합성 봉으로 즉시 역행 / T1 근접 후 반전 / 1시간 역방향 / 변동성 급등 / 이벤트 근처 /
                            T1 후 본절 / 추세 연장 익절 / T1 반익절만 + 실제 본 장부 거래의 태그
  E6 reviews.json 캐시     : 첫 실행에 거래마다 1회 호출, 다시 돌리면 0회, 키 없으면 0회·파일 없음, 실패는 3회까지만 재시도
  E7 하루 복기 리포트      : 전날 리포트 파일 1회 생성(다시 불러도 덮어쓰지 않음), 알림 ID R{날짜}, 요약 3~4줄
  E8 status 이벤트 필드    : 창 안이면 event_block.active, upcoming_events, 막힌 신호 목록 / 새 창 알림 1회용 ID
  E9 rule_history         : eventBlock 은 과거 단계가 물려받지 않음
"""
import copy
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import engine  # noqa: E402
import bot  # noqa: E402
from tools import gemini_client as gc  # noqa: E402
from tools import gemini_events as ge  # noqa: E402
from tools import review as rvw  # noqa: E402

KST = engine.KST
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
TF = "%Y-%m-%d %H:%M"


def k(s):
    return engine.parse_kst(s)


def ser(x):
    if isinstance(x, (dt.datetime, dt.date)):
        return x.isoformat()
    if isinstance(x, set):
        return sorted(ser(v) for v in x)
    if isinstance(x, float):
        return repr(x)
    if isinstance(x, dict):
        return {a: ser(b) for a, b in x.items()}
    if isinstance(x, (list, tuple)):
        return [ser(v) for v in x]
    return x


def base_cfg():
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    return {a: b for a, b in cfg.items() if a not in ("shadow_ledgers", "rule_history")}


def ev(t, name="테스트 일정", imp="high", coins=None, b4=None, af=None, added=None, src="https://example.com/x"):
    return (t, name, imp, coins, b4, af, added, src)


def same_result(Ra, Rb):
    keys = ("trades", "position", "pending", "realized", "equity", "events", "curve")
    return all(ser(Ra[x]) == ser(Rb[x]) for x in keys)

# ---------------- E1 ----------------

def e1(Db):
    cfg = base_cfg(); cfg.update(markets=["KRW-BTC"], maxOpenPositions=1, maxOpenRiskPct=0, slipMode="fixed",
                                 start_time="2026-11-15 00:00", eventBlock=True)
    P = engine.load_params(cfg)
    st = k(cfg["start_time"])
    R0 = engine.run(Db, P, start_time=st, events=[])
    t0 = R0["trades"][0]
    te = t0["entry_time"]                                     # 진입 체결 = 신호 봉 마감
    out = dict(first_trade=t0["id"], entry=te.strftime(TF))
    cases = {}
    for label, added in (("added_at 없음", None), ("added_at = 신호 봉 마감 5분 전", te - dt.timedelta(minutes=5)),
                         ("added_at = 진입 시각과 같음", te), ("added_at = 진입 5분 뒤", te + dt.timedelta(minutes=5)),
                         ("added_at = 하루 뒤", te + dt.timedelta(days=1))):
        R = engine.run(Db, P, start_time=st, events=[ev(te, added=added)])
        ids = [t["id"] for t in R["trades"]]
        blocked_first = t0["id"] not in ids
        cases[label] = dict(first_trade_blocked=blocked_first, blocked=[(b["time"].strftime(TF), b["reason"]) for b in R["blocked"]],
                            identical_to_no_event=same_result(R, R0))
    ok = (cases["added_at 없음"]["first_trade_blocked"] and cases["added_at = 신호 봉 마감 5분 전"]["first_trade_blocked"]
          and cases["added_at = 진입 시각과 같음"]["first_trade_blocked"]
          and not cases["added_at = 진입 5분 뒤"]["first_trade_blocked"]
          and cases["added_at = 하루 뒤"]["identical_to_no_event"]
          and all(c["blocked"] for n, c in cases.items() if c["first_trade_blocked"]))
    # 같은 일정 + eventBlock=false → 결과 동일, 막힌 신호 0
    Poff = engine.load_params(dict(cfg, eventBlock=False))
    Roff = engine.run(Db, Poff, start_time=st, events=[ev(te)])
    R0off = engine.run(Db, Poff, start_time=st, events=[])
    out["eventBlock_false_identical"] = same_result(Roff, R0off) and not Roff["blocked"]
    out["cases"] = cases
    return ok and out["eventBlock_false_identical"], out

# ---------------- E2 ----------------

def e2(Ds):
    cfg = base_cfg(); cfg.update(start_time="2026-11-15 00:00", eventBlock=True)
    P = engine.load_params(cfg); st = k(cfg["start_time"])
    R0 = engine.run_multi(Ds, P, start_time=st, events=[])
    eth = [t for t in R0["trades"] if t["coin"] == "ETH"]
    te = eth[0]["entry_time"]
    out = dict(eth_trade=eth[0]["id"], entry=te.strftime(TF))
    Re = engine.run_multi(Ds, P, start_time=st, events=[ev(te, coins=frozenset({"ETH"}), b4=10, af=10)])
    Rb = engine.run_multi(Ds, P, start_time=st, events=[ev(te, coins=frozenset({"BTC"}), b4=10, af=10)])
    Rm = engine.run_multi(Ds, P, start_time=st, events=[ev(te, imp="medium", b4=10, af=10)])
    win = (te - dt.timedelta(minutes=10), te + dt.timedelta(minutes=10))
    eth_in_win = [t["id"] for t in Re["trades"] + Re["positions"] if t["coin"] == "ETH" and win[0] <= t["entry_time"] < win[1]]
    out["ETH일정: ETH 첫 거래 막힘"] = eth[0]["id"] not in [t["id"] for t in Re["trades"]] and not eth_in_win
    out["ETH일정: 막힌 신호 코인"] = sorted({b["coin"] for b in Re["blocked"]})
    out["BTC일정: ETH 첫 거래 그대로"] = eth[0]["id"] in [t["id"] for t in Rb["trades"]]
    out["BTC일정: 막힌 신호 코인"] = sorted({b["coin"] for b in Rb["blocked"]})
    out["medium 일정: 결과 동일·막힘 0"] = same_result(Rm, R0) and not Rm["blocked"]
    # run() 의 coin 필터
    cfg1 = dict(cfg, markets=["KRW-ETH"], market="KRW-ETH", maxOpenPositions=1, maxOpenRiskPct=0)
    P1 = engine.load_params(cfg1)
    r_ok = engine.run(Ds["KRW-ETH"], P1, start_time=st, events=[])
    r_btc = engine.run(Ds["KRW-ETH"], P1, start_time=st, events=[ev(te, coins=frozenset({"BTC"}))], coin="ETH")
    r_eth = engine.run(Ds["KRW-ETH"], P1, start_time=st, events=[ev(te, coins=frozenset({"ETH"}))], coin="ETH")
    out["run(): BTC일정은 ETH 계산 불변"] = same_result(r_btc, r_ok) and not r_btc["blocked"]
    out["run(): ETH일정은 ETH 신호 막음"] = bool(r_eth["blocked"]) and all(b["coin"] == "ETH" for b in r_eth["blocked"])
    ok = (out["ETH일정: ETH 첫 거래 막힘"] and out["ETH일정: 막힌 신호 코인"] == ["ETH"] and out["BTC일정: ETH 첫 거래 그대로"]
          and set(out["BTC일정: 막힌 신호 코인"]) <= {"BTC"} and out["medium 일정: 결과 동일·막힘 0"]
          and out["run(): BTC일정은 ETH 계산 불변"] and out["run(): ETH일정은 ETH 신호 막음"])
    return ok, out

# ---------------- E3 ----------------

def e3(tmp, Db):
    p = os.path.join(tmp, "old3.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 옛 형식\ntime_kst,name,importance\n2026-11-16 22:30,미국 CPI,high\n2026-11-17 04:00,FOMC,medium\n"
                "잘못된시각,깨진 줄,high\n2026-11-18 21:30,이름만\n")
    evs = engine.load_events(p)
    out = dict(rows=[(e[0].strftime(TF), e[1], e[2], e[3], e[4], e[5], e[6], e[7]) for e in evs])
    ok = (len(evs) == 3 and evs[0][3] is None and evs[0][4] is None and evs[0][6] is None and evs[2][2] == "")
    P = engine.load_params(dict(base_cfg(), eventBlock=True, eventBeforeMin=30, eventAfterMin=30))
    w = engine.event_windows(P, evs, "SOL")
    out["windows_SOL"] = [(a.strftime(TF), b.strftime(TF), ad, nm) for a, b, ad, nm in w]
    ok = ok and len(w) == 1 and w[0][0] == k("2026-11-16 22:00") and w[0][1] == k("2026-11-16 23:00")
    out["hit_always(added 없음)"] = engine.event_hit(w, k("2026-11-16 22:00")) == "미국 CPI"
    ok = ok and out["hit_always(added 없음)"] and engine.event_hit(w, k("2026-11-16 23:00")) is None
    # 옛 파일에 새 줄 덧붙이기 → 8열 머리글, 기존 줄 보존
    ge.append_events(p, [dict(time_kst="2026-11-20 22:30", name="새 일정", importance="high", coins="BTC|ETH",
                              before_min="", after_min="60", added_at="2026-11-17 16:00:00", source="https://a.b/c")])
    head = [ln for ln in open(p, encoding="utf-8").read().splitlines() if not ln.startswith("#")][0]
    evs2 = engine.load_events(p)
    out["after_append_header"] = head
    out["after_append_rows"] = len(evs2)
    ok = ok and head == ",".join(engine.EVENT_COLS) and len(evs2) == 4 and evs2[-1][3] == frozenset({"BTC", "ETH"}) \
        and evs2[-1][5] == 60.0 and evs2[-1][6] == k("2026-11-17 16:00")
    # 실제 data/events.csv(새 형식, 빈 파일)도 읽힘
    out["data_events_csv_rows"] = len(engine.load_events(os.path.join(REPO, "data", "events.csv")))
    return ok, out

# ---------------- E4 ----------------

class FakeAPI:
    """가짜 generativelanguage: 모델별 응답을 정해 두고 호출을 센다"""

    def __init__(self, replies, models=None, fail_models=()):
        self.replies, self.models, self.fail = replies, models or [], set(fail_models)
        self.calls = []

    def __call__(self, method, url, key, body):
        self.calls.append((method, url.split("/v1beta/")[-1]))
        assert key == "FAKE-KEY"
        if method == "GET":
            return 200, {"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]} for m in self.models]}
        m = url.split("/models/")[1].split(":")[0]
        if m in self.fail:
            raise gc.GeminiError("HTTP 404 NOT_FOUND model")
        txt = body["contents"][0]["parts"][0]["text"]
        rep = self.replies["schedule"] if "예정된 일정" in txt else self.replies["breaking"]
        return 200, {"candidates": [{"content": {"parts": [{"text": rep}]}}]}


def e4(tmp):
    now = k("2026-11-17 16:02:30")
    nb = ge.next_bar_start(now)
    sched = {"events": [
        {"time_kst": "2026-11-19 22:30", "name": "미국 10월 CPI", "importance": "high", "coins": "ALL", "before_min": None, "after_min": None, "source": "https://www.bls.gov/schedule"},
        {"time_kst": "2026-11-19 22:30", "name": "미국 CPI (중복)", "importance": "high", "coins": "ALL", "source": "https://x.com/a"},
        {"time_kst": "2026/11/20 10:00", "name": "잘못된 시각", "importance": "high", "coins": "ALL", "source": "https://x.com/b"},
        {"time_kst": "2026-11-17 15:00", "name": "과거 일정", "importance": "high", "coins": "ALL", "source": "https://x.com/c"},
        {"time_kst": "2026-12-20 10:00", "name": "7일 밖", "importance": "high", "coins": "ALL", "source": "https://x.com/d"},
        {"time_kst": "2026-11-20 04:00", "name": "과도한 창", "importance": "high", "coins": "ALL", "before_min": 200, "after_min": 100, "source": "https://x.com/e"},
        {"time_kst": "2026-11-20 04:00", "name": "FOMC 금리 결정", "importance": "high", "coins": "ALL", "before_min": 60, "after_min": 90, "source": "https://www.federalreserve.gov/"},
        {"time_kst": "2026-11-21 12:00", "name": "SOL 업그레이드", "importance": "medium", "coins": "SOL", "source": "https://solana.com/x"},
        {"time_kst": "2026-11-21 13:00", "name": "근거 없음", "importance": "high", "coins": "BTC"},
        {"time_kst": "2026-11-21 14:00", "name": "모르는 코인", "importance": "high", "coins": "DOGE", "source": "https://x.com/f"},
        {"time_kst": "2026-11-21 15:00", "name": "중요도 이상", "importance": "critical", "coins": "BTC", "source": "https://x.com/g"},
    ]}
    shocks = {"shocks": [
        {"name": "거래소 해킹", "coins": "ETH", "importance": "high", "duration_min": 120, "happened_at": "2026-11-17 15:40", "source": "https://news.example.com/hack"},
        {"name": "너무 긴 창", "coins": "ALL", "importance": "high", "duration_min": 500, "source": "https://news.example.com/a"},
        {"name": "시작이 과거", "coins": "ALL", "importance": "high", "duration_min": 90, "start_kst": "2026-11-17 15:55", "source": "https://news.example.com/b"},
        {"name": "오래된 뉴스", "coins": "ALL", "importance": "high", "duration_min": 90, "happened_at": "2026-11-17 09:00", "source": "https://news.example.com/c"},
        {"name": "짧은 창", "coins": "SOL", "importance": "medium", "duration_min": 20, "source": "https://news.example.com/d"},
    ]}
    replies = {"schedule": "검색 결과입니다.\n```json\n" + json.dumps(sched, ensure_ascii=False) + "\n```",
               "breaking": json.dumps(shocks, ensure_ascii=False)}
    api = FakeAPI(replies, models=["gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash-preview-x", "gemini-2.5-pro"],
                  fail_models={"gemini-2.5-flash"})
    evp = os.path.join(tmp, "events.csv"); stp = os.path.join(tmp, "gemini_events.json")
    shutil.copy(os.path.join(REPO, "data", "events.csv"), evp)
    orig = gc.TRANSPORT
    gc.TRANSPORT = api
    logs = []
    try:
        cfg = dict(base_cfg())
        added = ge.maybe_update(now, cfg, evp, stp, log=lambda m, warn=False: logs.append(m), key="FAKE-KEY")
        st = json.load(open(stp, encoding="utf-8"))
        evs = engine.load_events(evp)
        names = [e[1] for e in evs]
        out = dict(added=[(r["time_kst"], r["name"], r["importance"], r["coins"], r["before_min"], r["after_min"]) for r in added],
                   schedule_rejected=st["schedule"]["last_rejected"], breaking_rejected=st["breaking"]["last_rejected"],
                   model_used=st.get("model"), calls=api.calls)
        ok = (set(names) == {"미국 10월 CPI", "FOMC 금리 결정", "SOL 업그레이드", "[돌발] 거래소 해킹", "[돌발] 짧은 창"})
        hack = [e for e in evs if e[1] == "[돌발] 거래소 해킹"][0]
        short = [e for e in evs if e[1] == "[돌발] 짧은 창"][0]
        out["돌발 시작 = 다음 봉"] = hack[0] == nb and hack[4] == 0 and hack[5] == 120 and hack[3] == frozenset({"ETH"})
        out["돌발 짧은 창 → 60분으로"] = short[5] == 60 and short[2] == "medium"
        out["모델 대체"] = st.get("model") == "gemini-2.5-flash-preview-x"
        out["added_at = 지금"] = all(e[6] == now.replace(microsecond=0) for e in evs)
        ok = ok and out["돌발 시작 = 다음 봉"] and out["돌발 짧은 창 → 60분으로"] and out["모델 대체"] and out["added_at = 지금"]
        # 곧바로 다시 부르면 시간 조건 때문에 호출 안 함
        n0 = len(api.calls)
        ge.maybe_update(now + dt.timedelta(minutes=5), cfg, evp, stp, key="FAKE-KEY")
        out["5분 뒤 재호출 수"] = len(api.calls) - n0
        # 30분 뒤: 돌발만 다시(같은 사건은 중복으로 거름)
        n0 = len(api.calls)
        ge.maybe_update(now + dt.timedelta(minutes=31), cfg, evp, stp, key="FAKE-KEY")
        st2 = json.load(open(stp, encoding="utf-8"))
        out["31분 뒤 호출(돌발만)"] = [c[1] for c in api.calls[n0:]]
        out["31분 뒤 중복 거름"] = st2["breaking"]["last_added"] == 0
        ok = ok and out["5분 뒤 재호출 수"] == 0 and len(out["31분 뒤 호출(돌발만)"]) == 1 and out["31분 뒤 중복 거름"]
    finally:
        gc.TRANSPORT = orig
    # 하루 돌발 high 한도 6건
    many = [{"name": f"사건{i}", "coins": "ALL", "importance": "high", "duration_min": 60, "source": f"https://n.example.com/{i}"} for i in range(8)]
    acc, rej = ge.validate_breaking(many, now, [])
    out["하루 한도: 8건 중 받음"] = len(acc)
    ok = ok and len(acc) == 6 and all(r[1] == "하루 돌발 한도" for r in rej)
    exist = [ev(nb, name="[돌발] 기존", added=now - dt.timedelta(hours=1))] * 5
    acc2, _ = ge.validate_breaking(many, now, exist)
    out["하루 한도: 오늘 이미 5건이면 받음"] = len(acc2)
    ok = ok and len(acc2) == 1
    # 키 없음 → 호출 0, 응답이 JSON 아님 → 실패 기록
    api2 = FakeAPI({"schedule": "죄송합니다. 찾지 못했습니다.", "breaking": "{깨진 json"})
    gc.TRANSPORT = api2
    try:
        r = ge.maybe_update(now, {}, evp, os.path.join(tmp, "s2.json"), key="")
        out["키 없음: 호출 수"] = len(api2.calls)
        ok = ok and r == [] and not api2.calls and not os.path.exists(os.path.join(tmp, "s2.json"))
        r = ge.maybe_update(now, {}, evp, os.path.join(tmp, "s3.json"), key="FAKE-KEY")
        s3 = json.load(open(os.path.join(tmp, "s3.json"), encoding="utf-8"))
        out["JSON 아님: 실패 기록"] = (s3["schedule"]["last_ok"], s3["breaking"]["last_ok"], s3["schedule"]["fails"])
        ok = ok and r == [] and s3["schedule"]["last_ok"] is False and s3["breaking"]["last_ok"] is False
    finally:
        gc.TRANSPORT = orig
    out["extract_json 엄격"] = []
    for s in ("```json\n{\"a\": 1}\n```", "앞말 {\"a\": 2} 뒷말", "no json", "[1,2]"):
        try:
            out["extract_json 엄격"].append(gc.extract_json(s))
        except ValueError as e:
            out["extract_json 엄격"].append(f"오류: {type(e).__name__}")
    ok = ok and out["extract_json 엄격"][:2] == [{"a": 1}, {"a": 2}] and all(isinstance(x, str) for x in out["extract_json 엄격"][2:])
    return ok, out

# ---------------- E5 ----------------

def synth(n=160, base=100_000_000.0, rng=50_000.0, spike_at=None):
    t0 = k("2026-11-10 00:00")
    T = [t0 + dt.timedelta(minutes=5 * i) for i in range(n)]
    O, H, L, C, V = [], [], [], [], []
    for i in range(n):
        r = rng * (8 if spike_at is not None and spike_at - 2 <= i <= spike_at else 1)
        O.append(base); C.append(base); H.append(base + r); L.append(base - r); V.append(1.0)
    return engine.Data(T, O, H, L, C, V)


def mk_trade(D, i0, i1, side=1, entry=100_000_000.0, stop=99_500_000.0, pnl=-1.0, R=-1.0, reason="손절",
             t1hit=False, dir60=0):
    return dict(id=f"T{i0}", coin="BTC", side=side, tag="구간", grade="B", entry=entry, stop0=stop, t1=entry + 750_000,
                entry_time=D.T[i0], exit_time=D.T[i1], pnl=pnl, R=R, exit_reason=reason, t1Hit=t1hit, dir60=dir60,
                why=dict(dir15=side, dir60=dir60), exits=[])


def e5(Ds):
    out, ok = {}, True
    D = synth()
    # 손절, 2봉 보유 → 즉시 역행
    D.H[120] = 100_000_000 + 50_000; D.L[121] = 99_400_000
    t = mk_trade(D, 120, 121); r = rvw.review_trade(t, D)
    out["즉시 역행"] = r; ok = ok and "즉시 역행" in r["tags"] and r["held_bars"] == 2
    # 손절, MFE 0.8R 뒤 손절(8봉) → T1 근접 후 반전, 1시간 반대 → 역방향
    D = synth(); D.H[123] = 100_400_000; D.L[127] = 99_400_000
    t = mk_trade(D, 120, 127, dir60=-1); r = rvw.review_trade(t, D)
    out["T1 근접 후 반전 + 1시간 역방향"] = r
    ok = ok and {"T1 근접 후 반전", "1시간 추세 역방향 진입"} <= set(r["tags"]) and "즉시 역행" not in r["tags"] and r["mfe_R"] == 0.8
    # 신호 봉 직전 변동성 급등 + 이벤트 근처
    D = synth(spike_at=119)
    t = mk_trade(D, 120, 130); evs = [ev(D.T[120] + dt.timedelta(minutes=90), name="미국 CPI")]
    r = rvw.review_trade(t, D, evs)
    out["변동성 급등 + 이벤트 근처"] = r; ok = ok and {"변동성 급등", "이벤트 근처"} <= set(r["tags"])
    r2 = rvw.review_trade(t, D, [ev(D.T[120] + dt.timedelta(minutes=90), coins=frozenset({"ETH"}))])
    ok = ok and "이벤트 근처" not in r2["tags"]           # 다른 코인 일정은 무관
    # 이익 쪽
    D = synth()
    for name, reason, R, want in (("T1 후 본절", "본절", 0.4, "T1 후 본절"), ("추세 연장 익절", "다음 스윙", 1.3, "추세 연장 익절"),
                                  ("T1 반익절만(반대 신호 0.6R)", "반대 장악형", 0.6, "T1 반익절만"),
                                  ("T1 반익절만(전량)", "T1 반익절", 0.9, "T1 반익절만")):
        t = mk_trade(D, 120, 140, pnl=R * 100, R=R, reason=reason, t1hit=True, dir60=-1)
        r = rvw.review_trade(t, D)
        out[name] = r["tags"]; ok = ok and r["tags"] == [want]
    # 숏 MFE/MAE 방향
    D = synth(); D.L[122] = 99_600_000; D.H[124] = 100_200_000
    t = mk_trade(D, 120, 125, side=-1, entry=100_000_000, stop=100_500_000, pnl=50, R=0.1, reason="돌파 실패")
    r = rvw.review_trade(t, D)
    out["숏 MFE/MAE"] = (r["mfe_R"], r["mae_R"]); ok = ok and r["mfe_R"] == 0.8 and r["mae_R"] == -0.4
    # 실제 본 장부(현재 config, 오프라인) 거래의 태그
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    P = engine.load_params(cfg)
    R = bot.compute_main(P, {m: bot.load_candles(bot.candle_path(m)) for m in MARKETS}, k(cfg["start_time"]), [])
    rvw.attach_reviews(R["trades"], R["datas"], [])
    out["실제 본 장부"] = [(t["id"], round(t["R"], 3), t["review"]["held_bars"], t["review"]["mfe_R"], t["review"]["mae_R"],
                         t["review"]["entry_hour"], t["review"]["tags"]) for t in R["trades"]]
    ok = ok and all("review" in t for t in R["trades"])
    return ok, out, R

# ---------------- E6 ----------------

def e6(tmp, trades):
    p = os.path.join(tmp, "reviews.json")
    calls = []

    def fake(prompt, key=None, **kw):
        calls.append(prompt)
        return "진입 직후 역행했습니다. 다음엔 1시간 방향을 확인하세요.", "fake-flash"
    now = k("2026-11-17 16:00")
    a = rvw.gemini_reviews(trades, p, now, key="", generate=fake)
    out = {"키 없음: 호출": len(calls), "키 없음: 파일 생김": os.path.exists(p)}
    ok = not calls and not os.path.exists(p) and a == {}
    a = rvw.gemini_reviews(trades, p, now, key="FAKE", generate=fake, max_calls=10)
    out["1차 호출 수"] = len(calls); out["1차 해설 수"] = len(a)
    n1 = len(calls)
    b = rvw.gemini_reviews(trades, p, now, key="FAKE", generate=fake, max_calls=10)
    out["2차(재계산) 추가 호출"] = len(calls) - n1
    ok = ok and n1 == len(trades) and len(a) == len(trades) and len(calls) == n1 and a == b
    out["프롬프트 예시"] = calls[0] if calls else ""
    # 실패는 최대 3회까지
    p2 = os.path.join(tmp, "reviews2.json"); fails = []

    def bad(prompt, key=None, **kw):
        fails.append(1); raise gc.GeminiError("HTTP 503")
    for _ in range(5):
        rvw.gemini_reviews(trades[:1], p2, now, key="FAKE", generate=bad)
    out["실패 재시도 횟수(5번 실행)"] = len(fails)
    ok = ok and len(fails) == rvw.GEMINI_MAX_FAILS
    # 한 번 실행 상한
    p3 = os.path.join(tmp, "reviews3.json"); calls.clear()
    rvw.gemini_reviews(trades, p3, now, key="FAKE", generate=fake)
    out["실행당 상한"] = len(calls)
    ok = ok and len(calls) == min(len(trades), rvw.GEMINI_MAX_CALLS_PER_RUN)
    # 일지 열
    bot_j = os.path.join(tmp, "journal.csv")
    bot.write_journal(trades, bot_j, coin_of=bot.coin_tr, events=[], review=True, texts=b)
    import csv
    rows = list(csv.reader(open(bot_j, encoding="utf-8-sig")))
    out["일지 새 열"] = rows[0][-5:]
    ok = ok and rows[0][-5:] == ["보유봉수", "MFE_R", "MAE_R", "원인태그", "복기해설"] and rows[1][-1].startswith("진입 직후")
    return ok, out

# ---------------- E7 ----------------

def e7(tmp, trades):
    d = os.path.join(tmp, "review")
    day = trades[0]["exit_time"].date()
    now = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 2), tzinfo=KST)
    e = rvw.maybe_daily_report(now, trades, d, k("2026-11-16 13:40"), {trades[0]["id"]: "해설 예시"})
    p = os.path.join(d, f"{day}.md")
    md = open(p, encoding="utf-8").read()
    with open(p, "a", encoding="utf-8") as f:
        f.write("<!-- 표시 -->")
    e2_ = rvw.maybe_daily_report(now + dt.timedelta(minutes=5), trades, d, None)
    kept = open(p, encoding="utf-8").read().endswith("<!-- 표시 -->")
    out = dict(event_id=e["id"], summary=e["msg"], same_id_again=e2_["id"] == e["id"], not_overwritten=kept,
               sections=[ln for ln in md.splitlines() if ln.startswith("#")], msg_lines=len(e["msg"].splitlines()),
               notify_text=bot.event_message(e))
    ok = (e["id"] == f"R{day:%Y%m%d}" and kept and 3 <= out["msg_lines"] <= 4 and "## 원인 태그별" in md
          and "## 반복된 실패 패턴 상위 3개 (이날)" in md and "해설 예시" in md)
    out["before_start"] = rvw.maybe_daily_report(k("2026-11-16 14:00"), trades, d, k("2026-11-16 13:40"))
    ok = ok and out["before_start"] is None
    return ok, out

# ---------------- E8 ----------------

def e8(R):
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    cfg["eventBlock"] = True
    P = engine.load_params(cfg)
    last = k(R["last_bar_time"])
    evs = [ev(last + dt.timedelta(minutes=20), name="미국 CPI", coins=frozenset({"BTC", "ETH"})),
           ev(last + dt.timedelta(days=2), name="FOMC"), ev(last + dt.timedelta(days=1), name="작은 일정", imp="medium")]
    f = bot.event_fields(P, R, evs, last + dt.timedelta(minutes=6))
    w = bot.window_events(P, evs, last + dt.timedelta(minutes=6), last)
    fo = bot.event_fields(engine.load_params(dict(cfg, eventBlock=False)), R, evs, last)
    wo = bot.window_events(engine.load_params(dict(cfg, eventBlock=False)), evs, last, last)
    out = dict(event_block=f["event_block"], upcoming=[(u["time"], u["name"]) for u in f["upcoming_events"]],
               window_msgs=[x["msg"] for x in w], window_ids=[x["id"] for x in w], off_block=fo["event_block"], off_msgs=len(wo))
    ok = (f["event_block"]["active"] and f["event_block"]["coins"] == ["BTC", "ETH"] and len(f["upcoming_events"]) == 2
          and len(w) == 1 and w[0]["msg"].startswith("⛔ 이벤트 회피: 미국 CPI BTC·ETH") and not fo["event_block"]["active"]
          and not wo and bot.window_events(P, evs, last + dt.timedelta(minutes=6), last)[0]["id"] == w[0]["id"])
    return ok, out

# ---------------- E9 ----------------

def e9():
    cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    cfg["eventBlock"] = True
    P = engine.load_params(cfg)
    out = dict(current=P["eventBlock"], history=[hp["eventBlock"] for _u, hp in P["rule_history"]],
               config_eventBlock=json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))["eventBlock"])
    return P["eventBlock"] and not any(out["history"]) and out["config_eventBlock"] is False, out


def main():
    res = dict(method=__doc__.strip(), tests={})
    Ds = {m: bot.to_data(bot.load_candles(bot.candle_path(m))) for m in MARKETS}
    tmp = tempfile.mkdtemp(prefix="evrv_")
    try:
        for name, fn in (("E1_added_at", lambda: e1(Ds["KRW-BTC"])), ("E2_per_coin", lambda: e2(Ds)),
                         ("E3_old_3col_file", lambda: e3(tmp, Ds["KRW-BTC"])), ("E4_gemini_fake", lambda: e4(tmp))):
            ok, det = fn()
            res["tests"][name] = dict(pass_=ok, **det)
        ok, det, R = e5(Ds)
        res["tests"]["E5_cause_tags"] = dict(pass_=ok, **det)
        trades = R["trades"]
        ok, det = e6(tmp, trades); res["tests"]["E6_reviews_cache"] = dict(pass_=ok, **det)
        ok, det = e7(tmp, trades); res["tests"]["E7_daily_report"] = dict(pass_=ok, **det)
        ok, det = e8(R); res["tests"]["E8_status_fields"] = dict(pass_=ok, **det)
        ok, det = e9(); res["tests"]["E9_rule_history"] = dict(pass_=ok, **det)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res["pass"] = all(t["pass_"] for t in res["tests"].values())
    with open(os.path.join(HERE, "c_events_review_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    for n, t in res["tests"].items():
        print(n, "PASS" if t["pass_"] else "FAIL")
    print("검증 3:", "PASS" if res["pass"] else "FAIL")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
