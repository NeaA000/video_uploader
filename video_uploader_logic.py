# video_uploader_logic.py - Railway 최적화된 비디오 업로더 코어 로직 (오류 수정 완료)
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
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Callable
from PIL import Image, ImageDraw, ImageFont
import qrcode

# 필수 라이브러리 import
try:
    import boto3
    from boto3.s3.transfer import TransferConfig
    import firebase_admin
    from firebase_admin import credentials, firestore
    from moviepy.video.io.VideoFileClip import VideoFileClip
except ImportError as e:
    print(f"필수 라이브러리 누락: {e}")
    sys.exit(1)

# 로깅 설정 (Railway 최적화)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 상수 정의
SUPPORTED_VIDEO_FORMATS = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv'}
SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5GB

# 카테고리 구조 (Railway 최적화)
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
    """Google Translate API를 사용한 실제 번역 시스템 (Railway 최적화)"""
    
    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_TRANSLATE_API_KEY')
        self.base_url = "https://translation.googleapis.com/language/translate/v2"
        
        # 지원 언어 코드
        self.language_codes = {
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt', 
            'th': 'ไทย',
            'ja': '日本語'
        }
        
        # Railway 최적화를 위한 타임아웃 설정
        self.timeout = 30
        self.max_retries = 2
    
    def translate_title(self, korean_title: str) -> Dict[str, str]:
        """강의명을 각 언어로 번역 (Railway 최적화)"""
        if not self.api_key:
            logger.warning("Google Translate API 키가 없습니다. 대체 번역을 사용합니다.")
            return self._fallback_translation(korean_title)
        
        translations = {}
        
        for lang_code in self.language_codes.keys():
            try:
                translated = self._translate_text_with_retry(korean_title, 'ko', lang_code)
                if translated:
                    safe_name = self._make_filename_safe(translated)
                    translations[lang_code] = safe_name
                else:
                    translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
                    
            except Exception as e:
                logger.error(f"{lang_code} 번역 실패: {e}")
                translations[lang_code] = self._fallback_single_translation(korean_title, lang_code)
        
        # 한국어 원본도 추가
        translations['ko'] = self._make_filename_safe(korean_title)
        
        return translations
    
    def _translate_text_with_retry(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """재시도 기능이 있는 번역 (Railway 최적화)"""
        for attempt in range(self.max_retries):
            try:
                result = self._translate_text(text, source_lang, target_lang)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"번역 시도 {attempt + 1} 실패: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)  # Railway 최적화된 대기 시간
        
        return None
    
    def _translate_text(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Google Translate API로 텍스트 번역 (Railway 최적화)"""
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
                headers={'User-Agent': 'Railway-Video-Uploader/1.0'}
            )
            response.raise_for_status()
            
            result = response.json()
            
            if 'data' in result and 'translations' in result['data']:
                translated_text = result['data']['translations'][0]['translatedText']
                return translated_text
            
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Google Translate API 요청 실패: {e}")
            return None
        except Exception as e:
            logger.error(f"번역 처리 중 오류: {e}")
            return None
    
    def _make_filename_safe(self, text: str) -> str:
        """파일명에 안전한 형태로 변환 (Railway 최적화)"""
        import html
        text = html.unescape(text)
        
        # Railway 최적화된 파일명 처리
        safe_text = re.sub(r'[<>:"/\\|?*]', '_', text)
        safe_text = re.sub(r'\s+', '_', safe_text)
        safe_text = re.sub(r'_+', '_', safe_text)
        safe_text = safe_text.strip('_')
        
        # Railway 파일 시스템 최적화
        if len(safe_text) > 50:
            safe_text = safe_text[:50].rstrip('_')
        
        return safe_text or 'Unknown_Title'
    
    def _fallback_translation(self, korean_title: str) -> Dict[str, str]:
        """API가 없을 때 사용하는 대체 번역 (Railway 최적화)"""
        translations = {
            lang_code: self._fallback_single_translation(korean_title, lang_code)
            for lang_code in self.language_codes.keys()
        }
        translations['ko'] = self._make_filename_safe(korean_title)
        return translations
    
    def _fallback_single_translation(self, korean_title: str, lang_code: str) -> str:
        """단일 언어 대체 번역 (Railway 최적화된 키워드 기반)"""
        keyword_maps = {
            'en': {
                '안전': 'Safety', '교육': 'Training', '기초': 'Basic', '용접': 'Welding',
                '크레인': 'Crane', '조작': 'Operation', '장비': 'Equipment', '사용법': 'Usage',
                '점검': 'Inspection', '유지보수': 'Maintenance', '응급처치': 'First_Aid',
                '산업': 'Industrial', '건설': 'Construction', '기계': 'Machine',
                '공구': 'Tool', '실습': 'Practice', '법규': 'Regulation', '규정': 'Standard',
                '작업': 'Work', '현장': 'Site', '관리': 'Management', '위험': 'Risk'
            },
            'zh': {
                '안전': '安全', '교육': '培训', '기초': '基础', '용접': '焊接',
                '크레인': '起重机', '조작': '操作', '장비': '设备', '사용법': '使用方法',
                '점검': '检查', '유지보수': '维护', '응급처치': '急救',
                '산업': '工业', '건설': '建设', '기계': '机器', '공구': '工具'
            },
            'vi': {
                '안전': 'An_Toan', '교육': 'Dao_Tao', '기초': 'Co_Ban', '용접': 'Han',
                '크레인': 'Cau_Truc', '조작': 'Van_Hanh', '장비': 'Thiet_Bi',
                '산업': 'Cong_Nghiep', '건설': 'Xay_Dung', '기계': 'May_Moc'
            },
            'th': {
                '안전': 'ปลอดภัย', '교육': 'การศึกษา', '기초': 'พื้นฐาน', '용접': 'เชื่อม',
                '크레인': 'เครน', '조작': 'ดำเนินงาน', '장비': 'อุปกรณ์'
            },
            'ja': {
                '안전': '安全', '교육': '教育', '기초': '基礎', '용접': '溶接',
                '크레인': 'クレーン', '조작': '操作', '장비': '設備', '공구': '工具'
            }
        }
        
        keyword_map = keyword_maps.get(lang_code, {})
        result = korean_title
        
        # 키워드 기반 번역
        for korean, translated in keyword_map.items():
            result = result.replace(korean, translated)
        
        # 파일명 안전화
        if lang_code in ['zh', 'th', 'ja']:
            result = re.sub(r'[<>:"/\\|?*]', '_', result)
        else:
            result = re.sub(r'[^\w\s-]', '_', result)
        
        result = re.sub(r'\s+', '_', result)
        result = re.sub(r'_+', '_', result)
        result = result.strip('_')
        
        return result[:50] if len(result) > 50 else result or 'Unknown'

class VideoUploaderLogic:
    """Railway 최적화된 비디오 업로더 코어 로직 (오류 수정 완료)"""
    
    def __init__(self):
        self._initialize_services()
        self.translator = GoogleTranslator()
    
    def _initialize_services(self):
        """서비스 초기화 (Railway 최적화)"""
        try:
            # Firebase 초기화 (Railway 최적화)
            self._initialize_firebase()
            self.db = firestore.client()
            
            # Wasabi S3 클라이언트 초기화 (Railway 최적화)
            self.s3_client = self._get_wasabi_client()
            self.bucket_name = os.environ['WASABI_BUCKET_NAME']
            self.app_base_url = os.environ.get('APP_BASE_URL', 'http://localhost:8080/watch/')
            self.wasabi_cdn_url = os.environ.get('WASABI_CDN_URL', '')
            
            # Railway 최적화된 전송 설정
            self.transfer_config = TransferConfig(
                multipart_threshold=1024 * 1024 * 20,  # 20MB (Railway 메모리 최적화)
                multipart_chunksize=1024 * 1024 * 8,   # 8MB
                max_concurrency=2,  # Railway 리소스 제한 고려
                use_threads=True
            )
            
            logger.info("서비스 초기화 완료")
            
        except Exception as e:
            logger.error(f"서비스 초기화 실패: {e}")
            raise
    
    def _initialize_firebase(self):
        """Firebase 초기화 (Railway 환경 최적화)"""
        if firebase_admin._apps:
            return
        
        try:
            firebase_creds = {
                "type": os.environ.get("FIREBASE_TYPE", "service_account"),
                "project_id": os.environ["FIREBASE_PROJECT_ID"],
                "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID", ""),
                "private_key": os.environ["FIREBASE_PRIVATE_KEY"].replace('\\n', '\n'),
                "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
                "client_id": os.environ.get("FIREBASE_CLIENT_ID", ""),
                "auth_uri": os.environ.get("FIREBASE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": os.environ.get("FIREBASE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
                "auth_provider_x509_cert_url": os.environ.get("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
                "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_X509_CERT_URL", "")
            }
            
            cred = credentials.Certificate(firebase_creds)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase 초기화 완료")
            
        except Exception as e:
            logger.error(f"Firebase 초기화 실패: {e}")
            raise
    
    def _get_wasabi_client(self):
        """와사비 S3 클라이언트 생성 (Railway 최적화)"""
        try:
            return boto3.client(
                's3',
                aws_access_key_id=os.environ['WASABI_ACCESS_KEY'],
                aws_secret_access_key=os.environ['WASABI_SECRET_KEY'],
                region_name=os.environ.get('WASABI_REGION', 'us-east-1'),
                endpoint_url=f"https://s3.{os.environ.get('WASABI_REGION', 'us-east-1')}.wasabisys.com",
                config=boto3.session.Config(
                    retries={'max_attempts': 3, 'mode': 'adaptive'},  # Railway 최적화
                    max_pool_connections=5  # Railway 메모리 최적화
                )
            )
        except Exception as e:
            logger.error(f"와사비 클라이언트 생성 실패: {e}")
            raise
    
    def validate_file(self, file_path: str, file_type: str = 'video') -> bool:
        """파일 유효성 검증 (Railway 최적화)"""
        try:
            path = Path(file_path)
            
            if not path.exists() or not path.is_file():
                return False
            
            ext = path.suffix.lower()
            
            if file_type == 'video' and ext not in SUPPORTED_VIDEO_FORMATS:
                return False
            
            if file_type == 'image' and ext not in SUPPORTED_IMAGE_FORMATS:
                return False
            
            # 파일 크기 검증 (Railway 최적화)
            file_size = os.path.getsize(file_path)
            if file_size == 0 or file_size > MAX_FILE_SIZE:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"파일 검증 오류: {e}")
            return False
    
    def extract_video_metadata(self, video_path: str) -> Dict[str, Any]:
        """동영상 메타데이터 추출 (Railway 메모리 최적화)"""
        try:
            # Railway 메모리 제한을 고려한 안전한 처리
            with VideoFileClip(video_path) as clip:
                duration_sec = int(clip.duration) if clip.duration else 0
                width = getattr(clip, 'w', 0)
                height = getattr(clip, 'h', 0)
                fps = getattr(clip, 'fps', 0)
                
                minutes = duration_sec // 60
                seconds = duration_sec % 60
                duration_str = f"{minutes}:{seconds:02d}"
                
                metadata = {
                    'duration_seconds': duration_sec,
                    'duration_string': duration_str,
                    'width': width,
                    'height': height,
                    'fps': fps,
                    'file_size': os.path.getsize(video_path)
                }
                
                return metadata
                
        except Exception as e:
            logger.warning(f"동영상 메타데이터 추출 실패: {e}")
            # Railway 안전 모드 - 기본값 반환
            file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
            return {
                'duration_seconds': 0,
                'duration_string': '0:00',
                'width': 0,
                'height': 0,
                'fps': 0,
                'file_size': file_size
            }
    
    def create_qr_code(self, data: str, output_path: str, title: str = "") -> bool:
        """QR 코드 생성 (Railway 최적화)"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=6,  # Railway 메모리 절약
                border=4,
            )
            qr.add_data(data)
            qr.make(fit=True)
            
            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            qr_size = 250  # Railway 최적화
            qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
            
            # 제목 추가 (Railway 메모리 최적화)
            if title:
                text_height = 50
                margin = 8
                total_height = qr_size + text_height + margin
                final_img = Image.new('RGB', (qr_size, total_height), 'white')
                final_img.paste(qr_img, (0, 0))
                
                draw = ImageDraw.Draw(final_img)
                
                try:
                    font = ImageFont.load_default()
                except:
                    font = ImageFont.load_default()
                
                # Railway 최적화된 텍스트 처리
                if len(title) > 25:
                    title = title[:25] + "..."
                
                bbox = draw.textbbox((0, 0), title, font=font)
                text_width = bbox[2] - bbox[0]
                text_x = max(0, (qr_size - text_width) // 2)
                text_y = qr_size + margin
                
                draw.text((text_x, text_y), title, font=font, fill='black')
                final_img.save(output_path, quality=75, optimize=True)  # Railway 최적화
                
            else:
                qr_img.save(output_path, quality=75, optimize=True)
            
            return True
            
        except Exception as e:
            logger.error(f"QR 코드 생성 실패: {e}")
            return False
    
    def upload_to_wasabi(self, local_path: str, s3_key: str, content_type: str = None, 
                        progress_callback: Callable = None) -> Optional[str]:
        """와사비 S3에 파일 업로드 (Railway 최적화)"""
        try:
            extra_args = {'ACL': 'public-read'}
            if content_type:
                extra_args['ContentType'] = content_type
            
            # Railway 최적화된 업로드
            if progress_callback:
                self.s3_client.upload_file(
                    local_path, 
                    self.bucket_name, 
                    s3_key,
                    Config=self.transfer_config,
                    ExtraArgs=extra_args,
                    Callback=progress_callback
                )
            else:
                self.s3_client.upload_file(
                    local_path, 
                    self.bucket_name, 
                    s3_key,
                    Config=self.transfer_config,
                    ExtraArgs=extra_args
                )
            
            # 공개 URL 생성
            if self.wasabi_cdn_url:
                public_url = f"{self.wasabi_cdn_url.rstrip('/')}/{s3_key}"
            else:
                region = os.environ.get('WASABI_REGION', 'us-east-1')
                public_url = f"https://s3.{region}.wasabisys.com/{self.bucket_name}/{s3_key}"
            
            logger.info(f"파일 업로드 성공: {s3_key}")
            return public_url
            
        except Exception as e:
            logger.error(f"와사비 업로드 실패: {e}")
            return None
    
    def upload_video(self, video_path: str, thumbnail_path: str, group_name: str, 
                    main_category: str, sub_category: str, leaf_category: str,
                    content_description: str, translated_filenames: Dict[str, str],
                    progress_callback: Callable = None) -> Dict[str, Any]:
        """메인 비디오 업로드 (Railway 최적화)"""
        try:
            def update_progress(value: int, message: str):
                if progress_callback:
                    progress_callback(value, message)
            
            update_progress(5, "파일 검증 중...")
            
            # 동영상 메타데이터 추출
            video_metadata = self.extract_video_metadata(video_path)
            
            update_progress(10, "업로드 준비 중...")
            
            # Railway 최적화된 그룹 ID 및 경로 생성
            group_id = uuid.uuid4().hex
            date_str = datetime.now().strftime('%Y%m%d')
            safe_name = re.sub(r'[^\w가-힣]', '_', group_name)
            
            # Railway 최적화 폴더 구조
            year = datetime.now().strftime('%Y')
            month = datetime.now().strftime('%m')
            base_folder = f"videos/{year}/{month}/{group_id}_{safe_name}"
            
            update_progress(15, "동영상 업로드 중...")
            
            # 동영상 업로드
            video_ext = Path(video_path).suffix.lower()
            translated_ko_title = translated_filenames.get('ko', safe_name)
            video_s3_key = f"{base_folder}/{translated_ko_title}_video_ko{video_ext}"
            
            content_type_map = {
                '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
                '.flv': 'video/x-flv'
            }
            video_content_type = content_type_map.get(video_ext, 'video/mp4')
            
            # Railway 최적화된 진행률 콜백
            def file_progress_callback(bytes_transferred):
                if hasattr(file_progress_callback, 'file_size') and file_progress_callback.file_size > 0:
                    percentage = min((bytes_transferred / file_progress_callback.file_size) * 60, 60)
                    update_progress(15 + percentage, f"동영상 업로드 중... {percentage:.1f}%")
            
            file_progress_callback.file_size = os.path.getsize(video_path)
            
            video_url = self.upload_to_wasabi(
                video_path, 
                video_s3_key, 
                video_content_type,
                file_progress_callback
            )
            
            if not video_url:
                raise Exception("동영상 업로드 실패")
            
            update_progress(75, "썸네일 처리 중...")
            
            # 썸네일 업로드 (Railway 최적화)
            thumbnail_url = None
            thumbnail_s3_key = None
            if thumbnail_path:
                thumb_ext = Path(thumbnail_path).suffix.lower()
                thumbnail_s3_key = f"{base_folder}/{translated_ko_title}_thumbnail{thumb_ext}"
                
                thumb_content_type_map = {
                    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'
                }
                thumb_content_type = thumb_content_type_map.get(thumb_ext, 'image/jpeg')
                
                thumbnail_url = self.upload_to_wasabi(thumbnail_path, thumbnail_s3_key, thumb_content_type)
            
            update_progress(85, "QR 코드 생성 중...")
            
            # QR 코드 생성 (Railway 최적화)
            qr_link = f"{self.app_base_url}{group_id}"
            qr_temp_path = os.path.join(tempfile.gettempdir(), f"qr_{group_id}.png")
            
            qr_title = group_name[:30]  # Railway 최적화
            if all([main_category, sub_category, leaf_category]):
                qr_title = f"{group_name}\n({main_category}>{sub_category})"
            
            qr_url = None
            qr_s3_key = None
            if self.create_qr_code(qr_link, qr_temp_path, qr_title):
                qr_s3_key = f"{base_folder}/{translated_ko_title}_qrcode.png"
                qr_url = self.upload_to_wasabi(qr_temp_path, qr_s3_key, 'image/png')
                
                # Railway 임시 파일 정리
                try:
                    os.remove(qr_temp_path)
                except:
                    pass
            
            update_progress(95, "데이터베이스 저장 중...")
            
            # Firestore에 저장 (Railway 최적화된 구조)
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
                'supported_video_languages': ['ko']  # Railway 최적화
            }
            
            # 선택적 필드 추가
            if qr_url:
                main_doc_data.update({
                    'qr_link': qr_link, 'qr_s3_key': qr_s3_key, 'qr_url': qr_url
                })
            
            if thumbnail_url:
                main_doc_data.update({
                    'thumbnail_s3_key': thumbnail_s3_key, 'thumbnail_url': thumbnail_url
                })
            
            # 메인 문서 저장
            main_doc_ref = self.db.collection('uploads').document(group_id)
            main_doc_ref.set(main_doc_data)
            
            # 언어별 영상 서브컬렉션에 저장
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
            language_doc_ref.set(language_doc_data)
            
            # 메타데이터 저장 (Railway 최적화)
            if translated_filenames:
                translation_metadata = {
                    'filenames': translated_filenames,
                    'created_at': firestore.SERVER_TIMESTAMP
                }
                translation_doc_ref = main_doc_ref.collection('metadata').document('translations')
                translation_doc_ref.set(translation_metadata)
            
            # 검색 메타데이터 저장
            search_metadata = {
                'searchable_title': group_name.lower(),
                'searchable_content': content_description.lower(),
                'category_path': f"{main_category}/{sub_category}/{leaf_category}",
                'tags': self._extract_tags_from_content(content_description),
                'created_at': firestore.SERVER_TIMESTAMP
            }
            search_doc_ref = main_doc_ref.collection('metadata').document('search_data')
            search_doc_ref.set(search_metadata)
            
            update_progress(100, "업로드 완료!")
            
            return {
                'success': True,
                'group_id': group_id,
                'video_url': video_url,
                'qr_link': qr_link,
                'qr_url': qr_url,
                'thumbnail_url': thumbnail_url,
                'metadata': video_metadata
            }
            
        except Exception as e:
            logger.error(f"비디오 업로드 실패: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def upload_language_video(self, video_id: str, language_code: str, video_path: str,
                             progress_callback: Callable = None) -> Dict[str, Any]:
        """언어별 영상 업로드 (Railway 최적화)"""
        try:
            def update_progress(value: int, message: str):
                if progress_callback:
                    progress_callback(value, message)
            
            update_progress(10, "기존 영상 정보 확인 중...")
            
            # 기존 영상 정보 가져오기
            doc_ref = self.db.collection('uploads').document(video_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                raise Exception("선택된 영상을 찾을 수 없습니다.")
            
            video_data = doc.to_dict()
            
            update_progress(20, "언어별 영상 준비 중...")
            
            # 메타데이터 추출
            video_metadata = self.extract_video_metadata(video_path)
            
            # Railway 최적화된 파일 경로 생성
            group_name = video_data.get('group_name', 'unknown')
            upload_date = video_data.get('upload_date', datetime.now().strftime('%Y%m%d'))
            safe_name = re.sub(r'[^\w가-힣]', '_', group_name)
            
            year = upload_date[:4] if len(upload_date) >= 4 else datetime.now().strftime('%Y')
            month = upload_date[4:6] if len(upload_date) >= 6 else datetime.now().strftime('%m')
            base_folder = f"videos/{year}/{month}/{video_id}_{safe_name}"
            
            # 번역된 파일명 가져오기
            try:
                metadata_ref = doc_ref.collection('metadata').document('translations')
                metadata_doc = metadata_ref.get()
                if metadata_doc.exists:
                    stored_translations = metadata_doc.to_dict().get('filenames', {})
                    translated_title = stored_translations.get(language_code, safe_name)
                else:
                    translated_title = safe_name
            except:
                translated_title = safe_name
            
            video_ext = Path(video_path).suffix.lower()
            video_s3_key = f"{base_folder}/{translated_title}_video_{language_code}{video_ext}"
            
            content_type_map = {
                '.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime',
                '.wmv': 'video/x-ms-wmv', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
                '.flv': 'video/x-flv'
            }
            video_content_type = content_type_map.get(video_ext, 'video/mp4')
            
            update_progress(40, "언어별 영상 업로드 중...")
            
            # Railway 최적화된 진행률 콜백
            def lang_progress_callback(bytes_transferred):
                if hasattr(lang_progress_callback, 'file_size') and lang_progress_callback.file_size > 0:
                    percentage = min((bytes_transferred / lang_progress_callback.file_size) * 40, 40)
                    update_progress(40 + percentage, f"언어별 영상 업로드 중... {percentage:.1f}%")
            
            lang_progress_callback.file_size = os.path.getsize(video_path)
            
            video_url = self.upload_to_wasabi(
                video_path,
                video_s3_key,
                video_content_type,
                lang_progress_callback
            )
            
            if not video_url:
                raise Exception("언어별 영상 업로드 실패")
            
            update_progress(90, "데이터베이스 업데이트 중...")
            
            # 언어별 영상 데이터 저장
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
                'is_original': False
            }
            
            # 언어별 영상 서브컬렉션에 저장
            language_doc_ref = doc_ref.collection('language_videos').document(language_code)
            language_doc_ref.set(language_doc_data)
            
            # 메인 문서 업데이트 (Railway 최적화)
            existing_languages = video_data.get('supported_video_languages', ['ko'])
            if language_code not in existing_languages:
                existing_languages.append(language_code)
            
            doc_ref.update({
                'supported_video_languages': existing_languages,
                'supported_languages_count': len(existing_languages),
                'updated_at': firestore.SERVER_TIMESTAMP
            })
            
            update_progress(100, "언어별 영상 업로드 완료!")
            
            return {
                'success': True,
                'video_url': video_url,
                'language_code': language_code,
                'metadata': video_metadata
            }
            
        except Exception as e:
            logger.error(f"언어별 영상 업로드 실패: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_existing_videos(self) -> List[Dict[str, Any]]:
        """기존 영상 목록 가져오기 (Railway 최적화)"""
        try:
            # Railway 메모리 최적화를 위해 제한된 수량만 가져오기
            docs = self.db.collection('uploads').order_by('created_at', direction=firestore.Query.DESCENDING).limit(50).get()
            
            videos_data = []
            
            for doc in docs:
                data = doc.to_dict()
                
                # 언어별 영상 정보 가져오기 (Railway 메모리 최적화)
                language_videos = {}
                supported_languages = []
                
                try:
                    lang_docs = doc.reference.collection('language_videos').get()
                    for lang_doc in lang_docs:
                        lang_data = lang_doc.to_dict()
                        language_videos[lang_doc.id] = lang_data
                        supported_languages.append(lang_doc.id)
                except Exception as e:
                    logger.warning(f"언어별 영상 로드 실패: {e}")
                    # 기본값으로 한국어만 지원
                    supported_languages = ['ko']
                
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
                    'data': data
                }
                
                videos_data.append(video_info)
            
            return videos_data
            
        except Exception as e:
            logger.error(f"영상 목록 로드 실패: {e}")
            return []
    
    def _extract_tags_from_content(self, content: str) -> List[str]:
        """강의 내용에서 태그 추출 (Railway 최적화)"""
        keywords = []
        
        try:
            # 불릿 포인트 추출 (Railway 최적화)
            bullet_items = re.findall(r'[•·▪▫◦‣⁃]\s*([^•·▪▫◦‣⁃\n]+)', content)
            numbered_items = re.findall(r'\d+\.\s*([^\d\n]+)', content)
            
            keywords.extend([item.strip() for item in bullet_items[:3]])  # Railway 메모리 절약
            keywords.extend([item.strip() for item in numbered_items[:3]])
            
            # 일반적인 키워드 (Railway 최적화)
            common_keywords = ['안전', '교육', '장비', '사용법', '점검', '응급처치', '비상대응', '법규', '규정']
            for keyword in common_keywords:
                if keyword in content and keyword not in keywords:
                    keywords.append(keyword)
                    if len(keywords) >= 6:  # Railway 메모리 제한
                        break
            
            # Railway 최적화: 중복 제거 및 길이 제한
            unique_keywords = []
            for k in keywords:
                if len(k.strip()) > 1 and k not in unique_keywords:
                    unique_keywords.append(k.strip())
                if len(unique_keywords) >= 6:
                    break
            
            return unique_keywords
            
        except Exception as e:
            logger.warning(f"태그 추출 실패: {e}")
            return ['교육', '안전']  # 기본 태그
    
    def _get_language_name(self, language_code: str) -> str:
        """언어 코드를 언어명으로 변환 (Railway 최적화)"""
        language_names = {
            'ko': '한국어',
            'en': 'English',
            'zh': '中文',
            'vi': 'Tiếng Việt',
            'th': 'ไทย',
            'ja': '日本語'
        }
        return language_names.get(language_code, language_code)
    
    def cleanup_temp_files(self, file_paths: List[str]):
        """임시 파일 정리 (Railway 메모리 최적화)"""
        for file_path in file_paths:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.debug(f"임시 파일 삭제: {file_path}")
            except Exception as e:
                logger.warning(f"임시 파일 삭제 실패 {file_path}: {e}")
    
    def get_upload_status(self, group_id: str) -> Dict[str, Any]:
        """업로드 상태 확인 (Railway 최적화)"""
        try:
            doc_ref = self.db.collection('uploads').document(group_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'success': False, 'error': '영상을 찾을 수 없습니다'}
            
            data = doc.to_dict()
            
            # 언어별 영상 상태 확인
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
                'thumbnail_url': data.get('thumbnail_url', '')
            }
            
        except Exception as e:
            logger.error(f"업로드 상태 확인 실패: {e}")
            return {'success': False, 'error': str(e)}
    
    def delete_video(self, group_id: str) -> Dict[str, Any]:
        """영상 삭제 (Railway 최적화)"""
        try:
            doc_ref = self.db.collection('uploads').document(group_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'success': False, 'error': '영상을 찾을 수 없습니다'}
            
            data = doc.to_dict()
            
            # S3에서 파일들 삭제
            files_to_delete = []
            
            # 언어별 영상 파일들
            try:
                lang_docs = doc_ref.collection('language_videos').get()
                for lang_doc in lang_docs:
                    lang_data = lang_doc.to_dict()
                    if 'video_s3_key' in lang_data:
                        files_to_delete.append(lang_data['video_s3_key'])
            except:
                pass
            
            # QR 코드, 썸네일
            if 'qr_s3_key' in data:
                files_to_delete.append(data['qr_s3_key'])
            if 'thumbnail_s3_key' in data:
                files_to_delete.append(data['thumbnail_s3_key'])
            
            # S3 파일 삭제 (Railway 최적화)
            deleted_files = []
            for s3_key in files_to_delete:
                try:
                    self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
                    deleted_files.append(s3_key)
                    logger.info(f"S3 파일 삭제: {s3_key}")
                except Exception as e:
                    logger.warning(f"S3 파일 삭제 실패 {s3_key}: {e}")
            
            # Firestore 문서 삭제
            # 서브컬렉션 삭제
            try:
                lang_docs = doc_ref.collection('language_videos').get()
                for lang_doc in lang_docs:
                    lang_doc.reference.delete()
                
                metadata_docs = doc_ref.collection('metadata').get()
                for meta_doc in metadata_docs:
                    meta_doc.reference.delete()
            except:
                pass
            
            # 메인 문서 삭제
            doc_ref.delete()
            
            return {
                'success': True,
                'message': f'영상이 성공적으로 삭제되었습니다',
                'deleted_files': deleted_files
            }
            
        except Exception as e:
            logger.error(f"영상 삭제 실패: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_video_analytics(self, group_id: str) -> Dict[str, Any]:
        """영상 분석 데이터 (Railway 최적화)"""
        try:
            doc_ref = self.db.collection('uploads').document(group_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'success': False, 'error': '영상을 찾을 수 없습니다'}
            
            data = doc.to_dict()
            
            # 기본 분석 데이터
            analytics = {
                'success': True,
                'group_id': group_id,
                'group_name': data.get('group_name', ''),
                'upload_date': data.get('upload_date', ''),
                'total_file_size': data.get('total_file_size', 0),
                'supported_languages_count': data.get('supported_languages_count', 1),
                'category_path': f"{data.get('main_category', '')} > {data.get('sub_category', '')} > {data.get('sub_sub_category', '')}",
                'qr_link': data.get('qr_link', ''),
                'created_at': data.get('created_at', ''),
                'language_breakdown': {}
            }
            
            # 언어별 세부 정보
            try:
                lang_docs = doc_ref.collection('language_videos').get()
                for lang_doc in lang_docs:
                    lang_data = lang_doc.to_dict()
                    analytics['language_breakdown'][lang_doc.id] = {
                        'language_name': lang_data.get('language_name', ''),
                        'file_size': lang_data.get('file_size', 0),
                        'duration': lang_data.get('duration_string', ''),
                        'resolution': f"{lang_data.get('video_width', 0)}x{lang_data.get('video_height', 0)}",
                        'fps': lang_data.get('video_fps', 0),
                        'upload_date': lang_data.get('upload_date', ''),
                        'is_original': lang_data.get('is_original', False)
                    }
            except:
                pass
            
            return analytics
            
        except Exception as e:
            logger.error(f"영상 분석 데이터 조회 실패: {e}")
            return {'success': False, 'error': str(e)}