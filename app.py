import os
import json
import math
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytz
import requests
import streamlit as st
from dotenv import load_dotenv
from ics import Calendar

# -------------------------
# Config / Setup
# -------------------------
load_dotenv()
SEOUL_TZ = pytz.timezone("Asia/Seoul")

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Seoul,KR")

WARDROBE_PATH = "wardrobe.json"


# -------------------------
# Data models
# -------------------------
@dataclass
class Weather:
    temp_c: float
    feels_like_c: float
    humidity: int
    wind_ms: float
    rain_1h_mm: float
    condition: str
    pop: float  # probability of precipitation (0~1), if available


@dataclass
class TPO:
    title: str
    start: dt.datetime
    end: dt.datetime
    tags: List[str]  # e.g., ["formal", "presentation", "date", "outdoor"]


# -------------------------
# Utilities
# -------------------------
def c_to_level(temp_c: float) -> str:
    """Rough temperature band."""
    if temp_c <= 0:
        return "freezing"
    if temp_c <= 8:
        return "cold"
    if temp_c <= 16:
        return "cool"
    if temp_c <= 23:
        return "mild"
    if temp_c <= 29:
        return "warm"
    return "hot"


def load_wardrobe(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_get(d: Dict, key: str, default):
    return d.get(key, default) if isinstance(d, dict) else default


# -------------------------
# Weather (OpenWeatherMap)
# -------------------------
def fetch_weather_openweather(city: str, api_key: str) -> Weather:
    """
    Uses OpenWeatherMap Current Weather + (optional) One Call for POP.
    For simplicity, we use current weather endpoint and attempt to infer rain.
    """
    if not api_key:
        raise RuntimeError("OPENWEATHER_API_KEY가 설정되어 있지 않습니다.")

    # Current weather
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": api_key, "units": "metric", "lang": "kr"}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    temp_c = float(data["main"]["temp"])
    feels_like_c = float(data["main"]["feels_like"])
    humidity = int(data["main"]["humidity"])
    wind_ms = float(data["wind"].get("speed", 0.0))
    condition = safe_get(data["weather"][0], "description", "unknown")

    rain_1h = 0.0
    if "rain" in data and isinstance(data["rain"], dict):
        rain_1h = float(data["rain"].get("1h", 0.0))

    # POP(강수확률)은 현재날씨에 없을 수 있어 0으로 둠(확장 지점)
    pop = 0.0

    return Weather(
        temp_c=temp_c,
        feels_like_c=feels_like_c,
        humidity=humidity,
        wind_ms=wind_ms,
        rain_1h_mm=rain_1h,
        condition=condition,
        pop=pop,
    )


# -------------------------
# Calendar (ICS upload)
# -------------------------
def parse_ics(file_bytes: bytes, tz=SEOUL_TZ) -> List[TPO]:
    cal = Calendar(file_bytes.decode("utf-8", errors="ignore"))
    tpos: List[TPO] = []

    now = dt.datetime.now(tz)
    horizon = now + dt.timedelta(days=7)

    for e in cal.events:
        # ics library returns Arrow-like / datetime; normalize
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

        title = (e.name or "Untitled").strip()
        tags = infer_tpo_tags(title)

        tpos.append(TPO(title=title, start=start, end=end, tags=tags))

    # sort by soonest
    tpos.sort(key=lambda x: x.start)
    return tpos


def infer_tpo_tags(title: str) -> List[str]:
    """
    아주 단순한 키워드 룰로 TPO 태그 추정.
    운영에서는 사용자가 직접 태그 편집 가능하게 하는 게 좋음.
    """
    t = title.lower()
    tags = []

    # context
    if any(k in t for k in ["면접", "interview"]):
        tags += ["formal", "smart"]
    if any(k in t for k in ["발표", "presentation", "피칭", "pitch"]):
        tags += ["smart", "formal"]
    if any(k in t for k in ["데이트", "date"]):
        tags += ["date", "smart"]
    if any(k in t for k in ["등산", "hiking", "캠핑", "camp", "야외", "outdoor"]):
        tags += ["outdoor", "casual"]
    if any(k in t for k in ["운동", "gym", "러닝", "run"]):
        tags += ["sport", "casual"]

    # default
    if not tags:
        tags = ["casual"]

    # dedupe
    return list(dict.fromkeys(tags))


# -------------------------
# Recommendation logic
# -------------------------
def score_item(item: Dict, needed_tags: List[str], weather: Weather, preferences: Dict) -> float:
    """
    Simple scoring:
    - tag match
    - warmth vs temperature band
    - rain compatibility
    - avoid banned items/colors
    """
    score = 0.0
    item_tags = item.get("tags", [])
    warmth = float(item.get("warmth", 0))

    # tag match
    for tag in needed_tags:
        if tag in item_tags:
            score += 2.0

    # warmth heuristic: map temp to ideal warmth
    # you can calibrate with data
    ideal = {
        "freezing": 6,
        "cold": 5,
        "cool": 3.5,
        "mild": 2.5,
        "warm": 1.5,
        "hot": 0.5,
    }[c_to_level(weather.feels_like_c)]
    score += max(0.0, 2.5 - abs(warmth - ideal))  # closeness bonus

    # rain
    rainy = (weather.rain_1h_mm > 0.0) or ("비" in weather.condition)
    if rainy:
        if item.get("rain_ok", False):
            score += 1.5
        else:
            score -= 1.5

    # banned keywords
    banned = preferences.get("banned_keywords", [])
    name = str(item.get("name", "")).lower()
    for b in banned:
        if b.lower() in name:
            score -= 5.0

    # preferred style boost
    preferred = preferences.get("preferred_tags", [])
    for p in preferred:
        if p in item_tags:
            score += 1.0

    return score


def pick_best(items: List[Dict], needed_tags: List[str], weather: Weather, preferences: Dict) -> Optional[Dict]:
    if not items:
        return None
    scored = [(score_item(it, needed_tags, weather, preferences), it) for it in items]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def build_outfit(wardrobe: Dict, weather: Weather, tpo_tags: List[str], preferences: Dict) -> Dict[str, Optional[Dict]]:
    """
    Create one outfit suggestion from wardrobe categories.
    """
    needed_tags = list(dict.fromkeys(tpo_tags + preferences.get("required_tags", [])))

    outfit = {
        "top": pick_best(wardrobe.get("tops", []), needed_tags, weather, preferences),
        "bottom": pick_best(wardrobe.get("bottoms", []), needed_tags, weather, preferences),
        "outer": None,
        "shoes": pick_best(wardrobe.get("shoes", []), needed_tags, weather, preferences),
        "extras": [],
    }

    # Decide outer by temperature/rain
    need_outer = weather.feels_like_c <= 16 or (weather.rain_1h_mm > 0.0) or ("비" in weather.condition)
    if need_outer:
        outfit["outer"] = pick_best(wardrobe.get("outer", []), needed_tags, weather, preferences)

    # Extras
    extras = wardrobe.get("extras", [])
    if (weather.rain_1h_mm > 0.0) or ("비" in weather.condition):
        outfit["extras"].append(find_by_tag(extras, "rain"))
    if weather.feels_like_c <= 8:
        outfit["extras"].append(find_by_tag(extras, "cold"))

    outfit["extras"] = [x for x in outfit["extras"] if x is not None]
    return outfit


def find_by_tag(items: List[Dict], tag: str) -> Optional[Dict]:
    for it in items:
        if tag in it.get("tags", []):
            return it
    return None


# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="OOTD 추천 앱 (날씨+요구사항+캘린더 TPO)", layout="wide")

st.title("🧥 OOTD 추천 앱 (날씨 + 사용자 요구사항 + 캘린더 TPO)")
st.caption("규칙 기반 MVP 예시: 오늘/이번 일정에 맞춰 옷장 기반 코디를 추천합니다. (Streamlit)")

wardrobe = load_wardrobe(WARDROBE_PATH)

with st.sidebar:
    st.header("설정")

    city = st.text_input("도시 (OpenWeather 형식)", value=DEFAULT_CITY)
    tz_name = st.selectbox("타임존", ["Asia/Seoul", "UTC"], index=0)
    tz = pytz.timezone(tz_name)

    st.subheader("사용자 요구사항")
    preferred_tags = st.multiselect(
        "선호 스타일 태그",
        options=["casual", "formal", "smart", "street", "outdoor", "sport", "date", "neutral", "dark"],
        default=["casual"],
    )
    required_tags = st.multiselect(
        "꼭 반영할 태그(드레스코드 등)",
        options=["casual", "formal", "smart", "street", "outdoor", "sport", "date"],
        default=[],
    )
    banned_keywords = st.text_input("피하고 싶은 키워드(쉼표로 구분)", value="")

    st.subheader("캘린더(TPO)")
    ics_file = st.file_uploader("ICS 파일 업로드(선택)", type=["ics"])
    manual_tpo = st.text_input("직접 TPO 입력(예: 면접/발표/데이트/등산 등)", value="")

preferences = {
    "preferred_tags": preferred_tags,
    "required_tags": required_tags,
    "banned_keywords": [x.strip() for x in banned_keywords.split(",") if x.strip()],
}

# Weather fetch
weather = None
weather_err = None
try:
    weather = fetch_weather_openweather(city, OPENWEATHER_API_KEY)
except Exception as e:
    weather_err = str(e)

col1, col2 = st.columns([1.1, 1.4])

with col1:
    st.subheader("🌦️ 현재 날씨")
    if weather_err:
        st.error(f"날씨를 가져오지 못했습니다: {weather_err}")
        st.info("팁: .env에 OPENWEATHER_API_KEY를 설정했는지 확인하세요.")
    else:
        st.metric("기온(℃)", f"{weather.temp_c:.1f}", help="OpenWeather 기준 현재 기온")
        st.metric("체감(℃)", f"{weather.feels_like_c:.1f}")
        st.write(f"- 상태: **{weather.condition}**")
        st.write(f"- 습도: **{weather.humidity}%**")
        st.write(f"- 바람: **{weather.wind_ms:.1f} m/s**")
        st.write(f"- 최근 1시간 강수량: **{weather.rain_1h_mm:.1f} mm**")
        st.write(f"- 체감 구간: **{c_to_level(weather.feels_like_c)}**")

# TPO parse
tpos: List[TPO] = []
if ics_file is not None:
    try:
        tpos = parse_ics(ics_file.getvalue(), tz=tz)
    except Exception as e:
        st.sidebar.error(f"ICS 파싱 실패: {e}")

# manual TPO -> tags
manual_tags = infer_tpo_tags(manual_tpo) if manual_tpo.strip() else []
today_tags = manual_tags[:]

# choose next event tags if exists
next_event = tpos[0] if tpos else None
if next_event:
    today_tags = list(dict.fromkeys(today_tags + next_event.tags))

with col2:
    st.subheader("📅 TPO (일정 기반)")
    if next_event:
        st.write(f"가장 가까운 일정: **{next_event.title}**")
        st.write(f"- 시작: {next_event.start.strftime('%Y-%m-%d %H:%M')} ({tz_name})")
        st.write(f"- 태그: {', '.join(next_event.tags)}")
    else:
        st.write("가까운 일정이 없거나(또는 ICS 미업로드), 직접 입력 TPO만 사용 중입니다.")

    if manual_tpo.strip():
        st.write(f"직접 입력: **{manual_tpo}** → 태그: {', '.join(manual_tags)}")

    if tpos:
        with st.expander("이번 주 일정 보기"):
            for e in tpos[:10]:
                st.write(f"- {e.start.strftime('%m/%d %H:%M')} ~ {e.end.strftime('%H:%M')} | {e.title} | {', '.join(e.tags)}")

# Recommend
st.divider()
st.subheader("✨ 오늘의 OOTD 추천")

if not wardrobe:
    st.warning("wardrobe.json을 찾을 수 없습니다. 샘플 wardrobe.json을 같은 폴더에 만들어주세요.")
elif weather_err:
    st.warning("날씨가 없어서 추천이 제한됩니다. 우선 임시로 진행하려면 코드를 수정해 기본값 날씨를 넣어주세요.")
else:
    if not today_tags:
        today_tags = ["casual"]

    outfit = build_outfit(wardrobe, weather, today_tags, preferences)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("### 👕 상의")
        st.write(outfit["top"]["name"] if outfit["top"] else "추천 없음")
    with c2:
        st.markdown("### 👖 하의")
        st.write(outfit["bottom"]["name"] if outfit["bottom"] else "추천 없음")
    with c3:
        st.markdown("### 🧥 아우터")
        st.write(outfit["outer"]["name"] if outfit["outer"] else "필요 없음/추천 없음")
    with c4:
        st.markdown("### 👟 신발")
        st.write(outfit["shoes"]["name"] if outfit["shoes"] else "추천 없음")

    if outfit["extras"]:
        st.markdown("### 🎒 추가 아이템")
        st.write(", ".join([x["name"] for x in outfit["extras"]]))

    with st.expander("추천 근거(요약)"):
        st.write(f"- 반영 TPO 태그: **{', '.join(today_tags)}**")
        st.write(f"- 선호 태그: **{', '.join(preferred_tags) if preferred_tags else '없음'}**")
        if preferences["banned_keywords"]:
            st.write(f"- 금지 키워드: **{', '.join(preferences['banned_keywords'])}**")
        st.write("- 추천은 규칙 기반 MVP이며, 옷장 데이터가 풍부할수록 정확해집니다.")

st.divider()
st.caption("확장 아이디어: ① 일정별 드레스코드 템플릿 ② 사용자 피드백(좋아요/싫어요)로 개인화 ③ LLM으로 문장 추천/코디 설명 생성 ④ 옷장 사진으로 자동 태깅")
