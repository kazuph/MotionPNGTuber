import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { PNG } from 'pngjs';
import { createHash } from 'node:crypto';
test.use({ channel: 'chrome', viewport: { width: 1600, height: 1100 } });
const evidence='projects/dokochan/review/ui';
test('Dokochan local project standard pose and lip controls',async({page})=>{
  const errors:{message:string,url:string}[]=[];const pageErrors:string[]=[];const warnings:string[]=[];const failed:string[]=[];const httpErrors:{url:string,status:number}[]=[];
  page.on('pageerror',e=>pageErrors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push({message:m.text(),url:m.location().url});if(m.type()==='warning')warnings.push(m.text());});page.on('requestfailed',r=>failed.push(r.url()));page.on('response',r=>{if(r.status()>=400)httpErrors.push({url:r.url(),status:r.status()});});
  await mkdir(evidence,{recursive:true});await page.goto('/');await page.waitForLoadState('networkidle');
  const guide=page.getByTestId('first-guide');if(await guide.isVisible())await guide.getByRole('button').click();
  await page.getByText('Open project',{exact:true}).click();await page.getByTestId('project-dokochan').click();
  await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready',{timeout:30000});
  await page.getByTestId('idle-toggle').click();await expect(page.getByTestId('idle-toggle')).toHaveAttribute('aria-pressed','false');await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready');
  const canvas=page.getByTestId('preview');const hash=async()=>createHash('sha256').update(await canvas.screenshot()).digest('hex');
  const rows:{name:string;sha256:string;changedPixelsFromRest?:number}[]=[];const images=new Map<string,Buffer>();
  async function capture(name:string){const image=await canvas.screenshot({path:`${evidence}/${name}.png`});images.set(name,image);rows.push({name,sha256:createHash('sha256').update(image).digest('hex')});await page.screenshot({path:`${evidence}/${name}-page.png`,fullPage:true});}
  async function move(label:string,key:string,name:string){const prior=await hash();await page.getByRole('slider',{name:label,exact:true}).focus();await page.getByRole('slider',{name:label,exact:true}).press(key);await expect.poll(hash).not.toBe(prior);await capture(name);}
  await capture('rest');await move('Eyes open','Home','eyes-closed');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Mouth open','End','mouth-open');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Turn left/right','Home','turn-left');await move('Turn left/right','End','turn-right');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Look up/down','Home','look-down');await move('Look up/down','End','look-up');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await move('Tilt','Home','tilt-left');await move('Tilt','End','tilt-right');
  await page.getByRole('button',{name:'Reset',exact:true}).click();await page.getByRole('tab',{name:'Lip sync',exact:true}).click();
  for(const vowel of ['あ','い','う','え','お','ん']){const prior=await hash();await page.getByRole('button',{name:vowel,exact:true}).click();await expect(page.getByRole('button',{name:vowel,exact:true})).toHaveAttribute('aria-pressed','true');await expect.poll(hash).not.toBe(prior);await capture(`vowel-${vowel}`);}
  await page.getByRole('button',{name:'Release',exact:true}).click();await page.getByRole('tab',{name:'Pose test',exact:true}).click();await page.getByRole('button',{name:'Reset',exact:true}).click();
  await page.reload();await page.waitForLoadState('networkidle');await expect(page.getByTestId('preview-status')).toHaveAttribute('data-state','ready');await page.getByTestId('part-head').click();await expect(page.getByRole('spinbutton',{name:'head.cx',exact:true})).toHaveValue('280');
  const rest=PNG.sync.read(images.get('rest')!);for(const row of rows){const png=PNG.sync.read(images.get(row.name)!);let changed=0;for(let i=0;i<png.data.length;i+=4)if([0,1,2,3].some(j=>png.data[i+j]!==rest.data[i+j]))changed++;row.changedPixelsFromRest=changed;}
  await writeFile(`${evidence}/results.json`,JSON.stringify({url:page.url(),rows,errors,pageErrors,warnings,failed,httpErrors},null,2));
  expect(pageErrors).toEqual([]);expect(errors.filter(e=>!e.url.endsWith('/favicon.ico'))).toEqual([]);expect(failed).toEqual([]);expect(httpErrors).toEqual([]);
});
