import requests
import streamlit as st

st.set_page_config(page_title="나와 어울리는 영화는?", page_icon="🎬")

# -----------------------------
# UI: Header
# -----------------------------
st.title("🎬 나와 어울리는 영화는?")
st.write("5개의 질문에 답하면, 당신의 취향과 어울리는 영화 장르를 골라 인기 영화 5편을 추천해드려요! 🎥🍿")
st.divider()

# -----------------------------
# Sidebar: TMDB API Key
# -----------------------------
st.sidebar.header("🔑 TMDB 설정")
tmdb_key = st.sidebar.text_input("TMDB API Key", type="password", placeholder="여기에 API Key 입력")

# -----------------------------
# Genre mapping
# -----------------------------
GENRE_ID = {
    "로맨스": 10749,
    "드라마": 18,
    "액션": 28,
    "어드벤처": 12,  # 참고: TMDB 어드벤처 ID (요구사항엔 없지만 보완)
    "SF": 878,
    "판타지": 14,
    "코미디": 35,
}

POSTER_BASE = "https://image.tmdb.org/t/p/w500"


# -----------------------------
# Questions (same as before)
# Each option starts with a tag so we can score cleanly.
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
        "q": "Q4. 이런 상황이라면 어떤 영화를 고를까? 비 오는 날, 약속이 취소됐다.",
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
]

# -----------------------------
# Helpers
# -----------------------------
def parse_choice(choice_text: str) -> str:
    """Return the preference tag part, e.g., '로맨스/드라마'."""
    return choice_text.split("|", 1)[0].strip()

def decide_genre(answers: list[str]) -> tuple[str, int, dict]:
    """
    Analyze answers -> pick one main genre among:
    romance, drama, action, sf, fantasy, comedy

    Strategy:
    - Count occurrences of the 4 preference groups
    - Map group to a 'main genre' ID (some groups are combos)
    - If tie, break with a consistent priority
    """
    # Count group picks
    counts = {"로맨스/드라마": 0, "액션/어드벤처": 0, "SF/판타지": 0, "코미디": 0}
    for a in answers:
        tag = parse_choice(a)
        if tag in counts:
            counts[tag] += 1

    # Resolve to a main genre
    # For combo groups, choose one representative genre ID.
    # - 로맨스/드라마: if more "감성/여운" -> 드라마, else 로맨스
    #   Here we keep it simple: default to 드라마, but if user picked Q5 romance/drama option (life/meaning)
    #   still drama; romance emphasis isn't explicit. We'll allow a small rule:
    #   if user picked romance/drama in Q2 (love/choices) AND Q1 (quiet night) -> romance, else drama.
    # - 액션/어드벤처: use 액션(28) as requested
    # - SF/판타지: use SF(878) as default; if user picked SF/판타지 in Q2 (other dimension) AND Q4 (other world)
    #   -> 판타지, else SF
    # - 코미디: 코미디(35)
    top = max(counts.values())
    top_groups = [g for g, v in counts.items() if v == top]

    # Tie-break priority (can be tuned)
    priority = ["SF/판타지", "액션/어드벤처", "로맨스/드라마", "코미디"]
    chosen_group = sorted(top_groups, key=lambda x: priority.index(x))[0]

    # Decide representative genre
    if chosen_group == "액션/어드벤처":
        genre_name = "액션"
        genre_id = GENRE_ID["액션"]
    elif chosen_group == "코미디":
        genre_name = "코미디"
        genre_id = GENRE_ID["코미디"]
    elif chosen_group == "로맨스/드라마":
        # Simple heuristic: romance if Q2 romance/drama AND Q1 romance/drama
        picked_q1 = parse_choice(answers[0]) == "로맨스/드라마"
        picked_q2 = parse_choice(answers[1]) == "로맨스/드라마"
        if picked_q1 and picked_q2:
            genre_name = "로맨스"
            genre_id = GENRE_ID["로맨스"]
        else:
            genre_name = "드라마"
            genre_id = GENRE_ID["드라마"]
    else:  # "SF/판타지"
        picked_q2 = parse_choice(answers[1]) == "SF/판타지"
        picked_q4 = parse_choice(answers[3]) == "SF/판타지"
        if picked_q2 and picked_q4:
            genre_name = "판타지"
            genre_id = GENRE_ID["판타지"]
        else:
            genre_name = "SF"
            genre_id = GENRE_ID["SF"]

    return genre_name, genre_id, counts

def fetch_movies(api_key: str, genre_id: int, n: int = 5) -> list[dict]:
    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": api_key,
        "with_genres": genre_id,
        "language": "ko-KR",
        "sort_by": "popularity.desc",
        "page": 1,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])[:n]
    return results

def reason_text(main_genre: str, counts: dict) -> str:
    # Short, human-friendly reason
    if main_genre == "액션":
        return "짜릿한 전개와 큰 스케일에서 에너지를 얻는 타입이라, 속도감 있는 액션 영화가 잘 맞아요."
    if main_genre == "코미디":
        return "영화는 편하게 즐기는 게 최고! 웃음 포인트가 확실한 코미디가 궁합이 좋아요."
    if main_genre == "드라마":
        return "감정선과 여운을 중요하게 여기는 편이라, 메시지 있는 드라마가 잘 맞아요."
    if main_genre == "로맨스":
        return "관계의 설렘과 감정의 흐름에 몰입하는 편이라, 로맨스 영화가 찰떡이에요."
    if main_genre == "SF":
        return "새로운 설정과 세계관에서 몰입을 느끼는 편이라, SF 영화가 잘 맞아요."
    if main_genre == "판타지":
        return "현실을 잠시 잊고 다른 세계로 여행하는 느낌을 좋아해서, 판타지 영화가 딱이에요."
    return "당신의 선택을 기반으로 가장 어울리는 장르를 골랐어요!"

# -----------------------------
# Render questions
# -----------------------------
answers = []
for idx, item in enumerate(questions, start=1):
    choice = st.radio(item["q"], item["options"], key=f"q{idx}")
    answers.append(choice)
    st.write("")

st.divider()

# -----------------------------
# Button action
# -----------------------------
if st.button("결과 보기", type="primary"):
    if not tmdb_key:
        st.warning("사이드바에 TMDB API Key를 입력해주세요.")
        st.stop()

    st.info("분석 중...")

    try:
        main_genre_name, main_genre_id, counts = decide_genre(answers)

        st.subheader(f"🎯 당신의 추천 장르: **{main_genre_name}**")
        st.caption(
            f"선택 분포: 로맨스/드라마 {counts['로맨스/드라마']} · "
            f"액션/어드벤처 {counts['액션/어드벤처']} · "
            f"SF/판타지 {counts['SF/판타지']} · "
            f"코미디 {counts['코미디']}"
        )
        st.write("**이 장르를 추천하는 이유:**", reason_text(main_genre_name, counts))
        st.divider()

        movies = fetch_movies(tmdb_key, main_genre_id, n=5)

        if not movies:
            st.warning("해당 장르의 영화를 가져오지 못했어요. (결과가 비어있음)")
            st.stop()

        st.subheader("🍿 지금 인기 있는 추천 영화 5편")

        for m in movies:
            title = m.get("title") or m.get("name") or "제목 없음"
            rating = m.get("vote_average", 0)
            overview = m.get("overview") or "줄거리 정보가 없어요."
            poster_path = m.get("poster_path")
            poster_url = f"{POSTER_BASE}{poster_path}" if poster_path else None

            cols = st.columns([1, 2])
            with cols[0]:
                if poster_url:
                    st.image(poster_url, use_container_width=True)
                else:
                    st.write("🖼️ 포스터 없음")

            with cols[1]:
                st.markdown(f"### {title}")
                st.write(f"⭐ 평점: {rating:.1f} / 10")
                st.write(overview)

                # Simple per-movie reason
                if main_genre_name in ["드라마", "로맨스"]:
                    why = "감정선이 살아있는 이야기로, 당신이 좋아하는 ‘여운’ 포인트를 채워줄 가능성이 높아요."
                elif main_genre_name in ["액션"]:
                    why = "전개가 빠르고 긴장감 있는 구성이어서, 스트레스 해소용으로 잘 맞아요."
                elif main_genre_name in ["SF", "판타지"]:
                    why = "설정과 세계관에 몰입할수록 재미가 커지는 타입의 작품일 가능성이 높아요."
                else:  # 코미디
                    why = "가볍게 보기 좋고 웃음 포인트가 기대돼서, 기분전환에 딱이에요."

                st.write("**이 영화를 추천하는 이유:**", why)

            st.divider()

    except requests.HTTPError as e:
        st.error("TMDB API 요청에 실패했어요. API Key가 올바른지, 호출 제한이 걸리지 않았는지 확인해주세요.")
        st.caption(f"HTTPError: {e}")
    except Exception as e:
        st.error("처리 중 오류가 발생했어요.")
        st.caption(str(e))
