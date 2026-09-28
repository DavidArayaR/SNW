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
# mysqldump junto al cliente si existe, si no el del PATH
MYSQLDUMP="$(dirname "$MYSQL")/mysqldump"
if [ ! -x "$MYSQLDUMP" ]; then MYSQLDUMP="mysqldump"; fi
mkdir -p backups

# --- Respaldo de snw_base ---------------------------------------------
# Pide un nombre (vacío = por defecto); si se indica, se le agrega la fecha
# automáticamente. Devuelve 0 si queda el .sql, 1 si falla.
hacer_backup() {
  local stamp nombre base
  stamp="$(date +%Y%m%d_%H%M%S)"
  nombre=""
  if [ -t 0 ]; then
    printf "Nombre del backup (vacío = por defecto): "
    read -r nombre
    nombre="$(printf '%s' "$nombre" | tr ' ' '_' | tr -cd 'A-Za-z0-9._-')"
  fi
  if [ -z "$nombre" ]; then
    base="snw_base_${stamp}"
  else
    base="${nombre}_${stamp}"
  fi
  local destino="backups/${base}.sql"
  echo "      Respaldando en $destino ..."
  if "$MYSQLDUMP" -u root -h 127.0.0.1 -P 3306 --default-character-set=utf8mb4 --routines snw_base > "$destino" 2>/dev/null; then
    echo " [OK] Respaldo creado."
    return 0
  fi
  echo " [!!] No se pudo crear el respaldo. Por seguridad no se sigue."
  rm -f "$destino"
  return 1
}

# --- 1/4 MySQL escuchando en 3306 ------------------------------------
echo " [1/4] Verificando MySQL en 127.0.0.1:3306 ..."
if ! "$PYTHON" -c "import socket;s=socket.socket();s.settimeout(2);s.connect(('127.0.0.1',3306));s.close()" >/dev/null 2>&1; then
  echo " [!!] MySQL no responde. Inicia MySQL (XAMPP/LAMPP) y vuelve a ejecutar."
else
  echo " [OK] MySQL activo."

  # --- 2/4 Base de datos: primera vez, flujo normal; si ya existe, menú
  echo " [2/4] Verificando base de datos..."
  if "$MYSQL" -u root -h 127.0.0.1 -P 3306 -e "SELECT 1 FROM snw_base.pacientes_prod LIMIT 1;" >/dev/null 2>&1; then
    echo
    echo " La base de datos ya fue inicializada antes. ¿Qué deseas hacer?"
    echo "  [1] Backup e iniciar desde cero (borra y recrea snw_base)"
    echo "  [2] Backup y seguir como está (no se toca la base)"
    echo "  [3] Seguir sin backup (no se respalda nada)"
    echo "  [4] Elegir un backup e iniciar con ese backup (primero respalda la actual)"
    echo
    opcion=""
    if [ -t 0 ]; then
      printf "Elige una opción [1/2/3/4] (por defecto 2): "
      read -r opcion
    fi
    case "$opcion" in
      1)
        if hacer_backup; then
          echo "      Borrando y recreando snw_base..."
          "$MYSQL" -u root -h 127.0.0.1 -P 3306 -e "DROP DATABASE IF EXISTS snw_base; CREATE DATABASE snw_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >/dev/null 2>&1 &&
          "$MYSQL" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < sql/snw_base.sql >/dev/null 2>&1 &&
          echo " [OK] Base recreada desde cero."
        fi
        ;;
      3)
        echo " [!!] Se sigue SIN respaldo."
        ;;
      4)
        echo " Primero se respalda la base actual."
        if hacer_backup; then
        archivos=(backups/*.sql)
        if [ ! -e "${archivos[0]}" ]; then
          echo " [!!] No hay respaldos en backups/."
        else
          echo " Respaldos disponibles:"
          select elegido in "${archivos[@]}"; do
            if [ -n "$elegido" ]; then
              printf "Se BORRARÁ la base actual y se cargará '%s'. ¿Seguro? [s/N]: " "$elegido"
              read -r conf
              if [ "$conf" = "s" ] || [ "$conf" = "S" ]; then
                echo "      Borrando y cargando respaldo..."
                "$MYSQL" -u root -h 127.0.0.1 -P 3306 -e "DROP DATABASE IF EXISTS snw_base; CREATE DATABASE snw_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" >/dev/null 2>&1 &&
                "$MYSQL" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 snw_base < "$elegido" >/dev/null 2>&1 &&
                echo " [OK] Respaldo cargado. Se inicia con esa base."
              else
                echo " Operación cancelada. Se sigue con la base actual."
              fi
            else
              echo " [!!] Opción inválida."
            fi
            break
          done
        fi
        fi
        ;;
      *)
        if hacer_backup; then
          echo " [OK] Se sigue con la base actual (no se crea nada)."
        fi
        ;;
    esac
  else
    echo "      Primera ejecución: cargando snw_base.sql..."
    if "$MYSQL" --default-character-set=utf8mb4 -u root -h 127.0.0.1 -P 3306 < sql/snw_base.sql >/dev/null 2>&1; then
      echo " [OK] snw_base.sql cargado (10 tablas + cuentas admin/usuario/dev + 2 numeros autorizados)."
    else
      echo " [!!] Error al cargar snw_base.sql. Revisa que MySQL esté activo."
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
