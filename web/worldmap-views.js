/* Exact local map neighborhoods. This never changes or projects source cells. */
(function (root, factory) {
  'use strict';
  const views = factory();
  if (typeof module === 'object' && module.exports) module.exports = views;
  else root.WorldMapViews = views;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const directions = new Set(['up', 'down', 'left', 'right']);

  function validSize(row) {
    return row && Number.isInteger(row.width) && row.width > 0 &&
      Number.isInteger(row.height) && row.height > 0;
  }

  function bounds(rows) {
    if (!rows.length) return {x: 0, y: 0, width: 0, height: 0};
    const x = Math.min(...rows.map(row => row.x));
    const y = Math.min(...rows.map(row => row.y));
    const right = Math.max(...rows.map(row => row.x + row.width));
    const bottom = Math.max(...rows.map(row => row.y + row.height));
    return {x, y, width: right - x, height: bottom - y};
  }

  function delta(source, target, connection) {
    switch (connection.direction) {
      case 'up': return {x: connection.offset, y: -target.height};
      case 'down': return {x: connection.offset, y: source.height};
      case 'left': return {x: -target.width, y: connection.offset};
      case 'right': return {x: source.width, y: connection.offset};
    }
  }

  function build(catalog, anchorName, anchorPosition) {
    const sourceRows = (catalog?.maps || []).filter(validSize);
    const sourceByName = new Map(sourceRows.map(row => [row.name, row]));
    const sourceById = new Map(sourceRows.filter(row => row.id).map(row => [row.id, row]));
    const sourceAnchor = sourceByName.get(anchorName);
    if (!sourceAnchor) return {maps: [], bounds: bounds([]), anchor: null, conflicts: [], overlaps: []};

    function targetFor(connection) {
      if (!connection || !directions.has(connection.direction) || !Number.isInteger(connection.offset)) return null;
      return sourceByName.get(connection.name) || sourceById.get(connection.map) || null;
    }

    const maps = [], placed = new Map();
    function add(source, position) {
      if (placed.has(source.name)) return;
      const row = {...source, x: position.x, y: position.y};
      if (Array.isArray(source.connections)) row.connections = source.connections.map(connection => ({...connection}));
      maps.push(row);
      placed.set(row.name, row);
    }
    const position = anchorPosition && Number.isFinite(anchorPosition.x) && Number.isFinite(anchorPosition.y)
      ? anchorPosition
      : {x: Number.isFinite(sourceAnchor.x) ? sourceAnchor.x : 0, y: Number.isFinite(sourceAnchor.y) ? sourceAnchor.y : 0};
    add(sourceAnchor, position);
    const anchor = placed.get(anchorName);

    // The anchor's own records determine its neighborhood first. An incoming
    // one-way link still belongs here, but cannot override an outgoing record.
    for (const connection of sourceAnchor.connections || []) {
      const target = targetFor(connection);
      if (!target || target.name === anchorName) continue;
      const change = delta(sourceAnchor, target, connection);
      add(target, {x: anchor.x + change.x, y: anchor.y + change.y});
    }
    for (const source of sourceRows) {
      if (source.name === anchorName) continue;
      for (const connection of source.connections || []) {
        if (targetFor(connection)?.name !== anchorName) continue;
        const change = delta(source, sourceAnchor, connection);
        add(source, {x: anchor.x - change.x, y: anchor.y - change.y});
      }
    }

    // Some neighbors may also connect to each other. Report contradictions
    // honestly rather than recursively moving maps or claiming a global fit.
    const conflicts = [], checked = new Set();
    for (const source of maps) {
      for (const connection of source.connections || []) {
        const target = targetFor(connection);
        const actual = target && placed.get(target.name);
        if (!actual) continue;
        const change = delta(source, target, connection);
        const forward = source.name < target.name;
        const key = JSON.stringify(forward
          ? [source.name, target.name, change.x, change.y]
          : [target.name, source.name, -change.x, -change.y]);
        if (checked.has(key)) continue;
        checked.add(key);
        const expected = {x: source.x + change.x, y: source.y + change.y};
        const mismatch = {x: actual.x - expected.x, y: actual.y - expected.y};
        if (mismatch.x || mismatch.y) {
          conflicts.push({a: source.name, b: target.name, direction: connection.direction, offset: connection.offset,
            expected, actual: {x: actual.x, y: actual.y}, delta: mismatch});
        }
      }
    }
    const overlaps = [];
    for (let i = 0; i < maps.length; i++) {
      const a = maps[i];
      for (let j = i + 1; j < maps.length; j++) {
        const b = maps[j];
        const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
        const width = Math.min(a.x + a.width, b.x + b.width) - x;
        const height = Math.min(a.y + a.height, b.y + b.height) - y;
        if (width > 0 && height > 0) overlaps.push({a: a.name, b: b.name, x, y, width, height});
      }
    }
    // Overview overlaps describe different placements. Selected-map details
    // must report only the full rectangles visible in this local neighborhood.
    for (const row of maps) row.overlaps = [];
    for (const overlap of overlaps) {
      placed.get(overlap.a).overlaps.push(overlap.b);
      placed.get(overlap.b).overlaps.push(overlap.a);
    }
    return {maps, bounds: bounds(maps), anchor: anchorName, conflicts, overlaps};
  }

  return {build, bounds};
});
