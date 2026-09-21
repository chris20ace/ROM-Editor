"""Read-only checks for source-bound trainer thumbnails and portrait choices."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from campaign import Campaign, TRAINERS


SOURCE = Path(__file__).resolve().parents[1] / "source" / "pokeemerald"


class CampaignPortraitTests(unittest.TestCase):
    def setUp(self):
        self.editor = Campaign(SOURCE)

    def test_every_selectable_picture_has_an_actual_source_preview(self):
        metadata = self.editor.metadata()
        original, _ = self.editor._catalog()
        self.assertEqual({row["id"] for row in metadata["pictures"]},
                         {row["id"] for row in original["pictures"]})
        self.assertGreaterEqual(len(metadata["pictures"]), 93)
        for row in metadata["pictures"]:
            with self.subTest(picture=row["id"]):
                self.assertTrue(row["path"].startswith("graphics/trainers/front_pics/"))
                self.assertTrue((SOURCE / row["path"]).is_file())
                url = urlsplit(row["preview_url"])
                self.assertEqual(url.path, "/api/appearance/preview")
                self.assertEqual(parse_qs(url.query)["path"], [row["path"]])

    def test_list_and_details_use_each_battles_actual_portrait(self):
        catalog = {row["id"]: row for row in self.editor.metadata()["pictures"]}
        rows = self.editor.trainers()["trainers"]
        self.assertTrue(any(row["gym"] for row in rows))
        self.assertTrue(any(row["id"].startswith("TRAINER_MAY_") for row in rows))
        self.assertTrue(any(not row["gym"] for row in rows))
        for row in rows:
            self.assertEqual(row["portrait"], catalog[row["trainerPic"]])
        for identifier in ("TRAINER_ROXANNE_1", "TRAINER_BRAWLY_1", "TRAINER_SAWYER_1"):
            with self.subTest(trainer=identifier):
                record = self.editor.trainers(identifier)
                self.assertEqual(record["portrait"], catalog[record["fields"]["trainerPic"]])

    def test_missing_optional_artwork_keeps_all_valid_portrait_ids(self):
        with patch("campaign.CharacterArt") as artwork:
            artwork.return_value.trainer_pictures.return_value = []
            metadata = self.editor.metadata()
            original, _ = self.editor._catalog()
            self.assertEqual(metadata["pictures"], original["pictures"])
            self.assertEqual(self.editor.trainers("TRAINER_ROXANNE_1")["portrait"]["id"],
                             "TRAINER_PIC_LEADER_ROXANNE")

    def test_artwork_preview_refresh_does_not_invalidate_trainer_form(self):
        picture = {"id": "TRAINER_PIC_LEADER_ROXANNE", "name": "Leader Roxanne",
                   "path": "graphics/trainers/front_pics/roxanne.png"}
        with patch("campaign.CharacterArt") as artwork:
            artwork.return_value.trainer_pictures.return_value = [{**picture, "preview_url": "/preview?v=before"}]
            before = self.editor.trainers("TRAINER_ROXANNE_1")
            artwork.return_value.trainer_pictures.return_value = [{**picture, "preview_url": "/preview?v=after"}]
            after = self.editor.trainers("TRAINER_ROXANNE_1")
        self.assertNotEqual(before["portrait"]["preview_url"], after["portrait"]["preview_url"])
        self.assertEqual(before["revision"], after["revision"])

    def test_selecting_another_portrait_only_plans_that_battle_record(self):
        original = (SOURCE / TRAINERS).read_bytes()
        record = self.editor.trainers("TRAINER_ROXANNE_1")
        body = deepcopy(record)
        body["fields"]["trainerPic"] = "TRAINER_PIC_HIKER"
        planned = self.editor.plan_trainer(body)
        self.assertEqual(set(planned), {TRAINERS})
        self.assertEqual((SOURCE / TRAINERS).read_bytes(), original)
        old = record["fields"]["trainerPic"].encode("utf-8")
        self.assertEqual(planned[TRAINERS].count(old), original.count(old) - 1)
        self.assertEqual(planned[TRAINERS].count(b"TRAINER_PIC_HIKER"),
                         original.count(b"TRAINER_PIC_HIKER") + 1)


if __name__ == "__main__":
    unittest.main()
