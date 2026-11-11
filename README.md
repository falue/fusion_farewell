# fusion_farewell

Requests exports (Fusion Archive, STEP, … see `-f`) of every Fusion design in the Autodesk Fusion Team hub, then sorts the emailed download links back into the original project/folder structure.

> **NOTE**: ⚠ VIBE CODED ⚠

## How it works

1. `crawler.py` drives a normal Chrome. It walks every project and folder by URL, opens each design, and clicks Export → `<format>` → OK for each chosen format. It reads the project and folder lists from the responses the page loads itself. Each request is appended to `manifest.jsonl` with project, folder path and name.
2. Autodesk emails a link for every export. Save those emails as `.eml` into `emails/`.
3. `fetch_emails.py` reads the emails and matches each file name against the manifest. An email fills the oldest request made before it that has no file yet; if all such requests already have their file, the email is a duplicate and skipped. It downloads to `export/<project>/<folders>/<file>`. Files it can't match go to `export/_unmatched/`. Processed emails are tracked by Message-ID in `downloads.json`.

Both scripts are safe to rerun. The crawler only requests an export when `export/` has no current file for it:
- new designs, folders or projects online
- formats newly added with `-f`
- designs changed online since their export was requested (they're requested and downloaded again)
- requests older than 24 h that never got a file (`Crawler.retry_after`)

Failed documents are retried once at the end of the run, with a screenshot in `logs/`. Every run ends with a summary, also after Ctrl-C.

## Usage

```bash
python3 crawler.py -n 3              # test: first 3 documents
python3 crawler.py -p "My Project"   # one project only
python3 crawler.py                   # everything (Fusion Archive + STEP)
python3 crawler.py -f archive smt    # choose formats: archive inventor smt step stl fbx sketchup obj
python3 crawler.py -f all            # all of them
python3 fetch_emails.py              # download what has arrived in emails/
python3 crawler.py -v archive step   # verify: compare online designs with export/, click nothing
```

Verify lists: missing, outdated (changed online since the export), pending (requested, file not here yet), extra (in `export/` but not online, e.g. renamed or deleted), and same-name designs in one folder. It exits with code 1 if anything is missing or outdated. It accepts the same format names as `-f` (including `all`) and can be combined with `-p` to check one project.

`-n` counts only documents that actually get an export request; already current ones don't count.

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
| `f2d`      | Fusion Archive (on drawings) | `.f2d` |
| `pdf`      | PDF (on drawings, paid plan) | `.pdf`, or downloaded directly |
| `dwg`      | DWG (on drawings, paid plan) | `.dwg`, or downloaded directly |
| `mp4`      | Download (on .mp4 files) | none, downloaded directly |
| `png`      | Download (on .png files) | none, downloaded directly |
| `all`      | all of the above  |                                 |

On the first run, if no Chrome is listening on port 9222, the crawler starts one with `chrome_profile/`. Log in there, then press Enter. The login persists.

- `Download requested.` means the confirmation dialog showed the expected text. Any other dialog text is printed in full as `ERROR:` and listed under `UNEXPECTED DIALOG TEXT` in the summary.
- `FAILED` documents are retried once at the end of the run. A screenshot of the page is saved in `logs/`.

`fetch_emails.py` prints one line per email (`<file> -> export/...`, `skip <file>: already downloaded`, `(ambiguous ...)`, `(no manifest match)`) and ends with the requested exports that still have no file.


## Notes

- Archived projects are not crawled. Restore them in Fusion Team first (All Projects → Archived → Restore), then rerun.
- Only Fusion designs, drawings (`-f f2d pdf dwg`) and uploaded `.mp4`/`.png` files (`-f mp4 png`) are handled. Other files are logged as `skip` and not downloaded.
- Email links expire after 7 days.
- Only `.stp`, `.f3d` and `.f3z` emails have been seen so far. The file extensions for the other formats in `fetch_emails.py` (`KIND`) are guesses. A `.zip` can belong to Inventor, STL, FBX, SketchUp or OBJ, so it's matched to the oldest open request of any of these and saved as `<name>.<kind>.zip`.
- Folders with more than 30 entries are loaded by scrolling; a `WARNING: listed x of y` line means paging didn't finish.
- Designs sharing a name in one folder get a piece of their id in the file name, e.g. `bracket (FHWNfc6bEQ).stp`. Which email belongs to which of them can't be told, so they're assigned by request order and flagged `(ambiguous)`.
