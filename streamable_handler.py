import re
import time
import traceback
import yt_dlp_wrapper
import configparser
import os

config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), "config.txt"))

def handle_url(link):
    print(f"[DEBUG] Received Streamable link: {link}")
    if not config['streamable'].getboolean('enabled'):
        print("[DEBUG] Streamable support disabled in config.")
        return {"error": "Streamable support is disabled."}
    clean_link = clean_up_url(link)
    print(f"[DEBUG] Cleaned link: {clean_link}")
    try:
        info_dict = yt_dlp_wrapper.get_video_info(clean_link)
        if not isinstance(info_dict, dict):
            print("[DEBUG] info_dict is not a dict, aborting.")
            return {"error": "Maximum file size, cant download"}
        max_filesize = 100 * 1024 * 1024  # 100 MB in bytes
        max_duration = 20 * 60  # 20 minutes in seconds
        filesize = None
        if 'filesize' in info_dict and info_dict['filesize']:
            filesize = info_dict['filesize']
        elif 'filesize_approx' in info_dict and info_dict['filesize_approx']:
            filesize = info_dict['filesize_approx']
        elif 'formats' in info_dict and info_dict['formats']:
            for fmt in info_dict['formats']:
                if 'filesize' in fmt and fmt['filesize']:
                    filesize = fmt['filesize']
                    break
                if 'filesize_approx' in fmt and fmt['filesize_approx']:
                    filesize = fmt['filesize_approx']
                    break
        print(f"[DEBUG] Detected filesize: {filesize}")
        duration = info_dict['duration'] if 'duration' in info_dict and info_dict['duration'] else 0
        print(f"[DEBUG] Detected duration: {duration}")
        if (filesize and filesize > max_filesize) or (duration and duration > max_duration):
            print("[DEBUG] File too large or too long, aborting.")
            return {"error": "Maximum file size, cant download"}
        print("[DEBUG] Downloading Streamable video...")
        [output_filename, info_dict] = yt_dlp_wrapper.download(clean_link)
        print(f"[DEBUG] Downloaded file: {output_filename}")
        return prepare_metadata(output_filename, info_dict)
    except Exception as e:
        print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
        traceback.print_exception(type(e), e, e.__traceback__)
        print("Couldn't get video from url: " + link)
        print()
        return {}

def clean_up_url(link):
    print(f"[DEBUG] Cleaning up Streamable URL: {link}")
    link = re.sub(r"[?&]+$", "", link)
    link = re.sub(r"&+", "&", link)
    link = link.replace("?&", "?")
    print(f"[DEBUG] Cleaned URL: {link}")
    return link

def prepare_metadata(output_filename, info_dict):
    print(f"[DEBUG] Preparing metadata for file: {output_filename}")
    return_data = {}
    return_data['site'] = "streamable"
    return_data['id'] = info_dict['id']
    return_data['url'] = info_dict['original_url']
    return_data['text'] = info_dict.get('title', info_dict.get('fulltitle', ''))
    return_data['spoiler'] = False
    return_data['media'] = [[output_filename, "video_file"]]
    return_data['type'] = "media"
    return return_data