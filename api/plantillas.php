<?php
require __DIR__ . '/db.php';

function fila_plantilla(array $r): array
{
    return [
        'id'          => (int) $r['id'],
        'clave'       => $r['clave'],
        'nombre'      => $r['nombre'] ?: $r['clave'],
        'texto'       => $r['texto'],
        'actualizada' => (int) strtotime($r['fecha_actualizacion']) * 1000,
    ];
}

function obtener_todas(mysqli $conn): array
{
    $res = mysqli_query($conn, "SELECT id, clave, nombre, texto, fecha_actualizacion FROM plantillas ORDER BY fecha_actualizacion DESC");
    return array_map('fila_plantilla', mysqli_fetch_all($res, MYSQLI_ASSOC));
}

function slug(string $t): string
{
    $t = strtolower(trim($t));
    $t = iconv('UTF-8', 'ASCII//TRANSLIT', $t) ?: $t;
    $t = preg_replace('/[^a-z0-9]+/', '_', $t);
    return trim($t, '_') ?: 'plantilla';
}

$metodo = $_SERVER['REQUEST_METHOD'];

if ($metodo === 'GET') {
    json_out(200, obtener_todas($conn));
}

if ($metodo === 'POST') {
    $b = cuerpo_json();
    $nombre = trim($b['nombre'] ?? '');
    $texto = trim($b['texto'] ?? '');
    $clave = slug($b['clave'] ?? $nombre);

    if ($nombre === '' || $texto === '') {
        json_out(400, ['ok' => false, 'error' => 'Nombre y mensaje son obligatorios']);
    }

    $st = mysqli_prepare($conn, "INSERT INTO plantillas (clave, nombre, texto) VALUES (?, ?, ?)");
    mysqli_stmt_bind_param($st, 'sss', $clave, $nombre, $texto);
    try {
        mysqli_stmt_execute($st);
    } catch (mysqli_sql_exception $e) {
        if ((int) $e->getCode() === 1062) {
            json_out(409, ['ok' => false, 'error' => 'Ya existe una plantilla con esa clave']);
        }
        throw $e;
    }

    $id = mysqli_insert_id($conn);
    $res = mysqli_query($conn, "SELECT id, clave, nombre, texto, fecha_actualizacion FROM plantillas WHERE id = $id");
    json_out(201, fila_plantilla(mysqli_fetch_assoc($res)));
}

if ($metodo === 'PUT') {
    parse_str($_SERVER['QUERY_STRING'] ?? '', $qs);
    $id = (int) ($qs['id'] ?? 0);
    if (!$id) json_out(400, ['ok' => false, 'error' => 'Falta id']);

    $b = cuerpo_json();
    $nombre = trim($b['nombre'] ?? '');
    $texto = trim($b['texto'] ?? '');
    if ($nombre === '' || $texto === '') {
        json_out(400, ['ok' => false, 'error' => 'Nombre y mensaje son obligatorios']);
    }

    $st = mysqli_prepare($conn, "UPDATE plantillas SET nombre = ?, texto = ? WHERE id = ?");
    mysqli_stmt_bind_param($st, 'ssi', $nombre, $texto, $id);
    mysqli_stmt_execute($st);
    if (!mysqli_affected_rows($conn)) {
        $existe = mysqli_query($conn, "SELECT 1 FROM plantillas WHERE id = $id");
        if (!mysqli_fetch_row($existe)) json_out(404, ['ok' => false, 'error' => 'Plantilla no encontrada']);
    }

    $res = mysqli_query($conn, "SELECT id, clave, nombre, texto, fecha_actualizacion FROM plantillas WHERE id = $id");
    json_out(200, fila_plantilla(mysqli_fetch_assoc($res)));
}

if ($metodo === 'DELETE') {
    parse_str($_SERVER['QUERY_STRING'] ?? '', $qs);
    $id = (int) ($qs['id'] ?? 0);
    if (!$id) json_out(400, ['ok' => false, 'error' => 'Falta id']);

    $st = mysqli_prepare($conn, "DELETE FROM plantillas WHERE id = ?");
    mysqli_stmt_bind_param($st, 'i', $id);
    mysqli_stmt_execute($st);

    if (!mysqli_affected_rows($conn)) {
        json_out(404, ['ok' => false, 'error' => 'Plantilla no encontrada']);
    }
    json_out(200, ['ok' => true]);
}

json_out(405, ['ok' => false, 'error' => 'Método no permitido']);
