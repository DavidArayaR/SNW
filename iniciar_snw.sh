#!/usr/bin/env bash
# SNW - Sistema de Notificaciones WhatsApp
# Equivalente Linux/macOS de INICIAR_SNW.bat
set -u

# Ir al directorio del script (funciona aunque se llame desde otra ruta)
cd "$(cd "$(dirname "$0")" && pwd)" || exit 1

echo
echo " ============================================"
echo "  SNW - Sistema de Notificaciones WhatsApp"
echo " ============================================"
echo

# --- Python -------------------------------------------------------------
PYTHON=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PYTHON="$c"; break; fi
done
if [ -z "$PYTHON" ]; then
  echo "ERROR: Python no encontrado. Instala Python 3.11+ y vuelve a intentarlo."
  exit 1
fi

# --- Cliente MySQL (XAMPP/LAMPP o del sistema) -------------------------
MYSQL="mysql"
for c in /opt/lampp/bin/mysql /usr/local/mysql/bin/mysql /usr/bin/mysql; do
  if [ -x "$c" ]; then MYSQL="$c"; break; fi
done

# --- 1/4 MySQL escuchando en 3306 ------------------------------------
echo " [1/4] Verificando MySQL en 127.0.0.1:3306 ..."
if ! "$PYTHON" -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',3306));s.close()" >/dev/null 2>&1; then
  echo " [!!] MySQL no responde. Inicia MySQL (XAMPP/LAMPP) y vuelve a ejecutar."
else
  echo " [OK] MySQL activo."

  # --- 2/4 Inicializar la base SOLO la primera vez -----------------
  echo " [2/4] Verificando base de datos..."
  if "$MYSQL" -u root -h 127.0.0.1 -P 3306 -e "SELECT 1 FROM snw_base.pacientes_prod LIMIT 1;" >/dev/null 2>&1; then
    echo " [OK] Base ya inicializada, se omite snw_base.sql."
  else
    echo "      Primera ejecucion: cargando snw_base.sql..."
    if "$MYSQL" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < sql/snw_base.sql >/dev/null 2>&1; then
      echo " [OK] snw_base.sql cargado (10 tablas + cuentas admin/usuario/dev + 2 numeros autorizados)."
    else
      echo " [!!] Error al cargar snw_base.sql. Revisa que MySQL este activo."
    fi
  fi
fi

# --- 3/4 Dependencias de Python -------------------------------------
echo " [3/4] Verificando dependencias de Python ..."
if "$PYTHON" -c "import fastapi, uvicorn, pymysql, dotenv" >/dev/null 2>&1; then
  echo " [OK] Dependencias listas."
else
  echo "      Instalando dependencias..."
  "$PYTHON" -m pip install -r requirements.txt
fi

# --- 4/4 Servidor -------------------------------------------------------
echo " [4/4] Iniciando servidor en http://127.0.0.1:8000 ..."
(
  sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1
  elif command -v open >/dev/null 2>&1; then open "http://127.0.0.1:8000" >/dev/null 2>&1
  fi
) &

exec "$PYTHON" -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
