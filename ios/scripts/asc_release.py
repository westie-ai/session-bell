#!/usr/bin/env python3
"""Minimal App Store Connect API client for release chores (VERSION / BUILD below).
Reads ~/.sessionbell/asc.json (key_id, issuer_id, p8_path). ES256 JWT via `openssl`."""
import base64, hashlib, json, os, subprocess, sys, time, urllib.request, urllib.error

APP_ID = "6801045681"
BASE = "https://api.appstoreconnect.apple.com"
CFG = json.load(open(os.path.expanduser("~/.sessionbell/asc.json")))
P8 = os.path.expanduser(CFG["p8_path"])

def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

_tok = None
def token():
    global _tok
    if _tok and _tok[1] > time.time() + 60: return _tok[0]
    now = int(time.time())
    h = b64u(json.dumps({"alg": "ES256", "kid": CFG["key_id"], "typ": "JWT"}).encode())
    c = b64u(json.dumps({"iss": CFG["issuer_id"], "iat": now, "exp": now + 1100, "aud": "appstoreconnect-v1"}).encode())
    sig_der = subprocess.run(["openssl", "dgst", "-sha256", "-sign", P8], input=f"{h}.{c}".encode(), capture_output=True, check=True).stdout
    # DER → raw r||s
    def der_to_raw(d):
        assert d[0] == 0x30; i = 2
        assert d[i] == 0x02; l = d[i+1]; r = d[i+2:i+2+l]; i += 2 + l
        assert d[i] == 0x02; l = d[i+1]; s = d[i+2:i+2+l]
        r = r.lstrip(b"\x00").rjust(32, b"\x00"); s = s.lstrip(b"\x00").rjust(32, b"\x00")
        return r + s
    _tok = (f"{h}.{c}.{b64u(der_to_raw(sig_der))}", now + 1100)
    return _tok[0]

def call(method, path, body=None, raw=None, headers=None):
    url = path if path.startswith("http") else BASE + path
    data = None
    hdrs = {"Authorization": f"Bearer {token()}"}
    if body is not None:
        data = json.dumps(body).encode(); hdrs["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    if headers: hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} → {e.code}\n{err[:1500]}")

def get_all(path):
    out = []
    while path:
        j = call("GET", path); out += j.get("data", []); path = j.get("links", {}).get("next")
    return out

def upload_screenshot(set_id, filepath):
    """Reserve → PUT parts → commit with md5. Returns the screenshot id."""
    name = os.path.basename(filepath); data = open(filepath, "rb").read()
    res = call("POST", "/v1/appScreenshots", {"data": {"type": "appScreenshots", "attributes": {"fileName": name, "fileSize": len(data)},
              "relationships": {"appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}}}})
    sid = res["data"]["id"]
    for op in res["data"]["attributes"]["uploadOperations"]:
        chunk = data[op["offset"]: op["offset"] + op["length"]]
        hdrs = {h["name"]: h["value"] for h in op["requestHeaders"]}
        req = urllib.request.Request(op["url"], data=chunk, method=op["method"], headers=hdrs)
        with urllib.request.urlopen(req, timeout=300) as r: r.read()
    call("PATCH", f"/v1/appScreenshots/{sid}", {"data": {"type": "appScreenshots", "id": sid,
         "attributes": {"uploaded": True, "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
    return sid

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    if cmd == "discover":
        vers = call("GET", f"/v1/apps/{APP_ID}/appStoreVersions?limit=5&fields[appStoreVersions]=versionString,appVersionState,appStoreState,platform")["data"]
        for v in vers: print("version", v["id"], v["attributes"]["versionString"], v["attributes"].get("appVersionState"))
        latest = vers[0]["id"]
        locs = call("GET", f"/v1/appStoreVersions/{latest}/appStoreVersionLocalizations")["data"]
        for l in locs:
            a = l["attributes"]; print("  loc", l["id"], a["locale"], "| whatsNew:", (a.get("whatsNew") or "")[:60].replace("\n", " "))
            sets = call("GET", f"/v1/appStoreVersionLocalizations/{l['id']}/appScreenshotSets")["data"]
            for st in sets:
                shots = call("GET", f"/v1/appScreenshotSets/{st['id']}/appScreenshots?fields[appScreenshots]=fileName,assetDeliveryState")["data"]
                print("     set", st["id"], st["attributes"]["screenshotDisplayType"], len(shots), [s["attributes"]["fileName"] for s in shots])
        infos = call("GET", f"/v1/apps/{APP_ID}/appInfos")["data"]
        for i in infos:
            il = call("GET", f"/v1/appInfos/{i['id']}/appInfoLocalizations")["data"]
            print("appInfo", i["id"], i["attributes"].get("appStoreState"), [(x["attributes"]["locale"], x["attributes"].get("subtitle")) for x in il])
        rd = call("GET", f"/v1/appStoreVersions/{latest}/appStoreReviewDetail")
        print("reviewDetail notes:", (rd.get("data", {}).get("attributes", {}).get("notes") or "")[:200].replace("\n", " "))
        builds = call("GET", f"/v1/builds?filter[app]={APP_ID}&sort=-uploadedDate&limit=3&fields[builds]=version,processingState,uploadedDate")["data"]
        for b in builds: print("build", b["id"], b["attributes"]["version"], b["attributes"]["processingState"], b["attributes"]["uploadedDate"])

WHATS_NEW = {
 "zh-Hans": "新增 Cursor 支持(macOS):Cursor 编辑器里的 Agent 任务和 Claude Code、Codex 一起出现在任务列表和锁屏面板上——开始、完成、需要批准时都会提醒;进展视图显示完整对话;shell 命令可在锁屏一键批准;任务完成后从手机回一句,会作为下一条消息直接发给 Cursor。Mac 上运行 sessionbell cursor-enable 即可开启。\n每个任务显示正在使用的模型,如「Cursor · grok-4.6」「Claude · fable-5-1」。\n任务列表和锁屏卡片新增 Cursor 图标。",
 "en-US": "Cursor support (macOS): Agent tasks in the Cursor editor show up next to Claude Code and Codex on the Tasks tab and the Lock Screen panel — you're pinged when a task starts, finishes, or needs approval; Progress shows the whole conversation; shell commands can be approved from the Lock Screen; and a reply from the phone after a task finishes is sent to Cursor as the next message. Run sessionbell cursor-enable on the Mac to turn it on.\nEach task now shows the model it is running on, e.g. \"Cursor · grok-4.6\" or \"Claude · fable-5-1\".\nNew Cursor icon on task rows and Lock Screen cards.",
}
# Description edits: None = leave as is. Only plain find/replace pairs applied to the live text,
# so a locale whose description already changed upstream is left alone.
DESCRIPTION_EDITS = {
 "zh-Hans": [("（如 Claude Code）", "（如 Claude Code、Codex）"), ("当前为邀请制。", "")],
 "en-US": [("(Claude Code and more)", "(Claude Code, Codex and more)"), (" Currently invite-only.", "")],
}
SUBTITLE_ZH = "AI 编程 agent 锁屏提醒与遥控"   # 标题/副标题里不能出现 Mac / Claude / Codex(1.5 因 5.2.5 + 4.1(a) 被拒过)
REVIEW_NOTES = ("SessionBell is a companion app for a Mac-side CLI that monitors local AI coding agents (Claude Code, Codex, Cursor). "
  "Reviewer setup WITHOUT a Mac: launch the app -> tap \"See it in action first\" (second button on the first screen) -> "
  "a demo workspace loads with simulated tasks, so every tab, the task detail (Progress | Terminal views) and the Lock Screen Live Activity "
  "can be reviewed without pairing anything. Pairing a real Mac is one line pasted into Terminal (shown in-app after \"Connect my Mac\"). "
  "Codex- and Cursor-specific screens only appear once a Mac running those tools is paired; the UI is otherwise identical to Claude tasks. "
  "Notifications are requested only on the connect screen. The feedback form's contact field is optional and only stored on our server for replying.")

VERSION = "1.7"
BUILD = "12"
SHOTS = ["1-onboarding.png", "2-tab0.png", "3-tab1.png", "4-detail.png", "5-terminal.png"]
IPAD_SHOTS = ["ipad-tab0.png", "ipad-tab1.png"]
SKIP_SHOTS = os.environ.get("SKIP_SHOTS") == "1"   # reuse the screenshots already on the previous version

def prepare():
    # 1. version (create if missing)
    vers = call("GET", f"/v1/apps/{APP_ID}/appStoreVersions?limit=5&fields[appStoreVersions]=versionString,appVersionState")["data"]
    v = next((v for v in vers if v["attributes"]["versionString"] == VERSION), None)
    if not v:
        v = call("POST", "/v1/appStoreVersions", {"data": {"type": "appStoreVersions", "attributes": {"platform": "IOS", "versionString": VERSION},
              "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}}}})["data"]
        print("created", VERSION, v["id"])
    else:
        print(VERSION, "exists", v["id"], v["attributes"].get("appVersionState"))
    vid = v["id"]
    # 2. localizations + what's new
    locs = {l["attributes"]["locale"]: l for l in call("GET", f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations")["data"]}
    for locale, text in WHATS_NEW.items():
        if locale not in locs:
            locs[locale] = call("POST", "/v1/appStoreVersionLocalizations", {"data": {"type": "appStoreVersionLocalizations", "attributes": {"locale": locale},
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})["data"]
            print("created localization", locale)
        lid = locs[locale]["id"]
        attrs = {"whatsNew": text}
        desc = locs[locale]["attributes"].get("description") or ""
        new_desc = desc
        for old, new in DESCRIPTION_EDITS.get(locale, []):
            new_desc = new_desc.replace(old, new)
        if new_desc != desc:
            attrs["description"] = new_desc
        call("PATCH", f"/v1/appStoreVersionLocalizations/{lid}", {"data": {"type": "appStoreVersionLocalizations", "id": lid, "attributes": attrs}})
        print("whatsNew set", locale, "+ description" if "description" in attrs else "")
        if SKIP_SHOTS:
            print("screenshots kept", locale); continue
        # 3. iPhone screenshots: replace the APP_IPHONE_67 set contents
        sets = {s["attributes"]["screenshotDisplayType"]: s for s in call("GET", f"/v1/appStoreVersionLocalizations/{lid}/appScreenshotSets")["data"]}
        st = sets.get("APP_IPHONE_67")
        if not st:
            st = call("POST", "/v1/appScreenshotSets", {"data": {"type": "appScreenshotSets", "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
                "relationships": {"appStoreVersionLocalization": {"data": {"type": "appStoreVersionLocalizations", "id": lid}}}}})["data"]
            print("created iPhone set", locale)
        for old in call("GET", f"/v1/appScreenshotSets/{st['id']}/appScreenshots")["data"]:
            call("DELETE", f"/v1/appScreenshots/{old['id']}")
        folder = os.path.expanduser(f"~/yusen/SessionBell/appstore/screenshots/{'zh-Hans' if locale == 'zh-Hans' else 'en'}")
        ids = []
        for name in SHOTS:
            ids.append(upload_screenshot(st["id"], os.path.join(folder, name))); print("  uploaded", locale, name)
        call("PATCH", f"/v1/appScreenshotSets/{st['id']}/relationships/appScreenshots", {"data": [{"type": "appScreenshots", "id": i} for i in ids]})
        # iPad 13": same replace-all dance
        ip = sets.get("APP_IPAD_PRO_3GEN_129")
        if not ip:
            ip = call("POST", "/v1/appScreenshotSets", {"data": {"type": "appScreenshotSets", "attributes": {"screenshotDisplayType": "APP_IPAD_PRO_3GEN_129"},
                "relationships": {"appStoreVersionLocalization": {"data": {"type": "appStoreVersionLocalizations", "id": lid}}}}})["data"]
            print("created iPad set", locale)
        for old in call("GET", f"/v1/appScreenshotSets/{ip['id']}/appScreenshots")["data"]:
            call("DELETE", f"/v1/appScreenshots/{old['id']}")
        ifolder = os.path.expanduser(f"~/yusen/SessionBell/appstore/ipad/{'zh-Hans' if locale == 'zh-Hans' else 'en'}")
        iids = [upload_screenshot(ip["id"], os.path.join(ifolder, n)) for n in IPAD_SHOTS]
        call("PATCH", f"/v1/appScreenshotSets/{ip['id']}/relationships/appScreenshots", {"data": [{"type": "appScreenshots", "id": i} for i in iids]})
        print("  uploaded iPad", locale, IPAD_SHOTS)
    # 4. subtitle (editable appInfo = the one not READY_FOR_SALE, else the only one)
    infos = call("GET", f"/v1/apps/{APP_ID}/appInfos")["data"]
    editable = next((i for i in infos if i["attributes"].get("appStoreState") != "READY_FOR_SALE"), infos[0])
    for il in call("GET", f"/v1/appInfos/{editable['id']}/appInfoLocalizations")["data"]:
        if il["attributes"]["locale"] == "zh-Hans" and il["attributes"].get("subtitle") != SUBTITLE_ZH:
            call("PATCH", f"/v1/appInfoLocalizations/{il['id']}", {"data": {"type": "appInfoLocalizations", "id": il["id"], "attributes": {"subtitle": SUBTITLE_ZH}}})
            print("subtitle zh-Hans updated on appInfo", editable["id"], editable["attributes"].get("appStoreState"))
    # 5. review notes
    rd = call("GET", f"/v1/appStoreVersions/{vid}/appStoreReviewDetail").get("data")
    if rd:
        call("PATCH", f"/v1/appStoreReviewDetails/{rd['id']}", {"data": {"type": "appStoreReviewDetails", "id": rd["id"], "attributes": {"notes": REVIEW_NOTES}}})
        print("review notes updated")
    else:
        prev = call("GET", f"/v1/appStoreVersions/53a84e84-61ad-4810-980f-777c04824e18/appStoreReviewDetail")["data"]["attributes"]
        attrs = {k: prev.get(k) for k in ["contactFirstName", "contactLastName", "contactPhone", "contactEmail", "demoAccountName", "demoAccountPassword", "demoAccountRequired"] if prev.get(k) is not None}
        attrs["notes"] = REVIEW_NOTES
        call("POST", "/v1/appStoreReviewDetails", {"data": {"type": "appStoreReviewDetails", "attributes": attrs,
             "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})
        print("review detail created")
    print("VERSION_ID", vid)

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "prepare":
    prepare()

def submit(vid):
    # wait for the build to finish processing
    bid = None
    for i in range(90):
        for b in call("GET", f"/v1/builds?filter[app]={APP_ID}&filter[version]={BUILD}&sort=-uploadedDate&limit=3&fields[builds]=version,processingState,uploadedDate")["data"]:
            print(time.strftime("%H:%M:%S"), "build", b["attributes"]["version"], b["attributes"]["processingState"], flush=True)
            if b["attributes"]["processingState"] == "VALID": bid = b["id"]
            elif b["attributes"]["processingState"] in ("FAILED", "INVALID"): raise SystemExit("build processing failed")
        if bid: break
        time.sleep(30)
    if not bid: raise SystemExit(f"build {BUILD} not processed after 45 min")
    call("PATCH", f"/v1/appStoreVersions/{vid}/relationships/build", {"data": {"type": "builds", "id": bid}})
    print("build attached", bid)
    # export compliance is declared in Info.plist; make sure the build isn't blocked on it
    b = call("GET", f"/v1/builds/{bid}?fields[builds]=usesNonExemptEncryption")["data"]["attributes"]
    if b.get("usesNonExemptEncryption") is None:
        call("PATCH", f"/v1/builds/{bid}", {"data": {"type": "builds", "id": bid, "attributes": {"usesNonExemptEncryption": False}}})
        print("export compliance set: no non-exempt encryption")
    # review submission
    subs = call("GET", f"/v1/reviewSubmissions?filter[app]={APP_ID}&filter[state]=READY_FOR_REVIEW,WAITING_FOR_REVIEW,IN_REVIEW,UNRESOLVED_ISSUES")["data"]
    sub = subs[0] if subs else call("POST", "/v1/reviewSubmissions", {"data": {"type": "reviewSubmissions", "attributes": {"platform": "IOS"},
          "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}}}})["data"]
    print("submission", sub["id"], sub["attributes"].get("state"))
    items = call("GET", f"/v1/reviewSubmissions/{sub['id']}/items")["data"]
    if not items:
        call("POST", "/v1/reviewSubmissionItems", {"data": {"type": "reviewSubmissionItems",
             "relationships": {"reviewSubmission": {"data": {"type": "reviewSubmissions", "id": sub["id"]}},
                               "appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}}}})
        print("version added to submission")
    r = call("PATCH", f"/v1/reviewSubmissions/{sub['id']}", {"data": {"type": "reviewSubmissions", "id": sub["id"], "attributes": {"submitted": True}}})
    print("SUBMITTED", r["data"]["attributes"].get("state"))
    v = call("GET", f"/v1/appStoreVersions/{vid}?fields[appStoreVersions]=appVersionState")["data"]["attributes"]
    print("version state:", v.get("appVersionState"))

if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "submit":
    submit(sys.argv[2])
