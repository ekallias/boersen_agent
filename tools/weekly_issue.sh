#!/bin/bash
# Weekly newsletter report.
#   poll     - run from Sat 08:00 to Sun 23:00 (hourly): ingest new issues from Paperless.
#              `ingest` writes and e-mails the issue report when a new issue arrives.
#   fallback - run Sun 23:30: if no issue report was made this ISO week, report the latest issue.
# A marker file per ISO week prevents a second report for the same week.
set -euo pipefail

mode="${1:-poll}"
cd /home/emil/boersen_agent
PY=/home/emil/.venvs/boersen-agent/bin/python
set -a
. /home/emil/.config/boersen-agent.env
set +a

week="$(date +%G-%V)"
marker="data/weekly-issue-done-${week}"

if [ -e "$marker" ]; then
    echo "already reported for ${week}"
    exit 0
fi

if [ "$mode" = "poll" ]; then
    out="$($PY -m boersen_agent --config config.toml ingest)"
    echo "$out"
    if echo "$out" | grep -q "^new issues: none"; then
        exit 0
    fi
    touch "$marker"
elif [ "$mode" = "fallback" ]; then
    echo "no new issue for ${week}; reporting the latest issue"
    $PY -m boersen_agent --config config.toml report
    touch "$marker"
else
    echo "usage: $0 poll|fallback" >&2
    exit 2
fi
