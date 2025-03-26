import streamlit as st
import requests, sqlite3, json, re, datetime, os
import pandas as pd, numpy as np
import plotly.graph_objects as go
from datetime import timedelta

#####################################
# 1. SETUP & CACHING WITH SQLITE
#####################################
DB_FILE = "cache.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS video_cache (
            video_id TEXT PRIMARY KEY,
            data TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_cache (
            channel_id TEXT PRIMARY KEY,
            data TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

conn = init_db()

def get_cached_video(video_id):
    c = conn.cursor()
    c.execute("SELECT data FROM video_cache WHERE video_id=?", (video_id,))
    row = c.fetchone()
    if row:
        return json.loads(row[0])
    return None

def set_cached_video(video_id, data):
    c = conn.cursor()
    c.execute("REPLACE INTO video_cache (video_id, data, last_updated) VALUES (?,?,CURRENT_TIMESTAMP)",
              (video_id, json.dumps(data)))
    conn.commit()

def get_cached_channel(channel_id):
    c = conn.cursor()
    c.execute("SELECT data FROM channel_cache WHERE channel_id=?", (channel_id,))
    row = c.fetchone()
    if row:
        return json.loads(row[0])
    return None

def set_cached_channel(channel_id, data):
    c = conn.cursor()
    c.execute("REPLACE INTO channel_cache (channel_id, data, last_updated) VALUES (?,?,CURRENT_TIMESTAMP)",
              (channel_id, json.dumps(data)))
    conn.commit()

#####################################
# 2. CONFIGURATION & CSS
#####################################
if "YT_API_KEY" in st.secrets:
    yt_api_key = st.secrets["YT_API_KEY"]
else:
    st.error("YouTube API key not found in st.secrets. Please add it to your secrets.")
    st.stop()

st.set_page_config(page_title="YouTube Video Outlier Analysis", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2rem; font-weight: 600; margin-bottom: 1rem; color: #333; }
    .subheader { font-size: 1.5rem; font-weight: 500; margin: 1rem 0; color: #333; }
    .metric-card {
        padding: 1rem; border-radius: 10px; margin-bottom: 1rem; text-align: center;
        background-color: #f0f2f6; color: #333; box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .outlier-high { color: #1e8e3e; font-weight: bold; }
    .outlier-normal { color: #188038; font-weight: normal; }
    .outlier-low { color: #c53929; font-weight: bold; }
    .explanation {
        padding: 1rem; border-left: 4px solid #4285f4; background-color: #f8f9fa; color: #333; margin: 1rem 0;
    }
    /* Hide raw HTML video cards; we now use buttons */
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-header'>YouTube Video Outlier Analysis</div>", unsafe_allow_html=True)
st.markdown("Compare a video’s performance against its channel’s typical performance.")

#####################################
# 3. HELPER FUNCTIONS (URL extraction, duration parsing)
#####################################
def extract_video_id(url):
    """Extract video ID from standard, shortened, embed, or Shorts URLs."""
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

def extract_channel_id(url):
    """Extract channel ID from various YouTube channel URL formats."""
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

def get_channel_id_from_identifier(identifier, pattern_used):
    """Resolve channel ID given a custom URL or handle."""
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
        res = requests.get(search_url).json()
        if 'items' in res and res['items']:
            return res['items'][0]['id']['channelId']
    except Exception as e:
        st.error(f"Error resolving channel identifier: {e}")
    return None

def parse_duration(duration_str):
    """Convert ISO 8601 duration to seconds."""
    hours = re.search(r'(\d+)H', duration_str)
    minutes = re.search(r'(\d+)M', duration_str)
    seconds = re.search(r'(\d+)S', duration_str)
    total = 0
    if hours: total += int(hours.group(1)) * 3600
    if minutes: total += int(minutes.group(1)) * 60
    if seconds: total += int(seconds.group(1))
    return total

#####################################
# 4. YOUTUBE API FUNCTIONS (with caching)
#####################################
def fetch_single_video(video_id):
    cached = get_cached_video(video_id)
    if cached:
        return cached
    url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={yt_api_key}"
    res = requests.get(url).json()
    if 'items' not in res or not res['items']:
        return None
    item = res['items'][0]
    dur = parse_duration(item['contentDetails']['duration'])
    data = {
        'videoId': video_id,
        'title': item['snippet']['title'],
        'channelId': item['snippet']['channelId'],
        'channelTitle': item['snippet']['channelTitle'],
        'publishedAt': item['snippet']['publishedAt'],
        'thumbnailUrl': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
        'viewCount': int(item['statistics'].get('viewCount', 0)),
        'likeCount': int(item['statistics'].get('likeCount', 0)),
        'commentCount': int(item['statistics'].get('commentCount', 0)),
        'duration': dur,
        'isShort': dur <= 60
    }
    set_cached_video(video_id, data)
    return data

def fetch_channel_videos(channel_id, max_videos=None):
    cached = get_cached_channel(channel_id)
    if cached:
        return cached.get("videos"), cached.get("channel_name"), cached.get("channel_stats")
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails,snippet,statistics&id={channel_id}&key={yt_api_key}"
    res = requests.get(url).json()
    if 'items' not in res or not res['items']:
        st.error("Invalid Channel ID or no uploads found.")
        return None, None, None
    channel_info = res['items'][0]
    channel_name = channel_info['snippet']['title']
    channel_stats = channel_info.get('statistics', {})
    uploads_playlist_id = channel_info['contentDetails']['relatedPlaylists']['uploads']
    videos = []
    next_token = ""
    while (max_videos is None or len(videos) < max_videos) and next_token is not None:
        url2 = f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails,snippet&maxResults=50&playlistId={uploads_playlist_id}&key={yt_api_key}"
        if next_token:
            url2 += f"&pageToken={next_token}"
        resp = requests.get(url2).json()
        for item in resp.get('items', []):
            videos.append({
                'videoId': item['contentDetails']['videoId'],
                'title': item['snippet']['title'],
                'publishedAt': item['snippet']['publishedAt']
            })
        next_token = resp.get('nextPageToken')
    cache_data = {"videos": videos, "channel_name": channel_name, "channel_stats": channel_stats}
    set_cached_channel(channel_id, cache_data)
    return videos, channel_name, channel_stats

def fetch_video_details(video_ids):
    details = {}
    to_fetch = []
    for vid in video_ids:
        cached = get_cached_video(vid)
        if cached:
            details[vid] = cached
        else:
            to_fetch.append(vid)
    if to_fetch:
        for i in range(0, len(to_fetch), 50):
            chunk = to_fetch[i:i+50]
            ids_str = ",".join(chunk)
            url = f"https://www.googleapis.com/youtube/v3/videos?part=contentDetails,statistics,snippet&id={ids_str}&key={yt_api_key}"
            res = requests.get(url).json()
            for item in res.get('items', []):
                dur = parse_duration(item['contentDetails']['duration'])
                d = {
                    'videoId': item['id'],
                    'title': item['snippet']['title'],
                    'channelId': item['snippet']['channelId'],
                    'channelTitle': item['snippet']['channelTitle'],
                    'publishedAt': item['snippet']['publishedAt'],
                    'thumbnailUrl': item['snippet'].get('thumbnails', {}).get('medium', {}).get('url', ''),
                    'viewCount': int(item['statistics'].get('viewCount', 0)),
                    'likeCount': int(item['statistics'].get('likeCount', 0)),
                    'commentCount': int(item['statistics'].get('commentCount', 0)),
                    'duration': dur,
                    'isShort': dur <= 60
                }
                details[item['id']] = d
                set_cached_video(item['id'], d)
    return details

#####################################
# 5. BENCHMARK & SIMULATION FUNCTIONS
#####################################
DEFAULT_BAND_PERCENTAGE = 50  # fixed default

def generate_historical_data(video_details, max_days, is_short=None):
    today = datetime.date.today()
    all_data = []
    for vid, det in video_details.items():
        if is_short is not None and det['isShort'] != is_short:
            continue
        try:
            pub_date = datetime.datetime.fromisoformat(det['publishedAt'].replace('Z','+00:00')).date()
            age = (today - pub_date).days
        except:
            continue
        if age < 3:
            continue
        days = min(age, max_days)
        total_views = det['viewCount']
        traj = generate_view_trajectory(vid, days, total_views, det['isShort'])
        all_data.extend(traj)
    if not all_data:
        return pd.DataFrame()
    return pd.DataFrame(all_data)

def generate_view_trajectory(video_id, days, total_views, is_short):
    data = []
    if is_short:
        traj = [total_views * (1 - np.exp(-5*((i+1)/days)**1.5)) for i in range(days)]
    else:
        k = 10
        traj = [total_views * (1/(1+np.exp(-k*((i+1)/days - 0.35)))) for i in range(days)]
    scaling = total_views / (traj[-1] if traj[-1] > 0 else 1)
    traj = [v * scaling for v in traj]
    noise = 0.05
    for i in range(days):
        n = np.random.normal(0, noise * total_views)
        if i == 0:
            noisy = max(100, traj[i] + n)
        else:
            noisy = max(traj[i-1] + 10, traj[i] + n)
        traj[i] = noisy
    daily = [traj[0]] + [traj[i]-traj[i-1] for i in range(1, days)]
    for d in range(days):
        data.append({
            'videoId': video_id,
            'day': d,
            'daily_views': int(daily[d]),
            'cumulative_views': int(traj[d])
        })
    return data

def calculate_benchmark(df):
    lower_q = (100 - DEFAULT_BAND_PERCENTAGE) / 200
    upper_q = 1 - lower_q
    summary = df.groupby('day')['cumulative_views'].agg(
        lower_band=lambda x: x.quantile(lower_q),
        upper_band=lambda x: x.quantile(upper_q),
        median='median',
        mean='mean',
        count='count'
    ).reset_index()
    summary['channel_average'] = (summary['lower_band'] + summary['upper_band']) / 2
    return summary

def calculate_outlier_score(current_views, channel_avg):
    return current_views / channel_avg if channel_avg > 0 else 0

def create_performance_chart(bench, video_perf, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=bench['day'], y=bench['lower_band'],
        name='Typical Performance Range',
        fill='tonexty', fillcolor='rgba(173,216,230,0.3)',
        line=dict(width=0), mode='lines'
    ))
    fig.add_trace(go.Scatter(
        x=bench['day'], y=bench['channel_average'],
        name='Channel Average',
        line=dict(color='#4285f4', width=2, dash='dash'),
        mode='lines'
    ))
    fig.add_trace(go.Scatter(
        x=bench['day'], y=bench['median'],
        name='Channel Median',
        line=dict(color='#34a853', width=2, dash='dot'),
        mode='lines'
    ))
    actual = video_perf[video_perf['projected'] == False]
    fig.add_trace(go.Scatter(
        x=actual['day'], y=actual['cumulative_views'],
        name=f'"{title}" (Actual)',
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

def simulate_video_performance(video, bench):
    try:
        pub_date = datetime.datetime.fromisoformat(video['publishedAt'].replace('Z','+00:00')).date()
        today = datetime.date.today()
        age = (today - pub_date).days
    except:
        age = 0
    if age < 2:
        age = 2
    data = []
    bench_index = min(age, len(bench)-1)
    for day in range(age+1):
        if day >= len(bench):
            break
        if day == age:
            cum = video['viewCount']
        else:
            ratio = bench.loc[day, 'median'] / bench.loc[bench_index, 'median'] if bench.loc[bench_index, 'median'] > 0 else 0
            cum = int(video['viewCount'] * ratio)
        daily = cum if day == 0 else max(0, cum - data[-1]['cumulative_views'])
        data.append({'day': day, 'daily_views': daily, 'cumulative_views': cum, 'projected': False})
    return pd.DataFrame(data)

def compute_video_outlier(video, all_details):
    details_ex = {vid: d for vid, d in all_details.items() if vid != video['videoId']}
    try:
        pub_date = datetime.datetime.fromisoformat(video['publishedAt'].replace('Z','+00:00')).date()
        age = (datetime.date.today() - pub_date).days
    except:
        return None
    if age < 2: age = 2
    bench_df = generate_historical_data(details_ex, max_days=age, is_short=video['isShort'])
    if bench_df.empty:
        return None
    bench_stats = calculate_benchmark(bench_df)
    video_perf = simulate_video_performance(video, bench_stats)
    day_idx = min(age, len(bench_stats)-1)
    channel_avg = bench_stats.loc[day_idx, 'channel_average']
    return calculate_outlier_score(video['viewCount'], channel_avg)

#####################################
# 6. MAIN UI LOGIC
#####################################
mode = st.sidebar.radio("Select Mode", options=["Video Analysis", "Channel Analysis"])

if mode == "Video Analysis":
    st.subheader("Enter YouTube Video/Shorts URL")
    video_url = st.text_input("Video URL", placeholder="https://www.youtube.com/watch?v=VideoID or /shorts/VideoID")
    if st.button("Analyze Video") and video_url:
        vid = extract_video_id(video_url)
        if not vid:
            st.error("Invalid video URL format.")
            st.stop()
        video_details = fetch_single_video(vid)
        if not video_details:
            st.error("Failed to fetch video details.")
            st.stop()
        pub_date = datetime.datetime.fromisoformat(video_details['publishedAt'].replace('Z','+00:00')).date()
        age = (datetime.date.today() - pub_date).days
        channel_videos, channel_name, _ = fetch_channel_videos(video_details['channelId'])
        vid_ids = [v['videoId'] for v in channel_videos if v['videoId'] != vid]
        details = fetch_video_details(vid_ids)
        bench_df = generate_historical_data(details, max_days=age, is_short=video_details['isShort'])
        if bench_df.empty:
            st.error("Not enough benchmark data.")
            st.stop()
        bench_stats = calculate_benchmark(bench_df)
        video_perf = simulate_video_performance(video_details, bench_stats)
        day_idx = min(age, len(bench_stats)-1)
        channel_avg = bench_stats.loc[day_idx, 'channel_average']
        outlier = calculate_outlier_score(video_details['viewCount'], channel_avg)
        fig = create_performance_chart(bench_stats, video_perf, video_details['title'])
        st.plotly_chart(fig, use_container_width=True)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"<div class='metric-card'><div>Current Views</div><div style='font-size:24px; font-weight:bold;'>{video_details['viewCount']:,}</div></div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div class='metric-card'><div>Channel Average</div><div style='font-size:24px; font-weight:bold;'>{int(channel_avg):,}</div></div>", unsafe_allow_html=True)
        with col3:
            st.markdown(f"<div class='metric-card'><div>Outlier Score</div><div style='font-size:24px; font-weight:bold;'>{outlier:.2f}</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='explanation'><p><strong>What this means:</strong></p><p>An outlier score of <strong>{outlier:.2f}</strong> indicates that the video has {outlier:.2f}x the views compared to the channel average at its current age.</p></div>", unsafe_allow_html=True)

else:  # Channel Analysis mode
    st.subheader("Enter YouTube Channel URL")
    channel_url = st.text_input("Channel URL", placeholder="https://www.youtube.com/channel/ChannelID or custom URL")
    sort_opt = st.selectbox("Sort Videos By", options=["Latest", "Popular", "Oldest"])
    type_opt = st.radio("Type", options=["Videos", "Shorts"], index=0)
    output_count = st.number_input("Number of videos to display", min_value=1, max_value=50, value=5, step=1)
    
    # If a video is selected via query parameters, show its analysis
    params = st.experimental_get_query_params()
    if "video" in params:
        selected_id = params["video"][0]
        selected = fetch_single_video(selected_id)
        if not selected:
            st.error("Failed to load selected video.")
        else:
            st.markdown(f"## Analysis for: {selected['title']}")
            channel_vids, _, _ = fetch_channel_videos(selected['channelId'])
            vid_ids = [v['videoId'] for v in channel_vids if v['videoId'] != selected['videoId']]
            details_bench = fetch_video_details(vid_ids)
            pub_date = datetime.datetime.fromisoformat(selected['publishedAt'].replace('Z','+00:00')).date()
            age = (datetime.date.today() - pub_date).days
            bench_df = generate_historical_data(details_bench, max_days=age, is_short=selected['isShort'])
            if bench_df.empty:
                st.error("Not enough benchmark data for this video.")
            else:
                bench_stats = calculate_benchmark(bench_df)
                video_perf = simulate_video_performance(selected, bench_stats)
                day_idx = min(age, len(bench_stats)-1)
                channel_avg = bench_stats.loc[day_idx, 'channel_average']
                outlier = calculate_outlier_score(selected['viewCount'], channel_avg)
                fig = create_performance_chart(bench_stats, video_perf, selected['title'])
                st.plotly_chart(fig, use_container_width=True)
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"<div class='metric-card'><div>Current Views</div><div style='font-size:24px; font-weight:bold;'>{selected['viewCount']:,}</div></div>", unsafe_allow_html=True)
                with col2:
                    st.markdown(f"<div class='metric-card'><div>Channel Average</div><div style='font-size:24px; font-weight:bold;'>{int(channel_avg):,}</div></div>", unsafe_allow_html=True)
                with col3:
                    st.markdown(f"<div class='metric-card'><div>Outlier Score</div><div style='font-size:24px; font-weight:bold;'>{outlier:.2f}</div></div>", unsafe_allow_html=True)
                st.markdown(f"<div class='explanation'><p><strong>What this means:</strong></p><p>An outlier score of <strong>{outlier:.2f}</strong> indicates that the video has {outlier:.2f}x the views compared to the channel average at its current age.</p></div>", unsafe_allow_html=True)
    else:
        if st.button("Load Channel Videos") and channel_url:
            ch_id = extract_channel_id(channel_url)
            if not ch_id:
                st.error("Could not extract a valid channel ID. Please check the URL format.")
                st.stop()
            vids, channel_name, _ = fetch_channel_videos(ch_id)
            if not vids:
                st.error("No videos found for this channel.")
                st.stop()
            vid_ids = [v['videoId'] for v in vids]
            all_details = fetch_video_details(vid_ids)
            # Filter by type based on selection
            if type_opt == "Videos":
                filtered = [d for d in all_details.values() if not d['isShort']]
            else:
                filtered = [d for d in all_details.values() if d['isShort']]
            # Sort based on selection
            if sort_opt == "Latest":
                sorted_vids = sorted(filtered, key=lambda x: datetime.datetime.fromisoformat(x['publishedAt'].replace('Z','+00:00')), reverse=True)
            elif sort_opt == "Oldest":
                sorted_vids = sorted(filtered, key=lambda x: datetime.datetime.fromisoformat(x['publishedAt'].replace('Z','+00:00')))
            else:  # Popular
                sorted_vids = sorted(filtered, key=lambda x: x['viewCount'], reverse=True)
            sorted_vids = sorted_vids[:output_count]
            st.markdown(f"### {channel_name} – {type_opt} ({sort_opt})")
            # Instead of raw HTML cards, list each video as a clickable button
            for vid in sorted_vids:
                outlier_val = compute_video_outlier(vid, all_details)
                outlier_disp = f"{outlier_val:.2f}x" if outlier_val is not None else "N/A"
                # Create a button for each video. When clicked, update query parameters to load its analysis.
                if st.button(f"{vid['title']} (Outlier: {outlier_disp})", key=vid['videoId']):
                    st.experimental_set_query_params(video=vid['videoId'], tab="videos")
