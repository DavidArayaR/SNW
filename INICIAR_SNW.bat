@echo off
setlocal enabledelayedexpansion
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
set "MYSQLDUMP=D:\xampp\mysql\bin\mysqldump.exe"
if not exist "%MYSQLDUMP%" set "MYSQLDUMP=mysqldump"
if not exist "%~dp0backups" mkdir "%~dp0backups"

REM Verificar MySQL (XAMPP) en el puerto 3306
echo  [1/4] Verificando MySQL en 127.0.0.1:3306 ...
python -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',3306));s.close()" >nul 2>nul
if errorlevel 1 goto SIN_MYSQL
echo  [OK] MySQL activo.

REM Inicializar la base: primera vez, flujo normal; si ya existe, se pregunta.
echo  [2/4] Verificando base de datos...
"%MYSQL%" -u root -h 127.0.0.1 -P 3306 -e "SELECT 1 FROM snw_base.pacientes_prod LIMIT 1;" >nul 2>nul
if errorlevel 1 goto PRIMERA_VEZ

echo.
echo  La base de datos ya fue inicializada antes. Que deseas hacer?
echo    [1] Backup e iniciar desde cero (borra y recrea snw_base)
echo    [2] Backup y seguir como esta (no se toca la base)
echo    [3] Seguir sin backup (no se respalda nada)
echo    [4] Elegir un backup e iniciar con ese backup (primero respalda la actual)
echo.
choice /C 1234 /N /T 60 /D 3 /M "Elige una opcion [1/2/3/4] (por defecto 3 en 60 s): "
if errorlevel 4 goto ELEGIR_BACKUP
if errorlevel 3 goto SEGUIR_SIN_BACKUP
if errorlevel 2 goto SOLO_BACKUP
if errorlevel 1 goto DESDE_CERO

:SEGUIR_SIN_BACKUP
echo  [!!] Se sigue SIN respaldo.
goto CHECKEAR_DEPS

:DESDE_CERO
call :HACER_BACKUP
if errorlevel 1 goto FIN
echo       Borrando y recreando snw_base...
"%MYSQL%" -u root -h 127.0.0.1 -P 3306 -e "DROP DATABASE IF EXISTS snw_base; CREATE DATABASE snw_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
echo       Cargando snw_base.sql...
"%MYSQL%" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < "%~dp0sql\snw_base.sql" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
echo  [OK] Base recreada desde cero.
goto CHECKEAR_DEPS

:SOLO_BACKUP
call :HACER_BACKUP
echo  [OK] Se sigue con la base actual (no se crea nada).
goto CHECKEAR_DEPS

:ELEGIR_BACKUP
echo  Primero se respalda la base actual.
call :HACER_BACKUP
if errorlevel 1 goto FIN
set "N=0"
for %%F in ("%~dp0backups\*.sql") do (
  set /a N+=1
  set "BK!N!=%%F"
  echo    [!N!] %%~nxF
)
if !N! EQU 0 (
  echo  [!!] No hay respaldos en backups\.
  goto CHECKEAR_DEPS
)
set /p "SEL=Elige el numero del backup: "
call set "ELEGIDO=%%BK%SEL%%%"
if not defined ELEGIDO (
  echo  [!!] Opcion invalida.
  goto CHECKEAR_DEPS
)
echo.
echo  Se BORRARA la base actual y se cargara "!ELEGIDO!".
choice /C SN /N /M "Seguro? [S/N]: "
if errorlevel 2 goto CHECKEAR_DEPS
echo       Borrando y cargando respaldo...
"%MYSQL%" -u root -h 127.0.0.1 -P 3306 -e "DROP DATABASE IF EXISTS snw_base; CREATE DATABASE snw_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
"%MYSQL%" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 snw_base < "!ELEGIDO!" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
echo  [OK] Respaldo cargado. Se inicia con esa base.
goto CHECKEAR_DEPS

:PRIMERA_VEZ
echo       Primera ejecucion: cargando snw_base.sql...
"%MYSQL%" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < "%~dp0sql\snw_base.sql" >nul 2>nul
if errorlevel 1 goto ERROR_SQL
echo  [OK] snw_base.sql cargado (10 tablas + cuentas admin/usuario/dev + 2 numeros autorizados).
goto CHECKEAR_DEPS

REM Subrutina: respalda snw_base completa en backups\. Pide un nombre (vacío =
REM por defecto); si se indica, se le agrega la fecha automáticamente.
REM Devuelve errorlevel 1 si falla.
:HACER_BACKUP
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "FECHA=%%I"
if not defined FECHA set "FECHA=manual"
set "FECHA=%FECHA:~0,8%_%FECHA:~8,6%"
set /p "NOMBRE=Nombre del backup (vacio = por defecto): "
if defined NOMBRE (
  REM Sanea: espacios a _ y fuera \ / : * ? " < > | !
  for /f "delims=" %%S in ('powershell -NoProfile -Command "$n=$env:NOMBRE; $n = ($n.ToCharArray() | Where-Object { [IO.Path]::GetInvalidFileNameChars() -notcontains $_ }) -join ''; $n = $n -replace '%%',''; $n = $n -replace ' ','_'; Write-Output $n" 2^>nul') do set "NOMBRE=%%S"
)
if not defined NOMBRE (
  set "RESPALDO=%~dp0backups\snw_base_%FECHA%.sql"
) else (
  set "RESPALDO=%~dp0backups\%NOMBRE%_%FECHA%.sql"
)
echo       Respaldando en %RESPALDO% ...
"%MYSQLDUMP%" -u root -h 127.0.0.1 -P 3306 --default-character-set=utf8mb4 --routines snw_base > "%RESPALDO%" 2>nul
if errorlevel 1 (
  echo  [!!] No se pudo crear el respaldo. Por seguridad no se sigue.
  del "%RESPALDO%" >nul 2>nul
  exit /b 1
)
echo  [OK] Respaldo creado.
exit /b 0

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
