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
        super().__init__(placeholder="Vote for the Impostor (Loser Team Only)", options=options)

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
        super().__init__(timeout=30)
        self.all_players = all_players
        self.embed = embed
        self.votes = {}
        self.voted_users = set()
        self.add_item(VoteDropdown(targets))

# --- MATCH CONTROL ---
class MatchOverView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.match_finished = asyncio.Event()
        self.winning_team = None

    @discord.ui.button(label="Match Finished (Blue Won)", style=discord.ButtonStyle.primary)
    async def blue_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.winning_team = "Blue"
        self.match_finished.set()
        await interaction.response.send_message("Blue Team declared winners!", ephemeral=True)

    # FIXED: Changed ButtonStyle.orange to ButtonStyle.secondary (Grey) 
    # since Discord doesn't have an orange button.
    @discord.ui.button(label="Match Finished (Orange Won)", style=discord.ButtonStyle.secondary)
    async def orange_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.winning_team = "Orange"
        self.match_finished.set()
        await interaction.response.send_message("Orange Team declared winners!", ephemeral=True)

# --- ENTRY VIEW ---
class EntryView(discord.ui.View):
    def __init__(self, timeout, embed, interaction):
        super().__init__(timeout=timeout)
        self.participants = []
        self.embed = embed
        self.interaction = interaction

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.danger, emoji="🥷")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            self.embed.set_footer(text=f"Total: {len(self.participants)}")
            await self.interaction.edit_original_response(embed=self.embed)
            await interaction.response.send_message("Joined!", ephemeral=True)

@bot.tree.command(name="start", description="Start Rocket League Impostor Game")
async def start(interaction: discord.Interaction, seconds: int):
    # 1. ENTRY
    entry_embed = discord.Embed(title="🚀 RL Impostor: Entry Phase", description=f"Entry closes <t:{int(time.time()+seconds)}:R>", color=0x3498db)
    view = EntryView(seconds, entry_embed, interaction)
    await interaction.response.send_message(embed=entry_embed, view=view)
    await asyncio.sleep(seconds)

    if len(view.participants) < 2 or len(view.participants) % 2 != 0:
        return await interaction.channel.send("❌ Needs an even number of players (2, 4, 6...).")

    # 2. TEAM SPLIT & IMPOSTOR
    random.shuffle(view.participants)
    mid = len(view.participants) // 2
    blue_team = view.participants[:mid]
    orange_team = view.participants[mid:]
    impostor = random.choice(view.participants)
    
    await impostor.send("🤫 **YOU ARE THE IMPOSTOR.** Your goal: Make your team lose without getting voted out.")

    teams_embed = discord.Embed(title="🎮 Teams Assigned", color=0x2ecc71)
    teams_embed.add_field(name="🔵 Blue Team", value="\n".join([p.mention for p in blue_team]))
    teams_embed.add_field(name="🟠 Orange Team", value="\n".join([p.mention for p in orange_team]))
    
    match_view = MatchOverView()
    await interaction.channel.send(embed=teams_embed, view=match_view)

    # 3. WAIT FOR MATCH
    await match_view.match_finished.wait()
    
    # Identify Loser Team
    losers = orange_team if match_view.winning_team == "Blue" else blue_team
    
    # 4. VOTING LOOP
    current_targets = losers
    while True:
        vote_embed = discord.Embed(title="🗳️ Voting Phase (30s)", description="Only the losing team can be voted!", color=0xf1c40f)
        v_view = VoteView(current_targets, view.participants, vote_embed)
        v_msg = await interaction.channel.send(embed=vote_embed, view=v_view)
        
        await v_view.wait()
        
        if not v_view.votes:
            await interaction.channel.send("No votes cast! The Impostor escapes. Re-voting for security...")
            continue

        max_v = max(v_view.votes.values())
        vote_winners = [bot.get_user(u_id) for u_id, count in v_view.votes.items() if count == max_v]

        if len(vote_winners) > 1:
            await interaction.channel.send(f"⚖️ Tie between: {', '.join([w.display_name for w in vote_winners])}. Re-voting...")
            current_targets = vote_winners
            continue
        
        ejected = vote_winners[0]
        break

    # 5. FINAL RESULTS
    # Impostor wins if they weren't ejected AND they were actually on the losing team
    impostor_won = (ejected.id != impostor.id) and (impostor in losers)
    result_title = "🚩 IMPOSTOR WINS" if impostor_won else "✅ CREWMATES WIN"
    
    final_embed = discord.Embed(title=result_title, color=0x2f3136)
    final_embed.add_field(name="The Impostor was:", value=impostor.mention)
    final_embed.add_field(name="Ejected Member:", value=ejected.mention)
    await interaction.channel.send(embed=final_embed)

bot.run(os.environ.get('DISCORD_TOKEN'))