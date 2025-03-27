# YouTube Channel Outlier Analysis Streamlit App

import streamlit as st
import requests
import pandas as pd
import numpy as np
import datetime
import sqlite3
import re

# Load API key
API_KEY = st.secrets["YT_API_KEY"]

# Set up SQLite for history
conn = sqlite3.connect("channel_analysis.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_url TEXT,
    num_videos INTEGER,
    video_type TEXT,
    sort_by TEXT,
    timestamp TEXT
)
""")
conn.commit()

# Utility: Extract channel ID from URL
def extract_channel_id(url):
    patterns = [
        r"youtube\.com/channel/([\w-]+)",
        r"youtube\.com/@([\w-]+)",
        r"youtube\.com/c/([\w-]+)",
        r"youtube\.com/user/([\w-]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url if url.startswith("UC") else None

# YouTube API Helpers
def get_uploads_playlist(channel_id):
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={API_KEY}"
    r = requests.get(url).json()
    try:
        return r['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    except:
        return None

def get_videos_from_playlist(playlist_id, max_results):
    videos = []
    next_page = ""
    while len(videos) < max_results:
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&key={API_KEY}&pageToken={next_page}"
        r = requests.get(url).json()
        for item in r.get("items", []):
            videos.append({
                "video_id": item['snippet']['resourceId']['videoId'],
                "title": item['snippet']['title'],
                "published": item['snippet']['publishedAt']
            })
            if len(videos) >= max_results:
                break
        next_page = r.get("nextPageToken", None)
        if not next_page:
            break
    return videos

def get_video_stats(video_ids):
    stats = []
    for i in range(0, len(video_ids), 50):
        ids = ",".join(video_ids[i:i+50])
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={ids}&key={API_KEY}"
        r = requests.get(url).json()
        for item in r.get("items", []):
            duration = parse_duration(item['contentDetails']['duration'])
            stats.append({
                "video_id": item['id'],
                "title": item['snippet']['title'],
                "published": item['snippet']['publishedAt'],
                "views": int(item['statistics'].get('viewCount', 0)),
                "duration": duration,
                "is_short": duration <= 60
            })
    return stats

def parse_duration(duration_str):
    match = re.findall(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    h, m, s = match[0]
    return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)

# Outlier Score
def calculate_outlier_scores(videos):
    df = pd.DataFrame(videos)
    df['age_days'] = (datetime.datetime.utcnow() - pd.to_datetime(df['published'])).dt.days
    df = df[df['age_days'] > 1]  # Ignore very recent videos
    grouped = df.groupby('age_days')['views']
    avg_views = grouped.transform('mean')
    df['outlier_score'] = df['views'] / avg_views.replace(0, np.nan)
    return df.sort_values("outlier_score", ascending=False)

# Streamlit UI
st.title("YouTube Channel Outlier Analyzer")
mode = st.radio("Choose Mode", ["Channel URL", "Single Video"], index=0)

if mode == "Channel URL":
    channel_url = st.text_input("Enter Channel URL")
    num_videos = st.slider("Number of Videos", 5, 150, 50)
    video_type = st.selectbox("Filter by Type", ["All", "Shorts", "Long-form"])
    sort_by = st.selectbox("Sort by", ["Newest", "Oldest", "Most Popular", "Outlier Score"])

    if st.button("Analyze Channel") and channel_url:
        st.info("Fetching channel videos...")
        channel_id = extract_channel_id(channel_url)
        uploads = get_uploads_playlist(channel_id)
        if not uploads:
            st.error("Could not fetch uploads playlist.")
            st.stop()

        videos = get_videos_from_playlist(uploads, num_videos)
        stats = get_video_stats([v['video_id'] for v in videos])

        # Filter
        if video_type == "Shorts":
            stats = [v for v in stats if v['is_short']]
        elif video_type == "Long-form":
            stats = [v for v in stats if not v['is_short']]

        if not stats:
            st.warning("No videos found for the selected filter.")
            st.stop()

        # Outlier Score
        df = calculate_outlier_scores(stats)

        # Sort
        if sort_by == "Newest":
            df = df.sort_values("published", ascending=False)
        elif sort_by == "Oldest":
            df = df.sort_values("published", ascending=True)
        elif sort_by == "Most Popular":
            df = df.sort_values("views", ascending=False)
        elif sort_by == "Outlier Score":
            df = df.sort_values("outlier_score", ascending=False)

        st.subheader("Outlier Scores")
        st.dataframe(df[['title', 'views', 'age_days', 'outlier_score']])

        # Save to SQLite
        c.execute("INSERT INTO analysis_history (channel_url, num_videos, video_type, sort_by, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (channel_url, num_videos, video_type, sort_by, datetime.datetime.utcnow().isoformat()))
        conn.commit()

    with st.expander("View Analysis History"):
        history = pd.read_sql_query("SELECT * FROM analysis_history ORDER BY timestamp DESC LIMIT 10", conn)
        st.dataframe(history)

else:
    st.warning("Single video analysis coming soon.")
