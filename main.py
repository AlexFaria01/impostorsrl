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
        activity = discord.Activity(type=discord.ActivityType.watching, name="/start")
        await self.change_presence(status=discord.Status.online, activity=activity)
        print(f'Logged in as {self.user}')

bot = SecretPickerBot()

# Updated View to handle live updates
class SecretGiveawayView(discord.ui.View):
    def __init__(self, timeout, embed, interaction):
        super().__init__(timeout=timeout)
        self.participants = []
        self.initial_embed = embed
        self.initial_interaction = interaction

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.danger, emoji="🥷")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            
            # --- LIVE UPDATE LOGIC ---
            # We update the footer of the embed to show the current pool size
            self.initial_embed.set_footer(text=f"Total participants in pool: {len(self.participants)}")
            await self.initial_interaction.edit_original_response(embed=self.initial_embed)
            
            await interaction.response.send_message(f"You've entered the pool!", ephemeral=True)
        else:
            await interaction.response.send_message("You're already in!", ephemeral=True)

@bot.tree.command(name="start", description="Starts the Impostor selection")
@app_commands.describe(seconds="How many seconds the entry period should last")
async def start(interaction: discord.Interaction, seconds: int):
    end_time = int(time.time() + seconds)
    
    # Create the embed
    embed = discord.Embed(
        title="Impostor Selection",
        description=(
            f"An Impostor will be chosen from the pool.\n\n"
            f"**Ends:** <t:{end_time}:R>\n"
            f"**Hosted by:** {interaction.user.mention}"
        ),
        color=0xe74c3c # Impostor Red
    )
    # Set the starting footer
    embed.set_footer(text="Total participants in pool: 0")
    
    # Pass the embed and interaction into the view so it can edit the message
    view = SecretGiveawayView(timeout=seconds, embed=embed, interaction=interaction)
    
    await interaction.response.send_message(embed=embed, view=view)
    
    await asyncio.sleep(seconds)
    
    view.stop()
    for item in view.children:
        item.disabled = True
    
    if view.participants:
        winner = random.choice(view.participants)
        
        # Final update to show the period is over
        end_embed = discord.Embed(
            title="An Impostor has been selected",
            description=f"The period has ended. **{len(view.participants)}** people joined.\n\nThe Impostor has been notified!",
            color=0x2f3136
        )
        await interaction.edit_original_response(embed=end_embed, view=view)

        try:
            await winner.send("You are the Impostor!")
        except discord.Forbidden:
            await interaction.channel.send(f"I couldn't DM the Impostor ({winner.mention})!")
    else:
        # Message if nobody joins
        cancel_embed = discord.Embed(title="Cancelled", description="Nobody joined the pool.", color=0x2f3136)
        await interaction.edit_original_response(embed=cancel_embed, view=view)

bot.run(os.environ.get('DISCORD_TOKEN'))