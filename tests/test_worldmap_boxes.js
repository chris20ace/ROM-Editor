'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/worldmap-boxes.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));

function harness(options = {}) {
  const initialRows = options.rows || [
    {name: 'Route102', display_name: 'Route 102', x: 10, y: 20, width: 40, height: 20},
    {name: 'PetalburgCity', x: 60, y: 20, width: 20, height: 20},
  ];
  const S = {maps: copy(initialRows), selected: initialRows[0].name, tool: 'arrange', mode: 'terrain',
    ready: true, busy: false, finishing: false, drag: null, pointerActive: null, press: 0, scale: 3,
    catalog: {groups: [{names: initialRows.map(row => row.name)}], sections: []}};
  const nodes = new Map(), calls = [], dialogs = [], toasts = [], drawCalls = [], selectionLoads = [];
  let positions = copy(options.positions || {}), revision = 1, saveFailure = null;
  let controller, pendingParts = null;
  const ctx = new Proxy({}, {get: (target, key) => target[key] || (target[key] = (...args) => drawCalls.push([key, ...args]))});
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {hidden: false, disabled: false, value: '',
      textContent: '', attributes: {}, setAttribute(name, value) {this.attributes[name] = value;}, getContext: () => ctx});
    return nodes.get(selector);
  };
  const map = (name = S.selected) => S.maps.find(row => row.name === name);
  const buffers = new Map(initialRows.map(row => [row.name, {data: {revision: `${row.name}-revision`}, raster: {name: row.name}}]));
  for (const name of options.uncachedMaps || []) buffers.delete(name);
  const boundsOf = rows => ({x: Math.min(...rows.map(row => row.x)), y: Math.min(...rows.map(row => row.y)),
    width: Math.max(...rows.map(row => row.x + row.width)) - Math.min(...rows.map(row => row.x)),
    height: Math.max(...rows.map(row => row.y + row.height)) - Math.min(...rows.map(row => row.y))});
  const api = async (url, body) => {
    calls.push({url, ...(body === undefined ? {} : {body: copy(body)})});
    if (url === '/api/worldmap/positions') {
      if (body !== undefined) {
        if (saveFailure) {const error = saveFailure; saveFailure = null; throw error;}
        assert.equal(body.revision, `positions-${revision}`);
        for (const [name, value] of Object.entries(body.positions)) {
          if (value === null) delete positions[name]; else positions[name] = copy(value);
        }
        revision++;
      }
      return {revision: `positions-${revision}`, positions: copy(positions)};
    }
    if (url === '/api/worldmap/split/preview') {
      const row = map(body.name), vertical = body.axis === 'vertical', name = body.new_name || `${row.name}Part2`;
      return {new_name: name, world_revision: 'source-world-revision', warnings: [], parts: [
        {name: row.name, width: vertical ? body.cut : row.width, height: vertical ? row.height : body.cut, origin_x: 0, origin_y: 0},
        {name, width: vertical ? row.width - body.cut : row.width, height: vertical ? row.height : row.height - body.cut,
          origin_x: vertical ? body.cut : 0, origin_y: vertical ? 0 : body.cut},
      ]};
    }
    if (url === '/api/worldmap/split') {
      const row = map(body.name), vertical = body.axis === 'vertical';
      pendingParts = [
        {name: row.name, width: vertical ? body.cut : row.width, height: vertical ? row.height : body.cut, origin_x: 0, origin_y: 0},
        {name: body.new_name, width: vertical ? row.width - body.cut : row.width, height: vertical ? row.height : row.height - body.cut,
          origin_x: vertical ? body.cut : 0, origin_y: vertical ? 0 : body.cut},
      ];
      return {parts: copy(pendingParts)};
    }
    throw new Error(`Unexpected API call ${url}`);
  };
  const answers = [...(options.answers || [])];
  const dialog = (title, html, action) => {
    dialogs.push({title, html, action});
    if (html.includes('route-split-axis')) {
      $('#route-split-axis').value = /value="horizontal" selected/.test(html) ? 'horizontal' : 'vertical';
      $('#route-split-cut').value = /id="route-split-cut"[^>]+value="([^"]+)"/.exec(html)[1];
    }
    const answer = answers.shift();
    return Promise.resolve(answer === 'defaults'
      ? {axis: $('#route-split-axis').value, cut: $('#route-split-cut').value, new_name: ''}
      : (answer ?? null));
  };
  const dependencies = {S, $, api, map, ctx, dialog, boundsOf, included: row => !options.excluded?.includes(row.name), esc: value => String(value),
    render() {}, toast: (...args) => toasts.push(args), status: () => controller?.sync(),
    screen: (x, y) => ({x: x * S.scale, y: y * S.scale}),
    buffer: name => buffers.get(name), ensureBuffer: async name => buffers.get(name), rasterize: b => b.raster,
    selectMap: async name => {
      S.selected = name;
      controller.focusMap(name);
      const load = {name, complete: false};
      selectionLoads.push(load);
      if (options.selectionWait) await options.selectionWait;
      buffers.set(name, {data: {revision: `${name}-revision`}, raster: {name}});
      load.complete = true;
    }, requireSavedWorld: async () => options.saved !== false,
    setTool: tool => {S.tool = tool;}, fitBounds: bounds => {S.fit = copy(bounds);},
    reloadWorld: async name => {
      S.maps = [...copy(initialRows.filter(row => row.name !== name)), ...pendingParts.map(part => ({...part, x: 100, y: 200}))];
      S.catalog.groups = [{names: S.maps.map(row => row.name)}];
      S.selected = name;
      await controller.load();
    },
  };
  const sandbox = vm.createContext({});
  vm.runInContext(source, sandbox, {filename: 'worldmap-boxes.js'});
  controller = sandbox.WorldMapBoxes.create(dependencies);
  const down = async (point, name = S.selected, modifiers = {}) => {
    S.press++;
    S.pointerActive = 5;
    return controller.pointerDown({button: 0, pointerId: 5, ...modifiers}, point, map(name), S.press);
  };
  const finish = async () => {
    const drag = S.drag;
    S.drag = null;
    S.pointerActive = null;
    return controller.finish(drag);
  };
  return {S, $, map, controller, calls, dialogs, toasts, drawCalls, selectionLoads, down, finish, boundsOf,
    posts: () => calls.filter(call => call.body !== undefined),
    failSave: error => {saveFailure = error;},
    resetCatalog: () => {S.maps = copy(initialRows);},
  };
}

test('load reapplies saved map positions and expands world and group bounds', async () => {
  const h = harness({positions: {Route102: {x: -100, y: 300}}});
  await h.controller.load();
  assert.equal(h.map().x, -100);
  assert.equal(h.map().y, 300);
  assert.deepEqual(h.S.catalog.bounds, {x: -100, y: 20, width: 180, height: 300});
  assert.deepEqual(h.S.catalog.groups[0].bounds, h.S.catalog.bounds);
  assert.equal(h.controller.hasOverride('Route102'), true);
  h.resetCatalog();
  await h.controller.load();
  assert.equal(h.map().x, -100);
  assert.equal(h.map().y, 300);
});

test('moving a box rounds to tiles, persists only that box, and undo removes its override', async () => {
  const h = harness();
  await h.controller.load();
  await h.down({x: 12, y: 25});
  h.controller.pointerMove({x: 19.6, y: 29.4});
  assert.deepEqual({x: h.map().x, y: h.map().y}, {x: 18, y: 24});
  assert.equal(h.posts().length, 0, 'drag preview must not save before release');
  await h.finish();
  assert.deepEqual(h.posts()[0].body.positions, {Route102: {x: 18, y: 24}});
  assert.equal(h.$('#undo-map-move').disabled, false);
  await h.$('#undo-map-move').onclick();
  assert.deepEqual(h.posts()[1].body.positions, {Route102: null});
  assert.deepEqual({x: h.map().x, y: h.map().y}, {x: 10, y: 20});
  assert.equal(h.$('#undo-map-move').disabled, true);
});

test('undo restores an existing override and keeps the other box in place', async () => {
  const h = harness({positions: {Route102: {x: 100, y: 100}, PetalburgCity: {x: 300, y: 500}}});
  await h.controller.load();
  await h.down({x: 101, y: 101});
  h.controller.pointerMove({x: 121, y: 141});
  await h.finish();
  await h.$('#undo-map-move').onclick();
  assert.deepEqual(h.posts()[1].body.positions, {Route102: {x: 100, y: 100}});
  assert.deepEqual({x: h.map('PetalburgCity').x, y: h.map('PetalburgCity').y}, {x: 300, y: 500});
});

test('failed position save restores the box and offers no invalid undo', async () => {
  const h = harness();
  await h.controller.load();
  const before = copy(h.S.catalog.bounds);
  await h.down({x: 11, y: 21});
  h.controller.pointerMove({x: 200, y: 300});
  h.failSave(new Error('World positions changed since loading'));
  await h.finish();
  assert.deepEqual({x: h.map().x, y: h.map().y}, {x: 10, y: 20});
  assert.deepEqual(h.S.catalog.bounds, before);
  assert.equal(h.S.finishing, false);
  assert.equal(h.$('#undo-map-move').disabled, true);
  assert.match(h.toasts.at(-1)[0], /changed since/);
});

test('cancel restores an active drag without sending writes', async () => {
  const h = harness();
  await h.controller.load();
  await h.down({x: 11, y: 21});
  h.controller.pointerMove({x: 70, y: 90});
  assert.equal(h.controller.cancel(), true);
  assert.equal(h.S.drag, null);
  assert.deepEqual({x: h.map().x, y: h.map().y}, {x: 10, y: 20});
  assert.equal(h.posts().length, 0);
  assert.equal(h.controller.cancel(), false);
});

test('a click without movement makes no position save', async () => {
  const h = harness();
  await h.controller.load();
  await h.down({x: 11, y: 21});
  await h.finish();
  assert.equal(h.posts().length, 0);
});

test('a newly selected box can be dragged and saved before its artwork finishes loading', async () => {
  let resolveArtwork;
  const artwork = new Promise(resolve => {resolveArtwork = resolve;});
  const h = harness({selectionWait: artwork, uncachedMaps: ['PetalburgCity']});
  await h.controller.load();
  const pointerDown = h.down({x: 61, y: 21}, 'PetalburgCity');
  assert.equal(h.S.selected, 'PetalburgCity');
  assert.equal(h.S.drag?.name, 'PetalburgCity', 'geometry must begin dragging before waiting for artwork');
  assert.equal(h.selectionLoads[0].complete, false);
  h.controller.pointerMove({x: 74, y: 29});
  await h.finish();
  await pointerDown;
  assert.deepEqual(h.posts()[0].body.positions, {PetalburgCity: {x: 73, y: 28}});
  assert.equal(h.selectionLoads[0].complete, false, 'the whole drag must finish while artwork remains pending');
  resolveArtwork();
  await artwork;
  await Promise.resolve();
  assert.equal(h.selectionLoads[0].complete, true);
  assert.equal(h.S.drag, null, 'late artwork must not revive a completed gesture');
  assert.deepEqual({x: h.map('PetalburgCity').x, y: h.map('PetalburgCity').y}, {x: 73, y: 28});
});

test('vertical drawn line becomes a cut at its map-local tile coordinate', async () => {
  const h = harness({answers: ['defaults', null]});
  await h.controller.load();
  h.S.tool = 'splitmap';
  await h.down({x: 23.1, y: 20});
  h.controller.pointerMove({x: 24.2, y: 40});
  await h.finish();
  const preview = h.posts()[0];
  assert.equal(preview.url, '/api/worldmap/split/preview');
  assert.deepEqual(preview.body, {name: 'Route102', revision: 'Route102-revision', axis: 'vertical', cut: 14});
  assert.equal(h.posts().length, 1, 'canceling confirmation must not call the split endpoint');
});

test('horizontal drawn line becomes a top/bottom cut, preserving source until approval', async () => {
  const h = harness({answers: ['defaults', null]});
  await h.controller.load();
  const before = copy(h.S.maps);
  h.S.tool = 'splitmap';
  await h.down({x: 10, y: 27.8});
  h.controller.pointerMove({x: 50, y: 28.1});
  await h.finish();
  assert.equal(h.posts()[0].body.axis, 'horizontal');
  assert.equal(h.posts()[0].body.cut, 8);
  assert.deepEqual(h.S.maps, before);
  assert.equal(h.S.busy, false);
  assert.equal(h.dialogs.length, 2);
});

test('Split in half uses the entire selected route regardless of tile selection', async () => {
  const h = harness({answers: ['defaults', null]});
  await h.controller.load();
  h.S.tool = 'splitmap';
  h.S.tileSelection = {name: 'Route102', x: 0, y: 0, width: 16, height: 16};
  await h.$('#split-midpoint').onclick();
  assert.equal(h.posts()[0].body.cut, 20);
  assert.equal(h.posts()[0].body.axis, 'vertical');
  assert.match(h.$('#route-split-dimensions').textContent, /20 × 20 tiles \+ 20 × 20 tiles/);
});

test('canceling either a drawn line or the initial split form sends no writes', async () => {
  const h = harness({answers: [null]});
  await h.controller.load();
  h.S.tool = 'splitmap';
  await h.down({x: 25, y: 20});
  h.controller.pointerMove({x: 25, y: 40});
  h.controller.cancel();
  assert.equal(h.posts().length, 0);
  await h.controller.split();
  assert.equal(h.posts().length, 0);
  assert.equal(h.S.maps.length, 2);
});

test('unsaved-world cancellation prevents even the source split preview request', async () => {
  const h = harness({answers: ['defaults'], saved: false});
  await h.controller.load();
  await h.controller.split();
  assert.equal(h.posts().length, 0);
});

test('approved split reloads two independent boxes and places both at the former route position', async () => {
  const h = harness({answers: ['defaults', {}], positions: {Route102: {x: -60, y: 110}}});
  await h.controller.load();
  h.S.tool = 'splitmap';
  await h.controller.split();
  assert.deepEqual(h.posts().map(call => call.url), ['/api/worldmap/split/preview', '/api/worldmap/split', '/api/worldmap/positions']);
  assert.equal(h.posts()[1].body.world_revision, 'source-world-revision');
  assert.deepEqual(h.posts()[2].body.positions, {Route102: {x: -60, y: 110}, Route102Part2: {x: -37, y: 110}});
  assert.equal(h.map('Route102').width, 20);
  assert.equal(h.map('Route102Part2').width, 20);
  assert.equal(h.S.tool, 'arrange');
  assert.deepEqual(h.S.fit, {x: -60, y: 110, width: 43, height: 20});
});

test('drawn cuts keep both pieces large enough for Emerald connection strips', async () => {
  const cases = [
    {start: {x: 11, y: 20}, end: {x: 11, y: 40}, axis: 'vertical', cut: 8},
    {start: {x: 49, y: 20}, end: {x: 49, y: 40}, axis: 'vertical', cut: 32},
    {start: {x: 10, y: 21}, end: {x: 50, y: 21}, axis: 'horizontal', cut: 7},
    {start: {x: 10, y: 39}, end: {x: 50, y: 39}, axis: 'horizontal', cut: 13},
  ];
  for (const sample of cases) {
    const h = harness({answers: ['defaults', null]});
    await h.controller.load();
    h.S.tool = 'splitmap';
    await h.down(sample.start);
    h.controller.pointerMove(sample.end);
    await h.finish();
    assert.equal(h.posts()[0].body.axis, sample.axis);
    assert.equal(h.posts()[0].body.cut, sample.cut);
    assert.equal(Number(h.$('#route-split-cut').min), sample.axis === 'vertical' ? 8 : 7);
    assert.equal(Number(h.$('#route-split-cut').max), sample.axis === 'vertical' ? 32 : 13);
  }
});

test('a narrow map disables the unavailable vertical split and defaults to its valid axis', async () => {
  const h = harness({answers: ['defaults', null], rows: [
    {name: 'NarrowRoute', x: 0, y: 0, width: 15, height: 14},
  ]});
  await h.controller.load();
  await h.controller.split();
  assert.equal(h.posts()[0].body.axis, 'horizontal');
  assert.equal(h.posts()[0].body.cut, 7);
  assert.match(h.dialogs[0].html, /<option[^>]*value="vertical"[^>]*disabled/);
  assert.equal(Number(h.$('#route-split-cut').min), 7);
  assert.equal(Number(h.$('#route-split-cut').max), 7);
});

test('a short map disables the unavailable horizontal split', async () => {
  const h = harness({answers: ['defaults', null], rows: [
    {name: 'ShortRoute', x: 0, y: 0, width: 16, height: 13},
  ]});
  await h.controller.load();
  await h.controller.split();
  assert.equal(h.posts()[0].body.axis, 'vertical');
  assert.equal(h.posts()[0].body.cut, 8);
  assert.match(h.dialogs[0].html, /<option[^>]*value="horizontal"[^>]*disabled/);
  assert.equal(Number(h.$('#route-split-cut').min), 8);
  assert.equal(Number(h.$('#route-split-cut').max), 8);
});

test('a map too small on both axes cannot request a source split', async () => {
  const h = harness({answers: ['defaults'], rows: [
    {name: 'SmallRoom', x: 0, y: 0, width: 15, height: 13},
  ]});
  await h.controller.load();
  await h.controller.split();
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.posts().length, 0);
  assert.equal(h.toasts.at(-1)[1], true);
});


test('Shift, Ctrl and Meta click toggle whole maps without starting a drag or saving', async () => {
  const h = harness();await h.controller.load();
  assert.equal(h.$('#map-box-count').textContent, '0 maps selected');
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity', {ctrlKey: true});
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  assert.equal(h.S.drag, null);
  await h.down({x: 11, y: 21}, 'Route102', {metaKey: true});
  assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  await h.down({x: 61, y: 21}, 'PetalburgCity', {shiftKey: true});
  assert.equal(h.$('#map-box-count').textContent, '0 maps selected');
  assert.equal(h.posts().length, 0);
});

test('dragging either selected map translates the entire group with one rounded delta and one atomic save', async () => {
  const h = harness();await h.controller.load();
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity', {shiftKey: true});
  await h.down({x: 11, y: 21}, 'Route102');
  h.controller.pointerMove({x: 18.6, y: 25.4});
  assert.deepEqual(h.S.maps.map(row => ({x: row.x, y: row.y})), [{x: 18, y: 24}, {x: 68, y: 24}]);
  assert.equal(h.posts().length, 0);
  await h.finish();
  assert.equal(h.posts().length, 1);
  assert.deepEqual(h.posts()[0].body.positions, {Route102: {x: 18, y: 24}, PetalburgCity: {x: 68, y: 24}});
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  await h.down({x: 69, y: 25}, 'PetalburgCity');h.controller.pointerMove({x: 70, y: 23});await h.finish();
  assert.deepEqual(h.posts()[1].body.positions, {Route102: {x: 19, y: 22}, PetalburgCity: {x: 69, y: 22}});
});

test('one group Undo restores old overrides and removes newly created overrides together', async () => {
  const h = harness({positions: {Route102: {x: -25, y: 80}}});await h.controller.load();
  await h.down({x: -24, y: 81}, 'Route102', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity');h.controller.pointerMove({x: 65, y: 27});await h.finish();
  await h.$('#undo-map-move').onclick();
  assert.deepEqual(h.posts()[1].body.positions, {Route102: {x: -25, y: 80}, PetalburgCity: null});
  assert.deepEqual(h.S.maps.map(row => ({x: row.x, y: row.y})), [{x: -25, y: 80}, {x: 60, y: 20}]);
  assert.equal(h.$('#undo-map-move').disabled, true);
});

test('group cancellation and failed save restore every exact map origin', async () => {
  const h = harness();await h.controller.load();const original = copy(h.S.maps);
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity', {shiftKey: true});
  await h.down({x: 11, y: 21}, 'Route102');h.controller.pointerMove({x: -200, y: 500});
  assert.equal(h.controller.cancel(), true);assert.deepEqual(h.S.maps, original);assert.equal(h.posts().length, 0);
  await h.down({x: 11, y: 21}, 'Route102');h.controller.pointerMove({x: 1000, y: 2000});
  h.failSave(new Error('stale group positions'));await h.finish();
  assert.deepEqual(h.S.maps, original);assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  assert.equal(h.$('#undo-map-move').disabled, true);assert.equal(h.S.finishing, false);
  assert.equal(h.posts().length, 1);assert.deepEqual(h.S.catalog.bounds, h.boundsOf(original));
});

test('normal click outside the selected group replaces it with a single map', async () => {
  const h = harness();await h.controller.load();
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 61, y: 21}, 'PetalburgCity');h.controller.pointerMove({x: 62, y: 22});await h.finish();
  assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  assert.deepEqual(h.posts()[0].body.positions, {PetalburgCity: {x: 61, y: 21}});
});

test('empty-space marquee includes only fully enclosed visible maps and resets on every frame', async () => {
  const h = harness({rows: [
    {name: 'A', x: 10, y: 10, width: 10, height: 10},
    {name: 'B', x: 30, y: 10, width: 10, height: 10},
    {name: 'HiddenInterior', x: 10, y: 10, width: 10, height: 10},
  ], excluded: ['HiddenInterior']});await h.controller.load();
  await h.down({x: 0, y: 0}, null);h.controller.pointerMove({x: 45, y: 25});
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  h.controller.pointerMove({x: 35, y: 25});assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  h.controller.pointerMove({x: 45, y: 25});await h.finish();
  await h.down({x: 11, y: 11}, 'A');h.controller.pointerMove({x: 12, y: 12});await h.finish();
  assert.deepEqual(h.posts()[0].body.positions, {A: {x: 11, y: 11}, B: {x: 31, y: 11}});
  assert.deepEqual({x: h.map('HiddenInterior').x, y: h.map('HiddenInterior').y}, {x: 10, y: 10});
});

test('additive marquee preserves the pregesture group but removes newly added maps when dragged back', async () => {
  const h = harness();await h.controller.load();
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 55, y: 15}, null, {ctrlKey: true});h.controller.pointerMove({x: 85, y: 45});
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  h.controller.pointerMove({x: 70, y: 45});assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  h.controller.pointerMove({x: 85, y: 45});await h.finish();
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');assert.equal(h.posts().length, 0);
});

test('Select maps button starts a one-shot marquee on top of a map and cancel restores selection', async () => {
  const h = harness();await h.controller.load();
  await h.down({x: 61, y: 21}, 'PetalburgCity', {shiftKey: true});
  h.$('#select-map-boxes').onclick();assert.equal(h.$('#select-map-boxes').attributes['aria-pressed'], 'true');
  await h.down({x: 10, y: 20}, 'Route102');h.controller.pointerMove({x: 50, y: 40});
  assert.equal(h.S.drag.mapBox, 'marquee');assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  assert.equal(h.controller.cancel(), true);
  assert.equal(h.$('#select-map-boxes').attributes['aria-pressed'], 'false');
  await h.down({x: 61, y: 21}, 'PetalburgCity');h.controller.pointerMove({x: 62, y: 22});await h.finish();
  assert.deepEqual(h.posts()[0].body.positions, {PetalburgCity: {x: 61, y: 21}});
  h.$('#select-map-boxes').onclick();await h.down({x: 10, y: 20}, 'Route102');h.controller.pointerMove({x: 85, y: 45});await h.finish();
  assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  assert.equal(h.$('#select-map-boxes').attributes['aria-pressed'], 'false');
});

test('empty click clears selection, additive empty click preserves it, and Escape cancels then clears', async () => {
  const h = harness();await h.controller.load();
  await h.down({x: 11, y: 21}, 'Route102', {shiftKey: true});
  await h.down({x: 0, y: 0}, null, {metaKey: true});await h.finish();
  assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  await h.down({x: 0, y: 0}, null);await h.finish();assert.equal(h.$('#map-box-count').textContent, '0 maps selected');
  await h.down({x: 11, y: 21}, 'Route102');h.controller.pointerMove({x: 30, y: 40});
  let prevented = 0;const escape = {key: 'Escape', preventDefault() {prevented++;}};
  assert.equal(h.controller.key(escape), true);assert.equal(h.map().x, 10);assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  assert.equal(h.controller.key(escape), true);assert.equal(h.$('#map-box-count').textContent, '0 maps selected');
  assert.equal(h.controller.key(escape), false);assert.equal(prevented, 2);assert.equal(h.posts().length, 0);
});

test('sidebar focus preserves an existing group, replaces it for another map, and load clears selection and Undo', async () => {
  const h = harness({rows: [
    {name: 'A', x: 0, y: 0, width: 10, height: 10}, {name: 'B', x: 20, y: 0, width: 10, height: 10},
    {name: 'C', x: 40, y: 0, width: 10, height: 10},
  ]});await h.controller.load();
  await h.down({x: 1, y: 1}, 'A', {shiftKey: true});await h.down({x: 21, y: 1}, 'B', {shiftKey: true});
  h.controller.focusMap('A');assert.equal(h.$('#map-box-count').textContent, '2 maps selected');
  h.controller.draw();assert.equal(h.drawCalls.filter(call => call[0] === 'strokeRect').length, 2);
  h.controller.focusMap('C');assert.equal(h.$('#map-box-count').textContent, '1 map selected');
  await h.down({x: 41, y: 1}, 'C');h.controller.pointerMove({x: 42, y: 2});await h.finish();
  assert.equal(h.$('#undo-map-move').disabled, false);
  h.resetCatalog();await h.controller.load();assert.equal(h.$('#map-box-count').textContent, '0 maps selected');assert.equal(h.$('#undo-map-move').disabled, true);
});
