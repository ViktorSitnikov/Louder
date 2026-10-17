import { Pressable, ScrollView, Text, View } from "react-native";
import { ChevronLeft, PowerOff } from "lucide-react";
import { Band } from "./Band";
import { IconButton, PlayButton, PlayWarn } from "./AppCard";
import { Slider } from "./Sliders";
import { BLUE, MUTED, styles, WARM } from "./theme";

const EQ_BANDS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];

export function CorridorCard({ low, high, disabled, onChange }) {
  return (
    <View style={styles.card}>
      <Text style={styles.section}>Коридор</Text>
      <Band low={low} high={high} disabled={disabled} onChange={onChange} />
      <Text style={styles.hint}>Тише нижней границы звук поднимается, громче верхней — прижимается.</Text>
    </View>
  );
}

export function BoostCard({ boost, disabled, onChange }) {
  const color = disabled ? MUTED : boost !== 100 ? WARM : BLUE;
  return (
    <View style={styles.card}>
      <View style={styles.row}>
        <Text style={[styles.section, styles.grow]}>Усиление</Text>
        <Text style={[styles.percent, { color }]}>{boost} %</Text>
      </View>
      <Slider value={boost} min={0} max={600} step={5} color={color} disabled={disabled} onChange={onChange} />
    </View>
  );
}

export function EqCard() {
  return (
    <View style={styles.card}>
      <View style={styles.row}>
        <Text style={[styles.section, styles.grow]}>Эквалайзер</Text>
        <Text style={styles.soon}>скоро</Text>
      </View>
      <View style={styles.eq}>
        {EQ_BANDS.map((label, i) => (
          <View key={label} style={styles.eqBand}>
            <View style={styles.eqTrack}>
              <View style={[styles.eqKnob, { bottom: `${40 + 10 * Math.sin(i)}%` }]} />
            </View>
            <Text style={styles.eqLabel}>{label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

export function DetailHead({ title, sub, playing, onBack, children }) {
  return (
    <View style={[styles.row, styles.detailHead]}>
      <Pressable onPress={onBack} style={styles.chevron} dataSet={{ btn: "" }}>
        <ChevronLeft size={20} color={MUTED} />
      </Pressable>
      <View style={styles.detailTitle}>
        <Text style={[styles.cardTitle, playing && styles.textPlaying]} numberOfLines={1}>
          {title}
        </Text>
        {sub ? (
          <Text style={styles.sub} numberOfLines={1}>
            {sub}
          </Text>
        ) : null}
      </View>
      {children}
    </View>
  );
}

export function ProcessView({ app, part, onBack, onBand, onBoost, onOff, onPlay }) {
  const item = part || app;
  const disabled = !!(app.off || part?.off);
  return (
    <View style={styles.list}>
      <DetailHead title={item.title} sub={part ? app.title : ""} playing={item.playing} onBack={onBack}>
        {(part?.tabs || []).some((tab) => tab.blocked) ? <PlayWarn /> : null}
        <PlayButton visible={!!item.media} playing={item.playing} onPress={onPlay} />
        <IconButton onPress={() => onOff(!item.off)}>
          <PowerOff size={16} color={disabled ? WARM : MUTED} />
        </IconButton>
      </DetailHead>
      <ScrollView style={[styles.list, styles.scroll]} contentContainerStyle={styles.listContent}>
        <CorridorCard low={item.low} high={item.high} disabled={disabled} onChange={onBand} />
        <BoostCard boost={item.boost} disabled={disabled} onChange={onBoost} />
        <EqCard />
      </ScrollView>
    </View>
  );
}
