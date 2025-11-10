#!/usr/bin/env python3
"""
Standalone Stripe subscription checker.

Usage:
    python3 stripe_subscription_checker.py
        - Prompts for card lines (up to 20) via stdin.
    python3 stripe_subscription_checker.py path/to/cc.txt
        - Reads cards from file (one per line). Lines with 16-digit card regex
          `dddddddddddddddd|MM|YY[|CVC]` are extracted.

Stripe keys:
    Provide a pair via --secret-key/--publishable-key or set STRIPE_SECRET_KEY /
    STRIPE_PUBLISHABLE_KEY. If omitted, the script randomly picks pairs from
    `keys.json` (same structure as the main bot).

Flow per card:
    1. Create card source via publishable key.
    2. Attach source to customer (created with secret key).
    3. Create and confirm off_session SetupIntent.
    4. Create product & price (once), then start subscription with 10 USD recurring.
    5. Confirm invoice payment; final state printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from colorama import Fore, Style, init as colorama_init

import aiohttp
from aiohttp import BasicAuth, ClientSession, ClientTimeout

# --- Configuration ---
CARD_REGEX = re.compile(r"(\d{16})\|(\d{1,2})\|(\d{2,4})(?:\|(\d{3,4}))?")
MAX_CARDS = 20
REQUEST_TIMEOUT = int(os.getenv("SUB_CHECK_TIMEOUT", "30"))
STRIPE_API_BASE = "https://api.stripe.com"
KEYS_FILE = os.getenv("STRIPE_KEYS_FILE", "keys.json")

STAGE_CODES = {
    "start": "START",
    "create_source": "SRC",
    "create_customer": "CUST",
    "setup_intent": "S_I",
    "subscription": "SUB",
    "invoice_missing": "INVOICE",
    "invoice_pay": "INVOICE",
}

def stage_label(stage: Optional[str]) -> str:
    if not stage:
        return "UNKNOWN"
    return STAGE_CODES.get(stage, stage.upper())


def extract_error_code(details: Optional[Dict]) -> str:
    if isinstance(details, dict):
        err = details.get("error")
        if isinstance(err, dict):
            for key in ("decline_code", "code", "type", "message"):
                val = err.get(key)
                if val:
                    return str(val).replace(" ", "_")
        for key in ("status", "failure_code", "message"):
            val = details.get(key)
            if val:
                return str(val).replace(" ", "_")
        raw = details.get("raw")
        if isinstance(raw, str):
            return raw[:60]
        return json.dumps(details)
    if details:
        return str(details)
    return "UNKNOWN"


@dataclass
class StripeKeys:
    secret_key: str
    publishable_key: str


class StripeSubscriptionChecker:
    def __init__(self, keys: StripeKeys, session: Optional[ClientSession] = None):
        self.keys = keys
        self.session = session or aiohttp.ClientSession(
            timeout=ClientTimeout(total=REQUEST_TIMEOUT)
        )
        self.product_id: Optional[str] = None
        self.price_id: Optional[str] = None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _post(
        self,
        path: str,
        data: Dict[str, str],
        auth: Optional[BasicAuth] = None,
    ) -> Tuple[int, Dict]:
        url = f"{STRIPE_API_BASE}{path}"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with self.session.post(
            url, data=data, headers=headers, auth=auth
        ) as resp:
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = {"raw": await resp.text()}
            return resp.status, payload

    async def create_source(self, card_line: str) -> Tuple[bool, Dict]:
        parts = card_line.strip().split("|")
        if len(parts) < 3:
            return False, {"error": "invalid_format"}
        number, exp_month, exp_year = parts[:3]
        cvc = parts[3] if len(parts) > 3 else ""

        data = {
            "type": "card",
            "card[number]": number,
            "card[exp_month]": exp_month,
            "card[exp_year]": exp_year,
            "key": self.keys.publishable_key,
            "payment_user_agent": "stripe-android/20.48.0;PaymentSheet",
            "card[tokenization_method]": "google",
        }
        if cvc:
            data["card[cvc]"] = cvc

        status, payload = await self._post("/v1/sources", data=data)
        if status == 200 and payload.get("id"):
            return True, payload
        return False, payload

    async def create_customer(self, source_id: str) -> Tuple[bool, Dict]:
        data = {"source": source_id}
        status, payload = await self._post(
            "/v1/customers", data=data, auth=BasicAuth(self.keys.secret_key, "")
        )
        if status == 200 and payload.get("id"):
            return True, payload
        return False, payload

    async def create_setup_intent(self, customer_id: str, payment_method: str) -> Tuple[bool, Dict]:
        data = {
            "customer": customer_id,
            "payment_method": payment_method,
            "confirm": "true",
            "usage": "off_session",
            "return_url": "https://example.com/stripe-return",
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
        }
        status, payload = await self._post(
            "/v1/setup_intents",
            data=data,
            auth=BasicAuth(self.keys.secret_key, ""),
        )
        if status == 200 and payload.get("status") == "succeeded":
            return True, payload
        return False, payload

    async def ensure_product_and_price(self) -> None:
        if self.product_id and self.price_id:
            return
        # Create product
        if not self.product_id:
            status, payload = await self._post(
                "/v1/products",
                data={"name": "Subscription Checker Product"},
                auth=BasicAuth(self.keys.secret_key, ""),
            )
            if status != 200 or "id" not in payload:
                raise RuntimeError(f"Failed to create product: {payload}")
            self.product_id = payload["id"]
        # Create price for $10 subscription (USD, monthly)
        if not self.price_id:
            data = {
                "unit_amount": "1000",
                "currency": "usd",
                "recurring[interval]": "month",
                "product": self.product_id,
            }
            status, payload = await self._post(
                "/v1/prices",
                data=data,
                auth=BasicAuth(self.keys.secret_key, ""),
            )
            if status != 200 or "id" not in payload:
                raise RuntimeError(f"Failed to create price: {payload}")
            self.price_id = payload["id"]

    async def create_subscription(self, customer_id: str) -> Tuple[bool, Dict]:
        await self.ensure_product_and_price()
        data = {
            "customer": customer_id,
            "items[0][price]": self.price_id,
            "expand[]": "latest_invoice.payment_intent",
            "payment_behavior": "default_incomplete",
        }
        status, payload = await self._post(
            "/v1/subscriptions",
            data=data,
            auth=BasicAuth(self.keys.secret_key, ""),
        )
        if status != 200 or "id" not in payload:
            return False, payload
        return True, payload

    async def confirm_invoice_payment(self, invoice_id: str) -> Tuple[bool, Dict]:
        data = {}
        status, payload = await self._post(
            f"/v1/invoices/{invoice_id}/pay",
            data=data,
            auth=BasicAuth(self.keys.secret_key, ""),
        )
        if status == 200:
            return True, payload
        return False, payload

    async def run_card(self, card_line: str) -> Dict:
        result = {
            "card": card_line.strip(),
            "status": "error",
            "stage": "start",
            "details": None,
        }
        ok, src = await self.create_source(card_line)
        if not ok:
            result["stage"] = "create_source"
            result["details"] = src
            return result
        source_id = src.get("id")
        pm_id = src.get("card", {}).get("id") or source_id

        ok, cust = await self.create_customer(source_id)
        if not ok:
            result["stage"] = "create_customer"
            result["details"] = cust
            return result
        customer_id = cust["id"]

        ok, setup_intent = await self.create_setup_intent(customer_id, pm_id)
        if not ok:
            result["stage"] = "setup_intent"
            result["details"] = setup_intent
            return result

        ok, subscription = await self.create_subscription(customer_id)
        if not ok:
            result["stage"] = "subscription"
            result["details"] = subscription
            return result

        invoice = subscription.get("latest_invoice")
        invoice_id = invoice.get("id") if isinstance(invoice, dict) else None
        if not invoice_id:
            result["stage"] = "invoice_missing"
            result["details"] = subscription
            return result

        ok, payment = await self.confirm_invoice_payment(invoice_id)
        result["stage"] = "invoice_pay"
        result["details"] = payment
        if ok:
            result["status"] = "success"
        return result


def parse_cards_from_iterable(lines: Iterable[str]) -> List[str]:
    cards: List[str] = []
    for line in lines:
        for match in CARD_REGEX.finditer(line):
            cards.append(match.group(0))
            if len(cards) >= MAX_CARDS:
                return cards
    return cards


def load_key_pairs(path: str = KEYS_FILE) -> List[StripeKeys]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"❌ Failed to load keys from {path}: {exc}")
        return []

    pairs: List[StripeKeys] = []
    containers: List = []
    if isinstance(data, dict):
        containers = list(data.values())
    elif isinstance(data, list):
        containers = [data]

    for container in containers:
        if not isinstance(container, list):
            continue
        for entry in container:
            if not isinstance(entry, dict):
                continue
            sk = entry.get("sk") or entry.get("secret_key")
            pk = entry.get("pk") or entry.get("publishable_key")
            if sk and pk:
                pairs.append(StripeKeys(secret_key=sk, publishable_key=pk))
    return pairs


async def main_async(args: argparse.Namespace) -> None:
    secret_key = args.secret_key or os.getenv("STRIPE_SECRET_KEY")
    publishable_key = args.publishable_key or os.getenv("STRIPE_PUBLISHABLE_KEY")

    key_pairs: List[StripeKeys] = []
    if secret_key and publishable_key:
        key_pairs.append(StripeKeys(secret_key=secret_key, publishable_key=publishable_key))
    else:
        key_pairs = load_key_pairs()

    if not key_pairs:
        print("❌ No Stripe keys available. Provide --secret-key/--publishable-key or populate keys.json.")
        return

    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="ignore") as f:
            cards = parse_cards_from_iterable(f)
    else:
        print("Enter card lines (format: 4111111111111111|MM|YY[|CVC]), press Ctrl+D when done:")
        cards = parse_cards_from_iterable(sys.stdin)

    unique_cards = list(dict.fromkeys(cards))[:MAX_CARDS]
    if not unique_cards:
        print("No valid cards found.")
        return

    checkers: Dict[str, StripeSubscriptionChecker] = {}
    try:
        for card in unique_cards:
            key_pair = random.choice(key_pairs)
            checker = checkers.get(key_pair.secret_key)
            if checker is None:
                checker = StripeSubscriptionChecker(key_pair)
                checkers[key_pair.secret_key] = checker

            start = time.time()
            info = await checker.run_card(card)
            duration = time.time() - start

            stage = stage_label(info.get("stage"))
            if info["status"] == "success":
                status_code = "SUCCESS"
                line = f"{card}|{status_code}|{stage}"
                meta = f"[{duration:.2f}s | {key_pair.secret_key[:10]}...]"
                print(f"{Fore.GREEN}{line}{Style.RESET_ALL} {Style.DIM}{meta}{Style.RESET_ALL}")
                print(f"{Style.DIM}{'-' * 70}{Style.RESET_ALL}")
            else:
                status_code = extract_error_code(info.get("details"))
                line = f"{card}|{status_code}|{stage}"
                meta = f"[{duration:.2f}s | {key_pair.secret_key[:10]}...]"
                print(f"{Fore.RED}{line}{Style.RESET_ALL} {Style.DIM}{meta}{Style.RESET_ALL}")
                if info.get("details"):
                    pretty = json.dumps(info["details"], indent=2, ensure_ascii=False)
                    print(f"{Fore.YELLOW}{pretty}{Style.RESET_ALL}")
                print(f"{Style.DIM}{'-' * 70}{Style.RESET_ALL}")
    finally:
        for checker in checkers.values():
            await checker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stripe subscription checker")
    parser.add_argument("file", nargs="?", help="Optional path to card list file")
    parser.add_argument("--secret-key", help="Stripe secret key (live)")
    parser.add_argument("--publishable-key", help="Stripe publishable key (live)")
    args = parser.parse_args()

    colorama_init(autoreset=True)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
