"""Compile lossless pixel pieces into Emerald graphics and metatile records.

All allocation is append-only, coordinated across a save batch, and held in
memory until the caller commits the complete source transaction.
"""
import io
import re

from PIL import Image

from world import integer, pack_words, words


class PixelPatchPlanner:
    def __init__(self, world):
        self.world = world
        self.tilesets = world._tilesets()
        self.pairs = {}
        self.banks = {}
        self.sheets = {}
        # Doors temporarily load their animation into the last sixteen tiles.
        self.animated = set(range(1008, 1024))
        animation_file = world._path('src/tileset_anims.c')
        if animation_file.is_file():
            for line in animation_file.read_text(encoding='utf-8').splitlines():
                for match in re.finditer(r'TILE_OFFSET_4BPP\((NUM_TILES_IN_PRIMARY\s*\+\s*)?(\d+)\)', line):
                    start = int(match[2]) + (512 if match[1] else 0)
                    length = re.search(r',\s*(\d+)\s*\*\s*TILE_SIZE_4BPP', line[match.end():])
                    # Destination arrays in the stock source contain 4-tile
                    # strips; direct copy calls state their precise size.
                    count = int(length[1]) if length else 4
                    self.animated.update(range(start, start + count))

    def _sheet(self, path):
        if path not in self.sheets:
            image = Image.open(io.BytesIO(self.world._read(path)))
            image.load()
            if image.mode != 'P' or image.width % 8 or image.height % 8:
                raise ValueError('Pixel pieces require indexed tiles with dimensions divisible by eight')
            across = image.width // 8
            tiles = [bytes(value & 15 for value in image.crop((x * 8, y * 8, x * 8 + 8, y * 8 + 8)).tobytes())
                     for y in range(image.height // 8) for x in range(across)]
            if len(tiles) > 512:
                raise ValueError('Graphics sheet exceeds Emerald tile capacity')
            self.sheets[path] = {'image': image, 'tiles': tiles, 'original_count': len(tiles),
                                 'dirty': False, 'next': len(tiles)}
        return self.sheets[path]

    def _pair(self, primary, secondary):
        key = primary, secondary
        if key in self.pairs:
            return self.pairs[key]
        atlas, metadata = self.world._pair(primary, secondary)
        p, s = self.tilesets[primary], self.tilesets[secondary]
        bank_key = s['metatiles'], s['attributes']
        if bank_key not in self.banks:
            descriptors = words(self.world._read(s['metatiles']))
            attributes = words(self.world._read(s['attributes']))
            self.banks[bank_key] = {'descriptors': descriptors, 'attributes': attributes,
                                    'original_count': len(attributes), 'lookup': {}}
            for i, attr in enumerate(attributes):
                self.banks[bank_key]['lookup'].setdefault((tuple(descriptors[i * 8:i * 8 + 8]), attr), 512 + i)
        bank = self.banks[bank_key]
        sheets = self._sheet(p['tiles']), self._sheet(s['tiles'])
        # Existing descriptors can refer beyond the stored PNG (animations or
        # unused placeholders). Never claim those slots as new static artwork.
        for tileset in self.tilesets.values():
            if tileset['tiles'] == s['tiles']:
                refs = [value & 1023 for value in words(self.world._read(tileset['metatiles'])) if value & 1023 >= 512]
                if refs:
                    sheets[1]['next'] = max(sheets[1]['next'], max(refs) - 512 + 1)
        descriptors, attributes = {}, {}
        for base, tileset in ((0, p), (512, s)):
            data = words(self.world._read(tileset['metatiles']))
            attrs = words(self.world._read(tileset['attributes']))
            for index, attr in enumerate(attrs):
                descriptors[base + index] = tuple(data[index * 8:index * 8 + 8])
                attributes[base + index] = attr
        palettes = [self.world._palette(path) for path in p['palettes'][:6] + s['palettes'][6:13]]
        lookup = {}
        for tile_id in sorted(descriptors):
            lookup.setdefault((descriptors[tile_id], attributes[tile_id]), tile_id)
        pair = {'atlas': atlas, 'metadata': metadata, 'descriptors': descriptors, 'attributes': attributes,
                'palettes': palettes, 'sheets': sheets, 'bank': bank, 'lookup': lookup,
                'sheet_paths': (p['tiles'], s['tiles']), 'rgba': {}, 'patterns': {}}
        self.pairs[key] = pair
        self._index_patterns(pair)
        return pair

    @staticmethod
    def _flipped(pixels, flip):
        return bytes(pixels[(7 - y if flip & 0x800 else y) * 8 + (7 - x if flip & 0x400 else x)]
                     for y in range(8) for x in range(8))

    def _index_patterns(self, pair):
        for bank_index, sheet in enumerate(pair['sheets']):
            for index, pixels in enumerate(sheet['tiles']):
                tile_id = bank_index * 512 + index
                if tile_id in self.animated:
                    continue
                for flip in (0, 0x400, 0x800, 0xC00):
                    pair['patterns'].setdefault(self._flipped(pixels, flip), tile_id | flip)

    def _rgba(self, pair, descriptor):
        if descriptor in pair['rgba']:
            return pair['rgba'][descriptor]
        tile_id, palette_id = descriptor & 1023, descriptor >> 12
        sheet = pair['sheets'][tile_id >= 512]
        local_id = tile_id % 512
        if local_id >= len(sheet['tiles']) or palette_id >= len(pair['palettes']):
            result = ((0, 0, 0, 0),) * 64
        else:
            indices = self._flipped(sheet['tiles'][local_id], descriptor & 0xC00)
            palette = pair['palettes'][palette_id]
            result = tuple((*palette[index], 255) if index else (0, 0, 0, 0) for index in indices)
        pair['rgba'][descriptor] = result
        return result

    def _allocate_graphic(self, pair, pixels):
        candidates = []
        for palette_id, palette in enumerate(pair['palettes']):
            colors = {}
            for index, color in enumerate(palette[1:], 1):
                colors.setdefault((*color, 255), index)
            colors[(0, 0, 0, 0)] = 0
            if all(pixel in colors for pixel in pixels):
                indices = bytes(colors[pixel] for pixel in pixels)
                existing = pair['patterns'].get(indices)
                if existing is not None:
                    return existing | palette_id << 12
                candidates.append((palette_id, indices))
        if not candidates:
            raise ValueError('This cut mixes colors that cannot fit one existing 16-color tile palette. Move whole tiles or choose a different cut.')
        palette_id, indices = candidates[0]
        sheet = pair['sheets'][1]
        index = sheet['next']
        while index < 512 and index + 512 in self.animated:
            index += 1
        if index >= 512:
            raise ValueError('This tileset has no free graphics slots for new pixel pieces. Existing game artwork was not overwritten.')
        while len(sheet['tiles']) <= index:
            sheet['tiles'].append(bytes(64))
        sheet['tiles'][index] = indices
        sheet['next'], sheet['dirty'] = index + 1, True
        # A shared secondary sheet can be used by multiple primary pairs.
        for other in self.pairs.values():
            if other['sheets'][1] is sheet:
                for flip in (0, 0x400, 0x800, 0xC00):
                    other['patterns'].setdefault(self._flipped(indices, flip), 512 + index | flip)
        return 512 + index | palette_id << 12

    def _compile_cell(self, pair, base, refs):
        descriptors, attributes = pair['descriptors'], pair['attributes']
        if base not in attributes:
            raise ValueError('Pixel pieces require a valid base metatile')
        attribute = attributes[base]
        layer = attribute >> 12
        if layer not in (0, 1, 2):
            raise ValueError('Pixel pieces require a supported metatile layer type')
        for value in refs:
            integer(value, 0, 1024 * 256 - 1, 'Pixel reference')
            source_id = value // 256
            if source_id not in attributes:
                raise ValueError(f'Pixel piece refers to missing metatile {source_id}')
            if attributes[source_id] >> 12 != layer:
                raise ValueError('These pixel pieces use different background layers. Move whole tiles to preserve sprite layering.')
        layers = [[], []]
        direct = []
        for level in range(2):
            for quadrant in range(4):
                rgba, originals = [], []
                same_coordinates = True
                for y in range(8):
                    for x in range(8):
                        target_pixel = ((quadrant // 2) * 8 + y) * 16 + (quadrant % 2) * 8 + x
                        source_id, source_pixel = divmod(refs[target_pixel], 256)
                        sx, sy = source_pixel % 16, source_pixel // 16
                        source_quadrant = sx // 8 + (sy // 8) * 2
                        descriptor = descriptors[source_id][level * 4 + source_quadrant]
                        rgba.append(self._rgba(pair, descriptor)[(sy % 8) * 8 + sx % 8])
                        originals.append(descriptor)
                        same_coordinates &= sx % 8 == x and sy % 8 == y
                copied = originals[0] if same_coordinates and len(set(originals)) == 1 else None
                if copied is None and any(descriptor & 1023 in self.animated for descriptor in originals):
                    raise ValueError('This cut splits an animated tile. Move its complete 8-pixel artwork block or use whole tiles to keep its animation.')
                layers[level].append(tuple(rgba))
                direct.append(copied)
        # NORMAL has a third implicit background. Pixel movement must not change
        # its exposed colors or silently flatten it into a different BG layer.
        background = (*pair['palettes'][0][0], 255)
        implicit = self._rgba(pair, 0x3014) if layer == 0 else None
        atlas = pair['atlas']
        for pixel, ref in enumerate(refs):
            x, y = pixel % 16, pixel // 16
            quadrant, local = x // 8 + (y // 8) * 2, (y % 8) * 8 + x % 8
            actual = background
            if implicit and implicit[local][3]:
                actual = implicit[local]
            for level in range(2):
                if layers[level][quadrant][local][3]:
                    actual = layers[level][quadrant][local]
            source_id, source_pixel = divmod(ref, 256)
            expected = atlas.getpixel(((source_id % 16) * 16 + source_pixel % 16,
                                       (source_id // 16) * 16 + source_pixel // 16))
            if actual != expected:
                raise ValueError('This cut exposes a background layer that cannot be preserved exactly. Move whole tiles here.')
        combined = tuple(direct[i] if direct[i] is not None else self._allocate_graphic(pair, layers[i // 4][i % 4]) for i in range(8))
        key = combined, attribute
        existing = pair['lookup'].get(key, pair['bank']['lookup'].get(key))
        if existing is not None:
            return existing
        bank = pair['bank']
        if len(bank['attributes']) >= 512:
            raise ValueError('This tileset has no free metatile slots for new pixel pieces. Existing game tiles were not overwritten.')
        tile_id = 512 + len(bank['attributes'])
        bank['descriptors'].extend(combined)
        bank['attributes'].append(attribute)
        bank['lookup'][key] = tile_id
        return tile_id

    def compose(self, primary, secondary, cells, patches):
        pair = self._pair(primary, secondary)
        result = list(cells)
        for key, refs in patches.items():
            if not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]*', key) or int(key) >= len(cells):
                raise ValueError('Pixel patch index must identify an existing map cell')
            if not isinstance(refs, list) or len(refs) != 256:
                raise ValueError('Each pixel patch must contain exactly 256 source pixel references')
            index = int(key)
            tile_id = self._compile_cell(pair, cells[index] & 1023, refs)
            result[index] = cells[index] & 0xFC00 | tile_id
            if result[index] == 0x03FF:
                raise ValueError('The last metatile slot would become Emerald\'s undefined map block here. No source changes were saved.')
        return result

    def plan(self):
        result = {}
        for (metatiles_path, attributes_path), bank in self.banks.items():
            if len(bank['attributes']) != bank['original_count']:
                result[metatiles_path] = pack_words(bank['descriptors'])
                result[attributes_path] = pack_words(bank['attributes'])
        for path, sheet in self.sheets.items():
            if not sheet['dirty']:
                continue
            original = sheet['image']
            across = original.width // 8
            height = ((len(sheet['tiles']) + across - 1) // across) * 8
            image = Image.new('P', (original.width, height))
            image.putpalette(original.getpalette())
            image.paste(original, (0, 0))
            for index in range(sheet['original_count'], len(sheet['tiles'])):
                tile = Image.frombytes('P', (8, 8), sheet['tiles'][index])
                image.paste(tile, ((index % across) * 8, (index // across) * 8))
            output = io.BytesIO()
            image.save(output, 'PNG')
            result[path] = output.getvalue()
        return result
