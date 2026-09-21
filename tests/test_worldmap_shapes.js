'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/worldmap-shapes.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));

function harness(options = {}) {
  const rows = copy(options.rows || [
    {name: 'RouteA', display_name: 'Route A', x: 10, y: 20, width: 16, height: 14},
    {name: 'RouteB', display_name: 'Route B', x: 26, y: 20, width: 8, height: 14},
  ]);
  const S = {maps: rows, selected: options.selected || rows[0].name, ready: true, busy: false,
    finishing: false, drag: null, erasers: new Map(rows.map(row => [row.name, 7])), tile: 9};
  const nodes = new Map(), calls = [], dialogs = [], toasts = [], reloads = [], loads = [], tools = [];
  let statusCount = 0, savedChecks = 0, tileDraws = 0;
  const context = {drawImage() {}, strokeRect() {}};
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {disabled: false, value: '', textContent: '', getContext: () => context});
    return nodes.get(selector);
  };
  const map = (name = S.selected) => rows.find(row => row.name === name);
  const boundsOf = items => {
    const x = Math.min(...items.map(row => row.x)), y = Math.min(...items.map(row => row.y));
    return {x, y, width: Math.max(...items.map(row => row.x + row.width)) - x,
      height: Math.max(...items.map(row => row.y + row.height)) - y};
  };
  const buffers = new Map(rows.map(row => [row.name, {
    raster: {name: row.name}, valid: new Set(options.validTiles || [0, 7, 9]),
    data: {revision: `${row.name}-source`, layout: {primary_tileset: 'gTileset_General',
      secondary_tileset: options.differentTileset === row.name ? 'gTileset_Other' : 'gTileset_Petalburg'}},
  }]));
  const dialogWaiters = new Map();
  const dialog = (title, html, action) => {
    // The real dialog inserts these controls before returning its promise.
    for (const match of html.matchAll(/<input\b[^>]*id="([^"]+)"[^>]*value="([^"]*)"[^>]*>/g)) {
      $('#' + match[1]).value = match[2];
    }
    const fill = /<select id="expand-fill"[^>]*>\s*<option value="([^"]+)"/.exec(html);
    if (fill) $('#expand-fill').value = fill[1];
    let resolve;
    const pending = new Promise(done => {resolve = done;});
    const record = {title, html, action, resolve};
    dialogs.push(record);
    dialogWaiters.get(dialogs.length - 1)?.(record);
    return pending;
  };
  const api = async (url, body) => {
    calls.push({url, body: copy(body)});
    if (url.endsWith('/preview')) {
      if (options.previewFailure) throw new Error(options.previewFailure);
      const row = map(body.name), bounds = boundsOf(rows);
      const width = url.includes('/expand/') ? row.width + body.left + body.right : bounds.width;
      const height = url.includes('/expand/') ? row.height + body.top + body.bottom : bounds.height;
      return {name: body.name, width, height, added_cells: width * height - row.width * row.height,
        world_revision: 'world-preview-token', positions_revision: 'positions-preview-token',
        warnings: options.warnings || ['Story coordinates need review.']};
    }
    if (options.commitFailure) throw new Error(options.commitFailure);
    return {saved: true, name: body.name};
  };
  const dependencies = {S, $, map, boundsOf, dialog, api,
    mapBoxes: {selectionNames: () => options.selection || rows.map(row => row.name),
      positionRevision: () => 'positions-current-token'},
    esc: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    toast: (...args) => toasts.push(args), status: () => {statusCount++;},
    ensureBuffer: async name => {loads.push(name);if (options.bufferFailure) throw new Error(options.bufferFailure);return buffers.get(name);},
    rasterize: b => b.raster, drawTile: () => {tileDraws++;},
    requireSavedWorld: async () => {savedChecks++;return options.saved !== false;},
    reloadWorld: async (...args) => {reloads.push(args);if (options.reloadFailure) throw new Error(options.reloadFailure);S.selected = args[0];},
    setTool: tool => {tools.push(tool);},
  };
  const sandbox = vm.createContext({});
  vm.runInContext(source, sandbox, {filename: 'worldmap-shapes.js'});
  const controller = sandbox.WorldMapShapes.create(dependencies);
  return {S, $, controller, calls, dialogs, toasts, reloads, loads, tools, buffers,
    statusCount: () => statusCount, savedChecks: () => savedChecks, tileDraws: () => tileDraws,
    waitDialog: index => dialogs[index] ? Promise.resolve(dialogs[index]) : new Promise(done => dialogWaiters.set(index, done)),
    expansionValues: () => ({top: $('#expand-top').value, bottom: $('#expand-bottom').value,
      left: $('#expand-left').value, right: $('#expand-right').value, fill_tile: $('#expand-fill').value}),
  };
}

async function previewExpansion(h, values = {top: '2', bottom: '3', left: '4', right: '5', fill_tile: '9'}) {
  const running = h.controller.expand();
  const first = await h.waitDialog(0);
  first.resolve(values);
  return {running, confirm: await h.waitDialog(1)};
}

async function previewMerge(h, values = {name: 'RouteB', encounter_policy: 'matching'}) {
  const running = h.controller.merge();
  const first = await h.waitDialog(0);
  first.resolve(values);
  return {running, confirm: await h.waitDialog(1)};
}

test('expansion controls serialize numeric sides and fill, then commit the exact preview tokens', async () => {
  const h = harness();
  const {running, confirm} = await previewExpansion(h);
  assert.deepEqual(h.calls, [{url: '/api/worldmap/expand/preview', body: {
    name: 'RouteA', revision: 'RouteA-source', fill_tile: 9, top: 2, bottom: 3, left: 4, right: 5,
    positions_revision: 'positions-current-token',
  }}]);
  assert.match(confirm.title, /25 × 19 tiles/);
  assert.match(confirm.html, /Story coordinates need review/);
  confirm.resolve(true);
  await running;
  assert.deepEqual(h.calls[1], {url: '/api/worldmap/expand', body: {
    name: 'RouteA', revision: 'RouteA-source', fill_tile: 9, top: 2, bottom: 3, left: 4, right: 5,
    positions_revision: 'positions-preview-token', world_revision: 'world-preview-token',
  }});
  assert.deepEqual(h.reloads, [['RouteA', true]]);
  assert.deepEqual(h.tools, ['arrange']);
  assert.equal(h.S.selected, 'RouteA');
  assert.equal(h.S.busy, false);
  assert.ok(h.tileDraws() > 0);
});

test('expansion defaults add four columns on the right using the background tile', async () => {
  const h = harness();
  const running = h.$('#expand-map').onclick();
  const first = await h.waitDialog(0);
  assert.deepEqual(h.expansionValues(), {top: '0', bottom: '0', left: '0', right: '4', fill_tile: '7'});
  assert.equal(h.$('#dialog-submit').disabled, false);
  assert.match(h.$('#reshape-dimensions').textContent, /16 × 14 → 20 × 14/);
  first.resolve(null);
  await running;
  assert.equal(h.calls.length, 0);
});

test('zero additions, nonintegers, negative values, dimensions and buffer limits disable preview', async () => {
  const h = harness();
  const running = h.controller.expand();
  const first = await h.waitDialog(0);
  const scenarios = [
    {values: {right: '0'}, warning: /at least one/},
    {values: {right: '1.5'}, warning: /whole numbers/},
    {values: {right: '-1'}, warning: /whole numbers/},
    {values: {right: '255'}, warning: /whole numbers/},
    {values: {right: '240'}, warning: /size limit/},
    {values: {right: '100', bottom: '100'}, warning: /size limit/},
    {values: {right: 'invalid'}, warning: /whole numbers/},
  ];
  for (const scenario of scenarios) {
    for (const side of ['top', 'bottom', 'left', 'right']) h.$('#expand-' + side).value = scenario.values[side] || '0';
    h.$('#expand-right').oninput();
    assert.equal(h.$('#dialog-submit').disabled, true, JSON.stringify(scenario.values));
    assert.match(h.$('#reshape-size-warning').textContent, scenario.warning);
  }
  h.$('#expand-right').value = '1';
  h.$('#expand-bottom').value = '0';
  h.$('#expand-right').oninput();
  assert.equal(h.$('#dialog-submit').disabled, false);
  assert.equal(h.calls.length, 0);
  first.resolve(null);
  await running;
  assert.equal(h.$('#dialog-submit').disabled, false);
});

test('cancelling expansion confirmation performs no source commit or reload', async () => {
  const h = harness();
  const {running, confirm} = await previewExpansion(h);
  confirm.resolve(null);
  await running;
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.reloads, []);
  assert.equal(h.S.busy, false);
});

test('merge sends the exact selected positions and revisions and uses the chosen retained map', async () => {
  const h = harness();
  const {running, confirm} = await previewMerge(h);
  assert.deepEqual(h.calls[0], {url: '/api/worldmap/merge/preview', body: {
    name: 'RouteB', maps: [
      {name: 'RouteA', x: 10, y: 20, revision: 'RouteA-source'},
      {name: 'RouteB', x: 26, y: 20, revision: 'RouteB-source'},
    ], positions_revision: 'positions-current-token',
  }});
  assert.match(confirm.title, /24 × 14 tiles/);
  confirm.resolve(true);
  await running;
  assert.deepEqual(h.calls[1], {url: '/api/worldmap/merge', body: {
    ...h.calls[0].body, positions_revision: 'positions-preview-token', world_revision: 'world-preview-token',
  }});
  assert.deepEqual(h.reloads, [['RouteB', true]]);
  assert.equal(h.S.selected, 'RouteB');
  assert.deepEqual(h.tools, ['arrange']);
});

test('merge explicitly offers encounter choice and only opts into retained encounters when selected', async () => {
  const h = harness({selected: 'RouteB'});
  const {running, confirm} = await previewMerge(h, {name: 'RouteB', encounter_policy: 'keep_primary'});
  assert.match(h.dialogs[0].html, /value="RouteB" selected/);
  assert.match(h.dialogs[0].html, /Only combine if encounter tables match/);
  assert.match(h.dialogs[0].html, /Use the kept map’s encounters for the whole map/);
  assert.equal(h.calls[0].body.encounter_policy, 'keep_primary');
  confirm.resolve(true);
  await running;
  assert.equal(h.calls[1].body.encounter_policy, 'keep_primary');
});

test('merge cancelling either dialog avoids a source commit', async () => {
  const first = harness();
  const firstRunning = first.$('#combine-map-boxes').onclick();
  (await first.waitDialog(0)).resolve(null);
  await firstRunning;
  assert.equal(first.calls.length, 0);
  const second = harness();
  const {running, confirm} = await previewMerge(second);
  confirm.resolve(null);
  await running;
  assert.equal(second.calls.length, 1);
  assert.deepEqual(second.reloads, []);
});

test('merge rejects a gap, overlap, or diagonal contact before loading or posting', async () => {
  for (const position of [{x: 27, y: 20}, {x: 25, y: 20}, {x: 26, y: 34}]) {
    const h = harness({rows: [
      {name: 'RouteA', x: 10, y: 20, width: 16, height: 14},
      {name: 'RouteB', ...position, width: 8, height: 14},
    ]});
    await h.controller.merge();
    assert.equal(h.calls.length, 0);
    assert.equal(h.dialogs.length, 0);
    assert.equal(h.loads.length, 0);
    assert.match(h.toasts.at(-1)[0], /no gaps or overlaps/);
  }
});

test('merge rejects fewer than two boxes and excessive combined dimensions before posting', async () => {
  const one = harness({selection: ['RouteA']});
  await one.controller.merge();
  assert.match(one.toasts.at(-1)[0], /at least two/);
  assert.equal(one.calls.length, 0);
  const large = harness({rows: [
    {name: 'RouteA', x: 0, y: 0, width: 150, height: 14},
    {name: 'RouteB', x: 150, y: 0, width: 150, height: 14},
  ]});
  await large.controller.merge();
  assert.match(large.toasts.at(-1)[0], /map size limit/);
  assert.equal(large.calls.length, 0);
  assert.equal(large.loads.length, 0);
});

test('merge rejects different tileset pairs before posting or opening its dialog', async () => {
  const h = harness({differentTileset: 'RouteB'});
  await h.controller.merge();
  assert.deepEqual(h.loads, ['RouteA', 'RouteB']);
  assert.equal(h.calls.length, 0);
  assert.equal(h.dialogs.length, 0);
  assert.match(h.toasts.at(-1)[0], /different terrain tilesets/);
  assert.equal(h.S.busy, false);
});

test('unsaved-world cancellation stops both operations before buffer loading or API calls', async () => {
  for (const action of ['expand', 'merge']) {
    const h = harness({saved: false});
    await h.controller[action]();
    assert.equal(h.savedChecks(), 1);
    assert.equal(h.loads.length, 0);
    assert.equal(h.dialogs.length, 0);
    assert.equal(h.calls.length, 0);
  }
});

test('failed preview reports the error and does not show confirmation or reload', async () => {
  for (const action of ['expand', 'merge']) {
    const h = harness({previewFailure: 'The source changed; preview again.'});
    const running = h.controller[action]();
    (await h.waitDialog(0)).resolve(action === 'expand'
      ? {top: '0', bottom: '0', left: '0', right: '1', fill_tile: '7'}
      : {name: 'RouteA', encounter_policy: 'matching'});
    await running;
    assert.equal(h.dialogs.length, 1);
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.reloads, []);
    assert.match(h.toasts.at(-1)[0], /source changed/);
    assert.equal(h.S.busy, false);
  }
});

test('failed commit never reloads or reports success', async () => {
  for (const action of ['expand', 'merge']) {
    const h = harness({commitFailure: 'Could not save this map.'});
    const {running, confirm} = action === 'expand' ? await previewExpansion(h) : await previewMerge(h);
    confirm.resolve(true);
    await running;
    assert.equal(h.calls.length, 2);
    assert.deepEqual(h.reloads, []);
    assert.deepEqual(h.tools, []);
    assert.deepEqual(h.toasts.at(-1), ['Could not save this map.', true]);
    assert.equal(h.S.busy, false);
  }
});

test('refresh failure after a successful commit explicitly reports that the change was saved', async () => {
  for (const action of ['expand', 'merge']) {
    const h = harness({reloadFailure: 'Network disconnected.'});
    const {running, confirm} = action === 'expand' ? await previewExpansion(h) : await previewMerge(h);
    confirm.resolve(true);
    await running;
    assert.equal(h.calls.length, 2);
    assert.equal(h.reloads.length, 1);
    assert.match(h.toasts.at(-1)[0], /change was saved, but the view could not reload/);
    assert.match(h.toasts.at(-1)[0], /Network disconnected/);
    assert.equal(h.toasts.at(-1)[1], true);
    assert.deepEqual(h.tools, []);
    assert.equal(h.S.busy, false);
  }
});

test('buffer load failure leaves both operations idle and performs no API calls', async () => {
  for (const action of ['expand', 'merge']) {
    const h = harness({bufferFailure: 'Map data failed to load.'});
    await h.controller[action]();
    assert.equal(h.calls.length, 0);
    assert.equal(h.dialogs.length, 0);
    assert.deepEqual(h.toasts.at(-1), ['Map data failed to load.', true]);
    assert.equal(h.S.busy, false);
  }
});

test('active drag, busy state, unready state, and finishing state guard both actions', async () => {
  for (const state of [{drag: {}}, {busy: true}, {ready: false}, {finishing: true}]) {
    for (const action of ['expand', 'merge']) {
      const h = harness();
      Object.assign(h.S, state);
      await h.controller[action]();
      assert.equal(h.savedChecks(), 0);
      assert.equal(h.loads.length, 0);
      assert.equal(h.calls.length, 0);
      assert.equal(h.dialogs.length, 0);
    }
  }
});

test('expansion requires a valid available background or brush tile', async () => {
  const h = harness({validTiles: [6]});
  await h.controller.expand();
  assert.equal(h.dialogs.length, 0);
  assert.equal(h.calls.length, 0);
  assert.match(h.toasts.at(-1)[0], /valid ground tile/);
});
