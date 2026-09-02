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

REM Detectar cliente MySQL de XAMPP
set "MYSQL=D:\xampp\mysql\bin\mysql.exe"
if not exist "%MYSQL%" set "MYSQL=mysql"

REM Verificar MySQL (XAMPP) en el puerto 3306
echo  [1/4] Verificando MySQL en 127.0.0.1:3306 ...
python -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',3306));s.close()" >nul 2>nul
if errorlevel 1 goto SIN_MYSQL
echo  [OK] MySQL activo.

REM Ejecutar scripts SQL de inicializacion
echo  [2/4] Ejecutando scripts SQL de base de datos...
"%MYSQL%" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < "%~dp0sql\snw_base.sql" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
echo  [OK] snw_base.sql cargado (5 tablas + datos de prueba).
goto CHECKEAR_DEPS

:SIN_MYSQL
echo  [!!] MySQL no responde. Te sugiero iniciar MySQL en XAMPP.
echo       No se ejecutaran los scripts SQL. La app fallara al consultar pacientes.
goto CHECKEAR_DEPS

:ERROR_SQL
echo  [!!] Error al ejecutar los scripts SQL. Intenta iniciar MySQL en XAMPP y vuelve a ejecutar.

:CHECKEAR_DEPS
echo  [3/4] Verificando dependencias de Python ...
python -c "import fastapi, uvicorn, pymysql, dotenv" >nul 2>nul
if errorlevel 1 goto INSTALAR_DEPS
echo  [OK] Dependencias listas.
goto INICIAR

:INSTALAR_DEPS
echo  Instalando dependencias...
python -m pip install -r "%~dp0requirements.txt"

:INICIAR
echo  [4/4] Iniciando servidor en http://127.0.0.1:8000 ...
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
