#!/bin/zsh
# MarketSignal Friday PDF report service.
#
#   ./deploy/weekly-report.sh install     # schedule Friday 19:30 local time
#   ./deploy/weekly-report.sh run         # generate and deliver one now
#   ./deploy/weekly-report.sh status      # inspect the schedule and recent output
#   ./deploy/weekly-report.sh logs        # tail report logs
#   ./deploy/weekly-report.sh uninstall   # remove the schedule
#
# The 19:30 schedule deliberately follows the 18:30 nightly scan, so the
# report includes the latest market pass before it is delivered to Telegram.
set -euo pipefail

LABEL="com.marketsignal.weeklyreport"
WEEKDAY=5  # Friday (launchd: Sunday=0)
HOUR=19
MINUTE=30

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="/usr/bin/python3"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/weekly_report.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/weekly_report.log"
STDERR_LOG="${LOG_DIR}/weekly_report.err.log"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python not found"
  [[ -f "${ENTRY}" ]] || die "weekly_report.py not found"
  mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"
  cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>${PYTHON}</string><string>${ENTRY}</string></array>
  <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>${WEEKDAY}</integer>
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
    print "installed ${LABEL} — Fridays at ${HOUR}:${MINUTE} local"
    ;;
  run)
    cd "${PROJECT_DIR}" && exec "${PYTHON}" "${ENTRY}"
    ;;
  status)
    launchctl print "${SERVICE}" 2>/dev/null | grep -E "state|last exit|program" || print "not loaded"
    print -r -- "--- last report output ---"
    tail -n 30 "${STDOUT_LOG}" 2>/dev/null || print "(no report log yet)"
    ;;
  logs)
    mkdir -p "${LOG_DIR}"; touch "${STDOUT_LOG}" "${STDERR_LOG}"
    tail -n 60 -f "${STDOUT_LOG}" "${STDERR_LOG}"
    ;;
  uninstall)
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    rm -f "${PLIST}"
    print "removed ${LABEL}"
    ;;
  *)
    print -u2 -r -- "usage: $0 {install|run|status|logs|uninstall}"
    exit 2
    ;;
esac
