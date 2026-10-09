/** Synthetic-only Chromium acceptance for progressive trace timeline.
 * No live credentials, Graph, service, or external requests.
 */
const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'templates/traces.html'),'utf8')
 .replace(/\{\{\s*url_for\('static',\s*path='([^']+)'\)\s*\}\}/g, (_,v)=>'/static/'+v);
const events=Array.from({length:1001},(_,i)=>({
  event_id:'event-'+String(i).padStart(4,'0'),
  event_type:i===999?'assignment.failed':i%7===0?'assignment.accepted':'router.started',
  source:'fictional-source',task_id:i%2?'task-X':'task-Y',
  ts_ms:1791500000000,
  payload_json:JSON.stringify({CANARY:'SYNTHETIC_ONLY_'+i})
}));
events.reverse(); // descending event IDs for the equal-timestamp fixture
const origin='https://trace-ui-fixture.invalid';
async function fixture(width) {
 const browser=await chromium.launch({headless:true,
  executablePath:'/home/scott/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
  args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width,height:850}});
 const page=await context.newPage();
 const requests=[];
 await page.route('**/*',async r=>{
   const u=new URL(r.request().url()),key=u.pathname;
   assert.equal(u.origin,origin,'No external network access permitted');
   requests.push({path:key,method:r.request().method(),cursor:u.searchParams.get('cursor')});
   if(key==='/traces')return r.fulfill({status:200,contentType:'text/html',body:html});
   if(key.startsWith('/static/')){
     const f=path.join(root,key.slice(1));
     assert.ok(f.startsWith(root+'/static/'));
     return r.fulfill({status:200,contentType:key.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(f)});
   }
   if(key==='/api/traces')return r.fulfill({status:200,contentType:'application/json',
     body:JSON.stringify({outcome:'all',total:1,traces:[{correlation_id:'huge-synthetic-trace',
       events:1001,outcome:'failed',last_ts_ms:1791500001000,duration_ms:1000}]})});
   if(key==='/api/traces/huge-synthetic-trace')
     assert.fail('No legacy full-detail request permitted');
   if(key==='/api/traces/huge-synthetic-trace/timeline'){
     assert.equal(r.request().method(),'GET');
     assert.equal(u.searchParams.get('limit'),'80');
     const offset=u.searchParams.get('cursor')?Number(u.searchParams.get('cursor').replace('cursor-','')):0;
     assert.ok(Number.isInteger(offset)&&offset>=0&&offset<=1001);
     const page=events.slice(offset,offset+80).map(({payload_json,...e})=>e);
     const has_more=offset+80<events.length;
     return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
       schema:'trace-event-page-v1',correlation_id:'huge-synthetic-trace',
       metadata_only:true,source_snapshot_immutable:false,historical_retention_proven:false,
       events:page,returned:page.length,has_more,
       next_cursor:has_more?'cursor-'+(offset+80):null
     })});
   }
   if(key==='/api/traces/huge-synthetic-trace/payload-preview'){
     assert.equal(r.request().method(),'POST');
     const id=JSON.parse(r.request().postData()).event_id;
     const e=events.find(e=>e.event_id===id);
     assert.ok(e);
     return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
       schema:'trace-payload-preview-v1',correlation_id:'huge-synthetic-trace',
       event_id:id,payload_preview:e.payload_json.slice(0,4096),
       truncated:e.payload_json.length>4096,historical_retention_proven:false
     })});
   }
   return r.fulfill({status:404,body:'Synthetic fixture unsupported route'});
 });
 return {browser,page,requests};
}
for(const width of [375,768,1440]){
 test('synthetic Chromium '+width+'px: progressive long timeline and keyboard type search',async()=>{
  const f=await fixture(width);
  try {
    await f.page.goto(origin+'/traces?trace=huge-synthetic-trace');
    await f.page.locator('.trace-event').first().waitFor();
    assert.equal(await f.page.locator('.trace-event').count(),80);
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 80/);
    assert.equal(await f.page.locator('#trace-detail').textContent().then(x=>x.includes('SYNTHETIC_ONLY_')),false);
    const count=f.requests.length;
    await f.page.locator('.trace-show-earlier').click();
    await f.page.locator('.trace-event').nth(159).waitFor();
    assert.equal(f.requests.length,count+1,'Explicit earlier page triggers one request');
    await f.page.locator('#trace-type-query').fill('assignment.failed');
    assert.equal(await f.page.locator('.trace-event').count(),1);
    assert.equal(await f.page.locator('#trace-type-query').inputValue(),'assignment.failed');
    assert.match(await f.page.locator('.trace-window-status').textContent(),/1 of 1/);
    await f.page.locator('.trace-clear-type').click();
    assert.equal(await f.page.locator('.trace-event').count(),80);
    await f.page.locator('button.trace-context-chip[data-context-field="task_id"]').first().click();
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 80/);
    assert.equal(await f.page.locator('.trace-event').count(),80);
    await f.page.locator('button.trace-clear-context').click();
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 160/);
    await f.page.locator('#trace-type-query').focus();
    assert.equal(await f.page.evaluate(()=>document.activeElement.id),'trace-type-query');
    const metrics=await f.page.evaluate(()=>({
      sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth
    }));
    assert.ok(metrics.sw<=metrics.cw+1,JSON.stringify(metrics));
    assert.equal(f.requests.filter(x=>x.path.endsWith('/payload-preview')).length,0);
    const disclosure=f.page.locator('.trace-event details').first();
    await disclosure.locator('summary').click();
    await f.page.waitForFunction(()=>document.querySelector('.trace-event details pre').textContent.includes('SYNTHETIC_ONLY_'));
    assert.equal(f.requests.filter(x=>x.path.endsWith('/payload-preview')).length,1);
    await disclosure.locator('summary').click();
    await f.page.waitForFunction(()=>document.querySelector('.trace-event details pre').textContent==='');
    assert.ok(f.requests.every(x=>x.path==='/traces'||x.path.startsWith('/static/')||x.path.startsWith('/api/traces')));
  }finally{await f.browser.close()}
 });
}


test('axe WCAG 2.1 A/AA mobile audit: bounded timeline and read-only evidence',async()=>{
 const f=await fixture(375);
 try{
  await f.page.goto(origin+'/traces?trace=huge-synthetic-trace');
  await f.page.locator('.trace-event').first().waitFor();
  const axe=require('@axe-core/playwright').default;
  const result=await new axe({page:f.page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
  assert.deepEqual(result.violations.map(x=>x.id),[]);
 }finally{await f.browser.close()}
});