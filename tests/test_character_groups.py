"""Source-backed appearance families preserve shared art without merging people."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from character_art import CharacterArt
from character_groups import ScriptBattles, build_groups
from tests.test_character_art import make_character_fixture, HIKER, ROXANNE, NPC, NPC_TWO, SOURCE


def asset(path, kind='overworld', category='npcs', character=None, pictures=(), graphics=(), trainers=(), scripts=()):
    label = character or Path(path).stem.title()
    return dict(path=path, kind=kind, category=category, character=label, label=label,
                variant='Emerald', trainer_pic_ids=list(pictures), object_graphics_ids=list(graphics),
                trainer_uses=[{'id': trainer, 'name': trainer} for trainer in trainers],
                map_uses=[{'map': 'Fixture', 'script': script} for script in scripts])


class CharacterGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)

    def write(self, relative, data):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding='utf-8')

    def groups(self, rows):
        return {g['id']: g for g in build_groups(self.source, rows)}

    def test_kindler_and_guitarist_share_overworld_without_merging_battle_portraits(self):
        rows = [asset('kindler.png', 'battle_front', 'trainers', 'Kindler', ['TRAINER_PIC_KINDLER']),
                asset('guitarist.png', 'battle_front', 'trainers', 'Guitarist', ['TRAINER_PIC_GUITARIST']),
                asset('man_5.png', graphics=['OBJ_EVENT_GFX_MAN_5'])]
        self.write('src/data/pokemon/trainer_class_lookups.h', 'const u8 gFacilityClassToPicIndex[] = { [FACILITY_CLASS_KINDLER] = TRAINER_PIC_KINDLER, [FACILITY_CLASS_GUITARIST] = TRAINER_PIC_GUITARIST };')
        self.write('src/battle_tower.c', '''
const u8 gTowerMaleFacilityClasses[2] = { FACILITY_CLASS_KINDLER, FACILITY_CLASS_GUITARIST };
const u8 gTowerMaleTrainerGfxIds[2] = { OBJ_EVENT_GFX_MAN_5, OBJ_EVENT_GFX_MAN_5 };
''')
        groups = self.groups(rows)
        self.assertEqual(set(groups['trainer:kindler']['asset_paths']), {'kindler.png', 'man_5.png'})
        self.assertEqual(set(groups['trainer:guitarist']['asset_paths']), {'guitarist.png', 'man_5.png'})
        self.assertEqual(groups['trainer:kindler']['categories'], ['npcs', 'trainers'])
        self.assertEqual(groups['trainer:kindler']['shared_asset_paths'], ['man_5.png'])
        self.assertEqual(rows[2]['group_ids'], ['trainer:guitarist', 'trainer:kindler'])

    def test_script_branches_and_calls_are_followed_without_text_or_multi_battle_false_matches(self):
        scripts = ScriptBattles([('fixture.inc', '''
Child::
 goto_if_set FLAG_TEST, Shared
 call Fight
 end
Shared::
 call Child
 end
Fight:
 trainerbattle_single TRAINER_ONE, IntroText, LoseText
 end
IntroText:
 .string "trainerbattle_single TRAINER_TEXT_ONLY"
Other::
 trainerbattle_single TRAINER_OTHER, IntroText, LoseText
 end
Partner::
 trainerbattle_multi TRAINER_ENEMY_ONE, TRAINER_ENEMY_TWO
 end
Pyramid::
 trainerbattle TRAINER_BATTLE_PYRAMID, TRAINER_PLACEHOLDER, LOCALID_NONE, IntroText, IntroText
 end
Hill::
 trainerbattle TRAINER_BATTLE_HILL, TRAINER_PLACEHOLDER, LOCALID_NONE, IntroText, IntroText
 end
PartnerSetup::
 trainerbattle TRAINER_BATTLE_SET_TRAINER_A, TRAINER_ENEMY_ONE, LOCALID_NONE, IntroText, IntroText
 end
Family::
 trainerbattle_no_intro TRAINER_FATHER, Defeat
 call EnterMother
 trainerbattle_no_intro TRAINER_MOTHER, Defeat
 goto Grandmother
Grandmother::
 trainerbattle_no_intro TRAINER_GRANDMOTHER, Defeat
 end
Captain::
 addobject LOCALID_GRUNT
 trainerbattle_no_intro TRAINER_GRUNT, Defeat
 end
''')])
        self.assertEqual(scripts.trainers('Child'), {'TRAINER_ONE'})
        self.assertEqual(scripts.trainers('Partner'), set())
        for label in ('Pyramid', 'Hill', 'PartnerSetup'):
            self.assertEqual(scripts.trainers(label), set())
        self.assertEqual(scripts.trainers('Family'), {'TRAINER_FATHER'})
        self.assertEqual(scripts.trainers('Captain'), set())
        self.assertEqual(scripts.trainers(None), set())

    def test_tate_and_liza_are_two_overworlds_with_one_portrait(self):
        rows = [asset('twins.png', 'battle_front', 'gyms', 'Tate And Liza', ['TRAINER_PIC_LEADER_TATE_AND_LIZA'], trainers=['TRAINER_TWINS']),
                asset('tate.png', category='gyms', graphics=['OBJ_EVENT_GFX_TATE'], scripts=['Twins']),
                asset('liza.png', category='gyms', graphics=['OBJ_EVENT_GFX_LIZA'], scripts=['Twins'])]
        self.write('data/maps/Fixture/scripts.inc', 'Twins::\n trainerbattle_double TRAINER_TWINS, Intro, Defeat, TwoMons\n end\n')
        groups = self.groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups['trainer:leader_tate_and_liza']['asset_paths']), {'tate.png', 'liza.png', 'twins.png'})

    def test_source_script_changes_update_group_membership_without_stale_cache(self):
        rows = [asset('kindler.png', 'battle_front', 'trainers', 'Kindler', ['TRAINER_PIC_KINDLER'], trainers=['TRAINER_ONE']),
                asset('hiker.png', 'battle_front', 'trainers', 'Hiker', ['TRAINER_PIC_HIKER'], trainers=['TRAINER_TWO']),
                asset('npc.png', graphics=['OBJ_EVENT_GFX_MAN_5'], scripts=['Child'])]
        self.write('data/maps/Fixture/scripts.inc', 'Child::\n trainerbattle_single TRAINER_ONE, Intro, Defeat\n end\n')
        self.assertIn('npc.png', self.groups(rows)['trainer:kindler']['asset_paths'])
        self.write('data/maps/Fixture/scripts.inc', 'Child::\n trainerbattle_single TRAINER_TWO, Intro, Defeat\n end\n')
        groups = self.groups(rows)
        self.assertNotIn('npc.png', groups['trainer:kindler']['asset_paths'])
        self.assertIn('npc.png', groups['trainer:hiker']['asset_paths'])

    def test_frontier_tables_use_actual_trainer_portrait_even_after_reassignment(self):
        rows = [asset('anabel.png', 'battle_front', 'trainers', 'Anabel', ['TRAINER_PIC_SALON_MAIDEN_ANABEL']),
                asset('hiker.png', 'battle_front', 'trainers', 'Hiker', ['TRAINER_PIC_HIKER'], trainers=['TRAINER_ANABEL']),
                asset('anabel_overworld.png', graphics=['OBJ_EVENT_GFX_ANABEL'])]
        self.write('src/frontier_util.c', '''
static const u8 sFrontierBrainObjEventGfx[NUM][2] = { [FRONTIER_FACILITY_TOWER] = {OBJ_EVENT_GFX_ANABEL, TRUE} };
static const u16 sFrontierBrainTrainerIds[NUM] = { [FRONTIER_FACILITY_TOWER] = TRAINER_ANABEL };
''')
        groups = self.groups(rows)
        self.assertIn('anabel_overworld.png', groups['trainer:hiker']['asset_paths'])
        self.assertNotIn('anabel_overworld.png', groups['trainer:salon_maiden_anabel']['asset_paths'])

    def test_players_keep_all_forms_separate_and_shared_bicycle_reachable_from_both(self):
        rows = []
        for person in ('Brendan', 'May'):
            rows.extend([asset(person + '_walk.png', category='players', character=person, scripts=['Rival']),
                         asset(person + '_front.png', 'battle_front', 'players', person, ['TRAINER_PIC_' + person.upper()], trainers=['TRAINER_' + person.upper()]),
                         asset(person + '_back.png', 'battle_back', 'players', person, ['TRAINER_BACK_PIC_' + person.upper()]),
                         asset(person + '_intro.png', 'intro', 'players', person)])
        rows.append(asset('bicycle.png', 'intro', 'players', 'Both'))
        self.write('data/maps/Fixture/scripts.inc', 'Rival::\n trainerbattle_single TRAINER_MAY, Intro, Defeat\n trainerbattle_single TRAINER_BRENDAN, Intro, Defeat\n end\n')
        groups = self.groups(rows)
        self.assertEqual(set(groups), {'player:brendan', 'player:may'})
        self.assertFalse(any('May_' in path for path in groups['player:brendan']['asset_paths']))
        self.assertIn('bicycle.png', groups['player:brendan']['asset_paths'])
        self.assertEqual(rows[-1]['group_ids'], ['player:brendan', 'player:may'])

    def test_unmatched_npcs_objects_and_back_sheets_remain_accessible_without_guessing(self):
        rows = [asset('npc.png'), asset('object.png', category='objects'), asset('unknown_back.png', 'battle_back', 'trainers')]
        groups = self.groups(rows)
        self.assertEqual({path for group in groups.values() for path in group['asset_paths']}, {row['path'] for row in rows})
        self.assertEqual(groups['npc:npc.png']['description'], 'No battle portrait')
        self.assertIn('no linked overworld', groups['npc:unknown_back.png']['description'])
        self.assertTrue(all(row['group_ids'] for row in rows))

    def test_catalog_uses_current_map_script_and_trainer_source_relationships(self):
        make_character_fixture(self.source)
        self.write('data/maps/TestTown/scripts.inc', 'Child_Script::\n trainerbattle_single TRAINER_HIKER_ONE, Intro, Defeat\n end\n')
        groups = {g['id']: g for g in CharacterArt(self.source).catalog()['groups']}
        self.assertIn(NPC, groups['trainer:hiker']['asset_paths'])
        trainers = self.source / 'src/data/trainers.h'
        trainers.write_text(trainers.read_text(encoding='utf-8').replace('TRAINER_PIC_HIKER', 'TRAINER_PIC_LEADER_ROXANNE'), encoding='utf-8')
        groups = {g['id']: g for g in CharacterArt(self.source).catalog()['groups']}
        self.assertNotIn(NPC, groups['trainer:hiker']['asset_paths'])
        self.assertIn(NPC, groups['trainer:leader_roxanne']['asset_paths'])
        map_path = self.source / 'data/maps/TestTown/map.json'
        data = json.loads(map_path.read_text())
        data['object_events'][0]['graphics_id'] = 'OBJ_EVENT_GFX_GIRL'
        map_path.write_text(json.dumps(data), encoding='utf-8')
        groups = {g['id']: g for g in CharacterArt(self.source).catalog()['groups']}
        self.assertNotIn(NPC, groups['trainer:leader_roxanne']['asset_paths'])
        self.assertIn(NPC_TWO, groups['trainer:leader_roxanne']['asset_paths'])


@unittest.skipUnless((SOURCE / 'src/data/graphics/trainers.h').exists(), 'game source not installed')
class InstalledCharacterGroupsTests(unittest.TestCase):
    def test_real_kindler_gyms_players_shared_sheets_and_full_coverage(self):
        catalog = CharacterArt(SOURCE).catalog()
        groups = {g['id']: g for g in catalog['groups']}
        rows = {a['path']: a for a in catalog['assets']}
        self.assertEqual(set(rows), {path for group in groups.values() for path in group['asset_paths']})
        self.assertTrue(all(a['group_ids'] for a in rows.values()))
        man5 = 'graphics/object_events/pics/people/man_5.png'
        self.assertIn(man5, groups['trainer:kindler']['asset_paths'])
        self.assertIn(man5, groups['trainer:guitarist']['asset_paths'])
        self.assertNotIn('graphics/trainers/front_pics/guitarist.png', groups['trainer:kindler']['asset_paths'])
        sailor_overworlds = [p for p in groups['trainer:sailor']['asset_paths'] if rows[p]['kind'] == 'overworld']
        self.assertEqual(sailor_overworlds, ['graphics/object_events/pics/people/sailor.png'])
        for gid in ('trainer:lass', 'trainer:expert_f', 'trainer:pokefan_f'):
            self.assertNotIn('graphics/object_events/pics/people/man_1.png', groups[gid]['asset_paths'])
        self.assertNotIn('graphics/object_events/pics/people/scientist_1.png', groups['trainer:aqua_grunt_m']['asset_paths'])
        self.assertTrue(all('May' not in rows[p]['character'] for p in groups['player:brendan']['asset_paths']))
        for person in ('tate', 'liza'):
            self.assertIn(f'graphics/object_events/pics/people/gym_leaders/{person}.png', groups['trainer:leader_tate_and_liza']['asset_paths'])
        self.assertIn('Frontier Brain trainer-to-graphics tables', groups['trainer:salon_maiden_anabel']['evidence'])
        for path, row in rows.items():
            for gid in row['group_ids']:
                self.assertIn(path, groups[gid]['asset_paths'])


if __name__ == '__main__':
    unittest.main()
