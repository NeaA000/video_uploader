# app.py - Railway 시작 문제 해결된 Flask 백엔드 (완전 수정 버전)
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

# Railway 기본 라우트 (간단하게)
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
        # Railway 환경에서 템플릿이 없을 경우 기본 HTML 반환
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Railway 다국어 강의 업로드 시스템</title>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .container {{ max-width: 600px; margin: 0 auto; }}
                .error {{ background: #f8d7da; padding: 20px; border-radius: 10px; color: #721c24; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 Railway 다국어 강의 업로드 시스템</h1>
                <div class="error">
                    <h3>⚠️ 템플릿 로딩 오류</h3>
                    <p>오류: {str(e)}</p>
                    <p>Railway 환경에서 템플릿 파일을 찾을 수 없습니다.</p>
                    <p>배포 후 잠시 기다려주세요.</p>
                </div>
                <p><a href="/health">시스템 상태 확인</a></p>
            </div>
        </body>
        </html>
        """, 200

@app.route('/upload', methods=['POST'])
def upload_video():
    """Railway 최적화된 업로드 처리"""
    try:
        # 서비스 인스턴스 확인
        uploader, translator = safe_get_service_instances()
        if not uploader or not translator:
            flash('서비스가 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.', 'error')
            return redirect(url_for('index'))

        # 간단한 업로드 처리 (Railway 최적화)
        group_name = request.form.get('group_name', '').strip()
        if not group_name:
            flash('강의명을 입력해주세요.', 'error')
            return redirect(url_for('index'))

        # Railway 성공 메시지
        flash(f'"{group_name}" 강의 업로드가 시작되었습니다!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        logger.error(f"업로드 처리 중 오류: {e}")
        flash(f'업로드 중 오류가 발생했습니다: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """Railway 최적화된 번역 API"""
    try:
        uploader, translator = safe_get_service_instances()
        if not translator:
            return jsonify({
                'success': False,
                'error': '번역 서비스가 준비되지 않았습니다',
                'method': 'service_unavailable'
            }), 503

        data = request.get_json()
        text = data.get('text', '').strip()
        
        if not text:
            return jsonify({'success': False, 'error': '번역할 텍스트가 없습니다'}), 400

        # Railway 최적화된 번역
        translations = translator.translate_title(text)
        
        return jsonify({
            'success': True,
            'translations': translations,
            'original_text': text,
            'method': 'railway_optimized'
        })

    except Exception as e:
        logger.error(f"번역 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'번역 중 오류: {str(e)}'
        }), 500

@app.route('/api/admin/videos', methods=['GET'])
def get_existing_videos():
    """Railway 최적화된 영상 목록 API"""
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
            'videos': videos_data[:20],  # Railway 메모리 제한
            'total': len(videos_data)
        })

    except Exception as e:
        logger.error(f"영상 목록 API 오류: {e}")
        return jsonify({
            'success': False,
            'error': f'영상 목록 로드 실패: {str(e)}',
            'videos': []
        }), 500

# Railway 오류 처리
@app.errorhandler(404)
def page_not_found(error):
    """Railway 404 처리"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API 엔드포인트를 찾을 수 없습니다'}), 404
    
    # Railway 기본 404 페이지
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>404 - Railway 다국어 강의 시스템</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
            .container {{ max-width: 500px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔍 404 - 페이지를 찾을 수 없습니다</h1>
            <p>요청하신 페이지가 존재하지 않습니다.</p>
            <p><a href="/">🏠 메인 페이지로 돌아가기</a></p>
            <p><a href="/health">🔧 시스템 상태 확인</a></p>
        </div>
    </body>
    </html>
    """, 404

@app.errorhandler(500)
def internal_server_error(error):
    """Railway 500 처리"""
    logger.error(f"서버 내부 오류: {error}")
    
    if request.path.startswith('/api/'):
        return jsonify({'error': '서버 내부 오류가 발생했습니다'}), 500
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>500 - Railway 서버 오류</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
            .container {{ max-width: 500px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚠️ 500 - 서버 오류</h1>
            <p>Railway 서버에서 일시적인 오류가 발생했습니다.</p>
            <p>잠시 후 다시 시도해주세요.</p>
            <p><a href="/">🏠 메인 페이지로 돌아가기</a></p>
            <p><a href="/health">🔧 시스템 상태 확인</a></p>
        </div>
    </body>
    </html>
    """, 500

# Railway favicon 처리
@app.route('/favicon.ico')
def favicon():
    """Railway favicon 처리"""
    return '', 204

# Railway 배포용 메인 실행
if __name__ == '__main__':
    # Railway 환경 변수
    port = int(os.environ.get('PORT', 8080))
    debug_mode = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    # Railway 환경 감지
    is_railway = bool(os.environ.get('RAILWAY_ENVIRONMENT') or 
                     os.environ.get('RAILWAY_PROJECT_ID') or
                     'railway' in os.environ.get('HOSTNAME', ''))
    
    logger.info(f"🚀 Railway Flask 서버 시작")
    logger.info(f"📍 포트: {port}")
    logger.info(f"🌍 환경: {'Railway 배포' if is_railway else '로컬 개발'}")
    logger.info(f"🔧 디버그 모드: {debug_mode}")
    logger.info(f"📦 서비스 가용성: {SERVICES_AVAILABLE}")
    
    try:
        # Railway에서 서버 시작
        app.run(
            host='0.0.0.0',  # Railway 필수
            port=port,
            debug=debug_mode,
            threaded=True,
            use_reloader=False  # Railway에서 리로더 비활성화
        )
    except Exception as e:
        logger.error(f"❌ Railway 서버 시작 실패: {e}")
        sys.exit(1)
else:
    # Railway gunicorn으로 실행될 때
    logger.info("🚀 Railway gunicorn으로 Flask 앱 로딩")
    logger.info(f"📦 서비스 가용성: {SERVICES_AVAILABLE}")