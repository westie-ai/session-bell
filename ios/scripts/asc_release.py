#!/usr/bin/env python3
"""Minimal App Store Connect API client for the 1.4 release chores.
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
 "zh-Hans": "全新接入:打开 App,告诉它你在不在 Mac 前,然后在 Mac 终端里粘一行命令,配对完成。不再需要下载安装包、不再复制长串配对码。\n界面重做:任务卡直接显示你给 Claude 的那句话;锁屏卡片新增 5 小时 / 每周用量;深色模式单独调过。\n设置页新增「再加一台 Mac」「发送反馈」「重置并重新开始」。\n修复:手机指令偶尔重复投递、任务名显示为系统消息、首屏被通知权限弹窗遮住。",
 "en-US": "New setup: open the app, say whether you're at your Mac, paste one line into Terminal on the Mac — paired. No installer download, no long pairing code.\nRedesigned UI: task cards show the prompt you gave Claude; the Lock Screen card now shows 5-hour and weekly usage; dark mode tuned separately.\nSettings: Add another Mac, Send Feedback, Reset and Start Over.\nFixes: occasional duplicate delivery of phone commands, system messages shown as task names, the notification prompt no longer covers the first screen.",
}
SUBTITLE_ZH = "把 Mac 上的 Claude Code 装进锁屏"
REVIEW_NOTES = ("SessionBell is a companion app for a Mac-side CLI that monitors local AI coding agents (e.g. Claude Code). "
  "Reviewer setup WITHOUT a Mac: launch the app -> tap \"Not right now — show me the demo\" (second button on the first screen) -> "
  "a demo workspace loads with simulated tasks, so every tab and the Lock Screen Live Activity can be reviewed without pairing anything. "
  "Pairing a real Mac is one line pasted into Terminal (shown in-app after \"I'm at my Mac now\"). "
  "Notifications are requested only on the connect screen. The feedback form's contact field is optional and only stored on our server for replying.")

def prepare():
    # 1. version 1.4 (create if missing)
    vers = call("GET", f"/v1/apps/{APP_ID}/appStoreVersions?limit=5&fields[appStoreVersions]=versionString,appVersionState")["data"]
    v14 = next((v for v in vers if v["attributes"]["versionString"] == "1.4"), None)
    if not v14:
        v14 = call("POST", "/v1/appStoreVersions", {"data": {"type": "appStoreVersions", "attributes": {"platform": "IOS", "versionString": "1.4"},
              "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}}}})["data"]
        print("created 1.4", v14["id"])
    else:
        print("1.4 exists", v14["id"], v14["attributes"].get("appVersionState"))
    vid = v14["id"]
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
        for name in ["1-onboarding.png", "2-tab0.png", "3-tab1.png", "4-detail.png"]:
            ids.append(upload_screenshot(st["id"], os.path.join(folder, name))); print("  uploaded", locale, name)
        call("PATCH", f"/v1/appScreenshotSets/{st['id']}/relationships/appScreenshots", {"data": [{"type": "appScreenshots", "id": i} for i in ids]})
        print("iPad set kept:", "APP_IPAD_PRO_3GEN_129" in sets)
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
    # wait for build 8 to finish processing
    bid = None
    for i in range(90):
        for b in call("GET", f"/v1/builds?filter[app]={APP_ID}&filter[version]=8&sort=-uploadedDate&limit=3&fields[builds]=version,processingState,uploadedDate")["data"]:
            print(time.strftime("%H:%M:%S"), "build", b["attributes"]["version"], b["attributes"]["processingState"], flush=True)
            if b["attributes"]["processingState"] == "VALID": bid = b["id"]
            elif b["attributes"]["processingState"] in ("FAILED", "INVALID"): raise SystemExit("build processing failed")
        if bid: break
        time.sleep(30)
    if not bid: raise SystemExit("build 8 not processed after 45 min")
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
