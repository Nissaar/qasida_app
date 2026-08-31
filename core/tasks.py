import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.utils import timezone
import time
from .models import Qasida, Tag

def scrape_mynaatbook():
    """
    Example scraper for mynaatbook.com
    This is a conceptual scraper as the actual HTML structure depends on the live site.
    We implement rate limiting (time.sleep) to avoid getting blocked.
    """
    base_url = "https://mynaatbook.com"
    # Assuming there is a page listing naats
    list_url = f"{base_url}/lyrics"
    
    headers = {
        'User-Agent': 'QasidaAppBot/1.0 (+http://your-app-domain.com)'
    }
    
    try:
        response = requests.get(list_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {list_url}: {e}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    
    # conceptual loop over listing
    links = soup.select('a.naat-link') # example selector
    for link in links[:5]: # limit for example
        detail_url = base_url + link['href']
        
        # Check if we already have it
        if Qasida.objects.filter(source_url=detail_url).exists():
            continue
            
        time.sleep(2) # Rate limiting
        
        try:
            detail_res = requests.get(detail_url, headers=headers, timeout=10)
            detail_res.raise_for_status()
            detail_soup = BeautifulSoup(detail_res.content, 'html.parser')
            
            title = detail_soup.select_one('h1.title').get_text(strip=True) if detail_soup.select_one('h1.title') else 'Unknown Title'
            lyrics_div = detail_soup.select_one('div.lyrics-content')
            lyrics = lyrics_div.get_text(separator='\n', strip=True) if lyrics_div else ''
            
            if lyrics:
                qasida = Qasida.objects.create(
                    title=title,
                    lyrics=lyrics,
                    source_url=detail_url,
                    language='Urdu' # Assuming default or parsing from page
                )
                
                # Assign default tags
                tag_naat, _ = Tag.objects.get_or_create(name='naat')
                tag_urdu, _ = Tag.objects.get_or_create(name='urdu')
                qasida.tags.add(tag_naat, tag_urdu)
                print(f"Scraped and saved: {title}")
                
        except Exception as e:
            print(f"Error processing {detail_url}: {e}")
            continue

@shared_task
def run_crawlers():
    """
    Periodic task to trigger all web crawlers.
    """
    print("Starting crawler task...")
    scrape_mynaatbook()
    print("Crawler task finished.")
