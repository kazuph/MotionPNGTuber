import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { PNG } from 'pngjs';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
test.setTimeout(120000);
test.use({ channel: 'chrome', viewport: { width: 1600, height: 1100 } });
for(const group of ['blink-mouth','head-angles','vowels-reload']) test(`Dokochan bust local project ${group}`,async({page})=>{
  const evidence=`projects/dokochan-bust/review/ui-final/${group}`;
  const errors:{message:string,url:string}[]=[];const pageErrors:string[]=[];const warnings:string[]=[];const failed:string[]=[];const httpErrors:{url:string,status:number}[]=[];
  page.on('pageerror',e=>pageErrors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),url:m.location().url});if(m.type()==='warning')warnings.push(m.text());});page.on('requestfailed',r=>failed.push(r.url()));page.on('response',r=>{if(r.status()>=400)httpErrors.push({url:r.url(),status:r.status()});});
  await mkdir(evidence,{recursive:true});await page.goto('/');await page.waitForLoadState('networkidle');
  const guide=page.getByTestId('first-guide');if(await guide.isVisible())await guide.getByRole('button').click();
  await page.getByText('Open project',{exact:true}).click();await page.getByTestId('project-dokochan-bust').click();
  await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready',{timeout:30000});
  const rig=JSON.parse(await readFile('projects/dokochan-bust/rig.json','utf8')) as {image:{width:number;height:number};view:{padTop:number;padSide:number};eyes:{roi:number[][]}[]};
  if(group==='blink-mouth'){
  const automaticBlink=await page.getByTestId('preview').evaluate(async(node,rig)=>{
    const canvas=node as HTMLCanvasElement,capture=document.createElement('canvas');capture.width=canvas.width;capture.height=canvas.height;const context=capture.getContext('2d')!;
    const scale=Math.min(canvas.width/(rig.image.width*(1+2*rig.view.padSide)),canvas.height/(rig.image.height*(1+rig.view.padTop))),ox=(canvas.width-rig.image.width*scale)/2,oy=canvas.height-rig.image.height*scale;
    const rows:{elapsedMs:number;blueGreen:number[]}[]=[];const start=performance.now();
    const duration=(1.8+4.2+0.17)*1000;
    while(performance.now()-start<duration){await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));context.drawImage(canvas,0,0);const data=context.getImageData(0,0,canvas.width,canvas.height).data;
      const counts=rig.eyes.map(e=>{const xs=e.roi.map(p=>p[0]),ys=e.roi.map(p=>p[1]);let count=0;for(let y=Math.max(0,Math.floor(oy+Math.min(...ys)*scale));y<Math.min(canvas.height,oy+Math.max(...ys)*scale);y++)for(let x=Math.max(0,Math.floor(ox+Math.min(...xs)*scale));x<Math.min(canvas.width,ox+Math.max(...xs)*scale);x++){const k=(y*canvas.width+x)*4;if(data[k+1]>data[k]&&data[k+2]>data[k])count++;}return count;});rows.push({elapsedMs:performance.now()-start,blueGreen:counts});}
    return {durationMs:duration,source:'motion.js blinkValue next interval1.8+random4.2 + blink duration0.17',rows,min:[0,1].map(i=>Math.min(...rows.map(r=>r.blueGreen[i]))),max:[0,1].map(i=>Math.max(...rows.map(r=>r.blueGreen[i])))};
  },rig);
  await writeFile(`${evidence}/automatic-blink.json`,JSON.stringify(automaticBlink,null,2));
  expect(automaticBlink.max[0]).toBeGreaterThan(automaticBlink.min[0]);expect(automaticBlink.max[1]).toBeGreaterThan(automaticBlink.min[1]);
  }
  await page.getByTestId('idle-toggle').click();await expect(page.getByTestId('idle-toggle')).toHaveAttribute('aria-pressed','false');await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready');
  const canvas=page.getByTestId('preview');const hash=async()=>createHash('sha256').update(await canvas.evaluate(node=>(node as HTMLCanvasElement).toDataURL())).digest('hex');
  const controlFailures:string[]=[];const rows:{name:string;sha256:string;changedPixelsFromRest?:number;mouthPixelHash?:string;mouthChangedPixels?:number}[]=[];const images=new Map<string,Buffer>();
  async function capture(name:string){const data=await canvas.evaluate(node=>(node as HTMLCanvasElement).toDataURL());const image=Buffer.from(data.split(',')[1],'base64');await writeFile(`${evidence}/${name}-canvas.png`,image);images.set(name,image);rows.push({name,sha256:createHash('sha256').update(image).digest('hex')});await page.screenshot({path:`${evidence}/${name}-page.png`,fullPage:true});}
  async function move(label:string,key:string,name:string){const prior=await hash();await page.getByRole('slider',{name:label,exact:true}).focus();await page.getByRole('slider',{name:label,exact:true}).press(key);try{await expect.poll(hash).not.toBe(prior);}catch(error){controlFailures.push(`${label} ${key}: ${String(error)}`);}await capture(name);}
  await page.getByRole('tab',{name:'Lip sync',exact:true}).click();const release=page.getByRole('button',{name:'Release',exact:true});if(await release.isEnabled())await release.click();
  await page.getByRole('tab',{name:'Pose test',exact:true}).click();
  await capture('rest');if(group==='blink-mouth'){await move('Eyes open','Home','eyes-closed');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Mouth open','End','mouth-open');}
  if(group==='head-angles'){
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Turn left/right','Home','turn-left');await move('Turn left/right','End','turn-right');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Look up/down','Home','look-down');await move('Look up/down','End','look-up');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Tilt','Home','tilt-left');await move('Tilt','End','tilt-right');}
  if(group==='vowels-reload'){
  await page.getByRole('button',{name:'Reset',exact:true}).click();await page.getByRole('tab',{name:'Lip sync',exact:true}).click();
  for(const vowel of ['あ','い','う','え','お','ん']){const prior=await hash();await page.getByRole('button',{name:vowel,exact:true}).click();await expect(page.getByRole('button',{name:vowel,exact:true})).toHaveAttribute('aria-pressed','true');await expect.poll(hash).not.toBe(prior);await capture(`vowel-${vowel}`);}
  await page.getByRole('button',{name:'Release',exact:true}).click();await page.getByRole('tab',{name:'Pose test',exact:true}).click();await page.getByRole('button',{name:'Reset',exact:true}).click();
  await page.reload();await page.waitForLoadState('networkidle');await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready');await page.getByTestId('part-head').click();await expect(page.getByRole('spinbutton',{name:'head.cx',exact:true})).toHaveValue('635');}
  const rest=PNG.sync.read(images.get('rest')!);for(const row of rows){const png=PNG.sync.read(images.get(row.name)!);let changed=0;for(let i=0;i<png.data.length;i+=4)if([0,1,2,3].some(j=>png.data[i+j]!==rest.data[i+j]))changed++;row.changedPixelsFromRest=changed;const scale=Math.min(png.width/(rig.image.width*(1+2*rig.view.padSide)),png.height/(rig.image.height*(1+rig.view.padTop))),ox=(png.width-rig.image.width*scale)/2,oy=png.height-rig.image.height*scale;const mouth:number[]=[];let mouthChanged=0;for(let y=Math.ceil(oy+570*scale);y<oy+657*scale;y++)for(let x=Math.ceil(ox+585*scale);x<ox+708*scale;x++){const k=(y*png.width+x)*4;mouth.push(...png.data.slice(k,k+4));if([0,1,2,3].some(j=>png.data[k+j]!==rest.data[k+j]))mouthChanged++;}row.mouthPixelHash=createHash('sha256').update(Buffer.from(mouth)).digest('hex');row.mouthChangedPixels=mouthChanged;}
  await writeFile(`${evidence}/results.json`,JSON.stringify({url:page.url(),rows,controlFailures,errors,pageErrors,warnings,failed,httpErrors},null,2));
  if(group==='vowels-reload'){const drawnVowels=rows.filter(r=>['vowel-あ','vowel-い','vowel-う','vowel-え','vowel-お'].includes(r.name));expect(new Set(drawnVowels.map(r=>r.mouthPixelHash)).size).toBe(5);for(const row of drawnVowels)expect(row.mouthChangedPixels).toBeGreaterThan(0);}
  expect(controlFailures).toEqual([]);expect(pageErrors).toEqual([]);expect(errors.filter(e=>!e.url.endsWith('/favicon.ico'))).toEqual([]);expect(failed).toEqual([]);expect(httpErrors.filter(e=>!e.url.endsWith('/favicon.ico'))).toEqual([]);
});
