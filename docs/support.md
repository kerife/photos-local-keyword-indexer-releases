# Soporte seguro para la beta pública

La beta no recopila diagnósticos automáticamente. Si necesitas ayuda, prepara
un reporte mínimo y compártelo solo por el canal que el responsable publique.
Para errores no sensibles, se prevé usar los
[Issues públicos del repositorio de descargas](https://github.com/kerife/photos-local-keyword-indexer-releases/issues),
cuando estén habilitados; confirma que no incluyes datos privados antes de enviar.
En la comprobación del 21 de septiembre de 2026, Issues estaba deshabilitado.
No se debe presentar ese enlace como un canal operativo hasta habilitarlo y
probarlo. El mantenedor debe definir también un contacto privado de seguridad.

## Información permitida

- Versión visible de la app, versión de macOS y si tu Mac es Apple Silicon.
- El paso que falló (preparación, dry-run, revisión, guardado o rollback).
- El código estable mostrado, por ejemplo `PHOTOS_ACCESS_DENIED`,
  `ANALYSIS_FAILED` o `WRITE_UNCERTAIN`.
- La salida de `doctor --json`, siempre que la revises antes y conserve su
  formato sanitizado.
- Si el checksum del DMG coincidió con el sidecar publicado.

## No compartas

No adjuntes fotos, capturas con contenido privado, manifests, CSV, receipts,
captions, keywords, UUIDs completos, coordenadas, archivos de log, rutas
locales, respuestas de Ollama, OCR, credenciales ni datos de tu fototeca.
Tampoco ejecutes comandos para desactivar Gatekeeper, SIP o AMFI como parte de
un diagnóstico.

## Antes de reportar

1. Confirma que usas la prerelease y el DMG cuyo checksum verificaste.
2. Repite únicamente un dry-run de una foto de prueba; no reintentes una
   escritura `uncertain` ni ejecutes rollback sobre ella.
3. Ejecuta `doctor --json` con el mismo runtime. Ese comando no abre Fotos ni
   cambia permisos.
4. Describe el código y siguiente acción sin copiar datos de la fototeca.

Los reportes de una posible vulnerabilidad, exposición de datos o defecto de
integridad no deben abrirse públicamente. El responsable debe publicar un
canal privado de seguridad antes de aceptar una beta más amplia; hasta entonces
la distribución queda limitada a participantes que acepten este límite.
