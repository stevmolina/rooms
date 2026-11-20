// Tokens from the rooms web page (rooms/web.py CSS, light scheme): dotted notebook paper, ink borders,
// pink highlighter, yellow notice, blue agent bubbles. Inter + Caveat + JetBrains Mono.
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadCaveat } from "@remotion/google-fonts/Caveat";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

export const sans = loadInter("normal", { weights: ["400", "500", "600", "700", "800"], subsets: ["latin"] }).fontFamily;
export const hand = loadCaveat("normal", { weights: ["500", "700"], subsets: ["latin"] }).fontFamily;
export const mono = loadMono("normal", { weights: ["500", "700"], subsets: ["latin"] }).fontFamily;

export const T = {
  ink: "#111111",
  paper: "#ffffff",
  dot: "#d4d4d4",
  muted: "#5c5c5c",
  soft: "#f3f3f3",
  line: "#e2e2e2",
  pk: "#ffc6ec", // highlighter pink
  pkd: "#f06bc4",
  yl: "#ffe58f", // notice yellow
  yld: "#e9c24a",
  bl: "#a6dcff",
  agent: "#eaf6ff", // agent message bubble
  good: "#2f9e44",
  danger: "#c92a2a",
};

export const FPS = 30;
export const BEAT = 18; // 100 BPM, matches make_audio.py
