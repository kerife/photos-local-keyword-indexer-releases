# Privacidad de la beta pública

Fecha de vigencia: 21 de septiembre de 2026. Esta nota describe la beta pública
sin firma Developer ID de **Photos Local Keyword Indexer**; no es una promesa de
una futura release firmada.

## Qué procesa la app

- La app lee las fotos mediante el permiso de Fotos de macOS. Usa
  Automatización → Fotos para leer metadatos y exportar una copia temporal para
  análisis, y solo usa setters de keywords/captions después de la confirmación
  explícita de Apply o Rollback.
- Las imágenes se envían únicamente al servicio local de Ollama en
  `127.0.0.1:11434`. La app no configura proxies, hosts remotos, telemetría ni
  reporte automático de fallos.
- Apple Maps es opcional y está desactivado por defecto. Si lo activas, se
  envía a Apple Maps la coordenada GPS ya incluida en la foto seleccionada para
  pedir contexto de lugar. No se envía la imagen. No actives esta opción si no
  aceptas esa transmisión puntual.

## Qué se guarda en la Mac

Los runs, manifests revisados, decisiones y receipts se guardan localmente en
Application Support con permisos privados. Los captions solo se conservan si
los activaste. La configuración conserva preferencias de la mesa y opciones
opt-in. Ninguno de esos archivos debe compartirse para pedir soporte.

Las miniaturas y exports de análisis son temporales: no se escriben en
manifests, IPC, logs ni una caché persistente y se eliminan al completar,
cancelar o fallar una operación que alcanza su limpieza normal. No se afirma
esa limpieza si el proceso se termina forzosamente; esa recuperación requiere
evidencia independiente al reabrir la app. Los logs y diagnósticos usan eventos
y códigos sanitizados; no incluyen imágenes, OCR literal, captions,
coordenadas, respuestas crudas del modelo ni rutas personales.

Para eliminar datos locales, cierra la app y borra únicamente los runs que ya
no necesites de `~/Library/Application Support/Photos Local Keyword Indexer/runs/`;
borra `settings.json` del mismo directorio para restablecer preferencias. Al
borrar un run se pierden su historial, manifest, receipt y la posibilidad de
ejecutar rollback desde la app. No borres un run con una escritura `uncertain`
hasta resolverla manualmente en Fotos, ni uno con cambios verificados si aún
podrías querer revertirlos. Las cachés y exports temporales están bajo
`~/Library/Caches/Photos Local Keyword Indexer/` y los eventos sanitizados bajo
`~/Library/Logs/Photos Local Keyword Indexer/`; borrarlos elimina solo esos
datos locales y no revierte cambios ya confirmados en Fotos.

## Límites importantes

Ollama y sus modelos se instalan y administran por separado. Esta app solo
habla con el endpoint local fijo; revisa también la política y configuración de
la instalación de Ollama que uses. La beta no descarga modelos ni los incluye
en el DMG.

La beta pública no tiene cuenta, servidor propio ni canal automático para
recibir datos de uso. Para soporte, comparte únicamente el diagnóstico
sanitizado indicado en la [guía de soporte](support.md).

## Contacto y cambios

Las aclaraciones de privacidad y las solicitudes relacionadas con esta beta se
reciben mediante el canal privado que publique el responsable antes de abrir
una beta más amplia. Hasta que ese canal exista, no publiques fotos, manifests
ni diagnósticos en un issue público. Las modificaciones materiales a esta nota
se versionarán junto con la siguiente prerelease.
