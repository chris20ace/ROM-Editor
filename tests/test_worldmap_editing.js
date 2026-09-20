'use strict';
// Exercise the real editor handlers without a browser or source-file writes.
// Only the boot call is replaced, in memory, to expose lexical test hooks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const G = require('../web/worldmap-geometry.js');
const plain = value => JSON.parse(JSON.stringify(value));
const filename = path.join(__dirname, '../web/worldmap.js');
const originalScript = fs.readFileSync(filename, 'utf8');
const boot = /\n\s*init\(\);\s*\n\}\)\(\);\s*$/;
assert.ok(boot.test(originalScript), 'Editor boot hook changed; update the test harness explicitly');
const testScript = originalScript.replace(boot, `
  globalThis.editorTest = {S,canvas,snapshot,fingerprint,hit,local,screen,world,
    worldLine,paintWorld,finishStroke,undo,eventAt,addEvent,saveWorld,pickAt,
    drawMap,drawWorld,mapPath,fitSelection,boundsOf};
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
  const requests = [];
  const sandbox = {
    console, document, URLSearchParams, Map, Set, Uint8Array,
    location: {search: '', origin: 'http://test.invalid'}, history: {replaceState() {}},
    requestAnimationFrame: () => 1, setTimeout: () => 1, clearTimeout() {},
    ResizeObserver: class { observe() {} }, Image: class {},
    addEventListener() {}, devicePixelRatio: 1,
    fetch: async (url, options) => {
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
  sandbox.WorldGeometry = G;
  vm.createContext(sandbox);
  vm.runInContext(testScript, sandbox, {filename});
  const editor = sandbox.editorTest, {S} = editor;
  const rows = [
    {name: 'Route103', id: 'MAP_ROUTE103', x: 120, y: 218, width: 80, height: 22,
      projection: {kind: 'vertical-shear', knots: [[0, 0], [28, 0], [40, 2], [80, 2]]}},
    {name: 'Route110', id: 'MAP_ROUTE110', x: 200, y: 160, width: 40, height: 100},
    {name: 'OldaleTown', id: 'MAP_OLDALE_TOWN', x: 120, y: 240, width: 20, height: 20}
  ];
  rows.forEach(row => Object.assign(row, {area_kind: 'route', role_label: 'Routes'}));
  Object.assign(S, {ready: true, selected: 'Route103', maps: rows,
    byName: new Map(rows.map(row => [row.name, row])),
    catalog: {bounds: {x: 120, y: 160, width: 120, height: 100}, conflicts: [], sections: [], groups: []},
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
        warp_events: [], coord_events: [], bg_events: [], connections: []},
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
  return {editor, S, rows, document, requests,
    original: new Map(rows.map(row => [row.name, plain(editor.snapshot(S.buffers.get(row.name)))]))};
}

function eventAtWorld(env, point) {
  const p = env.editor.screen(point.x, point.y);
  return {clientX: p.x + 11, clientY: p.y + 17, pointerId: 1, button: 0, preventDefault() {}};
}
function eventAtLocal(env, map, x, y) {
  return eventAtWorld(env, G.toWorld(env.S.byName.get(map), {x, y}));
}
function changedCells(env, name) {
  const current = env.S.buffers.get(name).data.cells, before = env.original.get(name).cells;
  const width = env.S.byName.get(name).width;
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
  await test('rendering, fitting and picking do not change source coordinates', async env => {
    const {editor, S, rows} = env, row = rows[0];
    for (const [x, y] of [[27, 4], [28, 4], [34, 6], [39, 21], [40, 9], [79, 21]]) {
      const p = G.toWorld(row, {x: x + .5, y: y + .5});
      assert.equal(editor.hit(p).name, row.name);
      assert.deepEqual(plain(editor.local(row, p)), {x, y});
    }
    assert.equal(editor.hit({x: 154, y: 218.5}), null, 'The empty wedge is not a source tile');
    assert.equal(editor.hit({x: 200, y: 226.5}).name, 'Route110');
    S.previews.set(row.name, {width: 256, height: 70.4});
    editor.drawMap(row);
    const calls = editor.canvas.getContext('2d').calls;
    assert.equal(calls.filter(call => call.op === 'drawImage').length, 3, 'All three map segments must be rendered');
    assert.deepEqual(calls.filter(call => call.op === 'transform').map(call => call.args),
      [[16, 0, 0, 16, 0, 0], [16, 16 / 6, 0, 16, 0, 0], [16, 0, 0, 16, 0, 0]]);
    await editor.pickAt({x: 154.5, y: 225.58333333333334});
    assert.equal(S.tile, 1);
    editor.fitSelection();
    assert.deepEqual(plain(S.camera), {x: 160, y: 230}, 'Fit uses the 24-tile projected height');
    pristine(env);
  });

  await test('a brush stroke over sloped water paints source cells and undoes exactly', async env => {
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 37.5, 6.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
    unchangedMetadata(env);
    assert.equal(env.S.history.length, 1);
    env.editor.undo();
    pristine(env);
    env.editor.undo(true);
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6', '35,6', '36,6', '37,6']);
  });

  await test('continuous brush crosses the aligned edge using each map source origin', async env => {
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['78,6', '79,6']);
    assert.deepEqual(changedCells(env, 'Route110'), ['0,66', '1,66']);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
  });

  await test('rectangle spans both projection bends but remains a source rectangle', async env => {
    env.S.tool = 'rectangle';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 27.4, 4.4));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 42.4, 7.4));
    assert.deepEqual(plain(env.S.drag.start), {x: 27, y: 4});
    assert.deepEqual(plain(env.S.drag.last), {x: 42, y: 7});
    await env.editor.finishStroke();
    const expected = [];
    for (let y = 4; y <= 7; y++) for (let x = 27; x <= 42; x++) expected.push(`${x},${y}`);
    assert.deepEqual(changedCells(env, 'Route103'), expected);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
  });

  await test('rectangle dragged beyond the map clamps to source dimensions', async env => {
    env.S.tool = 'rectangle';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.2, 20.2));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 84.8, 26.8));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['78,20', '79,20', '78,21', '79,21']);
    assert.deepEqual(changedCells(env, 'Route110'), []);
    env.editor.undo();
    pristine(env);
  });

  await test('event drag and undo preserve source coordinates and all other properties', async env => {
    env.S.mode = 'events';
    env.S.tool = 'select';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.7, 6.8));
    assert.equal(env.S.drag.event, true);
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 41.7, 12.8));
    await env.editor.finishStroke();
    const event = env.S.buffers.get('Route103').data.map.object_events[0];
    assert.deepEqual(plain(event), {...env.original.get('Route103').map.object_events[0], x: 41, y: 12});
    assert.deepEqual(changedCells(env, 'Route103'), []);
    env.editor.undo();
    pristine(env);
    env.editor.undo(true);
    assert.equal(env.S.buffers.get('Route103').data.map.object_events[0].y, 12);
  });

  await test('event dragging outside a sheared map clamps without relocating its map', async env => {
    env.S.mode = 'events';
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route103', 100, -20));
    await env.editor.finishStroke();
    const event = env.S.buffers.get('Route103').data.map.object_events[0];
    assert.deepEqual({x: event.x, y: event.y}, {x: 79, y: 0});
    assert.equal(env.S.buffers.get('Route110').data.map.object_events.length, 0);
    env.editor.undo();
    pristine(env);
  });

  await test('placing a new event stores the selected source cell, not display offset', async env => {
    env.S.mode = 'events';
    env.S.place = true;
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 45.5, 9.5));
    const event = env.S.buffers.get('Route103').data.map.object_events[1];
    assert.deepEqual({x: event.x, y: event.y}, {x: 45, y: 9});
    assert.equal(env.editor.eventAt(env.rows[0], {x: 165.5, y: 229.5}).index, 1);
    env.editor.undo();
    pristine(env);
  });

  await test('movement painting changes only chosen collision/elevation bits', async env => {
    env.S.mode = 'movement';
    env.document.querySelector('#paint-collision').checked = true;
    env.document.querySelector('#paint-elevation').checked = true;
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 34.5, 6.5));
    await env.editor.finishStroke();
    assert.deepEqual(changedCells(env, 'Route103'), ['34,6']);
    assert.equal(env.S.buffers.get('Route103').data.cells[6 * 80 + 34], (5 << 12) | (2 << 10) | 1);
    unchangedMetadata(env);
    env.editor.undo();
    pristine(env);
  });

  await test('save sends original sizes, coordinates and layouts without projection metadata', async env => {
    const rowsBefore = plain(env.rows);
    await env.editor.canvas.onpointerdown(eventAtLocal(env, 'Route103', 78.5, 6.5));
    env.editor.canvas.onpointermove(eventAtLocal(env, 'Route110', 1.5, 66.5));
    await env.editor.finishStroke();
    await env.editor.saveWorld();
    assert.equal(env.requests.length, 1);
    const request = env.requests[0];
    assert.equal(request.headers['X-Workbench-Token'], 'isolated-test-token');
    assert.deepEqual(request.body.maps.map(edit => edit.name).sort(), ['Route103', 'Route110']);
    for (const edit of request.body.maps) {
      assert.deepEqual(Object.keys(edit).sort(), ['border', 'cells', 'confirm_shared', 'height', 'layout', 'map', 'name', 'revision', 'width']);
      const before = env.original.get(edit.name);
      for (const key of ['width', 'height', 'layout', 'map', 'border']) assert.deepEqual(edit[key], before[key]);
      assert.ok(!JSON.stringify(edit).includes('vertical-shear'));
      assert.equal(edit.revision, 'original-revision');
    }
    assert.deepEqual(plain(env.rows), rowsBefore, 'Saving must not mutate catalog positions or projection');
    assert.equal(env.S.buffers.get('Route103').data.height, 22, 'Display height 24 must never become source height');
  });

  assert.equal(fs.readFileSync(filename, 'utf8'), originalScript, 'Test must not edit the application script');
  console.log(`World map editing passed: ${tests} actual-handler regression tests; all data stayed in isolated VM memory.`);
})().catch(error => {console.error(error); process.exitCode = 1;});
