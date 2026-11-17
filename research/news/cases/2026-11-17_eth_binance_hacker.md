# 사례 2026-11-17 — ETH 18:35~20:35 high 차단 (CoinDesk "Binance hacker moves $63 million in stolen ether…")

작성 2026-11-17 19시대(KST). 5분봉 자료는 **18:50 봉까지**(차단 창 20:35 끝 전) — 창 후반은 아직 관측 안 됨.
재판정 고정 입력: `2026-11-17_eth_binance_hacker.fixture.json` (테스트 N10).

## 1. 판정 당시 기록 복원 (기존 event_feeds)

| 항목 | 값 | 출처 |
|---|---|---|
| 실행 | 2026-11-17 18:30:20 시작. "코인 뉴스 RSS 3/3곳: 최근 2시간 4건, 규칙 해당 1건, 추가 1건" | state/bot.log 425행 |
| 수집 상태 | coindesk 25건 · cointelegraph 30건 · theblock 20건 받음, 모두 OK | state/event_feed.json |
| 추가된 줄 | `2026-11-17 18:35,[돌발] 해킹/익스플로잇 (ETH): Binance hacker moves $63 million in stolen ether a,high,ETH,0,120,2026-11-17 18:30:20,<CoinDesk URL>` | data/events.csv 11행 |
| 키워드 점수(재계산) | 제목의 `hacker` → 해킹 분류 3점 + 대형 거래소 `binance` 1점 = 4 → high. `$63 million` 은 1억$ 기준(`\d{3,} million`) 미달이라 보너스 없음. 코인 = 제목의 `ether` → ETH. 창 = 해킹 분류 고정 120분 | tools/event_feeds.py classify |
| 알림 | 18:30:29 "⛔ 이벤트 회피 … ETH 11-17 18:35~20:35" | bot.log 439행 |
| 막힌 신호 | 없음(ledger/blocked_signals.csv 미생성 = 막힌 신호 0, 18:50 봉까지) | ledger/ |
| 판정 당시 RSS 요약문 | **저장 안 됨**(기존 수집기는 제목·링크만 events.csv 에 남김). 19시대 RSS 를 다시 받아 같은 항목 확인: 요약 "North Korea's TraderTraitor group transferred 34,279 ETH to three new wallets. The funds have not moved again, blockchain investigators say." | 재수집 |

5종 시각: event_at **모름**(기사는 "on Tuesday"만, 시각 없음) · published_at 18:21:19 KST(RSS, 기사 표기 4:21 a.m. EST) ·
received_at 18:30:20 · decided_at 18:30:20 · effective_at 18:35(종료 20:35).

## 2. 기사 원문에서 뽑은 사실 (CoinDesk 본문, WebFetch)

| 항목 | 내용 | 근거 문장(발췌) |
|---|---|---|
| 사건 | 10/29 Binance 탈취(6.3B$, FBI 가 북한 TraderTraitor 소행으로 지목) 관련 주소가 2주 넘게 멈췄다가 ETH 를 옮김 | "An address linked to the Oct. 29 attack transferred 34,279.66 ETH" / "had been inactive since Oct. 30" |
| 시각 | 11/17(화) — 시각 없음 | "on Tuesday" |
| 금액 | 34,279.66 ETH ≈ 6,300만$ (**이동액**. 6.3B$ 는 원래 피해액 — 합산 금지) | "nearly $63 million in ether" |
| 목적지 | **새로 만든 주소 3곳**(12,000 / 12,000 / 10,279.66 ETH). 거래소·세탁 서비스와 연결 안 됨, 이후 추가 이동 없음 | "transferred 34,279.66 ETH to three newly created addresses" / "have not been linked to an exchange or another laundering service" / "have not moved again" |
| 근거 자료 | 온체인 보안업체 MistTrack·Solid Intel 의 X 게시물 | "according to reports via X by blockchain security firms MistTrack and Solid Intel" |
| 공식 확인 | **없음** — Binance 는 CoinDesk 문의에 답하지 않음. FBI 귀속·DOJ 압류는 원래 해킹에 대한 것이지 이번 이동에 대한 확인 아님 | "Binance did not immediately respond to a CoinDesk request" |
| 가격 언급 | 없음 | — |
| 원출처 표기 | 다른 매체 표기 없음(1차 출처 = 위 두 업체) | — |

## 3. 같은 사건 다른 매체

| 매체 | 성격 | 독립 출처로 셀까? |
|---|---|---|
| cryptonews.net, coinspectator.com, news.symplexia.com | 제목이 CoinDesk 와 **똑같음**("…after 2 weeks of silence") — 재배포(집계 사이트) | 아니오(제목 Jaccard ≥ 0.6 → 같은 묶음) |
| CoinCentral "Binance Hacker Splits 34,279.66 ETH Into Three Wallets" | 제목은 다르지만 같은 온체인 업체 보고(MistTrack 등)를 근거로 한 것으로 보임 | 아니오(같은 1차 출처 인용 → 같은 묶음). 단 본문은 확인 안 함 → **유보** |
| 우리 RSS 3곳(cointelegraph·theblock) | 18:30 실행 시점엔 이 이동 기사 없음(cointelegraph 는 다른 Binance 해킹 관련 기사만) | — |

→ 독립 출처 1곳(MistTrack·Solid Intel 주장, 매체 다수가 받아 씀) = **SINGLE_CLAIM**. 공식 공지 없음.

## 4. 그 시간대 ETH 5분봉 (업비트 KRW, data/candles_ETH_5m.csv)

결정(18:30:20) 시점까지 **확정**된 자료와 그 **이후** 자료를 나눔. 기준선 = 결정 전 7일(실제 자료는 11-14 09:10부터) 같은 시간대 ±1시간 봉 75개.

| 구간 | 봉 수 | (고가−저가)/종가 평균 · 최대 | 거래량 평균(ETH) | 가격 |
|---|---|---|---|---|
| 기준선(같은 시간대) | 75 | 중앙값 0.244% | 중앙값 64.6 | — |
| 16:30~17:20 (확정) | 11 | 0.172% · 0.241% | 19.4 | 2,492,000 → 2,490,000 |
| 17:25~18:25 (확정, 기사 18:21 공개 **이전**이 대부분) | 13 | 0.261% · 0.445% | 94.4 | 2,490,000 → 2,465,000 (−1.00%) |
| 18:30~18:50 (결정 **이후**, 창 진행 중) | 5 | 0.211% · 0.283% | 39.7 | 2,465,000 → 2,470,000 |

- 결정 시점 관측 반응(news_v1 정의): **NORMAL range×1.50 vol×1.50**.
- 같은 17:25~18:25 다른 코인: BTC −0.19%(거래량 ×2.73), XRP −0.61%(×3.27), SOL −0.30%(×1.59), ETH −1.00%(×5.09) — 시장 전체 약세 속 ETH 가 더 약함.
- 하락·거래량 증가는 CoinDesk 공개(18:21)보다 **먼저** 시작. X 게시물 시각을 몰라 이동 소식이 원인인지 판단 불가. 가격 반응은 위험 관측치일 뿐 원인 증거 아님.
- 결정 이후 5봉은 기준선보다 조용함(변동폭·거래량 모두 중앙값 아래). 창 끝(20:35)까지 자료가 없어 결론 보류.

## 5. 새 결정표(news_rules_v1.0)로 재판정

| 항목 | 값 |
|---|---|
| 사건 종류 | stolen_fund_move (탈취 자금 이동) |
| 신규 정보 | NEW (이번 이동은 새 사실, 해킹 자체는 10/29 — 같은 자금이므로 피해액 합산 안 함) |
| 대상 관계 | ETH = MOVED_ASSET (이더리움 체인·재단 피해 아님), BTC·XRP·SOL = NONE. 장소 Binance = 해외 대형(우리 체결 거래소 아님), 특정 자산 사건이라 VENUE_MAJOR 아님 |
| 규모 | moved_usd 6,300만$ = MID |
| 목적지 | own_wallets 또는 unknown(새 주소 3곳, 거래소·세탁 서비스 연결 없음) |
| 확인 | SINGLE_CLAIM (독립 출처 1) |
| 지속 | UNCLEAR(해당 없음) |
| **결정** | **D20 → 기록만(차단 없음)**, 30분 재확인. BTC·XRP·SOL 은 D04 기록 |
| 다시 판단할 새 사실 | 이 자금의 **거래소 유입**이 근거와 함께 보도되면 exchange_inflow → 1억$ 이상 + 공식/독립 확인이면 D30(ETH, B 60분 후보), 아니면 D31 기록 |
| 기존 판정과 차이 | 기존 high ETH 18:35~20:35 vs 새 판정 차단 없음 → **'기존만 차단'** |

테스트 N10: 저장된 기사·추출 예시로 두 번(다른 폴더) 돌려 decisions.csv 동일, 목적지를 unknown 으로 바꿔도 RECORD, `suggested_tier: "A"` 는 무시됨.
(추출 예시는 실제 Gemini 응답이 아님 — 그 시각 Gemini 는 429 였음.)

## 6. 유보(정보 부족)

1. **온체인 이동 시각**(event_at): X 게시물 시각 미확인 → 이동과 17:25~18:25 ETH 약세의 선후 판단 불가.
2. **새 주소 3곳의 소유자**: 공격자 소유인지 단정 못 함(검색 요약 기준, 기사는 이 이동만으로 매도 준비인지 자기 주소 간 이동인지 알 수 없다고 씀). 거래소 유입 여부는 이후 보도·온체인 확인 필요.
3. **CoinCentral 등 다른 매체가 독립 취재인지**: 본문 미확인. 지금은 같은 1차 출처로 봄.
4. **차단의 효과**: 창 안 ETH 신호가 아직 없고 창이 끝나지 않음 → 이번 차단이 손익에 준 영향 = 0(지금까지). 일반화 불가(사례 1건).
5. RSS 요약문은 판정 당시 저장본이 아니라 다시 받은 것 — 그 사이 수정됐을 가능성은 낮지만 0 은 아님. news_v1 은 이제 기사를 저장함(켜졌을 때).
