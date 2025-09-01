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

        print(f"[DEBUG] Fetching URL: {url}")  # 🔍 Add this debug line

        headers = {
            "Referer": "https://www.forexfactory.com/calendar"
        }

        response = scraper.get(url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        news_rows = soup.find_all('tr', class_='calendar__row')

        if not news_rows:
            print(f"[INFO] No news rows found for {display_date}. Possibly not published yet.")
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
