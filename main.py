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
        print(f"System: Synced slash commands for {self.user}")

    async def on_ready(self):
        activity = discord.Activity(type=discord.ActivityType.watching, name="Match Processing")
        await self.change_presence(status=discord.Status.online, activity=activity)
        print(f'System: Logged in as {self.user}')

bot = SecretPickerBot()

# --- CONSTANTS & GLOBALS ---
EMBED_COLOR = 0x2b2d31 
active_matches = {}    

# --- VOTING LOGIC ---
class VoteDropdown(discord.ui.Select):
    def __init__(self, losers):
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in losers]
        super().__init__(placeholder="Select the suspected player...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id not in [p.id for p in view.all_players]:
            return await interaction.response.send_message("Unauthorized. You are not in this match.", ephemeral=True)
        if interaction.user.id in view.voted_users:
            return await interaction.response.send_message("Vote already registered.", ephemeral=True)

        view.votes[int(self.values[0])] = view.votes.get(int(self.values[0]), 0) + 1
        view.voted_users.add(interaction.user.id)
        
        remaining = len(view.all_players) - len(view.voted_users)
        
        # If everyone has voted, clean up immediately
        if remaining == 0:
            view.embed.set_footer(text="Voting concluded.")
            view.clear_items() # Removes the dropdown
            await interaction.response.edit_message(embed=view.embed, view=view)
            view.stop()
        else:
            view.embed.set_footer(text=f"Awaiting {remaining} remaining vote(s).")
            await interaction.response.edit_message(embed=view.embed, view=view)

class VoteView(discord.ui.View):
    def __init__(self, targets, all_players, embed):
        super().__init__(timeout=45)
        self.all_players = all_players
        self.embed = embed
        self.votes = {}
        self.voted_users = set()
        self.add_item(VoteDropdown(targets))

# --- MATCH CONTROL ---
class MatchOverView(discord.ui.View):
    def __init__(self, host):
        super().__init__(timeout=None)
        self.host = host 
        self.match_finished = asyncio.Event()
        self.losing_team = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.host:
            await interaction.response.send_message("Authorization denied. Only the host can declare the match result.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Team 1 Defeated", style=discord.ButtonStyle.secondary)
    async def t1_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 1"
        self.clear_items() # Remove the buttons
        
        embed = interaction.message.embeds[0]
        embed.description = "Match concluded. Team 1 was defeated."
        await interaction.response.edit_message(embed=embed, view=self)
        
        self.match_finished.set()

    @discord.ui.button(label="Team 2 Defeated", style=discord.ButtonStyle.secondary)
    async def t2_lose(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.losing_team = "Team 2"
        self.clear_items() # Remove the buttons
        
        embed = interaction.message.embeds[0]
        embed.description = "Match concluded. Team 2 was defeated."
        await interaction.response.edit_message(embed=embed, view=self)
        
        self.match_finished.set()

# --- ENTRY VIEW ---
class EntryView(discord.ui.View):
    def __init__(self, timeout, embed, interaction):
        super().__init__(timeout=timeout)
        self.participants = []
        self.embed = embed
        self.interaction = interaction

    @discord.ui.button(label="Join Pool", style=discord.ButtonStyle.secondary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            self.embed.set_footer(text=f"Current Participants: {len(self.participants)}")
            await self.interaction.edit_original_response(embed=self.embed)
            await interaction.response.send_message("Registration confirmed.", ephemeral=True)


# --- COMMANDS ---

@bot.tree.command(name="start", description="Initialize a professional Rocket League Impostor match")
async def start(interaction: discord.Interaction, seconds: int):
    if interaction.channel_id in active_matches:
        return await interaction.response.send_message("A match protocol is already active in this channel.", ephemeral=True)
    
    active_matches[interaction.channel_id] = asyncio.current_task()

    try:
        # 1. ENTRY PHASE
        entry_embed = discord.Embed(
            title="Match Initialization", 
            description=f"Registration period closes <t:{int(time.time()+seconds)}:R>.", 
            color=EMBED_COLOR
        )
        view = EntryView(seconds, entry_embed, interaction)
        await interaction.response.send_message(embed=entry_embed, view=view)
        
        await asyncio.sleep(seconds)

        view.stop()
        view.clear_items() 
        entry_embed.description = "Registration period closed."
        await interaction.edit_original_response(embed=entry_embed, view=view)

        if len(view.participants) < 2 or len(view.participants) % 2 != 0:
            return await interaction.channel.send("Process terminated. An even number of participants is required.")

        # 2. ASSIGN TEAMS & IMPOSTOR
        random.shuffle(view.participants)
        mid = len(view.participants) // 2
        team1 = view.participants[:mid]
        team2 = view.participants[mid:]
        impostor = random.choice(view.participants)
        
        impostor_team_name = "Team 1" if impostor in team1 else "Team 2"
        await impostor.send(
            "**CLASSIFIED DIRECTIVE**\n"
            f"Role: Impostor\nAssignment: {impostor_team_name}\n"
            "Objective: Ensure the defeat of your assigned team without being detected by the group."
        )

        teams_embed = discord.Embed(
            title="Team Assignments", 
            description="The host will record the result upon match completion.", 
            color=EMBED_COLOR
        )
        teams_embed.add_field(name="Team 1", value="\n".join([p.mention for p in team1]))
        teams_embed.add_field(name="Team 2", value="\n".join([p.mention for p in team2]))
        
        match_view = MatchOverView(host=interaction.user)
        await interaction.channel.send(embed=teams_embed, view=match_view)

        # 3. WAIT FOR MATCH RESULT
        await match_view.match_finished.wait()
        
        # 4. AUTO-LOSS CHECK
        losers = team1 if match_view.losing_team == "Team 1" else team2
        if impostor not in losers:
            fail_embed = discord.Embed(
                title="Impostor Defeated",
                description=f"Impostor: {impostor.mention}\nReason: The Impostor's assigned team was victorious.",
                color=EMBED_COLOR
            )
            return await interaction.channel.send(embed=fail_embed)

        # 5. VOTING PHASE
        current_targets = losers
        while True:
            vote_embed = discord.Embed(
                title="Voting Phase Active", 
                description=f"The Impostor is confirmed to be among the defeated team: {', '.join([p.display_name for p in losers])}.\nSubmit your selection below.",
                color=EMBED_COLOR
            )
            v_view = VoteView(current_targets, view.participants, vote_embed)
            v_msg = await interaction.channel.send(embed=vote_embed, view=v_view)
            
            await v_view.wait()
            
            # --- NEW: Cleanup the dropdown when voting finishes ---
            v_view.clear_items()
            await v_msg.edit(view=v_view)
            
            if not v_view.votes:
                await interaction.channel.send("Zero votes registered. Initiating recount protocol...")
                continue

            max_v = max(v_view.votes.values())
            vote_winners = [bot.get_user(u_id) for u_id, count in v_view.votes.items() if count == max_v]

            if len(vote_winners) > 1:
                await interaction.channel.send(f"Tie detected between: {', '.join([w.display_name for w in vote_winners])}. Initiating tie-breaker phase...")
                current_targets = vote_winners
                continue
            
            ejected = vote_winners[0]
            break

        # 6. FINAL RESULTS
        impostor_won = (ejected.id != impostor.id)
        result_title = "Impostor Victorious" if impostor_won else "Impostor Defeated"
        final_embed = discord.Embed(title=result_title, color=EMBED_COLOR)
        final_embed.add_field(name="Chosen Player:", value=ejected.mention)
        final_embed.add_field(name="Impostor:", value=impostor.mention)
        await interaction.channel.send(embed=final_embed)

    except asyncio.CancelledError:
        cancel_embed = discord.Embed(
            title="Matched Canceled", 
            description="The match was forcefully canceled by an admin.", 
            color=EMBED_COLOR
        )
        await interaction.channel.send(embed=cancel_embed)
        raise

    finally:
        if interaction.channel_id in active_matches:
            del active_matches[interaction.channel_id]


@bot.tree.command(name="cancel", description="Administrator Override: Terminate the active match in this channel.")
@app_commands.default_permissions(administrator=True)
async def cancel(interaction: discord.Interaction):
    if interaction.channel_id in active_matches:
        active_matches[interaction.channel_id].cancel()
        del active_matches[interaction.channel_id]
        await interaction.response.send_message("Termination signal dispatched.", ephemeral=True)
    else:
        await interaction.response.send_message("No active match detected in this channel.", ephemeral=True)


bot.run(os.environ.get('DISCORD_TOKEN'))