# -*- coding: utf-8 -*-
"""E1 파라미터 — 현재 v1.4.1 신호 규칙(config.json) + 자금 관리 끔(사전 등록서 v3 'E1 구현 세부')"""
import json
import os

from common import REPO, engine

SIGNAL_KEYS = ("minStopP", "maxStopP", "bufP", "gradeMode", "useZone", "useBp", "useC", "allowLong", "allowShort",
               "swLen", "zoneTolP", "slopeThr", "chaseP", "t1MinR", "t1MaxR", "t1FbR", "volExitX", "useOppEng",
               "exitMode", "fee", "slip")


def base_signal_config():
    """운영 config.json 의 신호·비용 값만(자금 관리 값·rule_history 제외). 이식 전 사본과 같아야 함."""
    with open(os.path.join(REPO, "research", "checks", "baseline_head", "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    return {k: cfg[k] for k in SIGNAL_KEYS if k in cfg}


def e1_params(maxStopP=None, cost_mult=1.0):
    """자금 관리 끔: 1R 고정(R 은 수량과 무관), 하루 한도·목표·첫 승 절반·출금 없음, 코인당 포지션 1개(엔진 구조).
    비용: 수수료 편도 0.05% × cost_mult, 슬리피지 = 체결가 호가 단위 1틱 × cost_mult (BTC 는 1,000원).
    자본 1조 원·레버리지 상한 해제는 수량 내림(0.0001 단위)·상한 때문에 거래가 빠지지 않게 하려는 것(R 에는 영향 없음)."""
    cfg = base_signal_config()
    cfg.update(riskPct=1.0, redPct=1.0, maxRiskCapP=5.0, initial_capital=1e12, maxLev=1e9,
               dayLossP=1e12, maxConsec=10 ** 9, dayTargetP=0.0, firstWinHalf=False, withdrawPct=0.0,
               slipMode="tick", slipTicks=1.0 * cost_mult, fee=0.0005 * cost_mult)
    if maxStopP is not None:
        cfg["maxStopP"] = maxStopP
    return engine.load_params(cfg)
