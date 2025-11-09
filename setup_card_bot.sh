#!/bin/bash
# Setup script for Card Monitor Bot

echo "🚀 Setting up Card Monitor Bot..."

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install requirements
echo "📥 Installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

# Create config file
echo "⚙️ Setting up configuration..."
if [ ! -f config.env ]; then
    cp config.env.example config.env
    echo "✅ Created config.env - Please edit it with your credentials"
else
    echo "ℹ️ config.env already exists"
fi

# Create keys.json if it doesn't exist
if [ ! -f keys.json ]; then
    echo '{}' > keys.json
    echo "✅ Created empty keys.json"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Edit config.env with your Telegram credentials"
echo "2. Add Stripe keys to keys.json in format:"
echo '   {"default": [{"sk": "sk_live_xxx", "pk": "pk_live_xxx"}]}'
echo "3. Run: python3 card_monitor_bot.py"
