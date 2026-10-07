#!/usr/bin/env sh
# Устанавливает скилл Subscription Hunter в папку скиллов агента.
#   sh install.sh                 → Claude Code (~/.claude/skills)
#   sh install.sh claude          → Claude Code (~/.claude/skills)
#   sh install.sh codex           → Codex (~/.agents/skills)
#   sh install.sh cursor          → Cursor (~/.cursor/skills)
#   sh install.sh <путь>          → в любую другую папку скиллов
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)/skills"

case "${1:-claude}" in
  claude) DEST_DIR="$HOME/.claude/skills"; AGENT="Claude Code" ;;
  codex)  DEST_DIR="$HOME/.agents/skills"; AGENT="Codex" ;;
  cursor) DEST_DIR="$HOME/.cursor/skills"; AGENT="Cursor" ;;
  -h|--help)
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  */*|.*) DEST_DIR="$1"; AGENT="" ;;
  *)
    echo "Неизвестный агент: $1. Укажите claude, codex, cursor или путь к папке скиллов." >&2
    exit 2 ;;
esac

mkdir -p "$DEST_DIR"
for skill in "$SRC_DIR"/*/; do
  name="$(basename "$skill")"
  rm -rf "$DEST_DIR/$name"
  cp -R "$skill" "$DEST_DIR/$name"
  find "$DEST_DIR/$name" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "  + $name"
done
echo "Готово: установлено в $DEST_DIR${AGENT:+ ($AGENT)}"
if [ -n "$AGENT" ]; then
  echo "Перезапустите $AGENT, чтобы он увидел новый скилл."
fi
