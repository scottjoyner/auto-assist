/** Real headless Chromium acceptance for the read-only Trace Investigation UI.
 * All responses are synthetic; only the local HTML/CSS/JS code is loaded.
 * Requires NODE_PATH to point to an existing Playwright install, or playwright
 * in local node_modules. Never contacts production hosts.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const { test }=require('node:test');
const playwright=require('playwright');
const ROOT=path.resolve(__dirname,'..');
const pageHtml=fs.readFileSync(path.join(ROOT,'templates/traces.html'),'utf8')
 .replace(/\{\{\s*url_for\('static',\s*path='([^']+)'\)\s*\}\}/g, (_,p)=>'/static/'+p);
const CSS=fs.readFileSync(path.join(ROOT,'static/css/control_room.css'),'utf8');
const PAGECSS=fs.readFileSync(path.join(ROOT,'static/css/traces.css'),'utf8');
const SCRIPT=fs.readFileSync(path.join(ROOT,'static/js/traces.js'),'utf8');
const BASE='https://test.assistx.invalid';
function row(id,outcome,ms) {
 return {correlation_id:id,events:2,outcome,last_ts_ms:ms,duration_ms:126};
}
const TS=Date.parse('2026-10-08T15:00:00Z');
const fixtures=[row('failed-a','failed',TS),row('completed-b','completed',TS-1000),
                row('open-c','open',TS-2000),row('older-failure','failed',TS-3000)];
function outcome(rows) {
 return rows;
}
async function setupBrowser(width=375,auth='ok',legacy=false) {
 const browser=await playwright.chromium.launch({
   headless:true,
   executablePath:process.env.CHROMIUM_PATH || '/home/scott/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage']
 });
 const context=await browser.newContext({viewport:{width,height:850}, deviceScaleFactor:1,
  permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(), seen=[];
 await page.route('**/*',async route=>{
   const url=new URL(route.request().url()), p=url.pathname;
   if(url.origin!==BASE) throw Error('Browser attempted non-fixture access: '+url.origin);
   if(p==='/traces') return route.fulfill({status:200,contentType:'text/html',body:pageHtml});
   if(p==='/static/css/control_room.css') return route.fulfill({status:200,contentType:'text/css',body:CSS});
   if(p==='/static/css/traces.css') return route.fulfill({status:200,contentType:'text/css',body:PAGECSS});
   if(p==='/static/js/traces.js') return route.fulfill({status:200,contentType:'text/javascript',body:SCRIPT});
   if(p==='/api/traces') {
     seen.push('index:'+url.search);
     if(auth==='unauthorized') return route.fulfill({status:401,contentType:'application/json',body:'{"error":"auth"}'});
     if(auth==='limited') return route.fulfill({status:429,headers:{'Retry-After':'7'},contentType:'application/json',body:'{"detail":"query budget reached"}'});
     const value=url.searchParams.get('outcome');
     let results=fixtures.filter(x=>value?x.outcome===value:true);
     const search=url.searchParams.get('search');
     if(search)results=results.filter(x=>x.correlation_id.includes(search));
     const offset=Number(url.searchParams.get('offset')||0);
     const limit=Number(url.searchParams.get('limit')||50);
     const data={total:results.length,traces:results.slice(offset,offset+limit),limit,offset};
     if(!legacy)data.outcome=value||'all';
     return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
   }
   if(p.startsWith('/api/traces/')){
     const id=decodeURIComponent(p.slice('/api/traces/'.length));
     seen.push('detail:'+id);
     if(auth==='unauthorized') return route.fulfill({status:401,contentType:'application/json',body:'{"error":"auth"}'});
     return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({
       correlation_id:id,current_state:'recorded',events:[
         {event_type:'task.started',source:'synthetic-node',ts_ms:TS-200,payload_json:'{"synthetic_test_marker":"VISIBLE_ONLY_ON_DISCLOSURE"}'},
         {event_type:'task.failed',source:'synthetic-node',ts_ms:TS,payload_json:'{"test_reason":"synthetic failure only"}'}
       ]})});
   }
   return route.fulfill({status:404,contentType:'text/plain',body:'Test fixture: route unavailable'});
 });
 return {browser,page,seen,close:()=>browser.close()};
}
test('real browser: mobile 375px global failed filter, disclosure, keyboard focus and no overflow',async()=>{
 const x=await setupBrowser(375);
 try {
  await x.page.goto(BASE+'/traces?outcome=failed&trace=older-failure');
  await x.page.locator('#trace-status').getByText(/matching failed trace groups/).waitFor({timeout:5000});
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'2');
  assert.equal(await x.page.locator('#trace-loaded-metric').textContent(),'2');
  assert.equal(await x.page.locator('#trace-failed-metric').textContent(),'2');
  assert.equal(await x.page.locator('#trace-outcome').inputValue(),'failed');
  assert.match(await x.page.locator('#trace-detail').textContent(),/older-failure/);
  assert.equal(await x.page.locator('#trace-detail').textContent().then(t=>t.includes('VISIBLE_ONLY_ON_DISCLOSURE')),false);
  const details=x.page.locator('.trace-event details').first();
  await details.locator('summary').click();
  await x.page.waitForFunction(() => {
    const d=document.querySelector('.trace-event details');
    return d && d.open && d.querySelector('pre').textContent.includes('VISIBLE_ONLY_ON_DISCLOSURE');
  });
  await details.locator('summary').click();
  // HTMLDetailsElement dispatches "toggle" asynchronously in Chromium.
  await x.page.waitForFunction(() => {
    const d=document.querySelector('.trace-event details');
    return d && !d.open && d.querySelector('pre').textContent === '';
  });
  const geometry=await x.page.evaluate(()=>({
    scroll:document.documentElement.scrollWidth,
    viewport:document.documentElement.clientWidth,
    panels:Array.from(document.querySelectorAll('.trace-panel')).map(e=>({
      width:Math.round(e.getBoundingClientRect().width),left:Math.round(e.getBoundingClientRect().left)
    }))
  }));
  assert.ok(geometry.scroll<=geometry.viewport+1,JSON.stringify(geometry));
  assert.equal(geometry.panels.length,2);
  assert.ok(geometry.panels[1].left<geometry.viewport);
  await x.page.locator('#trace-search').focus();
  assert.equal(await x.page.evaluate(()=>document.activeElement.id),'trace-search');
  assert.equal(await x.page.locator('a[href="/provider-usage"]').count(),0);
  await x.page.screenshot({path:'/tmp/assistx-trace-mobile-synthetic-20261008.png',fullPage:true});
 }finally {await x.close();}
});
test('real browser: desktop 1440px outcome changes and responsive side-by-side panes',async()=>{
 const x=await setupBrowser(1440);
 try{
  await x.page.goto(BASE+'/traces');
  await x.page.locator('#trace-status').getByText(/matching all-outcome trace groups/).waitFor();
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'4');
  await x.page.locator('#trace-outcome').selectOption('completed');
  await x.page.locator('#trace-status').getByText(/matching completed trace groups/).waitFor();
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'1');
  assert.match(x.page.url(),/outcome=completed/);
  const bounds=await x.page.evaluate(()=>{
    const [l,r]=document.querySelectorAll('.trace-panel');
    return {left:l.getBoundingClientRect().left,right:r.getBoundingClientRect().left,
            width:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth};
  });
  assert.ok(bounds.right>bounds.left+200,JSON.stringify(bounds));
  assert.ok(bounds.width<=bounds.viewport+1,JSON.stringify(bounds));
  await x.page.screenshot({path:'/tmp/assistx-trace-desktop-synthetic-20261008.png',fullPage:true});
 }finally{await x.close();}
});
test('real browser: 401 shows auth failure and does not invent zero results',async()=>{
 const x=await setupBrowser(768,'unauthorized');
 try{
  await x.page.goto(BASE+'/traces?outcome=failed');
  await x.page.locator('#trace-status').getByText(/Authentication required or expired/).waitFor();
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'—');
  assert.equal(await x.page.locator('#trace-list #trace-retry').count(),1);
 }finally{await x.close();}
});
test('real browser: old backend reports incompatibility and unknown metrics',async()=>{
 const x=await setupBrowser(768,'ok',true);
 try{
  await x.page.goto(BASE+'/traces?outcome=failed');
  await x.page.locator('#trace-status').getByText(/Global outcome filtering is not yet available/).waitFor();
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'—');
 }finally{await x.close();}
});


test('real browser: axe WCAG 2.1 A/AA mobile audit on synthetic trace data', async()=>{
 const x=await setupBrowser(375);
 try{
  const AxeBuilder=require('@axe-core/playwright').default;
  await x.page.goto(BASE+'/traces?outcome=failed');
  await x.page.locator('#trace-status').getByText(/matching failed trace groups/).waitFor();
  const results=await new AxeBuilder({page:x.page})
      .withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
  console.log('AXE',JSON.stringify(results.violations.map(v=>({
    id:v.id,impact:v.impact,description:v.description,
    nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary?.slice(0,130)})).slice(0,7)
  }))));
  assert.equal(results.violations.length,0,'WCAG 2.1 A/AA automated violations');
 }finally{await x.close();}
});

test('real browser: tablet 768px keyboard-only selection and payload disclosure', async()=>{
  const x=await setupBrowser(768);
  try{
    await x.page.goto(BASE+'/traces');
    await x.page.locator('#trace-status').getByText(/matching all-outcome trace groups/).waitFor();
    const geometry=await x.page.evaluate(()=>{
      const panels=[...document.querySelectorAll('.trace-panel')];
      const boxes=panels.map(p=>p.getBoundingClientRect());
      return {width:document.documentElement.scrollWidth,
        viewport:document.documentElement.clientWidth,
        leftTop:boxes[0].top,leftBottom:boxes[0].bottom,
        rightTop:boxes[1].top};
    });
    assert.ok(geometry.width<=geometry.viewport+1,JSON.stringify(geometry));
    assert.ok(geometry.rightTop>=geometry.leftBottom-1,
      'tablet panes should stack instead of squeezing horizontally: '+JSON.stringify(geometry));
    const second=x.page.locator('#trace-list button.trace-row').nth(1);
    await second.focus();
    assert.equal(await second.evaluate(e=>document.activeElement===e),true);
    await x.page.keyboard.press('Enter');
    await x.page.locator('#trace-detail .trace-id').getByText('completed-b').waitFor();
    const details=x.page.locator('.trace-event details').first();
    await details.locator('summary').focus();
    await x.page.keyboard.press('Enter');
    await x.page.waitForFunction(()=>{
      const d=document.querySelector('.trace-event details');
      return d && d.open && d.querySelector('pre').textContent.includes('VISIBLE_ONLY_ON_DISCLOSURE');
    });
    await x.page.keyboard.press('Enter');
    await x.page.waitForFunction(()=>{
      const d=document.querySelector('.trace-event details');
      return d && !d.open && d.querySelector('pre').textContent === '';
    });
    const AxeBuilder=require('@axe-core/playwright').default;
    const report=await new AxeBuilder({page:x.page})
      .withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
    assert.equal(report.violations.length,0,
      'tablet automated axe violations: '+report.violations.map(v=>v.id).join(','));
  }finally{await x.close();}
});

test('real browser: Redis budget 429 clearly states Retry-After without false zero', async()=>{
 const x=await setupBrowser(375,'limited');
 try{
  await x.page.goto(BASE+'/traces?outcome=failed');
  await x.page.locator('#trace-status').getByText(/Retry in 7 seconds/).waitFor();
  assert.equal(await x.page.locator('#trace-total-metric').textContent(),'—');
  assert.equal(await x.page.locator('#trace-list #trace-retry').count(),1);
 }finally{await x.close();}
});
