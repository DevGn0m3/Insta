@echo off
setlocal
cd /d "%~dp0"
title Instagram Archiver

echo.
echo  ===================================================
echo    INSTAGRAM ARCHIVER - Iniciando...
echo  ===================================================
echo.
echo  [INFO] Directorio del proyecto: %CD%

if not exist "backend\main.py" goto project_error
if not exist "frontend\index.html" goto project_error
if not exist "frontend\assets\js\app.js" goto project_error
if not exist "backend\services\downloader\instagram_client.py" goto project_error

findstr /C:"Usar sessionid" "frontend\index.html" >nul
if errorlevel 1 goto old_frontend_error
findstr /C:"igLoginMethod" "frontend\assets\js\app.js" >nul
if errorlevel 1 goto old_frontend_error
findstr /C:"login_instagram_session" "backend\api\routes\downloads.py" >nul
if errorlevel 1 goto old_backend_error
findstr /C:"login_with_sessionid" "backend\services\downloader\instagram_client.py" >nul
if errorlevel 1 goto old_backend_error

echo  [OK] Soporte sessionid detectado.

if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
) else (
    echo  [SETUP] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 goto venv_error
    set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
)

"%PYTHON_EXE%" -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo  [SETUP] Instalando dependencias...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 goto dependency_error
)

echo.
echo  [INFO] Iniciando servidor en http://127.0.0.1:8000
echo  [INFO] Presiona Ctrl+C para detener este servidor.
echo.

"%PYTHON_EXE%" -m backend.main
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo  [INFO] Servidor detenido. Codigo: %EXIT_CODE%
pause
exit /b %EXIT_CODE%

:project_error
echo [ERROR] Ejecuta este archivo desde la raiz del proyecto.
pause
exit /b 1

:old_frontend_error
echo [ERROR] El frontend no contiene la version sessionid.
pause
exit /b 1

:old_backend_error
echo [ERROR] El backend no contiene la version sessionid.
pause
exit /b 1

:venv_error
echo [ERROR] No se pudo crear el entorno virtual.
pause
exit /b 1

:dependency_error
echo [ERROR] No se pudieron instalar las dependencias.
pause
exit /b 1
