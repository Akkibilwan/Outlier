import streamlit as st
import requests
import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go
import re
import sqlite3
from datetime import timedelta

# Set YouTube API Key from secrets
if "YT_API_KEY" in st.secrets:
    yt_api_key = st.secrets["YT_API_KEY"]
else:
    st.error("YouTube API key not found in st.secrets. Please add it to your secrets.")
    st.stop()

# Page configuration
st.set_page_config(
    page_title="YouTube Video Outlier Analysis",
    page_icon="📊",
    layout="wide"
)

# Custom CSS styling
st.markdown("""
<style>
    .main-header { font-size: 2rem; font-weight: 600; margin-bottom: 1rem; color: #333; }
    .subheader { font-size: 1.5rem; font-weight: 500; margin: 1rem 0; color: #333; }
    .metric-card { padding: 1rem; border-radius: 10px; margin-bottom: 1rem; text-align: center;
                   background-color: #f0f2f6; color: #333; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
    .outlier-high { color: #1e8e3e; font-weight: bold; }
    .outlier-normal { color: #188038; font-weight: normal; }
    .outlier-low { color: #c53929; font-weight: bold; }
    .explanation { padding: 1rem; border-left: 4px solid #4285f4; background-color: #f8f9fa; 
                   color: #333; margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-header'>YouTube Video Outlier Analysis</div>", unsafe_allow_html=True)
st.markdown("Analyze single videos or perform a channel-wide analysis to see which videos are outliers compared to the channel's typical performance.")

# ------------------------------------------------
# URL Parsing Functions
# ------------------------------------------------
def extract_channel_id(url):
    """Extract channel ID from various YouTube URL formats."""
    patterns = [
        r'youtube\.com/channel/([^/\s?]+)',
        r'youtube\.com/c/([^/\s?]+)',
        r'youtube\.com/user/([^/\s?]+)',
        r'youtube\.com/@([^/\s?]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            identifier = match.group(1)
            if pattern == patterns[0] and identifier.startswith('UC'):
                return identifier
            return get_channel_id_from_identifier(identifier, pattern)
    if url.strip().startswith('UC'):
        return url.strip()
    return None

def extract_video_id(url):
    """Extract video ID from various YouTube URL formats, including Shorts."""
    patterns = [
        r'youtube\.com/watch\?v=([^&\s]+)',
        r'youtu\.be/([^?\s]+)',
        r'youtube\.com/embed/([^?\s]+)',
        r'youtube\.com/v/([^?\s]+)',
        r'youtube\.com/shorts/([^?\s]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r'^[A-Za-z0-9_-]{11}$', url.strip()):
        return url.strip()
    return None

def get_channel_id_from_identifier(identifier, pattern_used):
    """Resolve a channel ID from a channel name, username, or handle."""
    try:
        if pattern_used == r'youtube\.com/channel/([^/\s?]+)':
            return identifier
        elif pattern_used in [r'youtube\.com/c/([^/\s?]+)', r'youtube\.com/@([^/\s?]+)']:
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        elif pattern_used == r'youtube\.com/user/([^/\s?]+)':
            username_url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forUsername={identifier}&key={yt_api_key}"
            username_res = requests.get(username_url).json()
            if 'items' in username_res and username_res['items']:
                return username_res['items'][0]['id']
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        else:
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        search_res = requests.get(search_url).json()
        if 'items' in search_res and search_res['items']:
            return search_res['items'][0]['id']['channelId']
    except Exception as e:
        st.error(f"Error resolving channel identifier: {e}")
    return None

# ------------------------------------------------
# Data Fetching Functions
# ------------------------------------------------
def fetch_single_video(video_id, api_key):
    """Fetch details for a single video."""
    video_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={api_key}"
    try:
        response = requests.get(video_url).json()
        if 'items' not in response or not response['items']:
            return None
        video_data = response['items'][0]
        duration_str = video_data['contentDetails']['duration']
        duration_seconds = parse_duration(duration_str)
        return {
            'videoId': video_id,
            'title': video_data['snippet']['title'],
            'channelId': video_data['snippet']['channelId'],
            'channelTitle': video_data['snippet']['channelTitle'],
            'publishedAt': video_data['snippet']['publishedAt'],
            'thumbnailUrl': video_data['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
            'viewCount': int(video_data['statistics'].get('viewCount', 0)),
            'likeCount': int(video_data['statistics'].get('likeCount', 0)),
            'commentCount': int(video_data['statistics'].get('commentCount', 0)),
            'duration': duration_seconds,
            'isShort': duration_seconds <= 60
        }
    except Exception as e:
        st.error(f"Error fetching video details: {e}")
        return None

def fetch_channel_videos(channel_id, max_videos, api_key):
    """Fetch videos from a channel using its uploads playlist."""
    playlist_url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails,snippet,statistics&id={channel_id}&key={api_key}"
    try:
        playlist_res = requests.get(playlist_url).json()
        if 'items' not in playlist_res or not playlist_res['items']:
            st.error("Invalid Channel ID or no uploads found.")
            return None, None, None
        channel_info = playlist_res['items'][0]
        channel_name = channel_info['snippet']['title']
        channel_stats = channel_info['statistics']
        uploads_playlist_id = channel_info['contentDetails']['relatedPlaylists']['uploads']
        videos = []
        next_page_token = ""
        while (max_videos is None or len(videos) < max_videos) and next_page_token is not None:
            playlist_items_url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails,snippet&maxResults=50&playlistId={uploads_playlist_id}&key={api_key}"
            if next_page_token:
                playlist_items_url += f"&pageToken={next_page_token}"
            playlist_items_res = requests.get(playlist_items_url).json()
            for item in playlist_items_res.get('items', []):
                video_id = item['contentDetails']['videoId']
                title = item['snippet']['title']
                published_at = item['snippet']['publishedAt']
                videos.append({
                    'videoId': video_id,
                    'title': title,
                    'publishedAt': published_at
                })
                if max_videos is not None and len(videos) >= max_videos:
                    break
            next_page_token = playlist_items_res.get('nextPageToken')
        return videos, channel_name, channel_stats
    except Exception as e:
        st.error(f"Error fetching YouTube channel data: {e}")
        return None, None, None

def fetch_video_details(video_ids, api_key):
    """Fetch details for multiple videos."""
    if not video_ids:
        return {}
    all_details = {}
    video_chunks = [video_ids[i:i+50] for i in range(0, len(video_ids), 50)]
    for chunk in video_chunks:
        video_ids_str = ','.join(chunk)
        details_url = f"https://www.googleapis.com/youtube/v3/videos?part=contentDetails,statistics,snippet&id={video_ids_str}&key={api_key}"
        try:
            details_res = requests.get(details_url).json()
            for item in details_res.get('items', []):
                duration_str = item['contentDetails']['duration']
                duration_seconds = parse_duration(duration_str)
                published_at = item['snippet']['publishedAt']
                all_details[item['id']] = {
                    'duration': duration_seconds,
                    'viewCount': int(item['statistics'].get('viewCount', 0)),
                    'likeCount': int(item['statistics'].get('likeCount', 0)),
                    'commentCount': int(item['statistics'].get('commentCount', 0)),
                    'publishedAt': published_at,
                    'title': item['snippet']['title'],
                    'thumbnailUrl': item['snippet']['thumbnails'].get('medium', {}).get('url', ''),
                    'isShort': duration_seconds <= 60
                }
        except Exception as e:
            st.warning(f"Error fetching details for some videos: {e}")
    return all_details

def parse_duration(duration_str):
    """Convert ISO 8601 duration to seconds."""
    hours = re.search(r'(\d+)H', duration_str)
    minutes = re.search(r'(\d+)M', duration_str)
    seconds = re.search(r'(\d+)S', duration_str)
    total_seconds = 0
    if hours:
        total_seconds += int(hours.group(1)) * 3600
    if minutes:
        total_seconds += int(minutes.group(1)) * 60
    if seconds:
        total_seconds += int(seconds.group(1))
    return total_seconds

# ------------------------------------------------
# Benchmark & Simulation Functions
# ------------------------------------------------
def generate_historical_data(video_details, max_days, is_short=None):
    """Generate historical view data for benchmark videos."""
    today = datetime.datetime.now().date()
    all_video_data = []
    for video_id, details in video_details.items():
        if is_short is not None and details['isShort'] != is_short:
            continue
        try:
            publish_date = datetime.datetime.fromisoformat(details['publishedAt'].replace('Z', '+00:00')).date()
            video_age_days = (today - publish_date).days
        except:
            continue
        if video_age_days < 3:
            continue
        days_to_generate = min(video_age_days, max_days)
        total_views = details['viewCount']
        video_data = generate_view_trajectory(video_id, days_to_generate, total_views, details['isShort'])
        all_video_data.extend(video_data)
    return pd.DataFrame(all_video_data) if all_video_data else pd.DataFrame()

def generate_view_trajectory(video_id, days, total_views, is_short):
    """Simulate a view trajectory over a given number of days."""
    data = []
    if is_short:
        trajectory = [total_views * (1 - np.exp(-5 * ((i+1)/days)**1.5)) for i in range(days)]
    else:
        k = 10
        trajectory = [total_views * (1 / (1 + np.exp(-k * ((i+1)/days - 0.35)))) for i in range(days)]
    scaling_factor = total_views / trajectory[-1] if trajectory[-1] > 0 else 1
    trajectory = [v * scaling_factor for v in trajectory]
    noise_factor = 0.05
    for i in range(days):
        noise = np.random.normal(0, noise_factor * total_views)
        trajectory[i] = trajectory[i] + noise if i == 0 else max(trajectory[i-1] + 10, trajectory[i] + noise)
    daily_views = [trajectory[0]] + [trajectory[i]-trajectory[i-1] for i in range(1, days)]
    for day in range(days):
        data.append({
            'videoId': video_id,
            'day': day,
            'daily_views': int(daily_views[day]),
            'cumulative_views': int(trajectory[day])
        })
    return data

def calculate_benchmark(df, band_percentage):
    """Calculate benchmark statistics (lower band, upper band, median, mean)."""
    lower_q = (100 - band_percentage) / 200
    upper_q = 1 - (100 - band_percentage) / 200
    summary = df.groupby('day')['cumulative_views'].agg([
        ('lower_band', lambda x: x.quantile(lower_q)),
        ('upper_band', lambda x: x.quantile(upper_q)),
        ('median', 'median'),
        ('mean', 'mean'),
        ('count', 'count')
    ]).reset_index()
    summary['channel_average'] = (summary['lower_band'] + summary['upper_band']) / 2
    return summary

def calculate_outlier_score(current_views, channel_average):
    """Calculate an outlier score (ratio of current views to channel average)."""
    return current_views / channel_average if channel_average > 0 else 0

def create_performance_chart(benchmark_data, video_data, video_title):
    """Plot a performance chart comparing actual vs benchmark trajectories."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['lower_band'],
        name='Typical Performance Range',
        fill='tonexty',
        fillcolor='rgba(173,216,230,0.3)',
        line=dict(width=0),
        mode='lines'
    ))
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['channel_average'],
        name='Channel Average',
        line=dict(color='#4285f4', width=2, dash='dash'),
        mode='lines'
    ))
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['median'],
        name='Channel Median',
        line=dict(color='#34a853', width=2, dash='dot'),
        mode='lines'
    ))
    actual_data = video_data[video_data['projected'] == False]
    fig.add_trace(go.Scatter(
        x=actual_data['day'], 
        y=actual_data['cumulative_views'],
        name=f'"{video_title}" (Actual)',
        line=dict(color='#ea4335', width=3),
        mode='lines'
    ))
    fig.update_layout(
        title='Video Performance Comparison',
        xaxis_title='Days Since Upload',
        yaxis_title='Cumulative Views',
        height=500,
        hovermode='x unified',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor='white'
    )
    return fig

def simulate_video_performance(video_data, benchmark_data):
    """Simulate future performance for a video using its current view count."""
    try:
        published_at = datetime.datetime.fromisoformat(video_data['publishedAt'].replace('Z', '+00:00')).date()
        days_since_publish = (datetime.datetime.now().date() - published_at).days
    except:
        days_since_publish = 0
    current_views = video_data['viewCount']
    if days_since_publish < 2:
        days_since_publish = 2
    data = []
    benchmark_day_index = min(days_since_publish, len(benchmark_data) - 1)
    for day in range(days_since_publish + 1):
        if day >= len(benchmark_data):
            break
        if day == days_since_publish:
            cumulative_views = current_views
        else:
            ratio = benchmark_data.loc[day, 'median'] / benchmark_data.loc[benchmark_day_index, 'median'] if benchmark_data.loc[benchmark_day_index, 'median'] > 0 else 0
            cumulative_views = int(current_views * ratio)
        daily_views = cumulative_views if day == 0 else max(0, cumulative_views - data[-1]['cumulative_views'])
        data.append({'day': day, 'daily_views': daily_views, 'cumulative_views': cumulative_views, 'projected': False})
    return pd.DataFrame(data)

# ------------------------------------------------
# SQLite Functions to Remember Settings
# ------------------------------------------------
def init_db():
    conn = sqlite3.connect("youtube_history.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_url TEXT,
            num_videos INTEGER,
            video_type TEXT,
            sort_filter TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    return conn

def save_history(conn, channel_url, num_videos, video_type, sort_filter):
    c = conn.cursor()
    timestamp = datetime.datetime.now().isoformat()
    c.execute("""
        INSERT INTO channel_history (channel_url, num_videos, video_type, sort_filter, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (channel_url, num_videos, video_type, sort_filter, timestamp))
    conn.commit()

# ------------------------------------------------
# Main App Sidebar & Mode Selection
# ------------------------------------------------
with st.sidebar:
    st.header("Settings")
    analysis_mode = st.radio("Select Analysis Mode", options=["Single Video", "Channel Analysis"])
    percentile_range = st.slider("Middle Percentage Range for Band", min_value=10, max_value=100, value=50, step=5,
                                 help="E.g., 50 gives you the 25th to 75th percentile range")
    if analysis_mode == "Channel Analysis":
        channel_url = st.text_input("Channel URL", placeholder="https://www.youtube.com/channel/ChannelID")
        num_videos = st.slider("Number of Videos to Analyze", min_value=5, max_value=150, value=50, step=5)
        video_type_option = st.radio("Video Type", options=["All Videos", "Long-form Only", "Shorts Only"])
        sort_filter = st.selectbox("Sort Results By", options=["Newest", "Oldest", "Popular", "Outlier"])
        # Map option to our internal filter value
        video_type_filter = "all" if video_type_option=="All Videos" else ("long_form" if video_type_option=="Long-form Only" else "shorts")
    else:
        video_url = st.text_input("Video URL", placeholder="https://www.youtube.com/watch?v=VideoID or /shorts/VideoID")

# ------------------------------------------------
# Main App Analysis Logic
# ------------------------------------------------
if st.button("Analyze Video" if analysis_mode=="Single Video" else "Analyze Channel"):
    if analysis_mode == "Single Video":
        if not video_url:
            st.error("Please enter a video URL.")
            st.stop()
        video_id = extract_video_id(video_url)
        if not video_id:
            st.error("Could not extract a valid video ID from the URL.")
            st.stop()
        with st.spinner("Fetching video details..."):
            video_details = fetch_single_video(video_id, yt_api_key)
            if not video_details:
                st.error("Failed to fetch video details.")
                st.stop()
            channel_id = video_details['channelId']
            published_date = datetime.datetime.fromisoformat(video_details['publishedAt'].replace('Z', '+00:00')).date()
            video_age = (datetime.datetime.now().date() - published_date).days
        with st.spinner("Fetching channel videos for benchmark..."):
            channel_videos, channel_name, _ = fetch_channel_videos(channel_id, None, yt_api_key)
            if not channel_videos:
                st.error("Failed to fetch channel videos.")
                st.stop()
        st.subheader("Video Information")
        col1, col2 = st.columns([1, 3])
        with col1:
            if video_details['thumbnailUrl']:
                st.image(video_details['thumbnailUrl'], width=200)
        with col2:
            st.markdown(f"**Title:** {video_details['title']}")
            st.markdown(f"**Channel:** {channel_name}")
            st.markdown(f"**Published:** {published_date} ({video_age} days ago)")
            minutes, seconds = divmod(video_details['duration'], 60)
            hours, minutes = divmod(minutes, 60)
            duration_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
            st.markdown(f"**Duration:** {duration_str} ({'Short' if video_details['isShort'] else 'Long-form'})")
            metric_cols = st.columns(3)
            with metric_cols[0]:
                st.metric("Views", f"{video_details['viewCount']:,}")
            with metric_cols[1]:
                st.metric("Likes", f"{video_details['likeCount']:,}")
            with metric_cols[2]:
                st.metric("Comments", f"{video_details['commentCount']:,}")
        with st.spinner("Calculating benchmark and outlier score..."):
            # For single video analysis, auto-detect video type for benchmark
            is_short_filter = video_details['isShort']
            video_ids = [v['videoId'] for v in channel_videos]
            detailed_videos = fetch_video_details(video_ids, yt_api_key)
            if video_id in detailed_videos:
                del detailed_videos[video_id]
            # If too few similar videos, fallback to all videos
            shorts_count = sum(1 for _, det in detailed_videos.items() if det['isShort'])
            longform_count = len(detailed_videos) - shorts_count
            if (is_short_filter and shorts_count < 5) or (not is_short_filter and longform_count < 5):
                is_short_filter = None
            max_days = video_age
            benchmark_df = generate_historical_data(detailed_videos, max_days, is_short_filter)
            if benchmark_df.empty:
                st.error("Not enough historical data to build a benchmark.")
                st.stop()
            benchmark_stats = calculate_benchmark(benchmark_df, percentile_range)
            video_performance = simulate_video_performance(video_details, benchmark_stats)
            day_index = min(video_age, len(benchmark_stats)-1)
            channel_average = benchmark_stats.loc[day_index, 'channel_average']
            outlier_score = calculate_outlier_score(video_details['viewCount'], channel_average)
            fig = create_performance_chart(benchmark_stats, video_performance, video_details['title'])
            st.plotly_chart(fig, use_container_width=True)
            st.subheader("Outlier Analysis")
            if outlier_score >= 2.0:
                outlier_category, outlier_class = "Significant Positive Outlier", "outlier-high"
            elif outlier_score >= 1.5:
                outlier_category, outlier_class = "Positive Outlier", "outlier-high"
            elif outlier_score >= 1.2:
                outlier_category, outlier_class = "Slight Positive Outlier", "outlier-normal"
            elif outlier_score >= 0.8:
                outlier_category, outlier_class = "Normal Performance", "outlier-normal"
            elif outlier_score >= 0.5:
                outlier_category, outlier_class = "Slight Negative Outlier", "outlier-low"
            else:
                outlier_category, outlier_class = "Significant Negative Outlier", "outlier-low"
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"""<div class='metric-card'><div>Current Views</div><div style='font-size:24px;font-weight:bold;'>{video_details['viewCount']:,}</div></div>""", unsafe_allow_html=True)
            with col2:
                st.markdown(f"""<div class='metric-card'><div>Channel Average</div><div style='font-size:24px;font-weight:bold;'>{int(channel_average):,}</div></div>""", unsafe_allow_html=True)
            with col3:
                st.markdown(f"""<div class='metric-card'><div>Outlier Score</div><div style='font-size:24px;font-weight:bold;' class='{outlier_class}'>{outlier_score:.2f}</div><div>{outlier_category}</div></div>""", unsafe_allow_html=True)
            st.markdown(f"""
            <div class='explanation'>
                <p><strong>Interpretation:</strong> An outlier score of <strong>{outlier_score:.2f}</strong> indicates that this video has {outlier_score:.2f}× the views compared to the channel's average for its age.</p>
                <ul>
                    <li>1.0 = Exactly average performance</li>
                    <li>&gt;1.0 = Outperforming the channel average</li>
                    <li>&lt;1.0 = Underperforming relative to the channel average</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)
    else:
        # CHANNEL ANALYSIS MODE
        if not channel_url:
            st.error("Please enter a channel URL.")
            st.stop()
        channel_id = extract_channel_id(channel_url)
        if not channel_id:
            st.error("Could not extract a valid channel ID from the URL.")
            st.stop()
        with st.spinner("Fetching channel videos..."):
            channel_videos, channel_name, _ = fetch_channel_videos(channel_id, num_videos, yt_api_key)
            if not channel_videos:
                st.error("Failed to fetch channel videos.")
                st.stop()
        # Save the channel analysis settings
        conn = init_db()
        save_history(conn, channel_url, num_videos, video_type_filter, sort_filter)
        st.subheader(f"Channel Analysis for: {channel_name}")
        st.markdown(f"Total videos fetched: **{len(channel_videos)}**")
        video_ids = [v['videoId'] for v in channel_videos]
        detailed_videos = fetch_video_details(video_ids, yt_api_key)
        # Apply video type filter if necessary
        if video_type_filter == "long_form":
            detailed_videos = {vid: det for vid, det in detailed_videos.items() if not det['isShort']}
        elif video_type_filter == "shorts":
            detailed_videos = {vid: det for vid, det in detailed_videos.items() if det['isShort']}
        if not detailed_videos:
            st.error("No videos available after applying the selected video type filter.")
            st.stop()
        # Build benchmark data from videos older than 3 days
        today = datetime.datetime.now().date()
        benchmark_input = {}
        for vid, det in detailed_videos.items():
            try:
                pub_date = datetime.datetime.fromisoformat(det['publishedAt'].replace('Z', '+00:00')).date()
                age = (today - pub_date).days
                det['age'] = age
                if age >= 3:
                    benchmark_input[vid] = det
            except:
                det['age'] = 0
        if not benchmark_input:
            st.error("Not enough historical data (videos older than 3 days) to create a benchmark.")
            st.stop()
        max_age = max(det['age'] for det in benchmark_input.values())
        benchmark_df = generate_historical_data(benchmark_input, max_age, None if video_type_filter=="all" else (video_type_filter=="shorts"))
        if benchmark_df.empty:
            st.error("Not enough data to create a benchmark.")
            st.stop()
        benchmark_stats = calculate_benchmark(benchmark_df, percentile_range)
        # Calculate outlier score for each video and build a results table
        analysis_results = []
        for vid, det in detailed_videos.items():
            age = det.get('age', 0)
            if age < 3 or len(benchmark_stats) == 0:
                outlier_score = None
            else:
                day_index = min(age, len(benchmark_stats) - 1)
                channel_avg = benchmark_stats.loc[day_index, 'channel_average']
                outlier_score = det['viewCount'] / channel_avg if channel_avg > 0 else 0
            analysis_results.append({
                "Video ID": vid,
                "Title": det['title'],
                "Published": det['publishedAt'][:10],
                "Age (days)": age,
                "Views": det['viewCount'],
                "Outlier Score": outlier_score,
                "Type": "Shorts" if det['isShort'] else "Long-form"
            })
        results_df = pd.DataFrame(analysis_results)
        # Sort results based on selected filter
        if sort_filter == "Newest":
            results_df = results_df.sort_values(by="Published", ascending=False)
        elif sort_filter == "Oldest":
            results_df = results_df.sort_values(by="Published", ascending=True)
        elif sort_filter == "Popular":
            results_df = results_df.sort_values(by="Views", ascending=False)
        elif sort_filter == "Outlier":
            results_df = results_df.sort_values(by="Outlier Score", ascending=False, na_position='last')
        st.subheader("Channel Videos Outlier Analysis")
        st.dataframe(results_df.reset_index(drop=True))
