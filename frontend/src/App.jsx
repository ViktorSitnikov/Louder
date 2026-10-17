import { memo, useEffect, useRef, useState } from "react";
import { ScrollView, Text, TextInput, View } from "react-native";
import { Moon, Search, Settings, Sun, Volume2 } from "lucide-react";
import { AppCard, HiddenCard, IconButton, useReorder } from "./AppCard";
import { ProcessView } from "./Process";
import { SettingsView } from "./Settings";
import { MUTED, styles, WARM } from "./theme";

function api() {
  return window.pywebview?.api;
}

function patchApps(apps, name, pid, values) {
  return apps.map((app) => {
    if (app.name !== name) return app;
    if (pid) {
      return { ...app, parts: (app.parts || []).map((part) => (part.pid === pid ? { ...part, ...values } : part)) };
    }
    return { ...app, ...values, parts: (app.parts || []).map((part) => ({ ...part, ...values })) };
  });
}

// У сайта pid — номер первой вкладки, он меняется между опросами. Держим нажатие по названию сайта.
function partKey(part) {
  if (!part) return "";
  return part.pid < 0 ? `s:${part.title}` : `p:${part.pid}`;
}

// Пауза гасит всё вложенное, запуск — только то, что нажали: что именно заиграет, решает плеер.
function holdPlaying(apps, h) {
  const silence = (part) => ({ ...part, playing: false, tabs: part.tabs?.map((tab) => ({ ...tab, playing: false })) });
  return apps.map((app) => {
    if (app.name !== h.name) return app;
    if (!h.part) return h.playing ? { ...app, playing: true } : { ...silence(app), parts: (app.parts || []).map(silence) };
    const parts = (app.parts || []).map((part) => {
      if (partKey(part) !== h.part) return part;
      if (h.tab) return { ...part, tabs: (part.tabs || []).map((tab) => (tab.id === h.tab ? { ...tab, playing: h.playing } : tab)) };
      return h.playing ? { ...part, playing: true } : silence(part);
    });
    return { ...app, parts };
  });
}

function moveBetween(parts, title, after, before) {
  const moved = parts.find((part) => part.title === title);
  const rest = parts.filter((part) => part !== moved);
  const at = after ? rest.findIndex((part) => part.title === after) + 1 : before ? rest.findIndex((part) => part.title === before) : rest.length;
  rest.splice(at, 0, moved);
  return rest;
}

const PAGE_ORDER = { list: 0, detail: 1, settings: 2 };
const SLIDE_MS = 280;

// В дереве только текущий экран и тот, что уезжает. Иначе список и настройки живут вместе и открытие заметно тормозит.
function useSlide(page) {
  const [slide, setSlide] = useState({ page, leaving: null, on: true, dir: 1 });
  if (page !== slide.page) {
    const dir = Math.sign(PAGE_ORDER[page] - PAGE_ORDER[slide.page]) || 1;
    setSlide({ page, leaving: slide.page, on: false, dir });
  }
  useEffect(() => {
    if (slide.on || slide.leaving == null) return undefined;
    const first = requestAnimationFrame(() => setSlide((state) => ({ ...state, on: true })));
    return () => cancelAnimationFrame(first);
  }, [slide.page, slide.on, slide.leaving]);
  useEffect(() => {
    if (!slide.on || slide.leaving == null) return undefined;
    const timer = setTimeout(() => setSlide((state) => ({ ...state, leaving: null })), SLIDE_MS + 40);
    return () => clearTimeout(timer);
  }, [slide.page, slide.on, slide.leaving]);
  return slide;
}

const ProgramList = memo(function ProgramList({ apps, query, open, showHidden, ready, note, bag }) {
  const needle = query.trim().toLowerCase();
  const hit = (text) => !needle || String(text).toLowerCase().includes(needle);
  const partHit = (part) => hit(part.title) || (part.tabs || []).some((tab) => hit(tab.title));
  const sorted = [...apps].sort(
    (a, b) => Number(b.favorite) - Number(a.favorite) || (a.pos ?? Infinity) - (b.pos ?? Infinity) || a.title.localeCompare(b.title, "ru")
  );
  const shown = sorted.filter(
    (app) => !app.hidden && (hit(app.title) || (app.parts || []).some((part) => !part.hidden && partHit(part)))
  );
  const reorder = useReorder(shown, (app) => app.name, (name, after, before) => bag.current.moveApp(name, after, before));
  const hiddenApps = sorted.filter((app) => app.hidden && hit(app.title));
  const hiddenParts = sorted.flatMap((app) =>
    app.hidden ? [] : (app.parts || []).filter((part) => part.hidden && (partHit(part) || hit(app.title))).map((part) => ({ ...part, app: app.name, appTitle: app.title }))
  );
  return (
    <>
      <Text style={styles.section}>Программы</Text>
      <View style={styles.searchWrap}>
        <Search size={15} color={MUTED} />
        <TextInput value={query} onChangeText={bag.current.setQuery} placeholder="Поиск" placeholderTextColor={MUTED} style={styles.search} />
      </View>
      <ScrollView style={[styles.list, styles.scroll]} contentContainerStyle={styles.listContent}>
        {shown.length === 0 ? (
          <Text style={styles.empty}>
            {ready ? (needle ? "Ничего не найдено." : "Нет программ, которые пользуются звуком.") : "Открываю микшер…"}
          </Text>
        ) : (
          shown.map((app) => {
            const parts = (app.parts || []).filter((part) => !part.hidden);
            const visible = hit(app.title) ? parts : parts.filter((part) => partHit(part));
            const expandable = parts.length > 1 || parts.some((part) => part.pid < 0);
            return (
              <AppCard
                key={app.name}
                app={app}
                parts={visible}
                expanded={expandable && (open[app.name] || !!needle)}
                sortable={!needle}
                cardRef={(el) => (reorder.rows.current[app.name] = el)}
                dragStyle={reorder.styleFor(app)}
                onGrip={!needle && shown.length > 1 ? (event) => reorder.start(shown.indexOf(app), event) : null}
                onToggle={() => bag.current.toggleOpen(app.name)}
                onOpen={(title = "") => bag.current.openDetail(app.name, title)}
                onPlay={(title = "", tabId = 0) => bag.current.play(app.name, title, tabId, parts)}
                onMove={(title, after, before) => bag.current.movePart(app.name, title, after, before)}
                onOff={(off, title = "") => bag.current.toggleOff(app.name, off, title)}
                onFavorite={() => bag.current.favorite(app.name, !app.favorite)}
                onHide={(hidden, title = "") => bag.current.hide(app.name, hidden, title)}
              />
            );
          })
        )}
        <HiddenCard
          apps={hiddenApps}
          parts={hiddenParts}
          open={showHidden || !!needle}
          onToggle={() => bag.current.toggleHidden()}
          onRestore={(name, title = "") => bag.current.hide(name, false, title)}
        />
        {note ? <Text style={styles.note}>{note}</Text> : null}
      </ScrollView>
    </>
  );
});

function whenReady() {
  if (window.pywebview?.api) return Promise.resolve();
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", resolve, { once: true });
  });
}

export default function App() {
  const [state, setState] = useState({ autostart: false, apps: [], note: "" });
  const [ready, setReady] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [grown, setGrown] = useState(false);
  const [listOn, setListOn] = useState(false);
  const [listMounted, setListMounted] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState({});
  const [showHidden, setShowHidden] = useState(false);
  const [detail, setDetail] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const hold = useRef(null);
  const playHold = useRef({});
  const settingsHold = useRef(null);
  const flight = useRef(false);
  const pending = useRef([]);

  // Браузер и Windows сообщают новое состояние плеера с задержкой — держим нажатое до подтверждения.
  const withPlayHolds = (apps) => {
    let out = apps;
    for (const [key, h] of Object.entries(playHold.current)) {
      const app = out.find((item) => item.name === h.name);
      const part = h.part ? app?.parts?.find((item) => partKey(item) === h.part) : null;
      const target = h.tab ? part?.tabs?.find((tab) => tab.id === h.tab) : h.part ? part : app;
      // Совпадение с ответом не снимает удержание: следом часто приходит более ранний снимок и возвращает старую иконку.
      if (!target || Date.now() > h.until) {
        delete playHold.current[key];
        continue;
      }
      out = holdPlaying(out, h);
    }
    // Кнопка сайта — сводка по его вкладкам, главная кнопка браузера — по сайтам: так они не расходятся.
    return out.map((app) => {
      const parts = (app.parts || []).map((part) =>
        part.tabs?.length && !playHold.current[`${app.name}|${partKey(part)}|`]
          ? { ...part, playing: part.tabs.some((tab) => tab.playing) }
          : part
      );
      const media = parts.filter((part) => part.media);
      if (!media.length || playHold.current[`${app.name}||`]) return { ...app, parts };
      return { ...app, parts, playing: media.some((part) => part.playing) };
    });
  };

  const applyState = (next) => {
    if (!next) return;
    const held = hold.current;
    const apps = held ? patchApps(next.apps, held.name, held.pid, held.values) : next.apps;
    const settings = settingsHold.current ? { ...next.settings, ...settingsHold.current } : next.settings;
    setState({ ...next, apps: withPlayHolds(apps), settings });
  };

  const patch = (name, pid, values, holdIt) => {
    if (holdIt) hold.current = { name, pid, values };
    setState((prev) => ({ ...prev, apps: patchApps(prev.apps, name, pid, values) }));
  };

  // Задания с ключом (ползунки) склеиваются — важно только последнее значение; остальные идут по очереди.
  const push = (job, key = "") => {
    if (flight.current) {
      const queue = pending.current;
      const at = key ? queue.findIndex((item) => item.key === key) : -1;
      if (at >= 0) queue[at] = { key, job };
      else queue.push({ key, job });
      return;
    }
    flight.current = true;
    job()
      .catch((error) => setState((prev) => ({ ...prev, note: String(error) })))
      .finally(() => {
        flight.current = false;
        const next = pending.current.shift();
        if (next) push(next.job, next.key);
      });
  };

  useEffect(() => {
    if (!grown) {
      setListOn(false);
      const hide = window.setTimeout(() => setListMounted(false), 190);
      return () => window.clearTimeout(hide);
    }
    setListMounted(true);
    const frame = window.requestAnimationFrame(() => setListOn(true));
    return () => window.cancelAnimationFrame(frame);
  }, [grown]);

  useEffect(() => {
    let stop = false;
    let pollTimer = 0;
    let active = false;

    const poll = () => {
      if (stop || !active) return;
      api()
        .get_state()
        .then((next) => {
          if (!stop && active) applyState(next);
        })
        .catch(() => {});
      pollTimer = window.setTimeout(poll, 1500);
    };

    const onOpen = () => {
      active = true;
      setRevealed(true);
      window.clearTimeout(pollTimer);
      api()
        ?.get_state()
        .then((next) => {
          if (!stop && active) applyState(next);
        })
        .catch(() => {});
      pollTimer = window.setTimeout(poll, 1500);
    };

    const onSettled = () => {
      if (!stop && active) setGrown(true);
    };

    const onClose = () => {
      active = false;
      setGrown(false);
      setRevealed(false);
      window.clearTimeout(pollTimer);
    };

    window.addEventListener("popup:open", onOpen);
    window.addEventListener("popup:settled", onSettled);
    window.addEventListener("popup:close", onClose);
    whenReady().then(() => {
      if (stop) return;
      setReady(true);
      if (!document.hidden) onOpen();
    });
    return () => {
      stop = true;
      window.removeEventListener("popup:open", onOpen);
      window.removeEventListener("popup:settled", onSettled);
      window.removeEventListener("popup:close", onClose);
      window.clearTimeout(pollTimer);
    };
  }, []);

  const changeBand = (name, part, low, high) => {
    patch(name, part?.pid, { low, high }, true);
    push(
      () => (part ? api().set_part(name, part.title, low, high) : api().set_app(name, low, high)).then(applyState),
      `band|${name}|${part?.pid || ""}`
    );
  };

  const changeBoost = (name, part, boost) => {
    patch(name, part?.pid, { boost }, true);
    push(() => api().set_boost(name, boost, part?.title || "").then(applyState), `boost|${name}|${part?.pid || ""}`);
  };

  // Действие решает интерфейс по тому, что видит пользователь: флаг «звучит» у браузера отстаёт.
  const togglePlay = (name, part, tab, playing) => {
    playHold.current[`${name}|${partKey(part)}|${tab?.id || ""}`] = {
      name,
      part: partKey(part),
      tab: tab?.id,
      playing: !playing,
      until: Date.now() + 4000,
    };
    setState((prev) => ({ ...prev, apps: withPlayHolds(prev.apps) }));
    push(() => api().toggle_play(name, part?.title || "", playing ? "pause" : "play", tab?.id || 0).then(applyState));
  };

  const moveApp = (name, after, before) => {
    setState((prev) => {
      const pos = (key) => (key ? prev.apps.find((app) => app.name === key)?.pos : null);
      const [a, b] = [pos(after), pos(before)];
      const value = a != null && b != null ? (a + b) / 2 : a != null ? a + 1 : b != null ? b - 1 : null;
      if (value == null) return prev;
      return { ...prev, apps: prev.apps.map((app) => (app.name === name ? { ...app, pos: value } : app)) };
    });
    push(() => api().move_app(name, after, before).then(applyState));
  };

  const movePart = (name, title, after, before) => {
    setState((prev) => ({
      ...prev,
      apps: prev.apps.map((app) =>
        app.name === name ? { ...app, parts: moveBetween(app.parts || [], title, after, before) } : app
      ),
    }));
    push(() => api().move_part(name, title, after, before).then(applyState));
  };

  const changeSetting = (key, value) => {
    const values = key === "default_band" ? { default_low: value[0], default_high: value[1] } : { [key]: value };
    settingsHold.current = { ...settingsHold.current, ...values };
    setState((prev) => ({ ...prev, settings: { ...prev.settings, ...values } }));
    push(
      () =>
        api()
          .set_setting(key, value)
          .then((settings) => {
            if (!pending.current.some((item) => item.key.startsWith("setting|"))) settingsHold.current = null;
            setState((prev) => ({ ...prev, settings }));
          })
          .catch((error) => {
            settingsHold.current = null;
            return api()
              .get_state()
              .then(applyState)
              .then(() => setState((prev) => ({ ...prev, note: error?.message || String(error) })));
          }),
      `setting|${key}`
    );
  };

  const hide = (name, hidden, title = "") => {
    push(() => api().set_hidden(name, hidden, title).then(applyState));
  };

  const toggleOff = (name, off, title = "") => {
    setState((prev) => ({
      ...prev,
      apps: prev.apps.map((app) =>
        app.name !== name
          ? app
          : title
            ? {
                ...app,
                parts: (app.parts || []).map((part) =>
                  part.title === title ? { ...part, off } : part
                ),
              }
            : { ...app, off }
      ),
    }));
    push(() => api().set_off(name, off, title).then(applyState));
  };

  const favorite = (name, value) => {
    push(() => api().set_favorite(name, value).then(applyState));
  };

  const settings = state.settings || {};
  const theme = settings.theme || (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    const root = document.documentElement.style;
    if (settings.accent) root.setProperty("--accent", settings.accent);
    else root.removeProperty("--accent");
  }, [settings.accent]);
  const toggleTheme = () => changeSetting("theme", theme === "light" ? "dark" : "light");

  const bag = useRef({});
  bag.current = {
    setQuery,
    toggleOpen: (name) => setOpen((prev) => ({ ...prev, [name]: !prev[name] })),
    toggleHidden: () => setShowHidden((value) => !value),
    openDetail: (name, title) => setDetail({ name, title }),
    play: (name, title, tabId, parts) => {
      const part = title ? parts.find((item) => item.title === title) : null;
      const tab = tabId ? part?.tabs?.find((item) => item.id === tabId) : null;
      const app = (state.apps || []).find((item) => item.name === name);
      togglePlay(name, part, tab, (tab || part || app)?.playing);
    },
    movePart,
    moveApp,
    toggleOff,
    favorite,
    hide,
  };

  const detailApp = detail && (state.apps || []).find((app) => app.name === detail.name);
  const detailPart = detail?.title ? detailApp?.parts?.find((part) => part.title === detail.title) : null;
  const header = (
    <View style={styles.header}>
      <View style={styles.mark}>
        <Volume2 size={20} color={WARM} />
      </View>
      <View>
        <Text style={styles.title}>Громкость</Text>
        <Text style={styles.sub}>Отдельные программы</Text>
      </View>
      <View style={styles.headerTools}>
        <IconButton onPress={toggleTheme} style={styles.headerButton}>
          {theme === "light" ? <Moon size={18} color={MUTED} /> : <Sun size={18} color={MUTED} />}
        </IconButton>
        <IconButton onPress={() => setSettingsOpen((value) => !value)} style={styles.headerButton}>
          <Settings size={18} color={settingsOpen ? WARM : MUTED} />
        </IconButton>
      </View>
    </View>
  );

  const showDetail = !!(detailApp && (!detail.title || detailPart));
  const page = settingsOpen && settings.default_low != null ? "settings" : showDetail ? "detail" : "list";
  const slide = useSlide(page);
  const detailHold = useRef(null);
  if (showDetail) detailHold.current = { app: detailApp, part: detailPart };
  const frozen = slide.leaving === "detail" ? detailHold.current : null;
  const detailView = showDetail ? { app: detailApp, part: detailPart } : frozen;
  // Двигается только верхний экран. Список под ним не сдвигается и не перерисовывается.
  const sheet = (name) => {
    const forward = slide.dir > 0;
    const moving = forward ? page : slide.leaving;
    const x = name !== moving ? 0 : forward ? (slide.on ? 0 : 100) : slide.on ? 100 : 0;
    return {
      transform: [{ translateX: `${x}%` }],
      transitionDuration: slide.on ? `${SLIDE_MS}ms` : "0ms",
      zIndex: PAGE_ORDER[name],
    };
  };

  return (
    <View style={styles.root} onMouseUp={() => (hold.current = null)}>
      <View style={[styles.content, revealed && styles.contentVisible]}>
        {header}
        <View style={styles.stage}>
        <View pointerEvents={page === "list" ? "auto" : "none"} style={styles.stagePage}>
          {listMounted ? (
          <View style={[styles.listFade, listOn && styles.listFadeOn]}>
          <ProgramList
            apps={state.apps || []}
            query={query}
            open={open}
            showHidden={showHidden}
            ready={ready}
            note={state.note}
            bag={bag}
          />
          </View>
          ) : null}
        </View>
        {detailView ? (
        <View pointerEvents={page === "detail" ? "auto" : "none"} style={[styles.stagePage, sheet("detail")]}>
            <ProcessView
              app={detailView.app}
              part={detailView.part}
              onBack={() => setDetail(null)}
              onBand={(low, high) => changeBand(detailView.app.name, detailView.part, low, high)}
              onBoost={(boost) => changeBoost(detailView.app.name, detailView.part, boost)}
              onOff={(off) => toggleOff(detailView.app.name, off, detailView.part?.title || "")}
              onPlay={() => togglePlay(detailView.app.name, detailView.part, null, (detailView.part || detailView.app).playing)}
            />
        </View>
        ) : null}
        {page === "settings" || slide.leaving === "settings" ? (
        <View pointerEvents={page === "settings" ? "auto" : "none"} style={[styles.stagePage, sheet("settings")]}>
          {settings.default_low != null ? (
            <SettingsView
              settings={settings}
              onBack={() => setSettingsOpen(false)}
              onChange={changeSetting}
              onCaptureHotkey={(paused) => push(() => api().suspend_hotkey(paused))}
            />
          ) : null}
        </View>
        ) : null}
        </View>
      </View>
    </View>
  );
}
