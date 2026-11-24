// ML 가상계좌(ml_paper_v1) 전용 화면 공통 코드.
// - account_id 가 ml_paper_v1 인 자료만 그린다. 다르거나 없으면 아무 계좌 자료도 대신 보여 주지 않는다.
// - 현재 포지션·대기 주문 카드, 거래 마커, 다른 계좌 그래프는 어느 페이지에도 없다.
// - 공개 화면은 원격 기록(ml_ledger/status.json)만 읽는다. 데모 자료는 로컬 파일로 열 때만 쓴다(공개 화면에서는 쓰지 않음).
// - 상태를 구분한다: 기록 없음(정식 시작 전, HTTP 404) / 통신 오류(연결 실패·다른 HTTP 오류·자료 손상) / 정상.
//   기록이 없으면 금액·수익률은 '—', 그래프·거래표는 설명이 있는 빈 상태로 둔다. 임의의 성과를 만들지 않는다.
const ML = (() => {
  const ACCOUNT = 'ml_paper_v1';
  const OWNER = 'minje0123', REPO = 'btc-paper-bot', PATH = 'ml_ledger/status.json';
  const PHASE_READY = '학습 준비 중 · ML 정식 가상매매 미시작';
  const LOCAL_DEMO = location.protocol === 'file:';
  const SRC = LOCAL_DEMO ? '../demo/ml_ledger/status.json'
    : `https://raw.githubusercontent.com/${OWNER}/${REPO}/main/${PATH}`;
  const DASH = '—';
  const $ = id => document.getElementById(id);
  const won = x => (x == null ? DASH : Math.round(x).toLocaleString('ko-KR') + '원');
  const pct = x => (x == null ? DASH : (x >= 0 ? '+' : '') + Number(x).toFixed(2) + '%');
  const cls = x => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const nowKST = () => new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false });
  const store = {                                    // 마지막 통신 성공 시각(이 브라우저에만, 실패해도 화면은 동작)
    get() { try { return localStorage.getItem('ml_last_net_ok'); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem('ml_last_net_ok', v); } catch (e) { /* 저장 불가 환경 */ } },
  };
  let lastTxt = null, lastNetOk = store.get();

  function phase(S) {
    const started = S && S.run_mode === 'forward';
    $('phase').textContent = started ? 'ML 정식 가상매매 기록 중' : PHASE_READY;
    $('phase').className = 'phase ' + (started ? 'on' : 'ready');
  }

  function meta(S) {
    $('m_int').textContent = S ? `${S.update_interval_min}분 (봇 실행 주기, 틱 단위 실시간 아님)` : DASH;
    $('m_ok').textContent = (S && S.last_success_at) || DASH;
    $('m_bar').textContent = (S && S.last_bar_time) || DASH;
    $('m_net').textContent = lastNetOk || DASH;
  }

  function banner(lines, kind) {
    $('banner').className = 'banner ' + (kind || '');
    $('banner').style.display = lines.length ? 'block' : 'none';
    $('banner').innerHTML = lines.map(esc).join('<br>');
  }

  function warnings(S) {
    const w = [];
    if (S.run_mode === 'demo') w.push('가상 자료 데모입니다. 실제 시세·실제 계좌 결과가 아닙니다.');
    if (S.run_mode === 'preview') w.push('정식 전진 가상매매 시작 전 미리보기입니다.');
    const stale = Object.entries(S.data_health || {}).filter(([, h]) => h.stale).map(([c]) => c);
    if (S.run_mode === 'forward' && stale.length) w.push(`자료 지연: ${stale.join(', ')} 마지막 봉이 30분 넘게 갱신되지 않음`);
    if (S.learning && String(S.learning.state || '').startsWith('실패')) w.push(`학습 실패: ${S.learning.state}`);
    if (!S.model || S.model.status !== '활성') w.push('유효 모델 없음 — 후보는 기록만 하고 진입 대기 중입니다.');
    return w;
  }

  function setConn(state, txt) {
    $('dot').className = 'dot ' + ({ ok: 'live', nodata: 'idle', error: 'bad', loading: '' }[state] || '');
    $('liveTxt').textContent = txt;
  }

  async function load(render) {
    $('m_chk').textContent = nowKST();
    let r;
    try {
      r = await fetch(SRC + (LOCAL_DEMO ? '' : `?t=${Date.now()}`), { cache: 'no-store' });
    } catch (e) {
      return onError(render, '통신 오류 — 기록 서버에 연결하지 못했습니다. 기록이 없는 것인지 확인하지 못한 상태입니다. 60초 뒤 다시 시도합니다.');
    }
    if (r.status === 404) {                         // 서버 응답은 정상, 기록 파일이 아직 없음 = 정식 시작 전
      lastNetOk = nowKST(); store.set(lastNetOk); lastTxt = null;
      setConn('nodata', 'ML 기록 없음');
      phase(null); meta(null);
      banner(['ML 기록 없음 — 아직 초기 학습과 정식 가상매매를 시작하지 않아 ML 계좌 기록이 만들어지지 않았습니다.',
        '금액·수익률·그래프·거래표는 기록이 생긴 뒤에 표시됩니다. 임의의 성과는 표시하지 않습니다.'], 'info');
      return render(null, 'nodata');
    }
    if (!r.ok) return onError(render, `통신 오류(HTTP ${r.status}) — 기록을 받지 못했습니다. 60초 뒤 다시 시도합니다.`);
    let txt, S;
    try { txt = await r.text(); S = JSON.parse(txt); } catch (e) {
      return onError(render, '통신 오류 — 받은 기록을 읽을 수 없습니다(손상·중간 끊김). 60초 뒤 다시 시도합니다.');
    }
    lastNetOk = nowKST(); store.set(lastNetOk);
    if (S.account_id !== ACCOUNT) {
      setConn('error', '계좌 ID 불일치'); phase(null); meta(null);
      banner([`계좌 ID 불일치(${S.account_id}) — ML 계좌 자료가 아니므로 표시하지 않습니다.`], 'err');
      return render(null, 'mismatch');
    }
    setConn('ok', '연결됨');
    phase(S); meta(S); banner(warnings(S), 'warn');
    if (txt !== lastTxt) { lastTxt = txt; render(S, 'ok'); }
  }

  function onError(render, msg) {
    setConn('error', '통신 오류');
    $('m_net').textContent = lastNetOk || DASH;
    const keep = lastTxt != null;                    // 앞서 받은 정상 기록이 있으면 그대로 두고 경고만
    banner([msg, `마지막 통신 성공: ${lastNetOk || '이 브라우저에서 아직 없음'}`].concat(keep ? ['아래 내용은 마지막으로 받은 기록입니다.'] : []), 'err');
    if (!keep) { phase(null); meta(null); render(null, 'error'); }
  }

  function start(render) { phase(null); meta(null); setConn('loading', '불러오는 중'); load(render); setInterval(() => load(render), 60e3); }
  return { start, $, won, pct, cls, esc, ACCOUNT, DASH, PHASE_READY };
})();
