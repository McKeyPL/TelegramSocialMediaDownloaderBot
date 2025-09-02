import requests
import re
from telebot.formatting import escape_markdown
from html import unescape

def html_to_markdown(text):
    # Replace <br> and <p> with newlines
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p\s*>', '\n', text)
    text = re.sub(r'<p\s*>', '', text)

    # Bold: <strong> or <b>
    text = re.sub(r'<(strong|b)>(.*?)</\1>', r'*\2*', text)
    # Italic: <em> or <i>
    text = re.sub(r'<(em|i)>(.*?)</\1>', r'_\2_', text)
    # Strikethrough: <del>
    text = re.sub(r'<del>(.*?)</del>', r'~\1~', text)
    # Inline code: <code>
    text = re.sub(r'<code>(.*?)</code>', r'`\1`', text)

    # Links: <a href="...">text</a>
    def link_replacer(match):
        url = match.group(1)
        label = match.group(2)
        # Escape label and url for MarkdownV2
        from telebot.formatting import escape_markdown
        label_escaped = escape_markdown(unescape(label))
        url_escaped = escape_markdown(unescape(url))
        return f'[{label_escaped}]({url_escaped})'
    text = re.sub(r'<a href="([^"]+)".*?>(.*?)</a>', link_replacer, text)

    # Remove all other tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = unescape(text)
    # Escape for Telegram MarkdownV2 (except links, already escaped)
    text = escape_markdown(text)
    # But unescape already escaped links (avoid double escaping)
    text = re.sub(r'\\\[(.*?)\\\]\(\\\((.*?)\\\)\)', r'[\1](\2)', text)
    return text.strip()

def html_to_clean_text(text):
    # Replace <br> and </p> with newlines, remove <p>
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*>', '', text, flags=re.IGNORECASE)
    # Remove all other HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = unescape(text)
    # Remove extra consecutive newlines
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()

def is_mastodon_instance(domain):
    """Check if a domain is a Mastodon instance (v2 or v1 API)."""
    for api_version in ["v2", "v1"]:
        try:
            resp = requests.get(f"https://{domain}/api/{api_version}/instance", timeout=3)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                return True
        except Exception:
            continue
    return False

def is_sharkey_instance(domain):
    """Check if a domain is a Sharkey instance by inspecting the API version string."""
    for api_version in ["v2", "v1"]:
        try:
            resp = requests.get(f"https://{domain}/api/{api_version}/instance", timeout=3)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
                if "version" in data and "Sharkey" in data["version"]:
                    return True
        except Exception:
            continue
    return False

def extract_links(text):
    """Extract all URLs from a text."""
    return re.findall(r'https?://[^\s]+', text)

def is_mastodon_link(url):
    """Return True if the URL is a Mastodon or Sharkey status/note link."""
    # Standard Mastodon status
    if re.match(r"https?://[^/]+/@[^/]+/\d+", url):
        return True
    # Sharkey note (e.g., https://catgirl.center/notes/<note_id>)
    if re.match(r"https?://[^/]+/notes/[a-zA-Z0-9]+", url):
        return True
    return False

def handle_url(url):
    print(f"[DEBUG] handle_url called with: {url}")
    # Debbugs links (unsupported)
    if re.match(r"https?://debbugs[\w\.-]+", url):
        print(f"[DEBUG] Debbugs link detected: {url}")
        return {"type": "error", "site": "debbugs", "url": url, "error": "Can't download debbugs links."}
    # Standard Mastodon status (thread handler)
    m = re.match(r"https?://([^/]+)/@[^/]+/(\d+)", url)
    if m:
        domain, status_id = m.groups()
        print(f"[DEBUG] Mastodon status detected: domain={domain}, status_id={status_id}")
        thread = []
        current_id = status_id
        used_api_version = None
        # Try to find working API version first
        for api_version in ["v2", "v1"]:
            api_url = f"https://{domain}/api/{api_version}/statuses/{current_id}"
            print(f"[DEBUG] Trying API URL: {api_url}")
            try:
                resp = requests.get(api_url, timeout=5)
                print(f"[DEBUG] Response status: {resp.status_code}")
                if resp.status_code == 200:
                    used_api_version = api_version
                    break
            except Exception as e:
                print(f"[DEBUG] Exception: {e}")
                continue
        if not used_api_version:
            print(f"[DEBUG] Could not download Mastodon status: {url}")
            return {"type": "error", "site": "mastodon", "url": url, "error": "Can't download"}
        # Download thread
        while current_id:
            api_url = f"https://{domain}/api/{used_api_version}/statuses/{current_id}"
            print(f"[DEBUG] Downloading thread status: {api_url}")
            try:
                resp = requests.get(api_url, timeout=5)
                print(f"[DEBUG] Response status: {resp.status_code}")
                if resp.status_code != 200:
                    print(f"[DEBUG] Failed to download status {current_id}")
                    break
                data = resp.json()
                print(f"[DEBUG] Data received: {data}")
                media_attachments = []
                for media in data.get("media_attachments", []):
                    print(f"[DEBUG] Media: {media}")
                    if media["type"] == "image":
                        media_attachments.append((media["url"], "photo"))
                    elif media["type"] in ["video", "gifv"]:
                        media_attachments.append((media["url"], "video"))
                handler_response = {
                    "type": "media" if media_attachments else "text",
                    "site": "mastodon",
                    "text": html_to_clean_text(data.get("content", "")),
                    "author": data.get("account", {}).get("acct", ""),
                    "url": f"https://{domain}/@{data.get('account', {}).get('acct', '')}/{current_id}",
                    "media": media_attachments,
                    "spoiler": False,
                }
                print(f"[DEBUG] Handler response: {handler_response}")
                thread.insert(0, handler_response)  # Insert at start for chronological order
                current_id = data.get("in_reply_to_id")
            except Exception as e:
                print(f"[DEBUG] Exception: {e}")
                break
        if len(thread) > 1:
            print(f"[DEBUG] Returning thread with {len(thread)} statuses")
            return {"type": "thread", "site": "mastodon", "thread": thread, "url": url}
        elif thread:
            print(f"[DEBUG] Returning single status")
            return thread[0]
        else:
            print(f"[DEBUG] No statuses downloaded")
            return {"type": "error", "site": "mastodon", "url": url, "error": "Can't download"}
    # Sharkey note (Misskey/Firefish compatible)
    m = re.match(r"https?://([^/]+)/notes/([a-zA-Z0-9]+)", url)
    if m:
        domain, note_id = m.groups()
        api_url = f"https://{domain}/api/notes/show"
        print(f"[DEBUG] Sharkey note detected: domain={domain}, note_id={note_id}")
        print(f"[DEBUG] Trying API URL: {api_url}")
        try:
            resp = requests.post(api_url, headers={"Content-Type": "application/json"}, json={"noteId": note_id}, timeout=5)
            print(f"[DEBUG] Response status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"[DEBUG] Data received: {data}")
                text = data.get("text", "")
                author = data.get("user", {}).get("username", "")
                media_attachments = []
                for file in data.get("files", []):
                    print(f"[DEBUG] File: {file}")
                    mime = file.get("type", "")
                    url_ = file.get("url", "")
                    if mime.startswith("image/"):
                        media_attachments.append((url_, "photo"))
                    elif mime.startswith("video/"):
                        media_attachments.append((url_, "video"))
                spoiler = bool(data.get("cw"))
                handler_response = {
                    "type": "media" if media_attachments else "text",
                    "site": "sharkey",
                    "text": html_to_clean_text(text),
                    "author": author,
                    "url": url,
                    "media": media_attachments,
                    "spoiler": spoiler,
                }
                print(f"[DEBUG] Handler response: {handler_response}")
                return handler_response
        except Exception as e:
            print(f"[DEBUG] Exception: {e}")
        print(f"[DEBUG] Could not download Sharkey note: {url}")
        return {"type": "error", "site": "sharkey", "url": url, "error": "Can't download"}
    print(f"[DEBUG] URL not recognized or unsupported: {url}")
    return {"type": "error", "site": "unknown", "url": url, "error": "Can't download"}
