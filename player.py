"""Source-backed, indexed player artwork editing. Planning never writes source files.

PNG palette indices are game data, not colors to quantize. Overworld graphics use
external palettes; the same pixel sheet can also use a separate reflection palette.
Only the named player assets below can be offered or written by this editor.
"""
import hashlib
import io
from pathlib import Path
import re
from urllib.parse import urlencode

from PIL import Image


OBJECT = 'src/data/object_events/'
METADATA = (
    OBJECT + 'object_event_graphics.h', OBJECT + 'object_event_graphics_info.h',
    OBJECT + 'object_event_pic_tables.h', 'src/event_object_movement.c',
    'src/data/graphics/trainers.h', 'src/data/trainer_graphics/front_pic_tables.h',
    'src/data/trainer_graphics/back_pic_tables.h',
    'src/data/graphics/intro_scene.h', 'src/intro_credits_graphics.c',
    'src/region_map.c',
)
FORMS = ('walking', 'running', 'mach_bike', 'acro_bike', 'surfing', 'underwater',
         'field_move', 'fishing', 'watering', 'decorating')
CHARACTERS = ('Brendan', 'May')
PLAYER_PNGS = {
    f'graphics/object_events/pics/people/{character}/{form}.png'
    for character in ('brendan', 'may', 'ruby_sapphire_brendan', 'ruby_sapphire_may')
    for form in (FORMS if not character.startswith('ruby') else FORMS[:2])
} | {
    f'graphics/trainers/{view}_pics/{character}{variant}.png'
    for view in ('front', 'back') for character in ('brendan', 'may') for variant in ('', '_rs')
} | {
    f'graphics/intro/scene_2/{character}{suffix}.png'
    for character in ('brendan', 'may') for suffix in ('', '_credits')
} | {f'graphics/pokenav/region_map/{character}_icon.png' for character in ('brendan', 'may')} | {
    'graphics/intro/scene_2/bicycle.png'
}
PLAYER_PALETTES = PLAYER_PNGS | {
    f'graphics/object_events/palettes/{name}.pal' for name in (
        'brendan', 'may', 'brendan_reflection', 'may_reflection',
        'ruby_sapphire_brendan', 'ruby_sapphire_may', 'player_underwater')
} | {
    f'graphics/trainers/palettes/{character}{variant}.pal'
    for character in ('brendan', 'may') for variant in ('', '_rs')
} | {'graphics/intro/scene_2/player.pal'}


def _declarations(text):
    return {symbol: {'path': path, 'format': fmt, 'options': options or ''}
            for symbol, path, fmt, options in re.findall(
                r'\b(\w+)\s*\[\s*\]\s*=\s*INCGFX_\w+\("([^"]+)",\s*"([^"]+)"'
                r'(?:,\s*"([^"]*)")?\)', text)}


def _fields(body):
    return dict(re.findall(r'\.(\w+)\s*=\s*([^,\n]+)', body))


def _rgb(color):
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Palette colors must be #RRGGBB values')
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return '#' + ''.join(f'{channel:02X}' for channel in rgb)


def _engine(rgb):
    # tools/gbagfx/gfx.c: DOWNCONVERT_BIT_DEPTH then UPCONVERT_BIT_DEPTH.
    return tuple((channel // 8) * 255 // 31 for channel in rgb)


def _png(image):
    output = io.BytesIO()
    image.save(output, 'PNG', bits=4, transparency=0)
    return output.getvalue()


class Player:
    def __init__(self, source):
        self.source = Path(source).resolve()
        self._metadata_key = None
        self._assets = {}

    def _path(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source):
            raise ValueError('Player asset must stay inside the source project')
        if path.relative_to(self.source).as_posix().casefold() != relative.casefold():
            raise ValueError('Player asset aliases are not supported')
        return path

    def _metadata(self):
        content = {}
        digest = hashlib.sha256()
        for relative in METADATA:
            path = self._path(relative)
            data = path.read_bytes() if path.is_file() else b''
            content[relative] = data.decode('utf-8')
            digest.update(relative.encode() + b'\0' + data + b'\0')
        return digest.hexdigest(), content

    def _records(self):
        key, texts = self._metadata()
        if key == self._metadata_key:
            return self._assets
        assets = {}
        declarations = _declarations('\n'.join(texts.values()))

        def add(symbol, character, variant, kind, label, palette_symbol, frame_width, frame_height,
                uses=(), palette_label='Normal'):
            graphic = declarations.get(symbol, {})
            palette = declarations.get(palette_symbol, {})
            path, pal = graphic.get('path'), palette.get('path')
            if path not in PLAYER_PNGS or pal not in PLAYER_PALETTES:
                return
            if not self._path(path).is_file() or not self._path(pal).is_file():
                return
            if path not in assets:
                assets[path] = {'path': path, 'label': f'{character} · {label}',
                                'character': character, 'variant': variant, 'kind': kind,
                                'frame_width': frame_width, 'frame_height': frame_height,
                                'palette_options': [], 'uses': [], 'symbols': []}
            asset = assets[path]
            if not any(item['path'] == pal for item in asset['palette_options']):
                asset['palette_options'].append({'path': pal, 'label': palette_label})
            asset['uses'] = sorted(set(asset['uses']) | set(uses))
            asset['symbols'] = sorted(set(asset['symbols']) | {symbol})

        pics = {}
        for label, body in re.findall(r'SpriteFrameImage (\w+)\[\]\s*=\s*\{(.*?)\};',
                                       texts[OBJECT + 'object_event_pic_tables.h'], re.S):
            frames = [(symbol, int(w) * 8, int(h) * 8)
                      for symbol, w, h in re.findall(r'overworld_frame\((\w+),\s*(\d+),\s*(\d+),', body)]
            frames += [(symbol, None, None) for symbol in re.findall(r'obj_frame_tiles\((\w+)\)', body)]
            pics[label] = set(frames)
        movement = texts['src/event_object_movement.c']
        tags = {tag: symbol for symbol, tag in re.findall(
            r'\{\s*(\w+),\s*(OBJ_EVENT_PAL_TAG_\w+)\s*\}', movement)}
        reflection_tables = {label: re.findall(r'OBJ_EVENT_PAL_TAG_\w+', body)
                             for label, body in re.findall(
                                 r'const u16 (sReflectionPaletteTags_\w+)\[\]\s*=\s*\{(.*?)\};', movement, re.S)}
        reflection = {tag: reflection_tables[table][0]
                      for tag, table in re.findall(r'\{\s*(OBJ_EVENT_PAL_TAG_\w+),\s*(sReflectionPaletteTags_\w+)\s*\}', movement)
                      if reflection_tables.get(table)}
        infos = re.findall(r'ObjectEventGraphicsInfo (\w+)\s*=\s*\{(.*?)\};',
                           texts[OBJECT + 'object_event_graphics_info.h'], re.S)
        for info_name, body in infos:
            fields = _fields(body)
            for symbol, width, height in sorted(pics.get(fields.get('images'), ())):
                path = declarations.get(symbol, {}).get('path', '')
                if path not in PLAYER_PNGS or '/object_events/' not in path:
                    continue
                character = 'Brendan' if 'brendan' in path else 'May'
                variant = 'Ruby/Sapphire' if '/ruby_sapphire_' in path else 'Emerald'
                form = Path(path).stem.replace('_', ' ').title()
                pal_tag = fields.get('paletteTag')
                width, height = width or int(fields.get('width', 16)), height or int(fields.get('height', 32))
                is_link = 'Link' in info_name
                usage = 'Link room avatar' if is_link else 'Rival overworld' if 'Rival' in info_name else 'Player overworld'
                add(symbol, character, variant, 'overworld', form, tags.get(pal_tag), width, height, [usage],
                    'Link room palette' if is_link else 'Normal')
                reflection_tag = reflection.get(pal_tag)
                if reflection_tag and reflection_tag != pal_tag and fields.get('disableReflectionPaletteLoad') != 'TRUE':
                    add(symbol, character, variant, 'overworld', form, tags.get(reflection_tag), width, height,
                        ['Water / ice reflection'], 'Link room reflection' if is_link else 'Reflection')

        # RS running images are compiled but the original game has no pic-table
        # references to them. Retain their frames as explicitly archived artwork.
        for character in CHARACTERS:
            symbol = 'gObjectEventPic_RubySapphire' + character + 'Running'
            declaration = declarations.get(symbol, {})
            if '-mwidth 2 -mheight 4' in declaration.get('options', ''):
                add(symbol, character, 'Ruby/Sapphire', 'overworld', 'Running (stored, unused)',
                    'gObjectEventPal_RubySapphire' + character, 16, 32, ['Stored RS artwork; unused by the original animation tables'])
                if declaration['path'] in assets:
                    assets[declaration['path']]['archived'] = True

        front_text = texts['src/data/trainer_graphics/front_pic_tables.h']
        back_text = texts['src/data/trainer_graphics/back_pic_tables.h']
        front_palettes = dict(re.findall(r'TRAINER_PAL\((\w+),\s*(\w+)\)', front_text))
        back_palettes = dict(re.findall(r'TRAINER_BACK_PAL\((\w+),\s*(\w+)\)', back_text))
        for character in CHARACTERS:
            for prefix, variant in (('', 'Emerald'), ('RubySapphire', 'Ruby/Sapphire')):
                suffix = prefix + character
                front_id = ('RS_' if prefix else '') + character.upper()
                back_id = ('RUBY_SAPPHIRE_' if prefix else '') + character.upper()
                front_symbol = 'gTrainerFrontPic_' + suffix
                back_symbol = 'gTrainerBackPic_' + suffix
                if re.search(r'TRAINER_SPRITE\(' + front_id + r',\s*' + front_symbol + r'\b', front_text):
                    add(front_symbol, character, variant, 'battle_front', 'Battle front / trainer portrait',
                        front_palettes.get(front_id), 64, 64,
                        ['Rival battle portrait', 'Player selection / trainer card'])
                if re.search(r'\b' + back_symbol + r'\b', back_text):
                    add(back_symbol, character, variant, 'battle_back', 'Battle back / throwing animation',
                        back_palettes.get(back_id), 64, 64, ['Player battle back sprite'])
            add('sRegionMapPlayerIcon_' + character + 'Gfx', character, 'Emerald', 'icon', 'Town map icon',
                'sRegionMapPlayerIcon_' + character + 'Pal', 16, 16, ['Region map player marker'])
            add('gIntro' + character + '_Gfx', character, 'Emerald', 'intro', 'Opening bicycle scene',
                'gIntroPlayer_Pal', 64, 64, ['Opening scene'])
            add('s' + character + 'Credits_Gfx', character, 'Emerald', 'credits', 'Credits bicycle scene',
                's' + character + 'Credits_Pal', 64, 64, ['Credits player and rival'])
        for pal_symbol, label in (('gIntroPlayer_Pal', 'Opening scene'),
                                  ('sBrendanCredits_Pal', 'Brendan credits'),
                                  ('sMayCredits_Pal', 'May credits')):
            add('sBicycle_Gfx', 'Both', 'Emerald', 'intro', 'Intro / credits bicycle layer', pal_symbol,
                64, 32, ['Bicycle layer shared by both players'], label)

        kind_order = {kind: i for i, kind in enumerate(('overworld', 'battle_back', 'battle_front', 'icon', 'intro', 'credits'))}
        self._assets = dict(sorted(assets.items(), key=lambda item: (
            item[1]['variant'] != 'Emerald', item[1]['character'], kind_order[item[1]['kind']],
            FORMS.index(Path(item[0]).stem) if Path(item[0]).stem in FORMS else 100, item[0])))
        self._metadata_key = key
        return self._assets

    def _image(self, path):
        with Image.open(self._path(path)) as original:
            if original.mode != 'P' or max(original.tobytes(), default=0) > 15:
                raise ValueError(f'{path} must be a 16-color indexed PNG')
            return original.copy()

    def _palette(self, path):
        if path.endswith('.png'):
            colors = self._image(path).getpalette()[:48]
            colors += [0] * (48 - len(colors))
            return [tuple(colors[i:i + 3]) for i in range(0, 48, 3)]
        lines = self._path(path).read_text(encoding='utf-8').splitlines()
        if len(lines) < 3 or lines[:2] != ['JASC-PAL', '0100'] or lines[2] != '16':
            raise ValueError(f'{path} must be a 16-color JASC palette')
        colors = [tuple(map(int, line.split())) for line in lines[3:] if line.strip()]
        if len(colors) != 16 or any(len(c) != 3 or any(v < 0 or v > 255 for v in c) for c in colors):
            raise ValueError(f'{path} has an invalid palette')
        return colors

    def _sharing(self, palette_path, current_path):
        return [{'path': path, 'label': asset['label']}
                for path, asset in self._assets.items() if path != current_path
                and any(p['path'] == palette_path for p in asset['palette_options'])]

    def _revision(self, asset):
        dependencies = {asset['path']} | {p['path'] for p in asset['palette_options']}
        for palette in asset['palette_options']:
            dependencies.update(item['path'] for item in self._sharing(palette['path'], asset['path']))
        digest = hashlib.sha256(self._metadata_key.encode())
        for path in sorted(dependencies):
            digest.update(path.encode() + b'\0' + self._path(path).read_bytes() + b'\0')
        return digest.hexdigest(), sorted(dependencies)

    def _row(self, asset, palette_path=None):
        if palette_path is not None and not isinstance(palette_path, str):
            raise ValueError('Palette path must identify a bound player palette')
        image = self._image(asset['path'])
        width, height = image.size
        fw, fh = asset['frame_width'], asset['frame_height']
        if width % fw or height % fh:
            raise ValueError(f"{asset['path']} dimensions do not match its engine frame size")
        selected = palette_path or asset['palette_options'][0]['path']
        if selected not in {p['path'] for p in asset['palette_options']}:
            raise ValueError('Palette is not bound to this player sprite')
        frames = [{'index': index, 'x': index % (width // fw) * fw,
                   'y': index // (width // fw) * fh, 'width': fw, 'height': fh,
                   'label': f'Frame {index + 1}'} for index in range(width // fw * (height // fh))]
        query = urlencode({'path': asset['path'], 'palette_path': selected})
        return {**asset, 'id': asset['path'], 'width': width, 'height': height,
                'frames': frames, 'frame_count': len(frames), 'palette_path': selected,
                'palette_shared_with': self._sharing(selected, asset['path']),
                'palette_options': [{**p, 'palette_shared_with': self._sharing(p['path'], asset['path'])}
                                    for p in asset['palette_options']],
                'transparent_index': 0, 'preview_url': '/api/player/preview?' + query}

    def catalog(self):
        assets = [self._row(asset) for asset in self._records().values()]
        digest = hashlib.sha256()
        for asset in assets:
            revision, _ = self._revision(asset)
            digest.update(revision.encode())
        return {'assets': assets, 'revision': digest.hexdigest(), 'asset_count': len(assets),
                'color_note': 'Color 0 is transparent. The GBA uses 32 shades per RGB channel.',
                'sharing_note': 'Player and rival share several sprites. Shared palettes recolor every listed sheet.'}

    def get_asset(self, path, palette_path=None):
        assets = self._records()
        if not isinstance(path, str) or path not in assets:
            raise ValueError('Choose a supported player sprite from the catalog')
        asset = self._row(assets[path], palette_path)
        colors = self._palette(asset['palette_path'])
        effective = [_engine(rgb) for rgb in colors]
        revision, dependencies = self._revision(asset)
        return {**asset, 'pixels': list(self._image(path).tobytes()),
                'palette': [_hex(c) for c in colors], 'engine_palette': [_hex(c) for c in effective],
                'palette_rgba': [[*c, 0 if i == 0 else 255] for i, c in enumerate(effective)],
                'revision': revision, 'source_paths': dependencies}

    get_record = get_asset

    def preview(self, path, palette_path=None):
        asset = self.get_asset(path, palette_path)
        image = Image.new('RGBA', (asset['width'], asset['height']))
        image.putdata([tuple(asset['palette_rgba'][pixel]) for pixel in asset['pixels']])
        output = io.BytesIO()
        image.save(output, 'PNG')
        return output.getvalue()

    sprite = preview

    def plan_save(self, body):
        if not isinstance(body, dict):
            raise ValueError('Player sprite edit must be an object')
        asset = self.get_asset(body.get('path'), body.get('palette_path'))
        if body.get('revision') != asset['revision']:
            raise ValueError('Player sprite or a shared palette changed. Reload before saving.')
        if any(key in body and body[key] != asset[key] for key in ('width', 'height', 'transparent_index')):
            raise ValueError('Sprite dimensions and transparent index 0 must be preserved')
        pixels = body.get('pixels')
        if not isinstance(pixels, list) or len(pixels) != asset['width'] * asset['height']:
            raise ValueError('Pixel count must match the original sprite sheet dimensions')
        if any(type(pixel) is not int or not 0 <= pixel <= 15 for pixel in pixels):
            raise ValueError('Every pixel must be an integer palette index from 0 to 15')
        palette = body.get('palette', asset['palette'])
        if not isinstance(palette, list) or not 1 <= len(palette) <= 16:
            raise ValueError('A player palette must contain 1 to 16 colors')
        colors = [_rgb(color) for color in palette]
        if max(pixels, default=0) >= len(colors):
            raise ValueError('Every pixel index must exist in the palette')
        colors += [(0, 0, 0)] * (16 - len(colors))
        palette_changed = colors != [_rgb(c) for c in asset['palette']]
        if palette_changed and asset['palette_shared_with'] and body.get('confirm_shared') is not True:
            raise ValueError('This palette is shared. Confirm recoloring the other listed sprites.')
        plan = {}
        images = {}
        path = asset['path']
        if pixels != asset['pixels']:
            image = self._image(path)
            image.putdata(pixels)
            images[path] = image
        if palette_changed:
            palette_path = asset['palette_path']
            if palette_path.endswith('.pal'):
                lines = ['JASC-PAL', '0100', '16'] + [' '.join(map(str, c)) for c in colors]
                plan[palette_path] = ('\r\n'.join(lines) + '\r\n').encode('ascii')
            else:
                image = images.get(palette_path)
                if image is None:
                    image = self._image(palette_path)
                image.putpalette([channel for color in colors for channel in color])
                images[palette_path] = image
        for relative, image in images.items():
            plan[relative] = _png(image)
        return plan
