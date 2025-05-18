import logging
import sqlite3
import requests
from time import time
import os
from dotenv import load_dotenv
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler


logger = logging.getLogger(__name__)
load_dotenv()
# Crypto Pay API Configuration
CRYPTO_PAY_API_TOKEN = os.getenv("CRYPTOPAYAPI")  # Replace with your actual Crypto Pay API token
CRYPTO_PAY_API_BASE = "https://testnet-pay.crypt.bot/api"
COINMARKETCAP_API_KEY = os.getenv("COINCAPMARKETAPI")
COINMARKETCAP_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

# Default headers for all API requests
HEADERS = {
    "Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN
}
# Cache dictionary: { symbol: { 'price': ..., 'timestamp': ... } }
EXCHANGE_RATE_CACHE = {}
CACHE_TTL = 60  # cache live time in seconds

# Supported assets and minimum deposit/withdrawal amounts
SUPPORTED_ASSETS = {
    "USDT": {"name": "Tether USD (TRC20)", "min_deposit": 10, "min_withdrawal": 10},
    "BTC": {"name": "Bitcoin", "min_deposit": 0.0005, "min_withdrawal": 0.0005},
    "TON": {"name": "Toncoin", "min_deposit": 50, "min_withdrawal": 150},
    "ETH": {"name": "Ethereum", "min_deposit": 0.003, "min_withdrawal": 0.003}
}

# Default asset
DEFAULT_ASSET = "USDT"


# Database setup
def setup_payment_database():
    """Set up database tables for payment system."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Create payment_invoices table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payment_invoices (
        invoice_id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount REAL,
        asset TEXT,
        status TEXT,
        type TEXT,
        created_at TEXT,
        paid_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    
    # Create withdrawal_requests table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS withdrawal_requests (
        request_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        asset TEXT,
        wallet_address TEXT,
        status TEXT,
        created_at TEXT,
        processed_at TEXT,
        memo TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Payment database tables created or verified")

def fetch_real_time_usd_price(symbol):
    """
    Fetch the real-time USD price for a given crypto symbol using CoinMarketCap API with caching.
    """
    # Check cache first
    current_time = time()
    if symbol in EXCHANGE_RATE_CACHE:
        cached = EXCHANGE_RATE_CACHE[symbol]
        if current_time - cached['timestamp'] < CACHE_TTL:
            return cached['price']

    try:
        headers = {
            "Accepts": "application/json",
            "X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY
        }

        params = {
            "symbol": symbol,
            "convert": "USD"
        }

        response = requests.get(COINMARKETCAP_API_URL, headers=headers, params=params, timeout=10)
        data = response.json()

        if response.status_code == 200 and "data" in data:
            price = data["data"][symbol]["quote"]["USD"]["price"]

            # Store in cache
            EXCHANGE_RATE_CACHE[symbol] = {
                "price": price,
                "timestamp": current_time
            }

            return price
        else:
            logger.error(f"Error from CoinMarketCap API for {symbol}: {data}")
            return EXCHANGE_RATE_CACHE.get(symbol, {}).get('price', None)  # fallback to old price
    except Exception as e:
        logger.error(f"Exception while fetching rate for {symbol}: {e}")
        return EXCHANGE_RATE_CACHE.get(symbol, {}).get('price', None)  # fallback

def convert_to_usd(amount, asset):
    """Convert crypto to USD using live exchange rate."""
    price = fetch_real_time_usd_price(asset)
    if not price:
        logger.warning(f"Failed to fetch rate for {asset}, fallback to 1:1")
        return amount
    return amount * price

# Convert USD to cryptocurrency
def convert_from_usd(usd_amount, asset):
    """Convert USD to crypto using live exchange rate."""
    price = fetch_real_time_usd_price(asset)
    if not price or price == 0:
        logger.warning(f"Failed to fetch rate for {asset}, fallback to 1:1")
        return usd_amount
    return usd_amount / price

# API Helper Functions
def test_api_connection():
    """Test connection to Crypto Pay API."""
    try:
        response = requests.get(f"{CRYPTO_PAY_API_BASE}/getMe", headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                logger.info(f"Connected to Crypto Pay API as: {data.get('result', {}).get('app_name')}")
                return True
        logger.error(f"Failed to connect to Crypto Pay API: {response.text}")
        return False
    except Exception as e:
        logger.error(f"Error connecting to Crypto Pay API: {e}")
        return False

def get_supported_assets():
    """Get list of supported assets from Crypto Pay API."""
    try:
        response = requests.get(f"{CRYPTO_PAY_API_BASE}/getAssets", headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                assets = data.get("result", [])
                return assets
        return []
    except Exception as e:
        logger.error(f"Error fetching assets: {e}")
        return []

def create_deposit_invoice(user_id, amount, asset=DEFAULT_ASSET):
    """Create a deposit invoice using Crypto Pay API."""
    try:
        payload = {
            "asset": asset,
            "amount": str(amount),
            "description": f"Deposit to bot account for user {user_id}",
            "hidden_message": "Thank you for your deposit!",
            "paid_btn_name": "openBot",
            "paid_btn_url": f"https://t.me/Botlistsbot?start=deposit_success"
        }
        
        response = requests.post(
            f"{CRYPTO_PAY_API_BASE}/createInvoice", 
            headers=HEADERS,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                invoice = data.get("result")
                
                # Store invoice in database
                conn = sqlite3.connect('referral_bot.db')
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO payment_invoices (invoice_id, user_id, amount, asset, status, type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        invoice["invoice_id"], 
                        user_id, 
                        amount, 
                        asset, 
                        "active", 
                        "deposit",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                )
                conn.commit()
                conn.close()
                
                return invoice
        
        logger.error(f"Failed to create invoice: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        return None

def get_invoice_status(invoice_id):
    """Get status of a specific invoice."""
    try:
        params = {
            "invoice_ids": invoice_id
        }
        
        response = requests.get(
            f"{CRYPTO_PAY_API_BASE}/getInvoices",
            headers=HEADERS,
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("ok") and data.get("result", {}).get("items"):
                return data["result"]["items"][0]
        
        return None
    except Exception as e:
        logger.error(f"Error checking invoice status: {e}")
        return None

def process_successful_deposit(user_id, amount, asset=DEFAULT_ASSET):
    """Process a successful deposit by updating user balance."""
    try:
        # Convert to USD equivalent (simplified - in reality, would use exchange rates)
        # For now, assume 1:1 for USDT, and use spot prices for others
        # Convert to USD equivalent
        usd_amount = convert_to_usd(amount, asset)
        
        conn = sqlite3.connect("referral_bot.db", check_same_thread=False, timeout=10)
        cursor = conn.cursor()

        try:
        
            # Get current deposit amount
            cursor.execute("SELECT deposit_amount FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            current_deposit = result[0] if result else 0
            
            # Update deposit amount
            new_deposit = current_deposit + usd_amount
            cursor.execute(
                "UPDATE users SET deposit_amount = ? WHERE user_id = ?",
                (new_deposit, user_id)
            )
            
            # Update user tier based on new deposit amount
            from main import update_user_tier, add_transaction
            new_tier = update_user_tier(user_id, new_deposit, conn, cursor)
            
            # Add transaction record
            
            add_transaction(user_id, usd_amount, f"deposit_{asset}",conn, cursor)
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "usd_amount": usd_amount,
                "new_deposit": new_deposit,
                "new_tier": new_tier
            }
        except Exception as e:
            conn.rollback()
            logger.error(f"Error processing deposit: {e}")
            return {"success": False, "error": str(e)}
        finally:
                conn.close()
    except Exception as e:
        logger.error(f"Error processing deposit: {e}")
        return {"success": False, "error": str(e)}


def create_withdrawal_request(user_id, amount, asset, wallet_address, usd_amount):
    """Create a withdrawal request to be processed by admin."""
    try:
        conn = sqlite3.connect('referral_bot.db')
        cursor = conn.cursor()
        
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Insert withdrawal request
            cursor.execute(
                "INSERT INTO withdrawal_requests (user_id, amount, asset, wallet_address, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, amount, asset, wallet_address, "pending", now)
            )
            
            request_id = cursor.lastrowid
            
            # Update user balance - deduct from earnings first, then deposits if needed
            cursor.execute("SELECT earning_amount, deposit_amount FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            if not result:
                raise ValueError("User not found")
                
            earning_amount, deposit_amount = result
            
            # Deduct from earnings first
            if earning_amount >= usd_amount:
                new_earning = earning_amount - usd_amount
                new_deposit = deposit_amount
            else:
                # Deduct remainder from deposit
                remainder = usd_amount - earning_amount
                new_earning = 0
                new_deposit = deposit_amount - remainder
                
                if new_deposit < 0:
                    raise ValueError("Insufficient funds")
            
            cursor.execute(
                "UPDATE users SET earning_amount = ?, deposit_amount = ? WHERE user_id = ?", 
                (new_earning, new_deposit, user_id)
            )
            
            # Add transaction record
            from main import add_transaction
            add_transaction(user_id, -usd_amount, f"withdrawal_request_{asset}", conn, cursor)
            
            conn.commit()
            
            return {
                "success": True, 
                "request_id": request_id,
                "new_balance": new_earning + new_deposit
            }
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error creating withdrawal: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Error creating withdrawal request: {e}")
        return {"success": False, "error": str(e)}

async def notify_admins_of_withdrawal(update, context, ADMIN_IDS, user_id, request_id, amount, asset, wallet_address):
    """Notify admins of a new withdrawal request."""
    from admin import ADMIN_IDS
    
    message = (
        f"🔔 *New Withdrawal Request*\n\n"
        f"Request ID: `{request_id}`\n"
        f"User ID: `{user_id}`\n"
        f"Amount: `{amount} {asset}`\n"
        f"Wallet: `{wallet_address}`\n\n"
        f"Use /admin to approve or reject."
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{request_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


# Telegram Bot Handlers
async def deposit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deposit request."""
    query = update.callback_query
    await query.answer()
    
    # Store the current state to return to after selecting asset
    context.user_data['previous_state'] = 'deposit'
    
    # Show asset selection
    keyboard = []
    for asset, details in SUPPORTED_ASSETS.items():
        keyboard.append([InlineKeyboardButton(f"{details['name']} ({asset})", callback_data=f"deposit_asset_{asset}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💰 *Deposit Funds*\n\n"
        "Welcome to the deposit section!\n\n"
        "• You can deposit in multiple cryptocurrencies (USDT, BTC, ETH, TON).\n"
        "• *Minimum deposit*: \n"
        "   - 20 USDT\n"
        "   - 0.0005 BTC\n"
        "   - 0.003 ETH\n"
        "   - 50 TON\n\n"
        "⚡️ *Tier System Benefits*:\n"
        "Your total deposit amount determines your tier. Higher tiers give you better referral and bonus rewards:\n\n"
        "🔹 *Tier 1* — $10+ deposited → Standard bonuses\n"
        "🔸 *Tier 2* — $100+ deposited → +20% referral bonus\n"
        "🏅 *Tier 3* — $500+ deposited → +50% referral bonus and priority rewards\n\n"
        "🔐 Your funds are secure and can be withdrawn anytime after meeting the minimum balance and referral conditions.\n\n"
        "👉 Please select a cryptocurrency to continue:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def deposit_asset_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle asset selection for deposit."""
    query = update.callback_query
    await query.answer()
    
    # Extract selected asset from callback data
    asset = query.data.split('_')[2]
    
    # Store selected asset in user data
    context.user_data['selected_asset'] = asset
    
    min_amount = SUPPORTED_ASSETS[asset]['min_deposit']
    
    deposit_message = (
        f"💰 *Deposit {asset}*\n\n"
        f"Please enter the amount of {asset} you wish to deposit.\n\n"
        f"Minimum deposit: {min_amount} {asset}\n\n"
        f"Example: To deposit {min_amount} {asset}, just type `{min_amount}`"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="deposit")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(deposit_message, reply_markup=reply_markup, parse_mode="Markdown")
    
    # Set user state to expect deposit amount
    context.user_data['expecting_crypto_deposit'] = True

async def process_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the crypto deposit amount entered by user."""
    # Reset state
    context.user_data['expecting_crypto_deposit'] = False
    
    try:
        amount = float(update.message.text.strip())
        asset = context.user_data.get('selected_asset', DEFAULT_ASSET)
        min_amount = SUPPORTED_ASSETS[asset]['min_deposit']
        
        if amount < min_amount:
            await update.message.reply_text(f"Amount too small. Minimum deposit is {min_amount} {asset}.")
            return
    except ValueError:
        await update.message.reply_text("Please enter a valid number for the deposit amount.")
        return
    
    user_id = update.effective_user.id
    
    # Create invoice
    invoice = create_deposit_invoice(user_id, amount, asset)
    
    if not invoice:
        await update.message.reply_text("Sorry, there was an error creating your deposit invoice. Please try again later.")
        return
    
    # Create payment button
    keyboard = [
        [InlineKeyboardButton("💳 Pay Now", url=invoice["pay_url"])],
        [InlineKeyboardButton("Check Payment Status", callback_data=f"check_deposit_{invoice['invoice_id']}")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🧾 I've created a deposit invoice for {amount} {asset}.\n\n"
        f"Click the 'Pay Now' button below to complete your deposit.\n\n"
        f"After payment, click 'Check Payment Status' to verify your deposit was received.",
        reply_markup=reply_markup
    )

async def check_deposit_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check status of a deposit invoice."""
    query = update.callback_query
    await query.answer()
    
    invoice_id = query.data.split('_')[2]
    
    # Get invoice details from database
    conn = sqlite3.connect("referral_bot.db", check_same_thread=False, timeout=10)

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM payment_invoices WHERE invoice_id = ?", (invoice_id,))
    invoice_record = cursor.fetchone()
    
    if not invoice_record:
        await query.edit_message_text("Invoice not found. Please contact support.")
        conn.close()
        return
    
    # Check status with API
    invoice = get_invoice_status(invoice_id)
    
    if not invoice:
        await query.edit_message_text("Unable to check invoice status. Please try again later.")
        conn.close()
        return
    
    # Extract info
    db_status = invoice_record[4]
    api_status = invoice["status"]
    user_id = invoice_record[1]
    amount = invoice_record[2]
    asset = invoice_record[3]
    
    # If status changed to paid
    if db_status != "paid" and api_status == "paid":
        # Update database
        cursor.execute(
            "UPDATE payment_invoices SET status = ?, paid_at = ? WHERE invoice_id = ?",
            ("paid", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), invoice_id)
        )
        conn.commit()
        
        # Process the deposit
        result = process_successful_deposit(user_id, amount, asset)
        
        if result["success"]:
            message = (
                f"✅ Deposit confirmed: {amount} {asset} (${result['usd_amount']:.2f})\n\n"
                f"Your deposit has been added to your account.\n\n"
                f"Total deposit balance: ${result['new_deposit']:.2f}\n"
                f"Current tier: {result['new_tier']}"
            )
        else:
            message = (
                f"✅ Payment received: {amount} {asset}\n\n"
                f"However, there was an error updating your account. Please contact support."
            )
    elif api_status == "paid":
        message = f"✅ This invoice has already been paid and processed."
    elif api_status == "active":
        message = (
            f"⏳ This invoice is still waiting for payment.\n\n"
            f"Amount: {amount} {asset}\n\n"
            f"Click 'Pay Now' to complete your deposit."
        )
    else:
        message = f"❌ This invoice is {api_status}. Please create a new deposit request."
    
    conn.close()
    
    # Create response buttons
    keyboard = []
    if api_status == "active":
        keyboard.append([InlineKeyboardButton("💳 Pay Now", url=invoice["pay_url"])])
        keyboard.append([InlineKeyboardButton("Check Again", callback_data=f"check_deposit_{invoice_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal request."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Get user balance
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT deposit_amount, earning_amount, user_id FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    
    if not user_data:
        await query.edit_message_text(
            "💸 *Withdraw Funds*\n\n"
            "You don't have an account with us yet. Please start using the bot first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]),
            parse_mode="Markdown"
        )
        return
        
    deposit = user_data[0] if user_data[0] else 0
    earning = user_data[1] if user_data[1] else 0
    available_balance = deposit + earning
    
    if available_balance <= 0:
        await query.edit_message_text(
            "💸 *Withdraw Funds*\n\n"
            "You currently have no funds available to withdraw.\n\n"
            "Earn by referring friends and claiming daily bonuses!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]),
            parse_mode="Markdown"
        )
        return   
    
    # Store the current state
    context.user_data['previous_state'] = 'withdraw'
    context.user_data['available_balance'] = available_balance
    context.user_data['earning_amount'] = earning
    context.user_data['deposit_amount'] = deposit
    
    # 🔍 Check referral count
    cursor.execute("""
        SELECT COUNT(*) FROM users WHERE referrer_id = ?
    """, (user_id,))
    referral_count = cursor.fetchone()[0]

    if referral_count < 3:
        await query.edit_message_text(
            f"❌ *Withdrawal Locked*\n\n"
            f"To withdraw funds, you must refer at least *3 new users* using your referral link.\n\n"
            f"You have referred only *{referral_count}* user(s) so far.\n"
            f"Start sharing your referral link to unlock withdrawals!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]),
            parse_mode="Markdown"
        )
        return
    conn.close()
    # Show asset selection
    keyboard = []
    for asset, details in SUPPORTED_ASSETS.items():
        keyboard.append([InlineKeyboardButton(f"{details['name']} ({asset})", callback_data=f"withdraw_asset_{asset}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"💸 *Withdraw Funds*\n\n"
        f"Available Balance: ${available_balance:.2f}\n"
        f"- From earnings: ${earning:.2f}\n"
        f"- From deposits: ${deposit:.2f}\n\n"
        f"Please select the cryptocurrency you want to withdraw:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def withdraw_asset_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle asset selection for withdrawal."""
    query = update.callback_query
    await query.answer()
    
    # Extract selected asset from callback data
    asset = query.data.split('_')[2]
    available_balance = context.user_data.get('available_balance', 0)
    earning_amount = context.user_data.get('earning_amount', 0)
    
    # Store selected asset in user data
    context.user_data['selected_asset'] = asset
    
    min_amount = SUPPORTED_ASSETS[asset]['min_withdrawal']

    # Calculate approximate crypto amount based on USD
    approx_crypto = convert_from_usd(available_balance, asset)
    
    # Store state for expecting wallet address
    context.user_data['expecting_wallet_address'] = True
    
    message = (
        f"💸 *Withdraw {asset}*\n\n"
        f"Available Balance: ${available_balance:.2f} \n"
        f"Withdrawal Amount: ${earning_amount:.2f} \n"
        f"Minimum withdrawal: {min_amount} {asset}\n\n"
        f"Please enter your {asset} wallet address:"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="withdraw")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def process_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the wallet address entered by the user."""
    # Reset state
    context.user_data['expecting_wallet_address'] = False
    
    wallet_address = update.message.text.strip()
    
    # Simple validation - in reality, you'd want more robust validation based on the specific cryptocurrency
    if len(wallet_address) < 10:
        await update.message.reply_text("Please enter a valid wallet address (at least 10 characters).")
        context.user_data['expecting_wallet_address'] = True
        return
    
    # Store wallet address
    context.user_data['wallet_address'] = wallet_address
    asset = context.user_data.get('selected_asset', DEFAULT_ASSET)
    available_balance = context.user_data.get('available_balance', 0)
    min_amount = SUPPORTED_ASSETS[asset]['min_withdrawal']
    
    # Calculate approximate crypto amount based on USD
    approx_crypto = convert_from_usd(available_balance, asset)
    min_usd = convert_to_usd(min_amount, asset)

    message = (
        f"💸 *Withdraw {asset}*\n\n"
        f"Available Balance: ${available_balance:.2f} (≈ {approx_crypto:.8f} {asset})\n"
        f"Minimum withdrawal: {min_amount} {asset} (≈ ${min_usd:.2f})\n\n"
        f"Wallet address: `{wallet_address}`\n\n"
        f"Now, please enter the amount of {asset} you wish to withdraw:"
    )
    
    # Set up state for expecting withdrawal amount
    context.user_data['expecting_crypto_withdrawal'] = True
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def process_withdrawal_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the crypto withdrawal amount entered by user."""
    # Reset state
    context.user_data['expecting_crypto_withdrawal'] = False
    
    try:
        amount = float(update.message.text.strip())
        asset = context.user_data.get('selected_asset', DEFAULT_ASSET)
        wallet_address = context.user_data.get('wallet_address', '')
        available_balance = context.user_data.get('available_balance', 0)
        min_amount = SUPPORTED_ASSETS[asset]['min_withdrawal']
        
        if amount < min_amount:
            await update.message.reply_text(f"Amount too small. Minimum withdrawal is {min_amount} {asset}.")
            context.user_data['expecting_crypto_withdrawal'] = True
            return
            
        # Convert to USD for balance check
        usd_amount = convert_to_usd(amount, asset)
        
        if usd_amount > available_balance:
            await update.message.reply_text(
                f"Insufficient funds. Your maximum withdrawal amount is ${available_balance:.2f} "
                f"(≈ {convert_from_usd(available_balance, asset):.8f} {asset}).\n\n"
                f"Please enter a smaller amount."
            )
            context.user_data['expecting_crypto_withdrawal'] = True
            return
    except ValueError:
        await update.message.reply_text("Please enter a valid number for the withdrawal amount.")
        context.user_data['expecting_crypto_withdrawal'] = True
        return
    
    user_id = update.effective_user.id
    
    # Create withdrawal request
    result = create_withdrawal_request(user_id, amount, asset, wallet_address, usd_amount)
    
    if not result["success"]:
        await update.message.reply_text(
            f"❌ Error creating withdrawal request: {result.get('error', 'Unknown error')}.\n\n"
            f"Please try again later or contact support."
        )
        return
    
    # Notify admins about the withdrawal request
    try:
        from admin import ADMIN_IDS
        await notify_admins_of_withdrawal(
            update,
            context, 
            ADMIN_IDS, 
            user_id, 
            result["request_id"], 
            amount, 
            asset, 
            wallet_address
        )
    except Exception as e:
        logger.error(f"Failed to notify admins: {e}")
    
    message = (
        f"✅ Withdrawal request submitted!\n\n"
        f"Amount: {amount} {asset} (≈ ${usd_amount:.2f})\n"
        f"To: `{wallet_address}`\n\n"
        f"Your request has been sent to our administrators for processing. "
        f"Usually it Takes 72 hours to process.\n\n"
        f"You will be notified once it's approved.\n\n"
        f"New balance: ${result['new_balance']:.2f}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📊 My Account", callback_data="account")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# Handler for general message processing
async def handle_payment_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process messages related to payment system."""
    # Check what we're expecting
    if context.user_data.get('expecting_crypto_deposit', False):
        await process_deposit_amount(update, context)
        return True
    
    if context.user_data.get('expecting_wallet_address', False):
        await process_wallet_address(update, context)
        return True
    
    if context.user_data.get('expecting_crypto_withdrawal', False):
        await process_withdrawal_amount(update, context)
        return True
    
    # If we're not processing anything payment-related
    return False

# Setup function to add handlers to the application
def add_payment_handlers(application):
    """Add payment system handlers to the main application."""
    # Override deposit and withdraw handlers
    application.add_handler(CallbackQueryHandler(deposit_handler, pattern="^deposit$"))
    application.add_handler(CallbackQueryHandler(withdraw_handler, pattern="^withdraw$"))
    
    # Add asset selection handlers
    application.add_handler(CallbackQueryHandler(deposit_asset_selected, pattern="^deposit_asset_"))
    application.add_handler(CallbackQueryHandler(withdraw_asset_selected, pattern="^withdraw_asset_"))
    
    # Add invoice status check handler
    application.add_handler(CallbackQueryHandler(check_deposit_status, pattern="^check_deposit_"))
    
    # Set up database tables
    setup_payment_database()
    
    # Test API connection
    if not test_api_connection():
        logger.error("Failed to connect to Crypto Pay API. Payment system may not function correctly.")
    
    logger.info("Payment system initialized")

# This function can be called from main.py's handle_message to check if a message should be handled by the payment system
def should_handle_payment_message(context):
    """Check if the current message should be handled by the payment system."""
    return (
        context.user_data.get('expecting_crypto_deposit', False) or
        context.user_data.get('expecting_wallet_address', False) or
        context.user_data.get('expecting_crypto_withdrawal', False)
    )