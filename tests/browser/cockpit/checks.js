/* Run this function in the existing Playwright session. No installs or providers.
   Browser interception below tests failure/contract states, not live product proof. */
async (page, {baseURL, outputDir, canonicalBaseURL, steeringOnly=false, r2Only=false, r4Only=false, planSnapshot, r4bOnly=false, planFacts, contractsOnly=false, recordedReviewsOnly=false, recordedReviewSnapshot, recordedConfirmationSnapshot} = {}) => {
  if (!baseURL || !outputDir) throw new Error('Pass the loopback baseURL and runtime evidence directory.');
  const checks=[],shots=[],metrics=[],errors=[],requests=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>requests.push({url:r.url(),method:r.method()}));
  const check=(pass,name,detail)=>{checks.push({name,passed:!!pass,...(detail!==undefined?{detail}:{})});if(!pass)throw new Error(name+': '+JSON.stringify(detail));};
  const go=async(query='',width=1440)=>{await page.setViewportSize({width,height:900});await page.goto(baseURL+'/?'+query);await page.waitForFunction(()=>document.querySelector('#freshness').textContent!=='Not refreshed yet');};
  const capture=async(name,fullPage=false)=>{await page.mouse.move(0,0);await page.locator(':focus').evaluateAll(es=>es.forEach(e=>e.blur()));await page.screenshot({path:outputDir+'/'+name+'.png',fullPage,animations:'disabled'});shots.push(name+'.png');};
  if(recordedReviewsOnly){
    if(!recordedReviewSnapshot?.cockpit?.recordedReviews)throw new Error('Pass the actual disposable-store recorded review snapshot.');
    let payload=structuredClone(recordedReviewSnapshot);
    await page.route('**/api/status',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)}));
    try{
      for(const width of [1440,375]){
        await go('mode=live',width);
        check((await page.locator('#right-now').innerText()).includes('1 recorded review points still need a response.'),'Producer plain review summary reaches header');
        await page.locator('#recorded-reviews').focus();await page.keyboard.press('Enter');
        const dialog=page.locator('dialog'),text=await dialog.innerText();
        check(text.includes('Model requested: requested-alpha')&&text.includes('Model requested: requested-beta'),'Requested models retain their actual recorded meaning');
        check(text.includes('Historical review')&&text.includes('Source-only / simulated review'),'Historical and simulated records are explicit');
        const shared=page.locator('.review-cluster').filter({has:page.locator('summary', {hasText:'2 members'})}).first();
        await shared.locator('summary').click();
        check((await shared.innerText()).includes('Review Alpha')&&(await shared.innerText()).includes('Review Beta'),'Both recorded cluster members remain attributed');
        for(const summary of await page.locator('.review-cluster:not([open]) summary').all())await summary.click();
        const detail=await dialog.innerText();
        check(['resolved','rejected','deferred'].every(status=>detail.includes('Disposition: '+status)&&detail.includes('Recorded '+status+' reason')),'Recorded dispositions and reasons remain distinct');
        check(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth),'Recorded details wrap inside dialog');
        await capture('recorded-reviews-'+width);
        await page.keyboard.press('Escape');check(await dialog.evaluate(e=>!e.open),'Keyboard Escape closes recorded details');
      }
      if(recordedConfirmationSnapshot){
        payload=structuredClone(recordedConfirmationSnapshot);await go('mode=live',375);
        await page.locator('#needs').click();
        const decision=await page.locator('dialog').innerText();
        check(decision.includes('Recorded confirmation: Daily tracker understanding - answer in chat.'),'Existing approval item shows recorded title and chat direction');
        check(decision.includes('The chat connection and whether a prompt is still open are unknown.'),'Recorded understanding never implies active connection');
        check(await page.locator('dialog').evaluate(e=>e.scrollWidth<=e.clientWidth),'Recorded understanding wraps at 375px');
        await capture('recorded-understanding-375');await page.keyboard.press('Escape');
        payload=structuredClone(recordedReviewSnapshot);
      }
      const current=payload.cockpit.recordedReviews.rounds.find(r=>r.relevance==='current');
      current.clusters[0].members[0].text='<img src=x onerror="window.__reviewXss=1">';
      await go('mode=live');await page.locator('#recorded-reviews').click();
      const hostile=page.locator('.review-cluster').first();await hostile.locator('summary').click();
      check((await hostile.innerText()).includes('<img src=x'),'Hostile finding remains literal text');
      check(await page.locator('dialog img, dialog script').count()===0&&await page.evaluate(()=>window.__reviewXss===undefined),'Recorded finding text never executes');
      await page.keyboard.press('Escape');
      payload.cockpit.recordedReviews.sourceRevision='foreign';payload.cockpit.answers.rightNow.sourceRevision='foreign';
      await go('mode=live');await page.locator('#recorded-reviews').click();
      check((await page.locator('dialog').innerText()).includes('Recorded review details are unavailable.'),'Foreign review projection is refused');
      check(!(await page.locator('#right-now').innerText()).includes('1 recorded review points'),'Foreign headline is refused');
      check(errors.length===0,'No recorded-review browser runtime errors',errors);
      check(requests.every(r=>r.method==='GET'),'Recorded-review flow stays GET-only');
      return {passed:true,checks,screenshots:shots,evidenceNature:'Actual disposable-store producer snapshot with simulated reviewer returns, replayed in browser. Hostile/binding mutations are explicit consumer doubles; no provider or native-host proof.'};
    }finally{await page.unroute('**/api/status');}
  }
  if(r4bOnly){
    if(!planFacts?.draft||!planFacts?.scenario)throw new Error('Pass DOT actual canonical draft/scenario snapshots.');
    const expect=(pass,name,detail)=>checks.push({name,passed:!!pass,...(detail!==undefined?{detail}:{})});
    let payload=structuredClone(planFacts.draft);
    await page.route('**/api/status',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)}));
    try{
      await go('mode=live');
      expect(await page.locator('.wave').count()===2&&(await page.locator('#wave-lens').innerText()).includes('Draft plan'),'R4b actual plan draft preserves W1/W2 without a dispatch run');
      expect(await page.locator('.wave[aria-pressed="true"]').count()===0&&!(await page.locator('#where').innerText()).includes('wave'),'R4b draft declaration order never becomes current');
      if(await page.locator('.wave').count()){await page.locator('.wave').first().click();expect(!(await page.locator('.wave').first().getAttribute('aria-label')).includes('current execution')&&!(await page.locator('#wave-lens').innerText()).includes('Current wave'),'R4b selecting draft lens never advances execution');}
      await capture('r4b-actual-draft',true);
      payload=structuredClone(planFacts.scenario);await go('mode=live');
      const rail=await page.locator('#truth-rail').innerText();
      expect(/Product scenario checks: 4\s*\/\s*4/.test(rail),'R4b actual canonical global 4/4 rendered',rail);
      expect((await page.locator('[data-cap="F1"] .rings').getAttribute('aria-label')).includes('Product scenario checks: 1 / 1'),'R4b actual F1 shows only its linked 1/1 scenario');
      expect(await page.locator('[data-cap="F2"] .rings.neutral').count()===1&&await page.locator('.rings .outer').count()===1,'R4b ambiguous/unlinked scenarios stay global; F2 unknown');
      expect(await page.locator('.rings .inner').count()===0,'R4b requirement validation remains independently null');
      expect(await page.locator('.crew-chip').count()===0&&!rail.includes('Accepted by proxy')&&!rail.includes('Verified personal acceptance'),'R4b scenario checks invent no current crew or acceptance');
      expect((await page.locator('#context').innerText()).includes('Product scenario checks: 4/4'),'R4b Vault shows canonical global result');
      await capture('r4b-actual-scenarios',true);
      for(const name of ['draft','scenario']){
        payload=structuredClone(planFacts[name]);await go('mode=live',375);
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'R4b '+name+' new plan-fact text fits 375px');
        await capture('r4b-actual-'+name+'-375',true);
      }
      payload=structuredClone(planFacts.scenario);await go('mode=live');
      await page.locator('[data-cap="F1"]').click();
      expect((await page.locator('#context').innerText()).includes('Product scenario checks: 1 / 1')&&(await page.locator('#context').innerText()).includes('Requirement validation: not tracked yet'),'R4b drill preserves separate scenario/requirement units');
      for(const fault of ['stale-plan','missing-plan','oracle','null','missing-build']){
        payload=structuredClone(planFacts.scenario);
        if(fault==='stale-plan')payload.cockpit.source.planRevision='foreign-plan';
        else if(fault==='missing-plan')delete payload.cockpit.source.planRevision;
        else for(const proof of [payload.cockpit.vision.proof,...payload.cockpit.capabilities.map(c=>c.proof)]){if(!proof.productScenarios)continue;if(fault==='null')proof.productScenarios=null;else if(fault==='oracle')proof.productScenarios.subject='oracle';else delete proof.productScenarios.buildRevision;}
        await go('mode=live');
        expect(!/Product scenario checks: 4\s*\/\s*4/.test(await page.locator('#truth-rail').innerText())&&await page.locator('.rings .outer').count()===0,'R4b '+fault+' suppresses global and capability numerators');
      }
      payload=structuredClone(planFacts.scenario);payload.cockpit.source.planRevision='foreign-plan';payload.cockpit.vision.title='Foreign vision';payload.cockpit.vision.promise='Foreign promise';payload.cockpit.capabilities[0].title='Foreign capability title';payload.cockpit.capabilities.push({id:'FOREIGN',title:'Foreign topology'});await go('mode=live');
      expect(await page.locator('[data-cap="FOREIGN"]').count()===0&&!(await page.locator('#stable-map').innerText()).includes('Foreign'),'R4b conflicting projection cannot replace canonical topology/title/promise');
      expect((await page.locator('.vision-node').innerText()).includes('Recorded promise'),'R4b canonical promise survives conflicting optional source');
      payload=structuredClone(planFacts.draft);payload.cockpit.source.planRevision='foreign-plan';await go('mode=live');
      expect(await page.locator('.wave').count()===0,'R4b conflicting draft source cannot supply waves');
      expect(errors.length===0,'R4b no browser runtime errors',errors);
      return {passed:checks.every(c=>c.passed),checks,screenshots:shots,evidenceNature:'Actual canonical draft/scenario producer snapshots replayed in browser; explicit mutations test source conflicts/unknowns. No dispatch/provider or full-vision proof.'};
    }finally{await page.unroute('**/api/status');}
  }
  if(r4Only){
    if(!planSnapshot)throw new Error('Pass the actual canonical v2 snapshot; do not substitute a synthetic plan.');
    let payload=structuredClone(planSnapshot);
    await page.route('**/api/status',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)}));
    try{
      for(const staleRun of [false,true]){
        payload=structuredClone(planSnapshot);
        if(staleRun){payload.currentRunId='old-run';payload.runs=[{runId:'old-run',planRevision:'old-P1',beads:[{id:'OLD',status:'done'}]}];payload.currentAgents=[{provider:'old-provider'}];payload.cockpit.answers.blocking.push({id:'OLD-BLOCK',plain:'Old runtime blocker',refs:['run:old-run']});payload.cockpit.answers.needsYou.push({id:'OLD-Q',question:'Old runtime question',refs:['run:old-run']});}
        await go('mode=live');
        check(await page.locator('#blocking strong').innerText()==='1 blocker','R4 '+staleRun+': canonical plan blocker survives runtime ineligibility');
        check(await page.locator('#needs strong').innerText()==='2 decisions','R4 '+staleRun+': canonical question and gate survive');
        check(await page.locator('.crew-chip').count()===0&&!(await page.locator('#truth-rail').innerText()).includes('1 done'),'R4 '+staleRun+': old runtime crew/done stay unavailable');
        await page.locator('#blocking').click();const blocker=await page.locator('dialog').innerText();
        check(blocker.includes('Canonical planning blocker before execution')&&!blocker.includes('Old runtime blocker'),'R4 '+staleRun+': Blocking opens only the plan finding');await page.keyboard.press('Escape');
        await page.locator('#needs').click();const needs=await page.locator('dialog').innerText();
        check(needs.includes('Canonical planning question before execution')&&!needs.includes('Old runtime question'),'R4 '+staleRun+': Needs you opens only plan answers');await page.keyboard.press('Escape');
        await capture('r4-plan-'+(staleRun?'stale-run':'no-run'),true);
      }
      payload=structuredClone(planSnapshot);payload.cockpit.source.planRevision='foreign-plan';await go('mode=live');
      check(await page.locator('#blocking strong').innerText()==='Not tracked yet'&&await page.locator('#needs strong').innerText()==='Not tracked yet','R4 conflicting source plan identity cannot supply planning answers');
      payload=structuredClone(planSnapshot);payload.cockpit.answers.blocking=null;payload.cockpit.answers.needsYou=null;await go('mode=live');
      check(await page.locator('#blocking strong').innerText()==='Not tracked yet'&&await page.locator('#needs strong').innerText()==='Not tracked yet','R4 explicit-null planning answers remain unknown');
      check(errors.length===0,'R4 no browser runtime errors',errors);
      return {passed:true,checks,screenshots:shots,evidenceNature:'Actual v2 SQLite/status_api snapshot replayed in the browser; stale run additions are explicit synthetic fault inputs. No provider review or live HTTP producer claim.'};
    }finally{await page.unroute('**/api/status');}
  }
  if(r2Only){
    const expect=(pass,name,detail)=>checks.push({name,passed:!!pass,detail});
    await go('mode=history&project=linkshelf');
    const rail=await page.locator('#truth-rail').innerText();
    expect(rail.includes('5 work items · 5 done')&&!rail.includes('state unknown'),'R2 actual linkshelf: all five validated work records done, zero unknown',rail);
    for(const [id,total] of [['F1',2],['F2',3],['F3',1],['F4',3],['F5',1]]){
      const node=page.locator('[data-cap="'+id+'"]'),copy=await node.innerText();
      expect(copy.includes(total+' work items · '+total+' done'),'R2 actual linkshelf: '+id+' scoped done count',copy);
    }
    expect(await page.locator('.rings.neutral').count()===5&&await page.locator('.rings .outer,.rings .inner').count()===0,'R2 reused work never fills proof rings');
    await capture('r2-history-linkshelf-1440',true);
    await page.locator('[data-view="board"]').click();await page.locator('.board-card').filter({hasText:'B1 ·'}).click();
    const detail=await page.locator('dialog').innerText();
    expect(detail.includes('done (reused from an earlier validated attempt)')&&detail.includes('"recordedStatus": "reused"'),'R2 reused work description and original status preserved',detail);
    await page.keyboard.press('Escape');
    return {passed:checks.every(c=>c.passed),checks,screenshots:shots,runtimeErrors:errors};
  }
  if(steeringOnly){
    const expect=(pass,name,detail)=>checks.push({name,passed:!!pass,detail});
    const stale={plan:{phase:'execute',plan_revision:'P2',published_revision:'P2',spine:[{id:'V2',kind:'vision',title:'Current P2 vision'},{id:'F2',kind:'feature',title:'Current capability',parents:['V2']},{id:'W2',kind:'bead',title:'Current canonical work',parents:['F2']}]},currentRunId:'old-P1',runs:[{runId:'old-P1',planRevision:'P1',beads:[{id:'OLD-ORPHAN',title:'Old blocked orphan',status:'blocked'},{id:'W2',status:'done'}]}],currentAgents:[{provider:'old-provider',model:'old-model'}]};
    let payload=stale;
    await page.route('**/api/status',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)}));
    for(const kind of ['a-explicit-null','b-fields-absent']){
      payload=structuredClone(stale);if(kind==='a-explicit-null')payload.cockpit={answers:{blocking:null},crew:null};
      await go('mode=live');
      expect((await page.locator('#blocking strong').innerText())==='Not tracked yet',kind+': no current old blocker');
      expect(await page.locator('.crew-chip').count()===0,kind+': old crew is not current');
      const rail=await page.locator('#truth-rail').innerText();expect(/1 work items?/.test(rail)&&!rail.includes('2 work'),kind+': canonical population excludes orphan',rail);
      expect((await page.locator('body').innerText()).includes('Earlier run'),kind+': old run is labelled earlier history');
      await page.locator('[data-view="board"]').click();expect(!(await page.locator('.board').innerText()).includes('OLD-ORPHAN'),kind+': old orphan is not actionable current work');
    }
    payload={plan:{plan_revision:'P2',published_revision:'P2',publication:{up_to_date:true}},currentRunId:'run-P2',runs:[{runId:'run-P2',planRevision:'P2',beads:[{id:'RUN-ONLY',status:'running'}]}]};
    await go('mode=live');const runRail=await page.locator('#truth-rail').innerText();
    expect(runRail.includes('Recorded run work')&&!runRail.includes('Global:'),'c: absent canonical membership has run scope only',runRail);
    payload=structuredClone(stale);payload.runs[0].planRevision='P2';payload.plan.publication={up_to_date:true};payload.runs[0].beads=[{id:'W2',status:'blocked'}];payload.cockpit={answers:{blocking:null},crew:null};
    await go('mode=live');expect((await page.locator('#blocking strong').innerText())==='Not tracked yet'&&await page.locator('.crew-chip').count()===0,'matching run: explicit null still overrides fallbacks');
    delete payload.cockpit;await go('mode=live');expect((await page.locator('#blocking strong').innerText())==='1 blocker'&&await page.locator('.crew-chip').count()===1,'matching valid run remains usable when optional fields absent');
    await page.unroute('**/api/status');
    for(const project of ['linkshelf','csv2md']){
      await go('mode=history&project='+project);
      const rail=await page.locator('#truth-rail').innerText(),n=project==='linkshelf'?5:8;
      expect(new RegExp('Product scenario checks: '+n+'\\s*/\\s*'+n).test(rail)&&rail.includes('recorded run'),'history '+project+': actual global recorded scenario result surfaced',rail);
      expect((await page.locator('#where strong').innerText()).includes('Done')&&(await page.locator('#where strong').innerText()).toLowerCase().includes('proxy'),'history '+project+': where answers recorded proxy completion');
      expect((await page.locator('#blocking strong').innerText())==='Nothing open','history '+project+': no fabricated open work');
      expect((await page.locator('#needs strong').innerText()).includes('Your acceptance'),'history '+project+': personal review remains optional and separate');
      const crew=await page.locator('#crew').innerText();expect(crew.includes('Workers used in this run')&&!crew.includes('liveness')&&!crew.includes('identity not tracked'),'history '+project+': past worker wording',crew);
      expect(await page.locator('#coverage').count()===1,'history '+project+': one coverage chip');
      if(project==='linkshelf'){
        await page.locator('[data-cap="F2"]').click();expect(await page.locator('[data-node="S2"]').count()===1&&await page.locator('.work-node').count()>0,'actual copied linkshelf raw rows decode to story/work trace');
        await page.getByRole('button',{name:'← Overview',exact:true}).click();
        await page.locator('#truth-rail button').last().click();const detail=await page.locator('dialog').innerText();expect(detail.includes('opus-user-proxy')&&detail.includes('dogfood only')&&detail.includes('reboundFrom'),'historical approval preserves actor, scope and rebind provenance');await page.keyboard.press('Escape');
      }
    }
    return {passed:checks.every(c=>c.passed),checks,runtimeErrors:errors};
  }
  if(!contractsOnly){
  await go('mode=synthetic');
  check(await page.locator('[data-node]').count()===11,'Product implementation: synthetic overview has 11 nodes');
  check((await page.locator('#truth-rail').innerText()).includes('120 work items · 45 done'),'Global count derives from the 120 work records');
  check((await page.locator('#where').innerText()).includes('Wave 3 of 9'),'Actual synthetic wave is explicitly identified');
  check((await page.locator('#blocking strong').innerText())==='1 blocker'&&(await page.locator('#needs strong').innerText())==='2 decisions','One blocker is distinct from three affected items and two decisions');
  check(await page.locator('.crew-chip').count()===5&&await page.locator('.crew-chip.quiet').count()===2,'Five recorded crew identities; two advisory quietness flags');
  check((await page.locator('#crew').innerText()).includes('gpt-6.1-sol')&&(await page.locator('#crew').innerText()).includes('Sonnet 4.5'),'Recorded provider and model identities are present');
  check(await page.locator('.area-frame').count()>0,'Synthetic canonical area metadata groups capabilities');
  const positions=()=>page.locator('[data-cap]').evaluateAll(es=>es.map(e=>({id:e.dataset.cap,x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y})));
  const before=await positions();await capture('01-synthetic-macro-forest');
  await page.getByRole('button',{name:'Find a wave',exact:true}).click();
  check(await page.locator('#picker-results .board-card').count()===5,'Wave picker has at most five results');
  await page.locator('#picker-search').fill('shared household');await page.locator('#picker-results .board-card').click();
  check(new URL(page.url()).searchParams.get('wave')==='wave-5','Wave search selects the fifth wave');
  check(JSON.stringify(before)===JSON.stringify(await positions()),'Wave lens keeps exact map positions and capability identities');
  check(await page.locator('#stable-map').count()===1&&await page.locator('[data-node]').count()===11,'Wave selection does not duplicate the overview');
  check((await page.locator('#where').innerText()).includes('Wave 3 of 9'),'Selected Wave 5 does not advance current Wave 3');
  check((await page.locator('#wave-lens').innerText()).includes('15 work items'),'Selected wave count comes from its work records');
  await capture('02-wave-5-forest');
  await page.locator('[data-cap="C5"]').hover();
  check(await page.locator('#preview').isVisible()&&(await page.locator('#preview').innerText()).includes('Why:'),'Capability hover preview gives what, state and why');
  await page.mouse.move(0,0);await page.locator('[data-cap="C5"]').focus();
  check(await page.locator('#preview').isVisible(),'Keyboard focus exposes the same node preview');
  await page.keyboard.press('Escape');check(!await page.locator('#preview').isVisible(),'Escape dismisses the preview');
  await page.getByRole('button',{name:'Find a capability',exact:true}).click();
  check(await page.locator('#picker-results .board-card').count()===5,'Capability picker has at most five results');
  await page.locator('#picker-search').fill('care');await page.locator('#picker-results .board-card').click();
  check(await page.locator('#stable-map').count()===0&&await page.locator('.drill').count()===1,'Semantic drill replaces the overview');
  check((await page.locator('.crumbs').innerText()).includes('Share')&&(await page.locator('.crumbs').innerText()).includes('Share with care'),'Breadcrumb distinguishes area from capability');
  check(await page.locator('[aria-expanded="true"]').count()===1,'One story path is expanded');
  check((await page.locator('.story-node').first().innerText()).includes('6 linked work items'),'Story count is six work records, not visible cards');
  check(await page.locator('[data-node]').count()<=40,'Drill remains within the node budget');
  await capture('03-drill-forest');
  await page.getByRole('button',{name:'Inspect capability ↗',exact:true}).click();
  check((await page.locator('dialog').innerText()).includes('W9'),'Capability inspector exposes the cross-branch dependency');
  check((await page.locator('dialog').innerText()).includes('Product scenario checks: not tracked yet')&&(await page.locator('dialog').innerText()).includes('Requirement validation: not tracked yet'),'Separate proof units remain null; done work is not a proof numerator');
  await page.keyboard.press('Escape');await page.locator('.story-node').nth(1).click();
  check(await page.locator('[aria-expanded="true"]').count()===1&&await page.locator('.count-node').count()>0,'Switching story collapses the other path and retains count groups');
  await page.getByRole('button',{name:'← Overview',exact:true}).click();
  check(new URL(page.url()).searchParams.get('wave')==='wave-5'&&await page.locator('[data-cap="C5"].last-selected').count()===1,'Return preserves lens and selected capability');
  for(let cycle=0;cycle<3;cycle++){
    await page.locator('[data-cap="C5"]').click();
    check(await page.locator('.drill').count()===1&&await page.locator('[data-node]').count()<=40,'Repeated drill '+cycle+' replaces the map within budget');
    await page.getByRole('button',{name:'← Overview',exact:true}).click();
    check(await page.locator('[data-node]').count()===11&&new URL(page.url()).searchParams.get('wave')==='wave-5','Repeated return '+cycle+' keeps lens and bounded overview');
  }
  await page.locator('[data-palette="navy"]').click();check(await page.locator('html').getAttribute('data-palette')==='navy','Navy control works');await capture('04-macro-navy');
  await page.locator('[data-palette="light"]').click();check(await page.locator('html').getAttribute('data-palette')==='light','Light control works');await capture('05-macro-light');
  await page.locator('#plain').click();
  check(await page.locator('#view-title').innerText()==='Your product map'&&await page.locator('[data-view="story"]').innerText()==='Story'&&!(await page.locator('#context').innerText()).includes('The Mule'),'Plain mode removes theme vocabulary without changing meaning');
  await go('mode=synthetic&view=board');await capture('06-board-forest');
  check((await page.locator('.map-toolbar').innerText()).includes('Global: 120 work items'),'Board scope is explicitly global');
  const columnTotal=await page.locator('.board-column h3').evaluateAll(es=>es.reduce((n,e)=>n+Number(e.textContent.split(' · ').at(-1)),0));check(columnTotal===120,'Board columns partition the work records, not visible cards',columnTotal);
  await page.locator('.board-card').first().click();
  check((await page.locator('dialog').innerText()).includes('Dependencies')&&(await page.locator('dialog').innerText()).includes('integratedValidation'),'Work inspector exposes dependency and original evidence fields');
  await page.keyboard.press('Escape');await page.locator('#needs').click();
  check((await page.locator('dialog').innerText()).includes('Answer in chat')&&await page.locator('.decision').count()===2,'Decision cards are view-only and keep the two choices distinct');
  await capture('07-decisions-forest');await page.keyboard.press('Escape');
  for(const project of ['linkshelf','csv2md']){
    await go('mode=history&project='+project);
    check((await page.locator('#source-banner').innerText()).includes('HISTORICAL REPLAY'),'Public '+project+' has an explicit historical mode');
    check(await page.locator('[data-cap]').count()>0,'Public '+project+' uses its actual capability records');
    check(await page.locator('.area-frame').count()===0,'Public '+project+' has no invented product-area grouping');
    check(await page.locator('.wave-unavailable').count()===1&&await page.locator('.wave').count()===0,'Public '+project+' does not fabricate outcome waves');
    check((await page.locator('#truth-rail').innerText()).includes('not you'),'Public '+project+' keeps proxy distinct from personal acceptance');
    check(await page.locator('#timeline').count()===1,'Public '+project+' shows its actual recorded event timeline');
    await page.locator('[data-cap]').first().click();
    check(await page.locator('.story-node').count()>0,'Public '+project+' preserves source parent links through the story drill');
    await page.getByRole('button',{name:'← Overview',exact:true}).click();
    await page.locator('#timeline').focus();const value=Number(await page.locator('#timeline').inputValue());await page.keyboard.press('ArrowLeft');
    check(Number(await page.locator('#timeline').inputValue())===Math.max(0,value-1),'Public '+project+' timeline supports keyboard navigation');
    await capture('08-history-'+project);
    await page.getByRole('button',{name:'Recorded decisions ↗',exact:true}).click();
    if(project==='csv2md')check((await page.locator('dialog').innerText()).includes('Preserve significant ASCII spaces with &nbsp;')&&(await page.locator('dialog').innerText()).includes('Normalize spaces'),'Real csv2md D2 preserves both original option names');
    await page.keyboard.press('Escape');
  }
  const audit=()=>{
    const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
    const rgba=c=>(c.match(/[\d.]+/g)||[]).map(Number);
    const lum=c=>rgba(c).slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    const ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
    const bg=e=>{while(e){const c=getComputedStyle(e).backgroundColor;if(rgba(c)[3]!==0)return c;e=e.parentElement;}return getComputedStyle(document.body).backgroundColor;};
    const all=[...document.querySelectorAll('body *')].filter(e=>visible(e)&&!e.closest('button:disabled')&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()));
    const small=all.filter(e=>parseFloat(getComputedStyle(e).fontSize)<15).map(e=>({tag:e.tagName,text:e.textContent.slice(0,50),font:getComputedStyle(e).fontSize}));
    const contrast=all.map(e=>({text:e.textContent.trim().slice(0,60),ratio:ratio(getComputedStyle(e).color,bg(e))}));
    return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('[data-node]')].filter(visible).length,bodySize:parseFloat(getComputedStyle(document.body).fontSize),minFont:Math.min(...all.map(e=>parseFloat(getComputedStyle(e).fontSize))),small,minContrast:Math.min(...contrast.map(x=>x.ratio)),lowContrast:contrast.filter(x=>x.ratio<4.5)};
  };
  const views=[['macro','mode=synthetic'],['lens','mode=synthetic&wave=wave-5'],['drill','mode=synthetic&wave=wave-5&cap=C5'],['board','mode=synthetic&view=board'],['linkshelf','mode=history&project=linkshelf'],['csv2md','mode=history&project=csv2md']];
  for(const palette of ['forest','navy','light'])for(const width of [1440,375])for(const [name,query] of views){
    await go(query+'&palette='+palette,width);const m=await page.evaluate(audit);metrics.push({palette,width,view:name,...m});
    check(m.scrollWidth<=width,`${palette} ${width} ${name}: no horizontal overflow`,m.scrollWidth);
    check(m.nodes<=40,`${palette} ${width} ${name}: <=40 nodes`,m.nodes);
    check(m.bodySize>=18&&m.small.length===0,`${palette} ${width} ${name}: body >=18 and all secondary >=15`,m.small);
    check(m.lowContrast.length===0,`${palette} ${width} ${name}: all text >=4.5:1`,m.lowContrast);
    await page.evaluate(()=>window.scrollTo(0,600));
    check(await page.locator('.glance').evaluate(e=>e.getBoundingClientRect().top)===0,`${palette} ${width} ${name}: answers stay visible`);
    if(width===375&&['macro','drill'].includes(name)){await page.locator('#spine-card').evaluate(e=>{const y=e.getBoundingClientRect().top+scrollY-document.querySelector('.glance').offsetHeight-10;window.scrollTo(0,y);});await capture('09-phone-'+name+'-'+palette);}
  }
  await page.emulateMedia({reducedMotion:'reduce'});check(await page.evaluate(()=>matchMedia('(prefers-reduced-motion: reduce)').matches),'Reduced-motion preference is supported');await page.emulateMedia({reducedMotion:null});
  }
  const primitive={plan:null,runs:null,currentRunId:null,currentAgents:null,cockpit:null};
  let response=primitive;
  await page.route('**/api/status',async route=>{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(response)});});
  await go('mode=live');
  check((await page.locator('#needs strong').innerText())==='Not tracked yet'&&(await page.locator('#blocking strong').innerText())==='Not tracked yet','Null optional fields remain unknown, not zero');
  check(await page.locator('[data-cap]').count()===0&&await page.locator('.wave').count()===0,'Missing live API does not substitute any fixture or wave');await capture('10-live-null');
  response={plan:{phase:'execute',spine:[{id:'F1',kind:'feature',title:'A live capability',parents:[]},{id:'S1',kind:'story',title:'A recorded story',parents:['F1']},{id:'B1',kind:'bead',title:'A live item',parents:['S1']}]},runs:[{runId:'r',beads:null}],currentRunId:'r',cockpit:{answers:{where:null,blocking:[null],needsYou:[null]},capabilities:[null,{}],waves:{status:'draft',items:[{}]}}};
  await go('mode=live');check((await page.locator('#needs strong').innerText())==='Not tracked yet'&&(await page.locator('#blocking strong').innerText())==='Not tracked yet','Malformed child arrays do not turn into zero decisions or blockers');
  const hostile='<img src=x onerror="window.__cockpitXss=1"><script>window.__cockpitXss=2</script>';
  response={plan:{phase:'shape',plan_revision:'p1',name:hostile,spine:[{id:'F1',kind:'feature',title:hostile,detail:hostile,parents:[]}]},cockpit:{source:{planRevision:'p1'},answers:{needsYou:[{id:'D1',question:hostile}]}}};
  await go('mode=live');await page.locator('[data-cap="F1"]').click();await page.locator('#needs').click();
  check((await page.locator('dialog').innerText()).includes(hostile),'Hostile decision strings render as literal source text');
  check(await page.locator('dialog img, dialog script, .map-panel img, .map-panel script').count()===0&&await page.evaluate(()=>window.__cockpitXss===undefined),'Hostile strings never become HTML or executable script');await page.keyboard.press('Escape');
  response={plan:{phase:'execute',plan_revision:'p1',published_revision:'p1',publication:{up_to_date:true},spine:[{id:'F1',kind:'feature',title:'Bound checks',parents:[]}]},cockpit:{source:{planRevision:'p1',publicationRevision:'p1'},capabilities:[{id:'F1',title:'Bound checks',proof:{productScenarios:{passed:2,total:4,runId:'original-run',subject:'product',buildRevision:'build-1',evidenceRefs:['original-result']},requirementValidation:{passed:1,total:3,planRevision:'p1',evidenceRefs:['original-claim']}}}],answers:{needsYou:[],blocking:[]},waves:{status:'draft',items:[{id:'w1',title:'Draft foundation',isDraft:true,phase:'done'}]}},currentRunId:'original-run',runs:[{runId:'original-run',planRevision:'p1',beads:[]}]};
  await go('mode=live');
  check((await page.locator('#where strong').innerText()).startsWith('Current ·')&&!(await page.locator('#where strong').innerText()).includes('wave'),'Stage fallback is not labelled a real wave');
  check(await page.locator('.wave[aria-pressed="true"]').count()===0&&!((await page.locator('.wave').innerText()).includes('✓')),'Draft declaration order marks neither current nor done');
  await page.locator('[data-cap="F1"]').click();
  check((await page.locator('#context').innerText()).includes('Product scenario checks: 2 / 4')&&(await page.locator('#context').innerText()).includes('Requirement validation: 1 / 3 work items'),'Optional proof units have separate labelled denominators');
  check((await page.locator('#truth-rail').innerText()).includes('Whole-vision proof: not tracked yet'),'Bound scenario and work validation units do not become whole-vision proof');await capture('11-contract-units');
  response.cockpit.source.planRevision='stale';await go('mode=live&cap=F1');
  check((await page.locator('#context').innerText()).includes('Product scenario checks: not tracked yet')&&(await page.locator('#context').innerText()).includes('Requirement validation: not tracked yet'),'Stale source revision suppresses both proof numerators');
  response.cockpit.source.planRevision='p1';response.cockpit.capabilities[0].proof.productScenarios.subject='oracle';response.plan.acceptance={accepted:true,recordedUserAccepted:true,isProxy:false,humanOriginVerified:false};
  await go('mode=live&cap=F1');check((await page.locator('#context').innerText()).includes('Product scenario checks: not tracked yet'),'Oracle results do not populate product scenario checks');
  check((await page.locator('#truth-rail').innerText()).includes('origin unverified')&&!(await page.locator('#truth-rail').innerText()).includes('Verified personal acceptance'),'Recorded user approval does not become verified personal acceptance');
  response.plan.acceptance={accepted:false,readyForReview:false};await go('mode=live');await page.locator('#needs').click();check((await page.locator('dialog').innerText()).includes('Acceptance not ready'),'No decisions and not-ready are distinct');await page.keyboard.press('Escape');
  response.plan.acceptance={accepted:false,readyForReview:true};await go('mode=live');await page.locator('#needs').click();check((await page.locator('dialog').innerText()).includes('Ready for your review'),'Ready-for-review is sourced explicitly and not inferred from no decisions');await page.keyboard.press('Escape');
  await page.unroute('**/api/status');await page.route('**/api/status',route=>route.fulfill({status:200,contentType:'application/json',body:'{malformed'}));
  await go('mode=live');check((await page.locator('#source-banner').innerText()).includes('Malformed JSON'),'Malformed live JSON produces an honest unavailable state');
  await page.unroute('**/api/status');let unavailable=false;
  await page.route('**/api/status',route=>route.fulfill({status:unavailable?503:200,contentType:'application/json',body:JSON.stringify(primitive)}));
  await go('mode=live');unavailable=true;await page.waitForFunction(()=>document.querySelector('#source-banner').textContent.includes('HTTP 503'),{timeout:7000});
  check((await page.locator('#source-banner').innerText()).includes('last received snapshot, not current data'),'A failed actual refresh retains the snapshot with an explicit stale label');
  await page.unroute('**/api/status');
  if(canonicalBaseURL){
    await page.goto(canonicalBaseURL+'/?mode=live');await page.waitForFunction(()=>document.querySelector('#freshness').textContent.startsWith('Received'));
    const actual=await (await page.request.get(canonicalBaseURL+'/api/status')).json();
    check(actual.plan.phase==='align'&&actual.plan.clarifications.length===1,'Real PlanStore canonical snapshot reaches the browser over HTTP');
    await page.locator('#needs').click();check((await page.locator('dialog').innerText()).includes(actual.plan.clarifications[0].question),'Actual canonical question reaches the product decision inspector without interception');
    await capture('12-real-canonical-producer');await page.keyboard.press('Escape');
  }
  if(!contractsOnly){
  for(const [name,query,width] of [['13-full-macro','mode=synthetic',1440],['14-full-drill','mode=synthetic&cap=C5',1440],['15-full-phone-macro','mode=synthetic',375],['16-full-phone-drill','mode=synthetic&cap=C5',375]]){await go(query,width);await capture(name,true);}
  for(const width of [1440,375])for(const [name,query] of [['history-linkshelf','mode=history&project=linkshelf'],['history-csv2md','mode=history&project=csv2md'],['synthetic','mode=synthetic']]){await go(query,width);await capture('steer-1-'+name+'-'+width,true);}
  }
  check(errors.length===0,'No browser JavaScript runtime errors',errors);
  check(requests.every(r=>r.method==='GET'),'Every browser request is GET-only');
  check(requests.every(r=>[baseURL,canonicalBaseURL].includes(new URL(r.url).origin)||r.url.startsWith('data:')),'No external runtime requests');
  await go('mode=synthetic');
  return {passed:true,checks,metrics,screenshots:shots,runtimeErrors:errors,requests:requests.length,scope:'Product UI implementation; historical/synthetic and intercepted failure cases are not provider or full-vision evidence.'};
}
