# rooms launch video

Remotion source for `docs/demo/rooms-launch.mp4` (1920x1080, 52 s).

- `src/Launch.tsx`: the scenes, timings and the exact room messages quoted on screen.
- `src/parts.tsx`: stage, windows, phone, captions, message cards.
- `src/theme.ts`: colors from the rooms web page.
- `scripts/make_audio.py`: original music bed and sounds (from the product-demo-video film kit).

Footage (not committed, goes in `public/footage/`): `laptop.mp4` and `server.mp4` are the two Codex
terminals recorded with asciinema and rendered with agg; `phone.mp4` and `desk.mp4` are Maya's browser
recorded with Playwright. All from one live run on 2026-11-10, room "Crumb Bakery: checkout bug".

```
pnpm install
uv run --with numpy --with scipy python scripts/make_audio.py sfx --dir public/audio/sfx
uv run --with numpy --with scipy python scripts/make_audio.py bed --out public/audio/bed.wav --seconds 52.2 --drop 3.0 --outro 46.2 --key A
pnpm exec remotion render Launch out/raw.mp4 --codec=h264 --crf=18
```

Then master to -16 LUFS with a fixed gain and a -2 dBFS limiter (see the film kit's `render-films.sh`).
