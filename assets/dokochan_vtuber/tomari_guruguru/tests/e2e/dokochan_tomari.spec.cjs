const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

const evidenceDir = path.resolve(__dirname, '../../verification/playwright');

function ensureEvidenceDir() {
  fs.mkdirSync(evidenceDir, { recursive: true });
}

async function visibleSources(page) {
  return page.evaluate(() => Array.from(document.images)
    .filter((img) => getComputedStyle(img).opacity !== '0')
    .map((img) => img.getAttribute('src')));
}

async function activeA(page) {
  const sources = await visibleSources(page);
  return sources.find((src) => src && src.includes('slices2/A/')) || '';
}

async function waitForA(page, row, col) {
  const expected = `slices2/A/r${row}c${col}.png`;
  await expect.poll(() => activeA(page), { timeout: 5000 }).toContain(expected);
}

async function imageAlphaBBox(page, src) {
  return page.evaluate(async (imageSrc) => {
    const img = new Image();
    img.src = imageSrc;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let minX = canvas.width;
    let minY = canvas.height;
    let maxX = -1;
    let maxY = -1;
    let area = 0;
    for (let y = 0; y < canvas.height; y++) {
      for (let x = 0; x < canvas.width; x++) {
        if (data[(y * canvas.width + x) * 4 + 3] > 8) {
          minX = Math.min(minX, x);
          minY = Math.min(minY, y);
          maxX = Math.max(maxX, x);
          maxY = Math.max(maxY, y);
          area++;
        }
      }
    }
    return { minX, minY, maxX: maxX + 1, maxY: maxY + 1, area };
  }, src);
}

async function compareAlphaMasks(page, aSrc, bSrc) {
  return page.evaluate(async ({ aSrc: leftSrc, bSrc: rightSrc }) => {
    async function loadData(src) {
      const img = new Image();
      img.src = src;
      await img.decode();
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(img, 0, 0);
      return {
        width: canvas.width,
        height: canvas.height,
        data: ctx.getImageData(0, 0, canvas.width, canvas.height).data,
      };
    }
    const left = await loadData(leftSrc);
    const right = await loadData(rightSrc);
    let diff = 0;
    for (let i = 3; i < left.data.length; i += 4) {
      if (left.data[i] !== right.data[i]) diff++;
    }
    return { diff, width: left.width, height: left.height };
  }, { aSrc, bSrc });
}

test.describe('Dokochan tomari-guruguru runtime', () => {
  test.beforeEach(async () => {
    ensureEvidenceDir();
  });

  test('mouse direction mapping follows tomari grid without reversal', async ({ page }) => {
    await page.addInitScript(() => { Math.random = () => 1; });
    await page.goto('/guruguru.html', { waitUntil: 'networkidle' });
    await waitForA(page, 2, 2);
    await page.screenshot({ path: path.join(evidenceDir, 'guruguru-center.png') });

    await page.mouse.move(8, 400);
    await waitForA(page, 2, 0);

    await page.mouse.move(470, 400);
    await waitForA(page, 2, 1);

    await page.mouse.move(810, 400);
    await waitForA(page, 2, 3);

    await page.mouse.move(1272, 400);
    await waitForA(page, 2, 4);

    await page.mouse.move(640, 8);
    await waitForA(page, 0, 2);

    await page.mouse.move(640, 792);
    await waitForA(page, 4, 2);
    await page.screenshot({ path: path.join(evidenceDir, 'guruguru-down.png') });
  });

  test('blink switches to D sheet without changing rendered or alpha bounds', async ({ page }) => {
    await page.addInitScript(() => {
      Math.random = () => 0;
      const realSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => {
        const controlledDelay = delay >= 500 ? 10 : Math.max(delay, 1000);
        return realSetTimeout(callback, controlledDelay, ...args);
      };
    });
    await page.goto('/guruguru.html', { waitUntil: 'networkidle' });
    await waitForA(page, 2, 2);

    await expect.poll(() => visibleSources(page), { timeout: 4000, intervals: [20] }).toContain('slices2/D/r2c2.png');
    await page.screenshot({ path: path.join(evidenceDir, 'guruguru-blink.png') });

    const aBox = await imageAlphaBBox(page, '/slices2/A/r2c2.png');
    const dBox = await imageAlphaBBox(page, '/slices2/D/r2c2.png');
    const delta = Math.max(
      Math.abs(aBox.minX - dBox.minX),
      Math.abs(aBox.minY - dBox.minY),
      Math.abs(aBox.maxX - dBox.maxX),
      Math.abs(aBox.maxY - dBox.maxY),
    );
    expect(delta).toBeLessThanOrEqual(1);
  });

  test('all blink and mouth states keep exact alpha coordinates', async ({ page }) => {
    await page.goto('/guruguru.html', { waitUntil: 'networkidle' });
    const pairs = [['A', 'D'], ['B', 'E'], ['C', 'F']];
    for (const [openSheet, closedSheet] of pairs) {
      for (let row = 0; row < 5; row++) {
        for (let col = 0; col < 5; col++) {
          const openSrc = `/slices2/${openSheet}/r${row}c${col}.png`;
          const closedSrc = `/slices2/${closedSheet}/r${row}c${col}.png`;
          const openBox = await imageAlphaBBox(page, openSrc);
          const closedBox = await imageAlphaBBox(page, closedSrc);
          expect(openBox, `${openSheet}${closedSheet} r${row}c${col} bbox`).toEqual(closedBox);
          const alpha = await compareAlphaMasks(page, openSrc, closedSrc);
          expect(alpha.diff, `${openSheet}${closedSheet} r${row}c${col} alpha diff`).toBe(0);
        }
      }
    }
  });

  test('talk page uses fake microphone to switch mouth sheets', async ({ page }) => {
    await page.addInitScript(() => { Math.random = () => 1; });
    await page.goto('/talk.html', { waitUntil: 'networkidle' });
    await waitForA(page, 2, 2);

    await page.getByRole('button', { name: 'マイク開始' }).click();
    await expect(page.getByRole('button', { name: 'マイク停止' })).toBeVisible();
    await expect.poll(async () => {
      const sources = await visibleSources(page);
      return sources.some((src) => src && (src.includes('slices2/B/') || src.includes('slices2/C/')));
    }, { timeout: 8000 }).toBeTruthy();
    await page.screenshot({ path: path.join(evidenceDir, 'talk-fake-mic-mouth.png') });
  });
});
