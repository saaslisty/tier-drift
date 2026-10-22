# tier-drift

**Does your directory still tell the truth?** A free GitHub Action that verifies pricing-page claims haven't drifted.

Every "free tools" directory has the same disease: rot. A vendor quietly shrinks its free tier, sunsets the free plan, or starts demanding a credit card — and the directory keeps cheerfully sending people to a deal that no longer exists. A page can return `200 OK` and still be lying. tier-drift catches that.

## How it works

You keep a `tiers.json` next to your directory:

```json
[
  {
    "name": "Example Tool",
    "url": "https://example.com/pricing",
    "expect": ["free plan", "free forever"],
    "forbid": ["credit card required"]
  }
]
```

On schedule (or every push), the action fetches each pricing page and checks:

- ✅ every `expect` phrase still appears → claim holds
- 🚨 an `expect` phrase vanished → **drift** (free tier killed/shrunk)
- 🚨 a `forbid` phrase appeared → **drift** (card now required, etc.)
- ⚠️ page redirects somewhere unexpected, or can't be fetched → reported

Drift fails the run and shows up in the step summary + a Markdown/JSON report. Zero API keys, zero browser, stdlib Python only.

## Usage

```yaml
# .github/workflows/tier-drift.yml
name: Tier drift check
on:
  schedule:
    - cron: "0 9 * * 1"   # every Monday 09:00 UTC
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: saaslisty/tier-drift@v1
        with:
          config: tiers.json        # default
          fail-on-drift: "true"     # default
          fail-on-error: "true"     # default
          delay: "1"                # seconds between requests
          timeout: "20"
```

Copy `tiers.example.json` to `tiers.json` and fill in your tools.

## Local run

```bash
python3 check.py --config tiers.json
python3 check.py --config tiers.json --no-fail-on-drift --json-out results.json
```

## Notes / honest limits

- Pages that render pricing purely in JavaScript may show "no readable text" — those get flagged for manual review rather than silently passing.
- Polite by default: 1s delay between requests, identifiable User-Agent.
- It verifies *claims on the page*, not the vendor's billing backend. When drift fires, confirm manually before editing your directory.

## License

MIT
