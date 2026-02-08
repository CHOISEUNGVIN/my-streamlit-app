import os
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import streamlit as st

# openai는 설치만(다음 시간 연동용). 지금 코드에서는 사용하지 않음.
from openai import OpenAI  # noqa: F401


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
    rain: bool
    description: str


# -----------------------------
# Weather helpers (no external deps)
# -----------------------------
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


def infer_tpo_tags(text: str) -> List[str]:
    """
    일정/텍스트 기반 간단 TPO 태그 추론(룰 기반).
    """
    t = (text or "").lower()
    tags: List[str] = []

    if any(k in t for k in ["면접", "interview"]):
        tags += ["formal", "smart"]
    if any(k in t for k in ["발표", "presentation", "피칭", "pitch", "회의", "미팅"]):
        tags += ["smart", "formal"]
    if any(k in t for k in ["결혼식", "웨딩", "wedding", "연회", "행사"]):
        tags += ["formal"]
    if any(k in t for k in ["데이트", "date", "소개팅", "모임"]):
        tags += ["date", "smart"]
    if any(k in t for k in ["등산", "hiking", "캠핑", "camp", "야외", "outdoor"]):
        tags += ["outdoor", "casual"]
    if any(k in t for k in ["운동", "gym", "러닝", "run", "필라테스", "요가"]):
        tags += ["sport", "casual"]

    if not tags:
        tags = ["casual"]

    # dedupe
    return list(dict.fromkeys(tags))


def get_env_default_city() -> str:
    return os.getenv("DEFAULT_CITY", "Seoul,KR")


def get_env_openweather_key() -> str:
    return os.getenv("OPENWEATHER_API_KEY", "")


# -----------------------------
# Optional: OpenWeather fetch (only if key exists)
# - We will NOT require requests package.
# - Use urllib from stdlib.
# -----------------------------
def fetch_openweather(city: str, api_key: str) -> Tuple[bool, Dict]:
    """
    OpenWeather Current Weather API.
    Uses stdlib urllib only. If fails, return ok=False.
    """
    try:
        import json
        import urllib.parse
        import urllib.request

        if not api_key:
            return False, {"error": "OPENWEATHER_API_KEY가 없습니다. 수동 입력으로 진행합니다."}

        base = "https://api.openweathermap.org/data/2.5/weather"
        qs = urllib.parse.urlencode(
            {"q": city, "appid": api_key, "units": "metric", "lang": "kr"}
        )
        url = f"{base}?{qs}"

        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)

        temp_c = float(data["main"]["temp"])
        feels = float(data["main"]["feels_like"])
        humidity = int(data["main"]["humidity"])
        wind = float(data.get("wind", {}).get("speed", 0.0))
        desc = (data.get("weather", [{}])[0].get("description") or "정보 없음").strip()

        # rain 여부만 간단 판단
        rain = False
        if isinstance(data.get("rain"), dict):
            rain = float(data["rain"].get("1h", 0.0)) > 0.0
        if "비" in desc or "눈" in desc:
            rain = True

        w = Weather(
            city=city,
            temp_c=temp_c,
            feels_like_c=feels,
            humidity=humidity,
            wind_ms=wind,
            rain=rain,
            description=desc,
        )
        return True, {"weather": w}
    except Exception as e:
        return False, {"error": f"날씨 자동 조회 실패: {e}"}


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="OOTD (날씨+요구사항+TPO) MVP", page_icon="👕", layout="wide")
st.title("👕 OOTD 추천 화면 (MVP)")
st.caption("패키지: streamlit + openai만 설치한 상태에서도 동작하는 UI 버전 (API 없으면 수동 입력).")

# Session init
if "answers" not in st.session_state:
    st.session_state.answers = {}
if "weather" not in st.session_state:
    st.session_state.weather = None
if "tpo_tags" not in st.session_state:
    st.session_state.tpo_tags = []


# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.header("설정")

    selected_date = st.date_input("날짜", value=dt.date.today())

    st.subheader("🌦️ 날씨 (API 없으면 수동 입력)")
    city = st.text_input("도시", value=get_env_default_city())

    api_key = get_env_openweather_key().strip()
    auto_available = bool(api_key)

    if auto_available:
        weather_mode = st.radio("날씨 모드", ["자동(OpenWeather)", "수동"], index=0)
    else:
        st.info("OPENWEATHER_API_KEY가 없어 수동 입력 모드로만 동작합니다.")
        weather_mode = "수동"

    # Manual inputs
    manual_temp = st.slider("기온(℃)", -20, 45, 16)
    manual_feels = st.slider("체감(℃)", -20, 45, 15)
    manual_humidity = st.slider("습도(%)", 0, 100, 50)
    manual_wind = st.slider("바람(m/s)", 0.0, 20.0, 1.5, step=0.1)
    manual_rain = st.selectbox("강수", ["없음", "비/눈 가능"], index=0)
    manual_desc = st.text_input("날씨 설명(선택)", value="맑음")

    st.subheader("🙋 사용자 요구사항")
    preferred_style = st.multiselect(
        "선호 스타일",
        ["casual", "formal", "smart", "street", "outdoor", "sport", "date", "minimal"],
        default=["casual"],
    )
    preferred_color = st.radio(
        "선호 컬러 톤",
        ["neutral", "black", "pastel", "vivid"],
        index=0,
        horizontal=True,
    )
    banned_keywords = st.text_input("피하고 싶은 키워드(쉼표로 구분)", value="")

    st.subheader("📅 캘린더(TPO) — 수동 입력 버전")
    st.caption("외부 패키지 없이 구현: 일정 텍스트 입력 → TPO 태그 추론")
    calendar_text = st.text_area(
        "오늘 일정/장소/상황을 적어주세요",
        placeholder="예: 14:00 팀 발표 / 19:00 친구 모임 / 야외 산책",
        height=120,
    )
    manual_tpo = st.text_input("TPO 키워드(선택)", placeholder="예: 면접, 발표, 데이트, 등산, 운동")

    # Build Weather object
    weather_error = None
    weather: Optional[Weather] = None

    if weather_mode.startswith("자동"):
        ok, payload = fetch_openweather(city, api_key)
        if ok:
            weather = payload["weather"]
        else:
            weather_error = payload["error"]
            # fallback to manual
            weather_mode = "수동"

    if weather_mode == "수동":
        weather = Weather(
            city=city,
            temp_c=float(manual_temp),
            feels_like_c=float(manual_feels),
            humidity=int(manual_humidity),
            wind_ms=float(manual_wind),
            rain=(manual_rain != "없음"),
            description=(manual_desc.strip() or "정보 없음"),
        )

    # TPO tags
    tpo_text = (calendar_text or "") + " " + (manual_tpo or "")
    tpo_tags = infer_tpo_tags(tpo_text)

    # Save session
    st.session_state.weather = weather
    st.session_state.tpo_tags = tpo_tags

    user_prefs = {
        "selected_date": selected_date,
        "preferred_style": preferred_style,
        "preferred_color": preferred_color,
        "banned_keywords": [x.strip() for x in banned_keywords.split(",") if x.strip()],
        "calendar_text": calendar_text.strip(),
        "manual_tpo": manual_tpo.strip(),
        "weather_mode": weather_mode,
        "weather_error": weather_error,
    }


# -----------------------------
# Main: Summary panels
# -----------------------------
col1, col2 = st.columns([1.1, 1.4])

with col1:
    st.subheader("🌦️ 날씨 요약")
    if user_prefs["weather_error"]:
        st.warning(user_prefs["weather_error"])
        st.info("자동 조회 실패 시 수동 입력값으로 대체됩니다.")

    w = st.session_state.weather
    if w:
        st.metric("기온(℃)", f"{w.temp_c:.1f}")
        st.metric("체감(℃)", f"{w.feels_like_c:.1f}")
        st.write(f"- 도시: **{w.city}**")
        st.write(f"- 상태: **{w.description}**")
        st.write(f"- 습도: **{w.humidity}%**")
        st.write(f"- 바람: **{w.wind_ms:.1f} m/s**")
        st.write(f"- 강수: **{'있음(우산 추천)' if w.rain else '없음'}**")
        st.write(f"- 체감 구간: **{temp_band(w.feels_like_c)}**")
        st.write(f"- 모드: **{user_prefs['weather_mode']}**")

with col2:
    st.subheader("📅 TPO 요약")
    st.write(f"- 날짜: **{user_prefs['selected_date']}**")
    if user_prefs["calendar_text"]:
        st.write(f"- 일정 텍스트: {user_prefs['calendar_text']}")
    if user_prefs["manual_tpo"]:
        st.write(f"- 추가 TPO 키워드: **{user_prefs['manual_tpo']}**")
    st.write(f"- 추론 태그: **{', '.join(st.session_state.tpo_tags)}**")

st.divider()

# -----------------------------
# Questions: 5 radios, 4 options each
# (이전 버전에서 만든 질문을 그대로 사용)
# -----------------------------
st.subheader("🧩 오늘의 코디 질문 (5개)")

QUESTIONS = [
    ("Q1. 오늘 주요 상황(TPO)은?", ["출근/등교", "격식(발표/행사/면접)", "데이트/모임", "운동/야외활동"]),
    ("Q2. 선호하는 무드는?", ["미니멀", "캐주얼", "스트릿", "포멀"]),
    ("Q3. 선호 컬러 톤은?", ["뉴트럴", "블랙톤", "파스텔", "비비드"]),
    ("Q4. 체감 온도 성향은?", ["추위 많이 탐", "보통", "더위 많이 탐", "레이어링 좋아함"]),
    ("Q5. 오늘 피하고 싶은 요소는?", ["구김/관리 어려움", "활동성 떨어짐", "통풍/땀 문제", "비/오염 취약"]),
]

for q, options in QUESTIONS:
    st.session_state.answers[q] = st.radio(q, options, index=0, key=q)

st.divider()

# -----------------------------
# Reflect sidebar prefs
# -----------------------------
st.subheader("🙋 사용자 요구사항(사이드바 입력) 반영 요약")
a, b = st.columns(2)

with a:
    styles = user_prefs["preferred_style"]
    st.write(f"- 선호 스타일: **{', '.join(styles) if styles else '없음'}**")
    st.write(f"- 선호 컬러 톤: **{user_prefs['preferred_color']}**")

with b:
    banned = user_prefs["banned_keywords"]
    st.write(f"- 피하고 싶은 키워드: **{', '.join(banned) if banned else '없음'}**")
    st.write(f"- TPO 태그: **{', '.join(st.session_state.tpo_tags)}**")

st.divider()

# -----------------------------
# Result button
# -----------------------------
if st.button("결과 보기", type="primary"):
    st.info("분석 중...")

    # 다음 시간에 들어갈 자리:
    # - (OpenAI/추천 API) 호출
    # - 날씨 + 선호 + 금지 + TPO + 질문답 합쳐서 추천 생성
    # 지금은 요구사항대로 "분석 중..."만 표시

# -----------------------------
# Debug (optional)
# -----------------------------
with st.expander("🔎 현재 입력값(디버그)"):
    st.write("Weather:", st.session_state.weather)
    st.write("TPO tags:", st.session_state.tpo_tags)
    st.write("User prefs:", user_prefs)
    st.write("Answers:", st.session_state.answers)
