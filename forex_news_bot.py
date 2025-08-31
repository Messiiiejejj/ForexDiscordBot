# forex_news_bot.py
# A Discord bot that scrapes Forex Factory for economic news.

import os
import discord
from discord.ext import commands, tasks
import cloudscraper
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import asyncio
import json # For saving settings
from flask import Flask
from threading import Thread


# --- CONFIGURATION ---
# The bot will get its token from an environment variable called DISCORD_TOKEN on the server.
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")

# The message to send when no news is found for a manual command.
NO_NEWS_MESSAGE = "There are no news you prick"

# The message to send for an empty daily announcement.
NO_NEWS_ANNOUNCEMENT_MESSAGE = "No news today"

# A set of currencies to ignore for all news.
EXCLUDED_CURRENCIES = {"AUD", "CAD", "CHF", "CNY", "NZD"}

# --- ANNOUNCEMENT CONFIGURATION ---
# This is now a fallback. The bot will prioritize environment variables on the server.
DEFAULT_ANNOUNCEMENT_CHANNEL_ID = int(os.environ.get("ANNOUNCEMENT_CHANNEL_ID", 1411000066252079154))
DEFAULT_ANNOUNCEMENT_TIMEZONE = "Europe/Zurich"
DEFAULT_ANNOUNCEMENT_TIME = "08:00"

CONFIG_FILE = "bot_config.json"
bot_config = {}
last_announcement_date = None # Tracks the date of the last announcement

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- CONFIGURATION MANAGEMENT ---
def load_config():
    """Loads configuration from a JSON file."""
    global bot_config
    try:
        with open(CONFIG_FILE, 'r') as f:
            bot_config = json.load(f)
            # Ensure the hardcoded channel ID is always used
            bot_config['channel_id'] = DEFAULT_ANNOUNCEMENT_CHANNEL_ID
    except FileNotFoundError:
        print("Config file not found, creating with default values.")
        bot_config = {
            "channel_id": DEFAULT_ANNOUNCEMENT_CHANNEL_ID,
            "time": DEFAULT_ANNOUNCEMENT_TIME,
            "timezone": DEFAULT_ANNOUNCEMENT_TIMEZONE
        }
        save_config()

def save_config():
    """Saves the current configuration to a JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(bot_config, f, indent=4)

# --- WEB SCRAPING LOGIC ---
def get_forex_news(day_offset=0, timezone_str="UTC"):
    """
    Scrapes Forex Factory for news for a given day, based on a specific timezone.
    """
    try:
        tz = pytz.timezone(timezone_str)
        now_in_tz = datetime.now(tz)
        
        target_date = now_in_tz + timedelta(days=day_offset)
        display_date = target_date.strftime("%A, %b %d, %Y")
        url_date_str = f"{target_date.strftime('%b').lower()}{target_date.day}.{target_date.year}"
        url = f"https://www.forexfactory.com/calendar?day={url_date_str}"

        scraper = cloudscraper.create_scraper()
        response = scraper.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        news_rows = soup.find_all('tr', class_='calendar__row')

        if not news_rows:
            return display_date, None

        events = []
        for row in news_rows:
            currency_cell = row.find('td', class_='calendar__currency')
            currency = currency_cell.text.strip() if currency_cell else ""

            if currency in EXCLUDED_CURRENCIES:
                continue

            impact_cell = row.find('td', class_='calendar__impact')
            impact_span = impact_cell.find('span') if impact_cell else None
            
            impact_class = ""
            if impact_span and impact_span.has_attr('class'):
                for cls in impact_span['class']:
                    if 'calendar__impact-icon--' in cls:
                        impact_class = cls
                        break
            
            event_cell = row.find('td', class_='calendar__event')
            event_name = event_cell.text.strip() if event_cell else ""

            is_holiday = "Bank Holiday" in event_name or "holiday" in impact_class
            is_high_impact = "high" in impact_class
            is_medium_impact = "medium" in impact_class

            if not (is_holiday or is_high_impact or is_medium_impact):
                continue

            time_cell = row.find('td', class_='calendar__time')
            time = time_cell.text.strip() if time_cell else ""
            
            forecast_cell = row.find('td', class_='calendar__forecast')
            forecast = forecast_cell.text.strip() if forecast_cell else ""
            
            previous_cell = row.find('td', class_='calendar__previous')
            previous = previous_cell.text.strip() if previous_cell else ""

            if "Bank Holiday" in event_name:
                impact_class = "holiday"

            events.append({
                "time": time or "All Day", "currency": currency, "impact": impact_class,
                "event": event_name, "forecast": forecast or "N/A", "previous": previous or "N/A",
            })
        
        return display_date, events if events else None
    except Exception as e:
        print(f"An error occurred during scraping: {e}")
        return "Error", None

def format_impact_emoji(impact_class):
    """Converts impact CSS class to a colored emoji."""
    if "high" in impact_class: return "🔴"
    if "medium" in impact_class: return "🟠"
    if "holiday" in impact_class: return "⚪️"
    return "⚫️"

# --- DISCORD BOT LOGIC ---
async def send_news_to_channel(channel, day_offset, mention=None):
    """
    A generic function to fetch and send news to a specific channel.
    Returns True if a message was sent, False otherwise.
    """
    if not isinstance(channel, discord.TextChannel):
        print(f"Error: Invalid channel provided.")
        return False

    display_date, news_events = get_forex_news(day_offset, timezone_str=bot_config.get("timezone"))

    if display_date == "Error":
        await channel.send("Sorry, I couldn't fetch the news. The website might be down or blocking requests.")
        return False

    if not news_events:
        try:
            if mention: # This is an announcement
                await channel.send(f"{mention} {NO_NEWS_ANNOUNCEMENT_MESSAGE}")
            else: # This is a manual command
                await channel.send(NO_NEWS_MESSAGE)
            return True
        except Exception as e:
            print(f"Failed to send 'no news' message to channel {channel.name}: {e}")
            return False

    embed = discord.Embed(
        title=f"Forex Factory News for {display_date}",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Data sourced from ForexFactory.com")

    for event in news_events:
        impact_emoji = format_impact_emoji(event['impact'])
        field_name = f"{event['time']} - {event['currency']} {impact_emoji}"
        field_value = (
            f"**Event:** {event['event']}\n"
            f"**Forecast:** {event['forecast']} | **Previous:** {event['previous']}"
        )
        embed.add_field(name=field_name, value=field_value, inline=False)
    
    try:
        if mention:
            await channel.send(mention, embed=embed)
        else:
            await channel.send(embed=embed)
        return True
    except Exception as e:
        print(f"Failed to send message to channel {channel.name}: {e}")
        return False


@tasks.loop(minutes=1)
async def daily_news_announcement():
    """The background task that checks the time and sends the daily news."""
    global last_announcement_date
    try:
        channel_id = bot_config.get("channel_id")
        announcement_time_str = bot_config.get("time")
        timezone_str = bot_config.get("timezone")

        if not all([channel_id, announcement_time_str, timezone_str]):
            return 

        tz = pytz.timezone(timezone_str)
        now_in_tz = datetime.now(tz)
        current_date = now_in_tz.date()

        try:
            announcement_time_obj = datetime.strptime(announcement_time_str, '%H:%M').time()
        except (ValueError, TypeError):
            return

        target_announcement_dt = tz.localize(datetime.combine(current_date, announcement_time_obj))

        if now_in_tz >= target_announcement_dt and current_date != last_announcement_date:
            channel = bot.get_channel(channel_id)
            if channel:
                print(f"Attempting to send daily news to channel: {channel.name}")
                sent_ok = await send_news_to_channel(channel, day_offset=0, mention="@everyone")
                last_announcement_date = current_date
                if sent_ok:
                    print(f"Daily news check complete. Last announcement date updated to {current_date}.")
                else:
                    print("Daily news check resulted in no message being sent (e.g. no events found).")

            else:
                print(f"Error: Could not find configured channel with ID {channel_id}")
    except Exception as e:
        print(f"Error in daily announcement task: {e}")

@daily_news_announcement.before_loop
async def before_daily_news_announcement():
    """Wait until the bot is ready before starting the task."""
    await bot.wait_until_ready()
    print("Daily news announcement task is ready.")

@bot.event
async def on_ready():
    """Event handler for when the bot logs in and is ready."""
    global last_announcement_date
    load_config()
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    print('Bot is ready to receive commands.')

    # --- Send news on startup ---
    print("Attempting to send initial news on startup...")
    channel_id = bot_config.get("channel_id")
    timezone_str = bot_config.get("timezone")

    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            sent_ok = await send_news_to_channel(channel, day_offset=0, mention="@everyone")
            if timezone_str:
                tz = pytz.timezone(timezone_str)
                now_in_tz = datetime.now(tz)
                last_announcement_date = now_in_tz.date()
                if sent_ok:
                    print(f"Initial news check complete. Last announcement date set to: {last_announcement_date}")
                else:
                    print(f"Initial news check found no news. Last announcement date set to: {last_announcement_date}")
        else:
            print(f"Could not find configured channel with ID {channel_id} on startup.")
    else:
        print("No announcement channel configured. Skipping initial news post.")


# --- BOT COMMANDS ---
@bot.command(name='newstoday', help='Shows today\'s trading news.')
async def news_today(ctx):
    await ctx.send(f"Searching for news...")
    await send_news_to_channel(ctx.channel, day_offset=0)

@bot.command(name='newstomorrow', help='Shows tomorrow\'s trading news.')
async def news_tomorrow(ctx):
    await ctx.send(f"Searching for news...")
    await send_news_to_channel(ctx.channel, day_offset=1)

@bot.command(name='settime', help='Sets the daily announcement time (HH:MM format). Admin only.')
@commands.has_permissions(administrator=True)
async def set_time(ctx, time_str: str):
    """Sets the announcement time."""
    try:
        datetime.strptime(time_str, '%H:%M')
        bot_config['time'] = time_str
        save_config()
        await ctx.send(f"✅ Announcement time has been set to **{time_str}** {bot_config.get('timezone')}.")
    except ValueError:
        await ctx.send("❌ Invalid time format. Please use **HH:MM** (e.g., 08:30).")

@set_time.error
async def permissions_error(ctx, error):
    """Handles permission errors for set commands."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You don't have permission to use this command.")

# --- ASYNCHRONOUS STARTUP ---
async def main():
    """Handles bot startup and background tasks."""
    async with bot:
        daily_news_announcement.start()
        await bot.start(BOT_TOKEN)

# --- Keep Alive Web Server (For Render Hosting) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running."

def run_web_server():
  app.run(host='0.0.0.0', port=10000)

# --- RUN THE BOT & SERVER ---
if __name__ == "__main__":
    # Start the web server in a background thread
    web_thread = Thread(target=run_web_server)
    web_thread.start()
    
    # Start the Discord bot in the main thread
    if BOT_TOKEN:
        try:
            asyncio.run(main())
        except discord.errors.LoginFailure:
            print("ERROR: Improper token has been passed. Check your DISCORD_TOKEN environment variable.")
        except Exception as e:
            print(f"An error occurred while running the bot: {e}")
    else:
        print("ERROR: DISCORD_TOKEN environment variable not found. The bot cannot start.")

