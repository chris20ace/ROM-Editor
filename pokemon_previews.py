"""Read-only party portraits from Emerald's species graphics and palette tables."""
from functools import lru_cache
import io
from pathlib import Path
import re

from PIL import Image
from player import _declarations, _engine


FILES = ('src/data/pokemon_graphics/front_pic_table.h',
         'src/data/pokemon_graphics/palette_table.h',
         'src/anim_mon_front_pics.c', 'src/data/graphics/pokemon.h')


@lru_cache(maxsize=4)
def _records(root, stamps):
    texts = [(Path(root) / name).read_text(encoding='utf-8') for name in FILES]
    pictures = dict(re.findall(r'SPECIES_SPRITE\((\w+),\s*(\w+)\)', texts[0]))
    palettes = dict(re.findall(r'SPECIES_PAL\((\w+),\s*(\w+)\)', texts[1]))
    declarations = _declarations('\n'.join(texts[2:]))
    result = {}
    for name, symbol in pictures.items():
        picture = declarations.get(symbol, {}).get('path')
        palette = declarations.get(palettes.get(name), {}).get('path')
        # The Makefile concatenates Castform forms. Party previews show its
        # normal form, the first image and palette in those generated outputs.
        if picture == 'graphics/pokemon/castform/anim_front.4bpp':
            picture = 'graphics/pokemon/castform/normal/anim_front.png'
        if palette == 'graphics/pokemon/castform/normal.gbapal':
            palette = 'graphics/pokemon/castform/normal/normal.pal'
        if picture and palette:
            result['SPECIES_' + name] = (picture, palette)
    return result


class PokemonPreviews:
    def __init__(self, source):
        self.source = Path(source).resolve()

    def records(self):
        stamps = tuple(((self.source / path).stat().st_mtime_ns,
                        (self.source / path).stat().st_size) for path in FILES)
        return _records(str(self.source), stamps)

    def _path(self, relative, extension):
        path = (self.source / relative).resolve()
        if (not path.is_relative_to(self.source / 'graphics/pokemon')
                or not relative.startswith('graphics/pokemon/') or path.suffix != extension):
            raise ValueError('Species artwork must stay in its graphics directory.')
        return path

    def preview(self, species):
        if not isinstance(species, str) or species not in self.records():
            raise ValueError('Choose a Pokémon species from the party list.')
        picture, palette = self.records()[species]
        with Image.open(self._path(picture, '.png')) as sheet:
            if sheet.mode != 'P' or sheet.width < 64 or sheet.height < 64:
                raise ValueError('This species needs an indexed 64-pixel battle sprite.')
            image = sheet.crop((0, 0, 64, 64))
        lines = self._path(palette, '.pal').read_text(encoding='utf-8').splitlines()
        if lines[:3] != ['JASC-PAL', '0100', '16']:
            raise ValueError('This species needs a 16-color battle palette.')
        colors = [tuple(map(int, line.split())) for line in lines[3:] if line.strip()]
        if len(colors) != 16 or any(len(color) != 3 or any(c < 0 or c > 255 for c in color) for color in colors):
            raise ValueError('Invalid species battle palette.')
        pixels = list(image.tobytes())
        if any(value > 15 for value in pixels):
            raise ValueError('Invalid species palette index.')
        output = Image.new('RGBA', image.size)
        rgba = [(*_engine(color), 0 if index == 0 else 255) for index, color in enumerate(colors)]
        output.putdata([rgba[value] for value in pixels])
        data = io.BytesIO()
        output.save(data, 'PNG')
        return data.getvalue()
