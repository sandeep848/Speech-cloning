/* Browser regression: authenticated cross-site audio/downloads without cookies.
   This uses isolated API fixtures, never real credentials or inference. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const path = require('node:path');
const net = require('node:net');
const ready=()=>new Promise(resolve=>{const socket=net.createConnection({host:'127.0.0.1',port});socket.setTimeout(200);socket.once('connect',()=>{socket.destroy();resolve(true);});socket.once('error',()=>{socket.destroy();resolve(false);});socket.once('timeout',()=>{socket.destroy();resolve(false);});});
const port=4176, origin=`http://127.0.0.1:${port}`, api='https://fixture-api.example.test';
const server=spawn(process.execPath,[path.resolve(__dirname,'../node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',String(port),'--strictPort'],{cwd:path.resolve(__dirname,'..'),env:{...process.env,VITE_API_URL:api},stdio:'ignore'});
const wav=Buffer.alloc(44+4800);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(4800,40);
(async()=>{
  let browser;
  try {
    for(let i=0;i<100;i++){
      if(await ready())break;
      if(i===99)throw Error('Fixture frontend did not start');
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE||undefined,args:JSON.parse(process.env.CHROMIUM_ARGS||'[]')});
    const context=await browser.newContext();const page=await context.newPage();
    let valid=false, audioRequests=0, logouts=0;
    const secret='synthetic-session-for-browser-test';
    await page.route(api+'/**',async route=>{
      const request=route.request(), pathname=new URL(request.url()).pathname;
      const headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'Authorization,Content-Type,X-Studio-CSRF','Access-Control-Allow-Methods':'GET,POST,PATCH,DELETE,OPTIONS'};
      if(request.method()==='OPTIONS')return route.fulfill({status:204,headers});
      if(pathname==='/api/session'&&request.method()==='POST'){
        assert.equal(request.postDataJSON().transport,'bearer');valid=true;
        return route.fulfill({headers,json:{authenticated:true,session_token:secret}});
      }
      const authorized=valid&&request.headers().authorization==='Session '+secret;
      if(pathname==='/api/session'&&request.method()==='GET')return route.fulfill({headers,json:{auth_mode:'accounts',authenticated:false}});
      if(!authorized)return route.fulfill({headers,status:401,json:{detail:'Unauthorized'}});
      if(pathname==='/api/session'&&request.method()==='DELETE'){valid=false;logouts++;return route.fulfill({headers,json:{authenticated:false}});}
      if(pathname.endsWith('/audio')){audioRequests++;return route.fulfill({headers,contentType:'audio/wav',body:wav});}
      if(pathname.startsWith('/api/exports/'))return route.fulfill({headers,contentType:'application/zip',body:Buffer.from('isolated browser download fixture')});
      let body={};
      if(pathname==='/api/models')body=[{id:'qwen3',name:'Qwen3-TTS',configured:true,ready:true}];
      else if(pathname==='/api/reference-profile')body={reference:{id:'reference',filename:'reference.wav'}};
      else if(pathname==='/api/workspace')body={active:['qwen3']};
      else if(pathname==='/api/generations'&&request.method()==='POST')body={id:'generation',jobs:[{id:'job',model_id:'qwen3',status:'pending'}]};
      else if(pathname.startsWith('/api/generations/'))body={id:'generation',text:'A private fixture generation.',jobs:[{id:'job',model_id:'qwen3',status:'complete',audio_url:'/api/recordings/job/audio',runtime_seconds:.1}]};
      else if(pathname.startsWith('/api/evaluations'))body={metrics:[],errors:[],status:'completed'};
      return route.fulfill({headers,json:body});
    });
    await page.goto(origin);
    await page.getByLabel('Username',{exact:true}).fill('synthetic-user');
    await page.getByLabel('Password',{exact:true}).fill('synthetic-password-for-test');
    await page.getByRole('button',{name:'Connect Session',exact:true}).click();
    await page.getByText('German Reference Active',{exact:true}).waitFor();
    await page.locator('#speech').fill('A private fixture generation.');
    const generationRequest = page.waitForResponse(r => new URL(r.url()).pathname === '/api/generations' && r.request().method() === 'POST');
    await page.getByRole('button',{name:'Generate Comparison',exact:true}).click();
    assert.equal((await generationRequest).status(),200);
    await page.locator('.voice-card:not(.reference) audio').first().waitFor();
    const downloadPromise=page.waitForEvent('download');
    await page.locator('.btn-card-download').first().click();
    assert((await downloadPromise).suggestedFilename().endsWith('.wav'));
    assert(audioRequests>0);
    assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes(secret));
    assert.equal((await context.cookies()).length,0);
    await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await page.getByRole('button',{name:'Connect Session',exact:true}).waitFor();
    assert.equal(logouts,1);
    assert.equal(await page.locator('audio').count(),0);
    assert.equal(await page.locator('#speech').inputValue(),'');
    assert.equal(await page.evaluate(()=>localStorage.getItem('voice.batch')),null);
    console.log('PASS cross-site: no-cookie login, authenticated playback/download, no persistent token, revoked logout');
  } finally {if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
