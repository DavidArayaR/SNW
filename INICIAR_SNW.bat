@echo off
title SNW - Sistema de Notificaciones WhatsApp
cd /d "%~dp0"

echo.
echo  ============================================
echo   SNW - Sistema de Notificaciones WhatsApp
echo  ============================================
echo.

REM Verificar que Python este instalado
where python >nul 2>nul
if errorlevel 1 goto SIN_PYTHON

REM Verificar MySQL (XAMPP) en el puerto 3306
echo  [1/3] Verificando MySQL en 127.0.0.1:3306 ...
python -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',3306));s.close()" >nul 2>nul
if errorlevel 1 goto SIN_MYSQL
echo  [OK] MySQL activo.
goto CHECKEAR_DEPS

:SIN_MYSQL
echo  [!!] MySQL no responde. Te sugiero iniciar MySQL en XAMPP.
echo       Puedes continuar igual, pero la app fallara al consultar pacientes.

:CHECKEAR_DEPS
echo  [2/3] Verificando dependencias de Python ...
python -c "import fastapi, uvicorn, pymysql, dotenv" >nul 2>nul
if errorlevel 1 goto INSTALAR_DEPS
echo  [OK] Dependencias listas.
goto INICIAR

:INSTALAR_DEPS
echo  Instalando dependencias...
python -m pip install -r "%~dp0requirements.txt"

:INICIAR
echo  [3/3] Iniciando servidor en http://127.0.0.1:8000 ...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000"

REM Levantar uvicorn en esta misma ventana
python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
goto FIN

:SIN_PYTHON
echo ERROR: Python no fue encontrado.
echo Instala Python 3.11+ y vuelve a intentarlo.

:FIN
pause
