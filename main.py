import os
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import time
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# ============================================================
# LOGGING SETUP
# ============================================================
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ImpostorBot")


# ============================================================
# DATA PERSISTENCE (JSON) - Multi-Guild Support
# ============================================================
DATA_DIR = Path("data")

def get_guild_data_path(guild_id: Optional[int]) -> Path:
    """Get data file path for a specific guild or global."""
    if guild_id:
        return DATA_DIR / f"guild_{guild_id}.json"
    return DATA_DIR / "global.json"

def load_data(guild_id: Optional[int] = None):
    path = get_guild_data_path(guild_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load data from {path}: {e}")
            return {"players": {}, "history": [], "series": []}
    return {"players": {}, "history": [], "series": []}

def save_data(data: dict, guild_id: Optional[int] = None):
    path = get_guild_data_path(guild_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved data to {path}")
    except IOError as e:
        logger.error(f"Failed to save data to {path}: {e}")

def get_player(pid: int, guild_id: Optional[int] = None):
    data = load_data(guild_id)
    return data["players"].get(str(pid))

def ensure_player(player, guild_id: Optional[int] = None):
    """Make sure a player exists in the DB with default stats."""
    data = load_data(guild_id)
    pid = str(player.id)
    if pid not in data["players"]:
        data["players"][pid] = _new_player(player.display_name)
    data["players"][pid]["name"] = player.display_name
    save_data(data, guild_id)

def _new_player(name: str):
    return {
        "name": name,
        "games_played": 0,
        "games_won": 0,
        "games_lost": 0,
        "times_impostor": 0,
        "impostor_wins": 0,
        "impostor_losses": 0,
        "correct_votes": 0,
        "total_votes": 0,
        "points": 0,
    }

def bump(pid: int, guild_id: Optional[int] = None, **kwargs):
    """Increment numeric fields for a player."""
    data = load_data(guild_id)
    pid_str = str(pid)
    if pid_str not in data["players"]:
        logger.warning(f"Attempted to bump non-existent player {pid}")
        return
    for k, v in kwargs.items():
        if k in data["players"][pid_str]:
            data["players"][pid_str][k] += v
    save_data(data, guild_id)

def add_history(entry: dict, guild_id: Optional[int] = None):
    data = load_data(guild_id)
    data["history"].append(entry)
    data["history"] = data["history"][-50:]  # keep last 50
    save_data(data, guild_id)

def add_series(series_data: dict, guild_id: Optional[int] = None):
    """Save a completed series to history."""
    data = load_data(guild_id)
    if "series" not in data:
        data["series"] = []
    data["series"].append(series_data)
    data["series"] = data["series"][-20:]  # keep last 20 series
    save_data(data, guild_id)


# ============================================================
# BOT SETUP
# ============================================================
class SecretPickerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info(f"Synced slash commands for {self.user}")

    async def on_ready(self):
        activity = discord.Activity(type=discord.ActivityType.watching, name="Match Processing")
        await self.change_presence(status=discord.Status.online, activity=activity)
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")

    async def on_command_error(self, interaction: discord.Interaction, error: Exception):
        """Global error handler."""
        logger.error(f"Command error: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ An error occurred. Please try again.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again.", ephemeral=True
                )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

bot = SecretPickerBot()

# --- CONSTANTS & GLOBALS ---
EMBED_COLOR = 0x2b2d31
ACCENT_GREEN = 0x57F287
ACCENT_RED = 0xED4245
ACCENT_GOLD = 0xFEE75C
VOTE_TIME = 45
MIN_PLAYERS = 4
active_matches = {}  # channel_id -> task
active_series = {}   # channel_id -> series_data

# Points system
PTS_IMPOSTOR_WIN = 30
PTS_IMPOSTOR_LOSS = -10
PTS_CORRECT_VOTE = 15
PTS_WRONG_VOTE = 0
PTS_TEAM_WIN = 10
PTS_TEAM_LOSS = 0


# ============================================================
# UI COMPONENTS
# ============================================================

class VoteDropdown(discord.ui.Select):
    def __init__(self, losers):
        options = [
            discord.SelectOption(label=p.display_name, value=str(p.id))
            for p in losers
        ]
        super().__init__(placeholder="Select the suspected player...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id not in [p.id for p in view.all_players]:
            return await interaction.response.send_message(
                "Unauthorized. You are not in this match.", ephemeral=True
            )
        if interaction.user.id in view.voted_users:
            return await interaction.response.send_message(
                "Vote already registered.", ephemeral=True
            )

        view.votes[int(self.values[0])] = view.votes.get(int(self.values[0]), 0) + 1
        view.voted_users.add(interaction.user.id)

        remaining = len(view.all_players) - len(view.voted_users)
        voted_list = ", ".join(
            [p.mention for p in view.all_players if p.id in view.voted_users]
        )

        view.embed.set_field_at(
            0,
            name=f"Votes: {len(view.voted_users)}/{len(view.all_players)}",
            value=f"✅ {voted_list}" if voted_list else "No votes yet",
            inline=False,
        )

        if remaining == 0:
            view.embed.set_footer(text="Voting concluded.")
            view.clear_items()
            await interaction.response.edit_message(embed=view.embed, view=view)
            view.stop()
        else:
            view.embed.set_footer(text=f"Awaiting {remaining} remaining vote(s).")
            await interaction.response.edit_message(embed=view.embed, view=view)
            await interaction.followup.send(f"✅ Vote recorded!", ephemeral=True)


class VoteView(discord.ui.View):
    def __init__(self, targets, all_players, embed, vote_time=VOTE_TIME):
        super().__init__(timeout=vote_time)
        self.all_players = all_players
        self.embed = embed
        self.votes = {}
        self.voted_users = set()
        self.add_item(VoteDropdown(targets))


class MatchOverView(discord.ui.View):
    def __init__(self, host):
        super().__init__(timeout=None)
        self.host = host
        self.match_finished = asyncio.Event()
        self.losing_team = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.host:
            await interaction.response.send_message(
                "Authorization denied. Only the host can declare the match result.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Team 1 Defeated", style=discord.ButtonStyle.danger)
    async def t1_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 1"
        self.clear_items()
        embed = interaction.message.embeds[0]
        embed.description = "⚔️ Match concluded. **Team 1** was defeated."
        embed.color = ACCENT_RED
        await interaction.response.edit_message(embed=embed, view=self)
        self.match_finished.set()

    @discord.ui.button(label="Team 2 Defeated", style=discord.ButtonStyle.danger)
    async def t2_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 2"
        self.clear_items()
        embed = interaction.message.embeds[0]
        embed.description = "⚔️ Match concluded. **Team 2** was defeated."
        embed.color = ACCENT_RED
        await interaction.response.edit_message(embed=embed, view=self)
        self.match_finished.set()


class EntryView(discord.ui.View):
    def __init__(self, timeout, embed, interaction):
        super().__init__(timeout=timeout)
        self.participants = []
        self.embed = embed
        self.interaction = interaction

    @discord.ui.button(label="Join Pool", style=discord.ButtonStyle.success)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            names = "\n".join(
                [f"▸ {p.display_name}" for p in self.participants]
            )
            if self.embed.fields:
                self.embed.set_field_at(0, name=f"Participants ({len(self.participants)})", value=names, inline=False)
            else:
                self.embed.add_field(name=f"Participants ({len(self.participants)})", value=names, inline=False)
            self.embed.set_footer(text=f"{len(self.participants)} registered")
            await self.interaction.edit_original_response(embed=self.embed)
            await interaction.response.send_message("Registration confirmed. ✅", ephemeral=True)
        else:
            await interaction.response.send_message("You are already registered.", ephemeral=True)


# ============================================================
# MATCH LOGIC (Extracted for reusability)
# ============================================================

async def run_single_match(
    interaction: discord.Interaction,
    participants: List[discord.Member],
    host: discord.Member,
    guild_id: Optional[int],
    previous_impostors: List[int] = None,
    round_info: str = ""
) -> dict:
    """
    Run a single match and return results.
    Returns: {
        'impostor': Member,
        'team1': [Members],
        'team2': [Members],
        'losing_team': str,
        'ejected': Member or None,
        'result': str,
        'points': {player_id: points_change}
    }
    """
    if previous_impostors is None:
        previous_impostors = []

    channel = interaction.channel
    points_awarded = {}

    # Assign teams
    shuffled = list(participants)
    random.shuffle(shuffled)
    mid = len(shuffled) // 2
    team1 = shuffled[:mid]
    team2 = shuffled[mid:]

    # Pick impostor (avoid recent impostors if possible)
    available = [p for p in participants if p.id not in previous_impostors]
    if not available:
        available = participants
    impostor = random.choice(available)

    impostor_team_name = "Team 1" if impostor in team1 else "Team 2"
    
    # DM impostor
    try:
        await impostor.send(
            f"🕵️ **You are the Impostor**{round_info}\n"
            f"Assignment: **{impostor_team_name}**\n"
            f"Objective: Ensure that your team is defeated, without being detected."
        )
    except discord.Forbidden:
        await channel.send(f"⚠️ Could not DM {impostor.mention}. Make sure DMs are enabled!")

    # Show teams
    teams_embed = discord.Embed(
        title=f"⚔️ Team Assignments {round_info}",
        description="The host will record the result upon match completion.",
        color=EMBED_COLOR,
    )
    teams_embed.add_field(
        name=f"🔵 Team 1 ({len(team1)})",
        value="\n".join([p.mention for p in team1]),
        inline=True,
    )
    teams_embed.add_field(
        name=f"🔴 Team 2 ({len(team2)})",
        value="\n".join([p.mention for p in team2]),
        inline=True,
    )
    teams_embed.set_footer(text=f"Host: {host.display_name}")

    match_view = MatchOverView(host=host)
    await channel.send(embed=teams_embed, view=match_view)

    # Wait for result
    await match_view.match_finished.wait()

    losers = team1 if match_view.losing_team == "Team 1" else team2
    winners = team2 if match_view.losing_team == "Team 1" else team1

    # Auto-loss check
    if impostor not in losers:
        fail_embed = discord.Embed(
            title="🏆 Impostor Defeated (Auto-Loss)",
            description=(
                f"**Impostor:** {impostor.mention}\n"
                f"**Reason:** The Impostor's team was victorious — mission failed!"
            ),
            color=ACCENT_GREEN,
        )
        await channel.send(embed=fail_embed)

        # Update stats
        for p in participants:
            ensure_player(p, guild_id)
        bump(impostor.id, guild_id, games_played=1, times_impostor=1, impostor_losses=1, games_lost=1)
        bump(impostor.id, guild_id, points=PTS_IMPOSTOR_LOSS)
        points_awarded[impostor.id] = PTS_IMPOSTOR_LOSS

        for p in participants:
            if p.id != impostor.id:
                if p in winners:
                    bump(p.id, guild_id, games_played=1, games_won=1, points=PTS_TEAM_WIN)
                    points_awarded[p.id] = PTS_TEAM_WIN
                else:
                    bump(p.id, guild_id, games_played=1, games_lost=1, points=PTS_TEAM_LOSS)
                    points_awarded[p.id] = PTS_TEAM_LOSS

        return {
            "impostor": impostor,
            "team1": team1,
            "team2": team2,
            "losing_team": match_view.losing_team,
            "ejected": None,
            "result": "impostor_auto_loss",
            "points": points_awarded
        }

    # Voting phase
    current_targets = list(losers)
    round_num = 1
    while True:
        vote_embed = discord.Embed(
            title=f"🗳️ Voting Phase — Round {round_num}",
            description=(
                f"The Impostor is among the defeated team:\n"
                f"{', '.join([p.mention for p in current_targets])}\n\n"
                f"Submit your selection below."
            ),
            color=ACCENT_GOLD,
        )
        vote_embed.add_field(
            name=f"Votes: 0/{len(participants)}",
            value="No votes yet",
            inline=False,
        )
        vote_embed.set_footer(text=f"{VOTE_TIME}s to vote")

        v_view = VoteView(current_targets, participants, vote_embed, VOTE_TIME)
        v_msg = await channel.send(embed=vote_embed, view=v_view)

        # Timer
        async def update_timer():
            remaining = VOTE_TIME
            try:
                while remaining > 0 and not v_view.is_finished():
                    await asyncio.sleep(1)
                    remaining -= 1
                    if remaining > 0 and not v_view.is_finished():
                        vote_embed.set_footer(text=f"⏱️ {remaining}s remaining")
                        await v_msg.edit(embed=vote_embed)
            except (discord.NotFound, discord.HTTPException):
                pass

        timer_task = asyncio.create_task(update_timer())

        try:
            await asyncio.wait_for(v_view.wait(), timeout=VOTE_TIME + 2)
        except asyncio.TimeoutError:
            v_view.stop()

        timer_task.cancel()
        try:
            await timer_task
        except asyncio.CancelledError:
            pass

        v_view.clear_items()
        await v_msg.edit(view=v_view)

        if not v_view.votes:
            await channel.send("⚠️ Zero votes registered. Initiating recount...")
            round_num += 1
            continue

        max_v = max(v_view.votes.values())
        vote_winners = [
            bot.get_user(u_id) or await bot.fetch_user(u_id)
            for u_id, count in v_view.votes.items()
            if count == max_v
        ]

        if len(vote_winners) > 1:
            await channel.send(
                f"⚖️ **Tie** between: {', '.join([w.mention for w in vote_winners])}. "
                f"Tie-breaker vote incoming..."
            )
            current_targets = vote_winners
            round_num += 1
            continue

        ejected = vote_winners[0]
        break

    # Final results
    impostor_won = (ejected.id != impostor.id)

    if impostor_won:
        final_embed = discord.Embed(
            title="🕵️ Impostor Victorious!",
            description="The impostor was NOT discovered. Mission accomplished!",
            color=ACCENT_RED,
        )
    else:
        final_embed = discord.Embed(
            title="✅ Impostor Exposed!",
            description="The group successfully identified the impostor!",
            color=ACCENT_GREEN,
        )

    final_embed.add_field(name="Chosen Player", value=ejected.mention, inline=True)
    final_embed.add_field(name="Impostor", value=impostor.mention, inline=True)
    final_embed.add_field(name="Result", value="Impostor escaped" if impostor_won else "Impostor caught", inline=True)

    breakdown_lines = []
    if impostor_won:
        bump(impostor.id, guild_id, points=PTS_IMPOSTOR_WIN)
        breakdown_lines.append(f"{impostor.mention} **+{PTS_IMPOSTOR_WIN}** (Impostor win)")
        points_awarded[impostor.id] = PTS_IMPOSTOR_WIN
    else:
        bump(impostor.id, guild_id, points=PTS_IMPOSTOR_LOSS)
        breakdown_lines.append(f"{impostor.mention} **{PTS_IMPOSTOR_LOSS}** (Impostor caught)")
        points_awarded[impostor.id] = PTS_IMPOSTOR_LOSS

    for p in participants:
        ensure_player(p, guild_id)
        if p.id == impostor.id:
            if impostor_won:
                bump(p.id, guild_id, games_played=1, times_impostor=1, impostor_wins=1, games_won=1)
            else:
                bump(p.id, guild_id, games_played=1, times_impostor=1, impostor_losses=1, games_lost=1)
        else:
            bump(p.id, guild_id, total_votes=1)
            if p.id == ejected.id and not impostor_won:
                bump(p.id, guild_id, correct_votes=1, points=PTS_CORRECT_VOTE)
                breakdown_lines.append(f"{p.mention} **+{PTS_CORRECT_VOTE}** (Correct vote)")
                points_awarded[p.id] = PTS_CORRECT_VOTE
            elif p.id != ejected.id and impostor_won:
                bump(p.id, guild_id, points=PTS_WRONG_VOTE)
                points_awarded[p.id] = PTS_WRONG_VOTE

    for p in winners:
        if p.id != impostor.id:
            bump(p.id, guild_id, games_played=1, games_won=1, points=PTS_TEAM_WIN)
            breakdown_lines.append(f"{p.mention} **+{PTS_TEAM_WIN}** (Team win)")
            points_awarded[p.id] = PTS_TEAM_WIN
    for p in losers:
        if p.id != impostor.id:
            bump(p.id, guild_id, games_played=1, games_lost=1)
            if p.id not in points_awarded:
                points_awarded[p.id] = 0

    if breakdown_lines:
        final_embed.add_field(
            name="💰 Points Awarded",
            value="\n".join(breakdown_lines),
            inline=False,
        )

    await channel.send(embed=final_embed)

    return {
        "impostor": impostor,
        "team1": team1,
        "team2": team2,
        "losing_team": match_view.losing_team,
        "ejected": ejected,
        "result": "impostor_win" if impostor_won else "impostor_caught",
        "points": points_awarded
    }


# ============================================================
# /start COMMAND (Single Match)
# ============================================================
@bot.tree.command(name="start", description="Start a single Rocket League Impostor match")
async def start(interaction: discord.Interaction, seconds: int = 30):
    guild_id = interaction.guild_id
    if interaction.channel_id in active_matches:
        return await interaction.response.send_message(
            "A match protocol is already active in this channel.", ephemeral=True
        )

    active_matches[interaction.channel_id] = asyncio.current_task()

    try:
        # Entry phase
        entry_embed = discord.Embed(
            title="🎮 Match Initialization",
            description=(
                f"Registration closes <t:{int(time.time() + seconds)}:R>.\n"
                f"Press **Join Pool** to enter. Minimum {MIN_PLAYERS} players (even number required)."
            ),
            color=EMBED_COLOR,
        )
        entry_embed.add_field(name=f"Participants (0)", value="No one yet...", inline=False)

        view = EntryView(seconds, entry_embed, interaction)
        await interaction.response.send_message(embed=entry_embed, view=view)

        await asyncio.sleep(seconds)

        view.stop()
        view.clear_items()
        entry_embed.description = "🔒 Registration closed."
        await interaction.edit_original_response(embed=entry_embed, view=view)

        n = len(view.participants)
        if n < MIN_PLAYERS or n % 2 != 0:
            fail = discord.Embed(
                title="❌ Match Aborted",
                description=f"Need an even number of players (min {MIN_PLAYERS}). Got **{n}**.",
                color=ACCENT_RED,
            )
            return await interaction.channel.send(embed=fail)

        # Run match
        result = await run_single_match(
            interaction, view.participants, interaction.user, guild_id
        )

        # Save to history
        add_history({
            "timestamp": datetime.now().isoformat(),
            "impostor": result["impostor"].display_name,
            "impostor_id": result["impostor"].id,
            "result": result["result"],
            "losing_team": result["losing_team"],
            "ejected": result["ejected"].display_name if result["ejected"] else None,
            "participants": [p.display_name for p in view.participants],
        }, guild_id)

        # Send recap
        await _send_recap(
            interaction.channel, result["team1"], result["team2"],
            result["impostor"], result["losing_team"], result["ejected"],
            result["result"]
        )

        logger.info(f"Match completed in guild {guild_id}: {result['result']}")

    except asyncio.CancelledError:
        cancel_embed = discord.Embed(
            title="🚫 Match Canceled",
            description="The match was forcefully canceled by an admin.",
            color=ACCENT_RED,
        )
        await interaction.channel.send(embed=cancel_embed)
        logger.info(f"Match canceled in guild {guild_id}")
        raise

    finally:
        if interaction.channel_id in active_matches:
            del active_matches[interaction.channel_id]


async def _send_recap(channel, team1, team2, impostor, losing_team, ejected, result):
    recap = discord.Embed(
        title="📋 Match Recap",
        color=EMBED_COLOR,
    )
    recap.add_field(
        name="🔵 Team 1",
        value="\n".join([f"{'🕵️ ' if p.id == impostor.id else '▸ '}{p.display_name}" for p in team1]),
        inline=True,
    )
    recap.add_field(
        name="🔴 Team 2",
        value="\n".join([f"{'🕵️ ' if p.id == impostor.id else '▸ '}{p.display_name}" for p in team2]),
        inline=True,
    )
    recap.add_field(name="Defeated Team", value=losing_team, inline=True)
    recap.add_field(name="Impostor", value=impostor.mention, inline=True)
    if ejected:
        recap.add_field(name="Ejected", value=ejected.mention, inline=True)
        recap.add_field(
            name="Outcome",
            value="Impostor escaped detection" if result == "impostor_win" else "Impostor correctly identified",
            inline=False,
        )
    else:
        recap.add_field(name="Outcome", value="Impostor auto-loss (team won)", inline=True)

    await channel.send(embed=recap)


# ============================================================
# /series COMMAND (Multiple Rounds)
# ============================================================
@bot.tree.command(name="series", description="Start a multi-round series (Bo3, Bo5, etc.)")
async def series(interaction: discord.Interaction, rounds: int = 3, seconds: int = 30):
    guild_id = interaction.guild_id
    
    if rounds not in [3, 5, 7]:
        return await interaction.response.send_message(
            "Series must be Best of 3, 5, or 7 rounds.", ephemeral=True
        )

    if interaction.channel_id in active_matches:
        return await interaction.response.send_message(
            "A match or series is already active in this channel.", ephemeral=True
        )

    active_matches[interaction.channel_id] = asyncio.current_task()
    series_data = {
        "rounds": rounds,
        "current_round": 0,
        "scores": {},
        "impostor_history": [],
        "participants": [],
        "results": []
    }
    active_series[interaction.channel_id] = series_data

    try:
        # Entry phase
        entry_embed = discord.Embed(
            title=f"🏆 Series Initialization — Best of {rounds}",
            description=(
                f"Registration closes <t:{int(time.time() + seconds)}:R>.\n"
                f"Press **Join Pool** to enter. Minimum {MIN_PLAYERS} players (even number required).\n"
                f"Points will accumulate across all {rounds} rounds!"
            ),
            color=ACCENT_GOLD,
        )
        entry_embed.add_field(name=f"Participants (0)", value="No one yet...", inline=False)

        view = EntryView(seconds, entry_embed, interaction)
        await interaction.response.send_message(embed=entry_embed, view=view)

        await asyncio.sleep(seconds)

        view.stop()
        view.clear_items()
        entry_embed.description = "🔒 Registration closed."
        await interaction.edit_original_response(embed=entry_embed, view=view)

        n = len(view.participants)
        if n < MIN_PLAYERS or n % 2 != 0:
            fail = discord.Embed(
                title="❌ Series Aborted",
                description=f"Need an even number of players (min {MIN_PLAYERS}). Got **{n}**.",
                color=ACCENT_RED,
            )
            return await interaction.channel.send(embed=fail)

        participants = list(view.participants)
        series_data["participants"] = [p.id for p in participants]

        # Initialize scores
        for p in participants:
            series_data["scores"][p.id] = 0
            ensure_player(p, guild_id)

        # Series announcement
        series_announce = discord.Embed(
            title=f"🏆 Best of {rounds} Series Starting!",
            description=f"**{len(participants)} players** will compete across **{rounds} rounds**.\nPoints accumulate — may the best player win!",
            color=ACCENT_GOLD,
        )
        series_announce.add_field(
            name="Participants",
            value=", ".join([p.mention for p in participants]),
            inline=False
        )
        await interaction.channel.send(embed=series_announce)
        await asyncio.sleep(3)

        # Run rounds
        for round_num in range(1, rounds + 1):
            round_info = f"(Round {round_num}/{rounds})"
            
            # Round announcement
            round_embed = discord.Embed(
                title=f"🎮 Round {round_num} of {rounds}",
                description="Starting next match...",
                color=EMBED_COLOR,
            )
            await interaction.channel.send(embed=round_embed)
            await asyncio.sleep(2)

            # Run match
            result = await run_single_match(
                interaction, participants, interaction.user, guild_id,
                previous_impostors=series_data["impostor_history"],
                round_info=round_info
            )

            # Track impostor
            series_data["impostor_history"].append(result["impostor"].id)
            series_data["current_round"] = round_num

            # Update series scores
            for pid, points in result["points"].items():
                series_data["scores"][pid] = series_data["scores"].get(pid, 0) + points

            # Save round to history
            add_history({
                "timestamp": datetime.now().isoformat(),
                "impostor": result["impostor"].display_name,
                "impostor_id": result["impostor"].id,
                "result": result["result"],
                "losing_team": result["losing_team"],
                "ejected": result["ejected"].display_name if result["ejected"] else None,
                "participants": [p.display_name for p in participants],
                "series_round": round_num,
                "series_total": rounds
            }, guild_id)

            series_data["results"].append(result)

            # Show round recap
            await _send_recap(
                interaction.channel, result["team1"], result["team2"],
                result["impostor"], result["losing_team"], result["ejected"],
                result["result"]
            )

            # Show current standings
            if round_num < rounds:
                standings = sorted(
                    [(p, series_data["scores"][p.id]) for p in participants],
                    key=lambda x: x[1],
                    reverse=True
                )
                standings_text = "\n".join([
                    f"**#{i+1}** {p.mention}: {pts} pts"
                    for i, (p, pts) in enumerate(standings[:5])
                ])
                
                standings_embed = discord.Embed(
                    title=f"📊 Standings After Round {round_num}",
                    description=standings_text,
                    color=ACCENT_GOLD,
                )
                await interaction.channel.send(embed=standings_embed)
                await asyncio.sleep(5)

        # Series complete - show final results
        final_standings = sorted(
            [(p, series_data["scores"][p.id]) for p in participants],
            key=lambda x: x[1],
            reverse=True
        )

        medals = ["🥇", "🥈", "🥉"]
        final_text = []
        for i, (p, pts) in enumerate(final_standings):
            medal = medals[i] if i < 3 else f"**#{i+1}**"
            final_text.append(f"{medal} {p.mention}: **{pts} pts**")

        winner = final_standings[0][0]
        winner_embed = discord.Embed(
            title=f"🏆 Series Champion: {winner.display_name}!",
            description="\n".join(final_text),
            color=ACCENT_GOLD,
        )
        winner_embed.set_thumbnail(url=winner.display_avatar.url)
        await interaction.channel.send(embed=winner_embed)

        # Save series to history
        add_series({
            "timestamp": datetime.now().isoformat(),
            "rounds": rounds,
            "winner": winner.display_name,
            "winner_id": winner.id,
            "final_scores": {str(p.id): series_data["scores"][p.id] for p in participants},
            "participants": [p.display_name for p in participants],
            "impostors": [
                bot.get_user(pid).display_name if bot.get_user(pid) else str(pid)
                for pid in series_data["impostor_history"]
            ]
        }, guild_id)

        logger.info(f"Series completed in guild {guild_id}: {winner.display_name} won")

    except asyncio.CancelledError:
        cancel_embed = discord.Embed(
            title="🚫 Series Canceled",
            description="The series was forcefully canceled by an admin.",
            color=ACCENT_RED,
        )
        await interaction.channel.send(embed=cancel_embed)
        logger.info(f"Series canceled in guild {guild_id}")
        raise

    finally:
        if interaction.channel_id in active_matches:
            del active_matches[interaction.channel_id]
        if interaction.channel_id in active_series:
            del active_series[interaction.channel_id]


# ============================================================
# /cancel COMMAND
# ============================================================
@bot.tree.command(name="cancel", description="Admin override: cancel the active match/series.")
@app_commands.default_permissions(administrator=True)
async def cancel(interaction: discord.Interaction):
    if interaction.channel_id in active_matches:
        active_matches[interaction.channel_id].cancel()
        del active_matches[interaction.channel_id]
        if interaction.channel_id in active_series:
            del active_series[interaction.channel_id]
        await interaction.response.send_message("🚫 Termination signal dispatched.", ephemeral=True)
    else:
        await interaction.response.send_message("No active match or series in this channel.", ephemeral=True)


# ============================================================
# /stats COMMAND
# ============================================================
@bot.tree.command(name="stats", description="View a player's Impostor stats")
async def stats(interaction: discord.Interaction, player: discord.Member = None):
    guild_id = interaction.guild_id
    target = player or interaction.user
    data = get_player(target.id, guild_id)

    if not data:
        return await interaction.response.send_message(
            f"📭 No stats found for **{target.display_name}**. Play a match first!",
            ephemeral=True,
        )

    gp = data["games_played"] or 1
    win_rate = (data["games_won"] / gp) * 100
    imp_rate = 0
    if data["times_impostor"] > 0:
        imp_rate = (data["impostor_wins"] / data["times_impostor"]) * 100
    vote_acc = 0
    if data["total_votes"] > 0:
        vote_acc = (data["correct_votes"] / data["total_votes"]) * 100

    embed = discord.Embed(
        title=f"📊 {data['name']}'s Stats",
        color=EMBED_COLOR,
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(name="🎮 Games Played", value=data["games_played"], inline=True)
    embed.add_field(name="🏆 Wins", value=data["games_won"], inline=True)
    embed.add_field(name="💀 Losses", value=data["games_lost"], inline=True)

    embed.add_field(name="📈 Win Rate", value=f"{win_rate:.0f}%", inline=True)
    embed.add_field(name="💰 Points", value=data["points"], inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(name="🕵️ Times Impostor", value=data["times_impostor"], inline=True)
    embed.add_field(name="✅ Impostor Wins", value=data["impostor_wins"], inline=True)
    embed.add_field(name="❌ Impostor Losses", value=data["impostor_losses"], inline=True)

    embed.add_field(name="🎯 Impostor Win Rate", value=f"{imp_rate:.0f}%", inline=True)
    embed.add_field(name="🗳️ Correct Votes", value=f"{data['correct_votes']}/{data['total_votes']}", inline=True)
    embed.add_field(name="📊 Vote Accuracy", value=f"{vote_acc:.0f}%", inline=True)

    await interaction.response.send_message(embed=embed)


# ============================================================
# /leaderboard COMMAND
# ============================================================
@bot.tree.command(name="leaderboard", description="View the server points ranking")
async def leaderboard(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    data = load_data(guild_id)
    players = data.get("players", {})

    if not players:
        return await interaction.response.send_message(
            "📭 No stats recorded yet. Play some matches first!", ephemeral=True
        )

    ranked = sorted(players.items(), key=lambda x: x[1]["points"], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (pid, p) in enumerate(ranked[:10]):
        medal = medals[i] if i < 3 else f"**#{i + 1}**"
        wr = 0
        if p["games_played"] > 0:
            wr = (p["games_won"] / p["games_played"]) * 100
        lines.append(
            f"{medal} **{p['name']}** — 💰 {p['points']} pts | "
            f"🎮 {p['games_played']} games | 📈 {wr:.0f}% WR"
        )

    embed = discord.Embed(
        title="🏆 Leaderboard",
        description="\n".join(lines),
        color=ACCENT_GOLD,
    )
    embed.set_footer(text=f"Top {min(10, len(ranked))} of {len(ranked)} players")
    await interaction.response.send_message(embed=embed)


# ============================================================
# /history COMMAND
# ============================================================
@bot.tree.command(name="history", description="View recent match history")
async def history(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    data = load_data(guild_id)
    matches = data.get("history", [])

    if not matches:
        return await interaction.response.send_message(
            "📭 No matches recorded yet.", ephemeral=True
        )

    recent = matches[-5:][::-1]

    embed = discord.Embed(
        title="📜 Recent Matches",
        color=EMBED_COLOR,
    )

    for i, m in enumerate(recent):
        ts = datetime.fromisoformat(m["timestamp"]).strftime("%d/%m %H:%M")
        result_emoji = {
            "impostor_win": "🕵️",
            "impostor_caught": "✅",
            "impostor_auto_loss": "🏆",
        }.get(m["result"], "❓")

        result_text = {
            "impostor_win": "Impostor escaped",
            "impostor_caught": "Impostor caught",
            "impostor_auto_loss": "Auto-loss",
        }.get(m["result"], "Unknown")

        ejected = m.get("ejected") or "—"
        players_str = ", ".join(m["participants"])
        
        series_info = ""
        if "series_round" in m:
            series_info = f" [Round {m['series_round']}/{m['series_total']}]"

        embed.add_field(
            name=f"{result_emoji} {ts} — {result_text}{series_info}",
            value=(
                f"🕵️ Impostor: **{m['impostor']}**\n"
                f"🗳️ Ejected: **{ejected}**\n"
                f"👥 {players_str}"
            ),
            inline=False,
        )

    embed.set_footer(text=f"Showing last {len(recent)} of {len(matches)} matches")
    await interaction.response.send_message(embed=embed)


# ============================================================
# /series_history COMMAND
# ============================================================
@bot.tree.command(name="series_history", description="View recent series history")
async def series_history(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    data = load_data(guild_id)
    series_list = data.get("series", [])

    if not series_list:
        return await interaction.response.send_message(
            "📭 No series recorded yet.", ephemeral=True
        )

    recent = series_list[-5:][::-1]

    embed = discord.Embed(
        title="🏆 Recent Series",
        color=ACCENT_GOLD,
    )

    for s in recent:
        ts = datetime.fromisoformat(s["timestamp"]).strftime("%d/%m %H:%M")
        participants_str = ", ".join(s["participants"])
        impostors_str = ", ".join(s["impostors"])
        
        embed.add_field(
            name=f"🏆 {ts} — Bo{s['rounds']} won by {s['winner']}",
            value=(
                f"👥 Players: {participants_str}\n"
                f"🕵️ Impostors: {impostors_str}"
            ),
            inline=False,
        )

    embed.set_footer(text=f"Showing last {len(recent)} of {len(series_list)} series")
    await interaction.response.send_message(embed=embed)


# ============================================================
# /reset COMMAND (admin only)
# ============================================================
@bot.tree.command(name="reset", description="Admin: reset all server stats (irreversible)")
@app_commands.default_permissions(administrator=True)
async def reset_stats(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    save_data({"players": {}, "history": [], "series": []}, guild_id)
    await interaction.response.send_message("🗑️ All server stats and history have been reset.", ephemeral=True)
    logger.warning(f"Stats reset in guild {guild_id} by {interaction.user.display_name}")


# ============================================================
# RUN BOT
# ============================================================
if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN environment variable not set!")
        exit(1)
    
    try:
        bot.run(token)
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}", exc_info=True)
        exit(1)
