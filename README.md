# 🤖 Telegram Card Monitor & Checker Bot

**High-performance automated card checking system for Telegram**

## ✅ Status: Complete & Production Ready

A fully-featured Telegram bot that monitors groups for credit card information, generates valid variations using Luhn algorithm, checks them via Stripe, and reports results with beautiful formatting.

---

## 🚀 Quick Start

```bash
# 1. Configure
cp config.env.example config.env
nano config.env  # Add your credentials

# 2. Add Stripe keys
cp keys.json.example keys.json
nano keys.json  # Add your keys

# 3. Install & Run
./setup_card_bot.sh
./run_bot.sh
```

**That's it!** Bot will start monitoring immediately.

---

## 📦 What It Does

1. **Monitors** group `-1002587158726` for card messages
2. **Extracts** card data using regex pattern
3. **Generates** 80 valid cards per BIN using Luhn algorithm
4. **Checks** all cards via Stripe API concurrently
5. **Reports** results to user `5211166230` with beautiful formatting

---

## 🎯 Example

**Input:** `"کارگر جدید: 5408898222933198|08|28|458"`

**Process:**
- Extracts: `5408898222933198|08|28`
- BIN: `540889` (first 12 digits)
- Generates: 80 cards like `540889822293XXXX|08|28`
- Checks: All 80 via Stripe concurrently
- Reports: `"12 succeeded, 68 failed (15% rate)"`

---

## 📚 Documentation

- **[QUICK_START.md](QUICK_START.md)** - Get started in 5 minutes
- **[CARD_BOT_README.md](CARD_BOT_README.md)** - Full documentation
- **[INSTALLATION_CHECKLIST.md](INSTALLATION_CHECKLIST.md)** - Setup verification
- **[PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)** - Technical details
- **[FINAL_SUMMARY.txt](FINAL_SUMMARY.txt)** - Complete overview

---

## ⚡ Key Features

- ✅ **Fast**: Pyrogram (fastest Python Telegram library)
- ✅ **Concurrent**: Checks 20 cards simultaneously
- ✅ **Reliable**: Queue system ensures no cards missed
- ✅ **Smart**: Luhn algorithm generates 100% valid cards
- ✅ **Beautiful**: Professional formatted reports
- ✅ **Production-Ready**: Complete error handling & logging

---

## 🔧 Requirements

1. **Telegram API** credentials (from https://my.telegram.org)
2. **Bot Token** (from @BotFather)
3. **Stripe Keys** (live mode: `sk_live_...`, `pk_live_...`)
4. **Python 3.8+**

---

## 📊 Report Example

```
╔═══════════════════════════════╗
║    🎯 CARD CHECKER REPORT    ║
╚═══════════════════════════════╝

📊 BIN Information
├ BIN: 470455******
├ Original Card: 470455******6738|06|28
└ Message ID: 12345

📈 Check Results
├ Total Generated: 80
├ ✅ Succeeded: 12
├ ❌ Failed: 68
└ 📊 Success Rate: 15.0%
```

---

## 🛠️ Files

- **card_monitor_bot.py** - Main bot (18 KB, 450+ lines)
- **stripe_checker_bot.py** - Stripe integration (48 KB)
- **config.env.example** - Configuration template
- **keys.json.example** - Stripe keys structure
- **setup_card_bot.sh** - One-time setup
- **run_bot.sh** - Bot runner

---

## 🎓 Support

- **View logs**: `tail -f bot.log`
- **Check stats**: Send `/stats` to bot
- **Troubleshooting**: See documentation files

---

## ⚠️ Legal Notice

This tool is for **authorized testing only**. Ensure you have proper authorization before use.

---

## 🎉 Status

- ✅ All features implemented
- ✅ Luhn algorithm verified
- ✅ Production tested
- ✅ Documentation complete
- ✅ Ready to deploy

**Built with ❤️ using Pyrogram**