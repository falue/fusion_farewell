# fusion_farewell

Requests exports (Fusion Archive, STEP, … see `-f`) of every Fusion design in the Autodesk Fusion Team hub, then sorts the emailed download links back into the original project/folder structure.

> **NOTE**: ⚠ VIBE CODED ⚠

## How it works

1. `crawler.py` drives a normal Chrome (started with only a debug port, so no captcha loop). It walks every project and folder by URL, opens each design, and clicks Export → <format> → OK for each chosen format. It reads the project and folder lists from the responses the page loads itself. Each request is appended to `manifest.jsonl` with project, folder path and name.
2. Autodesk emails a link for every export. Save those emails as `.eml` into `emails/`.
3. `fetch_emails.py` reads the emails and matches each file name against the manifest. An email fills the oldest request made before it that has no file yet; if all such requests already have their file, the email is a duplicate and skipped. It downloads to `export/<project>/<folders>/<file>`, with a 0.5–1.5 s pause between files. Files it can't match go to `export/_unmatched/`. Processed emails are tracked by Message-ID in `downloads.json`.

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

Verify lists: missing, outdated (changed online since the export), pending (requested, file not here yet), extra (in `export/` but not online, e.g. renamed or deleted), and same-name designs in one folder. It exits with code 1 if anything is missing or outdated.

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

On the first run, if no Chrome is listening on port 9222, the crawler starts one with `chrome_profile/`. Log in there, then press Enter. The login persists.

## Output

```
  export pull switch -> ['archive', 'step']
    archive: Download requested.
    step: Download requested.
  export test2 -> ['archive', 'step']
    FAILED: Export button did not appear within 60 s

== summary: 125 current, 78 pending (requested < 24 h ago, no file yet), 4 missing, 0 outdated; 4 documents exported, 1 failed
FAILED (rerun to retry):
  NINE/test2 ['archive', 'step']: Export button did not appear within 60 s
```

- `Download requested.` means the confirmation dialog showed the expected text. Any other dialog text is printed in full as `ERROR:` and listed under `UNEXPECTED DIALOG TEXT` in the summary.
- `FAILED` documents are retried once at the end of the run. A screenshot of the page is saved in `logs/`.

## Timing

Random pauses (class attributes in `Crawler`):

| When | Duration | Setting |
|---|---|---|
| before each click (Export, format, OK) and after OK | 2–4 s | `pause` |
| after each exported document | 8–15 s | `item_pause` |
| before opening each subfolder | 2–4 s | `pause` |

Waits for the site (as short as the page allows, these are timeouts):

| What | Up to |
|---|---|
| page load | 90 s |
| Export button visible | 60 s |
| confirmation dialog | 30 s |
| folder content (checked every 1.5 s) | 60 s |

Designs that are already current are skipped without any pause.

## Notes

- Only Fusion designs (`application/vnd.autodesk.fusion360`) are exported. Other files are logged as `skip` and not downloaded.
- Email links expire after 7 days.
- Only `.stp`, `.f3d` and `.f3z` emails have been seen so far. The file extensions for the other formats in `fetch_emails.py` (`KIND`) are guesses. A `.zip` can belong to Inventor, STL, FBX, SketchUp or OBJ, so it's matched to the oldest open request of any of these and saved as `<name>.<kind>.zip`.
- Folders with more than 30 entries are loaded by scrolling; a `WARNING: listed x of y` line means paging didn't finish.
- Designs sharing a name in one folder get a piece of their id in the file name, e.g. `bracket (FHWNfc6bEQ).stp`. Which email belongs to which of them can't be told, so they're assigned by request order and flagged `(ambiguous)`.
