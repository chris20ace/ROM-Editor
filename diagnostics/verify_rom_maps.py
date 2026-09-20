"""Read-only ROM/source map audit; writes only an optional diagnostic report.

No emulator, app geometry model or generated symbol file is used. Locate the
Route103 map block by exact bytes, follow its layout/header references, locate
the map-group pointer tables, and decode every source-registered ROM map.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import struct


BASE = 0x08000000
DIRECTIONS = {1: 'down', 2: 'up', 3: 'left', 4: 'right', 5: 'dive', 6: 'emerge'}
CARDINAL = {'down', 'up', 'left', 'right'}
EXPECTED_SHA1 = 'f3ae088181bf583e55daf962a92bb46f4f1d07b7'
DEFAULT_ROM = Path.home() / 'Desktop' / 'Pokemon - Emerald Version (USA, Europe).gba'
ROOT = Path(__file__).resolve().parents[1]


def hex_offset(value):
    return f'0x{value:06X}'


class Audit:
    def __init__(self, source, rom_path):
        self.source = source
        self.rom_path = rom_path
        self.rom = rom_path.read_bytes()
        self.layouts = json.loads((source / 'data/layouts/layouts.json').read_bytes())['layouts']
        self.layout_by_id = {row['id']: (index + 1, row) for index, row in enumerate(self.layouts) if row}
        groups = json.loads((source / 'data/maps/map_groups.json').read_bytes())
        self.groups = [groups[name] for name in groups['group_order']]
        self.maps = {name: json.loads((source / 'data/maps' / name / 'map.json').read_bytes())
                     for group in self.groups for name in group}
        self.ids = {data['id']: name for name, data in self.maps.items()}

    def valid_pointer(self, pointer, size=4):
        return BASE <= pointer and pointer + size <= BASE + len(self.rom) and pointer % 4 == 0

    def words(self, offset, count=1):
        return struct.unpack_from('<' + 'I' * count, self.rom, offset)

    def refs(self, value):
        pattern = struct.pack('<I', value)
        start = -1
        while True:
            start = self.rom.find(pattern, start + 1)
            if start < 0:
                return
            if start % 4 == 0:
                yield start

    def plausible_header(self, pointer):
        if not self.valid_pointer(pointer, 28):
            return False
        layout = self.words(pointer - BASE)[0]
        if not self.valid_pointer(layout, 24):
            return False
        w, h, border, block, primary, secondary = self.words(layout - BASE, 6)
        return (0 < w <= 255 and 0 < h <= 255 and self.valid_pointer(block, w * h * 2)
                and self.valid_pointer(border, 8) and self.valid_pointer(primary, 24)
                and self.valid_pointer(secondary, 24))

    def discover(self):
        source_map = self.maps['Route103']
        layout_id, layout = self.layout_by_id[source_map['layout']]
        blocks = (self.source / layout['blockdata_filepath']).read_bytes()
        block_offsets = []
        cursor = -1
        while True:
            cursor = self.rom.find(blocks, cursor + 1)
            if cursor < 0:
                break
            block_offsets.append(cursor)
        layouts = []
        for block in block_offsets:
            for ref in self.refs(BASE + block):
                start = ref - 12
                if start < 0:
                    continue
                w, h, border, block_pointer, primary, secondary = self.words(start, 6)
                if (0 < w <= 255 and 0 < h <= 255 and w * h * 2 == len(blocks)
                        and self.valid_pointer(border, 8) and self.valid_pointer(primary, 24)
                        and self.valid_pointer(secondary, 24)
                        and self.rom[border - BASE:border - BASE + 8]
                        == (self.source / layout['border_filepath']).read_bytes()):
                    layouts.append(start)
        headers = []
        for layout_offset in layouts:
            for ref in self.refs(BASE + layout_offset):
                if (self.plausible_header(BASE + ref)
                        and struct.unpack_from('<H', self.rom, ref + 18)[0] == layout_id):
                    headers.append(ref)
        if len(headers) != 1:
            raise ValueError(f'Expected one Route103 header; found {list(map(hex_offset, headers))}')
        map_number = self.groups[0].index('Route103')
        group_tables = []
        for ref in self.refs(BASE + headers[0]):
            table = ref - map_number * 4
            if table >= 0 and all(self.plausible_header(ptr)
                                  for ptr in self.words(table, len(self.groups[0]))):
                group_tables.append(table)
        masters = []
        for group_table in group_tables:
            for candidate in self.refs(BASE + group_table):
                pointers = self.words(candidate, len(self.groups))
                if all(self.valid_pointer(ptr, len(names) * 4)
                       and all(self.plausible_header(header)
                               for header in self.words(ptr - BASE, len(names)))
                       for ptr, names in zip(pointers, self.groups)):
                    masters.append(candidate)
        if len(masters) != 1:
            raise ValueError(f'Expected one map-group table; found {list(map(hex_offset, masters))}')
        self.master = masters[0]
        return {'anchor': 'Route103', 'exact_block_match_offsets': list(map(hex_offset, block_offsets)),
                'layout_offsets': list(map(hex_offset, layouts)), 'header_offset': hex_offset(headers[0]),
                'towns_routes_table_offset': hex_offset(group_tables[0]),
                'map_group_table_offset': hex_offset(self.master),
                'map_group_table_gba_pointer': hex_offset(BASE + self.master)}

    def map_record(self, name, header, group_id, map_id):
        offset = header - BASE
        layout, _, _, connection_pointer = self.words(offset, 4)
        layout_offset = layout - BASE
        width, height, border, block, primary, secondary = self.words(layout_offset, 6)
        source = self.maps[name]
        expected_id, expected_layout = self.layout_by_id[source['layout']]
        actual_id = struct.unpack_from('<H', self.rom, offset + 18)[0]
        connections = []
        connection_table_offset = None
        if connection_pointer:
            if not self.valid_pointer(connection_pointer, 8):
                raise ValueError(f'{name} has an invalid connection header pointer')
            count, table = self.words(connection_pointer - BASE, 2)
            if count > 32 or not self.valid_pointer(table, count * 12):
                raise ValueError(f'{name} has an invalid connection list')
            connection_table_offset = table - BASE
            for index in range(count):
                record_offset = table - BASE + index * 12
                direction, delta, target_group, target_map = struct.unpack_from('<B3xiBB2x', self.rom, record_offset)
                target_name = self.groups[target_group][target_map]
                connections.append({'direction': DIRECTIONS[direction], 'offset': delta,
                                    'target': target_name, 'map_group': target_group, 'map_number': target_map,
                                    'rom_offset': hex_offset(record_offset),
                                    'raw_hex': self.rom[record_offset:record_offset + 12].hex()})
        original_connections = [{'direction': item['direction'], 'offset': item['offset'],
                                 'target': self.ids[item['map']]} for item in (source.get('connections') or [])]
        actual_connections = [{key: item[key] for key in ('direction', 'offset', 'target')} for item in connections]
        expected_blocks = (self.source / expected_layout['blockdata_filepath']).read_bytes()
        expected_border = (self.source / expected_layout['border_filepath']).read_bytes()
        rom_blocks = self.rom[block - BASE:block - BASE + width * height * 2]
        rom_border = self.rom[border - BASE:border - BASE + len(expected_border)]
        checks = {
            'layout_id': actual_id == expected_id,
            'dimensions': (width, height) == (expected_layout['width'], expected_layout['height']),
            'map_blocks': rom_blocks == expected_blocks, 'border_blocks': rom_border == expected_border,
            'connections': actual_connections == original_connections,
        }
        return {'name': name, 'map_group': group_id, 'map_number': map_id,
                'header_rom_offset': hex_offset(offset), 'layout_rom_offset': hex_offset(layout_offset),
                'layout_raw_hex': self.rom[layout_offset:layout_offset + 24].hex(),
                'layout_id': actual_id, 'source_layout_id': expected_id,
                'width': width, 'height': height,
                'source_width': expected_layout['width'], 'source_height': expected_layout['height'],
                'map_data_rom_offset': hex_offset(block - BASE), 'map_data_bytes': len(rom_blocks),
                'map_data_sha256': hashlib.sha256(rom_blocks).hexdigest(),
                'source_map_data_sha256': hashlib.sha256(expected_blocks).hexdigest(),
                'border_rom_offset': hex_offset(border - BASE),
                'connections_header_rom_offset': hex_offset(connection_pointer - BASE) if connection_pointer else None,
                'connections_list_rom_offset': hex_offset(connection_table_offset) if connection_table_offset is not None else None,
                'connections': connections, 'source_connections': original_connections,
                'matches': checks, 'all_match': all(checks.values())}

    def run(self):
        discovery = self.discover()
        records = []
        pointers = self.words(self.master, len(self.groups))
        for group_id, (pointer, names) in enumerate(zip(pointers, self.groups)):
            headers = self.words(pointer - BASE, len(names))
            records.extend(self.map_record(name, header, group_id, map_id)
                           for map_id, (name, header) in enumerate(zip(names, headers)))
        by_name = {row['name']: row for row in records}
        graph = {row['name']: [] for row in records}
        for row in records:
            for connection in row['connections']:
                if connection['direction'] in CARDINAL:
                    graph[row['name']].append(connection['target'])
                    graph[connection['target']].append(row['name'])
        queue, main = deque(['LittlerootTown']), {'LittlerootTown'}
        while queue:
            for neighbor in graph[queue.popleft()]:
                if neighbor not in main:
                    main.add(neighbor)
                    queue.append(neighbor)
        cycle_names = ['Route103', 'OldaleTown', 'Route102', 'PetalburgCity', 'Route104',
                       'RustboroCity', 'Route116', 'VerdanturfTown', 'Route117', 'MauvilleCity',
                       'Route110', 'Route103']
        cycle = []
        x, y = 0, 0
        for a, b in zip(cycle_names, cycle_names[1:]):
            source, target = by_name[a], by_name[b]
            connection = next(item for item in source['connections'] if item['target'] == b)
            direction, offset = connection['direction'], connection['offset']
            delta = {'down': (offset, source['height']), 'up': (offset, -target['height']),
                     'left': (-target['width'], offset), 'right': (source['width'], offset)}[direction]
            x, y = x + delta[0], y + delta[1]
            cycle.append({'from': a, 'to': b, 'direction': direction, 'offset': offset,
                          'delta': list(delta), 'cumulative': [x, y], 'connection_rom_offset': connection['rom_offset']})
        rom_sha1 = hashlib.sha1(self.rom).hexdigest()
        return {
            'rom': {'path': str(self.rom_path), 'size': len(self.rom), 'sha1': rom_sha1,
                    'expected_sha1_match': rom_sha1 == EXPECTED_SHA1},
            'source': str(self.source), 'discovery': discovery,
            'method': 'Independent binary pointer traversal using MapLayout/MapHeader/MapConnection structs; no application geometry code imported.',
            'structure_sources': ['include/global.fieldmap.h:157', 'include/global.fieldmap.h:171',
                                  'asm/macros/map.inc:152', 'include/constants/global.h:149', 'src/fieldmap.c:580'],
            'summary': {'maps_verified': len(records), 'maps_fully_matching': sum(row['all_match'] for row in records),
                        'main_cardinal_component_maps': len(main),
                        'main_maps_fully_matching': sum(by_name[name]['all_match'] for name in main),
                        'connection_records_verified': sum(len(row['connections']) for row in records),
                        'main_cardinal_connection_records': sum(connection['direction'] in CARDINAL
                            for name in main for connection in by_name[name]['connections']),
                        'mismatches': [{'name': row['name'], 'checks': [key for key, ok in row['matches'].items() if not ok]}
                                       for row in records if not row['all_match']]},
            'main_component_names': sorted(main),
            'closed_loop_from_rom': {'steps': cycle, 'sum_delta': [x, y],
                'interpretation': 'A single rigid Cartesian embedding would require this closed loop to sum to [0,0]. '
                                  'This loop sums to [0,2] in the original ROM connection data. This does not establish '
                                  'that any individual connection is erroneous; the game stores local transitions, not one global atlas.'},
            'limitations': ['Verifies source-registered map layouts, blocks, borders and connection records. '
                            'Does not compare scripts, tileset artwork or all ROM contents against a compiled source build.',
                            'Map names and group ordering are provided by source map_groups.json; actual table pointers '
                            'and layout IDs validate their structural correspondence.',
                            'This diagnostic does not claim every connection edge is player-traversable.'],
            'maps': records,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, default=DEFAULT_ROM)
    parser.add_argument('--source', type=Path, default=ROOT / 'source/pokeemerald')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = Audit(args.source, args.rom).run()
    if args.report:
        if args.report.resolve().is_relative_to(args.source.resolve()) or args.report.resolve() == args.rom.resolve():
            raise ValueError('Diagnostic output must not overwrite game source or the ROM')
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'rom': report['rom'], 'discovery': report['discovery'], 'summary': report['summary'],
                      'closed_loop_delta': report['closed_loop_from_rom']['sum_delta']}, indent=2))


if __name__ == '__main__':
    main()
