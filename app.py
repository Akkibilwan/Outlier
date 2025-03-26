import streamlit as st
import requests
import numpy as np
import datetime
import re

# Page configuration
st.set_page_config(
    page_title="YouTube Video Outlier Calculator",
    page_icon="📊",
    layout="centered"
)

st.title("YouTube Video Outlier Calculator")
st.markdown("Enter a YouTube video URL and the app will calculate its outlier ratio compared to its channel’s typical performance.")

# Get API key from secrets
try:
    API_KEY = st.secrets["YT_API_KEY"]
    if not API_KEY:
        st.error("YouTube API Key is missing from secrets.toml!")
        st.stop()
except Exception as e:
    st.error(f"Error loading API key from secrets: {e}")
    st.stop()

# --- Helper functions ---

def extract_video_id(url):
    """Extracts the video ID from a YouTube URL."""
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
    # If not matched, try to see if it's a direct video ID (11 characters)
    if re.match(r"^[A-Za-z0-9_-]{11}$", url.strip()):
        return url.strip()
    return None

def get_video_details(video_id, api_key):
    """Fetches details for the given video ID."""
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={api_key}"
    response = requests.get(url).json()
    if 'items' not in response or not response['items']:
        return None
    item = response['items'][0]
    details = {
        "videoId": video_id,
        "title": item["snippet"]["title"],
        "channelId": item["snippet"]["channelId"],
        "channelTitle": item["snippet"]["channelTitle"],
        "publishedAt": item["snippet"]["publishedAt"],
        "viewCount": int(item["statistics"].get("viewCount", 0))
    }
    return details

def get_channel_uploads_playlist(channel_id, api_key):
    """Returns the uploads playlist ID for the given channel."""
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    response = requests.get(url).json()
    if "items" in response and response["items"]:
        return response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return None

def get_channel_video_ids(playlist_id, api_key, max_results=None):
    """Fetches video IDs from the given uploads playlist."""
    video_ids = []
    next_page_token = ""
    while next_page_token is not None:
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&maxResults=50&playlistId={playlist_id}&key={api_key}"
        if next_page_token:
            url += f"&pageToken={next_page_token}"
        response = requests.get(url).json()
        for item in response.get("items", []):
            vid = item["contentDetails"]["videoId"]
            video_ids.append(vid)
            if max_results and len(video_ids) >= max_results:
                return video_ids
        next_page_token = response.get("nextPageToken")
    return video_ids

def get_multiple_videos_details(video_ids, api_key):
    """Fetches details for multiple videos."""
    details = {}
    # The YouTube API allows up to 50 IDs per request.
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        ids_str = ",".join(chunk)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={ids_str}&key={api_key}"
        response = requests.get(url).json()
        for item in response.get("items", []):
            vid = item["id"]
            details[vid] = {
                "publishedAt": item["snippet"]["publishedAt"],
                "viewCount": int(item["statistics"].get("viewCount", 0)),
                "duration": item["contentDetails"]["duration"]  # ISO 8601 duration (not used here)
            }
    return details

def iso_to_date(iso_str):
    """Converts an ISO 8601 string to a date."""
    return datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()

# --- Main Outlier Calculation ---

def calculate_outlier(target_details, benchmark_details, target_age_days):
    """
    Given the target video's details and a dict of benchmark video details (each with total views),
    compute the 25th and 75th percentiles of the benchmark videos’ total views (at least for videos that
    are older than the target video's age) and return the outlier ratio.
    """
    # Collect benchmark video total views for videos that are at least as old as the target video.
    cumulative_views_list = []
    for vid, details in benchmark_details.items():
        video_age = (datetime.date.today() - iso_to_date(details["publishedAt"])).days
        if video_age >= target_age_days:
            # In this simplified model, we use the total view count as the cumulative view at target_age_days.
            cumulative_views_list.append(details["viewCount"])
    
    if not cumulative_views_list:
        return None, None, None  # Not enough data
    
    Q_low = np.percentile(cumulative_views_list, 25)   # 25th percentile
    Q_high = np.percentile(cumulative_views_list, 75)    # 75th percentile
    channel_average = (Q_low + Q_high) / 2
    outlier_ratio = target_details["viewCount"] / channel_average if channel_average > 0 else None
    return channel_average, outlier_ratio, cumulative_views_list

# --- Streamlit App Interface ---

video_url_input = st.text_input("Enter YouTube Video URL:", placeholder="https://www.youtube.com/watch?v=VIDEO_ID")

if st.button("Calculate Outlier") and video_url_input:
    with st.spinner("Fetching video details..."):
        video_id = extract_video_id(video_url_input)
        if not video_id:
            st.error("Could not extract video ID. Please check the URL.")
            st.stop()
        target_video = get_video_details(video_id, API_KEY)
        if not target_video:
            st.error("Failed to fetch video details. Please check the video URL.")
            st.stop()
        # Determine target video's age (in days)
        published_date = iso_to_date(target_video["publishedAt"])
        target_age = (datetime.date.today() - published_date).days
        if target_age < 2:
            st.error("Target video is too new for analysis (must be at least 2 days old).")
            st.stop()
    
    with st.spinner("Fetching channel videos..."):
        # Use the channel from the target video
        channel_id = target_video["channelId"]
        playlist_id = get_channel_uploads_playlist(channel_id, API_KEY)
        if not playlist_id:
            st.error("Could not fetch channel uploads playlist.")
            st.stop()
        # For this example, we fetch up to 200 videos (you can adjust as needed)
        channel_video_ids = get_channel_video_ids(playlist_id, API_KEY, max_results=200)
        if not channel_video_ids:
            st.error("No videos found in the channel.")
            st.stop()
        # Get details for all channel videos
        channel_video_details = get_multiple_videos_details(channel_video_ids, API_KEY)
        # Remove the target video from benchmark set
        if video_id in channel_video_details:
            del channel_video_details[video_id]
    
    with st.spinner("Calculating Outlier..."):
        channel_avg, outlier_ratio, bench_list = calculate_outlier(target_video, channel_video_details, target_age)
        if channel_avg is None or outlier_ratio is None:
            st.error("Not enough benchmark data (no channel videos are older than the target video's age).")
        else:
            st.success("Calculation complete!")
            st.metric("Target Video Views", f"{target_video['viewCount']:,}")
            st.metric("Channel Average Views at Age", f"{int(channel_avg):,}")
            st.metric("Outlier Ratio", f"{outlier_ratio:.2f}")
            st.markdown("### Formula Recap")
            st.latex(r"Outlier = \frac{V}{\frac{Q_{0.25}+Q_{0.75}}{2}}")
            st.markdown("*Where \(V\) is the target video’s current views, and \(Q_{0.25}\) and \(Q_{0.75}\) are the 25th and 75th percentiles of the benchmark videos’ total views (at least for videos older than the target video's age).*")
