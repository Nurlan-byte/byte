import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const base = __ENV.LOAD_ORIGIN;
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('Expected the disposable loopback fixture');
const orders = JSON.parse(__ENV.LOAD_ORDERS);
const opened = new Counter('verified_orders');
const refreshed = new Counter('finished_workers');

export const options = {
  scenarios: {
    read_orders: { executor: 'constant-arrival-rate', rate: 5, timeUnit: '1s', duration: '12s',
      preAllocatedVUs: 8, maxVUs: 8, exec: 'openOrder' },
    refresh_outlets: { executor: 'per-vu-iterations', vus: 4, iterations: 1,
      maxDuration: '1m', exec: 'refresh' },
  },
  thresholds: {
    'http_req_duration{name:read-confirmed-order}': ['p(95)<=500'],
    http_req_failed: ['rate==0'], checks: ['rate==1'],
    verified_orders: ['count==60'], finished_workers: ['count==4'], dropped_iterations: ['count==0'],
  },
};

export function openOrder() {
  const i = exec.scenario.iterationInTest;
  if (i >= 60) return; // Some platforms schedule one arrival exactly at the end boundary.
  const expected = orders[i % orders.length];
  const r = http.get(base + '/api/plans/' + expected.id, { tags: { name: 'read-confirmed-order' } });
  const ok = check(r, { 'correct confirmed branch, revision and quantities': (response) => {
    if (response.status !== 200) return false;
    const plan = response.json();
    return plan.id === expected.id && plan.bakery_id === expected.branch &&
      plan.status === 'confirmed' && plan.revision === expected.revision &&
      plan.dataset_id === expected.dataset && plan.items.length === Object.keys(expected.quantities).length &&
      plan.items.every(item => item.confirmed_qty === expected.quantities[item.product_id]);
  } });
  opened.add(ok ? 1 : 0);
}

export function refresh() {
  const r = http.post(base + '/fixture/run-due', null, { tags: { name: 'run-scheduler' }, timeout: '55s' });
  const ok = check(r, { 'worker completed': response => response.status === 200 && response.json() === true });
  refreshed.add(ok ? 1 : 0);
}
