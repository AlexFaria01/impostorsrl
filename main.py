import os
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import time # Needed for the timestamp

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
        print(f'Logged in as {self.user} (ID: {self.user.id})')

bot = SecretPickerBot()

class SecretGiveawayView(discord.ui.View):
    def __init__(self, timeout):
        super().__init__(timeout=timeout)
        self.participants = []

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.red, emoji="🥷")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            self.participants.append(interaction.user)
            # Ephemeral means only the user who clicked sees this
            await interaction.response.send_message(f"You've entered! There are now {len(self.participants)} people in the pool.", ephemeral=True)
        else:
            await interaction.response.send_message("You're already in!", ephemeral=True)

@bot.tree.command(name="start", description="Starts the Impostor selection")
@app_commands.describe(seconds="How many seconds the entry period should last")
async def start(interaction: discord.Interaction, seconds: int):
    # Calculate the exact time when it ends
    end_time = int(time.time() + seconds)
    
    view = SecretGiveawayView(timeout=seconds)
    
    # <t:timestamp:R> creates the live "in X seconds" countdown
    embed = discord.Embed(
        title="Impostor Selection",
        description=f"Click the button below to enter.\n\n**Ends:** <t:{end_time}:R>\n**Hosted by:** {interaction.user.mention}",
        color=0x5865F2
    )
    
    await interaction.response.send_message(embed=embed, view=view)
    
    # Wait for the timer to finish
    await asyncio.sleep(seconds)
    
    # Disable the button
    view.stop()
    for item in view.children:
        item.disabled = True
    
    # Update the original message to show it's over
    end_embed = discord.Embed(
        title="An Impostor as been selected",
        description="The impostor has been notified!",
        color=0x2f3136
    )
    await interaction.edit_original_response(embed=end_embed, view=view)

    if view.participants:
        winner = random.choice(view.participants)
        try:
            await winner.send(f"**You are the Impostor!**")
        except discord.Forbidden:
            await interaction.channel.send("I couldn't DM the Impostor! They need to open their DMs.")
    else:
        await interaction.channel.send("Nobody joined the selection!")

bot.run(os.environ.get('DISCORD_TOKEN'))