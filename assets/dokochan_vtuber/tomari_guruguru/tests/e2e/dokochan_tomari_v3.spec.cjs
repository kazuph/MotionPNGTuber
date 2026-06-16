const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

const base = process.env.GURUGURU_BASE || 'generated_v3/slices_cells_png';
const evidenceDir = path.resolve(__dirname, '../../verification/playwright');

function ensureEvidenceDir() {
  fs.mkdirSync(evidenceDir, { recursive: true });
}

async function visibleSources(page) {
  return page.evaluate(() => Array.from(document.images)
    .filter((img) => getComputedStyle(img).opacity !== '0')
    .map((img) => img.getAttribute('src')));
}

async function activeSheet(page, sheet) {
  const sources = await visibleSources(page);
  return sources.find((src) => src && src.includes(`${base}/${sheet}/`)) || '';
}

async function waitForSheetCell(page, sheet, row, col) {
  const expected = `${base}/${sheet}/r${row}c${col}.png`;
  await expect.poll(() => activeSheet(page, sheet), { timeout: 5000 }).toContain(expected);
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
    return {
      width: canvas.width,
      height: canvas.height,
      minX,
      minY,
      maxX: maxX + 1,
      maxY: maxY + 1,
      area,
      centerX: (minX + maxX + 1) / 2,
      centerY: (minY + maxY + 1) / 2,
      bboxWidth: maxX - minX + 1,
      bboxHeight: maxY - minY + 1,
    };
  }, src);
}

test.describe('Dokochan tomari-guruguru v3 runtime', () => {
  test.beforeEach(async () => {
    ensureEvidenceDir();
  });

  test('v3 base loads and mouse direction mapping is not reversed', async ({ page }) => {
    await page.addInitScript(() => { Math.random = () => 1; });
    await page.goto(`/guruguru.html?base=${base}`, { waitUntil: 'networkidle' });
    await waitForSheetCell(page, 'A', 2, 2);
    await page.screenshot({ path: path.join(evidenceDir, 'guruguru-v3-center.png') });

    await page.mouse.move(8, 400);
    await waitForSheetCell(page, 'A', 2, 0);

    await page.mouse.move(1272, 400);
    await waitForSheetCell(page, 'A', 2, 4);

    await page.mouse.move(640, 8);
    await waitForSheetCell(page, 'A', 0, 2);

    await page.mouse.move(640, 792);
    await waitForSheetCell(page, 'A', 4, 2);
    await page.screenshot({ path: path.join(evidenceDir, 'guruguru-v3-down.png') });
  });

  test('all v3 slices are loadable 1200px alpha images with bounded state drift', async ({ page }) => {
    await page.goto(`/guruguru.html?base=${base}`, { waitUntil: 'networkidle' });
    const sheets = ['A', 'B', 'C', 'D', 'E', 'F'];
    for (let row = 0; row < 5; row++) {
      for (let col = 0; col < 5; col++) {
        const boxes = {};
        for (const sheet of sheets) {
          boxes[sheet] = await imageAlphaBBox(page, `/${base}/${sheet}/r${row}c${col}.png`);
          expect(boxes[sheet].width, `${sheet} r${row}c${col} width`).toBe(1200);
          expect(boxes[sheet].height, `${sheet} r${row}c${col} height`).toBe(1200);
          expect(boxes[sheet].area, `${sheet} r${row}c${col} non-empty`).toBeGreaterThan(200000);
        }
        for (const sheet of sheets.slice(1)) {
          expect(Math.abs(boxes.A.centerX - boxes[sheet].centerX), `${sheet} r${row}c${col} centerX`).toBeLessThanOrEqual(2);
          expect(Math.abs(boxes.A.centerY - boxes[sheet].centerY), `${sheet} r${row}c${col} centerY`).toBeLessThanOrEqual(70);
          expect(Math.abs(boxes.A.bboxWidth - boxes[sheet].bboxWidth), `${sheet} r${row}c${col} bboxWidth`).toBeLessThanOrEqual(90);
          expect(Math.abs(boxes.A.bboxHeight - boxes[sheet].bboxHeight), `${sheet} r${row}c${col} bboxHeight`).toBeLessThanOrEqual(90);
        }
      }
    }
  });

  test('v3 blink switches to closed-eye sheet for the selected base', async ({ page }) => {
    await page.addInitScript(() => {
      Math.random = () => 0;
      const realSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => {
        const controlledDelay = delay >= 500 ? 10 : Math.max(delay, 1000);
        return realSetTimeout(callback, controlledDelay, ...args);
      };
    });
    await page.goto(`/guruguru.html?base=${base}`, { waitUntil: 'networkidle' });
    await waitForSheetCell(page, 'A', 2, 2);

    await expect.poll(() => visibleSources(page), { timeout: 4000, intervals: [20] })
      .toContain(`${base}/D/r2c2.png`);
    await page.screenshot({ path: path.join(evidenceDir, `guruguru-v3-blink-${base.replaceAll('/', '-')}.png`) });
  });

  test('v3 blink adjustment JSON is applied to the closed-eye overlay', async ({ page }) => {
    const adjustPath = base.replace(/\/?[^/]+$/, '/blink_adjustments.json');
    await page.route(`**/${adjustPath}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ 'A-D:2-2': { dx: 24, dy: -12, scale: 1.045, opacity: 0.72 } }),
      });
    });
    await page.addInitScript(() => {
      Math.random = () => 0;
      const realSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => {
        const controlledDelay = delay >= 500 ? 10 : Math.max(delay, 1000);
        return realSetTimeout(callback, controlledDelay, ...args);
      };
    });
    await page.goto(`/guruguru.html?base=${base}`, { waitUntil: 'networkidle' });
    await waitForSheetCell(page, 'A', 2, 2);

    await expect.poll(async () => {
      return page.evaluate((expectedSrc) => {
        const img = Array.from(document.images)
          .find((node) => node.getAttribute('src') === expectedSrc && getComputedStyle(node).opacity !== '0');
        if (!img) return null;
        const style = getComputedStyle(img);
        return { opacity: style.opacity, transform: img.style.transform };
      }, `${base}/D/r2c2.png`);
    }, { timeout: 4000, intervals: [20] }).toEqual({
      opacity: '0.72',
      transform: 'translate(2%, -1%) scale(1.045)',
    });
  });
});
