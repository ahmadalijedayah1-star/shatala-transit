import libsql_client
import os
try:
    import libsql_client
except ImportError:
    libsql_client = None

import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import LocateControl
import sqlite3
import json
import uuid
import hmac
import hashlib
import os
import math
import time
import requests

# --- 1. الإعدادات الأساسية والحدود الجغرافية للأردن ---
SUPER_ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "Desert#94-Galaxy!Amman_82")
DB_FILE = "masar_database.db"

JORDAN_CENTER = [31.95, 35.91]  # Amman Center
JORDAN_BOUNDS = [[29.1, 34.8], [33.5, 39.4]]

# --- 2. إدارة قاعدة البيانات وأمان الحسابات ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw_secure(password: str, salt: bytes = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"{salt.hex()}:{key.hex()}"

def verify_pw_secure(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, key_hex = stored_hash.split(":")
        salt = bytes.fromhex(salt_hex)
        expected_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return hmac.compare_digest(expected_key.hex(), key_hex)
    except Exception:
        return False


def get_db_client():
    turso_url = st.secrets.get("TURSO_DB_URL") if "TURSO_DB_URL" in st.secrets else os.getenv("TURSO_DB_URL")
    turso_token = st.secrets.get("TURSO_AUTH_TOKEN") if "TURSO_AUTH_TOKEN" in st.secrets else os.getenv("TURSO_AUTH_TOKEN")
    if turso_url and turso_token and libsql_client:
        return libsql_client.create_client_sync(url=turso_url, auth_token=turso_token)
    return None


def get_db_client():
    turso_url = st.secrets.get("TURSO_DB_URL") if "TURSO_DB_URL" in st.secrets else os.getenv("TURSO_DB_URL")
    turso_token = st.secrets.get("TURSO_AUTH_TOKEN") if "TURSO_AUTH_TOKEN" in st.secrets else os.getenv("TURSO_AUTH_TOKEN")
    if turso_url and turso_token and libsql_client:
        return libsql_client.create_client_sync(url=turso_url, auth_token=turso_token)
    return None

def init_db():
    client = get_db_client()
    create_tables_sql = [
        '''CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            university TEXT NOT NULL,
            fare REAL NOT NULL,
            stations TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );''',
        '''CREATE TABLE IF NOT EXISTS route_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_note TEXT,
            start_point TEXT,
            end_point TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );''',
        '''CREATE TABLE IF NOT EXISTS assistants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'assistant',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );'''
    ]
    if client:
        with client:
            for sql in create_tables_sql:
                client.execute(sql)
    else:
        conn = sqlite3.connect("masar_database.db")
        cursor = conn.cursor()
        for sql in create_tables_sql:
            cursor.execute(sql)
        conn.commit()
        conn.close()

def execute_query(query, params=()):
    client = get_db_client()
    if client:
        with client:
            rs = client.execute(query, params)
            return rs.rows
    else:
        conn = sqlite3.connect("masar_database.db")
        cursor = conn.cursor()
        cursor.execute(query, params)
        data = cursor.fetchall()
        conn.commit()
        conn.close()
        return data

def is_valid_coord(coord):
    if isinstance(coord, (list, tuple)) and len(coord) == 2:
        lat, lon = coord[0], coord[1]
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return (JORDAN_BOUNDS[0][0] <= lat <= JORDAN_BOUNDS[1][0]) and \
                   (JORDAN_BOUNDS[0][1] <= lon <= JORDAN_BOUNDS[1][1])
    return False

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def calculate_route_progress(user_lat, user_lng, route_coords, avg_speed_kmh=42.0):
    if not route_coords or len(route_coords) < 2:
        return None

    leg_lengths = [
        haversine_km(route_coords[i][0], route_coords[i][1], route_coords[i+1][0], route_coords[i+1][1])
        for i in range(len(route_coords) - 1)
    ]
    total_distance = sum(leg_lengths)
    if total_distance <= 0:
        return None

    closest_idx = 0
    min_dist_to_route = float("inf")
    for idx, pt in enumerate(route_coords):
        d = haversine_km(user_lat, user_lng, pt[0], pt[1])
        if d < min_dist_to_route:
            min_dist_to_route = d
            closest_idx = idx

    distance_covered = sum(leg_lengths[:closest_idx])
    remaining_distance = max(0.0, total_distance - distance_covered)
    progress_pct = min(100.0, (distance_covered / total_distance) * 100)

    remaining_hours = remaining_distance / avg_speed_kmh if avg_speed_kmh > 0 else 0
    remaining_minutes = max(1, math.ceil(remaining_hours * 60)) if remaining_distance > 0.1 else 0

    return {
        "total_km": round(total_distance, 2),
        "covered_km": round(distance_covered, 2),
        "remaining_km": round(remaining_distance, 2),
        "progress_pct": int(progress_pct),
        "off_route_km": round(min_dist_to_route, 2),
        "remaining_minutes": remaining_minutes,
        "closest_idx": closest_idx
    }

@st.cache_data(show_spinner=False, ttl=86400)
def get_coords_from_name(place_name):
    if not place_name or len(place_name.strip()) > 80:
        return None
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "MasarAppJordan/1.0"}
    params = {
        "q": f"{place_name.strip()}, الأردن",
        "format": "json",
        "countrycodes": "jo",
        "limit": 1,
        "accept-language": "ar"
    }
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                pt = [float(data[0]["lat"]), float(data[0]["lon"])]
                if is_valid_coord(pt):
                    return pt
    except Exception:
        pass
    return None

def fetch_street_geometry(start_coords, end_coords):
    if not (is_valid_coord(start_coords) and is_valid_coord(end_coords)):
        return [list(start_coords), list(end_coords)]
    url = f"https://router.project-osrm.org/route/v1/driving/{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}?overview=full&geometries=geojson"
    try:
        headers = {"User-Agent": "MasarAppJordan/1.0"}
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json()
            if data.get("routes"):
                raw_coords = data["routes"][0]["geometry"]["coordinates"]
                clean_geom = [[lat, lng] for lng, lat in raw_coords if is_valid_coord([lat, lng])]
                if len(clean_geom) >= 2:
                    return clean_geom
    except Exception:
        pass
    return [list(start_coords), list(end_coords)]

# --- 4. إدارة حالة الجلسة ---
session_defaults = {
    "auth_role": None,
    "auth_user": None,
    "active_tracking": False,
    "tracking_route_id": None,
    "sim_step": 0,
    "pin_mode": None,
    "pin_start": None,
    "pin_end": None,
    "user_current_pos": None,
    "last_processed_click": None
}
for k, v in session_defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# --- 5. تهيئة الصفحة العامة ---
st.set_page_config(page_title="شعتَله - رفيق باصات الجامعات الأردنية", page_icon="🎓", layout="wide")
st.sidebar.title("🚌 شعتَله - مسارات الجامعات")
mode = st.sidebar.radio("القائمة الرئيسية:", ["🧭 واجهة الركاب والتتبع المباشر", "🔐 Admin Panel"])

# --- 6. واجهة الركاب والتتبع المباشر ---
if mode == "🧭 واجهة الركاب والتتبع المباشر":
    st.header("🗺️ رحلات الجامعات والتتبع الميداني")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM routes WHERE status = 'active'")
        rows = cursor.fetchall()

    routes = []
    for r in rows:
        try:
            c = json.loads(r["coordinates"])
            if len(c) >= 2:
                routes.append({
                    "id": r["id"],
                    "name": r["name"],
                    "start": r["start_point"],
                    "end": r["end_point"],
                    "stops": json.loads(r["stops"]) if r["stops"] else [],
                    "fare": float(r["fare"]),
                    "time": r["duration"],
                    "coordinates": c
                })
        except Exception:
            continue

    route_map_dict = {r["name"]: r for r in routes}
    selected_name = st.selectbox("🚍 اختر خط السير المخصص:", options=["(عرض كل الخطوط)"] + list(route_map_dict.keys()))
    selected_route = route_map_dict.get(selected_name)

    # التحكم بالرحلة
    if selected_route:
        t_col1, t_col2, t_col3 = st.columns([2, 1, 1])
        with t_col1:
            st.markdown(f"**الخط الحالي:** `{selected_route['name']}` | الأجرة: **{selected_route['fare']:.2f} د.أ**")
        with t_col2:
            if not st.session_state["active_tracking"] or st.session_state["tracking_route_id"] != selected_route["id"]:
                if st.button("🚀 بدء تتبع الباص", type="primary", use_container_width=True):
                    st.session_state["active_tracking"] = True
                    st.session_state["tracking_route_id"] = selected_route["id"]
                    st.session_state["sim_step"] = 0
                    st.session_state["user_current_pos"] = selected_route["coordinates"][0]
                    st.rerun()
            else:
                if st.button("🛑 إنهاء الرحلة", type="secondary", use_container_width=True):
                    st.session_state["active_tracking"] = False
                    st.session_state["tracking_route_id"] = None
                    st.session_state["sim_step"] = 0
                    st.rerun()
        with t_col3:
            if st.session_state["active_tracking"] and st.session_state["tracking_route_id"] == selected_route["id"]:
                if st.button("⏩ خطوة للأمام (محاكاة)", use_container_width=True):
                    max_steps = len(selected_route["coordinates"]) - 1
                    step_increment = max(1, max_steps // 12)
                    st.session_state["sim_step"] = min(max_steps, st.session_state["sim_step"] + step_increment)
                    st.session_state["user_current_pos"] = selected_route["coordinates"][st.session_state["sim_step"]]
                    st.rerun()

    # أدوات التثبيت التفاعلي بالدبوس
    st.caption("أدوات التثبيت المباشر على الخريطة (اضغط الزر ثم انقر على موقعك في الخريطة):")
    c_btn1, c_btn2, c_btn3, c_btn4 = st.columns(4)
    with c_btn1:
        if st.button("📍 موقعي اليدوي", type="primary" if st.session_state["pin_mode"] == "my_pos" else "secondary", use_container_width=True):
            st.session_state["pin_mode"] = "my_pos"
            st.rerun()
    with c_btn2:
        if st.button("🟢 نقطة الانطلاق", type="primary" if st.session_state["pin_mode"] == "start" else "secondary", use_container_width=True):
            st.session_state["pin_mode"] = "start"
            st.rerun()
    with c_btn3:
        if st.button("🎓 بوابة الجامعة", type="primary" if st.session_state["pin_mode"] == "end" else "secondary", use_container_width=True):
            st.session_state["pin_mode"] = "end"
            st.rerun()
    with c_btn4:
        if st.button("🔄 تفريغ العلامات", use_container_width=True):
            st.session_state["pin_mode"] = None
            st.session_state["pin_start"] = None
            st.session_state["pin_end"] = None
            st.session_state["user_current_pos"] = None
            st.session_state["last_processed_click"] = None
            st.session_state["active_tracking"] = False
            st.rerun()

    map_focus = JORDAN_CENTER
    if selected_route:
        mid_point = len(selected_route["coordinates"]) // 2
        map_focus = selected_route["coordinates"][mid_point]
    elif st.session_state["user_current_pos"]:
        map_focus = st.session_state["user_current_pos"]

    m = folium.Map(
        location=map_focus,
        zoom_start=11 if selected_route else 8,
        min_lat=JORDAN_BOUNDS[0][0], max_lat=JORDAN_BOUNDS[1][0],
        min_lon=JORDAN_BOUNDS[0][1], max_lon=JORDAN_BOUNDS[1][1],
        max_bounds=True
    )
    LocateControl(auto_start=False, flyTo=True).add_to(m)

    if selected_route:
        folium.PolyLine(selected_route["coordinates"], color="#0055ff", weight=6, opacity=0.85).add_to(m)
        folium.Marker(selected_route["coordinates"][0], popup=f"الانطلاق: {selected_route['start']}", icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)
        folium.Marker(selected_route["coordinates"][-1], popup=f"الوجهة: {selected_route['end']}", icon=folium.Icon(color="purple", icon="graduation-cap", prefix="fa")).add_to(m)
    else:
        for r in routes:
            folium.PolyLine(r["coordinates"], color="#6c757d", weight=4, opacity=0.6, tooltip=r["name"]).add_to(m)

    if st.session_state["user_current_pos"]:
        folium.Marker(
            st.session_state["user_current_pos"],
            popup="🚍 موقع الباص / موقعك الآن",
            icon=folium.Icon(color="red" if st.session_state["active_tracking"] else "blue", icon="bus" if st.session_state["active_tracking"] else "user", prefix="fa")
        ).add_to(m)

    if st.session_state["pin_start"]:
        folium.Marker(st.session_state["pin_start"], popup="نقطة انطلاق مقترحة", icon=folium.Icon(color="lightgreen", icon="map-pin", prefix="fa")).add_to(m)
    if st.session_state["pin_end"]:
        folium.Marker(st.session_state["pin_end"], popup="نقطة وصول مقترحة", icon=folium.Icon(color="darkpurple", icon="flag", prefix="fa")).add_to(m)

    map_out = st_folium(m, height=450, use_container_width=True)

    if map_out and st.session_state["pin_mode"] and isinstance(map_out.get("last_clicked"), dict):
        clk = map_out["last_clicked"]
        if clk.get("lat") and clk.get("lng"):
            pt = [float(clk["lat"]), float(clk["lng"])]
            if is_valid_coord(pt) and pt != st.session_state["last_processed_click"]:
                st.session_state["last_processed_click"] = pt
                if st.session_state["pin_mode"] == "my_pos":
                    st.session_state["user_current_pos"] = pt
                elif st.session_state["pin_mode"] == "start":
                    st.session_state["pin_start"] = pt
                elif st.session_state["pin_mode"] == "end":
                    st.session_state["pin_end"] = pt
                st.session_state["pin_mode"] = None
                st.rerun()

    # لوحة العداد الحي للرحلة
    if selected_route and st.session_state["user_current_pos"]:
        stats = calculate_route_progress(
            st.session_state["user_current_pos"][0],
            st.session_state["user_current_pos"][1],
            selected_route["coordinates"]
        )
        if stats:
            st.markdown("---")
            st.markdown("### ⏱️ لوحة التتبع الحي للرحلة")
            
            if stats["remaining_km"] <= 0.2:
                st.balloons()
                st.success("🎉 وصلت بحمد الله إلى وجهتك النهائية!")
            elif stats["remaining_minutes"] <= 5:
                st.warning(f"🔔 اقترب الوصول! باقي **{stats['remaining_minutes']} دقائق فقط**.")

            st.progress(stats["progress_pct"] / 100.0)

            c_met1, c_met2, c_met3, c_met4 = st.columns(4)
            c_met1.metric("المتبقي للوصول", f"{stats['remaining_km']} كم", delta=f"-{stats['covered_km']} كم", delta_color="inverse")
            c_met2.metric("الوقت المتوقع المتبقي", f"{stats['remaining_minutes']} دقيقة")
            c_met3.metric("المسافة المقطوعة", f"{stats['covered_km']} كم")
            c_met4.metric("نسبة إنجاز المسار", f"{stats['progress_pct']}%")

            if stats["off_route_km"] > 0.8:
                st.warning(f"⚠️ تنبيه: موقعك يبعد عن خط السير الرسمي بمقدار {stats['off_route_km']} كم.")

    # نموذج اقتراح خط جديد
    st.markdown("---")
    st.subheader("➕ اقتراح خط باص جديد للاعتماد")
    with st.form("suggest_form"):
        r_name = st.text_input("اسم الخط المقترح:", placeholder="مثال: صويلح - الجامعة الألمانية الأردنية")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            r_start = st.text_input("نقطة الانطلاق (أو حدد بالدبوس على الخريطة):", value="موقع محدد بالدبوس" if st.session_state["pin_start"] else "")
        with col_s2:
            r_end = st.text_input("الجامعة المستهدفة:", value="موقع الجامعة بالدبوس" if st.session_state["pin_end"] else "")
        r_fare = st.number_input("الأجرة المقترحة (د.أ):", 0.1, 15.0, 0.65, 0.05)
        r_time = st.text_input("المدة الزمنية المعتادة:", value="35 دقيقة")

        if st.form_submit_button("إرسال الاقتراح للمراجعة"):
            c_s = st.session_state["pin_start"] or get_coords_from_name(r_start)
            c_e = st.session_state["pin_end"] or get_coords_from_name(r_end)
            if r_name.strip() and c_s and c_e:
                geom = fetch_street_geometry(c_s, c_e)
                with get_db_connection() as conn:
                    conn.cursor().execute("""
                        INSERT INTO routes (id, name, start_point, end_point, fare, duration, status, coordinates)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (uuid.uuid4().hex, r_name.strip(), r_start.strip(), r_end.strip(), round(float(r_fare), 2), r_time.strip(), json.dumps(geom)))
                    conn.commit()
                st.session_state["pin_start"] = None
                st.session_state["pin_end"] = None
                st.success("✅ تم إرسال الخط بنجاح إلى فريق Admin للمراجعة والاعتماد.")
            else:
                st.error("⚠️ يرجى التأكد من كتابة اسم الخط وتحديد المواقع داخل المملكة بدقة.")

# --- 7. لوحة Admin Panel ---
elif mode == "🔐 Admin Panel":
    st.header("⚙️ Admin Panel — نظام إدارة المسارات")

    # فحص الحظر الأمني
    with get_db_connection() as conn:
        sec = conn.cursor().execute("SELECT failed_attempts, lockout_until FROM admin_security WHERE id = 1").fetchone()

    now = time.time()
    if now < sec["lockout_until"]:
        st.error(f"⛔ تم حظر تسجيل الدخول مؤقتاً بسبب تكرار المحاولات الخاطئة. انتظر {int(sec['lockout_until'] - now)} ثانية.")
        st.stop()

    if not st.session_state["auth_role"]:
        tab_login1, tab_login2 = st.tabs(["👑 Super Admin", "👔 Sub-Admin"])

        with tab_login1:
            with st.form("super_login_form"):
                sup_pwd = st.text_input("Password (Super Admin):", type="password")
                if st.form_submit_button("Login as Super Admin"):
                    if hmac.compare_digest(sup_pwd, SUPER_ADMIN_PASSWORD):
                        with get_db_connection() as conn:
                            conn.cursor().execute("UPDATE admin_security SET failed_attempts = 0, lockout_until = 0 WHERE id = 1")
                            conn.commit()
                        st.session_state["auth_role"] = "super_admin"
                        st.session_state["auth_user"] = "Super Admin"
                        st.rerun()
                    else:
                        with get_db_connection() as conn:
                            fails = sec["failed_attempts"] + 1
                            lock = (now + 180.0) if fails >= 3 else 0.0
                            conn.cursor().execute("UPDATE admin_security SET failed_attempts = ?, lockout_until = ? WHERE id = 1", (fails, lock))
                            conn.commit()
                        st.error("❌ Invalid Password.")

        with tab_login2:
            with st.form("sub_login_form"):
                sub_user = st.text_input("Username:")
                sub_pwd = st.text_input("Password:", type="password")
                if st.form_submit_button("Login as Sub-Admin"):
                    with get_db_connection() as conn:
                        assistant = conn.cursor().execute(
                            "SELECT * FROM sub_admins WHERE username = ? AND is_active = 1",
                            (sub_user.strip(),)
                        ).fetchone()
                    if assistant and verify_pw_secure(sub_pwd, assistant["password_hash"]):
                        st.session_state["auth_role"] = "sub_admin"
                        st.session_state["auth_user"] = assistant["full_name"]
                        st.rerun()
                    else:
                        st.error("❌ Invalid credentials or account deactivated.")
    else:
        st.sidebar.markdown(f"Current User: **{st.session_state['auth_user']}**")
        st.sidebar.caption(f"Role: {'Super Admin 👑' if st.session_state['auth_role'] == 'super_admin' else 'Sub-Admin 👔'}")
        if st.sidebar.button("🚪 Logout"):
            st.session_state["auth_role"] = None
            st.session_state["auth_user"] = None
            st.rerun()

        tabs_list = ["⏳ Pending Routes", "✅ Active Routes", "➕ Add Route"]
        if st.session_state["auth_role"] == "super_admin":
            tabs_list.append("👥 Sub-Admins Management")

        active_tabs = st.tabs(tabs_list)

        # 1. Pending Routes
        with active_tabs[0]:
            with get_db_connection() as conn:
                pending = conn.cursor().execute("SELECT * FROM routes WHERE status = 'pending'").fetchall()
            if not pending:
                st.info("🎉 No pending route requests.")
            else:
                for p in pending:
                    with st.expander(f"🚍 {p['name']} | السعر: {p['fare']:.2f} د.أ"):
                        st.write(f"**من:** {p['start_point']} ⬅️ **إلى:** {p['end_point']}")
                        st.write(f"**المدة التقديرية:** {p['duration']}")
                        c_a, c_b = st.columns(2)
                        with c_a:
                            if st.button("✅ Approve & Publish", key=f"app_{p['id']}", type="primary"):
                                with get_db_connection() as conn:
                                    conn.cursor().execute("UPDATE routes SET status = 'active' WHERE id = ?", (p["id"],))
                                    conn.commit()
                                st.success("Route activated successfully.")
                                st.rerun()
                        with c_b:
                            if st.button("🗑️ Reject & Delete", key=f"del_{p['id']}"):
                                with get_db_connection() as conn:
                                    conn.cursor().execute("DELETE FROM routes WHERE id = ?", (p["id"],))
                                    conn.commit()
                                st.warning("Route deleted.")
                                st.rerun()

        # 2. Active Routes
        with active_tabs[1]:
            with get_db_connection() as conn:
                actives = conn.cursor().execute("SELECT * FROM routes WHERE status = 'active'").fetchall()
            if not actives:
                st.info("No active routes found.")
            else:
                for r in actives:
                    c1, c2, c3 = st.columns([4, 1, 1])
                    with c1:
                        st.write(f"**{r['name']}** — من {r['start_point']} إلى {r['end_point']} ({r['fare']:.2f} د.أ)")
                    with c2:
                        if st.button("⏸️ Suspend", key=f"sus_{r['id']}"):
                            with get_db_connection() as conn:
                                conn.cursor().execute("UPDATE routes SET status = 'pending' WHERE id = ?", (r["id"],))
                                conn.commit()
                            st.rerun()
                    with c3:
                        if st.session_state["auth_role"] == "super_admin":
                            if st.button("❌ Delete", key=f"rm_{r['id']}"):
                                with get_db_connection() as conn:
                                    conn.cursor().execute("DELETE FROM routes WHERE id = ?", (r["id"],))
                                    conn.commit()
                                st.rerun()

        # 3. Add Route
        with active_tabs[2]:
            with st.form("direct_add_form"):
                name = st.text_input("اسم الخط الجديد:")
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    start_p = st.text_input("نقطة الانطلاق:")
                with col_d2:
                    end_p = st.text_input("الجامعة / نقطة الوصول:")
                fare = st.number_input("الأجرة (د.أ):", 0.1, 15.0, 0.5)
                dur = st.text_input("الوقت التقديري:", value="25 دقيقة")
                if st.form_submit_button("Publish Immediately"):
                    p1 = get_coords_from_name(start_p)
                    p2 = get_coords_from_name(end_p)
                    if name.strip() and p1 and p2:
                        geom = fetch_street_geometry(p1, p2)
                        with get_db_connection() as conn:
                            conn.cursor().execute("""
                                INSERT INTO routes (id, name, start_point, end_point, fare, duration, status, coordinates)
                                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                            """, (uuid.uuid4().hex, name.strip(), start_p.strip(), end_p.strip(), round(float(fare), 2), dur.strip(), json.dumps(geom)))
                            conn.commit()
                        st.success("✅ Published successfully!")
                        st.rerun()
                    else:
                        st.error("❌ Could not resolve locations within Jordan.")

        # 4. Sub-Admins Management (Super Admin Only)
        if st.session_state["auth_role"] == "super_admin":
            with active_tabs[3]:
                st.subheader("👥 Sub-Admins Management")
                with st.form("new_subadmin_form"):
                    st.markdown("**Create New Sub-Admin Account:**")
                    col_u1, col_u2, col_u3 = st.columns(3)
                    with col_u1:
                        new_name = st.text_input("Full Name:")
                    with col_u2:
                        new_user = st.text_input("Username:")
                    with col_u3:
                        new_pwd = st.text_input("Temporary Password:", type="password")

                    if st.form_submit_button("➕ Create Account"):
                        if new_name.strip() and new_user.strip() and new_pwd.strip():
                            try:
                                with get_db_connection() as conn:
                                    conn.cursor().execute("""
                                        INSERT INTO sub_admins (id, username, password_hash, full_name, is_active, created_at)
                                        VALUES (?, ?, ?, ?, 1, ?)
                                    """, (uuid.uuid4().hex, new_user.strip(), hash_pw_secure(new_pwd), new_name.strip(), time.time()))
                                    conn.commit()
                                st.success(f"Sub-Admin account '{new_name}' created successfully!")
                                st.rerun()
                            except sqlite3.IntegrityError:
                                st.error("❌ Username already exists.")
                        else:
                            st.error("⚠️ All fields are required.")

                st.markdown("---")
                st.markdown("**Registered Sub-Admins:**")
                with get_db_connection() as conn:
                    assistants = conn.cursor().execute("SELECT * FROM sub_admins ORDER BY created_at DESC").fetchall()

                if not assistants:
                    st.info("No sub-admins registered yet.")
                else:
                    for a in assistants:
                        col_as1, col_as2, col_as3 = st.columns([3, 1, 1])
                        with col_as1:
                            status_tag = "🟢 Active" if a["is_active"] == 1 else "🔴 Inactive"
                            st.write(f"**{a['full_name']}** (`{a['username']}`) — Status: {status_tag}")
                        with col_as2:
                            toggle_txt = "Deactivate" if a["is_active"] == 1 else "Activate"
                            new_st = 0 if a["is_active"] == 1 else 1
                            if st.button(toggle_txt, key=f"tog_{a['id']}"):
                                with get_db_connection() as conn:
                                    conn.cursor().execute("UPDATE sub_admins SET is_active = ? WHERE id = ?", (new_st, a["id"]))
                                    conn.commit()
                                st.rerun()
                        with col_as3:
                            if st.button("Delete", key=f"del_a_{a['id']}"):
                                with get_db_connection() as conn:
                                    conn.cursor().execute("DELETE FROM sub_admins WHERE id = ?", (a["id"],))
                                    conn.commit()
                                st.rerun()

def get_connection():
    db_url = st.secrets["TURSO_DB_URL"]
    auth_token = st.secrets["TURSO_AUTH_TOKEN"]
    # تحويل الرابط إلى https للاتصال المباشر السلس
    http_url = db_url.replace("libsql://", "https://")
    client = libsql_client.create_client_sync(url=http_url, auth_token=auth_token)
    return client