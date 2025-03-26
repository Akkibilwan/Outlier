import streamlit as st
import requests
import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go
import re
import sqlite3
from datetime import timedelta

############################################
# 1. INITIAL SETUP & DB FUNCTIONS
############################################

# Attempt to load YouTube API Key from secrets
if "YT_API_KEY" in st.secrets:
    YT_API_KEY = st.secrets["YT_API_KEY"]
else:
    st.error("YouTube API key not found in st.secrets. Please add it to your secrets.")
    st.stop()

# Page configuration
st.set_page_config(page_title="YouTube Video Outlier Analysis",
                   page_icon="📊",
                   layout="wide")

# Initialize SQLite DB
def init_db():
    """Initialize SQLite database and create tables if they don't exist."""
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        # Table for channel videos (basic info)
        c.execute("""
            CREATE TABLE IF NOT EXISTS channel_videos (
                channel_id TEXT,
                video_id TEXT PRIMARY KEY,
                title TEXT,
                published_at TEXT,
                view_count INTEGER,
                like_count INTEGER,
                comment_count INTEGER,
                duration INTEGER,
                is_short BOOLEAN,
                thumbnail_url TEXT,
                fetched_at TIMESTAMP
            )
        """)
        # Table for storing outlier calculations or extended data if needed
        c.execute("""
            CREATE TABLE IF NOT EXISTS video_analysis (
                video_id TEXT PRIMARY KEY,
                outlier_score REAL,
                outlier_category TEXT,
                channel_average INTEGER,
                fetched_at TIMESTAMP
            )
        """)
        conn.commit()

init_db()  # Ensure DB is set up

############################################
# 2. CUSTOM STYLING (CSS)
############################################

st.markdown("""
<style>
    /* Page Background */
    body {
        background-color: #fff;
        color: #333;
    }
    /* Main Headers */
    .main-header {
        font-size: 2rem;
        font-weight: 600;
        margin-bottom: 1rem;
        color: #333;
    }
    .subheader {
        font-size: 1.5rem;
        font-weight: 500;
        margin: 1rem 0;
        color: #333;
    }
    /* Card Container: simulates a YouTube-like grid */
    .video-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
    }
    /* Single video card */
    .video-card {
        width: 304.9px; /* as requested */
        background-color: #f9f9f9;
        border-radius: 8px;
        overflow: hidden;
        cursor: pointer;
        text-decoration: none;
        color: inherit;
    }
    /* Thumbnail area: 304.9 x 171.55 for aspect ratio */
    .thumbnail-container {
        width: 304.9px;
        height: 171.55px;
        background-color: #ddd;
        overflow: hidden;
    }
    .thumbnail-container img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    /* Video info below thumbnail */
    .video-info {
        padding: 8px;
    }
    .video-title {
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 4px;
        display: -webkit-box;
        -webkit-line-clamp: 2; /* limit to 2 lines */
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .video-stats {
        font-size: 12px;
        color: #666;
    }
    /* For the detail view: metric cards, chart, etc. */
    .metric-card {
        padding: 1rem;
        border-radius: 10px;
        margin-bottom: 1rem;
        text-align: center;
        background-color: #f0f2f6;
        color: #333;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .outlier-high {color: #1e8e3e; font-weight: bold;}
    .outlier-normal {color: #188038; font-weight: normal;}
    .outlier-low {color: #c53929; font-weight: bold;}
    .explanation {
        padding: 1rem;
        border-left: 4px solid #4285f4;
        background-color: #f8f9fa;
        color: #333;
        margin: 1rem 0;
    }
    .hidden {
        display: none;
    }
</style>
""", unsafe_allow_html=True)

############################################
# 3. YOUTUBE API FUNCTIONS & LOGIC
############################################

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
            # If direct channel ID (starts with UC), return it
            if pattern == patterns[0] and identifier.startswith('UC'):
                return identifier
            # Otherwise, attempt to resolve
            return get_channel_id_from_identifier(identifier)
    # If the entire URL itself starts with UC
    if url.strip().startswith('UC'):
        return url.strip()
    return None

def get_channel_id_from_identifier(identifier):
    """Resolve channel ID by searching the identifier via YouTube Data API"""
    try:
        # Attempt direct search
        search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={YT_API_KEY}"
        res = requests.get(search_url).json()
        items = res.get('items', [])
        if items:
            return items[0]['id']['channelId']
    except:
        pass
    return None

def fetch_channel_videos(channel_id, api_key):
    """
    Fetch all videos from a channel's uploads playlist 
    (We won't limit them; we'll store them in DB).
    """
    # First get uploads playlist ID
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    res = requests.get(url).json()
    items = res.get('items', [])
    if not items:
        return []
    
    uploads_playlist_id = items[0]['contentDetails']['relatedPlaylists']['uploads']
    
    videos = []
    next_page_token = ""
    while True:
        playlist_items_url = (
            f"https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=contentDetails,snippet&maxResults=50&playlistId={uploads_playlist_id}&key={api_key}"
        )
        if next_page_token:
            playlist_items_url += f"&pageToken={next_page_token}"
        playlist_res = requests.get(playlist_items_url).json()
        for item in playlist_res.get('items', []):
            snippet = item['snippet']
            video_id = snippet['resourceId']['videoId']
            title = snippet['title']
            published_at = snippet['publishedAt']
            videos.append({
                "videoId": video_id,
                "title": title,
                "publishedAt": published_at
            })
        next_page_token = playlist_res.get('nextPageToken')
        if not next_page_token:
            break
    
    return videos

def fetch_video_details(video_ids, api_key):
    """Fetch snippet/stats for a list of video_ids. Returns dict keyed by video_id."""
    details = {}
    # chunk by 50
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        chunk_str = ",".join(chunk)
        url = (f"https://www.googleapis.com/youtube/v3/videos"
               f"?part=snippet,statistics,contentDetails&id={chunk_str}&key={api_key}")
        res = requests.get(url).json()
        for item in res.get('items', []):
            vid_id = item['id']
            snippet = item['snippet']
            stats = item.get('statistics', {})
            content_details = item['contentDetails']
            duration_seconds = parse_duration(content_details['duration'])
            details[vid_id] = {
                "videoId": vid_id,
                "title": snippet['title'],
                "publishedAt": snippet['publishedAt'],
                "viewCount": int(stats.get('viewCount', 0)),
                "likeCount": int(stats.get('likeCount', 0)),
                "commentCount": int(stats.get('commentCount', 0)),
                "duration": duration_seconds,
                "isShort": (duration_seconds <= 60),
                "thumbnailUrl": snippet.get('thumbnails', {}).get('medium', {}).get('url', '')
            }
    return details

############################################
# 4. DATABASE CACHING LAYER
############################################

def load_channel_videos_from_db(channel_id):
    """Load all videos for a channel from DB, return as list of dict."""
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        c.execute("""
            SELECT video_id, title, published_at, view_count, like_count, comment_count,
                   duration, is_short, thumbnail_url
            FROM channel_videos
            WHERE channel_id = ?
            ORDER BY published_at DESC
        """, (channel_id,))
        rows = c.fetchall()
        videos = []
        for r in rows:
            videos.append({
                "videoId": r[0],
                "title": r[1],
                "publishedAt": r[2],
                "viewCount": r[3],
                "likeCount": r[4],
                "commentCount": r[5],
                "duration": r[6],
                "isShort": bool(r[7]),
                "thumbnailUrl": r[8]
            })
        return videos

def save_channel_videos_to_db(channel_id, video_dicts):
    """Insert/replace multiple videos into channel_videos table."""
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        for vid in video_dicts:
            c.execute("""
                INSERT OR REPLACE INTO channel_videos (
                    channel_id, video_id, title, published_at, view_count,
                    like_count, comment_count, duration, is_short, thumbnail_url,
                    fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """, (
                channel_id,
                vid["videoId"],
                vid["title"],
                vid["publishedAt"],
                vid["viewCount"],
                vid["likeCount"],
                vid["commentCount"],
                vid["duration"],
                1 if vid["isShort"] else 0,
                vid["thumbnailUrl"]
            ))
        conn.commit()

############################################
# 5. OUTLIER ANALYSIS (SIMPLIFIED)
############################################

def generate_historical_data(video_details, max_days, is_short=None):
    """
    Build simplified historical data for outlier analysis.
    We'll keep the default percentile at 50 internally.
    """
    # (Same logic from previous versions; truncated for brevity)
    # We'll only generate up to "max_days" for each video, ignoring advanced metrics
    today = datetime.datetime.now().date()
    all_video_data = []
    for vid_id, vd in video_details.items():
        if is_short is not None and vd["isShort"] != is_short:
            continue
        try:
            publish_date = datetime.datetime.fromisoformat(vd["publishedAt"].replace('Z', '+00:00')).date()
            video_age_days = (today - publish_date).days
        except:
            continue
        if video_age_days < 3:
            continue
        days_to_generate = min(video_age_days, max_days)
        total_views = vd["viewCount"]
        # We do a simple linear approach for demonstration
        # (You can keep your logistic or exponential approach if desired)
        for d in range(days_to_generate):
            daily = (total_views / days_to_generate)  # naive
            all_video_data.append({
                "videoId": vid_id,
                "day": d,
                "cumulative_views": int((d+1) * daily)
            })
    return pd.DataFrame(all_video_data)

def calculate_benchmark(df):
    """
    With default middle range=50 => 25th to 75th percentile, 
    we calculate a single 'channel_average' = (25th+75th)/2.
    """
    if df.empty:
        return pd.DataFrame()
    grouped = df.groupby("day")["cumulative_views"]
    summary = grouped.agg(["median", "count"])
    summary["lower_band"] = grouped.quantile(0.25)
    summary["upper_band"] = grouped.quantile(0.75)
    summary["channel_average"] = (summary["lower_band"] + summary["upper_band"]) / 2
    summary = summary.reset_index()
    return summary

def simulate_video_performance(video_info, benchmark_data):
    """
    Generate a naive performance DF for the chosen video, 
    only up to its actual age. We'll just do a single data point 
    at the final day that matches the real viewCount.
    """
    try:
        published_date = datetime.datetime.fromisoformat(video_info["publishedAt"].replace('Z', '+00:00')).date()
        today = datetime.datetime.now().date()
        days_since_publish = (today - published_date).days
    except:
        days_since_publish = 0
    if days_since_publish < 1:
        days_since_publish = 1
    data = []
    for d in range(days_since_publish):
        if d < days_since_publish - 1:
            # Fill with some naive fraction
            fraction = (d+1)/days_since_publish
            data.append({
                "day": d,
                "cumulative_views": int(video_info["viewCount"] * fraction),
                "projected": False
            })
        else:
            # The last day is the real count
            data.append({
                "day": d,
                "cumulative_views": video_info["viewCount"],
                "projected": False
            })
    return pd.DataFrame(data)

def outlier_analysis(video_info, channel_avg):
    """Calculate outlier ratio. Return category & ratio."""
    if channel_avg <= 0:
        return ("Significant Positive Outlier", 999.9)  # if no average
    ratio = video_info["viewCount"] / channel_avg
    if ratio >= 2.0:
        return ("Significant Positive Outlier", ratio)
    elif ratio >= 1.5:
        return ("Positive Outlier", ratio)
    elif ratio >= 1.2:
        return ("Slight Positive Outlier", ratio)
    elif ratio >= 0.8:
        return ("Normal Performance", ratio)
    elif ratio >= 0.5:
        return ("Slight Negative Outlier", ratio)
    else:
        return ("Significant Negative Outlier", ratio)

############################################
# 6. APP UI & LOGIC
############################################

st.markdown("<div class='main-header'>YouTube Video Outlier Analysis</div>", unsafe_allow_html=True)

# State to hold selected channel ID & selected video ID
if "selected_channel_id" not in st.session_state:
    st.session_state["selected_channel_id"] = None
if "selected_video_id" not in st.session_state:
    st.session_state["selected_video_id"] = None

# For toggling between "Videos" & "Shorts"
if "selected_tab" not in st.session_state:
    st.session_state["selected_tab"] = "Videos"

# For sorting (Latest, Popular, Oldest)
if "sort_order" not in st.session_state:
    st.session_state["sort_order"] = "Latest"

def reset_video_selection():
    st.session_state["selected_video_id"] = None

def set_selected_video(video_id):
    st.session_state["selected_video_id"] = video_id

def set_tab(tab_name):
    st.session_state["selected_tab"] = tab_name
    reset_video_selection()

def set_sort_order(order):
    st.session_state["sort_order"] = order
    reset_video_selection()

# Channel URL input
channel_url = st.text_input("Enter YouTube Channel URL", "")
if st.button("Load Channel"):
    ch_id = extract_channel_id(channel_url.strip())
    if not ch_id:
        st.error("Could not extract a valid channel ID from the provided URL.")
    else:
        st.session_state["selected_channel_id"] = ch_id
        reset_video_selection()
        # Check DB first
        existing_videos = load_channel_videos_from_db(ch_id)
        if len(existing_videos) == 0:
            # If no videos in DB, fetch from API
            with st.spinner("Fetching all channel videos..."):
                all_videos_basic = fetch_channel_videos(ch_id, YT_API_KEY)
                video_ids = [v["videoId"] for v in all_videos_basic]
                if video_ids:
                    details_map = fetch_video_details(video_ids, YT_API_KEY)
                    # Merge the basic list with details
                    final_list = []
                    for v in all_videos_basic:
                        vid = v["videoId"]
                        if vid in details_map:
                            merged = {
                                "videoId": vid,
                                "title": details_map[vid]["title"],
                                "publishedAt": details_map[vid]["publishedAt"],
                                "viewCount": details_map[vid]["viewCount"],
                                "likeCount": details_map[vid]["likeCount"],
                                "commentCount": details_map[vid]["commentCount"],
                                "duration": details_map[vid]["duration"],
                                "isShort": details_map[vid]["isShort"],
                                "thumbnailUrl": details_map[vid]["thumbnailUrl"],
                            }
                            final_list.append(merged)
                    save_channel_videos_to_db(ch_id, final_list)
                else:
                    st.warning("No videos found for this channel.")
        else:
            st.success(f"Loaded {len(existing_videos)} videos from the local DB.")


# If we have a channel ID, load from DB
if st.session_state["selected_channel_id"]:
    channel_videos = load_channel_videos_from_db(st.session_state["selected_channel_id"])
    if not channel_videos:
        st.warning("No videos found in DB. Try reloading or confirm the channel has uploads.")
    else:
        # Render UI to switch between "Videos" and "Shorts"
        colA, colB, colC = st.columns([1,1,1])
        with colA:
            if st.button("Videos", on_click=set_tab, args=("Videos",)):
                pass
        with colB:
            if st.button("Shorts", on_click=set_tab, args=("Shorts",)):
                pass
        
        # Render filter for "Latest", "Popular", "Oldest"
        colD, colE, colF = st.columns([1,1,1])
        with colD:
            if st.button("Latest", on_click=set_sort_order, args=("Latest",)):
                pass
        with colE:
            if st.button("Popular", on_click=set_sort_order, args=("Popular",)):
                pass
        with colF:
            if st.button("Oldest", on_click=set_sort_order, args=("Oldest",)):
                pass
        
        # Filter videos by tab
        if st.session_state["selected_tab"] == "Videos":
            filtered_videos = [v for v in channel_videos if not v["isShort"]]
        else:
            filtered_videos = [v for v in channel_videos if v["isShort"]]
        
        # Sort them
        if st.session_state["sort_order"] == "Latest":
            filtered_videos.sort(key=lambda x: x["publishedAt"], reverse=True)
        elif st.session_state["sort_order"] == "Popular":
            filtered_videos.sort(key=lambda x: x["viewCount"], reverse=True)
        elif st.session_state["sort_order"] == "Oldest":
            filtered_videos.sort(key=lambda x: x["publishedAt"], reverse=False)

        # If no video selected, show grid
        if st.session_state["selected_video_id"] is None:
            st.write("### Browse Videos")
            st.markdown("<div class='video-grid'>", unsafe_allow_html=True)
            for vid in filtered_videos:
                # Build each card
                vid_id = vid["videoId"]
                thumb = vid["thumbnailUrl"] or ""
                title = vid["title"]
                # Short stats line
                stats_text = f"{vid['viewCount']:,} views"
                
                card_html = f"""
                <div class="video-card" onClick="window.location.href='?vid={vid_id}'">
                    <div class="thumbnail-container">
                        <img src="{thumb}" alt="thumbnail">
                    </div>
                    <div class="video-info">
                        <div class="video-title">{title}</div>
                        <div class="video-stats">{stats_text}</div>
                    </div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            
            # Hacky approach: capture the query param for 'vid=' if any
            # because we used an onClick approach in HTML
            query_params = st.experimental_get_query_params()
            if "vid" in query_params:
                st.session_state["selected_video_id"] = query_params["vid"][0]
                st.experimental_set_query_params()  # clear them
                st.experimental_rerun()
        
        else:
            # Show detail view for the selected video
            sel_vid = next((v for v in filtered_videos if v["videoId"] == st.session_state["selected_video_id"]), None)
            if not sel_vid:
                st.warning("Video not found in the current list.")
            else:
                # Hide calculations, just show chart & outlier classification
                st.write("### Video Details")
                col1, col2 = st.columns([1,3])
                with col1:
                    st.image(sel_vid["thumbnailUrl"], width=300)
                with col2:
                    st.write(f"**Title:** {sel_vid['title']}")
                    published_str = sel_vid['publishedAt'].split("T")[0]
                    st.write(f"**Published:** {published_str}")
                    st.write(f"**Views:** {sel_vid['viewCount']:,}")
                    st.write(f"**Likes:** {sel_vid['likeCount']:,}")
                    st.write(f"**Comments:** {sel_vid['commentCount']:,}")
                
                # We do a mini outlier analysis
                # 1) Build a benchmark from the DB data (all or same type)
                #    We'll keep it default percentile=50 internally
                is_short_filter = sel_vid["isShort"]
                # Gather details for all videos again (in memory)
                all_vid_map = {v["videoId"]:v for v in channel_videos}
                # Build a dict keyed by videoId for the benchmark
                detail_map = {}
                for v_id, v in all_vid_map.items():
                    detail_map[v_id] = {
                        "videoId": v_id,
                        "publishedAt": v["publishedAt"],
                        "viewCount": v["viewCount"],
                        "isShort": v["isShort"]
                    }
                
                # Generate & calculate
                max_days = (datetime.datetime.now().date() - datetime.datetime.fromisoformat(sel_vid["publishedAt"].replace('Z', '+00:00')).date()).days
                df_hist = generate_historical_data(detail_map, max_days, is_short_filter=is_short_filter)
                if df_hist.empty:
                    st.warning("Not enough data to build a benchmark for this type.")
                else:
                    bench = calculate_benchmark(df_hist)
                    if bench.empty:
                        st.warning("Benchmark data is empty.")
                    else:
                        # Simulate
                        sim_df = simulate_video_performance(sel_vid, bench)
                        # Find channel avg at final day
                        final_day = min(max_days-1, bench["day"].max())
                        row = bench.loc[bench["day"] == final_day]
                        if row.empty:
                            channel_avg = 1
                        else:
                            channel_avg = row["channel_average"].values[0]
                        
                        # Outlier classification
                        outlier_cat, ratio = outlier_analysis(sel_vid, channel_avg)
                        
                        # Plot chart
                        fig = go.Figure()
                        # performance band
                        fig.add_trace(go.Scatter(
                            x=bench["day"], 
                            y=bench["lower_band"],
                            fill='tonexty',
                            fillcolor='rgba(173, 216, 230, 0.3)',
                            line=dict(width=0),
                            mode='lines',
                            name='Typical Range'
                        ))
                        fig.add_trace(go.Scatter(
                            x=bench["day"],
                            y=bench["channel_average"],
                            line=dict(color='#4285f4', dash='dash'),
                            mode='lines',
                            name='Channel Avg'
                        ))
                        fig.add_trace(go.Scatter(
                            x=bench["day"],
                            y=bench["median"],
                            line=dict(color='#34a853', dash='dot'),
                            mode='lines',
                            name='Median'
                        ))
                        fig.add_trace(go.Scatter(
                            x=sim_df["day"],
                            y=sim_df["cumulative_views"],
                            line=dict(color='#ea4335', width=3),
                            mode='lines',
                            name='This Video'
                        ))
                        fig.update_layout(
                            title="Performance Comparison",
                            xaxis_title="Days Since Upload",
                            yaxis_title="Cumulative Views",
                            height=400,
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
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # Show outlier result
                        outlier_class = "outlier-low"
                        if outlier_cat in ["Significant Positive Outlier", "Positive Outlier"]:
                            outlier_class = "outlier-high"
                        elif outlier_cat in ["Slight Positive Outlier", "Normal Performance"]:
                            outlier_class = "outlier-normal"
                        
                        colA, colB = st.columns([1,2])
                        with colA:
                            st.markdown(f"""
                            <div class='metric-card'>
                                <div>Outlier Score</div>
                                <div style='font-size: 24px; font-weight: bold;' class='{outlier_class}'>{ratio:.2f}</div>
                                <div>{outlier_cat}</div>
                            </div>
                            """, unsafe_allow_html=True)
                        with colB:
                            st.markdown(f"""
                            <div class='explanation'>
                                <p><strong>What this means:</strong></p>
                                <p>An outlier score of <strong>{ratio:.2f}</strong> means this video has 
                                <strong>{ratio:.2f}x</strong> the views compared to the channel's average at the same age.</p>
                                <ul>
                                    <li>1.0 = Exactly average performance</li>
                                    <li>&gt;1.0 = Outperforming channel average</li>
                                    <li>&lt;1.0 = Underperforming channel average</li>
                                </ul>
                            </div>
                            """, unsafe_allow_html=True)
                
                if st.button("Back to Grid"):
                    reset_video_selection()
                    st.experimental_rerun()
