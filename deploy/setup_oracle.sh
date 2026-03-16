#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-$HOME/discordBot}"

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

if [ ! -d "$APP_DIR/.git" ]; then
  echo "Repository not found at $APP_DIR"
  echo "Clone it first, for example:"
  echo "git clone https://github.com/your-user/your-repo.git \"$APP_DIR\""
  exit 1
fi

cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
  echo "Edit $APP_DIR/.env before starting the service."
fi

echo
echo "Setup complete."
echo "Next steps:"
echo "1. Edit $APP_DIR/.env"
echo "2. Copy deploy/discord-activity-bot.service to /etc/systemd/system/"
echo "3. Run: sudo systemctl daemon-reload"
echo "4. Run: sudo systemctl enable --now discord-activity-bot"
