# main.py - Railway 최적화된 Streamlit 다국어 강의 업로드 시스템 (개선 버전)
import streamlit as st
import os
import tempfile
import gc
from pathlib import Path
import json
import threading
import time
from video_uploader_logic import VideoUploaderLogic, GoogleTranslator, CATEGORY_STRUCTURE

# Railway 최적화 설정
st.set_page_config(
    page_title="🌍 다국어 강의 업로드 시스템",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'About': "Railway 배포용 다국어 강의 업로드 시스템 v3.1"
    }
)

# Railway 최적화 CSS (압축 버전)
st.markdown("""
<style>
    .main-header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 1.5rem; border-radius: 10px; color: white; text-align: center; margin-bottom: 1.5rem; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
    .success-box { background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%); padding: 1rem; border-radius: 8px; border-left: 4px solid #28a745; margin: 0.8rem 0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); }
    .error-box { background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%); padding: 1rem; border-radius: 8px; border-left: 4px solid #dc3545; margin: 0.8rem 0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); }
    .info-box { background: linear-gradient(135deg, #e8f5e8 0%, #f0f8ff 100%); padding: 1rem; border-radius: 8px; border-left: 4px solid #2a9d8f; margin: 0.8rem 0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); }
    .warning-box { background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%); padding: 1rem; border-radius: 8px; border-left: 4px solid #ffc107; margin: 0.8rem 0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); }
    .lang-badge { display: inline-block; background: rgba(255, 255, 255, 0.2); color: white; padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.75rem; margin: 0.1rem; backdrop-filter: blur(10px); }
    .video-card { background: white; border-radius: 8px; padding: 0.8rem; margin: 0.4rem 0; border: 1px solid #e0e0e0; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1); transition: transform 0.2s ease; }
    .video-card:hover { transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15); }
    .progress-container { background: #f8f9fa; border-radius: 8px; padding: 0.8rem; margin: 0.8rem 0; }
    .stFileUploader > div > div { max-height: 200px; overflow-y: auto; }
    .stTextArea textarea { max-height: 150px; }
</style>
""", unsafe_allow_html=True)

# Railway 환경 체크 (개선 버전)
@st.cache_data(ttl=3600)  # Railway 메모리 최적화
def check_environment():
    """Railway 환경 및 필수 환경변수 체크"""
    required_vars = [
        'WASABI_ACCESS_KEY', 'WASABI_SECRET_KEY', 'WASABI_BUCKET_NAME',
        'FIREBASE_PROJECT_ID', 'FIREBASE_PRIVATE_KEY', 'FIREBASE_CLIENT_EMAIL'
    ]
    
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        st.error(f"❌ 필수 환경변수 누락: {', '.join(missing_vars)}")
        st.stop()
    
    # Railway 메모리 상태 체크
    railway_env = 'RAILWAY_ENVIRONMENT' in os.environ
    return railway_env

# 세션 상태 초기화 (Railway 메모리 최적화)
def initialize_session_state():
    """세션 상태 초기화 (메모리 최적화)"""
    defaults = {
        'current_tab': 'new_upload',
        'translated_filenames': {},
        'show_translations': False,
        'translation_confirmed': False,
        'upload_in_progress': False,
        'selected_video_for_lang': None,
        'uploader_instance': None,
        'translator_instance': None
    }
    
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
    
    # 서비스 인스턴스 초기화 (지연 로딩)
    if st.session_state.uploader_instance is None:
        try:
            with st.spinner("🔧 서비스 초기화 중..."):
                st.session_state.uploader_instance = VideoUploaderLogic()
                st.session_state.translator_instance = GoogleTranslator()
        except Exception as e:
            st.error(f"❌ 서비스 초기화 실패: {e}")
            st.stop()

# Railway 최적화된 헤더 (압축 버전)
def render_header():
    """Railway 최적화된 헤더 렌더링"""
    st.markdown("""
    <div class="main-header">
        <h1>🌍 다국어 강의 업로드 시스템 v3.1</h1>
        <p>Railway 배포 | AI 자동 번역으로 전 세계 사용자에게 강의를 제공하세요</p>
        <div>
            <span class="lang-badge">🇰🇷 한국어</span>
            <span class="lang-badge">🇺🇸 English</span>
            <span class="lang-badge">🇨🇳 中文</span>
            <span class="lang-badge">🇻🇳 Tiếng Việt</span>
            <span class="lang-badge">🇹🇭 ไทย</span>
            <span class="lang-badge">🇯🇵 日本語</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# Railway 최적화된 사이드바 (압축 버전)
def setup_sidebar(is_railway_env):
    """Railway 최적화된 사이드바 설정"""
    with st.sidebar:
        st.markdown("## 📋 사용 가이드")
        
        if st.session_state.current_tab == 'new_upload':
            st.markdown("### 🚀 새 강의 업로드\n1. 기본 정보 입력\n2. 강의 내용 작성\n3. 파일명 번역 확인\n4. 파일 업로드")
        else:
            st.markdown("### 🌐 언어별 영상 관리\n1. 기존 강의 선택\n2. 추가할 언어 선택\n3. 번역된 영상 업로드")
        
        st.markdown("---")
        st.markdown("### 🔧 지원 형식")
        st.markdown("**동영상**: MP4, AVI, MOV, WMV, FLV, WEBM, MKV")
        st.markdown("**이미지**: JPG, PNG, GIF, BMP, WEBP")
        st.markdown("**최대 크기**: 5GB")
        
        st.markdown("---")
        st.markdown("### 📊 환경 상태")
        
        # 환경변수 체크
        required_vars = ['WASABI_ACCESS_KEY', 'FIREBASE_PROJECT_ID']
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        
        if missing_vars:
            st.error(f"❌ 환경변수 누락: {len(missing_vars)}개")
        else:
            st.success("✅ 환경설정 완료")
        
        # Google Translate API 상태
        if os.environ.get('GOOGLE_TRANSLATE_API_KEY'):
            st.success("✅ Google Translate API")
        else:
            st.warning("⚠️ 기본 번역 사용")
        
        # Railway 상태
        if is_railway_env:
            st.success("🚀 Railway 배포 환경")
        else:
            st.info("💻 로컬 개발 환경")

# 탭 메뉴 (Railway 최적화)
def render_tab_menu():
    """Railway 최적화된 탭 메뉴"""
    col_tab1, col_tab2, col_tab3 = st.columns([1, 1, 6])

    with col_tab1:
        if st.button("📤 새 강의 업로드", key="tab_new", use_container_width=True):
            st.session_state.current_tab = 'new_upload'
            # Railway 메모리 최적화
            if 'videos_data' in st.session_state:
                del st.session_state.videos_data
            gc.collect()
            st.rerun()

    with col_tab2:
        if st.button("🌐 언어별 영상", key="tab_lang", use_container_width=True):
            st.session_state.current_tab = 'language_video'
            # Railway 메모리 최적화
            for key in ['translated_filenames', 'show_translations', 'translation_confirmed']:
                if key in st.session_state:
                    if key == 'translated_filenames':
                        st.session_state[key] = {}
                    else:
                        st.session_state[key] = False
            gc.collect()
            st.rerun()

# 새 강의 업로드 탭 (Railway 최적화)
def render_new_upload_tab():
    """Railway 최적화된 새 강의 업로드 탭"""
    st.markdown("## 📋 기본 정보")
    
    group_name = st.text_input(
        "강의명 *", 
        placeholder="예: 기초 용접 안전교육",
        help="한국어로 입력하시면 자동으로 6개 언어로 번역됩니다",
        disabled=st.session_state.upload_in_progress,
        max_chars=100
    )
    
    # 카테고리 선택 (압축 버전)
    col_cat1, col_cat2, col_cat3 = st.columns(3)
    
    with col_cat1:
        main_category = st.selectbox(
            "대분류 *",
            [""] + CATEGORY_STRUCTURE['main_categories'],
            disabled=st.session_state.upload_in_progress
        )
    
    with col_cat2:
        if main_category:
            sub_categories = CATEGORY_STRUCTURE['sub_categories'].get(main_category, [])
            sub_category = st.selectbox("중분류 *", [""] + sub_categories, disabled=st.session_state.upload_in_progress)
        else:
            sub_category = st.selectbox("중분류 *", ["먼저 대분류를 선택하세요"], disabled=True)
    
    with col_cat3:
        if main_category and sub_category:
            leaf_categories = CATEGORY_STRUCTURE['leaf_categories'].get(sub_category, [])
            leaf_category = st.selectbox("소분류 *", [""] + leaf_categories, disabled=st.session_state.upload_in_progress)
        else:
            leaf_category = st.selectbox("소분류 *", ["먼저 중분류를 선택하세요"], disabled=True)

    # 강의 내용 입력 (압축 버전)
    st.markdown("## 📝 강의 내용")
    
    # 템플릿 버튼들
    col_temp1, col_temp2, col_temp3, col_temp4 = st.columns(4)
    
    with col_temp1:
        if st.button("📋 안전교육", disabled=st.session_state.upload_in_progress):
            st.session_state.content_template = """이 강의는 안전교육으로, 다음 내용을 다룹니다:
• 기본 안전수칙
• 작업 전 점검사항
• 위험 상황 대처방법
• 응급처치 및 비상대응
• 관련 법규 및 규정"""
            st.rerun()
    
    with col_temp2:
        if st.button("🔧 장비교육", disabled=st.session_state.upload_in_progress):
            st.session_state.content_template = """이 강의는 장비 사용법 교육으로, 다음 내용을 다룹니다:
• 장비 구조 및 원리
• 올바른 조작 방법
• 일상 점검 및 유지보수
• 고장 시 대처방법
• 안전 운전 수칙"""
            st.rerun()
    
    with col_temp3:
        if st.button("🗑️ 지우기", disabled=st.session_state.upload_in_progress):
            st.session_state.content_template = ""
            st.rerun()
    
    with col_temp4:
        if st.button("🌍 파일명 번역", disabled=st.session_state.upload_in_progress):
            if group_name:
                with st.spinner("AI 번역 중..."):
                    try:
                        translations = st.session_state.translator_instance.translate_title(group_name)
                        st.session_state.translated_filenames = translations
                        st.session_state.show_translations = True
                        st.success("번역 완료!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"번역 오류: {str(e)}")
            else:
                st.error("먼저 강의명을 입력해주세요.")
    
    # 내용 텍스트박스
    default_content = st.session_state.get('content_template', "강의 내용을 입력해주세요...")
    content_description = st.text_area(
        "강의에서 다루는 내용 *",
        value=default_content,
        height=150,
        help="구체적이고 상세한 내용일수록 더 좋은 번역 결과를 얻습니다",
        disabled=st.session_state.upload_in_progress,
        max_chars=1000
    )

    # 번역 결과 표시 (압축 버전)
    if st.session_state.get('show_translations', False) and st.session_state.get('translated_filenames'):
        st.markdown("### 🌍 번역된 파일명")
        
        with st.expander("번역 결과 확인 및 수정", expanded=True):
            translations = st.session_state.translated_filenames
            st.markdown(f"**🇰🇷 한국어 (원본)**: `{group_name}`")
            
            languages = {
                'en': ('🇺🇸', 'English'), 'zh': ('🇨🇳', '中文'), 'vi': ('🇻🇳', 'Tiếng Việt'),
                'th': ('🇹🇭', 'ไทย'), 'ja': ('🇯🇵', '日本語')
            }
            
            for lang_code, (flag, lang_name) in languages.items():
                if lang_code in translations:
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.markdown(f"**{flag} {lang_name}**")
                    with col2:
                        edited_translation = st.text_input(
                            f"edit_{lang_code}",
                            value=translations[lang_code],
                            key=f"trans_{lang_code}",
                            label_visibility="collapsed",
                            disabled=st.session_state.upload_in_progress,
                            max_chars=50
                        )
                        st.session_state.translated_filenames[lang_code] = edited_translation
            
            if st.button("✅ 번역 확인 완료", type="primary", disabled=st.session_state.upload_in_progress):
                st.session_state.translation_confirmed = True
                st.success("✅ 파일명 번역 완료! 이제 파일을 업로드할 수 있습니다.")
                st.rerun()

    # 파일 업로드 섹션
    st.markdown("## 📁 파일 업로드")
    col_file1, col_file2 = st.columns(2)

    with col_file1:
        video_file = st.file_uploader(
            "동영상 파일 *",
            type=['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'],
            help="최대 5GB",
            disabled=st.session_state.upload_in_progress
        )

    with col_file2:
        thumbnail_file = st.file_uploader(
            "썸네일 이미지 (선택)",
            type=['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'],
            help="없으면 QR 코드 생성",
            disabled=st.session_state.upload_in_progress
        )

    # 업로드 실행
    st.markdown("---")
    upload_ready = (
        group_name and main_category and sub_category and leaf_category and 
        content_description and len(content_description.strip()) >= 10 and 
        video_file and st.session_state.get('translation_confirmed', False) and 
        not st.session_state.upload_in_progress
    )

    if st.button("🌍 다국어 강의 업로드 시작", type="primary", disabled=not upload_ready):
        if upload_ready:
            st.session_state.upload_in_progress = True
            perform_upload(video_file, thumbnail_file, group_name, main_category, 
                         sub_category, leaf_category, content_description)
        else:
            st.error("모든 필수 항목을 입력하고 파일명 번역을 확인해주세요.")

    # 업로드 준비 상태 표시
    if not upload_ready and not st.session_state.upload_in_progress:
        missing_items = []
        if not group_name: missing_items.append("강의명")
        if not main_category: missing_items.append("대분류")
        if not sub_category: missing_items.append("중분류") 
        if not leaf_category: missing_items.append("소분류")
        if not content_description or len(content_description.strip()) < 10: missing_items.append("강의 내용")
        if not video_file: missing_items.append("동영상 파일")
        if not st.session_state.get('translation_confirmed', False): missing_items.append("파일명 번역 확인")
        
        st.markdown(f"""
        <div class="warning-box">
            <h4>⚠️ 업로드 준비 체크</h4>
            <p>필요한 항목: <strong>{', '.join(missing_items)}</strong></p>
        </div>
        """, unsafe_allow_html=True)

# 언어별 영상 관리 탭 (Railway 최적화)
def render_language_video_tab():
    """Railway 최적화된 언어별 영상 관리 탭"""
    st.markdown("## 🌐 언어별 영상 관리")
    
    if st.button("🔄 목록 새로고침"):
        if 'videos_data' in st.session_state:
            del st.session_state.videos_data
        gc.collect()
        st.rerun()
    
    # 영상 목록 로드 (Railway 메모리 최적화)
    if 'videos_data' not in st.session_state:
        with st.spinner("강의 목록 로딩 중..."):
            try:
                st.session_state.videos_data = st.session_state.uploader_instance.get_existing_videos()
            except Exception as e:
                st.error(f"영상 목록 로드 실패: {str(e)}")
                st.session_state.videos_data = []
    
    videos_data = st.session_state.videos_data
    
    if not videos_data:
        st.markdown("""
        <div class="info-box">
            <h4>📚 업로드된 강의가 없습니다</h4>
            <p>먼저 '새 강의 업로드' 탭에서 강의를 업로드해주세요.</p>
        </div>
        """, unsafe_allow_html=True)
        return
    
    # 영상 목록 표시 (페이징 적용)
    st.markdown("### 📚 업로드된 강의 목록")
    
    videos_per_page = 10
    total_videos = len(videos_data)
    
    if total_videos > videos_per_page:
        page = st.selectbox("페이지", range(1, (total_videos // videos_per_page) + 2))
        start_idx = (page - 1) * videos_per_page
        end_idx = min(start_idx + videos_per_page, total_videos)
        display_videos = videos_data[start_idx:end_idx]
    else:
        display_videos = videos_data
    
    for i, video in enumerate(display_videos):
        language_count = len(video['languages'])
        status_icon = "🟢" if language_count == 6 else "🟡" if language_count > 1 else "🔴"
        status_text = "완료" if language_count == 6 else "진행중" if language_count > 1 else "시작"
        
        with st.container():
            st.markdown(f"""
            <div class="video-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h4>{status_icon} {video['title']}</h4>
                        <p><strong>카테고리:</strong> {video['category']}</p>
                        <p><strong>업로드일:</strong> {video['upload_date']}</p>
                        <p><strong>지원 언어:</strong> {', '.join(video['languages'])} ({language_count}/6)</p>
                    </div>
                    <div style="text-align: right;">
                        <span style="background: {'#28a745' if language_count == 6 else '#ffc107' if language_count > 1 else '#dc3545'}; 
                               color: white; padding: 0.3rem 0.8rem; border-radius: 15px; font-size: 0.8rem;">
                            {status_text}
                        </span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("선택", key=f"select_{i}"):
                    st.session_state.selected_video_for_lang = video
                    st.rerun()
    
    # 선택된 영상에 대한 언어별 업로드
    if st.session_state.selected_video_for_lang:
        video = st.session_state.selected_video_for_lang
        
        st.markdown("---")
        st.markdown(f"### 🎯 선택된 강의: {video['title']}")
        
        existing_languages = video['languages']
        all_languages = [("en", "🇺🇸 English"), ("zh", "🇨🇳 中文"), ("vi", "🇻🇳 Tiếng Việt"), ("th", "🇹🇭 ไทย"), ("ja", "🇯🇵 日본語")]
        available_languages = [(code, display) for code, display in all_languages if code not in existing_languages]
        
        if not available_languages:
            st.success("🎉 모든 언어 업로드 완료!")
            return
        
        col_lang, col_file = st.columns(2)
        
        with col_lang:
            selected_lang = st.selectbox(
                "추가할 언어",
                options=[None] + available_languages,
                format_func=lambda x: "언어 선택" if x is None else x[1]
            )
        
        with col_file:
            lang_video_file = st.file_uploader(
                "번역된 영상",
                type=['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv']
            )
        
        if selected_lang and lang_video_file:
            if st.button("🌐 언어별 영상 업로드", type="primary"):
                perform_language_upload(video['id'], selected_lang[0], lang_video_file)

# Railway 최적화된 업로드 함수들
def perform_upload(video_file, thumbnail_file, group_name, main_category, sub_category, leaf_category, content_description):
    """Railway 최적화된 메인 업로드 실행"""
    progress_container = st.container()
    
    with progress_container:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        def update_progress(value, message):
            progress_bar.progress(value / 100)
            status_text.text(message)
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                update_progress(10, "📁 파일 준비 중...")
                
                # 파일 저장
                video_path = os.path.join(temp_dir, video_file.name)
                with open(video_path, "wb") as f:
                    f.write(video_file.getvalue())
                
                thumbnail_path = None
                if thumbnail_file:
                    thumbnail_path = os.path.join(temp_dir, thumbnail_file.name)
                    with open(thumbnail_path, "wb") as f:
                        f.write(thumbnail_file.getvalue())
                
                update_progress(20, "🌍 업로드 시작...")
                
                # 업로드 실행
                result = st.session_state.uploader_instance.upload_video(
                    video_path=video_path,
                    thumbnail_path=thumbnail_path,
                    group_name=group_name,
                    main_category=main_category,
                    sub_category=sub_category,
                    leaf_category=leaf_category,
                    content_description=content_description,
                    translated_filenames=st.session_state.get('translated_filenames', {}),
                    progress_callback=update_progress
                )
                
                gc.collect()
                
                if result['success']:
                    progress_bar.empty()
                    status_text.empty()
                    
                    st.markdown("""
                    <div class="success-box">
                        <h3>🎉 업로드 완료!</h3>
                        <p>다국어 강의가 성공적으로 업로드되었습니다!</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 결과 정보
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("### 📊 업로드 정보")
                        st.write(f"**그룹 ID**: `{result['group_id']}`")
                        st.write(f"**강의명**: {group_name}")
                        st.write(f"**카테고리**: {main_category} > {sub_category} > {leaf_category}")
                        if result['metadata']:
                            st.write(f"**길이**: {result['metadata']['duration_string']}")
                            st.write(f"**크기**: {result['metadata']['file_size']:,} bytes")
                    
                    with col2:
                        st.markdown("### 🔗 링크 정보")
                        st.write(f"**시청 링크**: {result['qr_link']}")
                        if result['qr_url']:
                            st.write(f"**QR 코드**: [다운로드]({result['qr_url']})")
                    
                    # QR 코드 표시
                    if result['qr_url']:
                        st.markdown("### 📱 QR 코드")
                        st.image(result['qr_url'], width=250)
                    
                    # 세션 정리
                    for key in ['translated_filenames', 'show_translations', 'translation_confirmed']:
                        if key in st.session_state:
                            if key == 'translated_filenames':
                                st.session_state[key] = {}
                            else:
                                st.session_state[key] = False
                    
                    if 'videos_data' in st.session_state:
                        del st.session_state.videos_data
                    
                    gc.collect()
                    
                else:
                    progress_bar.empty()
                    status_text.empty()
                    st.markdown(f"""
                    <div class="error-box">
                        <h3>❌ 업로드 실패</h3>
                        <p>오류: {result.get('error', '알 수 없는 오류')}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.markdown(f"""
            <div class="error-box">
                <h3>❌ 업로드 중 오류</h3>
                <p>{str(e)}</p>
            </div>
            """, unsafe_allow_html=True)
        
        finally:
            st.session_state.upload_in_progress = False
            gc.collect()

def perform_language_upload(video_id, language_code, lang_video_file):
    """Railway 최적화된 언어별 영상 업로드"""
    progress_container = st.container()
    
    with progress_container:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        def update_progress(value, message):
            progress_bar.progress(value / 100)
            status_text.text(message)
        
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                update_progress(10, "📁 파일 준비 중...")
                
                lang_video_path = os.path.join(temp_dir, lang_video_file.name)
                with open(lang_video_path, "wb") as f:
                    f.write(lang_video_file.getvalue())
                
                update_progress(20, "🌐 언어별 영상 업로드 중...")
                
                result = st.session_state.uploader_instance.upload_language_video(
                    video_id=video_id,
                    language_code=language_code,
                    video_path=lang_video_path,
                    progress_callback=update_progress
                )
                
                gc.collect()
                
                if result['success']:
                    progress_bar.empty()
                    status_text.empty()
                    
                    language_names = {'en': '🇺🇸 English', 'zh': '🇨🇳 中文', 'vi': '🇻🇳 Tiếng Việt', 'th': '🇹🇭 ไทย', 'ja': '🇯🇵 日본語'}
                    lang_display = language_names.get(language_code, language_code)
                    
                    st.markdown(f"""
                    <div class="success-box">
                        <h3>🎉 언어별 영상 업로드 완료!</h3>
                        <p>{lang_display} 영상이 성공적으로 업로드되었습니다!</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.markdown("### 📊 업로드 정보")
                    st.write(f"**언어**: {lang_display}")
                    st.write(f"**동영상 URL**: {result['video_url']}")
                    
                    if result['metadata']:
                        st.write(f"**길이**: {result['metadata']['duration_string']}")
                        st.write(f"**크기**: {result['metadata']['file_size']:,} bytes")
                    
                    # 세션 정리
                    st.session_state.selected_video_for_lang = None
                    if 'videos_data' in st.session_state:
                        del st.session_state.videos_data
                    
                    gc.collect()
                    st.success("페이지를 새로고침하여 업데이트된 목록을 확인하세요.")
                    
                else:
                    progress_bar.empty()
                    status_text.empty()
                    st.markdown(f"""
                    <div class="error-box">
                        <h3>❌ 언어별 영상 업로드 실패</h3>
                        <p>오류: {result.get('error', '알 수 없는 오류')}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
        except Exception as e:
            progress_bar.empty()
            status_text.empty()
            st.markdown(f"""
            <div class="error-box">
                <h3>❌ 업로드 중 오류</h3>
                <p>{str(e)}</p>
            </div>
            """, unsafe_allow_html=True)

# Railway 최적화된 메인 함수
def main():
    """Railway 최적화된 메인 함수"""
    # 환경 체크
    is_railway_env = check_environment()
    
    # 세션 상태 초기화
    initialize_session_state()
    
    # 헤더 렌더링
    render_header()
    
    # 사이드바 설정
    setup_sidebar(is_railway_env)
    
    # 탭 메뉴
    render_tab_menu()
    
    # 탭별 콘텐츠
    if st.session_state.current_tab == 'new_upload':
        render_new_upload_tab()
    elif st.session_state.current_tab == 'language_video':
        render_language_video_tab()

    # 푸터
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; color: #666; padding: 1rem;">
        <p>🌍 다국어 강의 업로드 시스템 v3.1 | Railway 최적화 버전</p>
        <p>🚀 AI 자동 번역으로 전 세계에 지식을 전파하세요</p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()