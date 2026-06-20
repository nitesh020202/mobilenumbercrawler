import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import pandas as pd
import concurrent.futures
import time
import html
from collections import deque

# =====================================================
# CONFIG
# =====================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

REQUEST_TIMEOUT = (5, 10)

MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15

PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,4}[\s\-\.]?)?"
    r"(?:\(?\d{2,4}\)?[\s\-\.]?)?"
    r"\d{3,4}[\s\-\.]?\d{3,4}"
)

# =====================================================
# UTILS (ENTITY SAFE)
# =====================================================

def decode_html_entities(text: str) -> str:
    """Convert &#40; &#45; etc → normal characters"""
    return html.unescape(text)

def normalize_text(text: str) -> str:
    text = decode_html_entities(text)
    text = re.sub(r"\b[tTfF]\s*::\s*", " ", text)
    return text

def clean_digits(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)

def validate_phone(raw: str):
    digits = clean_digits(raw)
    if MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
        return True, raw.strip()
    return False, None

def extract_phones(text: str):
    phones = set()
    for match in PHONE_REGEX.findall(text):
        ok, phone = validate_phone(match)
        if ok:
            phones.add(phone)
    return phones

def is_internal(link, domain):
    p = urlparse(link)
    return p.netloc == "" or p.netloc == domain

# =====================================================
# CRAWLER
# =====================================================

def crawl_page(url, session):
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return [], set()

        soup = BeautifulSoup(r.text, "lxml")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else "N/A"

        raw_text = soup.get_text(" ", strip=True)
        clean_text = normalize_text(raw_text)

        phones = extract_phones(clean_text)

        # tel: links support
        for a in soup.select("a[href^=tel]"):
            phones.add(a["href"].replace("tel:", ""))

        data = [{
            "Mobile Number": p,
            "Page URL": r.url,
            "Page Title": title
        } for p in phones]

        links = set()
        for a in soup.find_all("a", href=True):
            full = urljoin(r.url, a["href"])
            p = urlparse(full)
            links.add(f"{p.scheme}://{p.netloc}{p.path}")

        return data, links

    except Exception:
        return [], set()

# =====================================================
# STREAMLIT UI
# =====================================================

st.set_page_config("Fast Mobile Extractor", "📱", layout="wide")
st.title("📱 FAST Mobile Number Extractor (Entity Safe)")

with st.sidebar:
    start_url = st.text_input("Start URL", "https://codewila.com/")
    max_pages = st.slider("Max Pages", 10, 500, 100)
    workers = st.slider("Threads", 5, 40, 20)
    dedup = st.checkbox("Remove Duplicates", True)

if st.button("🚀 Start Extraction", type="primary"):

    if not start_url:
        st.error("URL required")
        st.stop()

    domain = urlparse(start_url).netloc

    session = requests.Session()
    session.headers.update(HEADERS)

    visited = set()
    seen_numbers = set()
    results = []

    queue = deque([start_url])
    start_time = time.time()

    bar = st.progress(0)
    status = st.empty()
    table = st.empty()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}

        while queue and len(visited) < max_pages:

            while queue and len(futures) < workers:
                url = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                futures[executor.submit(crawl_page, url, session)] = url

            done, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )

            for future in done:
                url = futures.pop(future)
                data, links = future.result()

                for row in data:
                    key = clean_digits(row["Mobile Number"])
                    if not dedup or key not in seen_numbers:
                        seen_numbers.add(key)
                        results.append(row)

                for link in links:
                    if is_internal(link, domain) and link not in visited:
                        queue.append(link)

                bar.progress(min(len(visited) / max_pages, 1.0))
                status.text(f"Scanning: {url}")

                if results:
                    table.dataframe(pd.DataFrame(results), use_container_width=True)

    duration = round(time.time() - start_time, 2)
    st.success(f"✅ DONE | Pages: {len(visited)} | Numbers: {len(results)} | Time: {duration}s")

    if results:
        df = pd.DataFrame(results)
        st.download_button(
            "⬇️ Download CSV",
            df.to_csv(index=False).encode("utf-8"),
            "mobile_numbers.csv",
            "text/csv"
        )
    else:
        st.warning("No numbers found")
