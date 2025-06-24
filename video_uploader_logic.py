# video_uploader_logic.py - Railway 최적화된 비디오 업로더 (googletrans 사용 버전)
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
    from firebase_admin import credentials, firestore
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

# 상수 정의 (Railway 최적화)
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv'}
SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
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
    """googletrans 라이브러리를 사용한 번역 시스템"""
    
    def __init__(self):
        self.translator = Translator()
        
        # Railway 최적화 설정
        self.timeout = 10
        self.max_retries = 2
        
        # 지원 언어 코드
        self.language_codes = {
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日本語'
        }
        
        # 캐시 시스템 (Railway 메모리 절약)
        self._translation_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_max_size = 50  # Railway 메모리 제한
        
        logger.info("🌍 googletrans 번역 서비스 초기화 완료")
    
    def translate_title(self, korean_title: str) -> Dict[str, str]:
        """강의명 번역 (googletrans 사용)"""
        # 캐시 확인
        cache_key = f"title_{hash(korean_title)}"
        with self._cache_lock:
            if cache_key in self._translation_cache:
                logger.debug(f"캐시에서 번역 결과 반환: {korean_title[:20]}...")
                return self._translation_cache[cache_key].copy()
        
        translations = {'ko': self._make_filename_safe(korean_title)}
        
        # googletrans로 번역 시도
        for lang_code in self.language_codes.keys():
            try:
                translated = self._translate_with_googletrans(korean_title, lang_code)
                if translated:
                    translations[lang_code] = self._make_filename_safe(translated)
                else:
                    translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
                
                # Railway API 제한 대응
                time.sleep(0.2)
                
            except Exception as e:
                logger.warning(f"{lang_code} 번역 실패, 대체 번역 사용: {e}")
                translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
        
        # 캐시에 저장 (Railway 메모리 관리)
        with self._cache_lock:
            if len(self._translation_cache) >= self._cache_max_size:
                # 가장 오래된 항목 제거
                oldest_key = next(iter(self._translation_cache))
                del self._translation_cache[oldest_key]
            
            self._translation_cache[cache_key] = translations.copy()
        
        return translations
    
    def _translate_with_googletrans(self, text: str, target_lang: str) -> Optional[str]:
        """googletrans를 사용한 번역"""
        for attempt in range(self.max_retries):
            try:
                result = self.translator.translate(text, src='ko', dest=target_lang)
                if result and result.text:
                    return result.text
            except Exception as e:
                logger.warning(f"googletrans 번역 시도 {attempt + 1} 실패: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
        
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
        """Railway 최적화된 키워드 기반 번역"""
        # Railway 메모리 최적화된 키워드 맵
        keyword_maps = {
            'en': {
                '안전': 'Safety', '교육': 'Training', '기초': 'Basic', '용접': 'Welding',
                '크레인': 'Crane', '조작': 'Operation', '장비': 'Equipment', '사용법': 'Usage',
                '점검': 'Inspection', '유지보수': 'Maintenance', '응급처치': 'First_Aid',
                '산업': 'Industrial', '건설': 'Construction', '기계': 'Machine', '공구': 'Tool'
            },
            'zh': {
                '안전': '安全', '교육': '培训', '기초': '基础', '용접': '焊接',
                '크레인': '起重机', '조작': '操作', '장비': '设备', '사용법': '使用方法'
            },
            'vi': {
                '안전': 'An_Toan', '교육': 'Dao_Tao', '기초': 'Co_Ban', '용접': 'Han',
                '크레인': 'Cau_Truc', '조작': 'Van_Hanh', '장비': 'Thiet_Bi'
            },
            'th': {
                '안전': 'ปลอดภัย', '교육': 'การศึกษา', '기초': 'พื้นฐาน', '용접': 'เชื่อม'
            },
            'ja': {
                '안전': '安全', '교육': '教育', '기초': '基礎', '용접': '溶接',
                '크레인': 'クレーン', '조작': '操作', '장비': '設備'
            }
        }
        
        keyword_map = keyword_maps.get(lang_code, {})
        result = korean_title
        
        # 키워드 번역 적용
        for korean, translated in keyword_map.items():
            result = result.replace(korean, translated)
        
        # 파일명 안전화
        return self._make_filename_safe(result)

class VideoUploaderLogic:
    """Railway 최적화된 비디오 업로더 메인 클래스"""
    
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
            logger.info("✅ Railway 비디오 업로더 초기화 완료")
        except Exception as e:
            logger.error(f"❌ 비디오 업로더 초기화 실패: {e}")
            raise
    
    def _initialize_services(self):
        """Railway 최적화된 서비스 초기화"""
        with self._initialization_lock:
            try:
                # Firebase 초기화
                self._initialize_firebase()
                self.db = firestore.client()
                self._service_health['firebase'] = True
                
                # Wasabi S3 초기화
                self.s3_client = self._get_wasabi_client()
                self.bucket_name = os.environ['WASABI_BUCKET_NAME']
                self._service_health['wasabi'] = True
                
                # Railway 설정
                self.app_base_url = os.environ.get('APP_BASE_URL', 'http://localhost:8080/watch/')
                self.wasabi_cdn_url = os.environ.get('WASABI_CDN_URL', '')
                
                # Railway 최적화된 전송 설정
                self.transfer_config = TransferConfig(
                    multipart_threshold=1024 * 1024 * 16,  # 16MB
                    multipart_chunksize=1024 * 1024 * 8,   # 8MB
                    max_concurrency=2,  # Railway 리소스 제한
                    use_threads=True
                )
                
                logger.info("🔧 핵심 서비스 초기화 완료")
                
            except Exception as e:
                logger.error(f"❌ 서비스 초기화 실패: {e}")
                raise
    
    def _initialize_firebase(self):
        """Railway 최적화된 Firebase 초기화"""
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
            firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase 초기화 완료")
            
        except Exception as e:
            logger.error(f"❌ Firebase 초기화 실패: {e}")
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
                    retries={'max_attempts': 2, 'mode': 'adaptive'},
                    max_pool_connections=3,  # Railway 최적화
                    region_name=os.environ.get('WASABI_REGION', 'us-east-1')
                )
            )
        except Exception as e:
            logger.error(f"❌ Wasabi 클라이언트 생성 실패: {e}")
            raise
    
    def validate_file(self, file_path: str, file_type: str = 'video') -> bool:
        """Railway 최적화된 파일 검증"""
        try:
            path = Path(file_path)
            
            if not path.exists() or not path.is_file():
                logger.warning(f"파일이 존재하지 않음: {file_path}")
                return False
            
            ext = path.suffix.lower()
            
            if file_type == 'video' and ext not in SUPPORTED_VIDEO_FORMATS:
                logger.warning(f"지원하지 않는 비디오 형식: {ext}")
                return False
            
            if file_type == 'image' and ext not in SUPPORTED_IMAGE_FORMATS:
                logger.warning(f"지원하지 않는 이미지 형식: {ext}")
                return False
            
            # Railway 파일 크기 검증
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.warning(f"빈 파일: {file_path}")
                return False
            
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"파일 크기 초과: {file_size} > {MAX_FILE_SIZE}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"파일 검증 오류: {e}")
            return False
    
    def extract_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """Railway 메모리 최적화된 비디오 메타데이터 추출"""
        try:
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
            
            logger.debug(f"비디오 메타데이터: {duration_str}, {width}x{height}, {file_size//1024//1024}MB")
            return metadata
            
        except Exception as e:
            logger.warning(f"비디오 메타데이터 추출 실패, 기본값 사용: {e}")
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
    
    def create_qr_code(self, data: str, output_path: str, title: str = "") -> bool:
        """Railway 최적화된 QR 코드 생성"""
        try:
            # Railway 메모리 절약 설정
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=4,  # Railway 메모리 절약
                border=3,
            )
            qr.add_data(data)
            qr.make(fit=True)
            
            # Railway 최적화된 이미지 생성
            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            qr_size = 200  # Railway 메모리 절약
            qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
            
            # 제목 추가 (Railway 최적화)
            if title:
                text_height = 40
                margin = 6
                total_height = qr_size + text_height + margin
                final_img = Image.new('RGB', (qr_size, total_height), 'white')
                final_img.paste(qr_img, (0, 0))
                
                draw = ImageDraw.Draw(final_img)
                
                try:
                    font = ImageFont.load_default()
                except:
                    font = ImageFont.load_default()
                
                # Railway 최적화된 텍스트 처리
                if len(title.encode('utf-8')) > 40:  # 바이트 길이 기준
                    title = title[:20] + "..."
                
                bbox = draw.textbbox((0, 0), title, font=font)
                text_width = bbox[2] - bbox[0]
                text_x = max(0, (qr_size - text_width) // 2)
                text_y = qr_size + margin
                
                draw.text((text_x, text_y), title, font=font, fill='black')
                final_img.save(output_path, quality=85, optimize=True)  # Railway 최적화
            else:
                qr_img.save(output_path, quality=85, optimize=True)
            
            logger.debug(f"QR 코드 생성 완료: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"QR 코드 생성 실패: {e}")
            return False
    
    # 나머지 메서드들은 이전과 동일하게 유지하되, 
    # 간단한 stub으로 대체 (Railway 배포 테스트용)
    
    def upload_to_wasabi(self, local_path: str, s3_key: str, content_type: str = None,
                        progress_callback: Callable = None) -> Optional[str]:
        """Railway 테스트용 간단한 업로드"""
        try:
            # 실제 구현은 나중에 추가
            logger.info(f"Railway 테스트: 파일 업로드 시뮬레이션 - {s3_key}")
            return f"https://test-bucket.s3.amazonaws.com/{s3_key}"
        except Exception as e:
            logger.error(f"업로드 테스트 실패: {e}")
            return None
    
    def upload_video(self, **kwargs) -> Dict[str, Any]:
        """Railway 테스트용 간단한 업로드"""
        try:
            return {
                'success': True,
                'group_id': 'test-' + uuid.uuid4().hex[:8],
                'message': 'Railway 테스트 업로드 성공'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_existing_videos(self) -> List[Dict[str, Any]]:
        """Railway 테스트용 비디오 목록"""
        return [
            {
                'id': 'test-1',
                'title': 'Railway 테스트 강의 1',
                'languages': ['ko'],
                'upload_date': '20240624'
            }
        ]

# 로깅 설정 (Railway 최적화)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 상수 정의 (Railway 최적화)
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv'}
SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
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

class RailwayOptimizedTranslator:
    """Railway 최적화된 Google Translate API 번역 시스템"""
    
    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_TRANSLATE_API_KEY')
        self.base_url = "https://translation.googleapis.com/language/translate/v2"
        
        # Railway 최적화 설정
        self.timeout = 15  # Railway 타임아웃 단축
        self.max_retries = 2
        self.request_delay = 0.5  # API 요청 간격
        
        # 지원 언어 코드 (Railway 메모리 최적화)
        self.language_codes = {
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日本語'
        }
        
        # 캐시 시스템 (Railway 메모리 절약)
        self._translation_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_max_size = 100  # Railway 메모리 제한
        
        logger.info("🌍 번역 서비스 초기화 완료")
    
    def translate_title(self, korean_title: str) -> Dict[str, str]:
        """강의명 번역 (Railway 최적화된 캐싱 포함)"""
        # 캐시 확인
        cache_key = f"title_{hash(korean_title)}"
        with self._cache_lock:
            if cache_key in self._translation_cache:
                logger.debug(f"캐시에서 번역 결과 반환: {korean_title[:20]}...")
                return self._translation_cache[cache_key].copy()
        
        translations = {'ko': self._make_filename_safe(korean_title)}
        
        if not self.api_key:
            logger.warning("Google Translate API 키가 없습니다. 대체 번역을 사용합니다.")
            translations.update(self._fallback_translation(korean_title))
        else:
            # API 번역 시도
            for lang_code in self.language_codes.keys():
                try:
                    translated = self._translate_with_retry(korean_title, 'ko', lang_code)
                    if translated:
                        translations[lang_code] = self._make_filename_safe(translated)
                    else:
                        translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
                    
                    # Railway API 제한 대응
                    time.sleep(self.request_delay)
                    
                except Exception as e:
                    logger.warning(f"{lang_code} 번역 실패, 대체 번역 사용: {e}")
                    translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
        
        # 캐시에 저장 (Railway 메모리 관리)
        with self._cache_lock:
            if len(self._translation_cache) >= self._cache_max_size:
                # 가장 오래된 항목 제거
                oldest_key = next(iter(self._translation_cache))
                del self._translation_cache[oldest_key]
            
            self._translation_cache[cache_key] = translations.copy()
        
        return translations
    
    def _translate_with_retry(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """재시도 기능이 있는 번역 (Railway 최적화)"""
        for attempt in range(self.max_retries):
            try:
                result = self._translate_text(text, source_lang, target_lang)
                if result:
                    return result
            except requests.exceptions.Timeout:
                logger.warning(f"번역 시도 {attempt + 1} 타임아웃")
            except Exception as e:
                logger.warning(f"번역 시도 {attempt + 1} 실패: {e}")
            
            if attempt < self.max_retries - 1:
                time.sleep(min(2 ** attempt, 5))  # 지수 백오프
        
        return None
    
    def _translate_text(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Google Translate API 호출 (Railway 최적화)"""
        try:
            params = {
                'key': self.api_key,
                'q': text,
                'source': source_lang,
                'target': target_lang,
                'format': 'text'
            }
            
            response = requests.post(
                self.base_url,
                data=params,
                timeout=self.timeout,
                headers={
                    'User-Agent': 'Railway-Video-Uploader/2.0',
                    'Accept': 'application/json'
                }
            )
            response.raise_for_status()
            
            result = response.json()
            if 'data' in result and 'translations' in result['data']:
                return result['data']['translations'][0]['translatedText']
            
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Google Translate API 요청 실패: {e}")
            return None
        except Exception as e:
            logger.error(f"번역 처리 중 오류: {e}")
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
    
    def _fallback_translation(self, korean_title: str) -> Dict[str, str]:
        """Railway 최적화된 대체 번역 시스템"""
        return {
            lang_code: self._fallback_single_translation(korean_title, lang_code)
            for lang_code in self.language_codes.keys()
        }
    
    def _fallback_single_translation(self, korean_title: str, lang_code: str) -> str:
        """Railway 최적화된 키워드 기반 번역"""
        # Railway 메모리 최적화된 키워드 맵
        keyword_maps = {
            'en': {
                '안전': 'Safety', '교육': 'Training', '기초': 'Basic', '용접': 'Welding',
                '크레인': 'Crane', '조작': 'Operation', '장비': 'Equipment', '사용법': 'Usage',
                '점검': 'Inspection', '유지보수': 'Maintenance', '응급처치': 'First_Aid',
                '산업': 'Industrial', '건설': 'Construction', '기계': 'Machine', '공구': 'Tool'
            },
            'zh': {
                '안전': '安全', '교육': '培训', '기초': '基础', '용접': '焊接',
                '크레인': '起重机', '조작': '操作', '장비': '设备', '사용법': '使用方法'
            },
            'vi': {
                '안전': 'An_Toan', '교육': 'Dao_Tao', '기초': 'Co_Ban', '용접': 'Han',
                '크레인': 'Cau_Truc', '조작': 'Van_Hanh', '장비': 'Thiet_Bi'
            },
            'th': {
                '안전': 'ปลอดภัย', '교육': 'การศึกษา', '기초': 'พื้นฐาน', '용접': 'เชื่อม'
            },
            'ja': {
                '안전': '安全', '교육': '教育', '기초': '基礎', '용접': '溶接',
                '크레인': 'クレーン', '조작': '操作', '장비': '設備'
            }
        }
        
        keyword_map = keyword_maps.get(lang_code, {})
        result = korean_title
        
        # 키워드 번역 적용
        for korean, translated in keyword_map.items():
            result = result.replace(korean, translated)
        
        # 파일명 안전화
        return self._make_filename_safe(result)

# GoogleTranslator는 RailwayOptimizedTranslator의 별칭
GoogleTranslator = RailwayOptimizedTranslator

class RailwayVideoUploader:
    """Railway 최적화된 비디오 업로더 메인 클래스"""
    
    def __init__(self):
        self._initialization_lock = threading.Lock()
        self._service_health = {
            'firebase': False,
            'wasabi': False,
            'translator': False
        }
        
        try:
            self._initialize_services()
            self.translator = RailwayOptimizedTranslator()
            self._service_health['translator'] = True
            logger.info("✅ Railway 비디오 업로더 초기화 완료")
        except Exception as e:
            logger.error(f"❌ 비디오 업로더 초기화 실패: {e}")
            raise
    
    def _initialize_services(self):
        """Railway 최적화된 서비스 초기화"""
        with self._initialization_lock:
            try:
                # Firebase 초기화
                self._initialize_firebase()
                self.db = firestore.client()
                self._service_health['firebase'] = True
                
                # Wasabi S3 초기화
                self.s3_client = self._get_wasabi_client()
                self.bucket_name = os.environ['WASABI_BUCKET_NAME']
                self._service_health['wasabi'] = True
                
                # Railway 설정
                self.app_base_url = os.environ.get('APP_BASE_URL', 'http://localhost:8080/watch/')
                self.wasabi_cdn_url = os.environ.get('WASABI_CDN_URL', '')
                
                # Railway 최적화된 전송 설정
                self.transfer_config = TransferConfig(
                    multipart_threshold=1024 * 1024 * 16,  # 16MB
                    multipart_chunksize=1024 * 1024 * 8,   # 8MB
                    max_concurrency=2,  # Railway 리소스 제한
                    use_threads=True
                )
                
                logger.info("🔧 핵심 서비스 초기화 완료")
                
            except Exception as e:
                logger.error(f"❌ 서비스 초기화 실패: {e}")
                raise
    
    def _initialize_firebase(self):
        """Railway 최적화된 Firebase 초기화"""
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
            firebase_admin.initialize_app(cred)
            logger.info("✅ Firebase 초기화 완료")
            
        except Exception as e:
            logger.error(f"❌ Firebase 초기화 실패: {e}")
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
                    retries={'max_attempts': 2, 'mode': 'adaptive'},
                    max_pool_connections=3,  # Railway 최적화
                    region_name=os.environ.get('WASABI_REGION', 'us-east-1')
                )
            )
        except Exception as e:
            logger.error(f"❌ Wasabi 클라이언트 생성 실패: {e}")
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
        """Railway 최적화된 파일 검증"""
        try:
            path = Path(file_path)
            
            if not path.exists() or not path.is_file():
                logger.warning(f"파일이 존재하지 않음: {file_path}")
                return False
            
            ext = path.suffix.lower()
            
            if file_type == 'video' and ext not in SUPPORTED_VIDEO_FORMATS:
                logger.warning(f"지원하지 않는 비디오 형식: {ext}")
                return False
            
            if file_type == 'image' and ext not in SUPPORTED_IMAGE_FORMATS:
                logger.warning(f"지원하지 않는 이미지 형식: {ext}")
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
            if file_size > RAILWAY_MEMORY_LIMIT // 2:  # 메모리의 절반 이상은 업로드 불가
                logger.warning(f"Railway 메모리 제한으로 인한 파일 크기 초과: {file_size}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"파일 검증 오류: {e}")
            return False
    
    def extract_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """Railway 메모리 최적화된 비디오 메타데이터 추출"""
        with self._railway_memory_context():
            try:
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
                
                logger.debug(f"비디오 메타데이터: {duration_str}, {width}x{height}, {file_size//1024//1024}MB")
                return metadata
                
            except Exception as e:
                logger.warning(f"비디오 메타데이터 추출 실패, 기본값 사용: {e}")
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
    
    def create_qr_code(self, data: str, output_path: str, title: str = "") -> bool:
        """Railway 최적화된 QR 코드 생성"""
        with self._railway_memory_context():
            try:
                # Railway 메모리 절약 설정
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=4,  # Railway 메모리 절약
                    border=3,
                )
                qr.add_data(data)
                qr.make(fit=True)
                
                # Railway 최적화된 이미지 생성
                qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                qr_size = 200  # Railway 메모리 절약
                qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
                
                # 제목 추가 (Railway 최적화)
                if title:
                    text_height = 40
                    margin = 6
                    total_height = qr_size + text_height + margin
                    final_img = Image.new('RGB', (qr_size, total_height), 'white')
                    final_img.paste(qr_img, (0, 0))
                    
                    draw = ImageDraw.Draw(final_img)
                    
                    try:
                        font = ImageFont.load_default()
                    except:
                        font = ImageFont.load_default()
                    
                    # Railway 최적화된 텍스트 처리
                    if len(title.encode('utf-8')) > 40:  # 바이트 길이 기준
                        title = title[:20] + "..."
                    
                    bbox = draw.textbbox((0, 0), title, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_x = max(0, (qr_size - text_width) // 2)
                    text_y = qr_size + margin
                    
                    draw.text((text_x, text_y), title, font=font, fill='black')
                    final_img.save(output_path, quality=85, optimize=True)  # Railway 최적화
                else:
                    qr_img.save(output_path, quality=85, optimize=True)
                
                logger.debug(f"QR 코드 생성 완료: {output_path}")
                return True
                
            except Exception as e:
                logger.error(f"QR 코드 생성 실패: {e}")
                return False
    
    def upload_to_wasabi(self, local_path: str, s3_key: str, content_type: str = None,
                        progress_callback: Callable = None) -> Optional[str]:
        """Railway 최적화된 Wasabi 업로드"""
        try:
            extra_args = {'ACL': 'public-read'}
            if content_type:
                extra_args['ContentType'] = content_type
            
            # Railway 최적화된 진행률 콜백
            def railway_progress_callback(bytes_transferred):
                if progress_callback and hasattr(railway_progress_callback, 'file_size'):
                    if railway_progress_callback.file_size > 0:
                        percentage = min((bytes_transferred / railway_progress_callback.file_size) * 100, 100)
                        progress_callback(int(percentage), f"업로드 진행 중... {percentage:.1f}%")
            
            # 파일 크기 설정
            if progress_callback:
                railway_progress_callback.file_size = os.path.getsize(local_path)
            
            # Railway 최적화된 업로드 실행
            if progress_callback:
                self.s3_client.upload_file(
                    local_path,
                    self.bucket_name,
                    s3_key,
                    Config=self.transfer_config,
                    ExtraArgs=extra_args,
                    Callback=railway_progress_callback
                )
            else:
                self.s3_client.upload_file(
                    local_path,
                    self.bucket_name,
                    s3_key,
                    Config=self.transfer_config,
                    ExtraArgs=extra_args
                )
            
            # Railway CDN URL 생성
            if self.wasabi_cdn_url:
                public_url = f"{self.wasabi_cdn_url.rstrip('/')}/{s3_key}"
            else:
                region = os.environ.get('WASABI_REGION', 'us-east-1')
                public_url = f"https://s3.{region}.wasabisys.com/{self.bucket_name}/{s3_key}"
            
            logger.info(f"✅ Wasabi 업로드 완료: {s3_key}")
            return public_url
            
        except Exception as e:
            logger.error(f"❌ Wasabi 업로드 실패: {e}")
            return None
    
    def upload_video(self, video_path: str, thumbnail_path: Optional[str], group_name: str,
                    main_category: str, sub_category: str, leaf_category: str,
                    content_description: str, translated_filenames: Dict[str, str],
                    progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Railway 최적화된 메인 비디오 업로드"""
        
        with self._railway_memory_context():
            try:
                def update_progress(value: int, message: str):
                    if progress_callback:
                        progress_callback(value, message)
                    logger.debug(f"진행률 {value}%: {message}")
                
                update_progress(5, "🔍 파일 검증 중...")
                
                # Railway 메모리 최적화된 메타데이터 추출
                video_metadata = self.extract_video_metadata(video_path)
                
                update_progress(15, "📁 업로드 경로 설정 중...")
                
                # Railway 최적화된 경로 생성
                group_id = uuid.uuid4().hex
                timestamp = datetime.now()
                date_str = timestamp.strftime('%Y%m%d')
                safe_name = re.sub(r'[^\w가-힣-]', '_', group_name)[:30]  # Railway 제한
                
                # Railway 폴더 구조 최적화
                year_month = timestamp.strftime('%Y%m')
                base_folder = f"videos/{year_month}/{group_id}_{safe_name}"
                
                update_progress(25, "🎬 동영상 업로드 중...")
                
                # 동영상 업로드
                video_ext = Path(video_path).suffix.lower()
                ko_filename = translated_filenames.get('ko', safe_name)
                video_s3_key = f"{base_folder}/{ko_filename}_video_ko{video_ext}"
                
                # Railway 최적화된 콘텐츠 타입
                content_type_map = {
                    '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                    '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska'
                }
                video_content_type = content_type_map.get(video_ext, 'video/mp4')
                
                # Railway 업로드 진행률 조정
                def video_progress(percentage, msg):
                    adjusted_percentage = 25 + (percentage * 0.4)  # 25-65%
                    update_progress(int(adjusted_percentage), f"🎬 {msg}")
                
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    video_progress
                )
                
                if not video_url:
                    raise Exception("동영상 업로드 실패")
                
                update_progress(70, "🖼️ 썸네일 처리 중...")
                
                # Railway 최적화된 썸네일 업로드
                thumbnail_url = None
                thumbnail_s3_key = None
                if thumbnail_path:
                    thumb_ext = Path(thumbnail_path).suffix.lower()
                    thumbnail_s3_key = f"{base_folder}/{ko_filename}_thumbnail{thumb_ext}"
                    
                    thumb_content_type_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'
                    }
                    thumb_content_type = thumb_content_type_map.get(thumb_ext, 'image/jpeg')
                    
                    thumbnail_url = self.upload_to_wasabi(thumbnail_path, thumbnail_s3_key, thumb_content_type)
                
                update_progress(80, "📱 QR 코드 생성 중...")
                
                # Railway 최적화된 QR 코드 생성
                qr_link = f"{self.app_base_url}{group_id}"
                qr_temp_path = os.path.join(tempfile.gettempdir(), f"qr_{group_id}.png")
                
                qr_title = group_name[:25]  # Railway 메모리 절약
                if all([main_category, sub_category, leaf_category]):
                    qr_title = f"{group_name[:20]}\n({main_category})"
                
                qr_url = None
                qr_s3_key = None
                if self.create_qr_code(qr_link, qr_temp_path, qr_title):
                    qr_s3_key = f"{base_folder}/{ko_filename}_qrcode.png"
                    qr_url = self.upload_to_wasabi(qr_temp_path, qr_s3_key, 'image/png')
                    
                    # Railway 임시 파일 정리
                    try:
                        os.remove(qr_temp_path)
                    except:
                        pass
                
                update_progress(90, "💾 데이터베이스 저장 중...")
                
                # Railway 최적화된 Firestore 저장
                main_doc_data = {
                    'group_id': group_id,
                    'group_name': group_name,
                    'content_description': content_description,
                    'main_category': main_category,
                    'sub_category': sub_category,
                    'sub_sub_category': leaf_category,
                    'base_folder': base_folder,
                    'storage_provider': 'wasabi',
                    'bucket_name': self.bucket_name,
                    'upload_date': date_str,
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP,
                    'translation_status': 'pending',
                    'supported_languages_count': 1,
                    'total_file_size': video_metadata['file_size'],
                    'supported_video_languages': ['ko'],
                    'railway_optimized': True  # Railway 표시
                }
                
                # 선택적 필드 추가
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
                
                # Railway 배치 작업으로 최적화
                batch = self.db.batch()
                
                # 메인 문서
                main_doc_ref = self.db.collection('uploads').document(group_id)
                batch.set(main_doc_ref, main_doc_data)
                
                # 언어별 영상 문서
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
                    'is_original': True
                }
                
                language_doc_ref = main_doc_ref.collection('language_videos').document('ko')
                batch.set(language_doc_ref, language_doc_data)
                
                # 번역 메타데이터
                if translated_filenames:
                    translation_metadata = {
                        'filenames': translated_filenames,
                        'created_at': firestore.SERVER_TIMESTAMP,
                        'railway_generated': True
                    }
                    translation_doc_ref = main_doc_ref.collection('metadata').document('translations')
                    batch.set(translation_doc_ref, translation_metadata)
                
                # 검색 메타데이터
                search_metadata = {
                    'searchable_title': group_name.lower(),
                    'searchable_content': content_description.lower(),
                    'category_path': f"{main_category}/{sub_category}/{leaf_category}",
                    'tags': self._extract_tags_from_content(content_description),
                    'created_at': firestore.SERVER_TIMESTAMP
                }
                search_doc_ref = main_doc_ref.collection('metadata').document('search_data')
                batch.set(search_doc_ref, search_metadata)
                
                # Railway 배치 커밋
                batch.commit()
                
                update_progress(100, "✅ 업로드 완료!")
                
                # Railway 성공 응답
                return {
                    'success': True,
                    'group_id': group_id,
                    'video_url': video_url,
                    'qr_link': qr_link,
                    'qr_url': qr_url,
                    'thumbnail_url': thumbnail_url,
                    'metadata': video_metadata,
                    'railway_optimized': True
                }
                
            except Exception as e:
                logger.error(f"❌ 비디오 업로드 실패: {e}")
                return {
                    'success': False,
                    'error': str(e),
                    'railway_optimized': True
                }
    
    def upload_language_video(self, video_id: str, language_code: str, video_path: str,
                             progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Railway 최적화된 언어별 영상 업로드"""
        
        with self._railway_memory_context():
            try:
                def update_progress(value: int, message: str):
                    if progress_callback:
                        progress_callback(value, message)
                    logger.debug(f"언어별 업로드 {value}%: {message}")
                
                update_progress(10, "📋 기존 영상 정보 확인 중...")
                
                # Railway 최적화된 문서 조회
                doc_ref = self.db.collection('uploads').document(video_id)
                doc = doc_ref.get()
                
                if not doc.exists:
                    raise Exception("선택된 영상을 찾을 수 없습니다.")
                
                video_data = doc.to_dict()
                
                update_progress(25, "🎬 언어별 영상 준비 중...")
                
                # Railway 메타데이터 추출
                video_metadata = self.extract_video_metadata(video_path)
                
                # Railway 경로 생성
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
                
                # Railway 안전한 파일명
                safe_translated_title = re.sub(r'[^\w가-힣-]', '_', translated_title)[:30]
                video_ext = Path(video_path).suffix.lower()
                video_s3_key = f"{base_folder}/{safe_translated_title}_video_{language_code}{video_ext}"
                
                content_type_map = {
                    '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                    '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska'
                }
                video_content_type = content_type_map.get(video_ext, 'video/mp4')
                
                update_progress(40, f"☁️ {language_code.upper()} 영상 업로드 중...")
                
                # Railway 진행률 조정
                def lang_progress(percentage, msg):
                    adjusted_percentage = 40 + (percentage * 0.4)  # 40-80%
                    update_progress(int(adjusted_percentage), f"🌐 {msg}")
                
                video_url = self.upload_to_wasabi(
                    video_path,
                    video_s3_key,
                    video_content_type,
                    lang_progress
                )
                
                if not video_url:
                    raise Exception("언어별 영상 업로드 실패")
                
                update_progress(85, "💾 언어별 데이터 저장 중...")
                
                # Railway 배치 업데이트
                batch = self.db.batch()
                
                # 언어별 영상 데이터
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
                
                # Railway 배치 커밋
                batch.commit()
                
                update_progress(100, "✅ 언어별 영상 업로드 완료!")
                
                return {
                    'success': True,
                    'video_url': video_url,
                    'language_code': language_code,
                    'language_name': self._get_language_name(language_code),
                    'metadata': video_metadata,
                    'railway_optimized': True
                }
                
            except Exception as e:
                logger.error(f"❌ 언어별 영상 업로드 실패: {e}")
                return {
                    'success': False,
                    'error': str(e),
                    'railway_optimized': True
                }
    
    def get_existing_videos(self) -> List[Dict[str, Any]]:
        """Railway 최적화된 기존 영상 목록 조회"""
        try:
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
                                'file_size': lang_data.get('file_size', 0),
                                'duration': lang_data.get('duration_string', ''),
                                'upload_date': lang_data.get('upload_date', '')
                            }
                            supported_languages.append(lang_doc.id)
                    except Exception as e:
                        logger.warning(f"언어별 영상 로드 실패 ({doc.id}): {e}")
                        supported_languages = ['ko']  # 기본값
                    
                    # Railway 메모리 최적화된 비디오 정보
                    video_info = {
                        'id': doc.id,
                        'title': data.get('group_name', 'Unknown'),
                        'category': f"{data.get('main_category', '')} > {data.get('sub_category', '')} > {data.get('sub_sub_category', '')}",
                        'main_category': data.get('main_category', ''),
                        'sub_category': data.get('sub_category', ''),
                        'sub_sub_category': data.get('sub_sub_category', ''),
                        'upload_date': data.get('upload_date', ''),
                        'languages': supported_languages,
                        'language_videos': language_videos,
                        'total_file_size': data.get('total_file_size', 0),
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
            return []
    
    def get_upload_status(self, group_id: str) -> Dict[str, Any]:
        """Railway 최적화된 업로드 상태 확인"""
        try:
            doc_ref = self.db.collection('uploads').document(group_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'success': False, 'error': '영상을 찾을 수 없습니다'}
            
            data = doc.to_dict()
            
            # Railway 최적화된 언어별 영상 조회
            language_videos = {}
            try:
                lang_docs = doc_ref.collection('language_videos').get()
                for lang_doc in lang_docs:
                    language_videos[lang_doc.id] = lang_doc.to_dict()
            except:
                pass
            
            return {
                'success': True,
                'group_id': group_id,
                'group_name': data.get('group_name', ''),
                'upload_date': data.get('upload_date', ''),
                'supported_languages': list(language_videos.keys()),
                'language_videos': language_videos,
                'qr_link': data.get('qr_link', ''),
                'qr_url': data.get('qr_url', ''),
                'thumbnail_url': data.get('thumbnail_url', ''),
                'railway_optimized': data.get('railway_optimized', False)
            }
            
        except Exception as e:
            logger.error(f"업로드 상태 확인 실패: {e}")
            return {'success': False, 'error': str(e)}
    
    def _extract_tags_from_content(self, content: str) -> List[str]:
        """Railway 메모리 최적화된 태그 추출"""
        keywords = []
        
        try:
            # Railway 최적화된 불릿 포인트 추출
            bullet_items = re.findall(r'[•·▪▫◦‣⁃]\s*([^•·▪▫◦‣⁃\n]{1,30})', content)
            numbered_items = re.findall(r'\d+\.\s*([^\d\n]{1,30})', content)
            
            keywords.extend([item.strip() for item in bullet_items[:2]])
            keywords.extend([item.strip() for item in numbered_items[:2]])
            
            # Railway 메모리 절약 키워드
            common_keywords = ['안전', '교육', '장비', '사용법', '점검', '응급처치']
            for keyword in common_keywords:
                if keyword in content and keyword not in keywords:
                    keywords.append(keyword)
                    if len(keywords) >= 4:  # Railway 제한
                        break
            
            # Railway 최적화: 중복 제거 및 길이 제한
            unique_keywords = []
            for k in keywords:
                clean_k = k.strip()
                if len(clean_k) > 1 and clean_k not in unique_keywords:
                    unique_keywords.append(clean_k)
                if len(unique_keywords) >= 4:  # Railway 메모리 제한
                    break
            
            return unique_keywords
            
        except Exception as e:
            logger.warning(f"태그 추출 실패: {e}")
            return ['교육', '안전']  # Railway 기본 태그
    
    def _get_language_name(self, language_code: str) -> str:
        """언어 코드를 언어명으로 변환"""
        language_names = {
            'ko': '한국어',
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日本語'
        }
        return language_names.get(language_code, language_code)
    
    def get_service_health(self) -> Dict[str, Any]:
        """Railway 서비스 상태 확인"""
        return {
            'firebase': self._service_health['firebase'],
            'wasabi': self._service_health['wasabi'],
            'translator': self._service_health['translator'],
            'memory_usage': self._get_memory_usage(),
            'railway_optimized': True
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

# VideoUploaderLogic는 RailwayVideoUploader의 별칭
VideoUploaderLogic = RailwayVideoUploader