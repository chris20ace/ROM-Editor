"""Local-only Emerald source and world editor; no ROM writes."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
import zipfile

from records import Records
from workspace_edits import SourceTransactions, protected_species, safe_path

ROOT = Path(__file__).resolve().parent
MAX_TEXT = 4 * 1024 * 1024
TEXT_EXTENSIONS = {'.c', '.h', '.s', '.inc', '.json', '.txt', '.md', '.mk', '.ld', '.cfg', '.pal', '.py', '.sh', '.ps1', '.yml', '.yaml', '.pory', '.hpp', '.cpp'}
CATEGORIES = [
    dict(id='pokemon', title='Pokémon', description='Species, types, stats, evolutions, learnsets, abilities and the Pokédex.', entrypoints=['src/data/pokemon/species_info.h', 'src/data/pokemon/evolution.h', 'src/data/pokemon/level_up_learnsets.h', 'src/starter_choose.c']),
    dict(id='moves', title='Moves & battles', description='Move data, battle scripts, effects, animations and battle rules.', entrypoints=['src/data/battle_moves.h', 'data/battle_scripts_1.s', 'src/battle_util.c']),
    dict(id='trainers', title='Trainers & teams', description='Opponent rosters, levels, held items, classes and AI decisions.', entrypoints=['src/data/trainers.h', 'src/data/trainer_parties.h', 'src/battle_ai_script_commands.c']),
    dict(id='items', title='Items & economy', description='Items, prices, effects, berries and shop inventories.', entrypoints=['src/data/items.h', 'src/data/item_icon_table.h', 'src/item_use.c']),
    dict(id='maps', title='World & encounters', description='Map headers, NPC positions, warps, layouts, collision, tilesets and wild Pokémon.', entrypoints=['data/maps/LittlerootTown/map.json', 'data/layouts/layouts.json', 'src/data/wild_encounters.json']),
    dict(id='story', title='Story & dialogue', description='Conversations, event scripts, progression flags, variables and quests.', entrypoints=['data/maps/LittlerootTown/scripts.inc', 'data/scripts', 'data/text', 'include/constants/flags.h']),
    dict(id='graphics', title='Art & interface', description='Pokémon sprites, characters, tiles, palettes, menus and screen artwork.', entrypoints=['graphics/pokemon/treecko/front.png', 'graphics', 'src/data/graphics']),
    dict(id='audio', title='Music & sound', description='Music sequences, instruments, samples, sound effects and Pokémon cries.', entrypoints=['sound/songs/midi', 'sound/direct_sound_samples', 'sound/song_table.inc']),
    dict(id='engine', title='Engine & systems', description='Movement, saving, menus, link features, contests, minigames and core code.', entrypoints=['src/overworld.c', 'src/save.c', 'src/contest.c', 'src/main.c']),
    dict(id='build', title='Build & reference', description='Compilation tools, documentation, constants and project configuration.', entrypoints=['INSTALL.md', 'Makefile', 'README.md', 'rom.sha1']),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def category_for(path):
    if path.startswith('graphics/') or path.startswith('src/data/graphics'):
        return 'graphics'
    if path.startswith('sound/') or any(x in path for x in ('sound.h', 'songs.h', 'src/m4a', 'src/sound')):
        return 'audio'
    if path.startswith('data/maps/'):
        return 'maps' if path.endswith('.json') else 'story'
    if path.startswith(('data/layouts/', 'data/tilesets/', 'src/data/tilesets/')) or 'wild_encounters' in path:
        return 'maps'
    if path.startswith(('data/scripts/', 'data/text/')) or path.endswith(('/flags.h', '/vars.h')) or 'event_scripts' in path:
        return 'story'
    if path.startswith('src/data/pokemon/') or any(x in path for x in ('species', 'pokedex', 'starter_choose', 'ability', 'abilities')):
        return 'pokemon'
    if 'trainer' in path or 'battle_ai' in path:
        return 'trainers'
    if any(x in path for x in ('battle_', 'moves.h', 'move_names', 'contest_moves', 'contest_effect')):
        return 'moves'
    if any(x in path for x in ('item', 'berry', 'berries', 'shop')):
        return 'items'
    if path.startswith(('src/', 'asm/', 'include/', 'data/', 'libagbsyscall/')):
        return 'engine'
    return 'build'


def file_kind(path):
    ext = path.suffix.lower()
    if ext == '.png':
        return 'image'
    if ext == '.wav':
        return 'audio'
    if ext in ('.mid', '.midi'):
        return 'midi'
    if ext in TEXT_EXTENSIONS or path.name in ('Makefile', '.gitignore', '.gitattributes'):
        return 'text'
    return 'binary'


class Conflict(ValueError):
    pass


class Project:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.source = (self.root / 'source' / 'pokeemerald').resolve()
        if not (self.source / 'rom.sha1').is_file():
            raise ValueError('Source snapshot is missing. Run setup_source.py first.')
        self.state = self.root / '.workbench'
        self.originals = self.state / 'originals'
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.records = Records(self.source)
        self.transactions = SourceTransactions(self.source, self.state)
        self._world = None
        self._campaign = None
        self._connections = None
        self.index = {}
        for path in sorted(self.source.rglob('*')):
            if not path.is_file() or path.is_symlink() or '.git' in path.parts:
                continue
            rel = path.relative_to(self.source).as_posix()
            size = path.stat().st_size
            kind = file_kind(path)
            self.index[rel] = dict(path=rel, size=size, kind=kind,
                                   editable=kind == 'text' and size <= MAX_TEXT and not protected_species(rel), category=category_for(rel))

    @property
    def world(self):
        if self._world is None:
            from world import World
            self._world = World(self.source)
        return self._world

    @property
    def campaign(self):
        if self._campaign is None:
            from campaign import Campaign
            self._campaign = Campaign(self.source)
        return self._campaign

    def refresh(self, paths):
        self._world = self._campaign = None
        self._connections = None
        for rel in paths:
            path = safe_path(self.source, rel, allow_new=True)
            if not path.exists():
                self.index.pop(rel, None)
                continue
            kind, size = file_kind(path), path.stat().st_size
            self.index[rel] = dict(path=rel, size=size, kind=kind, editable=kind == 'text' and size <= MAX_TEXT and not protected_species(rel), category=category_for(rel))

    def commit(self, plan, label):
        result = self.transactions.commit(plan, label)
        self.refresh(result['files'])
        return result

    @property
    def connections(self):
        if self._connections is None:
            from connections import Connections
            self._connections = Connections(self.source)
        return self._connections

    def path(self, rel):
        if not isinstance(rel, str) or rel not in self.index or '\\' in rel or ':' in rel:
            raise ValueError('Choose an existing file from this source project.')
        path = (self.source / rel).resolve()
        if not path.is_relative_to(self.source) or not path.is_file():
            raise ValueError('File must stay inside the source project.')
        return path

    def read_json(self, path, default):
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default

    def overview(self):
        counts = Counter(f['kind'] for f in self.index.values())
        record_counts, editors = {}, {}
        for kind in ('pokemon', 'moves'):
            try:
                record_counts[kind] = len(self.records.list_records(kind)['records'])
                editors[kind] = {'ready': True}
            except ValueError as error:
                record_counts[kind] = None
                editors[kind] = {'ready': False, 'error': str(error)}
        cats = []
        for cat in CATEGORIES:
            members = [f for f in self.index.values() if f['category'] == cat['id']]
            cats.append({**cat, 'count': len(members), 'editable': sum(f['editable'] for f in members)})
        return dict(
            rom=self.read_json(self.root / 'rom-report.json', {'matches': False, 'title': 'ROM not inspected'}),
            provenance=self.read_json(self.root / 'source' / 'provenance.json', {}),
            counts=dict(files=len(self.index), text=counts['text'], images=counts['image'],
                        audio=counts['audio'] + counts['midi'], maps=sum(p.endswith('/map.json') for p in self.index),
                        pokemon=record_counts['pokemon'], moves=record_counts['moves']),
            categories=cats, editors=editors, pokemon_protected=True,
            build=dict(ready=False, note='Source editing is ready. ROM compilation and playtesting are not configured. See the guide before building.'))

    def file(self, rel):
        path = self.path(rel)
        blob = path.read_bytes()
        result = {**self.index[rel], 'size': len(blob), 'sha256': digest(blob),
                  'asset_url': '/asset?path=' + __import__('urllib.parse', fromlist=['quote']).quote(rel)}
        if result['kind'] == 'text' and len(blob) <= MAX_TEXT:
            try:
                result['content'] = blob.decode('utf-8')
            except UnicodeDecodeError:
                result['editable'] = False
                result['kind'] = 'binary'
        else:
            result['editable'] = False
        original = self.originals / rel
        result['changed'] = original.is_file() and original.read_bytes() != blob
        result['protected'] = protected_species(rel)
        return result

    def write(self, rel, content, expected, restore=False):
        with self.lock:
            path = self.path(rel)
            if protected_species(rel) or protected_species(path.relative_to(self.source).as_posix()):
                raise ValueError('Pokémon species data is read-only in this world-building project.')
            if not self.index[rel]['editable']:
                raise ValueError('This format needs a specialized editor; it cannot be saved as text.')
            current = path.read_bytes()
            if not isinstance(expected, str) or digest(current) != expected:
                raise Conflict('The file changed since it was opened. Reload it before saving.')
            original = self.originals / rel
            if restore:
                if not original.is_file():
                    raise ValueError('This file has no Workbench backup yet.')
                updated = original.read_bytes()
            else:
                if not isinstance(content, str):
                    raise ValueError('File content must be text.')
                updated = content.encode('utf-8')
                if len(updated) > MAX_TEXT or '\x00' in content:
                    raise ValueError('Text files must be under 4 MiB and contain no null bytes.')
                if path.suffix.lower() == '.json':
                    json.loads(content)
            if current == updated:
                return {**self.file(rel), 'message': 'No changes to save.'}
            # Retain legacy version backups; grouped transactions handle originals,
            # created-file tracking and the actual replacement for every editor.
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + secrets.token_hex(3)
            backup = self.state / 'versions' / stamp / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(current)
            self.commit({rel: updated}, ('Restore: ' if restore else 'Source: ') + rel)
            return {**self.file(rel), 'message': 'Original restored; previous version backed up.' if restore else 'Saved to source. Original and previous version backed up.'}

    def update_record(self, body):
        with self.lock:
            if body.get('kind') == 'pokemon':
                raise ValueError('Pokémon are preserved. Choose starters or trainer teams in the Campaign editor.')
            record = self.records.get_record(body.get('kind'), body.get('id'))
            if record['sha256'] != body.get('expected_sha256'):
                raise Conflict('The source file changed since this record was opened. Reload before saving.')
            file_sha256 = self.file(record['source'])['sha256']
            rel, content = self.records.update_content(body.get('kind'), body.get('id'), body.get('fields'), body.get('expected_sha256'))
            self.write(rel, content, file_sha256)
            return {**self.records.get_record(body['kind'], body['id']), 'message': 'Saved to source with a backup. Build and playtest to see it in game.'}

    def changes(self):
        result = []
        if self.originals.exists():
            for original in sorted(self.originals.rglob('*')):
                if not original.is_file():
                    continue
                rel = original.relative_to(self.originals).as_posix()
                candidate = (self.source / rel).resolve()
                if candidate.is_relative_to(self.source) and not candidate.exists():
                    after = None
                else:
                    after = self.path(rel).read_bytes()
                before = original.read_bytes()
                if before != after:
                    result.append(dict(path=rel, original_sha256=digest(before),
                                       current_sha256=digest(after) if after is not None else None,
                                       deleted=after is None, binary=file_kind(candidate) != 'text'))
        for rel in sorted(self.transactions.created()):
            target = safe_path(self.source, rel, allow_new=True)
            if target.is_file() and not any(item['path'] == rel for item in result):
                result.append(dict(path=rel, original_sha256=None, current_sha256=digest(target.read_bytes()), deleted=False, created=True, binary=file_kind(target) != 'text'))
        return dict(files=result, count=len(result), scope='Files saved through this Workbench; external-only edits are not tracked.')

    def patch(self):
        with self.lock:
            output = []
            for item in self.changes()['files']:
                rel = item['path']
                if item.get('binary'):
                    output.append(f'# Binary map/asset changed: {rel}; use Export rebuild files (.zip).\n')
                    continue
                before = [] if item.get('created') else (self.originals / rel).read_bytes().decode('utf-8').splitlines(keepends=True)
                after = [] if item['deleted'] else self.path(rel).read_bytes().decode('utf-8').splitlines(keepends=True)
                target = '/dev/null' if item['deleted'] else 'b/' + rel
                origin = '/dev/null' if item.get('created') else 'a/' + rel
                for line in difflib.unified_diff(before, after, fromfile=origin, tofile=target):
                    output.append(line)
                    if not line.endswith('\n'):
                        output.append('\n\\ No newline at end of file\n')
            return ''.join(output)

    def export(self):
        stream = io.BytesIO()
        with self.lock, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            changes = self.changes()['files']
            for item in changes:
                if not item['deleted']:
                    archive.writestr('source/' + item['path'], self.path(item['path']).read_bytes())
            archive.writestr('changes.json', json.dumps({'source': self.read_json(self.root / 'source/provenance.json', {}), 'files': changes}, indent=2))
            archive.writestr('README.txt', 'Edited rebuild source files (including binary map layouts). Copy source/ over the same pinned pokeemerald source project. Deleted files are listed in changes.json. This is not a playable ROM. Pokémon definitions were protected by the editor.\n')
        return stream.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = 'EmeraldWorkbench/1.0'

    @property
    def project(self):
        return self.server.project

    def log_message(self, fmt, *args):
        # Tokens and source contents are never logged.
        pass

    def respond(self, status, body, mime='application/json; charset=utf-8', download=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, mutation=False):
        host = self.headers.get('Host', '')
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if host not in hosts:
            self.respond(403, {'error': 'This Workbench only accepts local requests.'})
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin not in {'http://' + h for h in hosts}:
            self.respond(403, {'error': 'Requests from another website are not allowed.'})
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.respond(403, {'error': 'Open the Workbench directly to access files.'})
            return False
        if mutation and not secrets.compare_digest(self.headers.get('X-Workbench-Token', ''), self.project.token):
            self.respond(403, {'error': 'Refresh the Workbench to reconnect before saving.'})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        request = urlsplit(self.path)
        query = parse_qs(request.query)
        value = lambda key, default='': query.get(key, [default])[0]
        try:
            if request.path == '/api/session':
                return self.respond(200, {'token': self.project.token})
            if request.path == '/api/overview':
                return self.respond(200, self.project.overview())
            if request.path == '/api/files':
                search, category = value('q').lower(), value('category')
                members = [f for f in self.project.index.values() if (not category or category == 'all' or f['category'] == category) and search in f['path'].lower()]
                return self.respond(200, {'files': members[:400], 'total': len(members)})
            if request.path == '/api/file':
                return self.respond(200, self.project.file(value('path')))
            if request.path == '/api/records':
                return self.respond(200, self.project.records.list_records(value('kind')))
            if request.path == '/api/record':
                record = self.project.records.get_record(value('kind'), value('id'))
                record['readonly'] = value('kind') == 'pokemon'
                return self.respond(200, record)
            if request.path == '/api/world/maps':
                return self.respond(200, self.project.world.list_maps())
            if request.path == '/api/world/map':
                return self.respond(200, self.project.world.get_map(value('name'), value('primary') or None, value('secondary') or None))
            if request.path == '/api/world/objects':
                return self.respond(200, self.project.world.object_catalog())
            if request.path == '/api/player':
                from player import Player
                with self.project.lock:
                    return self.respond(200, Player(self.project.source).catalog())
            if request.path == '/api/player/asset':
                from player import Player
                with self.project.lock:
                    return self.respond(200, Player(self.project.source).get_asset(value('path'), palette_path=value('palette_path') or None))
            if request.path == '/api/player/preview':
                from player import Player
                with self.project.lock:
                    return self.respond(200, Player(self.project.source).preview(value('path'), palette_path=value('palette_path') or None), 'image/png')
            if request.path == '/api/connections':
                with self.project.lock:
                    return self.respond(200, self.project.connections.catalog())
            if request.path == '/api/areas':
                from areas import Areas
                with self.project.lock:
                    return self.respond(200, Areas(self.project.source).catalog())
            if request.path == '/api/worldmap':
                from worldmap import WorldMap
                with self.project.lock:
                    return self.respond(200, WorldMap(self.project.source).catalog())
            if request.path.startswith('/api/world/maps/') and request.path.endswith('/atlas.png'):
                return self.respond(200, self.project.world.atlas(request.path.split('/')[-2], value('primary') or None, value('secondary') or None), 'image/png')
            if request.path.startswith('/api/world/maps/') and request.path.endswith('/preview.png'):
                sizes = parse_qs(request.query, keep_blank_values=True).get('max_size')
                max_size = None
                if sizes is not None:
                    if len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdigit():
                        raise ValueError('Preview maximum size must be an integer from 32 to 1024.')
                    max_size = int(sizes[0])
                    if not 32 <= max_size <= 1024:
                        raise ValueError('Preview maximum size must be an integer from 32 to 1024.')
                return self.respond(200, self.project.world.render_map(request.path.split('/')[-2], max_size=max_size), 'image/png')
            if request.path.startswith('/api/world/objects/') and request.path.endswith('.png'):
                return self.respond(200, self.project.world.object_sprite(request.path.split('/')[-1][:-4]), 'image/png')
            if request.path == '/api/campaign':
                return self.respond(200, self.project.campaign.metadata())
            if request.path == '/api/campaign/starters':
                return self.respond(200, self.project.campaign.starters())
            if request.path == '/api/campaign/trainers':
                return self.respond(200, self.project.campaign.trainers(value('id') or None))
            if request.path == '/api/campaign/story':
                return self.respond(200, self.project.campaign.story(value('map')))
            if request.path in ('/api/region', '/api/region/atlas.png'):
                from region import Region
                region = Region(self.project.source)
                return self.respond(200, region.get()) if request.path == '/api/region' else self.respond(200, region.atlas(), 'image/png')
            if request.path == '/api/transactions':
                return self.respond(200, {'transactions': self.project.transactions.list_transactions()})
            if request.path == '/api/changes':
                return self.respond(200, self.project.changes())
            if request.path == '/api/patch':
                return self.respond(200, self.project.patch(), 'text/plain; charset=utf-8', 'emerald-source-changes.patch')
            if request.path == '/api/export':
                return self.respond(200, self.project.export(), 'application/zip', 'emerald-rebuild-changes.zip')
            if request.path == '/asset':
                path = self.project.path(value('path'))
                kind = file_kind(path)
                mime = {'image': 'image/png', 'audio': 'audio/wav', 'midi': 'audio/midi'}.get(kind, 'application/octet-stream')
                name = None if kind in ('image', 'audio') else ''.join(c for c in path.name if c.isalnum() or c in '._-')
                return self.respond(200, path.read_bytes(), mime, name)
            if request.path == '/guide':
                return self.respond(200, (ROOT / 'web' / 'guide.html').read_bytes(), 'text/html; charset=utf-8')
            if request.path == '/api/health':
                return self.respond(200, {'app': 'emerald-workbench', 'ready': True})
            static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css', '/guide.css': 'guide.css',
                      '/world': 'world.html', '/world.html': 'world.html', '/world.js': 'world.js', '/world.css': 'world.css',
                      '/campaign': 'campaign.html', '/campaign.html': 'campaign.html', '/campaign.js': 'campaign.js', '/campaign.css': 'campaign.css',
                      '/region': 'region.html', '/region.js': 'region.js', '/region.css': 'region.css',
                      '/connections': 'connections.html', '/connections.js': 'connections.js', '/connections.css': 'connections.css',
                      '/areas': 'areas.html', '/areas.js': 'areas.js', '/areas.css': 'areas.css',
                      '/worldmap': 'worldmap.html', '/worldmap.js': 'worldmap.js', '/worldmap.css': 'worldmap.css',
                      '/worldmap-geometry.js': 'worldmap-geometry.js',
                      '/player': 'player.html', '/player.js': 'player.js', '/player.css': 'player.css',
                      '/worldtools.js': 'worldtools.js', '/worldtools.css': 'worldtools.css'}
            if request.path in static:
                path = ROOT / 'web' / static[request.path]
                mime = mimetypes.guess_type(str(path))[0] or 'text/plain'
                return self.respond(200, path.read_bytes(), mime + '; charset=utf-8')
            return self.respond(404, {'error': 'Not found.'})
        except (ValueError, KeyError, TypeError) as error:
            self.respond(400, {'error': str(error)})
        except FileNotFoundError:
            self.respond(404, {'error': 'That file is no longer available.'})
        except Exception as error:
            print('Request failed:', type(error).__name__, str(error))
            self.respond(500, {'error': 'The request could not be completed. Check the local server log.'})

    def do_POST(self):
        if not self.allowed(mutation=True):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.respond(415, {'error': 'Expected a JSON request.'})
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_TEXT * 6 + 1024:
                return self.respond(413, {'error': 'Request exceeds the text editing limit.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            if self.path == '/api/file':
                result = self.project.write(body.get('path'), body.get('content'), body.get('expected_sha256'))
            elif self.path == '/api/restore':
                result = self.project.write(body.get('path'), None, body.get('expected_sha256'), restore=True)
            elif self.path == '/api/record':
                result = self.project.update_record(body)
            elif self.path == '/api/world/map':
                with self.project.lock:
                    name = body.get('name')
                    saved = self.project.commit(self.project.world.plan_save(name, body), 'Map: ' + str(name))
                    result = {**self.project.world.get_map(name), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/world/new':
                with self.project.lock:
                    name = body.get('name')
                    plan = self.project.world.plan_new(name, body.get('template', 'LittlerootTown'), body.get('width', 20), body.get('height', 20))
                    saved = self.project.commit(plan, 'New blank map: ' + str(name))
                    result = {**self.project.world.get_map(name), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/player/save':
                from player import Player
                with self.project.lock:
                    saved = self.project.commit(Player(self.project.source).plan_save(body), 'Player appearance: ' + str(body.get('path')))
                    result = {'asset': Player(self.project.source).get_asset(body.get('path'), palette_path=body.get('palette_path') or None),
                              'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/campaign/starters':
                with self.project.lock:
                    saved = self.project.commit(self.project.campaign.plan_starters(body), 'Starter choices')
                    result = {**self.project.campaign.starters(), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/worldmap/save':
                from world_batch import plan_world_batch
                with self.project.lock:
                    maps = body.get('maps')
                    plan = plan_world_batch(self.project.world, maps)
                    saved = self.project.commit(plan, 'World canvas: ' + str(len(maps)) + ' maps')
                    result = {'saved': [self.project.world.get_map(m['name']) for m in maps],
                              'transaction': saved['id'], 'message': saved['message']}
            elif self.path in ('/api/connections/edge', '/api/connections/warp'):
                with self.project.lock:
                    edge = self.path.endswith('/edge')
                    plan = self.project.connections.plan_edge(body) if edge else self.project.connections.plan_warp(body)
                    label = ('Map edges: ' + str(body.get('a')) + ' ↔ ' + str(body.get('b'))) if edge else ('Entrances: ' + str(body.get('a', {}).get('map')) + ' ↔ ' + str(body.get('b', {}).get('map')))
                    saved = self.project.commit(plan, label)
                    result = {**self.project.connections.catalog(), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/campaign/trainers':
                with self.project.lock:
                    saved = self.project.commit(self.project.campaign.plan_trainer(body), 'Trainer: ' + str(body.get('id')))
                    result = {**self.project.campaign.trainers(body.get('id')), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/campaign/story':
                with self.project.lock:
                    saved = self.project.commit(self.project.campaign.plan_story(body), 'Story: ' + str(body.get('map')))
                    result = {**self.project.campaign.story(body.get('map')), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/campaign/dialogue':
                with self.project.lock:
                    plan = self.project.campaign.plan_new_dialogue(body.get('map'), body.get('label'), body.get('text'), body.get('revision'))
                    saved = self.project.commit(plan, 'NPC dialogue: ' + str(body.get('map')))
                    result = {**self.project.campaign.story(body.get('map')), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/region':
                from region import Region
                with self.project.lock:
                    saved = self.project.commit(Region(self.project.source).plan_save(body), 'Region town map')
                    result = {**Region(self.project.source).get(), 'transaction': saved['id'], 'message': saved['message']}
            elif self.path == '/api/transaction/undo':
                with self.project.lock:
                    result = self.project.transactions.undo(body.get('id'))
                    self.project.refresh(result['files'])
            else:
                return self.respond(404, {'error': 'Not found.'})
            self.respond(200, result)
        except Conflict as error:
            self.respond(409, {'error': str(error)})
        except (ValueError, KeyError, TypeError) as error:
            self.respond(400, {'error': str(error)})
        except Exception as error:
            print('Save failed:', type(error).__name__, str(error))
            self.respond(500, {'error': 'The save could not be completed. Your backups are in .workbench.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='Open the local Workbench in your browser')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.project = Project()
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Emerald Workbench ready at {url}', flush=True)
    print('Source edits use backups. Original ROM is never written. Ctrl+C stops the server.', flush=True)
    if args.open:
        webbrowser.open(url + '/worldmap')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
