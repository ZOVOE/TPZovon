# 🚀 Quick Start Guide - Card Monitor Bot

## ⚡ 5-Minute Setup

### Step 1: Get Telegram Credentials

1. Go to https://my.telegram.org
2. Log in with your phone number
3. Click "API Development Tools"
4. Create an app and note down:
   - `api_id` (number)
   - `api_hash` (string)

### Step 2: Create a Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Follow instructions and get your `bot_token`
4. Send `/mybots` → Select your bot → Bot Settings → Group Privacy → **Turn OFF**
5. Add your bot to the group you want to monitor

### Step 3: Get Your User ID

1. Search for `@userinfobot` on Telegram
2. Start the bot and it will show your user ID

### Step 4: Configure

```bash
# Copy example config
cp config.env.example config.env

# Edit with your credentials
nano config.env
```

Fill in:
```env
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
MONITORED_GROUP_ID=-1002587158726
OWNER_USER_ID=5211166230
```

### Step 5: Add Stripe Keys

```bash
# Copy example keys
cp keys.json.example keys.json

# Edit with your Stripe keys
nano keys.json
```

### Step 6: Install & Run

```bash
# Run setup (one time only)
./setup_card_bot.sh

# Start the bot
./run_bot.sh
```

## 🎯 What Happens Next?

1. Bot starts monitoring the group
2. When someone posts a card (e.g., `5408898222933198|08|28|458`)
3. Bot extracts the BIN and generates 80 variations
4. Checks all cards via Stripe concurrently
5. Sends you a beautiful report with results!

## 📱 Example Report

You'll receive messages like:

```
╔═══════════════════════════════╗
║    🎯 CARD CHECKER REPORT    ║
╚═══════════════════════════════╝

📊 BIN Information
├ BIN: 540889******
├ Original Card: 540889******3198|08|28
└ Message ID: 12345

📈 Check Results
├ Total Generated: 80
├ ✅ Succeeded: 12
├ ❌ Failed: 68
└ 📊 Success Rate: 15.0%
```

## 🔧 Troubleshooting

### Bot not starting?
```bash
# Check logs
tail -f bot.log
```

### "No Stripe keys available"?
- Make sure `keys.json` exists and is valid JSON
- Check your Stripe keys are in live mode (start with `sk_live_` and `pk_live_`)

### Bot not receiving messages?
- Make sure bot is added to the group
- Make sure Group Privacy is OFF in BotFather settings
- Check `MONITORED_GROUP_ID` is correct (must be negative number)

## 🛑 Stopping the Bot

```bash
# If running in foreground: Ctrl+C

# If running in background:
pkill -f card_monitor_bot.py
```

## 📊 Check Statistics

Send `/stats` to your bot in private message to see:
- Total cards checked
- Success rate
- Uptime
- Queue status

## 🔄 Running in Background

```bash
# Start in background
nohup ./run_bot.sh > bot.log 2>&1 &

# Check if running
ps aux | grep card_monitor_bot

# View logs
tail -f bot.log
```

## 🎓 Advanced Options

Edit `card_monitor_bot.py` to customize:
- `CARDS_PER_BIN`: Change from 80 to any number
- `MAX_CONCURRENT_CHECKS`: Adjust concurrent Stripe checks
- Report formatting
- Card generation logic

## ⚠️ Important Notes

1. **Legal**: Only use with authorization
2. **Keys**: Never share your `config.env` or `keys.json`
3. **Limits**: Stripe has rate limits - adjust `MAX_CONCURRENT_CHECKS` if needed
4. **Costs**: Stripe charges may apply for successful checks

## 📞 Need Help?

Check the full documentation in `CARD_BOT_README.md`
