import sqlite3
import requests
from payment_method import HEADERS, CRYPTO_PAY_API_BASE
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

ADMIN_IDS = [1075995888]  # <--- Replace with your Telegram ID

def add_admin_handlers(application):
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(show_pending_withdrawals, pattern="^admin_withdrawals$"))
    application.add_handler(CallbackQueryHandler(show_pending_invoices, pattern="^admin_invoices$"))
    application.add_handler(CallbackQueryHandler(handle_approve_withdrawal, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(handle_reject_withdrawal, pattern="^reject_"))
    application.add_handler(CallbackQueryHandler(handle_delete_invoice, pattern="^delete_invoice_"))


# Admin Panel Entry
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ You are not authorized to access the admin panel.")
        return

    keyboard = [
        [InlineKeyboardButton("📄 Pending Withdrawals", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("💰 Unpaid Invoices", callback_data="admin_invoices")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔐 *Admin Panel*", parse_mode="Markdown", reply_markup=reply_markup)

# Show Pending Withdrawals
async def show_pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT request_id, user_id, amount, asset, wallet_address FROM withdrawal_requests WHERE status = 'pending'")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text("✅ No pending withdrawals.")
        return

    messages = []
    for row in rows:
        request_id, user_id, amount, asset, wallet = row
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{request_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{request_id}")
            ]
        ])
        await query.message.reply_text(
            f"🆔 Request ID: `{request_id}`\n👤 User: `{user_id}`\n💸 Amount: {amount} {asset}\n🏦 Wallet: `{wallet}`",
            reply_markup=buttons,
            parse_mode="Markdown"
        )

    await query.edit_message_text("📄 Showing pending withdrawal requests...")

# Show Pending Invoices
async def show_pending_invoices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT invoice_id, user_id, amount, asset, status FROM payment_invoices WHERE status = 'active'")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text("✅ No unpaid deposit invoices.")
        return
    await query.edit_message_text("📄 Showing unpaid invoices below:")
    
    for row in rows[:10]:
        invoice_id, user_id, amount, asset, status = row
    msg = (
        f"🧾 Invoice ID: `{invoice_id}`\n"
        f"👤 User: `{user_id}`\n"
        f"💰 Amount: {amount} {asset} - *{status}*"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_invoice_{invoice_id}")]
    ])
    await query.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")


# Approve Withdrawal
async def handle_approve_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    request_id = int(query.data.split("_")[1])

    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount, asset, wallet_address FROM withdrawal_requests WHERE request_id = ? AND status = 'pending'", (request_id,))
    row = cursor.fetchone()

    if not row:
        await query.edit_message_text("⚠️ Request not found or already processed.")
        return

    user_id, amount, asset, wallet = row

    cursor.execute(
        "UPDATE withdrawal_requests SET status = 'completed', processed_at = datetime('now') WHERE request_id = ?",
        (request_id,)
    )
    conn.commit()
    conn.close()

    await query.edit_message_text(f"✅ Withdrawal request #{request_id} approved.")

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Your withdrawal of {amount} {asset} to `{wallet}` has been approved and processed.",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Failed to notify user {user_id}: {e}")

# Delete invoice (admin only)
async def handle_delete_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("❌ You are not authorized to delete invoices.")
        return

    invoice_id = query.data.split("_")[2]

    # Delete from Crypto Pay API
    response = requests.post(
        f"{CRYPTO_PAY_API_BASE}/deleteInvoice",
        headers=HEADERS,
        json={"invoice_id": int(invoice_id)}
    )

    if response.status_code == 200 and response.json().get("ok"):
        # Update DB to reflect deletion
        conn = sqlite3.connect("referral_bot.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE payment_invoices SET status = 'deleted' WHERE invoice_id = ?", (invoice_id,))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"✅ Invoice `{invoice_id}` has been successfully deleted.", parse_mode="Markdown")
    else:
        await query.edit_message_text(f"❌ Failed to delete invoice `{invoice_id}`.\n\nError: {response.text}", parse_mode="Markdown")

# Reject Withdrawal
async def handle_reject_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    request_id = int(query.data.split("_")[1])

    conn = sqlite3.connect('referral_bot.db')
    cursor = conn.cursor()

    # Return funds to user balance
    cursor.execute("SELECT user_id, amount FROM withdrawal_requests WHERE request_id = ? AND status = 'pending'", (request_id,))
    row = cursor.fetchone()

    if not row:
        await query.edit_message_text("⚠️ Request not found or already processed.")
        return

    user_id, amount = row

    cursor.execute("UPDATE users SET earning_amount = earning_amount + ? WHERE user_id = ?", (amount, user_id))
    cursor.execute("UPDATE withdrawal_requests SET status = 'rejected', processed_at = datetime('now') WHERE request_id = ?", (request_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"❌ Withdrawal request #{request_id} rejected and funds returned to user.")

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Your withdrawal request of ${amount} has been rejected. Funds returned to your balance."
        )
    except Exception as e:
        print(f"Failed to notify user {user_id}: {e}")
