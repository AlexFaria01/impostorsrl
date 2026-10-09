# 🕵️ ImpostorSRL

A Discord bot for playing the **Impostor** mini-game in Rocket League. One player is secretly assigned to sabotage their own team — can the group figure out who it is?

## 🎮 How to Play

### Single Match
1. Use `/start <seconds>` to open registration
2. Players press **Join Pool** to enter
3. Teams are randomly assigned (2v2, 3v3, etc.)
4. One player secretly receives a DM: **they are the Impostor**
5. Play the Rocket League match!
6. The host declares which team lost
7. If the Impostor was on the losing team → **Voting Phase** begins
8. Players vote on who they think the Impostor is
9. Results revealed with full recap!

### Series (Best of 3/5/7)
1. Use `/series <rounds> <seconds>` to start a multi-round series
2. Same registration process
3. Play multiple rounds with **accumulating points**
4. Impostor rotates (avoids repeating recent impostors)
5. Live standings shown between rounds
6. Series champion crowned at the end!

## 📋 Commands

| Command | Description |
|---|---|
| `/start [seconds]` | Start a single match (default 30s registration) |
| `/series [rounds] [seconds]` | Start a Best of 3/5/7 series |
| `/cancel` | Admin: cancel the active match/series |
| `/stats [player]` | View a player's detailed stats |
| `/leaderboard` | View the server points ranking |
| `/history` | View recent match history |
| `/series_history` | View recent series history |
| `/reset` | Admin: reset all server stats |

## 💰 Points System

| Action | Points |
|---|---|
| Impostor wins (not caught) | +30 |
| Impostor caught | -10 |
| Correct vote (identified impostor) | +15 |
| Non-impostor team win | +10 |

## 🏆 Series Features

- **Best of 3, 5, or 7** rounds
- **Accumulating points** across all rounds
- **Impostor rotation** — avoids repeating recent impostors
- **Live standings** shown between rounds
- **Series champion** crowned with final rankings
- **Series history** tracked separately

## ⚙️ Setup

1. Create a Discord bot at [discord.com/developers](https://discord.com/developers/applications)
2. Enable **Server Members Intent** and **Message Content Intent**
3. Set the `DISCORD_TOKEN` environment variable
4. Install dependencies: `pip install -r requirements.txt`
5. Run: `python main.py`

## 📁 Data & Logs

- **Stats**: Per-server data in `data/guild_{id}.json` (auto-created)
- **Logs**: Structured logging in `logs/bot.log`
- **Multi-server**: Each Discord server has isolated stats and history

## 🔧 Backend Features

- **Multi-guild support** — each server has its own data
- **Structured logging** — file + console output
- **Error handling** — global error handler with user feedback
- **Data persistence** — JSON-based with error recovery
- **Validation** — robust input checking throughout

## 🎯 Minimum Requirements

- **4 players** minimum (2v2)
- **Even number** of players required
- All players must have **DMs enabled** (for impostor notification)
