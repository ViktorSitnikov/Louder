import { useState } from "react";
import { Text, TextInput, View } from "react-native";
import { RangeSlider } from "./Sliders";
import { BLUE, DB_MAX, DB_MIN, MUTED, styles, WARM } from "./theme";

function dbText(value) {
  return (Math.round(Number(value) * 10) / 10).toFixed(1).replace(".", ",");
}

function parseDb(text) {
  const n = Number(String(text).trim().replace(",", "."));
  if (!Number.isFinite(n)) return null;
  return Math.round(Math.min(DB_MAX, Math.max(DB_MIN, n)) * 10) / 10;
}

function DbInput({ value, color, disabled, onCommit }) {
  const [draft, setDraft] = useState(null);
  const commit = () => {
    const next = parseDb(draft ?? "");
    setDraft(null);
    if (!disabled && next != null && next !== value) onCommit(next);
  };
  return (
    <TextInput
      value={draft ?? dbText(value)}
      editable={!disabled}
      onFocus={(event) => {
        if (disabled) return;
        setDraft(dbText(value));
        event.target?.select?.();
      }}
      onChangeText={setDraft}
      onBlur={commit}
      onSubmitEditing={commit}
      style={[styles.dbInput, { color }]}
    />
  );
}

export function Band({ low, high, disabled, onChange }) {
  const active = !disabled && (low !== DB_MIN || high !== DB_MAX);
  const color = disabled ? MUTED : active ? WARM : BLUE;
  const textColor = active ? WARM : MUTED;
  return (
    <>
      <View style={styles.bandRow}>
        <View style={styles.band}>
          <DbInput
            value={low}
            color={textColor}
            disabled={disabled}
            onCommit={(next) => onChange(Math.min(next, high), high)}
          />
          <Text style={[styles.sep, active && styles.hot]}>…</Text>
          <DbInput
            value={high}
            color={textColor}
            disabled={disabled}
            onCommit={(next) => onChange(low, Math.max(next, low))}
          />
          <Text style={[styles.unit, active && styles.hot]}>дБ</Text>
        </View>
      </View>
      <RangeSlider
        low={low}
        high={high}
        min={DB_MIN}
        max={DB_MAX}
        color={color}
        disabled={disabled}
        onChange={onChange}
      />
    </>
  );
}
