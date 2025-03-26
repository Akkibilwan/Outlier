import streamlit as st
import requests
import numpy as np
import datetime
import re

# ------------------------
# Streamlit App Configuration
# ------------------------
st.set_page_config(page_title="YouTube Video Outlier Calculator", page_icon="📊", layout="centered")
st.title("YouTube Video Outlier Calculator")
st.markdown("Enter a YouTube video URL and the app will calculate its outlier ratio relative to its channel's typical performance.")

# ------------------------
# Load API Key from secrets.toml
# ------------------------
try:
    API_KEY = st.secrets["YT_API_KEY"]
    if not API_KEY:
        st.error("YouTube API Key missing in secrets.toml!")
        st.stop()
except Exception as e:
    st.error(f"Error loading API key: {e}")
    st.stop()

# ------------------------
# Helper Functions
# ------------------------

def extract_video_id(url):
    """Extracts the video ID from various YouTube URL formats."""
    patterns = [
        r"youtube\.com/watch\?v=([^&\s]+)",
        r"youtu\.be/([^?\s]+)",
        r"youtube\.com/embed/([^?\s]+)",
        r"youtube\.com/v/([^?\s]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Fallback: if the URL itself is an 11-character ID
    if re.match(r"^[A-Za-z0-9_-]{11}$", url.strip()):
        return url.strip()
    return None

def get_video_details(video_id, api_key):
    """Fetches target video details using the YouTube API."""
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={api_key}"
    response = requests.get(url).json()
    if "items" not in response or not response["items"]:
        return None
    item = response["items"][0]
    return {
        "videoId": video_id,
        "title": item["snippet"]["title"],
        "channelId": item["snippet"]["channelId"],
        "channelTitle": item["snippet"]["channelTitle"],
        "publishedAt": item["snippet"]["publishedAt"],
        "viewCount": int(item["statistics"].get("viewCount", 0))
    }

def get_channel_uploads_playlist(channel_id, api_key):
    """Retrieves the uploads playlist ID for a given channel."""
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    response = requests.get(url).json()
    if "items" in response and response["items"]:
        return response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return None

def get_channel_video_ids(playlist_id, api_key, max_results=200):
    """Fetches video IDs from the channel's uploads playlist."""
    video_ids = []
    next_page_token = ""
    while next_page_token is not None:
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&maxResults=50&playlistId={playlist_id}&key={api_key}"
        if next_page_token:
            url += f"&pageToken={next_page_token}"
        response = requests.get(url).json()
        for item in response.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])
            if len(video_ids) >= max_results:
                return video_ids
        next_page_token = response.get("nextPageToken")
    return video_ids

def get_multiple_videos_details(video_ids, api_key):
    """Fetches details for multiple videos using the YouTube API."""
    details = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        ids_str = ",".join(chunk)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={ids_str}&key={api_key}"
        response = requests.get(url).json()
        for item in response.get("items", []):
            vid = item["id"]
            details[vid] = {
                "publishedAt": item["snippet"]["publishedAt"],
                "viewCount": int(item["statistics"].get("viewCount", 0))
            }
    return details

def iso_to_date(iso_str):
    """Converts an ISO 8601 timestamp to a date object."""
    return datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()

def simulate_cumulative_views(total_views, video_age, target_day):
    """
    Estimates what the cumulative view count would be at 'target_day' days.
    If the video is older than target_day, returns total_views.
    Otherwise, uses linear extrapolation.
    """
    if video_age >= target_day:
        return total_views
    else:
        return int(total_views * (target_day / video_age))

def calculate_outlier(target_details, benchmark_details, target_age_days):
    """
    For each benchmark video (from the channel, excluding the target),
    simulate the cumulative view count at the target video's age.
    Then compute the 25th and 75th percentiles, take their average as the channel average,
    and compute the outlier ratio.
    """
    today = datetime.date.today()
    simulated_views_list = []
    for vid, details in benchmark_details.items():
        video_age = (today - iso_to_date(details["publishedAt"])).days
        simulated_views = simulate_cumulative_views(details["viewCount"], video_age, target_age_days)
        simulated_views_list.append(simulated_views)
    if not simulated_views_list:
        return None, None, None
    Q_low = np.percentile(simulated_views_list, 25)
    Q_high = np.percentile(simulated_views_list, 75)
    channel_average = (Q_low + Q_high) / 2
    outlier_ratio = target_details["viewCount"] / channel_average if channel_average > 0 else None
    return channel_average, outlier_ratio, simulated_views_list

# ------------------------
# Streamlit App Interface
# ------------------------

video_url_input = st.text_input("Enter YouTube Video URL:", placeholder="https://www.youtube.com/watch?v=VIDEO_ID")

if st.button("Calculate Outlier") and video_url_input:
    with st.spinner("Fetching target video details..."):
        video_id = extract_video_id(video_url_input)
        if not video_id:
            st.error("Could not extract video ID. Please check the URL.")
            st.stop()
        target_video = get_video_details(video_id, API_KEY)
        if not target_video:
            st.error("Failed to fetch target video details.")
            st.stop()
        published_date = iso_to_date(target_video["publishedAt"])
        target_age = (datetime.date.today() - published_date).days
        if target_age < 2:
            st.error("Target video is too new for analysis (minimum 2 days required).")
            st.stop()
    
    with st.spinner("Fetching channel videos..."):
        # Use the channel ID from the target video
        channel_id = target_video["channelId"]
        playlist_id = get_channel_uploads_playlist(channel_id, API_KEY)
        if not playlist_id:
            st.error("Could not fetch channel uploads playlist.")
            st.stop()
        channel_video_ids = get_channel_video_ids(playlist_id, API_KEY, max_results=200)
        if not channel_video_ids:
            st.error("No videos found on the channel.")
            st.stop()
        # Fetch details for all channel videos
        channel_details = get_multiple_videos_details(channel_video_ids, API_KEY)
        # Exclude the target video from the benchmark data
        if video_id in channel_details:
            del channel_details[video_id]
    
    with st.spinner("Calculating outlier ratio..."):
        channel_avg, outlier_ratio, bench_list = calculate_outlier(target_video, channel_details, target_age)
        if channel_avg is None or outlier_ratio is None:
            st.error("Not enough benchmark data.")
        else:
            st.success("Calculation complete!")
            st.metric("Target Video Views", f"{target_video['viewCount']:,}")
            st.metric("Channel Average Views (at target age)", f"{int(channel_avg):,}")
            st.metric("Outlier Ratio", f"{outlier_ratio:.2f}")
            st.markdown("### Formula Recap")
            st.latex(r"Outlier = \frac{V}{\frac{Q_{0.25} + Q_{0.75}}{2}}")
            st.markdown(
                "*Where \(V\) is the target video's view count, and \(Q_{0.25}\) and \(Q_{0.75}\) are the 25th and 75th percentiles of the simulated cumulative views (at the target video's age) for the channel's other videos.*"
            )
