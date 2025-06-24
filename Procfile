# Procfile (Railway 실행 명령어 설정)
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app

# railway.json (Railway 배포 설정)
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --access-logfile - --error-logfile - app:app",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 10,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}