import { test, expect, chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { PNG } from 'pngjs';
test.setTimeout(120000);
test.use({ channel: 'chrome', viewport: { width: 1440, height: 1000 }, launchOptions: { args: ['--mute-audio'] } });
const evidence = 'projects/dokochan-bust/review/player';
test('player synchronizes with a separate browser process and late transparent OBS', async ({ page, baseURL }) => {
  await mkdir(evidence, { recursive: true });
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/player?project=dokochan-bust');
  await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await expect(page.getByLabel('プロジェクト')).toHaveValue('dokochan-bust');
  const second = await chromium.launch({ channel: 'chrome', args: ['--mute-audio'] });
  try {
    const context = await second.newContext({ viewport: { width: 900, height: 900 } });
    const obs = await context.newPage(); obs.on('pageerror', e => errors.push(e.message));
    await obs.goto(`${baseURL}/obs`);
    await expect(obs.locator('canvas')).toHaveAttribute('data-ready', 'true');
    await expect(obs.locator('canvas')).toHaveAttribute('data-project', 'dokochan-bust');
    for (const [id, label] of Object.entries({ neutral: '通常', happy: '喜び', sad: '悲しみ', angry: '怒り', surprised: '驚き', relaxed: '穏やか' })) {
      await page.getByRole('button', { name: label, exact: true }).click();
      await expect(obs.locator('canvas')).toHaveAttribute('data-emotion', id);
    }
    const follow = page.getByLabel('操作ページ内のカーソルを追う');
    if (!await follow.isChecked()) await follow.click();
    await expect(follow).toBeChecked();
    await page.mouse.move(100, 100);
    const state = () => page.request.get('/__studio/player/state').then(r => r.json());
    await expect.poll(async () => (await state()).pointer).not.toBeNull();
    await follow.click(); await expect(follow).not.toBeChecked();
    await expect.poll(async () => (await state()).followPointer).toBe(false);
    await obs.reload();
    await expect(obs.locator('canvas')).toHaveAttribute('data-ready', 'true');
    await expect(obs.locator('canvas')).toHaveAttribute('data-emotion', 'relaxed');
    await expect(obs.locator('button, input, textarea, select')).toHaveCount(0);
    expect(await obs.locator('body').evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    const url = await obs.locator('canvas').evaluate(node => (node as HTMLCanvasElement).toDataURL());
    const bytes = Buffer.from(url.split(',')[1], 'base64'), png = PNG.sync.read(bytes);
    let transparent = 0, nonzero = 0; for (let i = 3; i < png.data.length; i += 4) { if (png.data[i] === 0) transparent++; else nonzero++; }
    expect(transparent).toBeGreaterThan(0); expect(nonzero).toBeGreaterThan(0);
    await writeFile(`${evidence}/obs-transparent.png`, bytes);
    await obs.screenshot({ path: `${evidence}/obs-page.png` });
    await page.screenshot({ path: `${evidence}/control-page.png`, fullPage: true });
    await page.getByRole('button', { name: '全画面', exact: true }).click();
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true);
    await page.evaluate(() => document.exitFullscreen());
    expect(errors).toEqual([]);
    await writeFile(`${evidence}/player-ui.json`, JSON.stringify({ independentBrowserProcess: true, emotions: 6, lateObsState: 'relaxed', pointerOnOff: true, fullscreen: true, transparentPixels: transparent, visiblePixels: nonzero, pageErrors: errors }, null, 2));
  } finally { await second.close(); }
});

test('Irodori real audio drives mouth volume and stops in a second context without OBS playback', async ({ page, browser, baseURL }) => {
  await page.goto('/player'); await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  const context = await browser.newContext(); const obs = await context.newPage();
  try {
    await obs.goto(`${baseURL}/obs`); await expect(obs.locator('canvas')).toHaveAttribute('data-ready', 'true');
    await page.getByLabel('読み上げる日本語').fill('こんにちは。どこちゃんです。よろしくお願いします。');
    const wav = page.waitForResponse(r => r.url().endsWith('/__studio/player/tts') && r.request().method() === 'POST');
    const levels: {voiceLevel: number; audioTime: number; speaking: boolean; sampleRate?: number; windowFrames?: number}[] = [];
    page.on('request', r => { if (r.url().endsWith('/audio-level')) levels.push(JSON.parse(r.postData() || '{}')); });
    await page.getByRole('button', { name: '読み上げる', exact: true }).click();
    const response = await wav; expect(response.status()).toBe(200);
    await writeFile(`${evidence}/player-tts.wav`, await response.body());
    await expect(obs.locator('canvas')).toHaveAttribute('data-speaking', 'true');
    await expect.poll(async () => Number(await obs.locator('canvas').getAttribute('data-voice-level'))).toBeGreaterThan(0);
    await expect.poll(async () => Number(await obs.locator('canvas').getAttribute('data-mouth-open'))).toBeGreaterThan(0);
    await page.screenshot({ path: `${evidence}/tts-playing.png`, fullPage: true });
    await page.getByRole('button', { name: '読み上げ停止', exact: true }).click();
    await expect(obs.locator('canvas')).toHaveAttribute('data-speaking', 'false');
    await expect(obs.locator('canvas')).toHaveAttribute('data-voice-level', '0');
    expect(await obs.locator('audio, video').count()).toBe(0);
    expect(levels.length).toBeGreaterThan(0);
    await writeFile(`${evidence}/tts-level-requests.json`, JSON.stringify(levels, null, 2));
    const clip = (await response.body()).toString('base64');
    const comparisons = await page.evaluate(async ({ clip, levels }) => {
      const sampleRate = levels.find(row => row.sampleRate)?.sampleRate;
      const context = new AudioContext(sampleRate ? { sampleRate } : undefined);
      try {
        const bytes = Uint8Array.from(atob(clip), c => c.charCodeAt(0));
        const buffer = await context.decodeAudioData(bytes.buffer);
        const rows = levels.filter(row => row.windowFrames && row.speaking).map(row => {
          const end = Math.min(buffer.length, Math.max(0, Math.floor(row.audioTime * buffer.sampleRate)));
          const start = Math.max(0, end - row.windowFrames!); let sum = 0;
          for (let c = 0; c < buffer.numberOfChannels; c++) for (let i = start; i < end; i++) sum += buffer.getChannelData(c)[i] ** 2;
          const rms = end === start ? 0 : Math.sqrt(sum / ((end - start) * buffer.numberOfChannels));
          return { time: row.audioTime, observed: row.voiceLevel, decodedRMS: rms, difference: Math.abs(row.voiceLevel - rms) };
        });
        return { sampleRate: buffer.sampleRate, duration: buffer.duration, rows, maximumDifference: Math.max(...rows.map(row => row.difference)) };
      } finally { await context.close(); }
    }, { clip, levels });
    expect(comparisons.rows.length).toBeGreaterThan(0);
    expect(comparisons.maximumDifference).toBe(0);
    await writeFile(`${evidence}/tts-time-rms-proof.json`, JSON.stringify(comparisons, null, 2));
  } finally { await context.close(); }
});

test('real TTS natural completion releases audio and a reopened connection receives current state', async ({ page }) => {
  await page.goto('/player'); await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await page.getByRole('button', { name: '通常', exact: true }).click();
  await page.getByLabel('読み上げる日本語').fill('こんにちは。');
  await page.getByRole('button', { name: '読み上げる', exact: true }).click();
  await expect(page.locator('canvas')).toHaveAttribute('data-speaking', 'true');
  await expect(page.getByRole('button', { name: '読み上げる', exact: true })).toBeEnabled();
  await expect(page.locator('canvas')).toHaveAttribute('data-speaking', 'false');
  await page.getByRole('button', { name: '喜び', exact: true }).click();
  await expect(page.locator('canvas')).toHaveAttribute('data-emotion', 'happy');
  await page.goto('about:blank');
  await page.goto('/player');
  await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await expect(page.getByRole('status')).toContainText('OBS同期に接続中');
  await expect(page.locator('canvas')).toHaveAttribute('data-emotion', 'happy');
  await writeFile(`${evidence}/natural-end-reconnect.json`, JSON.stringify({ naturalEnd: true, stoppedVoice: Number(await page.locator('canvas').getAttribute('data-voice-level')), connectionClosedAndReopened: true, restoredEmotion: 'happy' }, null, 2));
});

test('a real network failure while requesting TTS displays an error and allows recovery', async ({ page }) => {
  await page.goto('/player'); await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await page.context().setOffline(true);
  await page.getByRole('button', { name: '読み上げる', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  const error = await page.getByRole('alert').innerText();
  await expect(page.getByRole('button', { name: '読み上げる', exact: true })).toBeEnabled();
  await page.context().setOffline(false);
  await page.reload();
  await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await writeFile(`${evidence}/tts-network-error.json`, JSON.stringify({ actualBrowserNetworkFailure: true, error, UIRecovered: true, upstreamIrodoriFailureNotForced: true }, null, 2));
});

test('real microphone attempt reports permission errors rather than fake media success', async ({ page }) => {
  await page.goto('/player'); await expect(page.locator('canvas')).toHaveAttribute('data-ready', 'true');
  await page.getByRole('button', { name: 'マイク開始', exact: true }).click();
  await expect.poll(async () => (await page.getByRole('alert').count()) > 0 || await page.locator('canvas').getAttribute('data-speaking') === 'true').toBe(true);
  const denied = await page.getByRole('alert').count() > 0;
  let error = '', trackStates: string[] = [];
  if (denied) error = await page.getByRole('alert').innerText();
  else {
    const before = JSON.parse(await page.locator('canvas').getAttribute('data-microphone-tracks') || '[]');
    expect(before.length).toBeGreaterThan(0); expect(before.every((s: string) => s === 'live')).toBe(true);
    await page.getByRole('button', { name: 'マイク停止', exact: true }).click();
    await expect.poll(async () => JSON.parse(await page.locator('canvas').getAttribute('data-microphone-tracks') || '[]').every((s: string) => s === 'ended')).toBe(true);
    trackStates = JSON.parse(await page.locator('canvas').getAttribute('data-microphone-tracks') || '[]');
  }
  await expect(page.getByRole('button', { name: 'マイク開始', exact: true })).toBeEnabled();
  await expect(page.locator('canvas')).toHaveAttribute('data-speaking', 'false');
  await writeFile(`${evidence}/microphone-permission.json`, JSON.stringify({ realGetUserMedia: true, grantedByAgent: false, hardwareCaptureVerified: !denied, actualError: error, trackStatesAfterStop: trackStates, UIRecovered: true }, null, 2));
  await page.screenshot({ path: `${evidence}/microphone-permission.png`, fullPage: true });
});
