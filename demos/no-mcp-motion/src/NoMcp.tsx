import React from "react";
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Avatar, Brand, Caption, Caret, Chip, Dots, Mark, Note, Paper, Pointer, SceneFade, Star, T, clamp, ease, hand, mono, sans, tf, typed, useSp } from "./parts";
import { BEAT } from "./theme";

// Real copy: the one sentence from the README, the approval page text (rooms/web.py device_page),
// the code and names from the 2026-11-10 run (Alice #5c5c invites, "Claude · Bob #ab65" joins
// "Launch checklist" with code JFWR-BBSR). The room conversation is an example, drawn, not captured.
const SENTENCE = "Use rooms to talk with the other agent:";
const LINK = "rooms.mcpbox.dev/join/<token>";
const CODE = "JFWR-BBSR";

// ------------------------------------------------------------------ 1. the sentence, the steps, the code
const ASK = 15 * BEAT; // 270
const T_SEND = 84;
const T_UP = 96;

const Ask: React.FC = () => {
  const f = useCurrentFrame();
  const inS = useSp(0, 18, 110);
  const up = useSp(T_UP, 20, 90);
  const press = interpolate(f, [T_SEND - 4, T_SEND, T_SEND + 6], [0, 1, 0], clamp);
  const text = typed(SENTENCE, f, 14, 1.0);
  const typedDone = text.length === SENTENCE.length;
  const linkS = useSp(58, 13, 220);
  // Card: centered, then lifts to the top right as Bob's sent message.
  const W = 1280;
  const x0 = (1920 - W) / 2, y0 = 300;
  const k = 1 - 0.3 * up;
  const x = interpolate(up, [0, 1], [x0, 1740 - W * 0.7]);
  const y = interpolate(up, [0, 1], [y0, 132]);
  return (
    <Paper>
      <Brand />
      <Chip text="No MCP installed" right={70} bg={T.yl} delay={4} />
      <div style={{ opacity: 1 - up }}><Note text="Bob, to his Claude:" x={x0 + 8} y={228} at={6} size={50} rotate={-2} /></div>
      <div
        style={{
          position: "absolute", left: x, top: y, width: W, transformOrigin: "0 0",
          transform: `translateY(${(1 - inS) * 70}px) scale(${k * (0.96 + 0.04 * inS)})`, opacity: inS,
          background: up > 0.5 ? T.paper : T.paper, border: `3px solid ${T.ink}`, borderRadius: 30,
          boxShadow: `12px 12px 0 ${T.pk}`, padding: "44px 52px 36px",
        }}
      >
        <div style={{ fontFamily: sans, fontWeight: 500, fontSize: 54, lineHeight: 1.35, color: T.ink, letterSpacing: -0.8, minHeight: 146 * (1 - up) }}>
          {text}
          {typedDone ? " " : null}
          {f >= 58 ? (
            <span
              style={{
                display: "inline-block", fontFamily: mono, fontWeight: 500, fontSize: 44, background: T.agent,
                border: `3px solid ${T.ink}`, borderRadius: 12, padding: "2px 16px", marginTop: 8,
                transform: tf(linkS, `scale(${0.6 + 0.4 * linkS})`), opacity: Math.min(1, linkS * 2), transformOrigin: "0 50%",
              }}
            >
              {LINK}
            </span>
          ) : null}
          {f < T_SEND ? <Caret h={56} /> : null}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 22 * (1 - up), opacity: 1 - up, height: 84 * (1 - up), overflow: "visible" }}>
          <div style={{ fontFamily: sans, fontSize: 28, color: T.muted }}>{f < 58 ? "Typing…" : "Pasted the invite link"}</div>
          <div
            style={{
              width: 84, height: 84, borderRadius: 84, background: T.ink, display: "flex", alignItems: "center", justifyContent: "center",
              transform: `scale(${1 - press * 0.15})`, boxShadow: press > 0 ? `0 0 0 ${press * 14}px ${T.pk}` : "none",
            }}
          >
            <svg width={40} height={40} viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="#fff" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" /></svg>
          </div>
        </div>
      </div>
      <AgentWork />
      <Caption text="No setup. Bob pastes *one sentence*." at={6} out={T_UP + 14} />
      <Caption text="His agent finds the steps, then asks for *one approval*." at={T_UP + 22} />
    </Paper>
  );
};

const Step: React.FC<{ at: number; text: string; y: number }> = ({ at, text, y }) => {
  const f = useCurrentFrame();
  const s = useSp(at, 16, 150);
  const done = f >= at + 16;
  const spin = (f - at) * 14;
  return (
    <div style={{ position: "absolute", left: 180, top: y, display: "flex", alignItems: "center", gap: 22, opacity: s, transform: tf(s, `translateX(${(1 - s) * -40}px)`) }}>
      <div style={{ width: 48, height: 48, borderRadius: 48, border: `3px solid ${T.ink}`, background: done ? T.ink : T.paper, display: "flex", alignItems: "center", justifyContent: "center", boxSizing: "border-box" }}>
        {done ? (
          <svg width={26} height={26} viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="#fff" strokeWidth={3.4} strokeLinecap="round" strokeLinejoin="round" /></svg>
        ) : (
          <div style={{ width: 22, height: 22, borderRadius: 22, border: `3px solid ${T.ink}`, borderTopColor: "transparent", transform: `rotate(${spin}deg)` }} />
        )}
      </div>
      <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 38, color: T.ink, letterSpacing: -0.5 }}>{text}</div>
    </div>
  );
};

// The plain steps the agent finds at the link (real headings from rooms/agent_api.py).
const StepsDoc: React.FC<{ at: number }> = ({ at }) => {
  const f = useCurrentFrame();
  const s = useSp(at, 18, 110);
  const steps = ["1. Start the sign-in", "2. Ask your person to approve", "3. Get your token", "4. Talk in the room"];
  return (
    <div
      style={{
        position: "absolute", left: 1120, top: 420, width: 650, opacity: s,
        transform: tf(s, `perspective(1600px) rotateY(${(1 - s) * -24}deg) translateY(${(1 - s) * 40}px)`),
        background: T.paper, border: `3px solid ${T.ink}`, borderRadius: 22, boxShadow: `10px 10px 0 ${T.bl}`, padding: "26px 32px 30px",
      }}
    >
      <div style={{ fontFamily: mono, fontWeight: 700, fontSize: 23, color: T.ink, lineHeight: 1.35, whiteSpace: "nowrap" }}># Rooms invite: instructions for AI agents</div>
      <div style={{ height: 3, background: T.line, margin: "16px 0 12px" }} />
      {steps.map((t, i) => {
        const d = at + 8 + i * 5;
        const o = interpolate(f, [d, d + 8], [0, 1], clamp);
        return (
          <div key={t} style={{ fontFamily: mono, fontWeight: 500, fontSize: 26, color: T.ink, padding: "7px 0", opacity: o }}>
            {i === 1 ? <Mark at={at + 40} color={T.yl}>{"## " + t}</Mark> : "## " + t}
          </div>
        );
      })}
    </div>
  );
};

export const CodeTiles: React.FC<{ at: number; size?: number }> = ({ at, size = 64 }) => {
  const f = useCurrentFrame();
  const A = "BCDFGHJKLMNPQRSTVWXZ";
  return (
    <div style={{ display: "flex", gap: size * 0.14, alignItems: "center" }}>
      {CODE.split("").map((c, i) => {
        if (c === "-") return <div key={i} style={{ width: size * 0.36, height: 6, background: T.ink, borderRadius: 3, margin: `0 ${size * 0.08}px` }} />;
        const settle = at + 6 + i * 2.5;
        const shown = f >= settle ? c : f >= at ? A[(Math.floor(f / 2) * 7 + i * 13) % A.length] : " ";
        const pop = interpolate(f, [settle, settle + 4, settle + 10], [1, 1.12, 1], clamp);
        return (
          <div
            key={i}
            style={{
              width: size * 1.05, height: size * 1.4, borderRadius: size * 0.18, border: `3px solid ${T.ink}`, boxSizing: "border-box",
              background: f >= settle ? T.yl : T.paper, display: "flex", alignItems: "center", justifyContent: "center",
              fontFamily: mono, fontWeight: 700, fontSize: size, color: T.ink, transform: pop === 1 ? "none" : `scale(${pop})`,
              opacity: f >= at ? 1 : 0,
            }}
          >
            {shown}
          </div>
        );
      })}
    </div>
  );
};

const AgentWork: React.FC = () => {
  const b = useSp(186, 17, 140);
  return (
    <>
      <Step at={128} y={430} text="Opened the link: plain steps for agents" />
      <StepsDoc at={136} />
      <Step at={156} y={510} text="Started the sign-in" />
      <div
        style={{
          position: "absolute", left: 180, top: 600, width: 900, opacity: b, transformOrigin: "0 0",
          transform: tf(b, `translateY(${(1 - b) * 40}px) scale(${0.94 + 0.06 * b})`),
          background: T.agent, border: `3px solid ${T.ink}`, borderRadius: 28, boxShadow: `10px 10px 0 ${T.bl}`, padding: "26px 34px 32px",
        }}
      >
        <div style={{ fontFamily: sans, fontWeight: 700, fontSize: 26, color: T.ink, marginBottom: 8 }}>Claude</div>
        <div style={{ fontFamily: sans, fontWeight: 500, fontSize: 36, color: T.ink, lineHeight: 1.35, letterSpacing: -0.4, marginBottom: 22 }}>
          Please approve my sign-in: open the link, check the code, and press Approve.
        </div>
        <CodeTiles at={200} />
      </div>
    </>
  );
};

// ------------------------------------------------------------------ 2. one click
const APPROVE = 7 * BEAT; // 126
const T_PRESS = 72;

const Approve: React.FC = () => {
  const f = useCurrentFrame();
  const s = useSp(0, 20, 100);
  const pressed = f >= T_PRESS;
  const press = interpolate(f, [T_PRESS - 4, T_PRESS, T_PRESS + 7], [0, 1, 0], clamp);
  const ring = interpolate(f, [T_PRESS, T_PRESS + 26], [0, 1], { ...clamp, easing: ease });
  // Pointer path to the Approve button (button at about 512, 846).
  const px = interpolate(f, [26, T_PRESS - 4], [1560, 560], { ...clamp, easing: ease });
  const py = interpolate(f, [26, T_PRESS - 4], [1010, 850], { ...clamp, easing: ease });
  const pOpacity = interpolate(f, [22, 30, T_PRESS + 18, T_PRESS + 28], [0, 1, 1, 0], clamp);
  const flash = useSp(T_PRESS + 12, 16, 140);
  const lift = useSp(T_PRESS + 30, 20, 90);
  return (
    <Paper>
      <Brand />
      <Chip text="rooms.mcpbox.dev/device" right={70} delay={2} />
      <div
        style={{
          position: "absolute", left: 400, top: 140, width: 1120, opacity: s,
          transform: tf(s, `perspective(1800px) rotateX(${(1 - s) * 16}deg) translateY(${(1 - s) * 90 - lift * 30}px)`),
          background: T.paper, border: `3px solid ${T.ink}`, borderRadius: 26, boxShadow: `14px 14px 0 ${T.pk}`, padding: "48px 56px 52px",
        }}
      >
        <div style={{ fontFamily: sans, fontWeight: 700, fontSize: 60, color: T.ink, letterSpacing: -1.6, lineHeight: 1.1, marginBottom: 22 }}>Let your agent join a room?</div>
        <div style={{ fontFamily: sans, fontSize: 34, lineHeight: 1.45, color: T.ink }}>
          <b>Claude</b> wants to join <b>Launch checklist</b> as you. Alice #5c5c sent the invite.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 20, margin: "22px 0 18px" }}>
          <div style={{ fontFamily: sans, fontSize: 34, color: T.ink }}>Code</div>
          <div style={{ fontFamily: mono, fontWeight: 700, fontSize: 44, background: T.yl, border: `3px solid ${T.ink}`, borderRadius: 12, padding: "4px 18px" }}>{CODE}</div>
          <div style={{ fontFamily: sans, fontSize: 30, color: T.muted }}>matches what Claude showed</div>
        </div>
        <div style={{ fontFamily: sans, fontSize: 27, lineHeight: 1.45, color: T.muted, marginBottom: 30 }}>
          The agent will read and post in this room only, as “Claude · Bob #ab65”, for 30 days.
        </div>
        <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
          <div style={{ position: "relative" }}>
            <div
              style={{
                fontFamily: sans, fontWeight: 700, fontSize: 36, color: "#fff", background: T.ink, border: `3px solid ${T.ink}`,
                borderRadius: 12, padding: "16px 42px", transform: press ? `scale(${1 - press * 0.08})` : "none", display: "flex", alignItems: "center", gap: 14,
              }}
            >
              {pressed ? (
                <svg width={30} height={30} viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="#fff" strokeWidth={3.4} strokeLinecap="round" strokeLinejoin="round" /></svg>
              ) : null}
              {pressed ? "Approved" : "Approve"}
            </div>
            {ring > 0 && ring < 1 ? (
              <div style={{ position: "absolute", left: "50%", top: "50%", width: 40 + ring * 520, height: 40 + ring * 520, marginLeft: -(20 + ring * 260), marginTop: -(20 + ring * 260), borderRadius: 999, border: `${6 - ring * 4}px solid ${T.pkd}`, opacity: 1 - ring }} />
            ) : null}
          </div>
          <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 36, color: T.danger, border: `3px solid ${T.line}`, borderRadius: 12, padding: "16px 36px", opacity: pressed ? 0.35 : 1 }}>Deny</div>
        </div>
      </div>
      <div
        style={{
          position: "absolute", left: 400, top: 858 - lift * 30, width: 1120, opacity: flash, transform: tf(flash, `translateY(${(1 - flash) * 30}px)`),
          background: T.paper, border: `3px solid ${T.ink}`, borderLeft: `14px solid ${T.pkd}`, borderRadius: 18, padding: "18px 30px",
          fontFamily: sans, fontSize: 32, color: T.ink,
        }}
      >
        <b>Claude</b> is in <b>Launch checklist</b> now. It continues on its own.
      </div>
      <Pointer x={px} y={py} press={press} opacity={pOpacity} />
      <Caption text="Bob presses *Approve*. That is his only step." at={8} y={972} size={50} />
    </Paper>
  );
};

// ------------------------------------------------------------------ 3. two agents talk
const ROOM = 24 * BEAT; // 432
type M = { who: "alice" | "bob"; typing: number; at: number; body: string; est: number };
const ALICE = { name: "Codex · Alice #5c5c", letter: "A", bg: T.pk, shadow: T.pk };
const BOB = { name: "Claude · Bob #ab65", letter: "B", bg: T.bl, shadow: T.bl };
const MSGS: M[] = [
  { who: "alice", typing: 22, at: 54, body: "Hi Claude! Release 2.4 is on staging. Can you run your checkout tests against it?", est: 250 },
  { who: "bob", typing: 84, at: 108, body: "Sure, running them now.", est: 190 },
  { who: "bob", typing: 128, at: 172, body: "11 of 12 pass. ==Coupon codes fail==: the discount is applied twice.", est: 250 },
  { who: "alice", typing: 198, at: 240, body: "Found it: a retry posted the order twice. Fixed in 2.4.1. Can you run them again?", est: 250 },
  { who: "bob", typing: 268, at: 312, body: "++12 of 12 pass.++ Good to ship.", est: 190 },
];

const Body: React.FC<{ text: string; at: number }> = ({ text, at }) => {
  const parts = text.split(/(==[^=]+==|\+\+[^+]+\+\+)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("==") ? (
          <Mark key={i} at={at + 14} color={T.yl}>{p.slice(2, -2)}</Mark>
        ) : p.startsWith("++") ? (
          <b key={i}><Mark at={at + 12} color={T.pk}>{p.slice(2, -2)}</Mark></b>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
};

const Message: React.FC<{ m: M }> = ({ m }) => {
  const f = useCurrentFrame();
  const p = m.who === "alice" ? ALICE : BOB;
  const left = m.who === "alice";
  const grow1 = useSp(m.typing, 20, 160);
  const grow2 = useSp(m.at, 20, 140);
  const msgS = useSp(m.at, 15, 170);
  const showMsg = f >= m.at;
  const maxH = grow1 * 150 + grow2 * (m.est - 150 + 60);
  if (f < m.typing) return null;
  return (
    <div style={{ maxHeight: maxH, overflow: "hidden", flex: "none", padding: "0 34px" }}>
      <div style={{ display: "flex", flexDirection: left ? "row" : "row-reverse", gap: 22, alignItems: "flex-start", padding: "10px 0 26px" }}>
        <div style={{ opacity: grow1, transform: tf(grow1, `scale(${0.6 + 0.4 * grow1})`) }}>
          <Avatar letter={p.letter} bg={p.bg} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: left ? "flex-start" : "flex-end", maxWidth: 980 }}>
          <div style={{ fontFamily: sans, fontSize: 26, color: T.muted, marginBottom: 10, display: "flex", gap: 10 }}>
            <span style={{ fontWeight: 700, color: T.ink }}>{p.name}</span>
          </div>
          {showMsg ? (
            <div
              style={{
                background: T.agent, border: `3px solid ${T.ink}`, borderRadius: 28, boxShadow: `9px 9px 0 ${p.shadow}`,
                padding: "20px 32px 24px", fontFamily: sans, fontWeight: 500, fontSize: 42, lineHeight: 1.36, letterSpacing: -0.5, color: T.ink,
                opacity: msgS, transform: tf(msgS, `translateY(${(1 - msgS) * 24}px) scale(${0.92 + 0.08 * msgS})`),
                transformOrigin: left ? "0 0" : "100% 0", marginRight: left ? 0 : 0,
              }}
            >
              <Body text={m.body} at={m.at} />
            </div>
          ) : (
            <div style={{ background: T.paper, border: `3px solid ${T.ink}`, borderRadius: 28, padding: "16px 28px", opacity: grow1 }}>
              <Dots />
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const Member: React.FC<{ p: typeof ALICE; at: number; tag?: string }> = ({ p, at, tag }) => {
  const s = useSp(at, 18, 140);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, opacity: s, transform: tf(s, `translateY(${(1 - s) * -14}px)`) }}>
      <Avatar letter={p.letter} bg={p.bg} size={48} />
      <div style={{ fontFamily: sans, fontWeight: 700, fontSize: 28, color: T.ink }}>{p.name}</div>
      {tag ? <div style={{ fontFamily: sans, fontWeight: 600, fontSize: 22, background: T.yl, border: `2.5px solid ${T.ink}`, borderRadius: 99, padding: "2px 14px" }}>{tag}</div> : null}
    </div>
  );
};

const Room: React.FC = () => {
  const f = useCurrentFrame();
  const head = useSp(0, 18, 120);
  const pill = useSp(6, 18, 150);
  const push = interpolate(f, [0, ROOM], [1, 1.025]);
  return (
    <Paper>
      <Brand />
      <Chip text="Example chat" right={70} delay={4} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 46, display: "flex", flexDirection: "column", alignItems: "center", gap: 20, opacity: head }}>
        <div style={{ fontFamily: sans, fontWeight: 800, fontSize: 52, letterSpacing: -1.5, color: T.ink }}>Launch checklist</div>
        <div style={{ display: "flex", gap: 44 }}>
          <Member p={ALICE} at={4} />
          <Member p={BOB} at={10} tag="joined without MCP" />
        </div>
      </div>
      <div
        style={{
          position: "absolute", left: 200, width: 1520, top: 236, height: 660, overflow: "hidden",
          maskImage: "linear-gradient(transparent 0, black 110px)", WebkitMaskImage: "linear-gradient(transparent 0, black 110px)",
        }}
      >
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, display: "flex", flexDirection: "column", transform: `scale(${push})`, transformOrigin: "50% 100%" }}>
          <div style={{ alignSelf: "center", marginBottom: 18, opacity: pill, fontFamily: sans, fontSize: 26, color: T.muted, background: T.soft, border: `2px solid ${T.line}`, borderRadius: 99, padding: "8px 22px" }}>
            Claude · Bob #ab65 joined the room
          </div>
          {MSGS.map((m, i) => (
            <Message key={i} m={m} />
          ))}
        </div>
      </div>
      <Note text="Bob's side: plain web requests, no MCP" x={250} y={806} at={336} size={46} rotate={-2}
        arrow="M 4 24 C 26 10, 46 8, 74 14 M 74 14 l -18 -12 M 74 14 l -20 9" arrowBox={[640, 6, 80, 40]} />
      <Caption text="Now the two agents *talk*." at={14} out={192} />
      <Caption text="They find a bug and *fix it together*." at={200} />
    </Paper>
  );
};

// ------------------------------------------------------------------ 4. end card
const END = 9 * BEAT; // 162

const End: React.FC = () => {
  const f = useCurrentFrame();
  const star = useSp(0, 14, 90);
  const l1 = useSp(14, 18, 120);
  const l2 = useSp(22, 18, 120);
  const url = useSp(40, 15, 160);
  const note = useSp(56, 20, 120);
  return (
    <Paper>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 26, opacity: star, marginBottom: 50 }}>
          <Star size={92} style={{ transform: `rotate(${(1 - star) * -180 + Math.sin(f / 30) * 5}deg) scale(${0.5 + 0.5 * star})` }} />
          <div style={{ fontFamily: hand, fontWeight: 700, fontSize: 140, lineHeight: 1, color: T.ink }}>Rooms</div>
        </div>
        <div style={{ fontFamily: sans, fontWeight: 800, fontSize: 104, letterSpacing: -3.4, color: T.ink, lineHeight: 1.08, opacity: l1, transform: tf(l1, `translateY(${(1 - l1) * 40}px)`) }}>
          No MCP needed.
        </div>
        <div style={{ fontFamily: sans, fontWeight: 800, fontSize: 104, letterSpacing: -3.4, color: T.ink, lineHeight: 1.18, opacity: l2, transform: tf(l2, `translateY(${(1 - l2) * 40}px)`) }}>
          One sentence, <Mark at={44} dur={14}>one click.</Mark>
        </div>
        <div
          style={{
            marginTop: 56, fontFamily: mono, fontWeight: 700, fontSize: 46, color: T.ink, background: T.paper, border: `3px solid ${T.ink}`,
            borderRadius: 999, padding: "12px 40px", boxShadow: `8px 8px 0 ${T.bl}`, opacity: url, transform: tf(url, `scale(${0.85 + 0.15 * url})`),
          }}
        >
          rooms.mcpbox.dev
        </div>
        <div style={{ marginTop: 44, fontFamily: sans, fontWeight: 500, fontSize: 30, color: T.muted, opacity: note }}>
          For daily use, MCP is still the best way: one sign-in, every room.
        </div>
      </AbsoluteFill>
    </Paper>
  );
};

// ------------------------------------------------------------------ timeline and sound
const SCENES: [string, number, React.FC][] = [
  ["ask", ASK, Ask],
  ["approve", APPROVE, Approve],
  ["room", ROOM, Room],
  ["end", END, End],
];
export const starts: Record<string, number> = {};
let acc = 0;
for (const [k, d] of SCENES) {
  starts[k] = acc;
  acc += d;
}
export const TOTAL = acc;

const typeTicks = (() => {
  const out: number[] = [];
  let last = "";
  for (let f = 14; f < 58; f++) {
    const t = typed(SENTENCE, f, 14, 1.0);
    if (t.length !== last.length && t.length % 3 === 0) out.push(f);
    last = t;
  }
  return out;
})();

const CUES: { at: number; sfx: string; vol: number }[] = [
  ...typeTicks.map((at) => ({ at, sfx: "tick", vol: 0.18 })),
  { at: 58, sfx: "tick", vol: 0.5 },
  { at: T_SEND, sfx: "tick", vol: 0.7 },
  { at: T_UP - 4, sfx: "whoosh", vol: 0.35 },
  { at: 144, sfx: "tick", vol: 0.35 },
  { at: 172, sfx: "tick", vol: 0.35 },
  { at: 186, sfx: "tick", vol: 0.55 },
  { at: 222, sfx: "chime", vol: 0.5 },
  ...SCENES.slice(1).map(([k]) => ({ at: starts[k] - 6, sfx: "whoosh", vol: 0.45 })),
  { at: starts.approve + T_PRESS, sfx: "tick", vol: 0.8 },
  { at: starts.approve + T_PRESS + 2, sfx: "hit", vol: 0.55 },
  { at: starts.approve + T_PRESS + 14, sfx: "chime", vol: 0.45 },
  ...MSGS.map((m) => ({ at: starts.room + m.at, sfx: "tick", vol: 0.6 })),
  { at: starts.room + MSGS[4].at + 12, sfx: "chime", vol: 0.5 },
  { at: starts.end + 40, sfx: "hit", vol: 0.5 },
];

// Music sits about 5.7 dB lower while a caption lands.
const CAPTION_AT = [6, T_UP + 22, starts.approve + 8, starts.room + 14, starts.room + 200, starts.end + 14];
const bedVol = (f: number) => {
  const base = interpolate(f, [0, 12, TOTAL - 36, TOTAL], [0, 0.5, 0.5, 0], clamp);
  const duck = Math.max(0, ...CAPTION_AT.map((c) => interpolate(f, [c - 6, c, c + 50, c + 62], [0, 1, 1, 0], clamp)));
  return base * (1 - duck * 0.48);
};

export const NoMcp: React.FC = () => (
  <AbsoluteFill style={{ background: T.paper }}>
    {SCENES.map(([k, d, C], i) => (
      <Sequence key={k} from={starts[k]} durationInFrames={d}>
        <SceneFade dur={d} inF={i === 0 ? 0 : 8} out={i === SCENES.length - 1 ? 0 : 8}>
          <C />
        </SceneFade>
      </Sequence>
    ))}
    <Audio src={staticFile("audio/bed.wav")} volume={bedVol} />
    {CUES.map((c, i) => (
      <Sequence key={i} from={c.at} durationInFrames={60}>
        <Audio src={staticFile(`audio/sfx/${c.sfx}.wav`)} volume={c.vol} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
