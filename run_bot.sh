#!/bin/bash
# Simple runner script for Card Monitor Bot

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Check if config exists
if [ ! -f "config.env" ]; then
    echo "❌ config.env not found!"
    echo "📝 Please copy config.env.example to config.env and configure it"
    exit 1
fi

# Check if keys.json exists
if [ ! -f "keys.json" ]; then
    echo "⚠️ keys.json not found, creating empty one..."
    echo '{}' > keys.json
fi

# Run the bot
echo "🚀 Starting Card Monitor Bot..."
python3 card_monitor_bot.py
