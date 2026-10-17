import { useEffect, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { X } from "lucide-react";
import { IconButton } from "./AppCard";
import { BoostCard, CorridorCard, DetailHead, EqCard } from "./Process";
import { MUTED, styles } from "./theme";

// "" — цвет темы по умолчанию (свой для светлой и тёмной).
const PRESETS = ["", "#f2c94c", "#6fcf97", "#56ccf2", "#7b8cff", "#bb6bd9", "#eb5757"];

function comboOf(event) {
  const code = event.code || "";
  const key = code.startsWith("Key") ? code.slice(3) : code.startsWith("Digit") ? code.slice(5) : /^F\d+$|^Space$/.test(code) ? code : "";
  const mods = [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Win"].filter(Boolean);
  return key && mods.length ? [...mods, key].join("+") : "";
}

function HotkeyCard({ value, onChange, onCapture }) {
  const [capturing, setCapturing] = useState(false);
  useEffect(() => {
    if (!capturing) return undefined;
    onCapture(true);
    const down = (event) => {
      event.preventDefault();
      if (event.code === "Escape") return setCapturing(false);
      const combo = comboOf(event);
      if (!combo) return;
      setCapturing(false);
      onChange(combo);
    };
    const cancel = () => setCapturing(false);
    window.addEventListener("keydown", down, true);
    window.addEventListener("popup:close", cancel);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("popup:close", cancel);
      onCapture(false);
    };
  }, [capturing]);
  return (
    <View style={styles.card}>
      <Text style={styles.section}>Открыть окно сочетанием</Text>
      <View style={styles.row}>
        <Pressable onPress={() => setCapturing(true)} style={[styles.searchWrap, styles.grow, styles.hotkey]} dataSet={{ btn: "" }}>
          <Text style={[styles.cardTitle, !value && !capturing && { color: MUTED }]}>
            {capturing ? "Нажмите сочетание…" : value || "Выключено"}
          </Text>
        </Pressable>
        {value ? (
          <IconButton onPress={() => onChange("")}>
            <X size={16} color={MUTED} />
          </IconButton>
        ) : null}
      </View>
      <Text style={styles.hint}>Нужен хотя бы один из Ctrl, Alt, Shift, Win. Esc — отменить ввод.</Text>
    </View>
  );
}

export function SettingsView({ settings, onBack, onChange, onCaptureHotkey }) {
  const accent = settings.accent || "";
  return (
    <View style={styles.list}>
      <DetailHead title="Настройки" onBack={onBack} />
      <ScrollView style={[styles.list, styles.scroll]} contentContainerStyle={styles.listContent}>
        <View style={styles.card}>
          <Text style={styles.section}>Акцентный цвет</Text>
          <View style={styles.swatches}>
            {PRESETS.map((color) => (
              <Pressable
                key={color || "default"}
                onPress={() => onChange("accent", color)}
                style={[styles.swatch, { backgroundColor: color || "#e8a87c" }, accent === color && styles.swatchActive]}
              />
            ))}
            <input
              type="color"
              title="Свой цвет"
              value={accent || "#e8a87c"}
              onChange={(event) => onChange("accent", event.target.value)}
            />
          </View>
        </View>
        <HotkeyCard value={settings.hotkey} onChange={(combo) => onChange("hotkey", combo)} onCapture={onCaptureHotkey} />
        <Text style={styles.section}>По умолчанию для новых программ</Text>
        <CorridorCard
          low={settings.default_low}
          high={settings.default_high}
          onChange={(low, high) => onChange("default_band", [low, high])}
        />
        <BoostCard boost={settings.default_boost} onChange={(boost) => onChange("default_boost", boost)} />
        <EqCard />
      </ScrollView>
    </View>
  );
}
