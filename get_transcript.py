import sys
import os
import urllib.request
import json
import re

video_id = "YX0x13gFcYw"
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['pt', 'en'])
    text = " ".join([x['text'] for x in transcript])
    print("TRANSCRIPT:", text[:4000]) # Print first 4000 chars
except Exception as e:
    print("Could not download via library:", e)
