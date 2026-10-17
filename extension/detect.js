(() => {
  if (window !== window.top || window.__gromkost_detect__) return;
  window.__gromkost_detect__ = true;

  function can(el) {
    const tag = el && el.tagName;
    if (tag !== "VIDEO" && tag !== "AUDIO") return false;
    if (el.muted || !el.volume) return false;
    if (!el.paused && !el.ended) return true;
    return el.clientWidth >= 80 && el.clientHeight >= 40;
  }

  function showBar() {
    if (document.getElementById("gromkost-ask")) return;
    const host = document.createElement("div");
    host.id = "gromkost-ask";
    host.style.cssText = "all:initial;position:fixed;z-index:2147483647;inset:0;";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        .shade { position: fixed; inset: 0; background: rgba(0, 0, 0, .35); display: flex; align-items: center; justify-content: center; }
        .box { width: 360px; max-width: calc(100vw - 48px); background: #fff; color: #1a1a1a; border: 1px solid #d0d0d0; border-radius: 8px; box-shadow: 0 12px 40px rgba(0, 0, 0, .35); font: 14px/1.4 system-ui, sans-serif; padding: 18px 18px 14px; }
        h2 { margin: 0 0 8px; font-size: 15px; font-weight: 600; }
        p { margin: 0 0 16px; }
        .actions { display: flex; justify-content: flex-end; gap: 8px; }
        button { font: inherit; min-width: 72px; padding: 6px 14px; border-radius: 4px; border: 1px solid #c8c8c8; background: #f3f3f3; color: #1a1a1a; cursor: pointer; }
        button.yes { background: #0067c0; border-color: #0067c0; color: #fff; }
      </style>
      <div class="shade">
        <div class="box" role="alertdialog" aria-modal="true">
          <h2>Громкость</h2>
          <p>Эта страница может играть звук. Управлять вкладками с этим адресом?</p>
          <div class="actions">
            <button id="no">Нет</button>
            <button class="yes" id="yes">Да</button>
          </div>
        </div>
      </div>`;
    const parent = document.documentElement || document.body;
    if (!parent) return;
    parent.appendChild(host);
    const answer = (yes) => {
      chrome.runtime.sendMessage({ type: "gromkost:choice", yes }).catch(() => {});
      host.remove();
    };
    root.getElementById("yes").onclick = () => answer(true);
    root.getElementById("no").onclick = () => answer(false);
    root.getElementById("yes").focus();
    root.addEventListener("keydown", (e) => {
      if (e.key === "Enter") answer(true);
      if (e.key === "Escape") answer(false);
    });
  }

  let reported = false;
  function report() {
    if (reported) return;
    reported = true;
    chrome.runtime.sendMessage({ type: "gromkost:capable" }, (res) => {
      if (chrome.runtime.lastError) return;
      if (res && res.ask) showBar();
    });
  }

  document.addEventListener("play", (e) => { if (can(e.target)) report(); }, true);
  const scan = () => {
    for (const el of document.querySelectorAll("video, audio")) {
      if (can(el)) { report(); return; }
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "gromkost:ask") showBar();
  });
})();
