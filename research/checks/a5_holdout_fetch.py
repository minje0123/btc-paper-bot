# -*- coding: utf-8 -*-
"""
A5. 최종 시험(holdout) 데이터 확보 — 받기·해시 기록만 한다. 분석·백테스트에 절대 쓰지 않는다.

  python research/checks/a5_holdout_fetch.py select          # B 코인 선정(빗썸 ticker, 24시간 거래대금)
  python research/checks/a5_holdout_fetch.py fetch A BTC      # A: 18개월 시작 이전 12개월 5분봉
  python research/checks/a5_holdout_fetch.py fetch B <COIN>   # B: 18개월과 같은 기간 5분봉
  python research/checks/a5_holdout_fetch.py manifest         # research/holdout_manifest.json 작성

받은 파일은 메모리에서 바로 SHA-256·행 수·날짜 범위를 계산해 _meta_*.json 에 남긴다
(파일을 다시 읽어 분석하지 않음). 표준 라이브러리만 사용.
"""
import csv
import datetime as dt
import glob
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
HOLD = os.path.join(RESEARCH, "holdout")
UA = {"Accept": "application/json", "User-Agent": "paper-research (read-only candles)"}

DATA_START = dt.datetime(2025, 5, 18, 13, 5, tzinfo=KST)     # 18개월 데이터 첫 봉
DATA_END_EXCL = dt.datetime(2026, 11, 16, 13, 5, tzinfo=KST) # 18개월 데이터 마지막 봉(13:00) 다음
RANGES = {
    "A": (dt.datetime(2024, 5, 18, 0, 0, tzinfo=KST), DATA_START),   # 18개월 시작 이전 12개월(+13시간)
    "B": (DATA_START, DATA_END_EXCL),                                 # 18개월과 같은 기간
}
USED = {"BTC", "ETH", "XRP", "SOL"}
STABLE = {"USDT", "USDC", "USDE", "USDS", "DAI", "USD1", "PYUSD", "FDUSD", "TUSD", "USDP", "BUSD", "RLUSD", "USD0"}


def get(url):
    for k in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(1 + k); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(1 + k)
    raise RuntimeError("요청 실패: " + url)


def first_bar_near(market, t):
    """t 직전 5분봉 1개(시각) — 상장 여부 확인용"""
    q = urllib.parse.urlencode({"market": market, "count": 1, "to": t.strftime("%Y-%m-%dT%H:%M:%S")})
    d = get("https://api.bithumb.com/v1/candles/minutes/5?" + q)
    if not d:
        return None
    return d[0]["candle_date_time_kst"]


def select():
    now = dt.datetime.now(KST)
    mk = get("https://api.bithumb.com/v1/market/all?isDetails=false")
    krw = [m["market"] for m in mk if m["market"].startswith("KRW-")]
    tick = []
    for i in range(0, len(krw), 80):
        tick += get("https://api.bithumb.com/v1/ticker?markets=" + ",".join(krw[i:i + 80]))
        time.sleep(0.15)
    rank = sorted(tick, key=lambda x: -float(x["acc_trade_price_24h"]))
    rows, chosen = [], []
    for k, x in enumerate(rank[:30], 1):
        coin = x["market"].split("-")[1]
        note = ""
        if coin in USED:
            note = "제외: 18개월 연구 코인"
        elif coin in STABLE:
            note = "제외: 스테이블코인(가격 고정, 방향 매매 대상 아님)"
        elif len(chosen) < 3:
            near = first_bar_near(x["market"], DATA_START + dt.timedelta(minutes=5))
            if near is None or near[:16] < "2025-05-18T12:00":
                note = f"제외: 18개월 기간 시작({DATA_START:%Y-%m-%d %H:%M}) 시점 5분봉 없음(상장 전 또는 공백, 직전 봉={near})"
            else:
                chosen.append(coin); note = "선정"
        rows.append(dict(rank=k, market=x["market"], acc_trade_price_24h=float(x["acc_trade_price_24h"]), note=note))
    out = dict(selected_at_kst=now.strftime("%Y-%m-%d %H:%M:%S"),
               source="빗썸 공개 API /v1/market/all + /v1/ticker (KRW 마켓 전체), acc_trade_price_24h",
               criteria=("24시간 거래대금(acc_trade_price_24h) 내림차순. BTC·ETH·XRP·SOL 제외, 스테이블코인 제외, "
                         "18개월 데이터 시작 시점에 5분봉이 있는(이미 상장된) 코인만. 위에서부터 3개."),
               stablecoin_list=sorted(STABLE), selected=chosen, top30=rows)
    os.makedirs(os.path.join(HOLD, "B"), exist_ok=True)
    with open(os.path.join(HOLD, "B", "selection.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(selected=chosen, top=[(r["market"], round(r["acc_trade_price_24h"] / 1e8), r["note"]) for r in rows[:12]]),
                     ensure_ascii=False))


def fetch(ds, coin):
    start, end = RANGES[ds]
    market = f"KRW-{coin}"
    rows = {}
    to = end
    pages = 0
    while to > start:
        q = urllib.parse.urlencode({"market": market, "count": 200, "to": to.strftime("%Y-%m-%dT%H:%M:%S")})
        d = get("https://api.bithumb.com/v1/candles/minutes/5?" + q)
        pages += 1
        if not d:
            break
        oldest = None
        for x in d:
            t = dt.datetime.strptime(x["candle_date_time_kst"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST)
            oldest = t if oldest is None or t < oldest else oldest
            if start <= t < end:
                rows[t] = (x["opening_price"], x["high_price"], x["low_price"], x["trade_price"],
                           x["candle_acc_trade_volume"])
        if oldest is None or oldest >= to:
            break
        to = oldest
        time.sleep(0.08)
    ts = sorted(rows)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["time_kst", "open", "high", "low", "close", "volume"])

    def num(v):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    for t in ts:
        o, h, l, c, v = rows[t]
        w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), num(o), num(h), num(l), num(c), repr(float(v))])
    b = buf.getvalue().encode("utf-8")
    path = os.path.join(HOLD, ds, f"data_{coin}_5m.csv")
    with open(path, "wb") as f:
        f.write(b)
    expected = int((end - start).total_seconds() // 300)
    meta = dict(set=ds, coin=coin, market=market, file=os.path.relpath(path, RESEARCH).replace("\\", "/"),
                requested_range_kst=[start.strftime("%Y-%m-%d %H:%M"), (end - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")],
                first_bar_kst=ts[0].strftime("%Y-%m-%d %H:%M") if ts else None,
                last_bar_kst=ts[-1].strftime("%Y-%m-%d %H:%M") if ts else None,
                rows=len(ts), expected_5m_slots=expected, missing_slots=expected - len(ts),
                sha256=hashlib.sha256(b).hexdigest(), bytes=len(b), api_pages=pages,
                source="빗썸 공개 API /v1/candles/minutes/5 (to 파라미터로 거슬러 받기)",
                fetched_at_kst=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(HOLD, ds, f"_meta_{coin}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False))


def manifest():
    files = []
    for p in sorted(glob.glob(os.path.join(HOLD, "*", "_meta_*.json"))):
        with open(p, encoding="utf-8") as f:
            files.append(json.load(f))
    sel = None
    sp = os.path.join(HOLD, "B", "selection.json")
    if os.path.exists(sp):
        with open(sp, "rb") as f:
            raw = f.read()
        sel = json.loads(raw.decode("utf-8"))
        sel = dict(selected=sel["selected"], selected_at_kst=sel["selected_at_kst"], criteria=sel["criteria"],
                   file="holdout/B/selection.json", sha256=hashlib.sha256(raw).hexdigest())
    out = dict(
        note="최종 시험 데이터. 사전 등록서 v3 2장. 받은 즉시 해시를 기록했으며, FINAL=1 없이 읽지 않는다(research/holdout_guard.py). "
             "접근은 4장 선택 절차로 정한 최종 후보 1개(+기준선 v1.4.1 5m)에 대해 1회만.",
        created_at_kst=dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        sets={"A": dict(desc="18개월 데이터(BTC·ETH·XRP·SOL) 시작 이전 12개월, 같은 4코인",
                        range_kst=[RANGES["A"][0].strftime("%Y-%m-%d %H:%M"),
                                   (RANGES["A"][1] - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")]),
              "B": dict(desc="미사용 거래대금 상위 3코인, 18개월 데이터와 같은 기간",
                        range_kst=[RANGES["B"][0].strftime("%Y-%m-%d %H:%M"),
                                   (RANGES["B"][1] - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")],
                        selection=sel)},
        files=files)
    with open(os.path.join(RESEARCH, "holdout_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"manifest: 파일 {len(files)}개")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "select":
        select()
    elif cmd == "fetch":
        fetch(sys.argv[2], sys.argv[3])
    elif cmd == "manifest":
        manifest()
    elif cmd == "fetchall":          # A 4코인 + B 선정 3코인 동시 받기 → manifest
        import threading
        with open(os.path.join(HOLD, "B", "selection.json"), encoding="utf-8") as f:
            bsel = json.load(f)["selected"]
        jobs = [("A", c) for c in ("BTC", "ETH", "XRP", "SOL")] + [("B", c) for c in bsel]
        errs = []

        def job(ds, c):
            try:
                fetch(ds, c)
            except Exception as e:
                errs.append(f"{ds}/{c}: {e!r}")
        th = [threading.Thread(target=job, args=j) for j in jobs]
        for t in th:
            t.start()
        for t in th:
            t.join()
        manifest()
        print("오류:", errs)
