// k6 load test against the gateway with the mock upstream.
// Run: make loadtest   (RATE and DURATION can be overridden)
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const KEY = __ENV.API_KEY || 'demo-key-northwind-travel';
const RATE = Number(__ENV.RATE || 200);
const DURATION = __ENV.DURATION || '60s';
const UNIQUE = (__ENV.UNIQUE || 'true') === 'true'; // unique prompts avoid the cache

const upstreamMs = new Trend('gateway_reported_ms');

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: RATE, timeUnit: '1s', duration: DURATION,
      preAllocatedVUs: 100, maxVUs: 1000,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
  },
};

export default function () {
  const content = UNIQUE ? `Question ${__VU}-${__ITER}-${Math.random()}: what are your opening hours?`
                         : 'What are your opening hours?';
  const res = http.post(`${BASE}/v1/chat/completions`,
    JSON.stringify({ model: 'support-bot', messages: [{ role: 'user', content }] }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${KEY}` } });
  check(res, { 'status 200': (r) => r.status === 200 });
}
