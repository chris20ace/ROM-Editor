"""Editable character art discovered from the game's graphics and palette tables.

Discovery is deliberately separate from Player's narrow allowlist. Only source
graphics bound by the character tables enter this editor; Pokémon art stays out.
The indexed pixel, revision and atomic save rules are inherited from Player.
"""
from collections import Counter, defaultdict, OrderedDict
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from player import Player, METADATA, OBJECT, _declarations, _fields


CHARACTER_METADATA = METADATA + (
    OBJECT + 'object_event_graphics_info_pointers.h',
    'src/data/trainers.h',
)
_DISCOVERY_CACHE = OrderedDict()


def _name(value):
    value = re.sub(r'([a-z\d])([A-Z])', r'\1 \2', value).replace('_', ' ')
    return value.title().replace('Npc', 'NPC').replace('Rs ', 'RS ')


def _editable_png(path):
    return isinstance(path, str) and path.endswith('.png') and (
        path.startswith('graphics/trainers/front_pics/')
        or path.startswith('graphics/trainers/back_pics/')
        or path.startswith('graphics/object_events/pics/people/')
        or path.startswith('graphics/object_events/pics/misc/')
    )


class CharacterArt(Player):
    def __init__(self, source):
        super().__init__(source)
        self._protected_palettes = defaultdict(list)
        self._trainer_pics = {}

    def _metadata(self):
        content, digest = {}, hashlib.sha256()
        for relative in CHARACTER_METADATA:
            path = self._path(relative)
            data = path.read_bytes() if path.is_file() else b''
            content[relative] = data.decode('utf-8')
            digest.update(relative.encode() + b'\0' + data + b'\0')
        return digest.hexdigest(), content

    def _records(self):
        key, texts = self._metadata()
        if key == self._metadata_key:
            return self._assets
        cache_key = (str(self.source), key)
        cached = _DISCOVERY_CACHE.get(cache_key)
        if cached and all((self.source / path).is_file() == exists for path, exists in cached[3].items()):
            self._assets, self._protected_palettes, self._trainer_pics = cached[:3]
            self._metadata_key = key
            _DISCOVERY_CACHE.move_to_end(cache_key)
            return self._assets
        # Retain the complete player catalog, including intro and region-map art.
        assets = copy.deepcopy(Player(self.source)._records())
        for asset in assets.values():
            asset.update(category='players', trainer_pic_ids=[], object_graphics_ids=[], trainer_uses=[])
        declarations = _declarations('\n'.join(texts.values()))
        self._protected_palettes = defaultdict(list)
        self._trainer_pics = {}

        def add(symbol, palette_symbol, kind, label, fw, fh, category, uses=(),
                palette_label='Normal', trainer_id=None, object_ids=()):
            path = declarations.get(symbol, {}).get('path')
            pal = declarations.get(palette_symbol, {}).get('path')
            if not path or not pal or not _editable_png(path):
                return
            # A graphic declaration must never turn into an arbitrary file editor.
            if not (pal.startswith('graphics/object_events/palettes/') or
                    pal.startswith('graphics/trainers/palettes/') or
                    pal.startswith('graphics/trainers/front_pics/') or pal == path):
                return
            if not self._path(path).is_file() or not self._path(pal).is_file():
                return
            if path not in assets:
                assets[path] = dict(path=path, label=label, character=label.split(' · ')[0],
                                    variant='Emerald', kind=kind, frame_width=fw, frame_height=fh,
                                    category=category, palette_options=[], uses=[], symbols=[],
                                    trainer_pic_ids=[], object_graphics_ids=[], trainer_uses=[])
            asset = assets[path]
            if not any(p['path'] == pal for p in asset['palette_options']):
                asset['palette_options'].append({'path': pal, 'label': palette_label})
            asset['uses'] = sorted(set(asset['uses']) | set(uses))
            asset['symbols'] = sorted(set(asset['symbols']) | {symbol})
            asset['object_graphics_ids'] = sorted(set(asset['object_graphics_ids']) | set(object_ids))
            if trainer_id:
                asset['trainer_pic_ids'] = sorted(set(asset['trainer_pic_ids']) | {trainer_id})
                if kind == 'battle_front':
                    self._trainer_pics[trainer_id] = {'path': path, 'palette_path': pal}

        front_text = texts['src/data/trainer_graphics/front_pic_tables.h']
        front_palettes = dict(re.findall(r'TRAINER_PAL\((\w+),\s*(\w+)\)', front_text))
        for picture, symbol in re.findall(r'TRAINER_SPRITE\((\w+),\s*(\w+),', front_text):
            label = _name(picture.removeprefix('LEADER_').removeprefix('ELITE_FOUR_'))
            category = 'gyms' if picture.startswith('LEADER_') else 'trainers'
            add(symbol, front_palettes.get(picture), 'battle_front', label + ' · Battle portrait',
                64, 64, category, ['Trainer battle portrait'], trainer_id='TRAINER_PIC_' + picture)
        back_text = texts['src/data/trainer_graphics/back_pic_tables.h']
        back_palettes = dict(re.findall(r'TRAINER_BACK_PAL\((\w+),\s*(\w+)\)', back_text))
        for picture, symbol in re.findall(
                r'\[TRAINER_BACK_PIC_(\w+)\]\s*=\s*\{\s*\.data\s*=\s*(?:\(const u32 \*\))?(\w+)', back_text):
            add(symbol, back_palettes.get(picture), 'battle_back', _name(picture) + ' · Battle back',
                64, 64, 'trainers', ['Battle back / throwing animation'], trainer_id='TRAINER_BACK_PIC_' + picture)

        # A shared portrait must tell users exactly which trainer definitions change.
        trainer_text = texts['src/data/trainers.h']
        trainer_headers = list(re.finditer(r'\[(TRAINER_\w+)\]\s*=\s*\{', trainer_text))
        for index, header in enumerate(trainer_headers):
            end = trainer_headers[index + 1].start() if index + 1 < len(trainer_headers) else len(trainer_text)
            body = trainer_text[header.end():end]
            picture = re.search(r'\.trainerPic\s*=\s*(TRAINER_PIC_\w+)', body)
            name = re.search(r'\.trainerName\s*=\s*_\("([^"]*)"\)', body)
            if picture and picture[1] in self._trainer_pics and header[1] != 'TRAINER_NONE':
                row = {'id': header[1], 'name': name[1] if name else _name(header[1][8:])}
                assets[self._trainer_pics[picture[1]]['path']]['trainer_uses'].append(row)

        pics = {}
        for table, body in re.findall(r'SpriteFrameImage (\w+)\[\]\s*=\s*\{(.*?)\};',
                                      texts[OBJECT + 'object_event_pic_tables.h'], re.S):
            frames = [(symbol, int(w) * 8, int(h) * 8) for symbol, w, h in re.findall(
                r'overworld_frame\((\w+),\s*(\d+),\s*(\d+),', body)]
            frames += [(symbol, None, None) for symbol in re.findall(r'obj_frame_tiles\((\w+)\)', body)]
            pics[table] = set(frames)
        ids = defaultdict(list)
        for object_id, info in re.findall(r'\[(OBJ_EVENT_GFX_\w+)\]\s*=\s*&(\w+)',
                                         texts[OBJECT + 'object_event_graphics_info_pointers.h']):
            ids[info].append(object_id)
        movement = texts['src/event_object_movement.c']
        tags = {tag: symbol for symbol, tag in re.findall(r'\{\s*(\w+),\s*(OBJ_EVENT_PAL_TAG_\w+)\s*\}', movement)}
        reflection_tables = {table: re.findall(r'OBJ_EVENT_PAL_TAG_\w+', body)
                             for table, body in re.findall(r'const u16 (sReflectionPaletteTags_\w+)\[\]\s*=\s*\{(.*?)\};', movement, re.S)}
        reflections = {tag: reflection_tables[table][0] for tag, table in re.findall(
            r'\{\s*(OBJ_EVENT_PAL_TAG_\w+),\s*(sReflectionPaletteTags_\w+)\s*\}', movement)
            if reflection_tables.get(table)}
        for info, body in re.findall(r'ObjectEventGraphicsInfo (\w+)\s*=\s*\{(.*?)\};',
                                     texts[OBJECT + 'object_event_graphics_info.h'], re.S):
            fields = _fields(body)
            tag = fields.get('paletteTag')
            palette_tags = [(tag, 'Link room' if 'Link' in info else 'Normal')]
            if fields.get('disableReflectionPaletteLoad') != 'TRUE':
                reflection = reflections.get(tag)
                # NPC reflections occupy fixed slots even without a paired table.
                if re.fullmatch(r'OBJ_EVENT_PAL_TAG_NPC_[1-4]', tag or ''):
                    reflection = tag + '_REFLECTION'
                if reflection and reflection != tag:
                    palette_tags.append((reflection, 'Reflection'))
            for symbol, fw, fh in sorted(pics.get(fields.get('images'), ())):
                path = declarations.get(symbol, {}).get('path', '')
                if not path:
                    continue
                # Pokémon overworld graphics sometimes share NPC palettes.
                if '/pics/pokemon/' in path:
                    for pal_tag, _ in palette_tags:
                        pal = declarations.get(tags.get(pal_tag), {}).get('path')
                        if pal:
                            self._protected_palettes[pal].append({'path': path, 'label': _name(Path(path).stem), 'protected': True})
                    continue
                if not _editable_png(path):
                    continue
                label = _name(Path(path).stem)
                category = 'gyms' if '/gym_leaders/' in path else 'npcs' if '/people/' in path else 'objects'
                fw, fh = fw or int(fields.get('width', 16)), fh or int(fields.get('height', 32))
                for pal_tag, palette_label in palette_tags:
                    add(symbol, tags.get(pal_tag), 'overworld', label + ' · Overworld', fw, fh,
                        category, ['Overworld object / NPC'], palette_label=palette_label, object_ids=ids[info])
        for palette, entries in self._protected_palettes.items():
            self._protected_palettes[palette] = list({p['path']: p for p in entries}.values())
        # Retain compiled but unreferenced human sheets without inventing a game
        # palette binding. Their embedded palette is explicitly stored artwork.
        for symbol, declaration in declarations.items():
            path = declaration['path']
            if not path.startswith('graphics/object_events/pics/people/') or path in assets or not self._path(path).is_file():
                continue
            frame = re.search(r'-mwidth (\d+) -mheight (\d+)', declaration['options'])
            if not frame:
                continue
            label = _name(Path(path).stem)
            assets[path] = dict(path=path, label=label + ' · Stored unused sheet', character=label,
                                variant='Emerald', kind='overworld', category='npcs',
                                frame_width=int(frame[1]) * 8, frame_height=int(frame[2]) * 8,
                                palette_options=[{'path': path, 'label': 'Stored PNG palette'}],
                                uses=['Stored artwork; no original object-event mapping'], symbols=[symbol],
                                trainer_pic_ids=[], object_graphics_ids=[], trainer_uses=[], archived=True)
        self._assets = dict(sorted(assets.items(), key=lambda pair: (
            pair[1]['category'], pair[1]['character'], pair[1]['kind'], pair[0])))
        self._metadata_key = key
        existence = {d['path']: (self.source / d['path']).is_file() for d in declarations.values()}
        _DISCOVERY_CACHE[cache_key] = (self._assets, self._protected_palettes, self._trainer_pics, existence)
        while len(_DISCOVERY_CACHE) > 4:
            _DISCOVERY_CACHE.popitem(last=False)
        return self._assets

    def _sharing(self, palette_path, current_path):
        return super()._sharing(palette_path, current_path) + self._protected_palettes.get(palette_path, [])

    def _row(self, asset, palette_path=None):
        row = super()._row(asset, palette_path)
        row['preview_url'] = '/api/appearance/preview?' + urlencode({'path': asset['path'], 'palette_path': row['palette_path']})
        row['edit_url'] = '/player?' + urlencode({'path': asset['path']})
        for option in row['palette_options']:
            option['locked'] = bool(self._protected_palettes.get(option['path']))
        row['palette_locked'] = bool(self._protected_palettes.get(row['palette_path']))
        row['palette_lock_reason'] = ('This palette also colors Pokémon. Paint with its existing colors to keep Pokémon unchanged.' if row['palette_locked'] else '')
        row['usage_count'] = len(asset['trainer_uses'])
        return row

    def _revision(self, asset):
        cache = getattr(self, '_revision_cache', None)
        if cache is None:
            return super()._revision(asset)
        dependencies = {asset['path']} | {p['path'] for p in asset['palette_options']}
        for palette in asset['palette_options']:
            dependencies.update(item['path'] for item in self._sharing(palette['path'], asset['path']))
        digest = hashlib.sha256(self._metadata_key.encode())
        for path in sorted(dependencies):
            if path not in cache:
                cache[path] = self._path(path).read_bytes()
            digest.update(path.encode() + b'\0' + cache[path] + b'\0')
        return digest.hexdigest(), sorted(dependencies)

    def trainer_pictures(self):
        # Trainer selectors call this often; they need only two trainer tables,
        # not object-event discovery or the complete editable artwork catalog.
        def read(relative):
            path = self._path(relative)
            return path.read_text(encoding='utf-8') if path.is_file() else ''
        declarations = _declarations(read('src/data/graphics/trainers.h'))
        table = read('src/data/trainer_graphics/front_pic_tables.h')
        palettes = dict(re.findall(r'TRAINER_PAL\((\w+),\s*(\w+)\)', table))
        result = []
        for picture, symbol in re.findall(r'TRAINER_SPRITE\((\w+),\s*(\w+),', table):
            path = declarations.get(symbol, {}).get('path')
            pal = declarations.get(palettes.get(picture), {}).get('path')
            if not path or not pal or not path.startswith('graphics/trainers/front_pics/'):
                continue
            if not self._path(path).is_file() or not self._path(pal).is_file():
                continue
            picture = 'TRAINER_PIC_' + picture
            binding = {'path': path, 'palette_path': pal}
            digest = hashlib.sha256()
            for path in (binding['path'], binding['palette_path']):
                digest.update(self._path(path).read_bytes())
            query = {**binding, 'v': digest.hexdigest()[:16]}
            result.append({'id': picture, 'name': _name(picture.removeprefix('TRAINER_PIC_')),
                           **binding, 'preview_url': '/api/appearance/preview?' + urlencode(query),
                           'edit_url': '/player?' + urlencode({'path': binding['path']})})
        return result

    def catalog(self):
        # Shared NPC palettes otherwise reread the same sheets hundreds of times.
        self._revision_cache = {}
        try:
            result = super().catalog()
        finally:
            self._revision_cache = None
        by_graphic = defaultdict(list)
        # Usage listings do not participate in artwork revision tokens: editing an
        # NPC's dialogue or moving it does not make an unrelated sprite edit stale.
        for path in sorted((self.source / 'data/maps').glob('*/map.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                continue
            for event in data.get('object_events', []):
                graphic = event.get('graphics_id')
                if isinstance(graphic, str):
                    by_graphic[graphic].append({'map': path.parent.name, 'local_id': event.get('local_id'),
                                               'x': event.get('x'), 'y': event.get('y'), 'script': event.get('script')})
        for asset in result['assets']:
            asset['map_uses'] = [use for graphic in asset['object_graphics_ids'] for use in by_graphic[graphic]]
            asset['usage_count'] += len(asset['map_uses'])
        result['categories'] = dict(Counter(a['category'] for a in result['assets']))
        result['sharing_note'] = 'Editing a shared sheet changes every listed character using it. Shared palettes recolor every listed sheet.'
        result['scope_note'] = 'Player art, every trainer battle portrait/back sheet, and all mapped non-Pokémon people and object sheets. Pokémon graphics are protected.'
        from character_groups import build_groups
        result['groups'] = build_groups(self.source, result['assets'])
        return result

    def plan_save(self, body):
        if isinstance(body, dict):
            asset = self.get_asset(body.get('path'), body.get('palette_path'))
            if asset['palette_locked'] and body.get('palette', asset['palette']) != asset['palette']:
                raise ValueError('This palette also colors Pokémon and cannot be changed. Use its existing colors for this character.')
        return super().plan_save(body)
