(() => {
  if (window.__gromkost_hooked__) return;
  window.__gromkost_hooked__ = true;

  const hookedEls = new WeakSet();
  const mediaEls = new Set();
  let suspendedByUs = [];
  let reported = null;

  function mediaPlaying() {
    for (const el of mediaEls) {
      if (!el.paused && !el.ended && !el.muted && el.volume > 0) return true;
    }
    return false;
  }

  // chrome.tabs.audible держится ещё пару секунд после любого звука, поэтому «играет» решаем сами.
  function publish(force) {
    const playing = mediaPlaying() || performance.now() < (window.__gromkost_heard || 0);
    if (!force && playing === reported) return;
    reported = playing;
    window.dispatchEvent(new CustomEvent("__gromkost_live__", { detail: playing }));
  }

  let mediaCtx = null;

  function attachMedia(el) {
    if (!el || hookedEls.has(el)) return;
    hookedEls.add(el);
    mediaEls.add(el);
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const arm = window.__gromkost_arm;
      if (!Ctx || !arm) return;
      if (!mediaCtx) mediaCtx = new Ctx();
      const chain = arm(mediaCtx);
      const src = mediaCtx.createMediaElementSource(el);
      src.connect(chain.analyser);
      const resume = () => {
        if (mediaCtx.state === "suspended") mediaCtx.resume().catch(() => {});
      };
      el.addEventListener("play", resume);
      if (!el.paused) resume();
    } catch (_) {}
  }

  let pendingPlay = false;
  let pendingTimer = 0;
  // Беззвучные превью не должны перехватывать плеер. Из нескольких живых берём тот, что появился позже:
  // Яндекс Музыка при смене экрана оставляет старый audio в документе и заводит новый.
  function remember(el) {
    if (!el || el.muted || !el.volume) return;
    mediaEls.delete(el);
    mediaEls.add(el);
  }

  const origPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    attachMedia(this);
    remember(this);
    return origPlay.apply(this, args);
  };

  // Плееры с атрибутом autoplay не вызывают play(), их ловим по событию.
  document.addEventListener(
    "play",
    (e) => {
      attachMedia(e.target);
      remember(e.target);
      publish(false);
      if (pendingPlay) startChosen();
    },
    true
  );
  document.addEventListener("pause", () => publish(false), true);
  setInterval(() => publish(true), 1000);
  function alive() {
    for (const el of window.__gromkost_els || []) mediaEls.add(el);
    const list = [];
    for (const el of mediaEls) {
      if (el.muted || !el.volume) continue;
      list.push(el);
    }
    return list;
  }

  function pageContexts() {
    return (window.__gromkost_ctxs || []).filter((ctx) => ctx !== mediaCtx);
  }

  function pick() {
    const list = alive();
    for (let i = list.length - 1; i >= 0; i--) {
      const el = list[i];
      if (!el.paused && !el.ended) return el;
    }
    return list[list.length - 1] || null;
  }

  function startChosen() {
    const el = pick();
    if (!el) return false;
    resumeEl(el);
    pendingPlay = false;
    clearTimeout(pendingTimer);
    publish(false);
    return true;
  }

  const scan = () => {
    for (const el of document.querySelectorAll("video, audio")) attachMedia(el);
    if (pendingPlay) startChosen();
    publish(true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches?.("video, audio")) attachMedia(node);
        for (const el of node.querySelectorAll?.("video, audio") || []) attachMedia(el);
      }
    }
    if (pendingPlay) startChosen();
  }).observe(document.documentElement, { childList: true, subtree: true });

  function resumeEl(el) {
    el.play().catch((err) => {
      // AbortError — запуск перебили паузой, это не сбой.
      if (!err || err.name === "AbortError") return;
      if (err.name === "NotAllowedError") window.dispatchEvent(new CustomEvent("__gromkost_needplay__"));
      else window.dispatchEvent(new CustomEvent("__gromkost_failed__", { detail: err.name || String(err) }));
    });
  }

  function clickSitePlay() {
    const labels = ["воспроизведение", "воспроизвести", "play", "слушать", "playback"];
    const exact = (value) => labels.includes(String(value || "").replace(/\s+/g, " ").trim().toLowerCase());
    let best = null;
    let area = 0;
    const consider = (el) => {
      const box = el.getBoundingClientRect();
      const size = box.width * box.height;
      if (size <= area || box.width < 2 || box.height < 2) return;
      area = size;
      best = el;
    };
    for (const el of document.querySelectorAll("[aria-label]")) {
      if (exact(el.getAttribute("aria-label"))) consider(el);
    }
    if (!best) {
      for (const el of document.querySelectorAll("body *")) {
        if (exact(el.textContent)) consider(el);
      }
    }
    if (!best) return false;
    best.click();
    return true;
  }

  window.addEventListener("__gromkost_level__", () => publish(false));

  window.addEventListener("__gromkost_media__", (e) => {
    if (e.detail === "pause") {
      pendingPlay = false;
      clearTimeout(pendingTimer);
      for (const el of mediaEls) {
        if (!el.paused) el.pause();
      }
      window.__gromkost_heard = 0;
      publish(false);
      // Свой контекст для плееров не усыпляем: пробуждение заметно задерживает звук при запуске.
      for (const ctx of pageContexts()) {
        if (ctx.state === "running") {
          ctx.suspend().catch(() => {});
          suspendedByUs.push(ctx);
        }
      }
    } else if (e.detail === "play") {
      scan();
      const resume = suspendedByUs.splice(0);
      if (!resume.length) {
        const suspended = pageContexts().filter((ctx) => ctx.state === "suspended" || ctx.state === "interrupted");
        if (suspended.length) resume.push(suspended[suspended.length - 1]);
      }
      for (const ctx of resume) ctx.resume().catch(() => {});
      if (!startChosen()) {
        clickSitePlay();
        pendingPlay = true;
        clearTimeout(pendingTimer);
        pendingTimer = setTimeout(() => {
          pendingPlay = false;
        }, 15000);
        setTimeout(() => {
          if (mediaPlaying() || pageContexts().some((ctx) => ctx.state === "running")) return;
          window.dispatchEvent(new CustomEvent("__gromkost_needplay__"));
        }, 400);
      }
    }
  });
})();
