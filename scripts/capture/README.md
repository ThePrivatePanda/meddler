# README media capture

`capture.mjs` drives the real UI through the [60-second demo](../../docs/demo.md) in headless
Chrome and writes the screenshots and GIF used by the README into `docs/media/`:

| File | Shows |
|---|---|
| `hero.png` | the live world with a crisis headline selected in the inspector |
| `01-watch.png` | the world running on its own |
| `02-inspect.png` | a crisis selected: its causes and effects in the inspector |
| `03-trace.png` | the causal trace (`t`), root cause at the top |
| `04-scrub.png` | scrubbed back in time, viewing the past |
| `05-fork.png` | a god intervention forked a second timeline, shown side by side |
| `06-dossier.png` | the country dossier with full-history charts (`d`) |
| `07-annals.png` | the Annals (`h`) |
| `tour.gif` | all of the above as a looping clip, about 20 seconds |

## Requirements

- Node.js 20 or newer.
- Google Chrome or Chromium. `playwright-core` does not download a browser; the script looks in
  the usual install locations, or pass `--chrome /path/to/chrome` (or set `CHROME_PATH`).
- `ffmpeg` on your `PATH` for the GIF (or set `FFMPEG=/path/to/ffmpeg`), built with the `gif`
  encoder and the `palettegen`/`paletteuse` filters, as distribution packages are. The minimal
  ffmpeg that Playwright downloads for its own video recording lacks these and will not work. Use
  `--no-gif` to write only the screenshots.

## Usage

Start a server with the canonical seed in one terminal, then run the capture in another:

```sh
meddler serve --seed 1337
```

```sh
cd scripts/capture
npm ci
node capture.mjs                                  # against http://127.0.0.1:7677/
node capture.mjs --url http://127.0.0.1:7690/     # a server on another port
```

Use a freshly started server: the tour expects a single timeline with no forks yet.

The script waits for real UI state at every step (connection, a crisis headline, the trace, the
scrub banner, the fork tabs, the dossier charts, the Annals), not for fixed delays. It runs the
world at 4× until it has some history (tick 150 by default) and a crisis headline is on screen,
then tours at 1×. The whole browser session is recorded, and
the GIF is cut from the recording around each step, so time spent waiting on the server is left
out. The clip is capped at 20 seconds, and if it comes out larger than 8 MB it is re-encoded
smaller.

It exits non-zero if any step times out, the page logs a console error, or a request fails
(a missing `/favicon.ico` is only a warning). Look at every image
before committing it.

Other options: `--out DIR`, `--width`/`--height` (viewport, default 1440×900), `--gif-width`
(default 960), `--gif-fps` (default 12), `--warmup TICKS` (default 150), `--crisis-timeout
SECONDS` (default 240), `--timeout SECONDS` (hard limit for the whole run, default 600), and
`--headed` to watch the run in a visible window.
