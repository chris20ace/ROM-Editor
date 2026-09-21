'use strict';

// Run the real save/cache functions against in-memory map records. These tests
// never start a server or read or write the game's source files.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../web/worldmap.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const names = ['buffer', 'dirtyBuffers', 'snapshot', 'fingerprint', 'atlasFor', 'ensureBuffer', 'saveWorld'];
const declarations = [...script.matchAll(/^  (?:async )?function (\w+)\(/gm)];
const functions = names.map(name => {
  const index = declarations.findIndex(match => match[1] === name);
  assert.notEqual(index, -1, `Missing actual worldmap function: ${name}`);
  return script.slice(declarations[index].index, declarations[index + 1]?.index ?? script.length);
}).join('\n');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function record(name, compiled = false) {
  return {
    name, width: 2, height: 1, revision: compiled ? `${name}-saved` : `${name}-opened`,
    cells: [0x7400 | (compiled ? 512 : 1), 0x1802], border: [0, 0, 0, 0],
    map: {id: `MAP_${name}`, object_events: [{x: 0, y: 0, script: 'KeepMe'}], connections: []},
    layout: {primary_tileset: 'gTileset_Primary', secondary_tileset: 'gTileset_Secondary'},
    tileset: {
      primary: 'gTileset_Primary', secondary: 'gTileset_Secondary',
      revision: compiled ? 'art-saved' : 'art-opened', atlas_url: '/atlas.png?map=A',
      metatiles: [...[0, 1, 2].map(id => ({id, valid: true})),
        ...(compiled ? [{id: 512, valid: true}] : []), {id: 511, valid: false}],
    },
    shared_with: [], events_shared_with: [],
    ...(!compiled ? {pixel_patches: {'0': Array.from({length: 256}, (_,pixel) => 256 + pixel)}} : {}),
  };
}

function environment() {
  const calls = {api: [], images: [], rasters: [], errors: [], toasts: [], clears: 0, statuses: []};
  const backend = new Map([['A', record('A', true)], ['B', record('B', true)]]);
  const S = {
    ready: true, busy: false, finishing: false, selected: 'A', generation: 7,
    buffers: new Map(), loads: new Map(), atlases: new Map(), erasers: new Map(),
    history: [new Map([['A', {cells: [1, 2]}]])], future: [new Map([['A', {cells: [2, 1]}]])],
    previews: new Map([['A', {old: true}], ['B', {old: true}]]),
    previewInFlight: new Set(['B']), previewFailed: new Set(['C']),
  };
  const behavior = {rejectSave: null, loadImage: null, getMap: null, save: null};
  const sandbox = {
    S, clone, Map, Set, Promise, console,
    eventKinds: ['object_events', 'warp_events', 'coord_events', 'bg_events'],
    finishStroke: async () => {},
    dialog: async () => ({shared: 'on'}), esc: String, nice: String,
    status: () => calls.statuses.push({busy: S.busy, dirty: [...S.buffers.values()].filter(b => b.dirty).length}),
    error: message => calls.errors.push(message),
    toast: (message, bad = false) => calls.toasts.push({message, bad}),
    regionEditing: {clear: () => {calls.clears++;}},
    resetNativePreviews: () => {},
    renderPlaces: () => {}, renderEvents: () => {}, render: () => {},
    scheduleNearbyBuffers: () => {}, preload: () => {},
    loadImage: async url => {
      calls.images.push(url);
      if (behavior.loadImage) return behavior.loadImage(url);
      return {revision: new URL(url, 'http://test').searchParams.get('revision')};
    },
    api: async (url, body) => {
      calls.api.push({url, body: body === undefined ? undefined : clone(body)});
      if (url === '/api/worldmap/save') {
        if (behavior.rejectSave) throw new Error(behavior.rejectSave);
        if (behavior.save) return behavior.save(body);
        return {saved: body.maps.map(item => clone(backend.get(item.name)))};
      }
      const name = new URL(url, 'http://test').searchParams.get('name');
      if (behavior.getMap) return behavior.getMap(name);
      assert.ok(backend.has(name));
      return clone(backend.get(name));
    },
    rasterize: b => {
      // A stale atlas or old validity set must never render a compiled cell.
      assert.equal(b.atlas.revision, b.data.tileset.revision);
      for (const cell of b.data.cells) assert.ok(b.valid.has(cell & 1023));
      calls.rasters.push({name: b.data.name, cells: [...b.data.cells], revision: b.atlas.revision});
      b.raster = {revision: b.atlas.revision};
    },
    selectMap: async name => {
      // Match selectMap's error handling while leaving unrelated DOM work out.
      S.selected = name;
      try {
        const b = await sandbox.ensureBuffer(name);
        if (!b.raster) sandbox.rasterize(b);
      } catch (error) {
        calls.errors.push(error.message);
        calls.toasts.push({message: error.message, bad: true});
      }
    },
  };
  vm.runInNewContext(functions, sandbox, {filename: 'worldmap.js save functions'});
  for (const name of ['A', 'B']) {
    const data = record(name);
    const b = {data, atlas: {revision: 'art-opened'}, valid: new Set([0, 1, 2]), raster: {old: true},
      dirty: name === 'A', saved: 'previous saved state'};
    if (name === 'B') {delete data.pixel_patches;b.saved = sandbox.fingerprint(b);}
    S.buffers.set(name, b);
  }
  return {S, calls, backend, behavior, ...Object.fromEntries(names.map(name => [name, sandbox[name]]))};
}

test('save sends pixel references, full map words, and the opened artwork revision', async () => {
  const e = environment(), before = clone(e.buffer('A').data);
  await e.saveWorld();
  const request = e.calls.api.find(call => call.url === '/api/worldmap/save');
  assert.equal(request.body.maps.length, 1);
  const saved = request.body.maps[0];
  assert.equal(saved.name, 'A');
  assert.equal(saved.revision, before.revision);
  assert.equal(saved.tileset_revision, before.tileset.revision);
  for (const key of ['cells', 'pixel_patches', 'border', 'map', 'layout']) assert.deepEqual(saved[key], before[key]);
  assert.equal(saved.cells[0] & 0xfc00, 0x7400);
});

test('validation rejection retains pending pixels, buffers, and both Undo stacks', async () => {
  const e = environment(), original = e.buffer('A'), before = clone(original.data);
  const history = e.S.history, future = e.S.future, saved = original.saved;
  e.behavior.rejectSave = 'This cut splits an animated tile.';
  await e.saveWorld();
  assert.equal(e.buffer('A'), original);
  assert.deepEqual(original.data, before);
  assert.equal(original.saved, saved);
  assert.equal(original.dirty, true);
  assert.equal(e.S.history, history);
  assert.equal(e.S.future, future);
  assert.equal(e.S.generation, 7);
  assert.equal(e.S.busy, false);
  assert.equal(e.calls.clears, 0);
  assert.equal(e.calls.images.length, 0);
  assert.ok(e.calls.toasts.some(item => item.bad && /animated/.test(item.message)));
});

test('successful compile rebuilds buffers with new IDs, artwork, validity and saved fingerprints', async () => {
  const e = environment(), old = e.buffer('A');
  await e.saveWorld();
  const current = e.buffer('A');
  assert.notEqual(current, old);
  assert.equal(current.data.cells[0] & 1023, 512);
  assert.equal(current.data.cells[0] & 0xfc00, old.data.cells[0] & 0xfc00);
  assert.equal(current.atlas.revision, 'art-saved');
  assert.equal(current.raster.revision, 'art-saved');
  assert.equal(current.valid.has(512), true);
  assert.equal(current.valid.has(511), false);
  assert.equal(current.data.pixel_patches, undefined);
  assert.equal(current.saved, e.fingerprint(current));
  assert.equal(current.dirty, false);
  assert.equal(e.buffer('B'), undefined, 'clean buffers sharing older artwork are invalidated');
  assert.equal(e.S.history.length, 0);
  assert.equal(e.S.future.length, 0);
  assert.equal(e.S.generation, 8);
  assert.equal(e.S.busy, false);
  assert.ok(e.calls.images.every(url => url.endsWith('&revision=art-saved')));
});

test('server success retires dirty state and Undo before the new atlas finishes loading', async () => {
  const e = environment(), started = deferred(), image = deferred(), old = e.buffer('A');
  e.behavior.loadImage = () => {started.resolve();return image.promise;};
  const saving = e.saveWorld();
  await started.promise;
  assert.equal(e.S.busy, true);
  assert.equal(old.dirty, false);
  assert.equal(e.dirtyBuffers().length, 0);
  assert.equal(e.S.buffers.size, 0, 'no compiled IDs can be paired with the previous atlas');
  assert.equal(e.S.history.length, 0);
  assert.equal(e.S.future.length, 0);
  assert.equal(e.S.generation, 8);
  image.resolve({revision: 'art-saved'});
  await saving;
  assert.equal(e.buffer('A').valid.has(512), true);
});

test('post-commit atlas failure leaves retriable absent buffers, not stale IDs or old Undo', async () => {
  const e = environment();
  e.behavior.loadImage = () => {throw new Error('Artwork unavailable');};
  await e.saveWorld();
  assert.equal(e.S.buffers.size, 0);
  assert.equal(e.dirtyBuffers().length, 0);
  assert.equal(e.S.history.length, 0);
  assert.equal(e.S.future.length, 0);
  assert.equal(e.S.atlases.size, 0, 'rejected atlas promises must not poison retries');
  assert.equal(e.S.loads.size, 0);
  assert.equal(e.S.busy, false);
  assert.equal(e.calls.rasters.length, 0);
  assert.ok(e.calls.errors.some(message => /edits were saved/.test(message)));
  assert.equal(e.calls.api.filter(call => call.url === '/api/worldmap/save').length, 1);

  e.behavior.loadImage = null;
  const retried = await e.ensureBuffer('A');
  assert.equal(retried.data.revision, 'A-saved');
  assert.equal(retried.data.cells[0] & 1023, 512);
  assert.equal(retried.atlas.revision, 'art-saved');
  assert.equal(retried.valid.has(512), true);
  assert.equal(retried.dirty, false);
  assert.equal(e.calls.api.filter(call => call.url === '/api/worldmap/save').length, 1,
    'artwork recovery must not submit the cut twice');
});

test('atlas promises deduplicate loads, evict failures, and separate artwork revisions', async () => {
  const e = environment(), pending = deferred(), old = record('A');
  e.behavior.loadImage = () => pending.promise;
  const first = e.atlasFor(old), second = e.atlasFor(old);
  assert.equal(first, second);
  assert.equal(e.calls.images.length, 1);
  const rejected = assert.rejects(first, /temporary failure/);
  pending.reject(new Error('temporary failure'));
  await rejected;
  assert.equal(e.S.atlases.size, 0);
  e.behavior.loadImage = null;
  const retry = await e.atlasFor(old), fresh = await e.atlasFor(record('A', true));
  assert.equal(retry.revision, 'art-opened');
  assert.equal(fresh.revision, 'art-saved');
  assert.notEqual(retry, fresh);
  assert.equal(e.calls.images.length, 3);
  assert.equal(e.S.atlases.size, 2);
});

test('a pre-save background load cannot repopulate an old buffer after compilation', async () => {
  const e = environment(), pending = deferred(), old = record('B');
  delete old.pixel_patches;
  e.S.buffers.delete('B');
  e.behavior.getMap = name => name === 'B' ? pending.promise : clone(e.backend.get(name));
  const loading = e.ensureBuffer('B');
  assert.equal(e.S.loads.has('B'), true);
  await e.saveWorld();
  assert.equal(e.S.loads.has('B'), false);
  pending.resolve(old);
  const abandoned = await loading;
  assert.equal(abandoned.data.tileset.revision, 'art-opened');
  assert.equal(e.buffer('B'), undefined);
  assert.equal(e.buffer('A').data.tileset.revision, 'art-saved');
  assert.equal(e.S.loads.size, 0);
});
