from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from campaign import Campaign, STARTERS, TRAINERS, PARTIES
from records import _mask


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"
DEPENDENCIES = [STARTERS, TRAINERS, PARTIES, "src/data/pokemon/species_info.h",
                "include/constants/pokemon.h", "src/data/battle_moves.h", "src/data/items.h",
                "include/constants/trainers.h", "include/constants/battle_ai.h", "charmap.txt",
                "data/maps/LittlerootTown/map.json", "data/maps/LittlerootTown/scripts.inc"]


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in DEPENDENCIES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((SOURCE / relative).read_bytes())
        self.editor = Campaign(self.root)

    def apply(self, writes):
        for relative, blob in writes.items():
            (self.root / relative).write_bytes(blob)

    def test_catalog_and_every_party_format_parse(self):
        catalog = self.editor.metadata()
        self.assertEqual(len(catalog["species"]), 386)
        trainers = self.editor.trainers()["trainers"]
        self.assertEqual(len(trainers), 854)
        self.assertEqual(sum(row["gym"] for row in trainers), 40)
        _, text, entries, _ = self.editor._trainer_data()
        seen = set()
        masked = _mask(text)
        for identifier, entry in entries.items():
            party, _, _ = self.editor._party(text, entry, masked)
            fields = self.editor._trainer_fields(entry)
            with self.subTest(identifier=identifier):
                self.editor._validate_party(party, entry["variant"], catalog, fields["doubleBattle"])
                self.editor._validate_fields(fields, catalog)
            seen.add(entry["variant"])
        self.assertEqual(len(seen), 4)

    def test_starters_plan_only_changes_three_species_tokens(self):
        current = self.editor.starters()
        original = (self.root / STARTERS).read_bytes()
        species_definition = (self.root / "src/data/pokemon/species_info.h").read_bytes()
        new = ["SPECIES_BULBASAUR", "SPECIES_CHARMANDER", "SPECIES_SQUIRTLE"]
        writes = self.editor.plan_starters({"revision": current["revision"], "species": new})
        self.assertEqual(set(writes), {STARTERS})
        expected = original
        for old, updated in zip(current["species"], new):
            expected = expected.replace(old.encode(), updated.encode(), 1)
        self.assertEqual(writes[STARTERS], expected)
        self.assertEqual((self.root / STARTERS).read_bytes(), original)
        self.apply(writes)
        self.assertEqual(self.editor.starters()["species"], new)
        self.assertEqual((self.root / "src/data/pokemon/species_info.h").read_bytes(), species_definition)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.editor.plan_starters({"revision": current["revision"], "species": new})

    def test_starters_reject_unknown_species_and_catalog_conflict(self):
        current = self.editor.starters()
        with self.assertRaisesRegex(ValueError, "exactly three"):
            self.editor.plan_starters({**current, "species": ["SPECIES_MISSING"] * 3})
        dependency = self.root / "include/constants/trainers.h"
        dependency.write_bytes(dependency.read_bytes() + b"\n// changed catalog\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.editor.plan_starters(current)

    def test_trainer_edits_name_team_size_and_moves_then_roundtrips(self):
        record = self.editor.trainers("TRAINER_ROXANNE_1")
        old_party = (self.root / PARTIES).read_bytes()
        old_trainers = (self.root / TRAINERS).read_bytes()
        neighbor = self.editor.trainers("TRAINER_BRAWLY_1")
        body = deepcopy(record)
        body["fields"]["trainerName"] = "AVERY"
        body["party"] = [{"iv": 230, "lvl": 18, "species": "SPECIES_PIKACHU", "heldItem": "ITEM_ORAN_BERRY",
                          "moves": ["MOVE_THUNDER_SHOCK", "MOVE_TACKLE", "MOVE_NONE", "MOVE_NONE"]}]
        writes = self.editor.plan_trainer(body)
        self.assertEqual(set(writes), {TRAINERS, PARTIES})
        self.assertEqual((self.root / PARTIES).read_bytes(), old_party)
        self.assertEqual((self.root / TRAINERS).read_bytes(), old_trainers)
        self.apply(writes)
        updated = self.editor.trainers(record["id"])
        self.assertEqual(updated["fields"]["trainerName"], "AVERY")
        self.assertEqual(updated["party"], body["party"])
        self.assertEqual(self.editor.trainers(neighbor["id"])["party"], neighbor["party"])
        self.assertEqual(self.editor.plan_trainer(updated), {})

    def test_team_format_conversion_updates_struct_and_macro_together(self):
        record = self.editor.trainers("TRAINER_SAWYER_1")
        record["variant"] = "ITEM_CUSTOM_MOVES"
        for mon in record["party"]:
            mon.update(heldItem="ITEM_LEFTOVERS", moves=["MOVE_TACKLE", "MOVE_NONE", "MOVE_NONE", "MOVE_NONE"])
        writes = self.editor.plan_trainer(record)
        self.assertIn(b"ITEM_CUSTOM_MOVES(sParty_Sawyer1)", writes[TRAINERS])
        self.assertIn(b"struct TrainerMonItemCustomMoves sParty_Sawyer1", writes[PARTIES])
        self.apply(writes)
        updated = self.editor.trainers(record["id"])
        self.assertEqual(updated["party"], record["party"])
        self.assertEqual(updated["variant"], "ITEM_CUSTOM_MOVES")

    def test_invalid_trainer_values_never_produce_write_plans(self):
        record = self.editor.trainers("TRAINER_ROXANNE_1")
        for field, value in [("trainerName", 'BAD\"NAME'), ("trainerName", 'ELEVENCHARS!'),
                             ("trainerClass", 'TRAINER_CLASS_MADE_UP'), ("aiFlags", '__INJECT()')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.editor.plan_trainer({**record, "fields": {field: value}})
        for key, value in [("lvl", 101), ("iv", True), ("species", "SPECIES_NONE"), ("moves", ["MOVE_NONE"] * 4)]:
            body = deepcopy(record)
            body["party"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.editor.plan_trainer(body)
        body = deepcopy(record)
        body["party"] = body["party"][:1]
        body["fields"]["doubleBattle"] = True
        with self.assertRaisesRegex(ValueError, "double battle"):
            self.editor.plan_trainer(body)

    def test_story_dialogue_edit_preserves_other_scripts_and_checks_revision(self):
        record = self.editor.story("LittlerootTown")
        label = record["dialogues"][0]["label"]
        new_text = r"Welcome, {PLAYER}!\nYour adventure starts here.$"
        writes = self.editor.plan_story({"map": record["map"], "revision": record["revision"],
                                          "dialogues": [{"label": label, "text": new_text}]})
        self.assertEqual(set(writes), {record["source"]})
        self.assertIn(b"LittlerootTown_OnTransition:", writes[record["source"]])
        self.apply(writes)
        updated = self.editor.story(record["map"])
        self.assertEqual(updated["dialogues"][0]["text"], new_text)
        self.assertEqual(updated["dialogues"][1:], record["dialogues"][1:])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.editor.plan_story(record)

    def test_new_npc_dialogue_is_a_complete_linkable_script(self):
        original = self.editor.story("LittlerootTown")
        label = "LittlerootTown_EventScript_WorkbenchNPC1"
        writes = self.editor.plan_new_dialogue(original["map"], label, "A new world awaits!", original["revision"])
        blob = writes[original["source"]]
        self.assertTrue(blob.startswith(original["content"].encode()))
        self.assertIn(label.encode() + b"::", blob)
        self.assertIn(b"msgbox LittlerootTown_Text_WorkbenchNPC1, MSGBOX_DEFAULT", blob)
        self.assertIn(b'A new world awaits!$"', blob)
        self.apply(writes)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.editor.plan_new_dialogue(original["map"], label, "Hello")

    def test_first_conversation_on_blank_map_converts_enter_to_game_newline(self):
        source = "data/maps/LittlerootTown/scripts.inc"
        (self.root / source).write_bytes(b"LittlerootTown_MapScripts::\n\t.byte 0\n")
        record = self.editor.story("LittlerootTown")
        self.assertEqual(record["dialogues"], [])
        label = "LittlerootTown_EventScript_Conversation1"
        writes = self.editor.plan_new_dialogue(record["map"], label, "Hello, {PLAYER}!\r\nWelcome to our town.", record["revision"])
        self.assertIn(b'Hello, {PLAYER}!\\nWelcome to our town.$"', writes[source])
        self.assertEqual((self.root / source).read_bytes(), record["content"].encode())
        self.apply(writes)
        saved = self.editor.story(record["map"])
        self.assertEqual(saved["dialogues"], [{"label": "LittlerootTown_Text_Conversation1", "text": r"Hello, {PLAYER}!\nWelcome to our town.$"}])
        self.assertIn(f"\tmsgbox {saved['dialogues'][0]['label']}, MSGBOX_DEFAULT", saved["content"])

    def test_story_rejects_path_escape_and_missing_map_entrypoint(self):
        with self.assertRaises(ValueError):
            self.editor.story("../../src")
        record = self.editor.story("LittlerootTown")
        with self.assertRaisesRegex(ValueError, "MapScripts"):
            self.editor.plan_story({"map":record["map"],"revision":record["revision"],"content":"Other_MapScripts::\n\t.byte 0\n"})
        with self.assertRaises(ValueError):
            self.editor.plan_new_dialogue("LittlerootTown", "LittlerootTown_EventScript_Test", 'Hi"\n.include "bad"')
        with self.assertRaisesRegex(ValueError, "alphabet"):
            self.editor.plan_new_dialogue("LittlerootTown", "LittlerootTown_EventScript_Test", 'Hello 😀')
        with self.assertRaisesRegex(ValueError, "placeholder"):
            self.editor.plan_new_dialogue("LittlerootTown", "LittlerootTown_EventScript_Test", 'Hello {MISSING_TOKEN}')


if __name__ == "__main__":
    unittest.main()
