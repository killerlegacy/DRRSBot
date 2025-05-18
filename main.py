import logging
import sqlite3
import os
from datetime import datetime
from daily_bonus import add_daily_bonus_handlers ,check_daily_bonus,claim_daily_bonus
from payment_method import add_payment_handlers, handle_payment_message, deposit_handler, withdraw_handler
from admin import add_admin_handlers
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Configure basic logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('TELEGRAMBOTAPI')   # Replace with your actual token
APP_NAME = os.getenv("APP_NAME")   

# Webhook setup
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = f"/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{APP_NAME}.onrender.com{WEBHOOK_PATH}"

# Tier configuration
TIERS = {
    'Bronze': {'min_deposit': 0, 'referral_bonus': 5},       # 5% referral bonus
    'Silver': {'min_deposit': 50, 'referral_bonus': 15},    # 15% referral bonus
    'Gold': {'min_deposit': 150, 'referral_bonus': 25}, # 25% referral bonus
    'Diamond': {'min_deposit':500, 'referral_bonus':40}   # 40% referral bonus           
}

# Database setup
def setup_database():
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        referrer_id INTEGER,
        deposit_amount REAL DEFAULT 0.0,
        earning_amount REAL DEFAULT 0.0,
        tier TEXT DEFAULT 'Bronze',
        join_date TEXT,
        FOREIGN KEY (referrer_id) REFERENCES users(user_id)
    )
    ''')
    
    # Create transactions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        type TEXT,
        timestamp TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Helper functions
def get_user_tier(deposit_amount):
    """Determine user tier based on deposit amount."""
    if deposit_amount >= TIERS['Diamond']['min_deposit']:
        return 'Diamond'
    elif deposit_amount >= TIERS['Gold']['min_deposit']:
        return 'Gold'
    elif deposit_amount >= TIERS['Silver']['min_deposit']:
        return 'Silver'
    else:
        return 'Bronze'

def update_user_tier(user_id, deposit_amount,conn=None, cursor=None):
    """Update user tier based on new deposit amount."""
    close_conn = False
    if conn is None or cursor is None:
        conn = sqlite3.connect("referral_bot.db", check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        close_conn = True
    
    new_tier = get_user_tier(deposit_amount)
    
    cursor.execute(
        "UPDATE users SET deposit_amount = ?, tier = ? WHERE user_id = ?", 
        (deposit_amount, new_tier, user_id)
    )
    
    if close_conn:
        conn.commit()
        conn.close()
    
    return new_tier

def add_transaction(user_id, amount, transaction_type,conn=None, cursor=None):
    """Record a transaction."""
    close_conn = False
    if conn is None or cursor is None:
        conn = sqlite3.connect("referral_bot.db", check_same_thread=False, timeout=10)
        cursor = conn.cursor()
        close_conn = True
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute(
        "INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, amount, transaction_type, timestamp)
    )
    
    if close_conn:
        conn.commit()
        conn.close()

def get_user_info(user_id):
    """Get user information from database."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    
    
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'referrer_id': user[2],
            'deposit_amount': user[3],
            'earning_amount': user[4],
            'tier': user[5],
            'join_date': user[6]
        }
    return None

def get_referrals(user_id):
    """Get users referred by this user."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id, username, tier, deposit_amount FROM users WHERE referrer_id = ?", (user_id,))
    referrals = cursor.fetchall()
    
    conn.close()
    return referrals

# 5 Helper funtion to stramline the bot workiing and database flow.

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command - entry point for the bot."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Check if user already exists
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    existing_user = cursor.fetchone()
    
    # Handle referral link
    referrer_id = None
    if len(context.args) > 0:
        try:
            referrer_id = int(context.args[0])
            # Verify referrer exists
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (referrer_id,))
            if not cursor.fetchone():
                referrer_id = None
        except ValueError:
            referrer_id = None
    
    if not existing_user:
        # Register new user
        join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO users (user_id, username, referrer_id, join_date) VALUES (?, ?, ?, ?)",
            (user_id, username, referrer_id, join_date)
        )
        
        welcome_message = "🎉 Welcome to the DRRS --> Daily Reward & Referral Bot! 🎉\n\n"
        
        if referrer_id:
            welcome_message += "You were referred by a friend! You'll both earn bonuses when you make deposits.\n\n"
        
        conn.commit()
    else:
        welcome_message = "Welcome back to the DRRS --> Daily Reward & Referral Bot!\n\n"
    
    conn.close()
    
    # Create referral link
    referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
    
    # Prepare inline keyboard
    keyboard = [
        [InlineKeyboardButton("💰 Make Deposit", callback_data="deposit"),
        InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
        InlineKeyboardButton("👥 My Referrals", callback_data="referrals")],
        [InlineKeyboardButton("📊 My Account", callback_data="account")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_message += (
            f"• Use the buttons below to navigate\n\n"
            f"• Earn higher bonuses by upgrading your tier!\n\n"
        )
    welcome_message += f"• Share your referral link:\n `{referral_link}`"
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user account information. (My Account Button)"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_info = get_user_info(user_id)
    
    if not user_info:
        await query.edit_message_text("User not found. Please restart the bot with /start")
        return
    
    referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
    
    account_info = (
        f"📊 *Account Information*\n\n"
        f"User ID: `{user_id}`\n"
        f"Username: {user_info['username']}\n"
        f"Current Tier: {user_info['tier']}\n"
        f"Total Deposits: ${user_info['deposit_amount']:.2f}\n"
        f"Total Earnings: ${user_info['earning_amount']:.2f}\n"
        f"Available Balance: ${user_info['earning_amount'] + user_info['deposit_amount']:.2f}\n"
        f"Join Date: {user_info['join_date']}\n\n"
        f"Your Referral Link:\n`{referral_link}`\n\n"
        f"*Tier Benefits:*\n"
        f"• Bronze (${TIERS['Bronze']['min_deposit']}+): {TIERS['Bronze']['referral_bonus']}% referral bonus\n"
        f"• Silver (${TIERS['Silver']['min_deposit']}+): {TIERS['Silver']['referral_bonus']}% referral bonus\n"
        f"• Gold (${TIERS['Gold']['min_deposit']}+): {TIERS['Gold']['referral_bonus']}% referral bonus\n"
        f"• Diamond (${TIERS['Diamond']['min_deposit']}+): {TIERS['Diamond']['referral_bonus']}% referral bonus"
    )
    
    keyboard = [
        [InlineKeyboardButton("💰 Make Deposit", callback_data="deposit"),
        InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
        InlineKeyboardButton("👥 My Referrals", callback_data="referrals")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(account_info, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user's referrals."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    referrals = get_referrals(user_id)
    
    if not referrals:
        referral_message = "You haven't referred any users yet. Share your referral link to start earning bonuses!"
    else:
        referral_message = "👥 *Your Referrals:*\n\n"
        for i, (ref_id, username, tier, amount) in enumerate(referrals, 1):
            referral_message += f"{i}. {username} - Tier: {tier} - Deposits: ${amount:.2f}\n"
    
    user_info = get_user_info(user_id)
    tier = user_info['tier'] if user_info else 'Bronze'
    bonus_rate = TIERS[tier]['referral_bonus']
    
    referral_message += f"\n\n*Your Referral Bonus Rate: {bonus_rate}%*"
    referral_message += f"\n\nWhen your referrals make deposits, you earn {bonus_rate}% of their deposit amount!"
    
    referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
    referral_message += f"\n\nYour Referral Link:\n`{referral_link}`"
    
    keyboard = [
        [InlineKeyboardButton("💰 Make Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📊 My Account", callback_data="account")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(referral_message, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user messages for deposits or withdrawals."""
    await handle_payment_message(update, context)
    

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    
    if query.data == "deposit":
        await deposit_handler(update, context)
    elif query.data == "withdraw":
        await withdraw_handler(update, context)
    elif query.data == "referrals":
        await handle_referrals(update, context)
    elif query.data == "account":
        await handle_account(update, context)
    elif query.data == "daily_bonus":
        await check_daily_bonus(update, context)
    elif query.data == "claim_bonus":
        await claim_daily_bonus(update, context)
    elif query.data == "back_to_main":
        # Return to main menu
        user_id = query.from_user.id
    
        referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
        
        keyboard = [
            [InlineKeyboardButton("💰 Make Deposit", callback_data="deposit"),
            InlineKeyboardButton("🎁 Daily Bonus", callback_data="daily_bonus")],
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
            InlineKeyboardButton("👥 My Referrals", callback_data="referrals")],
            [InlineKeyboardButton("📊 My Account", callback_data="account")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = f"Welcome to the Deposit & Referral Bot!\n\n• Use the buttons below to navigate\n• Share your referral link to earn bonuses: {referral_link}\n• Earn higher bonuses by upgrading your tier!"
        
        await query.edit_message_text(welcome_message, reply_markup=reply_markup)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")
    if update and update.effective_user.id:
        try:
            await context.bot.send_message(chat_id=update.effective_user.id, text="⚠️ An error occurred. Please try again later.")
        except Exception:
            pass


def main():
    # Set up the database
    setup_database()
    
    # Create the application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    add_daily_bonus_handlers(application)
    add_payment_handlers(application)
    add_admin_handlers(application)
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_error_handler(error_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # # Start the bot
    # logger.info("Starting bot...")
    # application.run_polling()
    # Run as webhook
    logger.info("Starting bot with webhook...")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
    )
if __name__ == '__main__':
    main()