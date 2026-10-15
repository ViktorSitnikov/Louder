import { useEffect, useRef, useState } from "react";
import { findNodeHandle, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

const INK = "#101114";
const CARD = "#1a1c22";
const LINE = "#2a2d36";
const TEXT = "#f3f1ec";
const MUTED = "#8d919a";
const BLUE = "#9eb6f0";
const WARM = "#e8a87c";

function api() {
  return window.pywebview?.api;
}

function whenReady() {
  if (window.pywebview?.api) return Promise.resolve();
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", resolve, { once: true });
  });
}

function Slider({ value, max, color, onChange }) {
  const track = useRef(null);
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;

  const fromX = (clientX) => {
    const current = track.current;
    const node = current?.getBoundingClientRect ? current : findNodeHandle(current);
    if (!node?.getBoundingClientRect) return;
    const rect = node.getBoundingClientRect();
    if (!rect.width) return;
    const x = Math.min(rect.width, Math.max(0, clientX - rect.left));
    onChange(Math.round((x / rect.width) * max));
  };

  const down = (event) => {
    const point = event.nativeEvent?.clientX ?? event.clientX;
    fromX(point);
    const move = (ev) => fromX(ev.clientX);
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  };

  return (
    <View ref={track} style={styles.track} onMouseDown={down}>
      <View style={[styles.fill, { width: `${ratio * 100}%`, backgroundColor: color }]} />
      <View style={[styles.thumb, { left: `${ratio * 100}%`, backgroundColor: color }]} />
    </View>
  );
}

export default function App() {
  const [state, setState] = useState({ master: 0, autostart: false, apps: [], note: "" });
  const [ready, setReady] = useState(false);
  const hold = useRef(null);
  const flight = useRef(false);
  const pending = useRef(null);

  const applyState = (next) => {
    if (!next) return;
    const held = hold.current;
    if (!held) {
      setState(next);
      return;
    }
    setState({
      ...next,
      master: held.key === "master" ? held.value : next.master,
      apps: next.apps.map((app) =>
        held.key === app.name ? { ...app, percent: held.value } : app
      ),
    });
  };

  const push = (job) => {
    if (flight.current) {
      pending.current = job;
      return;
    }
    flight.current = true;
    job()
      .catch((error) => setState((prev) => ({ ...prev, note: String(error) })))
      .finally(() => {
        flight.current = false;
        const next = pending.current;
        pending.current = null;
        if (next) push(next);
      });
  };

  useEffect(() => {
    let stop = false;
    let timer = 0;
    whenReady().then(() => {
      if (stop) return;
      setReady(true);
      const tick = () => {
        api()
          .get_state()
          .then((next) => {
            if (!stop) applyState(next);
          })
          .catch(() => {});
        timer = window.setTimeout(tick, 1200);
      };
      tick();
    });
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, []);

  const changeMaster = (value) => {
    hold.current = { key: "master", value };
    setState((prev) => ({ ...prev, master: value }));
    push(() => api().set_master(value).then(applyState));
  };

  const changeApp = (name, value) => {
    hold.current = { key: name, value };
    setState((prev) => ({
      ...prev,
      apps: prev.apps.map((app) => (app.name === name ? { ...app, percent: value } : app)),
    }));
    push(() => api().set_app(name, value).then(applyState));
  };

  const endDrag = () => {
    hold.current = null;
  };

  return (
    <View style={styles.root} onMouseUp={endDrag}>
      <View style={styles.header}>
        <View style={styles.mark}>
          <View style={styles.markSm} />
          <View style={styles.markMd} />
          <View style={styles.markLg} />
        </View>
        <View>
          <Text style={styles.title}>Громкость</Text>
          <Text style={styles.sub}>Компьютер и отдельные программы</Text>
        </View>
      </View>

      <View style={styles.card}>
        <View style={styles.row}>
          <Text style={styles.cardTitle}>Компьютер</Text>
          <Text style={styles.percent}>{state.master}%</Text>
        </View>
        <Slider
          value={state.master}
          max={100}
          color={BLUE}
          onChange={changeMaster}
        />
      </View>

      <Text style={styles.section}>Программы</Text>
      <ScrollView style={styles.list} contentContainerStyle={styles.listContent}>
        {state.apps.length === 0 ? (
          <Text style={styles.empty}>
            {ready
              ? "Нет программ, которые пользуются звуком."
              : "Открываю микшер…"}
          </Text>
        ) : (
          state.apps.map((app) => {
            const hot = app.percent > 100;
            return (
              <View key={app.name} style={styles.card}>
                <View style={styles.row}>
                  <Text style={styles.cardTitle} numberOfLines={1}>
                    {app.title}
                  </Text>
                  <Pressable onPress={() => changeApp(app.name, 100)} style={styles.reset}>
                    <Text style={styles.resetText}>100%</Text>
                  </Pressable>
                  <Text style={[styles.percent, hot && styles.hot]}>{app.percent}%</Text>
                </View>
                <Slider
                  value={app.percent}
                  max={app.limit}
                  color={hot ? WARM : BLUE}
                  onChange={(value) => changeApp(app.name, value)}
                />
              </View>
            );
          })
        )}
        {state.apps.some((app) => app.percent > 100) ? (
          <Text style={styles.hint}>Выше 100% звук немного отстаёт — так работает усиление.</Text>
        ) : null}
        {state.note ? <Text style={styles.note}>{state.note}</Text> : null}
      </ScrollView>
      <Text style={styles.footer}>Клик мимо окна или по иконке в трее прячет его. Выход — правый клик по иконке.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    height: "100%",
    minHeight: 0,
    overflow: "hidden",
    backgroundColor: INK,
    paddingTop: 22,
    paddingHorizontal: 18,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 18,
  },
  mark: {
    width: 42,
    height: 42,
    borderRadius: 14,
    backgroundColor: CARD,
    borderWidth: 1,
    borderColor: LINE,
    marginRight: 12,
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "center",
    paddingBottom: 10,
    gap: 3,
  },
  markSm: { width: 4, height: 8, borderRadius: 2, backgroundColor: WARM },
  markMd: { width: 4, height: 14, borderRadius: 2, backgroundColor: WARM },
  markLg: { width: 4, height: 20, borderRadius: 2, backgroundColor: WARM },
  title: { color: TEXT, fontSize: 22, fontWeight: "700", fontFamily: "Segoe UI" },
  sub: { color: MUTED, fontSize: 12, marginTop: 2, fontFamily: "Segoe UI" },
  section: {
    color: MUTED,
    fontSize: 12,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    marginBottom: 8,
    marginLeft: 4,
    fontFamily: "Segoe UI",
  },
  list: { flexGrow: 1, flexShrink: 1, flexBasis: 0, minHeight: 0 },
  listContent: { paddingBottom: 24 },
  card: {
    backgroundColor: CARD,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: LINE,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 14,
    marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardTitle: { color: TEXT, fontSize: 15, fontFamily: "Segoe UI", flexShrink: 1, marginRight: 8 },
  percent: {
    color: MUTED,
    fontSize: 15,
    fontVariant: ["tabular-nums"],
    fontFamily: "Segoe UI",
    minWidth: 52,
    textAlign: "right",
  },
  hot: { color: WARM },
  track: {
    marginTop: 12,
    height: 22,
    justifyContent: "center",
    cursor: "pointer",
  },
  fill: { height: 6, borderRadius: 99 },
  thumb: {
    position: "absolute",
    top: 3,
    width: 16,
    height: 16,
    marginLeft: -8,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: INK,
  },
  reset: {
    flexShrink: 0,
    borderWidth: 1,
    borderColor: LINE,
    borderRadius: 8,
    paddingHorizontal: 7,
    paddingVertical: 2,
    marginRight: 8,
  },
  resetText: { color: MUTED, fontSize: 11, fontFamily: "Segoe UI" },
  empty: { color: MUTED, fontSize: 14, lineHeight: 20, fontFamily: "Segoe UI", padding: 8 },
  hint: { color: MUTED, fontSize: 12, lineHeight: 18, marginTop: 4, fontFamily: "Segoe UI" },
  note: { color: WARM, fontSize: 12, lineHeight: 18, marginTop: 6, fontFamily: "Segoe UI" },
  footer: {
    color: MUTED,
    fontSize: 11,
    textAlign: "center",
    paddingBottom: 14,
    fontFamily: "Segoe UI",
  },
});
