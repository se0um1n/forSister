"""우리집 한몫 · Streamlit Community Cloud

빠른 배포
1. GitHub 저장소에 app.py와 requirements.txt 업로드.
2. Streamlit Community Cloud에서 저장소 연결, app.py 선택.
3. App settings > Secrets에 아래 TOML 입력 (토큰을 코드에 넣지 마세요).

GITHUB_TOKEN = "github_pat_..."
GITHUB_REPO = "소유자/저장소"
GITHUB_BRANCH = "main"
APP_PIN = "가족끼리만-아는-긴-암호"
APP_URL = "https://실제앱주소.streamlit.app"

Fine-grained GitHub 토큰: 지정한 저장소만 선택, Contents Read and write.
실사용은 별도의 비공개 데이터 저장소를 GITHUB_REPO로 지정하는 것을 권장합니다.
데이터 저장소에는 지정 브랜치와 최소 1개 파일(README 등)이 있어야 합니다.
실행하면 household_data.json이 데이터 저장소에 자동 생성됩니다.
설정 없이는 브라우저 세션 한정 체험 모드이며 가족 간 공유되지 않습니다.
알림은 접속 중인 앱 안에서만 작동합니다. 종료/백그라운드 푸시는 미지원.
모든 시간은 한국 시간입니다. 가족 암호는 공용 접근 제어이며 개인 인증은 아닙니다.
"""
import base64
import copy
import io
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import qrcode
import requests
import streamlit as st

st.set_page_config(page_title="우리집 한몫", page_icon="🏡", layout="centered", initial_sidebar_state="collapsed")
TZ = ZoneInfo("Asia/Seoul")
MEMBERS = ["서수민", "서유민", "서혜민"]
CHORES = ["빨래", "분리수거", "청소기", "설거지", "강아지 산책", "욕실 청소", "정리정돈", "직접 입력"]
ICONS = {"빨래": "🧺", "분리수거": "♻️", "청소기": "🧹", "설거지": "🍽️", "강아지 산책": "🐕", "욕실 청소": "🛁", "정리정돈": "🧸"}
DATA_PATH = "household_data.json"

def secret(key, default=""):
    try:
        return str(st.secrets.get(key, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default

TOKEN = secret("GITHUB_TOKEN")
REPO = secret("GITHUB_REPO")
BRANCH = secret("GITHUB_BRANCH", "main")
SHARED = bool(TOKEN and REPO)
PIN = secret("APP_PIN")

st.markdown("""<style>
.block-container {max-width:560px;padding-top:1.3rem;padding-bottom:2rem;}
[data-testid="stHeader"] {background:transparent;}
h1 {font-size:1.9rem!important;} h2 {font-size:1.3rem!important;}
.stButton button,.stFormSubmitButton button {min-height:46px;border-radius:14px;}
[data-testid="stMetricValue"] {font-size:1.8rem;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:18px;}
[data-testid="stRadio"] label {font-size:.93rem;}
.hero {text-align:center;background:linear-gradient(140deg,#e9f7ef,#fff8df);padding:38px 14px;border-radius:26px;margin:14px 0 22px;}
.hero .logo {font-size:68px;} .hero h1 {color:#185b46;margin:4px 0;}
.hero p {color:#52675e;margin:7px 0;font-size:16px;}
</style>""", unsafe_allow_html=True)

def now():
    return datetime.now(TZ)

def parse(value):
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)

class StoreError(Exception):
    pass

def headers():
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

def endpoint():
    return f"https://api.github.com/repos/{REPO}/contents/{DATA_PATH}"

def remote_read():
    try:
        r = requests.get(endpoint(), headers=headers(), params={"ref": BRANCH}, timeout=12)
        if r.status_code == 404:
            # Repository/branch failures must not be confused with a new data file.
            probe = requests.get(f"https://api.github.com/repos/{REPO}/branches/{BRANCH}", headers=headers(), timeout=12)
            if probe.status_code != 200:
                raise StoreError("저장소·브랜치·토큰 권한을 확인해 주세요.")
            return {"tasks": []}, None
        if r.status_code != 200:
            raise StoreError(f"저장소 연결 실패 ({r.status_code}). 잠시 후 새로고침해 주세요.")
        body = r.json()
        data = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise StoreError("저장 데이터 형식이 올바르지 않습니다.")
        return data, body["sha"]
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise StoreError("저장소 응답을 읽지 못했습니다. 연결과 데이터 파일을 확인해 주세요.") from exc

@st.cache_data(ttl=25, show_spinner=False)
def cached_read(repo, branch):
    return remote_read()[0]

def load_data():
    if SHARED:
        return cached_read(REPO, BRANCH)
    if "demo_data" not in st.session_state:
        st.session_state.demo_data = {"tasks": []}
    return copy.deepcopy(st.session_state.demo_data)

def apply(data, action, task=None, task_id=None):
    tasks = data["tasks"]
    if action == "add":
        if not any(t["id"] == task["id"] for t in tasks):
            tasks.append(task)
    else:
        found = next((t for t in tasks if t["id"] == task_id), None)
        if found is None:
            raise StoreError("이미 삭제된 집안일입니다. 새로고침해 주세요.")
        if action == "complete":
            if not found.get("completed_at"):
                found["completed_at"] = now().isoformat()
        elif action == "reopen":
            found["completed_at"] = None
        elif action == "delete":
            data["tasks"] = [t for t in tasks if t["id"] != task_id]
    return data

def save(action, task=None, task_id=None):
    try:
        if not SHARED:
            st.session_state.demo_data = apply(load_data(), action, task, task_id)
        else:
            for _ in range(3):
                data, sha = remote_read()
                updated = apply(data, action, task, task_id)
                payload = {"message": "Update household chores", "branch": BRANCH,
                           "content": base64.b64encode(json.dumps(updated, ensure_ascii=False, indent=2).encode()).decode()}
                if sha:
                    payload["sha"] = sha
                r = requests.put(endpoint(), headers=headers(), json=payload, timeout=15)
                if r.status_code in (200, 201):
                    cached_read.clear()
                    break
                if r.status_code not in (409, 422):
                    raise StoreError(f"저장하지 못했습니다 ({r.status_code}). 권한을 확인해 주세요.")
            else:
                raise StoreError("동시에 수정 중입니다. 잠시 후 다시 눌러 주세요.")
        st.toast("저장했어요 ✅")
        return True
    except (StoreError, requests.RequestException) as exc:
        st.error(str(exc) if isinstance(exc, StoreError) else "통신 오류입니다. 다시 시도해 주세요.")
        return False

def get_data_or_stop():
    try:
        return load_data()
    except StoreError as exc:
        st.error(str(exc))
        if st.button("다시 연결", use_container_width=True):
            cached_read.clear()
            st.rerun()
        st.stop()

def page_tasks(tasks, key):
    """Three cards per page; no long task lists."""
    size = 3
    pages = max(1, (len(tasks) + size - 1) // size)
    index_key = f"page_{key}"
    index = min(st.session_state.get(index_key, 0), pages - 1)
    st.session_state[index_key] = index
    for t in tasks[index * size:(index + 1) * size]:
        card(t, key)
    if pages > 1:
        a, b, c = st.columns([1, 1, 1])
        if a.button("←", key=f"prev_{key}", disabled=index == 0, use_container_width=True):
            st.session_state[index_key] = index - 1
            st.rerun()
        b.caption(f"{index + 1} / {pages}")
        if c.button("→", key=f"next_{key}", disabled=index + 1 == pages, use_container_width=True):
            st.session_state[index_key] = index + 1
            st.rerun()

def card(t, prefix):
    done = bool(t.get("completed_at"))
    late = not done and parse(t["due_at"]) < now()
    status = "✅ 완료" if done else "🔴 기한 지남" if late else "🟡 미완료"
    with st.container(border=True):
        st.markdown(f"**{ICONS.get(t['title'], '🏠')} {t['title']}** · {status}")
        st.caption(f"{t['owner']} · 시작 {parse(t['start_at']):%m/%d %H:%M} → 마감 {parse(t['due_at']):%m/%d %H:%M}")
        left, right = st.columns([4, 1])
        action = "reopen" if done else "complete"
        if left.button("되돌리기" if done else "완료 ✓", key=f"{prefix}_{action}_{t['id']}", use_container_width=True, type="secondary" if done else "primary"):
            if save(action, task_id=t["id"]):
                st.rerun()
        if right.button("삭제", key=f"{prefix}_del_{t['id']}", use_container_width=True):
            st.session_state.delete_id = t["id"]
            st.rerun()

if "entered" not in st.session_state:
    st.session_state.entered = False
if not st.session_state.entered:
    st.markdown('<div class="hero"><div class="logo">🏡</div><h1>우리집 한몫</h1><p>집안일은 나누고, 여유는 함께.</p></div>', unsafe_allow_html=True)
    with st.form("entry"):
        member = st.selectbox("나는", MEMBERS)
        entered_pin = st.text_input("가족 암호", type="password") if PIN else ""
        if st.form_submit_button("시작하기 →", type="primary", use_container_width=True):
            import hmac
            if PIN and not hmac.compare_digest(entered_pin.encode(), PIN.encode()):
                st.error("가족 암호를 확인해 주세요.")
            elif SHARED and not PIN:
                st.error("공유 모드는 Secrets에 APP_PIN을 설정해 주세요.")
            else:
                st.session_state.member = member
                st.session_state.entered = True
                st.rerun()
    st.caption("서수민 · 서유민 · 서혜민")
    if not SHARED:
        st.info("체험 모드 · 이 화면에서만 임시 저장")
    with st.expander("처음 배포하나요?"):
        st.write("GitHub에 두 파일 업로드 → Streamlit Community Cloud 연결 → Secrets 설정")
        st.code('GITHUB_TOKEN = "발급한 토큰"\nGITHUB_REPO = "소유자/비공개데이터저장소"\nGITHUB_BRANCH = "main"\nAPP_PIN = "가족 공용 암호"\nAPP_URL = "https://실제주소.streamlit.app"', language="toml")
        st.caption("데이터 저장소를 먼저 만들고 README를 추가하세요. 토큰은 해당 저장소 Contents 읽기/쓰기 권한만 부여하세요. 자세한 설명은 app.py 맨 위에 있습니다.")
    st.stop()

member = st.session_state.member
head, refresh = st.columns([5, 1])
head.markdown(f"### 🏡 {member}의 한몫")
if refresh.button("↻", help="최신 목록", use_container_width=True):
    cached_read.clear()
    st.rerun()
if not SHARED:
    st.caption("체험 모드 · 새 세션에서는 초기화됩니다")
nav = st.radio("화면", ["내 할 일", "등록", "가족", "알림"], horizontal=True, label_visibility="collapsed")
data = get_data_or_stop()
tasks = data["tasks"]

# Confirmation prevents accidental deletions on small touch screens.
if st.session_state.get("delete_id"):
    st.warning("이 집안일을 삭제할까요?")
    yes, no = st.columns(2)
    if yes.button("삭제할게요", type="primary", use_container_width=True):
        if save("delete", task_id=st.session_state.delete_id):
            del st.session_state.delete_id
            st.rerun()
    if no.button("취소", use_container_width=True):
        del st.session_state.delete_id
        st.rerun()
    st.stop()

if nav == "내 할 일":
    mode = st.radio("상태", ["미완료", "완료"], horizontal=True, label_visibility="collapsed")
    mine = [t for t in tasks if t["owner"] == member and bool(t.get("completed_at")) == (mode == "완료")]
    mine.sort(key=lambda t: t["due_at"])
    if not mine:
        st.success("남은 집안일이 없어요 🌿" if mode == "미완료" else "아직 완료한 일이 없어요")
    page_tasks(mine, "mine_" + mode)
    st.caption("목록은 ↻로 갱신 · 시간은 한국 기준")

elif nav == "등록":
    # Outside form: selecting '직접 입력' immediately reveals the title field.
    kind = st.selectbox("집안일", CHORES)
    with st.form("new_task", clear_on_submit=False):
        title = st.text_input("할 일 이름", max_chars=30) if kind == "직접 입력" else kind
        owner = st.selectbox("담당", MEMBERS, index=MEMBERS.index(member))
        a, b = st.columns(2)
        start_date = a.date_input("시작 날짜", value=now().date())
        start_time = b.time_input("시작 시간", value=now().replace(second=0, microsecond=0).time().replace(tzinfo=None))
        c, d = st.columns(2)
        due_date = c.date_input("마감 날짜", value=now().date())
        due_time = d.time_input("마감 시간", value=(now() + timedelta(hours=1)).time().replace(second=0, microsecond=0, tzinfo=None))
        submitted = st.form_submit_button("등록 +", type="primary", use_container_width=True)
        if submitted:
            start = datetime.combine(start_date, start_time, tzinfo=TZ)
            due = datetime.combine(due_date, due_time, tzinfo=TZ)
            if not title.strip():
                st.error("이름을 입력해 주세요.")
            elif due <= start:
                st.error("마감은 시작 시간 이후로 정해 주세요.")
            else:
                task = {"id": uuid.uuid4().hex, "title": title.strip(), "owner": owner,
                        "start_at": start.isoformat(), "due_at": due.isoformat(),
                        "created_at": now().isoformat(), "completed_at": None}
                if save("add", task=task):
                    st.success("등록했어요. ‘내 할 일’에서 확인하세요.")

elif nav == "가족":
    week_start = (now() - timedelta(days=now().weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    st.caption(f"이번 주 완료 · {week_start:%m/%d}–{(week_end-timedelta(days=1)):%m/%d}")
    cols = st.columns(3)
    for col, name in zip(cols, MEMBERS):
        count = sum(1 for t in tasks if t["owner"] == name and t.get("completed_at") and week_start <= parse(t["completed_at"]) < week_end)
        col.metric(name, f"{count}개")
    owner = st.selectbox("담당 업무", MEMBERS)
    mode = st.radio("업무 상태", ["미완료", "완료"], horizontal=True)
    selected = sorted([t for t in tasks if t["owner"] == owner and bool(t.get("completed_at")) == (mode == "완료")], key=lambda t: t["due_at"])
    if not selected:
        st.info("등록된 일이 없어요.")
    page_tasks(selected, "family_" + owner + mode)

elif nav == "알림":
    st.caption("내 할 일 알림 · 마감 30분 전 / 시작 / 기한 지남")
    st.info("앱이 열려 있을 때만 30초마다 확인해요. 휴대폰 잠금·앱 종료 중 푸시 알림은 지원하지 않아요.")
    @st.fragment(run_every="30s")
    def notification_list():
        current = now()
        current_tasks = get_data_or_stop()["tasks"]
        alerts = []
        for t in current_tasks:
            if t["owner"] != member or t.get("completed_at"):
                continue
            start, due = parse(t["start_at"]), parse(t["due_at"])
            if current >= due:
                alerts.append(("🔴 미완료 · 기한 지남", t))
            elif current >= due - timedelta(minutes=30):
                alerts.append(("⏰ 곧 마감", t))
            elif current >= start:
                alerts.append(("🔔 시작할 시간", t))
        alerts.sort(key=lambda item: item[1]["due_at"])
        if not alerts:
            st.success("새 알림이 없어요 🌿")
        # Pagination stays inside the fragment; rerun after a page change refreshes only this view.
        pages = max(1, (len(alerts) + 2) // 3)
        p = min(st.session_state.get("alert_page", 0), pages-1)
        for label, t in alerts[p*3:(p+1)*3]:
            with st.container(border=True):
                st.markdown(f"**{label}**")
                st.write(t["title"])
                st.caption(f"마감 {parse(t['due_at']):%m/%d %H:%M}")
        if pages > 1:
            a, b, c = st.columns(3)
            if a.button("←", key="alert_prev", disabled=p == 0):
                st.session_state.alert_page = p-1
                st.rerun(scope="fragment")
            b.caption(f"{p+1} / {pages}")
            if c.button("→", key="alert_next", disabled=p == pages-1):
                st.session_state.alert_page = p+1
                st.rerun(scope="fragment")
    notification_list()

# Persistent in-app notification toasts on all screens, not only the notification page.
@st.fragment(run_every="30s")
def live_alerts():
    current = now()
    current_tasks = get_data_or_stop()["tasks"]
    seen = st.session_state.setdefault("seen_alerts", set())
    fresh = []
    for t in current_tasks:
        if t["owner"] != member or t.get("completed_at"):
            continue
        start, due = parse(t["start_at"]), parse(t["due_at"])
        category = "미완료 · 기한 지남" if current >= due else "곧 마감" if current >= due - timedelta(minutes=30) else "시작할 시간" if current >= start else None
        if category:
            key = (t["id"], category, current.date().isoformat())
            if key not in seen:
                fresh.append((category, t["title"]))
                seen.add(key)
    if fresh:
        category, title = fresh[0]
        st.toast(f"{category}: {title}" + (f" 외 {len(fresh)-1}건" if len(fresh)>1 else ""), icon="🔔")
    st.caption(f"알림 확인 {current:%H:%M} · 접속 중 자동 확인")
live_alerts()

with st.expander("QR · 사용자 변경"):
    url = secret("APP_URL")
    if url.startswith("https://"):
        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        st.image(buffer.getvalue(), width=180)
        st.caption("스마트폰 카메라로 접속 · 암호는 가족끼리만 공유")
        st.download_button("QR 저장", buffer.getvalue(), "family_qr.png", "image/png", use_container_width=True)
    else:
        st.caption("Secrets의 APP_URL에 배포된 HTTPS 주소를 넣으면 QR이 표시됩니다.")
    if st.button("사용자 변경 / 나가기", use_container_width=True):
        st.session_state.clear()
        st.rerun()
