"""Editable Emerald town-map artwork and location grid, separate from walkable maps."""
import hashlib
import io
import json
from pathlib import Path
import re
from PIL import Image


class Region:
    TILES = 'graphics/pokenav/region_map/map.bin'
    LAYOUT = 'src/data/region_map/region_map_layout.h'
    SECTIONS = 'src/data/region_map/region_map_sections.json'
    IMAGE = 'graphics/pokenav/region_map/map.png'
    PALETTE = 'graphics/pokenav/region_map/map.pal'

    def __init__(self, source):
        self.source = Path(source)

    def _read(self, rel):
        target = (self.source / rel).resolve()
        if not target.is_relative_to(self.source.resolve()):
            raise ValueError('Region source must stay in the project.')
        return target.read_bytes()

    def get(self):
        tiles = self._read(self.TILES)
        layout = self._read(self.LAYOUT)
        sections = self._read(self.SECTIONS)
        ids = re.findall(r'\bMAPSEC_[A-Z0-9_]+\b', layout.decode('utf-8'))
        if len(tiles) != 4096 or len(ids) != 28 * 15:
            raise ValueError('This region format differs from the supported Emerald town map.')
        return dict(width=64, height=64, tile_size=8, cells=list(tiles), section_width=28, section_height=15,
                    section_cells=ids, sections=json.loads(sections)['map_sections'],
                    tile_count=233, atlas_url='/api/region/atlas.png', atlas_columns=16,
                    revision=hashlib.sha256(tiles + layout + sections + self._read(self.IMAGE) + self._read(self.PALETTE)).hexdigest())

    def atlas(self):
        image = Image.open(io.BytesIO(self._read(self.IMAGE))).copy()
        lines = self._read(self.PALETTE).decode().splitlines()
        count = int(lines[2])
        palette = [int(c) for line in lines[3:3 + count] for c in line.split()]
        # Emerald loads this artwork's colors into background palette slots 7–8.
        full_palette = image.getpalette() or [0] * 768
        full_palette[112 * 3:112 * 3 + len(palette)] = palette
        image.putpalette(full_palette)
        output = io.BytesIO()
        image.convert('RGB').save(output, format='PNG')
        return output.getvalue()

    def plan_save(self, body):
        current = self.get()
        if body.get('revision') != current['revision']:
            raise ValueError('The region map changed. Reload before saving.')
        cells = body.get('cells', current['cells'])
        if not isinstance(cells, list) or len(cells) != 4096 or any(type(c) is not int or not 0 <= c < 233 for c in cells):
            raise ValueError('Region artwork requires 4096 tile IDs from 0 to 232.')
        section_cells = body.get('section_cells', current['section_cells'])
        allowed = {section['id'] for section in current['sections']} | {'MAPSEC_NONE'}
        if not isinstance(section_cells, list) or len(section_cells) != 420 or any(c not in allowed for c in section_cells):
            raise ValueError('Location grid requires 420 known section names.')
        edits = body.get('sections', current['sections'])
        if not isinstance(edits, list) or [e.get('id') for e in edits if isinstance(e, dict)] != [e['id'] for e in current['sections']]:
            raise ValueError('Existing location IDs and order must be preserved.')
        result = []
        for original, edited in zip(current['sections'], edits):
            item = dict(original)
            name = edited.get('name', original.get('name', ''))
            if name != original.get('name') and (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9 .,!?&'()/:-]{1,32}", name)):
                raise ValueError('Location names use 1–32 ordinary letters, numbers, spaces or punctuation.')
            if 'name' in edited:
                item['name'] = name
            for key in ('x', 'y', 'width', 'height'):
                if key in edited:
                    value = edited[key]
                    if type(value) is not int or not 0 <= value <= 255:
                        raise ValueError('Location positions and sizes must be integers from 0 to 255.')
                    item[key] = value
            result.append(item)
        text = self._read(self.LAYOUT).decode('utf-8')
        iterator = iter(section_cells)
        text = re.sub(r'\bMAPSEC_[A-Z0-9_]+\b', lambda _: next(iterator), text)
        doc = json.loads(self._read(self.SECTIONS))
        doc['map_sections'] = result
        return {self.TILES: bytes(cells), self.LAYOUT: text.encode('utf-8'),
                self.SECTIONS: (json.dumps(doc, indent=2, ensure_ascii=False) + '\n').encode('utf-8')}
