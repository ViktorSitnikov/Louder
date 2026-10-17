(() => {
  const ctxs = (window.__gromkost_ctxs = window.__gromkost_ctxs || []);
  const els = (window.__gromkost_els = window.__gromkost_els || []);
  const mark = (ctx) => {
    if (!ctx || ctxs.includes(ctx)) return;
    ctxs.push(ctx);
  };

  for (const name of ["AudioContext", "webkitAudioContext"]) {
    const Orig = window[name];
    if (!Orig || Orig.__gromkost) continue;
    function Wrapped(...args) {
      const ctx = new Orig(...args);
      mark(ctx);
      return ctx;
    }
    Wrapped.prototype = Orig.prototype;
    Wrapped.__gromkost = true;
    window[name] = Wrapped;
  }

  const start = AudioScheduledSourceNode?.prototype.start;
  if (start && !start.__gromkost) {
    function wrappedStart(...args) {
      mark(this.context);
      return start.apply(this, args);
    }
    wrappedStart.__gromkost = true;
    AudioScheduledSourceNode.prototype.start = wrappedStart;
  }

  const play = HTMLMediaElement.prototype.play;
  if (!play.__gromkost) {
    function wrappedPlay(...args) {
      if (!els.includes(this)) els.push(this);
      return play.apply(this, args);
    }
    wrappedPlay.__gromkost = true;
    HTMLMediaElement.prototype.play = wrappedPlay;
  }

  // Выход перехватываем до скриптов страницы. page.js грузится позже и уже не успевает.
  if (window.__gromkost_arm) return;

  let band = { low: -22, high: -12, off: false, boost: 100 };
  const chains = new WeakMap();
  const processors = [];
  let timer = 0;
  let hot = false;

  function gainFor(env, lowDb, highDb, off) {
    if (off || (lowDb <= -60 && highDb >= 0)) return 1.0;
    if (env < 1e-5) return 1.0;
    const level = 20 * Math.log10(env);
    let target = level;
    if (level < lowDb) target = lowDb;
    else if (level > highDb) target = highDb;
    else return 1.0;
    const g = Math.pow(10, (target - level) / 20);
    return Math.max(0, Math.min(31.6227766, g));
  }

  function contextRunning() {
    for (const ctx of ctxs) if (ctx.state === "running") return true;
    return false;
  }

  function startLoop() {
    if (timer || !contextRunning()) return;
    timer = setInterval(() => {
      if (!contextRunning()) {
        clearInterval(timer);
        timer = 0;
        return;
      }
      for (const step of processors) step();
      const heard = performance.now() < (window.__gromkost_heard || 0);
      if (heard === hot) return;
      hot = heard;
      window.dispatchEvent(new CustomEvent("__gromkost_level__"));
    }, 40);
  }

  const nativeConnect = AudioNode.prototype.connect;
  function arm(ctx) {
    let chain = chains.get(ctx);
    if (chain) return chain;
    mark(ctx);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    const gainNode = ctx.createGain();
    nativeConnect.call(analyser, gainNode);
    nativeConnect.call(gainNode, ctx.destination);
    const buf = new Float32Array(analyser.fftSize);
    let env = 0;
    let applied = 1;
    chain = { analyser, gainNode };
    chains.set(ctx, chain);
    ctx.addEventListener("statechange", startLoop);
    processors.push(() => {
      if (ctx.state !== "running") return;
      analyser.getFloatTimeDomainData(buf);
      let peak = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = Math.abs(buf[i]);
        if (v > peak) peak = v;
      }
      if (peak > env) env += (peak - env) * 0.85;
      else env += (peak - env) * 0.15;
      if (env > 0.01) window.__gromkost_heard = performance.now() + 400;
      const boost = band.off ? 1 : (band.boost ?? 100) / 100;
      const sense = peak > 1e-4 ? Math.max(peak, env) : env;
      const targetGain = sense > 1e-4 ? gainFor(sense, band.low, band.high, band.off) * boost : 1;
      if (targetGain < applied - 0.001) gainNode.gain.setValueAtTime(targetGain, ctx.currentTime);
      else if (targetGain > applied + 0.02) gainNode.gain.setTargetAtTime(targetGain, ctx.currentTime, 0.08);
      else return;
      applied = targetGain;
    });
    startLoop();
    return chain;
  }
  window.__gromkost_arm = arm;

  if (!nativeConnect.__gromkost) {
    function wrappedConnect(dest, ...rest) {
      try {
        if (dest && this.context && dest === this.context.destination) {
          const chain = arm(this.context);
          if (this !== chain.gainNode && this !== chain.analyser) return nativeConnect.call(this, chain.analyser, ...rest);
        }
      } catch (_) {}
      return nativeConnect.call(this, dest, ...rest);
    }
    wrappedConnect.__gromkost = true;
    AudioNode.prototype.connect = wrappedConnect;
  }

  window.addEventListener("__gromkost_band__", (e) => {
    if (e.detail) band = e.detail;
  });
})();
