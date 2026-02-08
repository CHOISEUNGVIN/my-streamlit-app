import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import streamlit as st

# openai는 설치만(다음 단계 연동용). 지금 코드는 호출하지 않음.
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
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return os.getenv(key, default)


def get_default_city() -> str:
    return get_secret("DEFAULT_CITY", "Seoul,KR")


def get_openweather_key() -> str:
    return get_secret("OPENWEATHER_API_KEY", "")


# =========================
# Weather (OpenWeather via stdlib)
# =========================
def fetch_openweather(city: str, api_key: str) -> Tuple[bool, Dict]:
    if not api_key:
        return False, {"error": "OPENWEATHER_API_KEY가 없어 수동 날씨 입력만 가능합니다."}
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


def parse_ics_minimal(ics_bytes: bytes, target_date: dt.date) -> List[EventTPO]:
    """
    MVP ICS 파서(외부 패키지 없이):
    - SUMMARY, DTSTART 읽어서 해당 날짜 이벤트만 추출
    - timezone/recurrence 완전 지원 X (구독/단순 일정엔 잘 동작)
    """
    text = ics_bytes.decode("utf-8", errors="ignore")
    text = re.sub(r"\r\n[ \t]", "", text)  # folding 처리

    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.DOTALL)
    events: List[EventTPO] = []

    for b in blocks:
        m_sum = re.search(r"SUMMARY:(.*)", b)
        title = m_sum.group(1).strip() if m_sum else "Untitled"

        m_dt = re.search(r"DTSTART[^:]*:(\d{8})(T(\d{6}))?(Z)?", b)
        if not m_dt:
            continue

        ymd = m_dt.group(1)
        hms = m_dt.group(3)
        if hms:
            hh = int(hms[0:2]); mm = int(hms[2:4]); ss = int(hms[4:6])
            start_dt = dt.datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), hh, mm, ss)
        else:
            start_dt = dt.datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), 9, 0, 0)

        if start_dt.date() != target_date:
            continue

        events.append(EventTPO(title=title, start=start_dt, tags=infer_tpo_tags(title)))

    events.sort(key=lambda x: x.start or dt.datetime.max)
    return events


# =========================
# Wardrobe defaults & storage
# =========================
def default_wardrobe() -> Dict:
    return {
        "tops": [
            {"name": "화이트 셔츠", "tags": ["formal", "smart", "neutral"], "warmth": 2},
            {"name": "맨투맨", "tags": ["casual"], "warmth": 3},
            {"name": "블랙 니트", "tags": ["smart", "casual", "black"], "warmth": 4},
        ],
        "bottoms": [
            {"name": "청바지", "tags": ["casual"], "warmth": 2},
            {"name": "슬랙스", "tags": ["formal", "smart"], "warmth": 2},
        ],
        "outer": [
            {"name": "자켓(블레이저)", "tags": ["formal", "smart"], "warmth": 3},
            {"name": "바람막이", "tags": ["outdoor", "sport", "casual"], "warmth": 2, "rain_ok": True},
        ],
        "shoes": [
            {"name": "스니커즈", "tags": ["casual", "street", "sport"], "rain_ok": True},
            {"name": "로퍼", "tags": ["formal", "smart"], "rain_ok": False},
        ],
        "extras": [
            {"name": "우산", "tags": ["rain"]},
            {"name": "머플러", "tags": ["cold"]},
        ],
    }


def normalize_wardrobe(w: Dict) -> Dict:
    base = default_wardrobe()
    if not isinstance(w, dict):
        return base
    for k in base.keys():
        if k not in w or not isinstance(w[k], list):
            w[k] = base[k]
    return w


# =========================
# Outfit engine (rule-based MVP)
# =========================
def ideal_warmth(feels_c: float, warmth_bias: float = 0.0) -> float:
    band = temp_band(feels_c)
    base = {
        "매우 추움": 6,
        "추움": 5,
        "쌀쌀": 3.5,
        "적당": 2.5,
        "더움": 1.5,
        "매우 더움": 0.5,
    }[band]
    return max(0.0, base + warmth_bias)


def score_item(item: Dict, wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> float:
    score = 0.0
    name = str(item.get("name", "")).lower()
    tags = item.get("tags", [])
    warmth = float(item.get("warmth", 0.0))

    for t in wanted_tags:
        if t in tags:
            score += 2.0

    # color preference (only if tags include it)
    color = prefs.get("preferred_color", "neutral")
    if color == "neutral" and "neutral" in tags:
        score += 0.8
    if color == "black" and ("black" in tags or "dark" in tags):
        score += 0.8
    if color == "pastel" and "pastel" in tags:
        score += 0.8
    if color == "vivid" and "vivid" in tags:
        score += 0.8

    # warmth closeness for clothes categories (tops/bottoms/outer)
    if category in ("tops", "bottoms", "outer"):
        ideal = ideal_warmth(weather.feels_c, prefs.get("warmth_bias", 0.0))
        score += max(0.0, 2.5 - abs(warmth - ideal))

    # rain
    if weather.rain:
        if item.get("rain_ok", False) or category not in ("outer", "shoes"):
            score += 0.6
        else:
            score -= 1.0

    # banned keywords
    for b in prefs.get("banned_keywords", []):
        if b.lower() in name:
            score -= 6.0

    # avoid shoes
    if category == "shoes":
        for s in prefs.get("avoid_shoes", []):
            if s.lower() in name:
                score -= 5.0

    return score


def pick_best(items: List[Dict], wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> Optional[Dict]:
    if not items:
        return None
    ranked = sorted(
        ((score_item(it, wanted_tags, prefs, weather, category), it) for it in items),
        key=lambda x: x[0],
        reverse=True,
    )
    return ranked[0][1]


def build_outfit(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> Tuple[Dict, List[str]]:
    wanted = list(dict.fromkeys(tpo_tags + prefs.get("preferred_style", [])))

    top = pick_best(wardrobe["tops"], wanted, prefs, weather, "tops")
    bottom = pick_best(wardrobe["bottoms"], wanted, prefs, weather, "bottoms")
    shoes = pick_best(wardrobe["shoes"], wanted, prefs, weather, "shoes")

    need_outer = weather.feels_c <= 16 or weather.rain or weather.wind_ms >= 7
    outer = pick_best(wardrobe["outer"], wanted, prefs, weather, "outer") if need_outer else None

    extras = []
    if weather.rain:
        extras.append({"name": "우산"})
    if weather.feels_c <= 8:
        extras.append({"name": "머플러"})

    outfit = {"top": top, "bottom": bottom, "outer": outer, "shoes": shoes, "extras": extras}

    reasons = []
    reasons.append(f"체감온도 **{weather.feels_c:.1f}℃({temp_band(weather.feels_c)})** 기준으로 레이어링/보온을 맞췄어요.")
    if weather.rain:
        reasons.append("비/눈 가능성이 있어 **우산/레인 대응**을 우선했어요.")
    if tpo_tags:
        reasons.append(f"캘린더 일정에서 자동 추론된 TPO가 **{', '.join(tpo_tags)}**라서 그 무드에 맞췄어요.")
    if prefs.get("preferred_style"):
        reasons.append(f"선호 스타일(**{', '.join(prefs['preferred_style'])}**)을 반영했어요.")
    if prefs.get("preferred_color"):
        reasons.append(f"선호 컬러 톤(**{prefs['preferred_color']}**)을 가능한 범위에서 우선했어요.")
    if prefs.get("banned_keywords"):
        reasons.append(f"피하고 싶은 키워드(**{', '.join(prefs['banned_keywords'])}**)는 가급적 제외했어요.")
    return outfit, reasons


# =========================
# Shopping recommendations (missing items)
# =========================
def wardrobe_has_item_like(wardrobe: Dict, category: str, keywords: List[str] = None, tag_any: List[str] = None) -> bool:
    keywords = keywords or []
    tag_any = tag_any or []
    for it in wardrobe.get(category, []):
        name = str(it.get("name", "")).lower()
        tags = it.get("tags", [])
        if any(k.lower() in name for k in keywords):
            return True
        if tag_any and any(t in tags for t in tag_any):
            return True
    return False


def suggest_missing_items(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> List[Dict]:
    """
    '사면 좋은' 아이템 추천 (없으면 추천) - 룰 기반 템플릿.
    """
    recs: List[Dict] = []

    # Rain essentials
    if weather.rain:
        if not wardrobe_has_item_like(wardrobe, "outer", tag_any=["rain"]):
            # rain_ok 속성으로 간접 판단도 가능하지만, 템플릿으로 추천
            recs.append({
                "name": "방수 아우터(레인 재킷/방수 바람막이)",
                "why": "오늘 비/눈 가능성이 있어 젖어도 부담 없는 아우터가 있으면 편해요.",
                "category": "outer",
                "tags": ["casual", "outdoor", "rain"],
            })
        if not wardrobe_has_item_like(wardrobe, "shoes", tag_any=["rain_ok"]):
            recs.append({
                "name": "비 오는 날용 신발(레인부츠/방수 스니커즈)",
                "why": "젖는 스트레스를 줄여줘요. 우중 이동이 잦으면 만족도가 높아요.",
                "category": "shoes",
                "tags": ["casual", "rain"],
            })

    # Cold essentials
    if weather.feels_c <= 8:
        if not wardrobe_has_item_like(wardrobe, "outer", keywords=["패딩", "코트"], tag_any=[]):
            recs.append({
                "name": "따뜻한 아우터(패딩/롱코트)",
                "why": "체감이 낮아서 보온 아우터가 있으면 코디 고민이 줄어요.",
                "category": "outer",
                "tags": ["casual", "smart"],
            })
        if not wardrobe_has_item_like(wardrobe, "extras", keywords=["머플러"], tag_any=["cold"]):
            recs.append({
                "name": "머플러(또는 넥워머)",
                "why": "추위 체감이 큰 날에는 작은 아이템이 체감 온도를 확 낮춰줘요.",
                "category": "extras",
                "tags": ["cold"],
            })

    # Formal essentials for formal/smart TPO
    if any(t in tpo_tags for t in ["formal", "smart"]):
        if not wardrobe_has_item_like(wardrobe, "outer", keywords=["블레이저", "자켓"], tag_any=["formal"]):
            recs.append({
                "name": "블레이저(기본 자켓)",
                "why": "면접/발표/미팅 같은 TPO에서 활용도가 매우 높고, 상/하의 아무거나 걸쳐도 정돈돼 보여요.",
                "category": "outer",
                "tags": ["formal", "smart"],
            })
        if not wardrobe_has_item_like(wardrobe, "tops", keywords=["셔츠"], tag_any=["formal"]):
            recs.append({
                "name": "기본 셔츠(화이트/라이트블루)",
                "why": "포멀/세미포멀의 핵심 베이스라 TPO 대응력이 확 올라가요.",
                "category": "tops",
                "tags": ["formal", "smart", "neutral"],
            })
        if not wardrobe_has_item_like(wardrobe, "shoes", keywords=["로퍼"], tag_any=["formal"]):
            recs.append({
                "name": "로퍼(또는 미니멀 레더 슈즈)",
                "why": "포멀 무드 완성도를 크게 올려주고, 슬랙스/셔츠와 궁합이 좋아요.",
                "category": "shoes",
                "tags": ["formal", "smart"],
            })

    # Date essentials (smart/date)
    if "date" in tpo_tags:
        if not wardrobe_has_item_like(wardrobe, "tops", keywords=["니트"], tag_any=["smart"]):
            recs.append({
                "name": "깔끔한 니트(베이직)",
                "why": "데이트/모임에서 과하지 않게 ‘신경 쓴 느낌’을 내기 좋아요.",
                "category": "tops",
                "tags": ["smart", "date"],
            })

    # Universal basics
    if not wardrobe_has_item_like(wardrobe, "bottoms", keywords=["청바지"], tag_any=["casual"]):
        recs.append({
            "name": "기본 청바지(스트레이트/와이드)",
            "why": "캐주얼 TPO에서 실패 확률이 낮고 상의/신발 어디든 붙어요.",
            "category": "bottoms",
            "tags": ["casual"],
        })
    if not wardrobe_has_item_like(wardrobe, "shoes", keywords=["스니커즈"], tag_any=["casual"]):
        recs.append({
            "name": "기본 스니커즈(화이트/블랙)",
            "why": "가장 범용성이 높아서 ‘매일 코디하기 귀찮을 때’ 구원템이에요.",
            "category": "shoes",
            "tags": ["casual"],
        })

    # Deduplicate by name
    seen = set()
    uniq = []
    for r in recs:
        if r["name"] not in seen:
            uniq.append(r)
            seen.add(r["name"])
    return uniq


# =========================
# Chat updates -> preferences
# =========================
def apply_chat_update(text: str, prefs: Dict) -> Dict:
    t = (text or "").strip().lower()
    if not t:
        return prefs

    if any(k in t for k in ["포멀", "격식", "깔끔", "정장"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["formal", "smart"])))
    if any(k in t for k in ["캐주얼", "편하게"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["casual"])))
    if any(k in t for k in ["스트릿"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["street", "casual"])))
    if any(k in t for k in ["운동", "스포츠", "활동적"]):
        prefs["preferred_style"] = list(dict.fromkeys((prefs.get("preferred_style", []) + ["sport", "casual"])))

    if any(k in t for k in ["따뜻", "보온", "추워"]):
        prefs["warmth_bias"] = prefs.get("warmth_bias", 0.0) + 0.5
    if any(k in t for k in ["시원", "가볍", "덥"]):
        prefs["warmth_bias"] = prefs.get("warmth_bias", 0.0) - 0.5

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

    if "로퍼" in t and any(k in t for k in ["말고", "빼", "제외"]):
        prefs["avoid_shoes"] = list(dict.fromkeys(prefs.get("avoid_shoes", []) + ["로퍼"]))
    if "운동화" in t and any(k in t for k in ["말고", "빼", "제외"]):
        prefs["avoid_shoes"] = list(dict.fromkeys(prefs.get("avoid_shoes", []) + ["스니커즈"]))

    m = re.findall(r"([가-힣a-z0-9]+)\s*(빼|제외|싫어|말고)", t)
    for word, _ in m:
        if len(word) >= 2:
            prefs["banned_keywords"] = list(dict.fromkeys(prefs.get("banned_keywords", []) + [word]))

    return prefs


# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="OOTD (옷장관리+구매추천)", page_icon="👕", layout="wide")
st.title("👕 OOTD 추천 앱 (옷장 관리 + 구매 추천)")
st.caption("날씨 + 캘린더(TPO) 자동 반영 + 채팅 수정 + 옷장 CRUD + 없는 아이템 구매 추천 (MVP)")

# Init state
if "page" not in st.session_state:
    st.session_state.page = "오늘 추천"
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
if "wardrobe" not in st.session_state:
    st.session_state.wardrobe = default_wardrobe()
st.session_state.wardrobe = normalize_wardrobe(st.session_state.wardrobe)

# Sidebar navigation + shared inputs (weather/calendar/prefs used for recommendation and shopping)
with st.sidebar:
    st.header("메뉴")
    st.session_state.page = st.radio("이동", ["오늘 추천", "옷장 관리", "구매 추천"], index=["오늘 추천", "옷장 관리", "구매 추천"].index(st.session_state.page))

    st.divider()
    st.subheader("📅 캘린더 연동(TPO 자동)")
    target_date = st.date_input("추천 날짜", value=dt.date.today())
    ics_file = st.file_uploader("ICS 업로드(.ics)", type=["ics"])
    ics_url = st.text_input("iCal(ICS) 공개 URL", value="", placeholder="https://.../calendar.ics")

    events: List[EventTPO] = []
    if ics_file is not None:
        events = parse_ics_minimal(ics_file.getvalue(), target_date)
    elif ics_url.strip():
        ok, b = fetch_ics_from_url(ics_url.strip())
        if ok:
            events = parse_ics_minimal(b, target_date)
        else:
            st.warning("ICS URL을 가져오지 못했습니다. 공개 URL인지 확인하세요.")

    chosen_event = events[0] if events else None
    tpo_tags = chosen_event.tags if chosen_event else ["casual"]

    if chosen_event:
        st.success(f"자동 반영: {chosen_event.title}")
        st.write(f"TPO: {', '.join(tpo_tags)}")
    else:
        st.info("일정이 감지되지 않아 기본 TPO(casual)로 진행합니다.")

    st.divider()
    st.subheader("🌦️ 날씨")
    city = st.text_input("도시", value=get_default_city())
    api_key = get_openweather_key().strip()
    auto_available = bool(api_key)

    if auto_available:
        weather_mode = st.radio("날씨 모드", ["자동(OpenWeather)", "수동"], index=0)
    else:
        st.info("OPENWEATHER_API_KEY가 없어 수동 입력만 가능합니다.")
        weather_mode = "수동"

    m_temp = st.slider("기온(℃)", -20, 45, 16)
    m_feels = st.slider("체감(℃)", -20, 45, 15)
    m_hum = st.slider("습도(%)", 0, 100, 50)
    m_wind = st.slider("바람(m/s)", 0.0, 20.0, 1.5, step=0.1)
    m_rain = st.selectbox("강수", ["없음", "비/눈 가능"], index=0)
    m_desc = st.text_input("날씨 설명(선택)", value="맑음")

    weather_err = None
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

    st.divider()
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


# =========================
# Page 1: Today OOTD
# =========================
if st.session_state.page == "오늘 추천":
    outfit, reasons = build_outfit(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)

    st.subheader("오늘의 추천 코디")
    st.write(
        f"**도시:** {weather.city}  |  **날씨:** {weather.desc}  |  **체감:** {weather.feels_c:.1f}℃ ({temp_band(weather.feels_c)})"
    )
    if chosen_event:
        st.write(f"**캘린더 일정 자동 반영:** {chosen_event.title}  →  **TPO:** {', '.join(tpo_tags)}")
    else:
        st.write(f"**TPO:** {', '.join(tpo_tags)}")

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

    st.divider()
    st.subheader("왜 이렇게 추천했나요?")
    for r in reasons:
        st.write(f"- {r}")

    st.divider()
    st.subheader("💬 채팅으로 수정사항 반영")
    st.caption("예) “좀 더 포멀하게”, “캐주얼하게”, “검정 빼줘”, “따뜻하게”, “운동화 말고 로퍼”")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_text = st.chat_input("수정사항을 입력해줘…")
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        st.session_state.prefs = apply_chat_update(user_text, st.session_state.prefs)
        st.session_state.messages.append({"role": "assistant", "content": "수정사항을 반영했어요. 상단 추천이 업데이트됩니다."})
        st.rerun()


# =========================
# Page 2: Wardrobe management
# =========================
elif st.session_state.page == "옷장 관리":
    st.subheader("옷장 관리")
    st.caption("내 옷을 등록/삭제하고, JSON으로 백업/복원할 수 있어요. (Cloud 재시작 대비 추천)")

    w = st.session_state.wardrobe

    # Add item form
    st.markdown("### ➕ 옷 추가")
    with st.form("add_item_form", clear_on_submit=True):
        category = st.selectbox("카테고리", ["tops", "bottoms", "outer", "shoes", "extras"])
        name = st.text_input("이름", placeholder="예: 그레이 후드티")
        tags_text = st.text_input("태그(쉼표)", placeholder="예: casual,street")
        warmth = st.slider("보온도(warmth) (의류만)", 0.0, 7.0, 3.0, step=0.5)
        rain_ok = st.checkbox("비/눈 OK (아우터/신발에 권장)", value=False)
        submitted = st.form_submit_button("추가")

        if submitted:
            if not name.strip():
                st.error("이름을 입력해주세요.")
            else:
                tags = [x.strip() for x in tags_text.split(",") if x.strip()]
                item = {"name": name.strip(), "tags": tags}
                if category in ("tops", "bottoms", "outer"):
                    item["warmth"] = float(warmth)
                if category in ("outer", "shoes"):
                    item["rain_ok"] = bool(rain_ok)
                w[category].append(item)
                st.success("추가 완료!")
                st.session_state.wardrobe = normalize_wardrobe(w)
                st.rerun()

    st.divider()

    # List items with delete
    st.markdown("### 📦 내 옷 목록")
    for cat in ["tops", "bottoms", "outer", "shoes", "extras"]:
        st.markdown(f"#### {cat}")
        if not w.get(cat):
            st.write("— 비어있음 —")
            continue

        for idx, it in enumerate(w[cat]):
            cols = st.columns([3.5, 2.5, 1.2, 1.0])
            with cols[0]:
                st.write(f"**{it.get('name','')}**")
            with cols[1]:
                st.write(", ".join(it.get("tags", [])) if it.get("tags") else "tags: -")
            with cols[2]:
                if "warmth" in it:
                    st.write(f"warmth: {it['warmth']}")
                else:
                    st.write("")
            with cols[3]:
                if st.button("삭제", key=f"del_{cat}_{idx}"):
                    w[cat].pop(idx)
                    st.session_state.wardrobe = normalize_wardrobe(w)
                    st.rerun()

    st.divider()

    # Export / Import JSON
    st.markdown("### 💾 백업/복원")
    export_json = json.dumps(st.session_state.wardrobe, ensure_ascii=False, indent=2)
    st.download_button(
        label="옷장 JSON 다운로드",
        data=export_json.encode("utf-8"),
        file_name="wardrobe.json",
        mime="application/json",
    )

    uploaded = st.file_uploader("옷장 JSON 업로드(복원)", type=["json"])
    if uploaded is not None:
        try:
            restored = json.loads(uploaded.getvalue().decode("utf-8", errors="ignore"))
            st.session_state.wardrobe = normalize_wardrobe(restored)
            st.success("복원 완료! (추천 화면에서도 즉시 반영됩니다)")
            st.rerun()
        except Exception as e:
            st.error(f"복원 실패: {e}")

    if st.button("샘플 옷장으로 초기화"):
        st.session_state.wardrobe = default_wardrobe()
        st.success("초기화 완료")
        st.rerun()


# =========================
# Page 3: Shopping recommendations
# =========================
elif st.session_state.page == "구매 추천":
    st.subheader("사면 좋은(없는) 옷 추천")
    st.caption("내 옷장 + 오늘 날씨 + 캘린더(TPO)를 보고, ‘없으면’ 도움이 되는 아이템을 추천해요.")

    missing = suggest_missing_items(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)

    st.write(
        f"기준: **체감 {weather.feels_c:.1f}℃({temp_band(weather.feels_c)})**, "
        f"**강수 {'있음' if weather.rain else '없음'}**, "
        f"**TPO {', '.join(tpo_tags)}**"
    )

    if not missing:
        st.success("현재 조건 기준으로 ‘꼭 필요하다’ 싶은 누락 아이템이 크게 보이지 않아요! 👍")
    else:
        for r in missing:
            with st.container(border=True):
                st.markdown(f"### 🛍️ {r['name']}")
                st.write(f"- 추천 이유: {r['why']}")
                st.write(f"- 예상 카테고리: **{r['category']}**")
                st.write(f"- 관련 태그: **{', '.join(r['tags'])}**")

        st.info("원하면 다음 단계에서 ‘예산/브랜드/스타일’ 조건까지 넣어서 실제 상품(링크) 추천으로 확장할 수 있어요.")

# Debug
with st.expander("🔎 디버그"):
    st.write("page:", st.session_state.page)
    st.write("prefs:", st.session_state.prefs)
    st.write("tpo_tags:", tpo_tags)
    st.write("wardrobe:", st.session_state.wardrobe)
