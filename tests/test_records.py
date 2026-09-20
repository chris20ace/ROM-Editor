import hashlib
from pathlib import Path
import re
import tempfile
import unittest

from records import Records


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"
SPECIES = "src/data/pokemon/species_info.h"
MOVES = "src/data/battle_moves.h"
CONSTANTS = "include/constants/pokemon.h"


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in (SPECIES, MOVES, CONSTANTS):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SOURCE / relative).read_bytes())
        self.editor = Records(self.root)

    def read(self, relative):
        return (self.root / relative).read_bytes().decode("utf-8")

    def write(self, relative, text):
        (self.root / relative).write_bytes(text.encode("utf-8"))

    def test_counts_and_exclusions_match_source_initializers(self):
        for kind, relative, prefix, expected in (
            ("pokemon", SPECIES, "SPECIES_", 386),
            ("moves", MOVES, "MOVE_", 354),
        ):
            identifiers = set(re.findall(r"\[(" + prefix + r"[A-Z0-9_]+)\]\s*=\s*\{", self.read(relative)))
            identifiers.discard(prefix + "NONE")
            listed = self.editor.list_records(kind)
            self.assertEqual({item["id"] for item in listed["records"]}, identifiers)
            self.assertEqual(len(identifiers), expected)
            self.assertTrue(all(field["type"] == "number" for field in listed["fields"]))

    def test_species_single_field_round_trip_changes_only_numeric_token(self):
        record = self.editor.get_record("pokemon", "SPECIES_BULBASAUR")
        original = self.read(SPECIES)
        relative, edited = self.editor.update_content("pokemon", record["id"], {"baseHP": 60}, record["sha256"])
        self.assertEqual(relative, SPECIES)
        self.assertEqual(edited, original.replace(".baseHP        = 45,", ".baseHP        = 60,", 1))
        self.assertEqual(self.read(SPECIES), original, "The editor must not write files")
        self.write(relative, edited)
        changed = self.editor.get_record("pokemon", record["id"])
        self.assertEqual(changed["fields"], {**record["fields"], "baseHP": 60})
        self.assertEqual(changed["source_sha256"], hashlib.sha256(edited.encode("utf-8")).hexdigest())
        self.assertNotEqual(changed["sha256"], record["sha256"])

    def test_move_edit_preserves_crlf_and_all_other_fields(self):
        original = self.read(MOVES).replace("\r\n", "\n").replace("\n", "\r\n")
        self.write(MOVES, original)
        record = self.editor.get_record("moves", "MOVE_POUND")
        relative, edited = self.editor.update_content("moves", record["id"], {"power": 75}, record["sha256"])
        self.assertEqual(edited, original.replace(".power = 40,", ".power = 75,", 1))
        self.write(relative, edited)
        changed = self.editor.get_record("moves", record["id"])
        self.assertEqual(changed["fields"], {**record["fields"], "power": 75})
        self.assertEqual(changed["sha256"], changed["source_sha256"])

    def test_friendship_constant_is_resolved_and_noop_preserves_token(self):
        record = self.editor.get_record("pokemon", "SPECIES_BULBASAUR")
        self.assertEqual(record["fields"]["friendship"], 70)
        _, unchanged = self.editor.update_content("pokemon", record["id"], record["fields"], record["sha256"])
        self.assertEqual(unchanged, self.read(SPECIES))
        _, changed = self.editor.update_content("pokemon", record["id"], {"friendship": 80}, record["sha256"])
        self.assertEqual(changed, unchanged.replace(".friendship = STANDARD_FRIENDSHIP,", ".friendship = 80,", 1))

    def test_stale_hash_rejected_after_external_edit(self):
        record = self.editor.get_record("moves", "MOVE_POUND")
        self.write(MOVES, self.read(MOVES) + "// external change\n")
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.editor.update_content("moves", record["id"], {"power": 80}, record["sha256"])
        for invalid in (None, "", "0" * 64):
            with self.subTest(hash=invalid), self.assertRaises(ValueError):
                self.editor.update_content("moves", record["id"], {"power": 80}, invalid)

    def test_friendship_dependency_change_rejects_stale_form(self):
        record = self.editor.get_record("pokemon", "SPECIES_BULBASAUR")
        before = self.read(SPECIES)
        self.write(CONSTANTS, self.read(CONSTANTS).replace(
            "#define STANDARD_FRIENDSHIP 70", "#define STANDARD_FRIENDSHIP 80", 1))
        refreshed = self.editor.get_record("pokemon", record["id"])
        self.assertEqual(refreshed["fields"]["friendship"], 80)
        self.assertEqual(refreshed["source_sha256"], record["source_sha256"])
        self.assertNotEqual(refreshed["sha256"], record["sha256"])
        for fields in ({"baseHP": 60}, {**record["fields"], "baseHP": 60}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "constants changed"):
                self.editor.update_content("pokemon", record["id"], fields, record["sha256"])
        self.assertEqual(self.read(SPECIES), before)
        _, edited = self.editor.update_content("pokemon", record["id"],
                                               {**refreshed["fields"], "baseHP": 60}, refreshed["sha256"])
        self.assertEqual(edited, before.replace(".baseHP        = 45,", ".baseHP        = 60,", 1))

    def test_numeric_validation_rejects_code_bool_nan_and_ranges(self):
        record = self.editor.get_record("moves", "MOVE_POUND")
        for fields in (
            {"power": True}, {"power": False}, {"power": float("nan")},
            {"power": float("inf")}, {"power": 40.0}, {"power": "40"},
            {"power": "40; evil()"}, {"power": -1}, {"power": 256},
            {"accuracy": 101}, {"secondaryEffectChance": -1},
            {"priority": -129}, {"priority": 128}, {"pp": None},
            {"type": "TYPE_FIRE"}, {}, [], None,
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.editor.update_content("moves", record["id"], fields, record["sha256"])

    def test_unknown_records_and_kinds_are_rejected(self):
        for kind, identifier in (("pokemon", "SPECIES_NONE"), ("pokemon", "SPECIES_OLD_UNOWN_B"),
                                 ("moves", "MOVE_NONE"), ("other", "MOVE_POUND"),
                                 ("pokemon", "../../secret"), ("moves", None)):
            with self.subTest(kind=kind, identifier=identifier), self.assertRaises(ValueError):
                self.editor.get_record(kind, identifier)

    def test_incompatible_scalar_duplicate_and_conditional_source_rejected(self):
        original = self.read(MOVES)
        mutations = [
            original.replace(".power = 40,", ".power = 20 + 20,", 1),
            original.replace(".power = 40,", ".power = 040,", 1),
            original.replace(".power = 40,", ".power = 40,\n .power = 41,", 1),
            original.replace(".power = 40,", ".otherPower = 40,", 1),
            original.replace("[MOVE_POUND]", "[MOVE_NONE]", 1),
            original.replace("[MOVE_POUND]", "#if 1\n [MOVE_POUND]", 1),
            original.replace("[MOVE_POUND] =", "[MOVE_POUND] = MACRO", 1),
            original + "/* unclosed",
        ]
        for edited in mutations:
            with self.subTest(edit=edited[:80]):
                self.write(MOVES, edited)
                with self.assertRaises(ValueError):
                    self.editor.list_records("moves")

    def test_comments_cannot_impersonate_records_and_are_preserved(self):
        original = self.read(MOVES)
        comment = '/* [MOVE_FAKE] = { .power = 999, }, " } */'
        original = original.replace(".power = 40,", f".power = {comment} 40 /* keep me */,", 1)
        self.write(MOVES, original)
        record = self.editor.get_record("moves", "MOVE_POUND")
        _, edited = self.editor.update_content("moves", record["id"], {"power": 60}, record["sha256"])
        self.assertEqual(edited, original.replace(f"{comment} 40", f"{comment} 60", 1))
        self.assertEqual(len(self.editor.list_records("moves")["records"]), 354)

    def test_sprite_only_returned_if_file_exists(self):
        self.assertNotIn("sprite", self.editor.get_record("pokemon", "SPECIES_BULBASAUR"))
        sprite = self.root / "graphics/pokemon/bulbasaur/front.png"
        sprite.parent.mkdir(parents=True)
        sprite.write_bytes(b"test")
        self.assertEqual(self.editor.get_record("pokemon", "SPECIES_BULBASAUR")["sprite"],
                         "/asset?path=graphics/pokemon/bulbasaur/front.png")


if __name__ == "__main__":
    unittest.main()
