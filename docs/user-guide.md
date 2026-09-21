# Guía de usuario de Photos Local Keyword Indexer

## Antes de empezar

La app funciona en Apple Silicon con macOS 14 o posterior. La
[`v0.1.0-beta.1` está disponible en GitHub](https://github.com/kerife/photos-local-keyword-indexer-releases/releases/tag/v0.1.0-beta.1)
como DMG arm64 autocontenido y se distribuye explícitamente **sin firma Developer ID ni notarización**. Descarga
también el archivo `.sha256` y comprueba el DMG antes de abrirlo. Monta el DMG, arrastra la app a Aplicaciones y usa primero clic secundario → **Abrir**. Si
macOS mantiene el bloqueo, habilita únicamente **Abrir de todos modos** para esta app en Privacidad y seguridad. No
desactives Gatekeeper globalmente, SIP ni AMFI. Después instala Ollama por separado e inicia su servicio local. La app
no descarga modelos ni transmite imágenes a servicios cloud.
La beta pública aún necesita aceptación independiente en una cuenta limpia antes de considerarse una release oficial.
No busca ni instala actualizaciones automáticamente: para actualizar, descarga una nueva prerelease y verifica de
nuevo su checksum. Sparkle está deshabilitado en esta beta.

Instala el modelo base manualmente:

```bash
ollama pull qwen3-vl:4b
```

La app queda lista con este modelo: el modo adaptativo predeterminado usa `qwen3-vl:4b` en todas las fotos.
Si quieres reservar más detalle para fotos con ubicación, instala opcionalmente:

```bash
ollama pull qwen3-vl:8b
```

Si los modelos están en un disco externo, configura `OLLAMA_MODELS` en el
proceso que inicia Ollama, reinicia Ollama y confirma con `ollama list` que
aparece exactamente `qwen3-vl:4b`. Mantén el disco montado al usar la app: si
no está disponible, el modelo se reportará como ausente. La app no configura
esta variable ni crea, mueve o elimina modelos.

La variable debe apuntar a la raíz del almacén de Ollama, que contiene las
carpetas `blobs` y `manifests`; no uses una de esas subcarpetas como destino.
Si Ollama se inicia desde Finder, una variable exportada en Terminal no se
hereda. Puedes cerrar Ollama y dejar su servidor local abierto desde Terminal:

```bash
OLLAMA_MODELS="/Volumes/Modelos/Ollama" OLLAMA_NO_CLOUD=1 ollama serve
```

O, si necesitas iniciar Ollama desde Finder, establece el entorno de la sesión
antes de abrirlo y retíralo al terminar:

```bash
launchctl setenv OLLAMA_MODELS "/Volumes/Modelos/Ollama"
launchctl setenv OLLAMA_NO_CLOUD "1"
open -a Ollama
```

Monta el disco antes de iniciar Ollama y confirma con `ollama list` que el
modelo exacto aparece. No retires las variables durante el arranque; cuando
cierres Ollama y ya no uses el volumen, limpia la configuración:

```bash
launchctl unsetenv OLLAMA_MODELS
launchctl unsetenv OLLAMA_NO_CLOUD
```

La app y su helper no leen ni modifican `OLLAMA_MODELS`; solo consultan el
servidor local en loopback.

En el primer inicio, permite acceso a **Fotos** y, cuando macOS lo solicite, **Automatización → Fotos** para Photos
Local Keyword Indexer. No concedas Acceso total al disco: no es necesario ni soluciona permisos denegados de Fotos.
Si ejecutas el CLI, el permiso pertenece al host que realmente lo inicia: la terminal integrada de Codex no hereda el
permiso de Terminal.app. Autoriza el host indicado por el diagnóstico o ejecuta el comando desde Terminal.app.

## Analizar y revisar

Abre **Revisión**. Cuando Ollama, el modelo y los permisos están listos, la app crea o recupera una sesión y mantiene
una mesa de 10 fotos elegibles por defecto. Solo incorpora imágenes con identidad y fecha válidas, sin keywords ni
caption; excluye vídeos, screenshots y duplicados de la sesión. Puedes cambiar la cantidad visible de 1 a 50, el
modelo, el análisis automático, los captions, Apple Maps y la concurrencia de Ollama de 1 a 4. Los cambios se aplican
a trabajo pendiente o futuro, nunca reescriben silenciosamente una inferencia ya iniciada.

Cada tarjeta compacta muestra una miniatura, estado y resumen de la propuesta. Pulsa **Revisar** para ampliar una
foto: aparecen todas sus **Etiquetas** (keywords) y su **Descripción** (caption). Puedes agregar o quitar etiquetas
y editar la descripción tan pronto termine el análisis, aunque otras fotos continúen. La barra durante el análisis
es indeterminada: no representa un porcentaje. **Guardar en Fotos** aparece junto a los valores completos de la
tarjeta ampliada. **Guardar en Fotos** confirma únicamente esa foto: vuelve a leerla, conserva valores añadidos externamente,
escribe los cambios visibles, hace read-back y la retira solo si la verificación produce evidence válida; al comenzar,
las selecciones aprobadas quedan fijadas para esa decisión. Para cambiar la selección, ejecuta un dry-run nuevo. Una nueva
foto ocupa el espacio libre. **Descartar** no modifica Fotos; **Reanalizar** conserva el intento anterior y las ediciones
manuales. Una escritura incierta permanece visible y bloqueada para revisión manual.

Los ajustes de cantidad, modelos, análisis automático, descripciones, Apple Maps y concurrencia están en
**Opciones**. **Actividad** muestra todos los contadores, incluidos los guardados en espera y en curso. El menú
secundario de cada tarjeta abre **Reanalizar** y **Detalles de la foto**; los detalles técnicos se consultan en una
hoja, con la traza avanzada contraída. El menú de sesión conserva **Finalizar sesión**.

La interfaz se adapta al tamaño de la ventana: puedes ocultar la barra lateral, los controles se reorganizan y los
editores conservan los borradores al redimensionar. Preparación, Historial, Configuración y los diálogos permiten
desplazarse para alcanzar sus controles. Se respeta el movimiento reducido de macOS.

El modelo se ejecuta en Ollama local. **Apple Maps** sigue siendo opt-in: puede recibir las coordenadas GPS ya presentes
en una foto para resolver contexto, pero no recibe la imagen y las coordenadas no se guardan en manifests, eventos ni
logs. La inferencia semántica es abierta, pero el sobre estructural continúa rechazando transcripciones, nombres de
personas, teléfonos, direcciones, identificadores, coordenadas y otros valores literales privados. El inspector técnico
muestra el prompt sanitizado, su hash, modelo, routing, tiempos y uso de contexto sin revelar esos datos.

En la app puedes activar **Seleccionar fotos al azar** en el alcance del dry-run. En el CLI avanzado puedes elegir fotos al azar de toda la fototeca elegible con `--random`; el manifiesto privado
conserva `strategy: random` para dejar constancia de la selección:

```bash
python -m photos_indexer scan --limit 10 --model qwen3-vl:4b --random
```

La miniatura se solicita bajo demanda a PhotoKit, no se descarga de iCloud, no se persiste y no cruza IPC. Cada análisis
completado conserva un manifest fuente de una foto; cada guardado crea una revisión schema 4 con propuesta original,
edición aprobada, origen de valores y digest de decisión. El CLI avanzado conserva el flujo batch y el comando `--random`
mostrado arriba.

## Aplicar y revertir

En la mesa continua, **Guardar en Fotos** es la confirmación explícita de una tarjeta y no abre una segunda hoja modal.
En manifests batch o históricos, la confirmación conserva el resumen agrupado tradicional. En ambos casos la
app vuelve a localizar cada foto, conserva sus keywords actuales, añade solo las aprobadas y escribe el caption aprobado
solo si el campo estaba vacío; después vuelve a leerlos para
verificar. Si una foto falla, continúa con las demás y presenta estados `verified`, `noop`, `failed` o `uncertain`.

No edites keywords en Fotos u otra app mientras ocurre apply o rollback. Si aparece `uncertain`, revisa esa foto
manualmente: la app no adivina si una escritura que se interrumpió alcanzó Fotos.

Desde Historial, rollback acepta solo el manifiesto revisado que ya pasó por `apply`, y elimina solo las keywords que
registra como aplicadas y verificadas por ese run. Si existe un caption aplicado por ese run, también lo elimina
únicamente cuando la descripción actual coincide exactamente con el valor aplicado; si fue editado después, lo conserva y
marca `uncertain` para revisión manual. Los captions que ya existían o que fueron preservados nunca se tocan. No elimina
keywords que hubieras agregado en otra app después. Conserva los manifests: son necesarios para revertir con seguridad.

Un fallo o una interrupción de otra ejecución no deshabilita un rollback histórico que ya tiene evidencia verificada; la
app mantiene bloqueado el rollback mientras haya otra operación activa y vuelve a validar el manifiesto y sus recibos antes
de escribir. El rollback del run que requiere revisión manual permanece bloqueado.

Cada aplicación verificada deja un recibo local (`mutation_digest`) por foto. Si el recibo falta o no coincide con los
valores aplicados, `rollback` queda bloqueado con `MUTATION_EVIDENCE_INVALID`; esto también afecta manifests antiguos
con estado de mutación pero sin recibo y obliga a generar un scan nuevo. Detecta corrupción o edición accidental, no
es autenticación contra el mismo usuario.

Puedes importar un `manifest.json` desde Historial. La app copia el archivo y su `preview.csv` sin mover el original.
Cuando el archivo revisado llega junto con su dry-run fuente privado y coincidente, también copia esa fuente y muestra
el run como listo para confirmar la aplicación. Un manifest revisado aislado queda en **Solo consulta**: no se puede
aplicar hasta importar también el dry-run fuente o generar un scan nuevo. La comprobación de provenance se mantiene en
Python y se repite antes de cualquier escritura.

Si un dry-run falla porque macOS bloqueó Fotos o Automatización, el estado muestra exactamente el permiso que falta.
Después de concederlo, vuelve a ejecutar **Analizar sin modificar Fotos** para generar un run nuevo. No intentes
aplicar el manifiesto de permisos: la app lo rechaza deliberadamente porque no contiene UUIDs y metadata suficientes
para una escritura segura.

Si una importación se interrumpe, la aplicación elimina el run parcial que estaba creando; el manifiesto original no se
mueve ni se modifica.

## Archivos locales

Los runs están en:

```text
~/Library/Application Support/Photos Local Keyword Indexer/runs/
```

Cada run incluye `manifest.json` y `preview.csv`. El CSV permite revisar las propuestas con Numbers u otra aplicación
que no suba archivos a la nube. Las listas se guardan como JSON dentro de sus celdas. La app no guarda imágenes,
OCR, rutas temporales, coordenadas ni respuestas crudas; los captions habilitados solo quedan en el manifiesto local.

Las preferencias de la mesa de Revisión se guardan en `settings.json` dentro de Application Support para
conservar cantidad visible, modelos, concurrencia y opciones opt-in entre aperturas. El archivo es local y privado; si está
ausente, inválido o pertenece a una versión no compatible, la app usa sus valores predeterminados sin bloquear el análisis.

La beta pública unsigned no muestra una comprobación ni búsqueda manual de actualizaciones: instala manualmente una
nueva prerelease después de verificar su checksum. Las preferencias de actualizaciones y Sparkle solo estarán
disponibles en una futura release firmada con feed HTTPS configurado.

## Problemas frecuentes

- **Ollama no disponible:** abre Ollama y confirma que su servicio local está iniciado. La app solo usa
  `127.0.0.1:11434`; no configura hosts alternos.
- **Falta un modelo:** ejecuta el comando `ollama pull` que muestra la app. No existe fallback ni descarga automática.
- **Error -1743:** concede Automatización a la app instalada en Configuración del Sistema. Dar permiso al terminal de
  compilación no concede permiso a la app.
- **Helper con protocolo inválido:** la app termina el helper local y marca la operación como fallida; vuelve a
  preparar la ejecución. No se reanuda ni se aplica automáticamente un run interrumpido.
- **`UNSAFE_WORKFLOW_RESULT`:** no intentes aplicar ese resultado. Repite el dry-run con el mismo intérprete y usa
  `doctor --json`; la CLI incluye versión y runtime acotados para detectar otra instalación sin revelar rutas completas.
- **Assets solo en iCloud:** abre Fotos y espera a que se descargue una copia local; después crea un scan nuevo.
- **PhotoKit sigue denegado:** un CLI de Python puede no tener bundle ni `NSPhotoLibraryUsageDescription`; autorizar otra instalación de Python no cambia esa identidad. Cierra y vuelve a abrir el proceso, prueba desde Terminal.app con su permiso de Fotos o usa la app distribuida.
- **Gatekeeper bloquea la beta sin firmar:** confirma primero que el checksum coincide con el publicado. Usa clic
  secundario → **Abrir** o la opción específica **Abrir de todos modos** para esa app. No desactives Gatekeeper,
  SIP o AMFI. Una release futura Developer ID firmada y notarizada no necesitará este flujo de beta.
- **Necesitas soporte:** sigue la [guía de soporte seguro](support.md). Comparte solo versión, código estable y
  diagnóstico sanitizado; nunca fotos, manifests, captions, coordenadas, rutas o logs crudos.

## CLI avanzado

La distribución para desarrolladores mantiene el CLI. Los comandos siguen siendo dry-run primero:

```bash
python -m photos_indexer doctor --model qwen3-vl:4b
python -m photos_indexer doctor --model qwen3-vl:4b --json
python -m photos_indexer doctor --model-policy adaptive --fast-model qwen3-vl:4b --detailed-model qwen3-vl:8b
python -m photos_indexer scan --limit 20 --model qwen3-vl:4b
python -m photos_indexer review runs/<timestamp>/manifest.json --selections seleccion.json
python -m photos_indexer apply runs/<timestamp>-review-<id>/manifest.json
python -m photos_indexer status runs/<timestamp>/manifest.json
python -m photos_indexer rollback runs/<timestamp>-review-<id>/manifest.json
```

Después de `scan`, el CLI imprime `manifest:<ruta>` con la ubicación exacta del run para usarla en `review` o abrirla
en la app. `status`, `apply` y `rollback` no vuelven a imprimir rutas; las tablas y eventos no incluyen rutas de
exportación ni imágenes.

`doctor` muestra `scope:ollama_photoscript_local`, `tcc:not_checked` y `helper:not_checked`: valida dependencias
locales sin abrir Fotos. También muestra `app_version` y `runtime` (intérprete/helper, versión de Python,
arquitectura y estado empaquetado) para detectar que Terminal esté usando otro entorno. La comprobación de TCC y del
helper ocurre al ejecutar el dry-run; la ruta completa del intérprete nunca se muestra. Con `--json`, la salida es un
objeto acotado para soporte automatizado e incluye también el `exit_code` normalizado (0, 1 o 2).

El CLI y la app comparten el mismo núcleo: `scan` no escribe; `apply` y `rollback` requieren una copia revisada
válida. Los manifests legacy schema1 de dry-run se rechazan con `MANIFEST_NOT_REVIEWED` hasta generar esa copia.
