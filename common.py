"""Shared by crawler.py and fetch_emails.py: where an export lives in export/ and what was requested."""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent
EXPORT, MANIFEST = ROOT / "export", ROOT / "manifest.jsonl"
UNMATCHED = EXPORT / "_unmatched"
# email file extension -> possible export kinds; only .stp and .f3d were seen in real emails so far
KIND = {".stp": {"step"}, ".step": {"step"}, ".f3d": {"archive"}, ".f3z": {"archive"}, ".smt": {"smt"},
        ".ipt": {"inventor"}, ".iam": {"inventor"}, ".stl": {"stl"}, ".fbx": {"fbx"}, ".skp": {"sketchup"},
        ".obj": {"obj"}, ".zip": {"inventor", "stl", "fbx", "sketchup", "obj"}, ".mp4": {"mp4"}}


def safe(part):
    return re.sub(r'[/\\:]', "_", part).strip() or "_"


def stem(name, lineage, clash):
    """File name stem of a design; designs sharing a name in one folder get a piece of their id."""
    return f"{name} ({lineage[-10:]})" if clash else name


def target(project, path, stem, kind, ext):
    """Local file for one export; zips get the kind in their name because several kinds arrive as .zip."""
    fname = f"{stem}.{kind}.zip" if ext == ".zip" else stem + ext
    return EXPORT.joinpath(safe(project), *map(safe, path), safe(fname))


def existing(project, path, stem, kind):
    """The downloaded file of this export, if any."""
    for ext, kinds in KIND.items():
        if kind in kinds and (p := target(project, path, stem, kind, ext)).exists():
            return p


def latest_requests():
    """Newest manifest entry per (lineage, kind), with its file name stem in e["stem"]."""
    latest = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text().splitlines(keepends=True):
            if not line.endswith("\n"):  # crawler may be writing this line right now
                continue
            e = json.loads(line)
            key = (e["lineage"], e["kind"])
            if key not in latest or e["requested"] > latest[key]["requested"]:
                latest[key] = e
    folders = {}
    for e in latest.values():
        folders.setdefault((e["project"], tuple(e["path"]), e["name"].casefold()), set()).add(e["lineage"])
    for e in latest.values():
        e["stem"] = stem(e["name"], e["lineage"], len(folders[e["project"], tuple(e["path"]), e["name"].casefold()]) > 1)
    return latest


def has_file(e):
    """True if the export requested by manifest entry e was downloaded after that request."""
    p = existing(e["project"], e["path"], e["stem"], e["kind"])
    return p is not None and p.stat().st_mtime >= e["requested"]
