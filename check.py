#!/usr/bin/env python3
"""
tier-drift: verify that pricing-page claims haven't drifted.

Reads a JSON config of {name, url, expect[], forbid[]} entries, fetches each
pricing page, and checks:
  - every "expect" phrase still appears on the page (e.g. "free plan")
  - no "forbid" phrase has appeared (e.g. "credit card required")
  - the page didn't redirect somewhere unexpected or disappear

A page can return 200 OK and still be lying (the claim quietly removed) --
that is drift, and this script catches it. Stdlib only, zero API keys.

Config format (tiers.json):
[
  {
    "name": "Example Tool",
    "url": "https://example.com/pricing",
    "expect": ["free plan", "free forever"],
    "forbid": ["credit card required"]
  }
]

Exit code: 0 = all claims hold, 1 = drift and/or errors found
(controlled by --fail-on-drift / --fail-on-error).
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"

# ---------------------------------------------------------------- fetching

def fetch(url, timeout, user_agent):
    """Fetch a URL. Returns dict with ok/status/final_url/body or error."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Respect declared charset, fall back to utf-8.
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            return {
                "ok": True,
                "status": resp.status,
                "final_url": resp.geturl(),
                "body": body,
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": "HTTP %d" % e.code}
    except Exception as e:  # network/DNS/TLS/timeout
        return {"ok": False, "status": None, "error": "%s: %s" % (type(e).__name__, e)}


# ------------------------------------------------------------- text extract

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_RE = re.compile(r"<[^>]+>")

def visible_text(body):
    """Crude but effective: drop scripts/styles/tags, unescape, normalize."""
    no_js = _TAG_RE.sub(" ", body)
    no_tags = _HTML_RE.sub(" ", no_js)
    text = html.unescape(no_tags)
    return _WS_RE.sub(" ", text).strip().lower()


def urls_equal(a, b):
    return a.rstrip("/").lower() == b.rstrip("/").lower()


# ---------------------------------------------------------------- checking

def check_entry(entry, timeout, user_agent):
    name = entry.get("name", "?")
    url = entry.get("url", "")
    expect = [p for p in entry.get("expect", []) if p]
    forbid = [p for p in entry.get("forbid", []) if p]

    result = {
        "name": name,
        "url": url,
        "status": "ok",       # ok | drift | error
        "details": [],
        "http_status": None,
        "final_url": None,
    }

    if not url:
        result["status"] = "error"
        result["details"].append("no URL configured")
        return result

    fetched = fetch(url, timeout, user_agent)
    if not fetched["ok"]:
        result["status"] = "error"
        result["http_status"] = fetched.get("status")
        result["details"].append("fetch failed: %s" % fetched.get("error"))
        return result

    result["http_status"] = fetched["status"]
    result["final_url"] = fetched["final_url"]

    if not urls_equal(fetched["final_url"], url):
        result["details"].append("redirects to %s" % fetched["final_url"])

    text = visible_text(fetched["body"])
    if not text:
        result["status"] = "drift"
        result["details"].append("page has no readable text (JS-rendered or blocked?)")
        return result

    drifted = False
    for phrase in expect:
        if phrase.lower() not in text:
            drifted = True
            result["details"].append('expected phrase missing: "%s"' % phrase)
    for phrase in forbid:
        if phrase.lower() in text:
            drifted = True
            result["details"].append('forbidden phrase found: "%s"' % phrase)

    if drifted:
        result["status"] = "drift"
    elif not result["details"]:
        result["details"].append("all claims hold")
    return result


# ----------------------------------------------------------------- reporting

def markdown_report(results):
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_drift = sum(1 for r in results if r["status"] == "drift")
    n_err = sum(1 for r in results if r["status"] == "error")

    icon = {"ok": "✅", "drift": "🚨", "error": "⚠️"}
    lines = [
        "# tier-drift report",
        "",
        "%d checked — %d ok, %d drifted, %d errors" % (len(results), n_ok, n_drift, n_err),
        "",
        "| Tool | Status | Details |",
        "| ---- | ------ | ------- |",
    ]
    for r in results:
        details = "<br>".join(r["details"]).replace("|", "\\|")
        lines.append("| %s | %s %s | %s |" % (r["name"], icon[r["status"]], r["status"], details))
    lines.append("")
    if n_drift:
        lines.append(
            "⚠️ **Drift detected:** a pricing page changed its claims. "
            "Verify manually, then update the config or the directory listing."
        )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify pricing-page claims haven't drifted.")
    ap.add_argument("--config", default="tiers.json", help="path to tiers JSON config")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=20.0, help="request timeout seconds")
    ap.add_argument("--user-agent", default="tier-drift/%s (+github.com/saaslisty/tier-drift)" % VERSION)
    ap.add_argument("--fail-on-drift", action="store_true", default=True)
    ap.add_argument("--no-fail-on-drift", dest="fail_on_drift", action="store_false")
    ap.add_argument("--fail-on-error", action="store_true", default=True)
    ap.add_argument("--no-fail-on-error", dest="fail_on_error", action="store_false")
    ap.add_argument("--markdown-out", default=None, help="write markdown report to file")
    ap.add_argument("--json-out", default="tier-drift-results.json", help="write JSON results to file")
    ap.add_argument("--version", action="version", version="tier-drift " + VERSION)
    args = ap.parse_args(argv)

    try:
        with open(args.config, encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        print("config not found: %s" % args.config, file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print("config is not valid JSON: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("config must be a JSON array of entries", file=sys.stderr)
        return 2

    results = []
    for i, entry in enumerate(entries):
        if i:
            time.sleep(args.delay)
        results.append(check_entry(entry, args.timeout, args.user_agent))
        r = results[-1]
        print("[%s] %s — %s" % (r["status"].upper(), r["name"], "; ".join(r["details"])))

    md = markdown_report(results)
    if args.markdown_out:
        with open(args.markdown_out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

    # GitHub Actions step summary.
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(md + "\n")

    n_drift = sum(1 for r in results if r["status"] == "drift")
    n_err = sum(1 for r in results if r["status"] == "error")
    if (n_drift and args.fail_on_drift) or (n_err and args.fail_on_error):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
