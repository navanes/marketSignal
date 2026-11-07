#!/bin/zsh
# MarketSignal monthly accuracy check-in — the "learn from history" loop.
#
# Joins graded predictions against reality over the last 7/30/60/90 days and
# all-time, compares live accuracy against what the champion model claimed at
# training time, and sends the result to Telegram. If live accuracy has
# drifted far below the training-time claim, it says so — that's the signal
# that the model needs a fresh, honest retrain (see review_accuracy.py and
# train.py's incumbent_walkforward(), which now caps the promotion bar at
# real live performance instead of trusting a possibly-inflated backtest).
#
#   ./deploy/model-review.sh install     # schedule 1st of month, 09:00 local
#   ./deploy/model-review.sh run         # run and send one now
#   ./deploy/model-review.sh status      # inspect the schedule and recent output
#   ./deploy/model-review.sh logs        # tail logs
#   ./deploy/model-review.sh uninstall   # remove the schedule
set -euo pipefail

LABEL="com.marketsignal.modelreview"
DAY=1
HOUR=9
MINUTE=0

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || PYTHON="/usr/bin/python3"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${PROJECT_DIR}/review_accuracy.py"
LOG_DIR="${PROJECT_DIR}/logs"
STDOUT_LOG="${LOG_DIR}/model_review.log"
STDERR_LOG="${LOG_DIR}/model_review.err.log"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python not found"
  [[ -f "${ENTRY}" ]] || die "review_accuracy.py not found"
  mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"
  cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>${PYTHON}</string><string>${ENTRY}</string><string>--send</string></array>
  <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key><dict>
    <key>Day</key><integer>${DAY}</integer>
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
    print "installed ${LABEL} — 1st of each month at ${HOUR}:${MINUTE} local"
    ;;
  run)
    cd "${PROJECT_DIR}" && exec "${PYTHON}" "${ENTRY}" --send
    ;;
  status)
    launchctl print "${SERVICE}" 2>/dev/null | grep -E "state|last exit|program" || print "not loaded"
    print -r -- "--- last review output ---"
    tail -n 30 "${STDOUT_LOG}" 2>/dev/null || print "(no log yet)"
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
