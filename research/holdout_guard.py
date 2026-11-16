# -*- coding: utf-8 -*-
"""
최종 시험(holdout) 데이터 접근 통제 — 사전 등록서 v3 2장 '최종 시험 데이터 통제'

  - 환경변수 FINAL=1 이 없으면 research/holdout/ 아래 파일을 읽지 못하게 막는다(HoldoutLocked 예외).
  - 읽을 때마다(허용·거부 모두) research/holdout_access.log 에 시각, 실험 번호, 파일, 결과를 한 줄 남긴다.
  - 읽기 전에 holdout_manifest.json 의 SHA-256 과 대조해 파일이 바뀌지 않았는지 확인한다.

사용 예 (최종 시험 때만):
    FINAL=1 python my_final_test.py      # 코드 안에서
    from holdout_guard import open_holdout
    with open_holdout("holdout/A/data_BTC_5m.csv", experiment="E1-final") as f: ...

모든 연구 스크립트의 데이터 읽기는 check_path() 를 거친다(holdout 경로면 FINAL 없이는 예외).
표준 라이브러리만 사용.
"""
import datetime as dt
import hashlib
import json
import os

KST = dt.timezone(dt.timedelta(hours=9))
RESEARCH = os.path.dirname(os.path.abspath(__file__))
HOLDOUT_DIR = os.path.join(RESEARCH, "holdout")
ACCESS_LOG = os.path.join(RESEARCH, "holdout_access.log")
MANIFEST = os.path.join(RESEARCH, "holdout_manifest.json")


class HoldoutLocked(PermissionError):
    pass


def _abs(path):
    p = path if os.path.isabs(path) else os.path.join(RESEARCH, path)
    return os.path.normcase(os.path.realpath(p))


def is_holdout(path):
    root = os.path.normcase(os.path.realpath(HOLDOUT_DIR))
    p = _abs(path)
    return p == root or p.startswith(root + os.sep)


def _log(experiment, path, result):
    line = (f"{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} KST\texperiment={experiment}\t"
            f"file={os.path.relpath(os.path.realpath(path if os.path.isabs(path) else os.path.join(RESEARCH, path)), os.path.realpath(RESEARCH))}"
            f"\t{result}\n")
    with open(ACCESS_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def check_path(path, experiment=None):
    """연구 스크립트의 모든 파일 읽기 전에 호출. holdout 경로면 FINAL=1 + 실험 번호 필수, 기록 남김."""
    if not is_holdout(path):
        return
    if os.environ.get("FINAL") != "1":
        _log(experiment or "-", path, "DENIED (FINAL=1 없음)")
        raise HoldoutLocked(f"최종 시험 데이터 읽기 금지: {path} (FINAL=1 필요, 사전 등록서 v3 2장)")
    if not experiment:
        _log("-", path, "DENIED (실험 번호 없음)")
        raise HoldoutLocked("최종 시험 데이터 읽기에는 실험 번호(experiment)가 필요합니다.")


def _expected_sha(path):
    if not os.path.exists(MANIFEST):
        return None
    with open(MANIFEST, encoding="utf-8") as f:
        man = json.load(f)
    rel = os.path.relpath(_abs(path), os.path.normcase(os.path.realpath(RESEARCH))).replace("\\", "/")
    for x in man.get("files", []):
        if os.path.normcase(x["file"]) == os.path.normcase(rel):
            return x["sha256"]
    return None


def open_holdout(path, experiment, mode="r", encoding="utf-8"):
    """FINAL=1 일 때만 열어 준다. 해시가 manifest 와 다르면 거부."""
    if "w" in mode or "a" in mode or "+" in mode:
        raise HoldoutLocked("최종 시험 데이터는 쓰기 금지")
    check_path(path, experiment)
    if not is_holdout(path):
        return open(_abs(path), mode, encoding=None if "b" in mode else encoding)
    exp = _expected_sha(path)
    with open(_abs(path), "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if exp is None or exp != got:
        _log(experiment, path, f"DENIED (해시 불일치 또는 manifest 없음: {got[:12]} vs {str(exp)[:12]})")
        raise HoldoutLocked(f"해시 불일치: {path}")
    _log(experiment, path, f"OPEN sha256={got[:16]}")
    return open(_abs(path), mode, encoding=None if "b" in mode else encoding)
