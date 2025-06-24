# app.py - 하이브리드 프록시 Flask 백엔드 (Wasabi 저장 + Railway 프록시)
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, Response, send_file
import os
import tempfile
import json
import gc
import time
import threading
import sys
import io
from pathlib import Path
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import logging
from datetime import datetime, timedelta

# Railway 환경에서 서비스 로딩을 안전하게 처리
try:
    from video_uploader_logic import VideoUploaderLogic, GoogleTranslator, CATEGORY_STRUCTURE
    SERVICES_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ 서비스 모듈 로딩 실패 (나중에 재시도): {e}")
    SERVICES_AVAILABLE = False
    # 기본 카테고리 구조
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

# Flask 앱 초기화 (Railway 최적화)
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-railway-2024')

# 브런치 도메인 설정
BRUNCH_DOMAIN = os.environ.get('BRUNCH_DOMAIN', 'videouploader-production.up.railway.app')
BRUNCH_ALTERNATE_DOMAIN = os.environ.get('BRUNCH_ALTERNATE_DOMAIN', 'videouploader-production.up.railway.app')

# Railway 최적화 설정
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024 * 1024,  # 5GB
    UPLOAD_FOLDER=tempfile.gettempdir(),
    JSON_SORT_KEYS=False,
    JSONIFY_PRETTYPRINT_REGULAR=False,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    SESSION_COOKIE_SECURE=False,  # Railway HTTPS 자동 처리
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# 지원 언어 정의
SUPPORTED_LANGUAGES = {
    'ko': '한국어',
    'en': 'English',
    'zh': '中文',
    'vi': 'Tiếng Việt',
    'th': 'ไทย',
    'ja': '日본語'
}

# Railway 로깅 설정
log_level = logging.INFO
if os.environ.get('DEBUG') == 'true':
    log_level = logging.DEBUG

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# 전역 서비스 인스턴스 (지연 로딩)
uploader_service = None
translator_service = None
service_lock = threading.Lock()
service_init_attempted = False

# 업로드 상태 추적
upload_status = {}
upload_lock = threading.Lock()

# 파일 캐시 (Railway 메모리 최적화)
file_cache = {}
cache_lock = threading.Lock()
MAX_CACHE_SIZE = 50 * 1024 * 1024  # 50MB 캐시 제한

def safe_get_service_instances():
    """Railway 안전한 서비스 인스턴스 획득"""
    global uploader_service, translator_service, service_init_attempted, SERVICES_AVAILABLE
    
    if not SERVICES_AVAILABLE and not service_init_attempted:
        try:
            global VideoUploaderLogic, GoogleTranslator
            from video_uploader_logic import VideoUploaderLogic, GoogleTranslator, CATEGORY_STRUCTURE
            SERVICES_AVAILABLE = True
            logger.info("✅ Railway 환경에서 서비스 모듈 로딩 성공")
        except ImportError as e:
            logger.error(f"❌ Railway 서비스 모듈 로딩 실패: {e}")
            service_init_attempted = True
            return None, None
    
    if not SERVICES_AVAILABLE:
        logger.warning("⚠️ 서비스가 사용 불가능합니다")
        return None, None
    
    with service_lock:
        try:
            if uploader_service is None:
                logger.info("🔧 Railway 하이브리드 서비스 초기화 시작")
                uploader_service = VideoUploaderLogic()
                translator_service = GoogleTranslator()
                logger.info("✅ Railway 하이브리드 서비스 초기화 완료")
        except Exception as e:
            logger.error(f"❌ Railway 서비스 초기화 실패: {e}")
            return None, None
    
    return uploader_service, translator_service

def cleanup_memory():
    """Railway 메모리 정리"""
    try:
        gc.collect()
        with upload_lock:
            current_time = time.time()
            expired_keys = [
                key for key, value in upload_status.items()
                if current_time - value.get('timestamp', 0) > 3600
            ]
            for key in expired_keys:
                del upload_status[key]
        
        # 파일 캐시 정리
        with cache_lock:
            if len(file_cache) > 20:  # 캐시 항목이 많으면 절반 정리
                sorted_cache = sorted(file_cache.items(), key=lambda x: x[1].get('last_access', 0))
                for key, _ in sorted_cache[:len(file_cache)//2]:
                    del file_cache[key]
        
        logger.debug(f"🧹 Railway 메모리 정리 완료, 활성 업로드: {len(upload_status)}, 캐시: {len(file_cache)}")
    except Exception as e:
        logger.warning(f"메모리 정리 중 오류: {e}")

def get_content_type(file_path: str) -> str:
    """파일 확장자에 따른 Content-Type 반환"""
    ext = Path(file_path).suffix.lower()
    content_types = {
        # 비디오
        '.mp4': 'video/mp4',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime',
        '.wmv': 'video/x-ms-wmv',
        '.webm': 'video/webm',
        '.mkv': 'video/x-matroska',
        '.flv': 'video/x-flv',
        '.3gp': 'video/3gpp',
        '.m4v': 'video/x-m4v',
        # 이미지
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml',
        '.tiff': 'image/tiff'
    }
    return content_types.get(ext, 'application/octet-stream')

# Railway 헬스체크 (가장 중요!)
@app.route('/health')
def health_check():
    """Railway 헬스체크 - 즉시 응답"""
    try:
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'environment': 'railway',
            'services_available': SERVICES_AVAILABLE,
            'service_init_attempted': service_init_attempted,
            'active_uploads': len(upload_status),
            'cached_files': len(file_cache),
            'python_version': sys.version.split()[0],
            'flask_ready': True,
            'brunch_domain': BRUNCH_DOMAIN,
            'proxy_enabled': True,
            'hybrid_mode': True
        }
        
        return jsonify(health_status), 200
        
    except Exception as e:
        logger.error(f"헬스체크 실패: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# =================== Railway 프록시 엔드포인트들 ===================

@app.route('/qr/<path:s3_key>')
def proxy_qr_code(s3_key):
    """QR 코드 파일 프록시"""
    try:
        logger.debug(f"QR 코드 프록시 요청: {s3_key}")
        
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        # 캐시 확인
        cache_key = f"qr_{s3_key}"
        with cache_lock:
            if cache_key in file_cache:
                cached_item = file_cache[cache_key]
                cached_item['last_access'] = time.time()
                logger.debug(f"QR 코드 캐시 히트: {s3_key}")
                
                return Response(
                    cached_item['data'],
                    mimetype=cached_item['content_type'],
                    headers={
                        'Cache-Control': 'public, max-age=86400',  # 1일 캐시
                        'Content-Length': str(len(cached_item['data']))
                    }
                )
        
        # Wasabi에서 파일 다운로드
        file_data = uploader.get_file_from_wasabi(s3_key)
        if not file_data:
            return jsonify({'error': 'QR 코드를 찾을 수 없습니다'}), 404
        
        content_type = 'image/png'  # QR 코드는 기본적으로 PNG
        
        # 캐시에 저장 (크기 확인)
        if len(file_data) < MAX_CACHE_SIZE // 10:  # 캐시 크기의 10% 이하만 저장
            with cache_lock:
                file_cache[cache_key] = {
                    'data': file_data,
                    'content_type': content_type,
                    'last_access': time.time()
                }
        
        logger.debug(f"✅ QR 코드 프록시 성공: {s3_key} ({len(file_data)} bytes)")
        
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Content-Length': str(len(file_data))
            }
        )
        
    except Exception as e:
        logger.error(f"❌ QR 코드 프록시 실패: {s3_key} - {e}")
        return jsonify({'error': 'QR 코드 로드 실패'}), 500

@app.route('/thumbnail/<path:s3_key>')
def proxy_thumbnail(s3_key):
    """썸네일 이미지 파일 프록시"""
    try:
        logger.debug(f"썸네일 프록시 요청: {s3_key}")
        
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        # 캐시 확인
        cache_key = f"thumb_{s3_key}"
        with cache_lock:
            if cache_key in file_cache:
                cached_item = file_cache[cache_key]
                cached_item['last_access'] = time.time()
                logger.debug(f"썸네일 캐시 히트: {s3_key}")
                
                return Response(
                    cached_item['data'],
                    mimetype=cached_item['content_type'],
                    headers={
                        'Cache-Control': 'public, max-age=86400',
                        'Content-Length': str(len(cached_item['data']))
                    }
                )
        
        # Wasabi에서 파일 다운로드
        file_data = uploader.get_file_from_wasabi(s3_key)
        if not file_data:
            return jsonify({'error': '썸네일을 찾을 수 없습니다'}), 404
        
        content_type = get_content_type(s3_key)
        
        # 캐시에 저장 (썸네일은 보통 작으므로 캐시)
        if len(file_data) < MAX_CACHE_SIZE // 5:  # 캐시 크기의 20% 이하만 저장
            with cache_lock:
                file_cache[cache_key] = {
                    'data': file_data,
                    'content_type': content_type,
                    'last_access': time.time()
                }
        
        logger.debug(f"✅ 썸네일 프록시 성공: {s3_key} ({len(file_data)} bytes)")
        
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Content-Length': str(len(file_data))
            }
        )
        
    except Exception as e:
        logger.error(f"❌ 썸네일 프록시 실패: {s3_key} - {e}")
        return jsonify({'error': '썸네일 로드 실패'}), 500

@app.route('/video/<path:s3_key>')
def proxy_video(s3_key):
    """동영상 파일 프록시 (스트리밍 지원)"""
    try:
        logger.debug(f"동영상 프록시 요청: {s3_key}")
        
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        # 파일 메타데이터 먼저 확인
        metadata = uploader.get_file_metadata_from_wasabi(s3_key)
        if not metadata:
            return jsonify({'error': '동영상을 찾을 수 없습니다'}), 404
        
        content_type = metadata['content_type']
        content_length = metadata['content_length']
        
        # Range 요청 처리 (동영상 스트리밍)
        range_header = request.headers.get('Range')
        if range_header:
            logger.debug(f"Range 요청: {range_header}")
            
            # Range 파싱 (예: bytes=0-1023)
            try:
                ranges = range_header.replace('bytes=', '').split('-')
                start = int(ranges[0]) if ranges[0] else 0
                end = int(ranges[1]) if ranges[1] else content_length - 1
                
                # Range가 유효한지 확인
                if start >= content_length or end >= content_length or start > end:
                    return Response(status=416)  # Range Not Satisfiable
                
                # Wasabi에서 Range 요청으로 부분 다운로드
                try:
                    response = uploader.s3_client.get_object(
                        Bucket=uploader.bucket_name,
                        Key=s3_key,
                        Range=f'bytes={start}-{end}'
                    )
                    partial_data = response['Body'].read()
                    
                    logger.debug(f"✅ 동영상 Range 프록시: {s3_key} (bytes {start}-{end})")
                    
                    return Response(
                        partial_data,
                        206,  # Partial Content
                        mimetype=content_type,
                        headers={
                            'Content-Range': f'bytes {start}-{end}/{content_length}',
                            'Accept-Ranges': 'bytes',
                            'Content-Length': str(len(partial_data)),
                            'Cache-Control': 'public, max-age=3600'
                        }
                    )
                    
                except Exception as range_error:
                    logger.warning(f"Range 요청 실패, 전체 파일로 폴백: {range_error}")
                    # Range 실패 시 전체 파일로 폴백
                    
            except (ValueError, IndexError) as parse_error:
                logger.warning(f"Range 헤더 파싱 실패: {parse_error}")
                # 파싱 실패 시 전체 파일로 폴백
        
        # 전체 파일 다운로드 (Range 요청이 없거나 실패한 경우)
        file_data = uploader.get_file_from_wasabi(s3_key)
        if not file_data:
            return jsonify({'error': '동영상을 찾을 수 없습니다'}), 404
        
        logger.debug(f"✅ 동영상 전체 프록시: {s3_key} ({len(file_data)} bytes)")
        
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Content-Length': str(len(file_data)),
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'public, max-age=3600'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ 동영상 프록시 실패: {s3_key} - {e}")
        return jsonify({'error': '동영상 로드 실패'}), 500

@app.route('/file/<path:s3_key>')
def proxy_generic_file(s3_key):
    """일반 파일 프록시 (필요시 확장 가능)"""
    try:
        logger.debug(f"일반 파일 프록시 요청: {s3_key}")
        
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        file_data = uploader.get_file_from_wasabi(s3_key)
        if not file_data:
            return jsonify({'error': '파일을 찾을 수 없습니다'}), 404
        
        content_type = get_content_type(s3_key)
        
        logger.debug(f"✅ 일반 파일 프록시: {s3_key} ({len(file_data)} bytes)")
        
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Content-Length': str(len(file_data)),
                'Cache-Control': 'public, max-age=3600'
            }
        )
        
    except Exception as e:
        logger.error(f"❌ 일반 파일 프록시 실패: {s3_key} - {e}")
        return jsonify({'error': '파일 로드 실패'}), 500

# =================== 기존 라우트들 ===================

@app.route('/')
def index():
    """Railway 메인 페이지"""
    try:
        return render_template('upload_form.html',
                             mains=CATEGORY_STRUCTURE['main_categories'],
                             subs=CATEGORY_STRUCTURE['sub_categories'],
                             leafs=CATEGORY_STRUCTURE['leaf_categories'])
    except Exception as e:
        logger.error(f"메인 페이지 로드 실패: {e}")
        return render_template('error.html', 
                             error_code=500, 
                             error_message=f"템플릿 로드 오류: {str(e)}"), 500

@app.route('/upload', methods=['POST'])
def upload_video():
    """완전한 비디오 업로드 처리 - 하이브리드 방식"""
    try:
        # 서비스 인스턴스 확인
        uploader, translator = safe_get_service_instances()
        if not uploader or not translator:
            flash('서비스가 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.', 'error')
            return redirect(url_for('index'))

        # 폼 데이터 검증
        group_name = request.form.get('group_name', '').strip()
        main_category = request.form.get('main_category', '').strip()
        sub_category = request.form.get('sub_category', '').strip()
        sub_sub_category = request.form.get('sub_sub_category', '').strip()
        content_description = request.form.get('content_description', '').strip()
        translated_filenames_json = request.form.get('translated_filenames', '{}')
        
        # 필수 필드 검증
        if not all([group_name, main_category, sub_category, sub_sub_category, content_description]):
            flash('모든 필수 항목을 입력해주세요.', 'error')
            return redirect(url_for('index'))
        
        # 파일 검증
        if 'file' not in request.files:
            flash('동영상 파일을 선택해주세요.', 'error')
            return redirect(url_for('index'))
        
        video_file = request.files['file']
        if video_file.filename == '':
            flash('동영상 파일을 선택해주세요.', 'error')
            return redirect(url_for('index'))
        
        # 썸네일 파일 (선택사항)
        thumbnail_file = request.files.get('thumbnail')
        
        # 번역된 파일명 파싱
        try:
            translated_filenames = json.loads(translated_filenames_json)
        except:
            flash('파일명 번역 정보가 올바르지 않습니다. 번역을 다시 확인해주세요.', 'error')
            return redirect(url_for('index'))
        
        if not translated_filenames:
            flash('파일명 번역을 먼저 확인해주세요.', 'error')
            return redirect(url_for('index'))
        
        # 임시 파일 저장
        with tempfile.TemporaryDirectory() as temp_dir:
            # 동영상 파일 저장
            video_filename = secure_filename(video_file.filename)
            video_path = os.path.join(temp_dir, video_filename)
            video_file.save(video_path)
            
            # 파일 크기 검증
            video_size = os.path.getsize(video_path)
            max_size = 5 * 1024 * 1024 * 1024  # 5GB
            if video_size > max_size:
                flash('파일 크기가 5GB를 초과합니다.', 'error')
                return redirect(url_for('index'))
            
            # 썸네일 파일 저장 (있는 경우)
            thumbnail_path = None
            if thumbnail_file and thumbnail_file.filename:
                thumbnail_filename = secure_filename(thumbnail_file.filename)
                thumbnail_path = os.path.join(temp_dir, thumbnail_filename)
                thumbnail_file.save(thumbnail_path)
            
            # 파일 검증
            if not uploader.validate_file(video_path, 'video'):
                flash('지원하지 않는 동영상 형식이거나 파일이 손상되었습니다.', 'error')
                return redirect(url_for('index'))
            
            if thumbnail_path and not uploader.validate_file(thumbnail_path, 'image'):
                flash('지원하지 않는 이미지 형식입니다.', 'error')
                return redirect(url_for('index'))
            
            # 실제 업로드 실행 (하이브리드 방식)
            logger.info(f"하이브리드 업로드 시작: {group_name}")
            
            result = uploader.upload_video(
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                group_name=group_name,
                main_category=main_category,
                sub_category=sub_category,
                leaf_category=sub_sub_category,
                content_description=content_description,
                translated_filenames=translated_filenames
            )
            
            if result['success']:
                logger.info(f"하이브리드 업로드 성공: {group_name} (ID: {result['group_id']})")
                
                # 성공 페이지로 리다이렉트
                category_path = f"{main_category} > {sub_category} > {sub_sub_category}"
                
                return render_template('upload_success.html',
                                     result=result,
                                     group_name=group_name,
                                     category_path=category_path)
            else:
                logger.error(f"하이브리드 업로드 실패: {result.get('error', '알 수 없는 오류')}")
                flash(f'업로드 실패: {result.get("error", "알 수 없는 오류")}', 'error')
                return redirect(url_for('index'))

    except RequestEntityTooLarge:
        flash('파일 크기가 너무 큽니다. 5GB 이하의 파일을 업로드해주세요.', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"업로드 처리 중 오류: {e}")
        flash(f'업로드 중 오류가 발생했습니다: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/watch/<video_id>')
def watch_video(video_id):
    """하이브리드 영상 시청 페이지 (Railway 프록시 URL 사용)"""
    try:
        # 서비스 인스턴스 확인
        uploader, translator = safe_get_service_instances()
        if not uploader:
            return render_template('error.html', 
                                 error_code=500, 
                                 error_message="서비스가 준비되지 않았습니다"), 500
        
        # 앱에서 요청한 언어 확인 (기본값: 한국어)
        requested_lang = request.args.get('lang', 'ko')
        if requested_lang not in SUPPORTED_LANGUAGES:
            requested_lang = 'ko'
        
        # User-Agent 확인 (앱 vs 웹 브라우저)
        user_agent = request.headers.get('User-Agent', '').lower()
        is_app_request = any(keyword in user_agent for keyword in ['dart', 'flutter', 'okhttp', 'mobile'])
        
        # 비디오 상태 확인
        video_status = uploader.get_upload_status(video_id)
        if not video_status['success']:
            if is_app_request:
                return jsonify({
                    'success': False,
                    'error': 'Video not found',
                    'message': '요청한 영상을 찾을 수 없습니다'
                }), 404
            else:
                return render_template('error.html', 
                                     error_code=404, 
                                     error_message="요청한 영상을 찾을 수 없습니다"), 404
        
        video_data = video_status
        
        # 언어별 영상 확인 및 URL 결정 (Railway 프록시 URL 사용)
        actual_language = requested_lang
        video_url = None
        has_language_video = False
        language_video_info = {}
        
        # 언어별 영상 데이터 확인
        language_videos = video_data.get('language_videos', {})
        
        if requested_lang != 'ko' and requested_lang in language_videos:
            # 요청한 언어의 영상이 있는 경우
            lang_video_data = language_videos[requested_lang]
            video_url = lang_video_data.get('video_url', '')  # Railway 프록시 URL
            
            if video_url:
                has_language_video = True
                language_video_info = {
                    'language_code': requested_lang,
                    'language_name': SUPPORTED_LANGUAGES[requested_lang],
                    'duration': lang_video_data.get('duration_string', ''),
                    'file_size': lang_video_data.get('file_size', 0),
                    'upload_date': lang_video_data.get('upload_date', ''),
                    'railway_proxy_enabled': lang_video_data.get('railway_proxy_enabled', True)
                }
                logger.info(f"🌍 언어별 영상 제공 (Railway 프록시): {video_id} ({requested_lang})")
            else:
                # URL이 없으면 한국어로 폴백
                actual_language = 'ko'
        else:
            # 요청한 언어가 없거나 한국어인 경우
            actual_language = 'ko'
        
        # 한국어 또는 폴백 영상 URL (Railway 프록시)
        if not video_url and 'ko' in language_videos:
            korean_video_data = language_videos['ko']
            video_url = korean_video_data.get('video_url', '')  # Railway 프록시 URL
        
        # 최종 폴백
        if not video_url:
            video_url = f"https://{BRUNCH_DOMAIN}/watch/{video_id}"
        
        # 앱용 JSON 응답
        if is_app_request:
            response_data = {
                'success': True,
                'video_id': video_id,
                'title': video_data.get('group_name', '제목 없음'),
                'video_url': video_url,  # Railway 프록시 URL
                'qr_url': video_data.get('qr_url', ''),  # Railway 프록시 URL
                'thumbnail_url': video_data.get('thumbnail_url', ''),  # Railway 프록시 URL
                'requested_language': requested_lang,
                'actual_language': actual_language,
                'language_name': SUPPORTED_LANGUAGES.get(actual_language, '한국어'),
                'has_language_video': has_language_video,
                'supported_languages': list(SUPPORTED_LANGUAGES.keys()),
                'brunch_domain': video_data.get('brunch_domain', BRUNCH_DOMAIN),
                'single_qr_link': f"https://{BRUNCH_DOMAIN}/watch/{video_id}",
                'railway_proxy_enabled': video_data.get('railway_proxy_enabled', True),
                'metadata': {
                    'upload_date': video_data.get('upload_date', ''),
                    'category': f"{video_data.get('main_category', '')} > {video_data.get('sub_category', '')} > {video_data.get('sub_sub_category', '')}",
                    'duration': language_video_info.get('duration', '0:00'),
                    'file_size': language_video_info.get('file_size', 0)
                }
            }
            
            # 언어별 영상 정보 추가
            if language_video_info:
                response_data['language_video_info'] = language_video_info
            
            # 자동 폴백 안내 (필요 시)
            if requested_lang != actual_language:
                response_data['fallback_info'] = {
                    'requested': SUPPORTED_LANGUAGES[requested_lang],
                    'provided': SUPPORTED_LANGUAGES[actual_language],
                    'reason': 'language_not_available'
                }
            
            return jsonify(response_data), 200
        
        # 웹 브라우저용 HTML 응답
        else:
            return render_template('watch.html',
                                 video_id=video_id,
                                 video_data=video_data,
                                 video_url=video_url,  # Railway 프록시 URL
                                 requested_language=requested_lang,
                                 actual_language=actual_language,
                                 has_language_video=has_language_video,
                                 supported_languages=SUPPORTED_LANGUAGES,
                                 brunch_domain=video_data.get('brunch_domain', BRUNCH_DOMAIN),
                                 single_qr_link=f"https://{BRUNCH_DOMAIN}/watch/{video_id}",
                                 railway_proxy_enabled=video_data.get('railway_proxy_enabled', True))
        
    except Exception as e:
        logger.error(f"영상 시청 페이지 오류: {e}")
        if 'is_app_request' in locals() and is_app_request:
            return jsonify({
                'success': False,
                'error': 'Video loading failed',
                'message': '영상 로드 중 오류가 발생했습니다',
                'details': str(e)
            }), 500
        else:
            return render_template('error.html', 
                                 error_code=500, 
                                 error_message=f"영상 로드 중 오류: {str(e)}"), 500

@app.route('/api/videos/<video_id>/languages', methods=['GET'])
def get_video_languages(video_id):
    """특정 영상의 사용 가능한 언어 목록 조회 (Railway 프록시 URL 포함)"""
    try:
        uploader, translator = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        video_status = uploader.get_upload_status(video_id)
        if not video_status['success']:
            return jsonify({'error': '영상을 찾을 수 없습니다'}), 404
        
        # 기본 한국어는 항상 사용 가능
        available_languages = {'ko': True}
        language_details = {}
        
        # 언어별 영상 확인
        language_videos = video_status.get('language_videos', {})
        for lang_code in SUPPORTED_LANGUAGES.keys():
            if lang_code != 'ko':
                if lang_code in language_videos:
                    lang_data = language_videos[lang_code]
                    available_languages[lang_code] = bool(lang_data.get('video_url'))
                    if available_languages[lang_code]:
                        language_details[lang_code] = {
                            'duration': lang_data.get('duration_string', ''),
                            'file_size': lang_data.get('file_size', 0),
                            'upload_date': lang_data.get('upload_date', ''),
                            'railway_proxy_enabled': lang_data.get('railway_proxy_enabled', True)
                        }
                else:
                    available_languages[lang_code] = False
        
        # 한국어 정보 추가
        if 'ko' in language_videos:
            ko_data = language_videos['ko']
            language_details['ko'] = {
                'duration': ko_data.get('duration_string', ''),
                'file_size': ko_data.get('file_size', 0),
                'upload_date': ko_data.get('upload_date', ''),
                'railway_proxy_enabled': ko_data.get('railway_proxy_enabled', True)
            }
        
        return jsonify({
            'video_id': video_id,
            'available_languages': available_languages,
            'language_details': language_details,
            'supported_languages': SUPPORTED_LANGUAGES,
            'total_available': len([lang for lang, available in available_languages.items() if available]),
            'single_qr_link': f"https://{BRUNCH_DOMAIN}/watch/{video_id}",
            'brunch_domain': BRUNCH_DOMAIN,
            'railway_proxy_enabled': True
        }), 200
        
    except Exception as e:
        logger.error(f"언어 목록 조회 실패: {e}")
        return jsonify({'error': '언어 목록을 가져올 수 없습니다', 'details': str(e)}), 500

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """완전한 번역 API"""
    try:
        uploader, translator = safe_get_service_instances()
        if not translator:
            return jsonify({
                'success': False,
                'error': '번역 서비스가 준비되지 않았습니다'
            }), 503

        data = request.get_json()
        text = data.get('text', '').strip()
        
        if not text:
            return jsonify({'success': False, 'error': '번역할 텍스트가 없습니다'}), 400

        # 번역 실행 (실제 GoogleTranslator 사용)
        translations = translator.translate_title(text)
        
        return jsonify({
            'success': True,
            'translations': translations,
            'original_text': text
        })

    except Exception as e:
        logger.error(f"번역 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'번역 중 오류: {str(e)}'
        }), 500

@app.route('/api/admin/videos', methods=['GET'])
def get_existing_videos():
    """기존 영상 목록 API (Railway 프록시 URL 포함)"""
    try:
        uploader, translator = safe_get_service_instances()
        if not uploader:
            return jsonify({
                'success': False,
                'error': '비디오 서비스가 준비되지 않았습니다',
                'videos': []
            }), 503

        videos_data = uploader.get_existing_videos()
        
        return jsonify({
            'success': True,
            'videos': videos_data,
            'total': len(videos_data),
            'brunch_domain': BRUNCH_DOMAIN,
            'railway_proxy_enabled': True,
            'hybrid_mode': True
        })

    except Exception as e:
        logger.error(f"영상 목록 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'영상 목록 로드 실패: {str(e)}',
            'videos': []
        }), 500

@app.route('/api/admin/upload_language_video', methods=['POST'])
def upload_language_video():
    """언어별 영상 업로드 API (하이브리드 방식)"""
    try:
        uploader, translator = safe_get_service_instances()
        if not uploader:
            return jsonify({
                'success': False,
                'error': '업로드 서비스가 준비되지 않았습니다'
            }), 503

        # 폼 데이터 검증
        group_id = request.form.get('group_id', '').strip()
        language_code = request.form.get('language_code', '').strip()
        
        if not group_id or not language_code:
            return jsonify({
                'success': False,
                'error': '그룹 ID와 언어 코드가 필요합니다'
            }), 400
        
        if language_code not in SUPPORTED_LANGUAGES:
            return jsonify({
                'success': False,
                'error': f'지원되지 않는 언어입니다: {language_code}'
            }), 400
        
        # 파일 검증
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'error': '동영상 파일을 선택해주세요'
            }), 400
        
        lang_video_file = request.files['file']
        if lang_video_file.filename == '':
            return jsonify({
                'success': False,
                'error': '동영상 파일을 선택해주세요'
            }), 400
        
        # 임시 파일 저장
        with tempfile.TemporaryDirectory() as temp_dir:
            lang_video_filename = secure_filename(lang_video_file.filename)
            lang_video_path = os.path.join(temp_dir, lang_video_filename)
            lang_video_file.save(lang_video_path)
            
            # 파일 검증
            if not uploader.validate_file(lang_video_path, 'video'):
                return jsonify({
                    'success': False,
                    'error': '지원하지 않는 동영상 형식이거나 파일이 손상되었습니다'
                }), 400
            
            # 언어별 영상 업로드 실행 (하이브리드 방식)
            result = uploader.upload_language_video(
                video_id=group_id,
                language_code=language_code,
                video_path=lang_video_path
            )
            
            if result['success']:
                logger.info(f"언어별 영상 업로드 성공 (하이브리드): {group_id} ({language_code})")
                
                # 결과에 Railway 프록시 정보 추가
                result['single_qr_link'] = f"https://{BRUNCH_DOMAIN}/watch/{group_id}"
                result['brunch_domain'] = BRUNCH_DOMAIN
                result['railway_proxy_enabled'] = True
                
                return jsonify(result)
            else:
                logger.error(f"언어별 영상 업로드 실패: {result.get('error', '알 수 없는 오류')}")
                return jsonify(result), 400

    except Exception as e:
        logger.error(f"언어별 영상 업로드 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'업로드 중 오류: {str(e)}'
        }), 500

# Railway 오류 처리
@app.errorhandler(404)
def page_not_found(error):
    """Railway 404 처리"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API 엔드포인트를 찾을 수 없습니다'}), 404
    
    return render_template('error.html', error_code=404), 404

@app.errorhandler(500)
def internal_server_error(error):
    """Railway 500 처리"""
    logger.error(f"서버 내부 오류: {error}")
    
    if request.path.startswith('/api/'):
        return jsonify({'error': '서버 내부 오류가 발생했습니다'}), 500
    
    return render_template('error.html', error_code=500), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    """파일 크기 초과 처리"""
    if request.path.startswith('/api/'):
        return jsonify({'error': '파일 크기가 너무 큽니다 (최대 5GB)'}), 413
    
    return render_template('error.html', error_code=413), 413

# Railway favicon 처리
@app.route('/favicon.ico')
def favicon():
    """Railway favicon 처리"""
    return '', 204

# Railway 메모리 정리 엔드포인트 (개발용)
@app.route('/admin/cleanup')
def admin_cleanup():
    """Railway 메모리 정리 (개발/관리용)"""
    try:
        cleanup_memory()
        return jsonify({
            'success': True,
            'message': '메모리 정리 완료',
            'active_uploads': len(upload_status),
            'cached_files': len(file_cache),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# 캐시 상태 확인 엔드포인트
@app.route('/admin/cache_status')
def cache_status():
    """캐시 상태 확인 (관리용)"""
    try:
        with cache_lock:
            cache_info = []
            total_cache_size = 0
            
            for key, cached_item in file_cache.items():
                cache_info.append({
                    'key': key,
                    'size': len(cached_item['data']),
                    'content_type': cached_item['content_type'],
                    'last_access': cached_item['last_access']
                })
                total_cache_size += len(cached_item['data'])
        
        return jsonify({
            'success': True,
            'cache_count': len(file_cache),
            'total_cache_size': total_cache_size,
            'max_cache_size': MAX_CACHE_SIZE,
            'cache_usage_percent': (total_cache_size / MAX_CACHE_SIZE) * 100,
            'cache_items': cache_info,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# QR 코드 재생성 엔드포인트 (필요시 사용)
@app.route('/api/admin/regenerate_qr/<video_id>', methods=['POST'])
def regenerate_qr_code(video_id):
    """QR 코드 재생성 (관리용)"""
    try:
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({
                'success': False,
                'error': '서비스가 준비되지 않았습니다'
            }), 503
        
        # 비디오 정보 확인
        video_status = uploader.get_upload_status(video_id)
        if not video_status['success']:
            return jsonify({
                'success': False,
                'error': '영상을 찾을 수 없습니다'
            }), 404
        
        video_data = video_status
        group_name = video_data.get('group_name', 'Unknown')
        
        # QR 코드 재생성
        with tempfile.TemporaryDirectory() as temp_dir:
            qr_temp_path = os.path.join(temp_dir, f"qr_regenerated_{video_id}.png")
            
            if uploader.create_qr_with_thumbnail(video_id, group_name, None, qr_temp_path):
                # 새 S3 키 생성
                base_folder = video_data.get('base_folder', f"videos/{video_id}")
                qr_s3_key = f"{base_folder}/qr_regenerated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                
                # Wasabi 업로드
                qr_url = uploader.upload_to_wasabi(qr_temp_path, qr_s3_key, 'image/png')
                
                if qr_url:
                    # Firestore 업데이트
                    uploader.db.collection('uploads').document(video_id).update({
                        'qr_s3_key': qr_s3_key,
                        'qr_url': qr_url,
                        'qr_regenerated_at': firestore.SERVER_TIMESTAMP
                    })
                    
                    return jsonify({
                        'success': True,
                        'qr_url': qr_url,
                        'message': 'QR 코드가 성공적으로 재생성되었습니다'
                    })
                else:
                    return jsonify({
                        'success': False,
                        'error': 'QR 코드 업로드 실패'
                    }), 500
            else:
                return jsonify({
                    'success': False,
                    'error': 'QR 코드 생성 실패'
                }), 500
                
    except Exception as e:
        logger.error(f"QR 코드 재생성 실패: {e}")
        return jsonify({
            'success': False,
            'error': f'QR 코드 재생성 중 오류: {str(e)}'
        }), 500

# 파일 정보 확인 엔드포인트
@app.route('/api/admin/file_info/<path:s3_key>')
def get_file_info(s3_key):
    """파일 정보 확인 (관리용)"""
    try:
        uploader, _ = safe_get_service_instances()
        if not uploader:
            return jsonify({'error': '서비스가 준비되지 않았습니다'}), 503
        
        # Wasabi에서 파일 메타데이터 조회
        metadata = uploader.get_file_metadata_from_wasabi(s3_key)
        if not metadata:
            return jsonify({'error': '파일을 찾을 수 없습니다'}), 404
        
        # 프록시 URL 생성
        if 'qr' in s3_key.lower():
            proxy_url = f"https://{BRUNCH_DOMAIN}/qr/{s3_key}"
        elif 'thumbnail' in s3_key.lower():
            proxy_url = f"https://{BRUNCH_DOMAIN}/thumbnail/{s3_key}"
        elif any(video_ext in s3_key.lower() for video_ext in ['.mp4', '.avi', '.mov', '.wmv']):
            proxy_url = f"https://{BRUNCH_DOMAIN}/video/{s3_key}"
        else:
            proxy_url = f"https://{BRUNCH_DOMAIN}/file/{s3_key}"
        
        return jsonify({
            'success': True,
            's3_key': s3_key,
            'proxy_url': proxy_url,
            'metadata': metadata,
            'file_exists': True
        })
        
    except Exception as e:
        logger.error(f"파일 정보 조회 실패: {s3_key} - {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Railway 배포용 메인 실행
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    logger.info(f"🚀 Railway 하이브리드 서버 시작 - 브런치 도메인: {BRUNCH_DOMAIN}")
    logger.info(f"🔄 프록시 엔드포인트: /qr/, /thumbnail/, /video/, /file/")
    logger.info(f"💾 Wasabi 저장소 + Railway 프록시 = 영구 URL 보장")
    
    app.run(host='0.0.0.0', port=port, debug=debug)