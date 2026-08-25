#!/bin/bash
# HELP: Kazumi Lite 番剧
# ICON: kazumilite
# GRID: KazumiLite
#

APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DATA_DIR="$APP_DIR/data"
LOG_FILE="$DATA_DIR/log.txt"

# Read the display size through muOS when the helper is available.
if [ -f /opt/muos/script/var/func.sh ]; then
  . /opt/muos/script/var/func.sh
  export APP_SCREEN_WIDTH="$(GET_VAR device mux/width)"
  export APP_SCREEN_HEIGHT="$(GET_VAR device mux/height)"
else
  export APP_SCREEN_WIDTH="${APP_SCREEN_WIDTH:-640}"
  export APP_SCREEN_HEIGHT="${APP_SCREEN_HEIGHT:-480}"
fi

# Reuse the runtime already supplied by PortMaster. Nothing is copied to /opt
# and no system library is replaced by this application.
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
if [ -d /opt/system/Tools/PortMaster ]; then
  CONTROL_DIR=/opt/system/Tools/PortMaster
elif [ -d /opt/tools/PortMaster ]; then
  CONTROL_DIR=/opt/tools/PortMaster
elif [ -d "$XDG_DATA_HOME/PortMaster" ]; then
  CONTROL_DIR="$XDG_DATA_HOME/PortMaster"
elif [ -d /mnt/mmc/MUOS/PortMaster ]; then
  CONTROL_DIR=/mnt/mmc/MUOS/PortMaster
elif [ -d /roms/ports/PortMaster ]; then
  CONTROL_DIR=/roms/ports/PortMaster
else
  CONTROL_DIR=""
fi

mkdir -p "$DATA_DIR"
: > "$LOG_FILE"
exec >> "$LOG_FILE" 2>&1

echo "[KazumiLite] start: $(date)"
echo "[KazumiLite] app: $APP_DIR"
echo "[KazumiLite] screen: ${APP_SCREEN_WIDTH}x${APP_SCREEN_HEIGHT}"
echo "[KazumiLite] version: 0.2.3-r2"

if [ -z "$CONTROL_DIR" ] || [ ! -f "$CONTROL_DIR/control.txt" ]; then
  echo "[KazumiLite] PortMaster runtime not found; exiting safely."
  exit 20
fi

. "$CONTROL_DIR/control.txt"
get_controls
if [ -f "$CONTROL_DIR/mod_${CFW_NAME}.txt" ]; then
  . "$CONTROL_DIR/mod_${CFW_NAME}.txt"
fi

export LD_LIBRARY_PATH="$CONTROL_DIR/libs:$CONTROL_DIR/utils/lib:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$DATA_DIR:$CONTROL_DIR/exlibs:$CONTROL_DIR/pylibs:$CONTROL_DIR/libs:${PYTHONPATH:-}"
export PYSDL2_DLL_PATH="$CONTROL_DIR/libs"
export XDG_DATA_DIRS="$APP_DIR:$CONTROL_DIR:${XDG_DATA_DIRS:-}"
export KAZUMI_LITE_CONTROL_DIR="$CONTROL_DIR"

cd "$DATA_DIR" || exit 21
echo "[KazumiLite] runtime: $CONTROL_DIR"
echo "[KazumiLite] cfw: ${CFW_NAME:-unknown}"

python3 -u "$DATA_DIR/app.py"
RESULT=$?

echo "[KazumiLite] exit: $RESULT"
exit "$RESULT"
