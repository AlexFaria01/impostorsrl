# 🕵️ ImpostorSRL

A Discord bot for playing the **Impostor** mini-game in Rocket League. One player is secretly assigned to sabotage their own team — can the group figure out who it is?

## 🎮 How to Play

1. Use `/start <seconds>` to open registration
2. Players press **Join Pool** to enter
3. Teams are randomly assigned (2v2, 3v3, etc.)
4. One player secretly receives a DM: **they are the Impostor**
5. Play the Rocket League match!
6. The host declares which team lost
7. If the Impostor was on the losing team → **Voting Phase** begins
8. Players vote on who they think the Impostor is
9. Results revealed with full recap!

## 📋 Commands

| Command | Description |
|---|---|
| `/start <seconds>` | Start a new match with registration timer |
| `/cancel` | Admin: cancel the active match |
| `/stats [player]` | View a player's detailed stats |
| `/leaderboard` | View the global points ranking |
| `/history` | View recent match history |
| `/reset` | Admin: reset all stats |

## 💰 Points System

| Action | Points |
|---|---|
| Impostor wins (not caught) | +30 |
| Impostor caught | -10 |
| Correct vote (identified impostor) | +15 |
| Non-impostor team win | +10 |

## ⚙️ Setup

1. Create a Discord bot at [discord.com/developers](https://discord.com/developers/applications)
2. Enable **Server Members Intent** and **Message Content Intent**
3. Set the `DISCORD_TOKEN` environment variable
4. Install dependencies: `pip install -r requirements.txt`
5. Run: `python main.py`

## 📁 Data

Stats and history are persisted in `data/stats.json` (auto-created).
