import logging
import sqlite3
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler
)

logger = logging.getLogger(__name__)

# Daily bonus configuration based on tier
BONUS_TIERS = {
    'Bronze': (0.5, 1.0),
    'Silver': (1.5, 3.0),
    'Gold': (2.5, 5.0),
    'Diamond': (4.5, 5.0)
}
# Maximum free bonus amount - after this, users need to deposit to keep getting bonuses
MAX_FREE_BONUS_TOTAL = 25.0
MIN_REQUIRED_DEPOSIT = 50.0

def setup_daily_bonus_database():
    """Set up database tables for daily bonus system."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    # Create daily_claims table to track when users claimed their daily bonus
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS daily_claims (
        user_id INTEGER PRIMARY KEY,
        last_claim_date TEXT,
        total_claimed REAL DEFAULT 0.0,
        eligible_for_free_bonus BOOLEAN DEFAULT 1,
        streak_days INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    
    conn.commit()
    conn.close()

def calculate_bonus(tier, streak_days):
    """Return a random bonus amount within the tier range, boosted by streak."""
    min_bonus, max_bonus = BONUS_TIERS.get(tier, BONUS_TIERS['Bronze'])
    base = random.uniform(min_bonus, max_bonus)

    # Boost up to 20% based on streak (max at 7 days)
    boost_percent = min(streak_days, 7) * 0.03  # 3% per day
    total = base * (1 + boost_percent)
    
    return round(min(total, max_bonus), 3)

def get_user_claim_status(user_id):
    """Get information about a user's daily claim status."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM daily_claims WHERE user_id = ?", (user_id,))
    claim_data = cursor.fetchone()
    
    # Also get user deposit info to check eligibility
    cursor.execute("SELECT deposit_amount FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    conn.close()
    
    # Default values if no previous claims
    if not claim_data:
        return {
            'user_id': user_id,
            'last_claim_date': None,
            'total_claimed': 0.0,
            'eligible_for_free_bonus': True,
            'deposit_amount': user_data[0] if user_data else 0.0
        }
    
    return {
        'user_id': claim_data[0],
        'last_claim_date': datetime.strptime(claim_data[1], "%Y-%m-%d %H:%M:%S") if claim_data[1] else None,
        'total_claimed': claim_data[2],
        'eligible_for_free_bonus': bool(claim_data[3]),
        'deposit_amount': user_data[0] if user_data else 0.0
    }

def can_claim_daily_bonus(user_id):
    """Check if a user can claim their daily bonus."""
    claim_status = get_user_claim_status(user_id)
    
    # First-time claimer
    if claim_status['last_claim_date'] is None:
        return True
    
    # Check if 24 hours have passed since last claim
    now = datetime.now()
    time_since_last_claim = now - claim_status['last_claim_date']
    
    if time_since_last_claim < timedelta(hours=24):
        return False
    
    # Check if user has reached the free bonus limit and needs to deposit
    if claim_status['total_claimed'] >= MAX_FREE_BONUS_TOTAL and claim_status['eligible_for_free_bonus']:
        # If user has deposited enough, they can continue getting bonuses
        if claim_status['deposit_amount'] >= MIN_REQUIRED_DEPOSIT:
            # Update their eligibility status
            conn = sqlite3.connect('referral_bot.db')
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE daily_claims SET eligible_for_free_bonus = 0 WHERE user_id = ?", 
                (user_id,)
            )
            conn.commit()
            conn.close()
            return True
        else:
            # User needs to deposit more
            return False
    
    return True

def get_bonus_amount(user_id, streak_days):
    """Get the randomized bonus amount based on user's tier and streak."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT tier FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    
    conn.close()
    
    tier= user_data[0] if user_data else 'Bronze' # Default to Bronze tier
    
    return calculate_bonus(tier, streak_days)

def update_last_claim(user_id, bonus_amount):
    """Update the user's streak, claim time, and earnings."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Check if user already exists in daily_claims
    cursor.execute("SELECT last_claim_date, streak_days FROM daily_claims WHERE user_id = ?", (user_id,))
    record = cursor.fetchone()
    
    if record:
        last_claim = datetime.strptime(record[0], "%Y-%m-%d %H:%M:%S") if record[0] else None
        current_streak = record[1] or 0
        
        if last_claim and last_claim.date() == (now - timedelta(days=1)).date():
            new_streak = current_streak + 1
        else:
            new_streak = 1

        cursor.execute(
            "UPDATE daily_claims SET last_claim_date = ?, total_claimed = total_claimed + ?, streak_days = ? WHERE user_id = ?",
            (now, bonus_amount, new_streak, user_id)
        )
    else:
        new_streak = 1
        cursor.execute(
            "INSERT INTO daily_claims (user_id, last_claim_date, total_claimed, streak_days) VALUES (?, ?, ?, ?)",
            (user_id, now, bonus_amount, new_streak)
        )
    
    # Update user's earning amount
    # Update user earnings
    cursor.execute("SELECT earning_amount FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        new_earnings = result[0] + bonus_amount
        cursor.execute("UPDATE users SET earning_amount = ? WHERE user_id = ?", (new_earnings, user_id))
    
    conn.commit()
    conn.close()

def add_daily_bonus_transaction(user_id, amount):
    """Record a daily bonus transaction."""
    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute(
        "INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, amount, "daily_bonus", timestamp)
    )
    
    conn.commit()
    conn.close()

async def check_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if daily bonus is available and show claim button if it is."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    can_claim = can_claim_daily_bonus(user_id)
    claim_status = get_user_claim_status(user_id)
    
    # Calculate time remaining until next claim if needed
    time_remaining_str = ""
    if claim_status['last_claim_date'] is not None:
        now = datetime.now()
        next_claim_time = claim_status['last_claim_date'] + timedelta(hours=24)
        
        if next_claim_time > now:
            time_diff = next_claim_time - now
            hours = time_diff.seconds // 3600
            minutes = (time_diff.seconds % 3600) // 60
            time_remaining_str = f"⏳ Next claim available in: {hours}h {minutes}m"
    claim_status = get_user_claim_status(user_id)
    streak_days = claim_status.get("streak_days", 0)
    bonus_amount = get_bonus_amount(user_id, streak_days)
    
    # Check if user needs to deposit more
    deposit_required = False
    if claim_status['total_claimed'] >= MAX_FREE_BONUS_TOTAL and claim_status['eligible_for_free_bonus']:
        if claim_status['deposit_amount'] < MIN_REQUIRED_DEPOSIT:
            deposit_required = True
    tier = claim_status.get("tier", "Bronze")
    bonus_range = BONUS_TIERS.get(tier, BONUS_TIERS["Bronze"])
    message = f"🎁 *Daily Bonus*\n\n"
    message += f"Your tier: *{tier}*\n"
    message += f"Your daily bonus amount: ${bonus_range[0]:.2f} - ${bonus_range[1]:.2f}\n"
    message += f"Total claimed so far: ${claim_status['total_claimed']:.2f}\n\n"
    
    if deposit_required:
        message += (f"⚠️ You've reached the maximum free bonus limit of ${MAX_FREE_BONUS_TOTAL:.2f}.\n"
                   f"To continue receiving daily bonuses, please deposit at least ${MIN_REQUIRED_DEPOSIT:.2f}.\n\n")
    
    if time_remaining_str:
        message += f"{time_remaining_str}\n\n"
    
    keyboard = []
    
    if can_claim and not deposit_required:
        keyboard.append([InlineKeyboardButton("🎁 Claim Daily Bonus", callback_data="claim_bonus")])
    elif deposit_required:
        keyboard.append([InlineKeyboardButton("💰 Make Deposit", callback_data="deposit")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def claim_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the daily bonus claim."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    can_claim = can_claim_daily_bonus(user_id)
    
    if not can_claim:
        await query.edit_message_text(
            "Sorry, you're not eligible to claim a bonus right now. Please try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="daily_bonus")]])
        )
        return
    
    claim_status = get_user_claim_status(user_id)
    streak_days = claim_status.get("streak_days", 0)
    bonus_amount = get_bonus_amount(user_id, streak_days)
    
    message = (f"🎉 Congratulations! You've claimed your daily bonus of ${bonus_amount:.2f}!\n\n"
              f"The bonus has been added to your balance. Come back in 24 hours to claim again!")
    
    keyboard = [
        [InlineKeyboardButton("📊 My Account", callback_data="account")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

# Function to add handlers to main application
def add_daily_bonus_handlers(application):
    """Add daily bonus handlers to the main application."""
    application.add_handler(CallbackQueryHandler(check_daily_bonus, pattern="^daily_bonus$"))
    application.add_handler(CallbackQueryHandler(claim_daily_bonus, pattern="^claim_bonus$"))
    
    # Set up database tables
    setup_daily_bonus_database()
    
    logger.info("Daily bonus system initialized")