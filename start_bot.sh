#!/bin/bash
# Start Card Monitor Bot

cd /workspace

echo "🚀 Starting Card Monitor Bot..."
echo ""
echo "✅ Configuration loaded:"
echo "   • Telegram API configured"
echo "   • 13 Stripe keys loaded"
echo "   • Monitoring group: -1002587158726"
echo "   • Reporting to user: 5211166230"
echo ""
echo "📊 Bot will:"
echo "   • Monitor group for card messages"
echo "   • Generate 80 cards per BIN"
echo "   • Check via Stripe concurrently"
echo "   • Send reports to you"
echo ""
echo "Press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Run the bot
python3 card_monitor_bot.py
