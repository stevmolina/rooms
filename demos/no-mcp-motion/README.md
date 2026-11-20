# rooms join-without-MCP video

Remotion source for `docs/demo/no-mcp/rooms-no-mcp-motion.mp4` (1920x1080, 33 s). All motion design, no
screen recording, in the rooms site's look (dotted notebook paper, ink borders, pink highlighter, Caveat
wordmark).

- `src/NoMcp.tsx`: the four scenes (sentence and steps, Approve, the room, end card), timings, sounds.
- `src/parts.tsx`: paper stage, captions, highlighter, typing, handwritten notes, avatars, pointer.
- `src/theme.ts`: colors and fonts from `rooms/web.py`.
- `scripts/make_audio.py`: original music bed and UI sounds (from the product-demo-video film kit).

Real: the sentence, the invite steps' headings, the approval page copy, the code JFWR-BBSR and the
names (Alice #5c5c, Claude · Bob #ab65, "Launch checklist") from the 2026-11-10 run. Drawn: the room
conversation, tagged "Example chat".

```
pnpm install
uv run --with numpy --with scipy python scripts/make_audio.py sfx --dir public/audio/sfx
uv run --with numpy --with scipy python scripts/make_audio.py bed --out public/audio/bed.wav --seconds 33 --drop 9.0 --outro 27.6 --key D
pnpm exec remotion render NoMcp out/raw.mp4 --codec=h264 --crf=12 --x264-preset=slow
```

Then master to -16 LUFS with a fixed gain and a -2 dBFS limiter (the film kit's `render-films.sh`).
