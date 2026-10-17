import { useRef } from "react";
import { findNodeHandle, View } from "react-native";
import { styles } from "./theme";

function valueAt(track, clientX, min, max, step) {
  const node = track?.getBoundingClientRect ? track : findNodeHandle(track);
  if (!node?.getBoundingClientRect || max === min) return null;
  const rect = node.getBoundingClientRect();
  if (!rect.width) return null;
  const x = Math.min(rect.width, Math.max(0, clientX - rect.left));
  const raw = min + (x / rect.width) * (max - min);
  const stepped = Math.round(raw / step) * step;
  return Math.round(Math.min(max, Math.max(min, stepped)) * 10) / 10;
}

function dragFrom(event, apply) {
  apply(event.nativeEvent?.clientX ?? event.clientX);
  const move = (ev) => apply(ev.clientX);
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}

export function RangeSlider({ low, high, min, max, color, disabled, onChange }) {
  const track = useRef(null);
  const drag = useRef(null);
  const bounds = useRef({ low, high });
  bounds.current = { low, high };
  const span = max - min;

  const apply = (clientX) => {
    const value = valueAt(track.current, clientX, min, max, 0.1);
    if (value == null) return;
    const current = bounds.current;
    let which = drag.current;
    if (which === "pick") {
      if (value === current.low) return;
      which = value < current.low ? "low" : "high";
      drag.current = which;
    }
    if (which === "low") onChange(Math.min(value, current.high), current.high);
    else onChange(current.low, Math.max(value, current.low));
  };

  const down = (event) => {
    if (disabled) return;
    const value = valueAt(track.current, event.nativeEvent?.clientX ?? event.clientX, min, max, 0.1);
    const current = bounds.current;
    if (current.low === current.high) drag.current = "pick";
    else if (value == null) drag.current = "low";
    else drag.current = Math.abs(value - current.low) <= Math.abs(value - current.high) ? "low" : "high";
    dragFrom(event, apply);
  };

  const left = span ? ((low - min) / span) * 100 : 0;
  const right = span ? ((high - min) / span) * 100 : 0;
  return (
    <View ref={track} style={[styles.track, disabled && styles.trackDisabled]} onMouseDown={down}>
      <View
        style={[
          styles.range,
          { left: `${left}%`, width: `${Math.max(0, right - left)}%`, backgroundColor: color },
        ]}
      />
      <View style={[styles.thumb, { left: `${left}%`, backgroundColor: color, zIndex: 2 }]} />
      <View style={[styles.thumb, { left: `${right}%`, backgroundColor: color, zIndex: 3 }]} />
    </View>
  );
}

export function Slider({ value, min, max, step, color, disabled, onChange }) {
  const track = useRef(null);
  const last = useRef(value);
  last.current = value;

  const apply = (clientX) => {
    const next = valueAt(track.current, clientX, min, max, step);
    if (next != null && next !== last.current) onChange(next);
  };

  const pos = max > min ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <View
      ref={track}
      style={[styles.track, disabled && styles.trackDisabled]}
      onMouseDown={(event) => !disabled && dragFrom(event, apply)}
    >
      <View style={[styles.range, { left: 0, width: `${pos}%`, backgroundColor: color }]} />
      <View style={[styles.thumb, { left: `${pos}%`, backgroundColor: color }]} />
    </View>
  );
}
