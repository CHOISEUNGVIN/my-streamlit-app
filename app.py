import os
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytz
import requests
import streamlit as st
from dotenv import load_dotenv
from ics import Calendar

# -----------------------------
# Setup
# -----------------------------
load_dotenv()
SEOUL_TZ = pytz.timezone("Asia/Seoul")

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Seoul,KR")


# -----------------------------
# Models
# -----------------------------
@dataclass
class Weather:
    city: str
    temp_c: float
    feels_like_c: float
    humidity: int
    wind_ms: float
    rain_1h_mm: float
    description: str


@dataclass
class EventTPO:
    title: str
    start: dt.datetime
    end: dt.datetime
    tags: List[str]


# -----------------------------
# Weather
# -----------------------------
def fetch_weather(city: str, api_key: str) -> Tuple[bool, Dict]:
    """
    OpenWeather 'Current weather' endpoint.
    Return: (ok, payload) where payload either has Weather fields or 'error'
    """
    if not api_key:
        return False, {"error": "OPENWEATHER_API_KEY가 없습니다. 사이드바에서 수동 날씨 모드를 사용하세요."}

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": city, "appid": api_key, "units": "metric", "lang": "kr"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        temp_c = float(data["main"]["temp"])
        feels = float(data["main"]["feels_like"])
        humidity = int(data["main"]["humidity"])
        wind = float(data.get("wind", {}).get("speed", 0.0))
        desc = (data.get("weather", [{}])[0].get("description") or "정보 없음").strip()

        rain_1h = 0.0
        if isinstance(data.get("rain"), dict):
            rain_1h = float(data["rain"].get("1h", 0.0))

        w = Weather(
            city=city,
            temp_c=temp_c,
            feels_like_c=feels,
            humidity=humidity,
            wind_ms=wind,
            rain_1h_mm=rain_1h,
            description=desc,
        )
        return True, {"weather": w}
    except Exception as e:
        return False, {"error": f"날씨 조회 실패: {e}"}


def temp_band(feels_like_c: float) -> str:
    if feels_like_c <= 0:
        return "매우 추움"
    if feels_like_c <= 8:
        return "추움"
    if feels_like_c <= 16:
        return "쌀쌀"
    if feels_like_c <= 23:
        return "적당"
    if feels_like_c <= 29:
        return "더움"
    return "매우 더움"


def is_rainy(weather: Weather) -> bool:
    return (weather.rain_1h_mm > 0.0) or ("비" in weather.description)


# -----------------------------
# Calendar (ICS)
# -----------------------------
def infer_tpo_tags(text: str) -> List[str]:
    """
    일정 제목 기반 간단 TPO 태그 추론(룰 기반).
    필요하면 키워드/태그 확장하면 됨.
    """
    t = (text or "").lower()
    tags: List[str] = []

    # formal/smart
    if any(k in t for k in ["면접", "interview"]):
        tags += ["formal", "smart"]
    if any(k in t for k in ["발표", "presentation", "피칭", "pitch", "회의", "미팅"]):
        tags += ["smart", "formal"]
    if any(k in t for k in ["결혼식", "웨딩", "wedding", "행사", "연회"]):
        tags += ["formal"]

    # social/date
    if any(k in t for k in ["데이트", "date", "소개팅"]):
        tags += ["date", "smart"]

    # outdoor/sport
    if any(k in t for k in ["등산", "hiking", "캠핑", "camp", "야외", "outdoor"]):
        tags += ["outdoor", "casual"]
    if any(k in t for k in ["운동", "gym", "러닝", "run", "필라테스", "요가"]):
        tags += ["sport", "casual"]

    # default
    if not tags:
        tags = ["casual"]

    # dedupe keep order
    return list(dict.fromkeys(tags))


def parse_ics_events(file_bytes: bytes, tz=SEOUL_TZ) -> List[EventTPO]:
    cal = Calendar(file_bytes.decode("utf-8", errors="ignore"))
    now = dt.datetime.now(tz)
    horizon = now + dt.timedelta(days=7)

    events: List[EventTPO] = []
    for e in cal.events:
        title = (e.name or "Untitled").strip()

        start = e.begin.datetime
        end = e.end.datetime if e.end else (start + dt.timedelta(hours=1))

        if start.tzinfo is None:
            start = tz.localize(start)
        else:
            start = start.astimezone(tz)

        if end.tzinfo is None:
            end = tz.localize(end)
        else:
            end = end.astimezone(tz)

        if end < now or start > horizon:
            continue

        tags = infer_tpo_tags(title)
        events.append(EventTPO(title=title, start=start, end=end, tags=tags))

    events.sort(key=lambda x: x.start)
    return events


def pick_relevant_event(events: List[EventTPO], base_date: dt.date, tz=SEOUL_TZ) -> Optional[EventTPO]:
    """
    선택한 날짜(base_date)에 가장 가까운 이벤트 1개 선택:
    - 같은 날짜에 시작하는 이벤트 우선
    - 없으면 가장 가까운 미래 이벤트
    """
    if not events:
        return None

    same_day = [e for e in events if e.start.astimezone(tz).date() == base_date]
    if same_day:
        same_day.sort(key=lambda x: x.start)
        return same_day[0]

    future = [e for e in events if e.start.astimezone(tz).date() >= base_date]
    if future:
        future.sort(key=lambda x: x.start)
        return future[0]

    return events[0]


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="OOTD 화면 (날씨+요구사항+TPO)", page_icon="👕", layout="wide")
st.title("👕 OOTD 추천 (MVP 화면)")
st.caption("날씨 + 사용자 요구사항 + 캘린더(TPO) 기반 UI. (추천 API는 다음 단계에서 연동)")

# Session state init
if "answers" not in st.session_state:
    st.session_state.answers = {}
if "tpo_tags" not in st.session_state:
    st.session_state.tpo_tags = []
if "weather_obj" not in st.session_state:
    st.session_state.weather_obj = None


# -----------------------------
# Sidebar - Inputs
# -----------------------------
with st.sidebar:
    st.header("설정")

    # 날짜 선택(Seoul 기준)
    selected_date = st.date_input("오늘/추천 날짜", value=dt.datetime.now(SEOUL_TZ).date())

    st.subheader("🌦️ 날씨")
    weather_mode = st.radio("날씨 입력 방식", ["자동(OpenWeather)", "수동"], index=0)

    city = st.text_input("도시 (예: Seoul,KR)", value=DEFAULT_CITY)

    manual_temp = st.slider("수동 기온(℃)", -10, 40, 16)
    manual_feels = st.slider("수동 체감(℃)", -10, 40, 15)
    manual_rain = st.selectbox("수동 강수", ["비 없음", "비/눈 가능"], index=0)
    manual_desc = st.text_input("수동 날씨 상태(선택)", value="맑음")

    st.subheader("🙋 사용자 요구사항")
    preferred_style = st.multiselect(
        "선호 스타일 태그",
        ["casual", "formal", "smart", "street", "outdoor", "sport", "date", "minimal"],
        default=["casual"],
    )
    preferred_colors = st.multiselect(
        "선호 컬러 톤",
        ["neutral", "black", "pastel", "vivid"],
        default=["neutral"],
    )
    banned_items = st.text_input("피하고 싶은 키워드(쉼표로 구분)", value="")

    st.subheader("📅 캘린더(TPO) 연동")
    st.caption("캘린더 앱에서 일정 내보내기(.ics) 후 업로드하면 TPO를 자동 추론합니다.")
    ics_file = st.file_uploader("ICS 파일 업로드", type=["ics"])
    manual_tpo_text = st.text_input("직접 TPO 입력(선택, 예: 면접/발표/데이트/등산)", value="")

    # 날씨 확정
    weather: Optional[Weather] = None
    weather_error = None

    if weather_mode.startswith("자동"):
        ok, payload = fetch_weather(city, OPENWEATHER_API_KEY)
        if ok:
            weather = payload["weather"]
        else:
            weather_error = payload["error"]
    else:
        # manual
        rain_1h_mm = 1.0 if manual_rain != "비 없음" else 0.0
        weather = Weather(
            city=city,
            temp_c=float(manual_temp),
            feels_like_c=float(manual_feels),
            humidity=50,
            wind_ms=1.5,
            rain_1h_mm=rain_1h_mm,
            description=(manual_desc.strip() or "정보 없음"),
        )

    # 캘린더 이벤트 파싱
    events: List[EventTPO] = []
    if ics_file is not None:
        try:
            events = parse_ics_events(ics_file.getvalue(), tz=SEOUL_TZ)
        except Exception as e:
            st.error(f"ICS 파싱 실패: {e}")

    # TPO 태그 계산: (캘린더 이벤트 1개 + 수동 TPO)
    tpo_tags: List[str] = []
    chosen_event: Optional[EventTPO] = None
    if events:
        chosen_event = pick_relevant_event(events, selected_date, tz=SEOUL_TZ)
        if chosen_event:
            tpo_tags += chosen_event.tags

    if manual_tpo_text.strip():
        tpo_tags += infer_tpo_tags(manual_tpo_text)

    # 기본값
    if not tpo_tags:
        tpo_tags = ["casual"]

    # dedupe
    tpo_tags = list(dict.fromkeys(tpo_tags))

    # save to session
    st.session_state.weather_obj = weather
    st.session_state.tpo_tags = tpo_tags

    # user prefs summary (store for display)
    user_prefs = {
        "preferred_style": preferred_style,
        "preferred_colors": preferred_colors,
        "banned_items": [x.strip() for x in banned_items.split(",") if x.strip()],
        "selected_date": selected_date,
        "manual_tpo_text": manual_tpo_text.strip(),
        "chosen_event": chosen_event,
    }


# -----------------------------
# Main - Weather & TPO summary
# -----------------------------
left, right = st.columns([1.1, 1.4])

with left:
    st.subheader("🌦️ 날씨 요약")
    if weather_error:
        st.warning(weather_error)
        st.info("자동 모드가 실패하면 사이드바에서 ‘수동’으로 전환해 진행할 수 있어요.")
    if st.session_state.weather_obj:
        w = st.session_state.weather_obj
        st.metric("기온(℃)", f"{w.temp_c:.1f}")
        st.metric("체감(℃)", f"{w.feels_like_c:.1f}")
        st.write(f"- 도시: **{w.city}**")
        st.write(f"- 상태: **{w.description}**")
        st.write(f"- 강수(최근 1시간): **{w.rain_1h_mm:.1f} mm**")
        st.write(f"- 체감 구간: **{temp_band(w.feels_like_c)}**")
        st.write(f"- 우산 추천: **{'네' if is_rainy(w) else '아니오'}**")

with right:
    st.subheader("📅 TPO(캘린더) 요약")
    st.write(f"- 선택 날짜: **{user_prefs['selected_date']}**")
    if user_prefs["chosen_event"]:
        e = user_prefs["chosen_event"]
        st.write(f"- 가까운 일정: **{e.title}**")
        st.write(f"  - 시간: {e.start.strftime('%Y-%m-%d %H:%M')} ~ {e.end.strftime('%H:%M')} (KST)")
        st.write(f"  - 추론 태그: **{', '.join(e.tags)}**")
    else:
        st.write("- 업로드된 ICS 일정이 없거나, 해당 날짜 근처 일정이 없습니다.")

    if user_prefs["manual_tpo_text"]:
        st.write(f"- 직접 입력 TPO: **{user_prefs['manual_tpo_text']}** → 태그 추론 반영")

    st.write(f"- 최종 TPO 태그: **{', '.join(st.session_state.tpo_tags)}**")

st.divider()

# -----------------------------
# Questions (5 radios x 4 options)
# -----------------------------
st.subheader("🧩 오늘의 코디 질문 (5개)")

QUESTIONS = [
    ("Q1. 오늘 주요 상황(TPO)은?", ["출근/등교", "격식(발표/행사/면접)", "데이트/모임", "운동/야외활동"]),
    ("Q2. 선호하는 무드는?", ["미니멀", "캐주얼", "스트릿", "포멀"]),
    ("Q3. 선호 컬러 톤은?", ["뉴트럴", "블랙톤", "파스텔", "비비드"]),
    ("Q4. 체감 온도 성향은?", ["추위 많이 탐", "보통", "더위 많이 탐", "레이어링 좋아함"]),
    ("Q5. 오늘 피하고 싶은 요소는?", ["구김/관리 어려움", "활동성 떨어짐", "통풍/땀 문제", "비/오염 취약"]),
]

# Render radios
for q, options in QUESTIONS:
    st.session_state.answers[q] = st.radio(q, options, index=0, key=q)

st.divider()

# -----------------------------
# Reflect sidebar preferences into main (요구사항 반영 표시)
# -----------------------------
st.subheader("🙋 사용자 요구사항 반영(요약)")

colA, colB = st.columns(2)
with colA:
    st.write(f"- 선호 스타일 태그: **{', '.join(user_prefs['preferred_style']) if user_prefs['preferred_style'] else '없음'}**")
    st.write(f"- 선호 컬러 톤: **{', '.join(user_prefs['preferred_colors']) if user_prefs['preferred_colors'] else '없음'}**")
with colB:
    banned = user_prefs["banned_items"]
    st.write(f"- 금지/회피 키워드: **{', '.join(banned) if banned else '없음'}**")
    st.write(f"- 질문 응답이 추천에 반영될 예정입니다. (다음 시간 API/모델 연동)")

st.divider()

# -----------------------------
# Result button
# -----------------------------
if st.button("결과 보기", type="primary"):
    st.info("분석 중...")

    # (다음 시간에 여기에 추천 API 연동/룰 기반 추천 로직이 들어갈 자리)
    # 지금은 요구사항대로 "분석 중..."만 표시합니다.

# Optional: 디버그용 (원하면 숨기세요)
with st.expander("🔎 현재 입력값(디버그)"):
    st.write("날씨:", st.session_state.weather_obj)
    st.write("TPO 태그:", st.session_state.tpo_tags)
    st.write("사용자 요구사항:", user_prefs)
    st.write("질문 답변:", st.session_state.answers)
