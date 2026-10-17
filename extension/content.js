(() => {
  // После обновления расширения старая копия скрипта остаётся во вкладке, но без связи: chrome.runtime.id пропадает.
  if (window.__gromkost_rt__?.id) return;
  window.__gromkost_rt__ = chrome.runtime;

  function apply(band) {
    if (band) {
      window.dispatchEvent(new CustomEvent("__gromkost_band__", { detail: band }));
    }
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "gromkost:band") apply(msg.band);
    if (msg && msg.type === "gromkost:media") {
      window.dispatchEvent(new CustomEvent("__gromkost_media__", { detail: msg.action }));
    }
    if (msg && msg.type === "gromkost:release") {
      window.dispatchEvent(new CustomEvent("__gromkost_band__", { detail: { low: -60, high: 0, off: true, boost: 100 } }));
    }
  });

  window.addEventListener("__gromkost_live__", (e) => {
    chrome.runtime.sendMessage({ type: "gromkost:live", playing: !!e.detail }).catch(() => {});
  });

  window.addEventListener("__gromkost_failed__", (e) => {
    chrome.runtime.sendMessage({ type: "gromkost:failed", reason: String(e.detail || "") }).catch(() => {});
  });

  window.addEventListener("__gromkost_needplay__", () => {
    chrome.runtime.sendMessage({ type: "gromkost:needplay" }).catch(() => {});
  });

  chrome.runtime.sendMessage({ type: "gromkost:init" }, (res) => {
    if (chrome.runtime.lastError) return;
    if (res && res.band) apply(res.band);
  });
})();
