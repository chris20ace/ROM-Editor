/* Display geometry only. Source tile and event coordinates never change. */
(function (root, factory) {
  'use strict';
  const geometry = factory();
  if (typeof module === 'object' && module.exports) module.exports = geometry;
  else root.WorldGeometry = geometry;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function knots(row) {
    return row.projection?.kind === 'vertical-shear' ? row.projection.knots : null;
  }

  function shift(row, x) {
    const points = knots(row);
    if (!points?.length) return 0;
    if (x <= points[0][0]) return points[0][1];
    for (let i = 1; i < points.length; i++) {
      const [right, end] = points[i];
      if (x <= right) {
        const [left, start] = points[i - 1];
        return start + (end - start) * (x - left) / (right - left);
      }
    }
    return points[points.length - 1][1];
  }

  function toWorld(row, point) {
    return {x: row.x + point.x, y: row.y + point.y + shift(row, point.x)};
  }

  function toLocal(row, point) {
    const x = point.x - row.x;
    return {x, y: point.y - row.y - shift(row, x)};
  }

  // Subtracting a large world origin can leave an exact tile edge a fraction
  // below an integer. Normalize that noise so outlines, clicks and painting
  // agree on the same half-open source cell.
  function snapInteger(value) {
    const nearest = Math.round(value);
    return Math.abs(value - nearest) <= 1e-9 ? (nearest || 0) : value;
  }

  function cell(row, point) {
    const local = toLocal(row, point);
    return {x: Math.floor(snapInteger(local.x)), y: Math.floor(snapInteger(local.y))};
  }

  function contains(row, point) {
    const local = toLocal(row, point);
    const x = snapInteger(local.x), y = snapInteger(local.y);
    return x >= 0 && x < row.width && y >= 0 && y < row.height;
  }

  function cuts(row, left, right) {
    const points = [left];
    for (const [x] of knots(row) || []) {
      if (x > left && x < right) points.push(x);
    }
    points.push(right);
    return points;
  }

  // Rectangles are in source-local coordinates. Every bend is retained, so a
  // fill/selection outline matches the same continuous projection as terrain.
  function polygon(row, rect) {
    rect = rect || {x: 0, y: 0, width: row.width, height: row.height};
    const points = cuts(row, rect.x, rect.x + rect.width);
    return points.map(x => toWorld(row, {x, y: rect.y})).concat(
      points.slice().reverse().map(x => toWorld(row, {x, y: rect.y + rect.height}))
    );
  }

  function bounds(row) {
    const points = polygon(row);
    const x = Math.min(...points.map(point => point.x));
    const y = Math.min(...points.map(point => point.y));
    const right = Math.max(...points.map(point => point.x));
    const bottom = Math.max(...points.map(point => point.y));
    return {x, y, width: right - x, height: bottom - y};
  }

  // Within each segment, display y = local y + shift + slope * (local x - x).
  // This can be drawn with one canvas affine transform per image slice.
  function segments(row) {
    const points = cuts(row, 0, row.width), result = [];
    for (let i = 1; i < points.length; i++) {
      const x = points[i - 1], width = points[i] - x;
      if (width > 0) {
        const start = shift(row, x);
        result.push({x, width, shift: start, slope: (shift(row, x + width) - start) / width});
      }
    }
    return result;
  }

  // Screen gestures stay continuous until each point is mapped back to its
  // source cell. Sampling at quarter-tile intervals also spans map boundaries.
  function trace(a, b, visit) {
    const distance = Math.hypot(b.x - a.x, b.y - a.y);
    if (!Number.isFinite(distance)) return;
    const steps = Math.max(1, Math.ceil(distance * 4));
    visit({x: a.x, y: a.y});
    if (!distance) return;
    for (let i = 1; i < steps; i++) {
      const fraction = i / steps;
      visit({x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction});
    }
    visit({x: b.x, y: b.y});
  }

  return {shift, toWorld, toLocal, cell, contains, bounds, polygon, segments, trace};
});
