'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {planMove, planClear} = require('../web/tile-move.js');

const attributes = [0, 1, 2, 3, 0, 1, 0x1000, 0x1001, 0x0010, 0x0110];
const clone = value => structuredClone(value);
const refs = id => Array.from({length: 256}, (_, pixel) => id * 256 + pixel);
function map(width, height, cells, name = 'Source') {
  return {name, width, height, cells: cells || Array(width * height).fill(1),
    layout: {primary_tileset: 'General', secondary_tileset: 'Petalburg'},
    tileset: {metatiles: attributes.map((attribute, id) => ({id, attribute, layer_type: attribute >> 12,
      behavior: attribute & 255, valid: true}))},
    map: {object_events: [{x: 0, y: 0, graphics_id: 'PERSON'}]}};
}
function selection(x, y, width, height, selected = () => true) {
  return {x, y, width, height, mask: Uint8Array.from({length: width * height}, (_, i) =>
    selected(i % width, Math.floor(i / width)) ? 1 : 0)};
}
function refAt(data, x, y) {
  const index = Math.floor(y / 16) * data.width + Math.floor(x / 16), pixel = y % 16 * 16 + x % 16;
  return data.pixel_patches?.[index]?.[pixel] ?? (data.cells[index] & 1023) * 256 + pixel;
}
function assertPixels(data, plan, expected) {
  const after = {...data, ...plan};
  for (let y = 0; y < data.height * 16; y++) for (let x = 0; x < data.width * 16; x++) {
    assert.equal(refAt(after, x, y), expected(x, y), `pixel ${x},${y}`);
  }
}

test('whole-cell move right snapshots overlapping cells and their movement bits', () => {
  const data = map(4, 1, [1 | 0x1400, 2 | 0x2800, 3 | 0x3c00, 4 | 0x4000]);
  const before = clone(data), result = planMove(data, data, selection(0, 0, 48, 16), {x: 16, y: 0}, 0);
  assert.deepEqual(result.source.cells, [0x1400, before.cells[0], before.cells[1], before.cells[2]]);
  assert.equal(result.source, result.target);
  assert.deepEqual(result.source.pixel_patches, {});
  assert.deepEqual(data, before);
  assert.deepEqual(Object.keys(result.source).sort(), ['cells', 'pixel_patches']);
});

test('whole-cell move left snapshots source before clearing or overwriting', () => {
  const data = map(4, 1, [1 | 0x1400, 2 | 0x2800, 3 | 0x3c00, 4 | 0x4000]);
  const before = clone(data), result = planMove(data, data, selection(16, 0, 48, 16), {x: 0, y: 0}, 0);
  assert.deepEqual(result.target.cells, [before.cells[1], before.cells[2], before.cells[3], 0x4000]);
  assert.deepEqual(data, before);
});

test('diagonal overlapping rectangle moves each full cell including collision and elevation', () => {
  const data = map(3, 3, Array.from({length: 9}, (_, i) => (i + 1) | ((i + 1) << 12)));
  const before = clone(data), result = planMove(data, data, selection(0, 0, 32, 32), {x: 16, y: 16}, 0);
  const expected = before.cells.slice();
  for (const index of [0, 1, 3, 4]) expected[index] &= 0xfc00;
  for (const [source, target] of [[0, 4], [1, 5], [3, 7], [4, 8]]) expected[target] = before.cells[source];
  assert.deepEqual(result.source.cells, expected);
  assert.deepEqual(data, before);
});

test('arbitrary triangle preserves pixels outside the mask, including source/destination overlap', () => {
  const data = map(2, 2, [1 | 0x1400, 2 | 0x2800, 3 | 0x3c00, 1 | 0x4000]);
  const before = clone(data), cut = selection(3, 4, 14, 13, (x, y) => x <= y);
  const destination = {x: 7, y: 6}, result = planMove(data, data, cut, destination, 0);
  const sourceSelected = (x, y) => x >= cut.x && y >= cut.y && x < cut.x + cut.width && y < cut.y + cut.height &&
    cut.mask[(y - cut.y) * cut.width + x - cut.x];
  assertPixels(data, result.source, (x, y) => {
    const sx = x - destination.x + cut.x, sy = y - destination.y + cut.y;
    if (sourceSelected(sx, sy)) return refAt(before, sx, sy);
    if (sourceSelected(x, y)) return y % 16 * 16 + x % 16;
    return refAt(before, x, y);
  });
  assert.deepEqual(result.source.cells, before.cells, 'partial edits retain cell attributes and movement');
  assert.deepEqual(data, before);
});

test('unaligned full-tile artwork becomes partial composites, retaining target movement and attributes', () => {
  const source = map(1, 1, [1 | 0x1400]);
  const target = map(2, 1, [2 | 0x2800, 3 | 0x3c00], 'Target');
  const sourceBefore = clone(source), targetBefore = clone(target);
  const result = planMove(source, target, selection(0, 0, 16, 16), {x: 8, y: 0}, 0);
  assert.deepEqual(result.source.cells, [0x1400]);
  assert.deepEqual(result.target.cells, targetBefore.cells);
  assertPixels(target, result.target, (x, y) => x >= 8 && x < 24 ? refAt(sourceBefore, x - 8, y) : refAt(targetBefore, x, y));
  assert.deepEqual(source, sourceBefore);
  assert.deepEqual(target, targetBefore);
});

test('cross-map full-cell moves copy complete raw cells and existing original-tile pixel references', () => {
  const source = map(2, 1, [1 | 0x1400, 2 | 0x2800]);
  source.pixel_patches = {0: refs(1)};
  source.pixel_patches[0][7] = 2 * 256 + 99;
  const target = map(2, 1, [3 | 0x3c00, 4 | 0x4000], 'Target');
  const a = clone(source), b = clone(target);
  const result = planMove(source, target, selection(0, 0, 16, 16), {x: 16, y: 0}, 0);
  assert.deepEqual(result.source.cells, [0x1400, a.cells[1]]);
  assert.deepEqual(result.source.pixel_patches, {});
  assert.equal(result.target.cells[1], a.cells[0]);
  assert.deepEqual(result.target.pixel_patches[1], a.pixel_patches[0]);
  result.target.pixel_patches[1][0] = 0;
  assert.deepEqual(source, a);
  assert.deepEqual(target, b);
});

test('whole-cell movement allows a different drawing layer while partial-layer mixing is rejected atomically', () => {
  const source = map(1, 1, [6 | 0x3400]), target = map(2, 1, [1 | 0x1800, 2 | 0x2800], 'Target');
  const a = clone(source), b = clone(target);
  const whole = planMove(source, target, selection(0, 0, 16, 16), {x: 0, y: 0}, 0);
  assert.equal(whole.target.cells[0], source.cells[0]);
  assert.throws(() => planMove(source, target, selection(0, 0, 16, 16), {x: 8, y: 0}, 0), /drawing layers/);
  assert.throws(() => planClear(source, selection(0, 0, 1, 1), 0), /drawing layers/);
  assert.deepEqual(source, a);
  assert.deepEqual(target, b);
});

test('patch normalization collapses only natural coordinates with the complete matching attribute', () => {
  const data = map(1, 1, [4 | 0x2800]);
  data.pixel_patches = {0: refs(0)};
  data.pixel_patches[0][0] = 4 * 256;
  const result = planClear(data, selection(0, 0, 1, 1), 0);
  assert.deepEqual(result, {cells: [0x2800], pixel_patches: {}});

  const differentAttribute = map(1, 1, [9 | 0x2800]);
  differentAttribute.pixel_patches = {0: refs(8)};
  differentAttribute.pixel_patches[0][0] = 9 * 256;
  const retained = planClear(differentAttribute, selection(0, 0, 1, 1), 8);
  assert.equal(retained.cells[0], differentAttribute.cells[0]);
  assert.deepEqual(retained.pixel_patches[0], refs(8), 'same behavior/layer alone cannot replace reserved attribute bits');

  const shiftedPixels = map(1, 1, [1]);
  shiftedPixels.pixel_patches = {0: refs(1).map(ref => 256 + (ref + 1) % 256)};
  assert.ok(planClear(shiftedPixels, selection(0, 0, 1, 1), 1).pixel_patches[0]);
});

test('missing raw attribute metadata conservatively retains a patch for a different original tile', () => {
  const data = map(1, 1, [4]);
  data.tileset.metatiles.forEach(meta => delete meta.attribute);
  data.pixel_patches = {0: refs(0)};
  data.pixel_patches[0][0] = 4 * 256;
  const result = planClear(data, selection(0, 0, 1, 1), 0);
  assert.equal(result.cells[0], 4);
  assert.deepEqual(result.pixel_patches[0], refs(0));
});

test('full interior cells are recognized even when the selection bounding box is not tile aligned', () => {
  const source = map(3, 1, [1 | 0x1400, 2 | 0x2800, 3 | 0x3c00]);
  const target = map(4, 1, [4 | 0x4000, 4 | 0x4000, 4 | 0x4000, 4 | 0x4000], 'Target');
  const result = planMove(source, target, selection(3, 0, 29, 16), {x: 19, y: 0}, 0);
  assert.equal(result.target.cells[2], source.cells[1]);
  assert.equal(result.source.cells[1], 0x2800);
  assert.equal(result.target.cells[1], target.cells[1]);
  assert.ok(result.target.pixel_patches[1]);
});

test('same-name equal snapshots share one atomic plan and mismatched snapshots are rejected', () => {
  const source = map(2, 1, [1, 2]), duplicate = clone(source);
  const result = planMove(source, duplicate, selection(0, 0, 16, 16), {x: 16, y: 0}, 0);
  assert.equal(result.source, result.target);
  assert.deepEqual(result.source.cells, [0, 1]);
  duplicate.cells[1] = 3;
  assert.throws(() => planMove(source, duplicate, selection(0, 0, 16, 16), {x: 16, y: 0}, 0), /versions.*disagree/);
});

test('returning a cut to its origin is an exact no-op, including existing patch representation', () => {
  const data = map(1, 1, [4 | 0x1400]);
  data.pixel_patches = {0: refs(0)};
  const before = clone(data), cut = selection(2, 3, 10, 8, (x, y) => x > y);
  const result = planMove(data, data, cut, {x: 2, y: 3}, 6);
  assert.deepEqual(result.source, {cells: before.cells, pixel_patches: before.pixel_patches});
  assert.deepEqual(data, before);
});

test('empty, invalid and out-of-bounds selections or destinations fail without modifying inputs', () => {
  const data = map(2, 2), before = clone(data), box = selection(0, 0, 16, 16);
  for (const [cut, destination] of [
    [selection(0, 0, 1, 1, () => false), {x: 0, y: 0}],
    [{...box, mask: new Uint8Array(1)}, {x: 0, y: 0}],
    [selection(-1, 0, 16, 16), {x: 0, y: 0}],
    [selection(20, 0, 16, 16), {x: 0, y: 0}],
    [box, {x: 17, y: 0}], [box, {x: 0, y: -1}], [box, {x: 0.5, y: 0}],
  ]) assert.throws(() => planMove(data, data, cut, destination, 0));
  assert.throws(() => planClear(data, box, 999), /not available/);
  assert.deepEqual(data, before);
});

test('different artwork pairs and unavailable pixel-reference tiles are rejected', () => {
  const source = map(1, 1), target = map(1, 1, [2], 'Target');
  target.layout.secondary_tileset = 'Mauville';
  assert.throws(() => planMove(source, target, selection(0, 0, 16, 16), {x: 0, y: 0}, 0), /same pair/);
  const invalid = map(1, 1);
  invalid.pixel_patches = {0: refs(1)};
  invalid.pixel_patches[0][0] = 999 * 256;
  const before = clone(invalid);
  assert.throws(() => planClear(invalid, selection(0, 0, 1, 1), 0), /not available/);
  assert.deepEqual(invalid, before);
});

test('clearing an arbitrary mask uses source-local eraser pixels and keeps events untouched', () => {
  const data = map(2, 1, [1 | 0x1400, 2 | 0x2800]), before = clone(data);
  const cut = selection(9, 3, 16, 10, (x, y) => (x + y) % 3 === 0);
  const result = planClear(data, cut, 3);
  assertPixels(data, result, (x, y) => {
    const selected = x >= cut.x && x < cut.x + cut.width && y >= cut.y && y < cut.y + cut.height &&
      cut.mask[(y - cut.y) * cut.width + x - cut.x];
    return selected ? 3 * 256 + y % 16 * 16 + x % 16 : refAt(before, x, y);
  });
  assert.deepEqual(result.cells, data.cells);
  assert.deepEqual(data, before);
  assert.equal('map' in result, false);
});
