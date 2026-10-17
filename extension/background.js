const BRIDGE = "http://127.0.0.1:47921/sync";

function detectBrowserExe() {
  const ua = navigator.userAgent || "";
  if (ua.includes("OPR/") || ua.includes("Opera")) return "opera.exe";
  if (ua.includes("Edg/")) return "msedge.exe";
  if (ua.includes("Brave")) return "brave.exe";
  if (ua.includes("Vivaldi")) return "vivaldi.exe";
  if (ua.includes("YaBrowser")) return "browser.exe";
  return "chrome.exe";
}

// Приложение видит, чей процесс подключился, и называет exe точно; по User-Agent Brave и Vivaldi неотличимы от Chrome.
let EXE = detectBrowserExe();
async function whoami() {
  try {
    const data = await (await fetch("http://127.0.0.1:47921/whoami")).json();
    if (data.exe) EXE = data.exe;
  } catch (_) {}
}
let lastBands = {};
const live = {};
let choices = {};
const asking = {};

function urlKey(raw) {
  const text = String(raw || "");
  try {
    const url = new URL(/^[a-z]+:/i.test(text) ? text : `https://${text}`);
    if (url.protocol === "file:") return "file";
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    return url.hostname.replace(/^www\./, "").toLowerCase();
  } catch (_) {
    return "";
  }
}

function known(url) {
  return Object.prototype.hasOwnProperty.call(choices, url);
}

function managed(url) {
  return !!url && choices[url] === true;
}

async function loadChoices() {
  const data = await chrome.storage.local.get("choices");
  const next = {};
  for (const [key, yes] of Object.entries(data.choices || {})) {
    const url = urlKey(key);
    if (url) next[url] = next[url] || !!yes;
  }
  choices = next;
  await chrome.storage.local.set({ choices });
  await syncHooks();
}

async function syncHooks() {
  const existing = await chrome.scripting.getRegisteredContentScripts();
  const ids = existing.map((script) => script.id).filter((id) => id.startsWith("gromkost-hook-"));
  if (ids.length) await chrome.scripting.unregisterContentScripts({ ids });
  for (const [host, yes] of Object.entries(choices)) {
    if (!yes) continue;
    const id = `gromkost-hook-${host.replace(/[^a-z0-9]+/gi, "-")}`.slice(0, 80);
    const matches = host === "file" ? ["file:///*"] : [`*://${host}/*`, `*://www.${host}/*`];
    await chrome.scripting
      .registerContentScripts([{ id, js: ["hook.js"], matches, runAt: "document_start", world: "MAIN", allFrames: true }])
      .catch(() => {});
  }
}

async function setChoice(url, yes) {
  if (!url) return;
  choices[url] = !!yes;
  await chrome.storage.local.set({ choices });
  await syncHooks();
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id || urlKey(tab.url) !== url) continue;
    delete asking[tab.id];
    if (yes) chrome.tabs.reload(tab.id);
    else chrome.tabs.sendMessage(tab.id, { type: "gromkost:release" }).catch(() => {});
  }
  scheduleSync();
}

function tabPlaying(tab) {
  const now = Date.now();
  const frames = Object.entries(live).filter(([key, value]) => key.startsWith(`${tab.id}:`) && now - value.at < 2500);
  if (!frames.length) return !!tab.audible;
  return frames.some(([, value]) => value.playing);
}
const blocked = {};
let currentBands = {};
let defaultBand = { low: -22, high: -12, off: false };

let syncTimer = 0;
function scheduleSync() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(syncTabs, 200);
}

async function syncTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    const payload = {
      exe: EXE,
      tabs: tabs
        .filter((t) => t.id && t.title && managed(urlKey(t.url)))
        .map((t) => {
          const playing = tabPlaying(t);
          if (playing) delete blocked[t.id];
          return {
            id: t.id,
            title: t.title.slice(0, 180),
            url: t.url,
            audible: playing,
            blocked: !!blocked[t.id],
            chosen: true,
          };
        }),
    };
    const res = await fetch(BRIDGE, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return;
    const data = await res.json();
    currentBands = data.bands || {};
    defaultBand = data.default || defaultBand;
    for (const [tabIdStr, band] of Object.entries(currentBands)) {
      const tabId = Number(tabIdStr);
      const key = `${tabId}:${band.low}:${band.high}:${band.boost}:${band.off ? 1 : 0}`;
      if (lastBands[tabId] !== key) {
        lastBands[tabId] = key;
        chrome.tabs.sendMessage(tabId, { type: "gromkost:band", band }).catch(() => {});
      }
    }
  } catch (_) {}
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab && sender.tab.id;
  if (msg && msg.type === "gromkost:init" && tabId) {
    const band = currentBands[String(tabId)] || defaultBand;
    sendResponse({ band });
    return true;
  }
  if (msg && msg.type === "gromkost:live" && tabId) {
    live[`${tabId}:${sender.frameId}`] = { playing: !!msg.playing, at: Date.now() };
    if (msg.playing) delete blocked[tabId];
    if (managed(urlKey(sender.tab.url))) scheduleSync();
  }
  if (msg && msg.type === "gromkost:capable" && sender.tab) {
    const url = urlKey(sender.tab.url);
    const ask = !!url && !known(url) && !asking[tabId];
    if (ask) asking[tabId] = true;
    sendResponse({ ask });
    return true;
  }
  if (msg && msg.type === "gromkost:choice") {
    const url = msg.url || urlKey(sender.tab && sender.tab.url);
    setChoice(url, !!msg.yes).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg && msg.type === "gromkost:popup") {
    chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      const tab = tabs[0];
      const url = tab ? urlKey(tab.url) : "";
      sendResponse({
        url,
        choice: url && known(url) ? choices[url] : null,
        enabled: Object.keys(choices).filter((key) => choices[key]),
      });
    });
    return true;
  }
  if (msg && msg.type === "gromkost:needplay" && sender.tab) {
    const tab = sender.tab;
    // Клавиша «играть» попадает в активную вкладку. Чужую не открываем: иначе уйдём с Ютуба на ВК.
    if (!tab.active) {
      blocked[tab.id] = true;
      scheduleSync();
    } else {
      fetch("http://127.0.0.1:47921/poke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exe: EXE }),
      }).catch(() => {});
      setTimeout(() => {
        chrome.tabs.get(tab.id).then((fresh) => {
          if (tabPlaying(fresh)) return;
          blocked[tab.id] = true;
          scheduleSync();
        }).catch(() => {});
      }, 1200);
    }
  }
  if (msg && msg.type === "gromkost:failed" && tabId) {
    fetch("http://127.0.0.1:47921/failed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exe: EXE, tab: tabId, reason: msg.reason }),
    }).catch(() => {});
  }
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === "loading") delete asking[tabId];
  const url = urlKey(tab.url);
  if (info.audible === true && url && !known(url) && !asking[tabId]) {
    asking[tabId] = true;
    chrome.tabs.sendMessage(tabId, { type: "gromkost:ask" }).catch(() => { delete asking[tabId]; });
  }
  if (info.status === "loading" && managed(url)) inject(tabId);
  if (managed(url)) scheduleSync();
});
chrome.tabs.onRemoved.addListener((tabId) => {
  delete lastBands[tabId];
  delete blocked[tabId];
  delete asking[tabId];
  for (const key of Object.keys(live)) if (key.startsWith(`${tabId}:`)) delete live[key];
  scheduleSync();
});

function waitComplete(tabId) {
  return new Promise((resolve) => {
    const timer = setTimeout(finish, 8000);
    function finish() {
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      resolve();
    }
    function onUpdated(id, info) {
      if (id === tabId && info.status === "complete") finish();
    }
    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

async function inject(tabId) {
  const target = { tabId, allFrames: true };
  await chrome.scripting.executeScript({ target, files: ["hook.js"], world: "MAIN" }).catch(() => {});
  await chrome.scripting.executeScript({ target, files: ["page.js"], world: "MAIN" }).catch(() => {});
  await chrome.scripting.executeScript({ target, files: ["content.js"] }).catch(() => {});
}

// Выгруженную вкладку браузер поднимает сам при клике. Повторяем это без смены окна:
// сначала тихая перезагрузка, и только если вкладка заморожена — короткий переход на неё и обратно.
async function wake(tabId) {
  let tab = await chrome.tabs.get(tabId);
  if (tab.discarded) {
    await chrome.tabs.reload(tabId);
    await waitComplete(tabId);
    tab = await chrome.tabs.get(tabId);
  }
  if (!tab.frozen) return;
  const [current] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
  await chrome.tabs.update(tabId, { active: true });
  const until = Date.now() + 1500;
  while (Date.now() < until) {
    const now = await chrome.tabs.get(tabId);
    if (!now.discarded && !now.frozen) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (current?.id && current.id !== tabId) await chrome.tabs.update(current.id, { active: true });
}

async function deliver(tabId, action) {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || !managed(urlKey(tab.url))) return;
  const msg = { type: "gromkost:media", action };
  try {
    await chrome.tabs.sendMessage(tabId, msg);
    return;
  } catch (_) {}
  try {
    await wake(tabId);
    await inject(tabId);
    await chrome.tabs.sendMessage(tabId, msg);
  } catch (_) {}
}

// Запрос висит, пока в приложении не нажмут старт/стоп, — команда приходит без задержки опроса.
async function waitCommands() {
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:47921/wait?exe=${EXE}`);
      const data = await res.json();
      for (const cmd of data.commands || []) deliver(cmd.tab, cmd.action);
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      await whoami();
    }
  }
}

whoami().then(async () => {
  await loadChoices();
  for (const tab of await chrome.tabs.query({})) {
    if (tab.id && managed(urlKey(tab.url))) await inject(tab.id);
  }
  scheduleSync();
  waitCommands();
});
