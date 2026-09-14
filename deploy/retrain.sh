#!/bin/zsh
# Weekly model retrain (macOS launchd) — the actual "learning" step.
#
# The nightly scan only grades predictions against reality; it never updates
# the model. This re-fits both model families (ridge, gbt) on the accumulated
# graded history and lets train.py's champion/challenger gate decide whether
# either one beats the current incumbent — if not, nothing changes.
#
#   ./deploy/retrain.sh install     # write the LaunchAgent, schedule it
#   ./deploy/retrain.sh uninstall   # remove it
#   ./deploy/retrain.sh run         # retrain once, right now
#   ./deploy/retrain.sh status      # loaded? last run? recent output
#   ./deploy/retrain.sh logs        # tail the retrain log
set -euo pipefail

LABEL="com.marketsignal.retrain"
WEEKDAY=0   # Sunday
HOUR=19
MINUTE=15

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="/usr/bin/python3"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/retrain.log"
STDERR_LOG="${LOG_DIR}/retrain.err.log"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"
RUNNER="${PROJECT_DIR}/deploy/_retrain_runner.sh"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_runner() {
  cat > "${RUNNER}" <<RUNNER
#!/bin/zsh
set -uo pipefail
cd "${PROJECT_DIR}"
echo "=== \$(date) retrain start ==="
"${PYTHON}" train.py --model ridge
echo "--- ridge done ---"
"${PYTHON}" train.py --model gbt
echo "--- gbt done ---"
echo "=== \$(date) retrain end ==="
RUNNER
  chmod +x "${RUNNER}"
}

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python3 not found on PATH"
  write_runner
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
		<string>/bin/zsh</string>
		<string>${RUNNER}</string>
	</array>
	<key>WorkingDirectory</key>
	<string>${PROJECT_DIR}</string>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Weekday</key><integer>${WEEKDAY}</integer>
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
    print "installed ${LABEL} — retrains Sundays at ${HOUR}:${MINUTE} local"
    ;;
  uninstall)
    launchctl bootout "${SERVICE}" 2>/dev/null || true
    rm -f "${PLIST}" "${RUNNER}"
    print "removed ${LABEL}"
    ;;
  run)
    write_runner
    exec /bin/zsh "${RUNNER}"
    ;;
  status)
    launchctl print "${SERVICE}" 2>/dev/null | grep -E "state|last exit|program" || print "not loaded"
    print -r -- "--- last retrain output ---"
    tail -n 30 "${STDOUT_LOG}" 2>/dev/null || print "(no log yet)"
    ;;
  logs)
    touch "${STDOUT_LOG}" "${STDERR_LOG}"
    tail -n 60 -f "${STDOUT_LOG}" "${STDERR_LOG}"
    ;;
  *)
    print -u2 "usage: ${0} {install|uninstall|run|status|logs}"
    exit 1
    ;;
esac
