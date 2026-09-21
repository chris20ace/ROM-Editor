"""Persist world-canvas box positions separately from game map data.

The caller holds the project lock while reading or saving. Saves are patches:
omitted map names retain their position, and ``None`` removes an override.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


MAX_COORDINATE = 100_000
MAX_FILE_BYTES = 4 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def _names(maps):
    """Accept a world catalog, catalog rows, or an iterable of map names."""
    if isinstance(maps, dict):
        maps = maps.get('maps', maps.keys())
    if isinstance(maps, (str, bytes)):
        raise ValueError('Map catalog must contain map names or map records')
    try:
        values = list(maps)
    except TypeError as error:
        raise ValueError('Map catalog must contain map names or map records') from error
    names = set()
    for row in values:
        name = row.get('name') if isinstance(row, dict) else row
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('Map catalog contains an invalid map name')
        names.add(name)
    return names


def _position(value):
    if not isinstance(value, dict) or set(value) != {'x', 'y'}:
        raise ValueError('Each box position needs exactly x and y coordinates')
    for coordinate in value.values():
        # bool is an int subclass; accepting it would silently save 0 or 1.
        if type(coordinate) is not int or not -MAX_COORDINATE <= coordinate <= MAX_COORDINATE:
            raise ValueError(f'Box coordinates must be whole tiles between {-MAX_COORDINATE} and {MAX_COORDINATE}')
    return {'x': value['x'], 'y': value['y']}


def _encoded(positions):
    return (json.dumps({'version': 1, 'positions': positions},
                       ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('utf-8')


class WorldPositions:
    def __init__(self, workspace_root):
        self.path = Path(workspace_root) / '.workbench' / 'world-positions.json'

    def _load(self):
        if not self.path.exists():
            return {}, hashlib.sha256(_encoded({})).hexdigest()
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Saved world positions file is too large')
        raw = self.path.read_bytes()
        try:
            document = json.loads(raw)
            if (not isinstance(document, dict) or document.get('version') != 1
                    or type(document.get('version')) is not int
                    or not isinstance(document.get('positions'), dict)):
                raise ValueError('Invalid world positions document')
            positions = {}
            for name, value in document['positions'].items():
                if not isinstance(name, str) or not _NAME.fullmatch(name):
                    raise ValueError('Invalid saved map name')
                positions[name] = _position(value)
        except (UnicodeError, ValueError) as error:
            raise ValueError('Saved world positions are invalid; the file was not changed') from error
        return positions, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _view(positions, revision, names):
        # Deleted maps can later be restored. Keep their saved positions on disk,
        # but do not expose them as currently movable boxes.
        return {'revision': revision,
                'positions': {name: dict(position) for name, position in positions.items() if name in names}}

    def read(self, maps):
        names = _names(maps)
        positions, revision = self._load()
        return self._view(positions, revision, names)

    def save(self, body, maps):
        """Atomically apply validated position updates, rejecting stale saves.

        ``body`` is ``{revision, positions: {map_name: {x, y} | None}}``.
        Positions affect only the editor's canvas; game connections are unchanged.
        """
        if not isinstance(body, dict):
            raise ValueError('World position edits must be an object')
        names = _names(maps)
        positions, revision = self._load()
        if not isinstance(body.get('revision'), str) or body['revision'] != revision:
            raise ValueError('World box positions changed since they were loaded; reload before moving them again')
        updates = body.get('positions')
        if not isinstance(updates, dict):
            raise ValueError('World position edits need a positions object')
        proposed = dict(positions)
        for name, value in updates.items():
            if not isinstance(name, str) or name not in names:
                raise ValueError(f'Cannot position an unknown map: {name}')
            if value is None:
                proposed.pop(name, None)
            else:
                proposed[name] = _position(value)
        if proposed == positions:
            return self._view(positions, revision, names)
        encoded = _encoded(proposed)
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError('Too many saved world box positions')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=self.path.parent,
                                             prefix='world-positions-', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # The temporary file is closed before replace, as required on Windows.
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return self._view(proposed, hashlib.sha256(encoded).hexdigest(), names)
