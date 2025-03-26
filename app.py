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
# 1. INITIAL SETUP & DATABASE FUNCTIONS
############################################

# Try loading the YouTube API key from st.secrets
if "YT_API_KEY" in st.secrets:
    YT_API_KEY = st.secrets["YT_API_KEY"]
else:
    st.error("YouTube API key not found in st.secrets. Please add it to your secrets.")
    st.stop()

# Initialize SQLite DB (stored as "videos.db")
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

############################################
# 2. DARK THEME & CUSTOM STYLING (CSS)
############################################

st.set_page_config(page_title="YouTube Outlier Analysis", page_icon="📊", layout="wide")

st.markdown("""
<style>
    body {
        background-color: #121212;
        color: #e0e0e0;
    }
    .main-header {
        font-size: 2rem;
        font-weight: 600;
        margin-bottom: 1rem;
        color: #ffffff;
    }
    .subheader {
        font-size: 1.5rem;
        font-weight: 500;
        margin: 1rem 0;
        color: #ffffff;
    }
    /* Container for input and grid */
    .input-container {
        padding: 16px;
        background-color: #1e1e1e;
        border-radius: 8px;
        margin-bottom: 24px;
    }
    /* Video grid layout */
    .video-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        justify-content: flex-start;
    }
    /* Each video card */
    .video-card {
        width: 304.9px;
        background-color: #2c2c2c;
        border-radius: 8px;
        overflow: hidden;
        cursor: pointer;
        text-decoration: none;
        color: #e0e0e0;
    }
    .thumbnail-container {
        width: 304.9px;
        height: 171.55px;
        background-color: #444;
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
        color: #a0a0a0;
    }
    /* Metric card for detailed view */
    .metric-card {
        padding: 1rem;
        border-radius: 10px;
        text-align: center;
        background-color: #2c2c2c;
        color: #e0e0e0;
        box-shadow: 0 2px 5px rgba(0,0,0,0.5);
    }
    .outlier-high { color: #4caf50; font-weight: bold; }
    .outlier-normal { color: #8bc34a; font-weight: normal; }
    .outlier-low { color: #f44336; font-weight: bold; }
    .explanation {
        padding: 1rem;
        border-left: 4px solid #2196f3;
        background-color: #1e1e1e;
        color: #e0e0e0;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-header'>YouTube Outlier Analysis</div>", unsafe_allow_html=True)

############################################
# 3. HELPER FUNCTIONS (API, Parsing, DB)
############################################

def parse_duration(duration_str):
    """Parse ISO 8601 duration format to seconds."""
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
            return get_channel_id_from_identifier(identifier)
    if url.strip().startswith("UC"):
        return url.strip()
    return None

def get_channel_id_from_identifier(identifier):
    """Resolve channel ID using YouTube Data API."""
    try:
        search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={identifier}&key={YT_API_KEY}"
        res = requests.get(search_url).json()
        items = res.get("items", [])
        if items:
            return items[0]["id"]["channelId"]
    except:
        pass
    return None

def fetch_channel_videos(channel_id, api_key):
    """Fetch all videos from a channel's uploads playlist."""
    url = f"https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}"
    res = requests.get(url).json()
    items = res.get("items", [])
    if not items:
        return []
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    videos = []
    next_page_token = ""
    while True:
        pl_url = (f"https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails,snippet"
                  f"&maxResults=50&playlistId={uploads_playlist_id}&key={api_key}")
        if next_page_token:
            pl_url += f"&pageToken={next_page_token}"
        pl_res = requests.get(pl_url).json()
        for item in pl_res.get("items", []):
            snip = item["snippet"]
            vid_id = snip["resourceId"]["videoId"]
            videos.append({
                "videoId": vid_id,
                "title": snip["title"],
                "publishedAt": snip["publishedAt"]
            })
        next_page_token = pl_res.get("nextPageToken")
        if not next_page_token:
            break
    return videos

def fetch_video_details(video_ids, api_key):
    """Fetch detailed information for a list of video_ids."""
    details = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        chunk_str = ",".join(chunk)
        url = (f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails"
               f"&id={chunk_str}&key={api_key}")
        res = requests.get(url).json()
        for item in res.get("items", []):
            vid_id = item["id"]
            snip = item["snippet"]
            stats = item.get("statistics", {})
            content = item["contentDetails"]
            dur = parse_duration(content["duration"])
            details[vid_id] = {
                "videoId": vid_id,
                "title": snip["title"],
                "publishedAt": snip["publishedAt"],
                "viewCount": int(stats.get("viewCount", 0)),
                "likeCount": int(stats.get("likeCount", 0)),
                "commentCount": int(stats.get("commentCount", 0)),
                "duration": dur,
                "isShort": (dur <= 60),
                "thumbnailUrl": snip.get("thumbnails", {}).get("medium", {}).get("url", "")
            }
    return details

def load_channel_videos_from_db(channel_id):
    """Load videos for a channel from SQLite DB."""
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

def save_channel_videos_to_db(channel_id, video_list):
    """Save a list of videos to the DB."""
    with sqlite3.connect("videos.db") as conn:
        c = conn.cursor()
        for vid in video_list:
            c.execute("""
                INSERT OR REPLACE INTO channel_videos 
                (channel_id, video_id, title, published_at, view_count,
                 like_count, comment_count, duration, is_short, thumbnail_url, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
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
# 4. OUTLIER ANALYSIS FUNCTIONS (HIDDEN)
############################################

def generate_historical_data(video_map, max_days, is_short=None):
    """
    Build a simple historical dataset for each video up to max_days.
    (Here we use a naive linear approach for demonstration.)
    """
    today = datetime.datetime.now().date()
    data = []
    for vid_id, info in video_map.items():
        if is_short is not None and info["isShort"] != is_short:
            continue
        try:
            pub_date = datetime.datetime.fromisoformat(info["publishedAt"].replace("Z", "+00:00")).date()
            age = (today - pub_date).days
        except:
            continue
        if age < 3:
            continue
        limit = min(age, max_days)
        total = info["viewCount"]
        for d in range(limit):
            # simple linear cumulative view count
            data.append({"videoId": vid_id, "day": d, "cumulative_views": int((d+1)*total/limit)})
    return pd.DataFrame(data)

def calculate_benchmark(df):
    """Calculate median, lower (25th) and upper (75th) bands, then average of lower and upper."""
    if df.empty:
        return pd.DataFrame()
    grp = df.groupby("day")["cumulative_views"]
    out = grp.agg(["median"])
    out["lower_band"] = grp.quantile(0.25)
    out["upper_band"] = grp.quantile(0.75)
    out["channel_average"] = (out["lower_band"] + out["upper_band"]) / 2
    return out.reset_index()

def simulate_video_performance(video_info, bench_df):
    """Generate a naive performance trajectory for a single video."""
    try:
        pub_date = datetime.datetime.fromisoformat(video_info["publishedAt"].replace("Z", "+00:00")).date()
        age = (datetime.datetime.now().date() - pub_date).days
    except:
        age = 1
    data = []
    for d in range(age):
        fraction = (d+1)/age
        data.append({"day": d, "cumulative_views": int(video_info["viewCount"] * fraction), "projected": False})
    return pd.DataFrame(data)

def classify_outlier_score(view_count, channel_avg):
    """Compute the outlier ratio and return classification and ratio."""
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

############################################
# 5. SESSION STATE & USER INPUT
############################################

if "channel_id" not in st.session_state:
    st.session_state["channel_id"] = None
if "selected_sort" not in st.session_state:
    st.session_state["selected_sort"] = "Latest"
if "selected_count" not in st.session_state:
    st.session_state["selected_count"] = 10
if "analyze_clicked" not in st.session_state:
    st.session_state["analyze_clicked"] = False
if "selected_video" not in st.session_state:
    st.session_state["selected_video"] = None

# Input form container (dark themed)
with st.container():
    st.markdown("<div class='subheader'>Channel Analysis Setup</div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([3,2,2])
    with col1:
        channel_url = st.text_input("Enter Channel URL:")
    with col2:
        sort_option = st.selectbox("Sort Videos By:", ["Latest", "Popular"], index=0)
    with col3:
        count_option = st.selectbox("Number of Videos:", [10, 20, 30, 50], index=0)
    if st.button("Analyze"):
        if not channel_url:
            st.error("Please enter a channel URL.")
        else:
            ch_id = extract_channel_id(channel_url.strip())
            if not ch_id:
                st.error("Could not extract a valid channel ID from the URL.")
            else:
                st.session_state["channel_id"] = ch_id
                st.session_state["selected_sort"] = sort_option
                st.session_state["selected_count"] = count_option
                st.session_state["analyze_clicked"] = True
                st.session_state["selected_video"] = None

############################################
# 6. DISPLAY TABS IF ANALYSIS IS TRIGGERED
############################################

if st.session_state["analyze_clicked"] and st.session_state["channel_id"]:
    # Load channel videos from DB; if none, fetch and save
    videos_db = load_channel_videos_from_db(st.session_state["channel_id"])
    if not videos_db:
        with st.spinner("Fetching channel videos from YouTube..."):
            basic_list = fetch_channel_videos(st.session_state["channel_id"], YT_API_KEY)
            if not basic_list:
                st.error("No videos found for this channel.")
            else:
                ids = [v["videoId"] for v in basic_list]
                details = fetch_video_details(ids, YT_API_KEY)
                final_list = []
                for v in basic_list:
                    vid = v["videoId"]
                    if vid in details:
                        final_list.append({
                            "videoId": vid,
                            "title": details[vid]["title"],
                            "publishedAt": details[vid]["publishedAt"],
                            "viewCount": details[vid]["viewCount"],
                            "likeCount": details[vid]["likeCount"],
                            "commentCount": details[vid]["commentCount"],
                            "duration": details[vid]["duration"],
                            "isShort": details[vid]["isShort"],
                            "thumbnailUrl": details[vid]["thumbnailUrl"]
                        })
                save_channel_videos_to_db(st.session_state["channel_id"], final_list)
                videos_db = load_channel_videos_from_db(st.session_state["channel_id"])
    # Prepare separate lists for Videos and Shorts based on the video type
    vids = [v for v in videos_db if not v["isShort"]]
    shorts = [v for v in videos_db if v["isShort"]]
    # Apply sort based on the selection ("Latest" = newest first, "Popular" = highest view count)
    if st.session_state["selected_sort"] == "Latest":
        vids.sort(key=lambda x: x["publishedAt"], reverse=True)
        shorts.sort(key=lambda x: x["publishedAt"], reverse=True)
    elif st.session_state["selected_sort"] == "Popular":
        vids.sort(key=lambda x: x["viewCount"], reverse=True)
        shorts.sort(key=lambda x: x["viewCount"], reverse=True)
    # Slice to the number selected
    vids = vids[:st.session_state["selected_count"]]
    shorts = shorts[:st.session_state["selected_count"]]
    
    # Create two tabs: Videos and Shorts
    tabs = st.tabs(["Videos", "Shorts"])
    
    # Function to render a grid of cards (each card shows thumbnail, title, and outlier score)
    def render_video_grid(video_list, tab_name):
        # To compute outlier score for each video, we build a benchmark using all videos of that type.
        # Create a minimal dict mapping for historical data calculation.
        info_map = {v["videoId"]: {"publishedAt": v["publishedAt"], "viewCount": v["viewCount"], "isShort": v["isShort"]} for v in video_list}
        # For each video, use its own age as max_days.
        grid_html = "<div class='video-grid'>"
        for v in video_list:
            # Compute benchmark for this video type (naively using linear approach)
            try:
                age = (datetime.datetime.now().date() - datetime.datetime.fromisoformat(v["publishedAt"].replace("Z","+00:00")).date()).days
            except:
                age = 1
            df_hist = generate_historical_data(info_map, age, is_short=v["isShort"])
            if df_hist.empty:
                channel_avg = 1
            else:
                bench = calculate_benchmark(df_hist)
                final_day = min(age-1, bench["day"].max()) if not bench.empty else 0
                if final_day == 0:
                    channel_avg = 1
                else:
                    row = bench.loc[bench["day"] == final_day]
                    channel_avg = row["channel_average"].values[0] if not row.empty else 1
            outlier_cat, ratio = classify_outlier_score(v["viewCount"], channel_avg)
            # Build card HTML with clickable onClick (using query parameters)
            card = f"""
            <div class="video-card" onClick="window.location.href='?video={v['videoId']}&tab={tab_name}'">
                <div class="thumbnail-container">
                    <img src="{v['thumbnailUrl']}" alt="thumbnail">
                </div>
                <div class="video-info">
                    <div class="video-title">{v['title']}</div>
                    <div class="video-stats">Outlier: {ratio:.2f}x</div>
                </div>
            </div>
            """
            grid_html += card
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)
    
    # Check for query parameters (when a card is clicked)
    qp = st.experimental_get_query_params()
    if "video" in qp and "tab" in qp:
        st.session_state["selected_video"] = qp["video"][0]
        selected_tab = qp["tab"][0]
        st.experimental_set_query_params()  # clear query params
    else:
        st.session_state["selected_video"] = None

    # VIDEOS tab
    with tabs[0]:
        st.markdown("<div class='subheader'>Long-form Videos</div>", unsafe_allow_html=True)
        if st.session_state["selected_video"] is None:
            render_video_grid(vids, "videos")
        else:
            # Show details for selected video from the long-form list
            sel = next((x for x in vids if x["videoId"] == st.session_state["selected_video"]), None)
            if sel:
                st.markdown("<div class='subheader'>Video Details</div>", unsafe_allow_html=True)
                col1, col2 = st.columns([1,2])
                with col1:
                    st.image(sel["thumbnailUrl"], width=300)
                with col2:
                    st.write(f"**Title:** {sel['title']}")
                    pub_date = sel["publishedAt"].split("T")[0]
                    st.write(f"**Published:** {pub_date}")
                    st.write(f"**Views:** {sel['viewCount']:,}")
                    st.write(f"**Likes:** {sel['likeCount']:,}")
                    st.write(f"**Comments:** {sel['commentCount']:,}")
                # Build a benchmark and simulate performance for chart
                try:
                    age = (datetime.datetime.now().date() - datetime.datetime.fromisoformat(sel["publishedAt"].replace("Z","+00:00")).date()).days
                except:
                    age = 1
                info_map = {v["videoId"]: {"publishedAt": v["publishedAt"], "viewCount": v["viewCount"], "isShort": v["isShort"]} for v in vids}
                df_hist = generate_historical_data(info_map, age, is_short=False)
                if df_hist.empty:
                    st.warning("Not enough data for benchmark.")
                else:
                    bench = calculate_benchmark(df_hist)
                    final_day = min(age-1, bench["day"].max())
                    row = bench.loc[bench["day"] == final_day]
                    channel_avg = row["channel_average"].values[0] if not row.empty else 1
                    outlier_cat, ratio = classify_outlier_score(sel["viewCount"], channel_avg)
                    perf_df = simulate_video_performance(sel, bench)
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["lower_band"],
                        fill='tonexty',
                        fillcolor='rgba(76,175,80,0.3)',
                        line=dict(width=0),
                        mode='lines',
                        name='Typical Range'
                    ))
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["channel_average"],
                        line=dict(color='#2196f3', dash='dash'),
                        mode='lines',
                        name='Channel Avg'
                    ))
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["median"],
                        line=dict(color='#8bc34a', dash='dot'),
                        mode='lines',
                        name='Median'
                    ))
                    fig.add_trace(go.Scatter(
                        x=perf_df["day"],
                        y=perf_df["cumulative_views"],
                        line=dict(color='#ff5722', width=3),
                        mode='lines',
                        name='This Video'
                    ))
                    fig.update_layout(
                        title="Performance Comparison",
                        xaxis_title="Days Since Upload",
                        yaxis_title="Cumulative Views",
                        height=400,
                        plot_bgcolor="#121212",
                        paper_bgcolor="#121212",
                        font_color="#e0e0e0"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.markdown(f"""
                    <div class='metric-card'>
                        <div>Outlier Score</div>
                        <div style='font-size: 24px; font-weight: bold;'>{ratio:.2f}x</div>
                        <div>{outlier_cat}</div>
                    </div>
                    """, unsafe_allow_html=True)
            if st.button("Back to Grid", key="back_vid"):
                st.session_state["selected_video"] = None
                st.experimental_rerun()
                
    # SHORTS tab
    with tabs[1]:
        st.markdown("<div class='subheader'>Shorts</div>", unsafe_allow_html=True)
        if st.session_state["selected_video"] is None:
            render_video_grid(shorts, "shorts")
        else:
            sel = next((x for x in shorts if x["videoId"] == st.session_state["selected_video"]), None)
            if sel:
                st.markdown("<div class='subheader'>Short Details</div>", unsafe_allow_html=True)
                col1, col2 = st.columns([1,2])
                with col1:
                    st.image(sel["thumbnailUrl"], width=300)
                with col2:
                    st.write(f"**Title:** {sel['title']}")
                    pub_date = sel["publishedAt"].split("T")[0]
                    st.write(f"**Published:** {pub_date}")
                    st.write(f"**Views:** {sel['viewCount']:,}")
                    st.write(f"**Likes:** {sel['likeCount']:,}")
                    st.write(f"**Comments:** {sel['commentCount']:,}")
                try:
                    age = (datetime.datetime.now().date() - datetime.datetime.fromisoformat(sel["publishedAt"].replace("Z","+00:00")).date()).days
                except:
                    age = 1
                info_map = {v["videoId"]: {"publishedAt": v["publishedAt"], "viewCount": v["viewCount"], "isShort": v["isShort"]} for v in shorts}
                df_hist = generate_historical_data(info_map, age, is_short=True)
                if df_hist.empty:
                    st.warning("Not enough data for benchmark.")
                else:
                    bench = calculate_benchmark(df_hist)
                    final_day = min(age-1, bench["day"].max())
                    row = bench.loc[bench["day"] == final_day]
                    channel_avg = row["channel_average"].values[0] if not row.empty else 1
                    outlier_cat, ratio = classify_outlier_score(sel["viewCount"], channel_avg)
                    perf_df = simulate_video_performance(sel, bench)
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["lower_band"],
                        fill='tonexty',
                        fillcolor='rgba(76,175,80,0.3)',
                        line=dict(width=0),
                        mode='lines',
                        name='Typical Range'
                    ))
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["channel_average"],
                        line=dict(color='#2196f3', dash='dash'),
                        mode='lines',
                        name='Channel Avg'
                    ))
                    fig.add_trace(go.Scatter(
                        x=bench["day"],
                        y=bench["median"],
                        line=dict(color='#8bc34a', dash='dot'),
                        mode='lines',
                        name='Median'
                    ))
                    fig.add_trace(go.Scatter(
                        x=perf_df["day"],
                        y=perf_df["cumulative_views"],
                        line=dict(color='#ff5722', width=3),
                        mode='lines',
                        name='This Short'
                    ))
                    fig.update_layout(
                        title="Shorts Performance Comparison",
                        xaxis_title="Days Since Upload",
                        yaxis_title="Cumulative Views",
                        height=400,
                        plot_bgcolor="#121212",
                        paper_bgcolor="#121212",
                        font_color="#e0e0e0"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    st.markdown(f"""
                    <div class='metric-card'>
                        <div>Outlier Score</div>
                        <div style='font-size: 24px; font-weight: bold;'>{ratio:.2f}x</div>
                        <div>{outlier_cat}</div>
                    </div>
                    """, unsafe_allow_html=True)
            if st.button("Back to Grid", key="back_shorts"):
                st.session_state["selected_video"] = None
                st.experimental_rerun()
