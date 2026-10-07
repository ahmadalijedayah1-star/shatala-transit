import streamlit as st
from streamlit_folium import st_folium
import folium
import json
import math
import os
import requests
import sqlite3

st.set_page_config(
    page_title="منظومة شعتله - مسارات باصات الجامعات",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;800&display=swap');
html, body, [class*="css"] {
    font-family: 'Cairo', sans-serif;
    direction: rtl;
    text-align: right;
}
.stMetric {
    background-color: #f8f9fa;
    border-radius: 8px;
    padding: 10px;
}
</style>
""", unsafe_allow_html=True)

JORDAN_BOUNDS = {
    'min_lat': 29.18,
    'max_lat': 33.38,
    'min_lon': 34.90,
    'max_lon': 39.30
}

def is_within_jordan(lat, lon):
    return (JORDAN_BOUNDS['min_lat'] <= lat <= JORDAN_BOUNDS['max_lat']) and \
           (JORDAN_BOUNDS['min_lon'] <= lon <= JORDAN_BOUNDS['max_lon'])

TURSO_URL = os.environ.get("TURSO_DB_URL") or st.secrets.get("TURSO_DB_URL", None)
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN") or st.secrets.get("TURSO_AUTH_TOKEN", None)

class TursoConnection:
    def __init__(self, url, token):
        self.url = url.replace("libsql://", "https://")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
    def execute(self, stmt, args=None):
        payload = {"statements": [{"q": stmt, "params": args or []}]}
        res = requests.post(f"{self.url}/v2/pipeline", headers=self.headers, json=payload, timeout=5)
        return res.json()

def get_db():
    conn = sqlite3.connect("masar_database.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_name TEXT NOT NULL,
            university TEXT NOT NULL,
            fare REAL NOT NULL,
            distance_km REAL,
            duration_min REAL,
            coordinates TEXT NOT NULL,
            notes TEXT,
            status TEXT DEFAULT 'approved'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            username TEXT PRIMARY KEY,
            role TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetchone=False, fetchall=False, commit=False):
    conn = get_db()
    c = conn.cursor()
    c.execute(query, params)
    data = None
    if commit:
        conn.commit()
    if fetchone:
        row = c.fetchone()
        data = dict(row) if row else None
    elif fetchall:
        rows = c.fetchall()
        data = [dict(r) for r in rows]
    conn.close()
    return data

def get_routes(status='approved'):
    return db_execute("SELECT * FROM routes WHERE status = ?", (status,), fetchall=True) or []

def add_route(route_name, university, fare, distance_km, duration_min, coords_json, notes, status='approved'):
    db_execute("""
        INSERT INTO routes (route_name, university, fare, distance_km, duration_min, coordinates, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (route_name, university, fare, distance_km, duration_min, coords_json, notes, status), commit=True)

def update_route_fare_and_details(route_id, new_fare, new_name=None, new_notes=None):
    if new_name and new_notes is not None:
        db_execute("""
            UPDATE routes 
            SET fare = ?, route_name = ?, notes = ?
            WHERE id = ?
        """, (float(new_fare), str(new_name), str(new_notes), int(route_id)), commit=True)
    else:
        db_execute("UPDATE routes SET fare = ? WHERE id = ?", (float(new_fare), int(route_id)), commit=True)

def set_route_status(route_id, status, fare=None):
    if fare is not None:
        db_execute("UPDATE routes SET status = ?, fare = ? WHERE id = ?", (status, float(fare), int(route_id)), commit=True)
    else:
        db_execute("UPDATE routes SET status = ? WHERE id = ?", (status, int(route_id)), commit=True)

def delete_route(route_id):
    db_execute("DELETE FROM routes WHERE id = ?", (int(route_id),), commit=True)

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c

@st.cache_data(ttl=3600)
def fetch_osrm_route(start_lat, start_lon, end_lat, end_lon):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('routes'):
                r = data['routes'][0]
                dist = round(r['distance'] / 1000.0, 2)
                dur = round(r['duration'] / 60.0, 1)
                coords = [[pt[1], pt[0]] for pt in r['geometry']['coordinates']]
                return dist, dur, coords
    except Exception:
        pass
    dist = round(haversine_km(start_lat, start_lon, end_lat, end_lon), 2)
    dur = round((dist / 40.0) * 60, 1)
    return dist, dur, [[start_lat, start_lon], [end_lat, end_lon]]

st.sidebar.image("https://img.icons8.com/color/96/bus.png", width=70)
st.sidebar.title("منظومة شعتله 🚌")
st.sidebar.caption("شبكة خطوط ونقل الجامعات الأردنية")

app_mode = st.sidebar.radio("التنقل:", ["تتبع ومسارات الباصات", "اقتراح خط جديد", "بوابة الإدارة"])

if app_mode == "تتبع ومسارات الباصات":
    st.title("🗺️ استعراض مسارات ومواعيد الباصات")
    
    routes = get_routes('approved')
    if not routes:
        st.info("لا توجد خطوط معتمدة حالياً. يمكنك اقتراح مسار من القائمة الجانبية.")
    else:
        col_sel, col_stat = st.columns([2, 1])
        with col_sel:
            r_names = {f"{r['route_name']} ({r['university']})": r for r in routes}
            chosen_label = st.selectbox("اختر المسار للاستعراض:", list(r_names.keys()))
            cur_route = r_names[chosen_label]
        
        with col_stat:
            st.metric("الأجرة المعتمدة", f"{cur_route['fare']:.2f} د.أ")
            st.metric("المسافة التقديرية", f"{cur_route['distance_km']} كم")
            st.caption(f"⏱️ زمن الرحلة التقريبي: {cur_route['duration_min']} دقيقة")
            if cur_route.get('notes'):
                st.info(f"ملاحظات: {cur_route['notes']}")
        
        coords = json.loads(cur_route['coordinates'])
        m = folium.Map(location=coords[0], zoom_start=12, tiles="CartoDB positron")
        folium.PolyLine(coords, color="#2A75D3", weight=5, opacity=0.8).add_to(m)
        folium.Marker(coords[0], tooltip="نقطة الانطلاق / المجمع", icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(coords[-1], tooltip=cur_route['university'], icon=folium.Icon(color="red", icon="flag")).add_to(m)
        st_folium(m, width=900, height=450)

elif app_mode == "اقتراح خط جديد":
    st.title("➕ اقتراح مسار باص جديد")
    st.write("أدخل بيانات الخط ونقاط البداية والنهاية ليتم تدقيقها واعتمادها من الإدارة.")
    
    with st.form("suggest_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("اسم الخط (مثال: مجمع الشمال - جامعة العلوم والتكنولوجيا)")
            uni = st.selectbox("الجامعة الوجهة:", [
                "جامعة اليرموك", "الجامعة الأردنية", "جامعة العلوم والتكنولوجيا", 
                "جامعة البلقاء التطبيقية", "الجامعة الهاشمية", "جامعة آل البيت", "أخرى"
            ])
        with c2:
            suggested_fare = st.number_input("الأجرة المتوقعة (د.أ):", min_value=0.10, value=0.65, step=0.05, format="%.2f")
            notes = st.text_area("أماكن التوقف أو ملاحظات:")
        
        st.subheader("إحداثيات نقطتي الانطلاق والوصول")
        cc1, cc2 = st.columns(2)
        with cc1:
            s_lat = st.number_input("خط عرض الانطلاق:", value=32.556, format="%.5f")
            s_lon = st.number_input("خط طول الانطلاق:", value=35.850, format="%.5f")
        with cc2:
            e_lat = st.number_input("خط عرض الوجهة:", value=32.500, format="%.5f")
            e_lon = st.number_input("خط طول الوجهة:", value=35.990, format="%.5f")
            
        submitted = st.form_submit_button("إرسال المقترح للإدارة")
        if submitted:
            if not is_within_jordan(s_lat, s_lon) or not is_within_jordan(e_lat, e_lon):
                st.error("❌ النقاط المدخلة خارج النطاق الجغرافي للمملكة الأردنية الهاشمية.")
            elif not name:
                st.warning("يرجى كتابة اسم الخط.")
            else:
                dist, dur, pts = fetch_osrm_route(s_lat, s_lon, e_lat, e_lon)
                add_route(name, uni, suggested_fare, dist, dur, json.dumps(pts), notes, status='pending')
                st.success("✅ تم إرسال مقترح المسار للإدارة بنجاح للمراجعة والاعتماد.")

elif app_mode == "بوابة الإدارة":
    st.title("🔒 بوابة الإدارة والتحكم")
    
    if "admin_logged_in" not in st.session_state:
        st.session_state.admin_logged_in = False
        st.session_state.admin_role = None

    if not st.session_state.admin_logged_in:
        role_choice = st.radio("رتبة الدخول:", ["مساعد آدمن (Assistant)", "الآدمن الرئيسي (Master Admin)"], horizontal=True)
        pwd = st.text_input("كلمة المرور الإدارية:", type="password")
        
        if st.button("تسجيل الدخول"):
            master_secret = st.secrets.get("ADMIN_PASSWORD", "Desert#94-Galaxy!Amman_82")
            asst_secret = st.secrets.get("ASST_PASSWORD", "Assistant#2026")
            
            if role_choice == "الآدمن الرئيسي (Master Admin)" and pwd == master_secret:
                st.session_state.admin_logged_in = True
                st.session_state.admin_role = "master"
                st.rerun()
            elif role_choice == "مساعد آدمن (Assistant)" and (pwd == asst_secret or pwd == master_secret):
                st.session_state.admin_logged_in = True
                st.session_state.admin_role = "assistant"
                st.rerun()
            else:
                st.error("كلمة المرور غير صحيحة.")
    else:
        st.sidebar.success(f"مرحباً بك ({'الآدمن الرئيسي' if st.session_state.admin_role == 'master' else 'مساعد'})")
        if st.sidebar.button("تسجيل الخروج"):
            st.session_state.admin_logged_in = False
            st.session_state.admin_role = None
            st.rerun()

        tab_edit, tab_pending, tab_new = st.tabs([
            "🛠️ تعديل أسعار وبيانات المسارات", 
            "⏳ تدقيق الطلبات الجديدة", 
            "➕ إضافة مسار مباشر"
        ])

        with tab_edit:
            st.subheader("تعديل الأجرة وتفاصيل الخطوط العاملة")
            routes = get_routes('approved')
            if routes:
                r_map = {f"#{r['id']} - {r['route_name']} (السعر الحالي: {r['fare']:.2f} د.أ)": r for r in routes}
                sel_lbl = st.selectbox("اختر المسار للتعديل:", list(r_map.keys()))
                sel_r = r_map[sel_lbl]
                
                with st.form(f"edit_form_{sel_r['id']}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        up_name = st.text_input("اسم المسار:", value=sel_r['route_name'])
                        up_fare = st.number_input(
                            "الأجرة المعتمدة (د.أ):", 
                            min_value=0.10, 
                            max_value=20.0, 
                            value=float(sel_r['fare']), 
                            step=0.05, 
                            format="%.2f"
                        )
                    with col2:
                        up_notes = st.text_area("ملاحظات / تردد الخط:", value=sel_r.get('notes') or "")
                    
                    btn_save = st.form_submit_button("💾 حفظ السعر والتعديلات")
                    if btn_save:
                        update_route_fare_and_details(sel_r['id'], up_fare, up_name, up_notes)
                        st.success("✅ تم تحديث بيانات المسار والسعر بنجاح!")
                        st.rerun()
                
                if st.session_state.admin_role == "master":
                    st.divider()
                    if st.button("🗑️ حذف هذا المسار نهائياً (صلاحية المشرف الرئيسي فقط)", key=f"del_{sel_r['id']}"):
                        delete_route(sel_r['id'])
                        st.warning("تم حذف المسار.")
                        st.rerun()
            else:
                st.info("لا توجد خطوط معتمدة حالياً.")

        with tab_pending:
            st.subheader("الطلبات المقترحة من الطلاب بانتظار الاعتماد")
            pending = get_routes('pending')
            if not pending:
                st.success("لا توجد طلبات معلقة حالياً.")
            else:
                for req in pending:
                    with st.expander(f"طلب: {req['route_name']} - الوجهة: {req['university']}"):
                        st.write(f"المسافة المحسوبة: {req['distance_km']} كم | الزمن المقدر: {req['duration_min']} دقيقة")
                        st.write(f"ملاحظات الطالب: {req.get('notes') or 'لا يوجد'}")
                        
                        app_fare = st.number_input(
                            f"تحديد السعر المعتمد النهائي (د.أ) لطلب #{req['id']}:",
                            min_value=0.10,
                            value=float(req['fare']),
                            step=0.05,
                            format="%.2f",
                            key=f"p_fare_{req['id']}"
                        )
                        
                        col_a, col_r = st.columns(2)
                        with col_a:
                            if st.button("✅ اعتماد الخط وإطلاقه للخدمة", key=f"acc_{req['id']}"):
                                set_route_status(req['id'], 'approved', app_fare)
                                st.success("تم اعتماد الخط بالسعر المحدد!")
                                st.rerun()
                        with col_r:
                            if st.button("❌ رفض وحذف الطلب", key=f"rej_{req['id']}"):
                                delete_route(req['id'])
                                st.warning("تم رفض المقترح.")
                                st.rerun()

        with tab_new:
            st.subheader("إضافة مسار معتمد مباشرة")
            with st.form("admin_add_route"):
                a_name = st.text_input("اسم الخط:")
                a_uni = st.selectbox("الجامعة:", [
                    "جامعة اليرموك", "الجامعة الأردنية", "جامعة العلوم والتكنولوجيا", 
                    "جامعة البلقاء التطبيقية", "الجامعة الهاشمية", "جامعة آل البيت", "أخرى"
                ])
                a_fare = st.number_input("السعر المعتمد (د.أ):", min_value=0.10, value=0.60, step=0.05, format="%.2f")
                a_notes = st.text_area("تفاصيل وملاحظات إضافية:")
                
                ac1, ac2 = st.columns(2)
                with ac1:
                    al1 = st.number_input("عرض البداية:", value=32.556, format="%.5f")
                    on1 = st.number_input("طول البداية:", value=35.850, format="%.5f")
                with ac2:
                    al2 = st.number_input("عرض النهاية:", value=32.500, format="%.5f")
                    on2 = st.number_input("طول النهاية:", value=35.990, format="%.5f")
                
                if st.form_submit_button("إضافة الخط فوراً إلى الخدمة"):
                    if is_within_jordan(al1, on1) and is_within_jordan(al2, on2) and a_name:
                        dist, dur, pts = fetch_osrm_route(al1, on1, al2, on2)
                        add_route(a_name, a_uni, a_fare, dist, dur, json.dumps(pts), a_notes, status='approved')
                        st.success("✅ تمت إضافة المسار بنجاح إلى شبكة الخطوط!")
                        st.rerun()
                    else:
                        st.error("تأكد من إدخال اسم الخط وأن الإحداثيات تقع داخل الأردن.")
