"""Walk all Fusion Team projects in a normal Chrome and request the chosen exports per design.

Every request is appended to manifest.jsonl (project, folder path, name) so fetch_emails.py can sort the
emailed downloads into the original folder structure. Already requested items are skipped on rerun.
"""
import argparse, asyncio, base64, json, random, subprocess, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
MANIFEST = ROOT / "manifest.jsonl"


class Crawler:
    hub_url = "https://filmkulissen.autodesk360.com"
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    profile = ROOT / "chrome_profile"
    cdp_port = 9222
    formats = {"archive": "Fusion Archive", "inventor": "Inventor", "smt": "SMT", "step": "STEP", "stl": "STL",
               "fbx": "FBX", "sketchup": "SketchUp", "obj": "OBJ"}  # kind: export menu label
    fusion_mime = "application/vnd.autodesk.fusion360"
    pause = (2.0, 4.0)        # seconds between clicks
    item_pause = (8.0, 15.0)  # seconds between documents

    async def goto(self, url):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=90000)

    def __init__(self, limit, only_project, kinds):
        self.limit, self.only_project, self.kinds = limit, only_project, kinds
        self.exported = 0
        self.done = set()
        if MANIFEST.exists():
            for line in MANIFEST.read_text().splitlines():
                e = json.loads(line)
                self.done.add((e["lineage"], e["kind"]))
        self.projects, self.contents = {}, []

    @staticmethod
    def b64(s):
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

    async def sleep(self, span=None):
        await asyncio.sleep(random.uniform(*(span or self.pause)))

    def ensure_chrome(self):
        """Start a plain Chrome (no automation flags, so no captcha loop) unless one is already listening."""
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=2)
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
            ctx = browser.contexts[0]
            self.page = await ctx.new_page()
            self.page.on("response", self.on_response)
            await self.goto(f"{self.hub_url}/g/all_projects/active")
            await asyncio.sleep(8)
            projects = sorted(self.projects.values(), key=lambda p: p["name"].lower())
            print(f"{len(projects)} projects found")
            try:
                for p in projects:
                    if self.only_project and p["name"] != self.only_project:
                        continue
                    print(f"== {p['name']}")
                    await self.walk(p, p["data"]["urn"], [])
                    if self.capped():
                        break
            finally:
                await self.page.close()
        print(f"done, {self.exported} documents exported this run")

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

    async def walk(self, p, folder_urn, path):
        folders, files = await self.list_folder(p["id"], folder_urn)
        print(f"  [{'/'.join(path) or '/'}] {len(folders)} folders, {len(files)} files")
        for f in files:
            if self.capped():
                return
            if f.get("mimeType") != self.fusion_mime:
                print(f"  skip (not a Fusion design): {'/'.join(path + [f['name']])} [{f.get('fileType')}]")
                continue
            kinds = [k for k in self.kinds if (f["id"], k) not in self.done]
            if not kinds:
                continue
            try:
                await self.export(p, folder_urn, path, f, kinds)
            except Exception as e:  # keep a long run going; a rerun retries the missing formats
                print(f"    FAILED: {str(e).splitlines()[0]}")
            self.exported += 1
            await self.sleep(self.item_pause)
        for sub in folders:
            if self.capped():
                return
            await self.sleep()
            await self.walk(p, sub["id"], path + [sub["name"]])

    async def export(self, p, folder_urn, path, f, kinds):
        print(f"  export {'/'.join(path + [f['name']])} -> {kinds}")
        await self.goto(f"{self.hub_url}/g/projects/{p['id']}/data/{self.b64(folder_urn)}/{self.b64(f['id'])}/overview")
        trigger = self.page.locator("[it-id=export-dropdown-trigger]").locator("visible=true")
        await trigger.wait_for(timeout=60000)
        await self.sleep()
        for kind in kinds:
            await trigger.click()
            await self.sleep()
            option = self.page.locator(".export-format").locator("visible=true").filter(has_text=self.formats[kind]).first
            if await option.locator(".disabled").count():
                print(f"    {kind}: format disabled, skipped")
                await self.page.keyboard.press("Escape")
                continue
            await option.click()
            ok = self.page.locator("[it-id=alert-confirm]").locator("visible=true")
            await ok.wait_for(timeout=30000)
            msg = " ".join((await self.page.locator(".modal-body").locator("visible=true").inner_text()).split())
            print(f"    {kind}: {msg[:90]}")
            await self.sleep()
            await ok.click()
            with MANIFEST.open("a") as fh:
                fh.write(json.dumps({"lineage": f["id"], "kind": kind, "project": p["name"], "path": path,
                                     "name": f["name"], "requested": time.time(), "message": msg},
                                    ensure_ascii=False) + "\n")
            self.done.add((f["id"], kind))
            await self.sleep()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--limit", type=int, help="export at most N documents this run")
    ap.add_argument("-p", "--project", help="only this project (exact name)")
    ap.add_argument("-f", "--formats", nargs="+", choices=[*Crawler.formats, "all"], default=["archive", "step"],
                    help="formats to export, or 'all' (default: archive step)")
    a = ap.parse_args()
    asyncio.run(Crawler(a.limit, a.project, list(Crawler.formats) if "all" in a.formats else a.formats).run())
