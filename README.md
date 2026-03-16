# Discord Activity Bot

This bot validates activity submissions in one Discord server, reposts approved submissions to another server, and keeps activity statistics for management reports.

## Features

- Validates activity posts in a source channel.
- Accepts only `Patrol` and `RP` as activity types.
- Requires at least one valid tagged participant.
- Rejects posts where tagged users do not exist in the source server.
- Accepts either:
  - `1` to `4` screenshot attachments, or
  - link(s) in the `Screens:` field.
- Reacts with `✅` on approval.
- Reacts with `🔴` on rejection and sends the author a DM with the reason.
- Reposts approved submissions into a channel in a second server.
- Sends the header image first, then reposts one combined image made from all attached screenshots.
- Stores approved submissions in SQLite for reporting.
- Provides `!showmonthly` stats in a management channel only.

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
- [assets/activity.png](r:/Projects/discordBot/assets/activity.png): Header image sent before reposts
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
COMMAND_PREFIX=!
```

Variable meanings:

- `DISCORD_BOT_TOKEN`: Your bot token
- `SOURCE_TEXT_CHANNEL_ID`: Channel where users submit activities
- `TARGET_TEXT_CHANNEL_ID`: Channel in the second server where approved posts are reposted
- `MANAGEMENT_CHANNEL_ID`: Channel where `!showmonthly` is allowed
- `COMMAND_PREFIX`: Prefix for text commands, currently `!`

## Run the Bot

```powershell
python bot.py
```

If startup succeeds, the bot logs in and creates `activity_stats.db` automatically.

## Submission Format

Users must post in the source channel using this structure:

```text
Activity Type: Patrol
Date: 15/03/2026
Participants: @Roskou @HunterHL
Screens:
```

Then attach `1` to `4` image files to the same message.

Link-based example:

```text
Activity Type: RP
Date: 15/03/2026
Participants: @Roskou
Screens: https://example.com/screenshot.png
```

## Validation Rules

The bot checks the following before approving a post:

- `Activity Type:` must be `Patrol` or `RP`
- `Date:` must be in `DD/MM/YYYY`
- `Participants:` must contain at least one real Discord mention
- Every mentioned participant must exist in the source server
- Duplicate participants are rejected
- Attachments must all be image files
- Maximum `4` screenshots per post
- A post must contain either screenshots or links, not both
- If using `Screens:`, every value there must be a valid `http://` or `https://` link

## Approval and Rejection Behavior

If valid:

- The bot adds `✅` to the original message
- The bot stores the activity for reporting
- The bot reposts the activity to the target server

If invalid:

- The bot adds `🔴` to the original message
- The bot DMs the author with the rejection reason

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
