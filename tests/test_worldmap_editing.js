'use strict';
// Exercise the real editor handlers without a browser or source-file writes.
// Only the boot call is replaced, in memory, to expose lexical test hooks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const plain = value => JSON.parse(JSON.stringify(value));
function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
const filename = path.join(__dirname, '../web/worldmap.js');
const originalScript = fs.readFileSync(filename, 'utf8');
const boot = /\n\s*init\(\);\s*\n\}\)\(\);\s*$/;
assert.ok(boot.test(originalScript), 'Editor boot hook changed; update the test harness explicitly');
const testScript = originalScript.replace(boot, `
  globalThis.editorTest = {S,canvas,snapshot,fingerprint,hit,local,screen,world,
    worldLine,paintWorld,finishStroke,undo,eventAt,addEvent,saveWorld,pickAt,
    drawMap,drawWorld,fitSelection,boundsOf,map,navigateTo,setCategory,updateMapConnections,
    selectMap,fitWorld,pruneRasters};
})();`);

function mockContext() {
  const calls = [];
  return new Proxy({calls, measureText: text => ({width: String(text).length * 6})}, {
    get(target, key) {
      if (key in target) return target[key];
      return (...args) => calls.push({op: key, args});
    }
  });
}

function environment() {
  const elements = new Map();
  function element(tag = 'div') {
    const classes = new Set(), context = mockContext();
    const node = {
      tagName: tag.toUpperCase(), children: [], dataset: {}, style: {}, value: '',
      width: 800, height: 600, checked: false, hidden: false, open: false,
      firstChild: {textContent: ''}, isConnected: true,
      classList: {add: (...args) => args.forEach(a => classes.add(a)),
        remove: (...args) => args.forEach(a => classes.delete(a)),
        toggle: (name, flag) => flag ? classes.add(name) : classes.delete(name)},
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = children; },
      setAttribute(name, value) { this[name] = value; },
      addEventListener() {}, setPointerCapture() {}, reportValidity: () => true,
      getBoundingClientRect: () => ({left: 11, top: 17, width: 800, height: 600}),
      getContext: () => context,
      showModal() { this.open = true; }, close() { this.open = false; },
      querySelectorAll(selector) {
        const descendants = [];
        const visit = n => { for (const child of n.children || []) {
          if (child.tagName === selector.toUpperCase()) descendants.push(child);
          visit(child);
        }};
        visit(this);
        return descendants;
      }
    };
    return node;
  }
  const document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element(selector.includes('canvas') ? 'canvas' : 'div'));
      return elements.get(selector);
    },
    querySelectorAll: () => [],
    createElement: element, createDocumentFragment: () => element('fragment'),
    addEventListener() {}, title: ''
  };
  const requests = [], deferredLoads = new Map();
  const sandbox = {
    console, document, URLSearchParams, Map, Set, Uint8Array,
    location: {search: '', origin: 'http://test.invalid'}, history: {replaceState() {}},
    requestAnimationFrame: () => 1, setTimeout: () => 1, clearTimeout() {},
    ResizeObserver: class { observe() {} }, Image: class {},
    addEventListener() {}, devicePixelRatio: 1,
    fetch: async (url, options) => {
      const deferred = deferredLoads.get(url);
      if (deferred) {
        deferred.started();
        return deferred.response;
      }
      assert.equal(url, '/api/worldmap/save', 'Tests must not load real project data');
      const body = JSON.parse(options.body);
      requests.push({url, body, headers: options.headers});
      return {ok: true, json: async () => ({saved: body.maps.map(edit => ({
        ...plain(sandbox.editorTest.S.buffers.get(edit.name).data), revision: 'after-save'
      }))})};
    }
  };
  sandbox.window = sandbox;
  sandbox.parent = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(testScript, sandbox, {filename});
  const editor = sandbox.editorTest, {S} = editor;
  const rows = [
    {name: 'Route103', id: 'MAP_ROUTE103', x: 120, y: 218, width: 80, height: 22,
      connections: [{map: 'MAP_OLDALE_TOWN', name: 'OldaleTown', direction: 'down', offset: 0},
        {map: 'MAP_ROUTE110', name: 'Route110', direction: 'right', offset: -60}]},
    {name: 'Route110', id: 'MAP_ROUTE110', x: 200, y: 158, width: 40, height: 100,
      connections: [{map: 'MAP_MAUVILLE_CITY', name: 'MauvilleCity', direction: 'up', offset: 0},
        {map: 'MAP_SLATEPORT_CITY', name: 'SlateportCity', direction: 'down', offset: 0},
        {map: 'MAP_ROUTE103', name: 'Route103', direction: 'left', offset: 60}]},
    {name: 'OldaleTown', id: 'MAP_OLDALE_TOWN', x: 0, y: 240, width: 20, height: 20,
      connections: [{map: 'MAP_ROUTE103', name: 'Route103', direction: 'up', offset: 0}]},
    {name: 'MauvilleCity', id: 'MAP_MAUVILLE_CITY', x: 200, y: 138, width: 40, height: 20,
      connections: [{map: 'MAP_ROUTE110', name: 'Route110', direction: 'down', offset: 0}]},
    {name: 'SlateportCity', id: 'MAP_SLATEPORT_CITY', x: 200, y: 258, width: 40, height: 60,
      connections: [{map: 'MAP_ROUTE110', name: 'Route110', direction: 'up', offset: 0}]}
  ];
  rows.forEach(row => Object.assign(row, {area_kind: 'route', role_label: 'Routes', component: 'Hoenn', overlaps: []}));
  const catalogRows = rows.concat(Array.from({length: 513}, (_, i) => ({
    name: 'Interior_' + i, id: 'MAP_INTERIOR_' + i, width: 2, height: 2,
    x: 500 + i % 25 * 4, y: 200 + Math.floor(i / 25) * 4,
    area_kind: 'special', role_label: 'Rooms', connections: [], overlaps: [], component: 'Interior_' + i
  })));
  const catalog = freeze({maps: plain(catalogRows), bounds: {x: 120, y: 140, width: 480, height: 180},
    conflicts: [], transitions: [], sections: [], groups: [], warnings: []});
  Object.assign(S, {ready: true, selected: 'Route103', maps: catalog.maps,
    byName: new Map(catalog.maps.map(row => [row.name, row])),
    catalog,
    camera: {x: 160, y: 230}, width: 800, height: 600, scale: 16, tile: 7,
    tool: 'brush', mode: 'terrain', token: 'isolated-test-token'});
  for (const row of rows) {
    const data = {name: row.name, revision: 'original-revision', width: row.width, height: row.height,
      cells: Array(row.width * row.height).fill((3 << 12) | (1 << 10) | 1), border: [1, 1, 1, 1],
      layout: {id: 'LAYOUT_' + row.name, width: row.width, height: row.height,
        primary_tileset: 'gTileset_General', secondary_tileset: 'gTileset_Petalburg'},
      map: {id: row.id, layout: 'LAYOUT_' + row.name,
        object_events: row.name === 'Route103' ? [{x: 34, y: 6, elevation: 3,
          graphics_id: 'OBJ_EVENT_GFX_BOY_1', local_id: 'LOCALID_KEEP', script: 'KeepScript'}] : [],
        warp_events: [], coord_events: [], bg_events: [],
        connections: row.connections.map(({map, direction, offset}) => ({map, direction, offset}))},
      tileset: {columns: 16, metatiles: [{id: 1}, {id: 7}]}};
    const buffer = {data, atlas: element('canvas'), raster: null, valid: new Set([1, 7]), dirty: false};
    buffer.saved = editor.fingerprint(buffer);
    S.buffers.set(row.name, buffer);
  }
  document.querySelector('#preserve-movement').checked = true;
  document.querySelector('#tile-bank').value = 'all';
  document.querySelector('#event-type').value = 'object_events';
  document.querySelector('#collision').value = '2';
  document.querySelector('#elevation').value = '5';
  function deferBuffer(name) {
    const old = S.buffers.get(name), data = plain(old.data);
    S.buffers.delete(name);
    const key = `${data.layout.primary_tileset}|${data.layout.secondary_tileset}`;
    S.atlases.set(key, Promise.resolve(old.atlas));
    let started, resolve;
    const waiting = new Promise(done => { started = done; });
    const response = new Promise(done => { resolve = done; });
    deferredLoads.set(`/api/world/map?name=${encodeURIComponent(name)}`, {started, response});
    return {started: waiting, resolve: () => resolve({ok: true, json: async () => plain(data)})};
  }
  return {editor, S, rows, document, requests, catalog, deferBuffer,
    original: new Map(rows.map(row => [row.name, plain(editor.snapshot(S.buffers.get(row.name)))]))};
}

function eventAtWorld(env, point) {
  const p = env.editor.screen(point.x, point.y);
  return {clientX: p.x + 11, clientY: p.y + 17, pointerId: 1, button: 0, preventDefault() {}};
}
function eventAtLocal(env, map, x, y) {
  const row = env.editor.map(map);
  return eventAtWorld(env, {x: row.x + x, y: row.y + y});
}
function changedCells(env, name) {
  const current = env.S.buffers.get(name).data.cells, before = env.original.get(name).cells;
  const width = env.S.buffers.get(name).data.width;
  return Array.from(current).flatMap((value, i) => value !== before[i] ? [`${i % width},${Math.floor(i / width)}`] : []);
}
function unchangedMetadata(env) {
  for (const row of env.rows) {
    const before = env.original.get(row.name), after = env.editor.snapshot(env.S.buffers.get(row.name));
    for (const key of ['width', 'height', 'map', 'layout', 'border']) assert.deepEqual(plain(after[key]), before[key], key);
  }
}
function pristine(env) {
  for (const row of env.rows) assert.deepEqual(plain(env.editor.snapshot(env.S.buffers.get(row.name))), env.original.get(row.name));
}

let tests = 0;
async function test(label, run) {
  await run(environment());
  tests++;
  console.log('ok - ' + label);
}

(async () => {
  await test('all 518 maps remain on one immutable canvas when changing selection and category', async env => {
    const before = plain(env.catalog);
    await env.editor.navigateTo('OldaleTown');
    assert.equal(env.S.maps.length, 518);
    assert.equal(env.S.selected, 'OldaleTown');
    assert.equal(env.editor.hit({x: 201, y: 224.5}).name, 'Route110');
    assert.equal(env.editor.hit({x: 501, y: 201}).name, 'Interior_0');
    await env.editor.setCategory('route');
    assert.equal(env.editor.hit({x: 501, y: 201}).name, 'Interior_0', 'Category navigation never hides other maps');
    await env.editor.fitWorld();
    assert.equal(env.S.maps.length, 518);
    assert.deepEqual(plain(env.catalog), before);
    assert.equal(env.editor.map('Route110'), env.S.byName.get('Route110'));
    assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../web/worldmap.html'), 'utf8'), /id="connected-view"|worldmap-views/);
    pristine(env);
  });

  await test('pending edits survive map-link navigation and Undo restores source tiles', async env => {
    const originalBuffer = env.S.buffers.get('Route103');
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 37.5, 6.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
    await env.editor.navigateTo('OldaleTown');
    await env.editor.fitWorld();
    assert.equal(env.S.buffers.get('Route103'), originalBuffer);
    assert.equal(originalBuffer.dirty, true);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
    env.editor.undo(true);
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
  });

  await test('a brush stroke crosses the joined Route103 and Route110 using original source cells', async env => {
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['78,6', '79,6']);
    assert.deepEqual(changedCells(env, 'Route110'), ['0,66', '1,66']);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
  });

  await test('section gutters have no editable terrain', async env => {
    const gap = {x: 75, y: 250};
    assert.equal(env.editor.hit(gap), null);
    await env.editor.canvas.onpointerdown(eventAtWorld(env, gap));
    await env.editor.finishStroke();
    pristine(env);
  });

  await test('rectangles finish on the source grid before following a map link', async env => {
    env.S.tool = 'rectangle';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.2, 20.2));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 84.8, 26.8));
    await env.editor.navigateTo('OldaleTown');
    assert.equal(env.S.drag, null);
    assert.equal(env.S.selected, 'OldaleTown');
    assert.deepEqual(changedCells(env, 'Route103'), ['78,20', '79,20', '78,21', '79,21']);
    assert.deepEqual(changedCells(env, 'Route110'), []);
    env.editor.undo();
    pristine(env);
  });

  await test('event drag retains source coordinates and is undoable after navigation', async env => {
    env.S.mode = 'events'; env.S.tool = 'select';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.7, 6.8));
    assert.equal(env.S.drag.event, true);
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 41.7, 12.8));
    await env.editor.navigateTo('OldaleTown');
    const event = env.S.buffers.get('Route103').data.map.object_events[0];
    assert.deepEqual(plain(event), {...env.original.get('Route103').map.object_events[0], x: 41, y: 12});
    assert.deepEqual(changedCells(env, 'Route103'), []);
    env.editor.undo(); pristine(env);
    env.editor.undo(true);
    assert.equal(env.S.buffers.get('Route103').data.map.object_events[0].y, 12);
  });

  await test('event placement on another map stores local coordinates', async env => {
    env.S.mode = 'events'; env.S.place = true;
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route110', 8.5, 66.5));
    const event = env.S.buffers.get('Route110').data.map.object_events[0];
    assert.deepEqual({x: event.x, y: event.y}, {x: 8, y: 66});
    env.editor.undo(); pristine(env);
  });

  await test('map connections use same-canvas navigation and preserve complete catalog and buffers', async env => {
    const before = plain(env.catalog), buffers = new Map(env.S.buffers);
    env.editor.updateMapConnections();
    const host = env.document.querySelector('#map-connections');
    const button = host.children.find(child => child['aria-label'] === 'Go down to Oldale Town on this canvas');
    assert.ok(button, 'An original source connection has a destination button');
    await button.onclick();
    assert.equal(env.S.selected, 'OldaleTown');
    assert.equal(env.S.maps.length, 518);
    assert.deepEqual(plain(env.catalog), before);
    for (const [name, buffer] of buffers) assert.equal(env.S.buffers.get(name), buffer);
    pristine(env);
  });

  await test('navigation is refused while pending edits are still finishing', async env => {
    env.S.finishing = true;
    assert.equal(await env.editor.navigateTo('MauvilleCity'), false);
    assert.equal(env.S.selected, 'Route103');
    env.S.finishing = false;
    pristine(env);
  });

  await test('drawing uses one full rectangle without transformed or cropped terrain', async env => {
    const row = env.editor.map('Route103'), image = {width: 256, height: 70.4};
    env.S.previews.set(row.name, image);
    env.editor.drawMap(row);
    const calls = env.editor.canvas.getContext('2d').calls;
    const draws = calls.filter(call => call.op === 'drawImage');
    assert.equal(draws.length, 1);
    assert.equal(draws[0].args[0], image);
    assert.equal(draws[0].args.length, 5);
    assert.deepEqual(draws[0].args.slice(-2), [80 * 16, 22 * 16]);
    assert.equal(calls.filter(call => call.op === 'transform').length, 0);
    pristine(env);
  });

  await test('saving includes edited maps outside the camera and excludes section layout coordinates', async env => {
    const before = plain(env.catalog);
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    await env.editor.navigateTo('OldaleTown');
    await env.editor.saveWorld();
    assert.equal(env.requests.length, 1);
    const request = env.requests[0];
    assert.equal(request.headers['X-Workbench-Token'], 'isolated-test-token');
    assert.deepEqual(request.body.maps.map(edit => edit.name).sort(), ['Route103', 'Route110']);
    for (const edit of request.body.maps) {
      assert.deepEqual(Object.keys(edit).sort(), ['border', 'cells', 'confirm_shared', 'height', 'layout', 'map', 'name', 'revision', 'width']);
      const source = env.original.get(edit.name);
      for (const key of ['width', 'height', 'layout', 'map', 'border']) assert.deepEqual(edit[key], source[key]);
      assert.equal(edit.revision, 'original-revision');
    }
    assert.equal(env.S.buffers.get('Route103').dirty, false);
    assert.deepEqual(plain(env.catalog), before);
    assert.equal(env.S.buffers.get('Route103').data.height, 22);
  });

  await test('slow destination artwork cannot override a newer map selection', async env => {
    const slow = env.deferBuffer('Route103');
    const navigating = env.editor.navigateTo('Route103');
    await slow.started;
    await env.editor.navigateTo('MauvilleCity');
    const newer = {camera: plain(env.S.camera), scale: env.S.scale,
      heading: env.document.querySelector('#selected-name').textContent};
    slow.resolve(); await navigating;
    assert.equal(env.S.selected, 'MauvilleCity');
    assert.deepEqual(plain(env.S.camera), newer.camera);
    assert.equal(env.S.scale, newer.scale);
    assert.equal(env.document.querySelector('#selected-name').textContent, newer.heading);
    pristine(env);
  });

  await test('slow destination artwork cannot undo See everything', async env => {
    const slow = env.deferBuffer('Route103');
    const navigating = env.editor.navigateTo('Route103');
    await slow.started;
    await env.editor.fitWorld();
    const newer = {camera: plain(env.S.camera), scale: env.S.scale};
    slow.resolve(); await navigating;
    assert.deepEqual(plain(env.S.camera), newer.camera);
    assert.equal(env.S.scale, newer.scale);
    assert.equal(env.S.maps.length, 518);
    pristine(env);
  });

  assert.equal(fs.readFileSync(filename, 'utf8'), originalScript, 'Tests must not edit application code');
  console.log(`World map editing passed: ${tests} actual-handler regression tests; all data stayed in isolated VM memory.`);
})().catch(error => {console.error(error); process.exitCode = 1;});
