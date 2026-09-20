"""Source-backed campaign editors. Every mutation returns a write plan only.

Pokémon definitions are read as a catalog; this module never edits species,
moves, evolutions, learnsets or graphics. The caller owns locking and backups.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from records import Records, _closing, _mask, _members, _skip


STARTERS = "src/starter_choose.c"
TRAINERS = "src/data/trainers.h"
PARTIES = "src/data/trainer_parties.h"
FORMATS = {
    "NO_ITEM_DEFAULT_MOVES": "TrainerMonNoItemDefaultMoves",
    "ITEM_DEFAULT_MOVES": "TrainerMonItemDefaultMoves",
    "NO_ITEM_CUSTOM_MOVES": "TrainerMonNoItemCustomMoves",
    "ITEM_CUSTOM_MOVES": "TrainerMonItemCustomMoves",
}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_DIALOGUE = re.compile(
    r'^(?P<label>[A-Za-z_][A-Za-z_0-9]*)::?[ \t]*\r?\n'
    r'(?P<strings>(?:[ \t]+\.string[ \t]+"(?:\\.|[^"\\\r\n])*"[ \t]*\r?\n)+)', re.M)


def _revision(blobs):
    digest = hashlib.sha256()
    for relative, blob in sorted(blobs.items()):
        digest.update(relative.encode("utf-8") + b"\0" + blob + b"\0")
    return digest.hexdigest()


def _replace(text, replacements):
    for left, right, value in sorted(replacements, reverse=True):
        text = text[:left] + value + text[right:]
    return text


def _label(identifier, prefix=""):
    return identifier.removeprefix(prefix).replace("_", " ").title()


def _list_value(value):
    if not value.startswith("{") or not value.endswith("}"):
        raise ValueError("Unsupported source: expected an initializer list")
    return [part.strip() for part in value[1:-1].split(",") if part.strip()]


class Campaign:
    def __init__(self, source):
        self.source = Path(source).resolve()
        self._catalog_stamp = None
        self._catalog_data = None

    def _read(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source) or not path.is_file():
            raise ValueError("Choose an existing source file inside this project")
        try:
            raw = path.read_bytes()
            return raw, raw.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise ValueError(f"Cannot read {relative}: {error}") from error

    def _catalog(self):
        dependencies = ["src/data/pokemon/species_info.h", "include/constants/pokemon.h",
                        "src/data/battle_moves.h", "src/data/items.h", "include/constants/trainers.h",
                        "include/constants/battle_ai.h", "charmap.txt"]
        blobs = {path: self._read(path)[0] for path in dependencies}
        stamp = _revision(blobs)
        if stamp == self._catalog_stamp:
            return self._catalog_data, stamp
        records = Records(self.source)
        species = records.list_records("pokemon")["records"]
        moves = [{"id": "MOVE_NONE", "name": "None"}] + records.list_records("moves")["records"]
        item_ids = list(dict.fromkeys(re.findall(r"\[(ITEM_[A-Z0-9_]+)\]\s*=", _mask(blobs["src/data/items.h"].decode("utf-8")))))
        trainer_constants = _mask(blobs["include/constants/trainers.h"].decode("utf-8"))
        def constants(prefix):
            return [{"id": token, "name": _label(token, prefix)} for token in
                    dict.fromkeys(re.findall(r"^#define\s+(" + prefix + r"[A-Z0-9_]+)\s+", trainer_constants, re.M))]
        ai = [{"id": token, "name": _label(token, "AI_SCRIPT_")} for token in
              re.findall(r"^#define\s+(AI_SCRIPT_[A-Z0-9_]+)\s+", blobs["include/constants/battle_ai.h"].decode("utf-8"), re.M)]
        result = {"species": species, "moves": moves,
                  "items": [{"id": token, "name": _label(token, "ITEM_")} for token in item_ids],
                  "classes": constants("TRAINER_CLASS_"), "pictures": constants("TRAINER_PIC_"),
                  "music": constants("TRAINER_ENCOUNTER_MUSIC_"), "ai": ai,
                  "partyFormats": list(FORMATS)}
        self._catalog_data, self._catalog_stamp = result, stamp
        return result, stamp

    def metadata(self):
        catalog, _ = self._catalog()
        maps = []
        for path in sorted((self.source / "data/maps").glob("*/scripts.inc")):
            if (path.parent / "map.json").is_file():
                maps.append({"id": path.parent.name, "name": re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", path.parent.name)})
        return {**catalog, "maps": maps, "pokemonDefinitionsProtected": True,
                "notes": {"starters": "Changes the three Birch bag choices. Rival teams, scripted gifts and story references are edited separately.",
                          "trainers": "Each battle and rematch has its own team. Badges, leader dialogue, rewards and progression are map scripts.",
                          "story": "Dialogue forms edit existing text blocks. Full story flow uses the map's event script; compilation and playtesting are still required."}}

    @staticmethod
    def _check_revision(body, revision):
        if not isinstance(body, dict) or body.get("revision") != revision:
            raise ValueError("Source changed since this form was opened. Reload before saving.")

    def _starter_data(self):
        raw, text = self._read(STARTERS)
        masked = _mask(text)
        matches = list(re.finditer(r"\bstatic\s+const\s+u16\s+sStarterMon\s*\[STARTER_MON_COUNT\]\s*=\s*\{", masked))
        if len(matches) != 1:
            raise ValueError("Unsupported starter source: expected one sStarterMon array")
        left = matches[0].end()
        right = _closing(masked, left - 1)
        slots = [entry.strip() for entry in masked[left:right].split(",") if entry.strip()]
        catalog, constants_revision = self._catalog()
        known = {row["id"] for row in catalog["species"]}
        if len(slots) != 3 or any(slot not in known for slot in slots):
            raise ValueError("Unsupported starter source: expected three known Pokémon species")
        revision = _revision({STARTERS: raw, "catalog": constants_revision.encode("ascii")})
        return text, left, right, slots, revision

    def starters(self):
        _, _, _, slots, revision = self._starter_data()
        return {"species": slots, "revision": revision, "source": STARTERS}

    def plan_starters(self, body):
        text, left, right, old, revision = self._starter_data()
        self._check_revision(body, revision)
        slots = body.get("species")
        catalog, _ = self._catalog()
        known = {row["id"] for row in catalog["species"]}
        if not isinstance(slots, list) or len(slots) != 3 or any(not isinstance(s, str) or s not in known for s in slots):
            raise ValueError("Choose exactly three Pokémon from the species catalog")
        if slots == old:
            return {}
        # Replace individual tokens, preserving comments and the surrounding file.
        replacements = [(left + m.start(), left + m.end(), slots[i]) for i, m in
                        enumerate(re.finditer(r"SPECIES_[A-Z0-9_]+", _mask(text)[left:right]))]
        return {STARTERS: _replace(text, replacements).encode("utf-8")}

    def _trainer_data(self):
        raw, text = self._read(TRAINERS)
        party_raw, party_text = self._read(PARTIES)
        masked = _mask(text)
        entries = {}
        for match in re.finditer(r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*\{", masked):
            if match[1] == "TRAINER_NONE":
                continue
            end = _closing(masked, match.end() - 1)
            fields = _members(masked, match.end(), end)
            values = {key: text[a:b] for key, (a, b) in fields.items()}
            party = re.fullmatch(r"([A-Z_]+)\((sParty_[A-Za-z_0-9]+)\)", values.get("party", ""))
            if not party or party[1] not in FORMATS:
                raise ValueError(f"Unsupported party reference in {match[1]}")
            name = re.fullmatch(r'_\("((?:\\.|[^"\\])*)"\)', values.get("trainerName", ""))
            if not name:
                raise ValueError(f"Unsupported trainer name in {match[1]}")
            entries[match[1]] = {"spans": fields, "values": values, "name": name[1],
                                 "variant": party[1], "partyName": party[2]}
        if not entries:
            raise ValueError("No editable trainers found")
        _, catalog_revision = self._catalog()
        revision = _revision({TRAINERS: raw, PARTIES: party_raw, "catalog": catalog_revision.encode("ascii")})
        return text, party_text, entries, revision

    @staticmethod
    def _party(party_text, entry, masked=None):
        masked = _mask(party_text) if masked is None else masked
        pattern = r"static\s+const\s+struct\s+(" + FORMATS[entry["variant"]] + r")\s+" + re.escape(entry["partyName"]) + r"\[\]\s*=\s*\{"
        matches = list(re.finditer(pattern, masked))
        if len(matches) != 1:
            raise ValueError("Unsupported source: trainer party definition missing or ambiguous")
        match = matches[0]
        close = _closing(masked, match.end() - 1)
        pos, mons = match.end(), []
        while (pos := _skip(masked, pos)) < close:
            if masked[pos] != "{":
                raise ValueError("Unsupported source: expected party member")
            end = _closing(masked, pos)
            members = _members(masked, pos + 1, end)
            mon = {}
            for key, (a, b) in members.items():
                value = masked[a:b].strip()
                if key in {"iv", "lvl"} and value.isdecimal():
                    mon[key] = int(value)
                elif key in {"species", "heldItem"} and _IDENTIFIER.fullmatch(value):
                    mon[key] = value
                elif key == "moves":
                    mon[key] = _list_value(value)
                else:
                    raise ValueError("Unsupported source: party member contains an unknown field or expression")
            mons.append(mon)
            pos = _skip(masked, end + 1)
            if pos < close:
                if masked[pos] != ",":
                    raise ValueError("Unsupported source: missing party separator")
                pos += 1
        if not 1 <= len(mons) <= 6:
            raise ValueError("Trainer party must have 1–6 Pokémon")
        return mons, (match.start(1), match.end(1)), (match.end(), close)

    @staticmethod
    def _trainer_fields(entry):
        values = entry["values"]
        return {"trainerName": entry["name"], "trainerClass": values["trainerClass"],
                "trainerPic": values["trainerPic"], "encounterMusic_gender": values["encounterMusic_gender"],
                "doubleBattle": values["doubleBattle"] == "TRUE", "items": _list_value(values["items"]),
                "aiFlags": values["aiFlags"]}

    def trainers(self, identifier=None):
        _, party_text, entries, revision = self._trainer_data()
        if not identifier:
            return {"revision": revision, "trainers": [
                {"id": key, "name": entry["name"], "label": _label(key, "TRAINER_"),
                 "gym": entry["values"]["trainerClass"] == "TRAINER_CLASS_LEADER",
                 "class": entry["values"]["trainerClass"]} for key, entry in entries.items()]}
        if not isinstance(identifier, str) or identifier not in entries:
            raise ValueError("Choose an existing trainer")
        entry = entries[identifier]
        party, _, _ = self._party(party_text, entry)
        return {"id": identifier, "revision": revision, "fields": self._trainer_fields(entry),
                "variant": entry["variant"], "party": party, "partyName": entry["partyName"],
                "sources": [TRAINERS, PARTIES]}

    def _validate_fields(self, fields, catalog):
        if not isinstance(fields, dict):
            raise ValueError("Trainer fields must be an object")
        allowed = {"trainerName", "trainerClass", "trainerPic", "encounterMusic_gender", "doubleBattle", "items", "aiFlags"}
        if set(fields) - allowed:
            raise ValueError("Unknown trainer field")
        rendered = {}
        for key, value in fields.items():
            if key == "trainerName":
                # The game reserves ten bytes; a conservative form avoids control codes.
                if not isinstance(value, str) or not 1 <= len(value) <= 10 or not re.fullmatch(r"[A-Za-z0-9 .,'!?&-]+", value):
                    raise ValueError("Trainer name must be 1–10 letters, digits, spaces or . , ' ! ? & -")
                rendered[key] = '_("' + value + '")'
            elif key in {"trainerClass", "trainerPic"}:
                choices = catalog["classes" if key == "trainerClass" else "pictures"]
                if value not in {row["id"] for row in choices}:
                    raise ValueError(f"Choose a known {key}")
                rendered[key] = value
            elif key == "doubleBattle":
                if type(value) is not bool:
                    raise ValueError("Double battle must be true or false")
                rendered[key] = "TRUE" if value else "FALSE"
            elif key == "items":
                if not isinstance(value, list) or len(value) > 4 or any(not isinstance(v, str) or v not in {r["id"] for r in catalog["items"]} for v in value):
                    raise ValueError("Choose up to four known trainer items")
                rendered[key] = "{" + ", ".join(value) + "}"
            elif key in {"encounterMusic_gender", "aiFlags"}:
                if not isinstance(value, str):
                    raise ValueError(f"Choose known {key} flags")
                tokens = [part.strip() for part in value.split("|")]
                allowed_tokens = {row["id"] for row in catalog["music" if key == "encounterMusic_gender" else "ai"]}
                allowed_tokens.add("F_TRAINER_FEMALE" if key == "encounterMusic_gender" else "0")
                if not tokens or len(tokens) != len(set(tokens)) or any(t not in allowed_tokens for t in tokens):
                    raise ValueError(f"Choose known {key} flags")
                if key == "encounterMusic_gender" and len([t for t in tokens if t != "F_TRAINER_FEMALE"]) != 1:
                    raise ValueError("Choose exactly one encounter music theme")
                rendered[key] = " | ".join(tokens)
        return rendered

    @staticmethod
    def _validate_party(party, variant, catalog, double_battle):
        if not isinstance(party, list) or not (2 if double_battle else 1) <= len(party) <= 6:
            raise ValueError("Choose 1–6 party members, or 2–6 for a double battle")
        known_species = {r["id"] for r in catalog["species"]}
        known_items = {r["id"] for r in catalog["items"]}
        known_moves = {r["id"] for r in catalog["moves"]}
        keys = {"iv", "lvl", "species"}
        if not variant.startswith("NO_ITEM"):
            keys.add("heldItem")
        if "CUSTOM" in variant:
            keys.add("moves")
        for mon in party:
            if not isinstance(mon, dict) or set(mon) != keys:
                raise ValueError("Party fields do not match the selected team format")
            if type(mon["iv"]) is not int or not 0 <= mon["iv"] <= 255:
                raise ValueError("Trainer IV strength must be an integer from 0 to 255")
            if type(mon["lvl"]) is not int or not 1 <= mon["lvl"] <= 100:
                raise ValueError("Party level must be an integer from 1 to 100")
            if not isinstance(mon["species"], str) or mon["species"] not in known_species:
                raise ValueError("Choose a known party species")
            if "heldItem" in keys and (not isinstance(mon["heldItem"], str) or mon["heldItem"] not in known_items):
                raise ValueError("Choose a known held item")
            if "moves" in keys:
                moves = mon["moves"]
                if not isinstance(moves, list) or len(moves) != 4 or any(not isinstance(m, str) or m not in known_moves for m in moves) or all(m == "MOVE_NONE" for m in moves):
                    raise ValueError("Choose four move slots, including at least one usable move")

    def plan_trainer(self, body):
        text, party_text, entries, revision = self._trainer_data()
        self._check_revision(body, revision)
        identifier = body.get("id")
        if not isinstance(identifier, str) or identifier not in entries:
            raise ValueError("Choose an existing trainer")
        entry = entries[identifier]
        if sum(e["partyName"] == entry["partyName"] for e in entries.values()) != 1:
            raise ValueError("This trainer shares a party. Edit shared references explicitly in source.")
        old_party, type_span, body_span = self._party(party_text, entry)
        catalog, _ = self._catalog()
        fields = self._trainer_fields(entry)
        updates = body.get("fields", {})
        rendered = self._validate_fields(updates, catalog)
        fields.update(updates)
        variant = body.get("variant", entry["variant"])
        if not isinstance(variant, str) or variant not in FORMATS:
            raise ValueError("Unknown trainer party format")
        party = body.get("party", old_party)
        self._validate_party(party, variant, catalog, fields["doubleBattle"])
        replacements = [(*entry["spans"][key], value) for key, value in rendered.items() if fields[key] != self._trainer_fields(entry)[key]]
        if variant != entry["variant"]:
            replacements.append((*entry["spans"]["party"], f'{variant}({entry["partyName"]})'))
        writes = {}
        if replacements:
            writes[TRAINERS] = _replace(text, replacements).encode("utf-8")
        if party != old_party or variant != entry["variant"]:
            nl = "\r\n" if "\r\n" in party_text else "\n"
            rows = []
            for mon in party:
                values = [f'        .iv = {mon["iv"]},', f'        .lvl = {mon["lvl"]},', f'        .species = {mon["species"]},']
                if "heldItem" in mon:
                    values.append(f'        .heldItem = {mon["heldItem"]},')
                if "moves" in mon:
                    values.append('        .moves = {' + ", ".join(mon["moves"]) + '},')
                rows.append("    {" + nl + nl.join(values) + nl + "    }")
            party_body = nl + ("," + nl).join(rows) + nl
            changes = [(*body_span, party_body)]
            if variant != entry["variant"]:
                changes.append((*type_span, FORMATS[variant]))
            writes[PARTIES] = _replace(party_text, changes).encode("utf-8")
        return writes

    def _story_path(self, map_name):
        if not isinstance(map_name, str) or not _IDENTIFIER.fullmatch(map_name):
            raise ValueError("Choose a known map")
        relative = f"data/maps/{map_name}/scripts.inc"
        self._read(f"data/maps/{map_name}/map.json")
        self._read(relative)
        return relative

    @staticmethod
    def _dialogues(content):
        result = {}
        for match in _DIALOGUE.finditer(content):
            text = "".join(re.findall(r'\.string[ \t]+"((?:\\.|[^"\\\r\n])*)"', match["strings"]))
            if match["label"] in result:
                raise ValueError("Duplicate dialogue label in this map")
            result[match["label"]] = {"text": text, "span": match.span("strings")}
        return result

    def story(self, map_name):
        relative = self._story_path(map_name)
        raw, content = self._read(relative)
        return {"map": map_name, "source": relative, "revision": _revision({relative: raw}),
                "content": content, "dialogues": [{"label": label, "text": row["text"]} for label, row in self._dialogues(content).items()]}

    def _safe_string(self, text):
        if not isinstance(text, str) or not text or len(text) > 16000 or any(c in text for c in ('\x00', '\r', '\n', '"')):
            raise ValueError('Dialogue must use game control codes (\\n, \\l, \\p), contain no quotes, and fit within 16,000 characters')
        if not text.endswith("$") or "$" in text[:-1]:
            raise ValueError("Dialogue needs one end marker ($), at its end")
        # Prevent an escaped closing quote from changing assembly structure.
        if re.search(r"\\(?![nlp])", text):
            raise ValueError("Simple dialogue supports \\n, \\l and \\p control codes; use the script editor for advanced codes")
        _, charmap = self._read("charmap.txt")
        characters = set()
        for value in re.findall(r"^'((?:\\.|[^'\\])*)'\s*=", charmap, re.M):
            if len(value) == 1:
                characters.add(value)
            elif value == "\\'":
                characters.add("'")
        placeholders = set(re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", charmap, re.M))
        for match in re.finditer(r"\{([^}]*)\}", text):
            if match[1] not in placeholders:
                raise ValueError(f"Unsupported simple dialogue placeholder {{{match[1]}}}; use the full script editor for advanced formatting")
        visible = re.sub(r"\{[^}]*\}|\\[nlp]", "", text)
        unknown = set(visible) - characters
        if unknown:
            raise ValueError("Dialogue contains characters outside the game's text alphabet: " + " ".join(sorted(unknown)))
        return text

    def plan_story(self, body):
        if not isinstance(body, dict):
            raise ValueError("Supply a story update")
        current = self.story(body.get("map"))
        self._check_revision(body, current["revision"])
        content = body.get("content")
        if "dialogues" in body:
            if "content" in body:
                raise ValueError("Save either dialogue edits or full script text")
            rows = body["dialogues"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("Supply dialogue edits")
            known = self._dialogues(current["content"])
            replacements, seen = [], set()
            nl = "\r\n" if "\r\n" in current["content"] else "\n"
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("label"), str) or row["label"] not in known or row["label"] in seen:
                    raise ValueError("Choose a unique existing dialogue label")
                seen.add(row["label"])
                value = self._safe_string(row.get("text"))
                existing = known[row["label"]]
                if value != existing["text"]:
                    replacements.append((*existing["span"], '\t.string "' + value + '"' + nl))
            content = _replace(current["content"], replacements)
        if not isinstance(content, str) or len(content.encode("utf-8")) > 4 * 1024 * 1024 or "\x00" in content:
            raise ValueError("Story source must be UTF-8 text under 4 MiB without null bytes")
        if not re.search(r"^" + re.escape(body["map"]) + r"_MapScripts::?", content, re.M):
            raise ValueError("Keep this map's MapScripts entry label so its header can still link")
        return {} if content == current["content"] else {current["source"]: content.encode("utf-8")}

    def plan_new_dialogue(self, map_name, label, text, expected_revision=None):
        current = self.story(map_name)
        if expected_revision is not None:
            self._check_revision({"revision": expected_revision}, current["revision"])
        if not isinstance(label, str) or not _IDENTIFIER.fullmatch(label) or not label.startswith(map_name + "_EventScript_"):
            raise ValueError("NPC script label must start with this map's name followed by _EventScript_")
        text_label = label.replace("_EventScript_", "_Text_", 1)
        for path in (self.source / "data/maps").glob("*/scripts.inc"):
            content = path.read_text(encoding="utf-8")
            if re.search(r"^(?:" + re.escape(label) + "|" + re.escape(text_label) + r")::?", content, re.M):
                raise ValueError("That NPC script or text label already exists")
        if isinstance(text, str):
            text = text.replace("\r\n", "\n").replace("\n", "\\n")
            if not text.endswith("$"):
                text += "$"
        text = self._safe_string(text)
        nl = "\r\n" if "\r\n" in current["content"] else "\n"
        appended = nl.join(["", "", label + "::", "\tlock", "\tfaceplayer", f"\tmsgbox {text_label}, MSGBOX_DEFAULT",
                             "\trelease", "\tend", "", text_label + "::", '\t.string "' + text + '"', ""])
        return {current["source"]: (current["content"] + appended).encode("utf-8")}
