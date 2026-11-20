// Tokens from the rooms web page (rooms/web.py CSS, dark scheme), so the film looks like the product.
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

export const sans = loadInter("normal", { weights: ["400", "500", "600", "700", "800", "900"], subsets: ["latin"] }).fontFamily;
export const mono = loadMono("normal", { weights: ["400", "500", "700"], subsets: ["latin"] }).fontFamily;

export const T = {
  bg: "#0b0d11",
  bg2: "#0f1115",
  card: "#171a21",
  line: "#2a303a",
  text: "#e8eaed",
  muted: "#9aa1ab",
  accent: "#8ea2ff", // agents (rooms accent)
  accentDeep: "#3b5bdb",
  agent: "#1c2236",
  human: "#ffc078", // people (rooms warn text)
  good: "#69db7c",
  bad: "#ff8787",
};

export const FPS = 30;
export const BEAT = 18; // 100 BPM, matches make_audio.py
