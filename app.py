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


def get_openai_key() -> str:
    # 사용자가 사이드바에서 입력한 키 우선, 없으면 secrets/env
    if st.session_state.get("openai_api_key"):
        return str(st.session_state.openai_api_key).strip()
    return get_secret("OPENAI_API_KEY", "")


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


# =========================
# Wardrobe defaults & storage
# =========================
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
            {"name": "자켓(블레이저)", "tags": ["formal", "smart", "clean"], "warmth": 3},
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


# =========================
# Free-text preference (Style DNA) + color extraction
# =========================
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

NEGATION_HINTS = ["빼", "제외", "싫", "말고", "no ", "not "]


def extract_signals_from_style_dna(style_dna: str) -> Dict[str, List[str]]:
    s = (style_dna or "").lower()
    prefer, avoid = [], []
    prefer_colors, avoid_colors = [], []

    for word, _ in re.findall(r"([가-힣a-z0-9]+)\s*(빼|제외|싫어|말고)", s):
        if len(word) >= 2:
            avoid.append(word)

    for label, kws in STYLE_KEYWORDS.items():
        if any(k.lower() in s for k in kws):
            if any(h in s for h in NEGATION_HINTS):
                avoid.append(label)
            else:
                prefer.append(label)

    for key, kws in COLOR_KEYWORDS.items():
        hit = any(k.lower() in s for k in [x.lower() for x in kws])
        if not hit:
            continue
        if any(h in s for h in ["빼", "제외", "싫", "말고"]):
            avoid_colors.append(key)
        else:
            prefer_colors.append(key)

    return {
        "prefer_signals": list(dict.fromkeys(prefer)),
        "avoid_signals": list(dict.fromkeys(avoid)),
        "prefer_colors": list(dict.fromkeys(prefer_colors)),
        "avoid_colors": list(dict.fromkeys(avoid_colors)),
    }


def update_style_dna_with_text(text: str, prefs: Dict) -> Dict:
    text = (text or "").strip()
    if not text:
        return prefs

    dna = prefs.get("style_dna", "")
    dna = (dna + "\n" + text).strip() if dna else text
    prefs["style_dna"] = dna[-1600:]
    prefs["signals"] = extract_signals_from_style_dna(prefs["style_dna"])

    s = text.lower()
    for word, _ in re.findall(r"([가-힣a-z0-9]+)\s*(빼|제외|싫어|말고)", s):
        if len(word) >= 2:
            prefs["banned_keywords"] = list(dict.fromkeys(prefs.get("banned_keywords", []) + [word]))

    return prefs


# =========================
# Outfit engine + Diversity + Color recommendation
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


def apply_diversity_penalty(item_name: str, recent: List[str], strength: float) -> float:
    if not item_name:
        return 0.0
    if item_name in recent:
        idx = recent.index(item_name)
        return -(strength * (1.0 - (idx / max(1, len(recent)))))
    return 0.0


def score_item(item: Dict, wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> float:
    score = 0.0
    name = str(item.get("name", "")).lower()
    tags = item.get("tags", [])
    warmth = float(item.get("warmth", 0.0))

    for t in wanted_tags:
        if t in tags:
            score += 2.0

    signals = prefs.get("signals", {})
    for p in signals.get("prefer_signals", []):
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
            score += 1.2
        if p.lower() in name:
            score += 0.5

    for a in signals.get("avoid_signals", []):
        if a.lower() in name:
            score -= 2.0

    if weather.rain:
        if item.get("rain_ok", False) or category not in ("outer", "shoes"):
            score += 0.6
        else:
            score -= 1.0

    if category in ("tops", "bottoms", "outer"):
        ideal = ideal_warmth(weather.feels_c, prefs.get("warmth_bias", 0.0))
        score += max(0.0, 2.5 - abs(warmth - ideal))

    for b in prefs.get("banned_keywords", []):
        if b.lower() in name:
            score -= 6.0

    return score


def rank_items(items: List[Dict], wanted_tags: List[str], prefs: Dict, weather: Weather, category: str) -> List[Tuple[float, Dict]]:
    ranked = [(score_item(it, wanted_tags, prefs, weather, category), it) for it in items]
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


def pick_with_diversity(
    items: List[Dict],
    wanted_tags: List[str],
    prefs: Dict,
    weather: Weather,
    category: str,
    recent_items: List[str],
    strength: float,
) -> Optional[Dict]:
    if not items:
        return None
    ranked = rank_items(items, wanted_tags, prefs, weather, category)

    best_score, best_item = None, None
    for base_score, it in ranked[:8]:
        nm = str(it.get("name", ""))
        score = base_score + apply_diversity_penalty(nm, recent_items, strength)
        if best_score is None or score > best_score:
            best_score, best_item = score, it
    return best_item or ranked[0][1]


def update_pick_history(prefs: Dict, outfit: Dict, max_len: int = 10) -> Dict:
    if "recent_picks" not in prefs or not isinstance(prefs["recent_picks"], dict):
        prefs["recent_picks"] = {"tops": [], "bottoms": [], "outer": [], "shoes": []}

    def push(cat: str, item: Optional[Dict]):
        if not item:
            return
        name = str(item.get("name", ""))
        if not name:
            return
        lst = prefs["recent_picks"].get(cat, [])
        if name in lst:
            lst.remove(name)
        lst.insert(0, name)
        prefs["recent_picks"][cat] = lst[:max_len]

    push("tops", outfit.get("top"))
    push("bottoms", outfit.get("bottom"))
    push("outer", outfit.get("outer"))
    push("shoes", outfit.get("shoes"))
    return prefs


def recommend_colors(weather: Weather, tpo_tags: List[str], prefs: Dict) -> Dict[str, str]:
    signals = prefs.get("signals", {})
    prefer_colors = signals.get("prefer_colors", [])
    avoid_colors = set(signals.get("avoid_colors", []))

    if prefer_colors:
        filtered = [c for c in prefer_colors if c not in avoid_colors]
        if filtered:
            base = filtered[0]
            accent = filtered[1] if len(filtered) > 1 else "neutral"
        else:
            base, accent = "neutral", "navy"
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
        if "date" in tpo_tags:
            if base in ("navy", "gray"):
                accent = "pink"

    shoe = "black" if "black" not in avoid_colors else "navy"
    bottom = "dark" if weather.rain else ("navy" if base == "white" else "gray")

    return {"base": base, "accent": accent, "bottom_hint": bottom, "shoe_hint": shoe}


def pretty_color_name(c: str) -> str:
    mapping = {
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
    return mapping.get(c, c)


def build_outfit(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> Tuple[Dict, List[str], Dict[str, str]]:
    wanted = list(dict.fromkeys(tpo_tags))
    diversity_strength = float(prefs.get("diversity_strength", 1.5))

    history = prefs.get("recent_picks", {})
    top = pick_with_diversity(wardrobe["tops"], wanted, prefs, weather, "tops", history.get("tops", []), diversity_strength)
    bottom = pick_with_diversity(wardrobe["bottoms"], wanted, prefs, weather, "bottoms", history.get("bottoms", []), diversity_strength)
    shoes = pick_with_diversity(wardrobe["shoes"], wanted, prefs, weather, "shoes", history.get("shoes", []), diversity_strength)

    need_outer = weather.feels_c <= 16 or weather.rain or weather.wind_ms >= 7
    outer = pick_with_diversity(wardrobe["outer"], wanted, prefs, weather, "outer", history.get("outer", []), diversity_strength) if need_outer else None

    extras = []
    if weather.rain:
        extras.append({"name": "우산"})
    if weather.feels_c <= 8:
        extras.append({"name": "머플러"})

    outfit = {"top": top, "bottom": bottom, "outer": outer, "shoes": shoes, "extras": extras}
    color_plan = recommend_colors(weather, tpo_tags, prefs)

    reasons = []
    reasons.append(f"체감온도 **{weather.feels_c:.1f}℃({temp_band(weather.feels_c)})** 기준으로 레이어링/보온을 맞췄어요.")
    if weather.rain:
        reasons.append("비/눈 가능성이 있어 **우산/레인 대응**을 우선했어요.")
    reasons.append(f"TPO(**{', '.join(tpo_tags)}**)를 반영했어요.")
    if prefs.get("style_dna"):
        reasons.append(f"자유 입력 무드(기록): “{prefs['style_dna'][:120]}{'…' if len(prefs['style_dna'])>120 else ''}”")
    if prefs.get("banned_keywords"):
        reasons.append(f"피하고 싶은 키워드(**{', '.join(prefs['banned_keywords'])}**)는 제외했어요.")
    reasons.append(f"반복 방지를 위해 **다양성 강도({diversity_strength})**를 적용했어요.")
    reasons.append(f"추천 컬러: **{pretty_color_name(color_plan['base'])} 베이스 + {pretty_color_name(color_plan['accent'])} 포인트**")

    return outfit, reasons, color_plan


# =========================
# Shopping recommendations (+ mood input)
# =========================
def wardrobe_has_item_like(wardrobe: Dict, category: str, keywords: List[str] = None) -> bool:
    keywords = keywords or []
    for it in wardrobe.get(category, []):
        name = str(it.get("name", "")).lower()
        if any(k.lower() in name for k in keywords):
            return True
    return False


def suggest_missing_items(wardrobe: Dict, weather: Weather, tpo_tags: List[str], prefs: Dict) -> List[Dict]:
    recs: List[Dict] = []
    signals = prefs.get("signals", {})
    is_minimal = any(x in signals.get("prefer_signals", []) for x in ["미니멀", "클린"])

    if weather.rain:
        if not wardrobe_has_item_like(wardrobe, "outer", keywords=["방수", "레인", "우비"]):
            recs.append({"name": "방수 바람막이/레인 재킷", "why": "비 오는 날 ‘편함’ 체감이 커요."})
        if not wardrobe_has_item_like(wardrobe, "shoes", keywords=["방수", "레인", "부츠"]):
            recs.append({"name": "방수 신발(레인부츠/방수 스니커즈)", "why": "젖는 스트레스를 확 줄여줘요."})

    if weather.feels_c <= 8 and not wardrobe_has_item_like(wardrobe, "outer", keywords=["패딩", "코트"]):
        recs.append({"name": "따뜻한 아우터(패딩/롱코트)", "why": "추운 날 코디 고민을 크게 줄여줘요."})

    if any(t in tpo_tags for t in ["formal", "smart"]):
        if not wardrobe_has_item_like(wardrobe, "outer", keywords=["블레이저", "자켓"]):
            recs.append({"name": "기본 블레이저", "why": "발표/미팅/면접의 전천후 아이템이에요."})
        if not wardrobe_has_item_like(wardrobe, "tops", keywords=["셔츠"]):
            recs.append({"name": "기본 셔츠(화이트/라이트블루)", "why": "세미포멀에서 실패 확률이 낮아요."})

    if is_minimal:
        if not wardrobe_has_item_like(wardrobe, "bottoms", keywords=["슬랙스"]):
            recs.append({"name": "미니멀 기본 슬랙스(다크 톤)", "why": "클린/미니멀 무드에서 활용도 최고예요."})

    color_plan = recommend_colors(weather, tpo_tags, prefs)
    recs.append({
        "name": f"컬러 방향: {pretty_color_name(color_plan['base'])} 베이스 + {pretty_color_name(color_plan['accent'])} 포인트",
        "why": "오늘 조건(날씨/TPO/무드)에서 실패 확률이 낮은 팔레트예요.",
    })

    seen, out = set(), []
    for r in recs:
        if r["name"] not in seen:
            out.append(r); seen.add(r["name"])
    return out


# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="OOTD (OpenAI 키 입력)", page_icon="👕", layout="wide")
st.title("👕 오늘의 OOTD (자유 텍스트 선호 기록 + 색 추천 + 다양성 + OpenAI 키 입력)")
st.caption("OpenAI 키는 사이드바에서 입력/저장(세션). 지금은 호출 안 하고 다음 단계에 연결합니다.")

# Init state
if "page" not in st.session_state:
    st.session_state.page = "오늘 추천"

if "openai_api_key" not in st.session_state:
    st.session_state.openai_api_key = ""

if "wardrobe" not in st.session_state:
    st.session_state.wardrobe = default_wardrobe()
st.session_state.wardrobe = normalize_wardrobe(st.session_state.wardrobe)

if "prefs" not in st.session_state:
    st.session_state.prefs = {
        "banned_keywords": [],
        "avoid_shoes": [],
        "warmth_bias": 0.0,
        "style_dna": "",
        "signals": {"prefer_signals": [], "avoid_signals": [], "prefer_colors": [], "avoid_colors": []},
        "diversity_strength": 1.5,
        "recent_picks": {"tops": [], "bottoms": [], "outer": [], "shoes": []},
    }

if "messages" not in st.session_state:
    st.session_state.messages = []

if "manual_events" not in st.session_state:
    st.session_state.manual_events = []


# -------------------------
# Sidebar
# -------------------------
with st.sidebar:
    st.header("🔐 API 키")
    # ✅ OpenAI 키 입력 칸(세션 저장)
    st.session_state.openai_api_key = st.text_input(
        "OpenAI API Key (세션 저장)",
        value=st.session_state.openai_api_key,
        type="password",
        placeholder="sk-... (앱 재시작하면 초기화됨)",
        help="여기에 넣은 키는 st.session_state에만 저장됩니다. 다음 단계에서 OpenAI 호출에 사용합니다.",
    )
    if get_openai_key():
        st.success("OpenAI 키: 입력됨(세션)")
    else:
        st.info("OpenAI 키: 아직 없음")

    st.divider()
    st.header("메뉴")
    st.session_state.page = st.radio(
        "이동",
        ["오늘 추천", "옷장 관리", "구매 추천"],
        index=["오늘 추천", "옷장 관리", "구매 추천"].index(st.session_state.page),
    )

    st.divider()
    st.subheader("📅 일정(TPO) 입력 방식")
    target_date = st.date_input("추천 날짜", value=dt.date.today())
    tpo_mode = st.radio("일정 가져오기", ["캘린더 연동(선택)", "앱에서 직접 입력"], index=0)

    tpo_tags: List[str] = ["casual"]
    tpo_summary_text = ""

    if tpo_mode.startswith("캘린더"):
        st.caption("외부 패키지 없이: ① ICS 업로드 또는 ② iCal(ICS) 공개 URL")
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
        if chosen_event:
            tpo_tags = chosen_event.tags
            tpo_summary_text = chosen_event.title
            st.success(f"자동 반영: {chosen_event.title}")
            st.write(f"TPO: {', '.join(tpo_tags)}")
        else:
            st.info("해당 날짜 일정이 감지되지 않아 기본 TPO(casual)로 진행합니다.")
            tpo_tags = ["casual"]
            tpo_summary_text = ""

    else:
        st.caption("앱에서 오늘 일정(여러 개) 추가 → 자동으로 TPO 추론")
        with st.form("add_manual_event", clear_on_submit=True):
            title = st.text_input("일정 제목", placeholder="예: 저녁 데이트 / 팀 발표 / 헬스장")
            time = st.text_input("시간(선택)", placeholder="예: 19:00")
            ok = st.form_submit_button("일정 추가")
            if ok:
                if title.strip():
                    st.session_state.manual_events.append({"title": title.strip(), "time": time.strip()})
                else:
                    st.warning("일정 제목을 입력해주세요.")

        if st.session_state.manual_events:
            st.write("등록된 일정(오늘):")
            for i, ev in enumerate(st.session_state.manual_events):
                cols = st.columns([3.2, 1.2, 1.0])
                with cols[0]:
                    st.write(f"- {ev['title']}" + (f" ({ev['time']})" if ev["time"] else ""))
                with cols[2]:
                    if st.button("삭제", key=f"rm_ev_{i}"):
                        st.session_state.manual_events.pop(i)
                        st.rerun()

            combined = " ".join([ev["title"] for ev in st.session_state.manual_events])
            tpo_tags = infer_tpo_tags(combined)
            tpo_summary_text = combined[:80] + ("…" if len(combined) > 80 else "")
            st.success(f"TPO 자동 반영: {', '.join(tpo_tags)}")
        else:
            st.info("일정이 없으면 기본 TPO(casual)로 진행합니다.")
            tpo_tags = ["casual"]
            tpo_summary_text = ""

    st.divider()
    st.subheader("🌦️ 날씨")
    city = st.text_input("도시", value=get_default_city())
    api_key = get_openweather_key().strip()
    auto_available = bool(api_key)

    if auto_available:
        weather_mode = st.radio("날씨 모드", ["자동(OpenWeather)", "수동"], index=0)
    else:
        st.info("OPENWEATHER_API_KEY가 없어 수동 날씨 입력만 가능합니다.")
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
    st.subheader("🚫 확실히 피하기")
    banned_text = st.text_input(
        "피하고 싶은 키워드(쉼표)",
        value=",".join(st.session_state.prefs.get("banned_keywords", [])),
    )
    st.session_state.prefs["banned_keywords"] = [x.strip() for x in banned_text.split(",") if x.strip()]

    st.divider()
    st.subheader("🎲 다양성(반복 방지)")
    st.session_state.prefs["diversity_strength"] = st.slider(
        "다양성 강도",
        0.0, 3.0,
        float(st.session_state.prefs.get("diversity_strength", 1.5)),
        0.1,
    )
    if st.button("추천 히스토리 초기화"):
        st.session_state.prefs["recent_picks"] = {"tops": [], "bottoms": [], "outer": [], "shoes": []}
        st.success("초기화 완료")


# =========================
# Page: Today (chat-first fix)
# =========================
if st.session_state.page == "오늘 추천":
    pending_text = st.chat_input("원하는 스타일/무드/색/수정사항을 자유롭게 써줘… (기록으로 남아요)")
    if pending_text:
        st.session_state.messages.append({"role": "user", "content": pending_text})
        st.session_state.prefs = update_style_dna_with_text(pending_text, st.session_state.prefs)
        st.session_state.messages.append({"role": "assistant", "content": "반영했어! 위쪽 추천이 새로 계산돼서 바뀔 거야."})
        st.rerun()

    outfit, reasons, color_plan = build_outfit(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)
    st.session_state.prefs = update_pick_history(st.session_state.prefs, outfit)

    st.subheader("오늘의 추천 코디")
    st.write(
        f"**도시:** {weather.city}  |  **날씨:** {weather.desc}  |  "
        f"**체감:** {weather.feels_c:.1f}℃ ({temp_band(weather.feels_c)})"
    )
    if tpo_summary_text:
        st.write(f"**일정 요약:** {tpo_summary_text}")
    st.write(f"**TPO 자동 반영:** {', '.join(tpo_tags)}")

    st.markdown("### 🎨 오늘 추천 컬러")
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
        st.write(", ".join([x["name"] for x in outfit["extras"]]))

    st.divider()
    st.subheader("왜 이렇게 추천했나요?")
    for r in reasons:
        st.write(f"- {r}")

    st.divider()
    st.subheader("💬 채팅 기록")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    with st.expander("현재 누적된 스타일/무드 기록(Style DNA)"):
        st.write(st.session_state.prefs.get("style_dna", "") or "아직 없음")
        st.write("추출된 신호:", st.session_state.prefs.get("signals", {}))

    st.info("✅ OpenAI 키를 넣어두면, 다음 단계에서 이 기록(Style DNA) + 날씨 + TPO를 기반으로 LLM 추천을 바로 붙일 수 있어요.")


# =========================
# Page: Wardrobe management
# =========================
elif st.session_state.page == "옷장 관리":
    st.subheader("옷장 관리")
    st.caption("내 옷을 등록/삭제하고 JSON으로 백업/복원할 수 있어요.")

    w = st.session_state.wardrobe

    st.markdown("### ➕ 옷 추가")
    with st.form("add_item_form", clear_on_submit=True):
        category = st.selectbox("카테고리", ["tops", "bottoms", "outer", "shoes", "extras"])
        name = st.text_input("이름", placeholder="예: 그레이 후드티")
        tags_text = st.text_input("태그(쉼표)", placeholder="예: casual,street,cozy,clean,minimal")
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
                st.session_state.wardrobe = normalize_wardrobe(w)
                st.success("추가 완료!")
                st.rerun()

    st.divider()
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
                st.write(f"warmth: {it['warmth']}" if "warmth" in it else "")
            with cols[3]:
                if st.button("삭제", key=f"del_{cat}_{idx}"):
                    w[cat].pop(idx)
                    st.session_state.wardrobe = normalize_wardrobe(w)
                    st.rerun()

    st.divider()
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
            st.success("복원 완료!")
            st.rerun()
        except Exception as e:
            st.error(f"복원 실패: {e}")

    if st.button("샘플 옷장으로 초기화"):
        st.session_state.wardrobe = default_wardrobe()
        st.success("초기화 완료")
        st.rerun()


# =========================
# Page: Shopping recommendations (+ mood input)
# =========================
elif st.session_state.page == "구매 추천":
    st.subheader("사면 좋은(없는) 옷 추천")
    st.caption("내 옷장 + 오늘 날씨 + TPO + (추가 무드 입력)으로 ‘없으면’ 도움 되는 아이템을 추천해요.")

    st.markdown("### ✍️ 구매 추천용 무드/스타일 추가 입력(선택)")
    mood_extra = st.text_area(
        "예) ‘코지하지만 단정하게’, ‘모노톤에 포인트 하나’, ‘빈티지 무드인데 과하지 않게’",
        value="",
        height=90,
        placeholder="여기에 적고 아래 버튼을 누르면 Style DNA 기록에 추가됩니다.",
    )
    if st.button("무드 추가 반영"):
        if mood_extra.strip():
            st.session_state.prefs = update_style_dna_with_text(mood_extra.strip(), st.session_state.prefs)
            st.success("반영 완료! 추천이 업데이트됩니다.")
            st.rerun()
        else:
            st.info("입력한 내용이 없어요.")

    missing = suggest_missing_items(st.session_state.wardrobe, weather, tpo_tags, st.session_state.prefs)

    st.write(
        f"기준: **체감 {weather.feels_c:.1f}℃({temp_band(weather.feels_c)})**, "
        f"**강수 {'있음' if weather.rain else '없음'}**, "
        f"**TPO {', '.join(tpo_tags)}**"
    )

    if not missing:
        st.success("현재 조건 기준으로 강력 추천할 ‘누락 아이템’이 크게 보이지 않아요! 👍")
    else:
        for r in missing:
            with st.container(border=True):
                st.markdown(f"### 🛍️ {r['name']}")
                st.write(f"- 추천 이유: {r['why']}")

    st.divider()
    st.subheader("현재 누적된 스타일/무드 기록(Style DNA)")
    dna = st.session_state.prefs.get("style_dna", "")
    st.write(dna[:700] + ("…" if len(dna) > 700 else "") if dna else "아직 없음")
    st.write("추출된 신호:", st.session_state.prefs.get("signals", {}))

    if get_openai_key():
        st.success("OpenAI 키가 입력되어 있어요. 다음 단계에서 구매 추천을 LLM으로 고도화할 수 있어요.")
    else:
        st.info("OpenAI 키를 넣어두면 다음 단계에서 LLM 추천을 쉽게 붙일 수 있어요.")


with st.expander("🔎 디버그"):
    st.write("page:", st.session_state.page)
    st.write("openai_key_present:", bool(get_openai_key()))
    st.write("prefs:", st.session_state.prefs)
    st.write("tpo_tags:", tpo_tags)
    st.write("wardrobe:", st.session_state.wardrobe)
    st.write("manual_events:", st.session_state.manual_events)
