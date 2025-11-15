# 🤖 High-Performance Card Monitor & Checker Bot

A fast, automated Telegram bot that monitors groups for credit card information, generates valid variations using the Luhn algorithm, checks them via Stripe, and reports results with beautiful formatting.

## 🌟 Features

- **Real-time Group Monitoring**: Listens to messages in specified Telegram group
- **Intelligent Card Extraction**: Uses regex to find card data in any message format
- **Luhn Algorithm**: Generates 80 valid card variations per BIN
- **Concurrent Checking**: Checks multiple cards simultaneously (configurable)
- **Beautiful Reports**: Sends detailed, formatted reports to owner
- **Queue System**: Processes cards efficiently without missing any
- **Statistics Tracking**: Keeps track of success rates and performance metrics
- **Error Handling**: Robust error handling and logging

## 📋 Requirements

- Python 3.8+
- Telegram API credentials (from https://my.telegram.org)
- Bot token (from @BotFather)
- Stripe API keys (SK and PK pairs)

## 🚀 Quick Start

### 1. Installation

```bash
# Clone or download the project
cd /workspace

# Run setup script
chmod +x setup_card_bot.sh
./setup_card_bot.sh
```

### 2. Configuration

Edit `config.env` with your credentials:

```env
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
MONITORED_GROUP_ID=-1002587158726
OWNER_USER_ID=5211166230
```

### 3. Add Stripe Keys

Edit `keys.json`:

```json
{
  "default": [
    {
      "sk": "sk_live_xxxxxxxxxxxxx",
      "pk": "pk_live_xxxxxxxxxxxxx"
    },
    {
      "sk": "sk_live_yyyyyyyyyyyyy",
      "pk": "pk_live_yyyyyyyyyyyyy"
    }
  ]
}
```

### 4. Run the Bot

```bash
# Activate virtual environment
source venv/bin/activate

# Run the bot
python3 card_monitor_bot.py
```

Or run in background:

```bash
nohup python3 card_monitor_bot.py > bot.log 2>&1 &
```

## 🎯 How It Works

1. **Monitoring**: Bot listens to all messages in the specified group
2. **Extraction**: When a card is found (format: `5408898222933198|08|28|458`), it extracts the BIN
3. **Generation**: Creates 80 valid card variations using Luhn algorithm
4. **Checking**: Tests each card against Stripe API concurrently
5. **Reporting**: Sends beautiful formatted report to owner with:
   - BIN information
   - Success/failure counts
   - Success rate percentage
   - List of successful cards
   - Session statistics

## 📊 Bot Commands

Send these commands in private message to the bot:

- `/stats` - View bot statistics and performance metrics
- `/proxy on|off` - Toggle the Stripe proxy defined in `config.env`
- `/binlookupproxy on|off` - Toggle the BIN lookup proxy (`BIN_LOOKUP_PROXY_URL`) used to fetch scheme/bank metadata

## 📝 Message Format

The bot recognizes cards in this format:
```
5408898222933198|08|28|458
4704550003576738|06|28|000
```

Regex pattern: `(\d{16})\|(\d{1,2})\|(\d{2,4})(?:\|(\d{3,4}))?`

## 🎨 Report Example

```
╔═══════════════════════════════╗
║    🎯 CARD CHECKER REPORT    ║
╚═══════════════════════════════╝

📊 **BIN Information**
├ BIN: `470455******`
├ Original Card: `470455******|06|28`
└ Message ID: `12345`

📈 **Check Results**
├ Total Generated: `80`
├ ✅ Succeeded: `12`
├ ❌ Failed: `68`
└ 📊 Success Rate: `15.0%`

⏱ **Session Stats**
├ Total Checked: `240`
├ Total Succeeded: `35`
└ Overall Rate: `14.6%`

✅ **SUCCESSFUL CARDS** (12):
470455******6738|06|28
470455******1234|06|28
...
```

## ⚙️ Configuration Options

Edit these in `card_monitor_bot.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONITORED_GROUP_ID` | -1002587158726 | Group to monitor |
| `OWNER_USER_ID` | 5211166230 | User to send reports |
| `CARDS_PER_BIN` | 80 | Cards to generate per BIN |
| `MAX_CONCURRENT_CHECKS` | 20 | Concurrent Stripe checks |

## 🔧 Advanced Configuration

### Using Environment Variables

Instead of editing the Python file, you can use environment variables:

```bash
export MONITORED_GROUP_ID=-1002587158726
export OWNER_USER_ID=5211166230
export CARDS_PER_BIN=80
export MAX_CONCURRENT_CHECKS=20
```

### Multiple Stripe Key Groups

Organize keys by group in `keys.json`:

```json
{
  "group1": [
    {"sk": "sk_live_xxx", "pk": "pk_live_xxx"}
  ],
  "group2": [
    {"sk": "sk_live_yyy", "pk": "pk_live_yyy"}
  ]
}
```

## 📊 Performance

- **Speed**: Uses Pyrogram (fastest Python Telegram library)
- **Efficiency**: Concurrent card checking (configurable)
- **Reliability**: Queue system ensures no cards are missed
- **Scalability**: Can handle multiple BINs simultaneously

## 🛡️ Security Notes

- Never commit `config.env` or `keys.json` to version control
- Use `.gitignore` to exclude sensitive files
- Rotate Stripe keys regularly
- Monitor bot logs for suspicious activity

## 🐛 Troubleshooting

### Bot not receiving messages
- Ensure bot is added to the group
- Check if `MONITORED_GROUP_ID` is correct (must include minus sign)
- Verify bot has permission to read messages

### Stripe checks failing
- Verify Stripe keys are valid and live mode
- Check `keys.json` format is correct
- Ensure keys have necessary permissions

### Reports not sending
- Verify `OWNER_USER_ID` is correct
- Start a conversation with the bot first
- Check bot logs for errors

## 📜 Logs

View logs in real-time:
```bash
tail -f bot.log
```

## 🔄 Updates

To update the bot:
```bash
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
```

## ⚠️ Disclaimer

This bot is for educational and authorized testing purposes only. Ensure you have proper authorization before using this tool. Unauthorized testing of payment card information is illegal.

## 📄 License

This project is provided as-is for educational purposes.

## 🤝 Support

For issues or questions, check the logs first. Common issues:
- Incorrect API credentials
- Invalid Stripe keys
- Bot not in the monitored group
- Insufficient permissions
