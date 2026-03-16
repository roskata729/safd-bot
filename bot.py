import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "activity_stats.db"
HEADER_IMAGE_PATH = BASE_DIR / "assets" / "activity.png"
ALLOWED_ACTIVITY_TYPES = {"PATROL": "Patrol", "RP": "RP"}
SCREEN_LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
SOURCE_TEXT_CHANNEL_ID = int(os.getenv("SOURCE_TEXT_CHANNEL_ID", "0"))
TARGET_TEXT_CHANNEL_ID = int(os.getenv("TARGET_TEXT_CHANNEL_ID", "0"))
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
db_lock = asyncio.Lock()


@dataclass
class ParsedSubmission:
    activity_type: str
    date_text: str
    activity_date: datetime
    participants: list[discord.Member]
    screen_links: list[str]
    image_attachments: list[discord.Attachment]


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                participant_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_message_id, participant_id)
            )
            """
        )
        connection.commit()


def normalize_activity_type(raw_value: str) -> str | None:
    compact = raw_value.strip().upper()
    if compact in ALLOWED_ACTIVITY_TYPES:
        return ALLOWED_ACTIVITY_TYPES[compact]
    return None


def parse_submission_body(message: discord.Message) -> ParsedSubmission:
    if not message.guild:
        raise ValueError("This command only works inside a server.")

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    fields: dict[str, str] = {}

    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()

    activity_type_raw = fields.get("activity type")
    if not activity_type_raw:
        raise ValueError("Missing `Activity Type:` line.")

    activity_type = normalize_activity_type(activity_type_raw)
    if not activity_type:
        raise ValueError("Activity Type must be `Patrol` or `RP`.")

    date_text = fields.get("date")
    if not date_text:
        raise ValueError("Missing `Date:` line.")

    try:
        activity_date = datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError as exc:
        raise ValueError("Date must be in `DD/MM/YYYY` format.") from exc

    participants_raw = fields.get("participants")
    if not participants_raw:
        raise ValueError("Missing `Participants:` line.")

    mention_ids = [int(member_id) for member_id in re.findall(r"<@!?(\d+)>", participants_raw)]
    if not mention_ids:
        raise ValueError("Participants must contain at least one valid server mention.")

    members: list[discord.Member] = []
    invalid_mentions: list[str] = []

    for member_id in mention_ids:
        member = message.guild.get_member(member_id)
        if member is None:
            invalid_mentions.append(f"<@{member_id}>")
            continue
        members.append(member)

    if invalid_mentions:
        raise ValueError(
            "These mentions are not valid members of this server: "
            + ", ".join(invalid_mentions)
        )

    unique_members = list(dict.fromkeys(members))
    if len(unique_members) != len(mention_ids):
        raise ValueError("Participants contains duplicate mentions. Mention each person once.")

    screens_raw = fields.get("screens", "")
    screen_links = [item for item in screens_raw.split() if SCREEN_LINK_RE.match(item)]
    image_attachments = [attachment for attachment in message.attachments if is_image(attachment)]
    non_image_attachments = [attachment for attachment in message.attachments if not is_image(attachment)]

    if non_image_attachments:
        raise ValueError("All attachments must be screenshots/images.")

    if len(image_attachments) > 4:
        raise ValueError("You can attach up to 4 screenshots only.")

    if image_attachments and screen_links:
        raise ValueError("Use screenshots or a link in `Screens:`, not both.")

    if not image_attachments and not screen_links:
        raise ValueError("Provide 1-4 image attachments or a valid link in `Screens:`.")

    if screen_links and len(screen_links) != len(screens_raw.split()):
        raise ValueError("`Screens:` must contain only valid link(s).")

    return ParsedSubmission(
        activity_type=activity_type,
        date_text=date_text,
        activity_date=activity_date,
        participants=unique_members,
        screen_links=screen_links,
        image_attachments=image_attachments,
    )


def is_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type:
        return attachment.content_type.startswith("image/")
    lowered_name = attachment.filename.lower()
    return lowered_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def build_forward_text(message: discord.Message, parsed: ParsedSubmission) -> str:
    participant_mentions = " ".join(member.mention for member in parsed.participants)
    lines = [
        f"Activity Type: {parsed.activity_type}",
        f"Date: {parsed.date_text}",
        f"Participants: {participant_mentions}",
        f"Posted by: {message.author.mention}",
    ]
    if parsed.screen_links:
        lines.append("Screens: " + " ".join(parsed.screen_links))
    return "\n".join(lines)


async def add_reaction_safely(message: discord.Message, emoji: str) -> None:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


async def deny_submission(message: discord.Message, reason: str) -> None:
    await add_reaction_safely(message, "\U0001F534")
    try:
        await message.author.send(
            "Your activity submission was denied.\n"
            f"Reason: {reason}\n\n"
            "Required format:\n"
            "Activity Type: Patrol or RP\n"
            "Date: DD/MM/YYYY\n"
            "Participants: use real Discord mentions such as <@user>\n"
            "Screens: https://example.com/screen.png\n"
            "Or attach 1-4 screenshots to the message."
        )
    except discord.Forbidden:
        pass


async def approve_submission(message: discord.Message) -> None:
    await add_reaction_safely(message, "\u2705")


async def forward_submission(parsed: ParsedSubmission, message: discord.Message) -> None:
    target_channel = bot.get_channel(TARGET_TEXT_CHANNEL_ID)
    if not isinstance(target_channel, discord.TextChannel):
        raise RuntimeError("Target channel not found. Check TARGET_TEXT_CHANNEL_ID.")

    if HEADER_IMAGE_PATH.exists():
        await target_channel.send(file=discord.File(HEADER_IMAGE_PATH))

    if parsed.image_attachments:
        files = [await attachment.to_file() for attachment in parsed.image_attachments]
        await target_channel.send(build_forward_text(message, parsed), files=files)
        return

    await target_channel.send(build_forward_text(message, parsed))


async def save_submission_stats(message: discord.Message, parsed: ParsedSubmission) -> None:
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            for participant in parsed.participants:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO activity_submissions (
                        guild_id,
                        channel_id,
                        source_message_id,
                        author_id,
                        activity_type,
                        activity_date,
                        participant_id,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.guild.id,
                        message.channel.id,
                        message.id,
                        message.author.id,
                        parsed.activity_type,
                        parsed.activity_date.strftime("%Y-%m-%d"),
                        participant.id,
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
            connection.commit()


async def build_monthly_stats(guild: discord.Guild, month: int, year: int) -> str:
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"

    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute(
                """
                SELECT participant_id,
                       SUM(CASE WHEN activity_type = 'Patrol' THEN 1 ELSE 0 END) AS patrols,
                       SUM(CASE WHEN activity_type = 'RP' THEN 1 ELSE 0 END) AS roleplays,
                       COUNT(*) AS total
                FROM activity_submissions
                WHERE guild_id = ?
                  AND activity_date >= ?
                  AND activity_date < ?
                GROUP BY participant_id
                ORDER BY total DESC, patrols DESC, roleplays DESC, participant_id ASC
                """,
                (guild.id, start, end),
            ).fetchall()

    if not rows:
        return f"No approved activities found for {month:02d}/{year}."

    lines = [f"Monthly statistics for {month:02d}/{year}"]
    for participant_id, patrols, roleplays, total in rows:
        member = guild.get_member(participant_id)
        display_name = member.display_name if member else f"User {participant_id}"
        lines.append(
            f"{display_name}: Patrols {patrols}, RP {roleplays}, Total {total}"
        )
    return "\n".join(lines)


@bot.event
async def on_ready() -> None:
    init_db()
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    await bot.process_commands(message)

    if message.guild is None:
        return

    if message.channel.id != SOURCE_TEXT_CHANNEL_ID:
        return

    if message.content.startswith(COMMAND_PREFIX):
        return

    try:
        parsed = parse_submission_body(message)
    except ValueError as exc:
        await deny_submission(message, str(exc))
        return

    try:
        await forward_submission(parsed, message)
        await save_submission_stats(message, parsed)
    except Exception as exc:
        await deny_submission(
            message,
            f"Internal bot error while forwarding the submission: {exc}",
        )
        return

    await approve_submission(message)


@bot.command(name="showmonthly")
async def show_monthly(ctx: commands.Context, month_year: str | None = None) -> None:
    if ctx.guild is None:
        await ctx.reply("This command can only be used inside a server.")
        return

    if month_year:
        try:
            month, year = month_year.split("/")
            month_value = int(month)
            year_value = int(year)
            datetime(year_value, month_value, 1)
        except (ValueError, TypeError):
            await ctx.reply("Use `!showmonthly` or `!showmonthly MM/YYYY`.")
            return
    else:
        now = datetime.utcnow()
        month_value = now.month
        year_value = now.year

    report = await build_monthly_stats(ctx.guild, month_value, year_value)
    await ctx.reply(f"```text\n{report}\n```")


def validate_environment() -> Iterable[str]:
    errors: list[str] = []
    if not os.getenv("DISCORD_BOT_TOKEN"):
        errors.append("DISCORD_BOT_TOKEN is missing in .env")
    if SOURCE_TEXT_CHANNEL_ID <= 0:
        errors.append("SOURCE_TEXT_CHANNEL_ID is missing or invalid in .env")
    if TARGET_TEXT_CHANNEL_ID <= 0:
        errors.append("TARGET_TEXT_CHANNEL_ID is missing or invalid in .env")
    return errors


if __name__ == "__main__":
    env_errors = list(validate_environment())
    if env_errors:
        raise RuntimeError("\n".join(env_errors))
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
