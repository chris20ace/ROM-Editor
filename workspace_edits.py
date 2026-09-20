"""Backed-up source transactions and the protected Pokémon boundary."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets


def protected_species(path):
    if not isinstance(path, str):
        return False
    # Windows treats case variants as the same file. Backslash normalization also
    # makes this predicate safe for callers before safe_path rejects that spelling.
    path = '/'.join(part.rstrip(' .') for part in path.replace('\\', '/').split('/')).casefold()
    return path.startswith(('src/data/pokemon/', 'src/data/pokemon_graphics/', 'graphics/pokemon/')) or path in {
        'include/constants/species.h', 'include/constants/pokemon.h',
        'src/data/text/species_names.h', 'src/data/graphics/pokemon.h',
    }


def safe_path(source, relative, allow_new=False):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Invalid source path.')
    parts = relative.split('/')
    if not parts or any(p in ('', '.', '..') or p.startswith('.') or p != p.rstrip(' .')
                        or any(ord(char) < 32 for char in p) or any(char in '<>"|?*' for char in p)
                        for p in parts):
        raise ValueError('Source paths must be ordinary project files.')
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if any(p.split('.')[0].casefold() in reserved for p in parts):
        raise ValueError('Windows device paths are not source files.')
    target = source.joinpath(*parts)
    resolved = target.resolve()
    if not resolved.is_relative_to(source.resolve()):
        raise ValueError('File must stay inside the source project.')
    if target.exists() and not target.is_file():
        raise ValueError('Expected a source file.')
    if not allow_new and not target.is_file():
        raise ValueError('Source file is missing.')
    # Resolve existing directory junctions, symlinks and Windows short names so
    # authorization and backups use the actual destination's project-relative path.
    return resolved


def _atomic_write(path, content):
    staging = path.with_name(path.name + '.metadata-' + secrets.token_hex(6) + '.tmp')
    try:
        staging.write_bytes(content)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _write_manifest(path, manifest):
    _atomic_write(path, json.dumps(manifest, indent=2).encode('utf-8'))


def sha(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


class SourceTransactions:
    def __init__(self, source, state):
        self.source, self.state = Path(source), Path(state)
        self.originals = self.state / 'originals'
        self.created_path = self.state / 'created-files.json'

    def created(self):
        return set(json.loads(self.created_path.read_text('utf-8'))) if self.created_path.exists() else set()

    def commit(self, plan, label='Source edit'):
        if not isinstance(plan, dict) or not plan:
            return {'id': None, 'files': [], 'message': 'No source changes.'}
        checked = {}
        destinations = set()
        for rel, content in plan.items():
            if protected_species(rel):
                raise ValueError('Pokémon definitions are protected in this world-building project.')
            target = safe_path(self.source, rel, allow_new=True)
            canonical = target.relative_to(self.source.resolve()).as_posix()
            if protected_species(canonical):
                raise ValueError('Pokémon definitions are protected in this world-building project.')
            if canonical.casefold() in destinations:
                raise ValueError('An edit cannot target the same source file through multiple path spellings.')
            destinations.add(canonical.casefold())
            rel = canonical
            if content is not None and (not isinstance(content, bytes) or len(content) > 8 * 1024 * 1024):
                raise ValueError('Invalid source edit payload.')
            before = target.read_bytes() if target.exists() else None
            if before != content:
                checked[rel] = (target, before, content)
        if not checked:
            return {'id': None, 'files': [], 'message': 'No source changes.'}
        transaction_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ-') + secrets.token_hex(3)
        folder = self.state / 'transactions' / transaction_id
        folder.mkdir(parents=True)
        created_before = self.created_path.read_bytes() if self.created_path.exists() else None
        created = set(json.loads(created_before)) if created_before is not None else set()
        manifest = {'id': transaction_id, 'label': label, 'status': 'prepared', 'files': {}}
        temporary, written = {}, []
        try:
            for rel, (target, before, after) in checked.items():
                manifest['files'][rel] = {'before': sha(before), 'after': sha(after), 'existed': before is not None}
                if before is not None:
                    previous = folder / 'before' / rel
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(previous, before)
                    original = self.originals / rel
                    if rel not in created and not original.exists():
                        original.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write(original, before)
                target.parent.mkdir(parents=True, exist_ok=True)
                if after is not None:
                    staging = target.with_name(target.name + '.workbench-' + transaction_id + '.tmp')
                    temporary[rel] = staging
                    staging.write_bytes(after)
            _write_manifest(folder / 'manifest.json', manifest)
            # Re-check every input before replacing any output, catching external editors.
            for rel, (target, before, after) in checked.items():
                actual = target.read_bytes() if target.exists() else None
                if actual != before:
                    raise ValueError('A source file changed while saving; reload before trying again.')
            for rel, (target, before, after) in checked.items():
                if after is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(temporary[rel], target)
                written.append(rel)
            created.update(rel for rel, (_, before, after) in checked.items()
                           if before is None and after is not None and not (self.originals / rel).exists())
            created.difference_update(rel for rel, (_, _, after) in checked.items() if after is None)
            self.state.mkdir(exist_ok=True)
            _atomic_write(self.created_path, json.dumps(sorted(created), indent=2).encode('utf-8'))
            manifest['status'] = 'committed'
            _write_manifest(folder / 'manifest.json', manifest)
        except Exception as failure:
            rollback_errors = []
            for rel in reversed(written):
                target, before, _ = checked[rel]
                try:
                    if before is None:
                        target.unlink(missing_ok=True)
                    else:
                        _atomic_write(target, before)
                except Exception as exc:
                    rollback_errors.append(f'{rel}: {exc}')
            # The ledger is part of the transaction: final-manifest failure must
            # also restore which files were created or deleted by this save.
            try:
                ledger_now = self.created_path.read_bytes() if self.created_path.exists() else None
                if ledger_now != created_before:
                    if created_before is None:
                        self.created_path.unlink(missing_ok=True)
                    else:
                        _atomic_write(self.created_path, created_before)
            except Exception as exc:
                rollback_errors.append(f'created-files ledger: {exc}')
            manifest['status'] = 'rollback_failed' if rollback_errors else 'rolled_back'
            if rollback_errors:
                manifest['rollback_errors'] = rollback_errors
            try:
                _write_manifest(folder / 'manifest.json', manifest)
            except Exception as exc:
                rollback_errors.append(f'transaction manifest: {exc}')
            if rollback_errors:
                raise RuntimeError('Save failed and rollback needs attention: ' + '; '.join(rollback_errors)) from failure
            raise
        finally:
            for staging in temporary.values():
                staging.unlink(missing_ok=True)
        return {'id': transaction_id, 'files': list(checked), 'message': 'Saved source files together with backups.'}

    def undo(self, transaction_id):
        if not isinstance(transaction_id, str) or not transaction_id.replace('-', '').replace('.', '').isalnum():
            raise ValueError('Invalid saved-edit identifier.')
        folder = self.state / 'transactions' / transaction_id
        path = folder / 'manifest.json'
        if not path.is_file():
            raise ValueError('Saved edit was not found.')
        manifest = json.loads(path.read_text('utf-8'))
        if manifest['status'] != 'committed':
            raise ValueError('This edit cannot be restored.')
        plan = {}
        for rel, versions in manifest['files'].items():
            target = safe_path(self.source, rel, allow_new=True)
            current = target.read_bytes() if target.exists() else None
            if sha(current) != versions['after']:
                raise ValueError('A file changed after this save. Undo newer edits first, or inspect the backups.')
            plan[rel] = (folder / 'before' / rel).read_bytes() if versions['existed'] else None
        return self.commit(plan, 'Undo: ' + manifest['label'])

    def list_transactions(self):
        folder = self.state / 'transactions'
        if not folder.exists():
            return []
        result = []
        for path in sorted(folder.glob('*/manifest.json'), reverse=True):
            item = json.loads(path.read_text('utf-8'))
            if item['status'] == 'committed':
                result.append({'id': item['id'], 'label': item['label'], 'files': list(item['files'])})
        return result
