# fusion_farewell

Requests exports (Fusion Archive, STEP, … see `-f`) of every Fusion design in the Autodesk Fusion Team hub, then sorts the emailed download links back into the original project/folder structure.

> **NOTE**: ⚠ VIBE CODED ⚠

## How it works

1. `crawler.py` drives a normal Chrome (started with only a debug port, so no captcha loop). It walks every project and folder by URL, opens each design, and clicks Export → <format> → OK for each chosen format. It reads the project and folder lists from the responses the page loads itself. Each request is appended to `manifest.jsonl` with project, folder path and name.
2. Autodesk emails a link for every export. Save those emails as `.eml` into `emails/`.
3. `fetch_emails.py` reads the emails and matches each file name against the manifest (oldest unmatched request first, in case of duplicate names). It downloads to `export/<project>/<folders>/<file>`. Files it can't match go to `export/_unmatched/`. Done emails are tracked in `downloads.json`.

Both scripts are safe to rerun: already requested formats and already downloaded emails are skipped.

## Usage

```bash
python3 crawler.py -n 3              # test: first 3 documents
python3 crawler.py -p "My Project"   # one project only
python3 crawler.py                   # everything (Fusion Archive + STEP)
python3 crawler.py -f archive smt    # choose formats: archive inventor smt step stl fbx sketchup obj
python3 crawler.py -f all            # all of them
python3 fetch_emails.py              # download what has arrived in emails/
```

Formats for `-f` (several can be given, separated by spaces):

| `-f` name  | Export menu entry | Email file matched by           |
|------------|-------------------|---------------------------------|
| `archive`  | Fusion Archive    | `.f3d`, `.f3z`                  |
| `inventor` | Inventor 2025     | `.ipt`, `.iam`, `.zip`          |
| `smt`      | SMT               | `.smt`                          |
| `step`     | STEP              | `.stp`, `.step`                 |
| `stl`      | STL               | `.stl`, `.zip`                  |
| `fbx`      | FBX               | `.fbx`, `.zip`                  |
| `sketchup` | SketchUp          | `.skp`, `.zip`                  |
| `obj`      | OBJ               | `.obj`, `.zip`                  |
| `all`      | all of the above  |                                 |

On the first run, if no Chrome is listening on port 9222, the crawler starts one with `chrome_profile/`. Log in there, then press Enter. The login persists. Pauses are random: 2–4 s between clicks and 8–15 s between documents. Tune them in `Crawler.pause` and `Crawler.item_pause`.

## Notes

- Only Fusion designs (`application/vnd.autodesk.fusion360`) are exported. Other files are logged as `skip` and not downloaded.
- Email links expire after 7 days.
- Only `.stp` and `.f3d` emails have been seen so far. The file extensions for the other formats in `fetch_emails.py` (`KIND`) are guesses. A `.zip` can belong to Inventor, STL, FBX, SketchUp or OBJ, so it's matched to the oldest open request of any of these and saved as `<name>.<kind>.zip`.
- Folders with more than 30 entries are loaded by scrolling; a `WARNING: listed x of y` line means paging didn't finish.
