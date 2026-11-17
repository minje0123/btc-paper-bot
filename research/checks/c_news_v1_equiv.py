# -*- coding: utf-8 -*-
"""
news_v1 동일성 검증 도우미 — 지정한 코드 폴더(bot.py·engine.py·tools/)로 본 장부·baseline·v4 두 장부를 오프라인 재계산해
거래 목록을 JSON 으로 출력한다. 데이터는 항상 이 저장소 data/ (읽기만). 네트워크·쓰기 없음.

  python research/checks/c_news_v1_equiv.py --code-dir <폴더> --config <config.json> [--out 파일]

비교 대상: 본 장부(compute_main, data/events.csv 그대로), baseline_v141_5m(engine.run, 설정 시작 시각),
v4_30m_fixed·v4_30m_cost(30분봉, 설정 시작 시각 + 거래가 생기도록 2026-11-15 09:00 시작 두 경우).
"""
import argparse
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]


def tr(t):
    return [t.get("coin") or "BTC", t["id"], t["entry_time"].strftime("%Y-%m-%d %H:%M"), t["exit_time"].strftime("%Y-%m-%d %H:%M"),
            t["exit_reason"], round(t["R"], 10), round(t["pnl"], 6)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.code_dir))
    engine = importlib.import_module("engine")
    bot = importlib.import_module("bot")
    cfg = json.load(open(a.config, encoding="utf-8-sig"))
    data = os.path.join(REPO, "data")
    path = lambda m: os.path.join(data, "candles_5m.csv" if m == "KRW-BTC" else f"candles_{m.split('-')[1]}_5m.csv")  # noqa: E731
    cands = {m: bot.load_candles(path(m)) for m in MARKETS}
    events = engine.load_events(os.path.join(data, "events.csv"))
    out = {"code_dir": os.path.abspath(a.code_dir), "last_bar": str(max(cands["KRW-BTC"]))}
    P = engine.load_params(cfg)
    R = bot.compute_main(P, cands, engine.parse_kst(cfg["start_time"]), events)
    out["main"] = [tr(t) for t in R["trades"]]
    out["main_open"] = len(R["positions"]) + len(R["pendings"])
    out["main_blocked"] = [[str(b["time"]), b["coin"], b["side"], b.get("event")] for b in R.get("blocked") or []]
    out["main_final"] = round(R["final"], 6)
    shs = {s["name"]: s for s in cfg["shadow_ledgers"]}
    for nm in ("baseline_v141_5m", "v4_30m_fixed", "v4_30m_cost"):
        sh = shs[nm]
        Ps = bot.shadow_params(cfg, sh)
        tf = int(sh.get("timeframe", 5) or 5)
        for lab, st in (("cfg", sh["start_time"]), ("from_11_15", "2026-11-15 09:00")):
            if tf == 5 and lab != "cfg":
                continue
            trades = []
            for m in sh["markets"]:
                c = cands[m]
                if tf == 5:
                    ts = sorted(c)
                    D = engine.Data(ts, [c[t][0] for t in ts], [c[t][1] for t in ts], [c[t][2] for t in ts],
                                    [c[t][3] for t in ts], [c[t][4] for t in ts])
                else:
                    D = bot.tf_data(c, tf)
                coin = m.split("-")[1]
                Rs = engine.run(D, Ps, start_time=engine.parse_kst(st), close_at_end=False, events=events, coin=coin)
                for t in Rs["trades"]:
                    t["coin"] = coin
                trades += Rs["trades"]
            out[f"{nm}|{lab}"] = sorted(tr(t) for t in trades)
    s = json.dumps(out, ensure_ascii=False, sort_keys=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(s)
    else:
        print(s)


if __name__ == "__main__":
    main()
