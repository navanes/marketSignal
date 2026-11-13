#!/bin/zsh
# MarketSignal Friday voice briefing (macOS launchd).
#
# Companion to weekly-report.sh's PDF — a 2-3 minute spoken "radio spot"
# version of the same weekly take, synthesized with macOS `say` and sent
# to Telegram as a voice/audio message. Scheduled 10 minutes after the PDF
# so both land the same evening.
#
#   ./deploy/voice-briefing.sh install     # schedule Fridays at 19:40 local
#   ./deploy/voice-briefing.sh run         # build + send once, right now
#   ./deploy/voice-briefing.sh status      # loaded? last run? recent output
#   ./deploy/voice-briefing.sh logs        # tail the log
#   ./deploy/voice-briefing.sh uninstall   # remove it
set -euo pipefail

LABEL="com.marketsignal.voicebriefing"
WEEKDAY=5  # Friday (launchd: Sunday=0)
HOUR=19
MINUTE=40

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/voice_briefing.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/voice_briefing.log"
STDERR_LOG="${LOG_DIR}/voice_briefing.err.log"
RUNNER="${PROJECT_DIR}/deploy/_voice_briefing_runner.sh"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_runner() {
  cat > "${RUNNER}" <<RUNNER
#!/bin/zsh
set -uo pipefail
cd "${PROJECT_DIR}"
echo "=== \$(date) voice-briefing start ==="
"${PYTHON}" "${ENTRY}" weekly --send
echo "=== \$(date) voice-briefing end ==="
RUNNER
  chmod +x "${RUNNER}"
}

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python not found"
  [[ -f "${ENTRY}" ]] || die "voice_briefing.py not found"
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
