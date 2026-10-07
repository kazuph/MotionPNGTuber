import { test, expect, chromium, type Browser } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
test.setTimeout(120000);
test.use({ channel: 'chrome', launchOptions: { headless: false, args: ['--mute-audio'] } });
test('real hidden controller continues PCM-driven OBS mouth updates and natural completion', async ({ baseURL }) => {
  const profile = await mkdtemp(join(tmpdir(), 'dokochan-hidden-native-'));
  const args = ['--remote-debugging-port=0', `--user-data-dir=${profile}`, '--mute-audio', '--no-first-run', '--no-default-browser-check', 'about:blank'];
  const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args, { stdio: ['ignore', 'ignore', 'pipe'] });
  let controller: Browser | undefined, second: Browser | undefined;
  try {
  const endpoint = await new Promise<string>((resolve, reject) => {
    let log = '';
    chrome.stderr.on('data', data => { log += data.toString(); const match = log.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (match) resolve(match[1]); });
    chrome.once('error', reject); chrome.once('exit', code => reject(new Error(`Dedicated Chrome exited: ${code}`)));
  });
  // Avoid Playwright's default focus emulation; this isolated browser retains native tab visibility.
  controller = await chromium.connectOverCDP(endpoint, { noDefaults: true });
  const context = controller.contexts()[0], page = context.pages()[0] || await context.newPage();
  await page.goto(`${baseURL}/player?project=dokochan-bust`);
  await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  const cover = await context.newPage(); await cover.goto('about:blank'); await cover.bringToFront();
  await expect.poll(() => page.evaluate(() => document.visibilityState)).toBe('hidden');
  const hiddenBeforeAudio = await page.evaluate(() => document.hidden);
  await page.bringToFront();
  await expect.poll(() => page.evaluate(() => document.visibilityState)).toBe('visible');
  second = await chromium.launch({ channel: 'chrome', headless: false, args: ['--mute-audio'] });
    const obsContext = await second.newContext(), obs = await obsContext.newPage();
    await obs.goto(`${baseURL}/obs`); await expect(obs.locator('canvas')).toHaveAttribute('data-ready', 'true');
    await page.bringToFront();
    await page.getByLabel('読み上げる日本語').fill('こんにちは。どこちゃんです。操作ページを隠した状態でも、音声に合わせて口を動かしています。よろしくお願いします。');
    const wav = page.waitForResponse(r => r.url().endsWith('/tts') && r.request().method() === 'POST');
    let hiddenPhase = false;
    const levels: { voiceLevel: number; audioTime: number; speaking: boolean; sampleRate: number; windowFrames: number }[] = [];
    page.on('request', request => { if (hiddenPhase && request.url().endsWith('/audio-level')) levels.push(JSON.parse(request.postData() || '{}')); });
    await page.getByRole('button', { name: '読み上げる', exact: true }).click();
    const response = await wav; expect(response.status()).toBe(200);
    const clip = await response.body();
    await writeFile('projects/dokochan-bust/review/player/background-tts.wav', clip);
    await expect(obs.locator('canvas')).toHaveAttribute('data-speaking', 'true');
    await cover.bringToFront();
    await expect.poll(() => page.evaluate(() => document.visibilityState)).toBe('hidden');
    hiddenPhase = true;
    await obs.bringToFront();
    await expect.poll(() => obs.evaluate(() => document.visibilityState)).toBe('visible');
    const rows = await obs.locator('canvas').evaluate(async node => {
      const rows: { voice: number; mouth: number; speaking: string | undefined }[] = [];
      const canvas = node as HTMLCanvasElement;
      do {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
        rows.push({ voice: Number(canvas.dataset.voiceLevel), mouth: Number(canvas.dataset.mouthOpen), speaking: canvas.dataset.speaking });
      } while (canvas.dataset.speaking === 'true');
      return rows;
    });
    expect(new Set(rows.filter(r => r.speaking === 'true').map(r => r.voice)).size).toBeGreaterThan(1);
    expect(new Set(rows.filter(r => r.speaking === 'true').map(r => r.mouth)).size).toBeGreaterThan(1);
    expect(rows.at(-1)?.voice).toBe(0);
    expect(await page.evaluate(() => document.visibilityState)).toBe('hidden');
    await expect(page.locator('canvas')).toHaveAttribute('data-speaking', 'false');
    await writeFile('projects/dokochan-bust/review/player/background-observations.json', JSON.stringify({ hiddenBeforeAudio, hiddenAfterAudio: await page.evaluate(() => document.hidden), rows, levels }, null, 2));
    const speakingLevels = levels.filter(level => level.speaking);
    expect(speakingLevels.length).toBeGreaterThan(1);
    expect(speakingLevels.at(-1)!.audioTime).toBeGreaterThan(speakingLevels[0].audioTime);
    const pcm = await page.evaluate(async ({ base64, levels }) => {
      const context = new AudioContext({ sampleRate: levels[0].sampleRate });
      try {
        const buffer = await context.decodeAudioData(Uint8Array.from(atob(base64), c => c.charCodeAt(0)).buffer);
        const differences = levels.map(level => {
          const end = Math.min(buffer.length, Math.max(0, Math.floor(level.audioTime * buffer.sampleRate))), start = Math.max(0, end - level.windowFrames);
          let sum = 0;
          for (let channel = 0; channel < buffer.numberOfChannels; channel++) for (let i = start; i < end; i++) sum += buffer.getChannelData(channel)[i] ** 2;
          const rms = end === start ? 0 : Math.sqrt(sum / ((end - start) * buffer.numberOfChannels));
          return Math.abs(level.voiceLevel - rms);
        });
        return { sampleRate: buffer.sampleRate, duration: buffer.duration, maximumDifference: Math.max(...differences) };
      } finally { await context.close(); }
    }, { base64: clip.toString('base64'), levels: speakingLevels });
    expect(pcm.maximumDifference).toBe(0);
    await writeFile('projects/dokochan-bust/review/player/background-audio.json', JSON.stringify({ realHiddenController: true, hiddenBeforeAudio, dedicatedControllerPid: chrome.pid, controllerLaunchArgs: args, noDefaultFocusEmulation: true, separateBrowserProcessObs: true, nativeAudioOutputMuted: true, timerGeneratedAudio: false, naturalCompletion: true, pcm, levels, rows }, null, 2));
    await obs.screenshot({ path: 'projects/dokochan-bust/review/player/background-obs.png' });
  } finally {
    await second?.close(); await controller?.close();
    chrome.kill('SIGTERM');
    await new Promise<void>(resolve => chrome.exitCode !== null ? resolve() : chrome.once('exit', () => resolve()));
    await rm(profile, { recursive: true, force: true });
  }
});
