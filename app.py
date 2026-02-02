import json
import requests
import streamlit as st
from typing import Dict, List, Tuple, Optional

# OpenAI Python SDK (v2+)
from openai import OpenAI

# -----------------------------
# Page config
# -----------------------------
st.set_page_config(page_title="🎬 나와 어울리는 영화는?", page_icon="🎬", layout="wide")

st.title("🎬 나와 어울리는 영화는?")
st.write("질문에 답하면, 당신의 성향을 분석해 **어울리는 영화 장르**와 **지금 인기 있는 영화 5편**을 추천해드려요! 🎥🍿")
st.caption("※ OpenAI는 '분석/추천 이유 생성'에 사용되고, 영화 데이터는 TMDB에서 가져옵니다.")
st.divider()

# -----------------------------
# Sidebar: API keys
# -----------------------------
st.sidebar.header("🔑 API 설정")
openai_key = st.sidebar.text_input("OpenAI API Key", type="password", placeholder="OpenAI API Key")
tmdb_key = st.sidebar.text_input("TMDB API Key", type="password", placeholder="TMDB API Key")
model_name = st.sidebar.text_input("OpenAI 모델(선택)", value="gpt-5.2-mini")
st.sidebar.caption("모델명은 계정/권한에 따라 다를 수 있어요.")

# -----------------------------
# TMDB config
# -----------------------------
POSTER_BASE = "https://image.tmdb.org/t/p/w500"

# 요구사항 장르 ID
TMDB_GENRES = {
    "액션": 28,
    "코미디": 35,
    "드라마": 18,
    "SF": 878,
    "로맨스": 10749,
    "판타지": 14,
}

# 4지선다(성향 그룹) -> 장르 후보(더 정교한 혼합을 위해 2개 후보를 둠)
PREFERENCE_TO_GENRES = {
    "로맨스/드라마": ["로맨스", "드라마"],
    "액션/어드벤처": ["액션"],  # 요구사항 내 ID 기준으로 액션만 사용
    "SF/판타지": ["SF", "판타지"],
    "코미디": ["코미디"],
}

# -----------------------------
# Questions (10)
# option format: "<TAG> | <TEXT>"
# TAG: 로맨스/드라마, 액션/어드벤처, SF/판타지, 코미디
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
    # 추가 질문 5개
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

def compute_preference_counts(answers: List[str]) -> Dict[str, int]:
    counts = {"로맨스/드라마": 0, "액션/어드벤처": 0, "SF/판타지": 0, "코미디": 0}
    for a in answers:
        tag = parse_tag(a)
        if tag in counts:
            counts[tag] += 1
    return counts

def fallback_pick_genres(counts: Dict[str, int]) -> Tuple[str, Optional[str]]:
    """OpenAI 없이도 동작하는 기본 로직: 최다 그룹 -> 대표 장르, 2등 그룹 -> 대표 장르"""
    # 그룹 우선순위(동점일 때)
    group_priority = ["SF/판타지", "액션/어드벤처", "로맨스/드라마", "코미디"]
    sorted_groups = sorted(counts.items(), key=lambda kv: (-kv[1], group_priority.index(kv[0])))
    primary_group = sorted_groups[0][0]
    secondary_group = sorted_groups[1][0] if len(sorted_groups) > 1 else None

    def group_to_genre(group: str) -> str:
        # 대표 장르 선택
        if group == "로맨스/드라마":
            return "드라마"  # 기본은 드라마
        if group == "액션/어드벤처":
            return "액션"
        if group == "SF/판타지":
            return "SF"
        return "코미디"

    primary = group_to_genre(primary_group)
    secondary = group_to_genre(secondary_group) if secondary_group else None
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
    genre_id = TMDB_GENRES[genre_name]
    data = tmdb_discover(api_key, genre_id, page=1)
    return (data.get("results") or [])[:n]

def build_poster_url(poster_path: Optional[str]) -> Optional[str]:
    if not poster_path:
        return None
    return f"{POSTER_BASE}{poster_path}"

def openai_analyze(
    api_key: str,
    model: str,
    qa_pairs: List[Tuple[str, str]],
    counts: Dict[str, int],
) -> dict:
    """
    Returns JSON with:
      primary_genre: one of [액션, 코미디, 드라마, SF, 로맨스, 판타지]
      secondary_genre: same or null
      reason: short korean
      keywords: [..] 3~7
    """
    client = OpenAI(api_key=api_key)

    # compact하게 QA를 텍스트로 구성
    qa_text = "\n".join([f"- {q} -> {a.split('|',1)[1].strip()}" for q, a in qa_pairs])
    counts_text = ", ".join([f"{k}:{v}" for k, v in counts.items()])

    schema_hint = {
        "primary_genre": "드라마",
        "secondary_genre": "로맨스",
        "reason": "감정선/여운을 중시하는 선택이 많고, 관계 중심 서사를 선호하는 경향이 보여요.",
        "keywords": ["여운", "감정선", "관계", "힐링"],
    }

    prompt = f"""
너는 '영화 취향 심리테스트' 결과 분석가야. 사용자의 응답을 바탕으로 가장 어울리는 영화 장르를 1~2개 고르고,
대학생 톤으로 짧고 설득력 있게 이유를 써.

반드시 아래 JSON만 출력해(설명 문장, 코드블록, 마크다운 금지).
규칙:
- primary_genre는 다음 중 하나: ["액션","코미디","드라마","SF","로맨스","판타지"]
- secondary_genre는 위 목록 중 하나 또는 null
- 같은 장르를 중복으로 넣지 마
- reason은 1~2문장
- keywords는 3~7개 한국어 키워드

사용자 선택 분포: {counts_text}

Q&A:
{qa_text}

예시 형식(값은 예시일 뿐):
{json.dumps(schema_hint, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
    )

    # SDK에서 output_text 제공 (docs 기준)
    text = resp.output_text.strip()
    return json.loads(text)

def openai_movie_reasons(
    api_key: str,
    model: str,
    profile: dict,
    movies: List[dict],
) -> Dict[int, str]:
    """
    각 영화별 한 줄 이유 생성.
    return: {movie_id: reason}
    """
    client = OpenAI(api_key=api_key)

    # 영화 후보 정보만 간단히
    items = []
    for m in movies:
        items.append({
            "id": m.get("id"),
            "title": m.get("title"),
            "overview": (m.get("overview") or "")[:300],
            "rating": m.get("vote_average"),
        })

    prompt = f"""
너는 영화 추천 큐레이터야. 아래 사용자 프로필(장르/키워드/이유)에 맞춰,
각 영화마다 '왜 이 영화가 어울리는지' 한 줄(최대 25자~45자 정도)로 써.

반드시 JSON 객체만 출력해.
형식: {{"<movie_id>": "이유", ...}}

사용자 프로필:
{json.dumps(profile, ensure_ascii=False)}

영화 목록:
{json.dumps(items, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(model=model, input=prompt)
    text = resp.output_text.strip()
    return {int(k): v for k, v in json.loads(text).items()}

# -----------------------------
# Render questions
# -----------------------------
answers: List[str] = []
qa_pairs: List[Tuple[str, str]] = []

for idx, item in enumerate(questions, start=1):
    choice = st.radio(item["q"], item["options"], key=f"q{idx}")
    answers.append(choice)
    qa_pairs.append((item["q"], choice))

st.divider()

# -----------------------------
# Result button
# -----------------------------
if st.button("결과 보기", type="primary"):
    if not tmdb_key:
        st.warning("사이드바에 TMDB API Key를 입력해주세요.")
        st.stop()

    counts = compute_preference_counts(answers)

    with st.spinner("분석 중..."):
        # 1) OpenAI로 정교 분석 (가능하면), 실패하면 fallback
        profile = None
        primary_genre = None
        secondary_genre = None

        if openai_key:
            try:
                profile = openai_analyze(openai_key, model_name, qa_pairs, counts)
                primary_genre = profile.get("primary_genre")
                secondary_genre = profile.get("secondary_genre")
                # 안전장치
                if primary_genre not in TMDB_GENRES:
                    primary_genre = None
                if secondary_genre not in TMDB_GENRES:
                    secondary_genre = None
                if secondary_genre == primary_genre:
                    secondary_genre = None
            except Exception as e:
                st.warning("OpenAI 분석에 실패해서 기본 로직으로 대체했어요.")
                st.caption(f"OpenAI error: {e}")

        if not primary_genre:
            primary_genre, secondary_genre = fallback_pick_genres(counts)
            profile = {
                "primary_genre": primary_genre,
                "secondary_genre": secondary_genre,
                "reason": "선택 분포를 기반으로 가장 강하게 드러난 취향을 골랐어요.",
                "keywords": [],
            }

        # 2) TMDB에서 영화 가져오기 (primary 3 + secondary 2)
        movies: List[dict] = []
        try:
            movies += fetch_top_movies(tmdb_key, primary_genre, n=3)
            if secondary_genre:
                movies += fetch_top_movies(tmdb_key, secondary_genre, n=2)
            else:
                # secondary 없으면 primary로 2편 더
                movies += fetch_top_movies(tmdb_key, primary_genre, n=5)[3:5]

            # 중복 제거(같은 id)
            seen = set()
            uniq = []
            for m in movies:
                mid = m.get("id")
                if mid and mid not in seen:
                    seen.add(mid)
                    uniq.append(m)
            movies = uniq[:5]
        except requests.HTTPError as e:
            st.error("TMDB API 요청에 실패했어요. API Key를 확인해주세요.")
            st.caption(f"TMDB HTTPError: {e}")
            st.stop()
        except Exception as e:
            st.error("TMDB 처리 중 오류가 발생했어요.")
            st.caption(str(e))
            st.stop()

        # 3) 각 영화별 추천 이유 생성(OpenAI 가능하면)
        per_movie_reason: Dict[int, str] = {}
        if openai_key:
            try:
                per_movie_reason = openai_movie_reasons(openai_key, model_name, profile, movies)
            except Exception as e:
                st.warning("영화별 추천 이유 생성에 실패했어요. 기본 문구로 표시할게요.")
                st.caption(f"OpenAI error: {e}")

    # -----------------------------
    # Output UI
    # -----------------------------
    st.subheader(f"🎯 당신의 추천 장르: **{primary_genre}**" + (f" + **{secondary_genre}**" if secondary_genre else ""))
    st.caption(
        f"선택 분포: 로맨스/드라마 {counts['로맨스/드라마']} · "
        f"액션/어드벤처 {counts['액션/어드벤처']} · "
        f"SF/판타지 {counts['SF/판타지']} · "
        f"코미디 {counts['코미디']}"
    )
    st.write("**요약 분석:**", profile.get("reason", ""))
    kws = profile.get("keywords") or []
    if kws:
        st.write("**키워드:**", " · ".join(kws))

    st.divider()
    st.subheader("🍿 지금 인기 있는 추천 영화 5편")

    # 카드형 표시
    for m in movies:
        title = m.get("title") or "제목 없음"
        rating = float(m.get("vote_average") or 0.0)
        overview = m.get("overview") or "줄거리 정보가 없어요."
        poster_url = build_poster_url(m.get("poster_path"))
        mid = m.get("id")

        with st.container(border=True):
            cols = st.columns([1, 2.2])
            with cols[0]:
                if poster_url:
                    st.image(poster_url, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")
            with cols[1]:
                st.markdown(f"### {title}")
                st.write(f"⭐ 평점: {rating:.1f} / 10")
                st.write(overview)

                # 영화별 이유
                why = per_movie_reason.get(mid)
                if not why:
                    # 기본 문구(오프라인)
                    why = f"당신의 **{primary_genre}** 성향과 잘 맞는 인기 작품이라 추천해요."
                    if secondary_genre:
                        why = f"당신의 **{primary_genre}/{secondary_genre}** 취향 포인트를 채워줄 가능성이 높아요."
                st.write("**이 영화를 추천하는 이유:**", why)
