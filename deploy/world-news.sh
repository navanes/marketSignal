#!/bin/zsh
# MarketSignal daily world/political news scan (macOS launchd).
#
# Scans broad market-moving news and a short list of market-moving public
# figures (president, Fed chair, etc.) every morning before the nightly
# per-symbol scan runs, scores the day's tone, and feeds it into every
# symbol's forecast as a small bounded tilt (see app.macro_news_tilt()).
# Also grades yesterday's call against what SPY actually did, so the
# political/macro read keeps a real accuracy track record like everything
# else in this project.
#
#   ./deploy/world-news.sh install     # schedule daily at 07:00 local
#   ./deploy/world-news.sh run         # scan + grade once, right now
#   ./deploy/world-news.sh status      # loaded? last run? recent output
#   ./deploy/world-news.sh logs        # tail the log
#   ./deploy/world-news.sh uninstall   # remove it
set -euo pipefail

LABEL="com.marketsignal.worldnews"
HOUR=7
MINUTE=0

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/world_news.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/world_news.log"
STDERR_LOG="${LOG_DIR}/world_news.err.log"
RUNNER="${PROJECT_DIR}/deploy/_world_news_runner.sh"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_runner() {
  cat > "${RUNNER}" <<RUNNER
#!/bin/zsh
set -uo pipefail
cd "${PROJECT_DIR}"
echo "=== \$(date) world-news start ==="
"${PYTHON}" "${ENTRY}" scan
"${PYTHON}" "${ENTRY}" grade
echo "=== \$(date) world-news end ==="
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
