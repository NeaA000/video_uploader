# video_uploader_logic.py - 하이브리드 프록시 방식 (Wasabi 저장 + Railway 프록시)
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
    from botocore.exceptions import ClientError
    from boto3.s3.transfer import TransferConfig
    import firebase_admin
    from firebase_admin import credentials, firestore, storage
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from googletrans import Translator
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
BRUNCH_DOMAIN = os.environ.get('BRUNCH_DOMAIN', 'videouploader-production.up.railway.app')
BRUNCH_ALTERNATE_DOMAIN = os.environ.get('BRUNCH_ALTERNATE_DOMAIN', 'videouploader-production.up.railway.app')

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
    """완전한 googletrans 번역 시스템"""
    
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
        self._cache_max_size = 100
        
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
                        not translated_text.isdigit() and
                        translated_text.lower() != clean_text.lower()):
                        
                        logger.debug(f"✅ googletrans 번역 성공: '{clean_text}' -> '{translated_text}' ({target_lang})")
                        return translated_text
                    else:
                        logger.warning(f"⚠️ googletrans 번역 결과가 의미없음: '{translated_text}' ({target_lang})")
                        
                else:
                    logger.warning(f"⚠️ googletrans 번역 결과가 비어있음: {target_lang}")
                    
            except Exception as e:
                logger.warning(f"❌ googletrans 번역 시도 {attempt + 1} 실패 ({target_lang}): {e}")
                if attempt < self.max_retries - 1:
                    wait_time = (attempt + 1) * 2
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
        if len(safe_text.encode('utf-8')) > 200:
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
    """하이브리드 비디오 업로더 - Wasabi 저장 + Railway 프록시"""
    
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
            logger.info("✅ Railway 하이브리드 업로더 완전 초기화 완료")
        except Exception as e:
            logger.error(f"❌ 비디오 업로더 초기화 실패: {e}")
            logger.error(f"초기화 실패 상세: {traceback.format_exc()}")
            raise
    
    def _initialize_services(self):
        """Railway 최적화된 서비스 초기화"""
        with self._initialization_lock:
            try:
                # Firebase Firestore 초기화 (메타데이터만)
                self._initialize_firebase()
                self.db = firestore.client()
                self._service_health['firebase'] = True
                
                # Wasabi S3 초기화
                self.s3_client = self._get_wasabi_client()
                self.bucket_name = os.environ['WASABI_BUCKET_NAME']
                self._service_health['wasabi'] = True
                
                # 브런치 도메인 설정 (Railway 프록시용)
                self.brunch_domain = BRUNCH_DOMAIN
                self.brunch_alternate_domain = BRUNCH_ALTERNATE_DOMAIN
                
                # Railway 프록시 URL 구조
                self.app_base_url = f'https://{self.brunch_domain}'
                self.proxy_endpoints = {
                    'qr': f'{self.app_base_url}/qr/',
                    'thumbnail': f'{self.app_base_url}/thumbnail/',
                    'video': f'{self.app_base_url}/video/',
                    'watch': f'{self.app_base_url}/watch/'
                }
                
                # Railway 최적화된 전송 설정
                self.transfer_config = TransferConfig(
                    multipart_threshold=1024 * 1024 * 16,  # 16MB
                    multipart_chunksize=1024 * 1024 * 8,   # 8MB
                    max_concurrency=2,  # Railway 리소스 제한
                    use_threads=True
                )
                
                logger.info(f"🔧 하이브리드 서비스 초기화 완료 (프록시 도메인: {self.brunch_domain})")
                
            except Exception as e:
                logger.error(f"❌ 서비스 초기화 실패: {e}")
                logger.error(f"서비스 초기화 실패 상세: {traceback.format_exc()}")
                raise
    
    def _initialize_firebase(self):
        """Firebase Firestore 초기화 (메타데이터 전용)"""
        if firebase_admin._apps:
            logger.debug("Firebase 이미 초기화됨")
            return
        
        try:
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
                    max_pool_connections=3,
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
        """개선된 파일 검증"""
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
                
                try:
                    with open(file_path, 'rb') as f:
                        file_header = f.read(16)
                    
                    if file_header.startswith(b'\x89PNG'):
                        ext = '.png'
                    elif file_header.startswith(b'\xff\xd8\xff'):
                        ext = '.jpg'
                    elif file_header.startswith(b'GIF'):
                        ext = '.gif'
                    elif file_header.startswith(b'\x00\x00\x00'):
                        ext = '.mp4'
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
        """QR 코드 + 썸네일 결합 생성 (Railway 프록시 URL 사용)"""
        with self._railway_memory_context():
            try:
                # Railway 프록시 URL 사용 (영구 URL)
                qr_link = f"https://{self.brunch_domain}/watch/{video_id}"
                
                logger.debug(f"QR+썸네일 결합 생성 시작: {qr_link}")
                
                if not output_path:
                    output_path = f"qr_thumbnail_{video_id}.png"
                
                # QR 코드 생성
                qr = qrcode.QRCode(
                    version=3,
                    error_correction=qrcode.constants.ERROR_CORRECT_H,
                    box_size=6,
                    border=4,
                )
                qr.add_data(qr_link)
                qr.make(fit=True)
                
                qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                qr_size = 400
                qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
                
                # 썸네일이 있는 경우 중앙에 삽입
                if thumbnail_path and os.path.exists(thumbnail_path):
                    try:
                        with Image.open(thumbnail_path) as thumbnail:
                            thumb_size = qr_size // 4
                            thumbnail = thumbnail.resize((thumb_size, thumb_size), Image.LANCZOS)
                            
                            # 원형 마스크 생성
                            mask = Image.new('L', (thumb_size, thumb_size), 0)
                            draw = ImageDraw.Draw(mask)
                            draw.ellipse((0, 0, thumb_size, thumb_size), fill=255)
                            
                            if thumbnail.mode != 'RGBA':
                                thumbnail = thumbnail.convert('RGBA')
                            thumbnail.putalpha(mask)
                            
                            circle_bg = Image.new('RGB', (thumb_size + 20, thumb_size + 20), 'white')
                            circle_bg.paste(thumbnail, (10, 10), thumbnail)
                            
                            thumb_pos = ((qr_size - thumb_size - 20) // 2, (qr_size - thumb_size - 20) // 2)
                            qr_img.paste(circle_bg, thumb_pos)
                            
                            logger.info(f"✅ 썸네일 결합 QR 코드 생성: {thumb_size}x{thumb_size}")
                    
                    except Exception as e:
                        logger.warning(f"⚠️ 썸네일 처리 실패, 기본 QR 코드 생성: {e}")
                
                # 제목 추가 (한글 지원 개선)
                if title:
                    try:
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
                            font_paths = [
                                '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
                                '/usr/share/fonts/TTF/NanumGothic.ttf',
                                '/System/Library/Fonts/AppleGothic.ttf',
                                'C:/Windows/Fonts/malgun.ttf',
                                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                            ]
                            
                            for font_path in font_paths:
                                if os.path.exists(font_path):
                                    font = ImageFont.truetype(font_path, 16)
                                    break
                        except Exception as font_error:
                            logger.debug(f"폰트 로드 실패: {font_error}")
                        
                        if font is None:
                            font = ImageFont.load_default()
                        
                        if len(safe_title.encode('utf-8')) > 60:
                            safe_title = safe_title[:25] + "..."
                        
                        try:
                            bbox = draw.textbbox((0, 0), safe_title, font=font)
                            text_width = bbox[2] - bbox[0]
                        except UnicodeEncodeError:
                            safe_title = f"Video_{video_id[:8]}"
                            bbox = draw.textbbox((0, 0), safe_title, font=font)
                            text_width = bbox[2] - bbox[0]
                        
                        text_x = max(0, (qr_size - text_width) // 2)
                        text_y = qr_size + margin
                        
                        text_bg_rect = [text_x - 10, text_y - 5, text_x + text_width + 10, text_y + 25]
                        draw.rectangle(text_bg_rect, fill='lightgray', outline='gray')
                        
                        try:
                            draw.text((text_x, text_y), safe_title, font=font, fill='black')
                        except UnicodeEncodeError:
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
    
    def upload_to_wasabi(self, local_path: str, s3_key: str, content_type: str = None,
                        progress_callback: Callable = None) -> Optional[str]:
        """Wasabi 업로드 (Railway 프록시 URL 반환)"""
        try:
            logger.info(f"Wasabi 업로드 시작: {s3_key}")
            
            # Private 업로드 설정 (공개 불필요, Railway가 프록시)
            extra_args = {
                'CacheControl': 'public, max-age=31536000',
                'Expires': (datetime.now() + timedelta(days=365)).strftime('%a, %d %b %Y %H:%M:%S GMT')
            }
            
            if content_type:
                extra_args['ContentType'] = content_type
            
            # 진행률 콜백
            uploaded_bytes = 0
            total_bytes = os.path.getsize(local_path)
            
            def railway_progress_callback(bytes_transferred):
                nonlocal uploaded_bytes
                uploaded_bytes = bytes_transferred
                
                if progress_callback and total_bytes > 0:
                    percentage = min((uploaded_bytes / total_bytes) * 100, 100)
                    progress_callback(int(percentage), f"업로드 진행 중... {percentage:.1f}% ({uploaded_bytes / 1024 / 1024:.1f}MB / {total_bytes / 1024 / 1024:.1f}MB)")
            
            # Wasabi 업로드 실행
            self.s3_client.upload_file(
                local_path,
                self.bucket_name,
                s3_key,
                Config=self.transfer_config,
                ExtraArgs=extra_args,
                Callback=railway_progress_callback if progress_callback else None
            )
            
            # Railway 프록시 URL 반환 (영구 URL)
            # 파일 타입에 따라 적절한 프록시 엔드포인트 결정
            if 'qr' in s3_key.lower():
                proxy_url = f"{self.proxy_endpoints['qr']}{s3_key}"
            elif 'thumbnail' in s3_key.lower():
                proxy_url = f"{self.proxy_endpoints['thumbnail']}{s3_key}"
            elif any(video_ext in s3_key.lower() for video_ext in ['.mp4', '.avi', '.mov', '.wmv']):
                proxy_url = f"{self.proxy_endpoints['video']}{s3_key}"
            else:
                # 기본 파일 프록시 (필요시 추가)
                proxy_url = f"{self.app_base_url}/file/{s3_key}"
            
            logger.info(f"✅ Wasabi 업로드 완료 (Railway 프록시 URL): {s3_key} -> {proxy_url}")
            return proxy_url
            
        except Exception as e:
            logger.error(f"❌ Wasabi 업로드 실패: {s3_key} - {e}")
            logger.error(f"Wasabi 업로드 실패 상세: {traceback.format_exc()}")
            return None
    
    def get_file_from_wasabi(self, s3_key: str) -> Optional[bytes]:
        """Wasabi에서 파일 다운로드 (Railway 프록시용)"""
        try:
            logger.debug(f"Wasabi에서 파일 다운로드: {s3_key}")
            
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            file_data = response['Body'].read()
            
            logger.debug(f"✅ 파일 다운로드 완료: {s3_key} ({len(file_data)} bytes)")
            return file_data
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                logger.warning(f"⚠️ 파일을 찾을 수 없음: {s3_key}")
            else:
                logger.error(f"❌ Wasabi 파일 다운로드 실패: {s3_key} - {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Wasabi 파일 다운로드 오류: {s3_key} - {e}")
            return None
    
    def get_file_metadata_from_wasabi(self, s3_key: str) -> Optional[Dict[str, Any]]:
        """Wasabi에서 파일 메타데이터 조회"""
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return {
                'content_type': response.get('ContentType', 'application/octet-stream'),
                'content_length': response.get('ContentLength', 0),
                'last_modified': response.get('LastModified'),
                'etag': response.get('ETag', '').strip('"')
            }
        except Exception as e:
            logger.error(f"❌ 파일 메타데이터 조회 실패: {s3_key} - {e}")
            return None
    
    def upload_video(self, video_path: str, thumbnail_path: Optional[str], group_name: str,
                    main_category: str, sub_category: str, leaf_category: str,
                    content_description: str, translated_filenames: Dict[str, str],
                    progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """하이브리드 메인 비디오 업로드 (Wasabi 저장 + Railway 프록시 URL)"""
        
        with self._railway_memory_context():
            try:
                def update_progress(value: int, message: str):
                    if progress_callback:
                        progress_callback(value, message)
                    logger.info(f"업로드 진행률 {value}%: {message}")
                
                update_progress(5, "🔍 파일 검증 및 메타데이터 추출 중...")
                
                video_metadata = self.extract_video_metadata(video_path)
                
                update_progress(15, "📁 업로드 경로 설정 중...")
                
                # Railway 최적화된 경로 생성
                group_id = uuid.uuid4().hex
                timestamp = datetime.now()
                date_str = timestamp.strftime('%Y%m%d')
                safe_name = re.sub(r'[^\w가-힣-]', '_', group_name)[:30]
                
                year_month = timestamp.strftime('%Y%m')
                base_folder = f"videos/{year_month}/{group_id}_{safe_name}"
                
                # 동영상 업로드 (Wasabi 저장)
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
                
                # Wasabi 업로드 (Railway 프록시 URL 반환)
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    video_progress
                )
                
                if not video_url:
                    raise Exception("동영상 업로드 실패")
                
                logger.info(f"✅ 동영상 Wasabi 업로드 완료 (Railway 프록시): {video_url}")
                
                update_progress(70, "🖼️ 썸네일 처리 중...")
                
                # 썸네일 업로드 (Wasabi 저장)
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
                    
                    # Wasabi 업로드 (Railway 프록시 URL 반환)
                    thumbnail_url = self.upload_to_wasabi(thumbnail_path, thumbnail_s3_key, thumb_content_type)
                    logger.info(f"✅ 썸네일 Wasabi 업로드 완료 (Railway 프록시): {thumbnail_url}")
                
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
                    
                    # Wasabi 업로드 (Railway 프록시 URL 반환)
                    qr_url = self.upload_to_wasabi(qr_temp_path, qr_s3_key, 'image/png')
                    logger.info(f"✅ QR코드 Wasabi 업로드 완료 (Railway 프록시): {qr_url}")
                    
                    # 임시 파일 정리
                    try:
                        os.remove(qr_temp_path)
                    except:
                        pass
                
                update_progress(90, "💾 Firestore 메타데이터 저장 중...")
                
                # Firestore에 메타데이터 저장 (Wasabi S3 키 포함)
                main_doc_data = {
                    'group_id': group_id,
                    'group_name': group_name,
                    'content_description': content_description,
                    'main_category': main_category,
                    'sub_category': sub_category,
                    'sub_sub_category': leaf_category,
                    'base_folder': base_folder,
                    'storage_provider': 'wasabi_hybrid',  # 하이브리드 방식 표시
                    'bucket_name': self.bucket_name,
                    'upload_date': date_str,
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP,
                    'translation_status': 'completed',
                    'supported_languages_count': 1,
                    'total_file_size': video_metadata['file_size'],
                    'supported_video_languages': ['ko'],
                    'brunch_domain': self.brunch_domain,
                    'qr_combined_enabled': True,
                    'railway_proxy_enabled': True,  # Railway 프록시 사용 표시
                    'permanent_links': True,
                    'railway_optimized': True
                }
                
                # Railway 프록시 URL과 Wasabi S3 키 모두 저장
                if qr_url and qr_s3_key:
                    main_doc_data.update({
                        'qr_link': qr_link,
                        'qr_s3_key': qr_s3_key,
                        'qr_url': qr_url  # Railway 프록시 URL
                    })
                
                if thumbnail_url and thumbnail_s3_key:
                    main_doc_data.update({
                        'thumbnail_s3_key': thumbnail_s3_key,
                        'thumbnail_url': thumbnail_url  # Railway 프록시 URL
                    })
                
                # 배치 작업으로 최적화
                batch = self.db.batch()
                
                # 메인 문서
                main_doc_ref = self.db.collection('uploads').document(group_id)
                batch.set(main_doc_ref, main_doc_data)
                
                # 언어별 영상 문서 (한국어 기본) - Railway 프록시 URL과 S3 키 저장
                language_doc_data = {
                    'language_code': 'ko',
                    'language_name': '한국어',
                    'video_s3_key': video_s3_key,
                    'video_url': video_url,  # Railway 프록시 URL
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
                    'storage_provider': 'wasabi_hybrid',
                    'railway_proxy_url': video_url
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
                
                batch.commit()
                
                update_progress(100, "✅ 업로드 완료!")
                
                # 성공 응답
                result = {
                    'success': True,
                    'group_id': group_id,
                    'video_url': video_url,  # Railway 프록시 URL
                    'qr_link': qr_link,
                    'qr_url': qr_url,  # Railway 프록시 URL
                    'thumbnail_url': thumbnail_url,  # Railway 프록시 URL
                    'metadata': video_metadata,
                    'brunch_domain': self.brunch_domain,
                    'qr_combined': True,
                    'storage_provider': 'wasabi_hybrid',
                    'railway_proxy_enabled': True,
                    'permanent_links': True,
                    'railway_optimized': True
                }
                
                logger.info(f"✅ 하이브리드 비디오 업로드 성공: {group_name} (ID: {group_id}) - Wasabi 저장 + Railway 프록시")
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
        """언어별 영상 업로드 (하이브리드 방식)"""
        
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
                
                # Wasabi 업로드 (Railway 프록시 URL 반환)
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    lang_progress
                )
                
                if not video_url:
                    raise Exception("언어별 영상 업로드 실패")
                
                logger.info(f"✅ 언어별 영상 Wasabi 업로드 완료 (Railway 프록시): {video_url}")
                
                update_progress(85, "💾 Firestore 메타데이터 저장 중...")
                
                # 배치 업데이트
                batch = self.db.batch()
                
                # 언어별 영상 데이터 (Railway 프록시 URL과 S3 키 저장)
                language_doc_data = {
                    'language_code': language_code,
                    'language_name': self._get_language_name(language_code),
                    'video_s3_key': video_s3_key,
                    'video_url': video_url,  # Railway 프록시 URL
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
                    'storage_provider': 'wasabi_hybrid',
                    'railway_proxy_url': video_url,
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
                    'video_url': video_url,  # Railway 프록시 URL
                    'language_code': language_code,
                    'language_name': self._get_language_name(language_code),
                    'metadata': video_metadata,
                    'storage_provider': 'wasabi_hybrid',
                    'railway_proxy_enabled': True,
                    'railway_optimized': True
                }
                
                logger.info(f"✅ 언어별 영상 업로드 성공: {video_id} ({language_code}) - 하이브리드 방식")
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
        """기존 영상 목록 조회 (Railway 프록시 URL 포함)"""
        try:
            logger.info("기존 영상 목록 조회 시작")
            
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
                        lang_docs = doc.reference.collection('language_videos').get()
                        for lang_doc in lang_docs:
                            lang_data = lang_doc.to_dict()
                            language_videos[lang_doc.id] = {
                                'language_code': lang_doc.id,
                                'language_name': lang_data.get('language_name', ''),
                                'video_url': lang_data.get('video_url', ''),  # Railway 프록시 URL
                                'video_s3_key': lang_data.get('video_s3_key', ''),  # Wasabi S3 키
                                'file_size': lang_data.get('file_size', 0),
                                'duration': lang_data.get('duration_string', ''),
                                'upload_date': lang_data.get('upload_date', ''),
                                'railway_proxy_enabled': lang_data.get('railway_proxy_url') is not None
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
                        'qr_url': data.get('qr_url', ''),  # Railway 프록시 URL
                        'thumbnail_url': data.get('thumbnail_url', ''),  # Railway 프록시 URL
                        'brunch_domain': data.get('brunch_domain', self.brunch_domain),
                        'qr_combined': data.get('qr_combined_enabled', False),
                        'storage_provider': data.get('storage_provider', 'wasabi_hybrid'),
                        'railway_proxy_enabled': data.get('railway_proxy_enabled', False),
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
        """업로드 상태 확인 (Railway 프록시 URL 포함)"""
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
                    language_videos[lang_doc.id] = {
                        **lang_data,
                        'railway_proxy_enabled': lang_data.get('railway_proxy_url') is not None
                    }
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
                'qr_url': data.get('qr_url', ''),  # Railway 프록시 URL
                'thumbnail_url': data.get('thumbnail_url', ''),  # Railway 프록시 URL
                'brunch_domain': data.get('brunch_domain', self.brunch_domain),
                'qr_combined': data.get('qr_combined_enabled', False),
                'storage_provider': data.get('storage_provider', 'wasabi_hybrid'),
                'railway_proxy_enabled': data.get('railway_proxy_enabled', False),
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
        """하이브리드 서비스 상태 확인"""
        return {
            'firebase_firestore': self._service_health['firebase'],
            'firebase_storage': False,  # 사용하지 않음
            'wasabi': self._service_health['wasabi'],
            'translator': self._service_health['translator'],
            'memory_usage': self._get_memory_usage(),
            'brunch_domain': self.brunch_domain,
            'single_qr_enabled': True,
            'qr_thumbnail_combined': True,
            'storage_provider': 'wasabi_hybrid',
            'railway_proxy_enabled': True,
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