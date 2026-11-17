# -*- coding: utf-8 -*-
"""
Gemini API(generativelanguage v1beta) 최소 클라이언트 — 표준 라이브러리만.

- 키는 환경변수 GEMINI_API_KEY 에서만 읽고, 요청 헤더(x-goog-api-key)로만 보낸다. 로그·파일에 키를 쓰지 않는다.
- 모델: 먼저 state["model"](지난번에 된 모델) 또는 기본 gemini-2.5-flash. 실패하면 사용 가능 모델 목록에서
  flash 계열(lite·image·tts·live 제외)로 대체 (.github/workflows/gemini-check.yml 과 같은 고르는 법).
- 테스트에서는 모듈 변수 TRANSPORT 를 가짜 함수로 바꿔 네트워크 없이 시험한다.
"""
import json
import os
import re
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
TIMEOUT = 60


def api_key():
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


class GeminiError(Exception):
    pass


def _http(method, url, key, body=None, timeout=TIMEOUT):
    """(상태코드, dict) — 실패 시 GeminiError(키는 메시지에 넣지 않음)"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {})
            txt = f"{msg.get('status')} {str(msg.get('message'))[:160]}"
        except Exception:
            txt = ""
        raise GeminiError(f"HTTP {e.code} {txt}".strip())
    except Exception as e:  # URLError, timeout, JSON 오류
        raise GeminiError(f"{type(e).__name__}: {str(e)[:160]}")


# 테스트에서 바꿔 끼우는 전송 함수: TRANSPORT(method, url, key, body) -> (status, dict)
TRANSPORT = _http


def flash_models(key):
    """사용 가능 모델 중 flash 계열(generateContent 지원, lite/image/tts/live 제외) — 새 버전이 뒤쪽"""
    _st, d = TRANSPORT("GET", f"{API}/models?pageSize=200", key, None)
    ms = [m["name"].split("/")[-1] for m in d.get("models", [])
          if "generateContent" in m.get("supportedGenerationMethods", [])]
    return [m for m in ms if "flash" in m and not any(x in m for x in ("lite", "image", "tts", "live"))]


def _text_of(d):
    try:
        parts = d["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise GeminiError(f"응답에 본문 없음 ({str(d.get('promptFeedback') or d)[:120]})")
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def generate(prompt, key=None, search=False, state=None, model=None, temperature=0.2, max_tokens=2048):
    """프롬프트 → (본문 텍스트, 쓴 모델). search=True 면 google_search 그라운딩 도구 사용.
    state(dict) 가 있으면 성공한 모델을 state["model"] 에 기록. 모두 실패하면 GeminiError."""
    key = key or api_key()
    if not key:
        raise GeminiError("GEMINI_API_KEY 없음")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if search:
        body["tools"] = [{"google_search": {}}]
    # 순서: 지난번에 된 모델(state) → 설정 모델 → 기본 gemini-2.5-flash → (모두 실패 시) 목록의 flash 계열
    order = list(dict.fromkeys(m for m in ((state or {}).get("model"), model, DEFAULT_MODEL) if m))
    tried, errs = [], []
    fallback_loaded = False
    while order:
        m = order.pop(0)
        if m in tried:
            continue
        tried.append(m)
        try:
            _st, d = TRANSPORT("POST", f"{API}/models/{m}:generateContent", key, body)
            txt = _text_of(d)
            if state is not None:
                state["model"] = m
            return txt, m
        except Exception as e:          # GeminiError 외 예상 밖 오류도 다음 모델로
            errs.append(f"{m}: {str(e)[:160]}")
        if not order and not fallback_loaded:
            fallback_loaded = True
            try:
                cands = flash_models(key)
                order = [c for c in reversed(cands) if c not in tried][:2]
                # 11/17: 새 사용자에겐 2.5-flash 가 막히고 최신 flash 는 무료 할당량이 0일 수 있음 → 별칭·lite 도 시도
                _st, d = TRANSPORT("GET", f"{API}/models?pageSize=200", key, None)
                allm = [m["name"].split("/")[-1] for m in d.get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])]
                extra = [m for m in ("gemini-flash-latest", "gemini-flash-lite-latest") if m in allm]
                extra += [m for m in reversed(allm) if "flash-lite" in m and not any(x in m for x in ("image", "tts", "live"))][:2]
                order += [m for m in dict.fromkeys(extra) if m not in tried and m not in order]
            except Exception as e:
                errs.append(f"모델 목록: {str(e)[:160]}")
    raise GeminiError(" / ".join(errs)[:900])


def extract_json(text):
    """모델 답에서 JSON 객체 하나를 엄격하게 꺼낸다(```json 블록 우선, 없으면 첫 '{' ~ 마지막 '}'). 실패 시 ValueError"""
    if not isinstance(text, str):
        raise ValueError("본문이 문자열이 아님")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    s = m.group(1) if m else None
    if s is None:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("JSON 객체 없음")
        s = text[a:b + 1]
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("JSON 최상위가 객체가 아님")
    return obj
