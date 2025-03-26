import streamlit as st
import requests
import pandas as pd
import numpy as np
import datetime
import plotly.graph_objects as go
import re
import sqlite3
from datetime import timedelta

########################################
# 1. INITIAL SETUP & DATABASE FUNCTIONS
########################################

# Try loading the YouTube API key from st.secrets
if "YT_API_KEY" in st.secrets:
    YT_API_KEY = st.secrets["YT_API_KEY"]
else:
    st.error("YouTube API key not found in st.secrets. Please add it to your secrets.")
    st.stop()

# Initialize the SQLite database
def init_db():
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
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
        conn.commit()

init_db()

########################################
# 2. CUSTOM STYLING (CSS)
########################################

st.set_page_config(page_title="YouTube Video Outlier Analysis",
                   page_icon="📊",
                   layout="wide")

# Basic CSS to replicate a YouTube-like layout
st.markdown("""
<style>
    /* Page body */
    body {
        background-color: #fff;
        color: #333;
    }
    /* Main header */
    .main-header {
        font-size: 2rem;
        font-weight: 600;
        margin-bottom: 1rem;
        color: #333;
    }
    /* Subheader */
    .subheader {
        font-size: 1.5rem;
        font-weight: 500;
        margin: 1rem 0;
        color: #333;
    }
    /* The tabs across the top (like YouTube: Home, Videos, Shorts, etc.) */
    .block-container {
        max-width: 1200px;
        margin: 0 auto;
    }
    /* Video grid styling */
    .video-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        margin-top: 16px;
    }
    .video-card {
        width: 304.9px; /* as requested */
        background-color: #f9f9f9;
        border-radius: 8px;
        overflow: hidden;
        text-decoration: none;
        color: inherit;
        cursor: pointer;
    }
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
    .video-info {
        padding: 8px;
    }
    .video-title {
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 4px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .video-stats {
        font-size: 12px;
        color: #666;
    }
    /* Metric card styling */
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
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-header'>YouTube Video Outlier Analysis</div>", unsafe_allow_html=True)

########################################
# 3. HELPER FUNCTIONS
########################################

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
            return get_channel_id_from_identifier(identifier)
    # If the entire URL itself starts with UC
    if url.strip().startswith('UC'):
        return url.strip()
    return None

def get_channel_id_from_identifier(identifier):
    """Resolve channel ID by searching the identifier via YouTube Data API"""
    try:
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
    Fetch *all* videos from a channel's uploads playlist
    """
    # First, get the uploads playlist
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    res = requests.get(url).json()
    items = res.get('items', [])
    if not items:
        return []
    uploads_playlist_id = items[0]['contentDetails']['relatedPlaylists']['uploads']
    
    videos = []
    next_page_token = ""
    while True:
        playlist_url = (
            f"https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=contentDetails,snippet&maxResults=50&playlistId={uploads_playlist_id}&key={api_key}"
        )
        if next_page_token:
            playlist_url += f"&pageToken={next_page_token}"
        playlist_res = requests.get(playlist_url).json()
        for item in playlist_res.get('items', []):
            snippet = item['snippet']
            vid_id = snippet['resourceId']['videoId']
            videos.append({
                "videoId": vid_id,
                "title": snippet['title'],
                "publishedAt": snippet['publishedAt']
            })
        next_page_token = playlist_res.get('nextPageToken')
        if not next_page_token:
            break
    return videos

def fetch_video_details(video_ids, api_key):
    """Fetch snippet/stats for a list of video_ids. Returns dict keyed by video_id."""
    details = {}
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
            dur_sec = parse_duration(content_details['duration'])
            details[vid_id] = {
                "videoId": vid_id,
                "title": snippet['title'],
                "publishedAt": snippet['publishedAt'],
                "viewCount": int(stats.get('viewCount', 0)),
                "likeCount": int(stats.get('likeCount', 0)),
                "commentCount": int(stats.get('commentCount', 0)),
                "duration": dur_sec,
                "isShort": (dur_sec <= 60),
                "thumbnailUrl": snippet.get('thumbnails', {}).get('medium', {}).get('url', "")
            }
    return details

########################################
# 4. DATABASE CACHING
########################################

def load_channel_videos_from_db(channel_id):
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        c.execute("""
            SELECT video_id, title, published_at, view_count, like_count, comment_count,
                   duration, is_short, thumbnail_url
            FROM channel_videos
            WHERE channel_id = ?
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

def save_channel_videos_to_db(channel_id, video_list):
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        for vid in video_list:
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

########################################
# 5. OUTLIER ANALYSIS (Hidden from UI)
########################################

def generate_historical_data(video_map, max_days, is_short=None):
    """
    Build a naive historical dataset up to 'max_days' for each video.
    Middle range is fixed at 50% internally => 25th-75th percentile
    We'll do a simple linear approach for demonstration.
    """
    today = datetime.datetime.now().date()
    rows = []
    for vid_id, info in video_map.items():
        if is_short is not None and info["isShort"] != is_short:
            continue
        try:
            pub_date = datetime.datetime.fromisoformat(info["publishedAt"].replace('Z', '+00:00')).date()
            age_days = (today - pub_date).days
        except:
            continue
        if age_days < 3:
            continue
        limit = min(age_days, max_days)
        total = info["viewCount"]
        if limit <= 0:
            continue
        # simple linear distribution
        for d in range(limit):
            daily_val = int((d+1) * (total / limit))
            rows.append({
                "videoId": vid_id,
                "day": d,
                "cumulative_views": daily_val
            })
    return pd.DataFrame(rows)

def calculate_benchmark(df):
    """Compute median, 25th (lower), 75th (upper), and average of 25th+75th => channel_average."""
    if df.empty:
        return pd.DataFrame()
    grouped = df.groupby("day")["cumulative_views"]
    out = grouped.agg(["median", "count"])
    out["lower_band"] = grouped.quantile(0.25)
    out["upper_band"] = grouped.quantile(0.75)
    out["channel_average"] = (out["lower_band"] + out["upper_band"]) / 2
    return out.reset_index()

def simulate_video_performance(video_info, bench_df):
    """Return a small DataFrame of day vs. cumulative_views for the chosen video."""
    try:
        pub_date = datetime.datetime.fromisoformat(video_info["publishedAt"].replace('Z', '+00:00')).date()
        now = datetime.datetime.now().date()
        age_days = (now - pub_date).days
    except:
        age_days = 1
    if age_days < 1:
        age_days = 1
    data = []
    for d in range(age_days):
        # linear fraction
        fraction = (d+1)/age_days
        cv = int(video_info["viewCount"] * fraction)
        data.append({"day": d, "cumulative_views": cv, "projected": False})
    return pd.DataFrame(data)

def classify_outlier_score(view_count, channel_avg):
    if channel_avg <= 0:
        return "Significant Positive Outlier", 999.9
    ratio = view_count / channel_avg
    if ratio >= 2.0:
        return "Significant Positive Outlier", ratio
    elif ratio >= 1.5:
        return "Positive Outlier", ratio
    elif ratio >= 1.2:
        return "Slight Positive Outlier", ratio
    elif ratio >= 0.8:
        return "Normal Performance", ratio
    elif ratio >= 0.5:
        return "Slight Negative Outlier", ratio
    else:
        return "Significant Negative Outlier", ratio

########################################
# 6. SESSION STATE
########################################

if "channel_id" not in st.session_state:
    st.session_state["channel_id"] = None
if "videos" not in st.session_state:
    st.session_state["videos"] = []
if "selected_video" not in st.session_state:
    st.session_state["selected_video"] = None

########################################
# 7. TOP-LEVEL TABS (like a YouTube channel page)
########################################

tabs = st.tabs(["Home", "Videos", "Shorts", "Playlists", "Posts"])

############################
# Tab 1: HOME
############################
with tabs[0]:
    st.subheader("Home")
    st.write("Enter a YouTube channel URL to load all videos into our local DB.")
    
    url = st.text_input("Channel URL:", "")
    if st.button("Load Channel"):
        ch_id = extract_channel_id(url.strip())
        if not ch_id:
            st.error("Could not extract a valid channel ID. Check your URL.")
        else:
            st.session_state["channel_id"] = ch_id
            st.session_state["selected_video"] = None
            # Check if DB has videos
            existing = load_channel_videos_from_db(ch_id)
            if len(existing) == 0:
                with st.spinner("Fetching videos from YouTube..."):
                    # 1) Get basic info from channel's uploads
                    raw_list = fetch_channel_videos(ch_id, YT_API_KEY)
                    ids = [r["videoId"] for r in raw_list]
                    if not ids:
                        st.warning("No uploads found for this channel.")
                    else:
                        # 2) Get details
                        det_map = fetch_video_details(ids, YT_API_KEY)
                        final_list = []
                        for item in raw_list:
                            vid = item["videoId"]
                            if vid in det_map:
                                final_list.append({
                                    "videoId": vid,
                                    "title": det_map[vid]["title"],
                                    "publishedAt": det_map[vid]["publishedAt"],
                                    "viewCount": det_map[vid]["viewCount"],
                                    "likeCount": det_map[vid]["likeCount"],
                                    "commentCount": det_map[vid]["commentCount"],
                                    "duration": det_map[vid]["duration"],
                                    "isShort": det_map[vid]["isShort"],
                                    "thumbnailUrl": det_map[vid]["thumbnailUrl"]
                                })
                        # Save to DB
                        save_channel_videos_to_db(ch_id, final_list)
                        st.success(f"Loaded {len(final_list)} videos into the database.")
            else:
                st.success(f"Channel already in DB with {len(existing)} videos cached.")

############################
# Utility to sort & slice
############################
def sort_and_slice_videos(videos, sort_by, how_many):
    # Filter out based on 'sort_by'
    if sort_by == "Latest":
        videos.sort(key=lambda x: x["publishedAt"], reverse=True)
    elif sort_by == "Popular":
        videos.sort(key=lambda x: x["viewCount"], reverse=True)
    elif sort_by == "Oldest":
        videos.sort(key=lambda x: x["publishedAt"], reverse=False)
    return videos[:how_many]

############################
# Tab 2: VIDEOS
############################
with tabs[1]:
    st.subheader("Videos")
    # If no channel loaded, ask user to go Home
    if not st.session_state["channel_id"]:
        st.info("Go to Home tab to load a channel first.")
    else:
        # Load from DB
        all_vids = load_channel_videos_from_db(st.session_state["channel_id"])
        # Filter out Shorts => only long-form
        vids = [v for v in all_vids if not v["isShort"]]
        
        # If none, show message
        if not vids:
            st.warning("No long-form videos found for this channel.")
        else:
            colA, colB = st.columns([1,1])
            with colA:
                sort_option = st.selectbox("Sort by:", ["Latest", "Popular", "Oldest"], index=0)
            with colB:
                how_many = st.selectbox("Number of videos to show:", [10,20,30,50,len(vids)], index=0)
            
            # Sort & slice
            display_videos = sort_and_slice_videos(vids, sort_option, how_many)
            
            # If no video is selected, show the grid
            if not st.session_state["selected_video"]:
                st.markdown("<div class='video-grid'>", unsafe_allow_html=True)
                for v in display_videos:
                    card_html = f"""
                    <div class="video-card" 
                         onClick="window.location.href='?video={v['videoId']}&tab=videos'">
                        <div class="thumbnail-container">
                            <img src="{v['thumbnailUrl']}" alt="thumbnail">
                        </div>
                        <div class="video-info">
                            <div class="video-title">{v['title']}</div>
                            <div class="video-stats">{v['viewCount']:,} views</div>
                        </div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)
                
                # If user clicked a card, we pick up query params
                query_params = st.experimental_get_query_params()
                if "video" in query_params and "tab" in query_params and query_params["tab"][0] == "videos":
                    st.session_state["selected_video"] = query_params["video"][0]
                    # Clear the param so we don't re-trigger
                    st.experimental_set_query_params()
                    st.experimental_rerun()
            else:
                # Show detail for selected video
                # Find it
                sel = next((x for x in vids if x["videoId"] == st.session_state["selected_video"]), None)
                if not sel:
                    st.warning("Video not found among current list. Clear selection.")
                else:
                    # Detail UI
                    col1, col2 = st.columns([1,2])
                    with col1:
                        st.image(sel["thumbnailUrl"], width=300)
                    with col2:
                        st.write(f"**Title:** {sel['title']}")
                        published_str = sel['publishedAt'].split("T")[0]
                        st.write(f"**Published:** {published_str}")
                        st.write(f"**Views:** {sel['viewCount']:,}")
                        st.write(f"**Likes:** {sel['likeCount']:,}")
                        st.write(f"**Comments:** {sel['commentCount']:,}")
                    
                    # Build a benchmark from the same type (long-form)
                    # to do the hidden outlier analysis
                    # 1) create a dict of {vid_id: minimal info}
                    info_map = {}
                    for v in vids:
                        info_map[v["videoId"]] = {
                            "publishedAt": v["publishedAt"],
                            "viewCount": v["viewCount"],
                            "isShort": v["isShort"]
                        }
                    # 2) generate historical data
                    video_age = (datetime.datetime.now().date() -
                                 datetime.datetime.fromisoformat(sel["publishedAt"].replace('Z','+00:00')).date()).days
                    df_hist = generate_historical_data(info_map, video_age, is_short=False)
                    if df_hist.empty:
                        st.warning("Not enough data to compute a channel benchmark.")
                    else:
                        bench = calculate_benchmark(df_hist)
                        if bench.empty:
                            st.warning("Benchmark is empty.")
                        else:
                            # Simulate this video's performance
                            perf_df = simulate_video_performance(sel, bench)
                            # Find channel average at final day
                            final_day = min(video_age-1, bench["day"].max())
                            row = bench.loc[bench["day"] == final_day]
                            if row.empty:
                                channel_avg = 1
                            else:
                                channel_avg = row["channel_average"].values[0]
                            
                            # Classify outlier
                            outlier_cat, ratio = classify_outlier_score(sel["viewCount"], channel_avg)
                            
                            # Build chart
                            fig = go.Figure()
                            # typical performance range
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
                                x=perf_df["day"],
                                y=perf_df["cumulative_views"],
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
                            
                            # Show outlier classification only
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
                                    <p>This video has <strong>{ratio:.2f}x</strong> the views compared to the channel's average at the same age.</p>
                                    <ul>
                                        <li>1.0 = Exactly average performance</li>
                                        <li>&gt;1.0 = Outperforming channel average</li>
                                        <li>&lt;1.0 = Underperforming channel average</li>
                                    </ul>
                                </div>
                                """, unsafe_allow_html=True)
                    
                    if st.button("Back to Videos"):
                        st.session_state["selected_video"] = None
                        st.experimental_rerun()

############################
# Tab 3: SHORTS
############################
with tabs[2]:
    st.subheader("Shorts")
    if not st.session_state["channel_id"]:
        st.info("Go to Home tab to load a channel first.")
    else:
        all_vids = load_channel_videos_from_db(st.session_state["channel_id"])
        # Filter for shorts only
        vids = [v for v in all_vids if v["isShort"]]
        if not vids:
            st.warning("No shorts found for this channel.")
        else:
            colA, colB = st.columns([1,1])
            with colA:
                sort_option = st.selectbox("Sort by:", ["Latest", "Popular", "Oldest"], index=0, key="shorts_sort")
            with colB:
                how_many = st.selectbox("Number of videos to show:", [10,20,30,50,len(vids)], index=0, key="shorts_limit")
            
            display_videos = sort_and_slice_videos(vids, sort_option, how_many)
            
            if not st.session_state["selected_video"]:
                st.markdown("<div class='video-grid'>", unsafe_allow_html=True)
                for v in display_videos:
                    card_html = f"""
                    <div class="video-card"
                         onClick="window.location.href='?video={v['videoId']}&tab=shorts'">
                        <div class="thumbnail-container">
                            <img src="{v['thumbnailUrl']}" alt="thumbnail">
                        </div>
                        <div class="video-info">
                            <div class="video-title">{v['title']}</div>
                            <div class="video-stats">{v['viewCount']:,} views</div>
                        </div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)
                
                query_params = st.experimental_get_query_params()
                if "video" in query_params and "tab" in query_params and query_params["tab"][0] == "shorts":
                    st.session_state["selected_video"] = query_params["video"][0]
                    st.experimental_set_query_params()
                    st.experimental_rerun()
            else:
                sel = next((x for x in vids if x["videoId"] == st.session_state["selected_video"]), None)
                if not sel:
                    st.warning("Short not found among current list. Clear selection.")
                else:
                    col1, col2 = st.columns([1,2])
                    with col1:
                        st.image(sel["thumbnailUrl"], width=300)
                    with col2:
                        st.write(f"**Title:** {sel['title']}")
                        pub_str = sel['publishedAt'].split("T")[0]
                        st.write(f"**Published:** {pub_str}")
                        st.write(f"**Views:** {sel['viewCount']:,}")
                        st.write(f"**Likes:** {sel['likeCount']:,}")
                        st.write(f"**Comments:** {sel['commentCount']:,}")
                    
                    # Build a benchmark from the same type (shorts)
                    info_map = {}
                    for v in vids:
                        info_map[v["videoId"]] = {
                            "publishedAt": v["publishedAt"],
                            "viewCount": v["viewCount"],
                            "isShort": v["isShort"]
                        }
                    vid_age = (datetime.datetime.now().date() -
                               datetime.datetime.fromisoformat(sel["publishedAt"].replace('Z','+00:00')).date()).days
                    df_hist = generate_historical_data(info_map, vid_age, is_short=True)
                    if df_hist.empty:
                        st.warning("Not enough data to build a Shorts benchmark.")
                    else:
                        bench = calculate_benchmark(df_hist)
                        if bench.empty:
                            st.warning("Benchmark is empty.")
                        else:
                            perf_df = simulate_video_performance(sel, bench)
                            final_day = min(vid_age-1, bench["day"].max())
                            row = bench.loc[bench["day"] == final_day]
                            if row.empty:
                                channel_avg = 1
                            else:
                                channel_avg = row["channel_average"].values[0]
                            
                            outlier_cat, ratio = classify_outlier_score(sel["viewCount"], channel_avg)
                            
                            fig = go.Figure()
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
                                x=perf_df["day"],
                                y=perf_df["cumulative_views"],
                                line=dict(color='#ea4335', width=3),
                                mode='lines',
                                name='This Short'
                            ))
                            fig.update_layout(
                                title="Shorts Performance Comparison",
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
                                    <p>This Short has <strong>{ratio:.2f}x</strong> the views compared to the channel's average at the same age.</p>
                                    <ul>
                                        <li>1.0 = Exactly average performance</li>
                                        <li>&gt;1.0 = Outperforming channel average</li>
                                        <li>&lt;1.0 = Underperforming channel average</li>
                                    </ul>
                                </div>
                                """, unsafe_allow_html=True)
                    
                    if st.button("Back to Shorts"):
                        st.session_state["selected_video"] = None
                        st.experimental_rerun()

############################
# Tabs 4 & 5: Placeholders
############################
with tabs[3]:
    st.subheader("Playlists")
    st.info("Placeholder for playlists (not implemented).")

with tabs[4]:
    st.subheader("Posts")
    st.info("Placeholder for posts/community tab (not implemented).")
