"""Conservative scalar editors for the checked-out pokeemerald C data.

This module never writes files and never evaluates C. Callers must save returned
text as UTF-8 without newline translation to preserve the original bytes.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


def _field(key, label, minimum=0, maximum=255):
    return {"key": key, "label": label, "type": "number", "min": minimum, "max": maximum}


_CONFIG = {
    "pokemon": {
        "path": "src/data/pokemon/species_info.h",
        "struct": "SpeciesInfo", "array": "gSpeciesInfo", "prefix": "SPECIES_",
        "fields": [
            _field("baseHP", "HP", 1),
            _field("baseAttack", "Attack", 1),
            _field("baseDefense", "Defense", 1),
            _field("baseSpeed", "Speed", 1),
            _field("baseSpAttack", "Special Attack", 1),
            _field("baseSpDefense", "Special Defense", 1),
            _field("catchRate", "Catch rate"),
            _field("expYield", "Experience yield"),
            _field("friendship", "Starting friendship"),
        ],
    },
    "moves": {
        "path": "src/data/battle_moves.h",
        "struct": "BattleMove", "array": "gBattleMoves", "prefix": "MOVE_",
        "fields": [
            _field("power", "Power"),
            _field("accuracy", "Accuracy (0 means always hit)", 0, 100),
            _field("pp", "PP"),
            _field("priority", "Priority", -128, 127),
            _field("secondaryEffectChance", "Secondary effect chance (%)", 0, 100),
        ],
    },
}
_LEXEMES = re.compile(r'//[^\r\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def _mask(text):
    """Hide comments and quoted punctuation while retaining exact offsets."""
    def replace(match):
        value = match.group()
        fill = " " if value.startswith("/") else "x"
        return "".join(c if c in "\r\n" else fill for c in value)
    masked = _LEXEMES.sub(replace, text)
    if "/*" in masked or '"' in masked or "'" in masked:
        raise ValueError("Unsupported source: unterminated comment or string")
    return masked


def _skip(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _closing(text, start):
    pairs = {"{": "}", "(": ")", "[": "]"}
    stack = []
    for pos in range(start, len(text)):
        char = text[pos]
        if char in pairs:
            stack.append(pairs[char])
        elif char in "})]":
            if not stack or stack.pop() != char:
                raise ValueError("Unsupported source: unbalanced delimiters")
            if not stack:
                return pos
    raise ValueError("Unsupported source: unterminated initializer")


def _members(masked, start, end):
    members = {}
    pos = start
    while (pos := _skip(masked, pos)) < end:
        match = re.match(r"\.([A-Za-z_][A-Za-z_0-9]*)\s*=\s*", masked[pos:end])
        if not match:
            raise ValueError("Unsupported source: expected designated field")
        key = match[1]
        if key in members:
            raise ValueError(f"Unsupported source: duplicate field {key}")
        left = pos + match.end()
        pos = left
        while pos < end and masked[pos] != ",":
            if masked[pos] in "({[":
                pos = _closing(masked, pos)
            elif masked[pos] in ")}]":
                raise ValueError("Unsupported source: unexpected field delimiter")
            pos += 1
        right = pos
        while right > left and masked[right - 1].isspace():
            right -= 1
        if left == right:
            raise ValueError(f"Unsupported source: empty field {key}")
        members[key] = (left, right)
        pos += 1
    return members


@dataclass
class _Entry:
    identifier: str
    values: dict
    spans: dict


class Records:
    """Form revisions include dependencies; source_sha256 identifies file bytes.

    A Pokémon form's sha256 hashes the species file and the constants header
    that supplies STANDARD_FRIENDSHIP. Move forms have no such dependency, so
    their sha256 and source_sha256 are identical. Pass sha256 to update_content;
    use source_sha256 for the caller's final on-disk source-file write check.
    """

    def __init__(self, source: Path):
        self.source = Path(source).resolve()

    def _read(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source):
            raise ValueError("Source path leaves the source directory")
        try:
            raw = path.read_bytes()
            return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Cannot read UTF-8 source {relative}: {exc}") from exc

    def _friendship(self):
        text, digest = self._read("include/constants/pokemon.h")
        matches = re.findall(r"^\s*#define\s+STANDARD_FRIENDSHIP\s+([^\r\n]+)", _mask(text), re.M)
        if len(matches) != 1 or not _DECIMAL.fullmatch(matches[0].strip()):
            raise ValueError("Unsupported STANDARD_FRIENDSHIP definition")
        value = int(matches[0].strip())
        if not 0 <= value <= 255:
            raise ValueError("STANDARD_FRIENDSHIP is outside the byte range")
        return value, digest

    def _load(self, kind):
        if not isinstance(kind, str) or kind not in _CONFIG:
            raise ValueError("Record kind must be pokemon or moves")
        config = _CONFIG[kind]
        text, digest = self._read(config["path"])
        masked = _mask(text)
        pattern = (r"\bconst\s+struct\s+" + config["struct"] + r"\s+" +
                   config["array"] + r"\s*\[[^\]\r\n]*\]\s*=\s*\{")
        arrays = list(re.finditer(pattern, masked))
        if len(arrays) != 1:
            raise ValueError("Unsupported source: expected one known data array")
        start = arrays[0].end() - 1
        end = _closing(masked, start)
        if "#" in masked[start:end] or masked[_skip(masked, end + 1):][:1] != ";":
            raise ValueError("Unsupported source: conditional or malformed data array")
        entries = {}
        seen = set()
        friendship = None
        revision = digest
        if kind == "pokemon":
            friendship, dependency_digest = self._friendship()
            revision = hashlib.sha256(
                ("pokemon\0" + digest + "\0" + dependency_digest).encode("ascii")
            ).hexdigest()
        pos = start + 1
        while (pos := _skip(masked, pos)) < end:
            match = re.match(r"\[(" + config["prefix"] + r"[A-Z0-9_]+)\]\s*=\s*", masked[pos:end])
            if not match:
                raise ValueError("Unsupported source: expected named record initializer")
            identifier = match[1]
            if identifier in seen:
                raise ValueError(f"Unsupported source: duplicate record {identifier}")
            seen.add(identifier)
            pos += match.end()
            excluded = identifier in {"SPECIES_NONE", "MOVE_NONE"} or identifier.startswith("SPECIES_OLD_UNOWN_")
            if masked[pos:pos + 1] == "{":
                close = _closing(masked, pos)
                if close >= end:
                    raise ValueError("Unsupported source: record extends outside array")
                if not excluded:
                    members = _members(masked, pos + 1, close)
                    values, spans = {}, {}
                    for field in config["fields"]:
                        key = field["key"]
                        if key not in members:
                            raise ValueError(f"Unsupported source: {identifier} is missing {key}")
                        left, right = members[key]
                        expression = masked[left:right]
                        if _DECIMAL.fullmatch(expression):
                            value = int(expression)
                        elif key == "friendship" and expression == "STANDARD_FRIENDSHIP":
                            value = friendship
                        else:
                            raise ValueError(f"Unsupported scalar expression in {identifier}.{key}")
                        self._validate(field, value)
                        values[key], spans[key] = value, (left, right)
                    entries[identifier] = _Entry(identifier, values, spans)
                pos = close + 1
            elif identifier.startswith("SPECIES_OLD_UNOWN_") and masked.startswith("OLD_UNOWN_SPECIES_INFO", pos):
                pos += len("OLD_UNOWN_SPECIES_INFO")
            else:
                raise ValueError(f"Unsupported initializer for {identifier}")
            pos = _skip(masked, pos)
            if pos < end:
                if masked[pos] != ",":
                    raise ValueError("Unsupported source: missing record separator")
                pos += 1
        if not entries:
            raise ValueError("Source contains no editable records")
        return config, text, digest, entries, revision

    @staticmethod
    def _validate(field, value):
        if type(value) is not int or not field["min"] <= value <= field["max"]:
            raise ValueError(f'{field["key"]} must be an integer from {field["min"]} to {field["max"]}')

    def _summary(self, kind, identifier):
        token = identifier.removeprefix(_CONFIG[kind]["prefix"])
        names = {"NIDORAN_F": "Nidoran ♀", "NIDORAN_M": "Nidoran ♂", "MR_MIME": "Mr. Mime", "FARFETCHD": "Farfetch’d", "HO_OH": "Ho-Oh"}
        result = {"id": identifier, "name": names.get(token, token.replace("_", " ").title())}
        if kind == "pokemon":
            sprite = Path("graphics") / "pokemon" / token.lower() / "front.png"
            resolved = (self.source / sprite).resolve()
            if resolved.is_relative_to(self.source) and resolved.is_file():
                result["sprite"] = "/asset?path=" + sprite.as_posix()
        return result

    def list_records(self, kind):
        config, _, _, entries, _ = self._load(kind)
        return {"records": [self._summary(kind, key) for key in entries],
                "fields": [dict(field) for field in config["fields"]]}

    def get_record(self, kind, identifier):
        config, _, digest, entries, revision = self._load(kind)
        if not isinstance(identifier, str) or identifier not in entries:
            raise ValueError("Unknown editable record")
        return {**self._summary(kind, identifier), "kind": kind,
                "fields": dict(entries[identifier].values), "source": config["path"],
                "sha256": revision, "source_sha256": digest}

    def update_content(self, kind, identifier, fields, expected_sha256):
        config, text, _, entries, revision = self._load(kind)
        if not isinstance(expected_sha256, str) or expected_sha256 != revision:
            raise ValueError("Source or its constants changed since this form was opened; reload before saving")
        if not isinstance(identifier, str) or identifier not in entries:
            raise ValueError("Unknown editable record")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("Supply at least one editable field")
        definitions = {field["key"]: field for field in config["fields"]}
        entry = entries[identifier]
        replacements = []
        for key, value in fields.items():
            if key not in definitions:
                raise ValueError(f"Unknown or read-only field: {key}")
            self._validate(definitions[key], value)
            # Keep the original token (including STANDARD_FRIENDSHIP) for no-ops.
            if value != entry.values[key]:
                left, right = entry.spans[key]
                replacements.append((left, right, str(value)))
        for left, right, value in sorted(replacements, reverse=True):
            text = text[:left] + value + text[right:]
        return config["path"], text
