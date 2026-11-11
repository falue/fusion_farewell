"""Download the files linked in Autodesk "Download file" emails (.eml in ./emails) into export/<project>/<folders>/.

Emails only name the file, so each one is matched against manifest.jsonl by name + format; with duplicate
names the oldest still-unmatched request wins. Unmatched files go to export/_unmatched. Safe to rerun.
"""
import email, email.policy, html, json, re, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
EMAILS, EXPORT, MANIFEST, STATE = ROOT / "emails", ROOT / "export", ROOT / "manifest.jsonl", ROOT / "downloads.json"
LINK = re.compile(r"https://cdn[^\"'\s<>]*oss\.api\.autodesk\.com/oss/v2/signedresources/[^\"'\s<>]+")
# extension -> possible export kinds; only .stp and .f3d were seen in real emails so far
KIND = {".stp": {"step"}, ".step": {"step"}, ".f3d": {"archive"}, ".f3z": {"archive"}, ".smt": {"smt"},
        ".ipt": {"inventor"}, ".iam": {"inventor"}, ".stl": {"stl"}, ".fbx": {"fbx"}, ".skp": {"sketchup"},
        ".obj": {"obj"}, ".zip": {"inventor", "stl", "fbx", "sketchup", "obj"}}


def safe(part):
    return re.sub(r'[/\\:]', "_", part).strip() or "_"


def parse(eml):
    """Return (date, download url, filename) of one email, or None if it has no download link."""
    msg = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
    body = msg.get_body(("html",))
    m = body and LINK.search(body.get_content())
    if not m:
        return None
    url = html.unescape(m.group(0))
    disp = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["response-content-disposition"][0]
    name = urllib.parse.unquote(disp.split("''", 1)[1].replace("+", " "))
    return msg["Date"].datetime, url, name


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    used = {tuple(v["entry"]) for v in state.values() if v.get("entry")}
    lines = MANIFEST.read_text().splitlines() if MANIFEST.exists() else []
    entries = sorted(map(json.loads, lines), key=lambda e: e["requested"])
    mails = [(m, p.name) for p in EMAILS.glob("*.eml") if p.name not in state and (m := parse(p))]
    for (_, url, fname), eml_name in sorted(mails, key=lambda x: x[0][0]):
        stem, ext = Path(fname).stem, Path(fname).suffix.lower()
        cands = [e for e in entries if (e["lineage"], e["kind"]) not in used
                 and e["kind"] in KIND.get(ext, ()) and e["name"].casefold() == stem.casefold()]
        if cands:
            e = cands[0]
            used.add((e["lineage"], e["kind"]))
            name = f"{stem}.{e['kind']}.zip" if ext == ".zip" else fname  # zips of different kinds share a name
            target = EXPORT.joinpath(safe(e["project"]), *map(safe, e["path"]), safe(name))
            note = f" (ambiguous: {len(cands)} candidates)" if len(cands) > 1 else ""
        else:
            e, target, note = None, EXPORT / "_unmatched" / safe(fname), " (no manifest match)"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})) as r:
                tmp.write_bytes(r.read())
        except OSError as err:
            print(f"FAILED {eml_name}: {err}")
            if e:
                used.discard((e["lineage"], e["kind"]))
            continue
        tmp.replace(target)
        state[eml_name] = {"file": str(target.relative_to(ROOT)), "entry": [e["lineage"], e["kind"]] if e else None}
        STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
        print(f"{eml_name} -> {target.relative_to(ROOT)}{note}")
    missing = [f"{e['project']}/{'/'.join(e['path'] + [e['name']])} [{e['kind']}]"
               for e in entries if (e["lineage"], e["kind"]) not in used]
    print(f"{len(missing)} requested exports still without email" + "".join(f"\n  {m}" for m in missing))


if __name__ == "__main__":
    main()
