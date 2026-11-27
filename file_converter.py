import os
import subprocess
import time
import traceback


def convert_webm_to_mp4(webm_filename):
    temp_filename = webm_filename + ".temp." + str(time.time()) + ".mp4"
    final_filename = webm_filename + ".mp4"
    try:
        if (not os.path.isfile(final_filename)):
            if (not os.path.isfile(temp_filename)):
                # TODO: try using ffmpeg-python
                # TODO: add reencoding to x264 for broader compatibility
                # (for now some devices don't show video msgs properly)
                subprocess.run(
                    ["ffmpeg", "-i", webm_filename, "-c", "copy", "-y", temp_filename],
                    check=True,
                )

                if (os.path.isfile(final_filename)):
                    final_filename = webm_filename + str(time.time()) + ".mp4"

                os.rename(temp_filename, final_filename)
                return final_filename
        else:
            return final_filename
    except Exception as e:
        print(time.strftime("%d.%m.%Y %H:%M:%S", time.localtime()))
        traceback.print_exception(type(e), e, e.__traceback__)
        print("Couldn't convert webm to mp4")
        print()

    return ""
