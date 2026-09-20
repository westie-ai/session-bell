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
 "zh-Hans": "任务详情重做:顶部「进展 | 终端」一键切换。进展视图直接显示整段对话——你的每条提示、Claude 的每段回复、每次工具调用,任务跑着的时候会一直往下长;终端视图是原始画面。两边共用一个输入栏。\n欢迎页重写,讲清楚它能帮你做什么,配色和 App 其他页面统一。\n多设备:设置里新增「再加一台手机或 iPad」,扫码或输 6 位数字即可加入同一个空间;欢迎页也能直接加入已有空间。\n更快:Mac 上跑完安装命令后手机几秒内就连上;打开终端最多等 5 秒;Mac 从休眠唤醒后立刻同步。",
 "en-US": "Task detail, rebuilt: switch between Progress and Terminal at the top. Progress shows the whole conversation — every prompt you gave, every reply from Claude, every tool call — and keeps growing while the task runs; Terminal is the raw screen. One input bar for both.\nNew welcome screen that says what the app does for you, in the app's own look.\nMulti-device: Settings › Add another phone or iPad — scan or type 6 digits to join the same space; the welcome screen can join an existing space too.\nFaster: the phone connects within seconds after the install command finishes on the Mac, the terminal answers within 5 s, and a Mac that wakes from sleep syncs right away.",
}
SUBTITLE_ZH = "把 Mac 上的 Claude Code 装进锁屏"
REVIEW_NOTES = ("SessionBell is a companion app for a Mac-side CLI that monitors local AI coding agents (e.g. Claude Code). "
  "Reviewer setup WITHOUT a Mac: launch the app -> tap \"See it in action first\" (second button on the first screen) -> "
  "a demo workspace loads with simulated tasks, so every tab, the task detail (Progress | Terminal views) and the Lock Screen Live Activity "
  "can be reviewed without pairing anything. Pairing a real Mac is one line pasted into Terminal (shown in-app after \"Connect my Mac\"). "
  "Notifications are requested only on the connect screen. The feedback form's contact field is optional and only stored on our server for replying.")

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
 "zh-Hans": "任务详情重做:顶部「进展 | 终端」一键切换。进展视图直接显示整段对话——你的每条提示、Claude 的每段回复、每次工具调用,任务跑着的时候会一直往下长;终端视图是原始画面。两边共用一个输入栏。\n欢迎页重写,讲清楚它能帮你做什么,配色和 App 其他页面统一。\n多设备:设置里新增「再加一台手机或 iPad」,扫码或输 6 位数字即可加入同一个空间;欢迎页也能直接加入已有空间。\n更快:Mac 上跑完安装命令后手机几秒内就连上;打开终端最多等 5 秒;Mac 从休眠唤醒后立刻同步。",
 "en-US": "Task detail, rebuilt: switch between Progress and Terminal at the top. Progress shows the whole conversation — every prompt you gave, every reply from Claude, every tool call — and keeps growing while the task runs; Terminal is the raw screen. One input bar for both.\nNew welcome screen that says what the app does for you, in the app's own look.\nMulti-device: Settings › Add another phone or iPad — scan or type 6 digits to join the same space; the welcome screen can join an existing space too.\nFaster: the phone connects within seconds after the install command finishes on the Mac, the terminal answers within 5 s, and a Mac that wakes from sleep syncs right away.",
}
SUBTITLE_ZH = "把 Mac 上的 Claude Code 装进锁屏"
REVIEW_NOTES = ("SessionBell is a companion app for a Mac-side CLI that monitors local AI coding agents (e.g. Claude Code). "
  "Reviewer setup WITHOUT a Mac: launch the app -> tap \"Not right now — show me the demo\" (second button on the first screen) -> "
  "a demo workspace loads with simulated tasks, so every tab and the Lock Screen Live Activity can be reviewed without pairing anything. "
  "Pairing a real Mac is one line pasted into Terminal (shown in-app after \"I'm at my Mac now\"). "
  "Notifications are requested only on the connect screen. The feedback form's contact field is optional and only stored on our server for replying.")

VERSION = "1.5"
BUILD = "10"
SHOTS = ["1-onboarding.png", "2-tab0.png", "3-tab1.png", "4-detail.png", "5-terminal.png"]
IPAD_SHOTS = ["ipad-tab0.png", "ipad-tab1.png"]

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
        call("PATCH", f"/v1/appStoreVersionLocalizations/{lid}", {"data": {"type": "appStoreVersionLocalizations", "id": lid, "attributes": {"whatsNew": text}}})
        print("whatsNew set", locale)
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
