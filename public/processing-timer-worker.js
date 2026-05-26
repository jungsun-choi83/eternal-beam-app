/** AI 처리 화면 — 메인 스레드(WASM) 막혀도 초·문구 인덱스 갱신 */
let seconds = 0;
setInterval(() => {
  seconds += 1;
  self.postMessage({ seconds, tick: seconds });
}, 1000);
