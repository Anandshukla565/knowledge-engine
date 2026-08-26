@echo off
chcp 65001 >nul
echo ============================================
echo   Knowledge Engine - FastAPI Setup Script
echo ============================================
echo.

REM Step 1: Install dependencies
echo [1/3] Installing FastAPI + Uvicorn...
pip install fastapi "uvicorn[standard]"
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo [2/3] Verifying installation...
python -c "import fastapi; print('FastAPI:', fastapi.__version__)"
python -c "import uvicorn; print('Uvicorn: OK')"

echo.
echo [3/3] Testing server startup (press Ctrl+C to stop)...
echo.
echo ============================================
echo   Server will start at: http://127.0.0.1:8765
echo   Swagger UI:        http://127.0.0.1:8765/docs
echo   Press Ctrl+C to stop
echo ============================================
echo.

python -m knowledge_engine api
pause
