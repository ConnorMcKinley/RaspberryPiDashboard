import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import time

# Using BBC World News RSS as a free, reliable source for Geopolitical/Political news
RSS_URL = "http://feeds.bbci.co.uk/news/world/rss.xml"


def get_political_news(limit=5):
    """
    Fetches the latest news from the RSS feed and returns a list of dictionaries.
    """
    try:
        response = requests.get(RSS_URL, timeout=10)
        response.raise_for_status()

        # Parse XML
        root = ET.fromstring(response.content)

        news_items = []
        # Iterate over items in the channel
        for item in root.findall('./channel/item'):
            if len(news_items) >= limit:
                break

            title = item.find('title').text
            # Basic cleaning of title if needed (BBC sometimes puts "VIDEO:" prefixes)
            title = title.replace("VIDEO:", "").strip()

            # Helper to get text safely
            source = "BBC World"

            # PubDate parsing could be added here if needed,
            # but for a dashboard, just the headline is often cleanest.

            news_items.append({
                "title": title,
                "source": source
            })

        return news_items

    except Exception as e:
        print(f"[news] Error fetching news: {e}")
        return []


if __name__ == "__main__":
    # Test the function
    print(get_political_news())