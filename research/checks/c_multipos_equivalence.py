# -*- coding: utf-8 -*-
"""
C 검증 1 — 다중 코인·다중 포지션(engine.run_multi + 새 bot.py 출력)이 기존 장부를 1원도 바꾸지 않는지.

  사례 1) config.json 에서 markets=[KRW-BTC], maxOpenPositions=1, maxOpenRiskPct=0, slipMode=fixed 로 바꿔 계산
  사례 2) 실제 config.json 그대로(15:00 부터 4코인 단계 포함, 4코인 봉 모두 넣음)
     → 새 코드로 trades.csv·status.json·journal.csv 를 임시 폴더에 써서 현재 ledger/ 파일과 비교
       - trades.csv: 새 '코인' 열을 뺀 나머지가 글자까지 같고, '코인' 은 모두 BTC
       - status.json: 현재 ledger/status.json 의 모든 키(updated_at 제외)가 값까지 같음 (새 키는 추가만)
       - journal.csv: 글자까지 같음
  사례 3) 엔진 원시 결과: engine.run(BTC) vs engine.run_multi({BTC}) — 거래(모든 필드)·이벤트·곡선·하루 상태·잔고
  사례 4) 코인 하나씩(ETH·XRP·SOL, slipMode tick): run() vs run_multi(markets=[그 코인]) 원시 결과 비교
  사례 5) 그림자 장부: 새 bot.run_shadows(오프라인)를 임시 폴더에 써서 ledger/shadow/ 와 비교(updated_at 제외)

  ledger/·data/ 는 읽기만 한다. 결과: research/checks/c_equiv_result.json
  python research/checks/c_multipos_equivalence.py
"""
import copy
import csv
import datetime as dt
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
import engine  # noqa: E402
import bot  # noqa: E402

LEDGER = os.path.join(REPO, "ledger")
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]


def ser(x):
    if isinstance(x, (dt.datetime, dt.date)):
        return x.isoformat()
    if isinstance(x, set):
        return sorted(ser(v) for v in x)
    if isinstance(x, float):
        return repr(x)
    if isinstance(x, dict):
        # run_multi 가 추가한 coin·market·rm_signal·riskCut 은 run() 에 없는 '추가 정보' 라 비교에서 뺀다
        return {k: ser(v) for k, v in x.items() if k not in ("coin", "market", "rm_signal", "riskCut")}
    if isinstance(x, (list, tuple)):
        return [ser(v) for v in x]
    return x


def read(p):
    with open(p, encoding="utf-8-sig") as f:
        return f.read()


def load_all():
    return {m: bot.load_candles(bot.candle_path(m)) for m in MARKETS}


def write_outputs(cfg, cands, out_dir, ledger_status):
    bot.LEDGER_DIR = out_dir
    bot.TRADES = os.path.join(out_dir, "trades.csv")
    bot.STATUS = os.path.join(out_dir, "status.json")
    bot.ACCOUNT = os.path.join(out_dir, "ACCOUNT.md")
    bot.JOURNAL = os.path.join(out_dir, "journal.csv")
    os.makedirs(out_dir, exist_ok=True)
    start_time = engine.parse_kst(cfg["start_time"])
    P = engine.load_params(cfg)
    events = engine.load_events(os.path.join(REPO, cfg.get("events_file") or "data/events.csv"))
    del bot._warnings[:]
    R = bot.compute_main(P, cands, start_time, events)
    now = engine.parse_kst(ledger_status["updated_at"])
    status = bot.build_status(cfg, P, R, start_time, ledger_status["data_source"],
                              ledger_status["data_source_this_run"], now)
    bot.write_trades(R["trades"])
    bot.write_json(bot.STATUS, status)
    bot.write_account(status, R)
    bot.write_journal(R["trades"], bot.JOURNAL, coin_of=bot.coin_tr, events=events)
    return R, status


def cmp_trades_csv(new_path, old_path):
    new = list(csv.reader(io.StringIO(read(new_path))))
    old = list(csv.reader(io.StringIO(read(old_path))))
    ci = new[0].index("코인")
    coins = {r[ci] for r in new[1:]}
    stripped = [r[:ci] + r[ci + 1:] for r in new]
    return dict(same_without_coin_col=stripped == old, coin_col_all_BTC=coins <= {"BTC"},
                header_coin_after_번호=new[0][:2] == ["번호", "코인"], rows=len(new) - 1)


def cmp_status(new, old, skip=("updated_at",)):
    diff = {k: (old[k], new.get(k)) for k in old if k not in skip and new.get(k) != old[k]}
    return dict(same_on_old_keys=not diff, diff=diff, added_keys=sorted(set(new) - set(old)))


def raw_keys():
    return ("trades", "position", "pending", "pending_close", "day", "realized", "final", "equity", "unrealized",
            "seed", "withdrawn", "withdrawals", "events", "curve", "curve_acct", "curve_time", "dayStopDays",
            "lossLimitDays", "consecDays", "targetDays", "last_close")


def norm_alt(x, coin):
    """알트 코인 run_multi 의 '의도된 표기 차이'만 지운다: 거래ID 앞 '{코인}-', 청산내역 문자열(가격 소수 표기).
    체결 가격·수량·손익은 fills·pnl 로 그대로 비교된다."""
    if isinstance(x, dict):
        return {k: (v.replace(f"{coin}-", "", 1) if k == "id" and isinstance(v, str) else norm_alt(v, coin))
                for k, v in x.items() if k != "exits"}
    if isinstance(x, list):
        return [norm_alt(v, coin) for v in x]
    return x


def cmp_raw(Ra, Rb, coin=None):
    if coin and coin != "BTC":
        Ra = {k: norm_alt(Ra[k], coin) for k in raw_keys()}
        Rb = {k: norm_alt(Rb[k], coin) for k in raw_keys()}
    out = {k: ser(Ra[k]) == ser(Rb[k]) for k in raw_keys()}
    return dict(all_same=all(out.values()), detail=out, n_trades=len(Ra["trades"]), n_events=len(Ra["events"]))


def main():
    res = dict(method=__doc__.strip(), cases={})
    cfg0 = json.load(open(os.path.join(REPO, "config.json"), encoding="utf-8-sig"))
    led_status = json.load(open(os.path.join(LEDGER, "status.json"), encoding="utf-8-sig"))
    cands = load_all()
    btc_only = {"KRW-BTC": cands["KRW-BTC"]}
    with tempfile.TemporaryDirectory() as tmp:
        # ---- 사례 1·2: 장부 파일 비교 ----
        single = copy.deepcopy(cfg0)
        single.update(markets=["KRW-BTC"], maxOpenPositions=1, maxOpenRiskPct=0, slipMode="fixed")
        for name, cfg, cc in (("1_single_BTC_config", single, btc_only), ("2_actual_config_4coins", cfg0, cands)):
            od = os.path.join(tmp, name)
            R, st = write_outputs(cfg, cc, od, led_status)
            c = dict(trades_csv=cmp_trades_csv(os.path.join(od, "trades.csv"), os.path.join(LEDGER, "trades.csv")),
                     status_json=cmp_status(st, led_status),
                     journal_csv_same=read(os.path.join(od, "journal.csv")) == read(os.path.join(LEDGER, "journal.csv")),
                     n_trades=len(R["trades"]), last_bar_time=R["last_bar_time"],
                     positions=len(R["positions"]), pendings=len(R["pendings"]))
            c["pass"] = (c["trades_csv"]["same_without_coin_col"] and c["trades_csv"]["coin_col_all_BTC"]
                         and c["status_json"]["same_on_old_keys"] and c["journal_csv_same"])
            res["cases"][name] = c
        # ---- 사례 3: 원시 결과 BTC ----
        P = engine.load_params(cfg0)
        st0 = engine.parse_kst(cfg0["start_time"])
        ev = engine.load_events(os.path.join(REPO, "data", "events.csv"))
        Db = bot.to_data(cands["KRW-BTC"])
        Ra = engine.run(Db, P, start_time=st0, close_at_end=False, events=ev)
        Rb = engine.run_multi({"KRW-BTC": Db}, P, start_time=st0, events=ev)
        c = cmp_raw(Ra, Rb); c["pass"] = c["all_same"]
        res["cases"]["3_raw_run_vs_run_multi_BTC"] = c
        # ---- 사례 4: 코인 하나씩 (tick 슬리피지, 그림자 장부와 같은 시작) ----
        for m in ("KRW-ETH", "KRW-XRP", "KRW-SOL"):
            cf = {k: v for k, v in cfg0.items() if k != "rule_history"}
            cf.update(market=m, markets=[m], maxOpenPositions=1, maxOpenRiskPct=0, slipMode="tick",
                      start_time="2026-11-15 00:00")
            Pm = engine.load_params(cf)
            Dm = bot.to_data(cands[m])
            st_m = engine.parse_kst(cf["start_time"])
            Ra = engine.run(Dm, Pm, start_time=st_m, events=ev)
            Rb = engine.run_multi({m: Dm}, Pm, start_time=st_m, events=ev)
            c = cmp_raw(Ra, Rb, engine.coin_of(m)); c["pass"] = c["all_same"]
            c["note"] = "거래ID 앞 코인 접두어·청산내역 문자열(가격 소수 표기)만 정규화, 나머지 모든 필드 비교"
            res["cases"][f"4_raw_single_{engine.coin_of(m)}"] = c
        # ---- 사례 5: 그림자 장부 ----
        sd = os.path.join(tmp, "shadow")
        bot.SHADOW_DIR = sd
        bot.CONFIG = os.path.join(tmp, "config_unused.json")
        del bot._warnings[:]
        bot.run_shadows(cfg0, engine.parse_kst(led_status["updated_at"]), cands["KRW-BTC"], ev, offline=True,
                        preloaded=cands)
        sh_ok = True; sh = {}
        for s in cfg0.get("shadow_ledgers") or []:
            if not s.get("enabled"):
                continue
            a, b = os.path.join(LEDGER, "shadow", s["name"]), os.path.join(sd, s["name"])
            d = {}
            for fn in ("trades.csv", "journal.csv"):
                d[fn] = read(os.path.join(a, fn)) == read(os.path.join(b, fn))
            ja, jb = json.loads(read(os.path.join(a, "status.json"))), json.loads(read(os.path.join(b, "status.json")))
            ja.pop("updated_at", None); jb.pop("updated_at", None)
            d["status.json"] = ja == jb
            sh[s["name"]] = d
            sh_ok = sh_ok and all(d.values())
        res["cases"]["5_shadow_ledgers"] = dict(detail=sh, warnings=bot._warnings[:], **{"pass": sh_ok})
    res["pass"] = all(c["pass"] for c in res["cases"].values())
    with open(os.path.join(HERE, "c_equiv_result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    for k, c in res["cases"].items():
        print(k, "PASS" if c["pass"] else "FAIL", {kk: v for kk, v in c.items() if kk not in ("pass",)})
    print("검증 1:", "PASS" if res["pass"] else "FAIL")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
