'use strict';
// Exercise the real editor handlers without a browser or source-file writes.
// Only the boot call is replaced, in memory, to expose lexical test hooks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Views = require('../web/worldmap-views.js');
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
    drawMap,drawWorld,fitSelection,boundsOf,map,viewMaps,applyView,changeView,
    selectMap,fitWorld,pruneRasters,focusJoin};
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
  sandbox.WorldMapViews = Views;
  vm.createContext(sandbox);
  vm.runInContext(testScript, sandbox, {filename});
  const editor = sandbox.editorTest, {S} = editor;
  const rows = [
    {name: 'Route103', id: 'MAP_ROUTE103', x: 120, y: 218, width: 80, height: 22,
      connections: [{map: 'MAP_OLDALE_TOWN', name: 'OldaleTown', direction: 'down', offset: 0},
        {map: 'MAP_ROUTE110', name: 'Route110', direction: 'right', offset: -60}]},
    {name: 'Route110', id: 'MAP_ROUTE110', x: 200, y: 160, width: 40, height: 100,
      connections: [{map: 'MAP_MAUVILLE_CITY', name: 'MauvilleCity', direction: 'up', offset: 0},
        {map: 'MAP_SLATEPORT_CITY', name: 'SlateportCity', direction: 'down', offset: 0},
        {map: 'MAP_ROUTE103', name: 'Route103', direction: 'left', offset: 60}]},
    {name: 'OldaleTown', id: 'MAP_OLDALE_TOWN', x: 120, y: 240, width: 20, height: 20,
      connections: [{map: 'MAP_ROUTE103', name: 'Route103', direction: 'up', offset: 0}]},
    {name: 'MauvilleCity', id: 'MAP_MAUVILLE_CITY', x: 200, y: 140, width: 40, height: 20,
      connections: [{map: 'MAP_ROUTE110', name: 'Route110', direction: 'down', offset: 0}]},
    {name: 'SlateportCity', id: 'MAP_SLATEPORT_CITY', x: 200, y: 260, width: 40, height: 60,
      connections: [{map: 'MAP_ROUTE110', name: 'Route110', direction: 'up', offset: 0}]}
  ];
  rows.forEach(row => Object.assign(row, {area_kind: 'route', role_label: 'Routes', component: 'Hoenn', overlaps: []}));
  const catalogRows = rows.concat(Array.from({length: 513}, (_, i) => ({
    name: 'Interior_' + i, id: 'MAP_INTERIOR_' + i, width: 2, height: 2,
    x: 500 + i % 25 * 4, y: 200 + Math.floor(i / 25) * 4,
    area_kind: 'special', role_label: 'Rooms', connections: [], overlaps: [], component: 'Interior_' + i
  })));
  const catalog = freeze({maps: plain(catalogRows), bounds: {x: 120, y: 140, width: 480, height: 180},
    conflicts: [], sections: [], groups: [], warnings: []});
  Object.assign(S, {ready: true, selected: 'Route103', maps: catalog.maps,
    byName: new Map(catalog.maps.map(row => [row.name, row])),
    catalog, view: 'world', anchor: null,
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
  await test('connected view uses exact offsets and keeps all 518 catalog entries immutable', async env => {
    const {editor, S, catalog} = env, before = plain(catalog);
    assert.equal(editor.applyView('connected', 'Route103', {fit: false}), true);
    assert.equal(S.connected.anchor, 'Route103');
    assert.deepEqual(Array.from(editor.viewMaps(), row => row.name), ['Route103', 'OldaleTown', 'Route110']);
    const route = editor.map('Route103'), east = editor.map('Route110'), town = editor.map('OldaleTown');
    assert.deepEqual({x: east.x - route.x, y: east.y - route.y}, {x: 80, y: -60});
    assert.deepEqual({x: town.x - route.x, y: town.y - route.y}, {x: 0, y: 22});
    assert.deepEqual({width: route.width, height: route.height}, {width: 80, height: 22});
    assert.equal(S.byName.get('Route110').y, 160, 'The raw overview origin stays unchanged');
    assert.equal(east.y, 158, 'Only the active whole-map origin uses the local connection');
    assert.equal(S.maps.length, 518);
    assert.equal(editor.hit({x: 501, y: 201}), null, 'An inactive interior is not hit-testable');
    assert.equal(editor.hit({x: 200, y: 224.5}).name, 'Route110');
    pristine(env);
    await editor.changeView('world', S.selected, {fit: false});
    assert.equal(editor.viewMaps().length, 518);
    assert.equal(editor.map('Route110').y, 160);
    assert.deepEqual(plain(catalog), before);
    pristine(env);
  });

  await test('pending brush edits reuse the same buffers across views and undo exactly', async env => {
    const originalBuffer = env.S.buffers.get('Route103');
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 37.5, 6.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
    await env.editor.changeView('connected', 'Route103', {fit: false});
    assert.equal(env.S.buffers.get('Route103'), originalBuffer);
    assert.equal(originalBuffer.dirty, true);
    await env.editor.changeView('world', 'Route103', {fit: false});
    assert.equal(env.S.buffers.get('Route103'), originalBuffer);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
    await env.editor.changeView('connected', 'Route103', {fit: false});
    env.editor.undo(true);
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
  });

  await test('one brush stroke crosses Route103 to Route110 using original source cells', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['78,6', '79,6']);
    assert.deepEqual(changedCells(env, 'Route110'), ['0,66', '1,66']);
    unchangedMetadata(env);
    await env.editor.changeView('world', 'Route103', {fit: false});
    env.editor.undo();
    pristine(env);
  });

  await test('rectangles stay on the complete source grid at a negative display origin', async env => {
    env.editor.applyView('connected', 'Route103', {position: {x: -200, y: -80}, fit: false});
    env.S.tool = 'rectangle';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 27.4, 4.4));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 42.4, 7.4));
    await env.editor.finishStroke();
    const expected = [];
    for (let y = 4; y <= 7; y++) for (let x = 27; x <= 42; x++) expected.push(`${x},${y}`);
    assert.deepEqual(changedCells(env, 'Route103'), expected);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
  });

  await test('active rectangles finish before a view switch can change their origins', async env => {
    env.editor.applyView('connected', 'Route103', {position: {x: -200, y: -80}, fit: false});
    env.S.tool = 'rectangle';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.2, 20.2));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 84.8, 26.8));
    assert.equal(env.editor.applyView('world'), false, 'Raw view changes are blocked during a gesture');
    assert.equal(env.S.view, 'connected');
    assert.equal(await env.editor.changeView('world', 'Route103', {fit: false}), true);
    assert.equal(env.S.drag, null);
    assert.deepEqual(changedCells(env, 'Route103'), ['78,20', '79,20', '78,21', '79,21']);
    assert.deepEqual(changedCells(env, 'Route110'), []);
    env.editor.undo();
    pristine(env);
  });

  await test('event drag uses source positions and remains undoable after changing view', async env => {
    env.editor.applyView('connected', 'Route103', {position: {x: 17, y: -50}, fit: false});
    env.S.mode = 'events';
    env.S.tool = 'select';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.7, 6.8));
    assert.equal(env.S.drag.event, true);
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 41.7, 12.8));
    await env.editor.changeView('world', 'Route103', {fit: false});
    const event = env.S.buffers.get('Route103').data.map.object_events[0];
    assert.deepEqual(plain(event), {...env.original.get('Route103').map.object_events[0], x: 41, y: 12});
    assert.deepEqual(changedCells(env, 'Route103'), []);
    env.editor.undo();
    pristine(env);
    env.editor.undo(true);
    assert.equal(env.S.buffers.get('Route103').data.map.object_events[0].y, 12);
  });

  await test('event placement on a moved neighboring map stores its original local coordinates', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    env.S.mode = 'events';
    env.S.place = true;
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route110', 8.5, 66.5));
    const event = env.S.buffers.get('Route110').data.map.object_events[0];
    assert.deepEqual({x: event.x, y: event.y}, {x: 8, y: 66});
    env.editor.undo();
    pristine(env);
  });

  await test('reanchoring follows exact neighboring connections and retains buffer identities', async env => {
    const buffers = new Map(env.S.buffers);
    env.editor.applyView('connected', 'Route103', {fit: false});
    await env.editor.selectMap('Route110');
    const before = plain(env.editor.map('Route110'));
    await env.editor.changeView('connected', 'Route110', {fit: false});
    assert.equal(env.S.connected.anchor, 'Route110');
    assert.deepEqual(Array.from(env.editor.viewMaps(), row => row.name), ['Route110', 'MauvilleCity', 'SlateportCity', 'Route103']);
    assert.deepEqual({x: env.editor.map('Route110').x, y: env.editor.map('Route110').y}, {x: before.x, y: before.y});
    assert.equal(env.editor.map('MauvilleCity').y, before.y - 20);
    assert.equal(env.editor.map('SlateportCity').y, before.y + 100);
    assert.equal(env.editor.map('Route103').y, before.y + 60);
    env.editor.pruneRasters();
    for (const [name, buffer] of buffers) assert.equal(env.S.buffers.get(name), buffer);
    pristine(env);
  });

  await test('selection outside the active neighborhood loads another neighborhood without losing edits', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    await env.editor.finishStroke();
    await env.editor.selectMap('MauvilleCity');
    assert.equal(env.S.connected.anchor, 'MauvilleCity');
    assert.equal(env.S.selected, 'MauvilleCity');
    assert.deepEqual(Array.from(env.editor.viewMaps(), row => row.name), ['MauvilleCity', 'Route110']);
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6']);
    env.editor.undo();
    pristine(env);
    await env.editor.fitWorld();
    assert.equal(env.editor.viewMaps().length, 518);
  });

  await test('selection stays visible when reanchoring is refused while a stroke finishes', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    env.S.finishing = true;
    await env.editor.selectMap('MauvilleCity');
    assert.equal(env.S.selected, 'Route103');
    assert.equal(env.S.connected.anchor, 'Route103');
    assert.equal(env.S.viewByName.has(env.S.selected), true);
    assert.equal(await env.editor.changeView('world', 'Route103', {fit: false}), false);
    assert.equal(env.S.view, 'connected');
    env.S.finishing = false;
    pristine(env);
  });

  await test('an inactive old selection cannot paint an invisible overview rectangle', async env => {
    env.editor.applyView('connected', 'MauvilleCity', {fit: false});
    assert.equal(env.S.selected, 'Route103');
    assert.equal(env.S.viewByName.has('Route103'), false);
    assert.equal(env.editor.map('Route103').y, 218, 'Fallback metadata remains available to cached buffers');
    assert.equal(env.editor.hit({x: 154.5, y: 224.5}), null, 'Hit testing must use only visible maps');
    pristine(env);
  });

  await test('drawing uses one complete rectangle per map with no transformed or cropped image', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    const row = env.editor.map('Route103'), image = {width: 256, height: 70.4};
    env.S.previews.set(row.name, image);
    env.editor.drawMap(row);
    const calls = env.editor.canvas.getContext('2d').calls;
    const draws = calls.filter(call => call.op === 'drawImage');
    assert.equal(draws.length, 1);
    assert.equal(draws[0].args[0], image);
    assert.equal(draws[0].args.length, 5, 'Source images are not cropped into partial segments');
    assert.deepEqual(draws[0].args.slice(-2), [80 * 16, 22 * 16]);
    assert.equal(calls.filter(call => call.op === 'transform').length, 0);
    pristine(env);
  });

  await test('save includes inactive pending buffers and excludes every display-view field', async env => {
    const before = plain(env.catalog);
    env.editor.applyView('connected', 'Route103', {fit: false});
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    await env.editor.selectMap('MauvilleCity');
    assert.equal(env.S.viewByName.has('Route103'), false);
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
      assert.ok(!JSON.stringify(edit).includes('projection'));
    }
    assert.equal(env.S.buffers.get('Route103').dirty, false);
    assert.deepEqual(plain(env.catalog), before);
    assert.equal(env.S.buffers.get('Route103').data.height, 22);
    await env.editor.changeView('world', 'MauvilleCity', {fit: false});
    assert.equal(env.editor.viewMaps().length, 518);
  });

  await test('a slow join focus cannot override a newer map selection', async env => {
    const slow = env.deferBuffer('Route103');
    let settled = false;
    const focusing = env.editor.focusJoin({a: 'Route103', b: 'Route110', direction: 'right', offset: -60})
      .finally(() => { settled = true; });
    await slow.started;
    assert.equal(settled, false, 'The old join focus must actually be awaiting its map request');
    const oldRequest = env.S.selectionRequest;
    await env.editor.selectMap('MauvilleCity');
    env.editor.fitSelection();
    assert.ok(env.S.selectionRequest > oldRequest, 'A newer selection was made while the first map loaded');
    const newer = {selected: env.S.selected, view: env.S.view, revision: env.S.viewRevision,
      camera: plain(env.S.camera), scale: env.S.scale, heading: env.document.querySelector('#selected-name').textContent};
    slow.resolve();
    await focusing;
    assert.equal(settled, true);
    assert.equal(env.S.selected, 'MauvilleCity');
    assert.equal(env.S.selected, newer.selected);
    assert.equal(env.S.view, newer.view);
    assert.equal(env.S.viewRevision, newer.revision, 'Stale completion must not apply another display view');
    assert.equal(env.S.connected, null);
    assert.deepEqual(plain(env.S.camera), newer.camera);
    assert.equal(env.S.scale, newer.scale);
    assert.equal(env.document.querySelector('#selected-name').textContent, newer.heading);
    assert.equal(env.editor.viewMaps().length, 518);
    pristine(env);
  });

  await test('a slow join focus cannot reopen connected view after a newer World overview action', async env => {
    env.editor.applyView('connected', 'Route103', {fit: false});
    const slow = env.deferBuffer('Route103');
    let settled = false;
    const focusing = env.editor.focusJoin({a: 'Route103', b: 'Route110', direction: 'right', offset: -60})
      .finally(() => { settled = true; });
    await slow.started;
    assert.equal(settled, false);
    const oldRequest = env.S.selectionRequest, oldRevision = env.S.viewRevision;
    await env.editor.fitWorld();
    assert.equal(env.S.selectionRequest, oldRequest, 'This race changes only the view, not the selected map');
    assert.ok(env.S.viewRevision > oldRevision, 'World overview superseded the pending join focus');
    const newer = {camera: plain(env.S.camera), scale: env.S.scale, revision: env.S.viewRevision};
    slow.resolve();
    await focusing;
    assert.equal(settled, true);
    assert.equal(env.S.view, 'world');
    assert.equal(env.S.connected, null);
    assert.equal(env.S.viewByName.size, 0);
    assert.equal(env.S.viewRevision, newer.revision);
    assert.deepEqual(plain(env.S.camera), newer.camera);
    assert.equal(env.S.scale, newer.scale);
    assert.equal(env.editor.viewMaps().length, 518);
    pristine(env);
  });

  assert.equal(fs.readFileSync(filename, 'utf8'), originalScript, 'Test must not edit the application script');
  console.log(`World map editing passed: ${tests} actual-handler regression tests; all data stayed in isolated VM memory.`);
})().catch(error => {console.error(error); process.exitCode = 1;});
