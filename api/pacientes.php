<?php
require __DIR__ . '/db.php';

$sql = "SELECT id, nombre, apellido, telefono, info_extra, estado,
               DATE_FORMAT(fecha_actualizacion, '%d-%m-%Y %H:%i') AS actualizado
        FROM pacientes";

if (isset($_GET['q']) && trim($_GET['q']) !== '') {
    $like = '%' . trim($_GET['q']) . '%';
    $sql .= " WHERE nombre LIKE ? OR telefono LIKE ? OR info_extra LIKE ?";
    $st = mysqli_prepare($conn, $sql . " ORDER BY id");
    mysqli_stmt_bind_param($st, 'sss', $like, $like, $like);
} else {
    $st = mysqli_prepare($conn, $sql . " ORDER BY id");
}

mysqli_stmt_execute($st);
$res = mysqli_stmt_get_result($st);
json_out(200, mysqli_fetch_all($res, MYSQLI_ASSOC));
