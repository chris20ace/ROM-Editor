'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const views = require('../web/worldmap-views.js');

function row(name, width, height, x = 0, y = 0, connections = []) {
  return {name, id: `MAP_${name}`, width, height, x, y, connections};
}
function connection(name, direction, offset) { return {map: `MAP_${name}`, name, direction, offset}; }
function byName(view, name) { return view.maps.find(map => map.name === name); }
function delta(source, target, direction, offset) {
  return {up: [offset, -target.height], down: [offset, source.height], left: [-target.width, offset], right: [source.width, offset]}[direction];
}
function exactLink(view, from, to, direction, offset) {
  const a = byName(view, from), b = byName(view, to), [dx, dy] = delta(a, b, direction, offset);
  assert.equal(b.x, a.x + dx);
  assert.equal(b.y, a.y + dy);
}
function frozen(value) {
  Object.freeze(value);
  for (const child of Object.values(value)) if (child && typeof child === 'object' && !Object.isFrozen(child)) frozen(child);
  return value;
}

const catalog = frozen({maps: [
  row('Route103', 80, 22, 120, 218, [connection('OldaleTown', 'down', 0), connection('Route110', 'right', -60)]),
  row('OldaleTown', 20, 20, 120, 240, [connection('Route103', 'up', 0), connection('Route102', 'left', 0)]),
  row('Route110', 40, 100, 200, 160, [connection('Route103', 'left', 60), connection('MauvilleCity', 'up', 0)]),
  row('Route102', 50, 20, 70, 240),
  row('MauvilleCity', 40, 20, 200, 140),
  row('Faraway', 8, 8, 900, 900)
]});
const original = JSON.stringify(catalog);
const routeView = views.build(catalog, 'Route103');
assert.deepEqual(routeView.maps.map(map => map.name), ['Route103', 'OldaleTown', 'Route110']);
assert.equal(routeView.anchor, 'Route103');
exactLink(routeView, 'Route103', 'Route110', 'right', -60);
exactLink(routeView, 'Route103', 'OldaleTown', 'down', 0);
assert.deepEqual(routeView.conflicts, []);
assert.deepEqual(routeView.overlaps, []);
assert.deepEqual(routeView.bounds, {x: 120, y: 158, width: 120, height: 102});
assert.equal(JSON.stringify(catalog), original);
for (const displayed of routeView.maps) {
  const source = catalog.maps.find(map => map.name === displayed.name);
  assert.notEqual(displayed, source);
  assert.notEqual(displayed.connections, source.connections);
  assert.equal(displayed.width, source.width);
  assert.equal(displayed.height, source.height);
  assert.deepEqual(displayed.connections, source.connections);
}

// Walking into a visible neighbor keeps that neighbor's current location, then
// builds only its exact immediate connections. It does not accumulate a world.
const eastPosition = byName(routeView, 'Route110');
const eastView = views.build(catalog, 'Route110', eastPosition);
assert.deepEqual({x: byName(eastView, 'Route110').x, y: byName(eastView, 'Route110').y}, {x: eastPosition.x, y: eastPosition.y});
exactLink(eastView, 'Route110', 'Route103', 'left', 60);
exactLink(eastView, 'Route110', 'MauvilleCity', 'up', 0);
assert.equal(byName(eastView, 'Route103').x, byName(routeView, 'Route103').x);
assert.equal(byName(eastView, 'Route103').y, byName(routeView, 'Route103').y);
assert.ok(!byName(eastView, 'OldaleTown'));
assert.deepEqual(eastView.conflicts, []);

// Incoming one-way cardinal records are real neighbors too; dive/warp records,
// unresolved names and invalid offsets never invent a placement.
const incoming = frozen({maps: [
  row('Anchor', 20, 30, -10, 40, [connection('East', 'right', 4), connection('Unknown', 'up', 0), connection('Dive', 'dive', 0), connection('Bad', 'down', '2')]),
  row('East', 8, 12, 99, 99, [connection('Anchor', 'left', -4)]),
  row('North', 9, 6, 99, 99, [connection('Anchor', 'down', -3)]),
  row('South', 14, 17, 99, 99, [connection('Anchor', 'up', 2)]),
  row('West', 11, 8, 99, 99, [connection('Anchor', 'right', -5)]),
  row('Dive', 12, 12), row('Bad', 12, 12), row('Invalid', 0, 10, 0, 0, [connection('Anchor', 'up', 0)])
]});
const incomingView = views.build(incoming, 'Anchor');
assert.deepEqual(incomingView.maps.map(map => map.name), ['Anchor', 'East', 'North', 'South', 'West']);
exactLink(incomingView, 'Anchor', 'East', 'right', 4);
exactLink(incomingView, 'North', 'Anchor', 'down', -3);
exactLink(incomingView, 'South', 'Anchor', 'up', 2);
exactLink(incomingView, 'West', 'Anchor', 'right', -5);
assert.equal(incomingView.maps.filter(map => map.name === 'East').length, 1);
assert.deepEqual(incomingView.conflicts, []);

// With contradictory input the anchor's outgoing record takes priority and
// the incompatible reciprocal record is reported rather than silently used.
const inconsistent = {maps: [
  row('A', 20, 20, 0, 0, [connection('B', 'right', 0), connection('C', 'down', 10)]),
  row('B', 20, 20, 0, 0, [connection('A', 'left', 0), connection('C', 'down', 0)]),
  row('C', 20, 20, 0, 0, [connection('A', 'up', -10), connection('B', 'up', 0)])
]};
const cycleView = views.build(inconsistent, 'A');
exactLink(cycleView, 'A', 'B', 'right', 0);
exactLink(cycleView, 'A', 'C', 'down', 10);
assert.equal(cycleView.conflicts.length, 1);
assert.deepEqual(cycleView.conflicts[0], {a: 'B', b: 'C', direction: 'down', offset: 0, expected: {x: 20, y: 20}, actual: {x: 10, y: 20}, delta: {x: -10, y: 0}});
assert.deepEqual(cycleView.overlaps, []);
const badReverse = {maps: [row('A', 20, 20, 0, 0, [connection('B', 'right', 5)]), row('B', 20, 20, 0, 0, [connection('A', 'left', 7)])]};
const priorityView = views.build(badReverse, 'A');
exactLink(priorityView, 'A', 'B', 'right', 5);
assert.equal(priorityView.conflicts.length, 1);

// Multiple neighbors on the same edge can overlap; retain full rectangles and
// expose the overlap without cropping or changing source map dimensions.
const overlapping = {maps: [
  row('A', 20, 20, 0, 0, [connection('B', 'up', 0), connection('C', 'up', 10)]),
  row('B', 20, 20), row('C', 20, 20)
]};
const overlapView = views.build(overlapping, 'A');
assert.deepEqual(overlapView.overlaps, [{a: 'B', b: 'C', x: 10, y: -20, width: 10, height: 20}]);
assert.deepEqual(overlapView.conflicts, []);
assert.deepEqual(byName(overlapView, 'A').overlaps, []);
assert.deepEqual(byName(overlapView, 'B').overlaps, ['C']);
assert.deepEqual(byName(overlapView, 'C').overlaps, ['B']);
const staleOverview = frozen({maps: [
  {...row('A', 20, 20, 0, 0, [connection('B', 'right', 0)]), overlaps: ['B', 'Distant']},
  {...row('B', 20, 20, 5, 0), overlaps: ['A']}
]});
const cleanLocalView = views.build(staleOverview, 'A');
assert.deepEqual(cleanLocalView.overlaps, []);
assert.deepEqual(byName(cleanLocalView, 'A').overlaps, []);
assert.deepEqual(byName(cleanLocalView, 'B').overlaps, []);
assert.deepEqual(staleOverview.maps[0].overlaps, ['B', 'Distant']);
assert.deepEqual(staleOverview.maps[1].overlaps, ['A']);
assert.deepEqual(views.bounds([]), {x: 0, y: 0, width: 0, height: 0});
assert.deepEqual(views.build(catalog, 'Missing'), {maps: [], bounds: {x: 0, y: 0, width: 0, height: 0}, anchor: null, conflicts: [], overlaps: []});
assert.deepEqual(views.build({maps: [row('Single', 7, 3, 5, 6)]}, 'Single').bounds, {x: 5, y: 6, width: 7, height: 3});
assert.deepEqual(views.build({maps: [row('A', 1, 1, 0, 0, [{map: 'MAP_B', direction: 'right', offset: 0}]), row('B', 1, 1)]}, 'A').maps.map(map => map.name), ['A', 'B']);

// Test every installed source map as anchor. Its local cardinal neighbors must
// match the original dimensions and all anchor links exactly; no claim is made
// that the complete game's graph fits a single flat global layout.
let sourceAnchors = 0;
const sourceDir = path.join(__dirname, '../source/pokeemerald/data');
if (fs.existsSync(path.join(sourceDir, 'layouts/layouts.json'))) {
  const layouts = new Map(JSON.parse(fs.readFileSync(path.join(sourceDir, 'layouts/layouts.json'), 'utf8')).layouts.map(layout => [layout.id, layout]));
  const raw = [];
  for (const name of fs.readdirSync(path.join(sourceDir, 'maps'))) {
    const file = path.join(sourceDir, 'maps', name, 'map.json');
    if (fs.existsSync(file)) raw.push(JSON.parse(fs.readFileSync(file, 'utf8')));
  }
  const names = new Map(raw.map(map => [map.id, map.name]));
  const all = frozen({maps: raw.map((map, index) => ({name: map.name, id: map.id, width: layouts.get(map.layout).width, height: layouts.get(map.layout).height, x: index * 3, y: index * 5,
    connections: (map.connections || []).filter(link => ['up', 'down', 'left', 'right'].includes(link.direction)).map(link => ({...link, name: names.get(link.map)}))}))});
  const unchanged = JSON.stringify(all);
  for (const anchor of all.maps) {
    const view = views.build(all, anchor.name);
    assert.equal(byName(view, anchor.name).x, anchor.x);
    assert.equal(byName(view, anchor.name).y, anchor.y);
    for (const link of anchor.connections) exactLink(view, anchor.name, link.name, link.direction, link.offset);
    for (const map of view.maps) {
      const originalMap = all.maps.find(source => source.name === map.name);
      assert.equal(map.width, originalMap.width);
      assert.equal(map.height, originalMap.height);
      assert.deepEqual(map.connections, originalMap.connections);
      assert.ok(!view.conflicts.some(conflict => conflict.a === anchor.name || conflict.b === anchor.name));
    }
    sourceAnchors++;
  }
  assert.equal(JSON.stringify(all), unchanged);
}

const browser = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/worldmap-views.js'), 'utf8'), browser);
assert.equal(typeof browser.WorldMapViews?.build, 'function');
assert.equal(typeof browser.WorldMapViews?.bounds, 'function');
console.log(`World map views passed: exact neighborhoods for ${sourceAnchors} source maps, full rectangles, walking continuity, incoming links, cycle/overlap diagnostics and immutable input.`);
