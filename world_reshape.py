"""Coordinate source-backed map reshapes with their world-canvas positions.

The caller holds the project lock. Canvas positions are editor state, so they
live beside the source transaction rather than inside the game source tree.
Both halves are backed up and rolled back together if either save fails.
"""
from __future__ import annotations

import base64
import json
import secrets

from world_positions import MAX_FILE_BYTES, WorldPositions, _encoded, _names, _position
from worldmap import WorldMap
from workspace_edits import _atomic_write


SIDECAR = 'world-reshape.json'


def _raw(path):
    return path.read_bytes() if path.exists() else None


def _pack(value):
    return None if value is None else base64.b64encode(value).decode('ascii')


def _unpack(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Saved canvas backup is invalid.')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('Saved canvas backup is invalid.') from error
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Saved canvas backup is too large.')
    try:
        document = json.loads(raw)
        if (not isinstance(document, dict) or type(document.get('version')) is not int
                or document['version'] != 1 or not isinstance(document.get('positions'), dict)):
            raise ValueError('Invalid saved positions.')
        _names(document['positions'].keys())
        for position in document['positions'].values():
            _position(position)
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('Saved canvas backup is invalid.') from error
    return raw


class WorldReshaper:
    def __init__(self, project):
        self.project = project
        self.positions = WorldPositions(project.root)

    def _backend(self, kind):
        if kind == 'expand':
            from route_expand import RouteExpander
            return RouteExpander(self.project.world)
        if kind == 'merge':
            from route_merge import RouteMerger
            return RouteMerger(self.project.world)
        raise ValueError('Choose expand or merge for this map edit.')

    def _prepare(self, kind, body):
        if not isinstance(body, dict):
            raise ValueError('Map reshaping needs an object.')
        backend = self._backend(kind)
        catalog = WorldMap(self.project.source).catalog()
        saved = self.positions.read(catalog)
        if (not isinstance(body.get('positions_revision'), str)
                or body['positions_revision'] != saved['revision']):
            raise ValueError('World box positions changed. Reload the canvas and preview this edit again.')
        current = {row['name']: dict(saved['positions'].get(row['name'],
                   {'x': row['x'], 'y': row['y']})) for row in catalog['maps']}
        if kind == 'merge':
            rows = body.get('maps')
            if not isinstance(rows, list) or len(rows) < 2:
                raise ValueError('Select at least two connected boxes to combine.')
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                    raise ValueError('Each selected box needs a map name and its current position.')
                name = row['name']
                if name in seen or name not in current:
                    raise ValueError('Selected maps must exist and cannot be repeated.')
                seen.add(name)
                position = _position({'x': row.get('x'), 'y': row.get('y')})
                if position != current[name]:
                    raise ValueError('A selected box moved. Reload the canvas and preview the combination again.')
        details = backend.preview(body)
        if kind == 'expand':
            name = body['name']
            anchor = {'name': name,
                      'x': current[name]['x'] - body.get('left', 0),
                      'y': current[name]['y'] - body.get('top', 0)}
        else:
            name = details['name']
            anchor = {'name': name, 'x': min(row['x'] for row in body['maps']),
                      'y': min(row['y'] for row in body['maps'])}
        _position({'x': anchor['x'], 'y': anchor['y']})
        return backend, {**details, 'positions_revision': saved['revision'],
                         'canvas_position': anchor}, current

    def preview(self, kind, body):
        return self._prepare(kind, body)[1]

    def commit(self, kind, body):
        backend, details, current = self._prepare(kind, body)
        plan = backend.plan(body)
        name = details['canvas_position']['name']
        removed = ({row['name'] for row in body['maps']} - {name}) if kind == 'merge' else set()
        updates = {key: value for key, value in current.items() if key not in removed}
        updates[name] = {key: details['canvas_position'][key] for key in ('x', 'y')}
        # Preserve existing hidden overrides too: undo can bring removed maps back.
        all_positions, revision = self.positions._load()
        if revision != body['positions_revision']:
            raise ValueError('World box positions changed. Preview this edit again.')
        proposed = {**all_positions, **updates}
        before = _raw(self.positions.path)
        after = before if proposed == all_positions else _encoded(proposed)
        if after is not None and len(after) > MAX_FILE_BYTES:
            raise ValueError('Too many saved world box positions.')
        label = ('Expand map: ' if kind == 'expand' else 'Combine maps: ') + name

        def save_positions():
            self.positions.save({'revision': revision, 'positions': updates}, self.project.world._maps())

        saved = self._coordinated(lambda: self.project.transactions.commit(plan, label),
                                  before, after, save_positions)
        return {**details, 'transaction': saved['id'], 'message': saved['message'],
                'canvas_positions': self.positions.read(self.project.world._maps())}

    def _folder(self, transaction_id):
        # Match SourceTransactions.undo's identifier rule before accessing a
        # sidecar. SourceTransactions still validates the manifest and all files.
        if not isinstance(transaction_id, str) or not transaction_id.replace('-', '').replace('.', '').isalnum():
            raise ValueError('Invalid saved-edit identifier.')
        root = (self.project.state / 'transactions').resolve()
        folder = (root / transaction_id).resolve()
        if not folder.is_relative_to(root) or not (folder / 'manifest.json').is_file():
            raise ValueError('Saved edit was not found.')
        return folder

    def _attach_sidecar(self, transaction_id, contents):
        _atomic_write(self._folder(transaction_id) / SIDECAR, contents)

    def _restore_positions(self, contents):
        if contents is None:
            self.positions.path.unlink(missing_ok=True)
        else:
            self.positions.path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.positions.path, contents)

    def _rollback_source(self, saved):
        undone = self.project.transactions.undo(saved['id'])
        self.project.refresh(undone['files'])
        # Failed operations and their internal restoration are not user edits.
        for identifier in (saved['id'], undone['id']):
            if identifier is None:
                continue
            path = self._folder(identifier) / 'manifest.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['status'] = 'rolled_back'
            _atomic_write(path, json.dumps(manifest, indent=2).encode('utf-8'))

    def _coordinated(self, source_action, before, after, position_action):
        contents = json.dumps({'version': 1, 'before': _pack(before), 'after': _pack(after)},
                              indent=2).encode('utf-8')
        self.project.state.mkdir(parents=True, exist_ok=True)
        pending = self.project.state / ('reshape-pending-' + secrets.token_hex(8) + '.json')
        saved = None
        # Stage the complete undo metadata before touching any source files.
        _atomic_write(pending, contents)
        try:
            if _raw(self.positions.path) != before:
                raise ValueError('World box positions changed while saving. Preview this edit again.')
            saved = source_action()
            if saved['id'] is None:
                raise ValueError('The map reshape did not produce a source edit.')
            self.project.refresh(saved['files'])
            self._attach_sidecar(saved['id'], contents)
            position_action()
            if _raw(self.positions.path) != after:
                raise ValueError('The saved canvas positions did not match this edit.')
            return saved
        except Exception as failure:
            errors = []
            # Our position writer is atomic. Restore its complete output after
            # failures, but never overwrite a newer external position edit.
            try:
                actual = _raw(self.positions.path)
                if actual != before and actual == after:
                    self._restore_positions(before)
                elif actual != before:
                    errors.append('canvas positions changed outside this edit; the current file was kept')
            except Exception as error:
                errors.append('canvas positions: ' + str(error))
            if saved is not None and saved['id'] is not None:
                try:
                    self._rollback_source(saved)
                except Exception as error:
                    errors.append('source files: ' + str(error))
            if errors:
                raise RuntimeError('Map edit failed and rollback needs attention: ' + '; '.join(errors)) from failure
            raise
        finally:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                # This disposable staging copy is not used by undo. A cleanup
                # failure must not turn a completed, backed-up save into an error.
                pass

    def undo(self, transaction_id):
        folder = self._folder(transaction_id)
        sidecar = folder / SIDECAR
        if not sidecar.exists():
            result = self.project.transactions.undo(transaction_id)
            self.project.refresh(result['files'])
            return result
        try:
            document = json.loads(sidecar.read_text(encoding='utf-8'))
            if (not isinstance(document, dict) or type(document.get('version')) is not int
                    or document['version'] != 1 or set(document) != {'version', 'before', 'after'}):
                raise ValueError('Invalid canvas backup.')
            original, reshaped = _unpack(document['before']), _unpack(document['after'])
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError('The saved canvas backup is invalid; this edit was not restored.') from error
        current = _raw(self.positions.path)

        def undo_source():
            return self.project.transactions.undo(transaction_id)

        if current != reshaped:
            result = undo_source()
            self.project.refresh(result['files'])
            return {**result, 'positions_restored': False,
                    'positions_message': 'Source edit restored. Later world-canvas position changes were kept.'}
        result = self._coordinated(undo_source, current, original,
                                   lambda: self._restore_positions(original))
        return {**result, 'positions_restored': True,
                'positions_message': 'Source edit and its world-canvas positions were restored together.'}
