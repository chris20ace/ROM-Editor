'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/build.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.events = {}; this.attributes = {};
    this.value = ''; this.disabled = false; this.hidden = true; this.textContent = ''; this.open = false;
    this.scrollHeight = this.scrollTop = this.clientHeight = 0;
    this.classList = {toggle() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, listener) { this.events[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; delete this[name]; }
  scrollIntoView() {}
}

async function harness(options = {}) {
  const nodes = new Map(), calls = [];
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
  let job = options.job || null;
  const history = () => job ? [job] : [];
  const active = () => ['snapshotting','queued','building'].includes(job?.status) ? job : null;
  let catalog = {toolchain:{ready:options.ready !== false,message:options.ready === false ? 'Installing local builder.' : 'Local builder ready.'},active_job:options.activeJob || active(),history:history()};
  const context = vm.createContext({document:{querySelector:$,createElement:tag => new Element(tag)},
    location:{origin:'http://127.0.0.1:8765'},URL,Set,Date,String,Number,encodeURIComponent,
    window:{},navigator:{clipboard:{writeText:async () => {}}},setTimeout:() => 1,clearTimeout() {},
    fetch: async (url, init = {}) => {
      calls.push({url, init});
      let data;
      if (url === '/api/session') data = {token:'test-token'};
      else if (url === '/api/build') data = catalog;
      else if (url.startsWith('/api/build/jobs?')) data = options.activeJob && new URL(url, 'http://127.0.0.1:8765').searchParams.get('id') === options.activeJob.id ? options.activeJob : job;
      else if (url === '/api/build/start') {
        job = {id:'new-build',name:JSON.parse(init.body).name,status:'queued',stage:'queued',log:'Waiting for compiler'};
        catalog = {...catalog,active_job:job,history:[job]}; data = job;
      } else if (url === '/api/build/retry') {
        if (options.retryError) return {ok:false,json:async () => ({error:options.retryError})};
        assert.equal(JSON.parse(init.body).id, job.id);
        job = {...job,status:'queued',stage:'Retrying saved snapshot',attempt_count:(job.attempt_count || 1)+1,log:job.log + '\nRetry attempt 2 — original saved snapshot',download_url:null};
        catalog = {...catalog,active_job:job,history:[job]}; data = job;
      } else throw new Error('Unexpected request ' + url);
      return {ok:true,json:async () => structuredClone(data)};
    }});
  vm.runInContext(source, context, {filename:'build.js'});
  await tick(); await tick();
  return {$,calls,submit:async () => { await $('#build-form').events.submit({preventDefault(){}}); await tick(); },
    retry:async () => { await $('#retry-build').events.click(); await tick(); },
    selectHistory:async () => { $('#build-history').children[0].children[3].children[0].events.click(); await tick(); await tick(); }};
}

test('builder setup state cannot start or offer a fake download', async () => {
  const h = await harness({ready:false});
  assert.equal(h.$('#start-build').disabled, true);
  assert.equal(h.$('#toolchain-message').textContent, 'Installing local builder.');
  await h.submit();
  assert.equal(h.calls.some(call => call.url === '/api/build/start'), false);
  assert.equal(h.$('#download-rom').hidden, true);
});

test('active real build shows log and stage without a download', async () => {
  const h = await harness({job:{id:'a',name:'Adventure',status:'building',stage:'Compiling maps',message:'Building your ROM',log:'mapjson Route101\n'}});
  assert.equal(h.$('#start-build').disabled, true);
  assert.equal(h.$('#build-progress').hidden, false);
  assert.equal(h.$('#build-progress').attributes['aria-valuetext'], 'Compiling maps');
  assert.equal(h.$('#build-log').textContent, 'mapjson Route101\n');
  assert.equal(h.$('#download-rom').hidden, true);
});

test('failed compiler exposes its error and log instead of a ROM', async () => {
  const h = await harness({job:{id:'bad',name:'Adventure',status:'failed',error:'Unknown script label Route101_Missing',log:'Error: Route101_Missing',download_url:'/api/build/download?id=bad'}});
  assert.equal(h.$('#build-error').hidden, false);
  assert.match(h.$('#build-error').textContent, /Unknown script label Route101_Missing/);
  assert.equal(h.$('#log-details').open, true);
  assert.equal(h.$('#download-rom').hidden, true);
  assert.equal(h.$('#build-progress').hidden, true);
  assert.equal(h.$('#start-build').disabled, false);
});

test('only a successful job offers its actual local ROM with playtest note', async () => {
  const h = await harness({job:{id:'good',name:'Adventure',status:'succeeded',log:'Done',size:16777216,download_url:'/api/build/download?id=good',source_sha256:'123456789012abcdef'}});
  assert.equal(h.$('#download-rom').hidden, false);
  assert.equal(h.$('#download-rom').href, 'http://127.0.0.1:8765/api/build/download?id=good');
  assert.equal(h.$('#playtest-note').hidden, false);
  assert.equal(h.$('#build-history').children.length, 1);
});

test('unexpected external download destinations are not linked', async () => {
  const h = await harness({job:{id:'good',name:'Adventure',status:'succeeded',download_url:'https://example.com/not-your-build.gba'}});
  assert.equal(h.$('#download-rom').hidden, true);
});

test('starting captures the entered name, uses CSRF, and observes actual queued job', async () => {
  const h = await harness();
  assert.equal(h.$('#start-build').disabled, false);
  h.$('#rom-name').value = ' My new adventure ';
  await h.submit();
  const request = h.calls.find(call => call.url === '/api/build/start');
  assert.equal(request.init.method, 'POST');
  assert.equal(request.init.headers['X-Workbench-Token'], 'test-token');
  assert.deepEqual(JSON.parse(request.init.body), {name:'My new adventure'});
  assert.equal(h.$('#build-heading').textContent, 'My new adventure');
  assert.equal(h.$('#start-build').disabled, true);
  assert.equal(h.$('#download-rom').hidden, true);
});

test('blank optional ROM name uses the server default', async () => {
  const h = await harness();
  await h.submit();
  const request = h.calls.find(call => call.url === '/api/build/start');
  assert.deepEqual(JSON.parse(request.init.body), {});
});

test('retry sends only the failed saved build ID and follows actual queued state', async () => {
  const h = await harness({job:{id:'saved-copy',name:'Old adventure',status:'failed',log:'Linker failed',source_sha256:'abcdef123456original'}});
  assert.equal(h.$('#retry-section').hidden, false);
  assert.equal(h.$('#retry-build').disabled, false);
  h.$('#rom-name').value = 'New unsaved name';
  await h.retry();
  const request = h.calls.find(call => call.url === '/api/build/retry');
  assert.deepEqual(JSON.parse(request.init.body), {id:'saved-copy'});
  assert.equal(request.init.headers['X-Workbench-Token'], 'test-token');
  assert.equal(h.$('#build-heading').textContent, 'Old adventure');
  assert.equal(h.$('#build-progress').hidden, false);
  assert.equal(h.$('#retry-build').disabled, true);
  assert.equal(h.$('#download-rom').hidden, true);
  assert.match(h.$('#build-log').textContent, /original saved snapshot/);
  assert.equal(h.calls.some(call => call.url === '/api/build/start'), false);
});

test('cancelled saved build can retry but a successful build cannot', async () => {
  const cancelled = await harness({job:{id:'stopped',name:'Stopped',status:'cancelled',log:''}});
  assert.equal(cancelled.$('#retry-build').disabled, false);
  await cancelled.retry();
  assert.equal(cancelled.calls.filter(call => call.url === '/api/build/retry').length, 1);
  const success = await harness({job:{id:'done',name:'Done',status:'succeeded',download_url:'/api/build/download?id=done'}});
  assert.equal(success.$('#retry-section').hidden, true);
  await success.retry();
  assert.equal(success.calls.some(call => call.url === '/api/build/retry'), false);
});

test('another global active job disables retry of a selected failed copy', async () => {
  const h = await harness({job:{id:'old',name:'Old',status:'failed'},activeJob:{id:'other',name:'Other build',status:'building'}});
  await h.selectHistory();
  assert.equal(h.$('#build-heading').textContent, 'Old');
  assert.equal(h.$('#retry-section').hidden, false);
  assert.equal(h.$('#retry-build').disabled, true);
  await h.retry();
  assert.equal(h.calls.some(call => call.url === '/api/build/retry'), false);
});

test('retry validation error remains visible after refreshing status', async () => {
  const h = await harness({job:{id:'bad-copy',status:'failed',name:'Changed copy'},retryError:'Saved build inputs changed. Start a new build.'});
  await h.retry();
  assert.equal(h.$('#page-error').hidden, false);
  assert.match(h.$('#page-error').textContent, /Saved build inputs changed/);
  assert.equal(h.$('#download-rom').hidden, true);
});
