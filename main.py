import os
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import time
import json
from pathlib import Path
from datetime import datetime

# ============================================================
# DATA PERSISTENCE (JSON)
# ============================================================
DATA_FILE = Path("data/stats.json")

def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"players": {}, "history": []}

def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_player(pid):
    data = load_data()
    return data["players"].get(str(pid))

def ensure_player(player):
    """Make sure a player exists in the DB with default stats."""
    data = load_data()
    pid = str(player.id)
    if pid not in data["players"]:
        data["players"][pid] = _new_player(player.display_name)
    data["players"][pid]["name"] = player.display_name
    save_data(data)

def _new_player(name):
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

def bump(pid, **kwargs):
    """Increment numeric fields for a player."""
    data = load_data()
    pid = str(pid)
    if pid not in data["players"]:
        return
    for k, v in kwargs.items():
        if k in data["players"][pid]:
            data["players"][pid][k] += v
    save_data(data)

def add_history(entry):
    data = load_data()
    data["history"].append(entry)
    data["history"] = data["history"][-50:]  # keep last 50
    save_data(data)


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
        print(f"System: Synced slash commands for {self.user}")

    async def on_ready(self):
        activity = discord.Activity(type=discord.ActivityType.watching, name="Match Processing")
        await self.change_presence(status=discord.Status.online, activity=activity)
        print(f'System: Logged in as {self.user}')

bot = SecretPickerBot()

# --- CONSTANTS & GLOBALS ---
EMBED_COLOR = 0x2b2d31
ACCENT_GREEN = 0x57F287
ACCENT_RED = 0xED4245
ACCENT_GOLD = 0xFEE75C
VOTE_TIME = 45        # seconds for voting
MIN_PLAYERS = 4       # minimum for a proper match (2v2)
active_matches = {}

# Points system
PTS_IMPOSTOR_WIN = 30      # impostor wins (not caught)
PTS_IMPOSTOR_LOSS = -10    # impostor caught
PTS_CORRECT_VOTE = 15      # player voted for the impostor
PTS_WRONG_VOTE = 0         # voted wrong
PTS_TEAM_WIN = 10          # non-impostor on winning team
PTS_TEAM_LOSS = 0          # non-impostor on losing team (non-impostor)


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

        # Register vote
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

            # Send private confirmation showing nothing sensitive
            await interaction.followup.send(
                f"✅ Vote recorded!", ephemeral=True
            )


class VoteView(discord.ui.View):
    def __init__(self, targets, all_players, embed):
        super().__init__(timeout=VOTE_TIME)
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
            # Update or add a participants field
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
# /start COMMAND
# ============================================================
@bot.tree.command(name="start", description="Start a Rocket League Impostor match")
async def start(interaction: discord.Interaction, seconds: int = 30):
    if interaction.channel_id in active_matches:
        return await interaction.response.send_message(
            "A match protocol is already active in this channel.", ephemeral=True
        )

    active_matches[interaction.channel_id] = asyncio.current_task()

    try:
        # ---- 1. ENTRY PHASE ----
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

        # ---- 2. ASSIGN TEAMS & IMPOSTOR ----
        participants = list(view.participants)
        random.shuffle(participants)
        mid = len(participants) // 2
        team1 = participants[:mid]
        team2 = participants[mid:]
        impostor = random.choice(participants)

        impostor_team_name = "Team 1" if impostor in team1 else "Team 2"
        try:
            await impostor.send(
                f"🕵️ **You are the Impostor**\n"
                f"Assignment: **{impostor_team_name}**\n"
                f"Objective: Ensure that your team is defeated, without being detected."
            )
        except discord.Forbidden:
            await interaction.channel.send(
                f"⚠️ Could not DM {impostor.mention}. Make sure DMs are enabled!"
            )

        teams_embed = discord.Embed(
            title="⚔️ Team Assignments",
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
        teams_embed.set_footer(text=f"Host: {interaction.user.display_name}")

        match_view = MatchOverView(host=interaction.user)
        await interaction.channel.send(embed=teams_embed, view=match_view)

        # ---- 3. WAIT FOR MATCH RESULT ----
        await match_view.match_finished.wait()

        # ---- 4. AUTO-LOSS CHECK ----
        losers = team1 if match_view.losing_team == "Team 1" else team2
        winners = team2 if match_view.losing_team == "Team 1" else team1

        if impostor not in losers:
            # Impostor's team won -> impostor fails automatically
            fail_embed = discord.Embed(
                title="🏆 Impostor Defeated (Auto-Loss)",
                description=(
                    f"**Impostor:** {impostor.mention}\n"
                    f"**Reason:** The Impostor's team was victorious — mission failed!\n\n"
                    f"Your team must lose for the impostor to succeed."
                ),
                color=ACCENT_GREEN,
            )
            await interaction.channel.send(embed=fail_embed)

            # Update stats
            for p in participants:
                ensure_player(p)
            bump(impostor.id, games_played=1, times_impostor=1, impostor_losses=1, games_lost=1)
            bump(impostor.id, points=PTS_IMPOSTOR_LOSS)
            for p in participants:
                if p.id != impostor.id:
                    if p in winners:
                        bump(p.id, games_played=1, games_won=1, points=PTS_TEAM_WIN)
                    else:
                        bump(p.id, games_played=1, games_lost=1, points=PTS_TEAM_LOSS)

            add_history({
                "timestamp": datetime.now().isoformat(),
                "impostor": impostor.display_name,
                "impostor_id": impostor.id,
                "result": "impostor_auto_loss",
                "losing_team": match_view.losing_team,
                "ejected": None,
                "participants": [p.display_name for p in participants],
            })

            await _send_recap(interaction.channel, team1, team2, impostor, match_view.losing_team, None, "auto_loss")
            return

        # ---- 5. VOTING PHASE ----
        current_targets = list(losers)
        round_num = 1
        while True:
            voted_list = "No votes yet"
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
                value=voted_list,
                inline=False,
            )
            vote_embed.set_footer(text=f"{VOTE_TIME}s to vote")

            v_view = VoteView(current_targets, participants, vote_embed)
            v_msg = await interaction.channel.send(embed=vote_embed, view=v_view)

            # Timer update task
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

            # Cleanup dropdown
            v_view.clear_items()
            await v_msg.edit(view=v_view)

            if not v_view.votes:
                notice = await interaction.channel.send("⚠️ Zero votes registered. Initiating recount...")
                round_num += 1
                continue

            max_v = max(v_view.votes.values())
            vote_winners = [
                bot.get_user(u_id) or await bot.fetch_user(u_id)
                for u_id, count in v_view.votes.items()
                if count == max_v
            ]

            if len(vote_winners) > 1:
                await interaction.channel.send(
                    f"⚖️ **Tie** between: {', '.join([w.mention for w in vote_winners])}. "
                    f"Tie-breaker vote incoming..."
                )
                current_targets = vote_winners
                round_num += 1
                continue

            ejected = vote_winners[0]
            break

        # ---- 6. FINAL RESULTS ----
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

        # Points breakdown
        breakdown_lines = []
        if impostor_won:
            bump(impostor.id, points=PTS_IMPOSTOR_WIN)
            breakdown_lines.append(f"{impostor.mention} **+{PTS_IMPOSTOR_WIN}** (Impostor win)")
        else:
            bump(impostor.id, points=PTS_IMPOSTOR_LOSS)
            breakdown_lines.append(f"{impostor.mention} **{PTS_IMPOSTOR_LOSS}** (Impostor caught)")

        for p in participants:
            ensure_player(p)
            if p.id == impostor.id:
                if impostor_won:
                    bump(p.id, games_played=1, times_impostor=1, impostor_wins=1, games_won=1)
                else:
                    bump(p.id, games_played=1, times_impostor=1, impostor_losses=1, games_lost=1)
            else:
                bump(p.id, total_votes=1)
                if p.id == ejected.id and not impostor_won:
                    bump(p.id, correct_votes=1, points=PTS_CORRECT_VOTE)
                    breakdown_lines.append(f"{p.mention} **+{PTS_CORRECT_VOTE}** (Correct vote)")
                elif p.id != ejected.id and impostor_won:
                    # voted wrong (or didn't vote for impostor)
                    bump(p.id, points=PTS_WRONG_VOTE)

        # Give team-win points to non-impostors on winning team
        for p in winners:
            if p.id != impostor.id:
                bump(p.id, games_played=1, games_won=1, points=PTS_TEAM_WIN)
                breakdown_lines.append(f"{p.mention} **+{PTS_TEAM_WIN}** (Team win)")
        for p in losers:
            if p.id != impostor.id:
                bump(p.id, games_played=1, games_lost=1)

        if breakdown_lines:
            final_embed.add_field(
                name="💰 Points Awarded",
                value="\n".join(breakdown_lines),
                inline=False,
            )

        await interaction.channel.send(embed=final_embed)

        add_history({
            "timestamp": datetime.now().isoformat(),
            "impostor": impostor.display_name,
            "impostor_id": impostor.id,
            "result": "impostor_win" if impostor_won else "impostor_caught",
            "losing_team": match_view.losing_team,
            "ejected": ejected.display_name if ejected else None,
            "participants": [p.display_name for p in participants],
        })

        await _send_recap(
            interaction.channel, team1, team2, impostor,
            match_view.losing_team, ejected,
            "impostor_win" if impostor_won else "impostor_caught"
        )

    except asyncio.CancelledError:
        cancel_embed = discord.Embed(
            title="🚫 Match Canceled",
            description="The match was forcefully canceled by an admin.",
            color=ACCENT_RED,
        )
        await interaction.channel.send(embed=cancel_embed)
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
# /cancel COMMAND
# ============================================================
@bot.tree.command(name="cancel", description="Admin override: cancel the active match.")
@app_commands.default_permissions(administrator=True)
async def cancel(interaction: discord.Interaction):
    if interaction.channel_id in active_matches:
        active_matches[interaction.channel_id].cancel()
        del active_matches[interaction.channel_id]
        await interaction.response.send_message("🚫 Termination signal dispatched.", ephemeral=True)
    else:
        await interaction.response.send_message("No active match in this channel.", ephemeral=True)


# ============================================================
# /stats COMMAND
# ============================================================
@bot.tree.command(name="stats", description="View a player's Impostor stats")
async def stats(interaction: discord.Interaction, player: discord.Member = None):
    target = player or interaction.user
    data = get_player(target.id)

    if not data:
        return await interaction.response.send_message(
            f"📭 No stats found for **{target.display_name}**. Play a match first!",
            ephemeral=True,
        )

    gp = data["games_played"] or 1  # avoid /0
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
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer

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
@bot.tree.command(name="leaderboard", description="View the global points ranking")
async def leaderboard(interaction: discord.Interaction):
    data = load_data()
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
    data = load_data()
    matches = data.get("history", [])

    if not matches:
        return await interaction.response.send_message(
            "📭 No matches recorded yet.", ephemeral=True
        )

    # Show last 5
    recent = matches[-5:][::-1]  # newest first

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

        embed.add_field(
            name=f"{result_emoji} {ts} — {result_text}",
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
# /reset COMMAND (admin only)
# ============================================================
@bot.tree.command(name="reset", description="Admin: reset all stats (irreversible)")
@app_commands.default_permissions(administrator=True)
async def reset_stats(interaction: discord.Interaction):
    save_data({"players": {}, "history": []})
    await interaction.response.send_message("🗑️ All stats and history have been reset.", ephemeral=True)


bot.run(os.environ.get("DISCORD_TOKEN"))
