# Checklist de aceptación — beta pública unsigned

Esta lista registra evidencia pendiente; una casilla sin marcar no es un pase.
El resultado no convierte la beta en una release Developer ID/notarizada.

## Aceptación local antes de publicar

- [ ] El candidato local identifica versión, build, Apple Silicon y macOS
  mínimo; no se presenta todavía como descarga disponible.
- [ ] El DMG local y su sidecar SHA-256 se generaron juntos y el checksum
  coincide con el archivo exacto.
- [ ] El DMG local pasó `hdiutil verify`, se montó de solo lectura y contiene
  la app y helper esperados.
- [ ] Las notas enlazan esta checklist, [privacidad](../privacy.md),
  [soporte](../support.md), estado de [licencia](license-status.md) y avisos de
  [terceros](third-party-notices.md).

## Cuenta limpia y permisos

- [ ] En una cuenta macOS limpia se abrió el DMG verificado y se usó solo el
  flujo por-app de Gatekeeper; no se desactivaron Gatekeeper, SIP ni AMFI.
- [ ] La app instalada pidió Fotos y Automatización en el bundle exacto, no en
  Terminal ni en una copia de build.
- [ ] Con Ollama y el modelo instalados manualmente, un dry-run de una foto de
  prueba completó sin escribir en Fotos.
- [ ] La propuesta se revisó contra la miniatura local; no se aplicó ningún
  cambio fuera de una confirmación explícita.
- [ ] En una foto de prueba distinta, Apply produjo receipt y read-back
  verificados; luego Rollback produjo read-back verificado de solo los valores
  añadidos por ese run.
- [ ] Se interrumpieron por separado un análisis y un guardado dentro de un
  entorno de prueba; al reabrir, el análisis no se trató como guardado y el
  guardado incierto no se reintentó automáticamente.
- [ ] Tras cerrar y reabrir la app, el historial y los receipts se conservaron
  sin crear decisiones o escrituras duplicadas.

## Privacidad, soporte y recuperación

- [ ] Apple Maps quedó desactivado salvo consentimiento específico para enviar
  coordenadas de la foto seleccionada; no se envió una imagen a Maps.
- [ ] El diagnóstico de soporte revisado no contiene fotos, manifests, rutas,
  captions, OCR, coordenadas, respuestas del modelo ni credenciales.
- [ ] La limpieza de exports temporales se comprobó tras finalización,
  cancelación y fallo controlado. Una terminación forzosa solo se marca limpia
  después de evidencia de recuperación al reabrir; no se presume.
- [ ] El canal público de Issues está habilitado y el responsable publicó un
  canal privado para seguridad/privacidad antes de ampliar la audiencia.
- [ ] Se probó un error o escritura incierta sin reintento automático; la
  documentación indica revisión manual y conserva el receipt local.

## Accesibilidad e interfaz

- [ ] La app se validó con teclado, VoiceOver, contraste aumentado y Reducir
  movimiento; los controles de revisar, guardar, descartar, pausa e historial
  siguen alcanzables y con nombres claros.
- [ ] Al redimensionar la ventana y colapsar la barra lateral, los borradores
  de revisión se conservan, no se ocultan acciones necesarias y no se genera
  una escritura.

## Límite de esta aceptación

- [ ] La release sigue rotulada **unsigned, non-notarized public beta** y con
  actualizaciones manuales; Sparkle permanece deshabilitado.
- [ ] No se afirma identidad validada por Apple, notarización, stapling ni
  aceptación normal de Gatekeeper. Esos gates se repiten sobre el futuro bundle
  Developer ID exacto.

## Confirmación posterior a una publicación autorizada

Esta sección no es un requisito previo para autorizar la publicación: evita el
gate circular de exigir descargar un asset que todavía no existe. Se completa
después de publicar la prerelease exacta.

- [ ] La prerelease publicada identifica versión, build, Apple Silicon y macOS
  mínimo, y enlaza sus notas, checksum, privacidad, soporte y avisos.
- [ ] El DMG y el sidecar se descargaron desde la publicación prevista y el
  checksum coincide; el DMG descargado vuelve a pasar `hdiutil verify`.
