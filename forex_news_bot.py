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
from flask import Flask
from threading import Thread


# --- CONFIGURATION ---
# The bot will get its token from an environment variable called DISCORD_TOKEN on the server.
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")

# Messages
NO_NEWS_MESSAGE = "No high/medium impact news found for the selected currencies on this day."
NO_NEWS_ANNOUNCEMENT_MESSAGE = "No high/medium impact news found for today."

# A set of currencies to ignore for all news.
EXCLUDED_CURRENCIES = {"AUD", "CAD", "CHF", "CNY", "NZD"}

# --- ANNOUNCEMENT CONFIGURATION ---
# The bot will prioritize environment variables on the server for the Channel ID.
# The announcement time is now fixed to midnight.
ANNOUNCEMENT_CHANNEL_ID = int(os.environ.get("ANNOUNCEMENT_CHANNEL_ID", 1411000066252079154))
ANNOUNCEMENT_TIMEZONE = "Europe/Zurich"
ANNOUNCEMENT_TIME = "00:00"

last_announcement_date = None # Tracks the date of the last announcement
bot_has_started = False # Flag to prevent multiple bot instances on Gunicorn

# --- BOT & SCRAPER SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Create a single, persistent scraper session to be reused for all requests.
# This maintains cookies and headers, appearing more like a real user.
scraper = cloudscraper.create_scraper()


# --- WEB SCRAPING LOGIC ---
def get_forex_news(day_offset=0, timezone_str="UTC"):
    """
    Scrapes Forex Factory for news for a given day, using the persistent scraper session.
    """
    try:
        tz = pytz.timezone(timezone_str)
        now_in_tz = datetime.now(tz)
        
        target_date = now_in_tz + timedelta(days=day_offset)
        display_date = target_date.strftime("%A, %b %d, %Y")
        url_date_str = f"{target_date.strftime('%b').lower()}{target_date.day}.{target_date.year}"
        url = f"https://www.forexfactory.com/calendar?day={url_date_str}"

        # Use the single, global scraper instance for the request.
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
    """
    if not isinstance(channel, discord.TextChannel):
        print(f"Error: Invalid channel provided.")
        return

    display_date, news_events = get_forex_news(day_offset, timezone_str=ANNOUNCEMENT_TIMEZONE)

    if display_date == "Error":
        await channel.send("Sorry, I couldn't fetch the news. The website might be down or blocking requests.")
        return

    if not news_events:
        try:
            if mention: # This is an announcement
                await channel.send(f"{mention} {NO_NEWS_ANNOUNCEMENT_MESSAGE}")
            else: # This is a manual command
                await channel.send(NO_NEWS_MESSAGE)
        except Exception as e:
            print(f"Failed to send 'no news' message to channel {channel.name}: {e}")
        return

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
    except Exception as e:
        print(f"Failed to send message to channel {channel.name}: {e}")


@tasks.loop(minutes=1)
async def daily_news_announcement():
    """The background task that checks the time and sends the daily news."""
    global last_announcement_date
    try:
        tz = pytz.timezone(ANNOUNCEMENT_TIMEZONE)
        now_in_tz = datetime.now(tz)
        current_date = now_in_tz.date()

        announcement_time_obj = datetime.strptime(ANNOUNCEMENT_TIME, '%H:%M').time()
        target_announcement_dt = tz.localize(datetime.combine(current_date, announcement_time_obj))

        if now_in_tz >= target_announcement_dt and current_date != last_announcement_date:
            channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if channel:
                print(f"Sending daily news to channel: {channel.name}")
                await send_news_to_channel(channel, day_offset=0, mention="@everyone")
                last_announcement_date = current_date
            else:
                print(f"Error: Could not find configured channel with ID {ANNOUNCEMENT_CHANNEL_ID}")
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
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    print('Bot is ready to receive commands.')

@bot.event
async def on_message(message):
    """
    Handles all messages to catch custom commands like !newsddmmyy
    and also processes regular commands.
    """
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    content = message.content.strip()
    
    # Custom handling for !news<date> format, e.g., !news010925 or !news01092025
    if content.startswith('!news') and content[5:].isdigit():
        date_str = content[5:]
        date_format = ''
        
        if len(date_str) == 6: # ddmmyy format
            date_format = '%d%m%y'
        elif len(date_str) == 8: # ddmmyyyy format
            date_format = '%d%m%Y'
        else:
            await message.channel.send("Invalid date length. Please use `!newsddmmyy` or `!newsddmmyyyy`.")
            return

        try:
            tz = pytz.timezone(ANNOUNCEMENT_TIMEZONE)
            today = datetime.now(tz).date()
            target_date = datetime.strptime(date_str, date_format).date()
            day_offset = (target_date - today).days

            await message.channel.send(f"Searching for news for {target_date.strftime('%A, %b %d, %Y')}...")
            await send_news_to_channel(message.channel, day_offset=day_offset)
            return # Stop processing so it doesn't conflict with other commands
        except ValueError:
            await message.channel.send("Invalid date format. Please use `!newsddmmyy` or `!newsddmmyyyy`.")
            return
        except Exception as e:
            await message.channel.send(f"An unexpected error occurred.")
            print(f"Error in custom news command handler: {e}")
            return

    # Process all other commands normally (!newstoday, !newstomorrow)
    await bot.process_commands(message)


# --- BOT COMMANDS ---
@bot.command(name='newstoday', help='Shows today\'s trading news.')
async def news_today(ctx):
    await ctx.send(f"Searching for news...")
    await send_news_to_channel(ctx.channel, day_offset=0)

@bot.command(name='newstomorrow', help='Shows tomorrow\'s trading news.')
async def news_tomorrow(ctx):
    await ctx.send(f"Searching for news...")
    await send_news_to_channel(ctx.channel, day_offset=1)

# --- ASYNCHRONOUS STARTUP ---
async def run_bot_async():
    """Handles bot startup and background tasks."""
    async with bot:
        daily_news_announcement.start()
        await bot.start(BOT_TOKEN)

# --- Keep Alive Web Server (For Render Hosting) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running."

# --- RUN THE BOT & SERVER ---
if __name__ == "__main__":
    # This block is for local development/testing ONLY.
    # Render will use Gunicorn and will not run this block.
    
    # Start the web server in a background thread
    web_thread = Thread(target=app.run, kwargs={'host':'0.0.0.0','port':10000})
    web_thread.start()
    
    # Start the Discord bot in the main thread
    if BOT_TOKEN:
        try:
            asyncio.run(run_bot_async())
        except discord.errors.LoginFailure:
            print("ERROR: Improper token has been passed.")
        except Exception as e:
            print(f"An error occurred while running the bot: {e}")
    else:
        print("ERROR: DISCORD_TOKEN environment variable not found.")
else:
    # This block is executed when Gunicorn imports the file to run the web server.
    # We start the bot in a background thread, ensuring it only starts once.
    if not bot_has_started:
        pid = os.getpid()
        print(f"Starting bot in background thread for Gunicorn. Process ID: {pid}")
        bot_thread = Thread(target=asyncio.run, args=(run_bot_async(),))
        bot_thread.daemon = True # Allows Gunicorn to manage the parent process
        bot_thread.start()
        bot_has_started = True

