# Discord Activity Bot

This bot validates activity submissions in one Discord server, reposts approved submissions to another server, and keeps activity statistics for management reports.

## Features

- Validates activity posts in a source channel.
- Accepts only `Patrol` and `RP` as activity types.
- Requires at least one participant name.
- Rejects posts where tagged users do not exist in the source server.
- Warns when plain-text participant names are used and allows manual confirmation with `✅`.
- Accepts either:
  - `1` to `4` screenshot attachments, or
  - link(s) in the `Screens:` field.
- Reacts with `✅` on approval.
- Reacts with `🔴` on rejection and sends the author a DM with the reason.
- Reposts approved submissions into a channel in a second server.
- Sends the header image first, then reposts one combined image made from all attached screenshots.
- Stores approved submissions in SQLite for reporting.
- Provides `!showmonthly` stats in a management channel only.
- Can post new GitHub commit messages into a changelog Discord channel.

## Requirements

- Python `3.12`
- A Discord bot application
- Access to both Discord servers

Important: `discord.py 2.4.0` is not compatible with Python `3.13+` because of the removed `audioop` module. Use Python `3.12`.

## Install

Create and activate a virtual environment with Python `3.12`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Dependencies

- `discord.py==2.4.0`
- `python-dotenv==1.0.1`
- `Pillow==10.4.0`

## Project Files

- [bot.py](r:/Projects/discordBot/bot.py): Main bot logic
- [requirements.txt](r:/Projects/discordBot/requirements.txt): Python dependencies
- [.env](r:/Projects/discordBot/.env): Runtime configuration
- [.env.example](r:/Projects/discordBot/.env.example): Safe environment template
- [assets/activity.png](r:/Projects/discordBot/assets/activity.png): Header image sent before reposts
- [deploy/setup_oracle.sh](r:/Projects/discordBot/deploy/setup_oracle.sh): Ubuntu setup script for Oracle Cloud
- [deploy/setup_gcp.sh](r:/Projects/discordBot/deploy/setup_gcp.sh): Ubuntu setup script for Google Cloud VM
- [deploy/discord-activity-bot.service](r:/Projects/discordBot/deploy/discord-activity-bot.service): `systemd` service template
- [deploy/discord-deploy-webhook.service](r:/Projects/discordBot/deploy/discord-deploy-webhook.service): `systemd` service for GitHub webhooks
- [deploy/deploy_on_push.sh](r:/Projects/discordBot/deploy/deploy_on_push.sh): Deploy script triggered by GitHub push
- [deploy_webhook.py](r:/Projects/discordBot/deploy_webhook.py): Webhook listener that starts deployment on push
- `activity_stats.db`: SQLite database created automatically after first run

## Discord Setup

### 1. Create the bot

Go to the Discord Developer Portal:

`https://discord.com/developers/applications`

Create an application, add a bot, and copy the bot token.

### 2. Enable intents

In the bot settings, enable:

- `MESSAGE CONTENT INTENT`
- `SERVER MEMBERS INTENT`

### 3. Invite the bot

Invite the bot to both servers and ensure it has these permissions:

- `View Channels`
- `Send Messages`
- `Read Message History`
- `Add Reactions`
- `Attach Files`
- `Embed Links`

### 4. Get channel IDs

Enable Discord Developer Mode, then copy these IDs:

- Source channel ID
- Target repost channel ID
- Management channel ID

## Environment Variables

Configure [.env](r:/Projects/discordBot/.env):

```env
DISCORD_BOT_TOKEN=YOUR_NEW_BOT_TOKEN
SOURCE_TEXT_CHANNEL_ID=123456789012345678
TARGET_TEXT_CHANNEL_ID=987654321098765432
MANAGEMENT_CHANNEL_ID=555555555555555555
CHANGELOG_CHANNEL_ID=666666666666666666
GITHUB_REPOSITORY=roskata729/safd-bot
GITHUB_BRANCH=main
GITHUB_WEBHOOK_SECRET=PUT_A_RANDOM_WEBHOOK_SECRET_HERE
DEPLOY_WEBHOOK_HOST=0.0.0.0
DEPLOY_WEBHOOK_PORT=9000
BOT_SERVICE_NAME=discord-activity-bot
COMMAND_PREFIX=!
```

Variable meanings:

- `DISCORD_BOT_TOKEN`: Your bot token
- `SOURCE_TEXT_CHANNEL_ID`: Channel where users submit activities
- `TARGET_TEXT_CHANNEL_ID`: Channel in the second server where approved posts are reposted
- `MANAGEMENT_CHANNEL_ID`: Channel where `!showmonthly` is allowed
- `CHANGELOG_CHANNEL_ID`: Channel where new commit messages should be posted
- `GITHUB_REPOSITORY`: GitHub repository in `owner/repo` format
- `GITHUB_BRANCH`: Branch to watch for new commits, usually `main`
- `GITHUB_WEBHOOK_SECRET`: Shared secret used to validate GitHub webhook requests
- `DEPLOY_WEBHOOK_HOST`: Host interface for the webhook listener, usually `0.0.0.0`
- `DEPLOY_WEBHOOK_PORT`: Port used by the webhook listener
- `BOT_SERVICE_NAME`: Name of the bot `systemd` service
- `COMMAND_PREFIX`: Prefix for text commands, currently `!`

The changelog feature is optional. It becomes active only when both `CHANGELOG_CHANNEL_ID` and `GITHUB_REPOSITORY` are set.

You can create your local `.env` from the template:

```powershell
Copy-Item .env.example .env
```

## Run the Bot

```powershell
python bot.py
```

If startup succeeds, the bot logs in and creates `activity_stats.db` automatically.

## Auto Deploy And Changelog

This project can deploy itself on every GitHub push by using a GitHub webhook on your VM.

Flow:

1. You push to GitHub
2. GitHub sends a webhook request to your VM
3. The webhook service starts the deploy script
4. The deploy script stops the bot, pulls the latest code, installs dependencies, and starts the bot again
5. The pushed commit messages are posted into the changelog channel after the bot comes back online

Required `.env` values:

- `CHANGELOG_CHANNEL_ID`
- `GITHUB_REPOSITORY`
- `GITHUB_BRANCH`
- `GITHUB_WEBHOOK_SECRET`
- `DEPLOY_WEBHOOK_PORT`
- `BOT_SERVICE_NAME`

Webhook endpoint:

```text
http://YOUR_VM_PUBLIC_IP:9000/github-webhook
```

Make sure your VM firewall allows inbound traffic on `DEPLOY_WEBHOOK_PORT`.

### GitHub Webhook Setup

In your GitHub repository:

1. Open `Settings`
2. Open `Webhooks`
3. Click `Add webhook`
4. Set `Payload URL` to:
   `http://YOUR_VM_PUBLIC_IP:9000/github-webhook`
5. Set `Content type` to:
   `application/json`
6. Set `Secret` to the same value as `GITHUB_WEBHOOK_SECRET` in `.env`
7. Choose:
   `Just the push event`

### VM Setup For Webhook Deploys

Start by making sure the normal bot service already works.

Install the webhook service:

```bash
sudo cp deploy/discord-deploy-webhook.service /etc/systemd/system/discord-deploy-webhook.service
sudo nano /etc/systemd/system/discord-deploy-webhook.service
```

If your VM username or path is not `ubuntu` and `/home/ubuntu/discordBot`, update:

- `User=ubuntu`
- `Group=ubuntu`
- `WorkingDirectory=/home/ubuntu/discordBot`
- `EnvironmentFile=/home/ubuntu/discordBot/.env`
- `ExecStart=/home/ubuntu/discordBot/.venv/bin/python /home/ubuntu/discordBot/deploy_webhook.py`

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-deploy-webhook
sudo systemctl status discord-deploy-webhook
```

### Sudoers Requirement

The webhook service needs permission to restart the bot service without prompting for a password.

Run:

```bash
sudo visudo
```

Add this line, replacing the username if needed:

```text
ubuntu ALL=NOPASSWD: /bin/systemctl stop discord-activity-bot, /bin/systemctl start discord-activity-bot
```

If your service name is different, update that line to match `BOT_SERVICE_NAME`.

### Changelog Message Format

Each pushed commit is posted like this:

```text
Title: Add Google Cloud deployment setup
Commit: 97ba87d
Author: Roskou
Branch: main
Repository: roskata729/safd-bot
Pushed: full Discord timestamp + relative time
Link: https://github.com/roskata729/safd-bot/commit/...
```

## Oracle Cloud Deployment

This bot is a long-running `discord.py` process. The simplest free deployment is an Oracle Cloud Always Free Ubuntu VM with `systemd`.

### 1. Create the VM

Create an Ubuntu VM in Oracle Cloud. Use an Always Free shape if available.

Open port `22` in Oracle Cloud so you can SSH into the machine.

### 2. Connect to the VM

From your local machine:

```bash
ssh ubuntu@YOUR_VM_PUBLIC_IP
```

### 3. Clone the repository

On the VM:

```bash
git clone https://github.com/roskata729/safd-bot.git ~/discordBot
cd ~/discordBot
```

### 4. Run the setup script

```bash
chmod +x deploy/setup_oracle.sh
./deploy/setup_oracle.sh
```

This installs Python dependencies, creates `.venv`, and copies `.env.example` to `.env` if needed.

### 5. Configure the bot

Edit `.env` on the VM:

```bash
nano ~/discordBot/.env
```

Fill in:

- `DISCORD_BOT_TOKEN`
- `SOURCE_TEXT_CHANNEL_ID`
- `TARGET_TEXT_CHANNEL_ID`
- `MANAGEMENT_CHANNEL_ID`
- `COMMAND_PREFIX`

### 6. Install the systemd service

Copy the service template into `systemd`:

```bash
sudo cp deploy/discord-activity-bot.service /etc/systemd/system/discord-activity-bot.service
```

If your VM user or project path is different from `ubuntu` and `/home/ubuntu/discordBot`, edit the service file first:

```bash
nano deploy/discord-activity-bot.service
```

Check these lines:

- `User=ubuntu`
- `Group=ubuntu`
- `WorkingDirectory=/home/ubuntu/discordBot`
- `EnvironmentFile=/home/ubuntu/discordBot/.env`
- `ExecStart=/home/ubuntu/discordBot/.venv/bin/python /home/ubuntu/discordBot/bot.py`

### 7. Start the bot on boot

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-activity-bot
```

### 8. Check logs

```bash
sudo systemctl status discord-activity-bot
journalctl -u discord-activity-bot -f
```

### 9. Updating the bot later

```bash
cd ~/discordBot
git pull
. .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart discord-activity-bot
```

## Google Cloud Deployment

This bot also runs cleanly on a Google Cloud Ubuntu VM with `systemd`.

### 1. Create the VM

Create a Compute Engine VM.

Recommended baseline:

- Machine type: `e2-micro` if available for your free tier
- OS: Ubuntu LTS
- Allow SSH access

You do not need to open HTTP or HTTPS ports for this bot. SSH on port `22` is enough.

### 2. Connect to the VM

You can connect from the Google Cloud console with the built-in SSH button, or from your local machine:

```bash
gcloud compute ssh YOUR_VM_NAME --zone YOUR_VM_ZONE
```

### 3. Clone the repository

On the VM:

```bash
git clone https://github.com/roskata729/safd-bot.git ~/discordBot
cd ~/discordBot
```

### 4. Run the setup script

```bash
chmod +x deploy/setup_gcp.sh
./deploy/setup_gcp.sh
```

This installs Python dependencies, creates `.venv`, and copies `.env.example` to `.env` if needed.

### 5. Configure the bot

Edit `.env` on the VM:

```bash
nano ~/discordBot/.env
```

Fill in:

- `DISCORD_BOT_TOKEN`
- `SOURCE_TEXT_CHANNEL_ID`
- `TARGET_TEXT_CHANNEL_ID`
- `MANAGEMENT_CHANNEL_ID`
- `COMMAND_PREFIX`

### 6. Install the systemd service

Copy the service template into `systemd`:

```bash
sudo cp deploy/discord-activity-bot.service /etc/systemd/system/discord-activity-bot.service
```

If your VM username or project path is different from `ubuntu` and `/home/ubuntu/discordBot`, edit the service file first:

```bash
nano deploy/discord-activity-bot.service
```

Update these lines if needed:

- `User=ubuntu`
- `Group=ubuntu`
- `WorkingDirectory=/home/ubuntu/discordBot`
- `EnvironmentFile=/home/ubuntu/discordBot/.env`
- `ExecStart=/home/ubuntu/discordBot/.venv/bin/python /home/ubuntu/discordBot/bot.py`

### 7. Start the bot on boot

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-activity-bot
```

### 8. Check logs

```bash
sudo systemctl status discord-activity-bot
journalctl -u discord-activity-bot -f
```

### 9. Updating the bot later

```bash
cd ~/discordBot
git pull
. .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart discord-activity-bot
```

## Submission Format

Users must post in the source channel using this structure:

```text
Activity Type: Patrol
Date: 15/03/2026
Participants: @Roskou @Infinity
Screens:
```

Then attach `1` to `4` image files to the same message.

Link-based example:

```text
Activity Type: RP
Date: 15/03/2026
Participants: @Roskou
Story: Fire extinguished successfully without any incidents.
Screens: https://example.com/screenshot.png
```

Mixed participant example:

```text
Activity Type: Patrol
Date: 15/03/2026
Participants: @Roskou validName
Screens:
```

RP with optional story:

```text
Activity Type: RP
Date: 17/03/2026
Participants: @Roskou
Story: Responsed to a fire and everything ended without any accidents.
Screens:
```

## Validation Rules

The bot checks the following before approving a post:

- `Activity Type:` must be `Patrol` or `RP`
- `Date:` must be in `DD/MM/YYYY`
- `Participants:` must contain at least one value
- `Story:` is optional and only allowed when `Activity Type:` is `RP`
- Every real Discord mention must point to a member that exists in the source server
- Duplicate participants are rejected
- Attachments must all be image files
- Maximum `4` screenshots per post
- A post must contain either screenshots or links, not both.
- If using `Screens:`, every value there must be a valid `http://` or `https://` link

If a participant is written in plain text instead of as a real Discord mention:

- The bot warns the author by DM
- The bot adds `🔴` to the message to show it needs attention
- The author can approve it manually by reacting to the original message with `✅`
- The repost will keep the plain-text name exactly as written

## Approval and Rejection Behavior

If valid:

- The bot adds `✅` to the original message
- The bot stores the activity for reporting
- The bot reposts the activity to the target server

If invalid:

- The bot adds `🔴` to the original message
- The bot DMs the author with the rejection reason

If the post contains plain-text participant names:

- The bot does not repost immediately
- The bot sends a warning DM to the author
- The author can react with `✅` on the original message to continue anyway
- After that confirmation, the bot reposts the activity and stores it in stats

## Repost Behavior

For approved posts, the target channel receives:

1. The header image from `assets/activity.png`
2. The activity text
3. One combined image containing all attached screenshots

If the source post uses links instead of image attachments:

- The bot reposts the activity text with the links included in the `Screens:` line

The reposted text contains:

- `Activity Type`
- `Date`
- `Participants`
- `Posted by`
- `Screens` when links are used

## Statistics

The bot stores approved activities only. Each participant in an approved post gets one count for that activity.

Example:

- One approved `Patrol` post with `3` participants adds:
  - `1` Patrol to participant A
  - `1` Patrol to participant B
  - `1` Patrol to participant C

If a participant was entered as plain text and then manually confirmed:

- The plain-text name is also counted in statistics
- It is shown in reports using the written text, because it is not linked to a Discord member ID

## `!showmonthly` Command

This command works only in the configured management channel.

### Supported formats

Default current reporting period:

```text
!showmonthly
```

Specific reporting month:

```text
!showmonthly 03/2026
```

Exact date range:

```text
!showmonthly 01/03/2026 15/03/2026
```

### Reporting logic

For `MM/YYYY`, the bot treats the month as the period ending in that month:

- `!showmonthly 03/2026` means `28/02/2026` to `27/03/2026`
- `!showmonthly 04/2026` means `28/03/2026` to `27/04/2026`

For exact dates:

- The start date is inclusive
- The end date is inclusive

### Output

The report shows:

- Each participant's `Patrols`
- Each participant's `RP`
- Each participant's `Total`
- `All activities total` at the end

Example output:

```text
Statistics for 28/02/2026 - 27/03/2026
Roskou: Patrols 4, RP 2, Total 6
HunterHL: Patrols 3, RP 1, Total 4
All activities total: 10
```

## Database

The bot uses SQLite and creates `activity_stats.db` in the project root.

Stored data includes:

- Source message ID
- Guild ID
- Channel ID
- Author ID
- Activity type
- Activity date
- Participant ID
- Creation timestamp

Only approved submissions are saved.

## Common Problems

### `ModuleNotFoundError: No module named 'audioop'`

You are likely using Python `3.13` or `3.14`.

Fix:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

### Bot does not respond

Check:

- The bot is running
- The token in `.env` is valid
- The bot has access to the configured channels
- `MESSAGE CONTENT INTENT` is enabled
- `SERVER MEMBERS INTENT` is enabled

### `!showmonthly` does not work

Check:

- You are using the configured management channel
- `MANAGEMENT_CHANNEL_ID` is correct in `.env`
- The bot can read and send messages in that channel

## Security Note

Your bot token should never be committed or shared. If it has been exposed, regenerate it in the Discord Developer Portal and update `.env`.
