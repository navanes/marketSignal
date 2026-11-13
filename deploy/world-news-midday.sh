#!/bin/zsh
# MarketSignal midday news "shock check" (macOS launchd).
#
# Separate from world-news.sh's once-a-day graded scan (07:00) — this runs
# again around midday and checks world_news.check_shock() for a sharp
# sentiment swing or a market-moving keyword (crash, resigns, war, ...).
# Does NOT write to the graded log (see world_news.check_shock's docstring),
# it only decides whether to push an immediate Telegram text alert plus a
# short spoken "breaking" voice briefing (see voice_briefing.py).
#
#   ./deploy/world-news-midday.sh install     # schedule daily at 12:30 local
#   ./deploy/world-news-midday.sh run         # check once, right now
#   ./deploy/world-news-midday.sh status      # loaded? last run? recent output
#   ./deploy/world-news-midday.sh logs        # tail the log
#   ./deploy/world-news-midday.sh uninstall   # remove it
set -euo pipefail

LABEL="com.marketsignal.worldnewsmidday"
HOUR=12
MINUTE=30

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/world_news.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/world_news_midday.log"
STDERR_LOG="${LOG_DIR}/world_news_midday.err.log"
RUNNER="${PROJECT_DIR}/deploy/_world_news_midday_runner.sh"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_runner() {
  cat > "${RUNNER}" <<RUNNER
#!/bin/zsh
set -uo pipefail
cd "${PROJECT_DIR}"
echo "=== \$(date) world-news-midday start ==="
"${PYTHON}" "${ENTRY}" alert
echo "=== \$(date) world-news-midday end ==="
RUNNER
  chmod +x "${RUNNER}"
}

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python not found"
  [[ -f "${ENTRY}" ]] || die "world_news.py not found"
  write_runner
  mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"
  cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>${RUNNER}</string></array>
  <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>${HOUR}</integer>
    <key>Minute</key><integer>${MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key><string>${STDOUT_LOG}</string>
  <key>StandardErrorPath</key><string>${STDERR_LOG}</string>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PLIST
  chmod 644 "${PLIST}"
}

case "${1:-}" in
  install)
    write_plist
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    launchctl bootstrap "${DOMAIN}" "${PLIST}"
    launchctl enable "${SERVICE}"
    print "installed ${LABEL} — daily at ${HOUR}:${MINUTE} local"
    ;;
  run)
    write_runner
    exec /bin/zsh "${RUNNER}"
    ;;
  status)
    launchctl print "${SERVICE}" 2>/dev/null | grep -E "state|last exit|program" || print "not loaded"
    print -r -- "--- last output ---"
    tail -n 30 "${STDOUT_LOG}" 2>/dev/null || print "(no log yet)"
    ;;
  logs)
    mkdir -p "${LOG_DIR}"; touch "${STDOUT_LOG}" "${STDERR_LOG}"
    tail -n 60 -f "${STDOUT_LOG}" "${STDERR_LOG}"
    ;;
  uninstall)
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    rm -f "${PLIST}" "${RUNNER}"
    print "removed ${LABEL}"
    ;;
  *)
    print -u2 -r -- "usage: $0 {install|run|status|logs|uninstall}"
    exit 2
    ;;
esac
