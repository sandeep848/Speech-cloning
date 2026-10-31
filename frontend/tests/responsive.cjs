/* Run: npx playwright install chromium && npm run test:responsive
   Uses API fixtures; no TTS models, credentials or GPU are needed. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdir } = require('node:fs/promises');
const { chromium } = require('playwright');
const net = require('node:net');
async function serverAcceptsConnections() {
  return new Promise(resolve => {
    const socket = net.createConnection({host:'127.0.0.1',port});
    socket.setTimeout(200);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => { socket.destroy(); resolve(false); });
    socket.once('timeout', () => { socket.destroy(); resolve(false); });
  });
}
const port = 4175;
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath,
  [require('node:path').resolve(__dirname, '../node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd: require('node:path').resolve(__dirname, '..'), stdio: 'ignore', shell: false });
const models = [
  { id:'qwen3', name:'Qwen3-TTS (1.7B)', configured:true, ready:true },
  { id:'cosyvoice3', name:'CosyVoice 3 (0.5B)', configured:true, ready:true },
  { id:'xtts-v2', name:'XTTS-v2', configured:true, ready:true },
];
const jobs = models.map((m, i) => ({ id:`job-${i}`, model_id:m.id, status:'complete',
  audio_url:`/api/audio/${i}`, runtime_seconds:1.25 }));
// One-second valid WAV, so native mobile audio widgets receive real media data.
const wav = Buffer.alloc(44 + 48000);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1,20); wav.writeUInt16LE(1,22);
wav.writeUInt32LE(24000,24); wav.writeUInt32LE(48000,28);
wav.writeUInt16LE(2,32); wav.writeUInt16LE(16,34); wav.write('data',36); wav.writeUInt32LE(48000,40);
for(let i=0;i<24000;i++) wav.writeInt16LE(Math.round(2000*Math.sin(i*2*Math.PI*180/24000)),44+i*2);
async function fits(page, name) {
  const dimensions = await page.evaluate(() => ({width:innerWidth, scroll:document.documentElement.scrollWidth}));
  assert(dimensions.scroll <= dimensions.width + 1, `${name} spills horizontally: ${JSON.stringify(dimensions)}`);
}
(async () => {
  let browser;
  try {
    for(let i=0;i<150;i++) {
      if(server.exitCode !== null) throw Error('Preview server exited before becoming ready');
      if(await serverAcceptsConnections()) break;
      if(i===149) throw Error('Preview server did not start');
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    browser = await chromium.launch({
      executablePath:process.env.CHROMIUM_EXECUTABLE || undefined,
      args:JSON.parse(process.env.CHROMIUM_ARGS || '[]'),
    });
    for(const width of [320,360,390,430,600,768,820,1024,1280,1440]) {
      const context=await browser.newContext({viewport:{width,height:844}, hasTouch:width<=1100, isMobile:width<=760});
      const page=await context.newPage();
      const errors=[];
      page.on('pageerror', e=>errors.push(e.message));
      await page.route('**/api/**', async route=>{
        const path=new URL(route.request().url()).pathname;
        if(path.includes('/audio')) return route.fulfill({contentType:'audio/wav',body:wav});
        let body={};
        if(path==='/api/models') body=models;
        else if(path==='/api/reference-profile') body={reference:{id:'reference',filename:'reference.wav'}};
        else if(path.startsWith('/api/generations')) body={id:'demo',text:'Hello, world. A new paragraph.',jobs};
        else if(path.startsWith('/api/evaluations')) body={status:'completed',errors:[],metrics:[
          {name:'wer',value:.05},{name:'cer',value:.03},{name:'ecapa_similarity',value:.88},
          {name:'scoreq',value:4},{name:'rtfx',value:1.5},{name:'rtf',value:.67}]};
        else if(path==='/api/agreement') body={kappa:null};
        return route.fulfill({json:body});
      });
      await page.goto(origin);
      await page.getByText('German Reference Active',{exact:true}).waitFor();
      await fits(page,`${width}: initial`);
      if(width<=1100) {
        assert(!(await page.locator('#sidebar-content').isVisible()),'Mobile library should start collapsed');
        await page.getByRole('button',{name:'Models & presets',exact:true}).click();
      }
      await fits(page,`${width}: library open`);
      await page.locator('.preset-btn').first().click();
      assert((await page.locator('#speech').inputValue()).length>0);
      if(width<=1100) assert(!(await page.locator('#sidebar-content').isVisible()));
      await page.getByRole('button',{name:'Move Qwen3-TTS (1.7B) later',exact:true}).click();
      let order=await page.locator('.voice-card:not(.reference) header > strong').allTextContents();
      assert.deepEqual(order,[models[1].name,models[0].name,models[2].name]);
      await page.getByRole('button',{name:'Remove XTTS-v2',exact:true}).click();
      if(width<=1100) await page.getByRole('button',{name:'Models & presets',exact:true}).click();
      await page.getByRole('button',{name:'XTTS-v2 + Add',exact:true}).click();
      if(width<=1100) await page.getByRole('button',{name:'Close library',exact:true}).click();
      await page.getByRole('button',{name:'English',exact:true}).click();
      await page.getByText('English Reference Active',{exact:true}).waitFor();
      await page.getByRole('button',{name:'German',exact:true}).click();
      await page.getByText('German Reference Active',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Generate Comparison',exact:true}).click();
      await page.locator('.voice-card:not(.reference) audio').first().waitFor();
      await fits(page,`${width}: populated comparison`);
      assert.equal((await page.getByRole('link',{name:'Export All',exact:true}).textContent()).trim(), 'Export All');
      if(width===390 && process.env.RESP_SCREENSHOTS) {
        await mkdir(process.env.RESP_SCREENSHOTS,{recursive:true});
        await page.screenshot({path:`${process.env.RESP_SCREENSHOTS}/mobile-compare.png`,fullPage:true});
      }
      await page.getByRole('button',{name:'Benchmark Metrics',exact:true}).click();
      await page.getByRole('heading',{name:'Model Competition Matrix',exact:true}).waitFor();
      await fits(page,`${width}: metrics`);
      if(width===390 && process.env.RESP_SCREENSHOTS) await page.screenshot({path:`${process.env.RESP_SCREENSHOTS}/mobile-metrics.png`,fullPage:true});
      const table=page.getByRole('region',{name:/Model comparison metrics/});
      if(width<=760) {
        assert(await table.evaluate(e=>e.scrollWidth>e.clientWidth),'Wide metrics should scroll inside their container');
        await table.evaluate(e=>{e.scrollLeft=e.scrollWidth;});
        assert(await table.evaluate(e=>e.scrollLeft>0));
      }
      await page.getByRole('button',{name:/Qwen3-TTS.*Health Scorecard/}).click();
      await page.getByRole('heading',{name:/Detailed Health Report/}).waitFor();
      await fits(page,`${width}: model report`);
      await page.getByRole('button',{name:'Listening Evaluation',exact:true}).click();
      await page.getByLabel('Recording Engine').selectOption('job-0');
      await page.getByLabel('MOS Score').selectOption('4');
      await page.getByLabel('Reviewer identifier').fill('Synthetic reviewer '+width);
      await page.getByRole('button',{name:'Submit Score',exact:true}).click();
      await page.getByText('Rating successfully saved.',{exact:true}).waitFor();
      await fits(page,`${width}: evaluation`);
      if(width===390 && process.env.RESP_SCREENSHOTS) await page.screenshot({path:`${process.env.RESP_SCREENSHOTS}/mobile-evaluation.png`,fullPage:true});
      assert.deepEqual(errors,[],`Unexpected page errors at ${width}`);
      console.log(`PASS ${width}px: navigation, library, ordering, audio UI, metrics scroll, rating`);
      await context.close();
    }
  } finally {
    if(browser) await browser.close();
    server.kill();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
