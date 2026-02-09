import base64
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import streamlit as st
from openai import OpenAI  # 설치만(다음 단계 연동용). 지금 코드는 호출하지 않음.  # noqa: F401


# =========================================================
# Models
# =========================================================
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


# =========================================================
# Helpers: secrets/env (optional)
# =========================================================
def get_secret(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return os.getenv(key, default)


def date_key(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")


def get_default_city() -> str:
    c = str(st.session_state.get("default_city", "") or "").strip()
    if c:
        return c
    return get_secret("DEFAULT_CITY", "Seoul,KR")


def get_openweather_key() -> str:
    k = str(st.session_state.get("openweather_api_key", "") or "").strip()
    if k:
        return k
    return get_secret("OPENWEATHER_API_KEY", "")


def get_openai_key() -> str:
    k = str(st.session_state.get("openai_api_key", "") or "").strip()
    if k:
        return k
    return get_secret("OPENAI_API_KEY", "")


# =========================================================
# Image helpers (base64)
# =========================================================
def imgfile_to_b64(uploaded_file) -> Tuple[Optional[str], Optional[str]]:
    if uploaded_file is None:
        return None, None
    raw = uploaded_file.getvalue()
    b64 = base64.b64encode(raw).decode("utf-8")
    mime = uploaded_file.type or "image/jpeg"
    return b64, mime


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64.encode("utf-8"))


# =========================================================
# Weather: OpenWeather optional (stdlib only)
# =========================================================
def fetch_openweather(city: str, api_key: str) -> Tuple[bool, Dict]:
    if not api_key:
        return False, {"error": "OPENWEATHER_API_KEY가 없어 수동 입력만 가능합니다."}
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
        return False, {"error": f"날씨 조회 실패: {e}"}


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


# =========================================================
# Calendar: ICS minimal parser (stdlib only)
# =========================================================
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
    text = ics_bytes.decode("utf-8", errors="ignore")
    text = re.sub(r"\r\n[ \t]", "", text)

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


# =========================================================
# Wardrobe
# =========================================================
def default_wardrobe() -> Dict:
    return {
        "tops": [
            {"name": "화이트 셔츠", "tags": ["formal", "smart", "neutral", "clean"], "warmth": 2},
            {"name": "맨투맨", "tags": ["casual", "cozy"], "warmth": 3},
            {"name": "블랙 니트", "tags": ["smart", "casual", "black", "minimal"], "warmth": 4},
        ],
        "bottoms": [
            {"name": "청바지", "tags": ["casual"], "warmth": 2},
            {"name": "슬랙스", "tags": ["formal", "smart", "clean"], "warmth": 2},
            {"name": "조거팬츠", "tags": ["sport", "casual", "cozy"], "warmth": 2},
        ],
        "outer": [
            {"name": "자켓(블레이저)", "tags": ["formal", "smart", "clean"], "warmth": 3, "rain_ok": False},
            {"name": "바람막이", "tags": ["outdoor", "sport", "casual"], "warmth": 2, "rain_ok": True},
            {"name": "패딩", "tags": ["casual", "cozy"], "warmth": 6, "rain_ok": True},
        ],
        "shoes": [
            {"name": "스니커즈", "tags": ["casual", "street", "sport"], "rain_ok": True},
            {"name": "로퍼", "tags": ["formal", "smart", "clean"], "rain_ok": False},
        ],
        "extras": [
            {"name": "우산", "tags": ["rain"]},
            {"name": "머플러", "tags": ["cold", "cozy"]},
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


# =========================================================
# Mood/Profile (free text)
# =========================================================
STYLE_KEYWORDS = {
    "미니멀": ["minimal", "미니멀", "깔끔", "심플", "정갈"],
    "클린": ["clean", "클린", "단정", "정돈"],
    "시크": ["chic", "시크", "도시적", "차분"],
    "러블리": ["lovely", "러블리", "사랑스", "포근"],
    "스트릿": ["street", "스트릿", "힙", "힙한"],
    "빈티지": ["vintage", "빈티지", "레트로"],
    "코지": ["cozy", "코지", "포근", "따뜻", "부드럽"],
    "모던": ["modern", "모던"],
    "아방가르드": ["avant", "아방", "실험적"],
}

COLOR_KEYWORDS = {
    "black": ["블랙", "검정", "검은", "black"],
    "white": ["화이트", "흰", "white"],
    "gray": ["그레이", "회색", "gray"],
    "navy": ["네이비", "남색", "navy"],
    "beige": ["베이지", "카멜", "beige", "camel"],
    "brown": ["브라운", "갈색", "brown"],
    "blue": ["블루", "파랑", "blue"],
    "green": ["그린", "초록", "green", "올리브", "olive"],
    "red": ["레드", "빨강", "red"],
    "pink": ["핑크", "분홍", "pink"],
    "purple": ["퍼플", "보라", "purple"],
    "pastel": ["파스텔", "pastel"],
    "vivid": ["비비드", "쨍", "선명", "vivid"],
    "neutral": ["뉴트럴", "무채색", "neutral", "모노톤", "모노"],
}

REASK_TRIGGERS = ["바꿔", "다시", "새로", "다른", "재추천", "다르게", "change", "reroll"]


def extract_signals(bundle_text: str) -> Dict[str, List[str]]:
    s = (bundle_text or "").lower()
    prefer, avoid = [], []
    prefer_colors, avoid_colors = [], []
    banned_words = []

    for word, _ in re.findall(r"([가-힣a-z0-9]+)\s*(빼|제외|싫어|말고)", s):
        if len(word) >= 2:
            avoid.append(word)
            banned_words.append(word)

    for label, kws in STYLE_KEYWORDS.items():
        if any(k.lower() in s for k in kws):
            prefer.append(label)

    for key, kws in COLOR_KEYWORDS.items():
        hit = any(k.lower() in s for k in [x.lower() for x in kws])
        if hit:
            if any(x in s for x in ["빼", "제외", "싫", "말고"]):
                avoid_colors.append(key)
            else:
                prefer_colors.append(key)

    return {
        "prefer_signals": list(dict.fromkeys(prefer)),
        "avoid_signals": list(dict.fromkeys(avoid)),
        "prefer_colors": list(dict.fromkeys(prefer_colors)),
        "avoid_colors": list(dict.fromkeys(avoid_colors)),
        "banned_from_text": list(dict.fromkeys(banned_words)),
    }


def rebuild_profile(prefs: Dict, mood_records: List[Dict], chat_messages: List[Dict], banned_manual: List[str]) -> Dict:
    mood_texts = [str(x.get("text", "")).strip() for x in mood_records if str(x.get("text", "")).strip()]
    chat_user_texts = [
        m["content"].strip()
        for m in chat_messages
        if m.get("role") == "user" and str(m.get("content", "")).strip()
    ]
    style_dna = "\n".join(mood_texts + chat_user_texts).strip()[-2500:]
    sig = extract_signals(style_dna)

    banned = []
    banned += [x.strip() for x in banned_manual if x.strip()]
    banned += sig.get("banned_from_text", [])
    banned = list(dict.fromkeys(banned))

    prefs["style_dna"] = style_dna
    prefs["signals"] = {
        "prefer_signals": sig.get("prefer_signals", []),
        "avoid_signals": sig.get("avoid_signals", []),
        "prefer_colors": sig.get("prefer_colors", []),
        "avoid_colors": sig.get("avoid_colors", []),
    }
    prefs["banned_keywords"] = banned
    return prefs


# =========================================================
# Outfit engine
# =========================================================
def ideal_warmth(feels_c: float, bias: float = 0.0) -> float:
    band = temp_band(feels_c)
    base = {"매우 추움": 6, "추움": 5, "쌀쌀": 3.5, "적당": 2.5, "더움": 1.5, "매우 더움": 0.5}[band]
    return max(0.0, base + bias)


def score_item(item: Dict, wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> float:
    name = str(item.get("name", "")).lower()
    tags = item.get("tags", [])
    warmth = float(item.get("warmth", 0.0))
    score = 0.0

    for t in wanted_tags:
        if t in tags:
            score += 2.0

    sig = prefs.get("signals", {})
    for p in sig.get("prefer_signals", []):
        tag_guess = {
            "미니멀": "minimal",
            "클린": "clean",
            "시크": "chic",
            "러블리": "lovely",
            "스트릿": "street",
            "빈티지": "vintage",
            "코지": "cozy",
            "모던": "modern",
            "아방가르드": "avant",
        }.get(p, "")
        if tag_guess and tag_guess in tags:
            score += 1.0

    if weather.rain:
        if item.get("rain_ok", False) or category not in ("outer", "shoes"):
            score += 0.5
        else:
            score -= 1.0

    if category in ("tops", "bottoms", "outer"):
        target = ideal_warmth(weather.feels_c, prefs.get("warmth_bias", 0.0))
        score += max(0.0, 2.2 - abs(warmth - target))

    for b in prefs.get("banned_keywords", []):
        if b.lower() in name:
            score -= 7.0

    temp_ban = set(st.session_state.get("temp_ban_items", []))
    if str(item.get("name", "")) in temp_ban:
        score -= 999.0

    return score


def pick_best(items: List[Dict], wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> Optional[Dict]:
    if not items:
        return None
    ranked = [(score_item(it, wanted_tags, prefs, weather, category), it) for it in items]
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def recommend_colors(weather: Weather, tpo_tags: List[str], prefs: Dict) -> Dict[str, str]:
    sig = prefs.get("signals", {})
    prefer = [c for c in sig.get("prefer_colors", []) if c not in set(sig.get("avoid_colors", []))]

    if prefer:
        base = prefer[0]
        accent = prefer[1] if len(prefer) > 1 else "neutral"
    else:
        if weather.feels_c <= 8:
            base, accent = "navy", "beige"
        elif weather.feels_c <= 16:
            base, accent = "gray", "navy"
        elif weather.feels_c <= 23:
            base, accent = "neutral", "blue"
        else:
            base, accent = "white", "green"

        if any(t in tpo_tags for t in ["formal", "smart"]):
            base = "navy" if base in ("white", "green", "pink") else base
            accent = "white" if accent in ("red", "pink", "vivid") else accent

    shoe = "black" if "black" not in set(sig.get("avoid_colors", [])) else "navy"
    bottom = "dark" if weather.rain else ("navy" if base == "white" else "gray")
    return {"base": base, "accent": accent, "bottom_hint": bottom, "shoe_hint": shoe}


def pretty_color_name(c: str) -> str:
    m = {
        "black": "블랙",
        "white": "화이트",
        "gray": "그레이",
        "navy": "네이비",
        "beige": "베이지/카멜",
        "brown": "브라운",
        "blue": "블루",
        "green": "그린/올리브",
        "red": "레드",
        "pink": "핑크",
        "purple": "퍼플",
        "pastel": "파스텔 톤",
        "vivid": "비비드 톤",
        "neutral": "뉴트럴(무채색)",
        "dark": "어두운 톤",
    }
    return m.get(c, c)


def build_outfit(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> Tuple[Dict, List[str], Dict[str, str]]:
    wanted = list(dict.fromkeys(tpo_tags))
    top = pick_best(wardrobe["tops"], wanted, prefs, weather, "tops")
    bottom = pick_best(wardrobe["bottoms"], wanted, prefs, weather, "bottoms")
    shoes = pick_best(wardrobe["shoes"], wanted, prefs, weather, "shoes")

    need_outer = weather.feels_c <= 16 or weather.rain or weather.wind_ms >= 7
    outer = pick_best(wardrobe["outer"], wanted, prefs, weather, "outer") if need_outer else None

    extras = []
    if weather.rain:
        extras.append("우산")
    if weather.feels_c <= 8:
        extras.append("머플러")

    outfit = {"top": top, "bottom": bottom, "outer": outer, "shoes": shoes, "extras": extras}
    color_plan = recommend_colors(weather, tpo_tags, prefs)

    reasons = []
    reasons.append(f"체감온도 **{weather.feels_c:.1f}℃({temp_band(weather.feels_c)})** 기준으로 구성했어요.")
    if weather.rain:
        reasons.append("비/눈 가능성이 있어 레인 대응(아우터/신발/우산)을 고려했어요.")
    reasons.append(f"TPO(**{', '.join(tpo_tags)}**)를 반영했어요.")
    if prefs.get("style_dna"):
        reasons.append(f"무드 기록/채팅을 반영했어요: “{prefs['style_dna'][:120]}{'…' if len(prefs['style_dna'])>120 else ''}”")
    if prefs.get("banned_keywords"):
        reasons.append(f"피하고 싶은 키워드(**{', '.join(prefs['banned_keywords'])}**)는 제외했어요.")
    reasons.append(f"컬러는 **{pretty_color_name(color_plan['base'])} 베이스 + {pretty_color_name(color_plan['accent'])} 포인트**를 추천해요.")
    return outfit, reasons, color_plan


# =========================================================
# Favorites
# =========================================================
def safe_item(it: Optional[Dict]) -> Optional[Dict]:
    if not isinstance(it, dict):
        return None
    out = {}
    for k in ["name", "tags", "warmth", "rain_ok", "image_b64", "image_mime"]:
        if k in it:
            out[k] = it.get(k)
    return out


def make_favorite_payload(
    target_date: dt.date,
    outfit: Dict,
    weather: Weather,
    tpo_tags: List[str],
    tpo_summary: str,
    reasons: List[str],
    color_plan: Dict[str, str],
) -> Dict:
    return {
        "date": date_key(target_date),
        "saved_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tpo_tags": list(tpo_tags),
        "tpo_summary": tpo_summary,
        "weather": {
            "city": weather.city,
            "temp_c": weather.temp_c,
            "feels_c": weather.feels_c,
            "humidity": weather.humidity,
            "wind_ms": weather.wind_ms,
            "rain": weather.rain,
            "desc": weather.desc,
        },
        "colors": dict(color_plan),
        "reasons": list(reasons),
        "outfit": {
            "top": safe_item(outfit.get("top")),
            "bottom": safe_item(outfit.get("bottom")),
            "outer": safe_item(outfit.get("outer")),
            "shoes": safe_item(outfit.get("shoes")),
            "extras": list(outfit.get("extras", [])),
        },
    }


def outfit_summary_text(payload: Dict) -> str:
    o = payload.get("outfit", {})
    def n(x): return (x or {}).get("name") if isinstance(x, dict) else None
    return f"상의:{n(o.get('top')) or '-'} / 하의:{n(o.get('bottom')) or '-'} / 아우터:{n(o.get('outer')) or '없음'} / 신발:{n(o.get('shoes')) or '-'}"


# =========================================================
# Purchase suggestions
# =========================================================
def wardrobe_has_item_like(wardrobe: Dict, category: str, keywords: List[str] = None) -> bool:
    keywords = keywords or []
    for it in wardrobe.get(category, []):
        n = str(it.get("name", "")).lower()
        if any(k.lower() in n for k in keywords):
            return True
    return False


def suggest_missing_items(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> List[Dict]:
    recs = []
    sig = prefs.get("signals", {})
    is_clean = any(x in sig.get("prefer_signals", []) for x in ["미니멀", "클린"])

    if weather.rain:
        if not wardrobe_has_item_like(wardrobe, "outer", ["방수", "레인", "우비"]):
            recs.append({"name": "방수 바람막이/레인 재킷", "why": "비 오는 날 체감 편의성이 커요."})
        if not wardrobe_has_item_like(wardrobe, "shoes", ["방수", "레인", "부츠"]):
            recs.append({"name": "방수 신발(레인부츠/방수 스니커즈)", "why": "젖는 스트레스를 줄여줘요."})

    if weather.feels_c <= 8 and not wardrobe_has_item_like(wardrobe, "outer", ["코트", "패딩"]):
        recs.append({"name": "따뜻한 아우터(코트/패딩)", "why": "추운 날 코디 고민을 크게 줄여줘요."})

    if any(t in tpo_tags for t in ["formal", "smart"]):
        if not wardrobe_has_item_like(wardrobe, "outer", ["블레이저", "자켓"]):
            recs.append({"name": "기본 블레이저", "why": "미팅/발표/면접에서 실패 확률이 낮아요."})
        if not wardrobe_has_item_like(wardrobe, "tops", ["셔츠"]):
            recs.append({"name": "기본 셔츠(화이트/라이트블루)", "why": "세미포멀/클린 무드에 강해요."})

    if is_clean and not wardrobe_has_item_like(wardrobe, "bottoms", ["슬랙스"]):
        recs.append({"name": "다크 톤 슬랙스", "why": "클린/미니멀 무드에서 조합이 쉬워요."})

    cp = recommend_colors(weather, tpo_tags, prefs)
    recs.append({"name": f"컬러 방향: {pretty_color_name(cp['base'])} + {pretty_color_name(cp['accent'])}", "why": "오늘 조건에서 안정적인 팔레트예요."})

    seen, out = set(), []
    for r in recs:
        if r["name"] not in seen:
            out.append(r); seen.add(r["name"])
    return out


# =========================================================
# Streamlit App
# =========================================================
st.set_page_config(page_title="OOTD (옷 수정/사진 완전 지원)", page_icon="👕", layout="wide")
st.title("👕 OOTD 추천 앱")


# ---------------------
# Session init
# ---------------------
if "page" not in st.session_state:
    st.session_state.page = "오늘 추천"

if "openai_api_key" not in st.session_state:
    st.session_state.openai_api_key = ""
if "openweather_api_key" not in st.session_state:
    st.session_state.openweather_api_key = ""
if "default_city" not in st.session_state:
    st.session_state.default_city = get_secret("DEFAULT_CITY", "Seoul,KR")

if "wardrobe" not in st.session_state:
    st.session_state.wardrobe = default_wardrobe()
st.session_state.wardrobe = normalize_wardrobe(st.session_state.wardrobe)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "mood_records" not in st.session_state:
    st.session_state.mood_records = []
if "manual_events_by_date" not in st.session_state:
    st.session_state.manual_events_by_date = {}
if "prefs" not in st.session_state:
    st.session_state.prefs = {
        "warmth_bias": 0.0,
        "style_dna": "",
        "signals": {"prefer_signals": [], "avoid_signals": [], "prefer_colors": [], "avoid_colors": []},
        "banned_keywords": [],
    }
if "temp_ban_items" not in st.session_state:
    st.session_state.temp_ban_items = []
if "last_outfit" not in st.session_state:
    st.session_state.last_outfit = {"top": None, "bottom": None, "outer": None, "shoes": None}
if "saved_outfits" not in st.session_state:
    st.session_state.saved_outfits = {}

# ✅ 편집 대상 저장 (dialog 트리거용)
if "edit_target" not in st.session_state:
    st.session_state.edit_target = None  # {"cat":..., "idx":...}


# ---------------------
# Sidebar
# ---------------------
with st.sidebar:
    st.header("🔐 API 키 & 기본 설정")
    st.session_state.openweather_api_key = st.text_input(
        "OpenWeather API Key (세션 저장)",
        value=st.session_state.openweather_api_key,
        type="password",
        placeholder="openweather key...",
    )
    st.session_state.default_city = st.text_input(
        "DEFAULT_CITY (예: Seoul,KR)",
        value=st.session_state.default_city,
    )
    st.session_state.openai_api_key = st.text_input(
        "OpenAI API Key (세션 저장)",
        value=st.session_state.openai_api_key,
        type="password",
        placeholder="sk-...",
    )
    st.caption(f"OpenWeather 키 상태: {'입력됨 ✅' if bool(get_openweather_key().strip()) else '없음 (수동만)'}")

    st.divider()
    st.header("메뉴")
    st.session_state.page = st.radio(
        "이동",
        ["오늘 추천", "저장한 코디", "옷장 관리", "구매 추천"],
        index=["오늘 추천", "저장한 코디", "옷장 관리", "구매 추천"].index(st.session_state.page),
    )

    st.divider()
    st.subheader("🧠 무드 기록(추가/삭제)")
    with st.form("add_mood_record", clear_on_submit=True):
        mood_text = st.text_input("무드 한 줄", placeholder="예: 차분한데 포근하게 / 모노톤+포인트")
        ok = st.form_submit_button("무드 저장")
        if ok and mood_text.strip():
            st.session_state.mood_records.append({"text": mood_text.strip(), "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
            st.rerun()

    if st.session_state.mood_records:
        for i, r in enumerate(st.session_state.mood_records):
            cols = st.columns([3.1, 1.0])
            with cols[0]:
                st.write(f"- {r['text']}  ({r['ts']})")
            with cols[1]:
                if st.button("삭제", key=f"del_mood_{i}"):
                    st.session_state.mood_records.pop(i)
                    st.rerun()

    st.divider()
    st.subheader("🚫 확실히 피하기(강제)")
    banned_text = st.text_input("금지 키워드(쉼표)", value=",".join(st.session_state.prefs.get("banned_keywords", [])))
    banned_manual = [x.strip() for x in banned_text.split(",") if x.strip()]

    st.divider()
    st.subheader("📅 추천 날짜 & 일정(TPO)")
    target_date = st.date_input("추천 날짜", value=dt.date.today())
    target_key = date_key(target_date)

    tpo_mode = st.radio("일정 입력 방식", ["캘린더 연동(선택)", "앱에서 직접 입력"], index=0)

    tpo_tags: List[str] = ["casual"]
    tpo_summary_text = ""

    if tpo_mode.startswith("캘린더"):
        ics_file = st.file_uploader("ICS 업로드(.ics)", type=["ics"])
        ics_url = st.text_input("iCal(ICS) URL", value="", placeholder="https://.../calendar.ics")
        events: List[EventTPO] = []
        if ics_file is not None:
            events = parse_ics_minimal(ics_file.getvalue(), target_date)
        elif ics_url.strip():
            ok, b = fetch_ics_from_url(ics_url.strip())
            if ok:
                events = parse_ics_minimal(b, target_date)

        if events:
            chosen = events[0]
            tpo_tags = chosen.tags
            tpo_summary_text = chosen.title
            st.success(f"자동 반영: {chosen.title}")
        else:
            st.info("일정 없음 → casual")
            tpo_tags = ["casual"]
            tpo_summary_text = ""
    else:
        st.session_state.manual_events_by_date.setdefault(target_key, [])
        with st.form("add_manual_event", clear_on_submit=True):
            title = st.text_input("일정 제목", placeholder="예: 데이트 / 팀 발표 / 헬스장")
            time = st.text_input("시간(선택)", placeholder="예: 19:00")
            ok = st.form_submit_button("일정 추가")
            if ok and title.strip():
                st.session_state.manual_events_by_date[target_key].append({"title": title.strip(), "time": time.strip()})
                st.rerun()

        todays = st.session_state.manual_events_by_date.get(target_key, [])
        if todays:
            combined = " ".join([ev["title"] for ev in todays])
            tpo_tags = infer_tpo_tags(combined)
            tpo_summary_text = combined[:80] + ("…" if len(combined) > 80 else "")
            st.success("TPO 자동 반영: " + ", ".join(tpo_tags))
        else:
            st.info("일정 없음 → casual")
            tpo_tags = ["casual"]
            tpo_summary_text = ""

    st.divider()
    st.subheader("🌦️ 날씨")
    city = st.text_input("도시", value=get_default_city())
    ow_key = get_openweather_key().strip()
    if ow_key:
        weather_mode = st.radio("날씨 모드", ["자동(OpenWeather)", "수동"], index=0)
    else:
        weather_mode = "수동"
        st.info("OpenWeather 키가 없어 수동 입력만 가능해요.")

    m_temp = st.slider("기온(℃)", -20, 45, 16)
    m_feels = st.slider("체감(℃)", -20, 45, 15)
    m_hum = st.slider("습도(%)", 0, 100, 50)
    m_wind = st.slider("바람(m/s)", 0.0, 20.0, 1.5, step=0.1)
    m_rain = st.selectbox("강수", ["없음", "비/눈 가능"], index=0)
    m_desc = st.text_input("날씨 설명", value="맑음")

    weather_err = None
    if weather_mode.startswith("자동"):
        ok, payload = fetch_openweather(city, ow_key)
        if ok:
            weather = payload["weather"]
            st.success("날씨 자동 연동 성공 ✅")
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


# ---------------------
# Rebuild profile every run
# ---------------------
st.session_state.prefs = rebuild_profile(
    st.session_state.prefs,
    st.session_state.mood_records,
    st.session_state.messages,
    banned_manual,
)


# =========================================================
# Dialog: wardrobe edit (사진 추가/교체/삭제 + 모든 필드 수정)
# =========================================================
def wardrobe_item_defaults(cat: str) -> Dict:
    d = {"name": "", "tags": []}
    if cat in ("tops", "bottoms", "outer"):
        d["warmth"] = 3.0
    if cat in ("outer", "shoes"):
        d["rain_ok"] = False
    return d


def open_edit_dialog(cat: str, idx: int):
    st.session_state.edit_target = {"cat": cat, "idx": idx}


@st.dialog("옷 수정하기")
def edit_dialog():
    tgt = st.session_state.get("edit_target")
    if not tgt:
        st.write("편집 대상이 없습니다.")
        return

    cat = tgt["cat"]
    idx = tgt["idx"]
    wardrobe = st.session_state.wardrobe

    # 방어
    if cat not in wardrobe or idx < 0 or idx >= len(wardrobe[cat]):
        st.error("편집 대상이 유효하지 않아요.")
        st.session_state.edit_target = None
        return

    it = wardrobe[cat][idx]
    it = {**wardrobe_item_defaults(cat), **it}

    # 현재 사진
    st.markdown(f"**카테고리:** `{cat}`  |  **항목:** `{it.get('name','')}`")
    if it.get("image_b64"):
        try:
            st.image(b64_to_bytes(it["image_b64"]), caption="현재 사진", use_container_width=True)
        except Exception:
            st.warning("현재 사진 표시 실패")

    # 편집 폼 (dialog 내부라 상태가 더 안정적)
    new_name = st.text_input("이름", value=str(it.get("name", "")), key=f"ed_name_{cat}_{idx}")
    new_tags_raw = st
::contentReference[oaicite:0]{index=0}
.text_input(
        "태그(쉼표로 구분)",
        value=",".join(it.get("tags", [])),
        key=f"ed_tags_{cat}_{idx}",
    )

    new_warmth = None
    if cat in ("tops", "bottoms", "outer"):
        new_warmth = st.slider(
            "보온도(warmth)",
            0.0, 7.0,
            float(it.get("warmth", 3.0)),
            0.5,
            key=f"ed_warmth_{cat}_{idx}",
        )

    new_rain_ok = None
    if cat in ("outer", "shoes"):
        new_rain_ok = st.checkbox(
            "비/눈 OK",
            value=bool(it.get("rain_ok", False)),
            key=f"ed_rainok_{cat}_{idx}",
        )

    st.markdown("#### 📷 사진")
    remove_photo = st.checkbox("현재 사진 삭제", value=False, key=f"ed_remove_{cat}_{idx}")
    new_photo = st.file_uploader(
        "새 사진 업로드(선택) — 업로드하면 교체/추가됩니다",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"ed_upl_{cat}_{idx}",
    )

    col1, col2 = st.columns(2)
    with col1:
        save = st.button("💾 저장", use_container_width=True)
    with col2:
        cancel = st.button("취소", use_container_width=True)

    if cancel:
        st.session_state.edit_target = None
        st.rerun()

    if save:
        if not new_name.strip():
            st.error("이름은 비울 수 없어요.")
            return

        it["name"] = new_name.strip()
        it["tags"] = [x.strip() for x in new_tags_raw.split(",") if x.strip()]

        if new_warmth is not None:
            it["warmth"] = float(new_warmth)
        if new_rain_ok is not None:
            it["rain_ok"] = bool(new_rain_ok)

        # 사진 처리: 업로드가 우선, 그다음 삭제
        b64, mime = imgfile_to_b64(new_photo)
        if b64:
            it["image_b64"] = b64
            it["image_mime"] = mime
        elif remove_photo:
            it.pop("image_b64", None)
            it.pop("image_mime", None)

        # 저장 반영
        st.session_state.wardrobe[cat][idx] = it
        st.session_state.wardrobe = normalize_wardrobe(st.session_state.wardrobe)

        # dialog 닫기
        st.session_state.edit_target = None
        st.success("수정 완료!")
        st.rerun()


# =========================================================
# Pages
# =========================================================
if st.session_state.page == "오늘 추천":
    st.subheader("오늘의 추천 코디")
    outfit, reasons, color_plan = build_outfit(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)

    st.write(
        f"**날짜:** {date_key(target_date)}  |  **도시:** {weather.city}  |  **날씨:** {weather.desc}  |  "
        f"**체감:** {weather.feels_c:.1f}℃ ({temp_band(weather.feels_c)})"
    )
    if tpo_summary_text:
        st.write(f"**일정 요약:** {tpo_summary_text}")
    st.write(f"**TPO:** {', '.join(tpo_tags)}")

    st.markdown("### 🎨 추천 컬러")
    st.write(
        f"- 베이스: **{pretty_color_name(color_plan['base'])}**\n"
        f"- 포인트: **{pretty_color_name(color_plan['accent'])}**\n"
        f"- 하의 톤 힌트: **{pretty_color_name(color_plan['bottom_hint'])}**\n"
        f"- 신발 톤 힌트: **{pretty_color_name(color_plan['shoe_hint'])}**"
    )

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
        st.write(", ".join(outfit["extras"]))

    st.divider()
    if st.button("❤️ 이 코디 저장", use_container_width=True):
        k = date_key(target_date)
        payload = make_favorite_payload(target_date, outfit, weather, tpo_tags, tpo_summary_text, reasons, color_plan)
        st.session_state.saved_outfits.setdefault(k, [])
        st.session_state.saved_outfits[k].insert(0, payload)
        st.success("저장 완료! (저장한 코디 탭에서 확인 가능)")

    st.subheader("추천 이유")
    for r in reasons:
        st.write(f"- {r}")

    st.divider()
    user_text = st.chat_input("수정사항을 자유롭게 써줘! (예: ‘블랙은 빼줘’, ‘좀 더 단정하게’, ‘다르게 해줘’)")
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        st.session_state.messages.append({"role": "assistant", "content": "반영했어! 위 코디를 다시 계산할게."})
        st.rerun()

    st.subheader("💬 채팅")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])


elif st.session_state.page == "저장한 코디":
    st.subheader("❤️ 저장한 코디")
    if not st.session_state.saved_outfits:
        st.info("아직 저장한 코디가 없어요.")
    else:
        dates = sorted(st.session_state.saved_outfits.keys(), reverse=True)
        sel = st.selectbox("날짜 선택", dates, index=0)
        items = st.session_state.saved_outfits.get(sel, [])
        st.write(f"총 {len(items)}개 저장됨")

        for i, p in enumerate(items):
            with st.container(border=True):
                st.write(f"**저장 시각:** {p.get('saved_at','-')}")
                st.write(f"**TPO:** {', '.join(p.get('tpo_tags', []))}")
                st.write("**코디:** " + outfit_summary_text(p))
                if st.button("🗑️ 삭제", key=f"del_fav_{sel}_{i}"):
                    st.session_state.saved_outfits[sel].pop(i)
                    if not st.session_state.saved_outfits[sel]:
                        del st.session_state.saved_outfits[sel]
                    st.rerun()

        st.divider()
        export_json = json.dumps(st.session_state.saved_outfits, ensure_ascii=False, indent=2)
        st.download_button("저장한 코디 JSON 다운로드", export_json.encode("utf-8"), "saved_outfits.json", "application/json")


elif st.session_state.page == "옷장 관리":
    st.subheader("옷장 관리")
    st.caption("✅ 목록에서 ‘수정’ 누르면 편집창(dialog)이 뜨고, 사진 포함 전부 바꿀 수 있어요.")

    w = st.session_state.wardrobe

    # ---- add
    st.markdown("### ➕ 옷 추가")
    with st.form("add_item_form", clear_on_submit=True):
        category = st.selectbox("카테고리", ["tops", "bottoms", "outer", "shoes", "extras"])
        name = st.text_input("이름", placeholder="예: 그레이 후드티")
        tags_text = st.text_input("태그(쉼표)", placeholder="예: casual,street,cozy,clean,minimal,black")

        warmth = 3.0
        rain_ok = False
        if category in ("tops", "bottoms", "outer"):
            warmth = st.slider("보온도(warmth)", 0.0, 7.0, 3.0, step=0.5)
        if category in ("outer", "shoes"):
            rain_ok = st.checkbox("비/눈 OK", value=False)

        photo = st.file_uploader("옷 사진 업로드(선택)", type=["png", "jpg", "jpeg", "webp"])
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

                b64, mime = imgfile_to_b64(photo)
                if b64:
                    item["image_b64"] = b64
                    item["image_mime"] = mime

                w[category].append(item)
                st.session_state.wardrobe = normalize_wardrobe(w)
                st.success("추가 완료!")
                st.rerun()

    st.divider()

    # ---- list with edit/delete
    st.markdown("### 📦 내 옷 목록 (수정/삭제)")
    for cat in ["tops", "bottoms", "outer", "shoes", "extras"]:
        st.markdown(f"#### {cat}")
        if not w.get(cat):
            st.write("— 비어있음 —")
            continue

        for idx, it in enumerate(w[cat]):
            with st.container(border=True):
                cols = st.columns([1.2, 3.2, 1.0, 1.0])
                with cols[0]:
                    if it.get("image_b64"):
                        try:
                            st.image(b64_to_bytes(it["image_b64"]), use_container_width=True)
                        except Exception:
                            st.write("이미지 표시 실패")
                    else:
                        st.write("📷 없음")
                with cols[1]:
                    st.write(f"**{it.get('name','')}**")
                    st.write("tags:", ", ".join(it.get("tags", [])) if it.get("tags") else "-")
                    if "warmth" in it:
                        st.write(f"warmth: {it.get('warmth')}")
                    if "rain_ok" in it:
                        st.write(f"rain_ok: {it.get('rain_ok')}")
                with cols[2]:
                    if st.button("✏️ 수정", key=f"edit_{cat}_{idx}"):
                        open_edit_dialog(cat, idx)
                        st.rerun()
                with cols[3]:
                    if st.button("🗑️ 삭제", key=f"del_{cat}_{idx}"):
                        w[cat].pop(idx)
                        st.session_state.wardrobe = normalize_wardrobe(w)
                        st.rerun()

    st.divider()
    st.markdown("### 💾 옷장 백업/복원(JSON)")
    export_json = json.dumps(st.session_state.wardrobe, ensure_ascii=False, indent=2)
    st.download_button("옷장 JSON 다운로드", export_json.encode("utf-8"), "wardrobe.json", "application/json")

    uploaded = st.file_uploader("옷장 JSON 업로드(복원)", type=["json"])
    if uploaded is not None:
        try:
            restored = json.loads(uploaded.getvalue().decode("utf-8", errors="ignore"))
            st.session_state.wardrobe = normalize_wardrobe(restored)
            st.success("복원 완료!")
            st.rerun()
        except Exception as e:
            st.error(f"복원 실패: {e}")

    if st.button("샘플 옷장으로 초기화"):
        st.session_state.wardrobe = default_wardrobe()
        st.success("초기화 완료")
        st.rerun()

    # dialog open trigger
    if st.session_state.edit_target is not None:
        edit_dialog()


elif st.session_state.page == "구매 추천":
    st.subheader("사면 좋은(없는) 옷 추천")
    recs = suggest_missing_items(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)

    st.write(
        f"기준: **{date_key(target_date)}**, 체감 **{weather.feels_c:.1f}℃({temp_band(weather.feels_c)})**, "
        f"강수 **{'있음' if weather.rain else '없음'}**, TPO **{', '.join(tpo_tags)}**"
    )
    for r in recs:
        with st.container(border=True):
            st.markdown(f"### 🛍️ {r['name']}")
            st.write(f"- 추천 이유: {r['why']}")


with st.expander("🔎 디버그"):
    st.write("edit_target:", st.session_state.get("edit_target"))
    st.write("OpenWeather key present:", bool(get_openweather_key().strip()))
    st.write("DEFAULT_CITY:", get_default_city())
