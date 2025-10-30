import time
import json
import traceback
import configparser
import os
import requests
from urllib.parse import urlparse
import yt_dlp_wrapper

config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), "config.txt"))


def get_auth_token():
    try:
        if not config['bluesky'].getboolean('enabled'):
            print("Bluesky support disabled in config")
            return None

        auth_response = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={
                "identifier": config['bluesky']['username'],
                "password": config['bluesky']['app_password']
            }
        )

        if auth_response.status_code != 200:
            print(f"Failed to authenticate with Bluesky API: {auth_response.status_code}")
            if auth_response.status_code == 401:
                print("Invalid credentials - check your username and app password")
            return None

        return auth_response.json().get('accessJwt')
    except Exception as e:
        print("Error authenticating with Bluesky API:", str(e))
        return None


def handle_url(link):
    try:
        # Extract post ID from URL
        parsed_url = urlparse(link)
        path_parts = parsed_url.path.strip('/').split('/')

        if len(path_parts) < 4 or path_parts[0] != 'profile':
            print(f"Invalid Bluesky URL format: {link}")
            return {}

        # In Bluesky, URLs are like: https://bsky.app/profile/username.bsky.social/post/postid
        username = path_parts[1]
        post_id = path_parts[3]

        # Get access token
        access_token = get_auth_token()
        if not access_token:
            print("Unable to authenticate with Bluesky API")
            return {}

        headers = {
            'Authorization': f'Bearer {access_token}',
            'User-Agent': 'Telegram Social Media Downloader Bot'
        }

        # Try to get thread view
        thread_response = requests.get(
            "https://bsky.social/xrpc/app.bsky.feed.getPostThread",
            headers=headers,
            params={
                "uri": f"at://{username}/app.bsky.feed.post/{post_id}"
            }
        )

        try:
            print("[DEBUG] Bluesky API thread response:", json.dumps(thread_response.json(), indent=2))
        except Exception:
            print("[DEBUG] Bluesky API thread response: (no json)")

        if thread_response.status_code != 200:
            print(f"Failed to fetch post data: {thread_response.status_code}")
            return {}

        thread_json = thread_response.json()
        thread_data = thread_json.get('thread', {})
        post_data = thread_data

        # If this is a reply and we have a root post, try to get that content too
        post_obj = thread_data.get('post', {})
        record = post_obj.get('record', {})
        reply = record.get('reply', {}) if isinstance(record, dict) else {}
        root = reply.get('root', {}) if isinstance(reply, dict) else {}
        if root and 'uri' in root:
            root_uri = root['uri']
            root_response = requests.get(
                "https://bsky.social/xrpc/app.bsky.feed.getPost",
                headers=headers,
                params={"uri": root_uri}
            )
            if root_response.status_code == 200:
                try:
                    root_data = root_response.json()
                    print("[DEBUG] Bluesky API root post response:", json.dumps(root_data, indent=2))
                    # Use the root post if it has images/embed
                    if 'post' in root_data and 'embed' in root_data['post']:
                        root_embed = root_data['post']['embed']
                        root_embed_type = root_embed.get('$type', '') if isinstance(root_embed, dict) else ''
                        if 'images' in root_embed_type:
                            post_data = {'post': root_data['post']}
                except Exception:
                    print("[DEBUG] Could not parse root response JSON")

        return parse_post(post_data, link)

    except Exception as e:
        print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
        traceback.print_exception(type(e), e, e.__traceback__)
        print(f"Couldn't get post from url: {link}")
        print()
        return {}


def parse_post(post_data, original_url):
    return_data = {}
    return_data['site'] = "bluesky"
    # defensive access
    post = post_data.get('post', {}) if isinstance(post_data, dict) else {}
    return_data['id'] = post.get('cid', '')
    return_data['url'] = original_url
    return_data['spoiler'] = False
    return_data['text'] = ''

    # record may contain the text and embeds
    record = post.get('record', {}) if isinstance(post, dict) else {}
    if record:
        return_data['text'] = record.get('text', '')

        # If this is a reply, try to get parent post info
        reply = record.get('reply', {})
        if reply:
            root_uri = reply.get('root', {}).get('uri', '')
            if root_uri:
                parts = root_uri.split('//')
                if len(parts) > 1:
                    path = parts[1].replace('app.bsky.feed.post/', '/post/')
                    return_data['reply_url'] = f"https://bsky.app/profile/{path}"
                    return_data['reply'] = True
            else:
                return_data['reply'] = False
        else:
            return_data['reply'] = False
    else:
        return_data['reply'] = False

    # Get author information
    author = post.get('author', {})
    display_name = author.get('displayName', '')
    handle = author.get('handle', '')
    if display_name and handle:
        return_data['author'] = f"{display_name} (@{handle})"
    elif handle:
        return_data['author'] = f"@{handle}"
    else:
        return_data['author'] = ''

    # Default to text type
    return_data['type'] = "text"
    return_data['media'] = []

    # Handle embedded media: check top-level post embed first, then record.embed
    embed = post.get('embed') if isinstance(post, dict) else None
    if embed and isinstance(embed, dict):
        embed_type = embed.get('$type', '')

        # Handle recordWithMedia type (nested media)
        if embed_type == 'app.bsky.embed.recordWithMedia#view' or embed_type.startswith('app.bsky.embed.recordWithMedia'):
            media = embed.get('media', {})
            if isinstance(media, dict) and ('video' in media.get('$type', '')):
                # Treat the nested video the same way as a top-level video
                embed = media
                embed_type = media.get('$type', '')

        # --- VIDEO: top-level embed (app.bsky.embed.video#view) ---
        # Some posts expose video at top-level embed (with playlist/thumbnail),
        # others embed video inside record.embed.video (blob ref). Handle both.
        if 'video' in embed_type:
            # top-level view contains playlist/thumbnail
            playlist = embed.get('playlist') or embed.get('video') and embed.get('video').get('url')
            thumb = embed.get('thumbnail')
            alt = embed.get('alt', '') or record.get('text', '')
            if alt:
                if return_data['text']:
                    return_data['text'] += f"\n[Video: {alt}]"
                else:
                    return_data['text'] = f"[Video: {alt}]"

            # Prefer to download HLS playlists into local file to send as 'video_file'
            if playlist:
                try:
                    if isinstance(playlist, str) and (playlist.endswith('.m3u8') or 'playlist' in playlist or '/watch/' in playlist):
                        print(f"[DEBUG] Attempting to download Bluesky video playlist: {playlist}")
                        # Use post ID in the filename
                        output_template = f"video.bsky.{return_data['id']}.%(ext)s"
                        downloaded = yt_dlp_wrapper.download(playlist, output_template=output_template, thumbnail_url=thumb if thumb else None)
                        filename = None
                        if isinstance(downloaded, (list, tuple)) and len(downloaded) > 0:
                            filename = downloaded[0]
                        elif isinstance(downloaded, str):
                            filename = downloaded

                        if filename and os.path.isfile(filename):
                            return_data['media'].append([filename, 'video_file'])
                        else:
                            print(f"[DEBUG] yt-dlp did not produce a local file, falling back to remote playlist: {downloaded}")
                            return_data['media'].append([playlist, 'video'])
                    else:
                        return_data['media'].append([playlist, 'video'])
                except Exception as e:
                    print(f"[DEBUG] Failed to download playlist: {e}")
                    return_data['media'].append([playlist, 'video'])

            # fallback: check record.embed.video (blob) for direct blob ref
            elif 'embed' in record and isinstance(record['embed'], dict) and 'video' in record['embed']:
                video_blob = record['embed']['video']
                if isinstance(video_blob, dict) and video_blob.get('$type') == 'blob':
                    blob_ref = video_blob.get('ref', {})
                    if '$link' in blob_ref:
                        blob_url = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={post.get('author', {}).get('did', '')}&cid={blob_ref['$link']}"
                        return_data['media'].append([blob_url, 'video'])

        # --- images view ---
        if embed_type == 'app.bsky.embed.images#view' or 'images' in embed_type:
            for image in embed.get('images', []):
                alt = image.get('alt', '')
                if alt:
                    if return_data['text']:
                        return_data['text'] += f"\n[Image: {alt}]"
                    else:
                        return_data['text'] = f"[Image: {alt}]"
                # Try to get the best quality image URL
                if 'fullsize' in image:
                    return_data['media'].append([image['fullsize'], "photo"])
                elif 'thumb' in image:
                    return_data['media'].append([image['thumb'], "photo"])

    # Fallback to embed inside record
    record_embed = record.get('embed') if isinstance(record, dict) else None
    if (not return_data['media']) and record_embed and isinstance(record_embed, dict):
        r_type = record_embed.get('$type', '')
        
        # Handle recordWithMedia in record.embed
        if r_type == 'app.bsky.embed.recordWithMedia' or r_type.startswith('app.bsky.embed.recordWithMedia'):
            media = record_embed.get('media', {})
            if isinstance(media, dict) and media.get('$type') == 'app.bsky.embed.video':
                video_blob = media.get('video', {})
                if isinstance(video_blob, dict) and video_blob.get('$type') == 'blob':
                    blob_ref = video_blob.get('ref', {})
                    if '$link' in blob_ref:
                        blob_url = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={post.get('author', {}).get('did', '')}&cid={blob_ref['$link']}"
                        return_data['media'].append([blob_url, 'video'])

        elif r_type.startswith('app.bsky.embed.images'):
            for image in record_embed.get('images', []):
                alt = image.get('alt', '')
                if alt:
                    if return_data['text']:
                        return_data['text'] += f"\n[Image: {alt}]"
                    else:
                        return_data['text'] = f"[Image: {alt}]"
                # blob image
                img_ref = None
                if 'image' in image and isinstance(image['image'], dict) and image['image'].get('$type') == 'blob':
                    blob = image['image'].get('ref', {})
                    if '$link' in blob:
                        img_ref = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={post.get('author', {}).get('did', '')}&cid={blob['$link']}"
                if img_ref:
                    return_data['media'].append([img_ref, 'photo'])

        elif r_type == 'app.bsky.embed.external#main' or r_type.startswith('app.bsky.embed.external'):
            external = record_embed.get('external', {})
            if return_data['text']:
                return_data['text'] += f"\n\nExternal link: {external.get('uri', '')}"
            else:
                return_data['text'] = f"External link: {external.get('uri', '')}"
            if 'thumb' in external and isinstance(external['thumb'], dict) and '$link' in external['thumb']:
                img_url = f"https://bsky.social/xrpc/com.atproto.sync.getBlob?did={post.get('author', {}).get('did', '')}&cid={external['thumb']['$link']}"
                return_data['media'].append([img_url, "photo"])

    # Set type to media if we found any media items
    if return_data['media']:
        return_data['type'] = "media"

    # Add quote post handling
    if record_embed and isinstance(record_embed, dict) and 'record' in record_embed:
        quoted = record_embed['record']
        if isinstance(quoted, dict) and 'record' in quoted:
            quoted_text = quoted['record'].get('text', '')
            quoted_author = quoted.get('author', {}).get('handle', '')
            if quoted_text:
                quote_info = f"\n\nQuoted post from @{quoted_author}:\n{quoted_text}"
                return_data['text'] += quote_info

    return return_data