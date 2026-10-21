# Despacho de WhatsApp: capacidad, operación y límites

Estado: implementación parcial, **no validar todavía como optimización integral de producción**.
Revisión documental: 21-10-2026. No se realizaron envíos de carga a Meta.

## Fuentes y capacidad

- [Documentación de AWS sobre throughput de WhatsApp](https://docs.aws.amazon.com/social-messaging/latest/userguide/increase-message-throughput.html): confirma 80 mensajes/s por número de forma predeterminada, contando tráfico entrante y saliente, y posible aumento a 1.000 bajo condiciones de elegibilidad. No confirma que alguno de los cuatro números de SNW tenga ese aumento.
- [Colección oficial de Meta para WhatsApp Cloud API](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api): confirma el modelo de número comercial y WABA, pero no expone en el documento consultable los techos concretos ni los códigos citados abajo.
- [Página de throughput de Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput) y [errores de Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/): devolvieron HTTP 429 al consultar el 20-10-2026. No se verificaron directamente los valores 20/s de coexistencia, 6 s por pareja ni los detalles de `130429`, `131056` y `131057` en la fuente oficial. Son **supuestos conservadores del diseño**, sujetos a confirmación antes de elevar velocidades.

80/s y 1.000/s son techos teóricos, no cuotas de destinatarios únicos ni autorización para contactar más pacientes. La calidad, el opt-out, el consentimiento, la categoría de plantilla y los límites de mensajería se siguen aplicando por separado.

## Diagnóstico previo

- Envíos manuales y programados convergen en `_procesar_job`, con una lista de destinatarios en memoria y un envío por base. El intervalo predeterminado era 1.000 ms entre destinatarios; por ello, aun con `wa_throughput_mps=10`, cada campaña normalmente no superaba ~1 solicitud/s. No hay medición de la latencia real de Meta para derivar una tasa observada.
- `wa_rate_limit.py` guardaba el próximo turno en un singleton del proceso. No distinguía el `Phone Number ID`, por lo que frenaba cuatro centros juntos en un proceso y no coordinaba procesos distintos.
- Cada solicitud crea un `httpx.AsyncClient`; hay conexiones y consultas individuales por destinatario. Los trabajos y solicitudes de aprobación viven en `JOBS`/`PENDIENTES`, sin recuperación completa al reiniciar.
- El scheduler reclama programados mediante `UPDATE` atómico, pero el lote activo posterior sigue en memoria. Una caída tras enviar a Meta y antes de registrar el resultado puede dejar una entrega de resultado incierto; reintentar automáticamente sería inseguro.

## Lo implementado

- `wa_dispatch_limit.py` reserva en MySQL un turno por `Phone Number ID` y una separación por pareja emisor-destinatario. Usa transacciones breves, `FOR UPDATE` y tiempo de la base; no deja la transacción abierta durante HTTP. La huella del destinatario reutiliza el HMAC de supresión, se elimina al tramitar `ELIMINAR` y caduca de la tabla operativa después de un día.
- Cuatro políticas independientes, con 10 mensajes/s solicitados inicialmente para estándar y 3/s iniciales para coexistencia. Sin verificación explícita de la capacidad del número, esos valores también son el techo. Una vez verificada, rige un techo conservador de 60/s estándar y 15/s coexistencia, con 25 % reservado; nunca se activan 1.000/s automáticamente. Los cambios de configuración se recargan desde MySQL en hasta cinco segundos.
- `130429` enfría y reduce a la mitad el ritmo del número afectado; `131057` pausa ese número al menos 60 s; `131056` aplaza la pareja afectada. Hay backoff exponencial con jitter, máximo de intentos configurable y respeto de `Retry-After` numérico. El ritmo solo vuelve a subir tras cinco minutos y suficiente volumen aceptado, nunca por tiempo únicamente.
- La API oficial ya no usa `intervalo_ms`; el motor simulado sí. La página Configuración muestra el ritmo de cada centro. El modal de progreso muestra mensajes/s observados durante el minuto reciente y un tiempo restante aproximado cuando hay datos suficientes. “Aceptado por Meta” no significa entregado.
- Si se pierde la respuesta HTTP o Meta responde sin identificador de mensaje, el lote se detiene como error de **resultado incierto**. El paciente no se marca como fallido ni se reintenta automáticamente: primero hay que conciliar el resultado con Meta. Esto evita una duplicación automática, pero aún no constituye conciliación ni recuperación duradera tras reinicio.
- Si falla la base o la comprobación de capacidad del canal, el lote también se detiene sin marcar como fallidos a los destinatarios pendientes. No se hacen llamadas a Meta mientras el limitador compartido no pueda reservar turno.

## Configuración (`configuracion`, no `.env`)

| Clave | Inicial | Alcance |
|---|---:|---|
| `<CENTRO>_WSP_RATE_LIMIT_MODE` | `estandar` | `estandar` o `coexistencia` |
| `<CENTRO>_WSP_RATE_LIMIT_MPS` | `10` | Solicitud por centro; el techo seguro prevalece |
| `<CENTRO>_WSP_RATE_LIMIT_VERIFIED` | `false` | Solo tras comprobar capacidad y modo en Meta |
| `wa_dispatch_pair_seconds` | `6` | Separación por emisor-destinatario |
| `wa_dispatch_reserved_percent` | `25` | Margen para tráfico adicional |
| `wa_dispatch_max_retries` | `3` | Saturación transitoria |
| `wa_dispatch_backoff_base_seconds` | `2` | Espera exponencial inicial |
| `wa_dispatch_backoff_max_seconds` | `60` | Tope normal; `131057` requiere al menos 60 s |

`wa_throughput_mps` queda en la base solo por compatibilidad; ya no gobierna `/messages`. La clave del segundo centro continúa siendo `MS_WSP_PHONE_NUMBER_ID`: es el contrato vigente implementado anteriormente, por lo que la grafía `MS_WSP_PHONE_NUMBER_I` del prompt no reemplaza datos existentes.

Para operar: revisar el modo real de cada número, empezar con los valores iniciales, observar aceptaciones y errores, y ajustar el MPS solicitado solo con la capacidad confirmada en Meta. Un centro no configurado no despacha. Si MySQL no está disponible, la reserva falla cerrada y no se inicia la llamada a Meta. El endpoint para desarrolladores `/api/whatsapp/rate-limit` muestra ritmo actual, pausas y errores acumulados; son contadores desde la creación de la fila, no tasas históricas.

## Pendiente antes de declarar completa la optimización

1. Sustituir `JOBS`/`PENDIENTES` en memoria por una cola persistente con reclamación y recuperación de trabajos; al reiniciar, no reintentar automáticamente resultados inciertos.
2. Procesar cohortes en lotes, evitar mantener campañas completas en RAM y medir consultas/latencia/CPU/memoria.
3. Añadir métricas duraderas por campaña y centro, alertas y pruebas de carga de 10.000 mensajes simulados, incluidos reinicio y tráfico entrante. No se ha medido una mejora “antes/después” con una línea base comparable.
4. Verificar en Meta las condiciones concretas de los cuatro números, el modo coexistencia y los códigos de error. No se ha probado envío real con esta política.

Validación local del 21-10-2026: 250 pruebas correctas, compilación Python, sintaxis de los JavaScript modificados y `git diff --check` correctos. Las pruebas nuevas simulan la conexión con Meta y comprueban que el lote se detiene sin tocar a los pacientes restantes; aún **no** sustituyen una prueba de carga de 10.000 mensajes ni una prueba de reinicio con cola persistente.

Hasta cerrar esos puntos, el limitador mejora la coordinación del tráfico, pero **no garantiza recuperación de campañas ni ausencia de duplicados tras un reinicio**. No elevar la configuración sobre el valor inicial para producción sin completar esta validación.
