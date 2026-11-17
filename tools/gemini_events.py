# -*- coding: utf-8 -*-
"""
(B) Gemini 이벤트 회피 — 고영향 일정·돌발 사태를 data/events.csv 에 추가한다(진입 금지 판단은 engine 이 함).

bot.py 가 실행 초반에 maybe_update() 를 부른다. 시간 조건은 스스로 판단:
  - 정기 일정 모드(eventScheduleHours, 기본 6시간마다): 앞으로 7일 안의 고영향 일정
      (미국 CPI·PCE·고용·FOMC 결정·연준 의장 발언, BTC·ETH·XRP·SOL ETF/SEC 판결, 대형 토큰 언락,
       네트워크 업그레이드·하드포크, 업비트/빗썸 유의·상장폐지 공지 등)
  - 돌발 모드(eventBreakingMin, 기본 30분마다): 최근 2시간 뉴스의 큰 충격(해킹·익스플로잇, 거래소 장애·출금 중단,
      규제 조치, 대규모 청산, 지정학 충격 등) → 지금부터 차단 창(시작 = 다음 5분봉, 길이 60~180분)
  - Gemini API + google_search 그라운딩. 그라운딩 도구와 responseSchema 를 함께 쓸 수 없는 모델이 있어
    'JSON 하나만' 답하게 하고 엄격하게 파싱·검증한다(validate_schedule / validate_breaking).
  - 검증: 시각 형식, 정기 일정은 미래 시각만(7일 안), 돌발은 시작 ≥ 다음 봉, 창 길이 ≤ 240분, 하루 돌발 high 최대 6건,
          같은 이름·시각(또는 같은 시각·코인) 중복 제거, 근거 URL(source) 필수, importance high/medium(medium 은 기록만).
  - 호출 시각·실패 상태·쓴 모델은 state/gemini_events.json (봇이 실행 중 만들고 갱신).
  - 키(GEMINI_API_KEY)가 없거나 실패하면 조용히 넘어가고 경고만. 절대 봇을 멈추지 않는다.
"""
import csv
import datetime as dt
import json
import os
import re

from engine import KST, EVENT_COLS, EVENT_COINS, parse_kst, _parse_coins

SCHEDULE_HOURS = 6.0
BREAKING_MIN = 30.0
SCHEDULE_RETRY_MIN = 60.0          # 정기 일정 호출이 실패하면 이만큼 뒤 다시
HORIZON_DAYS = 7
MAX_WINDOW_MIN = 240               # 창 길이(before + after) 상한
BREAK_MIN_LEN, BREAK_MAX_LEN = 60, 180
BREAKING_PER_DAY = 6               # 하루(KST, 추가 시각 기준) 돌발 high 차단 최대
BREAKING_LOOKBACK_H = 2
BREAKING_PREFIX = "[돌발] "
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")
TFMT = "%Y-%m-%d %H:%M"


def _floor5(t):
    t = t.replace(second=0, microsecond=0)
    return t - dt.timedelta(minutes=t.minute % 5)


def next_bar_start(now):
    """지금 진행 중인 봉의 다음 봉 시작 시각 (돌발 창 시작은 이 시각 이후)"""
    return _floor5(now) + dt.timedelta(minutes=5)


def _norm_name(s):
    return re.sub(r"[\s\[\]\(\)·,._\-]+", "", (s or "").lower())


def _coins_str(cs):
    return "ALL" if cs is None else "|".join([c for c in EVENT_COINS if c in cs] + sorted(c for c in cs if c not in EVENT_COINS))


def _parse_time(s):
    s = str(s or "").strip().replace("T", " ")
    if not TIME_RE.match(s):
        raise ValueError("시각 형식")
    return parse_kst(s)


def _coins_ok(raw, allowed):
    cs = _parse_coins(str(raw or "ALL"))
    if cs is not None and not cs <= set(allowed):
        raise ValueError("코인 이름")
    return cs


def _url_ok(u):
    u = str(u or "").strip()
    if not re.match(r"^https?://[^\s,]+\.[^\s,]+", u):
        raise ValueError("근거 URL 없음")
    return u


def _imp(x):
    x = str(x or "").strip().lower()
    if x not in ("high", "medium"):
        raise ValueError("importance")
    return x


def _clean_name(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip().replace(",", " ")
    if not s:
        raise ValueError("이름 없음")
    return s[:80]


def _dup(name, t, cs, existing, same_time_coin=True):
    n = _norm_name(name)
    for e in existing:
        if e[0] == t and (_norm_name(e[1]) == n or (same_time_coin and (e[3] if len(e) > 3 else None) == cs)):
            return True
    return False


def validate_schedule(items, now, existing, allowed=EVENT_COINS, def_before=30, def_after=30):
    """정기 일정 후보 → (받은 줄 dict 목록, 버린 [(항목, 이유)]). existing: engine.load_events 결과"""
    ok, bad = [], []
    seen = list(existing)
    for it in items if isinstance(items, list) else []:
        try:
            if not isinstance(it, dict):
                raise ValueError("항목 형식")
            t = _parse_time(it.get("time_kst"))
            if t <= now:
                raise ValueError("과거 시각")
            if t > now + dt.timedelta(days=HORIZON_DAYS, hours=12):
                raise ValueError("7일 밖")
            name = _clean_name(it.get("name"))
            imp = _imp(it.get("importance"))
            cs = _coins_ok(it.get("coins"), allowed)
            b4 = it.get("before_min"); af = it.get("after_min")
            b4 = None if b4 in (None, "") else int(float(b4))
            af = None if af in (None, "") else int(float(af))
            if (b4 is not None and not 0 <= b4 <= MAX_WINDOW_MIN) or (af is not None and not 0 <= af <= MAX_WINDOW_MIN):
                raise ValueError("창 길이 범위")
            if (def_before if b4 is None else b4) + (def_after if af is None else af) > MAX_WINDOW_MIN:
                raise ValueError("창 길이 초과")
            src = _url_ok(it.get("source"))
            if _dup(name, t, cs, seen):
                raise ValueError("중복")
            row = dict(time_kst=t.strftime(TFMT), name=name, importance=imp, coins=_coins_str(cs),
                       before_min="" if b4 is None else str(b4), after_min="" if af is None else str(af),
                       added_at=now.strftime("%Y-%m-%d %H:%M:%S"), source=src)
            ok.append(row)
            seen.append((t, name, imp, cs))
        except (ValueError, TypeError) as e:
            bad.append((it, str(e)))
    return ok, bad


def breaking_count_today(existing, now):
    """오늘(KST, 추가 시각 기준) 추가된 돌발 high 차단 수"""
    return sum(1 for e in existing if len(e) > 6 and e[6] is not None and e[6].date() == now.date()
               and (e[2] or "").lower() == "high" and str(e[1]).startswith(BREAKING_PREFIX))


def validate_breaking(items, now, existing, allowed=EVENT_COINS, per_day=BREAKING_PER_DAY):
    """돌발 후보 → (받은 줄, 버린 것). 창 = [시작, 시작 + 길이), 시작 = 다음 5분봉(또는 그 뒤 60분 안에서 모델이 준 시각)"""
    ok, bad = [], []
    seen = list(existing)
    nb = next_bar_start(now)
    used = breaking_count_today(existing, now)
    for it in items if isinstance(items, list) else []:
        try:
            if not isinstance(it, dict):
                raise ValueError("항목 형식")
            name = BREAKING_PREFIX + _clean_name(it.get("name")).removeprefix(BREAKING_PREFIX).strip()
            imp = _imp(it.get("importance"))
            cs = _coins_ok(it.get("coins"), allowed)
            src = _url_ok(it.get("source"))
            start = nb
            if it.get("start_kst") not in (None, ""):
                start = _parse_time(it.get("start_kst"))
                if start < nb:
                    raise ValueError("시작이 다음 봉 전")
                if start > nb + dt.timedelta(minutes=60):
                    raise ValueError("시작이 너무 늦음")
            if it.get("happened_at") not in (None, ""):
                ha = _parse_time(it.get("happened_at"))
                if ha < now - dt.timedelta(hours=BREAKING_LOOKBACK_H + 1) or ha > now + dt.timedelta(minutes=10):
                    raise ValueError("최근 2시간 밖의 뉴스")
            dur = float(it.get("duration_min"))
            if not dur > 0 or dur > MAX_WINDOW_MIN:
                raise ValueError("창 길이 초과")
            dur = int(min(max(dur, BREAK_MIN_LEN), BREAK_MAX_LEN))
            # 같은 사건이 최근 12시간 안에 이미 들어왔으면 중복
            n = _norm_name(name)
            if any(_norm_name(e[1]) == n and abs((e[0] - start).total_seconds()) < 12 * 3600 for e in seen):
                raise ValueError("중복")
            if imp == "high":
                if used >= per_day:
                    raise ValueError("하루 돌발 한도")
                used += 1
            row = dict(time_kst=start.strftime(TFMT), name=name, importance=imp, coins=_coins_str(cs),
                       before_min="0", after_min=str(dur), added_at=now.strftime("%Y-%m-%d %H:%M:%S"), source=src)
            ok.append(row)
            seen.append((start, name, imp, cs))
        except (ValueError, TypeError) as e:
            bad.append((it, str(e)))
    return ok, bad

# ---------------- events.csv 쓰기 ----------------

HEADER_COMMENT = [
    "# 경제 일정·이벤트 (KST). config.json eventBlock=true 일 때만 importance=high 일정의 [발표−before_min, 발표+after_min) 동안",
    "# 해당 코인(coins: ALL 또는 BTC|ETH|XRP|SOL) 신규 진입 금지. before/after 비면 config eventBeforeMin/eventAfterMin.",
    "# added_at(KST) 이 있으면 그 시각 이후 봉에만 적용(나중에 추가한 일정이 과거 장부를 바꾸지 않음). 없으면 늘 적용.",
    "# 봇이 Gemini(tools/gemini_events.py)로 자동 추가한다. 손으로 추가해도 됨: 예) 2026-12-10 22:30,미국 CPI,high,ALL,,,,https://...",
    "# 옛 형식(time_kst,name,importance)도 그대로 읽힌다. '#' 으로 시작하는 줄은 무시.",
]


def append_events(path, rows):
    """rows(dict, EVENT_COLS 키)를 덧붙인다. 파일이 옛 3열 형식이면 새 8열 머리글로 바꿔 다시 쓴다(기존 줄 보존)."""
    if not rows:
        return
    comments, data, header = [], [], None
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for ln in f.read().splitlines():
                if not ln.strip():
                    continue
                if ln.lstrip().startswith("#"):
                    comments.append(ln)
                elif header is None:
                    header = next(csv.reader([ln]))
                else:
                    data.append(ln)
    if header is not None and [h.strip() for h in header] == list(EVENT_COLS):
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            for r in rows:
                w.writerow([r.get(k, "") for k in EVENT_COLS])
        return
    old = [dict(zip([h.strip() for h in header], next(csv.reader([ln])))) for ln in data] if header else []
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        for c in (comments or HEADER_COMMENT):
            f.write(c + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(EVENT_COLS)
        for r in old + rows:
            w.writerow([r.get(k, "") or "" for k in EVENT_COLS])
    os.replace(tmp, path)

# ---------------- 프롬프트 ----------------

def schedule_prompt(now, coins):
    end = now + dt.timedelta(days=HORIZON_DAYS)
    return f"""지금은 {now:%Y-%m-%d %H:%M} 한국 시간(KST, UTC+9)이다.
Google 검색으로 확인해서, {now:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} (KST) 사이에 예정된 일정 중 {', '.join(coins)} 가격을 크게 움직일 수 있는 것만 찾아라.
대상 예: 미국 CPI·PCE·비농업 고용(NFP)·FOMC 금리 결정과 기자회견·연준 의장 발언, BTC·ETH·XRP·SOL 관련 ETF 승인/SEC 판결 기한,
대형 토큰 언락, 네트워크 업그레이드·하드포크, 업비트/빗썸의 해당 코인 유의 종목 지정·상장폐지 공지 일정.
규칙:
- 공식 발표처나 신뢰할 수 있는 언론으로 날짜·시각이 확인된 일정만. 추측·루머·시각 미정 일정은 넣지 마라.
- time_kst 는 한국 시간 'YYYY-MM-DD HH:MM' (미국 동부 시간이면 KST 로 바꿔라).
- importance: 시장 전체를 크게 흔드는 것(CPI·FOMC·고용·대형 판결 등)은 "high", 그보다 작은 것은 "medium".
- coins: 모든 코인에 영향이면 "ALL", 특정 코인만이면 "BTC|ETH" 처럼 {'/'.join(coins)} 중에서 '|' 로.
- before_min/after_min 은 특별한 이유가 없으면 null. 넣을 때는 0~120 정수.
- source: 근거 웹페이지 URL(필수, https://...).
- name: 짧은 한국어 이름(예: "미국 10월 CPI").
다른 글 없이 아래 JSON 하나만 답하라. 해당 일정이 없으면 {{"events": []}}.
{{"events": [{{"time_kst": "YYYY-MM-DD HH:MM", "name": "...", "importance": "high", "coins": "ALL", "before_min": null, "after_min": null, "source": "https://..."}}]}}"""


def breaking_prompt(now, coins):
    a = now - dt.timedelta(hours=BREAKING_LOOKBACK_H)
    return f"""지금은 {now:%Y-%m-%d %H:%M} 한국 시간(KST, UTC+9)이다.
Google 검색으로 {a:%Y-%m-%d %H:%M} ~ {now:%Y-%m-%d %H:%M} (KST, 최근 2시간) 사이에 처음 보도된 뉴스 중,
{', '.join(coins)} 가격에 즉각적인 큰 충격을 줄 수 있는 돌발 사건만 찾아라:
해킹·익스플로잇, 대형 거래소 장애·입출금 중단, 규제 당국의 집행·금지 조치, 대규모 강제 청산, 전쟁·테러 등 지정학 충격.
규칙:
- 2시간보다 오래된 뉴스, 일반 시황·가격 분석·전망 기사, 이미 알려진 예정 일정은 제외. 확실하지 않으면 빈 목록.
- importance: 시장이 크게 흔들릴 만하면 "high", 그보다 약하면 "medium".
- coins: "ALL" 또는 {'/'.join(coins)} 중에서 '|' 로 (예: "SOL").
- duration_min: 진입을 쉬어야 할 시간(60~180 정수).
- happened_at: 처음 보도된 시각(KST 'YYYY-MM-DD HH:MM').
- source: 근거 기사 URL(필수, https://...).
- name: 짧은 한국어 이름.
다른 글 없이 아래 JSON 하나만 답하라. 없으면 {{"shocks": []}}.
{{"shocks": [{{"name": "...", "coins": "ALL", "importance": "high", "duration_min": 120, "happened_at": "YYYY-MM-DD HH:MM", "source": "https://..."}}]}}"""

# ---------------- 상태 ----------------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, st):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    st["note"] = "Gemini 이벤트 수집 호출 기록(봇이 갱신). 키 값은 저장하지 않음."
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _due(st, mode, now, every_min, retry_min):
    m = st.get(mode) or {}
    last = m.get("last_attempt")
    if not last:
        return True
    try:
        t = parse_kst(last)
    except ValueError:
        return True
    gap = retry_min if m.get("last_ok") is False else every_min
    return now - t >= dt.timedelta(minutes=gap)


def _run_mode(mode, now, cfg, events_path, st, key, generate, log):
    from engine import load_events
    coins = [c for c in (cfg.get("eventCoins") or list(EVENT_COINS)) if c in EVENT_COINS] or list(EVENT_COINS)
    rec = st.setdefault(mode, {})
    rec["last_attempt"] = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        prompt = schedule_prompt(now, coins) if mode == "schedule" else breaking_prompt(now, coins)
        txt, model = generate(prompt, key=key, search=True, state=st, model=cfg.get("geminiModel"),
                              temperature=0.1, max_tokens=4096)
        from tools.gemini_client import extract_json
        obj = extract_json(txt)
        items = obj.get("events" if mode == "schedule" else "shocks")
        if not isinstance(items, list):
            raise ValueError("JSON 에 목록 없음")
        existing = load_events(events_path)
        if mode == "schedule":
            ok, bad = validate_schedule(items, now, existing, coins, float(cfg.get("eventBeforeMin", 30)),
                                        float(cfg.get("eventAfterMin", 30)))
        else:
            ok, bad = validate_breaking(items, now, existing, coins, int(cfg.get("eventBreakingMaxPerDay", BREAKING_PER_DAY)))
        append_events(events_path, ok)
        rec.update(last_ok=True, last_success=rec["last_attempt"], fails=0, model=model, last_error=None,
                   last_added=len(ok), last_rejected=[f"{(b[0] or {}).get('name', '?') if isinstance(b[0], dict) else '?'}: {b[1]}"
                                                      for b in bad][:10])
        if log:
            log(f"Gemini {'정기 일정' if mode == 'schedule' else '돌발'} 확인: 추가 {len(ok)}건, 거름 {len(bad)}건 ({model})")
        return ok
    except Exception as e:
        rec.update(last_ok=False, fails=int(rec.get("fails", 0)) + 1, last_error=str(e)[:300])
        if log:
            log(f"Gemini {'정기 일정' if mode == 'schedule' else '돌발'} 확인 실패(진입 판단은 기존 일정으로 계속): {str(e)[:200]}",
                warn=True)
        return []


def maybe_update(now, cfg, events_path, state_path, log=None, key=None, generate=None):
    """시간이 되었으면 정기 일정·돌발 확인을 하고 events.csv 에 추가. 반환: 새로 추가된 줄(dict) 목록.
    키 없음·설정 꺼짐·실패 → [] (예외를 밖으로 내보내지 않음)"""
    try:
        if cfg.get("eventAutoFetch", True) is False:
            return []
        from tools import gemini_client as gc
        key = key if key is not None else gc.api_key()
        if not key:
            return []
        generate = generate or gc.generate
        st = load_state(state_path)
        added = []
        sch = float(cfg.get("eventScheduleHours", SCHEDULE_HOURS)) * 60
        brk = float(cfg.get("eventBreakingMin", BREAKING_MIN))
        if _due(st, "schedule", now, sch, SCHEDULE_RETRY_MIN):
            added += _run_mode("schedule", now, cfg, events_path, st, key, generate, log)
        if _due(st, "breaking", now, brk, brk):
            added += _run_mode("breaking", now, cfg, events_path, st, key, generate, log)
        save_state(state_path, st)
        return added
    except Exception as e:
        if log:
            log(f"Gemini 이벤트 수집 오류(무시하고 계속): {str(e)[:200]}", warn=True)
        return []
