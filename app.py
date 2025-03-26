import streamlit as st
import requests
import numpy as np
import datetime
import re

# ------------------------
# App Configuration
# ------------------------
st.set_page_config(page_title="YouTube Outlier Calculator", layout="centered")
st.title("YouTube Outlier Calculator (Approach 3)")
st.markdown(
    """
Calculates the outlier ratio for a long-form video using:
- **Analysis Period:** 795 days  
- **Middle Band:** 50% (i.e. 25th and 75th percentiles)  
- **Formula:**  
\[
\text{Outlier} = \frac{V}{\displaystyle \frac{Q_{0.25} + Q_{0.75}}{2}}
\]
where \(V\) is the target video’s current views.
    """
)

# ------------------------
# Load API Key from Secrets
# ------------------------
try:
    API_KEY = st.secrets["YT_API_KEY"]
    if not API_KEY:
        st.error("YouTube API Key is missing in secrets.toml!")
        st.stop()
except Exception as e:
    st.error(f"Error loading API key: {e}")
    st.stop()

# ------------------------
# Helper Functions
# ------------------------
def extract_video_id(url):
    patterns = [
        r"youtube\.com/watch\?v=([^&\s]+)",
        r"youtu\.be/([^?\s]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[A-Za-z0-9_-]{11}$", url.strip()):
        return url.strip()
    return None

def get_video_details(video_id, api_key):
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={video_id}&key={api_key}"
    resp = requests.get(url).json()
    if "items" not in resp or not resp["items"]:
        return None
    item = resp["items"][0]
    return {
        "videoId": video_id,
        "title": item["snippet"]["title"],
        "channelId": item["snippet"]["channelId"],
        "viewCount": int(item["statistics"].get("viewCount", 0)),
        "publishedAt": item["snippet"]["publishedAt"]
    }

def get_channel_uploads_playlist(channel_id, api_key):
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    resp = requests.get(url).json()
    if "items" in resp and resp["items"]:
        return resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return None

def get_channel_video_ids(playlist_id, api_key, max_results=200):
    video_ids = []
    next_page_token = ""
    while next_page_token is not None:
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&maxResults=50&playlistId={playlist_id}&key={api_key}"
        if next_page_token:
            url += f"&pageToken={next_page_token}"
        resp = requests.get(url).json()
        for item in resp.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])
            if len(video_ids) >= max_results:
                return video_ids
        next_page_token = resp.get("nextPageToken")
    return video_ids

def get_multiple_videos_details(video_ids, api_key):
    details = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        ids_str = ",".join(chunk)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={ids_str}&key={api_key}"
        resp = requests.get(url).json()
        for item in resp.get("items", []):
            vid = item["id"]
            details[vid] = {
                "viewCount": int(item["statistics"].get("viewCount", 0)),
                "publishedAt": item["snippet"]["publishedAt"]
            }
    return details

def iso_to_date(iso_str):
    return datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()

def simulate_views(total_views, video_age, target_days):
    """
    Simulate the cumulative views at target_days.
    If the video is older than target_days, return total_views;
    otherwise, extrapolate linearly.
    """
    if video_age >= target_days:
        return total_views
    else:
        return int(total_views * (target_days / video_age))

# ------------------------
# Core Calculation Functions
# ------------------------
ANALYSIS_DAYS = 795         # Fixed analysis period
BAND_PERCENTAGE = 50        # 50% middle band => 25th & 75th percentiles

def calculate_outlier(target, benchmarks, target_days):
    simulated_views = []
    today = datetime.date.today()
    for vid, details in benchmarks.items():
        pub_date = iso_to_date(details["publishedAt"])
        vid_age = (today - pub_date).days
        if vid_age < 2:
            continue
        sim_views = simulate_views(details["viewCount"], vid_age, target_days)
        simulated_views.append(sim_views)
    if not simulated_views:
        return None, None
    Q25 = np.percentile(simulated_views, 25)
    Q75 = np.percentile(simulated_views, 75)
    channel_avg = (Q25 + Q75) / 2
    outlier_ratio = target["viewCount"] / channel_avg if channel_avg > 0 else None
    return channel_avg, outlier_ratio

# ------------------------
# Streamlit Interface
# ------------------------
st.markdown("## Enter Video URL")
video_url_input = st.text_input("Video URL", placeholder="https://www.youtube.com/watch?v=VIDEO_ID")

if st.button("Calculate Outlier") and video_url_input:
    video_id = extract_video_id(video_url_input)
    if not video_id:
        st.error("Unable to extract video ID. Check the URL.")
        st.stop()
    
    with st.spinner("Fetching target video details..."):
        target_video = get_video_details(video_id, API_KEY)
        if not target_video:
            st.error("Unable to fetch target video details.")
            st.stop()
        target_pub = iso_to_date(target_video["publishedAt"])
        target_age = (datetime.date.today() - target_pub).days
        if target_age < ANALYSIS_DAYS:
            st.error(f"Target video is only {target_age} days old; require at least {ANALYSIS_DAYS} days for analysis.")
            st.stop()
    
    with st.spinner("Fetching channel videos..."):
        channel_id = target_video["channelId"]
        playlist_id = get_channel_uploads_playlist(channel_id, API_KEY)
        if not playlist_id:
            st.error("Unable to fetch channel uploads playlist.")
            st.stop()
        channel_vid_ids = get_channel_video_ids(playlist_id, API_KEY, max_results=200)
        if not channel_vid_ids:
            st.error("No channel videos found.")
            st.stop()
        benchmarks = get_multiple_videos_details(channel_vid_ids, API_KEY)
        # Exclude the target video
        if video_id in benchmarks:
            del benchmarks[video_id]
    
    with st.spinner("Calculating outlier ratio..."):
        channel_avg, outlier = calculate_outlier(target_video, benchmarks, ANALYSIS_DAYS)
        if channel_avg is None or outlier is None:
            st.error("Not enough benchmark data to calculate outlier.")
        else:
            st.success("Calculation complete!")
            st.metric("Target Video Views", f"{target_video['viewCount']:,}")
            st.metric("Channel Average Views (795 days)", f"{int(channel_avg):,}")
            st.metric("Outlier Ratio", f"{outlier:.2f}")
            st.markdown("### Formula Recap")
            st.latex(r"\text{Outlier} = \frac{V}{\frac{Q_{0.25} + Q_{0.75}}{2}}")
            st.markdown(
                "*Where \(V\) is the target video's current views, and \(Q_{0.25}\) and \(Q_{0.75}\) are the 25th and 75th percentiles of the simulated cumulative views at 795 days for the channel's other videos.*"
            )
