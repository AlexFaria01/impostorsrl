import os
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import time

class SecretPickerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Synced slash commands for {self.user}")

    async def on_ready(self):
        activity = discord.Activity(type=discord.ActivityType.watching, name="for the Impostor 🕵️")
        await self.change_presence(status=discord.Status.online, activity=activity)
        print(f'Logged in as {self.user}')

bot = SecretPickerBot()

# --- VOTING LOGIC ---
class VoteDropdown(discord.ui.Select):
    def __init__(self, losers):
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in losers]
        super().__init__(placeholder="Who sabotaged the game?", options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id not in [p.id for p in view.all_players]:
            return await interaction.response.send_message("You aren't in this game!", ephemeral=True)
        if interaction.user.id in view.voted_users:
            return await interaction.response.send_message("Already voted!", ephemeral=True)

        view.votes[int(self.values[0])] = view.votes.get(int(self.values[0]), 0) + 1
        view.voted_users.add(interaction.user.id)
        
        remaining = len(view.all_players) - len(view.voted_users)
        view.embed.set_footer(text=f"Waiting for {remaining} more votes...")
        await interaction.response.edit_message(embed=view.embed, view=view)
        if len(view.voted_users) == len(view.all_players):
            view.stop()

class VoteView(discord.ui.View):
    def __init__(self, targets, all_players, embed):
        super().__init__(timeout=45)
        self.all_players = all_players
        self.embed = embed
        self.votes = {}
        self.voted_users = set()
        self.add_item(VoteDropdown(targets))

# --- MATCH CONTROL (UPDATED WITH HOST LOCK) ---
class MatchOverView(discord.ui.View):
    def __init__(self, host):
        super().__init__(timeout=None)
        self.host = host # Store the person who started the game
        self.match_finished = asyncio.Event()
        self.losing_team = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # If the person clicking isn't the host, stop them
        if interaction.user != self.host:
            await interaction.response.send_message("Only the person who started the game can declare the losers!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Team 1 Lost", style=discord.ButtonStyle.danger)
    async def t1_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 1"
        self.match_finished.set()
        await interaction.response.send_message("Team 1 marked as Losers.", ephemeral=True)

    @discord.ui.button(label="Team 2 Lost", style=discord.ButtonStyle.danger)
    async def t2_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 2"
        self.match_finished.set()
        await interaction.response.send_message("Team 2 marked as Losers.", ephemeral=True)

# --- ENTRY VIEW ---
class EntryView(discord.ui.View):
    def __init__(self, timeout, embed, interaction):
        super().__init__(timeout=timeout)
        self.participants = []
        self.embed = embed
        self.interaction = interaction

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.success, emoji="⚽")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            self.embed.set_footer(text=f"Total: {len(self.participants)}")
            await self.interaction.edit_original_response(embed=self.embed)
            await interaction.response.send_message("You're in!", ephemeral=True)

@bot.tree.command(name="start", description="Start Rocket League Impostor Game")
async def start(interaction: discord.Interaction, seconds: int):
    # 1. ENTRY PHASE
    entry_embed = discord.Embed(title="🏎️ Rocket League Impostor", description=f"Join up! Closing <t:{int(time.time()+seconds)}:R>", color=0x3498db)
    view = EntryView(seconds, entry_embed, interaction)
    await interaction.response.send_message(embed=entry_embed, view=view)
    await asyncio.sleep(seconds)

    if len(view.participants) < 2 or len(view.participants) % 2 != 0:
        return await interaction.channel.send("❌ Error: Game requires an even number of players.")

    # 2. ASSIGN TEAMS & IMPOSTOR
    random.shuffle(view.participants)
    mid = len(view.participants) // 2
    team1 = view.participants[:mid]
    team2 = view.participants[mid:]
    impostor = random.choice(view.participants)
    
    impostor_team_name = "Team 1" if impostor in team1 else "Team 2"
    await impostor.send(f"🤫 **YOU ARE THE IMPOSTOR.**\nYou are on **{impostor_team_name}**. Make them lose!")

    teams_embed = discord.Embed(title="🎮 Teams Assigned", description="Play your match and the host will report who lost!", color=0x2ecc71)
    teams_embed.add_field(name="👥 Team 1", value="\n".join([p.mention for p in team1]))
    teams_embed.add_field(name="👥 Team 2", value="\n".join([p.mention for p in team2]))
    
    # Pass the person who ran /start as the host
    match_view = MatchOverView(host=interaction.user)
    await interaction.channel.send(embed=teams_embed, view=match_view)

    # 3. WAIT FOR MATCH RESULT
    await match_view.match_finished.wait()
    
    # 4. AUTO-LOSS CHECK
    losers = team1 if match_view.losing_team == "Team 1" else team2
    if impostor not in losers:
        fail_embed = discord.Embed(
            title="🚩 IMPOSTOR FAILED",
            description=f"The Impostor ({impostor.mention}) was on the winning team!\n\nThey accidentally helped their team win.",
            color=0x3498db
        )
        return await interaction.channel.send(embed=fail_embed)

    # 5. VOTING PHASE
    current_targets = losers
    while True:
        vote_embed = discord.Embed(title="🗳️ Sabotage Found! Voting Open", color=0xf1c40f)
        v_view = VoteView(current_targets, view.participants, vote_embed)
        v_msg = await interaction.channel.send(embed=vote_embed, view=v_view)
        await v_view.wait()
        
        if not v_view.votes:
            await interaction.channel.send("No votes? Re-voting...")
            continue

        max_v = max(v_view.votes.values())
        vote_winners = [bot.get_user(u_id) for u_id, count in v_view.votes.items() if count == max_v]

        if len(vote_winners) > 1:
            await interaction.channel.send(f"⚖️ Tie between: {', '.join([w.display_name for w in vote_winners])}. Re-voting...")
            current_targets = vote_winners
            continue
        
        ejected = vote_winners[0]
        break

    # 6. FINAL RESULTS
    impostor_won = (ejected.id != impostor.id)
    result_title = "🚩 IMPOSTOR WINS" if impostor_won else "✅ CREWMATES WIN"
    final_embed = discord.Embed(title=result_title, color=0x2f3136)
    final_embed.add_field(name="The Impostor was:", value=impostor.mention)
    final_embed.add_field(name="The Group Ejected:", value=ejected.mention)
    await interaction.channel.send(embed=final_embed)

bot.run(os.environ.get('DISCORD_TOKEN'))