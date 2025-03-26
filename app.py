import streamlit as st
import requests
import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go
import re
from datetime import timedelta

# Page configuration
st.set_page_config(
    page_title="YouTube Video Outlier Analysis",
    page_icon="📊",
    layout="wide"
)

# Apply custom styling
st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 600; margin-bottom: 1rem;}
    .subheader {font-size: 1.5rem; font-weight: 500; margin: 1rem 0;}
    .metric-card {
        padding: 1rem;
        border-radius: 10px;
        margin-bottom: 1rem;
        text-align: center;
        background-color: #f0f2f6;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .outlier-high {color: #1e8e3e; font-weight: bold;}
    .outlier-normal {color: #188038; font-weight: normal;}
    .outlier-low {color: #c53929; font-weight: bold;}
    .explanation {
        padding: 1rem;
        border-left: 4px solid #4285f4;
        background-color: #f8f9fa;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

# Main title
st.markdown("<div class='main-header'>YouTube Video Outlier Analysis</div>", unsafe_allow_html=True)
st.markdown("Find out if your video is an outlier compared to the channel's average performance")

# ------------------------
# URL Parsing Functions
# ------------------------
def extract_channel_id(url):
    """Extract channel ID from various YouTube URL formats"""
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
    """Extract video ID from various YouTube URL formats"""
    patterns = [
        r'youtube\.com/watch\?v=([^&\s]+)',
        r'youtu\.be/([^?\s]+)',
        r'youtube\.com/embed/([^?\s]+)',
        r'youtube\.com/v/([^?\s]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r'^[A-Za-z0-9_-]{11}$', url.strip()):
        return url.strip()
    return None

def get_channel_id_from_identifier(identifier, pattern_used):
    """Get channel ID from channel name, username, or handle"""
    try:
        if pattern_used == r'youtube\.com/channel/([^/\s?]+)':
            return identifier
        elif pattern_used == r'youtube\.com/c/([^/\s?]+)':
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        elif pattern_used == r'youtube\.com/user/([^/\s?]+)':
            username_url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forUsername={identifier}&key={yt_api_key}"
            username_res = requests.get(username_url).json()
            if 'items' in username_res and username_res['items']:
                return username_res['items'][0]['id']
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        elif pattern_used == r'youtube\.com/@([^/\s?]+)':
            if identifier.startswith('@'):
                identifier = identifier[1:]
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        else:
            search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={yt_api_key}"
        if 'search_url' in locals():
            search_res = requests.get(search_url).json()
            if 'items' in search_res and search_res['items']:
                return search_res['items'][0]['id']['channelId']
    except Exception as e:
        st.error(f"Error resolving channel identifier: {e}")
    return None

# ------------------------
# Data Fetching Functions
# ------------------------
def fetch_single_video(video_id, api_key):
    """Fetch details for a single video"""
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
    """Fetch videos from a channel"""
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
        st.error(f"Error fetching YouTube data: {e}")
        return None, None, None

def fetch_video_details(video_ids, api_key):
    """Fetch details for multiple videos"""
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
                    'thumbnailUrl': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
                    'isShort': duration_seconds <= 60
                }
        except Exception as e:
            st.warning(f"Error fetching details for some videos: {e}")
    return all_details

def parse_duration(duration_str):
    """Parse ISO 8601 duration format to seconds"""
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

# ------------------------
# Benchmark & Simulation Functions
# ------------------------
def generate_historical_data(video_details, max_days, is_short=None):
    """Generate historical view data for benchmark videos"""
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
        days_to_generate = video_age_days if max_days > video_age_days else max_days
        total_views = details['viewCount']
        video_data = generate_view_trajectory(video_id, days_to_generate, total_views, details['isShort'])
        all_video_data.extend(video_data)
    if not all_video_data:
        return pd.DataFrame()
    return pd.DataFrame(all_video_data)

def generate_view_trajectory(video_id, days, total_views, is_short):
    """Generate view trajectory based on video type"""
    data = []
    # Different view patterns for shorts vs regular videos
    if is_short:
        trajectory = [total_views * (1 - np.exp(-5 * ((i+1)/days)**1.5)) for i in range(days)]
    else:
        k = 10
        trajectory = [total_views * (1 / (1 + np.exp(-k * ((i+1)/days - 0.35)))) for i in range(days)]
    
    scaling_factor = total_views / trajectory[-1] if trajectory[-1] > 0 else 1
    trajectory = [v * scaling_factor for v in trajectory]
    
    # Add some noise for realism
    noise_factor = 0.05
    for i in range(days):
        noise = np.random.normal(0, noise_factor * total_views)
        if i == 0:
            noisy_value = max(100, trajectory[i] + noise)
        else:
            noisy_value = max(trajectory[i-1] + 10, trajectory[i] + noise)
        trajectory[i] = noisy_value
    
    # Calculate daily views
    daily_views = [trajectory[0]]
    for i in range(1, days):
        daily_views.append(trajectory[i] - trajectory[i-1])
    
    for day in range(days):
        data.append({
            'videoId': video_id,
            'day': day,
            'daily_views': int(daily_views[day]),
            'cumulative_views': int(trajectory[day])
        })
    return data

def calculate_benchmark(df, band_percentage):
    """Calculate benchmark statistics based on historical data"""
    lower_q = (100 - band_percentage) / 200
    upper_q = 1 - (100 - band_percentage) / 200
    summary = df.groupby('day')['cumulative_views'].agg([
        ('lower_band', lambda x: x.quantile(lower_q)),
        ('upper_band', lambda x: x.quantile(upper_q)),
        ('median', 'median'),
        ('mean', 'mean'),
        ('count', 'count')
    ]).reset_index()
    
    # Add average of bands (used in Approach 3)
    summary['channel_average'] = (summary['lower_band'] + summary['upper_band']) / 2
    
    return summary

def calculate_outlier_score(current_views, channel_average):
    """Calculate outlier score as the ratio of current views to channel average"""
    if channel_average <= 0:
        return 0
    return current_views / channel_average

def create_performance_chart(benchmark_data, video_data, video_title):
    """Create a performance comparison chart"""
    fig = go.Figure()
    
    # Add performance band (lower bound)
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['lower_band'],
        name='Typical Performance Range',
        fill='tonexty',
        fillcolor='rgba(173, 216, 230, 0.3)',
        line=dict(width=0),
        mode='lines'
    ))
    
    # Add channel average line
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['channel_average'],
        name='Channel Average',
        line=dict(color='#4285f4', width=2, dash='dash'),
        mode='lines'
    ))
    
    # Add median line
    fig.add_trace(go.Scatter(
        x=benchmark_data['day'], 
        y=benchmark_data['median'],
        name='Channel Median',
        line=dict(color='#34a853', width=2, dash='dot'),
        mode='lines'
    ))
    
    # Add video data line
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
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        plot_bgcolor='white'
    )
    return fig

def simulate_video_performance(video_data, benchmark_data):
    """Simulate video performance based on its actual views"""
    try:
        published_at = datetime.datetime.fromisoformat(video_data['publishedAt'].replace('Z', '+00:00')).date()
        current_date = datetime.datetime.now().date()
        days_since_publish = (current_date - published_at).days
    except:
        days_since_publish = 0
    
    current_views = video_data['viewCount']
    is_short = video_data['isShort']
    
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
            # Backfill based on typical video trajectory
            ratio = benchmark_data.loc[day, 'median'] / benchmark_data.loc[benchmark_day_index, 'median'] if benchmark_data.loc[benchmark_day_index, 'median'] > 0 else 0
            cumulative_views = int(current_views * ratio)
        
        if day == 0:
            daily_views = cumulative_views
        else:
            prev_cumulative = data[-1]['cumulative_views']
            daily_views = max(0, cumulative_views - prev_cumulative)
        
        data.append({
            'day': day,
            'daily_views': daily_views,
            'cumulative_views': cumulative_views,
            'projected': False
        })
    
    return pd.DataFrame(data)

# ------------------------
# Main App Logic
# ------------------------

# Sidebar for settings
with st.sidebar:
    st.header("Settings")
    
    # Expand this section for API key input
    with st.expander("YouTube API Configuration", expanded=True):
        yt_api_key = st.text_input("YouTube API Key", type="password")
        st.info("You need a YouTube Data API key to use this app. Get one from the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).")
    
    # Analysis settings
    num_videos = st.slider(
        "Number of videos to include in analysis",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        help="More videos creates a more accurate benchmark"
    )
    
    video_type = st.radio(
        "Video Type to Compare Against",
        options=["all", "long_form", "shorts", "auto"],
        format_func=lambda x: "All Videos" if x == "all" else (
            "Shorts Only" if x == "shorts" else (
                "Long-form Only" if x == "long_form" else "Auto-detect (match video type)"
            )
        ),
        index=3
    )
    
    percentile_range = st.slider(
        "Middle Percentage Range for Band",
        min_value=10,
        max_value=100,
        value=50,
        step=5,
        help="Middle percentage range for typical performance (e.g., 50 = 25th to 75th percentile)"
    )

# Main input form
st.subheader("Enter YouTube Video URL")
video_url = st.text_input("Video URL:", placeholder="https://www.youtube.com/watch?v=VideoID or https://youtu.be/VideoID")

# Process button
if st.button("Analyze Video", type="primary") and video_url:
    
    if not yt_api_key:
        st.error("Please enter your YouTube API key in the sidebar first.")
        st.stop()
    
    # Extract video ID from URL
    video_id = extract_video_id(video_url)
    if not video_id:
        st.error("Could not extract a valid video ID from the provided URL. Please check the URL format.")
        st.stop()
    
    # Fetch video details
    with st.spinner("Fetching video details..."):
        video_details = fetch_single_video(video_id, yt_api_key)
        if not video_details:
            st.error("Failed to fetch video details. Please check the video URL and your API key.")
            st.stop()
        
        # Extract channel ID from video
        channel_id = video_details['channelId']
        published_date = datetime.datetime.fromisoformat(video_details['publishedAt'].replace('Z', '+00:00')).date()
        video_age = (datetime.datetime.now().date() - published_date).days
    
    # Fetch channel videos for benchmark
    with st.spinner("Fetching channel videos for benchmark..."):
        channel_videos, channel_name, channel_stats = fetch_channel_videos(channel_id, num_videos, yt_api_key)
        if not channel_videos:
            st.error("Failed to fetch channel videos. Please check your API key.")
            st.stop()
    
    # Display video info
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
    
    # Process benchmark data
    with st.spinner("Calculating benchmark and outlier score..."):
        # Determine video type for comparison
        if video_type == "auto":
            is_short_filter = video_details['isShort']
            video_type_str = "Shorts" if is_short_filter else "Long-form Videos"
        elif video_type == "shorts":
            is_short_filter = True
            video_type_str = "Shorts"
        elif video_type == "long_form":
            is_short_filter = False
            video_type_str = "Long-form Videos"
        else:
            is_short_filter = None
            video_type_str = "All Videos"
        
        # Get video details for all channel videos
        video_ids = [v['videoId'] for v in channel_videos]
        detailed_videos = fetch_video_details(video_ids, yt_api_key)
        
        # Remove the target video from the benchmark calculations
        if video_id in detailed_videos:
            del detailed_videos[video_id]
        
        # Count shorts vs. long-form videos
        shorts_count = sum(1 for _, details in detailed_videos.items() if details['isShort'])
        longform_count = len(detailed_videos) - shorts_count
        
        # Fallback to all videos if not enough of the specific type
        if is_short_filter is True and shorts_count < 5:
            st.warning(f"Not enough Shorts in this channel (found {shorts_count}). Using all videos instead.")
            is_short_filter = None
            video_type_str = "All Videos"
        elif is_short_filter is False and longform_count < 5:
            st.warning(f"Not enough Long-form videos in this channel (found {longform_count}). Using all videos instead.")
            is_short_filter = None
            video_type_str = "All Videos"
        
        st.info(f"Building benchmark from {len(detailed_videos)} videos: {shorts_count} shorts and {longform_count} long-form videos")
        
        # Generate historical data for benchmark
        # Change: Set max_days to video_age only, so simulation calculates views only till the current age of the video
        max_days = video_age
        benchmark_df = generate_historical_data(detailed_videos, max_days, is_short_filter)
        
        if benchmark_df.empty:
            st.error("Not enough data to create a benchmark. Try including more videos or changing the video type filter.")
            st.stop()
        
        # Calculate benchmark and outlier score
        benchmark_stats = calculate_benchmark(benchmark_df, percentile_range)
        video_performance = simulate_video_performance(video_details, benchmark_stats)
        
        # Find the day index for current video age
        day_index = min(video_age, len(benchmark_stats) - 1)
        if day_index < 0:
            day_index = 0
        
        # Get benchmark values at current video age
        benchmark_median = benchmark_stats.loc[day_index, 'median']
        benchmark_lower = benchmark_stats.loc[day_index, 'lower_band']
        benchmark_upper = benchmark_stats.loc[day_index, 'upper_band']
        channel_average = benchmark_stats.loc[day_index, 'channel_average']
        
        # Calculate outlier score
        outlier_score = calculate_outlier_score(video_details['viewCount'], channel_average)
        
        # Create performance chart
        fig = create_performance_chart(benchmark_stats, video_performance, 
                                      video_details['title'][:40] + "..." if len(video_details['title']) > 40 else video_details['title'])
        st.plotly_chart(fig, use_container_width=True)
        
        # Display outlier analysis
        st.subheader("Outlier Analysis")
        
        # Define outlier categories
        if outlier_score >= 2.0:
            outlier_category = "Significant Positive Outlier"
            outlier_class = "outlier-high"
        elif outlier_score >= 1.5:
            outlier_category = "Positive Outlier"
            outlier_class = "outlier-high"
        elif outlier_score >= 1.2:
            outlier_category = "Slight Positive Outlier"
            outlier_class = "outlier-normal"
        elif outlier_score >= 0.8:
            outlier_category = "Normal Performance"
            outlier_class = "outlier-normal"
        elif outlier_score >= 0.5:
            outlier_category = "Slight Negative Outlier"
            outlier_class = "outlier-low"
        else:
            outlier_category = "Significant Negative Outlier"
            outlier_class = "outlier-low"
        
        # Display primary metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"""
            <div class='metric-card'>
                <div>Current Views</div>
                <div style='font-size: 24px; font-weight: bold;'>{video_details['viewCount']:,}</div>
            </div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
            <div class='metric-card'>
                <div>Channel Average</div>
                <div style='font-size: 24px; font-weight: bold;'>{int(channel_average):,}</div>
            </div>
            """, unsafe_allow_html=True)
        with col3:
            st.markdown(f"""
            <div class='metric-card'>
                <div>Outlier Score</div>
                <div style='font-size: 24px; font-weight: bold;' class='{outlier_class}'>{outlier_score:.2f}</div>
                <div>{outlier_category}</div>
            </div>
            """, unsafe_allow_html=True)
        
        # Explanation
        st.markdown(f"""
        <div class='explanation'>
            <p><strong>What this means:</strong></p>
            <p>An outlier score of <strong>{outlier_score:.2f}</strong> means this video has <strong>{outlier_score:.2f}x</strong> the views compared to the channel's average at the same age.</p>
            <ul>
                <li>1.0 = Exactly average performance</li>
                <li>&gt;1.0 = Outperforming channel average</li>
                <li>&lt;1.0 = Underperforming channel average</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        
        # More detailed stats
        st.subheader("Detailed Performance Metrics")
        col1, col2 = st.columns(2)
        with col1:
            if benchmark_median > 0:
                vs_median_pct = ((video_details['viewCount'] / benchmark_median) - 1) * 100
                st.metric("Compared to Median", f"{vs_median_pct:+.1f}%")
            if channel_average > 0:
                vs_avg_pct = ((video_details['viewCount'] / channel_average) - 1) * 100
                st.metric("Compared to Channel Average", f"{vs_avg_pct:+.1f}%")
        with col2:
            if benchmark_upper > 0:
                vs_upper_pct = ((video_details['viewCount'] / benchmark_upper) - 1) * 100
                st.metric("Compared to Upper Band", f"{vs_upper_pct:+.1f}%")
            if benchmark_lower > 0:
                vs_lower_pct = ((video_details['viewCount'] / benchmark_lower) - 1) * 100
                st.metric("Compared to Lower Band", f"{vs_lower_pct:+.1f}%")
