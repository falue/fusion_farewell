"""Walk all Fusion Team projects in a normal Chrome and request the chosen exports per design.

Every request is appended to manifest.jsonl (project, folder path, name) so fetch_emails.py can sort the
emailed downloads into the original folder structure. On rerun only exports without a current file in export/
are requested: new designs, new formats, designs changed since their export, and requests that got no file
within a day. With -v nothing is clicked; online designs are only compared with export/.
"""
import argparse, asyncio, base64, difflib, json, os, random, re, subprocess, sys, time, urllib.request
from collections import Counter
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from common import ROOT, EXPORT, UNMATCHED, KIND, MANIFEST, safe, stem, target, existing, latest_requests


class Crawler:
    hub_url = "https://filmkulissen.autodesk360.com"
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    profile = ROOT / "chrome_profile"
    logs = ROOT / "logs"  # screenshots of failed documents
    cdp_port = 9222
    formats = {"archive": "Fusion Archive", "inventor": "Inventor", "smt": "SMT", "step": "STEP", "stl": "STL",
               "fbx": "FBX", "sketchup": "SketchUp", "obj": "OBJ"}  # design kind: export menu label
    drawing_formats = {"f2d": "Fusion Archive", "pdf": "PDF", "dwg": "DWG"}  # PDF/DWG need a paid plan
    direct = {"mp4", "png"}  # kinds of uploaded files whose Export button downloads them directly (kind = fileType)
    fusion_mime = "application/vnd.autodesk.fusion360"
    pause = (2.0, 4.0)          # seconds between clicks
    item_pause = (8.0, 15.0)    # seconds between documents
    retry_after = 24 * 3600     # seconds before a request without downloaded file is sent again
    confirmation = re.compile(r"(.+) is being exported to .+? format\. We will send you an email with a link to the "
                              r"exported file once the process is complete\. Note: The link will be active for a "
                              r"period of 7 days from the time you receive the email\.")

    def __init__(self, limit, only_project, kinds, verify):
        self.limit, self.only_project, self.kinds, self.verify = limit, only_project, kinds, verify
        self.exported = 0
        self.requests = latest_requests()
        self.states = Counter()              # status of each (design, format)
        self.issues = {"missing": [], "outdated": [], "pending": [], "collision": [], "extra": []}
        self.failed, self.errors = [], []    # documents that raised, dialogs with unexpected text
        self.found = set()                   # files in export/ that belong to an online design
        self.projects, self.contents = {}, []

    async def goto(self, url):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=90000)

    @staticmethod
    def b64(s):
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

    async def sleep(self, span=None):
        await asyncio.sleep(random.uniform(*(span or self.pause)))

    def ensure_chrome(self):
        """Start a plain Chrome (no automation flags, so no captcha loop) unless one is already listening."""
        base = f"http://127.0.0.1:{self.cdp_port}"
        try:
            tabs = json.load(urllib.request.urlopen(f"{base}/json/list", timeout=2))
            if not any(t["type"] == "page" for t in tabs):  # Chrome without window: attaching would fail
                urllib.request.urlopen(urllib.request.Request(f"{base}/json/new?about:blank", method="PUT"), timeout=5)
            return
        except OSError:
            pass
        subprocess.Popen([self.chrome, f"--remote-debugging-port={self.cdp_port}", f"--user-data-dir={self.profile}",
                          "--no-first-run", "--no-default-browser-check", f"{self.hub_url}/g/all_projects/active"])
        input("Chrome started. Log in until you see all projects, then press Enter here... ")

    async def on_response(self, r):
        if r.request.resource_type not in ("xhr", "fetch") or r.status != 200:
            return
        try:
            if "/hubs/filmkulissen/projects?" in r.url:
                for p in (await r.json())["success"]["body"].get("objects", []):
                    self.projects[p["id"]] = p
            elif "symphonydm/v2/folders/" in r.url and "/content?" in r.url:
                self.contents.append((r.url, (await r.json())["data"]))
        except Exception:  # page navigated away before the body was read
            pass

    async def run(self):
        self.ensure_chrome()
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.cdp_port}")
            self.page = await browser.contexts[0].new_page()
            self.page.on("response", self.on_response)
            try:
                await self.goto(f"{self.hub_url}/g/all_projects/active")
                for _ in range(60):  # the project list arrives a few seconds after the page
                    n = len(self.projects)
                    await asyncio.sleep(3)
                    if n and n == len(self.projects):
                        break
                projects = sorted(self.projects.values(), key=lambda p: p["name"].lower())
                print(f"{len(projects)} projects found")
                for p in self.pick(projects):
                    print(f"== {p['name']}")
                    await self.walk(p, p["data"]["urn"], [])
                    if self.capped():
                        break
                if self.failed:
                    print(f"== retrying {len(self.failed)} failed documents")
                    retry, self.failed = self.failed, []
                    for args in retry:
                        await self.export_logged(*args)
                if self.verify:
                    self.find_extra()
            finally:
                if self.states:  # skip after a wrong -p, nothing ran
                    self.summary()
                await self.page.close()
        return 1 if self.verify and (self.issues["missing"] or self.issues["outdated"]) else 0

    def pick(self, projects):
        """Projects selected by -p (exact name); suggests similar names if there is none."""
        if not self.only_project:
            return projects
        hits = [p for p in projects if p["name"] == self.only_project]
        if not hits:
            key = lambda s: "".join(s.split()).casefold()
            names = {key(p["name"]): p["name"] for p in projects}
            close = difflib.get_close_matches(key(self.only_project), names, n=3, cutoff=0.6)
            print(f'no project named "{self.only_project}"'
                  + (f"; did you mean {' or '.join(repr(names[c]) for c in close)}?" if close else ""))
            raise SystemExit(2)
        return hits

    def capped(self):
        return self.limit is not None and self.exported >= self.limit

    async def list_folder(self, pid, folder_urn):
        """Open the folder in the UI and collect its entries from the responses the page loads itself."""
        self.contents.clear()
        await self.goto(f"{self.hub_url}/g/projects/{pid}/data/{self.b64(folder_urn)}")
        files, folders, total = {}, {}, None
        for _ in range(40):
            await asyncio.sleep(1.5)
            for url, d in self.contents:
                if folder_urn not in url:
                    continue
                total = d.get("totalCount", total)
                files.update((f["id"], f) for f in d.get("files", []))
                folders.update((f["id"], f) for f in d.get("folders", []))
            self.contents.clear()
            if total is not None and len(files) + len(folders) >= total:
                break
            if total is not None:  # more pages: scroll the list to trigger lazy loading
                names = self.page.locator("span.filename")
                if await names.count():
                    await names.last.scroll_into_view_if_needed()
                await self.page.mouse.wheel(0, 3000)
        if total is None or len(files) + len(folders) < (total or 0):
            print(f"  WARNING: listed {len(files) + len(folders)} of {total} entries")
        return list(folders.values()), list(files.values())

    def status(self, project, path, f, fstem, kind):
        """current, pending (requested, file not here yet), missing or outdated (design changed since export)."""
        req = self.requests.get((f["id"], kind))
        file = existing(project, path, fstem, kind)
        if file:
            self.found.add(file)
        ref = req["requested"] if req else None
        basis = ref or (file.stat().st_mtime if file else None)  # time of the version we have or asked for
        if basis is None or f.get("modified", {}).get("on", 0) / 1000 > basis:
            return "outdated" if file else "missing"
        if file and (ref is None or file.stat().st_mtime >= ref):
            return "current"
        return "pending" if time.time() - ref < self.retry_after else "missing"

    def applicable(self, f, ftype):
        """Kinds selected with -f that this file supports."""
        if f.get("mimeType") == self.fusion_mime:
            return [k for k in self.kinds if k in self.formats]
        if ftype == "f2d":
            return [k for k in self.kinds if k in self.drawing_formats]
        return [ftype] if ftype in self.direct and ftype in self.kinds else []

    async def walk(self, p, folder_urn, path):
        folders, files = await self.list_folder(p["id"], folder_urn)
        print(f"  [{'/'.join(path) or '/'}] {len(folders)} folders, {len(files)} files")
        handled = []  # (file, kinds that apply to it, name stem)
        for f in files:
            ftype = (f.get("fileType") or "").lower()
            if kinds := self.applicable(f, ftype):
                handled.append((f, kinds, re.sub(rf"\.{ftype}$", "", f["name"], flags=re.I) if ftype in self.direct else f["name"]))
            else:
                print(f"  skip: {'/'.join(path + [f['name']])} [{ftype}]")
        names = Counter((kinds[0] in self.direct, n.casefold()) for _, kinds, n in handled)
        for (_, n), c in names.items():
            if c > 1:
                self.issues["collision"].append(f"{p['name']}/{'/'.join(path + [n])} ({c} files, they get an id suffix)")
        for f, kinds, name in handled:
            if self.capped():
                return
            fstem = stem(name, f["id"], names[kinds[0] in self.direct, name.casefold()] > 1)
            label = f"{p['name']}/{'/'.join(path + [f['name']])}"
            todo = []
            for kind in kinds:
                s = self.status(p["name"], path, f, fstem, kind)
                self.states[s] += 1
                if s != "current":
                    self.issues[s].append(f"{label} [{kind}]")
                if s in ("missing", "outdated"):
                    todo.append(kind)
            if self.verify or not todo:
                continue
            await self.export_logged(p, folder_urn, path, f, fstem, todo)
            self.exported += 1
            await self.sleep(self.item_pause)
        for sub in folders:
            if self.capped():
                return
            await self.sleep()
            await self.walk(p, sub["id"], path + [sub["name"]])

    async def export_logged(self, p, folder_urn, path, f, fstem, kinds):
        """Export or download, but record a failure (with screenshot) instead of stopping the run."""
        try:
            if kinds[0] in self.direct:
                await self.download(p, folder_urn, path, f, fstem, kinds)
            else:
                await self.export(p, folder_urn, path, f, fstem, kinds)
        except Exception as e:
            reason = str(e).splitlines()[0]
            print(f"    FAILED: {reason}")
            self.logs.mkdir(exist_ok=True)
            try:
                await self.page.screenshot(path=self.logs / f"{safe(p['name'])}_{safe(fstem)}.png")
            except Exception:
                pass
            self.failed.append((p, folder_urn, path, f, fstem, kinds, reason))

    async def open_export(self, p, folder_urn, f):
        """Open the file's page and return its visible Export button."""
        await self.goto(f"{self.hub_url}/g/projects/{p['id']}/data/{self.b64(folder_urn)}/{self.b64(f['id'])}/overview")
        trigger = self.page.locator("[it-id=export-dropdown-trigger]").locator("visible=true")
        try:
            await trigger.wait_for(timeout=60000)
        except PlaywrightTimeout:
            raise RuntimeError("Export button did not appear within 60 s") from None
        await self.sleep()
        return trigger

    async def download(self, p, folder_urn, path, f, fstem, kinds):
        """Uploaded file: the Export button downloads it directly into export/."""
        print(f"  download {'/'.join(path + [f['name']])}")
        trigger = await self.open_export(p, folder_urn, f)
        async with self.page.expect_download(timeout=60000) as info:
            await trigger.click()
        await self.save(await info.value, p, path, fstem, kinds[0])
        kinds.clear()

    async def save(self, download, p, path, fstem, kind):
        """Store a browser download in export/ under the file's name, with the download's extension."""
        ext = os.path.splitext(download.suggested_filename)[1].lower() or "." + kind
        dest = target(p["name"], path, fstem, kind, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        await download.save_as(tmp)
        tmp.replace(dest)
        print(f"    {kind}: downloaded -> {dest.relative_to(ROOT)}")

    async def dialog_or_download(self, ok, kind):
        """After choosing a format: email confirmation dialog (returns None) or direct download (returns it)."""
        dl = asyncio.ensure_future(self.page.wait_for_event("download", timeout=30000))
        dlg = asyncio.ensure_future(ok.wait_for(timeout=30000))
        pending = {dl, dlg}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.exception():
                    for other in pending:
                        other.cancel()
                    return t.result() if t is dl else None
        raise RuntimeError(f"{kind}: neither confirmation dialog nor download within 30 s")

    async def export(self, p, folder_urn, path, f, fstem, kinds):
        """Request each format in kinds; requested ones are removed from kinds, so a retry only does the rest."""
        print(f"  export {'/'.join(path + [f['name']])} -> {kinds}")
        trigger = await self.open_export(p, folder_urn, f)
        for kind in list(kinds):
            await trigger.click()
            await self.sleep()
            label = {**self.formats, **self.drawing_formats}[kind]
            option = self.page.locator(".export-format").locator("visible=true").filter(has_text=label).first
            if await option.locator(".disabled").count():
                print(f"    {kind}: format disabled, skipped")
                await self.page.keyboard.press("Escape")
                kinds.remove(kind)
                continue
            await option.click()
            ok = self.page.locator("[it-id=alert-confirm]").locator("visible=true")
            if download := await self.dialog_or_download(ok, kind):
                await self.save(download, p, path, fstem, kind)
                kinds.remove(kind)
                await self.sleep()
                continue
            msg = " ".join((await self.page.locator(".modal-body").locator("visible=true").inner_text()).split())
            m = self.confirmation.fullmatch(msg)
            if m and m.group(1) == f["name"]:
                print(f"    {kind}: Download requested.")
            else:
                print(f"    {kind}: ERROR: {msg}")
                self.errors.append(f"{p['name']}/{'/'.join(path + [f['name']])} [{kind}]: {msg}")
            await self.sleep()
            await ok.click()
            entry = {"lineage": f["id"], "kind": kind, "project": p["name"], "path": path, "name": f["name"],
                     "requested": time.time(), "message": msg}
            with MANIFEST.open("a") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.requests[(f["id"], kind)] = entry
            kinds.remove(kind)
            await self.sleep()

    def find_extra(self):
        """Files in export/ of the verified formats that belong to no online design."""
        root = EXPORT / safe(self.only_project) if self.only_project else EXPORT
        exts = {ext for ext, kinds in KIND.items() if kinds & set(self.kinds) and ext != ".zip"}
        zips = tuple(f".{k}.zip" for k in self.kinds)
        for p in sorted(root.rglob("*")):
            if p.is_file() and UNMATCHED not in p.parents and p not in self.found \
                    and (p.suffix.lower() in exts or p.name.lower().endswith(zips)):
                self.issues["extra"].append(str(p.relative_to(EXPORT)))

    def summary(self):
        s = self.states
        print(f"\n== summary: {s['current']} current, {s['pending']} pending (requested < "
              f"{self.retry_after // 3600} h ago, no file yet), {s['missing']} missing, {s['outdated']} outdated"
              + ("" if self.verify else f"; {self.exported} documents exported, {len(self.failed)} failed"))
        lists = [("FAILED (rerun to retry)", [f"{p['name']}/{'/'.join(path + [f['name']])} {kinds}: {reason}"
                                              for p, _, path, f, _, kinds, reason in self.failed]),
                 ("UNEXPECTED DIALOG TEXT", self.errors)]
        if self.verify:
            lists += [("MISSING", self.issues["missing"]), ("OUTDATED (changed online since export)",
                      self.issues["outdated"]), ("PENDING (requested, no file yet)", self.issues["pending"]),
                      ("EXTRA (in export/, not online)", self.issues["extra"]),
                      ("SAME NAME IN ONE FOLDER", self.issues["collision"])]
        for title, items in lists:
            if items:
                print(f"{title}:" + "".join(f"\n  {i}" for i in items))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--limit", type=int, help="export at most N documents this run")
    ap.add_argument("-p", "--project", help="only this project (exact name)")
    every = [*Crawler.formats, *Crawler.drawing_formats, *Crawler.direct]
    choices = [*every, "all"]
    ap.add_argument("-f", "--formats", nargs="+", choices=choices, default=["archive", "step"],
                    help="formats to export, or 'all' (default: archive step)")
    ap.add_argument("-v", "--verify", nargs="+", choices=choices, metavar="FORMAT",
                    help="only compare these formats online vs export/, click nothing")
    a = ap.parse_args()
    fmts = a.verify or a.formats
    kinds = every if "all" in fmts else fmts
    sys.exit(asyncio.run(Crawler(a.limit, a.project, kinds, bool(a.verify)).run()))
