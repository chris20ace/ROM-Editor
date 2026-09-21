"""Source-evidenced character families for the appearance gallery.

Families are centered on battle portraits, not connected components. A generic
overworld sheet can appear in several families without combining their portraits.
"""
from collections import defaultdict
from pathlib import Path
import re


def _read(path):
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def _without_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def _array(text, name):
    match = re.search(r'\b' + re.escape(name) + r'\s*(?:\[[^\]]*\])+\s*=\s*\{(.*?)\};', text, re.S)
    return match[1] if match else ''


class ScriptBattles:
    """Small read-only graph of event-script branches leading to trainer fights."""
    def __init__(self, sources):
        self.nodes, self.memo = {}, {}
        for relative, text in sources:
            headers = list(re.finditer(r'^\s*([A-Za-z_]\w*)::?\s*(?:@[^\n]*)?$', text, re.M))
            for index, header in enumerate(headers):
                end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
                body = text[header.end():end]
                commands = []
                for line in body.splitlines():
                    line = line.split('@', 1)[0].strip()
                    if line and not line.startswith(('.', '#')):
                        commands.append(line)
                trainers, targets = set(), set()
                inference_stopped = False
                for line in commands:
                    command = line.split()[0]
                    if command == 'addobject':
                        # Spawned-character cutscenes can launch somebody else's
                        # fight (Captain Stern and the Museum grunts). Without
                        # an explicit actor binding, do not guess its identity.
                        inference_stopped = True
                        break
                    if command.startswith('trainerbattle'):
                        # A partner who starts a multi battle is not the enemy
                        # depicted by its TRAINER_* operands (e.g. Steven).
                        # Pyramid/Hill use TRAINER_PHILLIP only as a placeholder;
                        # those opponents are selected dynamically by C code.
                        dynamic = re.search(r'\bTRAINER_BATTLE_(?:PYRAMID|HILL|SET_TRAINER_[AB]|MULTI)\b', line)
                        if 'multi' not in command and not dynamic:
                            trainers.update(re.findall(r'\bTRAINER_(?!BATTLE_)\w+', line))
                            if trainers:
                                # An event can stage later fights with different
                                # actors (the Winstrate family). Those later
                                # portraits do not describe its initiating NPC.
                                inference_stopped = True
                                break
                    if (command in ('goto', 'call', 'case') or command.startswith(('goto_if', 'call_if'))):
                        target = re.search(r'\b([A-Za-z_]\w*)\s*$', line)
                        if target:
                            targets.add(target[1])
                last = commands[-1].split()[0] if commands else ''
                if not inference_stopped and index + 1 < len(headers) and last not in ('end', 'endram', 'return', 'returnram', 'goto', 'gotoram'):
                    targets.add(headers[index + 1][1])
                self.nodes[header[1]] = (trainers, targets, relative)

    def trainers(self, label):
        if not isinstance(label, str):
            return set()
        if label in self.memo:
            return self.memo[label]
        pending, visited, found = [label], set(), set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            node = self.nodes.get(current)
            if node:
                found.update(node[0])
                pending.extend(node[1] - visited)
        self.memo[label] = found
        return found


def build_groups(source, assets):
    """Return stable groups and annotate catalog asset rows with group_ids."""
    source = Path(source)
    by_path = {asset['path']: asset for asset in assets}
    by_graphic, by_trainer = defaultdict(set), defaultdict(set)
    front_groups, groups, assigned = {}, {}, defaultdict(set)

    def group(group_id, label):
        if group_id not in groups:
            groups[group_id] = {'id': group_id, 'label': label, 'asset_paths': set(), 'evidence': set()}
        return groups[group_id]

    def add(group_id, path, evidence):
        groups[group_id]['asset_paths'].add(path)
        groups[group_id]['evidence'].add(evidence)
        assigned[path].add(group_id)

    for asset in assets:
        for graphic in asset.get('object_graphics_ids', []):
            by_graphic[graphic].add(asset['path'])
        if asset['category'] == 'players' and asset.get('character') in ('Brendan', 'May'):
            gid = 'player:' + asset['character'].lower()
            group(gid, asset['character'])
            add(gid, asset['path'], 'Player graphics bindings')
        if asset['kind'] != 'battle_front':
            continue
        picture_ids = asset.get('trainer_pic_ids', [])
        gid = next(iter(assigned[asset['path']]), None)
        if not gid:
            key = next((p.removeprefix('TRAINER_PIC_').lower() for p in picture_ids if p.startswith('TRAINER_PIC_')), Path(asset['path']).stem)
            gid = 'trainer:' + key
            group(gid, asset.get('character') or asset['label'].split(' · ')[0])
            add(gid, asset['path'], 'Trainer portrait table')
        for picture in picture_ids:
            front_groups[picture] = gid
        for trainer in asset.get('trainer_uses', []):
            by_trainer[trainer['id']].add(gid)

    # The common bicycle is deliberately included in both playable characters.
    for asset in assets:
        if asset['category'] == 'players' and asset.get('character') == 'Both':
            for gid in ('player:brendan', 'player:may'):
                if gid in groups:
                    add(gid, asset['path'], 'Shared player graphics binding')
        if asset['kind'] == 'battle_back':
            for picture in asset.get('trainer_pic_ids', []):
                if picture.startswith('TRAINER_BACK_PIC_'):
                    front = 'TRAINER_PIC_' + picture.removeprefix('TRAINER_BACK_PIC_').replace('RUBY_SAPPHIRE_', 'RS_')
                    if front in front_groups:
                        add(front_groups[front], asset['path'], 'Matching front/back trainer identity')

    script_files = set((source / 'data/maps').glob('*/scripts.inc')) | set((source / 'data/scripts').rglob('*.inc'))
    sources = [(path.relative_to(source).as_posix(), _read(path)) for path in sorted(script_files)]
    scripts = ScriptBattles(sources)
    observed_graphics = set()
    for asset in assets:
        # Rival scripts select Brendan or May at runtime. Their exact graphics
        # bindings above retain the correct identity without combining genders.
        if asset['kind'] != 'overworld' or asset['category'] == 'players':
            continue
        for use in asset.get('map_uses', []):
            for trainer in scripts.trainers(use.get('script')):
                for gid in by_trainer.get(trainer, []):
                    add(gid, asset['path'], 'Map event trainer battle')
                    observed_graphics.update(asset.get('object_graphics_ids', []))

    # The Battle Tower explicitly pairs generic overworld graphics with each
    # facility trainer class, including classes which reuse ordinary NPC art.
    tower_path = 'src/battle_tower.c'
    lookup_path = 'src/data/pokemon/trainer_class_lookups.h'
    tower = _without_comments(_read(source / tower_path))
    lookup = _without_comments(_read(source / lookup_path))
    facility_pictures = dict(re.findall(r'\[(FACILITY_CLASS_\w+)\]\s*=\s*(TRAINER_PIC_\w+)', _array(lookup, 'gFacilityClassToPicIndex')))
    for gender in ('Male', 'Female'):
        classes = re.findall(r'\bFACILITY_CLASS_\w+', _array(tower, 'gTower' + gender + 'FacilityClasses'))
        graphics = re.findall(r'\bOBJ_EVENT_GFX_\w+', _array(tower, 'gTower' + gender + 'TrainerGfxIds'))
        if len(classes) != len(graphics):
            continue  # An incomplete source table is not evidence for a guess.
        for facility, graphic in zip(classes, graphics):
            gid = front_groups.get(facility_pictures.get(facility))
            if gid:
                for path in by_graphic[graphic]:
                    add(gid, path, 'Battle Tower class-to-graphics tables')

    frontier = _without_comments(_read(source / 'src/frontier_util.c'))
    brain_graphics = dict(re.findall(r'\[(FRONTIER_FACILITY_\w+)\]\s*=\s*\{\s*(OBJ_EVENT_GFX_\w+)', _array(frontier, 'sFrontierBrainObjEventGfx')))
    brain_trainers = dict(re.findall(r'\[(FRONTIER_FACILITY_\w+)\]\s*=\s*(TRAINER_\w+)', _array(frontier, 'sFrontierBrainTrainerIds')))
    for facility, graphic in brain_graphics.items():
        for gid in by_trainer.get(brain_trainers.get(facility), []):
            for path in by_graphic[graphic]:
                add(gid, path, 'Frontier Brain trainer-to-graphics tables')
                observed_graphics.add(graphic)

    # Explicit named identities cover special/C-driven fights and back sprites;
    # map battle evidence takes precedence when a user has changed a portrait.
    named = {
        'LEADER_TATE_AND_LIZA': ('TATE', 'LIZA'),
        'CHAMPION_WALLACE': ('WALLACE',),
        'AQUA_LEADER_ARCHIE': ('ARCHIE',), 'MAGMA_LEADER_MAXIE': ('MAXIE',),
        'SALON_MAIDEN_ANABEL': ('ANABEL',), 'DOME_ACE_TUCKER': ('TUCKER',),
        'PALACE_MAVEN_SPENSER': ('SPENSER',), 'ARENA_TYCOON_GRETA': ('GRETA',),
        'FACTORY_HEAD_NOLAND': ('NOLAND',), 'PIKE_QUEEN_LUCY': ('LUCY',),
        'PYRAMID_KING_BRANDON': ('BRANDON',),
        'STEVEN': ('STEVEN',), 'WALLY': ('WALLY',), 'RED': ('RED',), 'LEAF': ('LEAF',),
    }
    for picture, gid in front_groups.items():
        suffix = picture.removeprefix('TRAINER_PIC_')
        characters = named.get(suffix, ())
        if suffix.startswith(('LEADER_', 'ELITE_FOUR_')) and suffix != 'LEADER_TATE_AND_LIZA':
            characters = (suffix.removeprefix('LEADER_').removeprefix('ELITE_FOUR_'),)
        for character in characters:
            graphic = 'OBJ_EVENT_GFX_' + character
            if graphic not in observed_graphics:
                for path in by_graphic[graphic]:
                    add(gid, path, 'Named character graphics identity')

    # Single standalone families keep non-trainers, objects and archived sheets
    # accessible. No battle counterpart is invented for an unmatched NPC.
    for asset in assets:
        path = asset['path']
        if not assigned[path]:
            gid = ('object:' if asset['category'] == 'objects' else 'npc:') + path
            group(gid, asset.get('character') or asset['label'].split(' · ')[0])
            add(gid, path, 'Standalone artwork')

    kind_order = {'overworld': 0, 'battle_front': 1, 'battle_back': 2, 'icon': 3, 'intro': 4, 'credits': 5}
    for entry in groups.values():
        entry['asset_paths'] = sorted(entry['asset_paths'], key=lambda p: (kind_order.get(by_path[p]['kind'], 9), by_path[p].get('variant') != 'Emerald', by_path[p]['label'], p))
        members = [by_path[p] for p in entry['asset_paths']]
        entry['categories'] = sorted({a['category'] for a in members})
        entry['evidence'] = sorted(entry['evidence'])
        entry['has_battle'] = any(a['kind'] in ('battle_front', 'battle_back') for a in members)
        entry['has_overworld'] = any(a['kind'] == 'overworld' for a in members)
        entry['shared_asset_paths'] = [p for p in entry['asset_paths'] if len(assigned[p]) > 1]
        entry['description'] = ('Overworld and battle artwork' if entry['has_overworld'] and entry['has_battle'] else
                                'Battle artwork; no linked overworld sheet found' if entry['has_battle'] else
                                'No battle portrait')
        if entry['shared_asset_paths']:
            entry['description'] += '. Some artwork is shared with other characters.'
    for asset in assets:
        asset['group_ids'] = sorted(assigned[asset['path']])
    return sorted(groups.values(), key=lambda entry: ('players' not in entry['categories'], entry['label'].casefold(), entry['id']))
