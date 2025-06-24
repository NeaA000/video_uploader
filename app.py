# app.py - 완전한 Flask 백엔드 (실제 업로드 처리)
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import os
import tempfile
import json
import gc
import time
import threading
import sys
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

# Railway 로깅 설정
log_level = logging.INFO
if os.environ.get('DEBUG') == 'true':
    log_level = logging.DEBUG

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]  # Railway stdout 로깅
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

def safe_get_service_instances():
    """Railway 안전한 서비스 인스턴스 획득"""
    global uploader_service, translator_service, service_init_attempted, SERVICES_AVAILABLE
    
    if not SERVICES_AVAILABLE and not service_init_attempted:
        # Railway에서 모듈 재로딩 시도
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
                logger.info("🔧 Railway 환경에서 서비스 초기화 시작")
                uploader_service = VideoUploaderLogic()
                translator_service = GoogleTranslator()
                logger.info("✅ Railway 서비스 초기화 완료")
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
        
        logger.debug(f"🧹 Railway 메모리 정리 완료, 활성 업로드: {len(upload_status)}")
    except Exception as e:
        logger.warning(f"메모리 정리 중 오류: {e}")

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
            'python_version': sys.version.split()[0],
            'flask_ready': True
        }
        
        return jsonify(health_status), 200
        
    except Exception as e:
        logger.error(f"헬스체크 실패: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# Railway 기본 라우트
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
    """완전한 비디오 업로드 처리"""
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
            
            # 실제 업로드 실행
            logger.info(f"업로드 시작: {group_name}")
            
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
                logger.info(f"업로드 성공: {group_name} (ID: {result['group_id']})")
                
                # 성공 페이지로 리다이렉트
                category_path = f"{main_category} > {sub_category} > {sub_sub_category}"
                
                return render_template('upload_success.html',
                                     result=result,
                                     group_name=group_name,
                                     category_path=category_path)
            else:
                logger.error(f"업로드 실패: {result.get('error', '알 수 없는 오류')}")
                flash(f'업로드 실패: {result.get("error", "알 수 없는 오류")}', 'error')
                return redirect(url_for('index'))

    except RequestEntityTooLarge:
        flash('파일 크기가 너무 큽니다. 5GB 이하의 파일을 업로드해주세요.', 'error')
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"업로드 처리 중 오류: {e}")
        flash(f'업로드 중 오류가 발생했습니다: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """번역 API"""
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

        # 번역 실행
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
    """기존 영상 목록 API"""
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
            'total': len(videos_data)
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
    """언어별 영상 업로드 API"""
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
            
            # 언어별 영상 업로드 실행
            result = uploader.upload_language_video(
                video_id=group_id,
                language_code=language_code,
                video_path=lang_video_path
            )
            
            if result['success']:
                logger.info(f"언어별 영상 업로드 성공: {group_id} ({language_code})")
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

@app.route('/watch/<video_id>')
def watch_video(video_id):

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

# Railway 배포용 메인 실행
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)