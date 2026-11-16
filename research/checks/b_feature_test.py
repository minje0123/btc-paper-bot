# -*- coding: utf-8 -*-
"""
B 기능 시험(임시 폴더, 네트워크·운영 장부 사용 안 함)
  1) 그림자 장부를 켜면 ledger/shadow/{이름}/ 에 trades.csv·journal.csv·status.json 이 생기고, 본 장부 결과는 그대로인지
     (BTC: 운영 candles_5m.csv / ETH: 18개월 데이터 일부를 임시 candles_ETH_5m.csv 로)
  2) eventBlock=true 일 때 일정 시각 근처 진입이 막히는지, false 면 그대로인지
결과: research/checks/b_feature_result.json
"""
import csv
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import bot     # noqa: E402
import engine  # noqa: E402

META = os.path.normpath(os.path.join(REPO, "..", "tasks", "btc-meta", "data"))


def main():
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        bot.SHADOW_DIR = os.path.join(tmp, "shadow")
        bot.DATA_DIR = tmp
        bot.CONFIG = os.path.join(tmp, "config.json")
        # ETH 임시 5분봉(2026-11-10 ~ 11-16) — 18개월 데이터에서 복사
        with open(os.path.join(META, "data_ETH_5m.csv"), encoding="utf-8") as f, \
                open(os.path.join(tmp, "candles_ETH_5m.csv"), "w", newline="", encoding="utf-8") as g:
            w = csv.writer(g)
            w.writerow(["time_kst", "open", "high", "low", "close", "volume", "source"])
            for r in csv.DictReader(f):
                if r["time_kst"] >= "2026-11-10":
                    w.writerow([r["time_kst"], r["open"], r["high"], r["low"], r["close"], r["volume"], "bithumb"])
        cfg = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8"))
        cfg["shadow_ledgers"] = [dict(name="test_v141_2coin", enabled=True, markets=["KRW-BTC", "KRW-ETH"],
                                      start_time=None, initial_capital=1000000,
                                      params=dict(slipMode="tick", dayTargetP=0, firstWinHalf=False, withdrawPct=0))]
        json.dump(cfg, open(bot.CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        main_c = bot.load_candles(os.path.join(REPO, "data", "candles_5m.csv"))
        end = dt.datetime(2026, 11, 14, 0, 0, tzinfo=engine.KST)    # 시작 시각 자동 기록 시험용(과거로 고정)
        bot.run_shadows(cfg, end, main_c, [], offline=True)
        d = os.path.join(bot.SHADOW_DIR, "test_v141_2coin")
        st = json.load(open(os.path.join(d, "status.json"), encoding="utf-8"))
        cfg_after = json.load(open(bot.CONFIG, encoding="utf-8"))
        res["shadow"] = dict(files=sorted(os.listdir(d)), trades_count=st["trades_count"], avg_R=st["avg_R"],
                             coins={k: dict(trades=v["trades_count"], has_position=v["has_position"]) for k, v in st["coins"].items()},
                             start_time_written=cfg_after["shadow_ledgers"][0]["start_time"])
        print("그림자:", res["shadow"])
        # 2) 경제 일정 진입 금지
        b = engine.load_csv(os.path.join(META, "data_BTC_5m.csv"))
        cut = [i for i, t in enumerate(b[0]) if t >= dt.datetime(2026, 9, 1, tzinfo=engine.KST)][0]
        bars = tuple(x[cut:] for x in b)
        D = engine.Data(*bars)
        P0 = engine.load_params(dict(cfg, rule_history=[]))
        R0 = engine.run(D, P0)
        tr = R0["trades"][5]
        ev = [(tr["entry_time"] + dt.timedelta(minutes=10), "시험 일정", "high")]
        R_off = engine.run(D, P0, events=ev)
        R_on = engine.run(D, engine.load_params(dict(cfg, rule_history=[], eventBlock=True)), events=ev)
        on_ids = {t["id"] for t in R_on["trades"]}
        res["event_block"] = dict(test_event=str(ev[0][0]), blocked_trade=tr["id"],
                                  off_identical=[t["id"] for t in R_off["trades"]] == [t["id"] for t in R0["trades"]],
                                  on_trade_removed=tr["id"] not in on_ids,
                                  on_no_entry_in_window=all(not (ev[0][0] - dt.timedelta(minutes=30) <= t["entry_time"] < ev[0][0] + dt.timedelta(minutes=15))
                                                            for t in R_on["trades"]))
        print("경제 일정:", res["event_block"])
        tmpj = os.path.join(tmp, "j.csv")
        bot.write_journal(R0["trades"][:3], tmpj, events=ev)
        res["journal_sample"] = open(tmpj, encoding="utf-8-sig").read()
    res["pass"] = (res["shadow"]["trades_count"] >= 0 and res["shadow"]["start_time_written"] == "2026-11-14 00:00"
                   and res["event_block"]["off_identical"] and res["event_block"]["on_trade_removed"]
                   and res["event_block"]["on_no_entry_in_window"])
    with open(os.path.join(HERE, "b_feature_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(res["journal_sample"])
    print("PASS" if res["pass"] else "FAIL")


if __name__ == "__main__":
    main()
