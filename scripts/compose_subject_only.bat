@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo.
echo [1] cutout.png 파일이 eternal-beam-app 폴더에 있어야 합니다.
echo     (없으면 1단계 cutout API로 먼저 생성)
echo.
if not exist "cutout.png" (
    echo cutout.png 없음. python\composited 폴더에서 PNG 추출하거나 cutout API 사용.
    if exist "python\composited\slot1_idle.mp4" (
        echo.
        echo slot1_idle.mp4 등 비디오는 있음. cutout PNG가 필요합니다.
        echo 웹앱에서 사진 업로드 후 AI 누끼 완료 -> cutout_url 저장된 PNG 사용.
    )
    pause
    exit /b 1
)

echo [2] 백엔드 실행 중인지 확인 (localhost:8000)
echo.
curl.exe -s -o nul -w "%%{http_code}" http://localhost:8000/health 2>nul | findstr "200" >nul
if errorlevel 1 (
    echo 백엔드가 실행되지 않았습니다.
    echo 다른 터미널에서: python -m uvicorn backend.main:app --port 8000
    pause
    exit /b 1
)

echo [3] subject_only 영상 생성 중...
curl.exe -X POST "http://localhost:8000/api/compose-video" -F "cutout_file=@cutout.png" -F "subject_only=true" -F "theme_id=subject_only" -o subject_only.mp4

if exist "subject_only.mp4" (
    echo.
    echo OK: subject_only.mp4 생성됨
    echo 위치: %cd%\subject_only.mp4
) else (
    echo 실패. cutout.png 확인 및 백엔드 로그 확인.
)
echo.
pause
