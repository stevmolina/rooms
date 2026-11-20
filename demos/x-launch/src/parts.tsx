import React from "react";
import { AbsoluteFill, Easing, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { T, mono, sans } from "./theme";

export const ease = Easing.bezier(0.22, 1, 0.36, 1);

export const useSpring = (delay = 0, damping = 18, stiffness = 120) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping, stiffness, mass: 0.9 } });
};

// Dark stage with a slow indigo and amber glow and a faint dot grid.
export const Stage: React.FC<{ children?: React.ReactNode; glow?: number }> = ({ children, glow = 1 }) => {
  const f = useCurrentFrame();
  const a = Math.sin(f / 90) * 60;
  const b = Math.cos(f / 110) * 50;
  return (
    <AbsoluteFill style={{ background: T.bg, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          opacity: glow,
          background: `radial-gradient(900px 600px at ${25 + a / 20}% ${30 + b / 25}%, rgba(142,162,255,0.18), transparent 70%),
            radial-gradient(800px 600px at ${78 - a / 25}% ${75 - b / 20}%, rgba(255,192,120,0.10), transparent 70%)`,
        }}
      />
      <AbsoluteFill
        style={{
          backgroundImage: "radial-gradient(rgba(255,255,255,0.06) 1.2px, transparent 1.2px)",
          backgroundSize: "28px 28px",
          maskImage: "radial-gradient(ellipse at center, black 30%, transparent 80%)",
        }}
      />
      {children}
    </AbsoluteFill>
  );
};

// Real footage, trimmed and sped, with a simple camera (scale around a focus point in footage px).
export type Cam = { at: number; scale: number; fx: number; fy: number };
export const Footage: React.FC<{
  src: string;
  from: number; // seconds into the source
  rate?: number;
  w: number; // source size
  h: number;
  cams?: Cam[]; // keyed by scene frame
}> = ({ src, from, rate = 1, w, h, cams }) => {
  const f = useCurrentFrame();
  let scale = 1, fx = w / 2, fy = h / 2;
  if (cams && cams.length) {
    const ats = cams.map((c) => c.at);
    const pick = (k: keyof Cam) =>
      cams.length === 1 ? (cams[0][k] as number) : interpolate(f, ats, cams.map((c) => c[k] as number), { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
    scale = pick("scale"); fx = pick("fx"); fy = pick("fy");
  }
  // Keep the focus point centered, but never show past the footage edges.
  const tx = Math.min(0, Math.max(w - w * scale, w / 2 - fx * scale));
  const ty = Math.min(0, Math.max(h - h * scale, h / 2 - fy * scale));
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
      <div style={{ width: w, height: h, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${scale})` }}>
        <OffthreadVideo src={staticFile(src)} trimBefore={Math.round(from * 30)} playbackRate={rate} muted style={{ width: w, height: h, display: "block" }} />
      </div>
    </div>
  );
};

// A desktop window around footage. Content is drawn at footage size and scaled to `width`.
export const Window: React.FC<{
  title: string;
  sub?: string;
  x: number;
  y: number;
  width: number;
  srcW: number;
  srcH: number;
  delay?: number;
  tilt?: number;
  tag?: string;
  dim?: number;
  children: React.ReactNode;
}> = ({ title, sub, x, y, width, srcW, srcH, delay = 0, tilt = 0, tag, dim = 0, children }) => {
  const s = useSpring(delay, 20, 110);
  const k = width / srcW;
  const bar = 46;
  return (
    <div
      style={{
        position: "absolute", left: x, top: y, width, height: srcH * k + bar,
        transform: `perspective(1800px) translateY(${(1 - s) * 60}px) rotateY(${tilt * (1 - s * 0.6)}deg) scale(${0.94 + 0.06 * s})`,
        opacity: s, borderRadius: 16, overflow: "hidden", background: "#12151b",
        boxShadow: "0 40px 90px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.08)",
      }}
    >
      <div style={{ height: bar, display: "flex", alignItems: "center", gap: 10, padding: "0 16px", background: "#1a1e26", borderBottom: `1px solid ${T.line}` }}>
        {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
          <div key={c} style={{ width: 13, height: 13, borderRadius: 7, background: c }} />
        ))}
        <div style={{ marginLeft: 10, fontFamily: sans, fontWeight: 700, fontSize: 21, color: T.text }}>{title}</div>
        {sub ? <div style={{ fontFamily: sans, fontSize: 19, color: T.muted }}>{sub}</div> : null}
        <div style={{ flex: 1 }} />
        {tag ? <SpeedTag text={tag} /> : null}
      </div>
      <div style={{ position: "relative", width: srcW, height: srcH, transform: `scale(${k})`, transformOrigin: "0 0" }}>{children}</div>
      <AbsoluteFill style={{ background: "#000", opacity: dim, pointerEvents: "none" }} />
    </div>
  );
};

export const SpeedTag: React.FC<{ text: string }> = ({ text }) => (
  <div style={{ fontFamily: mono, fontSize: 17, fontWeight: 700, color: T.bg, background: T.human, borderRadius: 6, padding: "3px 9px" }}>{text}</div>
);

// A phone around the 430x932 phone footage.
export const Phone: React.FC<{ x: number; y: number; height: number; delay?: number; label?: string; children: React.ReactNode; dim?: number }> = ({ x, y, height, delay = 0, children, dim = 0 }) => {
  const s = useSpring(delay, 20, 110);
  const k = (height - 28) / 932;
  const w = 430 * k + 28;
  return (
    <div
      style={{
        position: "absolute", left: x, top: y, width: w, height,
        transform: `translateY(${(1 - s) * 80}px) scale(${0.92 + 0.08 * s})`, opacity: s,
        borderRadius: 54 * (height / 900), background: "#05060a", padding: 14,
        boxShadow: "0 40px 90px rgba(0,0,0,0.6), 0 0 0 2px #2b2f38, inset 0 0 0 1px #3a3f4a",
      }}
    >
      <div style={{ position: "relative", width: 430 * k, height: 932 * k, borderRadius: 42 * (height / 900), overflow: "hidden", background: "#f6f7f9" }}>
        <div style={{ position: "absolute", width: 430, height: 932, transform: `scale(${k})`, transformOrigin: "0 0" }}>{children}</div>
        <AbsoluteFill style={{ background: "#000", opacity: dim }} />
      </div>
    </div>
  );
};

// Big caption at the bottom. *word* is drawn in the accent color.
export const Caption: React.FC<{ text: string; delay?: number; color?: string; y?: number; size?: number }> = ({ text, delay = 4, color = T.accent, y = 950, size = 62 }) => {
  const f = useCurrentFrame();
  const parts = text.split(/(\*[^*]+\*)/g).filter(Boolean);
  let i = 0;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: y, display: "flex", justifyContent: "center" }}>
      <div style={{ fontFamily: sans, fontWeight: 800, fontSize: size, letterSpacing: -1.2, color: T.text, textAlign: "center", textShadow: "0 4px 30px rgba(0,0,0,0.8)", whiteSpace: "nowrap" }}>
        {parts.map((p, pi) => {
          const hi = p.startsWith("*");
          const words = (hi ? p.slice(1, -1) : p).split(/(\s+)/);
          return words.map((wd, wi) => {
            if (/^\s+$/.test(wd)) return <span key={`${pi}-${wi}`}>{wd}</span>;
            const d = delay + i++ * 2.2;
            const o = interpolate(f, [d, d + 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
            const ty = interpolate(f, [d, d + 10], [18, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
            return (
              <span key={`${pi}-${wi}`} style={{ display: "inline-block", opacity: o, transform: `translateY(${ty}px)`, color: hi ? color : T.text }}>
                {wd}
              </span>
            );
          });
        })}
      </div>
    </div>
  );
};

// A message card in the rooms style, quoting the real message. ==text== is highlighted.
export const Msg: React.FC<{
  author: string;
  id: number;
  at: string;
  body: string;
  human?: boolean;
  x: number;
  y: number;
  width: number;
  delay: number;
  size?: number;
}> = ({ author, id, at, body, human, x, y, width, delay, size = 34 }) => {
  const s = useSpring(delay, 16, 150);
  const f = useCurrentFrame();
  const parts = body.split(/(==[^=]+==)/g).filter(Boolean);
  const hl = interpolate(f, [delay + 14, delay + 30], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const edge = human ? T.human : T.accent;
  return (
    <div
      style={{
        position: "absolute", left: x, top: y, width, opacity: s,
        transform: `translateY(${(1 - s) * 40}px) scale(${0.9 + 0.1 * s})`, transformOrigin: "50% 100%",
        background: human ? "#221c14" : T.agent, borderRadius: 22, padding: "22px 28px 26px",
        border: `2px solid ${edge}55`, boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 60px ${edge}22`,
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", fontFamily: sans, fontSize: 22, color: T.muted, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, color: edge }}>{author}</span>
        <span>#{id}</span>
        <span>{at}</span>
      </div>
      <div style={{ fontFamily: sans, fontWeight: 500, fontSize: size, lineHeight: 1.32, color: T.text, letterSpacing: -0.3 }}>
        {parts.map((p, i) =>
          p.startsWith("==") ? (
            <span key={i} style={{ background: `linear-gradient(transparent 8%, ${edge}${Math.round(hl * 70).toString(16).padStart(2, "0")} 8%, ${edge}${Math.round(hl * 70).toString(16).padStart(2, "0")} 92%, transparent 92%)`, borderRadius: 6, fontWeight: 700 }}>
              {p.slice(2, -2)}
            </span>
          ) : (
            <span key={i}>{p}</span>
          ),
        )}
      </div>
    </div>
  );
};

export const Chip: React.FC<{ text: string; x: number; y: number; color?: string; delay?: number; size?: number }> = ({ text, x, y, color = T.muted, delay = 0, size = 20 }) => {
  const s = useSpring(delay);
  return (
    <div style={{ position: "absolute", left: x, top: y, opacity: s, fontFamily: sans, fontWeight: 600, fontSize: size, color, padding: "6px 14px", borderRadius: 999, border: `1px solid ${T.line}`, background: "rgba(23,26,33,0.85)", whiteSpace: "nowrap" }}>
      {text}
    </div>
  );
};

// Fade a whole scene in and out.
export const SceneFade: React.FC<{ dur: number; children: React.ReactNode; out?: number }> = ({ dur, children, out = 8 }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 6, dur - out, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};
