"""Read actual desktop records into disposable state, never send or modify Codex.

Print only counts, status and schema availability, never conversation content.
"""
import collections
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "desktop_smoke", Path(__file__).resolve().parents[1] / "mac/sessionbell_hook.py")
sb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sb)

with tempfile.TemporaryDirectory(prefix="sessionbell-desktop-smoke-") as temporary:
    with patch.object(sb, "CONFIG_DIR", temporary), \
            patch.object(sb, "SESSIONS_PATH", str(Path(temporary) / "sessions.json")), \
            patch.object(sb, "codex_alert") as alert:
        observer = sb.CodexDesktopObserver({})
        observer.scan()
        entries = list(sb.load_sessions()["local"].values())
        assert not alert.called, "Initial history must never notify"
        assert all(e["source"] == "desktop" and not e["managed"] for e in entries)
        print(json.dumps({"validated_desktop_records": len(observer.cursors),
                          "currently_visible": len(entries),
                          "statuses": dict(collections.Counter(e["status"] for e in entries)),
                          "with_token_usage": sum(bool(e.get("token_usage")) for e in entries),
                          "historical_notifications": alert.call_count}))
