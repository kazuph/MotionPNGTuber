import { test, expect } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';

test.use({ channel: 'chrome' });
// Same real-browser execution budget as the bust UI test.
test.setTimeout(120000);
test('real sprite renderer preserves layered eyes for absent per-eye drawings', async ({ page }) => {
  const base = 'samples/miko-qipao';
  const rig = JSON.parse(await readFile(`${base}/rig.json`, 'utf8'));
  const sheet = JSON.parse(await readFile(`${base}/built/sprites/sprites.json`, 'utf8'));
  const images: Record<string, string> = {};
  for (const name of Object.keys(sheet.layers)) images[name] = `data:image/png;base64,${(await readFile(`${base}/built/sprites/${name}.png`)).toString('base64')}`;
  await page.goto('/');
  const result = await page.evaluate(async ({ rig, sheet, images }) => {
    const modules = { rig: '/src/engine/rig.js', renderer: '/src/engine/renderer.js', physics: '/src/engine/physics.js', sprites: '/src/engine/sprites.js' };
    const { createRig, PARAMS } = await import(modules.rig);
    const { createRenderer } = await import(modules.renderer);
    const { createPhysics } = await import(modules.physics);
    const { createSpriteModule } = await import(modules.sprites);
    const engine = createRig(rig), { Renderer, buildGrid } = createRenderer(engine, rig), { Physics } = createPhysics(engine, rig);
    const imgs: Record<string, HTMLImageElement> = {};
    for (const [name, url] of Object.entries(images)) { const im = new Image(); im.src = url; await im.decode(); imgs[name] = im; }
    const alphaOf = (img: HTMLImageElement) => { const c = document.createElement('canvas'); c.width = img.width; c.height = img.height; const ctx = c.getContext('2d')!; ctx.drawImage(img, 0, 0); const data = ctx.getImageData(0, 0, c.width, c.height).data; return Uint8Array.from({ length: c.width * c.height }, (_, i) => data[i * 4 + 3]); };
    const defaults = Object.fromEntries(PARAMS.map((p: { id: string; def: number }) => [p.id, p.def]));
    const rows = [];
    for (const kind of ['full', 'none', 'mouth-only', 'left-closed-only']) {
      const selected = Object.fromEntries(Object.entries(sheet.layers).filter(([name]) => kind === 'full' || (kind === 'mouth-only' && name.startsWith('mouth_')) || (kind === 'left-closed-only' && name === 'eyes_closed_0')));
      const canvas = document.createElement('canvas'); canvas.width = rig.image.width; canvas.height = rig.image.height;
      const renderer = new Renderer(canvas), physics = new Physics();
      const sprites = createSpriteModule(engine, rig).createSprites(renderer, { ...sheet, layers: selected }, imgs, buildGrid, alphaOf);
      for (const open of [1, 0.5, 0]) {
        const p = { ...defaults, eyeLOpen: open, eyeROpen: open, eyeSmile: 0, mouthOpen: 0 };
        let state;
        for (let frame = 0; frame < 60; frame++) state = sprites.update(p, physics.step(p, 1 / 60), 1 / 60);
        const visible = renderer.layers.filter((l: { visible: boolean }) => l.visible).map((l: { name: string; alpha: number }) => ({ name: l.name, alpha: l.alpha }));
        rows.push({ kind, open, ...state, visible });
      }
      renderer.destroy();
    }
    return rows;
  }, { rig, sheet, images });
  await writeFile('projects/dokochan-bust/review/sprite-eye-contract.json', JSON.stringify(result, null, 2));
  for (const row of result) {
    if (row.kind === 'full') { expect(row.drawnEyes).toEqual([true, true]); expect(row.eyes).toEqual(row.open === 1 ? [false, false] : [true, true]); }
    else if (row.kind === 'left-closed-only') { expect(row.eyes).toEqual(row.open === 0 ? [true, false] : [false, false]); expect(row.drawnEyes).toEqual(row.open === 0 || row.open === 1 ? [true, false] : [false, false]); }
    else { expect(row.eyes).toEqual([false, false]); expect(row.drawnEyes).toEqual([false, false]); }
    for (let eye = 0; eye < 2; eye++) if (row.eyes[eye]) expect(row.visible.some((l: { name: string; alpha: number }) => l.name.endsWith(`_${eye}`) && l.alpha >= 0.999)).toBe(true);
  }
});

test('mouth-only and absent assets render identical layered blinks', async ({ page }) => {
  const project = 'projects/dokochan-bust';
  const rig = JSON.parse(await readFile(`${project}/rig.json`, 'utf8'));
  const layers = JSON.parse(await readFile(`${project}/built/layers.json`, 'utf8'));
  const sheet = JSON.parse(await readFile(`${project}/built/sprites/sprites.json`, 'utf8'));
  const assets: Record<string, string> = {};
  const names = ['layers.json', 'base.png', 'hairmask.png', ...Object.keys(layers.layers).map(n => `${n}.png`), 'sprites/sprites.json', ...Object.keys(sheet.layers).map(n => `sprites/${n}.png`)];
  for (const name of names) assets[name] = `data:${name.endsWith('.png') ? 'image/png' : 'application/json'};base64,${(await readFile(`${project}/built/${name}`)).toString('base64')}`;
  await page.goto('/');
  const report = await page.evaluate(async ({ rig, assets }) => {
    const modulePath = '/src/engine/createMeshAvatar.js';
    const { createMeshAvatarImpl } = await import(modulePath);
    const rigModule = '/src/engine/rig.js';
    const { PARAMS } = await import(rigModule);
    const defaults = Object.fromEntries(PARAMS.map((p: { id: string; def: number }) => [p.id, p.def]));
    const results: { kind: string; open: number; pixels: Uint8ClampedArray }[] = [];
    for (const kind of ['mouth-only', 'absent']) {
      const selected = Object.fromEntries(Object.entries(assets).filter(([name]) => kind === 'mouth-only' || !name.startsWith('sprites/')));
      const canvas = document.createElement('canvas'); canvas.width = rig.image.width; canvas.height = rig.image.height;
      canvas.style.width = `${rig.image.width}px`; canvas.style.height = `${rig.image.height}px`; document.body.append(canvas);
      const avatar = await createMeshAvatarImpl(canvas, { rig, assets: selected, manual: true, padTop: 0, padSide: 0 });
      avatar.setAutoIdle(false); avatar.setAutoMotion(false);
      for (const open of [1, 0.5, 0]) {
        avatar.setParameters({ ...defaults, eyeLOpen: open, eyeROpen: open, eyeSmile: 0, mouthOpen: 0 });
        avatar.advance(1, 60);
        const copy = document.createElement('canvas'); copy.width = canvas.width; copy.height = canvas.height; const ctx = copy.getContext('2d')!; ctx.drawImage(canvas, 0, 0);
        results.push({ kind, open, pixels: ctx.getImageData(0, 0, copy.width, copy.height).data });
      }
      avatar.destroy(); canvas.remove();
    }
    const opened = results.find(r => r.kind === 'mouth-only' && r.open === 1)!.pixels;
    return [1, 0.5, 0].map(open => {
      const a = results.find(r => r.kind === 'mouth-only' && r.open === open)!.pixels;
      const b = results.find(r => r.kind === 'absent' && r.open === open)!.pixels;
      return { open, differentChannels: a.reduce((n, value, i) => n + Number(value !== b[i]), 0), changedFromOpen: a.reduce((n, value, i) => n + Number(value !== opened[i]), 0) };
    });
  }, { rig, assets });
  await writeFile(`${project}/review/layered-blink-integration.json`, JSON.stringify(report, null, 2));
  for (const row of report) expect(row.differentChannels).toBe(0);
  expect(report.find(r => r.open === 0)!.changedFromOpen).toBeGreaterThan(0);
});
