import React from "react";
import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { T, hand, mono, sans } from "./theme";

export const ease = Easing.bezier(0.22, 1, 0.36, 1);
export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export const useSp = (delay = 0, damping = 18, stiffness = 130, mass = 0.9) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping, stiffness, mass } });
};

// Settled transforms go back to "none" so text is drawn crisp, never as a scaled bitmap.
export const tf = (s: number, t: string) => (s > 0.999 ? "none" : t);

// Dotted notebook paper, as on the rooms site, drifting slowly, with soft pink and blue light.
export const Paper: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const f = useCurrentFrame();
  const a = Math.sin(f / 80) * 40;
  return (
    <AbsoluteFill style={{ background: T.paper, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(1100px 700px at ${12 + a / 30}% ${8 + a / 40}%, rgba(255,198,236,0.45), transparent 70%),
            radial-gradient(1000px 700px at ${92 - a / 30}% ${96 - a / 40}%, rgba(166,220,255,0.45), transparent 70%)`,
        }}
      />
      <AbsoluteFill
        style={{
          backgroundImage: `radial-gradient(${T.dot} 2.1px, transparent 2.5px)`,
          backgroundSize: "36px 36px",
          backgroundPosition: `${(f * 0.25) % 36}px ${(f * 0.12) % 36}px`,
        }}
      />
      {children}
    </AbsoluteFill>
  );
};

// The rooms four-point star.
export const Star: React.FC<{ size: number; color?: string; style?: React.CSSProperties }> = ({ size, color = T.ink, style }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" style={{ display: "block", ...style }}>
    <path fill={color} d="M12 0C13 8 16 11 24 12 16 13 13 16 12 24 11 16 8 13 0 12 8 11 11 8 12 0Z" />
  </svg>
);

export const Brand: React.FC<{ size?: number; x?: number; y?: number; delay?: number }> = ({ size = 58, x = 70, y = 44, delay = 0 }) => {
  const s = useSp(delay);
  const f = useCurrentFrame();
  return (
    <div style={{ position: "absolute", left: x, top: y, display: "flex", alignItems: "center", gap: size * 0.28, opacity: s }}>
      <Star size={size * 0.7} style={{ transform: `rotate(${(1 - s) * -90 + Math.sin(f / 40) * 4}deg)` }} />
      <div style={{ fontFamily: hand, fontWeight: 700, fontSize: size, lineHeight: 1, color: T.ink }}>Rooms</div>
    </div>
  );
};

export const Chip: React.FC<{ text: string; x?: number; y?: number; right?: number; delay?: number; bg?: string }> = ({ text, x, y = 52, right, delay = 0, bg = T.paper }) => {
  const s = useSp(delay);
  return (
    <div
      style={{
        position: "absolute", left: x, right, top: y, opacity: s, transform: tf(s, `translateY(${(1 - s) * -16}px)`),
        fontFamily: sans, fontWeight: 600, fontSize: 24, color: T.ink, background: bg,
        border: `3px solid ${T.ink}`, borderRadius: 999, padding: "7px 20px", whiteSpace: "nowrap",
      }}
    >
      {text}
    </div>
  );
};

// Highlighter that draws itself from left to right, like the site's pink .hl mark.
export const Mark: React.FC<{ children: React.ReactNode; at: number; color?: string; dur?: number }> = ({ children, at, color = T.pk, dur = 12 }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [at, at + dur], [0, 100], { ...clamp, easing: ease });
  return (
    <span
      style={{
        backgroundImage: `linear-gradient(transparent 52%, ${color} 52%, ${color} 92%, transparent 92%)`,
        backgroundRepeat: "no-repeat", backgroundSize: `${p}% 100%`, padding: "0 4px", margin: "0 -4px",
        boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone",
      }}
    >
      {children}
    </span>
  );
};

// Bottom caption. Words rise one by one; *words* get the pink highlighter after they land.
type Seg = { t: string; hi: boolean };
const splitCaption = (text: string): Seg[][] => {
  const words: Seg[][] = [];
  let hi = false;
  for (const raw of text.split(/\s+/).filter(Boolean)) {
    const segs: Seg[] = [];
    let cur = "";
    for (const ch of raw) {
      if (ch === "*") {
        if (cur) segs.push({ t: cur, hi });
        cur = "";
        hi = !hi;
      } else cur += ch;
    }
    if (cur) segs.push({ t: cur, hi });
    // A highlight that runs on to the next word keeps the space inside the mark.
    if (hi && segs.length) segs[segs.length - 1].t += "\u00a0";
    words.push(segs);
  }
  return words;
};

export const Caption: React.FC<{ text: string; at?: number; out?: number; y?: number; size?: number }> = ({ text, at = 0, out, y = 948, size = 54 }) => {
  const f = useCurrentFrame();
  const words = splitCaption(text);
  const fadeOut = out === undefined ? 1 : interpolate(f, [out - 8, out], [1, 0], clamp);
  const lastAt = at + words.length * 2.4 + 6;
  let hiIdx = 0;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: y, display: "flex", justifyContent: "center", opacity: fadeOut }}>
      <div style={{ fontFamily: sans, fontWeight: 800, fontSize: size, letterSpacing: -1.4, color: T.ink, whiteSpace: "nowrap" }}>
        {words.map((segs, i) => {
          const d = at + i * 2.4;
          const o = interpolate(f, [d, d + 8], [0, 1], clamp);
          const ty = interpolate(f, [d, d + 12], [22, 0], { ...clamp, easing: ease });
          const endsHi = segs[segs.length - 1].hi && segs[segs.length - 1].t.endsWith("\u00a0");
          return (
            <React.Fragment key={i}>
              <span style={{ display: "inline-block", opacity: o, transform: ty === 0 ? "none" : `translateY(${ty}px)` }}>
                {segs.map((sg, j) => (sg.hi ? <Mark key={j} at={lastAt + hiIdx++ * 3}>{sg.t}</Mark> : <span key={j}>{sg.t}</span>))}
              </span>
              {i < words.length - 1 && !endsHi ? " " : null}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

// Handwritten note in Caveat with a drawn arrow.
export const Note: React.FC<{ text: string; x: number; y: number; at: number; rotate?: number; size?: number; arrow?: string; arrowBox?: [number, number, number, number]; color?: string }> = ({
  text, x, y, at, rotate = -3, size = 46, arrow, arrowBox = [0, 0, 120, 80], color = T.pkd,
}) => {
  const f = useCurrentFrame();
  const chars = Math.round(interpolate(f, [at, at + text.length * 1.1], [0, text.length], clamp));
  const draw = interpolate(f, [at + text.length * 1.1 - 4, at + text.length * 1.1 + 12], [1, 0], { ...clamp, easing: ease });
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: `rotate(${rotate}deg)`, transformOrigin: "0 50%" }}>
      <div style={{ fontFamily: hand, fontWeight: 700, fontSize: size, color, whiteSpace: "nowrap", lineHeight: 1 }}>{text.slice(0, chars)}</div>
      {arrow ? (
        <svg style={{ position: "absolute", left: arrowBox[0], top: arrowBox[1], overflow: "visible" }} width={arrowBox[2]} height={arrowBox[3]}>
          <path d={arrow} fill="none" stroke={color} strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={1} strokeDashoffset={draw} />
        </svg>
      ) : null}
    </div>
  );
};

// A typed line with a blinking caret. Returns the visible text for a frame.
export const typed = (full: string, f: number, start: number, cps = 1.05) => {
  // Slight human rhythm: pause a touch after spaces and commas.
  let t = start;
  let n = 0;
  for (let i = 0; i < full.length; i++) {
    const c = full[i];
    t += c === " " ? cps * 1.5 : c === "," || c === ":" ? cps * 3 : cps * (0.8 + ((i * 37) % 7) / 14);
    if (t <= f) n = i + 1;
  }
  return full.slice(0, n);
};

export const Caret: React.FC<{ h?: number; color?: string }> = ({ h = 48, color = T.ink }) => {
  const f = useCurrentFrame();
  return <span style={{ display: "inline-block", width: 4, height: h, background: color, marginLeft: 3, verticalAlign: "-0.15em", opacity: Math.floor(f / 15) % 2 ? 0 : 1 }} />;
};

// Three bouncing dots.
export const Dots: React.FC<{ size?: number; color?: string }> = ({ size = 14, color = T.ink }) => {
  const f = useCurrentFrame();
  return (
    <div style={{ display: "flex", gap: size * 0.7, alignItems: "center", height: size * 2.2 }}>
      {[0, 1, 2].map((i) => {
        const p = (Math.sin((f - i * 5) / 4) + 1) / 2;
        return <div key={i} style={{ width: size, height: size, borderRadius: size, background: color, opacity: 0.35 + p * 0.65, transform: `translateY(${-p * size * 0.5}px)` }} />;
      })}
    </div>
  );
};

// Avatar: a colored disc with the person's initial, plus a small star badge for an agent.
export const Avatar: React.FC<{ letter: string; bg: string; size?: number }> = ({ letter, bg, size = 68 }) => (
  <div style={{ position: "relative", width: size, height: size, flex: "none" }}>
    <div style={{ width: size, height: size, borderRadius: size, background: bg, border: `3px solid ${T.ink}`, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: sans, fontWeight: 800, fontSize: size * 0.42, color: T.ink, boxSizing: "border-box" }}>
      {letter}
    </div>
    <div style={{ position: "absolute", right: -6, bottom: -6, width: size * 0.42, height: size * 0.42, borderRadius: size, background: T.ink, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Star size={size * 0.24} color="#fff" />
    </div>
  </div>
);

// Mouse pointer.
export const Pointer: React.FC<{ x: number; y: number; press?: number; opacity?: number }> = ({ x, y, press = 0, opacity = 1 }) => (
  <svg width={52} height={60} viewBox="0 0 26 30" style={{ position: "absolute", left: x, top: y, opacity, transform: `scale(${1 - press * 0.14})`, transformOrigin: "0 0", filter: "drop-shadow(0 6px 10px rgba(0,0,0,0.25))" }}>
    <path d="M2 2 L2 24 L8 18.5 L12 27.5 L16 25.8 L12 17 L20 17 Z" fill={T.ink} stroke="#fff" strokeWidth={2} strokeLinejoin="round" />
  </svg>
);

export const SceneFade: React.FC<{ dur: number; children: React.ReactNode; inF?: number; out?: number }> = ({ dur, children, inF = 8, out = 8 }) => {
  const f = useCurrentFrame();
  const a = inF ? interpolate(f, [0, inF], [0, 1], clamp) : 1;
  const b = out ? interpolate(f, [dur - out, dur], [1, 0], clamp) : 1;
  const o = Math.min(a, b);
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

export { T, hand, mono, sans };
