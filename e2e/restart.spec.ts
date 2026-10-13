import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

test('saved SQLite snapshots survive real API process restart and another import', async ({ request }) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'bakeplan-restart-'));
  const base = 'http://127.0.0.1:18001/api';
  let child: ChildProcess | undefined;
  async function start() {
    child = spawn(path.resolve(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), ['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','18001'], {env:{...process.env,BAKEPLAN_DB:path.join(directory,'test.sqlite3')}, windowsHide:true,stdio:'ignore'});
    await expect.poll(async () => {try {return (await request.get(`${base}/plans`)).status()} catch {return 0}}).toBe(200);
  }
  async function stop() {
    if (!child || child.exitCode !== null) return;
    const exited = new Promise<void>(resolve => child!.once('exit',()=>resolve()));
    child.kill(); await exited;
  }
  try {
    await start();
    const csv_text=await fs.readFile('fixtures/synthetic_demo.csv','utf8');
    const dataset=await (await request.post(`${base}/datasets`,{data:{csv_text,source:'synthetic'}})).json();
    const plan=await (await request.post(`${base}/plans`,{data:{dataset_id:dataset.id,target_date:'2026-10-12',cutoff:'2026-10-11',method:'B1',mode:'replay'}})).json();
    const response=await request.post(`${base}/plans/${plan.id}/confirm`,{data:{revision:0,idempotency_key:'restart-test',quantities:Object.fromEntries(plan.items.map((i:{product_id:string;recommended_qty:number})=>[i.product_id,i.recommended_qty]))}});
    expect(response.status()).toBe(200);
    const saved=await response.json();
    const exported=await (await request.get(`${base}/plans/${plan.id}/export`)).text();
    const anotherImport = await request.post(`${base}/datasets`,{data:{csv_text,source:'external'}});
    expect(anotherImport.status()).toBe(201);
    expect((await anotherImport.json()).id).not.toBe(dataset.id);
    const scoped = await (await request.post(`${base}/datasets`, {data:{
      csv_text, source:'synthetic', bakery_id:'restart-ml', target:'sold_units',
      unit:'physical_unit', provenance:'Synthetic real-process restart test',
    }})).json();
    const trained = await request.post(`${base}/datasets/${scoped.id}/train`, {data:{
      cutoff:'2026-10-11', idempotency_key:'restart-model',
    }});
    expect(trained.status()).toBe(201);
    const model = await trained.json();
    expect(model.status).toBe('ready');
    const forecastBody = {dataset_id:scoped.id, target_date:'2026-10-12', cutoff:'2026-10-11', method:'ML'};
    const forecastBefore = await (await request.post(`${base}/forecasts`,{data:forecastBody})).json();
    expect(forecastBefore.items[0].method).toBe('ML');
    const mlPlan = await (await request.post(`${base}/plans`,{data:{...forecastBody, mode:'replay'}})).json();
    expect(mlPlan.model_id).toBe(model.id);
    const confirmBody = {revision:0, idempotency_key:'restart-ml-confirm',
      quantities:Object.fromEntries(mlPlan.items.map((i:{product_id:string;recommended_qty:number})=>[i.product_id,i.recommended_qty]))};
    const mlSavedResponse = await request.post(`${base}/plans/${mlPlan.id}/confirm`,{data:confirmBody});
    expect(mlSavedResponse.status()).toBe(200);
    const mlSaved = await mlSavedResponse.json();
    await stop(); await start();
    expect(await (await request.get(`${base}/plans/${plan.id}`)).json()).toEqual(saved);
    expect(await (await request.get(`${base}/plans/${plan.id}/export`)).text()).toBe(exported);
    expect((await (await request.get(`${base}/plans`)).json()).map((p:{id:string})=>p.id)).toContain(saved.id);
    expect(await (await request.get(`${base}/datasets/${scoped.id}/models`)).json()).toEqual([model]);
    expect(await (await request.post(`${base}/forecasts`,{data:forecastBody})).json()).toEqual(forecastBefore);
    expect(await (await request.get(`${base}/plans/${mlPlan.id}`)).json()).toEqual(mlSaved);
    expect(await (await request.post(`${base}/plans/${mlPlan.id}/confirm`,{data:confirmBody})).json()).toEqual(mlSaved);
    const repeatedTraining = await request.post(`${base}/datasets/${scoped.id}/train`, {data:{cutoff:'2026-10-11', idempotency_key:'restart-model'}});
    expect(repeatedTraining.status()).toBe(200);
    expect(await repeatedTraining.json()).toEqual(model);
  } finally { await stop(); await fs.rm(directory,{recursive:true,force:true}); }
});
