import streamlit as st
from streamlit_folium import st_folium
import folium
import json
import math
import os
import requests
import sqlite3

st.set_page_config(
    page_title="شعتَله",
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

JORDAN_HUBS = {
    "مجمع الشمال (إربد)": (32.5562, 35.8498),
    "مجمع عمان الجديد (إربد)": (32.5315, 35.8540),
    "مجمع الأغوار الجديد (إربد)": (32.5442, 35.8398),
    "جامعة اليرموك - البوابة الشمالية": (32.5370, 35.8530),
    "جامعة اليرموك - البوابة الجنوبية": (32.5290, 35.8550),
    "جامعة العلوم والتكنولوجيا (JUST)": (32.4950, 35.9912),
    "مجمع صويلح (عمان)": (32.0232, 35.8425),
    "مجمع الشمال (عمان - طبربور)": (32.0018, 35.9221),
    "الجامعة الأردنية - البوابة الرئيسية": (32.0155, 35.8700),
    "جامعة البلقاء التطبيقية (السلط)": (32.0350, 35.7275),
    "الجامعة الهاشمية (الزرقاء)": (32.1025, 36.1830),
    "مجمع الأمير راشد (الزرقاء)": (32.0620, 36.0880),
    "جامعة آل البيت (المفرق)": (32.3420, 36.2390),
    "جامعة فيلادلفيا": (32.1765, 35.8450),
    "جامعة جرش الأهلية": (32.2530, 35.8920)
}

UNIVERSITIES = [
    "جامعة اليرموك", 
    "الجامعة الأردنية", 
    "جامعة العلوم والتكنولوجيا", 
    "جامعة البلقاء التطبيقية", 
    "الجامعة الهاشمية", 
    "جامعة آل البيت", 
    "أخرى"
]

def get_db():
    conn = sqlite3.connect("shatala_live.db", check_same_thread=False)
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
            fare REAL DEFAULT 0.50,
            distance_km REAL DEFAULT 0.0,
            duration_min REAL DEFAULT 0.0,
            coordinates TEXT NOT NULL,
            notes TEXT,
            status TEXT DEFAULT 'approved'
        )
    """)
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetchall=False, commit=False):
    conn = get_db()
    c = conn.cursor()
    c.execute(query, params)
    data = None
    if commit:
        conn.commit()
    if fetchall:
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
    """, (route_name, university, float(fare), float(distance_km), float(duration_min), coords_json, notes, status), commit=True)

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
st.sidebar.title("شَعتَله 🚌")
st.sidebar.caption("مسارات باصات الجامعات الأردنية")

app_mode = st.sidebar.radio("التنقل:", ["تتبع ومسارات الباصات", "اقتراح خط جديد", "بوابة الإدارة"])

if app_mode == "تتبع ومسارات الباصات":
    st.title("🗺️ استعراض مسارات ومواعيد الباصات")
    
    routes = get_routes('approved')
    if not routes:
        st.info("لا توجد مسارات مسجلة حالياً. يمكنك اقتراح مسار جديد من القائمة الجانبية.")
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
        m = folium.Map(location=coords[0], zoom_start=12, tiles="OpenStreetMap")
        folium.PolyLine(coords, color="#2A75D3", weight=5, opacity=0.8).add_to(m)
        folium.Marker(coords[0], tooltip="نقطة الانطلاق / المجمع", icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(coords[-1], tooltip=cur_route['university'], icon=folium.Icon(color="red", icon="flag")).add_to(m)
        st_folium(m, width=900, height=450)

elif app_mode == "اقتراح خط جديد":
    st.title("➕ اقتراح مسار باص جديد")
    st.write("حدد محطتي الانطلاق والوصول ليتم تجهيز المسار وحساب المسافة تلقائياً.")
    
    with st.form("suggest_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("اسم الخط (مثال: مجمع الأغوار الجديد - جامعة اليرموك)")
            uni = st.selectbox("الجامعة الوجهة:", UNIVERSITIES)
        with c2:
            suggested_fare = st.number_input("الأجرة المتوقعة (د.أ):", min_value=0.10, value=0.50, step=0.05, format="%.2f")
            notes = st.text_area("أماكن التوقف أو ملاحظات إضافية:")
        
        st.subheader("محطات البداية والنهاية")
        hub_list = list(JORDAN_HUBS.keys())
        cc1, cc2 = st.columns(2)
        with cc1:
            start_hub = st.selectbox("مكان الانطلاق:", hub_list, index=2)
        with cc2:
            end_hub = st.selectbox("مكان الوصول / الجامعة:", hub_list, index=3)
            
        submitted = st.form_submit_button("إرسال المقترح للإدارة")
        if submitted:
            if not name:
                st.warning("يرجى إدخال اسم المسار.")
            elif start_hub == end_hub:
                st.error("نقطة الانطلاق ونقطة الوصول متطابقتان، يرجى اختيار نقطتين مختلفتين.")
            else:
                s_lat, s_lon = JORDAN_HUBS[start_hub]
                e_lat, e_lon = JORDAN_HUBS[end_hub]
                dist, dur, pts = fetch_osrm_route(s_lat, s_lon, e_lat, e_lon)
                add_route(name, uni, suggested_fare, dist, dur, json.dumps(pts), notes, status='pending')
                st.success("✅ تم إرسال المقترح بنجاح للإدارة للتدقيق والاعتماد.")

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
            st.subheader("الطلبات المقترحة بانتظار الاعتماد وتعديل الأسعار")
            pending = get_routes('pending')
            if not pending:
                st.success("لا توجد طلبات معلقة حالياً.")
            else:
                for req in pending:
                    with st.expander(f"طلب: {req['route_name']} - الوجهة: {req['university']}"):
                        st.write(f"المسافة: {req['distance_km']} كم | الزمن المتوقع: {req['duration_min']} دقيقة")
                        st.write(f"ملاحظات: {req.get('notes') or 'لا يوجد'}")
                        
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
                            if st.button("✅ اعتماد الخط فوراً", key=f"acc_{req['id']}"):
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
                a_uni = st.selectbox("الجامعة:", UNIVERSITIES)
                a_fare = st.number_input("السعر المعتمد (د.أ):", min_value=0.10, value=0.60, step=0.05, format="%.2f")
                a_notes = st.text_area("تفاصيل وملاحظات إضافية:")
                
                hub_list = list(JORDAN_HUBS.keys())
                ac1, ac2 = st.columns(2)
                with ac1:
                    a_start = st.selectbox("نقطة الانطلاق المعتمدة:", hub_list, index=2)
                with ac2:
                    a_end = st.selectbox("نقطة الوصول المعتمدة:", hub_list, index=3)
                
                if st.form_submit_button("إضافة الخط فوراً إلى الخدمة"):
                    if a_name and a_start != a_end:
                        al1, on1 = JORDAN_HUBS[a_start]
                        al2, on2 = JORDAN_HUBS[a_end]
                        dist, dur, pts = fetch_osrm_route(al1, on1, al2, on2)
                        add_route(a_name, a_uni, a_fare, dist, dur, json.dumps(pts), a_notes, status='approved')
                        st.success("✅ تمت إضافة المسار بنجاح إلى شبكة الخطوط!")
                        st.rerun()
                    else:
                        st.error("يرجى التأكد من كتابة اسم الخط واختيار محطتين مختلفتين.")
