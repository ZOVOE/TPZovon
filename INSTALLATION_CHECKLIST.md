# ✅ Installation Checklist

Use this checklist to ensure proper setup of your Card Monitor Bot.

## 📋 Pre-Installation

- [ ] Python 3.8+ installed (`python3 --version`)
- [ ] pip installed (`pip3 --version`)
- [ ] Git installed (optional, for updates)
- [ ] Access to Telegram account
- [ ] Valid Stripe account with live keys

## 🔑 Credentials Setup

### Telegram API Credentials
- [ ] Visited https://my.telegram.org
- [ ] Logged in with phone number
- [ ] Created API app
- [ ] Noted `api_id` (number)
- [ ] Noted `api_hash` (string)

### Bot Token
- [ ] Opened @BotFather on Telegram
- [ ] Created new bot with `/newbot`
- [ ] Noted `bot_token`
- [ ] Disabled Group Privacy (`/mybots` → Bot Settings → Group Privacy → **Turn OFF**)
- [ ] Added bot to target group as admin

### User ID
- [ ] Messaged @userinfobot
- [ ] Noted your user ID
- [ ] Started conversation with your bot (send `/start`)

### Stripe Keys
- [ ] Have at least one SK/PK pair
- [ ] Keys are in **LIVE mode** (`sk_live_...` and `pk_live_...`)
- [ ] Keys have necessary permissions (Customers, PaymentIntents, SetupIntents)

### Group ID
- [ ] Have the correct group ID (negative number with ~13 digits)
- [ ] Bot is added to this group
- [ ] Bot has permission to read messages

## 🛠️ Installation Steps

- [ ] Run `./setup_card_bot.sh`
- [ ] Virtual environment created (`venv/` directory exists)
- [ ] Dependencies installed (no errors)

## ⚙️ Configuration

- [ ] Created `config.env` from example
  ```bash
  cp config.env.example config.env
  ```
- [ ] Edited `config.env` with correct values:
  - [ ] `API_ID`
  - [ ] `API_HASH`
  - [ ] `BOT_TOKEN`
  - [ ] `MONITORED_GROUP_ID`
  - [ ] `OWNER_USER_ID`

- [ ] Created `keys.json` from example
  ```bash
  cp keys.json.example keys.json
  ```
- [ ] Edited `keys.json` with Stripe keys
- [ ] JSON format is valid (test with `python3 -m json.tool keys.json`)

## 🧪 Testing

- [ ] Run bot: `./run_bot.sh`
- [ ] Bot starts without errors
- [ ] See "Bot started successfully!" in logs
- [ ] Bot appears online in Telegram
- [ ] Bot can receive your `/stats` command

## 🎯 Functionality Test

- [ ] Post a test card in the monitored group (use test card: `4111111111111111|12|25|123`)
- [ ] Bot detects the message (check logs)
- [ ] Bot generates cards (check logs)
- [ ] Bot attempts to check cards
- [ ] You receive a report message

## 📊 Verification Commands

```bash
# Check if bot process is running
ps aux | grep card_monitor_bot

# View recent logs
tail -n 50 bot.log

# Check config is loaded
cat config.env | grep -v "^#" | grep "="

# Verify keys format
python3 -m json.tool keys.json

# Test Python dependencies
python3 -c "import pyrogram, aiohttp; print('Dependencies OK')"
```

## 🚀 Production Deployment

Optional: Run as system service

- [ ] Edited service file user if needed
- [ ] Copied service file: `sudo cp card-monitor-bot.service /etc/systemd/system/`
- [ ] Reloaded systemd: `sudo systemctl daemon-reload`
- [ ] Enabled service: `sudo systemctl enable card-monitor-bot`
- [ ] Started service: `sudo systemctl start card-monitor-bot`
- [ ] Checked status: `sudo systemctl status card-monitor-bot`

## 🔍 Common Issues

### ❌ "Please configure your API credentials"
- **Fix**: Edit `config.env` with real credentials, not placeholders

### ❌ "No Stripe keys available"
- **Fix**: Ensure `keys.json` exists and contains valid keys
- **Check**: Run `python3 -m json.tool keys.json`

### ❌ Bot not receiving messages
- **Fix 1**: Turn OFF Group Privacy in BotFather
- **Fix 2**: Re-add bot to group
- **Fix 3**: Verify `MONITORED_GROUP_ID` is correct (negative number)

### ❌ "Invalid token" error
- **Fix**: Get new token from @BotFather, update `config.env`

### ❌ Reports not arriving
- **Fix 1**: Start conversation with bot (send `/start`)
- **Fix 2**: Verify `OWNER_USER_ID` is correct
- **Fix 3**: Check bot has permission to message you

### ❌ Stripe checks all failing
- **Fix 1**: Ensure keys are **live mode** (`sk_live_`, not `sk_test_`)
- **Fix 2**: Verify keys are active in Stripe dashboard
- **Fix 3**: Check rate limits not exceeded

## 📈 Performance Tuning

Adjust these in `config.env`:

- [ ] `CARDS_PER_BIN=80` - More cards = longer processing
- [ ] `MAX_CONCURRENT_CHECKS=20` - Higher = faster but more load

Recommended settings:
- **Fast**: 40 cards, 30 concurrent
- **Balanced**: 80 cards, 20 concurrent  ← Default
- **Thorough**: 150 cards, 10 concurrent

## 🎉 Final Verification

- [ ] Bot is running (check with `ps aux | grep card_monitor_bot`)
- [ ] Bot responds to `/stats` command
- [ ] Bot detects cards in group
- [ ] Bot generates valid cards (Luhn check passes)
- [ ] Bot checks cards via Stripe
- [ ] Bot sends reports with results
- [ ] Logs are clean (no critical errors)

## 📝 Post-Installation

- [ ] Documented your setup
- [ ] Backed up `config.env` and `keys.json` securely
- [ ] Set up monitoring/alerting (optional)
- [ ] Tested with real data
- [ ] Reviewed logs for issues

## 🎊 Success Indicators

✅ Bot uptime > 5 minutes without crashes
✅ Successfully processed at least 1 BIN
✅ Received at least 1 report
✅ No errors in logs (warnings are OK)
✅ `/stats` shows accurate data

---

**Date Completed**: __________________

**Completed By**: __________________

**Notes**: 
_____________________________________
_____________________________________
_____________________________________
