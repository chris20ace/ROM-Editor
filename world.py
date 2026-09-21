"""Source-backed Emerald maps, tilesets and object previews.

This module never writes source files. Edit methods return validated transaction plans.
Binary definitions follow include/global.fieldmap.h and src/field_camera.c.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import struct
from urllib.parse import urlencode

from PIL import Image, ImageDraw


EVENT_KEYS = ('object_events', 'warp_events', 'coord_events', 'bg_events')
LAYOUTS = 'data/layouts/layouts.json'
GROUPS = 'data/maps/map_groups.json'
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*$')


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def words(data):
    if len(data) % 2:
        raise ValueError('Invalid odd-length map data')
    return list(struct.unpack('<' + 'H' * (len(data) // 2), data))


def pack_words(values):
    return struct.pack('<' + 'H' * len(values), *values)


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}')
    return value


class World:
    def __init__(self, source):
        self.source = Path(source).resolve()
        self._cache = {}
        self._atlas_cache = {}
        self._catalog = None
        self._symbols = None

    def _path(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source):
            raise ValueError('Source path must stay inside the project')
        return path

    def _read(self, relative):
        path = self._path(relative)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(relative)
        if cached and cached[0] == signature:
            return cached[1]
        result = path.read_bytes()
        self._cache[relative] = (signature, result)
        return result

    def _text(self, relative):
        return self._read(relative).decode('utf-8')

    def _json(self, relative):
        return json.loads(self._read(relative))

    def _maps(self):
        return {p.parent.name: self._json(p.relative_to(self.source).as_posix())
                for p in sorted((self.source / 'data/maps').glob('*/map.json'))}

    def _lookup(self, name):
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise ValueError('Invalid map name')
        relative = f'data/maps/{name}/map.json'
        if not self._path(relative).is_file():
            raise ValueError('Map does not exist')
        data = self._json(relative)
        layout = next((x for x in self._json(LAYOUTS)['layouts'] if x['id'] == data['layout']), None)
        if layout is None:
            raise ValueError(f'Map {name} has an unknown layout')
        return data, layout

    def _tilesets(self):
        headers = self._text('src/data/tilesets/headers.h')
        graphics = self._text('src/data/tilesets/graphics.h') + '\n' + self._text('src/graphics.c')
        metatiles = self._text('src/data/tilesets/metatiles.h')
        paths = dict(re.findall(r'\b(\w+)\s*\[\s*\]\s*=\s*INC(?:GFX|BIN)_\w+\("([^"]+)"', graphics + metatiles))
        palette_sets = {label: re.findall(r'"([^"]+\.pal)"', body)
                        for label, body in re.findall(r'\b(\w+)\s*\[\s*\]\s*\[16\]\s*=\s*\{(.*?)\};', graphics, re.S)}
        result = {}
        for label, body in re.findall(r'const struct Tileset (\w+)\s*=\s*\{(.*?)\};', headers, re.S):
            fields = dict(re.findall(r'\.(\w+)\s*=\s*(\w+)', body))
            result[label] = {'id': label, 'name': re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', label.removeprefix('gTileset_')),
                             'secondary': fields.get('isSecondary') == 'TRUE',
                             'tiles': paths[fields['tiles']], 'palettes': palette_sets[fields['palettes']],
                             'metatiles': paths[fields['metatiles']], 'attributes': paths[fields['metatileAttributes']]}
        return result

    def list_maps(self):
        layouts = {x['id']: x for x in self._json(LAYOUTS)['layouts']}
        groups = self._json(GROUPS)
        membership = {name: group for group in groups['group_order'] for name in groups[group]}
        maps = []
        for name, data in self._maps().items():
            layout = layouts[data['layout']]
            maps.append({'name': name, 'id': data['id'], 'layout': data['layout'],
                         'width': layout['width'], 'height': layout['height'],
                         'group': membership.get(name), 'map_type': data['map_type'],
                         'region_map_section': data['region_map_section']})
        tilesets = [{k: t[k] for k in ('id', 'name', 'secondary')} for t in self._tilesets().values()]
        return {'maps': maps, 'tilesets': tilesets, 'groups': groups['group_order'],
                'limits': {'width': 255, 'height': 255, 'buffer_cells': 10240,
                           'padding_width': 15, 'padding_height': 14, 'border_width': 2, 'border_height': 2}}

    def _events_owner(self, name, maps):
        visited = set()
        while maps[name].get('shared_events_map'):
            if name in visited:
                raise ValueError('Circular shared event reference')
            visited.add(name)
            name = maps[name]['shared_events_map']
            if name not in maps:
                raise ValueError('Missing shared event map')
        return name

    def _revision(self, name, data, layout, owner):
        # Including the complete registry catches concurrent resize/tileset changes.
        paths = {f'data/maps/{name}/map.json', f'data/maps/{owner}/map.json', LAYOUTS,
                 layout['blockdata_filepath'], layout['border_filepath']}
        digest = hashlib.sha256()
        for relative in sorted(paths):
            digest.update(relative.encode())
            digest.update(self._read(relative))
        return digest.hexdigest()

    def get_map(self, name, primary=None, secondary=None):
        data, layout = self._lookup(name)
        maps = self._maps()
        owner = self._events_owner(name, maps)
        revision = self._revision(name, data, layout, owner)
        if owner != name:
            for key in EVENT_KEYS:
                data[key] = deepcopy(maps[owner].get(key, []))
        primary = primary or layout['primary_tileset']
        secondary = secondary or layout['secondary_tileset']
        _, meta = self._pair(primary, secondary)
        query = '?' + urlencode({'primary': primary, 'secondary': secondary})
        cells = words(self._read(layout['blockdata_filepath']))
        if len(cells) != layout['width'] * layout['height']:
            raise ValueError('Map binary size does not match its layout')
        return {'name': name, 'id': data['id'], 'width': layout['width'], 'height': layout['height'],
                'cells': cells, 'border': words(self._read(layout['border_filepath'])),
                'border_width': 2, 'border_height': 2, 'map': data, 'layout': layout,
                'revision': revision, 'shared_with': [n for n, d in maps.items() if n != name and d['layout'] == data['layout']],
                'events_owner': owner, 'events_shared_with': [n for n in maps if n != name and self._events_owner(n, maps) == owner],
                'scripts_path': f"data/maps/{data.get('shared_scripts_map', name)}/scripts.inc",
                'tileset': {**meta, 'primary': primary, 'secondary': secondary,
                            'atlas_url': f'/api/world/maps/{name}/atlas.png' + query},
                'render_url': f'/api/world/maps/{name}/render.png'}

    def _palette(self, relative):
        lines = self._text(relative).splitlines()
        if lines[:2] != ['JASC-PAL', '0100'] or int(lines[2]) < 16:
            raise ValueError(f'Unsupported palette: {relative}')
        colors = [tuple(map(int, line.split())) for line in lines[3:19]]
        return colors

    def _pair(self, primary, secondary):
        tilesets = self._tilesets()
        if primary not in tilesets or tilesets[primary]['secondary']:
            raise ValueError('Unknown primary tileset')
        if secondary not in tilesets or not tilesets[secondary]['secondary']:
            raise ValueError('Unknown secondary tileset')
        p, s = tilesets[primary], tilesets[secondary]
        paths = [t[k] for t in (p, s) for k in ('tiles', 'metatiles', 'attributes')]
        paths += p['palettes'][:6] + s['palettes'][6:13]
        signature = tuple(hashlib.sha256(self._read(path)).digest() for path in paths)
        key = (primary, secondary, signature)
        if key in self._atlas_cache:
            return self._atlas_cache[key]
        palettes = [self._palette(path) for path in p['palettes'][:6] + s['palettes'][6:13]]
        sheets = [Image.open(io.BytesIO(self._read(t['tiles']))) for t in (p, s)]
        if any(sheet.mode != 'P' for sheet in sheets):
            raise ValueError('Tiles must be indexed-color PNGs')
        tile_cache = {}

        def tile(descriptor):
            if descriptor in tile_cache:
                return tile_cache[descriptor]
            tile_id, palette_id = descriptor & 1023, descriptor >> 12
            sheet = sheets[0 if tile_id < 512 else 1]
            local_id = tile_id if tile_id < 512 else tile_id - 512
            across = sheet.width // 8
            x, y = (local_id % across) * 8, (local_id // across) * 8
            result = Image.new('RGBA', (8, 8))
            if y < sheet.height and palette_id < len(palettes):
                indices = sheet.crop((x, y, x + 8, y + 8)).tobytes()
                result.putdata([(*palettes[palette_id][index % 16], 0 if index % 16 == 0 else 255) for index in indices])
                if descriptor & 0x400:
                    result = result.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if descriptor & 0x800:
                    result = result.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            tile_cache[descriptor] = result
            return result

        metatile_words = [words(self._read(t['metatiles'])) for t in (p, s)]
        attributes = [words(self._read(t['attributes'])) for t in (p, s)]
        counts = [len(m) // 8 for m in metatile_words]
        if any(len(m) % 8 for m in metatile_words) or any(n != len(a) for n, a in zip(counts, attributes)):
            raise ValueError('Invalid metatile table size')
        if any(count > 512 for count in counts):
            raise ValueError('Tileset exceeds Emerald metatile bank')
        count = 512 + counts[1]
        atlas = Image.new('RGBA', (16 * 16, ((count + 15) // 16) * 16))
        behavior_names = re.findall(r'\b(MB_[A-Z_0-9]+)\s*(?:,|=)', self._text('include/constants/metatile_behaviors.h'))
        metadata = []
        for metatile_id in range(count):
            bank, local_id = (0, metatile_id) if metatile_id < 512 else (1, metatile_id - 512)
            valid = local_id < counts[bank]
            attr = attributes[bank][local_id] if valid else 0
            behavior, layer_type = attr & 255, attr >> 12
            metadata.append({'id': metatile_id, 'attribute': attr, 'behavior': behavior, 'layer_type': layer_type,
                             'behavior_name': behavior_names[behavior] if behavior < len(behavior_names) else str(behavior), 'valid': valid})
            if not valid:
                continue
            result = Image.new('RGBA', (16, 16), (*palettes[0][0], 255))
            # BG3 contains tile 0x3014 for NORMAL metatiles, otherwise lower four
            # descriptors are on BG3. BG priorities change sprite occlusion only.
            if layer_type == 0:
                for q in range(4):
                    result.alpha_composite(tile(0x3014), ((q % 2) * 8, (q // 2) * 8))
            descriptors = metatile_words[bank][local_id * 8:local_id * 8 + 8]
            for i, descriptor in enumerate(descriptors):
                q = i % 4
                result.alpha_composite(tile(descriptor), ((q % 2) * 8, (q // 2) * 8))
            atlas.paste(result, ((metatile_id % 16) * 16, (metatile_id // 16) * 16))
        revision = hashlib.sha256(b''.join(signature)).hexdigest()
        result = (atlas, {'count': count, 'columns': 16, 'tile_size': 16, 'primary_count': counts[0], 'revision': revision,
                          'secondary_count': counts[1], 'metatiles': metadata})
        # A bounded cache avoids retaining every tileset variant after external edits.
        if len(self._atlas_cache) >= 80:
            self._atlas_cache.clear()
        self._atlas_cache[key] = result
        return result

    def atlas(self, name, primary=None, secondary=None):
        _, layout = self._lookup(name)
        atlas, _ = self._pair(primary or layout['primary_tileset'], secondary or layout['secondary_tileset'])
        return png(atlas)

    def render_map(self, name, primary=None, secondary=None, max_size=None):
        if max_size is not None:
            integer(max_size, 32, 1024, 'Preview maximum size')
        _, layout = self._lookup(name)
        atlas, meta = self._pair(primary or layout['primary_tileset'], secondary or layout['secondary_tileset'])
        width, height = layout['width'], layout['height']
        result = Image.new('RGBA', (width * 16, height * 16), '#da45be')
        for i, value in enumerate(words(self._read(layout['blockdata_filepath']))):
            tile_id = value & 1023
            if tile_id < meta['count']:
                x, y = (tile_id % 16) * 16, (tile_id // 16) * 16
                result.paste(atlas.crop((x, y, x + 16, y + 16)), ((i % width) * 16, (i // width) * 16))
        if max_size is not None and max(result.size) > max_size:
            scale = max_size / max(result.size)
            size = tuple(max(1, round(side * scale)) for side in result.size)
            result = result.resize(size, Image.Resampling.NEAREST)
        return png(result)

    def _build_object_catalog(self):
        prefix = 'src/data/object_events/'
        pointers = dict(re.findall(r'\[(OBJ_EVENT_GFX_\w+)\]\s*=\s*&(\w+)', self._text(prefix + 'object_event_graphics_info_pointers.h')))
        infos = {label: dict(re.findall(r'\.(\w+)\s*=\s*([^,\n]+)', body))
                 for label, body in re.findall(r'struct ObjectEventGraphicsInfo (\w+)\s*=\s*\{(.*?)\};', self._text(prefix + 'object_event_graphics_info.h'), re.S)}
        pic_text = self._text(prefix + 'object_event_pic_tables.h') + '\n' + self._text(prefix + 'berry_tree_graphics_tables.h')
        pics = {}
        for label, body in re.findall(r'SpriteFrameImage (\w+)\[\]\s*=\s*\{(.*?)\};', pic_text, re.S):
            frame = re.search(r'overworld_frame\((\w+),\s*(\d+),\s*(\d+),\s*(\d+)\)', body)
            simple = re.search(r'obj_frame_tiles\((\w+)\)', body)
            if frame:
                pics[label] = (frame.group(1), int(frame.group(4)), int(frame.group(2)) * 8, int(frame.group(3)) * 8)
            elif simple:
                pics[label] = (simple.group(1), 0, None, None)
        graphic_text = self._text(prefix + 'object_event_graphics.h')
        gfx_paths = dict(re.findall(r'\b(\w+)\[\]\s*=\s*INCGFX_\w+\("([^"]+)"', graphic_text))
        pal_tags = dict((tag, symbol) for symbol, tag in re.findall(r'\{\s*(\w+),\s*(OBJ_EVENT_PAL_TAG_\w+)\s*\}', self._text('src/event_object_movement.c')))
        constants = re.findall(r'^#define (OBJ_EVENT_GFX_\w+)\s+([^\r\n]+)', self._text('include/constants/event_objects.h'), re.M)
        result = {}
        for identifier, value in constants:
            if identifier == 'OBJ_EVENT_GFX_VARS':
                continue
            info = infos.get(pointers.get(identifier), {})
            frame = pics.get(info.get('images'))
            path = gfx_paths.get(frame[0]) if frame else None
            palette = gfx_paths.get(pal_tags.get(info.get('paletteTag')))
            width, height = int(info.get('width', 16)), int(info.get('height', 16))
            result[identifier] = {'id': identifier, 'name': identifier.removeprefix('OBJ_EVENT_GFX_').replace('_', ' ').title(),
                                  'width': width, 'height': height, 'preview_available': bool(path),
                                  'preview_url': f'/api/world/objects/{identifier}.png',
                                  'dynamic': identifier.startswith('OBJ_EVENT_GFX_VAR_'),
                                  '_path': path, '_palette': palette,
                                  '_frame': frame[1] if frame else 0,
                                  '_frame_width': frame[2] if frame else None, '_frame_height': frame[3] if frame else None}
        self._catalog = result

    def object_catalog(self):
        if self._catalog is None:
            self._build_object_catalog()
        return {'objects': [{k: v for k, v in row.items() if not k.startswith('_')} for row in self._catalog.values()],
                'movement_types': sorted(s for s in self._constant_symbols() if s.startswith('MOVEMENT_TYPE_'))}

    def object_sprite(self, identifier):
        if self._catalog is None:
            self._build_object_catalog()
        if identifier not in self._catalog:
            raise ValueError('Unknown object graphics')
        info = self._catalog[identifier]
        if info['_path']:
            sheet = Image.open(io.BytesIO(self._read(info['_path'])))
            w, h, frame = info['_frame_width'] or info['width'], info['_frame_height'] or info['height'], info['_frame']
            across = max(1, sheet.width // w)
            x, y = (frame % across) * w, (frame // across) * h
            indexed = sheet.crop((x, y, x + w, y + h))
            if sheet.mode == 'P':
                palette = self._palette(info['_palette']) if info['_palette'] else [tuple(sheet.getpalette()[i:i + 3]) for i in range(0, 48, 3)]
                result = Image.new('RGBA', indexed.size)
                result.putdata([(*palette[v % 16], 0 if v % 16 == 0 else 255) for v in indexed.tobytes()])
            else:
                result = indexed.convert('RGBA')
        else:
            result = Image.new('RGBA', (16, 16))
            draw = ImageDraw.Draw(result)
            draw.rounded_rectangle((1, 1, 14, 14), 3, fill='#825ce6', outline='white')
            draw.text((5, 2), '?', fill='white')
        return png(result)

    def _constant_symbols(self):
        if self._symbols is None:
            text = '\n'.join(p.read_text(encoding='utf-8') for p in (self.source / 'include/constants').glob('*.h'))
            text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
            definitions = re.findall(r'^\s*#define\s+([A-Z][A-Z_0-9]+)\b', text, re.M)
            enum_members = [name for block in re.findall(r'\benum\s*(?:\w+)?\s*\{(.*?)\}', text, re.S)
                            for name in re.findall(r'(?:^|,)\s*([A-Z][A-Z_0-9]+)\b', block)]
            self._symbols = set(definitions + enum_members)
        return self._symbols

    def _symbol(self, value, label, prefix=None, allow_number=True):
        if allow_number and ((type(value) is int and 0 <= value <= 65535) or
                             (isinstance(value, str) and re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', value) and int(value, 0 if value.startswith('0x') else 10) <= 65535)):
            return
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ValueError(f'{label} must be a source constant')
        if prefix and not value.startswith(prefix):
            raise ValueError(f'{label} must use a {prefix} constant')
        if value not in self._constant_symbols():
            raise ValueError(f'Unknown {label}: {value}')

    def _validate_dimensions(self, width, height):
        integer(width, 1, 255, 'Map width')
        integer(height, 1, 255, 'Map height')
        if (width + 15) * (height + 14) > 10240:
            raise ValueError('Map exceeds Emerald buffer: (width + 15) × (height + 14) must be at most 10240')

    def _script_symbols(self):
        labels = set()
        for extension in ('*.inc', '*.s'):
            for path in (self.source / 'data').rglob(extension):
                labels.update(re.findall(r'^([A-Za-z_]\w*)::?', self._text(path.relative_to(self.source).as_posix()), re.M))
        return labels

    def _validate_events(self, data, original, width, height, maps, resized=False):
        all_ids = {d['id']: d for d in maps.values()}
        local_ids = set()
        script_symbols = None
        for key in EVENT_KEYS:
            events = data.get(key, [])
            if not isinstance(events, list) or len(events) > (126 if key == 'object_events' else 255):
                raise ValueError(f'{key} must be a list within the game event limit')
            old_events = original.get(key, [])
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    raise ValueError(f'{key} event must be an object')
                if event in old_events and not resized:
                    continue
                label = f'{key} #{index + 1}'
                # Existing off-map events are legitimate scripted actors, but a resize
                # must not silently strand ordinary on-map events outside the new map.
                for axis, extent in [('x', width), ('y', height)]:
                    value = event.get(axis)
                    integer(value, -32768, 32767, f'{label} {axis}')
                    if event not in old_events and not 0 <= value < extent:
                        raise ValueError(f'{label} {axis} is outside the map')
                    if resized and event in old_events and value >= extent:
                        raise ValueError(f'Resize leaves {label} outside the map; move or delete it first')
                integer(event.get('elevation'), 0, 15, f'{label} elevation')
                if 'script' in event and not (event['script'] in ('0', '0x0', 'NULL', 0) or (isinstance(event['script'], str) and IDENTIFIER.fullmatch(event['script']))):
                    raise ValueError(f'{label} script must be a script label or 0')
                if event.get('script') not in (None, '0', '0x0', 'NULL', 0) and event.get('script') not in {e.get('script') for e in old_events}:
                    if script_symbols is None:
                        script_symbols = self._script_symbols()
                    if event['script'] not in script_symbols:
                        raise ValueError(f"Unknown script {event['script']}; create it in the story editor first")
                if key == 'object_events':
                    for required in ('graphics_id', 'movement_type', 'trainer_type', 'movement_range_x', 'movement_range_y', 'trainer_sight_or_berry_tree_id', 'script', 'flag'):
                        if required not in event:
                            raise ValueError(f'{label} needs {required}')
                    if event.get('type', 'object') != 'object':
                        raise ValueError('Emerald supports normal object events only')
                    self._symbol(event['graphics_id'], 'graphics', 'OBJ_EVENT_GFX_', False)
                    self._symbol(event['movement_type'], 'movement', 'MOVEMENT_TYPE_', False)
                    self._symbol(event['trainer_type'], 'trainer type', 'TRAINER_TYPE_')
                    self._symbol(event['trainer_sight_or_berry_tree_id'], 'trainer range')
                    self._symbol(event['flag'], 'flag', 'FLAG_')
                    for field in ('movement_range_x', 'movement_range_y'):
                        integer(event[field], 0, 15, field)
                    if event.get('local_id'):
                        if not isinstance(event['local_id'], str) or not IDENTIFIER.fullmatch(event['local_id']):
                            raise ValueError('Object local_id must be an identifier')
                elif key == 'warp_events':
                    destination = event.get('dest_map')
                    if destination not in all_ids and destination not in ('MAP_DYNAMIC', 'MAP_UNDEFINED'):
                        raise ValueError(f'{label} has an unknown destination map')
                    warp_id = event.get('dest_warp_id')
                    if warp_id in ('WARP_ID_SECRET_BASE', 'WARP_ID_DYNAMIC'):
                        if destination != 'MAP_DYNAMIC':
                            raise ValueError('Special warp IDs require MAP_DYNAMIC')
                        continue
                    if warp_id == 'WARP_ID_NONE':
                        warp_id = -1
                    if isinstance(warp_id, str) and re.fullmatch(r'-?\d+', warp_id):
                        warp_id = int(warp_id)
                    integer(warp_id, -1, 255, f'{label} destination warp')
                    if destination in all_ids and warp_id != -1:
                        target = data if destination == data['id'] else all_ids[destination]
                        owner = target.get('shared_events_map')
                        if owner:
                            target = maps[owner]
                        if warp_id >= len(target.get('warp_events', [])):
                            raise ValueError(f'{label} destination warp {warp_id} does not exist')
                elif key == 'coord_events':
                    kind = event.get('type')
                    if kind == 'trigger':
                        for field in ('var', 'var_value', 'script'):
                            if field not in event:
                                raise ValueError(f'{label} needs {field}')
                        self._symbol(event['var'], 'variable', 'VAR_')
                        self._symbol(event['var_value'], 'variable value')
                    elif kind == 'weather':
                        self._symbol(event.get('weather'), 'weather')
                    else:
                        raise ValueError('Coordinate event type must be trigger or weather')
                else:
                    kind = event.get('type')
                    if kind == 'sign':
                        self._symbol(event.get('player_facing_dir'), 'facing direction', 'BG_EVENT_PLAYER_FACING_')
                        if 'script' not in event:
                            raise ValueError('Sign requires a script')
                    elif kind == 'hidden_item':
                        self._symbol(event.get('item'), 'item', 'ITEM_', False)
                        self._symbol(event.get('flag'), 'hidden item flag', 'FLAG_', False)
                    elif kind == 'secret_base':
                        self._symbol(event.get('secret_base_id'), 'secret base')
                    else:
                        raise ValueError('Background type must be sign, hidden_item, or secret_base')
            for event in events if key == 'object_events' else []:
                if event.get('local_id'):
                    if event['local_id'] in local_ids:
                        raise ValueError('Object local_id values must be unique')
                    local_ids.add(event['local_id'])
        connections = data.get('connections')
        if connections is not None and (not isinstance(connections, list) or len(connections) > 255):
            raise ValueError('Connections must be null or a list of at most 255 entries')
        for connection in connections or []:
            if not isinstance(connection, dict) or connection.get('map') not in all_ids:
                raise ValueError('Connection destination must be an existing map')
            if connection.get('direction') not in ('up', 'down', 'left', 'right', 'dive', 'emerge'):
                raise ValueError('Invalid connection direction')
            integer(connection.get('offset'), -32768, 32767, 'Connection offset')

    def plan_save(self, name, body, pixel_planner=None):
        if not isinstance(body, dict):
            raise ValueError('Map edit must be an object')
        current = self.get_map(name)
        if body.get('revision') != current['revision']:
            raise ValueError('Map changed since it was opened. Reload before saving.')
        width, height = body.get('width', current['width']), body.get('height', current['height'])
        self._validate_dimensions(width, height)
        cells, border = body.get('cells', current['cells']), body.get('border', current['border'])
        if not isinstance(cells, list) or len(cells) != width * height:
            raise ValueError('Map cell count must equal width × height')
        if not isinstance(border, list) or len(border) != 4:
            raise ValueError('Emerald borders must contain exactly four cells (2 × 2)')
        for value in cells + border:
            integer(value, 0, 65535, 'Map block')
        original, old_layout = self._lookup(name)
        layout = deepcopy(old_layout)
        incoming_layout = body.get('layout', {})
        if not isinstance(incoming_layout, dict):
            raise ValueError('Layout must be an object')
        for key in ('id', 'name', 'blockdata_filepath', 'border_filepath'):
            if key in incoming_layout and incoming_layout[key] != old_layout[key]:
                raise ValueError(f'Layout {key} cannot be changed by a map edit')
        for key in ('primary_tileset', 'secondary_tileset'):
            layout[key] = body.get(key, incoming_layout.get(key, layout[key]))
        layout.update(width=width, height=height)
        _, metadata = self._pair(layout['primary_tileset'], layout['secondary_tileset'])
        valid_ids = {m['id'] for m in metadata['metatiles'] if m['valid']}
        old_values = current['cells'] + current['border']
        for i, value in enumerate(cells + border):
            if (value & 1023) not in valid_ids and (i >= len(old_values) or old_values[i] != value or layout != old_layout):
                raise ValueError(f'Metatile {value & 1023} does not exist in this tileset pair')
        patches = body.get('pixel_patches', {})
        if not isinstance(patches, dict):
            raise ValueError('Pixel patches must be an object keyed by map cell index')
        owns_pixel_plan = False
        if patches:
            if any(layout[key] != old_layout[key] for key in ('primary_tileset', 'secondary_tileset')):
                raise ValueError('Save tileset changes before cutting or moving pixel pieces')
            if body.get('tileset_revision') != metadata['revision']:
                raise ValueError('Tile artwork changed since it was opened. Reload before saving pixel pieces.')
            if pixel_planner is None:
                from world_pixels import PixelPatchPlanner
                pixel_planner = PixelPatchPlanner(self)
                owns_pixel_plan = True
            cells = pixel_planner.compose(layout['primary_tileset'], layout['secondary_tileset'], cells, patches)
        changed_layout = cells != current['cells'] or border != current['border'] or layout != old_layout
        if changed_layout and current['shared_with'] and body.get('confirm_shared') is not True:
            raise ValueError('This layout is shared. Confirm shared changes to update: ' + ', '.join(current['shared_with']))
        incoming = body.get('map', {})
        if not isinstance(incoming, dict):
            raise ValueError('Map properties must be an object')
        for key in ('id', 'name', 'layout', 'shared_events_map', 'shared_scripts_map'):
            if key in incoming and incoming[key] != original.get(key):
                raise ValueError(f'Map {key} cannot be changed by a map edit')
        edited = deepcopy(current['map'])
        edited.update(incoming)
        for key in ('requires_flash', 'allow_cycling', 'allow_escaping', 'allow_running', 'show_map_name'):
            if type(edited.get(key)) is not bool:
                raise ValueError(f'{key} must be true or false')
        for key, prefix in [('music', 'MUS_'), ('weather', 'WEATHER_'), ('map_type', 'MAP_TYPE_'), ('region_map_section', 'MAPSEC_'), ('battle_scene', 'MAP_BATTLE_SCENE_')]:
            if edited.get(key) != original.get(key):
                self._symbol(edited.get(key), key, prefix, False)
        maps = self._maps()
        resized = (width, height) != (current['width'], current['height'])
        self._validate_events(edited, current['map'], width, height, maps, resized)
        if resized:
            for shared_name in current['shared_with']:
                shared = deepcopy(maps[shared_name])
                owner = self._events_owner(shared_name, maps)
                for key in EVENT_KEYS:
                    shared[key] = maps[owner].get(key, [])
                try:
                    self._validate_events(shared, shared, width, height, maps, True)
                except ValueError as exc:
                    raise ValueError(f'Shared map {shared_name}: {exc}') from exc
        plan = {}
        if current['events_owner'] != name:
            owner = current['events_owner']
            owner_data = deepcopy(maps[owner])
            events_changed = any(edited.get(k, []) != current['map'].get(k, []) for k in EVENT_KEYS)
            if events_changed and body.get('confirm_shared') is not True:
                raise ValueError('Events are shared. Confirm shared changes or edit ' + owner)
            if events_changed:
                for key in EVENT_KEYS:
                    owner_data[key] = edited.get(key, [])
                plan[f'data/maps/{owner}/map.json'] = json_bytes(owner_data)
            for key in EVENT_KEYS:
                edited.pop(key, None)
        elif current['events_shared_with'] and any(edited.get(k, []) != current['map'].get(k, []) for k in EVENT_KEYS) and body.get('confirm_shared') is not True:
            raise ValueError('Events are shared. Confirm shared changes to update: ' + ', '.join(current['events_shared_with']))
        if edited != original:
            plan[f'data/maps/{name}/map.json'] = json_bytes(edited)
        if cells != current['cells']:
            plan[layout['blockdata_filepath']] = pack_words(cells)
        if border != current['border']:
            plan[layout['border_filepath']] = pack_words(border)
        if layout != old_layout:
            layouts = self._json(LAYOUTS)
            layouts['layouts'] = [layout if item['id'] == layout['id'] else item for item in layouts['layouts']]
            plan[LAYOUTS] = json_bytes(layouts)
        if owns_pixel_plan:
            plan.update(pixel_planner.plan())
        return plan

    def plan_new(self, name, template='LittlerootTown', width=20, height=20):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name):
            raise ValueError('New map name must use letters, digits and underscores, starting with a letter (max 64 characters)')
        if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('Map name is reserved by Windows')
        maps = self._maps()
        candidate_dirs = [self.source / 'data/maps' / name, self.source / 'data/layouts' / name]
        occupied = any(path.exists() and (not path.is_dir() or any(path.iterdir())) for path in candidate_dirs)
        if name.casefold() in {n.casefold() for n in maps} or occupied:
            raise ValueError('That map or layout folder already exists')
        self._validate_dimensions(width, height)
        original = self.get_map(template)
        constant = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).upper()
        map_id, layout_id = 'MAP_' + constant, 'LAYOUT_' + constant
        layouts = self._json(LAYOUTS)
        if map_id in {m['id'] for m in maps.values()} or layout_id in {m['id'] for m in layouts['layouts']}:
            raise ValueError('That map identifier already exists')
        data = deepcopy(original['map'])
        data.update(name=name, id=map_id, layout=layout_id, connections=None)
        data.pop('shared_events_map', None)
        data.pop('shared_scripts_map', None)
        for key in EVENT_KEYS:
            data[key] = []
        layout = deepcopy(original['layout'])
        layout.update(id=layout_id, name=name + '_Layout', width=width, height=height,
                      blockdata_filepath=f'data/layouts/{name}/map.bin', border_filepath=f'data/layouts/{name}/border.bin')
        layouts['layouts'].append(layout)
        groups = self._json(GROUPS)
        group = 'gMapGroup_CustomWorld'
        if group not in groups['group_order']:
            if len(groups['group_order']) >= 255:
                raise ValueError('Map group capacity reached')
            groups['group_order'].append(group)
            groups[group] = []
        if len(groups[group]) >= 255:
            raise ValueError('Custom map group capacity reached')
        groups[group].append(name)
        scripts = self._text('data/event_scripts.s')
        scripts += f'\n\t.include "data/maps/{name}/scripts.inc"\n'
        fill = next((cell for cell in original['cells'] if ((cell >> 10) & 3) == 0), original['cells'][0])
        return {LAYOUTS: json_bytes(layouts), GROUPS: json_bytes(groups),
                f'data/maps/{name}/map.json': json_bytes(data),
                layout['blockdata_filepath']: pack_words([fill] * width * height),
                layout['border_filepath']: pack_words(original['border']),
                f'data/maps/{name}/scripts.inc': f'{name}_MapScripts::\n\t.byte 0\n'.encode(),
                'data/event_scripts.s': scripts.encode('utf-8')}
