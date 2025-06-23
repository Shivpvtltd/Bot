import firebase_admin
from firebase_admin import credentials, db
import re
import random
import string
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events, Button
from telethon.tl.types import MessageMediaPhoto

# ================= CONFIGURATION =================
API_ID = 27298720  # Replace with your API ID
API_HASH = '4a8ba03b4a014f39a6a9289e4bcfcef2'  # Replace with your API HASH
BOT_TOKEN = '8008957414:AAGqxfq60V6fKaZhwDIHdzfZD9iENzRtVm0'  # Replace with your bot token
ADMIN_ID = 5814359834  # Replace with your Telegram user ID

# Firebase configuration
FIREBASE_CREDENTIALS = 'firebase_credentials.json'  # Path to your Firebase service account JSON
FIREBASE_URL = 'https://admin-dec31-default-rtdb.asia-southeast1.firebasedatabase.app/'  # Replace with your Firebase URL
# =================================================

# ============== FIREBASE INITIALIZATION ==============
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_CREDENTIALS)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_URL
    })

# Database references
users_ref = db.reference('users')
tasks_ref = db.reference('tasks')
withdrawal_requests_ref = db.reference('withdrawal_requests')
submissions_ref = db.reference('submissions')
# =====================================================

# ============== GLOBAL VARIABLES ================
user_states = {}  # Temporary user state storage
MIN_WITHDRAWAL = 30  # Minimum withdrawal amount in INR
SESSION_TIMEOUT = 600  # 10 minutes in seconds

# UPI Validation Regex
UPI_REGEX = re.compile(r'^[a-zA-Z0-9._-]+@[a-zA-Z0-9]+$')

# Button layouts
HOME_BUTTON = [[Button.text('👀 View All', resize=True)]]

MAIN_MENU_BUTTONS = [
    [Button.text('📝 TASKS', resize=True)],
    [Button.text('💼 WALLET', resize=True), Button.text('💸 WITHDRAW', resize=True)],
    [Button.text('👥 JOIN TG GROUP', resize=True)]
]

BACK_BUTTON = [[Button.text('🔙 Back', resize=True)]]
CANCEL_BUTTON = [[Button.text('🛑 Cancel', resize=True)]]

# =================================================

# ============== HELPER FUNCTIONS ================
def generate_id(prefix='WD', length=6):
    """Generate a random ID with given prefix and length"""
    chars = string.digits
    return prefix + ''.join(random.choice(chars) for _ in range(length))

def get_user_ref(user_id):
    """Get Firebase reference for a user"""
    return users_ref.child(str(user_id))

def get_user_balance(user_id):
    """Get user's wallet balance from Firebase"""
    user = get_user_ref(user_id).get()
    return user.get('wallet_balance', 0) if user else 0

def update_user_balance(user_id, amount):
    """Update user's wallet balance in Firebase"""
    current_balance = get_user_balance(user_id)
    get_user_ref(user_id).update({
        'wallet_balance': current_balance + amount,
        'last_updated': datetime.now(timezone.utc).isoformat()
    })

def add_withdrawal_request(user_id, amount, upi_id):
    """Add withdrawal request to Firebase"""
    request_id = generate_id()
    request_data = {
        'request_id': request_id,
        'amount': amount,
        'upi_id': upi_id,
        'status': 'pending',
        'requested_time': datetime.now(timezone.utc).isoformat()
    }
    withdrawal_requests_ref.child(str(user_id)).child(request_id).set(request_data)
    return request_id

def get_withdrawal_history(user_id):
    """Get user's withdrawal history from Firebase"""
    requests = withdrawal_requests_ref.child(str(user_id)).get()
    if not requests:
        return []
    
    if isinstance(requests, dict):
        return [requests]
    return requests

def add_to_withdrawal_history(user_id, amount, status, request_id=None):
    """Add to user's withdrawal history"""
    history_ref = get_user_ref(user_id).child('withdrawal_history')
    history_ref.push().set({
        'amount': amount,
        'status': status,
        'request_id': request_id or generate_id(),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

def get_tasks_for_today():
    """Get tasks added today"""
    today = datetime.now(timezone.utc).date().isoformat()
    tasks = tasks_ref.order_by_child('date').equal_to(today).get()
    return tasks if tasks else {}

def get_task_count_for_today():
    """Get count of tasks added today"""
    return len(get_tasks_for_today())

def get_task_by_id(task_id):
    """Get task by ID"""
    return tasks_ref.child(task_id).get()

def get_task_by_download_link(download_link):
    """Get task by download link"""
    if not download_link:
        return None
    tasks = tasks_ref.order_by_child('download_link').equal_to(download_link).get()
    return next(iter(tasks.values())) if tasks else None

def get_task_for_today(task_number):
    """Get task by number (1-based index) from today's tasks"""
    tasks = list(get_tasks_for_today().values())
    if not tasks or task_number < 1 or task_number > len(tasks):
        return None
    return tasks[task_number - 1]

def check_duplicate_submission(user_id, task_id, image_id):
    """Check if this image was already submitted for this task"""
    submissions = submissions_ref.child(str(user_id)).child(str(task_id)).get()
    if not submissions:
        return False
    return any(sub.get('image_id') == image_id for sub in submissions.values())

async def notify_admin(message):
    """Notify admin about important events"""
    try:
        await client.send_message(
            ADMIN_ID,
            f"⚠️ *Admin Notification* ⚠️\n\n{message}",
            parse_mode='md'
        )
    except Exception as e:
        print(f"Failed to notify admin: {str(e)}")

def clean_user_state(user_id):
    """Completely clean user state"""
    if user_id in user_states:
        del user_states[user_id]

def check_session_timeout(user_id):
    """Check if user session has timed out"""
    if user_id not in user_states:
        return True
    
    if 'last_activity' not in user_states[user_id]:
        return True
    
    last_activity = datetime.fromisoformat(user_states[user_id]['last_activity'])
    return (datetime.now(timezone.utc) - last_activity) > timedelta(seconds=SESSION_TIMEOUT)

def update_user_activity(user_id):
    """Update user's last activity time"""
    if user_id not in user_states:
        user_states[user_id] = {}
    user_states[user_id]['last_activity'] = datetime.now(timezone.utc).isoformat()
# =================================================

# =============== BOT INITIALIZATION ==============
client = TelegramClient('earnbot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
# =================================================

# ================== BOT EVENTS ===================
@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Handle /start command from users"""
    try:
        user_id = event.sender_id
        clean_user_state(user_id)
        
        # Initialize user in Firebase if not exists
        if not get_user_ref(user_id).get():
            get_user_ref(user_id).set({
                'user_id': user_id,
                'username': event.sender.username or '',
                'wallet_balance': 0,
                'last_updated': datetime.now(timezone.utc).isoformat()
            })
        else:
            # Update username if changed
            get_user_ref(user_id).update({
                'username': event.sender.username or '',
                'last_updated': datetime.now(timezone.utc).isoformat()
            })
        
        welcome_msg = """
🌟 *Welcome to EarnBot* 🌟

Earn money by completing simple tasks!

🔹 Click *👀 View All* to see available options
🔹 Complete tasks and withdraw to your UPI
🔹 Fast and reliable payments

Start earning now! 💰
"""
        await event.respond(welcome_msg, buttons=HOME_BUTTON, parse_mode='md')
        update_user_activity(user_id)
    except Exception as e:
        await notify_admin(f"Error in /start: {str(e)}")
        await event.respond(
            "⚠️ Temporary issue. Please try again later.",
            buttons=HOME_BUTTON
        )

@client.on(events.NewMessage(pattern='👀 View All'))
async def view_all(event):
    """Show main menu options"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        await event.respond(
            "📌 *Main Menu* - Select an option:",
            buttons=MAIN_MENU_BUTTONS,
            parse_mode='md'
        )
        update_user_activity(user_id)
    except Exception as e:
        await event.respond(
            "⚠️ Couldn't load menu. Please try again.",
            buttons=HOME_BUTTON
        )

@client.on(events.NewMessage(pattern='🔙 Back'))
async def back_to_menu(event):
    """Return to main menu"""
    user_id = event.sender_id
    if check_session_timeout(user_id):
        clean_user_state(user_id)
        await event.respond(
            "⚠️ Session expired. Please use /start again.",
            buttons=HOME_BUTTON
        )
        return
        
    await view_all(event)
    update_user_activity(user_id)

@client.on(events.NewMessage(pattern='📝 TASKS'))
async def show_tasks(event):
    """Show today's tasks to user"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        tasks = get_tasks_for_today()
        
        if not tasks:
            await event.respond(
                "⚠️ *No Tasks Available Today*\n\n"
                "There are no tasks available for today.\n"
                "Please check again later.",
                buttons=BACK_BUTTON,
                parse_mode='md'
            )
            return
        
        # Start with first task
        user_states[user_id] = {
            'state': 'viewing_tasks',
            'current_task': 1,
            'last_activity': datetime.now(timezone.utc).isoformat()
        }
        
        await send_task_message(event, user_id, 1)
        update_user_activity(user_id)
    except Exception as e:
        await notify_admin(f"Error in show_tasks: {str(e)}")
        await event.respond(
            "⚠️ Couldn't load tasks. Please try again.",
            buttons=BACK_BUTTON
        )

async def send_task_message(event, user_id, task_number):
    """Send task message for the given task number"""
    tasks = list(get_tasks_for_today().values())
    if not tasks or task_number < 1 or task_number > len(tasks):
        await event.respond(
            "⚠️ Task not found.",
            buttons=BACK_BUTTON
        )
        return
    
    task = tasks[task_number - 1]
    task_id = list(get_tasks_for_today().keys())[task_number - 1]
    
    task_msg = f"""
📋 *Task {task_number}*

📝 *Description*: {task.get('description', 'No description available')}
💰 *Reward*: ₹{task.get('reward', 0)}
"""
    
    # Create action buttons (inline buttons)
    action_buttons = []
    
    if task.get('download_link'):
        action_buttons.append(Button.url('🔹 Download', task['download_link']))
    
    if task.get('group_link'):
        action_buttons.append(Button.url('🔹 Join Group', task['group_link']))
    
    if task.get('tutorial_link'):
        action_buttons.append(Button.url('🔹 Tutorial', task['tutorial_link']))
    else:
        # Use a text button instead of inline for no tutorial
        action_buttons.append(Button.text('🔹 Tutorial (Not Available)'))
    
    # Create navigation buttons (text buttons)
    nav_buttons = []
    task_count = len(tasks)
    
    if task_count > 1:
        row = []
        if task_number > 1:
            row.append(Button.text('⬅️ Previous'))
        if task_number < task_count:
            row.append(Button.text('➡️ Next'))
        nav_buttons.append(row)
    
    nav_buttons.append([Button.text('🔙 Back')])
    
    # Store task ID in user state
    user_states[user_id]['current_task_id'] = task_id
    
    try:
        # First send the task message with inline buttons (if any)
        if task.get('image_file_id'):
            if action_buttons:
                await event.respond(
                    task_msg,
                    file=task['image_file_id'],
                    buttons=[action_buttons],
                    parse_mode='md'
                )
            else:
                await event.respond(
                    task_msg,
                    file=task['image_file_id'],
                    parse_mode='md'
                )
        else:
            if action_buttons:
                await event.respond(
                    task_msg,
                    buttons=[action_buttons],
                    parse_mode='md'
                )
            else:
                await event.respond(
                    task_msg,
                    parse_mode='md'
                )
        
        # Then send the navigation buttons in a separate message
        await event.respond(
            "👇 Use the buttons below to navigate:",
            buttons=nav_buttons,
            parse_mode='md'
        )
        
    except Exception as e:
        await event.respond(
            "⚠️ Couldn't load task. Please try again.",
            buttons=BACK_BUTTON
        )
        await notify_admin(f"Error sending task {task_number}: {str(e)}")
    
    update_user_activity(user_id)

@client.on(events.NewMessage(pattern='⬅️ Previous|➡️ Next'))
async def navigate_tasks(event):
    """Handle task navigation"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        if user_id not in user_states or user_states[user_id]['state'] != 'viewing_tasks':
            await event.respond(
                "⚠️ Session expired. Please select tasks again.",
                buttons=BACK_BUTTON
            )
            return
        
        current_task = user_states[user_id]['current_task']
        task_count = len(get_tasks_for_today())
        
        if event.raw_text == '⬅️ Previous' and current_task > 1:
            user_states[user_id]['current_task'] -= 1
        elif event.raw_text == '➡️ Next' and current_task < task_count:
            user_states[user_id]['current_task'] += 1
        
        await event.delete()
        await send_task_message(event, user_id, user_states[user_id]['current_task'])
        update_user_activity(user_id)
    except Exception as e:
        await event.respond(
            "⚠️ Couldn't navigate tasks. Please try again.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage(pattern='💼 WALLET'))
async def wallet_balance(event):
    """Show wallet balance and withdrawal history"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        balance = get_user_balance(user_id)
        history = get_withdrawal_history(user_id)
        
        history_msg = "📜 *Withdrawal History*:\n"
        if history:
            for item in history:
                if isinstance(item, dict):
                    status_emoji = "✅" if item.get('status') == 'paid' else "⏳" if item.get('status') == 'pending' else "❌"
                    reason = f" ({item.get('reason', '')})" if item.get('status') == 'rejected' else ""
                    request_id = f" (ID: {item.get('request_id', '')})" if item.get('request_id') else ""
                    history_msg += f"• ₹{item.get('amount', 0)}{request_id} – {status_emoji} {item.get('status', '').capitalize()}{reason}\n"
        else:
            history_msg += "No withdrawal history yet."
        
        await event.respond(
            f"💰 *Your Wallet Balance*: ₹{balance}\n\n{history_msg}",
            buttons=BACK_BUTTON,
            parse_mode='md'
        )
        update_user_activity(user_id)
    except Exception as e:
        await notify_admin(f"Error in wallet_balance: {str(e)}")
        await event.respond(
            "⚠️ Couldn't fetch wallet details. Please try again.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage(pattern='💸 WITHDRAW'))
async def withdraw_money(event):
    """Handle withdrawal process"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        balance = get_user_balance(user_id)
        
        if balance < MIN_WITHDRAWAL:
            await event.respond(
                f"⚠️ *Your balance too low ₹{balance}.*\n\n"
                f"You need ₹{MIN_WITHDRAWAL} to withdraw.",
                buttons=BACK_BUTTON,
                parse_mode='md'
            )
            return
        
        user_states[user_id] = {
            'state': 'awaiting_withdrawal_amount',
            'last_activity': datetime.now(timezone.utc).isoformat()
        }
        
        await event.respond(
            f"💸 *Withdraw Money*\n\n"
            f"Your current balance: ₹{balance}\n"
            f"Minimum withdrawal: ₹{MIN_WITHDRAWAL}\n\n"
            "Please enter the amount you want to withdraw:",
            buttons=CANCEL_BUTTON,
            parse_mode='md'
        )
        update_user_activity(user_id)
    except Exception as e:
        await notify_admin(f"Error in withdraw_money: {str(e)}")
        await event.respond(
            "⚠️ Couldn't process withdrawal. Please try again.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage(pattern='👥 JOIN TG GROUP'))
async def join_group(event):
    """Show Telegram group link"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        group_info = db.reference('group_info').get() or {}
        group_link = group_info.get('link', 'https://t.me/examplegroup')
        
        await event.respond(
            f"👥 *Join our Telegram Group*\n\n"
            f"Connect with other members and get updates:\n"
            f"👉 [Join Now]({group_link})",
            buttons=BACK_BUTTON,
            parse_mode='md',
            link_preview=True
        )
        update_user_activity(user_id)
    except Exception as e:
        await event.respond(
            "⚠️ Couldn't fetch group link. Please try again.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage(pattern='🛑 Cancel'))
async def cancel_operation(event):
    """Cancel current operation"""
    try:
        user_id = event.sender_id
        clean_user_state(user_id)
        await event.respond(
            "❌ Operation cancelled.",
            buttons=BACK_BUTTON
        )
        update_user_activity(user_id)
    except Exception as e:
        await event.respond(
            "⚠️ Couldn't cancel operation. Returning to menu.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage())
async def handle_messages(event):
    """Handle all incoming messages"""
    try:
        user_id = event.sender_id
        if check_session_timeout(user_id):
            clean_user_state(user_id)
            await event.respond(
                "⚠️ Session expired. Please use /start again.",
                buttons=HOME_BUTTON
            )
            return
            
        if event.sender_id == ADMIN_ID or event.raw_text in [
            '/start', '👀 View All', '📝 TASKS', '💼 WALLET', 
            '💸 WITHDRAW', '👥 JOIN TG GROUP', '🔙 Back', '🛑 Cancel',
            '⬅️ Previous', '➡️ Next'
        ]:
            update_user_activity(user_id)
            return
        
        if user_id in user_states and user_states[user_id]['state'] == 'awaiting_withdrawal_amount':
            try:
                amount = float(event.raw_text)
                balance = get_user_balance(user_id)
                
                if amount < MIN_WITHDRAWAL:
                    await event.respond(
                        f"⚠️ Minimum withdrawal is ₹{MIN_WITHDRAWAL}.\n"
                        "Please enter a valid amount:",
                        buttons=CANCEL_BUTTON,
                        parse_mode='md'
                    )
                    update_user_activity(user_id)
                    return
                
                if amount > balance:
                    await event.respond(
                        f"⚠️ You don't have enough balance.\n"
                        f"Your balance: ₹{balance}\n"
                        "Please enter a valid amount:",
                        buttons=CANCEL_BUTTON,
                        parse_mode='md'
                    )
                    update_user_activity(user_id)
                    return
                
                user_states[user_id] = {
                    'state': 'awaiting_upi',
                    'withdrawal_amount': amount,
                    'last_activity': datetime.now(timezone.utc).isoformat()
                }
                
                await event.respond(
                    "💳 *Enter UPI ID*\n\n"
                    "Please provide your UPI ID for payment.\n\n"
                    "➤ Valid formats:\n"
                    "• name@upi\n"
                    "• mobile@bankname\n\n"
                    "The ID must contain '@' symbol.",
                    buttons=CANCEL_BUTTON,
                    parse_mode='md'
                )
                update_user_activity(user_id)
            
            except ValueError:
                await event.respond(
                    "⚠️ Invalid amount. Please enter a number (e.g. 50):",
                    buttons=CANCEL_BUTTON,
                    parse_mode='md'
                )
                update_user_activity(user_id)
        
        elif user_id in user_states and user_states[user_id]['state'] == 'awaiting_upi':
            if not UPI_REGEX.match(event.raw_text.strip()):
                await event.respond(
                    "⚠️ *Invalid UPI ID Format*\n\n"
                    "Please provide a valid UPI ID in one of these formats:\n"
                    "• example@upi\n"
                    "• mobilenumber@bankname\n\n"
                    "The UPI ID must contain '@' symbol.\n\n"
                    "Please try again:",
                    buttons=CANCEL_BUTTON,
                    parse_mode='md'
                )
                update_user_activity(user_id)
                return
            
            amount = user_states[user_id]['withdrawal_amount']
            upi_id = event.raw_text.strip()
            
            request_id = add_withdrawal_request(user_id, amount, upi_id)
            
            username = f"@{event.sender.username}" if event.sender.username else "No Username"
            await notify_admin(
                f"💸 *New Withdrawal Request*\n\n"
                f"Request ID: {request_id}\n"
                f"User: {username}\n"
                f"ID: `{user_id}`\n"
                f"Amount: ₹{amount}\n"
                f"UPI ID: `{upi_id}`\n\n"
                "Reply with:\n"
                "✅ Paid\n"
                "❌ Reject (Reason)"
            )
            
            clean_user_state(user_id)
            
            await event.respond(
                f"✅ *Withdrawal Request Submitted!*\n\n"
                f"Request ID: {request_id}\n"
                "Your request has been sent for processing.\n"
                "You'll receive a notification when payment is completed.",
                buttons=BACK_BUTTON,
                parse_mode='md'
            )
            update_user_activity(user_id)
        
        elif user_id in user_states and user_states[user_id]['state'] == 'awaiting_task_number':
            if event.photo:
                try:
                    task_number = int(event.raw_text.strip())
                    tasks = list(get_tasks_for_today().values())
                    
                    if task_number < 1 or task_number > len(tasks):
                        await event.respond(
                            "⚠️ Invalid task number. Please enter a valid task number:",
                            buttons=CANCEL_BUTTON
                        )
                        update_user_activity(user_id)
                        return
                    
                    task_id = list(get_tasks_for_today().keys())[task_number - 1]
                    image_id = event.photo.id
                    
                    if check_duplicate_submission(user_id, task_id, image_id):
                        await event.respond(
                            "⚠️ You have already submitted this image for this task.",
                            buttons=BACK_BUTTON
                        )
                        clean_user_state(user_id)
                        return
                    
                    # Save the submission
                    submission_ref = submissions_ref.child(str(user_id)).child(str(task_id))
                    submission_ref.push().set({
                        'image_id': image_id,
                        'submitted_at': datetime.now(timezone.utc).isoformat()
                    })
                    
                    username = f"@{event.sender.username}" if event.sender.username else "No Username"
                    caption = (
                        f"🖼️ *New Submission Received*\n\n"
                        f"Task No: {task_number}\n"
                        f"User: {username}\n"
                        f"ID: `{user_id}`\n\n"
                        "Reply with:\n"
                        "✅ Approve\n"
                        "❌ Reject (Reason)"
                    )
                    
                    forwarded = await client.send_message(
                        ADMIN_ID,
                        caption,
                        file=event.photo,
                        parse_mode='md'
                    )
                    
                    user_states[user_id] = {
                        'state': 'submitted_screenshot',
                        'forwarded_msg_id': forwarded.id,
                        'task_number': task_number,
                        'last_activity': datetime.now(timezone.utc).isoformat()
                    }
                    
                    await event.respond(
                        f"✅ *Screenshot Received for Task {task_number}!*\n\n"
                        "Admin will review your submission shortly.\n"
                        "You'll be notified when approved.",
                        buttons=BACK_BUTTON,
                        parse_mode='md'
                    )
                    update_user_activity(user_id)
                except ValueError:
                    await event.respond(
                        "⚠️ Please enter a valid task number:",
                        buttons=CANCEL_BUTTON
                    )
                    update_user_activity(user_id)
            else:
                await event.respond(
                    "⚠️ Please send a screenshot after entering the task number:",
                    buttons=CANCEL_BUTTON
                )
                update_user_activity(user_id)
        
        elif event.photo:
            if user_id in user_states and user_states[user_id]['state'] == 'viewing_tasks':
                task_number = user_states[user_id]['current_task']
                task_id = user_states[user_id]['current_task_id']
                image_id = event.photo.id
                
                if check_duplicate_submission(user_id, task_id, image_id):
                    await event.respond(
                        "⚠️ You have already submitted this image for this task.",
                        buttons=BACK_BUTTON
                    )
                    return
                
                # Save the submission
                submission_ref = submissions_ref.child(str(user_id)).child(str(task_id))
                submission_ref.push().set({
                    'image_id': image_id,
                    'submitted_at': datetime.now(timezone.utc).isoformat()
                })
                
                username = f"@{event.sender.username}" if event.sender.username else "No Username"
                caption = (
                    f"🖼️ *New Submission Received*\n\n"
                    f"Task No: {task_number}\n"
                    f"User: {username}\n"
                    f"ID: `{user_id}`\n\n"
                    "Reply with:\n"
                    "✅ Approve\n"
                    "❌ Reject (Reason)"
                )
                
                forwarded = await client.send_message(
                    ADMIN_ID,
                    caption,
                    file=event.photo,
                    parse_mode='md'
                )
                
                user_states[user_id] = {
                    'state': 'submitted_screenshot',
                    'forwarded_msg_id': forwarded.id,
                    'task_number': task_number,
                    'last_activity': datetime.now(timezone.utc).isoformat()
                }
                
                await event.respond(
                    f"✅ *Screenshot Received for Task {task_number}!*\n\n"
                    "Admin will review your submission shortly.\n"
                    "You'll be notified when approved.",
                    buttons=BACK_BUTTON,
                    parse_mode='md'
                )
                update_user_activity(user_id)
            else:
                user_states[user_id] = {
                    'state': 'awaiting_task_number',
                    'last_activity': datetime.now(timezone.utc).isoformat()
                }
                await event.respond(
                    "📝 *Please enter the Task Number for this screenshot:*",
                    buttons=CANCEL_BUTTON,
                    parse_mode='md'
                )
                update_user_activity(user_id)
        
        else:
            await event.respond(
                "⚠️ *Unrecognized Command*\n\n"
                "Please use the buttons to navigate the bot.",
                buttons=BACK_BUTTON,
                parse_mode='md'
            )
            update_user_activity(user_id)
    
    except Exception as e:
        await notify_admin(f"Error in message handling for user {event.sender_id}: {str(e)}")
        await event.respond(
            "⚠️ An error occurred. Please try again.",
            buttons=BACK_BUTTON
        )

@client.on(events.NewMessage(from_users=ADMIN_ID))
async def handle_admin_replies(event):
    """Handle admin replies to approve payments and tasks"""
    try:
        if not event.is_reply:
            if event.raw_text.startswith('/addtask'):
                parts = [p.strip() for p in event.raw_text.split('|')]
                if len(parts) < 5:
                    await event.respond(
                        "❌ Invalid format. Use:\n"
                        "/addtask Description | Steps | Download Link | Group Link | Tutorial Link | Reward\n"
                        "[Attach image if needed]"
                    )
                    return
                
                description = parts[0].replace('/addtask', '').strip()
                steps = parts[1] if len(parts) > 1 else "No steps provided"
                download_link = parts[2] if len(parts) > 2 else ""
                group_link = parts[3] if len(parts) > 3 else ""
                tutorial_link = parts[4] if len(parts) > 4 else ""
                reward = parts[5] if len(parts) > 5 else "0"
                
                # Check for duplicate download link
                existing_task = get_task_by_download_link(download_link)
                if existing_task:
                    await event.respond(
                        f"⚠️ This Download Link already exists.\n\n"
                        f"Task ID: {next((k for k, v in get_tasks_for_today().items() if v.get('download_link') == download_link), 'N/A')}\n"
                        "Please use /updateTask to modify the existing task.",
                        parse_mode='md'
                    )
                    return
                
                try:
                    reward = float(reward)
                except ValueError:
                    reward = 0
                
                image_file_id = None
                if event.media and isinstance(event.media, MessageMediaPhoto):
                    # Get the actual file_id from the media
                    image_file_id = event.media.photo.id
                    if isinstance(image_file_id, int):
                        # Convert to string if it's an integer
                        image_file_id = str(image_file_id)
                
                new_task = {
                    'description': description,
                    'steps': steps,
                    'download_link': download_link,
                    'group_link': group_link,
                    'tutorial_link': tutorial_link,
                    'reward': reward,
                    'image_file_id': image_file_id,
                    'date': datetime.now(timezone.utc).date().isoformat(),
                    'created_at': datetime.now(timezone.utc).isoformat()
                }
                
                tasks_ref.push(new_task)
                
                await event.respond(
                    "✅ *Task Added Successfully!*\n\n"
                    f"🔹 *Description*: {description}\n"
                    f"🔹 *Steps*: {steps}\n"
                    f"🔹 *Download Link*: {'[Click Here](' + download_link + ')' if download_link else 'Not set'}\n"
                    f"🔹 *Group Link*: {'[Join Group](' + group_link + ')' if group_link else 'Not set'}\n"
                    f"🔹 *Tutorial Link*: {'[Tutorial](' + tutorial_link + ')' if tutorial_link else 'Not set'}\n"
                    f"🔹 *Reward*: ₹{reward}\n"
                    f"🔹 *Date*: {new_task['date']}\n"
                    f"🔹 *Image*: {'Attached' if image_file_id else 'Not attached'}",
                    parse_mode='md'
                )
            
            elif event.raw_text.startswith('/updateTask'):
                parts = [p.strip() for p in event.raw_text.split('|')]
                if len(parts) < 7:
                    await event.respond(
                        "❌ Invalid format. Use:\n"
                        "/updateTask task_id | Description | Steps | Download Link | Group Link | Tutorial Link | Reward\n"
                        "[Attach image if needed]"
                    )
                    return
                
                task_id = parts[0].replace('/updateTask', '').strip()
                description = parts[1] if len(parts) > 1 else "No description"
                steps = parts[2] if len(parts) > 2 else "No steps provided"
                download_link = parts[3] if len(parts) > 3 else ""
                group_link = parts[4] if len(parts) > 4 else ""
                tutorial_link = parts[5] if len(parts) > 5 else ""
                reward = parts[6] if len(parts) > 6 else "0"
                
                try:
                    reward = float(reward)
                except ValueError:
                    reward = 0
                
                image_file_id = None
                if event.media and isinstance(event.media, MessageMediaPhoto):
                    # Get the actual file_id from the media
                    image_file_id = event.media.photo.id
                    if isinstance(image_file_id, int):
                        # Convert to string if it's an integer
                        image_file_id = str(image_file_id)
                
                updated_task = {
                    'description': description,
                    'steps': steps,
                    'download_link': download_link,
                    'group_link': group_link,
                    'tutorial_link': tutorial_link,
                    'reward': reward,
                    'date': datetime.now(timezone.utc).date().isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                
                if image_file_id:
                    updated_task['image_file_id'] = image_file_id
                
                tasks_ref.child(task_id).update(updated_task)
                
                await event.respond(
                    "✅ *Task Updated Successfully!*\n\n"
                    f"🔹 *Task ID*: {task_id}\n"
                    f"🔹 *Description*: {description}\n"
                    f"🔹 *Steps*: {steps}\n"
                    f"🔹 *Download Link*: {'[Click Here](' + download_link + ')' if download_link else 'Not set'}\n"
                    f"🔹 *Group Link*: {'[Join Group](' + group_link + ')' if group_link else 'Not set'}\n"
                    f"🔹 *Tutorial Link*: {'[Tutorial](' + tutorial_link + ')' if tutorial_link else 'Not set'}\n"
                    f"🔹 *Reward*: ₹{reward}\n"
                    f"🔹 *Date*: {updated_task['date']}\n"
                    f"🔹 *Image*: {'Updated' if image_file_id else 'Not updated'}",
                    parse_mode='md'
                )
            
            return
        
        replied_msg = await event.get_reply_message()
        reply_text = event.raw_text.strip()
        
        if reply_text.startswith('✅ Paid') and ('Withdrawal Request' in replied_msg.text or 'UPI ID Received' in replied_msg.text):
            try:
                # Extract request ID from the message
                request_id = None
                if 'Request ID:' in replied_msg.text:
                    request_id = replied_msg.text.split('Request ID:')[1].split('\n')[0].strip()
                
                if not request_id:
                    await event.reply("❌ Could not find Request ID in the message.")
                    return
                
                # Check if already processed
                user_id_text = replied_msg.text.split('ID: `')[1].split('`')[0]
                user_id = int(user_id_text)
                
                request_data = withdrawal_requests_ref.child(str(user_id)).child(request_id).get()
                if not request_data:
                    await event.reply("❌ Withdrawal request not found.")
                    return
                
                if request_data.get('status') == 'paid':
                    await event.reply("⚠️ This withdrawal has already been processed.")
                    return
                
                amount = float(request_data.get('amount', 0))
                
                withdrawal_requests_ref.child(str(user_id)).child(request_id).update({
                    'status': 'paid',
                    'processed_time': datetime.now(timezone.utc).isoformat()
                })
                
                update_user_balance(user_id, -amount)
                add_to_withdrawal_history(user_id, amount, 'paid', request_id)
                
                await client.send_message(
                    user_id,
                    f"✅ *Withdrawal of ₹{amount} (Request ID: {request_id}) has been Successfully Paid.*\n\n"
                    "Thank you for using our service!",
                    buttons=BACK_BUTTON,
                    parse_mode='md'
                )
                
                await event.reply("✅ Payment confirmed and user notified.", parse_mode='md')
            
            except Exception as e:
                await event.reply(f"❌ Error processing payment: {str(e)}", parse_mode='md')
        
        elif reply_text.startswith('❌ Reject') and ('Withdrawal Request' in replied_msg.text or 'UPI ID Received' in replied_msg.text):
            try:
                # Extract request ID from the message
                request_id = None
                if 'Request ID:' in replied_msg.text:
                    request_id = replied_msg.text.split('Request ID:')[1].split('\n')[0].strip()
                
                if not request_id:
                    await event.reply("❌ Could not find Request ID in the message.")
                    return
                
                # Get rejection reason
                reason = reply_text.replace('❌ Reject', '').strip()
                if not reason:
                    reason = "No reason provided"
                
                # Get user ID
                user_id_text = replied_msg.text.split('ID: `')[1].split('`')[0]
                user_id = int(user_id_text)
                
                request_data = withdrawal_requests_ref.child(str(user_id)).child(request_id).get()
                if not request_data:
                    await event.reply("❌ Withdrawal request not found.")
                    return
                
                if request_data.get('status') != 'pending':
                    await event.reply("⚠️ This request has already been processed.")
                    return
                
                withdrawal_requests_ref.child(str(user_id)).child(request_id).update({
                    'status': 'rejected',
                    'reason': reason,
                    'processed_time': datetime.now(timezone.utc).isoformat()
                })
                
                add_to_withdrawal_history(user_id, request_data.get('amount', 0), 'rejected', request_id)
                
                await client.send_message(
                    user_id,
                    f"❌ *Your Withdrawal Request ({request_id}) has been Rejected.*\n\n"
                    f"Reason: {reason}",
                    buttons=BACK_BUTTON,
                    parse_mode='md'
                )
                
                await event.reply("✅ Rejection sent to user.", parse_mode='md')
            
            except Exception as e:
                await event.reply(f"❌ Error rejecting request: {str(e)}", parse_mode='md')
        
        elif reply_text == '✅ Approve' and 'New Submission' in replied_msg.text:
            try:
                user_id = int(replied_msg.text.split('ID: `')[1].split('`')[0])
                task_number = int(replied_msg.text.split('Task No:')[1].split('\n')[0].strip())
                
                task = get_task_for_today(task_number)
                if not task:
                    await event.reply("❌ Task not found.")
                    return
                
                credit_amount = task.get('reward', 10)
                update_user_balance(user_id, credit_amount)
                
                await client.send_message(
                    user_id,
                    f"🎉 *Congratulations!* 🎉\n\n"
                    f"You have earned ₹{credit_amount} for completing Task {task_number}.\n"
                    f"Your wallet has been updated.",
                    buttons=BACK_BUTTON,
                    parse_mode='md'
                )
                
                await event.reply(
                    f"✅ User notified and ₹{credit_amount} credited to their wallet.",
                    parse_mode='md'
                )
            except Exception as e:
                await event.reply(f"❌ Error approving submission: {str(e)}", parse_mode='md')
        
        elif reply_text.startswith('❌ Reject') and 'New Submission' in replied_msg.text:
            try:
                user_id = int(replied_msg.text.split('ID: `')[1].split('`')[0])
                task_number = int(replied_msg.text.split('Task No:')[1].split('\n')[0].strip())
                
                reason = reply_text.replace('❌ Reject', '').strip()
                if not reason:
                    reason = "No reason provided"
                
                await client.send_message(
                    user_id,
                    f"❌ *Your Screenshot Submission for Task {task_number} has been Rejected.*\n\n"
                    f"Reason: {reason}",
                    buttons=BACK_BUTTON,
                    parse_mode='md'
                )
                
                await event.reply("✅ Rejection sent to user.", parse_mode='md')
            except Exception as e:
                await event.reply(f"❌ Error rejecting submission: {str(e)}", parse_mode='md')
    
    except Exception as e:
        await notify_admin(f"Error in admin reply handling: {str(e)}")
        await event.reply(
            "❌ Failed to process your reply. Please try again.",
            parse_mode='md'
        )

# ================== MAIN LOOP ===================
print("✅ Bot is running...")
print(f"👤 Admin ID: {ADMIN_ID}")
print("Press Ctrl+C to stop")
client.run_until_disconnected()
# =================================================