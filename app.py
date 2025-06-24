# app.py - Flask 백엔드 서버 (Railway 최적화)
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import os
import tempfile
import json
import gc
from pathlib import Path
from werkzeug.utils import secure_filename
import logging
from video_uploader_logic import VideoUploaderLogic, GoogleTranslator, CATEGORY_STRUCTURE

# Flask 앱 초기화 (Railway 최적화)
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Railway 최적화 설정
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024 * 1024,  # 5GB
    UPLOAD_FOLDER=tempfile.gettempdir(),
    JSON_SORT_KEYS=False,
    JSONIFY_PRETTYPRINT_REGULAR=False  # Railway 메모리 절약
)

# 로깅 설정 (Railway 최적화)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 전역 서비스 인스턴스
uploader_service = None
translator_service = None

def initialize_services():
    """서비스 초기화 (Railway 최적화)"""
    global uploader_service, translator_service
    try:
        if not uploader_service:
            uploader_service = VideoUploaderLogic()
        if not translator_service:
            translator_service = GoogleTranslator()
        logger.info("서비스 초기화 완료")
        return True
    except Exception as e:
        logger.error(f"서비스 초기화 실패: {e}")
        return False

@app.before_first_request
def startup():
    """앱 시작 시 실행 (Railway 최적화)"""
    logger.info("🚀 Flask 백엔드 서버 시작 - Railway 배포")
    if not initialize_services():
        logger.error("❌ 서비스 초기화 실패")

@app.route('/')
def index():
    """메인 페이지 (업로드 폼)"""
    try:
        return render_template('upload_form.html',
                             mains=CATEGORY_STRUCTURE['main_categories'],
                             subs=CATEGORY_STRUCTURE['sub_categories'],
                             leafs=CATEGORY_STRUCTURE['leaf_categories'])
    except Exception as e:
        logger.error(f"메인 페이지 로드 실패: {e}")
        return f"페이지 로드 실패: {str(e)}", 500

@app.route('/upload', methods=['POST'])
def upload_video():
    """메인 동영상 업로드 처리 (Railway 최적화)"""
    try:
        if not uploader_service:
            if not initialize_services():
                flash('서비스 초기화 실패', 'error')
                return redirect(url_for('index'))

        # 폼 데이터 추출
        group_name = request.form.get('group_name', '').strip()
        main_category = request.form.get('main_category', '').strip()
        sub_category = request.form.get('sub_category', '').strip()
        sub_sub_category = request.form.get('sub_sub_category', '').strip()
        content_description = request.form.get('content_description', '').strip()
        translated_filenames_json = request.form.get('translated_filenames', '{}')

        # 입력값 검증
        if not all([group_name, main_category, sub_category, sub_sub_category, content_description]):
            flash('모든 필수 필드를 입력해주세요.', 'error')
            return redirect(url_for('index'))

        if len(content_description) < 10:
            flash('강의 내용은 10글자 이상 입력해주세요.', 'error')
            return redirect(url_for('index'))

        # 파일 검증
        if 'file' not in request.files:
            flash('동영상 파일을 선택해주세요.', 'error')
            return redirect(url_for('index'))

        video_file = request.files['file']
        if video_file.filename == '':
            flash('동영상 파일을 선택해주세요.', 'error')
            return redirect(url_for('index'))

        thumbnail_file = request.files.get('thumbnail')

        # 번역된 파일명 파싱
        try:
            translated_filenames = json.loads(translated_filenames_json) if translated_filenames_json else {}
        except json.JSONDecodeError:
            logger.warning("번역된 파일명 파싱 실패, 기본값 사용")
            translated_filenames = {}

        # Railway 임시 디렉토리 사용
        with tempfile.TemporaryDirectory() as temp_dir:
            # 동영상 파일 저장
            video_filename = secure_filename(video_file.filename)
            video_path = os.path.join(temp_dir, video_filename)
            video_file.save(video_path)

            # 썸네일 파일 저장 (있는 경우)
            thumbnail_path = None
            if thumbnail_file and thumbnail_file.filename != '':
                thumbnail_filename = secure_filename(thumbnail_file.filename)
                thumbnail_path = os.path.join(temp_dir, thumbnail_filename)
                thumbnail_file.save(thumbnail_path)

            # 파일 검증
            if not uploader_service.validate_file(video_path, 'video'):
                flash('유효하지 않은 동영상 파일입니다.', 'error')
                return redirect(url_for('index'))

            if thumbnail_path and not uploader_service.validate_file(thumbnail_path, 'image'):
                flash('유효하지 않은 썸네일 파일입니다.', 'error')
                return redirect(url_for('index'))

            # 업로드 실행
            result = uploader_service.upload_video(
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                group_name=group_name,
                main_category=main_category,
                sub_category=sub_category,
                leaf_category=sub_sub_category,
                content_description=content_description,
                translated_filenames=translated_filenames,
                progress_callback=None  # 웹에서는 실시간 진행률 미사용
            )

            # Railway 메모리 정리
            gc.collect()

            if result['success']:
                return render_template('upload_success.html',
                                     result=result,
                                     group_name=group_name,
                                     category_path=f"{main_category} > {sub_category} > {sub_sub_category}")
            else:
                flash(f'업로드 실패: {result.get("error", "알 수 없는 오류")}', 'error')
                return redirect(url_for('index'))

    except Exception as e:
        logger.error(f"업로드 처리 중 오류: {e}")
        flash(f'업로드 중 오류가 발생했습니다: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """텍스트 번역 API (Railway 최적화)"""
    try:
        if not translator_service:
            if not initialize_services():
                return jsonify({'error': '번역 서비스 초기화 실패'}), 500

        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({'error': '번역할 텍스트가 없습니다'}), 400

        text = data['text']
        target_languages = data.get('target_languages', ['en', 'zh', 'vi', 'th', 'ja'])

        # 번역 실행
        translations = translator_service.translate_title(text)

        # 요청된 언어만 필터링
        filtered_translations = {
            lang: translations.get(lang, text) 
            for lang in target_languages 
            if lang in translations
        }

        return jsonify(filtered_translations)

    except Exception as e:
        logger.error(f"번역 API 오류: {e}")
        return jsonify({'error': f'번역 중 오류: {str(e)}'}), 500

@app.route('/api/admin/videos', methods=['GET'])
def get_existing_videos():
    """기존 영상 목록 API (Railway 최적화)"""
    try:
        if not uploader_service:
            if not initialize_services():
                return jsonify({'error': '서비스 초기화 실패'}), 500

        videos_data = uploader_service.get_existing_videos()
        
        # Railway 메모리 최적화를 위한 데이터 정리
        simplified_videos = []
        for video in videos_data[:50]:  # 최대 50개로 제한
            simplified_video = {
                'group_id': video['id'],
                'title': video['title'],
                'main_category': video.get('data', {}).get('main_category', ''),
                'sub_category': video.get('data', {}).get('sub_category', ''),
                'sub_sub_category': video.get('data', {}).get('sub_sub_category', ''),
                'upload_date': video['upload_date'],
                'languages': {}
            }
            
            # 언어별 영상 정보
            for lang_code in ['ko', 'en', 'zh', 'vi', 'th', 'ja']:
                simplified_video['languages'][lang_code] = lang_code in video['languages']
            
            simplified_videos.append(simplified_video)

        return jsonify({
            'success': True,
            'videos': simplified_videos,
            'total': len(simplified_videos)
        })

    except Exception as e:
        logger.error(f"영상 목록 API 오류: {e}")
        return jsonify({'error': f'영상 목록 로드 실패: {str(e)}'}), 500

@app.route('/api/admin/upload_language_video', methods=['POST'])
def upload_language_video():
    """언어별 영상 업로드 API (Railway 최적화)"""
    try:
        if not uploader_service:
            if not initialize_services():
                return jsonify({'error': '서비스 초기화 실패'}), 500

        # 폼 데이터 추출
        group_id = request.form.get('group_id', '').strip()
        language_code = request.form.get('language_code', '').strip()

        if not all([group_id, language_code]):
            return jsonify({'error': '그룹 ID와 언어 코드가 필요합니다'}), 400

        # 파일 검증
        if 'file' not in request.files:
            return jsonify({'error': '동영상 파일을 선택해주세요'}), 400

        video_file = request.files['file']
        if video_file.filename == '':
            return jsonify({'error': '동영상 파일을 선택해주세요'}), 400

        # Railway 임시 디렉토리 사용
        with tempfile.TemporaryDirectory() as temp_dir:
            # 파일 저장
            video_filename = secure_filename(video_file.filename)
            video_path = os.path.join(temp_dir, video_filename)
            video_file.save(video_path)

            # 파일 검증
            if not uploader_service.validate_file(video_path, 'video'):
                return jsonify({'error': '유효하지 않은 동영상 파일입니다'}), 400

            # 언어별 영상 업로드 실행
            result = uploader_service.upload_language_video(
                video_id=group_id,
                language_code=language_code,
                video_path=video_path,
                progress_callback=None  # 웹에서는 실시간 진행률 미사용
            )

            # Railway 메모리 정리
            gc.collect()

            if result['success']:
                return jsonify({
                    'success': True,
                    'message': f'{language_code} 언어 영상이 성공적으로 업로드되었습니다',
                    'video_url': result['video_url'],
                    'language_code': language_code
                })
            else:
                return jsonify({'error': result.get('error', '알 수 없는 오류')}), 500

    except Exception as e:
        logger.error(f"언어별 영상 업로드 API 오류: {e}")
        return jsonify({'error': f'업로드 중 오류: {str(e)}'}), 500

@app.route('/watch/<video_id>')
def watch_video(video_id):
    """동영상 시청 페이지"""
    try:
        # 기본적인 시청 페이지 (추후 구현)
        return f"""
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <title>강의 시청 - {video_id}</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; text-align: center; }}
                .container {{ max-width: 800px; margin: 0 auto; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎬 강의 시청</h1>
                <p>영상 ID: <strong>{video_id}</strong></p>
                <p>시청 페이지는 개발 중입니다.</p>
                <a href="/">⬅️ 메인 페이지로 돌아가기</a>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"시청 페이지 오류: {e}")
        return f"페이지 로드 실패: {str(e)}", 500

@app.errorhandler(413)
def file_too_large(error):
    """파일 크기 초과 오류 처리"""
    flash('파일 크기가 5GB를 초과합니다.', 'error')
    return redirect(url_for('index'))

@app.errorhandler(404)
def page_not_found(error):
    """404 오류 처리"""
    return render_template('error.html', 
                         error_code=404, 
                         error_message="페이지를 찾을 수 없습니다."), 404

@app.errorhandler(500)
def internal_server_error(error):
    """500 오류 처리"""
    logger.error(f"서버 내부 오류: {error}")
    return render_template('error.html', 
                         error_code=500, 
                         error_message="서버 내부 오류가 발생했습니다."), 500

# Railway 배포용 메인 실행
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))  # Railway 포트
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    
    logger.info(f"🚀 Flask 서버 시작 - 포트: {port}")
    
    app.run(
        host='0.0.0.0',  # Railway 요구사항
        port=port,
        debug=debug_mode,
        threaded=True  # Railway 성능 최적화
    )