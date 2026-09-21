# photos-local-keyword-indexer

Código fuente publicado bajo [licencia MIT](LICENSE). Este repositorio también
aloja las descargas de la beta pública. No incluye datos de fototecas, evidencia
privada, entornos de build ni material de firma. Publicar el código no acredita
la aceptación manual ni publica automáticamente una nueva versión de la app.

`photos-local-keyword-indexer` analiza las fotos recientes de Apple Fotos con un modelo de visión ejecutado por
Ollama en la propia Mac y propone keywords descriptivas en español. El comando `scan` es siempre un *dry-run*;
solo los comandos explícitos `apply` y `rollback` pueden escribir keywords y captions a partir de un manifiesto revisado.

El proyecto incluye la app nativa **Photos Local Keyword Indexer** para Apple Silicon y macOS 14 o posterior. La
app incluye el helper Python y sus dependencias: no requiere que quien la instala prepare un entorno Python. Ollama y
sus modelos se instalan por separado y de forma manual. El CLI se conserva para automatización y usuarios avanzados.

El proyecto está diseñado para macOS y Python 3.11 o posterior, pero menor que Python 4. Se recomienda Python 3.12
para el primer entorno.

## Límites de privacidad y seguridad

- Por defecto, la imagen se envía únicamente al servidor local fijo `http://127.0.0.1:11434/api`. El cliente ignora
  proxies del entorno y no usa APIs cloud.
- `--apple-maps` es una excepción explícita: envía a Apple Maps la coordenada GPS de cada foto que la tenga para
  recuperar nombres de lugares cercanos. Nunca envía la imagen a Apple, no guarda coordenadas ni conserva la
  respuesta cruda; si no se especifica la opción, no se consulta Maps.
- No descarga, crea ni elimina modelos. Rechaza modelos cuyo nombre indique `cloud` y exige una instalación local
  con capacidad de visión.
- PhotoKit solo selecciona imágenes recientes y excluye capturas de pantalla. PhotoScript lee metadatos, exporta
  temporalmente una copia renderizada y es la única capa que cambia `photo.keywords`.
- Al inicializar PhotoScript se desactivan sus reintentos internos: la versión 0.5.3 podría intentar `killall Photos`
  tras un timeout; los reintentos limitados y seguros los controla exclusivamente este programa.
- Nunca se abre la base de datos de Fotos ni el interior de una fototeca. No se modifican originales, títulos,
  fechas, ubicaciones, álbumes, favoritos, caras ni identidades de «Personas y mascotas».
- El modelo realiza inferencia semántica abierta a partir de evidencia visible. Puede identificar tipos de documento,
  finalidad, actividad, entorno y categorías médicas, financieras, profesionales o sociales sin que el concepto tenga
  que existir en una allowlist. Puede interpretar texto de forma efímera para distinguir, por ejemplo, una
  `prescripción óptica` de una foto genérica de `texto`, pero no debe transcribir nombres, teléfonos, direcciones,
  correos, identificadores, coordenadas, mediciones, valores ni pasajes literales.
- Si Fotos ya tiene geolocalización, se lee solo en memoria. Sin `--apple-maps` se entrega únicamente al modelo local
  como contexto aproximado; con `--apple-maps`, Apple Maps puede devolver lugares cercanos para enriquecer ese
  contexto. Nunca se modifica, registra o guarda la coordenada en el manifest/CSV.
- La política `es-semantic-open-v3` normaliza, deduplica y limita a ocho propuestas nuevas por foto, rechaza formas
  de datos literales y nombres propios no verificados, y deja la decisión final a la revisión explícita. Los manifests
  anteriores `es-visible-v1` y `es-visible-contextual-v2` siguen siendo cargables.
- No se registran imágenes. Tampoco se guardan base64, prompts completos, respuestas crudas, OCR, filenames ni rutas
  temporales. Los captions solo se conservan en un manifiesto local cuando activas explícitamente esa opción. Los
  archivos exportados se eliminan incluso cuando falla una foto.

Las keywords de Fotos son metadatos de búsqueda independientes de las identidades nativas de «Personas y
mascotas». Esta herramienta no crea, renombra, fuerza ni reindexa esas identidades.

## Instalación

### App macOS

El proyecto ya ofrece una app autocontenida y un DMG arm64 de **beta de desarrollo sin firma Developer ID ni
notarización**. El helper Python va incluido; Ollama y sus modelos siguen instalándose por separado. Descarga
[`v0.1.0-beta.1` desde GitHub](https://github.com/kerife/photos-local-keyword-indexer-releases/releases/tag/v0.1.0-beta.1)
y verifica el archivo `.sha256` publicado junto al DMG. El checksum comprueba la integridad del archivo descargado,
pero no sustituye una identidad Developer ID. Esta beta es pública, pero no es una release oficial firmada,
notarizada o aceptada normalmente por Gatekeeper. Sus actualizaciones son manuales: descarga y verifica una nueva
prerelease; Sparkle está deshabilitado en esta beta.

Para la beta gratuita, monta el DMG, arrastra **Photos Local Keyword Indexer** a Aplicaciones y usa primero
clic secundario → **Abrir**. Si macOS mantiene el bloqueo, usa únicamente la opción **Abrir de todos modos** para
esta app en Privacidad y seguridad. No desactives Gatekeeper globalmente, SIP ni AMFI. Una futura distribución
Developer ID firmada y notarizada podrá abrirse con la experiencia normal de Gatekeeper.

1. Instala Ollama manualmente desde [ollama.com/download](https://ollama.com/download), inicia el servicio e instala
   al menos el modelo rápido. La app no descarga ni mueve modelos automáticamente:

   ```bash
   ollama pull qwen3-vl:4b
   ```

   La app queda lista con este modelo: la configuración adaptativa predeterminada usa `qwen3-vl:4b` tanto
   para fotos normales como geolocalizadas. Para una segunda etapa más detallada en fotos con GPS, instala
   opcionalmente:

   ```bash
   ollama pull qwen3-vl:8b
   ```

2. Abre la app. El onboarding comprueba que Ollama responda exclusivamente en `127.0.0.1:11434` y que los modelos
   locales requeridos tengan visión. Si algo falta, muestra el comando manual correspondiente sin modificar Fotos.

La app nunca incluye modelos en el DMG, no configura `OLLAMA_MODELS` y no necesita Acceso total al disco. Si
guardas los modelos en un disco externo, configura `OLLAMA_MODELS` en el proceso que inicia Ollama (no en la app),
reinicia Ollama y confirma que `ollama list` muestra exactamente `qwen3-vl:4b` antes de abrir la app. El disco debe
estar montado en cada ejecución; si no lo está, el preflight informará que el modelo no está instalado y mostrará
el comando manual correspondiente. La app no crea, mueve ni elimina modelos.

`OLLAMA_MODELS` debe apuntar a la raíz del almacén de Ollama, la carpeta que
contiene `blobs` y `manifests`; no lo apuntes a una de esas subcarpetas. Si
inicias Ollama desde Finder, una variable exportada en otra terminal no llega a
ese proceso. La forma más directa es cerrar Ollama por completo y mantener este
servidor local abierto:

```bash
OLLAMA_MODELS="/Volumes/Modelos/Ollama" OLLAMA_NO_CLOUD=1 ollama serve
```

Si necesitas iniciar la aplicación de Ollama desde Finder, configura su
entorno de lanzamiento antes de abrirla y quítalo cuando dejes de usar ese
volumen:

```bash
launchctl setenv OLLAMA_MODELS "/Volumes/Modelos/Ollama"
launchctl setenv OLLAMA_NO_CLOUD "1"
open -a Ollama
```

Espera a que Ollama esté iniciado y confirma con `ollama list`; no ejecutes
`unsetenv` durante ese arranque. Cuando cierres Ollama y ya no necesites el
volumen, limpia la configuración de la sesión:

```bash
launchctl unsetenv OLLAMA_MODELS
launchctl unsetenv OLLAMA_NO_CLOUD
```

En ambos casos, ejecuta `ollama list` después de montar el disco y antes de
abrir la app. La app y el helper no leen ni cambian `OLLAMA_MODELS`: solo
consultan el servidor fijo de loopback. Nunca sustituyas ese servidor por un
host remoto.

### CLI avanzado

1. Instala Python 3.12 desde una distribución de confianza (Python.org o Homebrew) y crea un entorno aislado:

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .
   ```

   También funciona Python 3.11 o una versión posterior compatible, siempre que sea menor que Python 4.

2. Instala Ollama manualmente desde [ollama.com/download](https://ollama.com/download), inicia su servicio e instala
   el modelo predeterminado. El programa nunca ejecutará esta descarga por sí mismo:

   ```bash
   ollama pull qwen3-vl:4b
   ```

   Se requiere Ollama 0.12.7 o posterior. Para desarrollar o ejecutar las pruebas instala el extra local:

   ```bash
   python -m pip install -e '.[dev]'
   ```

## Permisos de macOS

La primera ejecución puede pedir dos permisos distintos en **Configuración del Sistema → Privacidad y seguridad**:

1. En **Fotos**, permite el acceso a Terminal, Codex o al IDE desde el que ejecutes el comando. PhotoKit usa este
   permiso para enumerar y validar assets. La API de macOS denomina al nivel necesario `ReadWrite` o «acceso
   completo», porque no ofrece un nivel `ReadOnly`; la implementación PhotoKit del proyecto solo ejecuta lecturas y
   toda escritura sigue confinada a PhotoScript bajo `apply` o `rollback`. Con acceso limitado solo se procesan las
   fotos visibles para esa app.
2. En **Automatización**, permite que Terminal, Codex o el IDE controle **Fotos**. PhotoScript usa Apple Events para
   leer, exportar y, solo durante `apply`/`rollback`, escribir keywords y captions.

Son permisos distintos. `PHOTOS_ACCESS_DENIED` significa que PhotoKit no puede
enumerar la fototeca y se corrige en **Fotos**; no se corrige concediendo
Automatización. `PHOTOS_AUTOMATION_DENIED` significa que PhotoScript no puede
enviar Apple Events a Fotos y se corrige en **Automatización**. El CLI imprime
además el intérprete activo (por ejemplo, `.venv/bin/python3.11`) para que no
se autorice por error otra instalación de Python. En la app distribuida, el
destino es **Photos Local Keyword Indexer** y su helper firmado incluido. El
diagnóstico no solicita ni modifica permisos automáticamente.

Cuando el CLI muestra una línea `runtime:`, úsala para autorizar exactamente el
proceso que falló: en desarrollo identifica el intérprete activo (por ejemplo,
`.venv311/bin/python`); en una instalación distribuida identifica
`Photos Local Keyword Indexer / PhotosIndexerWorker`. No autorices otra versión
de Python ni Terminal como sustituto del helper. La línea distingue también si
la superficie fallida es `Fotos para PhotoKit` o `Automatización para controlar
Fotos`. La terminal integrada de Codex no hereda el permiso de Terminal.app:
si el diagnóstico identifica Codex, autoriza ese host o ejecuta el comando en
Terminal.app, que debe tener su propio permiso.

Un CLI de Python puro puede no tener un bundle de aplicación ni una clave
`NSPhotoLibraryUsageDescription`; en ese caso macOS puede mantener PhotoKit en
estado denegado aunque la versión de Python que aparece en Configuración del
Sistema sea la misma. Cierra y vuelve a abrir el proceso después de cambiar el
permiso. Si el estado continúa denegado, ejecuta el CLI desde Terminal.app con
ese host autorizado o usa la app distribuida, cuyo bundle incluye la
descripción de uso. Autorizar `/usr/bin/python3.11` no autoriza automáticamente
`.venv311/bin/python`.

No se necesita **Acceso total al disco**. No lo concedas para intentar resolver un problema de permisos de Fotos o
Automatización.

Cuando uses la app instalada, concede esos permisos a **Photos Local Keyword Indexer**. Cuando uses el CLI, se
conceden al proceso que lo ejecuta (Terminal, Codex o el IDE). Apple Maps es opcional: el interruptor de la app y
`--apple-maps` solicitan la excepción explícita antes de enviar solo las coordenadas GPS disponibles a Apple Maps.
No se usa la ubicación actual del sistema.

## Flujo de la app

1. **Revisión** es la mesa de trabajo principal. Con la preparación lista, abre o recupera una sesión y carga hasta
   10 fotos aleatorias que no sean screenshots y que no tengan keywords ni caption. El tamaño de la mesa, el modelo,
   los captions, Apple Maps y el número de inferencias simultáneas se pueden ajustar en la barra superior. PhotoKit,
   PhotoScript y las escrituras siguen serializados aunque Ollama analice varias imágenes en paralelo.
2. Cada tarjeta aparece de inmediato con miniatura y estado propio. Cuando termina su análisis, sus keywords y caption
   se vuelven editables sin esperar al resto de la cola. La barra durante Ollama es indeterminada; al terminar se muestra
   la confianza del modelo, no un porcentaje de progreso inventado. El inspector técnico muestra identidad, modelo,
   routing y traza sanitizada sin coordenadas, OCR literal, rutas temporales ni respuesta cruda.
3. **Guardar en Fotos** es la confirmación explícita de una sola tarjeta. La app vuelve a leer la foto, conserva cambios
   externos, escribe solo los valores visibles, realiza read-back y retira la tarjeta únicamente después de guardar el
   receipt verificable. Al iniciar el guardado, las selecciones aprobadas quedan fijadas para evitar que una edición
   posterior cambie esa decisión. Para cambiar la selección, ejecuta un dry-run nuevo. **Descartar** no ejecuta setters.
   **Reanalizar** conserva el intento anterior y las ediciones
   manuales, y permite usar otro modelo o un prompt analítico privado sin cambiar el sobre estructural de seguridad.
4. Una tarjeta guardada o descartada deja sitio a otra foto al final de la cola. Pausar impide iniciar trabajo nuevo;
   lo ya iniciado termina de forma segura. Después de un reinicio se recuperan análisis de solo lectura, pero una
   escritura interrumpida queda `uncertain` y nunca se repite automáticamente.
5. En **Historial** consulta sesiones, intentos y manifests, y, si procede, ejecuta rollback. El rollback elimina
   exclusivamente keywords verificadas que añadió ese run.
   También puedes importar un `manifest.json`: si viene acompañado por su dry-run fuente coherente, la app copia ambos
   runs y permite confirmar la aplicación; si falta esa fuente, lo marca **Solo consulta** y tendrás que importar el
   dry-run o ejecutar un scan nuevo. La validación de provenance nunca se desactiva.

El CLI conserva el flujo batch `scan → review → apply` para automatización avanzada y auditoría; la consolidación
continua corresponde a la app nativa.

Los runs de la app se guardan localmente con permisos privados en:

```text
~/Library/Application Support/Photos Local Keyword Indexer/runs/
```

Las opciones de la mesa continua (cantidad visible, modelos, concurrencia y preferencias opt-in) se conservan en
`~/Library/Application Support/Photos Local Keyword Indexer/settings.json`, también con permisos privados. Ese archivo
no contiene imágenes, captions, coordenadas ni respuestas del modelo.
Su esquema está versionado; si falta o no es compatible, la app vuelve a los valores predeterminados sin tocar Fotos.

Los exports temporales están en Cachés y se eliminan al completar, fallar o cancelar. Los logs solo almacenan eventos
y códigos sanitizados; no incluyen imágenes, captions, OCR, coordenadas ni respuestas del modelo.

## Actualizaciones de la app

La beta pública unsigned no busca ni instala actualizaciones automáticamente: descarga y verifica manualmente una
nueva prerelease. Sparkle permanece deshabilitado. En una futura release firmada con un feed configurado, la app podrá
comprobar actualizaciones mediante Sparkle por HTTPS al abrir o cuando el usuario lo solicite. Una actualización nunca
descarga modelos Ollama ni ejecuta `apply`/`rollback`.

Consulta la [guía de usuario](docs/user-guide.md), la [nota de privacidad](docs/privacy.md), la [guía de soporte
seguro](docs/support.md), la [licencia MIT](LICENSE) y [la guía de release](docs/release.md).

## Uso seguro

### 0. Diagnóstico local (sin abrir Fotos)

Antes de un dry-run puedes comprobar las dependencias locales sin crear un run:

```bash
python -m photos_indexer doctor --model qwen3-vl:4b
# Para integrarlo con soporte automatizado, usa salida JSON bounded:
python -m photos_indexer doctor --model qwen3-vl:4b --json
# Para una ejecución adaptive, comprueba ambos modelos:
python -m photos_indexer doctor --model-policy adaptive --fast-model qwen3-vl:4b --detailed-model qwen3-vl:8b
```

`doctor` consulta únicamente Ollama en `127.0.0.1:11434` y comprueba que PhotoScript pueda cargar su puente. No
abre Fotos, no solicita permisos, no descarga modelos y no verifica TCC de forma pasiva; por eso informa
`scope:ollama_photoscript_local`, `tcc:not_checked` y `helper:not_checked`. La comprobación real de TCC y del
helper ocurre durante el dry-run; `doctor` no abre Fotos. Un resultado `doctor:blocked` debe resolverse antes de
ejecutar `scan`.

También muestra `app_version` y una identidad acotada (`runtime`) con el nombre del intérprete/helper, versión de
Python, arquitectura y si está empaquetado. Si un dry-run y `doctor` parecen usar entornos distintos, compara esos
campos y repite ambos comandos con el mismo intérprete; nunca se imprime la ruta completa.

Con `--json`, `doctor` emite únicamente un objeto acotado con esos datos, modelos, códigos, siguiente acción y las
marcas `tcc_not_checked`/`helper_not_checked`; no incluye rutas completas ni datos de fotos.

### 1. Escanear en modo dry-run

```bash
python -m photos_indexer scan --limit 20 --model qwen3-vl:4b
```

Al terminar, el CLI imprime `manifest:<ruta>` para que puedas abrir el run en `review` o en la app. Esa es la única
ruta local que muestra deliberadamente el CLI; la tabla, el CSV y los eventos de la app siguen usando UUID abreviados
y no muestran rutas de exportación ni contenido de imágenes.

Para incluir captions breves en la revisión (sin reemplazar captions existentes), añade `--include-caption`:

```bash
python -m photos_indexer scan --limit 20 --model qwen3-vl:4b --include-caption
```

Para enriquecer fotos geolocalizadas con lugares cercanos de Apple Maps, usa la excepción explícita:

```bash
python -m photos_indexer scan --limit 20 --model qwen3-vl:4b --apple-maps
```

En la app activa **Seleccionar fotos al azar** en el alcance del dry-run. En el CLI, para elegir fotos al azar de toda la fototeca elegible, añade `--random`:

```bash
python -m photos_indexer scan --limit 10 --model qwen3-vl:4b --random
```

La selección aleatoria queda registrada como `strategy: random` en el manifiesto privado. La tabla, el CSV y los eventos
de la app solo muestran UUID abreviados y nunca incluyen rutas de exportación; el UUID completo se conserva únicamente
en el manifiesto `0600`, necesario para la revisión y el rollback.

`--limit` acepta de 1 a 500 y vale 20 por defecto; `--model` vale `qwen3-vl:4b`. Antes de abrir Fotos, el comando
comprueba el servidor, su versión, la coincidencia exacta del modelo instalado y su capacidad de visión. Si falta el
modelo, muestra el comando exacto `ollama pull <modelo>` y termina sin modificar Fotos.

Las imágenes se recorren por fecha descendente. Las capturas se descartan antes de exportar o analizar. Si hay menos
de 20 fotos elegibles, el run sigue siendo válido, procesa las disponibles y muestra `FEWER_PHOTOS_AVAILABLE`.
Una confianza inferior a `0.60` produce `LOW_CONFIDENCE` y ninguna propuesta para esa foto.

El resultado queda en `runs/YYYYMMDDTHHMMSSZ-<id>/`:

- `manifest.json`: registro auditable con UUID, título, fecha sin zona horaria, keywords existentes, propuestas,
  caption opcional, confianza, estados, keywords/captions realmente aplicados y códigos de error. No lo edites: `apply` valida su esquema y el
  digest de la parte inmutable del scan.
- `preview.csv`: una fila por foto elegible. Las listas se serializan como JSON dentro de la celda. Incluye
  `caption_status` como indicador (`pendiente`, `preservado`, `verificado`, `retirado` o `incierto`), pero nunca el texto
  del caption. No contiene copias de las imágenes, OCR ni rutas de exportación.

El directorio del run tiene permisos `0700` y sus archivos `0600`, porque título y keywords pueden seguir siendo
datos personales. Revisa `preview.csv` antes de continuar, por ejemplo con Numbers o una herramienta que no lo suba
a la nube. El `scan` nunca escribe en Apple Fotos.

### 2. Aplicar un manifiesto revisado

```bash
python -m photos_indexer apply runs/<timestamp>-review-<id>/manifest.json
```

`apply` vuelve a localizar y validar cada foto, lee sus keywords actuales y añade únicamente propuestas aún ausentes,
sin alterar el texto ni orden de las ya existentes. Después reabre la foto y comprueba la lectura. Un fallo individual
no detiene las demás fotos. Los estados principales son `verified`, `noop`, `failed` y `uncertain`.

No edites keywords simultáneamente desde Fotos u otra app durante `apply`: AppleScript no ofrece una operación
compare-and-set. Un estado `uncertain` requiere inspección manual; el programa no adivina si una escritura
interrumpida ocurrió.

Para el CLI avanzado, crea primero un JSON local con UUID completos y keywords aprobadas. Un manifest de dry-run
(incluidos los manifests legacy schema1) nunca se aplica directamente y produce `MANIFEST_NOT_REVIEWED`:

`status` también marca como bloqueado cualquier schema1 que ya contenga estado de mutación; no lo interpreta como un
objetivo válido de rollback y exige un dry-run nuevo.

```json
{"49f027c6-0000-4000-8000-10215adf7804": ["Basílica de Santa María de la Salud", "arquitectura barroca"]}
```

Genera una copia revisada y aplica únicamente esa copia:

```bash
python -m photos_indexer review runs/<timestamp>/manifest.json --selections seleccion.json
python -m photos_indexer apply runs/<timestamp>-review-<id>/manifest.json
```

Si el dry-run se ejecutó con `--include-caption`, la aprobación del caption se entrega en un segundo JSON local:

```json
{"49f027c6-0000-4000-8000-10215adf7804": true}
```

```bash
python -m photos_indexer review runs/<timestamp>/manifest.json \
  --selections seleccion.json --caption-selections captions.json
python -m photos_indexer apply runs/<timestamp>-review-<id>/manifest.json
```

El caption se escribe en la descripción de Fotos, no en el título. Si la foto ya tiene una descripción no se
reemplaza; el manifiesto registra `preserved`. Los captions escritos y verificados quedan registrados para que un
rollback pueda retirar exclusivamente los que añadió esa ejecución.

### 3. Consultar el estado

```bash
python -m photos_indexer status runs/<timestamp>/manifest.json
```

`status` solo lee el manifiesto local: no abre Fotos ni llama a Ollama. Resume estados de scan, apply y rollback,
errores y una siguiente acción segura. Según el estado puede ser `review_then_apply`, `retry_failed_operation`,
`rollback_available`, `manual_review`, `fix_failed_scan`, `rescan_after_permissions`, `rescan_after_provenance`,
`rescan_after_mutation_evidence`, `fix_fatal_error` o `none`.

Si `errors_by_code` incluye `PHOTOS_ACCESS_DENIED` o `PHOTOS_AUTOMATION_DENIED`, `status` muestra la concesión de
ese permiso como siguiente acción. Ese run es un punto de recuperación, no una autorización para aplicar: concede el
permiso y ejecuta un **scan nuevo**. Un manifiesto que solo contiene fallos de permisos se rechaza con `apply` para
evitar un falso éxito o una escritura basada en metadata incompleta.

### 4. Revertir exclusivamente esta ejecución

```bash
python -m photos_indexer rollback runs/<timestamp>/manifest.json
```

El rollback acepta únicamente el manifiesto revisado que ya pasó por `apply`; nunca usa directamente un dry-run
ruteado sin revisión. Considera solo `applied_keywords` registradas después de una verificación exitosa. Elimina coincidencias
exactas añadidas por ese run y conserva las keywords externas agregadas después. Si existe un caption aplicado por ese run,
también lo elimina únicamente cuando la descripción actual coincide exactamente con el valor aplicado; si fue editado después,
lo conserva y marca `uncertain` para revisión manual. Los captions que ya existían o que fueron preservados nunca se tocan.
Si una keyword ya no está, marca
`already_absent`; si solo queda una variante de mayúsculas/minúsculas, la conserva y marca `casing_conflict`. No
revierte automáticamente estados `uncertain`. Después de iniciar un rollback, `apply` sobre ese manifiesto se rechaza:
haz un scan nuevo.

Cada fila aplicada conserva un recibo local (`mutation_digest`) calculado después del read-back. Si falta o no coincide,
`rollback` y `status` se bloquean con `MUTATION_EVIDENCE_INVALID`; los manifests antiguos con cambios aplicados pero sin
ese recibo requieren un scan y apply nuevos. No edites manualmente esos campos ni compartas el manifest como si fuera
prueba autenticada: el recibo detecta corrupción o cambios accidentales, no autentica frente al mismo usuario.

Fotos puede tardar brevemente en reflejar un setter de AppleScript en su getter. Para evitar falsos estados inciertos,
la capa PhotoScript escribe cada campo una sola vez y hace un polling acotado del read-back (hasta seis lecturas, sin
repetir la escritura). Si el valor todavía no aparece, el resultado sigue siendo `WRITE_UNCERTAIN` y queda bloqueado
para revisión manual; nunca se reintenta automáticamente el setter.

Tampoco edites keywords desde otra app mientras corre el rollback. Conserva el manifiesto: sin él no existe un
registro seguro de qué añadió esa ejecución.

Un fallo o interrupción de otra ejecución no bloquea un rollback histórico que conserve evidencia verificada. La app sí
impide cualquier rollback mientras otra operación esté activa y mantiene bloqueado el run que requiere revisión manual.

## Interpretar el CSV, los estados y la salida

La tabla de terminal y `preview.csv` incluyen UUID abreviado, título truncado de forma segura, fecha, keywords
existentes, propuestas, indicador `caption_status`, confianza, estado y códigos de error. No muestran captions ni
contenido visual.

- Scan: `ready` tiene propuestas aplicables; `noop` no propone cambios; `analysis_failed` no se puede aplicar.
- Apply: `verified` confirma escritura y lectura; `noop` no necesitó cambios; `failed` se puede reintentar;
  `uncertain` necesita revisión manual.
- Rollback: `verified_removed` confirma eliminación; `already_absent` no necesitó cambios; `casing_conflict` conserva
  la variante; `failed` se puede reintentar; `uncertain` necesita revisión manual.

Códigos de salida:

- `0`: éxito o no-op seguro.
- `1`: resultado parcial; revisa errores, estados inciertos y la siguiente acción.
- `2`: preflight o manifiesto inválido; no comenzó una mutación válida.

Códigos comunes del scan son `READ_FAILED`, `EXPORT_FAILED`, `ANALYSIS_FAILED`, `LOW_CONFIDENCE`,
`PHOTOSCRIPT_UNAVAILABLE` y
`EXPORT_DELETE_FAILED`. Durante mutaciones pueden aparecer `APPLY_FAILED`, `WRITE_UNCERTAIN`, `ROLLBACK_FAILED`,
`CASING_CONFLICT` o `REMOVAL_UNCERTAIN`. Los mensajes persisten códigos cerrados, no excepciones crudas que puedan
revelar rutas.

## Resolución de problemas

### Ollama no responde o falta el modelo

Confirma que la app/servicio de Ollama está iniciado y que responde exclusivamente en loopback. Comprueba los modelos
con `ollama list`. Si el modelo exacto no aparece, ejecuta:

```bash
ollama pull qwen3-vl:4b
```

No hay fallback ni descarga automática. Un modelo con `cloud` en el nombre o sin capacidad `vision` se rechaza.

### Resultado interno no coherente (`UNSAFE_WORKFLOW_RESULT`)

Ejecuta `doctor` y repite el dry-run con el mismo intérprete. Si persiste, comparte únicamente el diagnóstico
sanitizado y no intentes `apply` sobre ese resultado. La CLI muestra también `app_version` y una identidad de runtime
acotada, sin la ruta completa del usuario, para detectar si se ejecutó otra instalación o una copia antigua.

### Error Apple Events `-1743`

macOS denegó Automatización. Ve a **Configuración del Sistema → Privacidad y seguridad → Automatización**, habilita
Fotos para la app desde la que ejecutas Python y vuelve a intentar. Si cambiaste de Terminal, IDE o intérprete, macOS
puede considerarlo otro solicitante. No concedas Acceso total al disco como sustituto.

### Error PhotoScript/AppleScript `-2741`

Este error indica que PhotoScript no pudo compilar o cargar su puente AppleScript; no es el permiso de
Automatización `-1743`. El programa lo reporta como `PHOTOSCRIPT_UNAVAILABLE`, no muestra el texto crudo del
compilador y no intenta modificar Fotos. Para el script exacto de PhotoScript 0.5.3, el proyecto aplica en memoria una
corrección acotada por versión y hash que sustituye únicamente sus dos lecturas del reloj por `NSDate`; no modifica
`site-packages` ni cambia los handlers de lectura, exportación o escritura. Si `doctor` todavía reporta este código, la
fuente instalada no coincide con la auditada o existe otra incompatibilidad: no intentes `apply`, no edites la base de
datos de Fotos y no desactives SIP o AMFI.

### Fotos no aparecen, acceso limitado o assets en iCloud

Revisa el permiso **Fotos** del proceso ejecutor. Con acceso limitado el programa solo ve la selección autorizada. Un
asset almacenado únicamente en iCloud puede tardar o agotar los 120 segundos de exportación; abre Fotos, deja que la
copia se descargue localmente y ejecuta un scan nuevo. El programa no modifica el original ni fuerza descargas fuera
del mecanismo normal de Fotos.

### PyObjC, AMFI o macOS 26

Si Python no puede cargar PyObjC/Photos o macOS rechaza el binario, recrea el entorno con una instalación firmada y
compatible de Python.org o Homebrew y reinstala el proyecto. Ejecuta siempre desde el mismo intérprete al que
concediste permisos. No deshabilites SIP, AMFI ni otras protecciones del sistema. En macOS 26 el proyecto verifica el
puente de PhotoScript durante `doctor` y antes del scan; cualquier fuente distinta del artefacto 0.5.3 auditado queda
fail-closed. El preflight del helper falla antes de limpiar el helper anterior, por lo que un intento bloqueado no
destruye el último build utilizable.

## Desarrollo y pruebas

La suite normal usa adapters falsos y no accede a una fototeca ni a un servidor Ollama real:

```bash
pytest
ruff check .
pytest --cov --cov-report=term-missing --cov-fail-under=90
```

El último comando mide específicamente política (`taxonomy` y `models`), manifiesto y workflows, tal como define
`pyproject.toml`; no presenta la cobertura de adapters o CLI como si perteneciera a ese umbral.

Las pruebas mutantes reales están deshabilitadas por defecto. El harness no coincide con el patrón de colección de
`pytest`, usa visión falsa determinista y exige un UUID de una única foto en una biblioteca de prueba más esta
confirmación exacta:

```bash
PHOTOS_INDEXER_SMOKE_UUID='<UUID-DE-UNA-FOTO-DE-PRUEBA>' \
PHOTOS_INDEXER_SMOKE_CONFIRM='I_CONFIRM_TEST_LIBRARY_KEYWORD_MUTATION' \
PHOTOS_INDEXER_SMOKE_CAPTION_CONFIRM='I_CONFIRM_TEST_LIBRARY_CAPTION_MUTATION' \
PYTHONPATH=src .venv/bin/python tests/macos_mutation_smoke.py
```

Este comando sí exporta esa foto y modifica temporalmente sus keywords y su descripción mediante los bridges reales de
macOS. La foto de prueba debe tener la descripción vacía: el harness nunca reemplaza una descripción preexistente.
Ejecuta `scan` con caption, `apply`, read-back del setter `photo.description`, añade después un marcador externo único,
ejecuta `rollback`, comprueba que el marcador se conserve y finalmente intenta restaurar la descripción original y
limpiar solo el marcador y la keyword de prueba elegida. Las dos variables de confirmación son deliberadamente
independientes. No lo ejecutes sobre la fototeca personal, no edites la foto simultáneamente y atiende
`smoke:cleanup_required` manualmente si aparece.

Antes del primer `apply`, utiliza una biblioteca de Fotos de prueba. Si no es posible, realiza un respaldo reciente,
revisa el CSV completo y aplica primero sobre un conjunto pequeño. La reversión protege únicamente las keywords que
el manifiesto registró como añadidas y verificadas por esta herramienta.
