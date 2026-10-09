/** Synthetic-only Chromium acceptance for progressive trace timeline.
 * No live credentials, Graph, service, or external requests.
 */
const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'templates/traces.html'),'utf8')
 .replace(/\{\{\s*url_for\('static',\s*path='([^']+)'\)\s*\}\}/g, (_,v)=>'/static/'+v);
const events=Array.from({length:1000},(_,i)=>({
  event_type:i===999?'assignment.failed':i%7===0?'assignment.accepted':'router.started',
  source:'fictional-source',task_id:i%2?'task-X':'task-Y',
  ts_ms:1791500000000+i,
  payload_json:JSON.stringify({CANARY:'SYNTHETIC_ONLY_'+i})
}));
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
   requests.push(key);
   if(key==='/traces')return r.fulfill({status:200,contentType:'text/html',body:html});
   if(key.startsWith('/static/')){
     const f=path.join(root,key.slice(1));
     assert.ok(f.startsWith(root+'/static/'));
     return r.fulfill({status:200,contentType:key.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(f)});
   }
   if(key==='/api/traces')return r.fulfill({status:200,contentType:'application/json',
     body:JSON.stringify({outcome:'all',total:1,traces:[{correlation_id:'huge-synthetic-trace',
       events:1000,outcome:'failed',last_ts_ms:1791500001000,duration_ms:1000}]})});
   if(key==='/api/traces/huge-synthetic-trace')return r.fulfill({status:200,contentType:'application/json',
     body:JSON.stringify({correlation_id:'huge-synthetic-trace',current_state:'failed',events,
       context:{schema:'trace-context-v1',fields:{source:[],task_id:[
         {value:'task-X',events:500,provenance:'trace_event_property'}],
         dispatch_id:[],route_id:[],assignment_id:[]},truncated:[]}})});
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
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 1000/);
    assert.equal(await f.page.locator('#trace-detail').textContent().then(x=>x.includes('SYNTHETIC_ONLY_')),false);
    const count=f.requests.length;
    await f.page.locator('.trace-show-earlier').click();
    assert.equal(await f.page.locator('.trace-event').count(),160);
    assert.equal(f.requests.length,count,'UI-only expansion must not re-fetch data');
    await f.page.locator('#trace-type-query').fill('assignment.failed');
    assert.equal(await f.page.locator('.trace-event').count(),1);
    assert.equal(await f.page.locator('#trace-type-query').inputValue(),'assignment.failed');
    assert.match(await f.page.locator('.trace-window-status').textContent(),/1 of 1/);
    await f.page.locator('.trace-clear-type').click();
    assert.equal(await f.page.locator('.trace-event').count(),80);
    await f.page.locator('button.trace-context-chip[data-context-field="task_id"]').click();
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 500/);
    assert.equal(await f.page.locator('.trace-event').count(),80);
    await f.page.locator('button.trace-clear-context').click();
    assert.match(await f.page.locator('.trace-window-status').textContent(),/80 of 1000/);
    await f.page.locator('#trace-type-query').focus();
    assert.equal(await f.page.evaluate(()=>document.activeElement.id),'trace-type-query');
    const metrics=await f.page.evaluate(()=>({
      sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth
    }));
    assert.ok(metrics.sw<=metrics.cw+1,JSON.stringify(metrics));
    assert.ok(f.requests.every(x=>x==='/traces'||x.startsWith('/static/')||x.startsWith('/api/traces')));
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
