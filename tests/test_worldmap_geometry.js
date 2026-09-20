'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const geometry = require('../web/worldmap-geometry.js');

const route = {
  name: 'Route103', x: 120, y: 218, width: 80, height: 22,
  projection: {kind: 'vertical-shear', knots: [[0, 0], [28, 0], [40, 2], [80, 2]]}
};
const east = {name: 'Route110', x: 200, y: 160, width: 40, height: 100};
const south = {name: 'OldaleTown', x: 120, y: 240, width: 20, height: 20};
let checks = 0;
function near(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`);
  checks++;
}
function samePoint(actual, expected) {
  near(actual.x, expected.x);
  near(actual.y, expected.y);
}

// Every cell remains independently addressable and has the exact same source
// coordinates after display projection, including fractional brush positions.
for (let y = 0; y < route.height; y++) {
  for (let x = 0; x < route.width; x++) {
    for (const offset of [0, 0.07, 0.5, 0.91]) {
      const local = {x: x + offset, y: y + offset};
      const world = geometry.toWorld(route, local);
      samePoint(geometry.toLocal(route, world), local);
      assert.deepEqual(geometry.cell(route, world), {x, y});
      assert.ok(geometry.contains(route, world));
    }
  }
}
for (let y = 0; y <= route.height; y++) {
  for (let x = 0; x <= route.width; x++) {
    const local = {x, y};
    samePoint(geometry.toLocal(route, geometry.toWorld(route, local)), local);
    assert.deepEqual(geometry.cell(route, geometry.toWorld(route, local)), local);
  }
}

// Only the water transition moves gradually. Both banks stay rigid.
for (const [x, shift] of [[-5, 0], [0, 0], [28, 0], [31, 0.5], [34, 1], [40, 2], [80, 2], [100, 2]]) {
  near(geometry.shift(route, x), shift);
}
assert.deepEqual(geometry.segments(route), [
  {x: 0, width: 28, shift: 0, slope: 0},
  {x: 28, width: 12, shift: 0, slope: 1 / 6},
  {x: 40, width: 40, shift: 2, slope: 0}
]);

// Half-open boundaries choose one map at a shared edge and reject empty space
// above the sloped part of the map, even though it lies inside the AABB.
for (const x of [0, 28, 34, 40, 79.99]) {
  assert.ok(geometry.contains(route, geometry.toWorld(route, {x, y: 0})));
  assert.ok(!geometry.contains(route, geometry.toWorld(route, {x, y: -1e-7})));
  assert.ok(geometry.contains(route, geometry.toWorld(route, {x, y: 22 - 1e-7})));
  assert.ok(!geometry.contains(route, geometry.toWorld(route, {x, y: 22})));
}
assert.ok(!geometry.contains(route, geometry.toWorld(route, {x: -1e-7, y: 5})));
assert.ok(!geometry.contains(route, geometry.toWorld(route, {x: 80, y: 5})));
assert.ok(!geometry.contains(route, {x: 154, y: 218.5}));
assert.ok(geometry.contains(route, {x: 154, y: 219.5}));

// All points along both original travel connections meet: east offset -60 and
// south offset 0. No seam is transferred to Oldale when the east bank shifts.
for (let y = 0; y <= 22; y += 0.125) {
  const edge = geometry.toWorld(route, {x: 80, y});
  samePoint(edge, geometry.toWorld(east, {x: 0, y: y + 60}));
  assert.ok(!geometry.contains(route, edge));
  assert.ok(geometry.contains(east, edge));
}
for (let x = 0; x < 20; x += 0.125) {
  const edge = geometry.toWorld(route, {x, y: 22});
  samePoint(edge, geometry.toWorld(south, {x, y: 0}));
  assert.ok(!geometry.contains(route, edge));
  assert.ok(geometry.contains(south, edge));
}

assert.deepEqual(geometry.bounds(route), {x: 120, y: 218, width: 80, height: 24});
assert.deepEqual(geometry.polygon(route), [
  {x: 120, y: 218}, {x: 148, y: 218}, {x: 160, y: 220}, {x: 200, y: 220},
  {x: 200, y: 242}, {x: 160, y: 242}, {x: 148, y: 240}, {x: 120, y: 240}
]);
assert.deepEqual(geometry.polygon(route, {x: 27, y: 3, width: 14, height: 5}), [
  {x: 147, y: 221}, {x: 148, y: 221}, {x: 160, y: 223}, {x: 161, y: 223},
  {x: 161, y: 228}, {x: 160, y: 228}, {x: 148, y: 226}, {x: 147, y: 226}
]);
const negative = {...route, projection: {kind: 'vertical-shear', knots: [[0, 0], [40, -3], [80, 2]]}};
assert.deepEqual(geometry.bounds(negative), {x: 120, y: 215, width: 80, height: 27});
const clipped = {...route, projection: {kind: 'vertical-shear', knots: [[-10, -1], [20, 2], [100, 10]]}};
assert.deepEqual(geometry.segments(clipped), [
  {x: 0, width: 20, shift: 0, slope: 0.1}, {x: 20, width: 60, shift: 2, slope: 0.1}
]);

// Legacy/ordinary maps keep identical coordinates, bounds and one image draw.
// Use every shipped source map when available, so unusual sizes are covered.
const source = path.join(__dirname, '../source/pokeemerald/data');
let identityRows = [east, south, {x: -73, y: 99, width: 1, height: 1}];
if (fs.existsSync(path.join(source, 'layouts/layouts.json'))) {
  const layouts = new Map(JSON.parse(fs.readFileSync(path.join(source, 'layouts/layouts.json'), 'utf8')).layouts.map(row => [row.id, row]));
  for (const name of fs.readdirSync(path.join(source, 'maps'))) {
    const file = path.join(source, 'maps', name, 'map.json');
    if (!fs.existsSync(file)) continue;
    const map = JSON.parse(fs.readFileSync(file, 'utf8'));
    const layout = layouts.get(map.layout);
    if (layout) identityRows.push({name, x: 233, y: -47, width: layout.width, height: layout.height});
  }
}
for (const row of identityRows) {
  assert.deepEqual(geometry.bounds(row), {x: row.x, y: row.y, width: row.width, height: row.height});
  assert.deepEqual(geometry.segments(row), [{x: 0, width: row.width, shift: 0, slope: 0}]);
  for (const local of [{x: 0, y: 0}, {x: 0.5, y: 0.5}, {x: row.width - 0.5, y: row.height - 0.5}]) {
    assert.deepEqual(geometry.toWorld(row, local), {x: row.x + local.x, y: row.y + local.y});
    samePoint(geometry.toLocal(row, geometry.toWorld(row, local)), local);
    assert.ok(geometry.contains(row, geometry.toWorld(row, local)));
  }
}

// Long brush strokes are never truncated and select the same source cells on
// either side of the projected join. Sampling stays <= one quarter tile apart.
for (const [start, end] of [
  [{x: 149.2, y: 223.6}, {x: 170.4, y: 225.7}],
  [{x: -5, y: -7}, {x: 2050, y: 1900}],
  [{x: 5, y: 5}, {x: 5, y: 5}]
]) {
  const points = [];
  geometry.trace(start, end, point => points.push(point));
  assert.deepEqual(points[0], start);
  assert.deepEqual(points.at(-1), end);
  for (let i = 1; i < points.length; i++) {
    assert.ok(Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y) <= 0.250000001);
  }
}
const painted = new Set();
geometry.trace({x: 198.5, y: 226.5}, {x: 201.5, y: 226.5}, point => {
  const row = geometry.contains(route, point) ? route : geometry.contains(east, point) ? east : null;
  if (row) {
    const cell = geometry.cell(row, point);
    painted.add(`${row.name}:${cell.x},${cell.y}`);
  }
});
assert.deepEqual([...painted], ['Route103:78,6', 'Route103:79,6', 'Route110:0,66', 'Route110:1,66']);

// Browser loading exposes the same API without requiring a module loader.
const browser = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/worldmap-geometry.js'), 'utf8'), browser);
assert.equal(typeof browser.WorldGeometry?.toWorld, 'function');
assert.equal(browser.WorldGeometry.shift(route, 34), 1);
console.log(`World map geometry passed: ${checks} numeric checks, 1,760 source cells, ${identityRows.length} identity maps, continuous edges and brush tracing.`);
