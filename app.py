# app.py - 개선된 Flask 백엔드 서버 (Railway 최적화, Flask 2.2+ 완전 호환)
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import os
import tempfile
import json
import gc
import time
import threading
from pathlib import Path
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import logging
from datetime import datetime, timedelta
from video_uploader_logic import VideoUploaderLogic, GoogleTranslator, CATEGORY_STRUCTURE

# Flask 앱 초기화 (Railway 최적화)
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Railway 최적화 설정
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024 * 1024,  # 5GB
    UPLOAD_FOLDER=tempfile.gettempdir(),
    JSON_SORT_KEYS=False,
    JSONIFY_PRETTYPRINT_REGULAR=False,  # Railway 메모리 절약
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),  # 세션 관리
    SESSION_COOKIE_SECURE=True if os.environ.get('RAILWAY_ENVIRONMENT') else False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# 로깅 설정 (Railway 최적화)
log_level = logging.INFO if os.environ.get('RAILWAY_ENVIRONMENT') else logging.DEBUG
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 전역 서비스 인스턴스 (지연 로딩)
uploader_service = None
translator_service = None
service_lock = threading.Lock()

# 업로드 상태 추적
upload_status = {}
upload_lock = threading.Lock()

def get_service_instances():
    """스레드 안전한 서비스 인스턴스 획득"""
    global uploader_service, translator_service
    
    with service_lock:
        if uploader_service is None:
            try:
                logger.info("🔧 서비스 초기화 시작")
                uploader_service = VideoUploaderLogic()
                translator_service = GoogleTranslator()
                logger.info("✅ 서비스 초기화 완료")
            except Exception as e:
                logger.error(f"❌ 서비스 초기화 실패: {e}")
                raise
    
    return uploader_service, translator_service

def cleanup_memory():
    """Railway 메모리 정리"""
    try:
        gc.collect()
        # 오래된 업로드 상태 정리 (1시간 이상)
        with upload_lock:
            current_time = time.time()
            expired_keys = [
                key for key, value in upload_status.items()
                if current_time - value.get('timestamp', 0) > 3600
            ]
            for key in expired_keys:
                del upload_status[key]
        
        logger.debug(f"🧹 메모리 정리 완료, 활성 업로드: {len(upload_status)}")
    except Exception as e:
        logger.warning(f"메모리 정리 중 오류: {e}")

# Flask 2.2+ 호환 - before_request
@app.before_request
def before_request():
    """요청 전 처리"""
    # 주기적 메모리 정리
    if hasattr(app, '_last_cleanup'):
        if time.time() - app._last_cleanup > 300:  # 5분마다
            cleanup_memory()
            app._last_cleanup = time.time()
    else:
        app._last_cleanup = time.time()
    
    # API 요청에 대한 CORS 헤더
    if request.path.startswith('/api/'):
        session.permanent = True

@app.after_request
def after_request(response):
    """응답 후 처리"""
    # API 응답에 대한 헤더 설정
    if request.path.startswith('/api/'):
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
    return response

# 헬스체크 엔드포인트 (Railway 모니터링용)
@app.route('/health')
def health_check():
    """Railway 헬스체크"""
    try:
        # 서비스 상태 확인
        uploader, translator = get_service_instances()
        
        status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'environment': 'railway' if os.environ.get('RAILWAY_ENVIRONMENT') else 'local',
            'services': {
                'uploader': bool(uploader),
                'translator': bool(translator),
                'firebase': bool(uploader and uploader.db),
                'wasabi': bool(uploader and uploader.s3_client)
            },
            'active_uploads': len(upload_status),
            'memory_usage': 'optimized'
        }
        
        return jsonify(status), 200
        
    except Exception as e:
        logger.error(f"헬스체크 실패: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/')
def index():
    """메인 페이지"""
    try:
        return render_template('upload_form.html',
                             mains=CATEGORY_STRUCTURE['main_categories'],
                             subs=CATEGORY_STRUCTURE['sub_categories'],
                             leafs=CATEGORY_STRUCTURE['leaf_categories'])
    except Exception as e:
        logger.error(f"메인 페이지 로드 실패: {e}")
        flash('페이지 로드 중 오류가 발생했습니다.', 'error')
        return render_template('error.html', 
                             error_code=500, 
                             error_message="페이지 로드 실패"), 500

@app.route('/upload', methods=['POST'])
def upload_video():
    """메인 동영상 업로드 처리"""
    upload_id = None
    
    try:
        # 서비스 인스턴스 획득
        uploader, translator = get_service_instances()
        
        # 업로드 ID 생성 및 상태 추적 시작
        upload_id = f"upload_{int(time.time())}_{os.getpid()}"
        with upload_lock:
            upload_status[upload_id] = {
                'status': 'started',
                'progress': 0,
                'message': '업로드 시작',
                'timestamp': time.time()
            }

        # 폼 데이터 추출 및 검증
        form_data = extract_and_validate_form_data(request)
        if 'error' in form_data:
            flash(form_data['error'], 'error')
            return redirect(url_for('index'))

        # 파일 검증
        video_file = request.files.get('file')
        thumbnail_file = request.files.get('thumbnail')
        
        if not video_file or video_file.filename == '':
            flash('동영상 파일을 선택해주세요.', 'error')
            return redirect(url_for('index'))

        # 번역된 파일명 파싱
        translated_filenames = parse_translated_filenames(request.form.get('translated_filenames', '{}'))

        # Railway 임시 디렉토리 사용
        with tempfile.TemporaryDirectory() as temp_dir:
            # 파일 저장 및 검증
            video_path, thumbnail_path = save_and_validate_files(
                video_file, thumbnail_file, temp_dir, uploader
            )
            
            if not video_path:
                flash('유효하지 않은 파일입니다.', 'error')
                return redirect(url_for('index'))

            # 진행률 콜백 함수
            def progress_callback(progress, message):
                with upload_lock:
                    if upload_id in upload_status:
                        upload_status[upload_id].update({
                            'progress': progress,
                            'message': message,
                            'timestamp': time.time()
                        })

            # 업로드 실행
            result = uploader.upload_video(
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                group_name=form_data['group_name'],
                main_category=form_data['main_category'],
                sub_category=form_data['sub_category'],
                leaf_category=form_data['sub_sub_category'],
                content_description=form_data['content_description'],
                translated_filenames=translated_filenames,
                progress_callback=progress_callback
            )

            # Railway 메모리 정리
            cleanup_memory()

            # 결과 처리
            if result['success']:
                # 성공 시 업로드 상태 업데이트
                with upload_lock:
                    if upload_id in upload_status:
                        upload_status[upload_id].update({
                            'status': 'completed',
                            'progress': 100,
                            'message': '업로드 완료',
                            'result': result
                        })

                return render_template('upload_success.html',
                                     result=result,
                                     group_name=form_data['group_name'],
                                     category_path=f"{form_data['main_category']} > {form_data['sub_category']} > {form_data['sub_sub_category']}")
            else:
                error_msg = result.get('error', '알 수 없는 오류')
                logger.error(f"업로드 실패: {error_msg}")
                flash(f'업로드 실패: {error_msg}', 'error')
                return redirect(url_for('index'))

    except RequestEntityTooLarge:
        flash('파일 크기가 5GB를 초과합니다.', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"업로드 처리 중 오류: {e}")
        flash(f'업로드 중 오류가 발생했습니다: {str(e)}', 'error')
        return redirect(url_for('index'))
    finally:
        # 업로드 상태 정리
        if upload_id:
            with upload_lock:
                if upload_id in upload_status and upload_status[upload_id].get('status') != 'completed':
                    upload_status[upload_id].update({
                        'status': 'failed',
                        'message': '업로드 실패'
                    })

def extract_and_validate_form_data(request):
    """폼 데이터 추출 및 검증"""
    required_fields = ['group_name', 'main_category', 'sub_category', 'sub_sub_category', 'content_description']
    form_data = {}
    
    for field in required_fields:
        value = request.form.get(field, '').strip()
        if not value:
            return {'error': f'{field}을(를) 입력해주세요.'}
        form_data[field] = value
    
    # 내용 길이 검증
    if len(form_data['content_description']) < 10:
        return {'error': '강의 내용은 10글자 이상 입력해주세요.'}
    
    return form_data

def parse_translated_filenames(json_string):
    """번역된 파일명 파싱"""
    try:
        return json.loads(json_string) if json_string else {}
    except json.JSONDecodeError:
        logger.warning("번역된 파일명 파싱 실패, 기본값 사용")
        return {}

def save_and_validate_files(video_file, thumbnail_file, temp_dir, uploader):
    """파일 저장 및 검증"""
    try:
        # 동영상 파일 저장
        video_filename = secure_filename(video_file.filename)
        video_path = os.path.join(temp_dir, video_filename)
        video_file.save(video_path)
        
        if not uploader.validate_file(video_path, 'video'):
            return None, None
        
        # 썸네일 파일 저장 (있는 경우)
        thumbnail_path = None
        if thumbnail_file and thumbnail_file.filename != '':
            thumbnail_filename = secure_filename(thumbnail_file.filename)
            thumbnail_path = os.path.join(temp_dir, thumbnail_filename)
            thumbnail_file.save(thumbnail_path)
            
            if not uploader.validate_file(thumbnail_path, 'image'):
                return video_path, None
        
        return video_path, thumbnail_path
        
    except Exception as e:
        logger.error(f"파일 저장 중 오류: {e}")
        return None, None

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """텍스트 번역 API (개선된 버전)"""
    try:
        uploader, translator = get_service_instances()
        
        # 입력 데이터 검증
        if not request.is_json:
            return jsonify({'success': False, 'error': 'JSON 형식의 데이터가 필요합니다'}), 400
        
        data = request.get_json()
        text = data.get('text', '').strip()
        
        if not text:
            return jsonify({'success': False, 'error': '번역할 텍스트가 없습니다'}), 400
        
        if len(text) > 200:  # Railway 메모리 최적화
            return jsonify({'success': False, 'error': '텍스트가 너무 깁니다 (200자 제한)'}), 400

        target_languages = data.get('target_languages', ['en', 'zh', 'vi', 'th', 'ja'])
        
        # 번역 실행
        start_time = time.time()
        translations = translator.translate_title(text)
        translation_time = time.time() - start_time
        
        # 요청된 언어만 필터링
        filtered_translations = {
            lang: translations.get(lang, text) 
            for lang in target_languages 
            if lang in translations
        }
        
        # 성공 응답
        response_data = {
            'success': True,
            'translations': filtered_translations,
            'original_text': text,
            'translation_time': round(translation_time, 2),
            'method': 'google_api' if translator.api_key else 'fallback'
        }
        
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"번역 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'번역 중 오류: {str(e)}',
            'translations': {},
            'method': 'error'
        }), 500

@app.route('/api/admin/videos', methods=['GET'])
def get_existing_videos():
    """기존 영상 목록 API (개선된 버전)"""
    try:
        uploader, translator = get_service_instances()
        
        # 페이지네이션 파라미터
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 20)), 50)  # Railway 메모리 제한
        
        # 검색 파라미터
        search_query = request.args.get('search', '').strip()
        category_filter = request.args.get('category', '').strip()
        
        # 영상 데이터 가져오기
        videos_data = uploader.get_existing_videos()
        
        # 필터링
        if search_query:
            videos_data = [
                video for video in videos_data
                if search_query.lower() in video['title'].lower()
            ]
        
        if category_filter:
            videos_data = [
                video for video in videos_data
                if category_filter in video.get('category', '')
            ]
        
        # 페이지네이션
        total_videos = len(videos_data)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_videos = videos_data[start_idx:end_idx]
        
        # Railway 메모리 최적화를 위한 데이터 정리
        simplified_videos = []
        for video in paginated_videos:
            simplified_video = {
                'group_id': video['id'],
                'title': video['title'],
                'main_category': video.get('main_category', ''),
                'sub_category': video.get('sub_category', ''),
                'sub_sub_category': video.get('sub_sub_category', ''),
                'upload_date': video['upload_date'],
                'languages': video['languages'],
                'language_count': len(video['languages']),
                'status': 'complete' if len(video['languages']) == 6 else 'partial' if len(video['languages']) > 1 else 'initial'
            }
            simplified_videos.append(simplified_video)

        # 성공 응답
        response_data = {
            'success': True,
            'videos': simplified_videos,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_videos,
                'total_pages': (total_videos + per_page - 1) // per_page,
                'has_next': end_idx < total_videos,
                'has_prev': page > 1
            },
            'filters': {
                'search': search_query,
                'category': category_filter
            }
        }
        
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"영상 목록 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'영상 목록 로드 실패: {str(e)}',
            'videos': [],
            'pagination': {'page': 1, 'per_page': 20, 'total': 0, 'total_pages': 0}
        }), 500

@app.route('/api/admin/upload_language_video', methods=['POST'])
def upload_language_video():
    """언어별 영상 업로드 API (개선된 버전)"""
    upload_id = None
    
    try:
        uploader, translator = get_service_instances()
        
        # 업로드 ID 생성
        upload_id = f"lang_upload_{int(time.time())}_{os.getpid()}"
        with upload_lock:
            upload_status[upload_id] = {
                'status': 'started',
                'progress': 0,
                'message': '언어별 영상 업로드 시작',
                'timestamp': time.time()
            }

        # 폼 데이터 추출
        group_id = request.form.get('group_id', '').strip()
        language_code = request.form.get('language_code', '').strip()

        if not all([group_id, language_code]):
            return jsonify({
                'success': False,
                'error': '그룹 ID와 언어 코드가 필요합니다'
            }), 400

        # 언어 코드 검증
        valid_languages = ['en', 'zh', 'vi', 'th', 'ja']
        if language_code not in valid_languages:
            return jsonify({
                'success': False,
                'error': f'지원하지 않는 언어 코드입니다: {language_code}'
            }), 400

        # 파일 검증
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'error': '동영상 파일을 선택해주세요'
            }), 400

        video_file = request.files['file']
        if video_file.filename == '':
            return jsonify({
                'success': False,
                'error': '동영상 파일을 선택해주세요'
            }), 400

        # Railway 임시 디렉토리 사용
        with tempfile.TemporaryDirectory() as temp_dir:
            # 파일 저장
            video_filename = secure_filename(video_file.filename)
            video_path = os.path.join(temp_dir, video_filename)
            video_file.save(video_path)

            # 파일 검증
            if not uploader.validate_file(video_path, 'video'):
                return jsonify({
                    'success': False,
                    'error': '유효하지 않은 동영상 파일입니다'
                }), 400

            # 진행률 콜백
            def progress_callback(progress, message):
                with upload_lock:
                    if upload_id in upload_status:
                        upload_status[upload_id].update({
                            'progress': progress,
                            'message': message,
                            'timestamp': time.time()
                        })

            # 언어별 영상 업로드 실행
            result = uploader.upload_language_video(
                video_id=group_id,
                language_code=language_code,
                video_path=video_path,
                progress_callback=progress_callback
            )

            # Railway 메모리 정리
            cleanup_memory()

            if result['success']:
                # 성공 응답
                language_names = {
                    'en': 'English', 'zh': '中文', 'vi': 'Tiếng Việt', 
                    'th': 'ไทย', 'ja': '日本語'
                }
                
                response_data = {
                    'success': True,
                    'message': f'{language_names.get(language_code, language_code)} 언어 영상이 성공적으로 업로드되었습니다',
                    'video_url': result['video_url'],
                    'language_code': language_code,
                    'language_name': language_names.get(language_code, language_code),
                    'metadata': result.get('metadata', {})
                }
                
                # 성공 상태 업데이트
                with upload_lock:
                    if upload_id in upload_status:
                        upload_status[upload_id].update({
                            'status': 'completed',
                            'progress': 100,
                            'message': '업로드 완료',
                            'result': response_data
                        })
                
                return jsonify(response_data)
            else:
                error_msg = result.get('error', '알 수 없는 오류')
                return jsonify({
                    'success': False,
                    'error': error_msg
                }), 500

    except RequestEntityTooLarge:
        return jsonify({
            'success': False,
            'error': '파일 크기가 5GB를 초과합니다'
        }), 413
    except Exception as e:
        logger.error(f"언어별 영상 업로드 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'업로드 중 오류: {str(e)}'
        }), 500
    finally:
        # 업로드 상태 정리
        if upload_id:
            with upload_lock:
                if upload_id in upload_status and upload_status[upload_id].get('status') != 'completed':
                    upload_status[upload_id].update({
                        'status': 'failed',
                        'message': '업로드 실패'
                    })

@app.route('/api/upload_status/<upload_id>')
def get_upload_status(upload_id):
    """업로드 상태 조회 API"""
    try:
        with upload_lock:
            status = upload_status.get(upload_id)
        
        if not status:
            return jsonify({
                'success': False,
                'error': '업로드 상태를 찾을 수 없습니다'
            }), 404
        
        return jsonify({
            'success': True,
            'upload_id': upload_id,
            'status': status['status'],
            'progress': status['progress'],
            'message': status['message'],
            'timestamp': status['timestamp']
        })
        
    except Exception as e:
        logger.error(f"업로드 상태 조회 오류: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/watch/<video_id>')
def watch_video(video_id):
    """동영상 시청 페이지"""
    try:
        uploader, translator = get_service_instances()
        
        # 영상 정보 가져오기
        video_info = uploader.get_upload_status(video_id)
        
        if not video_info['success']:
            return render_template('error.html',
                                 error_code=404,
                                 error_message="영상을 찾을 수 없습니다"), 404
        
        return render_template('watch_video.html', video_info=video_info)
        
    except Exception as e:
        logger.error(f"시청 페이지 오류: {e}")
        return render_template('error.html',
                             error_code=500,
                             error_message="페이지 로드 실패"), 500

# 에러 핸들러들
@app.errorhandler(413)
def file_too_large(error):
    """파일 크기 초과 오류 처리"""
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': '파일 크기가 5GB를 초과합니다'
        }), 413
    else:
        flash('파일 크기가 5GB를 초과합니다.', 'error')
        return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(error):
    """404 오류 처리"""
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': 'API 엔드포인트를 찾을 수 없습니다'
        }), 404
    else:
        return render_template('error.html',
                             error_code=404,
                             error_message="페이지를 찾을 수 없습니다"), 404

@app.errorhandler(500)
def internal_server_error(error):
    """500 오류 처리"""
    logger.error(f"서버 내부 오류: {error}")
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': '서버 내부 오류가 발생했습니다'
        }), 500
    else:
        return render_template('error.html',
                             error_code=500,
                             error_message="서버 내부 오류가 발생했습니다"), 500

@app.errorhandler(Exception)
def handle_exception(error):
    """일반 예외 처리"""
    logger.error(f"처리되지 않은 예외: {error}")
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': '예상치 못한 오류가 발생했습니다'
        }), 500
    else:
        flash('예상치 못한 오류가 발생했습니다.', 'error')
        return redirect(url_for('index'))

# Railway 배포용 메인 실행
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # Railway 포트
    debug_mode = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    # Railway 환경 감지
    is_railway = bool(os.environ.get('RAILWAY_ENVIRONMENT'))
    
    logger.info(f"🚀 Flask 서버 시작")
    logger.info(f"📍 포트: {port}")
    logger.info(f"🌍 환경: {'Railway 배포' if is_railway else '로컬 개발'}")
    logger.info(f"🔧 디버그 모드: {debug_mode}")
    
    # 서비스 초기화 (선택적)
    try:
        get_service_instances()
        logger.info("✅ 초기 서비스 확인 완료")
    except Exception as e:
        logger.warning(f"⚠️ 초기 서비스 확인 실패 (지연 로딩으로 진행): {e}")
    
    # 서버 실행
    app.run(
        host='0.0.0.0',  # Railway 요구사항
        port=port,
        debug=debug_mode,
        threaded=True,  # Railway 성능 최적화
        use_reloader=False if is_railway else debug_mode  # Railway에서는 리로더 비활성화
    )