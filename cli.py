"""`hermes wpbruecke ...`: doctor, tick (for a no-agent cron job), watch-setup, connector-zip."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WATCH_SCRIPT = """#!/usr/bin/env bash
# WPBrücke watcher: prints nothing on a clean tick, so the no-agent cron job delivers nothing.
set -u
work="${TMPDIR:-/tmp}/wpbruecke-tick"
mkdir -p "$work" && cd "$work" || exit 1
timeout 600 hermes wpbruecke tick
"""


def setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="wpbruecke_command")
    doc = sub.add_parser("doctor", help="Check every site: who the key is, what it may do, which features it lights")
    doc.add_argument("--site", help="only this site id")
    doc.add_argument("--json", action="store_true", help="print the report as JSON")
    tick = sub.add_parser("tick", help="Read every site once and print only what changed (for a no-agent cron job)")
    tick.add_argument("--json", action="store_true", help="print the report as JSON")
    sub.add_parser("watch-setup", help="Print the script and the cron command for the scheduled watcher")
    z = sub.add_parser("connector-zip", help="Write the WPBrücke connector WordPress plugin as a zip to upload in wp-admin")
    z.add_argument("--out", required=True, help="folder or .zip path to write")


def make_handler(get_setting, data_dir: Any, get_secret):
    def handler(args) -> int:
        from . import runtime
        command = getattr(args, "wpbruecke_command", None)
        if command in ("doctor", "tick") and not data_dir:
            print("WPBrücke has no plugin data folder in this context.")
            return 1
        if command == "doctor":
            from .tools import doctor
            rt = runtime.Runtime(get_setting, get_secret, data_dir)
            report = doctor.run(rt, getattr(args, "site", None))
            print(json.dumps(report, ensure_ascii=False, indent=2) if getattr(args, "json", False)
                  else doctor.format_text(report))
            return 0 if report.get("ok") else 1
        if command == "tick":
            from . import tick
            rt = runtime.Runtime(get_setting, get_secret, data_dir, client_timeout=10)
            report = tick.run_tick(rt)
            text = json.dumps(report, ensure_ascii=False, indent=2) if getattr(args, "json", False) else tick.format_text(report)
            if text:
                print(text)
            return 0
        if command == "watch-setup":
            print("1. Save this as scripts/wpbruecke_tick.sh in your Hermes profile folder:\n")
            print(WATCH_SCRIPT)
            print("2. Then create the no-agent job (every 15 minutes; it reports only changes):\n")
            print("   hermes cron create 'every 15m' --no-agent --script wpbruecke_tick.sh --name 'WPBrücke watch' --deliver origin")
            return 0
        if command == "connector-zip":
            from . import connector_zip
            out = Path(args.out).expanduser()
            if out.suffix != ".zip":
                out = out / "wpbruecke-connector.zip"
            info = connector_zip.build_zip(out)
            print(f"{info['path']}  {info['bytes']} bytes  sha256 {info['sha256']}")
            print("Upload it in wp-admin: Plugins, Add New, Upload Plugin, then Activate.")
            return 0
        print("usage: hermes wpbruecke doctor [--site ID] [--json] | tick [--json] | watch-setup | connector-zip --out PATH")
        return 2
    return handler
