import asyncio
import feedparser
import motor.motor_asyncio
import os
from datetime import datetime
from dateutil import parser
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import aiohttp
import random
import logging
import ssl
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
# Main RSS URL and fallbacks
RSS_URLS = [
    "https://rss.unian.net/site/news_ukr.rss"
]

# Browser headers
BROWSER_HEADERS = [
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'uk-UA,uk;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://korrespondent.net/'
    },
    {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        'Accept': 'application/rss+xml,text/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'uk-UA,uk;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'no-cache'
    }
]

UPDATE_INTERVAL = 30  # 30 seconds

async def connect_to_mongodb():
    try:
        client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        db = client.news_db
        await db.command("ping")  # Test the connection
        logger.info("Successfully connected to MongoDB")
        return db.news
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise

async def cleanup_database(collection):
    """Clean up the entire collection before starting"""
    try:
        await collection.delete_many({})
        logger.info("Database cleaned up")
    except Exception as e:
        logger.error(f"Failed to clean up database: {e}")

def extract_image_url(entry):
    try:
        # Try to get image from media_content
        if hasattr(entry, 'media_content') and entry.media_content:
            for media in entry.media_content:
                if media.get('type', '').startswith('image/'):
                    return media.get('url')
        
        # Try to get image from enclosures
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enclosure in entry.enclosures:
                if enclosure.get('type', '').startswith('image/'):
                    return enclosure.get('url')
        
        # Try to get image from content
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].value
        # If no content, try description
        elif hasattr(entry, 'description'):
            content = entry.description
        else:
            return None

        # Parse HTML content
        soup = BeautifulSoup(content, 'html.parser')
        
        # Find first image
        img = soup.find('img')
        if img and img.get('src'):
            return img['src']
        
        return None
    except Exception as e:
        logger.error(f"Error extracting image URL: {e}")
        return None

def clean_html(raw_html):
    if not raw_html:
        return ""
    try:
        soup = BeautifulSoup(raw_html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return text
    except Exception as e:
        logger.error(f"Error cleaning HTML: {e}")
        return raw_html

async def fetch_rss(url):
    timeout = aiohttp.ClientTimeout(total=30)  # 30 seconds timeout
    headers = random.choice(BROWSER_HEADERS)
    
    logger.info(f"Attempting to fetch RSS from {url}")
    logger.debug(f"Using headers: {headers}")
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            # Create SSL context that verifies certificates
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with session.get(url, headers=headers, ssl=ssl_context) as response:
                logger.info(f"Response status: {response.status}")
                logger.debug(f"Response headers: {response.headers}")
                
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"Successfully fetched content (length: {len(content)})")
                    logger.debug(f"First 500 characters: {content[:500]}")
                    return content
                else:
                    logger.error(f"Error status: {response.status}")
                    error_text = await response.text()
                    logger.error(f"Error response: {error_text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"Exception during fetch from {url}: {str(e)}")
            return None

async def parse_rss():
    entries = []
    
    # Try each RSS URL until we get content
    for url in RSS_URLS:
        content = await fetch_rss(url)
        if content:
            logger.info(f"Successfully fetched content from {url}")
            feed = feedparser.parse(content)
            
            if hasattr(feed, 'bozo_exception'):
                logger.error(f"Feed parsing error: {feed.bozo_exception}")
                continue
            
            if not hasattr(feed, 'entries') or not feed.entries:
                logger.warning(f"No entries found in feed from {url}")
                logger.debug(f"Feed keys: {feed.keys()}")
                continue
            
            logger.info(f"Found {len(feed.entries)} entries in feed")
            
            for entry in feed.entries:
                try:
                    # Print raw entry for debugging
                    logger.debug(f"Processing entry: {entry.title if hasattr(entry, 'title') else 'No title'}")
                    logger.debug(f"Available entry attributes: {entry.keys()}")
                    
                    # Clean description from HTML tags
                    description = clean_html(entry.description if hasattr(entry, 'description') else '')
                    
                    # Parse date with multiple formats support
                    try:
                        pub_date = parser.parse(entry.published) if hasattr(entry, 'published') else \
                                parser.parse(entry.updated) if hasattr(entry, 'updated') else \
                                datetime.now(pytz.UTC)
                        
                        # Convert to UTC if timezone is present, otherwise assume UTC
                        if pub_date.tzinfo is None:
                            pub_date = pytz.UTC.localize(pub_date)
                        else:
                            pub_date = pub_date.astimezone(pytz.UTC)
                            
                        # Convert to Ukraine timezone (UTC+3)
                        ukraine_tz = pytz.timezone('Europe/Kiev')
                        pub_date = pub_date.astimezone(ukraine_tz)
                        # Remove timezone info before saving to MongoDB
                        pub_date = pub_date.replace(tzinfo=None)
                            
                    except Exception as e:
                        logger.error(f"Date parsing error: {e}")
                        ukraine_tz = pytz.timezone('Europe/Kiev')
                        pub_date = datetime.now(ukraine_tz).replace(tzinfo=None)
                    
                    parsed_entry = {
                        "title": entry.title.replace(" - Радіо Свобода", "") if hasattr(entry, 'title') else "Без заголовку",
                        "link": entry.link if hasattr(entry, 'link') else "",
                        "description": description,
                        "pubDate": pub_date,
                        "image": extract_image_url(entry)
                    }
                    entries.append(parsed_entry)
                    logger.info(f"Successfully parsed entry: {parsed_entry['title']}")
                except Exception as e:
                    logger.error(f"Error parsing entry: {e}")
                    continue
            
            if entries:
                break  # If we got entries, no need to try other URLs
    
    return entries

async def save_entries(collection, entries):
    saved_count = 0
    for entry in entries:
        try:
            # Check if entry already exists
            existing = await collection.find_one({"link": entry["link"]})
            if not existing:
                await collection.insert_one(entry)
                saved_count += 1
                logger.info(f"Saved new entry: {entry['title']}")
            else:
                logger.debug(f"Entry already exists: {entry['title']}")
        except Exception as e:
            logger.error(f"Error saving entry: {e}")
    
    logger.info(f"Saved {saved_count} new entries")
    return saved_count

async def main():
    try:
        collection = await connect_to_mongodb()
        
        while True:
            try:
                logger.info("Starting RSS update cycle")
                entries = await parse_rss()
                
                if entries:
                    saved_count = await save_entries(collection, entries)
                    logger.info(f"Update cycle completed. Saved {saved_count} new entries")
                else:
                    logger.warning("No entries were parsed in this cycle")
                
                logger.info(f"Waiting {UPDATE_INTERVAL} seconds until next update")
                await asyncio.sleep(UPDATE_INTERVAL)
            except Exception as e:
                logger.error(f"Error in update cycle: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main()) 