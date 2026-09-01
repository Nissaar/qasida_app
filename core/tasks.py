import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.utils import timezone
import time
import re
import json
from .models import Qasida, Tag, SourceWebsite

def scrape_mynaatbook(website):
    """
    Scraper for mynaatbook.com which stores its data inside a React JS bundle.
    We fetch the JS bundle, extract the JSON-like objects using regex, and load them.
    """
    print(f"Scraping mynaatbook: {website.url}")
    # The actual data is currently bundled in this JS file:
    js_url = "https://www.mynaatbook.com/static/js/main.9c3048ce.js"
    headers = {'User-Agent': 'QasidaAppBot/1.0 (+http://your-app-domain.com)'}
    
    try:
        response = requests.get(js_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching JS from {js_url}: {e}")
        return

    text = response.text

    # Extract naats
    pattern = re.compile(r'\{"naat_name":"(.*?)","naat_body":\[(.*?)\].*?"naat_url":"(.*?)"')
    matches = pattern.findall(text)
    print(f"Found {len(matches)} naats on mynaatbook.")

    for name, body_str, url in matches:
        if Qasida.objects.filter(source_url=url).exists():
            continue

        lines = re.findall(r'"([^"]*)"', body_str)
        lyrics = "\n".join(lines)
        if lyrics:
            qasida = Qasida.objects.create(
                title=name,
                lyrics=lyrics,
                source_url=url,
                language='Urdu'
            )
            tag_naat, _ = Tag.objects.get_or_create(name='naat')
            tag_urdu, _ = Tag.objects.get_or_create(name='urdu')
            qasida.tags.add(tag_naat, tag_urdu)
            print(f"Scraped and saved: {name}")

def scrape_desertechoblog(website):
    """
    Scraper for desertechoblog.wordpress.com
    We fetch the homepage, find all post links, and extract lyrics from entry-content.
    """
    print(f"Scraping desertechoblog: {website.url}")
    headers = {'User-Agent': 'QasidaAppBot/1.0 (+http://your-app-domain.com)'}
    
    try:
        response = requests.get(website.url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {website.url}: {e}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    # Find all article/post links on the homepage
    post_links = [a['href'] for a in soup.select('h2.entry-title a, h1.entry-title a, article a[rel="bookmark"]')]
    post_links = list(set(post_links)) # Remove duplicates
    
    # Often blogs have an archive or multiple pages, but for now we scrape links on the front page
    if not post_links:
        # Fallback if selectors above didn't catch anything, just grab links in the content area
        post_links = [a['href'] for a in soup.select('div#content a, main#main a') if a.has_attr('href') and '/20' in a['href']]
        post_links = list(set(post_links))

    print(f"Found {len(post_links)} links on desertechoblog.")

    for link in post_links:
        if Qasida.objects.filter(source_url=link).exists():
            continue
            
        time.sleep(2) # Rate limit
        try:
            detail_res = requests.get(link, headers=headers, timeout=10)
            detail_res.raise_for_status()
            detail_soup = BeautifulSoup(detail_res.content, 'html.parser')
            
            title_tag = detail_soup.select_one('h1.entry-title')
            title = title_tag.get_text(strip=True) if title_tag else 'Unknown Title'
            
            content_div = detail_soup.select_one('div.entry-content')
            if content_div:
                # Remove sharing/like buttons if any
                for div in content_div.select('div.sharedaddy, div.wpcnt'):
                    div.decompose()
                lyrics = content_div.get_text(separator='\n', strip=True)
                
                if lyrics:
                    qasida = Qasida.objects.create(
                        title=title,
                        lyrics=lyrics,
                        source_url=link,
                        language='Arabic' # Assuming mostly Arabic Qasidas
                    )
                    tag_qasida, _ = Tag.objects.get_or_create(name='qasida')
                    tag_arabic, _ = Tag.objects.get_or_create(name='arabic')
                    qasida.tags.add(tag_qasida, tag_arabic)
                    print(f"Scraped and saved: {title}")
        except Exception as e:
            print(f"Error processing {link}: {e}")

def scrape_damas(website):
    """
    Scraper for damas.nur.nu
    """
    print(f"Scraping damas: {website.url}")
    headers = {'User-Agent': 'QasidaAppBot/1.0 (+http://your-app-domain.com)'}

    # Use their poetry archive page as the main index
    archive_url = "https://damas.nur.nu/30536/poetry-archive/"
    try:
        response = requests.get(archive_url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {archive_url}: {e}")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    # They have thumbnails and links to qasidas. Example: <a href="https://damas.nur.nu/34394/qasida/qasida_taiyya-al-harraq/" class="_self pt-cv-href-thumbnail...
    links = [a['href'] for a in soup.select('a.pt-cv-href-thumbnail, h4.pt-cv-title a')]
    links = list(set(links))
    print(f"Found {len(links)} links on damas archive.")

    for link in links:
        if Qasida.objects.filter(source_url=link).exists():
            continue

        time.sleep(2)
        try:
            detail_res = requests.get(link, headers=headers, timeout=10)
            detail_res.raise_for_status()
            detail_soup = BeautifulSoup(detail_res.content, 'html.parser')

            title_tag = detail_soup.select_one('h2')
            title = title_tag.get_text(strip=True) if title_tag else 'Unknown Title'

            # The lyrics are usually inside <div class="arabic">
            lyrics_div = detail_soup.select_one('div.arabic')
            if not lyrics_div:
                lyrics_div = detail_soup.select_one('div.entry-content')

            if lyrics_div:
                lyrics = lyrics_div.get_text(separator='\n', strip=True)
                if lyrics:
                    qasida = Qasida.objects.create(
                        title=title,
                        lyrics=lyrics,
                        source_url=link,
                        language='Arabic'
                    )
                    tag_qasida, _ = Tag.objects.get_or_create(name='qasida')
                    tag_arabic, _ = Tag.objects.get_or_create(name='arabic')
                    qasida.tags.add(tag_qasida, tag_arabic)
                    print(f"Scraped and saved: {title}")
        except Exception as e:
            print(f"Error processing {link}: {e}")


@shared_task
def run_crawlers():
    """
    Periodic task to trigger all active web crawlers based on database configuration.
    """
    print("Starting crawler task...")
    active_websites = SourceWebsite.objects.filter(is_active=True)

    for website in active_websites:
        if website.parser_type == 'mynaatbook':
            scrape_mynaatbook(website)
        elif website.parser_type == 'desertechoblog':
            scrape_desertechoblog(website)
        elif website.parser_type == 'damas':
            scrape_damas(website)
        else:
            print(f"Unknown parser type '{website.parser_type}' for {website.name}")

    print("Crawler task finished.")
