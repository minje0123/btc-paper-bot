# -*- coding: utf-8 -*-
"""
B 검증. 이식 전 코드(HEAD 사본: research/checks/baseline_head/ 의 engine.py·bot.py·config.json)와
이식 후 코드(저장소 engine.py·bot.py·config.json)로 같은 data/candles_5m.csv 를 각각 다시 계산해 비교한다.

  비교: trades(모든 필드, 일지용 'why' 제외), position, pending, pending_close, day(오늘 상태), 계좌 값
        (realized·final·equity·unrealized·seed·withdrawn·withdrawals), events, curve,
        그리고 실제 장부 출력 파일(trades.csv, status.json[updated_at 제외], ACCOUNT.md[업데이트 시각 줄 제외]).
  운영 장부(ledger/)는 읽지도 쓰지도 않는다 — 출력은 임시 폴더에만 쓴다. 네트워크 안 씀.
  추가: 18개월 BTC·ETH 5분봉(운영 설정 신호 + 자금 관리)으로도 같은 비교(긴 데이터에서 상위봉 정렬 변경 확인).

  python research/checks/b_equivalence.py            # 비교 실행 → research/checks/b_result.json
  python research/checks/b_equivalence.py snap DIR CFG CANDLES OUT   # (내부용) 한쪽 코드로 스냅샷
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BASE = os.path.join(HERE, "baseline_head")
META = os.path.normpath(os.path.join(REPO, "..", "tasks", "btc-meta", "data"))
FIXED_NOW = "2026-11-17 08:30:00"


def ser(x):
    if isinstance(x, dt.datetime):
        return x.isoformat()
    if isinstance(x, (dt.date,)):
        return x.isoformat()
    if isinstance(x, set):
        return sorted(ser(v) for v in x)
    if isinstance(x, float):
        return repr(x)
    if isinstance(x, dict):
        return {k: ser(v) for k, v in x.items() if k != "why"}
    if isinstance(x, (list, tuple)):
        return [ser(v) for v in x]
    return x


def snap(code_dir, cfg_path, candles_path, out_dir):
    sys.path.insert(0, code_dir)
    import engine
    import bot
    assert os.path.dirname(os.path.abspath(engine.__file__)) == os.path.abspath(code_dir)
    os.makedirs(out_dir, exist_ok=True)
    bot.LEDGER_DIR = out_dir
    bot.TRADES = os.path.join(out_dir, "trades.csv")
    bot.STATUS = os.path.join(out_dir, "status.json")
    bot.ACCOUNT = os.path.join(out_dir, "ACCOUNT.md")
    if hasattr(bot, "JOURNAL"):
        bot.JOURNAL = os.path.join(out_dir, "journal.csv")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    start_time = engine.parse_kst(cfg["start_time"])
    P = engine.load_params(cfg)
    candles = bot.load_candles(candles_path) if "path" in bot.load_candles.__code__.co_varnames else None
    if candles is None:
        bot.CANDLES = candles_path
        candles = bot.load_candles()
    ts = sorted(candles)
    D = engine.Data(ts, [candles[t][0] for t in ts], [candles[t][1] for t in ts], [candles[t][2] for t in ts],
                    [candles[t][3] for t in ts], [candles[t][4] for t in ts])
    kw = {}
    if hasattr(engine, "load_events"):
        kw["events"] = engine.load_events(os.path.join(REPO, cfg.get("events_file") or "data/events.csv"))
    R = engine.run(D, P, start_time=start_time, close_at_end=False, **kw)
    R["last_bar_time"] = ts[-1].strftime(bot.TFMT)
    now = dt.datetime.strptime(FIXED_NOW, "%Y-%m-%d %H:%M:%S").replace(tzinfo=engine.KST)
    status = bot.build_status(cfg, P, R, start_time, "bithumb", None, now)
    bot.write_trades(R["trades"])
    bot.write_json(bot.STATUS, status)
    bot.write_account(status, R)
    if hasattr(bot, "write_journal"):
        bot.write_journal(R["trades"], bot.JOURNAL, events=kw.get("events"))
        bot.run_shadows(cfg, now, candles, kw.get("events"), offline=True)   # 기본 설정: 그림자 없음 → 아무것도 안 함
    keep = ("trades", "position", "pending", "pending_close", "day", "realized", "final", "equity", "unrealized",
            "seed", "withdrawn", "withdrawals", "events", "curve", "curve_acct", "dayStopDays", "lossLimitDays",
            "consecDays", "targetDays", "last_close")
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({k: ser(R[k]) for k in keep}, f, ensure_ascii=False, sort_keys=True)


def run_snap(code_dir, cfg, candles, out):
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "snap", code_dir, cfg, candles, out],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-3000:])


def read(p):
    with open(p, encoding="utf-8-sig") as f:
        return f.read()


def compare(a, b):
    out = {}
    ra, rb = json.loads(read(os.path.join(a, "result.json"))), json.loads(read(os.path.join(b, "result.json")))
    for k in ra:
        out[k] = ra[k] == rb.get(k)
    out["n_trades"] = len(ra["trades"])
    out["file trades.csv"] = read(os.path.join(a, "trades.csv")) == read(os.path.join(b, "trades.csv"))
    sa, sb = json.loads(read(os.path.join(a, "status.json"))), json.loads(read(os.path.join(b, "status.json")))
    out["file status.json"] = sa == sb
    out["file ACCOUNT.md"] = read(os.path.join(a, "ACCOUNT.md")) == read(os.path.join(b, "ACCOUNT.md"))
    return out


def to_bot_csv(src, dst, start, end):
    """18개월 데이터 일부를 봇 CSV 형식으로(연구 데이터 → 임시 파일)"""
    import csv
    with open(src, encoding="utf-8") as f, open(dst, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["time_kst", "open", "high", "low", "close", "volume", "source"])
        for r in csv.DictReader(f):
            if start <= r["time_kst"] < end:
                w.writerow([r["time_kst"], r["open"], r["high"], r["low"], r["close"], r["volume"], "bithumb"])


def main():
    res = dict(method=__doc__.strip(), cases=[])
    with tempfile.TemporaryDirectory() as tmp:
        cases = [("운영: 현재 config.json + data/candles_5m.csv", os.path.join(BASE, "config.json"),
                  os.path.join(REPO, "config.json"), os.path.join(REPO, "data", "candles_5m.csv"))]
        for coin in ("BTC", "ETH"):
            # 18개월 전체(시작 2025-06-01, rule_history 그대로) — 같은 운영 설정으로 긴 기간
            c = os.path.join(tmp, f"{coin}.csv")
            to_bot_csv(os.path.join(META, f"data_{coin}_5m.csv"), c, "2025-05-18", "2026-11-17")
            for nm, cfgp in (("old", os.path.join(BASE, "config.json")), ("new", os.path.join(REPO, "config.json"))):
                cfg = json.load(open(cfgp, encoding="utf-8"))
                cfg["start_time"] = "2025-06-01 00:00"
                cfg["rule_history"] = [dict(h, until=u) for h, u in zip(cfg["rule_history"], ("2026-03-01 00:00", "2025-12-01 00:00"))]
                json.dump(cfg, open(os.path.join(tmp, f"{coin}_{nm}.json"), "w", encoding="utf-8"), ensure_ascii=False)
            cases.append((f"18개월 {coin} 5분봉 + 운영 설정(시작 2025-06-01, 규칙 변경 2회)",
                          os.path.join(tmp, f"{coin}_old.json"), os.path.join(tmp, f"{coin}_new.json"), c))
        for k, (label, cfg_old, cfg_new, candles) in enumerate(cases):
            a, b = os.path.join(tmp, f"old{k}"), os.path.join(tmp, f"new{k}")
            run_snap(BASE, cfg_old, candles, a)
            run_snap(REPO, cfg_new, candles, b)
            cmp_ = compare(a, b)
            ok = all(v for kk, v in cmp_.items() if kk != "n_trades")
            res["cases"].append(dict(label=label, identical=ok, checks=cmp_))
            print(label, "→", "완전히 같음" if ok else "다름", cmp_)
            if k == 0:
                res["journal_preview"] = read(os.path.join(b, "journal.csv"))
    res["pass"] = all(c["identical"] for c in res["cases"])
    with open(os.path.join(HERE, "b_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("PASS" if res["pass"] else "FAIL")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "snap":
        snap(*sys.argv[2:6])
    else:
        main()
