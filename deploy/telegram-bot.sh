#!/bin/zsh
# Telegram bot service manager (macOS launchd).
#
# Usage:
#   ./deploy/telegram-bot.sh install     # write the LaunchAgent and start it
#   ./deploy/telegram-bot.sh uninstall   # stop and remove the LaunchAgent
#   ./deploy/telegram-bot.sh start        # start (or reload) the service
#   ./deploy/telegram-bot.sh stop         # stop; stays stopped across logins
#   ./deploy/telegram-bot.sh restart      # stop then start
#   ./deploy/telegram-bot.sh status       # loaded? running? pid? recent errors
#   ./deploy/telegram-bot.sh logs         # tail the stdout + stderr logs
#   ./deploy/telegram-bot.sh health       # process check + Telegram getMe probe
#
# The bot token is read from the project .env only for the health probe and is
# never printed. The generated plist contains no secrets.
set -euo pipefail
SELF="${0}"

#### Per-project configuration ###############################################
LABEL="com.marketsignal.telegrambot"
WORKDIR_REL="."                 # bot working directory, relative to project root
ENTRY_REL="telegram_bot.py"     # python entry point, relative to WORKDIR
ENV_FILE_REL=".env"             # dotenv file (health probe reads token), rel to root
#############################################################################

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
WORKDIR="${PROJECT_DIR}/${WORKDIR_REL}"
PYTHON="/usr/bin/python3"
[[ -x "${PYTHON}" ]] || PYTHON="$(command -v python3)"
ENTRY="${ENTRY_REL}"
WORKDIR="${WORKDIR:A}"
ENV_FILE="${PROJECT_DIR}/${ENV_FILE_REL}"
LOG_DIR="${WORKDIR}/logs"
STDOUT_LOG="${LOG_DIR}/telegram_bot.log"
STDERR_LOG="${LOG_DIR}/telegram_bot.err.log"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

die() { print -u2 -r -- "error: $*"; exit 1; }

write_plist() {
  [[ -x "${PYTHON}" ]] || die "python3 not found on PATH"
  [[ -f "${WORKDIR}/${ENTRY}" ]] || die "entry point not found at ${WORKDIR}/${ENTRY}"
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
	<string>${WORKDIR}</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PYTHONUNBUFFERED</key>
		<string>1</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
		<key>Crashed</key>
		<true/>
	</dict>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>StandardOutPath</key>
	<string>${STDOUT_LOG}</string>
	<key>StandardErrorPath</key>
	<string>${STDERR_LOG}</string>
</dict>
</plist>
PLIST
  chmod 644 "${PLIST}"
}

load_service() {
  local i
  for i in 1 2 3 4 5; do
    if launchctl bootstrap "${DOMAIN}" "${PLIST}" 2>/dev/null; then return 0; fi
    sleep 1
  done
  launchctl bootstrap "${DOMAIN}" "${PLIST}"
}
unload_service() {
  launchctl bootout "${SERVICE}" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    launchctl print "${SERVICE}" >/dev/null 2>&1 || return 0
    sleep 1
  done
  return 0
}

svc_field() {  # $1 = field name as printed by `launchctl print`
  launchctl print "${SERVICE}" 2>/dev/null \
    | awk -F' = ' -v k="$1" '$1 ~ "^\t"k"$" {print $2; exit}'
}

cmd_install() {
  [[ -f "${ENV_FILE}" ]] || die "no .env at ${ENV_FILE} — create it with TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_ID first"
  write_plist
  unload_service
  launchctl enable "${SERVICE}" 2>/dev/null || true
  load_service
  sleep 1
  cmd_status || true
  print -r -- "installed ${LABEL}"
}

cmd_uninstall() {
  unload_service
  rm -f "${PLIST}"
  print -r -- "uninstalled ${LABEL} (plist removed; logs kept under ${LOG_DIR})"
}

cmd_start() {
  [[ -f "${PLIST}" ]] || die "not installed — run: ${SELF} install"
  launchctl enable "${SERVICE}" 2>/dev/null || true
  load_service 2>/dev/null || launchctl kickstart -k "${SERVICE}"
  sleep 1
  cmd_status
}

cmd_stop() {
  launchctl disable "${SERVICE}" 2>/dev/null || true
  unload_service
  print -r -- "stopped ${LABEL} — stays stopped (across reboot) until: ${SELF} start"
}

cmd_restart() {
  [[ -f "${PLIST}" ]] || die "not installed — run: ${SELF} install"
  launchctl enable "${SERVICE}" 2>/dev/null || true
  unload_service
  sleep 1
  load_service
  sleep 1
  cmd_status
}

cmd_status() {
  if launchctl print "${SERVICE}" >/dev/null 2>&1; then
    print -r -- "service : ${LABEL}"
    print -r -- "loaded  : yes"
    print -r -- "state   : $(svc_field state)"
    print -r -- "pid     : $(svc_field pid)"
    print -r -- "last exit: $(svc_field 'last exit code')"
  else
    print -r -- "service : ${LABEL}"
    print -r -- "loaded  : no (stopped or not installed)"
  fi
  print -r -- "plist   : ${PLIST}"
  print -r -- "workdir : ${WORKDIR}"
  print -r -- "python  : ${PYTHON}"
  print -r -- "logs    : ${STDOUT_LOG}"
  print -r -- "          ${STDERR_LOG}"
  if [[ -s "${STDERR_LOG}" ]]; then
    print -r -- "--- last 5 stderr lines ---"
    tail -n 5 "${STDERR_LOG}" 2>/dev/null || true
  fi
}

cmd_logs() {
  mkdir -p "${LOG_DIR}"
  touch "${STDOUT_LOG}" "${STDERR_LOG}"
  print -r -- "tailing ${STDOUT_LOG} + ${STDERR_LOG} (Ctrl+C to stop)"
  tail -n 50 -F "${STDOUT_LOG}" "${STDERR_LOG}"
}

cmd_health() {
  local rc=0 pid
  if launchctl print "${SERVICE}" >/dev/null 2>&1; then
    pid="$(svc_field pid)"
    if [[ -n "${pid}" && "${pid}" != "0" ]]; then
      print -r -- "process : running (pid ${pid})"
    else
      print -r -- "process : loaded but not running"; rc=1
    fi
  else
    print -r -- "process : not loaded"; rc=1
  fi

  if [[ -f "${ENV_FILE}" ]]; then
    local token resp uname
    token="$(grep -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'\''[:space:]' || true)"
    if [[ -n "${token}" ]]; then
      resp="$(curl -s -m 10 "https://api.telegram.org/bot${token}/getMe" || true)"
      if print -r -- "${resp}" | grep -q '"ok":true'; then
        uname="$(print -r -- "${resp}" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
        print -r -- "telegram: ok (bot @${uname:-unknown})"
      else
        print -r -- "telegram: FAILED (getMe did not return ok)"; rc=1
      fi
    else
      print -r -- "telegram: skipped (no TELEGRAM_BOT_TOKEN in ${ENV_FILE})"
    fi
  else
    print -r -- "telegram: skipped (${ENV_FILE} not found)"
  fi
  return ${rc}
}

case "${1:-}" in
  install)   cmd_install ;;
  uninstall) cmd_uninstall ;;
  start)     cmd_start ;;
  stop)      cmd_stop ;;
  restart)   cmd_restart ;;
  status)    cmd_status ;;
  logs)      cmd_logs ;;
  health)    cmd_health ;;
  *) print -u2 -r -- "usage: $0 {install|uninstall|start|stop|restart|status|logs|health}"; exit 2 ;;
esac
