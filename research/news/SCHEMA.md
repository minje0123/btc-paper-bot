# 뉴스 판정 v1 — 사실 추출 스키마 · 독립 항목 정의 · 결정표 (news_rules_v1.0)

코드: `tools/news_rules.py`(순수 규칙), `tools/news_v1.py`(수집·추출·저장·그림자 장부 연결). 테스트: `research/checks/c_news_v1_tests.py`.
이 문서와 코드가 다르면 **코드의 `RULES_VERSION`·`table_hash()`** 가 기준이다. 표나 후보값을 바꾸면 버전을 올린다.

## 0. 원칙 (2026-11-17 검토 결론)

| 원칙 | 구현 |
|---|---|
| 차단 시간은 **검증 전 후보값** | `CANDIDATE_MIN` = S 180 · A 120 · B 60 · 지표 ±30 · FOMC 전 60/후 90 (분). 모든 기록에 "후보값(미검증)" 표기 |
| Gemini 는 **사실 + 근거 문장만** | 추출 JSON 의 값마다 `*_quote`. 인용이 원문(제목+요약)에 그대로 없으면 그 값은 `null`(dropped_fields 에 기록) |
| 등급 제안 무시 | `suggested_tier`·`tier`·`severity`·`should_block` 등은 읽지 않고 `ignored_keys` 에만 남김 |
| Python 이 결정 | 출처 검증·재배포 묶기·확인 수준·대상 관계·결정표·창 시작/끝/연장/보류 |
| 재현성 | 기사(`articles.jsonl`)·Gemini 응답 원문(`extractions.jsonl`: 모델·프롬프트 버전·프롬프트 해시)을 저장. 재판정은 저장본으로(`judge_article`). 온도 0 이라도 재호출 결과가 같다고 가정하지 않음 |
| 텍스트로 알 수 없는 것 | 최초 보도 시각·교차 보도·공식 공지는 수집기(RSS 3곳 + 링크 도메인)로 판단. 공식 공지 피드 직접 수집은 v1 미구현(§6) |
| 독립 항목 + 조합 결정표 | 등급 더하기/빼기 없음. 표에 없는 조합 = `UNMAPPED`(기록만) |
| 소급 금지 | `effective_at = ceil5(decided_at)` ≥ decided_at, 창 줄의 `added_at = decided_at` → engine 이 그 전 체결 신호를 막지 않음 |
| 연장 | 같은 사건 재배포로 연장 안 함. `EXTENSION_FACTS` 의 새 사실이 있을 때만 새 창 |
| 진행 중 장애 | 고정 시간 끝으로 자동 해제 안 함. 30분마다 재확인 창 추가, 복구 확인 시 해제, 12시간 상한이면 '수동 확인 필요'로 멈춤 |
| 실패 구분 | collector(수집) / extractor(Gemini) 상태 별도. 추출 못 한 기사 = `NOT_EXTRACTED`/`EXTRACTOR_FAILED` 기록 — '이벤트 없음'·B 등급으로 바꾸지 않음 |
| 기존 정책 불변 | 본 장부·baseline·v4 는 data/events.csv(event_feeds) 그대로. news_v1 창은 `state/news_v1/windows.csv` 에만, 그림자 `news_v1_5m` 만 읽음 |

## 1. 시각 5종

| 이름 | 뜻 | 출처 |
|---|---|---|
| `event_at` | 사건이 실제로 일어난 시각 | 기사에 **명시된 시각만**(근거 문장 필수), 없으면 빈칸 |
| `published_at` | 기사 공개 시각 | RSS pubDate |
| `received_at` | 우리 수집기가 처음 본 시각 | 실행 시각 |
| `decided_at` | Python 이 판정한 시각 | 추출이 끝난 실행 시각(추출이 늦으면 판정도 늦음 — 소급 안 함) |
| `effective_at` | 차단 시작 | `ceil5(decided_at)` |

## 2. 독립 항목 정의

| 항목 | 값 | 판정 방법(Python) |
|---|---|---|
| 사건 종류 `event_type` | new_breach 신규 침해 · stolen_fund_move 탈취 자금 이동 · exchange_inflow 거래소 유입 · withdrawal_restriction 출금 제한 · chain_outage 체인 장애 · regulatory_decision 규제 결정 · delisting_warning 상장폐지·유의 · mass_liquidation 대규모 청산 · macro_schedule 거시 일정 · other | Gemini 값 + 근거. `stolen_fund_move` 인데 근거 있는 `destination=exchange` 이면 `exchange_inflow` 로 본다(effective_type) |
| 신규 정보 `novelty` | NEW / REPEAT / UNKNOWN | `is_new_information` true/false/null. **이번에 새로 확인된 사실**인지(예: 2주 전 해킹의 새 자금 이동 = NEW, 해킹 회고 기사 = REPEAT) |
| 대상 관계 `relation`(코인별) | NATIVE(그 코인 체인·재단·발행사가 주체, 또는 그 코인이 제한·폐지 대상) · MOVED_ASSET(그 코인이 탈취·이동된 자산) · VENUE_OURS(우리 체결 거래소 업비트·빗썸 전체 사건) · VENUE_MAJOR(해외 대형 거래소 전체 사건) · MARKET(근거 있는 시장 전체) · MENTION · NONE · UNKNOWN | **제목에 코인 이름이 있는지는 쓰지 않음.** 근거 문장 있는 `assets[].role`·`venue_name`·`market_wide` 로만. 사건 종류를 모르면 UNKNOWN |
| 규모 `scale` | 종류(damage_usd 피해액 / moved_usd 이동액 / exchange_inflow_usd 유입액) + 금액 + 등급 LARGE ≥ 1억$ · MID ≥ 1천만$ · SMALL · UNKNOWN | 사건 종류에 맞는 금액 하나만. 서로 섞지 않고, **같은 자금의 반복 이동을 합산하지 않음**(묶음 금액 = 보도된 최대값) |
| 확인 상태 `confirmation` | OFFICIAL 공식 확인 · INDEPENDENT 독립 취재 2곳 이상 · SINGLE_CLAIM 단일 주장 · UNVERIFIABLE 확인 불가(익명 출처만) | §3 |
| 지속 상태 `persistence` | ONGOING 진행 중 · RECOVERED 복구 확인(recovered/ended) · UNCLEAR 종료 불명 | `status` + 근거 |
| 관측 반응 `observed_reaction` | NORMAL / ELEVATED(×2) / EXTREME(×4) / UNKNOWN + 배수 | 결정 시점까지 **닫힌** 5분봉: 최근 3봉 (고가−저가)/종가 최대 ÷ 지난 7일 같은 시간대(±1h) 중앙값. **기록만, v1 결정표에 안 씀**(가격 반응은 위험 관측치이지 뉴스가 원인이라는 증거가 아님) |
| 보조 | venue_class(OURS/MAJOR/OTHER/NONE), destination(own_wallets/exchange/mixer/bridge_or_swap/unknown), scheduled_in_advance | 근거 있는 값만 |

## 3. 독립 출처 · 공식 공지

- **같은 원문 재배포 = 한 묶음** (하나라도 맞으면 union): 제목 단어 Jaccard ≥ 0.6 / 한 기사가 다른 기사 매체를 원출처로 표기(`credited_outlet`) / 같은 통신사·"first reported by" 표기 / **공식이 아닌 같은 1차 출처(온체인 분석사 등)를 인용**.
  묶음 수 = 독립 출처 수. 같은 원문을 받아 쓴 매체 2곳 = 1.
- **공식 공지**: 기사 링크 도메인이 `OFFICIAL_DOMAINS`(upbit.com, bithumb.com, binance.com, coinbase.com, kraken.com, okx.com, bybit.com, ethereum.org, solana.com, status.solana.com, ripple.com, xrpl.org, sec.gov, cftc.gov, justice.gov, fbi.gov, fsc.go.kr, fss.or.kr, federalreserve.gov) 이거나, `sources_cited` 에 type=official 이고 이름이 `OFFICIAL_ENTITIES` 인 출처가 근거 문장과 함께 있으면(보도로 전달된 공식 확인).
- 확인 수준: 공식이 하나라도 있으면 **OFFICIAL — 교차 보도가 없어도 낮추지 않음**. 아니면 독립 묶음 ≥2 INDEPENDENT, 1 SINGLE_CLAIM. 모든 기사가 익명만 인용하면 UNVERIFIABLE.
- 확인 수준은 v1 에서 **사건 묶음 단위**(계열 + 주체, 14일). 사실 하나하나 단위가 아님(한계).

## 4. Gemini 추출 JSON 스키마 (`news_extract_v1`, 프롬프트 `news_prompt_v1`)

```
event_type, event_type_quote
is_new_information (true/false/null), new_information_quote
event_time_utc ("YYYY-MM-DDTHH:MMZ", 명시된 경우만), event_time_quote
entity_name, entity_kind (exchange|protocol|chain|issuer|regulator|person|other), entity_quote
venue_name, venue_quote
assets: [{symbol, role (native_subject|stolen_or_moved|restricted|delisted|mentioned), quote}]
damage_usd, damage_quote · moved_usd, moved_quote · exchange_inflow_usd, inflow_quote
same_funds_as_earlier, same_funds_quote
destination (own_wallets|exchange|mixer|bridge_or_swap|unknown), destination_quote
sources_cited: [{name, type (official|onchain_analyst|media|anonymous|other), quote}]
status (ongoing|recovered|ended|unclear), status_quote
scheduled_in_advance, scheduled_quote · market_wide, market_wide_quote
credited_outlet, credited_quote
```
- 모르면 null. 인용은 원문 그대로의 부분 문자열(대소문자·공백·따옴표만 정규화, 4자 이상). 허용값 밖·형식 오류 → null(errors).
- 저장: 요청 시각, 모델, 프롬프트 버전, 프롬프트 SHA-256 앞 16자, 응답 원문(6,000자까지), 성공 여부·오류.
- 호출: temperature 0, JSON 응답, 요청 1건 ≤ 15초, 실행당 ≤ 3건, 남은 시간 < 5초면 안 부름. 429·할당량 → `state/reviews.json backoff_until` 24시간(복기 해설과 공유).

## 5. 결정표 (위에서부터 처음 맞는 줄 · 코인별로 적용 후 BLOCK 코인을 모아 가장 긴 키 하나로 창 생성)

`*` 는 '알려진 값 아무거나'. **UNKNOWN 은 명시된 줄에서만 맞는다** → 그 밖은 `UNMAPPED`(기록만).
OK = OFFICIAL·INDEPENDENT.

| 줄 | 사건 종류 | 신규 | 대상 관계 | 확인 | 규모 | 기타 | 결정 | 시간 키 | 해제·재확인 |
|---|---|---|---|---|---|---|---|---|---|
| D00 | other | | | | | | 기록 | | |
| D01 | macro_schedule | | | | | | 기록(캘린더가 처리) | | |
| D02 | mass_liquidation | | | | | | 기록(반응이지 원인 아님) | | |
| D03 | * | REPEAT | | | | | 기록(차단·연장 없음) | | |
| D04 | * | NEW | MENTION·NONE | | | | 기록 | | |
| D10 | new_breach | NEW | NATIVE | OK | LARGE | | **차단** 그 코인 | S | 진행 중이면 복구 확인까지 보류, 30분 |
| D11 | new_breach | NEW | NATIVE | OK | MID·SMALL·UNKNOWN | | **차단** | A | 보류, 30분 |
| D12 | new_breach | NEW | NATIVE | SINGLE | LARGE | | **차단** | B | 30분 재확인 |
| D13 | new_breach | NEW | NATIVE | SINGLE·UNVERIF | MID·SMALL·UNKNOWN | | 기록 | | 30분 재확인 |
| D13b | new_breach | NEW | NATIVE | UNVERIF | LARGE | | 기록 | | 30분 |
| D14 | new_breach | NEW | VENUE_OURS | OK·SINGLE | | | **차단** 전 코인 | S | 보류, 30분 |
| D15 | new_breach | NEW | MOVED_ASSET | OK | LARGE | | **차단** 그 코인 | B | 고정 |
| D16 | new_breach | NEW | MOVED_ASSET·VENUE_MAJOR·MARKET | | | | 기록 | | |
| D20 | stolen_fund_move | NEW | | | | 목적지 own_wallets·mixer·bridge·unknown·모름 | 기록(지갑 간 이동은 매도 증거 아님) | | 30분 재확인(유입 확인 대기) |
| D30 | exchange_inflow* | NEW | MOVED_ASSET·NATIVE | OK | LARGE | | **차단** 그 코인 | B | 고정 |
| D31 | exchange_inflow* | NEW | | | | | 기록 | | 30분 |
| D40 | withdrawal_restriction | NEW | | | | 사전 공지 점검 | 기록 | | |
| D41 | withdrawal_restriction | NEW | NATIVE·VENUE_OURS | OK·SINGLE | | 장소=우리 거래소, ONGOING·UNCLEAR | **차단** | A | 보류, 30분 |
| D42 | withdrawal_restriction | NEW | VENUE_MAJOR | OK | | ONGOING·UNCLEAR | **차단** 전 코인 | A | 보류, 30분 |
| D43 | withdrawal_restriction | NEW | | | | | 기록 | | |
| D50 | chain_outage | NEW | NATIVE | OK·SINGLE | | ONGOING·UNCLEAR | **차단** 그 코인 | S | 보류, 30분 |
| D51 | chain_outage | NEW | NATIVE | OK·SINGLE | | RECOVERED | **차단**(복구 후 꼬리) | B | 고정 |
| D52 | chain_outage | NEW | | | | | 기록 | | 30분 |
| D60 | regulatory_decision | NEW | NATIVE | OK | | | **차단** | A | 고정 |
| D61 | regulatory_decision | NEW | MARKET | OK | | | **차단** 전 코인 | B | 고정 |
| D62 | regulatory_decision | NEW | | | | | 기록 | | |
| D70 | delisting_warning | NEW | NATIVE | OK·SINGLE | | 장소=우리 거래소 | **차단** | S | 고정 |
| D71 | delisting_warning | NEW | NATIVE | OK | | 장소=해외 대형 | **차단** | B | 고정 |
| D72 | delisting_warning | NEW | | | | | 기록 | | |
| — | 그 밖 | | | | | | **UNMAPPED** | | |

\* exchange_inflow = 근거 있는 목적지가 거래소인 stolen_fund_move 포함.

**거시 일정**(뉴스가 아니라 캘린더): data/events.csv 의 `[일정]` high 줄을 읽기만 해서 FOMC 결정·성명·기자회견 = 전 60/후 90, 그 밖(의사록 포함) = ±30. news_v1 활성 시각 이전에 추가된 줄은 added_at 을 활성 시각으로 올림(소급 없음).

**연장(EXTENSION_FACTS)**: 활성 창이 있는 사건 묶음에서 다음 중 하나가 새로 확인될 때만 새 창(시작 = 새 effective_at, 길이 = 새 결정의 후보값, 합산 아님) — 확인 수준 상승 · 규모 등급 상승 · 금액 1.5배 이상 · 새 대상 코인 · 거래소 유입으로 전환 · 복구 확인(D51 꼬리). 재배포·같은 사실 = `no_extend`.

**보류(진행 중)**: hold 줄(D10·D11·D14·D41·D42·D50)이고 지속 상태가 RECOVERED 가 아니면, 창 끝 30분 전부터 매 실행 다음 30분 창을 미리 붙인다(added_at = 붙인 시각 ≤ 시작). 복구 확인 기사 → `HOLD_RELEASE`. 12시간 넘게 복구 소식 없음 → `HOLD_CAP`(수동 확인 필요, 자동 해제가 아니라 '모름' 표시).

## 6. 알려진 한계 (v1)

- 입력은 RSS 제목+요약뿐(본문 미수집) → 근거 문장도 그 안에서만. 본문에만 있는 사실은 null.
- 공식 공지 피드(업비트 공지 등) 직접 수집 미구현 → 공식 확인은 링크 도메인·보도된 공식 인용으로만.
- 사건 묶음 키는 '계열 + 주체 이름' 정규화(예: `security:binance`) — 다른 사건이 같은 주체면 14일 안에서 합쳐질 수 있음.
- Gemini 할당량이 막히면(429) 추출이 안 되고 6시간 뒤 NOT_EXTRACTED — 그동안 news_v1 장부는 그 기사로 막지 않는다(의도: 모름을 차단 등급으로 바꾸지 않음).
- 후보 시간값·임계값(1억$·1천만$·Jaccard 0.6·재확인 30분·상한 12시간)은 모두 미검증 가정.
