/* Lossless terrain cut/move plans. Pixel references always address original tiles. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TileMove = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SIZE = 16, PIXELS = 256, TILE_MASK = 1023, MOVEMENT_MASK = 0xfc00;

  function integer(value, min, max, label) {
    if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${label} is invalid.`);
    return value;
  }

  function tileMeta(context, id) {
    const meta = context.metadata.get(id);
    if (!meta || meta.valid === false || !Number.isInteger(meta.layer_type)) {
      throw new Error(`Tile ${id} is not available in this map's artwork.`);
    }
    return meta;
  }

  function describe(data) {
    if (!data || typeof data !== 'object') throw new Error('Choose an editable map.');
    const width = integer(data.width, 1, 255, 'Map width');
    const height = integer(data.height, 1, 255, 'Map height');
    if (!data.cells || data.cells.length !== width * height) throw new Error('Map tile data has the wrong size.');
    for (const cell of data.cells) integer(cell, 0, 65535, 'Map tile');
    const primary = data.tileset?.primary || data.layout?.primary_tileset;
    const secondary = data.tileset?.secondary || data.layout?.secondary_tileset;
    if (typeof primary !== 'string' || !primary || typeof secondary !== 'string' || !secondary) {
      throw new Error('Map artwork information is missing.');
    }
    const metadata = new Map();
    for (const meta of data.tileset?.metatiles || []) {
      if (Number.isInteger(meta.id) && meta.id >= 0 && meta.id <= TILE_MASK) metadata.set(meta.id, meta);
    }
    const context = {data, width, height, primary, secondary, metadata};
    const patches = data.pixel_patches || {};
    if (typeof patches !== 'object' || Array.isArray(patches)) throw new Error('Pixel edits must be indexed by map tile.');
    for (const [key, pixels] of Object.entries(patches)) {
      const index = Number(key);
      if (String(index) !== key) throw new Error('Pixel edit tile index is invalid.');
      integer(index, 0, data.cells.length - 1, 'Pixel edit tile index');
      if (!pixels || pixels.length !== PIXELS) throw new Error('A pixel edit must contain all 256 pixel references.');
      const base = tileMeta(context, data.cells[index] & TILE_MASK);
      for (const ref of pixels) {
        integer(ref, 0, 1024 * PIXELS - 1, 'Pixel reference');
        if (tileMeta(context, Math.floor(ref / PIXELS)).layer_type !== base.layer_type) {
          throw new Error('Pixel edits cannot mix tiles with different drawing layers.');
        }
      }
    }
    return context;
  }

  function selectionFor(context, selection) {
    if (!selection || typeof selection !== 'object') throw new Error('Select some terrain first.');
    const {x, y, width, height, mask} = selection;
    integer(x, 0, context.width * SIZE - 1, 'Selection X');
    integer(y, 0, context.height * SIZE - 1, 'Selection Y');
    integer(width, 1, context.width * SIZE, 'Selection width');
    integer(height, 1, context.height * SIZE, 'Selection height');
    if (x + width > context.width * SIZE || y + height > context.height * SIZE) {
      throw new Error('The selection must stay inside its source map.');
    }
    if (!(mask instanceof Uint8Array) || mask.length !== width * height) {
      throw new Error('The selection mask has the wrong size.');
    }
    if (!mask.some(Boolean)) throw new Error('Select at least one terrain pixel.');
    return {x, y, width, height, mask};
  }

  function copyPlan(context) {
    const pixel_patches = {};
    for (const [index, pixels] of Object.entries(context.data.pixel_patches || {})) {
      pixel_patches[index] = Array.from(pixels);
    }
    return {cells: Array.from(context.data.cells), pixel_patches};
  }

  function naturalPixels(id) {
    return Array.from({length: PIXELS}, (_, pixel) => id * PIXELS + pixel);
  }

  function cellAt(context, x, y) {
    return Math.floor(y / SIZE) * context.width + Math.floor(x / SIZE);
  }

  function pixelAt(x, y) {
    return (y % SIZE) * SIZE + x % SIZE;
  }

  function pixelsFor(plan, index) {
    return plan.pixel_patches[index] || (plan.pixel_patches[index] = naturalPixels(plan.cells[index] & TILE_MASK));
  }

  function capture(context, selection) {
    const refs = new Uint32Array(selection.width * selection.height), counts = new Map();
    for (let i = 0; i < selection.mask.length; i++) {
      if (!selection.mask[i]) continue;
      const x = selection.x + i % selection.width;
      const y = selection.y + Math.floor(i / selection.width);
      const index = cellAt(context, x, y), pixel = pixelAt(x, y);
      const id = context.data.cells[index] & TILE_MASK;
      tileMeta(context, id);
      refs[i] = context.data.pixel_patches?.[index]?.[pixel] ?? id * PIXELS + pixel;
      counts.set(index, (counts.get(index) || 0) + 1);
    }
    return {refs, counts};
  }

  function clearInto(context, plan, selection, counts, eraserId) {
    const touched = new Set(counts.keys());
    for (const [index, count] of counts) {
      if (count !== PIXELS) continue;
      plan.cells[index] = (plan.cells[index] & MOVEMENT_MASK) | eraserId;
      delete plan.pixel_patches[index];
    }
    for (let i = 0; i < selection.mask.length; i++) {
      if (!selection.mask[i]) continue;
      const x = selection.x + i % selection.width;
      const y = selection.y + Math.floor(i / selection.width);
      const index = cellAt(context, x, y);
      if (counts.get(index) !== PIXELS) pixelsFor(plan, index)[pixelAt(x, y)] = eraserId * PIXELS + pixelAt(x, y);
    }
    return touched;
  }

  function normalize(context, plan, touched) {
    for (const index of touched) {
      const baseId = plan.cells[index] & TILE_MASK, base = tileMeta(context, baseId);
      const pixels = plan.pixel_patches[index];
      if (!pixels) continue;
      const id = Math.floor(pixels[0] / PIXELS);
      const natural = pixels.every((ref, pixel) => ref === id * PIXELS + pixel);
      const candidate = tileMeta(context, id);
      const sameAttribute = id === baseId ||
        (Number.isInteger(base.attribute) && Number.isInteger(candidate.attribute) && base.attribute === candidate.attribute);
      if (natural && sameAttribute) {
        plan.cells[index] = (plan.cells[index] & MOVEMENT_MASK) | id;
        delete plan.pixel_patches[index];
        continue;
      }
      for (const ref of pixels) {
        if (tileMeta(context, Math.floor(ref / PIXELS)).layer_type !== base.layer_type) {
          throw new Error('This cut would mix different drawing layers inside one tile. Move complete tiles or use matching artwork.');
        }
      }
    }
  }

  function sameSnapshot(source, target) {
    if (source.width !== target.width || source.height !== target.height ||
        source.cells.length !== target.cells.length || source.cells.some((value, index) => value !== target.cells[index])) return false;
    if (source.revision !== undefined && target.revision !== undefined && source.revision !== target.revision) return false;
    const a = source.pixel_patches || {}, b = target.pixel_patches || {}, keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(key =>
      b[key] && a[key].length === b[key].length && Array.from(a[key]).every((value, index) => value === b[key][index]));
  }

  // selection/destination coordinates are pixels; map dimensions are metatiles.
  // Inputs and events are untouched. A same-map move returns one shared plan.
  function planMove(sourceData, targetData, selection, destination, eraserId) {
    const source = describe(sourceData), target = sourceData === targetData ? source : describe(targetData);
    const selected = selectionFor(source, selection);
    integer(eraserId, 0, TILE_MASK, 'Eraser tile');
    tileMeta(source, eraserId);
    if (source.primary !== target.primary || source.secondary !== target.secondary) {
      throw new Error('Move this piece within maps that use the same pair of tilesets.');
    }
    if (!destination || typeof destination !== 'object') throw new Error('Choose a destination inside a map.');
    integer(destination.x, 0, target.width * SIZE - 1, 'Destination X');
    integer(destination.y, 0, target.height * SIZE - 1, 'Destination Y');
    if (destination.x + selected.width > target.width * SIZE || destination.y + selected.height > target.height * SIZE) {
      throw new Error('The whole selected piece must fit inside the destination map.');
    }
    const sameMap = sourceData === targetData ||
      (typeof sourceData.name === 'string' && sourceData.name.length > 0 && sourceData.name === targetData.name);
    if (sameMap && !sameSnapshot(sourceData, targetData)) throw new Error('The source and destination versions of this map disagree. Reload before moving terrain.');
    const captured = capture(source, selected);
    const sourcePlan = copyPlan(source), targetPlan = sameMap ? sourcePlan : copyPlan(target);
    const dx = destination.x - selected.x, dy = destination.y - selected.y;
    if (sameMap && dx === 0 && dy === 0) return {source: sourcePlan, target: sourcePlan};
    const sourceTouched = clearInto(source, sourcePlan, selected, captured.counts, eraserId);
    const targetTouched = sameMap ? sourceTouched : new Set();
    const aligned = dx % SIZE === 0 && dy % SIZE === 0;
    const fullCells = new Set();
    if (aligned) {
      for (const [index, count] of captured.counts) {
        if (count !== PIXELS) continue;
        fullCells.add(index);
        const x = index % source.width * SIZE + dx, y = Math.floor(index / source.width) * SIZE + dy;
        const targetIndex = cellAt(target, x, y);
        targetPlan.cells[targetIndex] = sourceData.cells[index];
        if (sourceData.pixel_patches?.[index]) targetPlan.pixel_patches[targetIndex] = Array.from(sourceData.pixel_patches[index]);
        else delete targetPlan.pixel_patches[targetIndex];
        targetTouched.add(targetIndex);
      }
    }
    for (let i = 0; i < selected.mask.length; i++) {
      if (!selected.mask[i]) continue;
      const localX = i % selected.width, localY = Math.floor(i / selected.width);
      const sourceIndex = cellAt(source, selected.x + localX, selected.y + localY);
      if (fullCells.has(sourceIndex)) continue;
      const x = destination.x + localX, y = destination.y + localY, index = cellAt(target, x, y);
      pixelsFor(targetPlan, index)[pixelAt(x, y)] = captured.refs[i];
      targetTouched.add(index);
    }
    normalize(source, sourcePlan, sourceTouched);
    if (!sameMap) normalize(target, targetPlan, targetTouched);
    return {source: sourcePlan, target: targetPlan};
  }

  // Delete a selected piece. The return value is one {cells, pixel_patches} plan.
  function planClear(data, selection, eraserId) {
    const context = describe(data), selected = selectionFor(context, selection);
    integer(eraserId, 0, TILE_MASK, 'Eraser tile');
    tileMeta(context, eraserId);
    const captured = capture(context, selected), plan = copyPlan(context);
    const touched = clearInto(context, plan, selected, captured.counts, eraserId);
    normalize(context, plan, touched);
    return plan;
  }

  return {planMove, planClear};
});
