<?php
$DB_HOST = '127.0.0.1';
$DB_USER = 'root';
$DB_PASS = '';
$DB_NAME = 'snw_pacientes';

mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);

try {
    $conn = mysqli_connect($DB_HOST, $DB_USER, $DB_PASS, $DB_NAME);
    mysqli_set_charset($conn, 'utf8mb4');
} catch (Throwable $e) {
    json_out(500, ['ok' => false, 'error' => 'Error de conexión con MySQL']);
}

function json_out(int $code, $data): void
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function cuerpo_json(): array
{
    $raw = file_get_contents('php://input');
    $datos = json_decode($raw, true);
    return is_array($datos) ? $datos : [];
}
