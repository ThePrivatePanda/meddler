#!/usr/bin/env node
// Scripted capture of the README media: PNG screenshots of each step of the 60-second demo
// (docs/demo.md) plus a short looping GIF of the whole tour.
//
//   meddler serve --seed 1337 &            # in another terminal
//   cd scripts/capture && npm ci
//   node capture.mjs --url http://127.0.0.1:7677/
//
// See README.md in this directory for options and requirements (Chrome/Chromium, ffmpeg).

import { chromium } from "playwright-core";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");

// ---------- options ----------
function parseArgs(argv) {
  const opts = {
    url: "http://127.0.0.1:7677/",
    out: path.join(REPO, "docs", "media"),
    chrome: process.env.CHROME_PATH || "",
    ffmpeg: process.env.FFMPEG || "ffmpeg",
    width: 1440,
    height: 900,
    gif: true,
    gifWidth: 960,
    gifFps: 12,
    gifMaxBytes: 8 * 1024 * 1024,
    crisisTimeout: 240,
    warmup: 150,
    gifMaxSeconds: 20,
    timeout: 600,
    headed: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`${arg} needs a value`);
      return argv[++i];
    };
    switch (arg) {
      case "--url": opts.url = next(); break;
      case "--out": opts.out = path.resolve(next()); break;
      case "--chrome": opts.chrome = next(); break;
      case "--ffmpeg": opts.ffmpeg = next(); break;
      case "--width": opts.width = Number(next()); break;
      case "--height": opts.height = Number(next()); break;
      case "--gif-width": opts.gifWidth = Number(next()); break;
      case "--gif-fps": opts.gifFps = Number(next()); break;
      case "--crisis-timeout": opts.crisisTimeout = Number(next()); break;
      case "--warmup": opts.warmup = Number(next()); break;
      case "--timeout": opts.timeout = Number(next()); break;
      case "--no-gif": opts.gif = false; break;
      case "--headed": opts.headed = true; break;
      case "-h": case "--help":
        console.log(
          "usage: node capture.mjs [--url URL] [--out DIR] [--chrome PATH] [--ffmpeg PATH]\n" +
          "                        [--width N] [--height N] [--gif-width N] [--gif-fps N]\n" +
          "                        [--warmup TICKS] [--crisis-timeout SECONDS] [--timeout SECONDS]\n" +
          "                        [--no-gif] [--headed]",
        );
        process.exit(0);
        break;
      default:
        throw new Error(`unknown option ${arg} (try --help)`);
    }
  }
  return opts;
}

function findChrome(explicit) {
  if (explicit) {
    if (!existsSync(explicit)) throw new Error(`--chrome ${explicit} does not exist`);
    return explicit;
  }
  const candidates = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  ];
  const found = candidates.find((p) => existsSync(p));
  if (!found) {
    throw new Error(
      "no Chrome/Chromium found; pass --chrome /path/to/chrome or set CHROME_PATH " +
      "(playwright-core does not download browsers)",
    );
  }
  return found;
}

// ---------- tour bookkeeping ----------
const log = (msg) => console.log(`[capture] ${msg}`);

class Tour {
  constructor(page, outDir, t0) {
    this.page = page;
    this.outDir = outDir;
    this.t0 = t0;
    this.segments = [];
  }
  now() {
    return (Date.now() - this.t0) / 1000;
  }
  // One demo beat: `act` performs the interaction and waits until its result is on screen.
  // The GIF keeps a short lead-in before the action and `dwell` seconds after it is ready;
  // any wait for the server in between (loading spinners) is cut out.
  async step(name, { shot, dwell = 2.2, lead = 0.4 } = {}, act) {
    const started = this.now();
    await act();
    const ready = this.now();
    await this.page.waitForTimeout(700); // let the transition settle before the still
    if (shot) {
      for (const file of [].concat(shot)) {
        await this.page.screenshot({ path: path.join(this.outDir, file) });
        log(`wrote ${file}`);
      }
    }
    const remaining = dwell - (this.now() - ready);
    if (remaining > 0) await this.page.waitForTimeout(remaining * 1000);
    const end = this.now();
    if (ready - started > 1.5) {
      this.segments.push([Math.max(0, started - lead), started + 0.6], [ready - 0.2, end]);
    } else {
      this.segments.push([Math.max(0, started - lead), end]);
    }
    log(`${name}: ${(ready - started).toFixed(1)}s to ready`);
  }
}

const visible = (selector) => `${selector}:not([hidden])`;

// The topbar clock reads e.g. "t142" (or "t142 · B" while a fork is focused).
async function clockTick(page) {
  const text = (await page.locator("#clockTick").textContent()) || "";
  const match = /t(\d+)/.exec(text);
  return match ? Number(match[1]) : NaN;
}

async function waitForClockToMove(page, timeout) {
  const start = await clockTick(page);
  try {
    await page.waitForFunction(
      (t0) => {
        const m = /t(\d+)/.exec(document.getElementById("clockTick")?.textContent || "");
        return m && Number(m[1]) !== t0;
      },
      start,
      { timeout },
    );
    return true;
  } catch {
    return false;
  }
}

async function waitOverlay(page, innerSelector, timeout = 60_000) {
  await page.waitForSelector(visible("#overlay"), { timeout });
  await page.waitForSelector(`#overlayCard ${innerSelector}`, { timeout });
}

async function closeOverlay(page) {
  if (await page.locator(visible("#overlay")).count()) {
    await page.keyboard.press("Escape");
    await page.waitForSelector("#overlay", { state: "hidden", timeout: 10_000 });
  }
}

// ---------- the scripted tour (docs/demo.md) ----------
async function runTour(page, tour, opts) {
  await page.goto(opts.url, { waitUntil: "domcontentloaded" });
  // Connected once the hello message has filled in the seed chip and the world list.
  await page.waitForFunction(
    () => !/—/.test(document.getElementById("seedChip")?.textContent || "—"),
    null,
    { timeout: 60_000 },
  );
  await page.waitForSelector("#worldList .ccard", { timeout: 60_000 });
  await page.waitForSelector("#feedA li.ev", { timeout: 60_000 });
  log("connected");

  // Run at 4x until the world has some history (--warmup ticks) and the chronicle shows a
  // crisis (severity 2) headline to investigate.
  await page.keyboard.press("4");
  const crisisSel = "#feedA li.ev.sev2:not(.iv)";
  try {
    await page.waitForFunction(
      ({ warmup, sel }) => {
        const m = /t(\d+)/.exec(document.getElementById("clockTick")?.textContent || "");
        return m && Number(m[1]) >= warmup && document.querySelector(sel);
      },
      { warmup: opts.warmup, sel: crisisSel },
      { timeout: opts.crisisTimeout * 1000, polling: 250 },
    );
  } catch {
    throw new Error(
      `no crisis headline by tick ${opts.warmup} within ${opts.crisisTimeout}s ` +
      "(try a larger --crisis-timeout or a smaller --warmup)",
    );
  }
  // Back to 1x so the recording reads at a human pace.
  await page.keyboard.press("2");
  const crisis = page.locator(crisisSel).first();
  const eventId = await crisis.getAttribute("data-id");
  const country = (await crisis.locator(".cchip").first().textContent())?.trim();
  const crisisTick = Number(((await crisis.locator(".ev-tick").first().textContent()) || "").replace(/\D/g, ""));
  log(`crisis event #${eventId} in ${country} at t${crisisTick}`);

  // 1. Watch the world run.
  await tour.step("watch", { shot: "01-watch.png", dwell: 2.2 }, async () => {
    await page.waitForTimeout(300);
  });

  // 2. Ask why: select the crisis; the inspector shows its causes and effects.
  await tour.step("inspect", { shot: ["02-inspect.png", "hero.png"], dwell: 2.6 }, async () => {
    await page.locator(`#feedA li.ev[data-id="${eventId}"]`).first().click();
    await page.waitForSelector(`#feedA li.ev.sel[data-id="${eventId}"]`, { timeout: 10_000 });
    await page.waitForFunction(
      () => !document.querySelector("#inspBody .hint") &&
        (document.getElementById("inspBody")?.textContent || "").length > 40,
      null,
      { timeout: 30_000 },
    );
  });

  // 3. Trace the whole causal DAG with one key.
  await tour.step("trace", { shot: "03-trace.png", dwell: 2.6 }, async () => {
    await page.keyboard.press("t");
    await waitOverlay(page, ".trace-rows li");
  });
  await closeOverlay(page);

  // 4. Scrub back in time (read-only reconstruction).
  await tour.step("scrub", { shot: "04-scrub.png", dwell: 2.0 }, async () => {
    // Shift+Left steps back 25 ticks from the tick on screen, so wait for each step to land
    // before the next. Aim a few ticks before the crisis, but at most 3 steps so the fork
    // still shares some visible history with prime.
    const live = await clockTick(page);
    const steps = Math.min(3, Math.max(1, Math.ceil((live - (crisisTick - 5)) / 25)));
    for (let i = 0; i < steps; i++) {
      const before = await clockTick(page);
      await page.keyboard.press("Shift+ArrowLeft");
      await page.waitForFunction(
        (limit) => {
          const m = /t(\d+)/.exec(document.getElementById("clockTick")?.textContent || "");
          return m && Number(m[1]) <= limit;
        },
        Math.max(0, before - 20),
        { timeout: 30_000 },
      );
    }
    await page.waitForSelector(visible("#scrubBanner"), { timeout: 30_000 });
  });

  // 5. Intervene at that past tick ("Intervene here" on the scrub banner): fork a new timeline
  // with a drought in the crisis country.
  await tour.step("fork", { shot: "05-fork.png", dwell: 3.0 }, async () => {
    await page.locator("#btnForkHere").click();
    await waitOverlay(page, ".ivcard");
    const preferred = page.locator('#overlayCard .ivcard[data-kind="INTERVENE_DROUGHT"]');
    await ((await preferred.count()) ? preferred : page.locator("#overlayCard .ivcard").first()).click();
    const target = page.locator(
      `#overlayCard .cpick[data-grid="1"][data-code="${country}"]:not([disabled])`,
    );
    await ((await target.count()) ? target : page.locator('#overlayCard .cpick[data-grid="1"]:not([disabled])').first()).click();
    const second = page.locator('#overlayCard .cpick[data-grid="2"]:not([disabled])');
    if (await second.count()) await second.first().click();
    await page.locator("#overlayCard .go:not([disabled])").click();
    await page.waitForSelector("#overlay", { state: "hidden", timeout: 30_000 });
    await page.waitForSelector(visible("#tlbar"), { timeout: 30_000 });
    await page.waitForSelector(visible("#colB"), { timeout: 30_000 });
    // Both timelines should now advance, and the ΔWORLD strip appears once they diverge (run
    // at 4x while waiting; the wait is cut from the GIF). If the clock stays put, the bridge
    // left the scrub view set after forking from the past; the split view is still shown.
    if (await waitForClockToMove(page, 4000)) {
      await page.keyboard.press("4");
      await page.waitForSelector(visible("#deltaStrip"), { timeout: 45_000 }).catch(() => {
        log("note: ΔWORLD strip not visible yet (timelines have not diverged)");
      });
      await page.keyboard.press("2");
    } else {
      log("warning: timelines are not advancing after the fork; capturing the static split view");
    }
    // Let the speed change settle so its pending notice is not in the still.
    await page.waitForSelector("#activityIndicator", { state: "hidden", timeout: 30_000 });
  });

  // 6. Country dossier: full-history charts.
  await tour.step("dossier", { shot: "06-dossier.png", dwell: 2.4 }, async () => {
    await page.locator(`#worldList .ccard[data-code="${country}"]`).first().click();
    await page.keyboard.press("d");
    await waitOverlay(page, ".history-chart");
  });
  await closeOverlay(page);

  // 7. The Annals.
  await tour.step("annals", { shot: "07-annals.png", dwell: 2.2 }, async () => {
    await page.keyboard.press("h");
    await waitOverlay(page, "[data-anntab]");
    // The tabs render at once; the record arrives from the engine behind a spinner.
    await page.waitForFunction(() => !document.querySelector("#overlayCard .loading-state"), null, {
      timeout: 60_000,
    });
  });
}

// ---------- GIF ----------
// Shorten every segment's tail proportionally so the clip fits in `maxSeconds`.
function fitSegments(segments, maxSeconds) {
  const total = segments.reduce((sum, [a, b]) => sum + (b - a), 0);
  if (total <= maxSeconds) return segments;
  const scale = maxSeconds / total;
  return segments.map(([a, b]) => [a, a + Math.max(0.8, (b - a) * scale)]);
}

function buildGif(opts, video, rawSegments, outFile) {
  const segments = fitSegments(rawSegments, opts.gifMaxSeconds);
  const total = segments.reduce((sum, [a, b]) => sum + (b - a), 0);
  log(`GIF: ${segments.length} segments, ${total.toFixed(1)}s`);
  const attempt = (fps, width) => {
    const parts = segments.map(
      ([a, b], i) => `[0:v]trim=start=${a.toFixed(3)}:end=${b.toFixed(3)},setpts=PTS-STARTPTS[s${i}]`,
    );
    const inputs = segments.map((_, i) => `[s${i}]`).join("");
    const filter =
      parts.join(";") +
      `;${inputs}concat=n=${segments.length}:v=1:a=0,fps=${fps},scale=${width}:-1:flags=lanczos,` +
      "split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];" +
      "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle";
    const res = spawnSync(
      opts.ffmpeg,
      ["-y", "-loglevel", "error", "-i", video, "-filter_complex", filter, "-loop", "0", outFile],
      { stdio: ["ignore", "inherit", "inherit"] },
    );
    if (res.error) {
      throw new Error(`could not run ffmpeg (${opts.ffmpeg}): ${res.error.message}; set FFMPEG or --ffmpeg`);
    }
    if (res.status !== 0) throw new Error(`ffmpeg exited with ${res.status}`);
    return statSync(outFile).size;
  };
  let fps = opts.gifFps;
  let width = opts.gifWidth;
  for (let tries = 0; tries < 4; tries++) {
    const size = attempt(fps, width);
    log(`GIF ${width}px @ ${fps}fps = ${(size / 1048576).toFixed(2)} MB`);
    if (size <= opts.gifMaxBytes) return;
    fps = Math.max(8, fps - 2);
    width = Math.round(width * 0.85);
  }
  throw new Error(`GIF is still larger than ${opts.gifMaxBytes} bytes; lower --gif-width/--gif-fps`);
}

// ---------- main ----------
async function main() {
  const opts = parseArgs(process.argv.slice(2));
  mkdirSync(opts.out, { recursive: true });
  const videoDir = mkdtempSync(path.join(tmpdir(), "meddler-capture-"));
  const problems = [];

  // Hard stop for the whole run, so a wedged browser or server can never hang a caller.
  const watchdog = setTimeout(() => {
    console.error(`[capture] failed: gave up after ${opts.timeout}s (--timeout)`);
    process.exit(2);
  }, opts.timeout * 1000);
  watchdog.unref();

  const browser = await chromium.launch({
    executablePath: findChrome(opts.chrome),
    headless: !opts.headed,
  });
  let video = null;
  let tour = null;
  try {
    const context = await browser.newContext({
      viewport: { width: opts.width, height: opts.height },
      deviceScaleFactor: 1,
      colorScheme: "dark",
      ...(opts.gif ? { recordVideo: { dir: videoDir, size: { width: opts.width, height: opts.height } } } : {}),
    });
    // The README shows a returning visitor, not the first-run intro card.
    await context.addInitScript(() => {
      try { window.localStorage.setItem("meddler.introSeen", "1"); } catch (e) { /* not persisted */ }
    });
    const page = await context.newPage();
    const t0 = Date.now(); // the recording starts with the page
    page.on("console", (msg) => {
      // Failed HTTP loads are reported with their URL by the response handler below.
      if (msg.type() === "error" && !/^Failed to load resource/.test(msg.text())) {
        problems.push(`console error: ${msg.text()}`);
      }
    });
    page.on("response", (res) => {
      if (res.status() < 400) return;
      const url = res.url();
      if (new URL(url).pathname === "/favicon.ico") {
        log(`warning: ${url} returned ${res.status()} (the frontend has no favicon)`);
      } else {
        problems.push(`HTTP ${res.status()} for ${url}`);
      }
    });
    page.on("pageerror", (err) => problems.push(`page error: ${err.message}`));
    page.setDefaultTimeout(30_000);

    tour = new Tour(page, opts.out, t0);
    try {
      await runTour(page, tour, opts);
    } catch (err) {
      const shot = path.join(tmpdir(), `meddler-capture-failure-${Date.now()}.png`);
      await page.screenshot({ path: shot }).then(
        () => console.error(`[capture] screen at failure: ${shot}`),
        () => {},
      );
      throw err;
    }
    video = page.video();
    await context.close(); // finalizes the video file
  } finally {
    await browser.close();
  }

  if (opts.gif && video && tour) {
    buildGif(opts, await video.path(), tour.segments, path.join(opts.out, "tour.gif"));
    log("wrote tour.gif");
  }
  rmSync(videoDir, { recursive: true, force: true });

  if (problems.length) {
    console.error(`[capture] ${problems.length} browser error(s):`);
    for (const p of problems) console.error(`  ${p}`);
    process.exitCode = 1;
  } else {
    log("done, no console errors");
  }
}

main().catch((err) => {
  console.error(`[capture] failed: ${err.message}`);
  process.exit(1);
});
