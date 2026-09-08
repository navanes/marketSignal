#!/bin/zsh
# MarketSignal nightly universe scan (macOS launchd).
#
#   ./deploy/scan.sh install     # write the LaunchAgent, schedule the nightly run
#   ./deploy/scan.sh uninstall   # remove it
#   ./deploy/scan.sh run         # run the scan once, right now
#   ./deploy/scan.sh status      # loaded? last run? recent output
#   ./deploy/scan.sh logs        # tail the scan log
#
# Runs scan.py every night at 18:35 local time (after the US close) so the
# prediction track record compounds on its own. No secrets in the plist.
set -euo pipefail

LABEL="com.marketsignal.scan"
HOUR=18
MINUTE=35

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/scan.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/scan.log"
STDERR_LOG="${LOG_DIR}/scan.err.log"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python3 not found on PATH"
  [[ -f "${ENTRY}" ]] || die "scan.py not found at ${ENTRY}"
  mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"
  cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>${PYTHON}</string>
		<string>${ENTRY}</string>
	</array>
	<key>WorkingDirectory</key>
	<string>${PROJECT_DIR}</string>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key><integer>${HOUR}</integer>
		<key>Minute</key><integer>${MINUTE}</integer>
	</dict>
	<key>StandardOutPath</key>
	<string>${STDOUT_LOG}</string>
	<key>StandardErrorPath</key>
	<string>${STDERR_LOG}</string>
	<key>RunAtLoad</key>
	<false/>
	<key>ProcessType</key>
	<string>Background</string>
</dict>
</plist>
PLIST
}

case "${1:-}" in
  install)
    write_plist
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    launchctl bootstrap "${DOMAIN}" "${PLIST}"
    launchctl enable "${SERVICE}"
    print "installed ${LABEL} — runs nightly at ${HOUR}:${MINUTE} local"
    ;;
  uninstall)
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    rm -f "${PLIST}"
    print "removed ${LABEL}"
    ;;
  run)
    cd "${PROJECT_DIR}" && exec "${PYTHON}" "${ENTRY}"
    ;;
  status)
    launchctl print "${SERVICE}" 2>/dev/null | grep -E "state|last exit|program" || print "not loaded"
    print -r -- "--- last scan output ---"
    tail -n 20 "${STDOUT_LOG}" 2>/dev/null || print "(no log yet)"
    ;;
  logs)
    touch "${STDOUT_LOG}" "${STDERR_LOG}"
    tail -n 40 -f "${STDOUT_LOG}" "${STDERR_LOG}"
    ;;
  *)
    print -u2 "usage: ${0} {install|uninstall|run|status|logs}"
    exit 1
    ;;
esac
