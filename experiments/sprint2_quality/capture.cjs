/* Real browser evidence. Requires Playwright outside the frontend checkout.
 * UI must be serving the exact frontend HEAD and tests.e2e_server must be active.
 * No interception, mock responses, injected layouts, or cut confirmation.
 */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.QUALITY_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const evidence = path.join(root, 'docs/evidence/sprint-02/andro-calidad-algoritmos');
const api = 'http://127.0.0.1:8017';
const save = (name, value) => fs.writeFileSync(path.join(evidence, 'ui', name), JSON.stringify(value, null, 2)+'\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

(async () => {
  fs.mkdirSync(path.join(evidence,'screenshots'),{recursive:true});
  fs.mkdirSync(path.join(evidence,'ui'),{recursive:true});
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1600,height:1100},reducedMotion:'reduce'});
  const failures=[];
  page.on('pageerror', error => failures.push(error.message));
  const credentials={username:process.env.QUALITY_UI_USERNAME,password:process.env.QUALITY_UI_PASSWORD};
  assert(credentials.username && credentials.password,'Set QUALITY_UI_USERNAME and QUALITY_UI_PASSWORD for the disposable test server');
  const response=await fetch(api+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(credentials)});
  assert(response.ok,'Login failed');
  const headers={Authorization:`Bearer ${(await response.json()).access_token}`,'Content-Type':'application/json'};
  async function request(url,options={}) {
    const res=await fetch(api+url,{...options,headers:{...headers,...options.headers}});
    const data=await res.json(); assert(res.ok,`${res.status} ${JSON.stringify(data)}`); return data;
  }
  await page.goto('http://127.0.0.1:5173');
  await page.getByLabel('Usuario',{exact:true}).fill(credentials.username);
  await page.getByLabel('Contraseña',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'Ingresar',exact:true}).click();
  await page.getByRole('button',{name:'Optimización',exact:true}).click();
  const captures=[], comparisons=[];
  async function shot(name,locator=page) {
    await locator.screenshot({path:path.join(evidence,'screenshots',name+'.png'),animations:'disabled',...(locator===page?{fullPage:true}:{})});
    captures.push(name+'.png');
  }
  for (const caseId of ['CP-01','CP-05','CP-06']) {
    const savedPath=path.join(evidence,'ui',caseId+'.json');
    const saved=fs.existsSync(savedPath)?JSON.parse(fs.readFileSync(savedPath,'utf8')):null;
    const seeded=saved?.seeded || JSON.parse(execFileSync(process.env.QUALITY_PYTHON || 'python',['-m','experiments.sprint2_quality.ui_seed',caseId],{cwd:root,encoding:'utf8'}));
    const before=await request('/api/inventory/planchas');
    const remnantsBefore=await request('/api/inventory/retazos');
    let current=saved ? await request(`/api/optimization/comparisons-v2/${saved.comparison.comparison_id}`) : await request('/api/optimization/comparisons-v2',{method:'POST',headers:{'Idempotency-Key':`quality-${caseId}-${Date.now()}`},body:JSON.stringify({order_ids:[seeded.order_id]})});
    const started=Date.now();
    while (!current.terminal) {
      assert(Date.now()-started<950000,'Comparison deadline');
      await new Promise(resolve=>setTimeout(resolve,1000));
      current=await request(`/api/optimization/comparisons-v2/${current.comparison_id}`);
    }
    assert(current.executions.length===6,'Six production executions required');
    const executions=[];
    for (const execution of current.executions) {
      const result=await request(`/api/optimization/comparisons-v2/${current.comparison_id}/executions/${execution.execution_id}`);
      const measured=JSON.parse(fs.readFileSync(path.join(evidence,'results',`${caseId}_${execution.method}_1.json`))).row;
      assert(execution.placed_units===measured.placed_units,`${caseId} coverage mismatch ${execution.method}`);
      for (const key of ['aprovechamiento_pct','merma_mm2','retazo_recuperable_mm2'])
        assert(Number(execution.metrics[key])===Number(measured[key]),`${caseId} metric mismatch ${execution.method} ${key}`);
      executions.push(result);
    }
    save(caseId+'.json',{seeded,comparison:current,executions,measured_quality_matches:true});
    console.log(`Loading saved UI comparison ${caseId}`);
    if (await page.getByLabel('Métodos').isEnabled())
      await page.getByLabel('Métodos').selectOption('comparison-v2');
    await page.getByText('Consulta avanzada',{exact:true}).click();
    await page.getByLabel('Consultar trabajo guardado',{exact:true}).fill(String(current.comparison_id));
    await page.getByRole('button',{name:'Consultar',exact:true}).click();
    await page.getByRole('tab',{name:'Comparación',exact:true}).click();
    await page.getByRole('region',{name:'Resumen para comparar motores',exact:true}).waitFor();
    await page.getByRole('button',{name:'Ver patrón FF',exact:true}).waitFor({state:'visible'});
    // Wait until persisted result detail has loaded, not just the summary.
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Ver patrón FF'&&!b.disabled));
    if (caseId==='CP-01') {
      await shot('01-comparacion-seis-motores',page.getByRole('tabpanel',{name:'Comparación',exact:true}));
      await shot('02-tabla-completa',page.getByRole('region',{name:'Resumen para comparar motores',exact:true}));
      await page.getByText('Datos técnicos exactos de los seis motores',{exact:true}).click();
      await shot('03-datos-tecnicos',page.locator('.comparison-technical-results').filter({has:page.getByText('Datos técnicos exactos de los seis motores',{exact:true})}));
      await page.getByText('Configuración y unidades aplicadas',{exact:true}).click();
      await shot('04-configuracion',page.locator('.comparison-configuration'));
      await page.getByRole('button',{name:'Ver patrón M6-BS',exact:true}).click();
      await page.getByRole('heading',{name:'Patrón de corte · M6-BS',exact:true}).waitFor();
      await shot('05-patron-diferenciador-M6',page.locator('.comparison-result-visual:visible'));
    } else {
      await page.getByRole('button',{name:'Ver patrón FF',exact:true}).click();
      await page.getByRole('heading',{name:'Patrón de corte · FF',exact:true}).waitFor();
      await shot(caseId==='CP-05'?'06-multi-plancha':'07-plancha-retazo',page.locator('.comparison-result-visual:visible'));
      if (caseId==='CP-06' || caseId==='CP-05') {
        const trace=page.getByRole('region',{name:'Materiales propuestos',exact:true}).first();
        for (const detail of await trace.locator('details').all()) await detail.locator('summary').click();
        await shot(caseId==='CP-06'?'08-trazabilidad-material':'09-fuentes-multi-plancha',trace);
      }
    }
    assert(JSON.stringify(await request('/api/inventory/planchas'))===JSON.stringify(before),'Sheet inventory mutated');
    assert(JSON.stringify(await request('/api/inventory/retazos'))===JSON.stringify(remnantsBefore),'Remnant inventory mutated');
    assert(!current.confirmed,'Must not confirm cuts');
    comparisons.push({case_id:caseId,comparison_id:current.comparison_id,input_sha256:current.input_sha256,quality_matches:true,inventory_unchanged:true});
    if (!current.released) await request(`/api/optimization/comparisons-v2/${current.comparison_id}/release`,{method:'POST',body:JSON.stringify({expected_version:current.version})});
    await page.reload();
    await page.getByRole('button',{name:'Optimización',exact:true}).click();
  }
  save('manifest.json',{frontend_sha:process.env.QUALITY_FRONTEND_SHA,browser:browser.version(),viewport:{width:1600,height:1100},comparisons,captures,page_errors:failures,confirmed:false});
  assert(failures.length===0,'Browser page errors');
  await browser.close();
  console.log(JSON.stringify({captures:captures.length,comparisons:comparisons.length,quality_matches:true}));
})().catch(error=>{console.error(error);process.exit(1);});
