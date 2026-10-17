import { useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { ChevronRight, CircleAlert, Eye, EyeOff, GripVertical, Pause, Play, PowerOff, Settings, Star } from "lucide-react";
import { MUTED, styles, WARM } from "./theme";

// data-btn — подсветка акцентом при наведении, правило в index.html.
export function IconButton({ onPress, style, children }) {
  return (
    <Pressable onPress={onPress} style={[styles.icon, style]} dataSet={{ btn: "" }}>
      {children}
    </Pressable>
  );
}

// grid 0fr → 1fr анимирует высоту до «auto» без замеров; содержимое остаётся смонтированным, чтобы было что сворачивать.
function Collapse({ open, clip = true, children }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateRows: open ? "1fr" : "0fr",
        opacity: open ? 1 : 0,
        transition: "grid-template-rows 240ms cubic-bezier(0.22, 1, 0.36, 1), opacity 200ms",
      }}
    >
      <div style={{ overflow: clip ? "hidden" : "visible", minHeight: 0, display: "flex", flexDirection: "column" }}>{children}</div>
    </div>
  );
}

function Chevron({ open }) {
  return (
    <View style={[styles.chevronIcon, open && styles.chevronOpen]}>
      <ChevronRight size={16} color={MUTED} />
    </View>
  );
}

// Медиасессия у программы появляется и пропадает на лету — кнопку не выдёргиваем, а плавно прячем.
export function PlayWarn() {
  return (
    <span title="Сначала нажмите «Играть» на вкладке" style={{ display: "flex", marginRight: 6, flexShrink: 0 }}>
      <CircleAlert size={14} color={WARM} />
    </span>
  );
}

export function PlayButton({ visible = true, playing, size = 16, onPress }) {
  const cell = { width: size, height: size, alignItems: "center", justifyContent: "center" };
  return (
    <View
      pointerEvents={visible ? "auto" : "none"}
      style={[styles.playSlot, { width: visible ? size + 12 : 0 }, !visible && styles.playSlotHidden]}
    >
      <IconButton onPress={onPress}>
        <View style={[styles.swap, { width: size, height: size }]}>
          <View style={[styles.swapTrack, { width: size * 2, transform: [{ translateX: playing ? -size : 0 }] }]}>
            <View style={cell}>
              <Play size={size} color={MUTED} />
            </View>
            <View style={cell}>
              <Pause size={size} color={WARM} />
            </View>
          </View>
        </View>
      </IconButton>
    </View>
  );
}

const SWAP_MS = 180;

// Строки остаются на своих местах в вёрстке, а двигаются сдвигом: тащимая едет за курсором,
// остальные за SWAP_MS разъезжаются в образовавшийся зазор. Иначе перестановка в списке просто прыгает.
export function useReorder(items, keyOf, onMove) {
  const rows = useRef({});
  const [drag, setDrag] = useState(null);
  const dragRef = useRef(null);

  const start = (from, event) => {
    event.preventDefault();
    const layout = items.map((item) => {
      const rect = rows.current[keyOf(item)]?.getBoundingClientRect();
      return rect ? { top: rect.top, height: rect.height } : { top: 0, height: 0 };
    });
    const startY = event.clientY;
    // Избранные всегда сверху, поэтому карточку нельзя перетащить через эту границу — она бы вернулась назад.
    const favCount = items.filter((it) => it.favorite).length;
    const place = (dy) => {
      const center = layout[from].top + dy + layout[from].height / 2;
      const raw = layout.filter((box, i) => i !== from && box.top + box.height / 2 < center).length;
      return items[from].favorite ? Math.min(raw, favCount - 1) : Math.max(raw, favCount);
    };
    const apply = (next) => {
      dragRef.current = next;
      setDrag(next);
    };
    apply({ from, to: from, dy: 0, layout, dropping: false });
    const move = (e) => {
      const dy = e.clientY - startY;
      const to = place(dy);
      const prev = dragRef.current;
      if (prev && prev.to === to && Math.abs(prev.dy - dy) < 0.5) return;
      apply({ from, to, dy, layout, dropping: false });
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      const last = dragRef.current;
      if (!last) return;
      apply({ ...last, dropping: true });
      window.setTimeout(() => {
        dragRef.current = null;
        setDrag(null);
        if (last.to === last.from) return;
        const rest = items.filter((_, i) => i !== last.from);
        onMove(keyOf(items[last.from]), rest[last.to - 1] ? keyOf(rest[last.to - 1]) : "", rest[last.to] ? keyOf(rest[last.to]) : "");
      }, SWAP_MS);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  useEffect(() => {
    if (!drag || drag.dropping) return undefined;
    const prev = document.body.style.cursor;
    document.body.style.cursor = "grabbing";
    return () => {
      document.body.style.cursor = prev;
    };
  }, [!!drag && !drag.dropping]);

  let slots = null;
  if (drag) {
    const gap = drag.layout.length > 1 ? drag.layout[1].top - drag.layout[0].top - drag.layout[0].height : 0;
    const order = drag.layout.map((_, i) => i).filter((i) => i !== drag.from);
    order.splice(Math.max(0, Math.min(drag.to, order.length)), 0, drag.from);
    slots = {};
    let y = drag.layout[0]?.top || 0;
    for (const i of order) {
      slots[i] = y;
      y += drag.layout[i].height + gap;
    }
  }

  const styleFor = (item) => {
    if (!drag || !slots) return null;
    const i = items.findIndex((it) => keyOf(it) === keyOf(item));
    if (i < 0 || !drag.layout[i]) return null;
    const follow = i === drag.from && !drag.dropping;
    const y = (follow ? drag.layout[i].top + drag.dy : slots[i]) - drag.layout[i].top;
    return {
      transform: [{ translateY: y }, ...(follow ? [{ scale: 1.02 }] : [])],
      zIndex: i === drag.from ? 5 : 1,
      transitionProperty: follow ? "none" : "transform",
      transitionDuration: `${SWAP_MS}ms`,
      transitionTimingFunction: "cubic-bezier(0.22, 1, 0.36, 1)",
      ...(follow ? styles.dragLift : null),
    };
  };

  return { rows, start, styleFor, dragging: !!drag };
}

function Grip({ onStart, size = 14 }) {
  return (
    <View onMouseDown={onStart} style={styles.grip} dataSet={{ btn: "" }}>
      <GripVertical size={size} color={MUTED} />
    </View>
  );
}

const byTitle = (part) => part.title;

export function AppCard({
  app,
  parts,
  expanded,
  sortable,
  cardRef,
  dragStyle,
  onGrip,
  onToggle,
  onOpen,
  onPlay,
  onOff,
  onFavorite,
  onHide,
  onMove,
}) {
  const visible = (app.parts || []).filter((part) => !part.hidden);
  const many = visible.length > 1 || visible.some((part) => part.pid < 0);
  const { rows, start, styleFor, dragging } = useReorder(parts, byTitle, onMove);
  return (
    <View ref={cardRef} style={[styles.card, styles.cardCompact, app.playing && styles.cardPlaying, dragStyle]}>

      <View style={styles.row}>
        {onGrip ? <Grip onStart={onGrip} size={16} /> : null}
        {many ? (
          <Pressable onPress={onToggle} style={styles.chevron} dataSet={{ btn: "" }}>
            <Chevron open={expanded} />
          </Pressable>
        ) : null}
        <Pressable onPress={many ? onToggle : undefined} style={styles.grow}>
          <Text style={styles.cardTitle} numberOfLines={1}>
            {app.title}
          </Text>
        </Pressable>
        <PlayButton visible={!!app.media} playing={app.playing} onPress={() => onPlay()} />
        <IconButton onPress={() => onOpen()}>
          <Settings size={16} color={MUTED} />
        </IconButton>
        <IconButton onPress={() => onOff(!app.off)}>
          <PowerOff size={16} color={app.off ? WARM : MUTED} />
        </IconButton>
        <IconButton onPress={onFavorite}>
          <Star size={16} color={app.favorite ? WARM : MUTED} fill={app.favorite ? WARM : "none"} />
        </IconButton>
        <IconButton onPress={() => onHide(true)}>
          <EyeOff size={16} color={MUTED} />
        </IconButton>
      </View>
      {many ? (
        <Collapse open={expanded} clip={!dragging}>
          {parts.map((part) => {
            const tabs = part.tabs || [];
            return (
              <View
                key={`${part.pid}:${part.title}`}
                ref={(el) => (rows.current[part.title] = el)}
                style={[styles.part, part.playing && styles.partPlaying, styleFor(part)]}
              >
                <View style={styles.row}>
                  {sortable && parts.length > 1 ? (
                    <Grip onStart={(e) => start(parts.indexOf(part), e)} />
                  ) : null}
                  <View style={styles.grow}>
                    <Text style={[styles.partTitle, part.playing && styles.textPlaying]} numberOfLines={1}>
                      {part.title}
                    </Text>
                    {tabs.length === 1 ? (
                      <Text style={styles.tabTitle} numberOfLines={1}>
                        {tabs[0].title}
                      </Text>
                    ) : null}
                  </View>
                  {tabs.some((tab) => tab.blocked) ? <PlayWarn /> : null}
                  {part.media ? (
                    <PlayButton playing={part.playing} size={15} onPress={() => onPlay(part.title)} />
                  ) : null}
                  <IconButton onPress={() => onOpen(part.title)}>
                    <Settings size={15} color={MUTED} />
                  </IconButton>
                  <IconButton onPress={() => onOff(!part.off, part.title)}>
                    <PowerOff size={15} color={app.off || part.off ? WARM : MUTED} />
                  </IconButton>
                  <IconButton onPress={() => onHide(true, part.title)}>
                    <EyeOff size={15} color={MUTED} />
                  </IconButton>
                </View>
                {tabs.length > 1
                  ? tabs.map((tab) => (
                      <View key={tab.id} style={[styles.row, styles.tabRow, tab.playing && styles.tabPlaying]}>
                        <Text style={[styles.tabTitle, styles.grow, tab.playing && styles.textPlaying]} numberOfLines={1}>
                          {tab.title}
                        </Text>
                        {tab.blocked ? <PlayWarn /> : null}
                        <PlayButton playing={tab.playing} size={14} onPress={() => onPlay(part.title, tab.id)} />
                      </View>
                    ))
                  : null}
              </View>
            );
          })}
        </Collapse>
      ) : null}
    </View>
  );
}

export function HiddenCard({ apps, parts, open, onToggle, onRestore }) {
  const total = apps.length + parts.length;
  if (!total) return null;
  return (
    <View style={styles.card}>
      <Pressable onPress={onToggle} style={styles.row}>
        <View style={styles.chevron}>
          <Chevron open={open} />
        </View>
        <Text style={styles.cardTitle}>Скрытые ({total})</Text>
      </Pressable>
      <Collapse open={open}>
        {apps.map((app) => (
          <View key={app.name} style={styles.hiddenRow}>
            <Text style={styles.partTitle} numberOfLines={1}>
              {app.title}
            </Text>
            <IconButton onPress={() => onRestore(app.name)}>
              <Eye size={16} color={MUTED} />
            </IconButton>
          </View>
        ))}
        {parts.map((part) => (
          <View key={`${part.app}:${part.title}`} style={styles.hiddenRow}>
            <Text style={styles.partTitle} numberOfLines={1}>
              {part.appTitle} · {part.title}
            </Text>
            <IconButton onPress={() => onRestore(part.app, part.title)}>
              <Eye size={16} color={MUTED} />
            </IconButton>
          </View>
        ))}
      </Collapse>
    </View>
  );
}
