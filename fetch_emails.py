"""Download the files linked in Autodesk "Download file" emails (.eml in ./emails) into export/<project>/<folders>/.

Emails only name the file, so each one is matched against manifest.jsonl by name + format: it fills the oldest
request made before the email that has no downloaded file yet. If every such request already has its file, the
email is a duplicate and skipped. Unmatched files go to export/_unmatched. Safe to rerun.
"""
import email, email.policy, html, json, random, re, time, urllib.parse, urllib.request
from pathlib import Path
from common import ROOT, KIND, UNMATCHED, safe, target, latest_requests, has_file

EMAILS, STATE = ROOT / "emails", ROOT / "downloads.json"
LINK = re.compile(r"https://cdn[^\"'\s<>]*oss\.api\.autodesk\.com/oss/v2/signedresources/[^\"'\s<>]+")


def parse(eml):
    """Return (message id, date, download url, filename) of one email, or None if it has no download link."""
    msg = email.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
    body = msg.get_body(("html",))
    m = body and LINK.search(body.get_content())
    if not m:
        return None
    url = html.unescape(m.group(0))
    disp = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["response-content-disposition"][0]
    name = urllib.parse.unquote(disp.split("''", 1)[1].replace("+", " "))
    return msg["Message-ID"] or eml.name, msg["Date"].datetime.timestamp(), url, name


def main(pause=(0.5, 1.5), clock_slack=300):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    latest = latest_requests()
    save = lambda: STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    mails = [m for p in sorted(EMAILS.glob("*.eml")) if (m := parse(p))]
    for mid, sent, url, fname in sorted(mails, key=lambda m: m[1]):
        if mid in state:
            continue
        stem, ext = Path(fname).stem, Path(fname).suffix.lower()
        named = sorted((e for e in latest.values() if e["kind"] in KIND.get(ext, ())
                        and e["name"].casefold() == stem.casefold() and e["requested"] <= sent + clock_slack),
                       key=lambda e: e["requested"])
        open_ = [e for e in named if not has_file(e)]
        e = open_[0] if open_ else None
        if e:
            dest = target(e["project"], e["path"], e["stem"], e["kind"], ext)
            n = len({x["lineage"] for x in named})
            note = f" (ambiguous: {n} designs with this name)" if n > 1 else ""
        elif named:
            print(f"skip {fname}: already downloaded")
            state[mid] = {"file": None, "note": "duplicate"}
            save()
            continue
        else:
            dest, note = UNMATCHED / safe(fname), " (no manifest match)"
            if dest.exists():
                print(f"skip {fname}: already in _unmatched")
                state[mid] = {"file": str(dest.relative_to(ROOT)), "note": "duplicate"}
                save()
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})) as r:
                tmp.write_bytes(r.read())
        except OSError as err:
            print(f"FAILED {fname}: {err}")
            continue
        tmp.replace(dest)
        state[mid] = {"file": str(dest.relative_to(ROOT)), "entry": [e["lineage"], e["kind"]] if e else None}
        save()
        print(f"{fname} -> {dest.relative_to(ROOT)}{note}")
        time.sleep(random.uniform(*pause))
    missing = [f"{e['project']}/{'/'.join(e['path'] + [e['name']])} [{e['kind']}]"
               for e in sorted(latest.values(), key=lambda e: e["requested"]) if not has_file(e)]
    print(f"{len(missing)} requested exports still without file" + "".join(f"\n  {m}" for m in missing))


if __name__ == "__main__":
    main()
