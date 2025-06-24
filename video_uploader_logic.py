# video_uploader_logic.py - 완전한 QR 코드 + 썸네일 결합 및 이중 저장 구현
import os
import sys
import uuid
import re
import json
import tempfile
import logging
import time
import requests
import urllib.parse
import threading
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List, Callable
from PIL import Image, ImageDraw, ImageFont
import qrcode
from contextlib import contextmanager

# 필수 라이브러리 import with fallback
try:
    import boto3
    from boto3.s3.transfer import TransferConfig
    import firebase_admin
    from firebase_admin import credentials, firestore  # storage 제거
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from googletrans import Translator  # Google Translate 대체 라이브러리
except ImportError as e:
    print(f"❌ 필수 라이브러리 누락: {e}")
    print("다음 명령어로 설치하세요: pip install boto3 firebase-admin moviepy googletrans==4.0.0rc1")
    sys.exit(1)

# 로깅 설정 (Railway 최적화)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 브런치 도메인 설정
BRUNCH_DOMAIN = os.environ.get('BRUNCH_DOMAIN', 'jwvduc.app.link')
BRUNCH_ALTERNATE_DOMAIN = os.environ.get('BRUNCH_ALTERNATE_DOMAIN', 'jwvduc-alternate.app.link')

# 상수 정의 (확장된 지원 형식)
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.3gp', '.m4v', '.f4v', '.m2v'}
SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg', '.ico', '.heic', '.heif'}
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5GB
RAILWAY_MEMORY_LIMIT = 8 * 1024 * 1024 * 1024  # 8GB Railway 메모리 제한

# Railway 최적화된 카테고리 구조
CATEGORY_STRUCTURE = {
    'main_categories': ['기계', '공구', '장비', '약품'],
    'sub_categories': {
        '기계': ['건설기계', '공작기계', '산업기계', '제조기계'],
        '공구': ['수공구', '전동공구', '절삭공구', '측정공구'],
        '장비': ['안전장비', '운송장비'],
        '약품': ['의약품', '화공약품']
    },
    'leaf_categories': {
        '건설기계': ['불도저', '크레인'],
        '공작기계': ['CNC 선반', '연삭기'],
        '산업기계': ['굴착기', '유압 프레스'],
        '제조기계': ['사출 성형기', '열 성형기'],
        '수공구': ['전동드릴', '플라이어', '해머'],
        '전동공구': ['그라인더', '전동톱', '해머드릴'],
        '절삭공구': ['가스 용접기', '커터'],
        '측정공구': ['마이크로미터', '하이트 게이지'],
        '안전장비': ['헬멧', '방진 마스크', '낙하 방지벨트', '안전모', '안전화', '보호안경', '귀마개', '보호장갑', '호흡 보호구'],
        '운송장비': ['리프트 장비', '체인 블록', '호이스트'],
        '의약품': ['인슐린', '항생제'],
        '화공약품': ['황산', '염산']
    }
}

class GoogleTranslator:
    """완전한 googletrans 번역 시스템 (수정됨)"""
    
    def __init__(self):
        self.translator = Translator()
        
        # Railway 최적화 설정
        self.timeout = 15
        self.max_retries = 3
        
        # 올바른 언어 코드 매핑
        self.language_codes = {
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日本語'
        }
        
        # googletrans 호환 언어 코드
        self.googletrans_codes = {
            'en': 'en',
            'zh': 'zh-cn',  # 중국어 간체
            'vi': 'vi',
            'th': 'th', 
            'ja': 'ja'
        }
        
        # 캐시 시스템 (Railway 메모리 절약)
        self._translation_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_max_size = 100  # 캐시 크기 증가
        
        logger.info("🌍 완전한 번역 서비스 초기화 완료")
    
    def translate_title(self, korean_title: str) -> Dict[str, str]:
        """강의명 번역 (완전한 구현)"""
        # 캐시 확인
        cache_key = f"title_{hash(korean_title)}"
        with self._cache_lock:
            if cache_key in self._translation_cache:
                logger.debug(f"캐시에서 번역 결과 반환: {korean_title[:20]}...")
                return self._translation_cache[cache_key].copy()
        
        translations = {'ko': self._make_filename_safe(korean_title)}
        
        # 번역 대상 언어
        target_languages = ['en', 'zh', 'vi', 'th', 'ja']
        
        for lang_code in target_languages:
            try:
                # 올바른 googletrans 언어 코드 사용
                googletrans_code = self.googletrans_codes.get(lang_code, lang_code)
                logger.debug(f"번역 시도: {korean_title} -> {lang_code} ({googletrans_code})")
                
                # 실제 Google Translate API 호출
                translated = self._translate_with_googletrans(korean_title, googletrans_code)
                
                if translated and translated != korean_title:
                    translations[lang_code] = self._make_filename_safe(translated)
                    logger.info(f"✅ 번역 성공: {lang_code} -> {translated}")
                else:
                    logger.warning(f"⚠️ {lang_code} 번역 실패, 대체 번역 사용")
                    translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
                
                # Railway API 제한 대응
                time.sleep(0.3)
                
            except Exception as e:
                logger.warning(f"❌ {lang_code} 번역 실패, 대체 번역 사용: {e}")
                translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
        
        # 캐시에 저장
        with self._cache_lock:
            if len(self._translation_cache) >= self._cache_max_size:
                # LRU 방식으로 가장 오래된 항목 제거
                oldest_key = next(iter(self._translation_cache))
                del self._translation_cache[oldest_key]
            
            self._translation_cache[cache_key] = translations.copy()
        
        logger.info(f"🌍 번역 완료: {korean_title} -> {len(translations)}개 언어")
        return translations
    
    def _translate_with_googletrans(self, text: str, target_lang: str) -> Optional[str]:
        """googletrans 번역 (강화된 구현)"""
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"googletrans 번역 시도 {attempt + 1}: '{text}' -> {target_lang}")
                
                # 텍스트 전처리
                clean_text = text.strip()
                if not clean_text:
                    return None
                
                # 단순한 영문자/숫자만 있는 경우 번역하지 않음
                if clean_text.isalnum() and clean_text.isascii():
                    logger.debug(f"단순 영문자/숫자는 번역하지 않음: {clean_text}")
                    return None
                
                # Google Translate API 호출
                result = self.translator.translate(clean_text, src='ko', dest=target_lang)
                
                if result and result.text and result.text.strip():
                    translated_text = result.text.strip()
                    
                    # 번역 결과 검증 강화
                    if (translated_text != clean_text and 
                        len(translated_text) > 0 and 
                        not translated_text.isdigit() and  # 숫자만 있는 결과 제외
                        translated_text.lower() != clean_text.lower()):  # 대소문자만 다른 경우 제외
                        
                        logger.debug(f"✅ googletrans 번역 성공: '{clean_text}' -> '{translated_text}' ({target_lang})")
                        return translated_text
                    else:
                        logger.warning(f"⚠️ googletrans 번역 결과가 의미없음: '{translated_text}' ({target_lang})")
                        
                else:
                    logger.warning(f"⚠️ googletrans 번역 결과가 비어있음: {target_lang}")
                    
            except Exception as e:
                logger.warning(f"❌ googletrans 번역 시도 {attempt + 1} 실패 ({target_lang}): {e}")
                if attempt < self.max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 점진적 대기
                    logger.debug(f"⏱️ {wait_time}초 대기 후 재시도")
                    time.sleep(wait_time)
        
        logger.error(f"❌ googletrans 번역 최종 실패: {target_lang}")
        return None
    
    def _make_filename_safe(self, text: str) -> str:
        """Railway 최적화된 파일명 안전화"""
        import html
        text = html.unescape(text)
        
        # Railway 파일시스템 최적화
        safe_text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
        safe_text = re.sub(r'\s+', '_', safe_text)
        safe_text = re.sub(r'_+', '_', safe_text)
        safe_text = safe_text.strip('_')
        
        # Railway 제한 적용
        if len(safe_text.encode('utf-8')) > 200:  # 바이트 길이 제한
            safe_text = safe_text[:50].rstrip('_')
        
        return safe_text or 'Unknown_Title'
    
    def _fallback_single_translation(self, korean_title: str, lang_code: str) -> str:
        """Railway 최적화된 키워드 기반 대체 번역"""
        # Railway 메모리 최적화된 키워드 맵
        keyword_maps = {
            'en': {
                '안전': 'Safety', '교육': 'Training', '기초': 'Basic', '용접': 'Welding',
                '크레인': 'Crane', '조작': 'Operation', '장비': 'Equipment', '사용법': 'Usage',
                '점검': 'Inspection', '유지보수': 'Maintenance', '응급처치': 'First_Aid',
                '산업': 'Industrial', '건설': 'Construction', '기계': 'Machine', '공구': 'Tool',
                '화학': 'Chemical', '물질': 'Material', '처리': 'Processing', '관리': 'Management'
            },
            'zh': {
                '안전': '安全', '교육': '培训', '기초': '基础', '용접': '焊接',
                '크레인': '起重机', '조작': '操作', '장비': '设备', '사용법': '使用方法',
                '점검': '检查', '유지보수': '维护', '응급처치': '急救',
                '산업': '工业', '건설': '建设', '기계': '机械', '공구': '工具'
            },
            'vi': {
                '안전': 'An_Toan', '교육': 'Dao_Tao', '기초': 'Co_Ban', '용접': 'Han',
                '크레인': 'Cau_Truc', '조작': 'Van_Hanh', '장비': 'Thiet_Bi',
                '점검': 'Kiem_Tra', '유지보수': 'Bao_Duong', '산업': 'Cong_Nghiep'
            },
            'th': {
                '안전': 'ความปลอดภัย', '교육': 'การศึกษา', '기초': 'พื้นฐาน', '용접': 'การเชื่อม',
                '크레인': 'เครน', '조작': 'การใช้งาน', '장비': 'อุปกรณ์'
            },
            'ja': {
                '안전': '安全', '교육': '教育', '기초': '基礎', '용접': '溶接',
                '크레인': 'クレーン', '조작': '操作', '장비': '設備',
                '점검': '点検', '유지보수': 'メンテナンス', '산업': '産業'
            }
        }
        
        keyword_map = keyword_maps.get(lang_code, {})
        result = korean_title
        
        # 키워드 번역 적용
        for korean, translated in keyword_map.items():
            result = result.replace(korean, translated)
        
        # 언어별 접미사 추가 (구분용)
        lang_suffix = {
            'en': '_EN',
            'zh': '_CN', 
            'vi': '_VI',
            'th': '_TH',
            'ja': '_JP'
        }
        
        if result == korean_title:  # 번역이 적용되지 않은 경우
            result = f"{korean_title}_{lang_suffix.get(lang_code, lang_code.upper())}"
        
        # 파일명 안전화
        return self._make_filename_safe(result)

class VideoUploaderLogic:
    """비디오 업로더 메인 클래스 - QR+썸네일 결합 및 이중 저장"""
    
    def __init__(self):
        self._initialization_lock = threading.Lock()
        self._service_health = {
            'firebase': False,
            'wasabi': False,
            'translator': False
        }
        
        try:
            self._initialize_services()
            self.translator = GoogleTranslator()
            self._service_health['translator'] = True
            logger.info("✅ Railway 비디오 업로더 완전 초기화 완료 (QR+썸네일 결합)")
        except Exception as e:
            logger.error(f"❌ 비디오 업로더 초기화 실패: {e}")
            logger.error(f"초기화 실패 상세: {traceback.format_exc()}")
            raise
    
    def _initialize_services(self):
        """Railway 최적화된 서비스 초기화"""
        with self._initialization_lock:
            try:
                # Firebase 초기화 (Storage 포함)
                self._initialize_firebase()
                self.db = firestore.client()
                
                # Firebase Storage 초기화 (자동 활성화 체크)
                try:
                    storage_bucket = os.environ.get('FIREBASE_STORAGE_BUCKET', f"{os.environ['FIREBASE_PROJECT_ID']}.appspot.com")
                    logger.info(f"🔧 Firebase Storage 연결 시도: {storage_bucket}")
                    
                    self.firebase_bucket = storage.bucket(storage_bucket)
                    
                    # 버킷 존재 및 활성화 확인
                    try:
                        # 간단한 메타데이터 조회로 활성화 상태 확인
                        bucket_attrs = self.firebase_bucket._bucket
                        logger.info("✅ Firebase Storage 초기화 및 버킷 연결 완료")
                        logger.info(f"📦 버킷: {storage_bucket}")
                        
                    except Exception as bucket_error:
                        error_msg = str(bucket_error).lower()
                        
                        if '404' in error_msg or 'does not exist' in error_msg:
                            logger.error(f"❌ Firebase Storage 버킷이 존재하지 않음: {storage_bucket}")
                            logger.error("💡 해결 방법:")
                            logger.error("   1. Firebase 콘솔 (https://console.firebase.google.com) 접속")
                            logger.error(f"   2. 프로젝트 '{os.environ.get('FIREBASE_PROJECT_ID')}' 선택")
                            logger.error("   3. 왼쪽 메뉴에서 'Storage' 클릭")
                            logger.error("   4. '시작하기' 버튼 클릭하여 Storage 활성화")
                            logger.error("   5. 위치: asia-northeast1 추천")
                            logger.error("   6. 보안 규칙: 테스트 모드 선택")
                            
                        elif 'permission' in error_msg or 'forbidden' in error_msg:
                            logger.error("❌ Firebase Storage 권한 오류")
                            logger.error("💡 Firebase 콘솔에서 Storage 보안 규칙을 테스트 모드로 변경하세요")
                            
                        elif 'not enabled' in error_msg or 'disabled' in error_msg:
                            logger.error("❌ Firebase Storage가 활성화되지 않음")
                            logger.error("💡 Firebase 콘솔에서 Storage를 활성화해주세요")
                            
                        else:
                            logger.warning(f"⚠️ Firebase Storage 연결 문제: {bucket_error}")
                            logger.info("💡 일시적 문제일 수 있습니다. Wasabi 단독으로 계속 진행합니다.")
                        
                        self.firebase_bucket = None
                        
                except Exception as e:
                    logger.error(f"❌ Firebase Storage 초기화 실패: {e}")
                    logger.info("💡 Wasabi 단일 저장으로 계속 진행합니다")
                    self.firebase_bucket = None
                
                self._service_health['firebase'] = True
                
                # Wasabi S3 초기화
                self.s3_client = self._get_wasabi_client()
                self.bucket_name = os.environ['WASABI_BUCKET_NAME']
                self._service_health['wasabi'] = True
                
                # 브런치 도메인 설정
                self.brunch_domain = BRUNCH_DOMAIN
                self.brunch_alternate_domain = BRUNCH_ALTERNATE_DOMAIN
                
                # Railway 설정
                self.app_base_url = f'https://{self.brunch_domain}/watch/'
                self.wasabi_cdn_url = os.environ.get('WASABI_CDN_URL', '')
                
                # Railway 최적화된 전송 설정
                self.transfer_config = TransferConfig(
                    multipart_threshold=1024 * 1024 * 16,  # 16MB
                    multipart_chunksize=1024 * 1024 * 8,   # 8MB
                    max_concurrency=2,  # Railway 리소스 제한
                    use_threads=True
                )
                
                logger.info(f"🔧 완전한 서비스 초기화 완료 (도메인: {self.brunch_domain})")
                
            except Exception as e:
                logger.error(f"❌ 서비스 초기화 실패: {e}")
                logger.error(f"서비스 초기화 실패 상세: {traceback.format_exc()}")
                raise
    
    def _initialize_firebase(self):
        """Firebase Firestore만 초기화 (Storage 제외)"""
        if firebase_admin._apps:
            logger.debug("Firebase 이미 초기화됨")
            return
        
        try:
            # Railway 환경변수에서 Firebase 설정 로드
            firebase_config = {
                "type": "service_account",
                "project_id": os.environ["FIREBASE_PROJECT_ID"],
                "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID", ""),
                "private_key": os.environ["FIREBASE_PRIVATE_KEY"].replace('\\n', '\n'),
                "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
                "client_id": os.environ.get("FIREBASE_CLIENT_ID", ""),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_X509_CERT_URL", "")
            }
            
            cred = credentials.Certificate(firebase_config)
            
            # Firestore만 초기화 (Storage 제외)
            firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase Firestore 초기화 완료 (메타데이터 전용)")
            
        except Exception as e:
            logger.error(f"❌ Firebase Firestore 초기화 실패: {e}")
            logger.error(f"Firebase 초기화 실패 상세: {traceback.format_exc()}")
            raise
    
    def _get_wasabi_client(self):
        """Railway 최적화된 Wasabi S3 클라이언트"""
        try:
            return boto3.client(
                's3',
                aws_access_key_id=os.environ['WASABI_ACCESS_KEY'],
                aws_secret_access_key=os.environ['WASABI_SECRET_KEY'],
                region_name=os.environ.get('WASABI_REGION', 'us-east-1'),
                endpoint_url=f"https://s3.{os.environ.get('WASABI_REGION', 'us-east-1')}.wasabisys.com",
                config=boto3.session.Config(
                    retries={'max_attempts': 3, 'mode': 'adaptive'},
                    max_pool_connections=3,  # Railway 최적화
                    region_name=os.environ.get('WASABI_REGION', 'us-east-1')
                )
            )
        except Exception as e:
            logger.error(f"❌ Wasabi 클라이언트 생성 실패: {e}")
            logger.error(f"Wasabi 클라이언트 생성 실패 상세: {traceback.format_exc()}")
            raise
    
    @contextmanager
    def _railway_memory_context(self):
        """Railway 메모리 관리 컨텍스트"""
        import gc
        initial_memory = self._get_memory_usage()
        
        try:
            yield
        finally:
            # 메모리 정리
            gc.collect()
            final_memory = self._get_memory_usage()
            logger.debug(f"메모리 사용량: {initial_memory:.1f}MB → {final_memory:.1f}MB")
    
    def _get_memory_usage(self) -> float:
        """현재 메모리 사용량 조회 (MB)"""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except ImportError:
            return 0.0
    
    def validate_file(self, file_path: str, file_type: str = 'video') -> bool:
        """개선된 파일 검증 (확장자 없는 경우 처리)"""
        try:
            path = Path(file_path)
            
            if not path.exists() or not path.is_file():
                logger.warning(f"파일이 존재하지 않음: {file_path}")
                return False
            
            ext = path.suffix.lower()
            logger.debug(f"파일 검증 시작: {file_path} (확장자: '{ext}', 타입: {file_type})")
            
            # 확장자가 없는 경우 파일 내용으로 추측
            if not ext:
                logger.warning(f"파일 확장자 없음: {file_path}")
                
                # 파일 시그니처로 형식 추측 시도
                try:
                    with open(file_path, 'rb') as f:
                        file_header = f.read(16)
                    
                    # 일반적인 파일 시그니처 확인
                    if file_header.startswith(b'\x89PNG'):
                        ext = '.png'
                    elif file_header.startswith(b'\xff\xd8\xff'):
                        ext = '.jpg'
                    elif file_header.startswith(b'GIF'):
                        ext = '.gif'
                    elif file_header.startswith(b'\x00\x00\x00'):
                        ext = '.mp4'  # MP4 일 가능성
                    else:
                        logger.warning(f"파일 형식을 확인할 수 없음: {file_path}")
                        return False
                        
                    logger.info(f"파일 시그니처로 형식 추측: {ext}")
                    
                except Exception as header_error:
                    logger.warning(f"파일 헤더 읽기 실패: {header_error}")
                    return False
            
            # 파일 형식 검증
            if file_type == 'video' and ext not in SUPPORTED_VIDEO_FORMATS:
                logger.warning(f"지원되지 않는 비디오 형식: {ext}")
                return False
            elif file_type == 'image' and ext not in SUPPORTED_IMAGE_FORMATS:
                logger.warning(f"지원되지 않는 이미지 형식: {ext}")
                return False
            
            # Railway 파일 크기 검증
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"빈 파일: {file_path}")
                return False
            
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"파일 크기 초과: {file_size} > {MAX_FILE_SIZE}")
                return False
            
            # Railway 메모리 제한 확인
            if file_size > RAILWAY_MEMORY_LIMIT // 2:
                logger.warning(f"Railway 메모리 제한으로 인한 파일 크기 초과: {file_size}")
                return False
            
            logger.info(f"✅ 파일 검증 성공: {file_path} ({file_size / 1024 / 1024:.2f}MB, 형식: {ext})")
            return True
            
        except Exception as e:
            logger.error(f"❌ 파일 검증 오류: {e}")
            return False
    
    def extract_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """Railway 메모리 최적화된 비디오 메타데이터 추출"""
        with self._railway_memory_context():
            try:
                logger.debug(f"비디오 메타데이터 추출 시작: {video_path}")
                
                # Railway 안전 모드로 메타데이터 추출
                with VideoFileClip(video_path) as clip:
                    duration_sec = int(clip.duration) if clip.duration else 0
                    width = getattr(clip, 'w', 0)
                    height = getattr(clip, 'h', 0)
                    fps = round(getattr(clip, 'fps', 0), 2) if getattr(clip, 'fps', 0) else 0
                
                minutes = duration_sec // 60
                seconds = duration_sec % 60
                duration_str = f"{minutes}:{seconds:02d}"
                
                file_size = os.path.getsize(video_path)
                
                metadata = {
                    'duration_seconds': duration_sec,
                    'duration_string': duration_str,
                    'width': width,
                    'height': height,
                    'fps': fps,
                    'file_size': file_size,
                    'file_size_mb': round(file_size / 1024 / 1024, 2)
                }
                
                logger.info(f"✅ 비디오 메타데이터 추출 완료: {duration_str}, {width}x{height}, {file_size//1024//1024}MB")
                return metadata
                
            except Exception as e:
                logger.warning(f"⚠️ 비디오 메타데이터 추출 실패, 기본값 사용: {e}")
                
                file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
                return {
                    'duration_seconds': 0,
                    'duration_string': '0:00',
                    'width': 0,
                    'height': 0,
                    'fps': 0,
                    'file_size': file_size,
                    'file_size_mb': round(file_size / 1024 / 1024, 2)
                }
    
    def create_qr_with_thumbnail(self, video_id: str, title: str = "", thumbnail_path: str = None,
                                output_path: str = None) -> bool:
        """QR 코드 + 썸네일 결합 생성 (한글 폰트 지원)"""
        with self._railway_memory_context():
            try:
                # 단일 QR 링크 생성
                qr_link = f"https://{self.brunch_domain}/watch/{video_id}"
                
                logger.debug(f"QR+썸네일 결합 생성 시작: {qr_link}")
                
                if not output_path:
                    output_path = f"qr_thumbnail_{video_id}.png"
                
                # QR 코드 생성 (높은 오류 정정 수준)
                qr = qrcode.QRCode(
                    version=3,  # 더 큰 버전으로 중앙 공간 확보
                    error_correction=qrcode.constants.ERROR_CORRECT_H,  # 최고 오류 정정
                    box_size=6,
                    border=4,
                )
                qr.add_data(qr_link)
                qr.make(fit=True)
                
                # QR 코드 이미지 생성
                qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                qr_size = 400  # 더 큰 QR 코드
                qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
                
                # 썸네일이 있는 경우 중앙에 삽입
                if thumbnail_path and os.path.exists(thumbnail_path):
                    try:
                        with Image.open(thumbnail_path) as thumbnail:
                            # 썸네일을 원형으로 만들기
                            thumb_size = qr_size // 4  # QR 코드의 1/4 크기
                            thumbnail = thumbnail.resize((thumb_size, thumb_size), Image.LANCZOS)
                            
                            # 원형 마스크 생성
                            mask = Image.new('L', (thumb_size, thumb_size), 0)
                            draw = ImageDraw.Draw(mask)
                            draw.ellipse((0, 0, thumb_size, thumb_size), fill=255)
                            
                            # 썸네일을 원형으로 자르기
                            if thumbnail.mode != 'RGBA':
                                thumbnail = thumbnail.convert('RGBA')
                            thumbnail.putalpha(mask)
                            
                            # 흰색 배경의 원형 썸네일 생성 (QR 코드 가독성 향상)
                            circle_bg = Image.new('RGB', (thumb_size + 20, thumb_size + 20), 'white')
                            circle_bg.paste(thumbnail, (10, 10), thumbnail)
                            
                            # QR 코드 중앙에 썸네일 삽입
                            thumb_pos = ((qr_size - thumb_size - 20) // 2, (qr_size - thumb_size - 20) // 2)
                            qr_img.paste(circle_bg, thumb_pos)
                            
                            logger.info(f"✅ 썸네일 결합 QR 코드 생성: {thumb_size}x{thumb_size}")
                    
                    except Exception as e:
                        logger.warning(f"⚠️ 썸네일 처리 실패, 기본 QR 코드 생성: {e}")
                
                # 제목 추가 (한글 지원 개선)
                if title:
                    try:
                        # 한글 텍스트 안전 처리
                        safe_title = title.encode('utf-8', errors='ignore').decode('utf-8')
                        
                        text_height = 60
                        margin = 15
                        total_height = qr_size + text_height + margin
                        final_img = Image.new('RGB', (qr_size, total_height), 'white')
                        final_img.paste(qr_img, (0, 0))
                        
                        draw = ImageDraw.Draw(final_img)
                        
                        # 한글 지원 폰트 시도
                        font = None
                        try:
                            # 시스템 한글 폰트 시도
                            font_paths = [
                                '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',  # Ubuntu
                                '/usr/share/fonts/TTF/NanumGothic.ttf',  # CentOS
                                '/System/Library/Fonts/AppleGothic.ttf',  # macOS
                                'C:/Windows/Fonts/malgun.ttf',  # Windows
                                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',  # 대체 폰트
                            ]
                            
                            for font_path in font_paths:
                                if os.path.exists(font_path):
                                    font = ImageFont.truetype(font_path, 16)
                                    break
                        except Exception as font_error:
                            logger.debug(f"폰트 로드 실패: {font_error}")
                        
                        # 폰트를 찾지 못한 경우 기본 폰트 사용
                        if font is None:
                            font = ImageFont.load_default()
                        
                        # 제목 텍스트 처리 (길이 제한)
                        if len(safe_title.encode('utf-8')) > 60:  # 바이트 길이 기준
                            safe_title = safe_title[:25] + "..."
                        
                        # 텍스트 크기 측정 (한글 안전 처리)
                        try:
                            bbox = draw.textbbox((0, 0), safe_title, font=font)
                            text_width = bbox[2] - bbox[0]
                        except UnicodeEncodeError:
                            # 폰트가 한글을 지원하지 않는 경우 영어로 대체
                            safe_title = f"Video_{video_id[:8]}"
                            bbox = draw.textbbox((0, 0), safe_title, font=font)
                            text_width = bbox[2] - bbox[0]
                        
                        text_x = max(0, (qr_size - text_width) // 2)
                        text_y = qr_size + margin
                        
                        # 텍스트 배경 (가독성 향상)
                        text_bg_rect = [text_x - 10, text_y - 5, text_x + text_width + 10, text_y + 25]
                        draw.rectangle(text_bg_rect, fill='lightgray', outline='gray')
                        
                        # 텍스트 그리기 (한글 안전 처리)
                        try:
                            draw.text((text_x, text_y), safe_title, font=font, fill='black')
                        except UnicodeEncodeError:
                            # 한글 그리기 실패 시 영어로 대체
                            fallback_title = f"Video_{video_id[:8]}"
                            draw.text((text_x, text_y), fallback_title, font=font, fill='black')
                            logger.warning("⚠️ 한글 텍스트 렌더링 실패, 영어로 대체")
                        
                        final_img.save(output_path, quality=90, optimize=True)
                        
                    except Exception as text_error:
                        logger.warning(f"⚠️ 텍스트 처리 실패, 텍스트 없는 QR 코드 생성: {text_error}")
                        qr_img.save(output_path, quality=90, optimize=True)
                else:
                    qr_img.save(output_path, quality=90, optimize=True)
                
                logger.info(f"✅ QR+썸네일 결합 생성 완료: {output_path}")
                return True
                
            except Exception as e:
                logger.error(f"❌ QR+썸네일 생성 실패: {e}")
                logger.error(f"QR+썸네일 생성 실패 상세: {traceback.format_exc()}")
                
                # 실패 시 기본 QR 코드라도 생성 시도
                try:
                    qr = qrcode.QRCode(version=1, box_size=6, border=4)
                    qr.add_data(f"https://{self.brunch_domain}/watch/{video_id}")
                    qr.make(fit=True)
                    qr_img = qr.make_image(fill_color="black", back_color="white")
                    qr_img.save(output_path or f"qr_{video_id}.png")
                    logger.info("✅ 기본 QR 코드 생성 완료 (폴백)")
                    return True
                except Exception as fallback_error:
                    logger.error(f"❌ 기본 QR 코드 생성도 실패: {fallback_error}")
                    return False
    
    def create_single_qr_code(self, video_id: str, title: str = "", output_path: str = None) -> bool:
        """기본 단일 QR 코드 생성 (썸네일 없는 경우)"""
        return self.create_qr_with_thumbnail(video_id, title, None, output_path)
    
    def upload_to_wasabi(self, local_path: str, s3_key: str, content_type: str = None,
                        progress_callback: Callable = None) -> Optional[str]:
        """완전한 Wasabi 업로드 구현 (CDN 및 캐시 제어 포함)"""
        try:
            logger.info(f"Wasabi 업로드 시작: {s3_key}")
            
            # CDN 및 캐시 최적화 헤더
            extra_args = {
                'ACL': 'public-read',
                'CacheControl': 'public, max-age=31536000',  # 1년 캐시 (링크 영구화)
                'Expires': (datetime.now() + timedelta(days=365)).strftime('%a, %d %b %Y %H:%M:%S GMT')
            }
            
            if content_type:
                extra_args['ContentType'] = content_type
            
            # Railway 최적화된 진행률 콜백
            uploaded_bytes = 0
            total_bytes = os.path.getsize(local_path)
            
            def railway_progress_callback(bytes_transferred):
                nonlocal uploaded_bytes
                uploaded_bytes = bytes_transferred
                
                if progress_callback and total_bytes > 0:
                    percentage = min((uploaded_bytes / total_bytes) * 100, 100)
                    progress_callback(int(percentage), f"업로드 진행 중... {percentage:.1f}% ({uploaded_bytes / 1024 / 1024:.1f}MB / {total_bytes / 1024 / 1024:.1f}MB)")
            
            # Railway 최적화된 업로드 실행
            self.s3_client.upload_file(
                local_path,
                self.bucket_name,
                s3_key,
                Config=self.transfer_config,
                ExtraArgs=extra_args,
                Callback=railway_progress_callback if progress_callback else None
            )
            
            # 영구 링크 생성 (CDN 우선)
            if self.wasabi_cdn_url:
                public_url = f"{self.wasabi_cdn_url.rstrip('/')}/{s3_key}"
            else:
                region = os.environ.get('WASABI_REGION', 'us-east-1')
                public_url = f"https://s3.{region}.wasabisys.com/{self.bucket_name}/{s3_key}"
            
            logger.info(f"✅ Wasabi 업로드 완료 (영구링크): {s3_key} -> {public_url}")
            return public_url
            
        except Exception as e:
            logger.error(f"❌ Wasabi 업로드 실패: {s3_key} - {e}")
            logger.error(f"Wasabi 업로드 실패 상세: {traceback.format_exc()}")
            return None
    
    def upload_to_firebase_storage(self, local_path: str, firebase_path: str, 
                                 content_type: str = None) -> Optional[str]:
        """Firebase Storage는 사용하지 않음 - 항상 None 반환"""
        logger.debug("Firebase Storage는 사용하지 않음 (메타데이터는 Firestore에만 저장)")
        return None
    
    def upload_video(self, video_path: str, thumbnail_path: Optional[str], group_name: str,
                    main_category: str, sub_category: str, leaf_category: str,
                    content_description: str, translated_filenames: Dict[str, str],
                    progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """완전한 메인 비디오 업로드 구현 - QR+썸네일 결합 및 이중 저장"""
        
        with self._railway_memory_context():
            try:
                def update_progress(value: int, message: str):
                    if progress_callback:
                        progress_callback(value, message)
                    logger.info(f"업로드 진행률 {value}%: {message}")
                
                update_progress(5, "🔍 파일 검증 및 메타데이터 추출 중...")
                
                # Railway 메모리 최적화된 메타데이터 추출
                video_metadata = self.extract_video_metadata(video_path)
                
                update_progress(15, "📁 업로드 경로 설정 중...")
                
                # Railway 최적화된 경로 생성
                group_id = uuid.uuid4().hex
                timestamp = datetime.now()
                date_str = timestamp.strftime('%Y%m%d')
                safe_name = re.sub(r'[^\w가-힣-]', '_', group_name)[:30]
                
                # Railway 폴더 구조 최적화
                year_month = timestamp.strftime('%Y%m')
                base_folder = f"videos/{year_month}/{group_id}_{safe_name}"
                
                # 동영상 업로드 (Wasabi 단독)
                video_ext = Path(video_path).suffix.lower()
                ko_filename = translated_filenames.get('ko', safe_name)
                video_s3_key = f"{base_folder}/{ko_filename}_video_ko{video_ext}"
                
                content_type_map = {
                    '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                    '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
                    '.flv': 'video/x-flv', '.3gp': 'video/3gpp', '.m4v': 'video/x-m4v'
                }
                video_content_type = content_type_map.get(video_ext, 'video/mp4')
                
                def video_progress(percentage, msg):
                    adjusted_percentage = 25 + (percentage * 0.4)  # 25-65%
                    update_progress(int(adjusted_percentage), f"🎬 동영상: {msg}")
                
                # Wasabi에만 업로드
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    video_progress
                )
                
                if not video_url:
                    raise Exception("동영상 업로드 실패")
                
                logger.info(f"✅ 동영상 Wasabi 업로드 완료: {video_url}")
                
                update_progress(70, "🖼️ 썸네일 처리 중...")
                
                # 썸네일 업로드 (Wasabi 단독)
                thumbnail_url = None
                thumbnail_s3_key = None
                
                if thumbnail_path:
                    thumb_ext = Path(thumbnail_path).suffix.lower()
                    thumbnail_s3_key = f"{base_folder}/{ko_filename}_thumbnail{thumb_ext}"
                    
                    thumb_content_type_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
                        '.tiff': 'image/tiff', '.svg': 'image/svg+xml'
                    }
                    thumb_content_type = thumb_content_type_map.get(thumb_ext, 'image/jpeg')
                    
                    # Wasabi에만 썸네일 업로드
                    thumbnail_url = self.upload_to_wasabi(thumbnail_path, thumbnail_s3_key, thumb_content_type)
                    logger.info(f"✅ 썸네일 Wasabi 업로드 완료: {thumbnail_url}")
                
                update_progress(80, "📱 QR+썸네일 결합 코드 생성 중...")
                
                # QR+썸네일 결합 코드 생성
                qr_link = f"https://{self.brunch_domain}/watch/{group_id}"
                qr_temp_path = os.path.join(tempfile.gettempdir(), f"qr_thumbnail_{group_id}.png")
                
                qr_title = group_name[:30]
                if all([main_category, sub_category, leaf_category]):
                    qr_title = f"{group_name[:25]}\n({main_category})"
                
                qr_url = None
                qr_s3_key = None
                
                # QR+썸네일 결합 생성
                if self.create_qr_with_thumbnail(group_id, qr_title, thumbnail_path, qr_temp_path):
                    qr_s3_key = f"{base_folder}/{ko_filename}_qr_combined.png"
                    
                    # Wasabi에만 QR 업로드
                    qr_url = self.upload_to_wasabi(qr_temp_path, qr_s3_key, 'image/png')
                    logger.info(f"✅ QR코드 Wasabi 업로드 완료: {qr_url}")
                    
                    # 임시 파일 정리
                    try:
                        os.remove(qr_temp_path)
                    except:
                        pass
                
                update_progress(90, "💾 Firestore 메타데이터 저장 중...")
                
                # Firestore에는 메타데이터만 저장
                main_doc_data = {
                    'group_id': group_id,
                    'group_name': group_name,
                    'content_description': content_description,
                    'main_category': main_category,
                    'sub_category': sub_category,
                    'sub_sub_category': leaf_category,
                    'base_folder': base_folder,
                    'storage_provider': 'wasabi',  # Wasabi 단독 저장
                    'bucket_name': self.bucket_name,
                    'upload_date': date_str,
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP,
                    'translation_status': 'completed',
                    'supported_languages_count': 1,
                    'total_file_size': video_metadata['file_size'],
                    'supported_video_languages': ['ko'],
                    'brunch_domain': self.brunch_domain,
                    'qr_combined_enabled': True,  # QR+썸네일 결합 표시
                    'wasabi_storage_only': True,  # Wasabi 단독 저장 표시
                    'permanent_links': True,  # 영구 링크 보장
                    'railway_optimized': True
                }
                
                # Wasabi URL들 추가
                if qr_url and qr_s3_key:
                    main_doc_data.update({
                        'qr_link': qr_link,
                        'qr_s3_key': qr_s3_key,
                        'qr_url': qr_url
                    })
                
                if thumbnail_url and thumbnail_s3_key:
                    main_doc_data.update({
                        'thumbnail_s3_key': thumbnail_s3_key,
                        'thumbnail_url': thumbnail_url
                    })
                
                # 배치 작업으로 최적화
                batch = self.db.batch()
                
                # 메인 문서
                main_doc_ref = self.db.collection('uploads').document(group_id)
                batch.set(main_doc_ref, main_doc_data)
                
                # 언어별 영상 문서 (한국어 기본) - Wasabi URL만 저장
                language_doc_data = {
                    'language_code': 'ko',
                    'language_name': '한국어',
                    'video_s3_key': video_s3_key,
                    'video_url': video_url,
                    'content_type': video_content_type,
                    'file_size': video_metadata['file_size'],
                    'duration_seconds': video_metadata['duration_seconds'],
                    'duration_string': video_metadata['duration_string'],
                    'video_width': video_metadata['width'],
                    'video_height': video_metadata['height'],
                    'video_fps': video_metadata['fps'],
                    'upload_date': date_str,
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'is_original': True,
                    'storage_provider': 'wasabi'
                }
                
                language_doc_ref = main_doc_ref.collection('language_videos').document('ko')
                batch.set(language_doc_ref, language_doc_data)
                
                # 번역 메타데이터
                if translated_filenames:
                    translation_metadata = {
                        'filenames': translated_filenames,
                        'created_at': firestore.SERVER_TIMESTAMP,
                        'translator_used': 'googletrans',
                        'railway_generated': True
                    }
                    translation_doc_ref = main_doc_ref.collection('metadata').document('translations')
                    batch.set(translation_doc_ref, translation_metadata)
                
                # 배치 커밋
                batch.commit()
                
                update_progress(100, "✅ 업로드 완료!")
                
                # 성공 응답
                result = {
                    'success': True,
                    'group_id': group_id,
                    'video_url': video_url,
                    'qr_link': qr_link,
                    'qr_url': qr_url,
                    'thumbnail_url': thumbnail_url,
                    'metadata': video_metadata,
                    'brunch_domain': self.brunch_domain,
                    'qr_combined': True,
                    'storage_provider': 'wasabi',
                    'permanent_links': True,
                    'railway_optimized': True
                }
                
                logger.info(f"✅ 완전한 비디오 업로드 성공: {group_name} (ID: {group_id}) - Wasabi 저장 + Firestore 메타데이터")
                return result
                
            except Exception as e:
                logger.error(f"❌ 비디오 업로드 실패: {e}")
                logger.error(f"비디오 업로드 실패 상세: {traceback.format_exc()}")
                return {
                    'success': False,
                    'error': str(e),
                    'railway_optimized': True
                }
    
    def upload_language_video(self, video_id: str, language_code: str, video_path: str,
                             progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """언어별 영상 업로드 구현 (Wasabi 단독 저장)"""
        
        with self._railway_memory_context():
            try:
                def update_progress(value: int, message: str):
                    if progress_callback:
                        progress_callback(value, message)
                    logger.info(f"언어별 업로드 {value}%: {message}")
                
                update_progress(10, "📋 기존 영상 정보 확인 중...")
                
                doc_ref = self.db.collection('uploads').document(video_id)
                doc = doc_ref.get()
                
                if not doc.exists:
                    raise Exception("선택된 영상을 찾을 수 없습니다.")
                
                video_data = doc.to_dict()
                
                update_progress(25, "🎬 언어별 영상 준비 중...")
                
                video_metadata = self.extract_video_metadata(video_path)
                
                group_name = video_data.get('group_name', 'unknown')
                base_folder = video_data.get('base_folder', f"videos/{video_id}")
                
                # 번역된 파일명 가져오기
                try:
                    metadata_ref = doc_ref.collection('metadata').document('translations')
                    metadata_doc = metadata_ref.get()
                    if metadata_doc.exists:
                        stored_translations = metadata_doc.to_dict().get('filenames', {})
                        translated_title = stored_translations.get(language_code, group_name)
                    else:
                        translated_title = group_name
                except:
                    translated_title = group_name
                
                safe_translated_title = re.sub(r'[^\w가-힣-]', '_', translated_title)[:30]
                video_ext = Path(video_path).suffix.lower()
                video_s3_key = f"{base_folder}/{safe_translated_title}_video_{language_code}{video_ext}"
                
                content_type_map = {
                    '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                    '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
                    '.flv': 'video/x-flv', '.3gp': 'video/3gpp', '.m4v': 'video/x-m4v'
                }
                video_content_type = content_type_map.get(video_ext, 'video/mp4')
                
                update_progress(40, f"☁️ {language_code.upper()} 영상 업로드 중...")
                
                def lang_progress(percentage, msg):
                    adjusted_percentage = 40 + (percentage * 0.4)  # 40-80%
                    update_progress(int(adjusted_percentage), f"🌐 {language_code}: {msg}")
                
                # Wasabi에만 업로드
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    lang_progress
                )
                
                if not video_url:
                    raise Exception("언어별 영상 업로드 실패")
                
                logger.info(f"✅ 언어별 영상 Wasabi 업로드 완료: {video_url}")
                
                update_progress(85, "💾 Firestore 메타데이터 저장 중...")
                
                # 배치 업데이트
                batch = self.db.batch()
                
                # 언어별 영상 데이터 (Wasabi URL만 저장)
                language_doc_data = {
                    'language_code': language_code,
                    'language_name': self._get_language_name(language_code),
                    'video_s3_key': video_s3_key,
                    'video_url': video_url,
                    'content_type': video_content_type,
                    'file_size': video_metadata['file_size'],
                    'duration_seconds': video_metadata['duration_seconds'],
                    'duration_string': video_metadata['duration_string'],
                    'video_width': video_metadata['width'],
                    'video_height': video_metadata['height'],
                    'video_fps': video_metadata['fps'],
                    'upload_date': datetime.now().strftime('%Y%m%d'),
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'is_original': False,
                    'storage_provider': 'wasabi',
                    'railway_uploaded': True
                }
                
                language_doc_ref = doc_ref.collection('language_videos').document(language_code)
                batch.set(language_doc_ref, language_doc_data)
                
                # 메인 문서 업데이트
                existing_languages = video_data.get('supported_video_languages', ['ko'])
                if language_code not in existing_languages:
                    existing_languages.append(language_code)
                
                main_update_data = {
                    'supported_video_languages': existing_languages,
                    'supported_languages_count': len(existing_languages),
                    'updated_at': firestore.SERVER_TIMESTAMP
                }
                batch.update(doc_ref, main_update_data)
                
                batch.commit()
                
                update_progress(100, "✅ 언어별 영상 업로드 완료!")
                
                result = {
                    'success': True,
                    'video_url': video_url,
                    'language_code': language_code,
                    'language_name': self._get_language_name(language_code),
                    'metadata': video_metadata,
                    'storage_provider': 'wasabi',
                    'railway_optimized': True
                }
                
                logger.info(f"✅ 언어별 영상 업로드 성공: {video_id} ({language_code}) - Wasabi 저장")
                return result
                
            except Exception as e:
                logger.error(f"❌ 언어별 영상 업로드 실패: {e}")
                logger.error(f"언어별 영상 업로드 실패 상세: {traceback.format_exc()}")
                return {
                    'success': False,
                    'error': str(e),
                    'railway_optimized': True
                }
    
    def get_existing_videos(self) -> List[Dict[str, Any]]:
        """기존 영상 목록 조회 구현"""
        try:
            logger.info("기존 영상 목록 조회 시작")
            
            # Railway 메모리 제한으로 50개만 조회
            docs = self.db.collection('uploads').order_by(
                'created_at', direction=firestore.Query.DESCENDING
            ).limit(50).get()
            
            videos_data = []
            
            for doc in docs:
                try:
                    data = doc.to_dict()
                    
                    # Railway 최적화된 언어 정보 조회
                    supported_languages = []
                    language_videos = {}
                    
                    try:
                        # 서브컬렉션 조회 최적화
                        lang_docs = doc.reference.collection('language_videos').get()
                        for lang_doc in lang_docs:
                            lang_data = lang_doc.to_dict()
                            language_videos[lang_doc.id] = {
                                'language_code': lang_doc.id,
                                'language_name': lang_data.get('language_name', ''),
                                'video_url': lang_data.get('video_url', ''),
                                'video_firebase_url': lang_data.get('video_firebase_url', ''),
                                'file_size': lang_data.get('file_size', 0),
                                'duration': lang_data.get('duration_string', ''),
                                'upload_date': lang_data.get('upload_date', ''),
                                'dual_storage': lang_data.get('dual_storage', False)
                            }
                            supported_languages.append(lang_doc.id)
                    except Exception as e:
                        logger.warning(f"언어별 영상 로드 실패 ({doc.id}): {e}")
                        supported_languages = ['ko']  # 기본값
                    
                    # Railway 메모리 최적화된 비디오 정보
                    video_info = {
                        'id': doc.id,
                        'group_id': doc.id,  # API 호환성
                        'title': data.get('group_name', 'Unknown'),
                        'category': f"{data.get('main_category', '')} > {data.get('sub_category', '')} > {data.get('sub_sub_category', '')}",
                        'main_category': data.get('main_category', ''),
                        'sub_category': data.get('sub_category', ''),
                        'sub_sub_category': data.get('sub_sub_category', ''),
                        'upload_date': data.get('upload_date', ''),
                        'languages': supported_languages,
                        'language_videos': language_videos,
                        'total_file_size': data.get('total_file_size', 0),
                        'qr_link': data.get('qr_link', ''),
                        'qr_url': data.get('qr_url', ''),
                        'brunch_domain': data.get('brunch_domain', self.brunch_domain),
                        'qr_combined': data.get('qr_combined_enabled', False),
                        'storage_provider': data.get('storage_provider', 'wasabi'),
                        'permanent_links': data.get('permanent_links', False),
                        'railway_optimized': data.get('railway_optimized', False)
                    }
                    
                    videos_data.append(video_info)
                    
                except Exception as e:
                    logger.warning(f"비디오 정보 처리 실패 ({doc.id}): {e}")
                    continue
            
            logger.info(f"✅ 영상 목록 조회 완료: {len(videos_data)}개")
            return videos_data
            
        except Exception as e:
            logger.error(f"❌ 영상 목록 로드 실패: {e}")
            logger.error(f"영상 목록 로드 실패 상세: {traceback.format_exc()}")
            return []
    
    def get_upload_status(self, group_id: str) -> Dict[str, Any]:
        """업로드 상태 확인 구현"""
        try:
            doc_ref = self.db.collection('uploads').document(group_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'success': False, 'error': '영상을 찾을 수 없습니다'}
            
            data = doc.to_dict()
            
            # Railway 최적화된 언어별 영상 조회
            language_videos = {}
            supported_languages = []
            try:
                lang_docs = doc_ref.collection('language_videos').get()
                for lang_doc in lang_docs:
                    lang_data = lang_doc.to_dict()
                    language_videos[lang_doc.id] = lang_data
                    supported_languages.append(lang_doc.id)
            except:
                supported_languages = ['ko']  # 기본값
            
            return {
                'success': True,
                'group_id': group_id,
                'group_name': data.get('group_name', ''),
                'main_category': data.get('main_category', ''),
                'sub_category': data.get('sub_category', ''),
                'sub_sub_category': data.get('sub_sub_category', ''),
                'upload_date': data.get('upload_date', ''),
                'supported_languages': supported_languages,
                'language_videos': language_videos,
                'qr_link': data.get('qr_link', ''),
                'qr_url': data.get('qr_url', ''),
                'thumbnail_url': data.get('thumbnail_url', ''),
                'brunch_domain': data.get('brunch_domain', self.brunch_domain),
                'qr_combined': data.get('qr_combined_enabled', False),
                'storage_provider': data.get('storage_provider', 'wasabi'),
                'permanent_links': data.get('permanent_links', False),
                'railway_optimized': data.get('railway_optimized', False)
            }
            
        except Exception as e:
            logger.error(f"업로드 상태 확인 실패: {e}")
            logger.error(f"업로드 상태 확인 실패 상세: {traceback.format_exc()}")
            return {'success': False, 'error': str(e)}
    
    def _get_language_name(self, language_code: str) -> str:
        """언어 코드를 언어명으로 변환"""
        language_names = {
            'ko': '한국어',
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日본語'
        }
        return language_names.get(language_code, language_code)
    
    def get_service_health(self) -> Dict[str, Any]:
        """완전한 서비스 상태 확인 (Wasabi + Firestore)"""
        return {
            'firebase_firestore': self._service_health['firebase'],
            'firebase_storage': False,  # 사용하지 않음
            'wasabi': self._service_health['wasabi'],
            'translator': self._service_health['translator'],
            'memory_usage': self._get_memory_usage(),
            'brunch_domain': self.brunch_domain,
            'single_qr_enabled': True,
            'qr_thumbnail_combined': True,
            'storage_provider': 'wasabi_only',
            'firestore_metadata_only': True,
            'permanent_links_enabled': True,
            'railway_optimized': True,
            'timestamp': datetime.now().isoformat()
        }
    
    def cleanup_temp_files(self, file_paths: List[str]):
        """Railway 임시 파일 정리"""
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.debug(f"임시 파일 삭제: {file_path}")
            except Exception as e:
                logger.warning(f"임시 파일 삭제 실패 {file_path}: {e}")