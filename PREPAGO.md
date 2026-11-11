# Recordatorio de recargas prepago

Los administradores y desarrolladores pueden abrir **Extras → Recordatorio prepago**. Ahí pueden agregar líneas, corregir sus datos, desactivarlas y consultar el historial. Los desarrolladores también pueden administrar el catálogo de operadores.

## Puesta en marcha

1. Reinicia el backend. La tarea de arranque crea cuatro tablas con claves foráneas en la base de SNW y registra las fichas WOM iniciales sin duplicarlas. El usuario de MySQL necesita permiso para crear esas tablas.
2. Revisa **SNW MVYS**, **SNW MS**, **CRM MS** y **CRM MVYS**. El módulo copia un móvil válido y no repetido únicamente al crear la ficha. Para SNW usa `VS_WSP_PHONE_NUMBER` o `MS_WSP_PHONE_NUMBER`. Para CRM usa `VS_CRM_PHONE_NUMBER` o `MS_CRM_PHONE_NUMBER`. `MVYS` es el alias de `VS`. Las fichas con datos ausentes o ambiguos se completan desde Extras.
3. Registra la última recarga real. Abrir el portal del operador no cambia la fecha. Después del pago, usa **Registrar recarga**. Si la fecha guardada era incorrecta, cámbiala en **Editar** para dejar una corrección en el historial.
4. Comprueba el SMTP de SNW y el correo administrativo. Si quieres otro destinatario para este módulo, guárdalo desde **Correo de aviso**. La clave `prepago_correo_destino` se guarda en `configuracion`. Una ficha necesita número, fecha, estado activo y avisos habilitados para que se programe su correo.

## Portales de recarga

| Operador | Página verificada |
| --- | --- |
| WOM | [Portal de recargas](https://www.wom.cl/recargas/) |
| Entel | [Recarga en línea desde el portal Entel](https://miportal.entel.cl/marketing/lugares-recargas) |
| Movistar | [Portal de recargas](https://recargaweb.movistar.cl/) |
| Claro | [Portal de recargas](https://recargas.clarochile.cl/) |

En las fuentes oficiales consultadas no se encontró un parámetro documentado para rellenar el teléfono en estos portales. Usa **Copiar número** y pégalo en el sitio del operador. Entel muestra el botón **Recargar aquí** en su página de recargas. Para otro operador, un desarrollador puede registrarlo sin enlace y habilitarlo tras comprobar el portal HTTPS y su dominio oficial. SNW abre el portal en una pestaña independiente y nunca agrega el teléfono a la URL.

## Despacho y diagnóstico

- El plazo inicial es de 150 días calendario en `America/Santiago`. En **Extras → Ajustes → Plazo del recordatorio**, un administrador o desarrollador puede fijar entre 1 y 3650 días para todas las líneas. Se guarda en `configuracion` como `prepago_recordatorio_dias`. Las fechas se recalculan desde la última recarga de cada línea y los avisos enviados conservan su ciclo. La tarea revisa los vencimientos al arrancar y cada día a las 08:00 de Chile.
- MySQL serializa el despacho y la edición de cada ficha. Cada intento se persiste antes de contactar a SMTP. Un correo enviado queda deduplicado por línea y ciclo de recarga, incluso al corregir su fecha.
- Si SMTP confirma un rechazo, el próximo chequeo diario puede reintentar. Si se corta la conexión en una fase en que SMTP pudo haber aceptado el correo, o se detiene el proceso en medio del despacho, el intento queda **Entrega incierta**. Revisa el historial y el servidor SMTP antes de tomar acción sobre ese ciclo.
- El historial de Extras registra las recargas, correcciones y los intentos de aviso. Las acciones administrativas aparecen también en la auditoría de SNW. Editar una ficha de Extras no altera las integraciones de WhatsApp o CRM.

## Verificación

```powershell
python verificaciones/prepago.py
python verificaciones/prepago.py --puerto 33316
node --check frontend/js/extras.js
```

La segunda variante requiere una instancia local aislada de MariaDB con acceso de prueba como root sin clave. Cada prueba crea y elimina su propio esquema. Las dos variantes usan SMTP simulado.
