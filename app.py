import json
import requests
import streamlit as st
from typing import Dict, List, Tuple, Optional

from openai import OpenAI

# -----------------------------
# Page config
# -----------------------------
st.set_page_config(
    page_title="🎬 나와 어울리는 영화는?",
    page_icon="🎬",
    layout="wide",
)

# -----------------------------
# Cinema-like UI (readable, not too dark)
# -----------------------------
st.markdown(
    """
<style>
/* App background + typography */
.stApp {
  background: radial-gradient(1200px 600px at 30% 0%, #fff7e6 0%, #fffaf1 35%, #fffdf7 70%, #ffffff 100%);
  color: #1f2937;
}

/* Make the top header area breathe */
.block-container { padding-top: 2.0rem; padding-bottom: 3rem; max-width: 1100px; }

/* “Cinema” accent */
:root {
  --cinema-red: #c81d25;
  --cinema-gold: #f2c94c;
  --card: rgba(255,255,255,0.86);
  --border: rgba(17,24,39,0.10);
}

/* Title badge */
.cinema-badge {
  display: inline-flex;
  align-items: center;
  gap: .5rem;
  padding: .5rem .75rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(255,255,255,0.75);
  box-shadow: 0 10px 30px rgba(17,24,39,0.06);
  font-weight: 700;
}

/* Section card */
.section-card {
  border: 1px solid var(--border);
  background: var(--card);
  border-radius: 18px;
  padding: 1rem 1.1rem;
  box-shadow: 0 14px 40px rgba(17,24,39,0.08);
}

/* Movie card */
.movie-card {
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.90);
  border-radius: 22px;
  padding: 1rem;
  box-shadow: 0 16px 50px rgba(17,24,39,0.10);
}

/* Small label chips */
.chip {
  display:inline-flex;
  align-items:center;
  gap:.4rem;
  padding:.22rem .55rem;
  border-radius:999px;
  border: 1px solid rgba(200,29,37,0.18);
  background: rgba(200,29,37,0.06);
  color: #7f1d1d;
  font-size: .85rem;
  font-weight: 600;
}

/* Subtle divider */
hr { border: none; border-top: 1px solid rgba(17,24,39,0.08); margin: 1.2rem 0; }

/* Primary button tone (Streamlit theme-safe) */
.stButton > button[kind="primary"] {
  border-radius: 14px;
  font-weight: 700;
}
</style>
""",
    unsafe_allow_html=True,
)

# -----------------------------
# Sidebar: API keys
# -----------------------------
st.sidebar.header("🔑 API 설정")
openai_key = st.sidebar.text_input("OpenAI API Key", type="password", placeholder="OpenAI API Key")
tmdb_key = st.sidebar.text_input("TMDB API Key", type="password", placeholder="TMDB API Key")
model_name = st.sidebar.text_input("OpenAI 모델(선택)", value="gpt-5.2-mini")
st.sidebar.caption("OpenAI 키가 없으면 기본 로직으로만 추천합니다.")

# -----------------------------
# Header
# -----------------------------
st.markdown('<div class="cinema-badge">🍿 <span>Campus Cinema Test</span> <span class="chip">가독성 좋은 영화관 무드</span></div>', unsafe_allow_html=True)
st.title("🎬 나와 어울리는 영화는?")
st.write("질문에 답하면 당신의 취향을 분석해 **딱 3편만** 골라 추천해드려요. (많이 말고, 제대로!)")
st.caption("추천 결과에는 **한 줄 소개**와 **추천 이유**가 함께 나옵니다.")
st.markdown("<hr/>", unsafe_allow_html=True)

# -----------------------------
# TMDB config
# -----------------------------
POSTER_BASE = "https://image.tmdb.org/t/p/w500"

TMDB_GENRES = {
    "액션": 28,
    "코미디": 35,
    "드라마": 18,
    "SF": 878,
    "로맨스": 10749,
    "판타지": 14,
}

# group -> candidates
PREFERENCE_TO_GENRES = {
    "로맨스/드라마": ["로맨스", "드라마"],
    "액션/어드벤처": ["액션"],
    "SF/판타지": ["SF", "판타지"],
    "코미디": ["코미디"],
}

# -----------------------------
# Questions (10) - same structure
# option: "<TAG> | <TEXT>"
# -----------------------------
questions = [
    {
        "q": "Q1. 시험이 끝난 금요일 밤, 너의 선택은?",
        "options": [
            "로맨스/드라마 | 조용한 방에서 여운 남는 영화 한 편 보며 생각에 잠긴다",
            "액션/어드벤처 | 친구들이랑 극장 가서 박진감 넘치는 영화로 스트레스 날린다",
            "SF/판타지 | 세계관 탄탄한 영화 보면서 “이 설정 뭐야” 하며 몰입한다",
            "코미디 | 아무 생각 안 하고 웃긴 영화 틀어놓고 깔깔 웃는다",
        ],
    },
    {
        "q": "Q2. 영화 속 주인공으로 살 하루가 주어진다면?",
        "options": [
            "로맨스/드라마 | 사랑과 인생의 갈림길에서 고민하는 주인공",
            "액션/어드벤처 | 위기의 순간마다 몸으로 돌파하는 히어로",
            "SF/판타지 | 다른 차원이나 미래 세계를 여행하는 존재",
            "코미디 | 사고를 치지만 미워할 수 없는 문제적 인물",
        ],
    },
    {
        "q": "Q3. 영화를 보고 난 뒤, 네가 가장 중요하게 느끼는 건?",
        "options": [
            "로맨스/드라마 | 감정선과 메시지, 그리고 여운",
            "액션/어드벤처 | 액션 장면의 쾌감과 긴장감",
            "SF/판타지 | 설정의 신선함과 “와 이런 생각을?” 하는 놀라움",
            "코미디 | 얼마나 웃었는지, 기분이 가벼워졌는지",
        ],
    },
    {
        "q": "Q4. 비 오는 날, 약속이 취소됐다. 어떤 영화가 땡겨?",
        "options": [
            "로맨스/드라마 | 혼자 보기 좋은 감성적인 영화",
            "액션/어드벤처 | 집에서라도 스케일 큰 영화로 기분 전환",
            "SF/판타지 | 현실을 잠시 잊게 해주는 다른 세계 이야기",
            "코미디 | 우울함을 날려줄 웃긴 영화",
        ],
    },
    {
        "q": "Q5. 친구가 “이 영화 꼭 봐야 해”라고 추천했다. 이유는?",
        "options": [
            "로맨스/드라마 | “인생에 대해 생각하게 돼”",
            "액션/어드벤처 | “액션 미쳤어, 시간 순삭”",
            "SF/판타지 | “세계관이랑 설정이 진짜 신박해”",
            "코미디 | “진짜 웃다가 눈물 난다”",
        ],
    },
    {
        "q": "Q6. 영화 예고편을 볼 때 제일 먼저 꽂히는 건?",
        "options": [
            "로맨스/드라마 | 표정/대사/감정선이 확 끌리는 장면",
            "액션/어드벤처 | 폭발/추격/전투처럼 텐션 터지는 장면",
            "SF/판타지 | ‘이 세계는 뭐지?’ 싶은 설정/비주얼",
            "코미디 | 한 방에 웃기는 대사나 상황",
        ],
    },
    {
        "q": "Q7. 너의 여행 스타일과 가장 비슷한 영화는?",
        "options": [
            "로맨스/드라마 | 사람/관계 위주로 기억에 남는 여행",
            "액션/어드벤처 | 빡빡하게 코스 돌고 액티비티도 하는 여행",
            "SF/판타지 | 새로운 장소/전시/테마파크처럼 ‘다른 세계’ 탐험",
            "코미디 | 계획은 대충! 즉흥과 해프닝이 재미인 여행",
        ],
    },
    {
        "q": "Q8. 과제가 산더미일 때, 너의 도피 방식은?",
        "options": [
            "로맨스/드라마 | 감정 몰입되는 영화로 현실을 잠시 내려놓기",
            "액션/어드벤처 | 강한 자극으로 머리를 비우기",
            "SF/판타지 | 현실과 완전 다른 세계로 탈출하기",
            "코미디 | 웃긴 거 보면서 긴장 풀기",
        ],
    },
    {
        "q": "Q9. 친구들과 영화 취향이 다를 때, 너는?",
        "options": [
            "로맨스/드라마 | ‘좋은 이야기’면 뭐든 오케이, 감상파 설득 가능",
            "액션/어드벤처 | “재밌는 게 최고!” 스펙터클로 밀어붙인다",
            "SF/판타지 | “설정이 미쳤다” 세계관 소개부터 시작한다",
            "코미디 | 다 같이 웃을 수 있는 걸로 타협한다",
        ],
    },
    {
        "q": "Q10. 영화의 엔딩이 이렇게 끝나면 ‘최고’라고 느껴!",
        "options": [
            "로맨스/드라마 | 마음이 묵직해지거나 울컥하는 여운",
            "액션/어드벤처 | 마지막까지 긴장감 터지고 카타르시스",
            "SF/판타지 | 떡밥 회수/세계관 확장으로 뒷맛 짜릿",
            "코미디 | 끝까지 웃기고 기분 좋게 마무리",
        ],
    },
]

# -----------------------------
# Helpers
# -----------------------------
def parse_tag(choice_text: str) -> str:
    return choice_text.split("|", 1)[0].strip()

def parse_text(choice_text: str) -> str:
    return choice_text.split("|", 1)[1].strip()

def compute_preference_counts(answers: List[str]) -> Dict[str, int]:
    counts = {"로맨스/드라마": 0, "액션/어드벤처": 0, "SF/판타지": 0, "코미디": 0}
    for a in answers:
        tag = parse_tag(a)
        if tag in counts:
            counts[tag] += 1
    return counts

def fallback_pick_genres(counts: Dict[str, int]) -> Tuple[str, Optional[str]]:
    group_priority = ["SF/판타지", "액션/어드벤처", "로맨스/드라마", "코미디"]
    sorted_groups = sorted(counts.items(), key=lambda kv: (-kv[1], group_priority.index(kv[0])))
    primary_group = sorted_groups[0][0]
    secondary_group = sorted_groups[1][0] if len(sorted_groups) > 1 else None

    def rep(group: str) -> str:
        if group == "로맨스/드라마":
            return "드라마"
        if group == "액션/어드벤처":
            return "액션"
        if group == "SF/판타지":
            return "SF"
        return "코미디"

    primary = rep(primary_group)
    secondary = rep(secondary_group) if secondary_group else None
    if secondary == primary:
        secondary = None
    return primary, secondary

@st.cache_data(show_spinner=False, ttl=60 * 30)
def tmdb_discover(api_key: str, genre_id: int, page: int = 1) -> dict:
    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": api_key,
        "with_genres": genre_id,
        "language": "ko-KR",
        "sort_by": "popularity.desc",
        "page": page,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def fetch_top_movies(api_key: str, genre_name: str, n: int) -> List[dict]:
    gid = TMDB_GENRES[genre_name]
    data = tmdb_discover(api_key, gid, page=1)
    return (data.get("results") or [])[:n]

def build_poster_url(poster_path: Optional[str]) -> Optional[str]:
    if not poster_path:
        return None
    return f"{POSTER_BASE}{poster_path}"

def clamp_text(s: str, max_len: int = 140) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"

def openai_analyze(
    api_key: str,
    model: str,
    qa_pairs: List[Tuple[str, str]],
    counts: Dict[str, int],
) -> dict:
    client = OpenAI(api_key=api_key)

    qa_text = "\n".join([f"- {q} -> {parse_text(a)}" for q, a in qa_pairs])
    counts_text = ", ".join([f"{k}:{v}" for k, v in counts.items()])

    schema_hint = {
        "primary_genre": "드라마",
        "secondary_genre": "로맨스",
        "summary": "너는 감정선/여운을 챙기는 타입! 관계 중심 서사나 현실 공감 이야기에 잘 몰입해.",
        "keywords": ["여운", "감정선", "관계", "현실공감"],
    }

    prompt = f"""
너는 '영화 취향 심리테스트' 결과 분석가야. 대학생 톤으로 짧고 깔끔하게 결과를 내.

반드시 아래 JSON만 출력해(설명 문장/코드블록/마크다운 금지).
규칙:
- primary_genre: ["액션","코미디","드라마","SF","로맨스","판타지"] 중 1
- secondary_genre: 위 목록 중 1 또는 null (primary와 중복 금지)
- summary: 사용자가 '어떤 영화'를 좋아하는지 1~2문장(가볍고 영화관 안내멘트 느낌)
- keywords: 3~7개 한국어 키워드

사용자 선택 분포: {counts_text}

Q&A:
{qa_text}

예시 형식(값은 예시일 뿐):
{json.dumps(schema_hint, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(model=model, input=prompt)
    return json.loads(resp.output_text.strip())

def openai_movie_lines(
    api_key: str,
    model: str,
    profile: dict,
    movies: List[dict],
) -> Dict[int, dict]:
    """
    Return per movie:
      { movie_id: {"one_liner": "...", "why": "..."} }
    """
    client = OpenAI(api_key=api_key)

    items = []
    for m in movies:
        items.append(
            {
                "id": m.get("id"),
                "title": m.get("title"),
                "overview": clamp_text(m.get("overview") or "", 220),
                "rating": m.get("vote_average"),
            }
        )

    prompt = f"""
너는 영화 추천 큐레이터야. 아래 사용자 프로필에 맞춰,
각 영화마다 (1) 한 줄 소개(one_liner) (2) 추천 이유(why)를 만들어.

반드시 JSON 객체만 출력해.
형식:
{{
  "<movie_id>": {{
     "one_liner": "어떤 영화인지 1문장(25~55자)",
     "why": "왜 추천인지 1문장(25~55자)"
  }},
  ...
}}

사용자 프로필:
{json.dumps(profile, ensure_ascii=False)}

영화 목록:
{json.dumps(items, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(model=model, input=prompt)
    raw = json.loads(resp.output_text.strip())
    return {int(k): v for k, v in raw.items()}

# -----------------------------
# Question UI container
# -----------------------------
st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.subheader("🎟️ 질문에 답해주세요")
st.caption("각 문항은 4가지 영화 취향(로맨스/드라마, 액션/어드벤처, SF/판타지, 코미디)을 반영해요.")

answers: List[str] = []
qa_pairs: List[Tuple[str, str]] = []
for idx, item in enumerate(questions, start=1):
    choice = st.radio(item["q"], item["options"], key=f"q{idx}")
    answers.append(choice)
    qa_pairs.append((item["q"], choice))
    st.write("")

st.markdown("</div>", unsafe_allow_html=True)
st.markdown("<hr/>", unsafe_allow_html=True)

# -----------------------------
# Result button
# -----------------------------
left, right = st.columns([1, 1])
with left:
    go = st.button("결과 보기", type="primary")
with right:
    st.caption("💡 추천은 3편만 보여요. 너무 많으면 선택이 더 어려우니까!")

if go:
    if not tmdb_key:
        st.warning("사이드바에 TMDB API Key를 입력해주세요.")
        st.stop()

    counts = compute_preference_counts(answers)

    with st.spinner("분석 중... (조금만 기다려줘요)"):
        # 1) profile: OpenAI or fallback
        profile = None
        primary_genre = None
        secondary_genre = None

        if openai_key:
            try:
                profile = openai_analyze(openai_key, model_name, qa_pairs, counts)
                primary_genre = profile.get("primary_genre")
                secondary_genre = profile.get("secondary_genre")

                if primary_genre not in TMDB_GENRES:
                    primary_genre = None
                if secondary_genre not in TMDB_GENRES:
                    secondary_genre = None
                if secondary_genre == primary_genre:
                    secondary_genre = None
            except Exception as e:
                st.warning("OpenAI 분석에 실패해서 기본 로직으로 진행할게요.")
                st.caption(f"OpenAI error: {e}")

        if not primary_genre:
            primary_genre, secondary_genre = fallback_pick_genres(counts)
            profile = {
                "primary_genre": primary_genre,
                "secondary_genre": secondary_genre,
                "summary": "선택 분포를 기반으로 가장 강하게 드러난 취향을 골랐어요.",
                "keywords": [],
            }

        # 2) TMDB: only 3 movies (primary 2 + secondary 1)
        try:
            movies: List[dict] = []
            movies += fetch_top_movies(tmdb_key, primary_genre, n=3)[:2]  # 2편
            if secondary_genre:
                movies += fetch_top_movies(tmdb_key, secondary_genre, n=3)[:1]  # 1편
            else:
                # secondary 없으면 primary에서 1편 더
                movies += fetch_top_movies(tmdb_key, primary_genre, n=5)[2:3]

            # de-dup by id
            seen = set()
            uniq = []
            for m in movies:
                mid = m.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    uniq.append(m)
            movies = uniq[:3]
        except requests.HTTPError as e:
            st.error("TMDB API 요청에 실패했어요. API Key를 확인해주세요.")
            st.caption(f"TMDB HTTPError: {e}")
            st.stop()
        except Exception as e:
            st.error("TMDB 처리 중 오류가 발생했어요.")
            st.caption(str(e))
            st.stop()

        # 3) OpenAI: one-liner + why (optional)
        per_movie = {}
        if openai_key:
            try:
                per_movie = openai_movie_lines(openai_key, model_name, profile, movies)
            except Exception as e:
                st.warning("영화 소개/이유 생성에 실패했어요. 기본 문구로 표시할게요.")
                st.caption(f"OpenAI error: {e}")

    # -----------------------------
    # Result UI
    # -----------------------------
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("🎞️ 당신의 결과")
    tag = f"**{primary_genre}**" + (f" + **{secondary_genre}**" if secondary_genre else "")
    st.write(f"추천 장르: {tag}")
    st.caption(
        f"선택 분포: 로맨스/드라마 {counts['로맨스/드라마']} · "
        f"액션/어드벤처 {counts['액션/어드벤처']} · "
        f"SF/판타지 {counts['SF/판타지']} · "
        f"코미디 {counts['코미디']}"
    )

    st.write("**어떤 영화 취향이냐면:**")
    st.write(profile.get("summary", ""))

    kws = profile.get("keywords") or []
    if kws:
        st.write("**키워드:** " + " · ".join(kws))
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("🍿 오늘의 추천 3편")
    st.caption("너무 많이 추천하지 않고, 지금 바로 보기 좋은 작품만 골랐어요.")

    for m in movies:
        mid = m.get("id")
        title = m.get("title") or "제목 없음"
        rating = float(m.get("vote_average") or 0.0)
        overview = m.get("overview") or ""
        poster_url = build_poster_url(m.get("poster_path"))

        # fallback lines
        one_liner = clamp_text(overview, 60) or "한 줄 소개를 준비 중이에요."
        why = f"당신의 **{primary_genre}** 취향 포인트와 잘 맞는 인기작이라 추천해요."
        if secondary_genre:
            why = f"당신의 **{primary_genre}/{secondary_genre}** 취향을 만족시킬 가능성이 높아요."

        if isinstance(per_movie, dict) and mid in per_movie:
            one_liner = per_movie[mid].get("one_liner") or one_liner
            why = per_movie[mid].get("why") or why

        st.markdown('<div class="movie-card">', unsafe_allow_html=True)
        cols = st.columns([1, 2.2], vertical_alignment="top")
        with cols[0]:
            if poster_url:
                st.image(poster_url, use_container_width=True)
            else:
                st.write("🖼️ 포스터 없음")
        with cols[1]:
            st.markdown(f"### {title}")
            st.write(f"⭐ 평점: {rating:.1f} / 10")
            st.markdown(f"**한 줄 소개:** {one_liner}")
            st.markdown(f"**추천 이유:** {why}")

            if overview.strip():
                with st.expander("줄거리 더 보기"):
                    st.write(overview)

        st.markdown("</div>", unsafe_allow_html=True)
        st.write("")

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.caption("원하면 다음 단계에서 ‘대표 1편만 픽’ 또는 ‘장르 혼합 검색(예: SF+로맨스)’로 더 정밀하게도 만들 수 있어요.")
