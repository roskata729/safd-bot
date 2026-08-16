import asyncio
import io
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "activity_stats.db"
HEADER_IMAGE_PATH = BASE_DIR / "assets" / "activity.png"
PENDING_CHANGELOG_PATH = BASE_DIR / "pending_changelog.json"
ALLOWED_ACTIVITY_TYPES = {"PATROL": "Patrol", "RP": "RP"}
SCREEN_LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
MENTION_RE = re.compile(r"^<@!?(\d+)>$")
SOURCE_TEXT_CHANNEL_ID = int(os.getenv("SOURCE_TEXT_CHANNEL_ID", "0"))
TARGET_TEXT_CHANNEL_ID = int(os.getenv("TARGET_TEXT_CHANNEL_ID", "0"))
MANAGEMENT_CHANNEL_ID = int(os.getenv("MANAGEMENT_CHANNEL_ID", "0"))
CHANGELOG_CHANNEL_ID = int(os.getenv("CHANGELOG_CHANNEL_ID", "0"))
ROSTER_CHANNEL_ID = 1231717204530298961
FIREFIGHTER_OF_THE_MONTH_ROLE_NAME = "Firefighter of the Month"
HQ_ROLES_IN_ORDER = [
    "Chief",
    "Assistant Chief",
    "Head of BC",
    "Supervisor",
]
MEMBER_ROLES_IN_ORDER = [
    "Battalion Chief",
    "Captain",
    "Lieutenant",
    "Engineer",
    "Firefighter",
    "Probationary FF",
]
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)
db_lock = asyncio.Lock()
pending_confirmations: dict[int, "PendingConfirmation"] = {}


@dataclass
class ParticipantEntry:
    participant_id: int | None
    label: str
    output_text: str


@dataclass
class ParsedSubmission:
    activity_type: str
    date_text: str
    activity_date: datetime
    participants: list[ParticipantEntry]
    has_unverified_participants: bool
    unverified_labels: list[str]
    story: str | None
    screen_links: list[str]
    image_attachments: list[discord.Attachment]


@dataclass
class PendingConfirmation:
    author_id: int
    source_channel_id: int
    parsed: ParsedSubmission


@dataclass(frozen=True)
class PromotionRule:
    current_role: str
    next_role: str
    required_activities: int
    display_current_role: str | None = None
    display_next_role: str | None = None
    extra_note: str | None = None

    @property
    def current_label(self) -> str:
        return self.display_current_role or self.current_role

    @property
    def next_label(self) -> str:
        return self.display_next_role or self.next_role


PROMOTION_RULES = [
    PromotionRule(
        current_role="Probationary FF",
        next_role="Firefighter",
        required_activities=1,
        display_current_role="Probationary Firefighter",
    ),
    PromotionRule(
        current_role="Firefighter",
        next_role="Engineer",
        required_activities=5,
    ),
    PromotionRule(
        current_role="Engineer",
        next_role="Lieutenant",
        required_activities=10,
    ),
    PromotionRule(
        current_role="Lieutenant",
        next_role="Captain",
        required_activities=15,
    ),
]
PROMOTION_RULE_BY_ROLE = {rule.current_role: rule for rule in PROMOTION_RULES}
PROMOTION_ROLE_ORDER = [rule.current_role for rule in PROMOTION_RULES]


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(activity_submissions)").fetchall()
        }
        if not columns:
            connection.execute(
                """
                CREATE TABLE activity_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    activity_type TEXT NOT NULL,
                    activity_date TEXT NOT NULL,
                    participant_id INTEGER,
                    participant_label TEXT NOT NULL,
                    participant_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_message_id, participant_key)
                )
                """
            )
        elif "participant_label" not in columns or "participant_key" not in columns:
            connection.execute(
                """
                CREATE TABLE activity_submissions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    activity_type TEXT NOT NULL,
                    activity_date TEXT NOT NULL,
                    participant_id INTEGER,
                    participant_label TEXT NOT NULL,
                    participant_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_message_id, participant_key)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO activity_submissions_new (
                    guild_id,
                    channel_id,
                    source_message_id,
                    author_id,
                    activity_type,
                    activity_date,
                    participant_id,
                    participant_label,
                    participant_key,
                    created_at
                )
                SELECT guild_id,
                       channel_id,
                       source_message_id,
                       author_id,
                       activity_type,
                       activity_date,
                       participant_id,
                       CAST(participant_id AS TEXT),
                       'id:' || participant_id,
                       created_at
                FROM activity_submissions
                """
            )
            connection.execute("DROP TABLE activity_submissions")
            connection.execute("ALTER TABLE activity_submissions_new RENAME TO activity_submissions")
        connection.commit()


def normalize_activity_type(raw_value: str) -> str | None:
    compact = raw_value.strip().upper()
    if compact in ALLOWED_ACTIVITY_TYPES:
        return ALLOWED_ACTIVITY_TYPES[compact]
    return None


def get_state_value(key: str) -> str | None:
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            (key,),
        ).fetchone()
    return row[0] if row else None


def set_state_value(key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO bot_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()


def parse_submission_body(message: discord.Message) -> ParsedSubmission:
    if not message.guild:
        raise ValueError("This command only works inside a server.")

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    fields: dict[str, str] = {}
    allowed_field_names = {"activity type", "date", "participants", "story", "screens"}
    current_key: str | None = None
    current_value_lines: list[str] = []

    for line in lines:
        potential_key, separator, potential_value = line.partition(":")
        normalized_key = potential_key.strip().lower()

        if separator and normalized_key in allowed_field_names:
            if current_key is not None:
                fields[current_key] = "\n".join(current_value_lines).strip()
            current_key = normalized_key
            current_value_lines = [potential_value.strip()]
            continue

        if current_key is not None:
            current_value_lines.append(line)

    if current_key is not None:
        fields[current_key] = "\n".join(current_value_lines).strip()

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

    raw_participants = [token.strip(",") for token in participants_raw.split() if token.strip(",")]
    if not raw_participants:
        raise ValueError("Participants must contain at least one name or mention.")

    participants: list[ParticipantEntry] = []
    invalid_mentions: list[str] = []
    unverified_labels: list[str] = []
    seen_keys: set[str] = set()

    for token in raw_participants:
        mention_match = MENTION_RE.fullmatch(token)
        if mention_match:
            member_id = int(mention_match.group(1))
            member = message.guild.get_member(member_id)
            if member is None:
                invalid_mentions.append(token)
                continue
            participant_key = f"id:{member.id}"
            if participant_key in seen_keys:
                raise ValueError("Participants contains duplicate names. List each participant once.")
            seen_keys.add(participant_key)
            participants.append(
                ParticipantEntry(
                    participant_id=member.id,
                    label=member.display_name,
                    output_text=member.mention,
                )
            )
            continue

        normalized_label = token.strip()
        participant_key = f"name:{normalized_label.casefold()}"
        if participant_key in seen_keys:
            raise ValueError("Participants contains duplicate names. List each participant once.")
        seen_keys.add(participant_key)
        unverified_labels.append(normalized_label)
        participants.append(
            ParticipantEntry(
                participant_id=None,
                label=normalized_label,
                output_text=normalized_label,
            )
        )

    if invalid_mentions:
        raise ValueError(
            "These mentions are not valid members of this server: "
            + ", ".join(invalid_mentions)
        )

    story = fields.get("story")
    if story and activity_type != "RP":
        raise ValueError("`Story:` is only allowed when Activity Type is `RP`.")

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
        participants=participants,
        has_unverified_participants=bool(unverified_labels),
        unverified_labels=unverified_labels,
        story=story or None,
        screen_links=screen_links,
        image_attachments=image_attachments,
    )


def is_image(attachment: discord.Attachment) -> bool:
    if attachment.content_type:
        return attachment.content_type.startswith("image/")
    lowered_name = attachment.filename.lower()
    return lowered_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def build_forward_text(message: discord.Message, parsed: ParsedSubmission) -> str:
    participant_mentions = " ".join(participant.output_text for participant in parsed.participants)
    lines = [
        f"Activity Type: {parsed.activity_type}",
        f"Date: {parsed.date_text}",
        f"Participants: {participant_mentions}",
        f"Posted by: {message.author.mention}",
    ]
    if parsed.story:
        story_lines = parsed.story.splitlines()
        if story_lines:
            lines.append(f"Story: {story_lines[0]}")
            lines.extend(story_lines[1:])
    if parsed.screen_links:
        lines.append("Screens: " + " ".join(parsed.screen_links))
    return "\n".join(lines)


def build_activity_embed(message: discord.Message, parsed: ParsedSubmission) -> discord.Embed:
    participant_mentions = " ".join(participant.output_text for participant in parsed.participants)
    embed = discord.Embed(
        title="SAFD Activity Report",
        description=(
            f"**Report ID:** `{message.id}`\n"
            f"[Jump to original message]({message.jump_url})"
        ),
        color=discord.Color.from_rgb(191, 62, 47),
    )
    embed.add_field(name="Date", value=parsed.date_text, inline=True)
    embed.add_field(name="Type of activity", value=parsed.activity_type, inline=True)
    embed.add_field(name="Participants", value=participant_mentions, inline=False)
    embed.add_field(name="Submitted by", value=message.author.mention, inline=False)
    if parsed.story:
        embed.add_field(name="Story", value=parsed.story[:1024], inline=False)
    if parsed.screen_links:
        embed.add_field(name="Screens", value="\n".join(parsed.screen_links), inline=False)
    embed.set_footer(text=f"Submitted by {message.author.display_name}")
    embed.timestamp = message.created_at
    if message.author.display_avatar:
        embed.set_thumbnail(url=message.author.display_avatar.url)
    return embed


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
            "Story: optional, RP only\n"
            "Screens: https://example.com/screen.png\n"
            "Or attach 1-4 screenshots to the message."
        )
    except discord.Forbidden:
        pass


async def warn_unverified_submission(message: discord.Message, parsed: ParsedSubmission) -> None:
    await add_reaction_safely(message, "\U0001F534")
    warning = (
        "Your activity submission contains participant names that are not real Discord mentions.\n"
        f"Unverified participant names: {', '.join(parsed.unverified_labels)}\n\n"
        "If you want to post it anyway, react to your original message with ✅.\n"
        "Tagged participants will still be validated normally. Plain-text names will be posted as written."
    )
    try:
        await message.author.send(warning)
    except discord.Forbidden:
        pass


async def approve_submission(message: discord.Message) -> None:
    await add_reaction_safely(message, "\u2705")


async def resolve_target_channel() -> discord.TextChannel | discord.Thread:
    target_channel = bot.get_channel(TARGET_TEXT_CHANNEL_ID)
    if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
        return target_channel

    try:
        fetched_channel = await bot.fetch_channel(TARGET_TEXT_CHANNEL_ID)
    except discord.HTTPException as exc:
        raise RuntimeError(
            "Target channel could not be fetched. Check TARGET_TEXT_CHANNEL_ID and bot access."
        ) from exc

    if isinstance(fetched_channel, (discord.TextChannel, discord.Thread)):
        return fetched_channel

    if isinstance(fetched_channel, discord.ForumChannel):
        raise RuntimeError(
            "TARGET_TEXT_CHANNEL_ID points to a forum channel. Use the ID of a specific post/thread inside that forum."
        )

    raise RuntimeError(
        "Target channel is not a text channel or thread. Check TARGET_TEXT_CHANNEL_ID."
    )


async def resolve_changelog_channel() -> discord.TextChannel | discord.Thread:
    if CHANGELOG_CHANNEL_ID <= 0:
        raise RuntimeError("CHANGELOG_CHANNEL_ID is missing or invalid in .env")

    target_channel = bot.get_channel(CHANGELOG_CHANNEL_ID)
    if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
        return target_channel

    try:
        fetched_channel = await bot.fetch_channel(CHANGELOG_CHANNEL_ID)
    except discord.HTTPException as exc:
        raise RuntimeError(
            "Changelog channel could not be fetched. Check CHANGELOG_CHANNEL_ID and bot access."
        ) from exc

    if isinstance(fetched_channel, (discord.TextChannel, discord.Thread)):
        return fetched_channel

    raise RuntimeError(
        "Changelog channel is not a text channel or thread. Check CHANGELOG_CHANNEL_ID."
    )


async def resolve_roster_channel() -> discord.TextChannel | discord.Thread:
    target_channel = bot.get_channel(ROSTER_CHANNEL_ID)
    if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
        return target_channel

    try:
        fetched_channel = await bot.fetch_channel(ROSTER_CHANNEL_ID)
    except discord.HTTPException as exc:
        raise RuntimeError(
            "Roster channel could not be fetched. Check the channel ID and bot access."
        ) from exc

    if isinstance(fetched_channel, (discord.TextChannel, discord.Thread)):
        return fetched_channel

    raise RuntimeError("Roster channel is not a text channel or thread.")


def build_single_role_roster(guild: discord.Guild, role_name: str) -> str | None:
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        return f"**• {role_name} — 0**\n▫️ _No members assigned_"

    role_members = [
        member.display_name
        for member in guild.members
        if not member.bot and role in member.roles
    ]
    role_members.sort(key=str.casefold)
    if role_members:
        member_lines = [f"👤 {member_name}" for member_name in role_members]
    else:
        member_lines = ["▫️ _No members assigned_"]
    lines = [f"**• {role.name} — {len(role_members)}**", *member_lines]
    return "\n".join(lines)


def build_roster_messages(guild: discord.Guild) -> list[str]:
    messages: list[str] = []

    hq_entries = [
        roster_text
        for role_name in HQ_ROLES_IN_ORDER
        if (roster_text := build_single_role_roster(guild, role_name)) is not None
    ]
    if hq_entries:
        messages.append("\n\n".join(["## **🏢 HQ Team**", *hq_entries]))

    member_entries = [
        roster_text
        for role_name in MEMBER_ROLES_IN_ORDER
        if (roster_text := build_single_role_roster(guild, role_name)) is not None
    ]
    if member_entries:
        messages.append("\n\n".join(["## **🚒 Members**", *member_entries]))

    return messages


async def clear_previous_roster_posts(channel: discord.TextChannel | discord.Thread) -> None:
    stored_message_ids = get_state_value("roster_message_ids")
    if not stored_message_ids:
        return

    try:
        message_ids = json.loads(stored_message_ids)
    except json.JSONDecodeError:
        message_ids = []

    if not isinstance(message_ids, list):
        return

    for message_id in message_ids:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.HTTPException, ValueError):
            continue
        if bot.user is None or message.author.id != bot.user.id:
            continue
        try:
            await message.delete()
        except discord.HTTPException:
            continue


async def refresh_roster_posts() -> None:
    roster_channel = await resolve_roster_channel()
    guild = roster_channel.guild

    await clear_previous_roster_posts(roster_channel)

    message_ids: list[int] = []
    for content in build_roster_messages(guild):
        posted_message = await roster_channel.send(content)
        message_ids.append(posted_message.id)

    set_state_value("roster_message_ids", json.dumps(message_ids))


async def forward_submission(parsed: ParsedSubmission, message: discord.Message) -> None:
    target_channel = await resolve_target_channel()
    embed = build_activity_embed(message, parsed)

    if parsed.image_attachments:
        merged_file = await build_combined_image_file(parsed.image_attachments)
        embed.set_image(url=f"attachment://{merged_file.filename}")
        await target_channel.send(embed=embed, file=merged_file)
        return

    await target_channel.send(embed=embed)


async def build_combined_image_file(
    attachments: list[discord.Attachment],
) -> discord.File:
    images: list[Image.Image] = []
    for attachment in attachments:
        image_bytes = await attachment.read()
        with Image.open(io.BytesIO(image_bytes)) as opened_image:
            images.append(opened_image.convert("RGB"))

    collage = create_image_collage(images)
    output = io.BytesIO()
    collage.save(output, format="JPEG", quality=92)
    output.seek(0)
    collage.close()
    for image in images:
        image.close()
    return discord.File(output, filename="activity_collage.jpg")


def create_image_collage(images: list[Image.Image]) -> Image.Image:
    if len(images) == 1:
        single = images[0].copy()
        return decorate_collage([single], cols=1, rows=1)

    cell_width = 1200
    cell_height = 675
    padding = 24

    if len(images) == 2:
        cols, rows = 2, 1
    else:
        cols, rows = 2, 2
    return decorate_collage(images, cols=cols, rows=rows, cell_width=cell_width, cell_height=cell_height, padding=padding)


def decorate_collage(
    images: list[Image.Image],
    cols: int,
    rows: int,
    cell_width: int = 1200,
    cell_height: int = 675,
    padding: int = 24,
) -> Image.Image:
    header_height = 76
    canvas_width = padding + cols * cell_width + (cols - 1) * padding + padding
    canvas_height = header_height + padding + rows * cell_height + (rows - 1) * padding + padding
    canvas = Image.new("RGB", (canvas_width, canvas_height), color=(20, 24, 30))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    draw.rounded_rectangle(
        (padding, 16, canvas_width - padding, 16 + header_height - 20),
        radius=18,
        fill=(28, 38, 48),
        outline=(49, 208, 136),
        width=3,
    )
    draw.text((padding + 24, 28), "SAFD Combined Evidence", fill=(240, 244, 248), font=font)

    for index, image in enumerate(images):
        if len(images) == 3 and index == 2:
            row = 1
            x = (canvas_width - cell_width) // 2
        else:
            row = index // cols
            col = index % cols
            x = padding + col * (cell_width + padding)
        y = header_height + padding + row * (cell_height + padding)
        draw.rounded_rectangle(
            (x - 4, y - 4, x + cell_width + 4, y + cell_height + 4),
            radius=16,
            fill=(17, 22, 28),
            outline=(38, 160, 112),
            width=3,
        )
        fitted = fit_image_to_box(image, cell_width, cell_height)
        paste_x = x + (cell_width - fitted.width) // 2
        paste_y = y + (cell_height - fitted.height) // 2
        canvas.paste(fitted, (paste_x, paste_y))
        label = f"Screenshot #{index + 1}"
        draw.rounded_rectangle(
            (x + 14, y + 14, x + 14 + 180, y + 14 + 34),
            radius=10,
            fill=(23, 52, 70),
        )
        draw.text((x + 28, y + 24), label, fill=(240, 244, 248), font=font)
        fitted.close()

    return canvas


def fit_image_to_box(image: Image.Image, width: int, height: int) -> Image.Image:
    resized = image.copy()
    resized.thumbnail((width, height), Image.Resampling.LANCZOS)
    return resized


async def save_submission_stats(message: discord.Message, parsed: ParsedSubmission) -> None:
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            for participant in parsed.participants:
                participant_key = (
                    f"id:{participant.participant_id}"
                    if participant.participant_id is not None
                    else f"name:{participant.label.casefold()}"
                )
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
                        participant_label,
                        participant_key,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.guild.id,
                        message.channel.id,
                        message.id,
                        message.author.id,
                        parsed.activity_type,
                        parsed.activity_date.strftime("%Y-%m-%d"),
                        participant.participant_id,
                        participant.label,
                        participant_key,
                        datetime.utcnow().isoformat(timespec="seconds"),
                    ),
                )
            connection.commit()


def get_reporting_window(month: int, year: int) -> tuple[datetime, datetime]:
    end = datetime(year, month, 28)
    if month == 1:
        start = datetime(year - 1, 12, 28)
    else:
        start = datetime(year, month - 1, 28)
    return start, end


def get_current_reporting_period(now: datetime) -> tuple[int, int]:
    if now.day >= 28:
        next_month_anchor = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_month_anchor.month, next_month_anchor.year
    return now.month, now.year


async def build_stats_for_period(
    guild: discord.Guild,
    start_dt: datetime,
    end_dt_exclusive: datetime,
    label: str,
) -> str:
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt_exclusive.strftime("%Y-%m-%d")
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute(
                """
                SELECT participant_id,
                       participant_label,
                       SUM(CASE WHEN activity_type = 'Patrol' THEN 1 ELSE 0 END) AS patrols,
                       SUM(CASE WHEN activity_type = 'RP' THEN 1 ELSE 0 END) AS roleplays,
                       COUNT(*) AS total
                FROM activity_submissions
                WHERE guild_id = ?
                  AND activity_date >= ?
                  AND activity_date < ?
                GROUP BY CASE
                             WHEN participant_id IS NOT NULL THEN 'id:' || participant_id
                             ELSE 'name:' || participant_label
                         END
                ORDER BY total DESC, patrols DESC, roleplays DESC, participant_label COLLATE NOCASE ASC
                """,
                (guild.id, start, end),
            ).fetchall()

    if not rows:
        return f"No approved activities found for {label}."

    grand_total = 0
    lines = [f"Statistics for {label}"]
    for participant_id, participant_label, patrols, roleplays, total in rows:
        member = guild.get_member(participant_id) if participant_id is not None else None
        display_name = member.display_name if member else participant_label
        grand_total += total
        lines.append(
            f"{display_name}: Patrols {patrols}, RP {roleplays}, Total {total}"
        )
    lines.append(f"All activities total: {grand_total}")
    return "\n".join(lines)


async def get_activity_totals_for_period(
    guild_id: int,
    start_dt: datetime,
    end_dt_exclusive: datetime,
) -> dict[int, int]:
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt_exclusive.strftime("%Y-%m-%d")
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute(
                """
                SELECT participant_id, COUNT(*) AS total
                FROM activity_submissions
                WHERE guild_id = ?
                  AND participant_id IS NOT NULL
                  AND activity_date >= ?
                  AND activity_date < ?
                GROUP BY participant_id
                """,
                (guild_id, start, end),
            ).fetchall()
    return {participant_id: total for participant_id, total in rows}


def get_reporting_period_key(date_value: datetime) -> tuple[int, int]:
    if date_value.day >= 28:
        next_month_anchor = (date_value.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_month_anchor.month, next_month_anchor.year
    return date_value.month, date_value.year


def get_reporting_period_label(month: int, year: int) -> str:
    start_dt, end_dt = get_reporting_window(month, year)
    display_end = (end_dt - timedelta(days=1)).strftime("%d/%m/%Y")
    return f"{start_dt.strftime('%d/%m/%Y')} - {display_end}"


async def get_all_activity_rows(
    guild_id: int,
) -> list[tuple[int, str, str]]:
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute(
                """
                SELECT participant_id, participant_label, activity_date
                FROM activity_submissions
                WHERE guild_id = ?
                  AND participant_id IS NOT NULL
                """,
                (guild_id,),
            ).fetchall()
    return rows


def resolve_member_name(
    guild: discord.Guild,
    member_id: int,
    fallback_names: dict[int, str],
) -> str:
    member = guild.get_member(member_id)
    if member is not None:
        return member.display_name
    return fallback_names.get(member_id, f"User {member_id}")


async def build_firefighter_of_the_month_sections(
    guild: discord.Guild,
    current_period_key: tuple[int, int],
) -> tuple[list[str], list[str]]:
    rows = await get_all_activity_rows(guild.id)
    if not rows:
        return ["- No approved activities found yet."], ["- No historical winners yet."]

    current_period_totals: dict[int, int] = {}
    historical_period_totals: dict[tuple[int, int], dict[int, int]] = {}
    fallback_names: dict[int, str] = {}

    for participant_id, participant_label, activity_date in rows:
        parsed_date = datetime.strptime(activity_date, "%Y-%m-%d")
        period_key = get_reporting_period_key(parsed_date)
        fallback_names[participant_id] = participant_label

        if period_key == current_period_key:
            current_period_totals[participant_id] = current_period_totals.get(participant_id, 0) + 1
            continue

        period_bucket = historical_period_totals.setdefault(period_key, {})
        period_bucket[participant_id] = period_bucket.get(participant_id, 0) + 1

    current_leader_lines: list[str] = []
    if current_period_totals:
        top_total = max(current_period_totals.values())
        leader_ids = sorted(
            [member_id for member_id, total in current_period_totals.items() if total == top_total],
            key=lambda member_id: resolve_member_name(guild, member_id, fallback_names).casefold(),
        )
        current_leader_lines = [
            f"- {resolve_member_name(guild, member_id, fallback_names)}: {top_total} activities, current leader for `{FIREFIGHTER_OF_THE_MONTH_ROLE_NAME}`"
            for member_id in leader_ids
        ]
    else:
        current_leader_lines = ["- No approved activities yet in the current reporting period."]

    firefighter_title_counts: dict[int, int] = {}
    for period_totals in historical_period_totals.values():
        if not period_totals:
            continue
        top_total = max(period_totals.values())
        for member_id, total in period_totals.items():
            if total == top_total:
                firefighter_title_counts[member_id] = firefighter_title_counts.get(member_id, 0) + 1

    historical_lines: list[str] = []
    if firefighter_title_counts:
        sorted_counts = sorted(
            firefighter_title_counts.items(),
            key=lambda item: (-item[1], resolve_member_name(guild, item[0], fallback_names).casefold()),
        )
        historical_lines = [
            f"- {resolve_member_name(guild, member_id, fallback_names)}: {count} time(s)"
            for member_id, count in sorted_counts
        ]
    else:
        historical_lines = ["- No historical winners yet."]

    return current_leader_lines, historical_lines


def get_member_promotion_rule(member: discord.Member) -> PromotionRule | None:
    matched_rules = [
        PROMOTION_RULE_BY_ROLE[role_name]
        for role_name in PROMOTION_ROLE_ORDER
        if discord.utils.get(member.roles, name=role_name) is not None
    ]
    if not matched_rules:
        return None
    return matched_rules[-1]


async def build_promotion_report_for_current_period(guild: discord.Guild) -> str:
    now = datetime.utcnow()
    month_value, year_value = get_current_reporting_period(now)
    start_dt, end_dt = get_reporting_window(month_value, year_value)
    label = get_reporting_period_label(month_value, year_value)
    totals_by_member = await get_activity_totals_for_period(guild.id, start_dt, end_dt)
    current_leader_lines, historical_title_lines = await build_firefighter_of_the_month_sections(
        guild,
        (month_value, year_value),
    )

    eligible_lines: list[str] = []
    not_ready_lines: list[str] = []

    for member in guild.members:
        if member.bot:
            continue

        rule = get_member_promotion_rule(member)
        if rule is None:
            continue

        total = totals_by_member.get(member.id, 0)
        if total <= 0:
            continue
        summary = (
            f"{member.display_name}: {rule.current_label} -> {rule.next_label}, "
            f"Activities {total}/{rule.required_activities}"
        )
        if total >= rule.required_activities:
            if rule.extra_note:
                summary = f"{summary}. {rule.extra_note}"
            eligible_lines.append(summary)
        else:
            remaining = rule.required_activities - total
            not_ready_lines.append(f"{summary}, Needs {remaining} more")

    if not eligible_lines and not not_ready_lines:
        return f"No promotable members found for {label}."

    lines = [
        f"Promotion check for {label}",
        "",
        "Eligible now:",
    ]

    if eligible_lines:
        lines.extend(f"- {line}" for line in eligible_lines)
    else:
        lines.append("- No members are currently eligible.")

    lines.extend(["", "Not eligible yet:"])
    if not_ready_lines:
        lines.extend(f"- {line}" for line in not_ready_lines)
    else:
        lines.append("- Everyone with a tracked rank currently meets the activity requirement.")

    lines.extend(["", f"{FIREFIGHTER_OF_THE_MONTH_ROLE_NAME} leader:"])
    lines.extend(current_leader_lines)

    lines.extend(["", f"{FIREFIGHTER_OF_THE_MONTH_ROLE_NAME} history:"])
    lines.extend(historical_title_lines)

    return "\n".join(lines)


def find_member_by_name(guild: discord.Guild, raw_name: str) -> discord.Member | None:
    lookup = raw_name.strip().casefold()
    if not lookup:
        return None

    for member in guild.members:
        display_name = member.display_name.casefold()
        global_name = member.global_name.casefold() if member.global_name else None
        username = member.name.casefold()
        if lookup in {display_name, username, global_name}:
            return member
    return None


async def get_last_activity_details(
    guild: discord.Guild,
    player_input: str,
    mentioned_member: discord.Member | None = None,
) -> str:
    lookup_label = player_input.strip()
    if not lookup_label:
        raise ValueError("Provide a player mention or name.")

    member = mentioned_member or find_member_by_name(guild, lookup_label)
    async with db_lock:
        with sqlite3.connect(DB_PATH) as connection:
            if member is not None:
                row = connection.execute(
                    """
                    SELECT participant_id,
                           participant_label,
                           activity_type,
                           activity_date,
                           author_id,
                           channel_id,
                           source_message_id,
                           created_at
                    FROM activity_submissions
                    WHERE guild_id = ?
                      AND (participant_id = ? OR participant_label = ? COLLATE NOCASE)
                    ORDER BY activity_date DESC, created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (guild.id, member.id, lookup_label),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT participant_id,
                           participant_label,
                           activity_type,
                           activity_date,
                           author_id,
                           channel_id,
                           source_message_id,
                           created_at
                    FROM activity_submissions
                    WHERE guild_id = ?
                      AND participant_label = ? COLLATE NOCASE
                    ORDER BY activity_date DESC, created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (guild.id, lookup_label),
                ).fetchone()

    if row is None:
        return f"No approved activities found for `{lookup_label}`."

    (
        participant_id,
        participant_label,
        activity_type,
        activity_date,
        author_id,
        channel_id,
        source_message_id,
        created_at,
    ) = row
    participant_member = guild.get_member(participant_id) if participant_id is not None else None
    author_member = guild.get_member(author_id)
    display_name = participant_member.display_name if participant_member else participant_label
    author_name = author_member.display_name if author_member else f"User {author_id}"
    jump_url = (
        f"https://discord.com/channels/{guild.id}/{channel_id}/{source_message_id}"
    )

    lines = [
        f"Last approved activity for {display_name}",
        f"Type: {activity_type}",
        f"Date: {datetime.strptime(activity_date, '%Y-%m-%d').strftime('%d/%m/%Y')}",
        f"Posted by: {author_name}",
        f"Recorded at: {created_at}",
        f"Original post: {jump_url}",
    ]
    return "\n".join(lines)


def build_help_text() -> str:
    source_channel_hint = (
        f"<#{SOURCE_TEXT_CHANNEL_ID}>" if SOURCE_TEXT_CHANNEL_ID > 0 else "the submission channel"
    )
    management_channel_hint = (
        f"<#{MANAGEMENT_CHANNEL_ID}>" if MANAGEMENT_CHANNEL_ID > 0 else "the management channel"
    )

    sections = [
        f"{COMMAND_PREFIX}help",
        "",
        "Shows this help message with commands, parameters, rules, and examples.",
        "",
        "SUBMISSION FORMAT",
        f"Post activity submissions in {source_channel_hint} using this format:",
        "Activity Type: Patrol or RP",
        "Date: DD/MM/YYYY",
        "Participants: @User1 @User2",
        "Story: Optional, only for RP",
        "Screens: leave blank if attaching images, or add one link",
        "",
        "SUBMISSION RULES",
        "Activity Type must be Patrol or RP.",
        "Date must use DD/MM/YYYY.",
        "At least 1 participant is required.",
        "Participants can be tagged with @ or written as plain text.",
        "If a plain-text participant is used, the bot warns the author and waits for a ✅ confirmation.",
        "Tagged participants must exist in the server.",
        "Use either 1 link or 1 to 4 screenshots.",
        "If everything is valid, the bot reacts with ✅.",
        "If something is wrong, the bot reacts with 🔴 and sends a DM with the reason.",
        "",
        "EXAMPLE SUBMISSIONS",
        "Example 1:",
        "Activity Type: Patrol",
        "Date: 16/08/2026",
        "Participants: @Roskou @Infinity",
        "Screens:",
        "",
        "Example 2:",
        "Activity Type: RP",
        "Date: 16/08/2026",
        "Participants: @Roskou validName",
        "Story: Training session at the station, equipment checks, and cleanup after callout.",
        "Screens: https://imgur.com/a/example",
        "",
        "MANAGEMENT COMMANDS",
        f"These commands can only be used in {management_channel_hint}.",
        "",
        f"1. {COMMAND_PREFIX}showmonthly",
        "Shows activity totals for the current reporting period.",
        "Current reporting months run from the 28th of one month to the 27th of the next month.",
        f"Example: {COMMAND_PREFIX}showmonthly",
        "",
        f"2. {COMMAND_PREFIX}showmonthly MM/YYYY",
        "Shows activity totals for the selected reporting month.",
        "Important: MM/YYYY means the month whose period ends in that month.",
        "Example: 03/2026 means 28/02/2026 to 27/03/2026.",
        f"Example command: {COMMAND_PREFIX}showmonthly 03/2026",
        "",
        f"3. {COMMAND_PREFIX}showmonthly DD/MM/YYYY DD/MM/YYYY",
        "Shows activity totals for an exact custom date range.",
        f"Example: {COMMAND_PREFIX}showmonthly 01/08/2026 15/08/2026",
        "",
        f"4. {COMMAND_PREFIX}lastactivity @Player",
        f"5. {COMMAND_PREFIX}lastactivity PlayerName",
        "Shows the latest approved activity for the selected player.",
        "The result includes type, date, author, recorded time, and a jump link to the original submission.",
        f"Examples: {COMMAND_PREFIX}lastactivity @Roskou",
        f"          {COMMAND_PREFIX}lastactivity Roskou",
        "",
        f"6. {COMMAND_PREFIX}promotioncheck",
        "Shows who is eligible for promotion in the current reporting period.",
        "The report uses the 28th to 27th month window and compares each member's current rank with the required monthly activity count.",
        "Tracked promotions:",
        "Probationary Firefighter -> Firefighter: 1 activity",
        "Firefighter -> Engineer: 5 activities",
        "Engineer -> Lieutenant: 10 activities",
        "Lieutenant -> Captain: 15 activities",
        "The report also shows the current leader for Firefighter of the Month and a historical count of past winners.",
        f"Example: {COMMAND_PREFIX}promotioncheck",
        "",
        "NOTES",
        "Statistics only count approved activities.",
        "Roster posts and changelog posts are automatic and do not need commands.",
    ]
    return "\n".join(sections)


async def reply_with_text_blocks(
    ctx: commands.Context,
    text: str,
    *,
    block_type: str = "text",
    max_length: int = 1900,
) -> None:
    lines = text.splitlines()
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for line in lines:
        line_length = len(line) + 1
        if current_chunk and current_length + line_length > max_length:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for index, chunk in enumerate(chunks):
        wrapped = f"```{block_type}\n{chunk}\n```"
        if index == 0:
            await ctx.reply(wrapped)
        else:
            await ctx.send(wrapped)


async def build_monthly_stats(guild: discord.Guild, month: int, year: int) -> str:
    start_dt, end_dt = get_reporting_window(month, year)
    display_end = (end_dt - timedelta(days=1)).strftime("%d/%m/%Y")
    label = f"{start_dt.strftime('%d/%m/%Y')} - {display_end}"
    return await build_stats_for_period(guild, start_dt, end_dt, label)


def load_pending_changelog() -> list[dict[str, str]]:
    if not PENDING_CHANGELOG_PATH.exists():
        return []

    payload = json.loads(PENDING_CHANGELOG_PATH.read_text(encoding="utf-8"))
    commits = payload.get("commits", [])
    return commits if isinstance(commits, list) else []


def parse_commit_timestamp(timestamp_text: str | None) -> datetime | None:
    if not timestamp_text:
        return None
    try:
        return datetime.strptime(timestamp_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_changelog_embed(commit: dict[str, str]) -> discord.Embed:
    title = commit["message"]
    if len(title) > 256:
        title = title[:253] + "..."

    embed = discord.Embed(
        title=title,
        url=commit["url"],
        color=discord.Color.blue(),
    )
    embed.add_field(name="Commit", value=f"`{commit['short_sha']}`", inline=True)
    embed.add_field(name="Author", value=commit["author"], inline=True)
    embed.add_field(name="Branch", value=GITHUB_BRANCH, inline=True)
    embed.add_field(name="Repository", value=GITHUB_REPOSITORY or "Unknown", inline=False)

    committed_at = parse_commit_timestamp(commit.get("timestamp"))
    if committed_at is not None:
        unix_time = int(committed_at.timestamp())
        embed.add_field(
            name="Pushed",
            value=f"<t:{unix_time}:F>\n<t:{unix_time}:R>",
            inline=False,
        )
        embed.timestamp = committed_at

    embed.set_footer(text="Bot changelog")
    return embed


async def post_pending_changelog() -> None:
    if CHANGELOG_CHANNEL_ID <= 0:
        return

    commits = load_pending_changelog()
    if not commits:
        return

    changelog_channel = await resolve_changelog_channel()
    for commit in commits:
        await changelog_channel.send(embed=build_changelog_embed(commit))

    PENDING_CHANGELOG_PATH.unlink(missing_ok=True)


@tasks.loop(hours=24)
async def roster_refresh_loop() -> None:
    try:
        await refresh_roster_posts()
    except Exception as exc:
        print(f"Roster refresh error: {exc}")


@roster_refresh_loop.before_loop
async def before_roster_refresh_loop() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    init_db()
    try:
        await post_pending_changelog()
    except Exception as exc:
        print(f"Changelog post error: {exc}")
    try:
        await refresh_roster_posts()
    except Exception as exc:
        print(f"Initial roster refresh error: {exc}")
    if not roster_refresh_loop.is_running():
        roster_refresh_loop.start()
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

    if parsed.has_unverified_participants:
        pending_confirmations[message.id] = PendingConfirmation(
            author_id=message.author.id,
            source_channel_id=message.channel.id,
            parsed=parsed,
        )
        await warn_unverified_submission(message, parsed)
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


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if bot.user and payload.user_id == bot.user.id:
        return

    if str(payload.emoji) != "\u2705":
        return

    pending = pending_confirmations.get(payload.message_id)
    if pending is None:
        return

    if payload.user_id != pending.author_id or payload.channel_id != pending.source_channel_id:
        return

    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        pending_confirmations.pop(payload.message_id, None)
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        pending_confirmations.pop(payload.message_id, None)
        return

    try:
        await forward_submission(pending.parsed, message)
        await save_submission_stats(message, pending.parsed)
    except Exception as exc:
        await deny_submission(
            message,
            f"Internal bot error while forwarding the submission: {exc}",
        )
        pending_confirmations.pop(payload.message_id, None)
        return

    pending_confirmations.pop(payload.message_id, None)
    await approve_submission(message)


@bot.command(name="showmonthly")
async def show_monthly(
    ctx: commands.Context,
    first_arg: str | None = None,
    second_arg: str | None = None,
) -> None:
    if ctx.guild is None:
        await ctx.reply("This command can only be used inside a server.")
        return

    if ctx.channel.id != MANAGEMENT_CHANNEL_ID:
        await ctx.reply("This command can only be used in the management channel.")
        return

    if first_arg and second_arg:
        try:
            start_dt = datetime.strptime(first_arg, "%d/%m/%Y")
            end_dt_inclusive = datetime.strptime(second_arg, "%d/%m/%Y")
        except ValueError:
            await ctx.reply(
                "Use `!showmonthly`, `!showmonthly MM/YYYY`, or "
                "`!showmonthly DD/MM/YYYY DD/MM/YYYY`."
            )
            return

        if end_dt_inclusive < start_dt:
            await ctx.reply("The end date must be the same as or later than the start date.")
            return

        end_dt_exclusive = end_dt_inclusive + timedelta(days=1)
        label = f"{start_dt.strftime('%d/%m/%Y')} - {end_dt_inclusive.strftime('%d/%m/%Y')}"
        report = await build_stats_for_period(ctx.guild, start_dt, end_dt_exclusive, label)
    elif first_arg:
        try:
            month, year = first_arg.split("/")
            month_value = int(month)
            year_value = int(year)
            datetime(year_value, month_value, 28)
        except (ValueError, TypeError):
            await ctx.reply(
                "Use `!showmonthly`, `!showmonthly MM/YYYY`, or "
                "`!showmonthly DD/MM/YYYY DD/MM/YYYY`."
            )
            return

        report = await build_monthly_stats(ctx.guild, month_value, year_value)
    else:
        now = datetime.utcnow()
        month_value, year_value = get_current_reporting_period(now)
        report = await build_monthly_stats(ctx.guild, month_value, year_value)
    await ctx.reply(f"```text\n{report}\n```")


@bot.command(name="help")
async def help_command(ctx: commands.Context) -> None:
    await reply_with_text_blocks(ctx, build_help_text())


@bot.command(name="lastactivity")
async def last_activity(ctx: commands.Context, *, player: str | None = None) -> None:
    if ctx.guild is None:
        await ctx.reply("This command can only be used inside a server.")
        return

    if ctx.channel.id != MANAGEMENT_CHANNEL_ID:
        await ctx.reply("This command can only be used in the management channel.")
        return

    if not player:
        await ctx.reply(f"Use `{COMMAND_PREFIX}lastactivity @Player` or `{COMMAND_PREFIX}lastactivity PlayerName`.")
        return

    report = await get_last_activity_details(
        ctx.guild,
        player,
        ctx.message.mentions[0] if ctx.message.mentions else None,
    )
    await ctx.reply(f"```text\n{report}\n```")


@bot.command(name="promotioncheck")
async def promotion_check(ctx: commands.Context) -> None:
    if ctx.guild is None:
        await ctx.reply("This command can only be used inside a server.")
        return

    if ctx.channel.id != MANAGEMENT_CHANNEL_ID:
        await ctx.reply("This command can only be used in the management channel.")
        return

    report = await build_promotion_report_for_current_period(ctx.guild)
    await reply_with_text_blocks(ctx, report)


def validate_environment() -> Iterable[str]:
    errors: list[str] = []
    if not os.getenv("DISCORD_BOT_TOKEN"):
        errors.append("DISCORD_BOT_TOKEN is missing in .env")
    if SOURCE_TEXT_CHANNEL_ID <= 0:
        errors.append("SOURCE_TEXT_CHANNEL_ID is missing or invalid in .env")
    if TARGET_TEXT_CHANNEL_ID <= 0:
        errors.append("TARGET_TEXT_CHANNEL_ID is missing or invalid in .env")
    if MANAGEMENT_CHANNEL_ID <= 0:
        errors.append("MANAGEMENT_CHANNEL_ID is missing or invalid in .env")
    if CHANGELOG_CHANNEL_ID > 0 and not GITHUB_REPOSITORY:
        errors.append("GITHUB_REPOSITORY is required when CHANGELOG_CHANNEL_ID is set")
    if GITHUB_REPOSITORY and CHANGELOG_CHANNEL_ID <= 0:
        errors.append("CHANGELOG_CHANNEL_ID is required when GITHUB_REPOSITORY is set")
    return errors


if __name__ == "__main__":
    env_errors = list(validate_environment())
    if env_errors:
        raise RuntimeError("\n".join(env_errors))
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
