import datetime as dt
import json
import math
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import streamlit as st

# openai는 설치만(다음 시간 추천/대화 모델 연동용). 지금은 호출하지 않음.
from openai import OpenAI  # noqa: F401


# =========================
# Data models
# =========================
@dataclass
class Weather:
    city: str
    temp_c: float
    feels_c: float
    humidity: int
    wind_ms: float
    rain: bool
    desc: str


@dataclass
class EventTPO:
    title: str
    start: Optional[dt.datetime]
    tags: List[str]


# =========================
# Secrets / env helpers
# =========================
def get_secret(key: str, default: str = "") -> str:
    # Streamlit Cloud Secrets 우선
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return os.getenv(key, default)


def get_default_city() -> str:
    return get_secret("DEFAULT_CITY", "Seoul,KR")


def get_openweather_key() -> str:
    return get_secret("OPENWEATHER_API_KEY", "")


# =========================
# Weather
# =========================
def fetch_openweather(city: str, api_key: str) -> Tuple[bool, Dict]:
    if not api_key:
        return False, {"error": "OPENWEATHER_API_KEY가 없어 수동 날씨 모드로 진행합니다."}

    try:
        base = "https://api.openweathermap.org/data/2.5/weather"
        qs = urllib.parse.urlencode({"q": city, "appid": api_key, "units": "metric", "lang": "kr"})
        url = f"{base}?{qs}"

        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")

        data = json.loads(raw)

        temp_c = float(data["main"]["temp"])
        feels_c = float(data["main"]["feels_like"])
        humidity = int(data["main"]["humidity"])
        wind_ms = float(data.get("wind", {}).get("speed", 0.0))
        desc = (data.get("weather", [{}])[0].get("description") or "정보 없음").strip()

        rain = False
        if isinstance(data.get("rain"), dict):
            rain = float(data["rain"].get("1h", 0.0)) > 0.0
        if "비" in desc or "눈" in desc:
            rain = True

        return True, {
            "weather": Weather(
                city=city,
                temp_c=temp_c,
                feels_c=feels_c,
                humidity=humidity,
                wind_ms=wind_ms,
                rain=rain,
                desc=desc,
            )
        }
    except Exception as e:
        return False, {"error": f"날씨 자동 조회 실패: {e}"}


def temp_band(feels_c: float) -> str:
    if feels_c <= 0:
        return "매우 추움"
    if feels_c <= 8:
        return "추움"
    if feels_c <= 16:
        return "쌀쌀"
    if feels_c <= 23:
        return "적당"
    if feels_c <= 29:
        return "더움"
    return "매우 더움"


# =========================
# Calendar (ICS) - stdlib parsing
# =========================
def fetch_ics_from_url(url: str) -> Tuple[bool, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            return True, resp.read()
    except Exception as e:
        return False, str(e).encode("utf-8", errors="ignore")


def parse_ics_minimal(ics_bytes: bytes, target_date: dt.date) -> List[EventTPO]:
    """
    외부 패키지 없이 돌아가는 '미니' ICS 파서.
    - SUMMARY, DTSTART를 대충 읽어서 target_date 해당 이벤트만 추출
    - 복잡한 recurrence/zone은 완벽 지원 X (MVP)
    """
    text = ics_bytes.decode("utf-8", errors="ignore")
    # 줄바꿈 이어쓰기(ICS folding) 처리: \n + 공백 시작은 이어진 줄
    text = re.sub(r"\r\n[ \t]", "", text)

    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.DOTALL)
    events: List[EventTPO] = []

    for b in blocks:
        # SUMMARY
        m_sum = re.search(r"SUMMARY:(.*)", b)
        title = m_sum.group(1).strip() if m_sum else "Untitled"

        # DTSTART (예: DTSTART:20260208T090000Z / DTSTART:20260208 / DTSTART;TZID=Asia/Seoul:20260208T090000)
        m_dt = re.search(r"DTSTART[^:]*:(\d{8})(T(\d{6}))?(Z)?", b)
        start_dt = None
        if m_dt:
            ymd = m_dt.group(1)
            hms = m_dt.group(3)  # HHMMSS
            if hms:
                hh = int(hms[0:2]); mm = int(hms[2:4]); ss = int(hms[4:6])
                start_dt = dt.datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), hh, mm, ss)
            else:
                start_dt = dt.datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), 9, 0, 0)

            ev_date = start_dt.date()
            if ev_date != target_date:
                continue
        else:
            # DTSTART가 없으면 스킵
            continue

        tags = infer_tpo_tags(title)
        events.append(EventTPO(title=title, start=start_dt, tags=tags))

    # 시간순 정렬
    events.sort(key=lambda x: x.start or dt.datetime.max)
    return events


def infer_tpo_tags(text: str) -> List[str]:
    t = (text or "").lower()
    tags: List[str] = []

    if any(k in t for k in ["면접", "interview"]):
        tags += ["formal", "smart"]
    if any(k in t for k in ["발표", "presentation", "피칭", "pitch", "회의", "미팅", "컨퍼런스", "세미나"]):
        tags += ["formal", "smart"]
    if any(k in t for k in ["결혼식", "웨딩", "wedding", "연회", "행사"]):
        tags += ["formal"]
    if any(k in t for k in ["데이트", "date", "소개팅", "와인", "레스토랑"]):
        tags += ["date", "smart"]
    if any(k in t for k in ["친구", "모임", "파티"]):
        tags += ["smart", "casual"]
    if any(k in t for k in ["등산", "hiking", "캠핑", "camp", "야외", "outdoor", "피크닉"]):
        tags += ["outdoor", "casual"]
    if any(k in t for k in ["운동", "gym", "러닝", "run", "필라테스", "요가"]):
        tags += ["sport", "casual"]

    if not tags:
        tags = ["casual"]

    return list(dict.fromkeys(tags))


# =========================
# Outfit engine (rule-based MVP)
# =========================
WARDROBE = {
    "tops": [
        {"name": "화이트 셔츠", "tags": ["formal", "smart", "neutral"], "warmth": 2},
        {"name": "블랙 니트", "tags": ["smart", "casual", "black"], "warmth": 4},
        {"name": "맨투맨", "tags": ["casual"], "warmth": 3},
        {"name": "후드티", "tags": ["casual", "street"], "warmth": 4},
    ],
    "bottoms": [
        {"name": "슬랙스", "tags": ["formal", "smart"], "warmth": 2},
        {"name": "청바지", "tags": ["casual"], "warmth": 2},
        {"name": "조거팬츠", "tags": ["sport", "casual"], "warmth": 2},
    ],
    "outer": [
        {"name": "트렌치코트", "tags": ["formal", "smart"], "warmth": 3, "rain_ok": True},
        {"name": "자켓(블레이저)", "tags": ["formal", "smart"], "warmth": 3},
        {"name": "패딩", "tags": ["casual"], "warmth": 6, "rain_ok": True},
        {"name": "바람막이", "tags": ["outdoor", "sport", "casual"], "warmth": 2, "rain_ok": True},
    ],
    "shoes": [
        {"name": "로퍼", "tags": ["formal", "smart"], "rain_ok": False},
        {"name": "스니커즈", "tags": ["casual", "street", "sport"], "rain_ok": True},
    ],
    "extras": [
        {"name": "우산", "tags": ["rain"]},
        {"name": "머플러", "tags": ["cold"]},
    ],
}


def ideal_warmth(feels_c: float) -> float:
    band = temp_band(feels_c)
    return {
        "매우 추움": 6,
        "추움": 5,
        "쌀쌀": 3.5,
        "적당": 2.5,
        "더움": 1.5,
        "매우 더움": 0.5,
    }[band]


def score_item(item: Dict, wanted_tags: List[str], prefs: Dict, weather: Weather) -> float:
    score = 0.0
    name = str(item.get("name", "")).lower()
    tags = item.get("tags", [])
    warmth = float(item.get("warmth", 0))

    # tag match
    for t in wanted_tags:
        if t in tags:
            score += 2.0

    # color preference
    color = prefs.get("preferred_color", "neutral")
    if color == "neutral" and "neutral" in tags:
        score += 0.8
    if color == "black" and ("black" in tags or "dark" in tags):
        score += 0.8
    if color == "pastel" and "pastel" in tags:
        score += 0.8
    if color == "vivid" and "vivid" in tags:
        score += 0.8

    # warmth closeness
    ideal = ideal_warmth(weather.feels_c)
    score += max(0.0, 2.5 - abs(warmth - ideal))

    # rain compatibility
    if weather.rain:
        if item.get("rain_ok", False):
            score += 1.0
        else:
            score -= 1.0

    # banned keywords
    for b in prefs.get("banned_keywords", []):
        if b.lower() in name:
            score -= 6.0

    # explicit avoid shoes
    avoid_shoes = prefs.get("avoid_shoes", [])
    for s in avoid_shoes:
        if s.lower() in name:
            score -= 5.0

    return score


def pick_best(items: List[Dict], wanted_tags: List[str], prefs: Dict, weather: Weather) -> Optional[Dict]:
    if not items:
        return None
    ranked = sorted(((score_item(it, wanted_tags, prefs, weather), it) for it in items), key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def build_outfit(weather: Weather, tpo_tags: List[str], prefs: Dict) -> Tuple[Dict, List[str]]:
    """
    Returns: (outfit dict, reasons list)
    """
    # combine tags
    wanted = list(dict.fromkeys(tpo_tags + prefs.get("preferred_style", [])))

    top = pick_best(WARDROBE["tops"], wanted, prefs, weather)
    bottom = pick_best(WARDROBE["bottoms"], wanted, prefs, weather)
    shoes = pick_best(WARDROBE["shoes"], wanted, prefs, weather)

    need_outer = weather.feels_c <= 16 or weather.rain or weather.wind_ms >= 7
    outer = pick_best(WARDROBE["outer"], wanted, prefs, weather) if need_outer else None

    extras = []
    if weather.rain:
        extras.append({"name": "우산"})
    if weather.feels_c <= 8:
        extras.append({"name": "머플러"})

    outfit = {
        "top": top,
        "bottom": bottom,
        "outer": outer,
        "shoes": shoes,
        "extras": extras,
    }

    # reasons
    reasons = []
    reasons.append(f"오늘 체감온도 **{weather.feels_c:.1f}℃({temp_band(weather.feels_c)})**에 맞춰 보온/레이어링을 고려했어요.")
    if weather.rain:
        reasons.append("비/눈 가능성이 있어 **젖어도 괜찮은 선택(우산/레인 대응)**을 우선했어요.")
    if tpo_tags:
        reasons.append(f"캘린더 일정에서 추론한 TPO가 **{', '.join(tpo_tags)}**라서 그 무드에 맞췄어요.")
    if prefs.get("preferred_style"):
        reasons.append(f"사용자 선호 스타일(**{', '.join(prefs['preferred_style'])}**)을 반영했어요.")
    if prefs.get("preferred_color"):
        reasons.append(f"선호 컬러 톤(**{prefs['preferred_color']}**)을 가능한 범위에서 우선했어요.")
    if prefs.get("banned_keywords"):
        reasons.append(f"피하고 싶은 요소(**{', '.join(prefs['banned_keywords'])}**)를 제외하려고 했어요.")

    return outfit, reasons


# =========================
# Chat-based preference updates (simple rules)
# =========================
def apply_chat_update(text: str, prefs: Dict) -> Dict:
    t = (text or "").strip().lower()
    if not t:
        return prefs

    # style nudges
    if any(k in t for k in ["포멀", "격식", "깔끔", "정장"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["formal", "smart"])))
    if any(k in t for k in ["캐주얼", "편하게"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["casual"])))
    if any(k in t for k in ["스트릿"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["street", "casual"])))
    if any(k in t for k in ["운동", "스포츠", "활동적"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["sport", "casual"])))

    # warmth
    if any(k in t for k in ["따뜻", "보온", "추워"]):
        prefs["warmth_bias"] = prefs.get("warmth_bias", 0.0) + 0.5
    if any(k in t for k in ["시원", "가볍", "덥"]):
        prefs["warmth_bias"] = prefs.get("warmth_bias", 0.0) - 0.5

    # colors
    if "검정" in t or "블랙" in t:
        if any(k in t for k in ["빼", "제외", "말고", "싫"]):
            prefs["banned_keywords"] = list(dict.fromkeys(prefs.get("banned_keywords", []) + ["블랙", "black"]))
        else:
            prefs["preferred_color"] = "black"
    if "뉴트럴" in t or "무채색" in t:
        prefs["preferred_color"] = "neutral"
    if "파스텔" in t:
        prefs["preferred_color"] = "pastel"
    if "비비드" in t or "쨍" in t:
        prefs["preferred_color"] = "vivid"

    # shoes constraints
    if "로퍼" in t and any(k in t for k in ["말고", "빼", "제외"]):
        prefs["avoid_shoes"] = list(dict.fromkeys(prefs.get("avoid_shoes", []) + ["로퍼"]))
    if "운동화" in t and any(k in t for k in ["말고", "빼", "제외"]):
        prefs["avoid_shoes"] = list(dict.fromkeys(prefs.get("avoid_shoes", []) + ["스니커즈"]))

    # generic bans: "OO 빼줘"
    m = re.findall(r"([가-힣a-z0-9]+)\s*(빼|제외|싫어|말고)", t)
    for word, _ in m:
        if len(word) >= 2:
            prefs["banned_keywords"] = list(dict.fromkeys(prefs.get("banned_keywords", []) + [word]))

    return prefs


# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="OOTD (날씨+TPO 자동반영)", page_icon="👕", layout="wide")
st.title("👕 오늘의 OOTD")
st.caption("날씨 + 캘린더(TPO) 자동 반영 + 채팅으로 수정 반영 (MVP)")

# session init
if "prefs" not in st.session_state:
    st.session_state.prefs = {
        "preferred_style": ["casual"],
        "preferred_color": "neutral",
        "banned_keywords": [],
        "avoid_shoes": [],
        "warmth_bias": 0.0,
    }
if "messages" not in st.session_state:
    st.session_state.messages = []


# -------------------------
# Sidebar: weather + prefs + calendar
# -------------------------
with st.sidebar:
    st.header("설정")

    target_date = st.date_input("추천 날짜", value=dt.date.today())

    # Weather section
    st.subheader("🌦️ 날씨")
    city = st.text_input("도시", value=get_default_city())
    api_key = get_openweather_key().strip()
    auto_available = bool(api_key)

    if auto_available:
        weather_mode = st.radio("날씨 모드", ["자동(OpenWeather)", "수동"], index=0)
    else:
        st.info("OPENWEATHER_API_KEY가 없어 수동 날씨 입력만 가능합니다.")
        weather_mode = "수동"

    # manual inputs always visible (fallback)
    m_temp = st.slider("기온(℃)", -20, 45, 16)
    m_feels = st.slider("체감(℃)", -20, 45, 15)
    m_hum = st.slider("습도(%)", 0, 100, 50)
    m_wind = st.slider("바람(m/s)", 0.0, 20.0, 1.5, step=0.1)
    m_rain = st.selectbox("강수", ["없음", "비/눈 가능"], index=0)
    m_desc = st.text_input("날씨 설명(선택)", value="맑음")

    weather_err = None
    weather: Weather

    if weather_mode.startswith("자동"):
        ok, payload = fetch_openweather(city, api_key)
        if ok:
            weather = payload["weather"]
        else:
            weather_err = payload["error"]
            weather_mode = "수동"

    if weather_mode == "수동":
        weather = Weather(
            city=city,
            temp_c=float(m_temp),
            feels_c=float(m_feels),
            humidity=int(m_hum),
            wind_ms=float(m_wind),
            rain=(m_rain != "없음"),
            desc=(m_desc.strip() or "정보 없음"),
        )

    if weather_err:
        st.warning(weather_err)

    # Preferences section
    st.subheader("🙋 사용자 요구사항")
    preferred_style = st.multiselect(
        "선호 스타일",
        ["casual", "formal", "smart", "street", "outdoor", "sport", "date", "minimal"],
        default=st.session_state.prefs.get("preferred_style", ["casual"]),
    )
    preferred_color = st.radio(
        "선호 컬러 톤",
        ["neutral", "black", "pastel", "vivid"],
        index=["neutral", "black", "pastel", "vivid"].index(st.session_state.prefs.get("preferred_color", "neutral")),
        horizontal=True,
    )
    banned_text = st.text_input("피하고 싶은 키워드(쉼표)", value=",".join(st.session_state.prefs.get("banned_keywords", [])))

    st.session_state.prefs["preferred_style"] = preferred_style
    st.session_state.prefs["preferred_color"] = preferred_color
    st.session_state.prefs["banned_keywords"] = [x.strip() for x in banned_text.split(",") if x.strip()]

    # Calendar section
    st.subheader("📅 캘린더 연동(TPO 자동)")
    st.caption("외부 패키지 없이: ① ICS 파일 업로드 또는 ② iCal(ICS) 공개 URL로 연동")

    ics_file = st.file_uploader("ICS 파일 업로드(.ics)", type=["ics"])
    ics_url = st.text_input("iCal(ICS) 공개 URL(선택)", value="", placeholder="https://.../calendar.ics")

    events: List[EventTPO] = []
    if ics_file is not None:
        events = parse_ics_minimal(ics_file.getvalue(), target_date)
    elif ics_url.strip():
        ok, b = fetch_ics_from_url(ics_url.strip())
        if ok:
            events = parse_ics_minimal(b, target_date)
        else:
            st.warning("ICS URL을 가져오지 못했습니다. URL이 공개/접근 가능해야 합니다.")

    # pick event for the date
    chosen_event = events[0] if events else None
    tpo_tags = chosen_event.tags if chosen_event else ["casual"]

    if chosen_event:
        st.success(f"자동 반영: {chosen_event.title}")
        st.write(f"TPO 태그: {', '.join(tpo_tags)}")
    else:
        st.info("해당 날짜에 감지된 일정이 없어 기본 TPO(casual)로 진행합니다.")

# -------------------------
# Main view: show outfit + reasons
# -------------------------
outfit, reasons = build_outfit(weather, tpo_tags, st.session_state.prefs)

top, bottom, outer, shoes = outfit["top"], outfit["bottom"], outfit["outer"], outfit["shoes"]
extras = outfit["extras"]

c1, c2 = st.columns([1.2, 1.0])

with c1:
    st.subheader("오늘의 추천 코디")
    st.write(f"**도시:** {weather.city}  |  **날씨:** {weather.desc}  |  **체감:** {weather.feels_c:.1f}℃ ({temp_band(weather.feels_c)})")
    if chosen_event:
        st.write(f"**캘린더 일정 자동 반영:** {chosen_event.title}  →  **TPO:** {', '.join(tpo_tags)}")
    else:
        st.write(f"**TPO:** {', '.join(tpo_tags)}")

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        st.markdown("### 👕 상의")
        st.write(top["name"] if top else "추천 없음")
    with card2:
        st.markdown("### 👖 하의")
        st.write(bottom["name"] if bottom else "추천 없음")
    with card3:
        st.markdown("### 🧥 아우터")
        st.write(outer["name"] if outer else "필요 없음/추천 없음")
    with card4:
        st.markdown("### 👟 신발")
        st.write(shoes["name"] if shoes else "추천 없음")

    if extras:
        st.markdown("### 🎒 추가 아이템")
        st.write(", ".join([x["name"] for x in extras]))

with c2:
    st.subheader("왜 이렇게 추천했나요?")
    for r in reasons:
        st.write(f"- {r}")

st.divider()

# -------------------------
# Chat: apply modifications
# -------------------------
st.subheader("💬 수정사항을 채팅으로 반영하기")
st.caption("예) “좀 더 포멀하게”, “캐주얼하게”, “검정 빼줘”, “따뜻하게”, “운동화 말고 로퍼”, “비 오는 날이라 젖기 싫어”")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

user_text = st.chat_input("수정사항을 입력해줘…")
if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.prefs = apply_chat_update(user_text, st.session_state.prefs)

    # After update, rebuild outfit
    outfit2, reasons2 = build_outfit(weather, tpo_tags, st.session_state.prefs)

    # assistant response (간단 안내)
    assistant_msg = "수정사항을 반영해서 추천을 업데이트했어요. (화면이 새로고침되며 최신 코디가 표시됩니다.)"
    st.session_state.messages.append({"role": "assistant", "content": assistant_msg})

    st.rerun()

with st.expander("🔎 현재 상태(디버그)"):
    st.write("weather:", weather)
    st.write("tpo_tags:", tpo_tags)
    st.write("prefs:", st.session_state.prefs)
    st.write("chosen_event:", chosen_event.title if chosen_event else None)
