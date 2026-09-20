"""Download a pinned, inspectable source snapshot; never executes downloaded code."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
HEADERS = {"User-Agent": "Emerald-Workbench-local-setup"}


def download(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120).read()


def main():
    dest = ROOT / "source" / "pokeemerald"
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("Source already exists; refusing to overwrite your work.")
    provenance_path = ROOT / "source" / "provenance.json"
    pinned = json.loads(provenance_path.read_text("utf-8")) if provenance_path.exists() else None
    if pinned:
        commit = pinned.get("commit", "")
        expected_checksum = pinned.get("archive_sha256", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", expected_checksum):
            raise SystemExit("The pinned source provenance has an invalid commit or checksum.")
    else:
        commit = json.loads(download("https://api.github.com/repos/pret/pokeemerald/commits/master"))["sha"]
        expected_checksum = None
    url = f"https://codeload.github.com/pret/pokeemerald/zip/{commit}"
    blob = download(url)
    checksum = hashlib.sha256(blob).hexdigest()
    if expected_checksum and checksum != expected_checksum:
        raise SystemExit("Downloaded source does not match the pinned archive checksum; nothing was extracted.")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for item in archive.infolist():
            rel = PurePosixPath(item.filename).relative_to(f"pokeemerald-{commit}")
            if ".." in rel.parts:
                raise ValueError("Unsafe archive path")
            target = dest.joinpath(*rel.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    provenance = {"repository": "https://github.com/pret/pokeemerald", "commit": commit,
                  "archive_url": url, "archive_sha256": checksum}
    provenance_path.write_text(json.dumps(provenance, indent=2))
    print(json.dumps({**provenance, "files": sum(p.is_file() for p in dest.rglob("*"))}))


if __name__ == "__main__":
    main()
